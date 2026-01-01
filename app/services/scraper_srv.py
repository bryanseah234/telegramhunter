import asyncio
from typing import List, Dict, Optional
from telethon import TelegramClient
from telethon.tl.types import Message, MessageMediaPhoto, MessageMediaDocument
from app.core.config import settings

class ScraperService:
    def __init__(self):
        self.api_id = settings.TELEGRAM_API_ID
        self.api_hash = settings.TELEGRAM_API_HASH

    async def scrape_history(self, bot_token: str, chat_id: int, limit: int = 3000) -> List[Dict]:
        """
        Attempts to scrape chat history.
        Strategy 1: Telethon (GetHistory) - Best for deep history. often restricted for bots.
        Strategy 2: Bot API (getUpdates) - Fallback. Only gets recent buffered messages.
        """
        scraped_messages = []
        
        # Strategy 1: Telethon
        try:
            telethon_msgs = await self._scrape_via_telethon(bot_token, chat_id, limit)
            scraped_messages.extend(telethon_msgs)
            print(f"✨ [Scraper] Telethon success: {len(telethon_msgs)} messages.")
            return scraped_messages
        except Exception as e:
            print(f"⚠️ [Scraper] Telethon restricted/failed ({e}). Attempting fallback...")

        # Strategy 2: Bot API Fallback
        try:
            api_msgs = self._scrape_via_bot_api(bot_token)
            # Filter for specific chat if possible, or just return all recent updates
            # getUpdates returns everything the bot sees.
            relevant_msgs = [m for m in api_msgs if str(m.get('chat_id')) == str(chat_id)]
            scraped_messages.extend(relevant_msgs)
            print(f"✨ [Scraper] Bot API fallback success: {len(relevant_msgs)} messages.")
        except Exception as e:
            print(f"❌ [Scraper] Bot API fallback failed: {e}")

        return scraped_messages

    async def _scrape_via_telethon(self, bot_token: str, chat_id: int, limit: int) -> List[Dict]:
        session_name = f"session_{hash(bot_token)}"
        client = TelegramClient(session_name, self.api_id, self.api_hash)
        msgs = []
        try:
            print(f"🔐 [Scraper] Logging in as bot (Telethon)...")
            await client.start(bot_token=bot_token)
            
            print(f"📖 [Scraper] Fetching history via Telethon (Limit: {limit})...")
            async for message in client.iter_messages(chat_id, limit=limit):
                if not isinstance(message, Message): continue
                
                content = message.text or ""
                media_type = "text"
                file_meta = {}

                if message.media:
                    if isinstance(message.media, MessageMediaPhoto):
                        media_type = "photo"
                        file_meta = {"wc": "photo", "id": getattr(message.media.photo, 'id', 0)}
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

                msgs.append({
                    "telegram_msg_id": message.id,
                    "sender_name": sender_name,
                    "content": content,
                    "media_type": media_type,
                    "file_meta": file_meta,
                    "chat_id": chat_id # Ensure we track where it came from
                })
        finally:
            await client.disconnect()
        return msgs

    def _scrape_via_bot_api(self, bot_token: str) -> List[Dict]:
        """
        Fallback: Use requests to hit https://api.telegram.org/bot<token>/getUpdates
        """
        import requests
        print(f"🔄 [Scraper] Attempting Bot API getUpdates fallback...")
        
        base_url = f"https://api.telegram.org/bot{bot_token}"
        res = requests.get(f"{base_url}/getUpdates", params={'limit': 100}, timeout=15)
        
        msgs = []
        if res.status_code == 200 and res.json().get('ok'):
            updates = res.json().get('result', [])
            for update in updates:
                # We care about 'message', 'edited_message', 'channel_post'
                target = update.get('message') or update.get('channel_post') or update.get('edited_message')
                if not target: continue
                
                chat = target.get('chat', {})
                sender = target.get('from', {})
                
                content = target.get('text') or target.get('caption') or ""
                
                # Determine media
                media_type = "text"
                file_meta = {}
                if 'photo' in target:
                    media_type = "photo"
                elif 'document' in target:
                    media_type = "document"
                    
                msgs.append({
                    "telegram_msg_id": target.get('message_id'),
                    "sender_name": sender.get('username') or sender.get('first_name') or "Unknown",
                    "content": content,
                    "media_type": media_type,
                    "file_meta": file_meta,
                    "chat_id": chat.get('id')
                })
        else:
            print(f"    ❌ Bot API Error: {res.text}")
            
        return msgs

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
