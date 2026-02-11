from telegram import Bot
from telegram.request import HTTPXRequest  
from telegram.error import TelegramError, RetryAfter, TimedOut, NetworkError
import asyncio
import time
from app.core.config import settings

class BroadcasterService:
    def __init__(self):
        self.bot_token = settings.MONITOR_BOT_TOKEN
        self._bot = None # Lazy initialization

    @property
    def bot(self):
        if self._bot is None:
            # Initialize Bot and HTTPXRequest strictly ON DEMAND
            # This ensures they are created inside the current Worker process/Event Loop
            request = HTTPXRequest(
                connection_pool_size=100, # Allow up to 100 concurrent connections
                pool_timeout=60.0,        # Wait up to 60s
                read_timeout=25.0,        
                write_timeout=25.0,       
            )
            self._bot = Bot(token=self.bot_token, request=request)
        return self._bot

    async def _retry_on_flood(self, func, *args, **kwargs):
        """
        Executes an async function with retry logic for Flood Control (429).
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return await func(*args, **kwargs)
            except RetryAfter as e:
                wait_time = e.retry_after + 1  # Add buffer
                print(f"⚠️ Flood control exceeded. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            except TimedOut:
                print(f"⚠️ Request Timed Out. Retrying in 5s...")
                await asyncio.sleep(5)
            except NetworkError as e:
                print(f"⚠️ Network Error ({e.message}). Retrying in 5s...")
                await asyncio.sleep(5)
            except TelegramError as e:
                # If it's a generic buffer error (sometimes happens with 429 without RetryAfter type)
                if "Flood control exceeded" in str(e) or "Too Many Requests" in str(e):
                     # Parse time or default
                     print(f"⚠️ Flood control exceeded (Generic). Retrying in 10s... ({e})")
                     await asyncio.sleep(10)
                else:
                    raise e
        # Final attempt
        return await func(*args, **kwargs)

    async def ensure_topic(self, group_id: int | str, topic_name: str) -> int:
        """
        Ensures a forum topic exists for the credential.
        Checks ALL available topics via UserAgent first to avoid duplicates.
        """
        # 1. Try to find existing topic via UserAgent (User strict requirement)
        try:
            from app.services.user_agent_srv import user_agent
            existing_id = await user_agent.find_topic_id(group_id, topic_name)
            if existing_id:
                return existing_id
        except Exception as e:
            print(f"⚠️ Failed to check existing topics via UserAgent: {e}")

        # 2. Create if not found
        try:
            topic = await self._retry_on_flood(
                self.bot.create_forum_topic, chat_id=group_id, name=topic_name
            )
            thread_id = topic.message_thread_id
            
            # 3. Lay the ground: Send Topic Name as first message
            try:
                await self.send_topic_header(group_id, thread_id, topic_name)
            except Exception as e:
                print(f"⚠️ Failed to send header for new topic: {e}")
                
            return thread_id
        except TelegramError as e:
            print(f"Error creating topic: {e}")
            raise e

    async def send_message(self, group_id: int | str, thread_id: int, msg_obj: dict):
        """
        Sends a message to the specific topic.
        """
        content = msg_obj.get("content", "")
        sender = msg_obj.get("sender_name", "Unknown")
        media_type = msg_obj.get("media_type", "text")
        msg_id = msg_obj.get("telegram_msg_id", "?")
        
        caption = f"[ID: {msg_id}] [From: {sender}]\n{content}"
        # Truncate caption if too long (Telegram limit 1024)
        if len(caption) > 1024:
            caption = caption[:1021] + "..."

        try:
            to_send_text = caption
            if media_type == "photo":
                to_send_text = f"{caption}\n\n[Photo Media Detected - Not downloaded]"
            elif media_type != "text":
                to_send_text = f"{caption}\n\n[{media_type} Media Detected]"

            await self._retry_on_flood(
                self.bot.send_message,
                chat_id=group_id, 
                message_thread_id=thread_id, 
                text=to_send_text
            )
        except TelegramError as e:
            # Re-raise to allow caller (flow_tasks) to handle specific errors like Topic_deleted
            raise e

    async def send_log(self, message: str):
        """
        Sends a log message to the General topic of the monitor group.
        """
        try:
            await self._retry_on_flood(
                self.bot.send_message,
                chat_id=settings.MONITOR_GROUP_ID,
                message_thread_id=None, # Explicitly target General Topic
                text=f"🤖 [System Log]\n{message}"
            )
        except Exception as e:
            print(f"Failed to send log: {e}")

    async def send_topic_header(self, group_id: int | str, thread_id: int, text: str):
        """
        Sends a plain text message to the topic (used for headers).
        """
        try:
            await self._retry_on_flood(
                self.bot.send_message,
                chat_id=group_id,
                message_thread_id=thread_id,
                text=text
            )
        except Exception as e:
            print(f"Failed to send topic header: {e}")


