from telegram import Bot
from telegram.request import HTTPXRequest
from telegram.error import TelegramError, RetryAfter, TimedOut, NetworkError, Forbidden
import asyncio
import logging
import time
import itertools
from app.core.config import settings
from app.services.user_agent_srv import user_agent

logger = logging.getLogger("broadcaster")

class BroadcasterService:
    def __init__(self):
        self.bot_tokens = settings.bot_tokens
        self._bots = {} # token -> Bot instance
        self._failed_tokens: set = set()

        # Rotation pool: bots ONLY for broadcast messages.
        # MTProto user sessions are reserved exclusively for admin operations
        # (topic creation, group management, Matkap scraping).
        # Sending broadcasts via a real user account is an OPSEC risk — real
        # phone numbers are visible to group admins in the member list.
        self._pool = []
        for token in self.bot_tokens:
            self._pool.append({"type": "bot", "id": token})

        self._cycle = itertools.cycle(self._pool)
        self._last_send_time = 0
        from app.core.constants import BROADCAST_RATE_LIMIT_SLEEP
        self._min_delay = BROADCAST_RATE_LIMIT_SLEEP

    def _get_bot_instance(self, token: str) -> Bot:
        if token not in self._bots:
            request = HTTPXRequest(
                connection_pool_size=100,
                pool_timeout=60.0,
                read_timeout=25.0,
                write_timeout=25.0,
            )
            self._bots[token] = Bot(token=token, request=request)
        return self._bots[token]

    async def _wait_for_rate_limit(self):
        """Ensures a minimum delay between ANY two messages sent by the system."""
        elapsed = time.time() - self._last_send_time
        if elapsed < self._min_delay:
            wait_time = self._min_delay - elapsed
            await asyncio.sleep(wait_time)
        self._last_send_time = time.time()

    async def send_message(self, group_id: int | str, thread_id: int, msg_obj: dict):
        """
        Sends a message using the next available identity (Bot or User Account).
        """
        content = msg_obj.get("content", "")
        sender = msg_obj.get("sender_name", "Unknown")
        media_type = msg_obj.get("media_type", "text")
        msg_id = msg_obj.get("telegram_msg_id", "?")

        caption = f"[ID: {msg_id}] [From: {sender}]\n{content}"
        if len(caption) > 1024:
            caption = caption[:1021] + "..."

        to_send_text = caption
        if media_type == "photo":
            to_send_text = f"{caption}\n\n[Photo Media Detected]"
        elif media_type != "text":
            to_send_text = f"{caption}\n\n[{media_type} Media Detected]"

        # Try up to N times (total size of pool)
        for _ in range(len(self._pool)):
            identity = next(self._cycle)
            
            await self._wait_for_rate_limit()

            if identity["type"] == "bot":
                token = identity["id"]
                if token in self._failed_tokens: continue
                
                bot = self._get_bot_instance(token)
                try:
                    logger.info(f"📤 [Broadcaster] Sending via Bot: {token[:10]}...")
                    await bot.send_message(
                        chat_id=group_id,
                        message_thread_id=thread_id if thread_id != 1 else None, # 1 often causes issues
                        text=to_send_text
                    )
                    return
                except Forbidden:
                    self._failed_tokens.add(token)
                    logger.warning(f"⚠️ Bot {token[:10]}... kicked. Rotating...")
                except TelegramError as e:
                    if "Message thread not found" in str(e) and thread_id is not None:
                        logger.warning("⚠️ Topic not supported in this group. Retrying in General...")
                        await self._wait_for_rate_limit()
                        try:
                            await bot.send_message(chat_id=group_id, text=to_send_text)
                            return
                        except Exception as fallback_e:
                            logger.error(f"❌ General fallback send failed: {fallback_e}")
                    logger.error(f"❌ Bot send failed: {e}")

        logger.error("❌ All identities failed to send message.")

    async def send_log(self, message: str):
        """Sends a log to the General topic using a healthy bot."""
        await self._wait_for_rate_limit()
        try:
            bot = self._get_bot_instance(self.bot_tokens[0])
            await bot.send_message(
                chat_id=settings.MONITOR_GROUP_ID,
                text=f"🤖 [System Log]\n{message}"
            )
        except Exception as e:
            logger.error(f"Failed to send log: {e}")

    async def ensure_topic(self, group_id: int | str, topic_name: str) -> int:
        """Ensures a forum topic exists."""
        try:
            existing_id = await user_agent.find_topic_id(group_id, topic_name)
            if existing_id: return existing_id
        except Exception: pass

        if topic_name in ["General", "general", "main"]: return 1

        bot = self._get_bot_instance(self.bot_tokens[0])
        try:
            topic = await bot.create_forum_topic(chat_id=group_id, name=topic_name)
            return topic.message_thread_id
        except Exception as e:
            logger.error(f"Topic creation failed: {e}")
            return 1 # Fallback to general
