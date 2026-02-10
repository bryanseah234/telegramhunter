import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.workers.tasks.audit_tasks import _audit_active_topics_async
from unittest.mock import AsyncMock, patch, MagicMock

async def test_audit():
    print("🧪 Testing Audit Task (Mocked)...")
    
    # Mock DB response
    mock_db_response = MagicMock()
    mock_db_response.data = [
        {
            "id": "test-cred-1",
            "meta": {"topic_id": 123},
            "chat_id": 456
        }
    ]
    
    # Mock UserAgent response
    mock_user_agent = AsyncMock()
    mock_user_agent.get_last_message_id.return_value = 1005 # Telegram has msg 1005

    # Mock DB message query response (Latest DB msg is 1000)
    mock_msg_response = MagicMock()
    mock_msg_response.data = [{"telegram_msg_id": 1000}]

    # Patch where the objects are IMPORTED in audit_tasks.py
    with patch("app.workers.tasks.audit_tasks.db") as mock_db:
        # 1. Mock Fetch Active Creds
        # Chain for: db.table("discovered_credentials").select(...).eq(...).execute()
        mock_db.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_db_response
        
        # 2. Mock Message Query
        # Chain for: db.table("exfiltrated_messages").select(...).eq(...).eq(...).order(...).limit(...).execute()
        # We simplify the chain matching by targeting the final .execute()
        # But since we have multiple table calls, we need to distinguish them or just return different things based on call count
        # A simpler way is to mock the table() call to return different mocks for different tables
        
        mock_creds_table = MagicMock()
        mock_creds_table.select.return_value.eq.return_value.execute.return_value = mock_db_response
        
        mock_msgs_table = MagicMock()
        mock_msgs_table.select.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = mock_msg_response
        
        def table_side_effect(name):
            if name == "discovered_credentials":
                return mock_creds_table
            if name == "exfiltrated_messages":
                return mock_msgs_table
            return MagicMock()
            
        mock_db.table.side_effect = table_side_effect
        
        with patch("app.workers.tasks.audit_tasks.BroadcasterService") as MockBroadcaster:
            mock_broadcaster_instance = MockBroadcaster.return_value
            mock_broadcaster_instance.send_log = AsyncMock()
            mock_broadcaster_instance.bot.send_chat_action = AsyncMock()
            
            # Mock UserAgent correctly
            # It is imported inside the function, so we patch the module path
            with patch("app.services.user_agent_srv.user_agent", mock_user_agent):
                print("  - Running Audit Logic...")
                res = await _audit_active_topics_async()
                print(f"  ✅ Audit Result: {res}")
                
    print("🎉 Audit Test Completed.")

if __name__ == "__main__":
    try:
        asyncio.run(test_audit())
    except Exception as e:
        print(f"❌ Test Failed: {e}")
        import traceback
        traceback.print_exc()
