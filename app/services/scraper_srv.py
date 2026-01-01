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
        Strategy 1: Telethon (GetHistory) - Best for deep history. Often restricted.
        Strategy 2: ID Bruteforce (GetMessages) - Uses finding from Strategy 3 to scan backwards.
        Strategy 3: Bot API (getUpdates) - Fallback. Finds recent IDs (needed for Strat 2).
        """
        scraped_messages = []
        unique_ids = set()
        
        # Strategy 1: Telethon (GetHistory)
        try:
            telethon_msgs = await self._scrape_via_telethon(bot_token, chat_id, limit)
            for m in telethon_msgs:
                if m['telegram_msg_id'] not in unique_ids:
                    scraped_messages.append(m)
                    unique_ids.add(m['telegram_msg_id'])
            
            if len(scraped_messages) > 10: # If we got a decent amount, likely success
                print(f"✨ [Scraper] Telethon normal dump success: {len(scraped_messages)} messages.")
                return scraped_messages
        except Exception as e:
            print(f"⚠️ [Scraper] Telethon history dump restricted/failed ({e}).")

        # Get 'Anchor' ID from Bot API (Strategy 3) to enable Strategy 2
        anchor_id = 0
        try:
            api_msgs = self._scrape_via_bot_api(bot_token)
            for m in api_msgs:
                # Add these finding too
                if m['telegram_msg_id'] not in unique_ids:
                    # Filter for checking chat_id if we have one?
                    # Bot API getUpdates is global, so we check if it matches target chat
                    # OR if target chat is unknown, we take all? 
                    # Here we target specific chat_id.
                    if str(m.get('chat_id')) == str(chat_id):
                        scraped_messages.append(m)
                        unique_ids.add(m['telegram_msg_id'])
                        if m['telegram_msg_id'] > anchor_id:
                            anchor_id = m['telegram_msg_id']
            print(f"    [Scraper] Found anchor ID {anchor_id} from Bot API.")
        except Exception as e:
            print(f"    [Scraper] Bot API fallback failed: {e}")

        # Strategy 2: Blind ID Bruteforce (Telethon GetMessages)
        # If we found an anchor, we can look backwards!
        if anchor_id > 0:
            try:
                print(f"🔨 [Scraper] Attempting Blind ID Bruteforce from ID {anchor_id} downwards...")
                brute_msgs = await self._scrape_via_id_bruteforce(bot_token, chat_id, anchor_id, limit=500)
                for m in brute_msgs:
                    if m['telegram_msg_id'] not in unique_ids:
                        scraped_messages.append(m)
                        unique_ids.add(m['telegram_msg_id'])
                print(f"✨ [Scraper] Bruteforce added {len(brute_msgs)} messages.")
            except Exception as e:
                print(f"❌ [Scraper] Bruteforce failed: {e}")

        return scraped_messages

    async def _scrape_via_id_bruteforce(self, bot_token: str, chat_id: int, start_id: int, limit: int) -> List[Dict]:
        """
        Fetches messages by ID batches (GetMessages) instead of listing history (GetHistory).
        Bypasses 'API restricted' error for listing history.
        """
        session_name = f"session_{hash(bot_token)}"
        client = TelegramClient(session_name, self.api_id, self.api_hash)
        msgs = []
        try:
            await client.start(bot_token=bot_token)
            
            # Create batches of IDs to check
            # Scan backwards from start_id
            # e.g. 1000 IDs total
            ids_to_check = []
            for i in range(start_id, max(0, start_id - limit), -1):
                ids_to_check.append(i)
            
            # Chunk into 100s
            chunk_size = 100
            for i in range(0, len(ids_to_check), chunk_size):
                batch = ids_to_check[i:min(i + chunk_size, len(ids_to_check))]
                try:
                    # Request specific IDs
                    # check if we can filter by entity? get_messages(entity, ids=...)
                    # We need the entity (Peer).
                    # 'chat_id' might be int. Telethon needs input entity.
                    # We can try passing chat_id directly if we have seen it?
                    # Or 'get_messages(ids=...)' gets from ANY chat? No, usually needs entity.
                    # Warning: If we don't have the entity in cache, this might fail.
                    # But if we logged in and 'getUpdates' saw the chat, maybe?
                    # Let's try passing the chat_id.
                    
                    found = await client.get_messages(chat_id, ids=batch)
                    
                    for message in found:
                        if not message or not isinstance(message, Message): continue
                        
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
                            "chat_id": chat_id
                        })
                except Exception as e:
                    # print(f"Batch fail: {e}")
                    pass
        finally:
            await client.disconnect()
        return msgs

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
