import asyncio
import logging
from telethon import TelegramClient
from telethon.sessions import MemorySession
from app.core.config import settings

logger = logging.getLogger("bot_manager")

_MAX_CACHED_CLIENTS = 50  # evict LRU entries beyond this to bound memory


class BotClientManager:
    """
    Manages a pool of active Telethon clients for bots to prevent frequent logins.
    Bounded to _MAX_CACHED_CLIENTS entries — oldest disconnected and evicted when full.
    """
    def __init__(self):
        self.api_id = settings.TELEGRAM_API_ID
        self.api_hash = settings.TELEGRAM_API_HASH
        self._clients: dict = {}   # bot_token -> TelegramClient (insertion-ordered for LRU)
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
                    logger.warning(f"Existing client for bot disconnected. Reconnecting...")
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    del self._clients[bot_token]
                
            # Create new client
            import os
            pid = os.getpid()
            logger.info(f"🚀 [BotManager] [PID:{pid}] Creating fresh connection for bot...")
            client = TelegramClient(MemorySession(), self.api_id, self.api_hash)
            try:
                await asyncio.wait_for(client.start(bot_token=bot_token), timeout=30.0)
            except asyncio.TimeoutError:
                logger.error(f"[BotManager] Timeout connecting bot client after 30s")
                raise
            
            # Evict oldest entry when cache is full
            if len(self._clients) >= _MAX_CACHED_CLIENTS:
                oldest_token, oldest_client = next(iter(self._clients.items()))
                try:
                    await asyncio.wait_for(oldest_client.disconnect(), timeout=5.0)
                except Exception:
                    pass
                del self._clients[oldest_token]
                logger.info(f"[BotManager] Evicted oldest cached client (cache full at {_MAX_CACHED_CLIENTS})")

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
