import sys
import os

print("--- START OF TEST ---", flush=True)

try:
    import asyncio
    from unittest.mock import MagicMock, AsyncMock, patch
    print("Mocks and asyncio imported", flush=True)

    # Mock dependencies before import
    mock_settings = MagicMock()
    mock_settings.bot_tokens = ["123:abc"]
    sys.modules['app.core.config'] = MagicMock(settings=mock_settings)
    sys.modules['app.core.database'] = MagicMock(db=MagicMock())
    sys.modules['app.core.redis_srv'] = MagicMock()
    print("Dependencies mocked in sys.modules", flush=True)

    from app.services.bot_listener import LoginState
    print(f"LoginState imported: {LoginState}", flush=True)
    
except Exception as e:
    print(f"Error during import: {e}", flush=True)
    sys.exit(1)

print("--- SUCCESSFUL IMPORT ---", flush=True)
