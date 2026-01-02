import sys
import os
import asyncio

# Add project root to path
sys.path.append(os.getcwd())

from app.workers.tasks.flow_tasks import system_heartbeat

print("Testing system_heartbeat task...")
try:
    result = system_heartbeat()
    print(f"✅ Task executed successfully. Result: {result}")
except Exception as e:
    print(f"❌ Task failed: {e}")
