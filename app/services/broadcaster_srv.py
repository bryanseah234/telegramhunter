from telegram import Bot
from telegram.error import TelegramError
from app.core.config import settings

class BroadcasterService:
    def __init__(self):
        self.bot_token = settings.MONITOR_BOT_TOKEN
        self.bot = Bot(token=self.bot_token)

    async def ensure_topic(self, group_id: int | str, topic_name: str) -> int:
        """
        Ensures a forum topic exists for the credential.
        In a real scenario, we might need to store topic IDs in DB to avoid re-creation errors 
        or searching through all topics.
        For now, we will TRY to create it. If it fails (maybe duplicates?), we might need a workaround.
        However, the Bot API 'createForumTopic' returns the created topic.
        If we want to be idempotent, we'd store the topic_id in 'discovered_credentials.meta'.
        """
        try:
            topic = await self.bot.create_forum_topic(chat_id=group_id, name=topic_name)
            return topic.message_thread_id
        except TelegramError as e:
            # If error is "topic already exists" (hard to detect via basic API without error codes),
            # we might default to 0 (General) or handle it.
            # BUT: Telegram allows multiple topics with same name.
            print(f"Error creating topic: {e}")
            # Fallback to main thread or handled by caller
            raise e

    async def send_message(self, group_id: int | str, thread_id: int, msg_obj: dict):
        """
        Sends a message to the specific topic.
        """
        content = msg_obj.get("content", "")
        sender = msg_obj.get("sender_name", "Unknown")
        media_type = msg_obj.get("media_type", "text")
        
        caption = f"[From: {sender}]\n{content}"
        # Truncate caption if too long (Telegram limit 1024)
        if len(caption) > 1024:
            caption = caption[:1021] + "..."

        try:
            if media_type == "text":
                await self.bot.send_message(
                    chat_id=group_id,
                    message_thread_id=thread_id,
                    text=caption
                )
            elif media_type == "photo":
                # If we had the file_id from TELETHON, it is NOT compatible with BOT API.
                # Telethon file_id != Bot API file_id.
                # We would normally need to download the file in Scraper and upload here.
                # Since we didn't implement download, we will purely notify about the photo.
                await self.bot.send_message(
                    chat_id=group_id,
                    message_thread_id=thread_id,
                    text=f"{caption}\n\n[Photo Media Detected - Not downloaded]"
                )
            else:
                 await self.bot.send_message(
                    chat_id=group_id,
                    message_thread_id=thread_id,
                    text=f"{caption}\n\n[{media_type} Media Detected]"
                )
        except TelegramError as e:
            print(f"Failed to send message: {e}")

broadcaster_service = BroadcasterService()
