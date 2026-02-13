import os
import asyncio
from telethon import TelegramClient, functions, types, errors
from app.core.config import settings
import time

# Determine absolute path to project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SESSION_FILE = os.path.join(BASE_DIR, "user_session.session")

class UserAgentService:
    """
    Service acting as a real Telegram User (not a bot).
    Used for actions bots cannot perform, like inviting other bots to groups.
    """
    def __init__(self):
        self.api_id = settings.TELEGRAM_API_ID
        self.api_hash = settings.TELEGRAM_API_HASH
        self.session_path = SESSION_FILE
        self.client = None
        self.lock = asyncio.Lock()

    async def start(self):
        """Starts the user client. Requires existing session file or env var."""
        # 1. Check if already connected (Persistent Mode)
        # Check this BEFORE the lock to allow concurrent checks if already connected
        if self.client and self.client.is_connected():
            return True

        async with self.lock:
            # Re-check after acquiring lock in case another task connected it
            if self.client and self.client.is_connected():
                return True

            from app.core.redis_srv import redis_srv
            
            # 2. Check Persistent Cooldown (Redis)
            if redis_srv.is_on_cooldown("user_agent"):
                 ttl = redis_srv.get_cooldown_remaining("user_agent")
                 print(f"    ⏳ [UserAgent] PERSISTENT COOLDOWN: {ttl}s remaining. Skipping.")
                 return False

            # 3. Bypass Read-Only Mount: Copy session to /tmp
            import shutil
            TEMP_SESSION_PATH = "/tmp/user_agent_session"
            
            source_path = None
            if os.path.exists(self.session_path):
                 source_path = self.session_path
            elif os.path.exists(f"{self.session_path}.session"):
                 source_path = f"{self.session_path}.session"
                 
            if source_path:
                try:
                    shutil.copy2(source_path, f"{TEMP_SESSION_PATH}.session")
                except Exception as e:
                    print(f"    ⚠️ [UserAgent] Failed to copy session to tmp: {e}")

            # 4. Check Env Vars (Railway/Cloud)
            if not source_path:
                session_b64 = settings.USER_SESSION_STRING
                if not session_b64:
                    parts = []
                    idx = 1
                    while True:
                        val = os.getenv(f"USER_SESSION_STRING_{idx}")
                        if not val: break
                        parts.append(val)
                        idx += 1
                    if parts:
                        session_b64 = "".join(parts)

                if session_b64:
                    try:
                        import base64
                        decoded = base64.b64decode(session_b64)
                        real_path = f"{TEMP_SESSION_PATH}.session"
                        with open(real_path, "wb") as f:
                            f.write(decoded)
                        source_path = real_path
                    except Exception as e:
                        print(f"    ❌ [UserAgent] Failed to decode session string: {e}")

            if not os.path.exists(f"{TEMP_SESSION_PATH}.session"):
                 print("    ⚠️ [UserAgent] No session file found. Run 'scripts/login_user.py' first.")
                 return False
            
            # 5. Enable WAL Mode & Busy Timeout (Fixes 'database is locked')
            try:
                import sqlite3
                conn = sqlite3.connect(f"{TEMP_SESSION_PATH}.session")
                # WAL allows concurrent reads and one writer without blocking
                conn.execute("PRAGMA journal_mode=WAL")
                # Wait up to 20s for a lock instead of failing instantly
                conn.execute("PRAGMA busy_timeout=20000")
                conn.close()
            except Exception as e:
                print(f"    ⚠️ [UserAgent] Failed to set SQLite PRAGMAs: {e}")
                
            self.client = TelegramClient(TEMP_SESSION_PATH, self.api_id, self.api_hash)
            await self.client.connect()
            
            if not await self.client.is_user_authorized():
                print("    ⚠️ [UserAgent] Session invalid or expired.")
                await self.client.disconnect()
                return False
                
            return True

    async def stop(self):
        async with self.lock:
            if self.client:
                await self.client.disconnect()

    async def invite_bot_to_group(self, bot_username: str, group_id: int | str) -> bool:
        """
        Invites a bot to the specified group (chat/channel).
        """
        async with self.lock:
            if not await self.start():
                return False
            
        try:
            # Ensure identifiers are correct
            # Group ID might need modification depending on type (chat vs channel)
            # -100 prefix is typically for channels/supergroups. Telethon handles standard IDs often.
            
            # Resolve entities
            try:
                bot_entity = await self.client.get_entity(bot_username)
                
                # Handle both integer IDs and usernames
                if str(group_id).lstrip('-').isdigit(): 
                    target = int(group_id)
                else:
                    target = group_id # Assume username string
                    
                group_entity = await self.client.get_entity(target)
            except errors.FloodWaitError as e:
                await self._handle_flood_error(e)
                return False
            except Exception as e:
                print(f"    ❌ [UserAgent] Entity resolution failed: {e}")
                return False

            print(f"    🚀 [UserAgent] Inviting {bot_username} to group...")
            
            # Try AddChatUserRequest (for basic groups) or InviteToChannelRequest (for supergroups/channels)
            from telethon.tl.functions.channels import InviteToChannelRequest
            from telethon.tl.functions.messages import AddChatUserRequest
            
            try:
                # Try as Channel/Supergroup first
                await self.client(InviteToChannelRequest(
                    channel=group_entity,
                    users=[bot_entity]
                ))
                print("    ✅ [UserAgent] Invite successful (Channel/Supergroup).")
                return True
            except Exception as e_channel:
                # Fallback to basic chat
                try:
                    await self.client(AddChatUserRequest(
                        chat_id=group_entity.id,
                        user_id=bot_entity,
                        fwd_limit=0
                    ))
                    print("    ✅ [UserAgent] Invite successful (Basic Chat).")
                    return True
                except Exception as e_chat:
                    print(f"    ❌ [UserAgent] Invite failed: {e_channel} | {e_chat}")
                    return False
                    
        except errors.FloodWaitError as e:
            await self._handle_flood_error(e)
            return False
        except Exception as e:
            print(f"    ❌ [UserAgent] Error: {e}")
            return False

    async def _handle_flood_error(self, e):
        """Logs and sets persistent cooldown for FloodWaitError."""
        from app.core.redis_srv import redis_srv
        wait_seconds = e.seconds
        
        if wait_seconds > 300: # Over 5 minutes is "Serious"
            print(f"\n🛑 [UserAgent] SEVERE FLOOD WAIT: {wait_seconds} seconds (~{wait_seconds//3600}h).")
            print("👉 Feature 'Kickstart' will be disabled until this expires to protect the account.")
            # Set persistent Redis key
            redis_srv.set_cooldown("user_agent", wait_seconds + 60)
        else:
            print(f"    🛑 [UserAgent] FLOOD WAIT: {wait_seconds}s.")
            # Still set it in Redis for worker safety
            redis_srv.set_cooldown("user_agent", wait_seconds + 10)

    async def find_topic_id(self, group_id: int | str, topic_name: str) -> int | None:
        """
        Searches for a forum topic by name using the User Agent.
        Returns topic_id if found, else None.
        """
        async with self.lock:
            if not await self.start():
                return None
            
        try:
            # Resolve entity
            if str(group_id).lstrip('-').isdigit(): 
                target = int(group_id)
            else:
                target = group_id
                
            entity = await self.client.get_entity(target)
            
            # Use GetForumTopicsRequest with query for efficiency
            from telethon.tl.functions.channels import GetForumTopicsRequest
            
            # Search by name
            res = await self.client(GetForumTopicsRequest(
                channel=entity,
                q=topic_name,
                offset_date=0,
                offset_id=0,
                offset_topic=0,
                limit=10 
            ))
            
            if res.topics:
                for topic in res.topics:
                    # Strict match
                    if topic.title == topic_name:
                        print(f"    🔍 [UserAgent] Found existing topic: {topic.title} ({topic.id})")
                        return topic.id
                        
            return None
            
        except Exception as e:
            print(f"    ⚠️ [UserAgent] Find topic failed: {e}")
            return None
        finally:
            pass

    async def cleanup_bots(self, group_id: int | str, whitelist_ids: list[int | str]) -> int:
        """
        Removes all bots from the group that are NOT in the whitelist.
        Returns the number of bots removed.
        """
        async with self.lock:
            if not await self.start():
                return 0
            
        removed_count = 0
        try:
            # Resolve entity
            if str(group_id).lstrip('-').isdigit(): 
                target = int(group_id)
            else:
                target = group_id
            
            entity = await self.client.get_entity(target)
            
            print(f"    🧹 [UserAgent] Starting Bot Cleanup in {entity.title}...")
            
            # Iterate participants
            # We filter for bots only
            from telethon.tl.functions.channels import EditBannedRequest
            from telethon.tl.types import ChatBannedRights
            
            # Prepare rights for kicking (view_messages=True banning kicks them)
            # Actually, standard kick is often just banning with default rights? 
            # Or setting ChatBannedRights(view_messages=True)
            kick_rights = ChatBannedRights(
                until_date=None,
                view_messages=True
            )
            
            # Normalize whitelist: Convert to string, strip whitespace, remove leading '@'
            whitelist_str = [str(x).strip().lstrip('@') for x in whitelist_ids]
            
            async for user in self.client.iter_participants(entity):
                if user.bot:
                    # Check Whitelist
                    if str(user.id) in whitelist_str or user.username in whitelist_str:
                         # print(f"    🛡️ [UserAgent] Safe: {user.username} ({user.id})")
                         continue
                         
                    # Check if it is ME (User Agent) - unlikely as I am not a bot, but safety first
                    if user.is_self: continue
                    
                    print(f"    🚫 [UserAgent] Kicking unauthorized bot: @{user.username} ({user.id})")
                    try:
                        await self.client(EditBannedRequest(
                            channel=entity,
                            participant=user,
                            banned_rights=kick_rights
                        ))
                        removed_count += 1
                        # Unban immediately so they can be re-added later if needed? 
                        # Or just leave them banned?
                        # Usually for "Testing", kicking is enough. 
                        # EditBannedRequest with view_messages=True removes them.
                        # Do we need to Unban? If we re-invite them manually later, we might need to unban.
                        # Let's unban them right after to just "Kick" (Remove) but not "Ban" forever.
                        # To Unban: set rights to empty/default.
                        await self.client(EditBannedRequest(
                            channel=entity,
                            participant=user,
                            banned_rights=ChatBannedRights(until_date=None, view_messages=False)
                        ))
                    except Exception as e_kick:
                        print(f"        ❌ Failed to kick: {e_kick}")

            print(f"    ✨ [UserAgent] Cleanup Complete. Removed {removed_count} bots.")
            return removed_count

        except Exception as e:
            print(f"    ❌ [UserAgent] Cleanup failed: {e}")
            return 0
        finally:
            pass

    async def send_message(self, target: int | str, message: str) -> bool:
        """
        Sends a text message to a target (group/user) as the User Agent.
        """
        async with self.lock:
            if not await self.start():
                return False

        try:
            # Resolve entity
            if str(target).lstrip('-').isdigit():
                entity = int(target)
            else:
                entity = target

            await self.client.send_message(entity, message)
            print(f"    🗣️ [UserAgent] Sent: '{message}'")
            return True
        except Exception as e:
            print(f"    ❌ [UserAgent] Send failed: {e}")
            return False

    async def get_last_message_id(self, group_id: int | str, topic_id: int) -> int | None:
        """
        Fetches the ID of the last message in a specific topic.
        Used for integrity checks.
        """
        async with self.lock:
            if not await self.start():
                return None
            
        try:
            # Resolve entity
            if str(group_id).lstrip('-').isdigit(): 
                target = int(group_id)
            else:
                target = group_id
                
            entity = await self.client.get_entity(target)
            
            # Fetch last message in the topic
            # Telethon's iter_messages with reply_to=topic_id filters for that thread
            messages = await self.client.get_messages(
                entity, 
                limit=1, 
                reply_to=topic_id
            )
            
            if messages:
                # print(f"    🔍 [UserAgent] Last Msg ID in Topic {topic_id}: {messages[0].id}")
                return messages[0].id
                
            return None
            
        except Exception as e:
            print(f"    ❌ [UserAgent] Failed to get last message: {e}")
            return None
        finally:
            pass

    async def get_history(self, group_id: int | str, limit: int) -> list[dict]:
        """
        Fetches message history as a real user.
        Used as a fallback when bots are restricted from GetHistory.
        """
        from telethon.tl.types import Message, MessageMediaPhoto, MessageMediaDocument
        async with self.lock:
            if not await self.start():
                return []
            
        msgs = []
        try:
            # Resolve entity
            if str(group_id).lstrip('-').isdigit(): 
                target = int(group_id)
            else:
                target = group_id
                
            entity = await self.client.get_entity(target)
            
            async for message in self.client.iter_messages(entity, limit=limit):
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
                    "chat_id": entity.id if hasattr(entity, 'id') else group_id
                })
        except Exception as e:
            print(f"    ❌ [UserAgent] Failed to fetch history: {e}")
            
        return msgs

user_agent = UserAgentService()
