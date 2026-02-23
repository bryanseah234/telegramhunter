import sys
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 1. Mock settings and db BEFORE importing anything else
mock_settings = MagicMock()
mock_settings.TELEGRAM_API_ID = 12345
mock_settings.TELEGRAM_API_HASH = "hash"
mock_settings.MONITOR_BOT_TOKEN = "123:abc"
mock_settings.bot_tokens = ["123:abc"]
mock_settings.MONITOR_GROUP_ID = -100123
mock_settings.WHITELISTED_BOT_IDS = ""
mock_settings.REDIS_URL = "redis://localhost"
mock_settings.ENV = "test"

mock_db = MagicMock()

sys.modules['app.core.config'] = MagicMock(settings=mock_settings)
sys.modules['app.core.database'] = MagicMock(db=mock_db)

async def test_login_flow_alignment():
    print("🚀 Testing Login Flow Alignment...")
    
    # Now safe to import
    from app.services.bot_listener import LoginState, finalize_login
    
    # Verify LoginState
    print(f"✅ LoginState WAITING_FOR_PHONE: {LoginState.WAITING_FOR_PHONE}")
    print(f"✅ LoginState WAITING_FOR_CODE: {LoginState.WAITING_FOR_CODE}")
    print(f"✅ LoginState WAITING_FOR_2FA: {LoginState.WAITING_FOR_2FA}")
    
    # Mock Update and Context
    update = MagicMock()
    update.effective_chat.id = 123
    update.message.reply_text = AsyncMock()
    
    context = MagicMock()
    context.bot.username = "test_bot"
    context.user_data = {
        'phone': '+1234567890',
        'bot_messages': [1, 2],
        'temp_session_path': '/tmp/test_session'
    }
    
    # Mock TelegramClient
    # We need to patch the one inside bot_listener
    with patch("app.services.bot_listener.TelegramClient") as MockClient:
        client = AsyncMock()
        MockClient.return_value = client
        
        me = MagicMock()
        me.first_name = "Test User"
        me.username = "testuser"
        me.phone = "1234567890"
        client.get_me = AsyncMock(return_value=me)
        
        notification_msg = MagicMock()
        notification_msg.message = "Login from a new device"
        notification_msg.delete = AsyncMock()
        
        async def mock_iter(*args, **kwargs):
            yield notification_msg
            
        client.iter_messages.return_value.__aiter__.side_effect = mock_iter
        client.get_entity = AsyncMock()
        client.delete_dialog = AsyncMock()
        client.disconnect = AsyncMock()
        
        context.user_data['client'] = client
        
        # Patch other OS calls
        with patch("os.path.exists", return_value=True), \
             patch("os.makedirs"), \
             patch("os.remove"), \
             patch("shutil.copy2"), \
             patch("os.path.abspath", side_effect=lambda x: x):
            
            # Run finalize_login
            await finalize_login(update, context)
            
            # Verify Database Update
            mock_db.table.assert_called_with("telegram_accounts")
            print("✅ Database update for telegram_accounts verified.")
            
            # Verify Cleanup
            notification_msg.delete.assert_called_once()
            print("✅ Telegram Service Notification deletion verified.")
            
            client.delete_dialog.assert_called_once()
            print("✅ Bot dialogue deletion verified.")
            
            client.disconnect.assert_called_once()
            print("✅ Client disconnection verified.")

async def test_session_discovery():
    print("\n🚀 Testing Session Discovery Alignment...")
    # Mock redis_srv and other deps
    sys.modules['app.core.redis_srv'] = MagicMock()
    
    from app.services.user_agent_srv import UserAgentService
    
    ua = UserAgentService()
    
    # Mock Database Result
    mock_res = MagicMock()
    mock_res.data = [{"session_path": "/absolute/path/to/account_123.session"}]
    mock_db.table().select().eq().execute.return_value = mock_res
    
    with patch("os.path.exists", return_value=True), \
         patch("os.listdir", return_value=["account_456.session"]), \
         patch("os.path.abspath", side_effect=lambda x: x):
        
        ua._discover_sessions()
        
        basenames = [os.path.basename(s) for s in ua.sessions]
        print(f"Discovered sessions: {basenames}")
        assert "account_123.session" in basenames
        assert "account_456.session" in basenames
        print("✅ Session discovery from both DB and FS verified.")

if __name__ == "__main__":
    asyncio.run(test_login_flow_alignment())
    asyncio.run(test_session_discovery())
