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
        self._refresher_task = None

    def _discover_sessions(self):
        """Scans BASE_DIR and telegram_accounts DB for valid .session files."""
        new_sessions = set() # Use set to avoid duplicates
        
        # 1. Check Env Var Override first (Single Session Mode)
        env_session = os.getenv("USER_SESSION_NAME")
        if env_session:
            path = os.path.join(BASE_DIR, f"{env_session}.session")
            if os.path.exists(path):
                new_sessions.add(path)
                self.sessions = sorted(list(new_sessions))
                return

        # 2. Scan Directory (Project Root)
        search_dirs = [BASE_DIR, os.path.join(BASE_DIR, "sessions")] # Still check sessions/ for temp legacy
        for sdir in search_dirs:
            if not os.path.exists(sdir): continue
            try:
                for f in os.listdir(sdir):
                    if f.endswith(".session"):
                        # Exclude known non-user sessions
                        if f in ["anon.session", "journal.session"]: continue
                        if f.startswith("bot_"): continue 
                        
                        full_path = os.path.abspath(os.path.join(sdir, f))
                        new_sessions.add(full_path)
            except Exception as e:
                logger.error(f"    ❌ [UserAgent] Directory scan failed for {sdir}: {e}")

        # 3. Discover via Database (Requirement-aligned tracking)
        try:
            from app.core.database import db
            res = db.table("telegram_accounts").select("session_path").eq("status", "active").execute()
            for row in res.data:
                path = row.get("session_path")
                if path:
                    # Double check existence
                    if os.path.exists(path):
                        new_sessions.add(os.path.abspath(path))
                    else:
                        # Maybe it was relative?
                        rel_path = os.path.join(BASE_DIR, os.path.basename(path))
                        if os.path.exists(rel_path):
                            new_sessions.add(os.path.abspath(rel_path))
        except Exception as e:
            pass

        # Fallback to default if nothing found (legacy support)
        if not new_sessions:
            default_path = os.path.abspath(os.path.join(BASE_DIR, "user_session.session"))
            new_sessions.add(default_path)
            
        final_list = sorted(list(new_sessions))
        
        # Log only if the session list has changed
        if final_list != self.sessions:
            logger.info(f"    🔄 [UserAgent] Discovered {len(final_list)} session(s): {[os.path.basename(s) for s in final_list]}")
            
        self.sessions = final_list

    async def _session_refresher_loop(self):
        """Background loop to periodically scan for new .session files."""
        while True:
            await asyncio.sleep(60)
            self._discover_sessions()

    async def start(self):
        """
        Starts the user client. 
        Rotates through available sessions to find a usable one.
        """
        if not self.sessions:
            self._discover_sessions()
            
        # Start background refresher if not already running
        if self._refresher_task is None:
            self._refresher_task = asyncio.create_task(self._session_refresher_loop())

        # Ensure all broadcaster bots are in the monitor group
        asyncio.create_task(self._ensure_monitor_bots_membership())

        # Try up to N times (where N = number of sessions) to find a usable one
        from app.core.redis_srv import redis_srv
        
        attempts = len(self.sessions)
        for _ in range(attempts):
            # 1. Round Robin Selection (Global Redis Counter)
            global_idx = redis_srv.get_next_rotation_index("user_agent", attempts)
            
            session_path = self.sessions[global_idx]
            session_name = os.path.splitext(os.path.basename(session_path))[0]
            
            # Update local reference
            self.current_index = global_idx
            
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

    async def cleanup_bots(self, group_id: int | str, whitelist_ids: list[int | str] = None, only_non_admins: bool = True) -> int:
        """
        Removes bots from the group.
        - whitelist_ids: Bots to ignore.
        - only_non_admins: If True, only kicks bots that are NOT admins.
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
            
            # Prepare rights for kicking (view_messages=True banning kicks them)
            from telethon.tl.functions.channels import EditBannedRequest, GetParticipantRequest
            from telethon.tl.types import ChatBannedRights, ChannelParticipantAdmin, ChannelParticipantCreator
            
            kick_rights = ChatBannedRights(
                until_date=None,
                view_messages=True
            )
            
            # Normalize whitelist
            whitelist_str = [str(x).strip().lstrip('@') for x in (whitelist_ids or [])]
            
            async for user in self.client.iter_participants(entity):
                if user.bot:
                    # 1. Check Whitelist
                    if str(user.id) in whitelist_str or user.username in whitelist_str:
                         continue
                         
                    # 2. Check Admin Status if requested
                    if only_non_admins:
                        try:
                            participant = await self.client(GetParticipantRequest(channel=entity, participant=user))
                            if isinstance(participant.participant, (ChannelParticipantAdmin, ChannelParticipantCreator)):
                                # logger.info(f"    🛡️ [UserAgent] Skipping admin bot: @{user.username}")
                                continue
                        except Exception:
                            pass

                    # Check if it is ME (User Agent)
                    if user.is_self: continue
                    
                    logger.info(f"    🚫 [UserAgent] Kicking bot: @{user.username} ({user.id})")
                    try:
                        await self.client(EditBannedRequest(
                            channel=entity,
                            participant=user,
                            banned_rights=kick_rights
                        ))
                        removed_count += 1
                        
                        # Unban immediately to clear from "Removed Users" list in UI 
                        # and allow re-inviting if needed.
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

    async def clear_removed_users(self, group_id: int | str) -> int:
        """
        Iterates over the 'Kicked' participants list and unbans them.
        This clears the "Removed Users" list in Telegram groups.
        """
        async with self.lock:
            if not await self.start():
                return 0
                
        cleared_count = 0
        try:
            if str(group_id).lstrip('-').isdigit():
                target = int(group_id)
            else:
                target = group_id
            entity = await self.client.get_entity(target)
            
            from telethon.tl.types import ChannelParticipantsKicked, ChatBannedRights
            from telethon.tl.functions.channels import EditBannedRequest
            
            logger.info(f"    🧹 [UserAgent] Clearing Removed Users list in {entity.title}...")
            
            async for user in self.client.iter_participants(entity, filter=ChannelParticipantsKicked()):
                try:
                    # Setting view_messages=False effectively unbans/clears them
                    await self.client(EditBannedRequest(
                        channel=entity,
                        participant=user,
                        banned_rights=ChatBannedRights(until_date=None, view_messages=False)
                    ))
                    cleared_count += 1
                except Exception as e:
                    logger.warning(f"    ⚠️ [UserAgent] Could not clear user {user.id}: {e}")
            
            logger.info(f"    ✨ [UserAgent] Cleared {cleared_count} users from removed list.")
            return cleared_count
        except Exception as e:
            logger.error(f"    ❌ [UserAgent] clear_removed_users failed: {e}")
            return 0
        finally:
            pass

    async def delete_old_messages(self, group_id: int | str, age_hours: int, topic_id: int | None = None) -> int:
        """
        Deletes messages older than age_hours in a specific topic (or General if topic_id is None).
        Returns the number of messages deleted.
        """
        async with self.lock:
            if not await self.start():
                return 0

        import datetime
        from telethon.tl.types import Message
        
        deleted_count = 0
        try:
            if str(group_id).lstrip('-').isdigit():
                target = int(group_id)
            else:
                target = group_id
            entity = await self.client.get_entity(target)
            
            now = datetime.datetime.now(datetime.timezone.utc)
            cutoff = now - datetime.timedelta(hours=age_hours)
            
            logger.info(f"    🧹 [UserAgent] Cleaning up messages older than {age_hours}h in topic {topic_id or 'General'}...")
            
            # Use iter_messages with reply_to=topic_id for specific topics
            # If topic_id is None, it targets General (thread-less) in many Supergroups
            # or simply the main chat.
            async for message in self.client.iter_messages(entity, reply_to=topic_id):
                if not isinstance(message, Message): continue
                
                # Check if message is older than cutoff
                if message.date < cutoff:
                    try:
                        await self.client.delete_messages(entity, [message.id])
                        deleted_count += 1
                    except Exception as e:
                        logger.warning(f"    ⚠️ [UserAgent] Failed to delete message {message.id}: {e}")
                        
            logger.info(f"    ✨ [UserAgent] Deleted {deleted_count} messages.")
            return deleted_count
            
        except Exception as e:
            logger.error(f"    ❌ [UserAgent] delete_old_messages failed: {e}")
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

    async def check_membership(self, group_id: int | str, user_identifier: str | int) -> dict | None:
        """
        Checks if a user/bot is a member of the group.
        Returns participant info dict if found, None if not a member.
        """
        async with self.lock:
            if not await self.start():
                return None

        try:
            # Resolve group
            if str(group_id).lstrip('-').isdigit():
                target = int(group_id)
            else:
                target = group_id
            group_entity = await self.client.get_entity(target)

            # Resolve user/bot
            if str(user_identifier).lstrip('-').isdigit():
                user_target = int(user_identifier)
            else:
                user_target = user_identifier
            
            try:
                user_entity = await self.client.get_entity(user_target)
            except Exception:
                logger.warning(f"    ⚠️ [UserAgent] Could not resolve entity: {user_identifier}")
                return None

            # Check if user is in the group
            from telethon.tl.functions.channels import GetParticipantRequest
            try:
                result = await self.client(GetParticipantRequest(
                    channel=group_entity,
                    participant=user_entity
                ))
                return {
                    "id": getattr(user_entity, 'id', 0),
                    "username": getattr(user_entity, 'username', None),
                    "is_admin": hasattr(result.participant, 'admin_rights') and result.participant.admin_rights is not None
                }
            except Exception as e:
                err_str = str(e)
                if "USER_NOT_PARTICIPANT" in err_str or "400" in err_str:
                    return None  # Not a member
                logger.warning(f"    ⚠️ [UserAgent] Membership check error: {e}")
                return None

        except Exception as e:
            logger.error(f"    ❌ [UserAgent] check_membership failed: {e}")
            return None

    async def promote_to_admin(self, group_id: int | str, user_identifier: str | int, title: str = "Bot") -> bool:
        """
        Promotes a user/bot to admin in the specified group with full permissions.
        """
        async with self.lock:
            if not await self.start():
                return False

        try:
            # Resolve entities
            if str(group_id).lstrip('-').isdigit():
                target = int(group_id)
            else:
                target = group_id
            group_entity = await self.client.get_entity(target)

            if str(user_identifier).lstrip('-').isdigit():
                user_target = int(user_identifier)
            else:
                user_target = user_identifier
            user_entity = await self.client.get_entity(user_target)

            from telethon.tl.functions.channels import EditAdminRequest
            from telethon.tl.types import ChatAdminRights

            admin_rights = ChatAdminRights(
                change_info=True,
                post_messages=True,
                edit_messages=True,
                delete_messages=True,
                ban_users=True,
                invite_users=True,
                pin_messages=True,
                manage_call=True,
                other=True,
                manage_topics=True,
            )

            await self.client(EditAdminRequest(
                channel=group_entity,
                user_id=user_entity,
                admin_rights=admin_rights,
                rank=title
            ))
            logger.info(f"    👑 [UserAgent] Promoted {user_identifier} to admin in group.")
            return True

        except errors.FloodWaitError as e:
            await self._handle_flood_error(e)
            return False
        except Exception as e:
            logger.error(f"    ❌ [UserAgent] Promote failed for {user_identifier}: {e}")
            return False

    async def _ensure_monitor_bots_membership(self):
        """Checks and ensures all broadcaster bots are in the monitor group."""
        try:
            tokens = settings.bot_tokens
            group_id = settings.MONITOR_GROUP_ID
            
            if not tokens or not group_id:
                return

            logger.info("    🐶 [UserAgent] Checking broadcaster bots membership...")
            
            for token in tokens:
                try:
                    # Resolve bot ID from token (pre-calculated or fetched)
                    bot_id = int(token.split(':')[0])
                    
                    # 1. Check membership
                    member = await self.check_membership(group_id, bot_id)
                    if not member:
                        logger.warning(f"    ⚠️ [UserAgent] Bot {bot_id} NOT in monitor group. Inviting...")
                        # 2. Invite bot
                        # We need the username if possible, but ID often works for invite
                        # Fetch username if not cached
                        try:
                            from telegram import Bot
                            temp_bot = Bot(token)
                            me = await temp_bot.get_me()
                            success = await self.invite_bot_to_group(me.username, group_id)
                            if success:
                                logger.info(f"    ✅ [UserAgent] Successfully invited @{me.username} to group.")
                                # 3. Promote to admin (optional but usually needed for topics)
                                await self.promote_to_admin(group_id, me.username)
                        except Exception as e_bot:
                            logger.error(f"    ❌ [UserAgent] Failed to invite bot {bot_id}: {e_bot}")
                    else:
                        # Ensure it's admin if it's already a member
                        if not member.get("is_admin"):
                            logger.info(f"    👑 [UserAgent] Bot {bot_id} is member but not admin. Promoting...")
                            await self.promote_to_admin(group_id, bot_id)
                        
                except Exception as e_tok:
                    logger.error(f"    ❌ [UserAgent] error checking token {token[:10]}...: {e_tok}")
                    
        except Exception as e:
            logger.error(f"    ❌ [UserAgent] _ensure_monitor_bots_membership fatal error: {e}")

user_agent = UserAgentService()
