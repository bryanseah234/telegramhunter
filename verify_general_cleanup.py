import asyncio
import os
import sys

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from app.workers.tasks.audit_tasks import _cleanup_general_topic_async
from app.core.config import settings

async def main():
    print("🚀 Starting manual verification of General Topic Cleanup...")
    print(f"Group ID: {settings.MONITOR_GROUP_ID}")
    
    try:
        result = await _cleanup_general_topic_async()
        print("\n" + "="*50)
        print("RESULT:")
        print(result)
        print("="*50)
    except Exception as e:
        print(f"❌ Verification failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
