import asyncio
from typing import List, Dict, Optional
from telethon import TelegramClient
from telethon.tl.types import Message, MessageMediaPhoto, MessageMediaDocument
from app.core.config import settings

class ScraperService:
    def __init__(self):
        self.api_id = settings.TELEGRAM_API_ID
        self.api_hash = settings.TELEGRAM_API_HASH

    async def scrape_history(self, bot_token: str, chat_id: int, limit: int = 100) -> List[Dict]:
        """
        Logs in as the compromised bot using the token,
        scrapes the chat history for the given chat_id,
        and returns a list of processed messages.
        """
        # We use a session name based on the token hash or just ephemeral
        # For simplicity in this architecture, we use an in-memory session or temp file
        # 'session_name' argument to TelegramClient usually creates a .session file.
        # We might want to handle this carefully in containerized env.
        # Using 'anon' or a hash of the token to isolate sessions.
        session_name = f"session_{hash(bot_token)}"
        
        client = TelegramClient(session_name, self.api_id, self.api_hash)

        scraped_messages = []

        try:
            # Login
            await client.start(bot_token=bot_token)

            # Check if we can access the chat (basic check)
            # Fetch history
            async for message in client.iter_messages(chat_id, limit=limit):
                if not isinstance(message, Message):
                    continue

                content = message.text or ""
                media_type = "text"
                file_meta = {}

                if message.media:
                    if isinstance(message.media, MessageMediaPhoto):
                        media_type = "photo"
                        # We extract file_id or some identifier. 
                        # Telethon doesn't give 'file_id' like Bot API. 
                        # We might need to download it or just store attributes.
                        # For this brief, we'll store basic layout.
                        file_meta = {"wc": "photo", "id": message.media.photo.id}
                    elif isinstance(message.media, MessageMediaDocument):
                        media_type = "document"
                        file_meta = {"mime": message.media.document.mime_type}
                    else:
                        media_type = "other"

                sender_name = "Unknown"
                if message.sender:
                    if hasattr(message.sender, 'username') and message.sender.username:
                        sender_name = message.sender.username
                    elif hasattr(message.sender, 'first_name'):
                        sender_name = message.sender.first_name

                scraped_messages.append({
                    "telegram_msg_id": message.id,
                    "sender_name": sender_name,
                    "content": content,
                    "media_type": media_type,
                    "file_meta": file_meta
                })

        except Exception as e:
            # Log error (in real app use logger)
            print(f"Error scraping {chat_id}: {e}")
            raise e
        finally:
            await client.disconnect()

        return scraped_messages

scraper_service = ScraperService()
