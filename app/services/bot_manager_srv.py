import asyncio
import logging
from telethon import TelegramClient
from telethon.sessions import MemorySession
from app.core.config import settings

logger = logging.getLogger("bot_manager")

class BotClientManager:
    """
    Manages a pool of active Telethon clients for bots to prevent frequent logins.
    """
    def __init__(self):
        self.api_id = settings.TELEGRAM_API_ID
        self.api_hash = settings.TELEGRAM_API_HASH
        self._clients = {} # bot_token -> TelegramClient
        self._lock = asyncio.Lock()

    async def get_client(self, bot_token: str) -> TelegramClient:
        """
        Returns a connected and authorized Telethon client for the given bot_token.
        Reuses existing connections if available.
        """
        async with self._lock:
            client = self._clients.get(bot_token)
            
            # Check if client exists and is still connected
            if client:
                if client.is_connected():
                    return client
                else:
                    logger.warning(f"Existing client for bot disconnected or invalid.")
                    await client.disconnect()
                
            # Create new client if needed
            import os
            pid = os.getpid()
            logger.info(f"🚀 [BotManager] [PID:{pid}] Creating fresh connection for bot...")
            # We still use MemorySession for now, but we keep the client alive in memory
            client = TelegramClient(MemorySession(), self.api_id, self.api_hash)
            await client.start(bot_token=bot_token)
            
            self._clients[bot_token] = client
            return client

    async def disconnect_all(self):
        """Cleanly disconnect all managed clients."""
        async with self._lock:
            for token, client in self._clients.items():
                logger.info(f"🔌 [BotManager] Disconnecting bot client...")
                await client.disconnect()
            self._clients.clear()

bot_manager = BotClientManager()
