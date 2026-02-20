import os
import asyncio
from telethon import TelegramClient, functions, types, errors
from app.core.config import settings
import time
import logging

logger = logging.getLogger("user_agent")

# Determine absolute path to project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Support multiple accounts via Env Var (default: user_session)
SESSION_NAME = os.getenv("USER_SESSION_NAME", "user_session")
SESSION_FILE = os.path.join(BASE_DIR, f"{SESSION_NAME}.session")

class UserAgentService:
    """
    Service acting as a real Telegram User (not a bot).
    Used for actions bots cannot perform, like inviting other bots to groups.
    """
    def __init__(self):
        self.api_id = settings.TELEGRAM_API_ID
        self.api_hash = settings.TELEGRAM_API_HASH
        self.client = None
        self.lock = asyncio.Lock()
        
        # Rotation Logic
        self.sessions = [] # List of session paths
        self.current_index = 0
        self.current_session_name = "unknown"

    def _discover_sessions(self):
        """Scans BASE_DIR for valid .session files."""
        self.sessions = []
        
        # 1. Check Env Var Override first (Single Session Mode)
        env_session = os.getenv("USER_SESSION_NAME")
        if env_session:
            path = os.path.join(BASE_DIR, f"{env_session}.session")
            if os.path.exists(path):
                self.sessions.append(path)
                return

        # 2. Scan Directory
        try:
            for f in os.listdir(BASE_DIR):
                if f.endswith(".session"):
                    # Exclude known non-user sessions
                    if f in ["anon.session", "journal.session"]: continue
                    if f.startswith("bot_"): continue 
                    
                    full_path = os.path.join(BASE_DIR, f)
                    self.sessions.append(full_path)
        except Exception as e:
            logger.error(f"    ❌ [UserAgent] Session discovery failed: {e}")

        # Fallback to default if nothing found (legacy support)
        if not self.sessions:
            default_path = os.path.join(BASE_DIR, "user_session.session")
            # We add it even if it doesn't exist yet, so we can warn later
            self.sessions.append(default_path)
            
        self.sessions.sort() # Ensure deterministic order (e.g. user_1, user_2)
        logger.info(f"    🔄 [UserAgent] Discovered {len(self.sessions)} session(s): {[os.path.basename(s) for s in self.sessions]}")

    async def start(self):
        """
        Starts the user client. 
        Rotates through available sessions to find a usable one.
        """
        if not self.sessions:
            self._discover_sessions()

        # Try up to N times (where N = number of sessions) to find a usable one
        from app.core.redis_srv import redis_srv
        
        attempts = len(self.sessions)
        for _ in range(attempts):
            # 1. Round Robin Selection
            session_path = self.sessions[self.current_index]
            session_name = os.path.splitext(os.path.basename(session_path))[0]
            
            # Increment for next time (even if this one fails, we rotate)
            self.current_index = (self.current_index + 1) % len(self.sessions)
            
            # 2. Check Cooldown for THIS session
            cooldown_key = f"user_agent:{session_name}"
            if redis_srv.is_on_cooldown(cooldown_key):
                 ttl = redis_srv.get_cooldown_remaining(cooldown_key)
                 logger.info(f"    ⏳ [UserAgent] Session '{session_name}' on cooldown ({ttl}s). Rotating...")
                 continue

            # 3. Check if already connected is THIS session
            if self.client and self.client.is_connected():
                # If we are already connected, check if it's the SAME session we just picked?
                # Actually, if we are connected, we might want to keep using it to save handshake overhead?
                # BUT user requested ROTATION.
                # So we should probably disconnect if it's a different session.
                
                # Check current session path
                if getattr(self.client.session, 'filename', '') == session_path:
                    self.current_session_name = session_name # Update tracker
                    return True
                
                # Disconnect old
                session_filename = getattr(self.client.session, 'filename', None)
                await self.client.disconnect()
                if session_filename:
                    self._cleanup_temp_session(session_filename)

            # 4. Initialize & Connect
            # Copy to tmp (Bypass Read-Only)
            import shutil
            import sqlite3
            TEMP_SESSION_PATH = f"/tmp/{session_name}" # Unique tmp path per session
            
            try:
                # Copy logic
                if os.path.exists(session_path):
                    shutil.copy2(session_path, f"{TEMP_SESSION_PATH}.session")
                
                # WAL Mode
                conn = sqlite3.connect(f"{TEMP_SESSION_PATH}.session")
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=20000")
                conn.close()
                
                self.client = TelegramClient(TEMP_SESSION_PATH, self.api_id, self.api_hash)
                await self.client.connect()
                
                if not await self.client.is_user_authorized():
                    logger.warning(f"    ⚠️ [UserAgent] Session '{session_name}' invalid/expired. Skipping.")
                    await self.client.disconnect()
                    self._cleanup_temp_session(f"{TEMP_SESSION_PATH}.session")
                    continue
                    
                self.current_session_name = session_name
                logger.info(f"    ✅ [UserAgent] Connected with session: {session_name}")
                return True

            except Exception as e:
                logger.warning(f"    ⚠️ [UserAgent] Failed to connect '{session_name}': {e}")
                self._cleanup_temp_session(f"{TEMP_SESSION_PATH}.session")
                continue
        
        logger.error("    ❌ [UserAgent] All sessions failed or on cooldown.")
        return False

    async def stop(self):
        async with self.lock:
            if self.client:
                session_filename = getattr(self.client.session, 'filename', None)
                await self.client.disconnect()
                if session_filename:
                    self._cleanup_temp_session(session_filename)

    def _cleanup_temp_session(self, filename: str):
        """Removes the temporary session files from /tmp/"""
        if not filename or not filename.startswith("/tmp/"): return
        try:
            if os.path.exists(filename): os.remove(filename)
            if os.path.exists(filename + "-wal"): os.remove(filename + "-wal")
            if os.path.exists(filename + "-shm"): os.remove(filename + "-shm")
        except OSError as e:
            logger.warning(f"    ⚠️ [UserAgent] Failed to cleanup {filename}: {e}")

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
                logger.error(f"    ❌ [UserAgent] Entity resolution failed: {e}")
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
                logger.info("    ✅ [UserAgent] Invite successful (Channel/Supergroup).")
                return True
            except Exception as e_channel:
                # Fallback to basic chat
                try:
                    await self.client(AddChatUserRequest(
                        chat_id=group_entity.id,
                        user_id=bot_entity,
                        fwd_limit=0
                    ))
                    logger.info("    ✅ [UserAgent] Invite successful (Basic Chat).")
                    return True
                except Exception as e_chat:
                    logger.error(f"    ❌ [UserAgent] Invite failed: {e_channel} | {e_chat}")
                    return False
                    
        except errors.FloodWaitError as e:
            await self._handle_flood_error(e)
            return False
        except Exception as e:
            logger.error(f"    ❌ [UserAgent] Error: {e}")
            return False

    async def _handle_flood_error(self, e):
        """Logs and sets persistent cooldown for FloodWaitError (Per Session)."""
        from app.core.redis_srv import redis_srv
        wait_seconds = e.seconds
        
        # Use current session name for granular cooldown
        current_session = getattr(self, 'current_session_name', 'unknown')
        cooldown_key = f"user_agent:{current_session}"
        
        if wait_seconds > 300: # Over 5 minutes is "Serious"
            logger.warning(f"\n🛑 [UserAgent] SEVERE FLOOD WAIT for '{current_session}': {wait_seconds}s.")
            # Set persistent Redis key
            redis_srv.set_cooldown(cooldown_key, wait_seconds + 60)
        else:
            logger.warning(f"    🛑 [UserAgent] FLOOD WAIT for '{current_session}': {wait_seconds}s.")
            redis_srv.set_cooldown(cooldown_key, wait_seconds + 10)

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
                        logger.info(f"    🔍 [UserAgent] Found existing topic: {topic.title} ({topic.id})")
                        return topic.id
                        
            return None
            
        except Exception as e:
            logger.warning(f"    ⚠️ [UserAgent] Find topic failed: {e}")
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
            
            logger.info(f"    🧹 [UserAgent] Starting Bot Cleanup in {entity.title}...")
            
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
                    
                    logger.info(f"    🚫 [UserAgent] Kicking unauthorized bot: @{user.username} ({user.id})")
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
                        logger.error(f"        ❌ Failed to kick: {e_kick}")

            print(f"    ✨ [UserAgent] Cleanup Complete. Removed {removed_count} bots.")
            return removed_count

        except Exception as e:
            logger.error(f"    ❌ [UserAgent] Cleanup failed: {e}")
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
            logger.info(f"    🗣️ [UserAgent] Sent: '{message}'")
            return True
        except Exception as e:
            logger.error(f"    ❌ [UserAgent] Send failed: {e}")
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
            logger.error(f"    ❌ [UserAgent] Failed to get last message: {e}")
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
            logger.error(f"    ❌ [UserAgent] Failed to fetch history: {e}")
            
        return msgs

user_agent = UserAgentService()
