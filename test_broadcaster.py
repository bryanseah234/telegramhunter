import asyncio
import os
import sys
import logging

# Configure Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), ".")))

from app.services.user_agent_srv import user_agent
from app.services.broadcaster_srv import BroadcasterService

async def test_broadcaster():
    print("🚀 Starting Multi-Identity Broadcaster Test...")
    
    # 1. Sync Memberships and Admins
    print("🛠️ Syncing memberships and promoting to anonymous admins...")
    await user_agent._ensure_monitor_bots_membership()
    
    # 2. Test Broadcaster Rotation
    print("📡 Testing broadcaster rotation (Bot + User Accounts)...")
    broadcaster = BroadcasterService()
    
    test_msg = {
        "content": "TEST MESSAGE - Multi-Identity Rotation Active",
        "sender_name": "AI Debugger",
        "media_type": "text",
        "telegram_msg_id": 12345
    }
    
    # Send 5 messages to see the rotation and rate limiting in action
    for i in range(5):
        print(f"📤 Sending message {i+1}/5...")
        try:
            # Send to General topic (thread_id=None or 1)
            await broadcaster.send_message(
                group_id="@theprawnhunter",
                thread_id=1,
                msg_obj=test_msg
            )
            print(f"✅ Message {i+1} sent successfully.")
        except Exception as e:
            print(f"❌ Message {i+1} failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_broadcaster())
