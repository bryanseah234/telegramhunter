from telegram import Bot
from telegram.error import TelegramError, RetryAfter
import asyncio
import time
from app.core.config import settings

class BroadcasterService:
    def __init__(self):
        self.bot_token = settings.MONITOR_BOT_TOKEN
        self.bot = Bot(token=self.bot_token)

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
        """
        try:
            topic = await self._retry_on_flood(
                self.bot.create_forum_topic, chat_id=group_id, name=topic_name
            )
            return topic.message_thread_id
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

broadcaster_service = BroadcasterService()
