import asyncio
import os
import sys

# Setup logging
import logging
logging.basicConfig(level=logging.INFO)

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.user_agent_srv import UserAgentService, BASE_DIR

async def main():
    print("Starting User Agent Service...")
    ua = UserAgentService()
    
    # Pre-test: create a mock session file
    mock_file_1 = os.path.join(BASE_DIR, "mock_test_1.session")
    with open(mock_file_1, "w") as f:
        f.write("mock")
        
    print(f"Created {mock_file_1}")
    
    # Start it up so it discovers the first session
    ua._discover_sessions()
    print("Initial sessions:", ua.sessions)
    
    # Start the task manually since start() does a lot of connection logic we don't want to actually run
    if getattr(ua, '_refresher_task', None) is None:
        # Patch the sleep time to 2 seconds for the test
        async def fast_refresher():
            while True:
                await asyncio.sleep(2)
                ua._discover_sessions()
        
        ua._refresher_task = asyncio.create_task(fast_refresher())
        
    print("Background refresher started.")
    
    await asyncio.sleep(1)
    
    # Create a second mock session file
    mock_file_2 = os.path.join(BASE_DIR, "mock_test_2.session")
    with open(mock_file_2, "w") as f:
        f.write("mock")
        
    print(f"Created {mock_file_2}")
    
    # Wait for the refresher to pick it up
    await asyncio.sleep(3)
    
    print("Final sessions:", ua.sessions)
    
    # Cleanup
    if os.path.exists(mock_file_1): os.remove(mock_file_1)
    if os.path.exists(mock_file_2): os.remove(mock_file_2)
    
    ua._refresher_task.cancel()
    
    print("Done")

if __name__ == "__main__":
    asyncio.run(main())
