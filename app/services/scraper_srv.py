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
            print(f"🔐 [Scraper] Logging in as bot for chat history {chat_id}...")
            await client.start(bot_token=bot_token)
            print(f"✅ [Scraper] Login successful.")

            # Check if we can access the chat (basic check)
            # Fetch history
            print(f"📖 [Scraper] Fetching messages from {chat_id} (Limit: {limit})...")
            count = 0
            async for message in client.iter_messages(chat_id, limit=limit):
                count += 1
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

        print(f"✨ [Scraper] Scraped {len(scraped_messages)} messages (Processed {count} raw objects).")
        return scraped_messages

    async def discover_chats(self, bot_token: str) -> List[Dict]:
        """
        Validates a bot token and discovers chats using Telegram Bot API.
        
        Bot tokens CANNOT use Telethon's iter_dialogs (user-only method).
        Instead, we use:
        1. getMe - validate token works
        2. getUpdates - find chats the bot has interacted with
        """
        import requests
        
        base_url = f"https://api.telegram.org/bot{bot_token}"
        discovered_chats = []
        
        try:
            print(f"🔍 [Discovery] Validating token {bot_token[:15]}... via Bot API")
            
            # Step 1: Validate token with getMe
            me_res = requests.get(f"{base_url}/getMe", timeout=10)
            if me_res.status_code != 200 or not me_res.json().get('ok'):
                print(f"    ❌ Token invalid or revoked")
                return []
            
            bot_info = me_res.json().get('result', {})
            print(f"    ✅ Token valid! Bot: @{bot_info.get('username', 'unknown')}")
            
            # Step 2: Get recent chats from getUpdates
            updates_res = requests.get(f"{base_url}/getUpdates", params={'limit': 100}, timeout=15)
            if updates_res.status_code == 200 and updates_res.json().get('ok'):
                updates = updates_res.json().get('result', [])
                
                # Extract unique chats from updates
                seen_chats = set()
                for update in updates:
                    # Check message, edited_message, channel_post, etc.
                    for key in ['message', 'edited_message', 'channel_post', 'edited_channel_post', 'my_chat_member', 'chat_member']:
                        if key in update:
                            chat = update[key].get('chat', {})
                            chat_id = chat.get('id')
                            if chat_id and chat_id not in seen_chats:
                                seen_chats.add(chat_id)
                                chat_type = chat.get('type', 'unknown')
                                chat_name = chat.get('title') or chat.get('username') or chat.get('first_name') or str(chat_id)
                                
                                discovered_chats.append({
                                    "id": chat_id,
                                    "name": chat_name,
                                    "type": chat_type
                                })
                                print(f"    📍 Found Chat: {chat_name} (ID: {chat_id}, Type: {chat_type})")
                
                # If no updates but token is valid, use bot's own ID as fallback
                if not discovered_chats:
                    # Token works but no recent activity - still valid!
                    # Use a placeholder to indicate token is valid but no chats found
                    print(f"    ℹ️ Token valid but no recent chat activity")
                    # Return bot info as a "chat" so validation passes
                    discovered_chats.append({
                        "id": bot_info.get('id'),
                        "name": f"@{bot_info.get('username', 'bot')} (Bot Self)",
                        "type": "bot_self"
                    })
            
            print(f"🏁 [Discovery] Found {len(discovered_chats)} chat(s) for this bot.")
            
        except requests.Timeout:
            print(f"    ⚠️ Telegram API timeout")
        except Exception as e:
            print(f"Error discovering chats: {e}")
            
        return discovered_chats

scraper_service = ScraperService()
