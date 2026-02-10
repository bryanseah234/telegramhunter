import asyncio
import os
import sys

# Ensure app can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.workers.tasks.flow_tasks import _broadcast_logic
from app.core.database import db
from app.core.config import settings
import redis

# Mocking logger to print to stdout
import logging
logging.basicConfig(level=logging.DEBUG)

async def debug_broadcast():
    print("🕵️  DEBUG: Starting Manual Broadcast...")
    
    # 1. Check Pending Count
    try:
        count = db.table("exfiltrated_messages").select("id", count="exact").eq("is_broadcasted", False).execute()
        print(f"    📊 Pending Messages in DB: {count.count}")
    except Exception as e:
        print(f"    ❌ DB Connection Error: {e}")
        return

    # 2. Force Clear Lock (for debugging)
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    lock_key = "telegram_hunter:lock:broadcast"
    if redis_client.get(lock_key):
        print("    🔓 Clearing stuck lock...")
        redis_client.delete(lock_key)

    # 3. Check Stale Claims
    # We can't easily check claims without complex query, but let's see logic run
    
    print("\n🚀 Running Broadcast Logic (Verbose)...")
    try:
        result = await _broadcast_logic()
        print(f"\n✅ Result: {result}")
    except Exception as e:
        print(f"\n❌ Broadcast Crashed: {e}")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(debug_broadcast())
