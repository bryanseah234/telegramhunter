import os
import asyncio
from telethon import TelegramClient, functions, types, errors
from telethon.errors import SecurityError, FloodWaitError, AuthKeyUnregisteredError
from app.core.config import settings
import time
import logging
import socket
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("user_agent")

# MTProto conflict backoff (seconds) -- kept short since connections are brief
_MTPROTO_CONFLICT_BACKOFF = 10
_MTPROTO_MAX_RETRIES = 3

# Determine absolute path to project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")

# Support multiple accounts via Env Var (default: user_session)
SESSION_NAME = os.getenv("USER_SESSION_NAME", "user_session")
SESSION_FILE = os.path.join(BASE_DIR, f"{SESSION_NAME}.session")


def _is_session_file_healthy(path: str) -> bool:
    """
    Lightweight integrity check for a Telethon .session file (SQLite database).

    Telethon sessions are SQLite files. A corrupt or partial file will cause
    TelegramClient to raise struct.unpack / DatabaseError on first use, crashing
    the worker process. This check opens the file as SQLite and reads the sessions
    table, which is enough to confirm the file is not corrupt.

    On failure, renames the file to .session.corrupt.{timestamp} so it won't
    be picked up on subsequent scans, and logs a warning.
    """
    import sqlite3
    import time as _time

    if not os.path.exists(path):
        return False

    try:
        conn = sqlite3.connect(path, timeout=5)
        cursor = conn.cursor()
        # Telethon writes a 'sessions' table; reading it verifies file integrity
        cursor.execute("SELECT dc_id FROM sessions LIMIT 1")
        conn.close()
        return True
    except Exception as e:
        # Corrupt / truncated / not a Telethon session file
        logger.warning(
            f"    [UserAgent] Session file appears corrupt: {path} ({e}) -- "
            f"renaming to .corrupt and skipping."
        )
        try:
            corrupt_path = f"{path}.corrupt.{int(_time.time())}"
            os.rename(path, corrupt_path)
            logger.warning(f"    [UserAgent] Moved corrupt session to: {corrupt_path}")
        except Exception as e_rename:
            logger.error(f"    [UserAgent] Could not rename corrupt session {path}: {e_rename}")
        return False


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
        self._ensure_task = None
        self._session_lock_key = None
        # Instance ID must be stable across container restarts.
        # Using a fixed process name (derived from env or hardcoded fallback) instead of hostname
        # because Docker generates new hostnames on each container recreate.
        import os as _os
        self._instance_id = _os.getenv("WORKER_INSTANCE_ID", "worker-scrape")  # Override via env if needed
        self._current_phone = None

    def _discover_sessions(self):
        """Scans BASE_DIR and telegram_accounts DB for valid .session files."""
        new_sessions = set() # Use set to avoid duplicates
        
        # 1. Check Env Var Override first (Single Session Mode)
        env_session = os.getenv("USER_SESSION_NAME")
        if env_session:
            path = os.path.join(SESSIONS_DIR, f"{env_session}.session")
            if os.path.exists(path):
                new_sessions.add(path)
                self.sessions = sorted(list(new_sessions))
                return

        # 2. Scan Directory (sessions/)
        if not os.path.exists(SESSIONS_DIR):
            os.makedirs(SESSIONS_DIR, exist_ok=True)
        try:
            for f in os.listdir(SESSIONS_DIR):
                if f.endswith(".session"):
                    if f in ["anon.session", "journal.session"]:
                        continue
                    if f.startswith("bot_"):
                        continue
                    full_path = os.path.abspath(os.path.join(SESSIONS_DIR, f))
                    if _is_session_file_healthy(full_path):
                        new_sessions.add(full_path)
        except Exception as e:
            logger.error(f"    ❌ [UserAgent] Directory scan failed for {SESSIONS_DIR}: {e}")

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
            logger.warning(f"[UserAgent] DB session discovery failed: {e}")

        # Fallback to default if nothing found (legacy support)
        if not new_sessions:
            default_path = os.path.abspath(os.path.join(SESSIONS_DIR, "user_session.session"))
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
        On-demand pattern: caller MUST call _disconnect() when done.
        """
        if not self.sessions:
            self._discover_sessions()
            
        # Start background refresher if not already running
        if self._refresher_task is None:
            self._refresher_task = asyncio.create_task(self._session_refresher_loop())

        if self._ensure_task is None or self._ensure_task.done():
            from app.core.redis_srv import redis_srv
            if not redis_srv.is_on_cooldown("user_agent:ensure_membership"):
                self._ensure_task = asyncio.create_task(self._ensure_monitor_bots_membership())
                redis_srv.set_cooldown("user_agent:ensure_membership", 6 * 3600)

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
            # Use distinct key namespaces: cooldown vs lock.
            # Previously both used `user_agent:{session_name}` which caused
            # release_lock() to also clear the cooldown -- meaning FloodWait
            # cooldowns were silently wiped on every disconnect.
            cooldown_key = f"user_agent:cooldown:{session_name}"
            if redis_srv.is_on_cooldown(cooldown_key):
                 ttl = redis_srv.get_cooldown_remaining(cooldown_key)
                 logger.info(f"    ⏳ [UserAgent] Session '{session_name}' on cooldown ({ttl}s). Rotating...")
                 continue

            lock_key = f"user_agent:lock:{session_name}"
            if not redis_srv.acquire_lock(lock_key, 600):
                logger.info(f"    🔒 [UserAgent] Session '{session_name}' locked by another worker. Rotating...")
                continue
            self._session_lock_key = lock_key
            self._current_phone = None
            if not await self._acquire_db_lease(session_path):
                if self._session_lock_key:
                    redis_srv.release_lock(self._session_lock_key)
                    self._session_lock_key = None


            # 3. Check if already connected is THIS session
            if self.client and self.client.is_connected():
                if getattr(self.client.session, 'filename', '') == session_path:
                    self.current_session_name = session_name # Update tracker
                    return True
                
                # Disconnect old
                session_filename = getattr(self.client.session, 'filename', None)
                await self.client.disconnect()
                if session_filename:
                    self._cleanup_temp_session(session_filename)
                if self._session_lock_key:
                    redis_srv.release_lock(self._session_lock_key)
                    self._session_lock_key = None
                await self._release_db_lease()

            # 4. Initialize & Connect
            import shutil
            import sqlite3
            TEMP_SESSION_PATH = f"/tmp/{session_name}" # Unique tmp path per session
            
            try:
                if os.path.exists(session_path):
                    shutil.copy2(session_path, f"{TEMP_SESSION_PATH}.session")
                
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
                    redis_srv.incr_key(f"user_agent_fail:{session_name}", 3600)
                    if self._session_lock_key:
                        redis_srv.release_lock(self._session_lock_key)
                        self._session_lock_key = None
                    await self._release_db_lease()
                    continue
                    
                self.current_session_name = session_name
                redis_srv.reset_key(f"user_agent_fail:{session_name}")
                logger.info(f"    ✅ [UserAgent] Connected with session: {session_name}")
                return True

            except SecurityError as e:
                if "Too many messages had to be ignored" in str(e):
                    logger.warning(
                        f"    🔴 [UserAgent] MTProto conflict detected for '{session_name}': {e}. "
                        f"Backing off for {_MTPROTO_CONFLICT_BACKOFF}s..."
                    )
                    try:
                        await self.client.disconnect()
                    except Exception:
                        pass
                    self._cleanup_temp_session(f"{TEMP_SESSION_PATH}.session")
                    redis_srv.incr_key(f"user_agent_fail:{session_name}", 3600)
                    redis_srv.set_cooldown(cooldown_key, _MTPROTO_CONFLICT_BACKOFF + 5)
                    if self._session_lock_key:
                        redis_srv.release_lock(self._session_lock_key)
                        self._session_lock_key = None
                    await self._release_db_lease()
                    await asyncio.sleep(_MTPROTO_CONFLICT_BACKOFF)
                    continue
                raise
            except Exception as e:
                logger.warning(f"    ⚠️ [UserAgent] Failed to connect '{session_name}': {e}")
                self._cleanup_temp_session(f"{TEMP_SESSION_PATH}.session")
                fail_count = redis_srv.incr_key(f"user_agent_fail:{session_name}", 3600)
                if fail_count >= _MTPROTO_MAX_RETRIES:
                    redis_srv.set_cooldown(cooldown_key, 120)
                if self._session_lock_key:
                    redis_srv.release_lock(self._session_lock_key)
                    self._session_lock_key = None
                await self._release_db_lease()
                continue
        
        logger.error("    ❌ [UserAgent] All sessions failed or on cooldown.")
        return False

    async def _disconnect(self):
        try:
            if self.client and self.client.is_connected():
                session_filename = getattr(self.client.session, 'filename', None)
                await self.client.disconnect()
                if session_filename:
                    self._cleanup_temp_session(session_filename)
            if self._session_lock_key:
                from app.core.redis_srv import redis_srv
                redis_srv.release_lock(self._session_lock_key)
                self._session_lock_key = None
            await self._release_db_lease()
        except Exception as e:
            logger.warning(f"    ⚠️ [UserAgent] Error during disconnect: {e}")

    async def _acquire_db_lease(self, session_path: str) -> bool:
        try:
            from app.core.database import db
            abs_path = os.path.abspath(session_path)
            res = await asyncio.to_thread(
                lambda: db.table("telegram_accounts").select("phone,locked_by,locked_until").eq("session_path", abs_path).limit(1).execute()
            )
            if not res.data:
                return True
            row = res.data[0]
            phone = row.get("phone")
            if not phone:
                return True
            
            # Check if we already hold this lease (same instance_id and not expired)
            current_holder = row.get("locked_by")
            current_until = row.get("locked_until")
            if current_holder == self._instance_id and current_until:
                # We already hold it -- just refresh the TTL
                lease_until = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
                await asyncio.to_thread(
                    lambda: db.table("telegram_accounts")
                        .update({"locked_until": lease_until})
                        .eq("phone", phone)
                        .eq("locked_by", self._instance_id)
                        .execute()
                )
                self._current_phone = phone
                return True
            
            # Try to acquire fresh lease (only if unlocked or expired)
            lease_until = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            updated = await asyncio.to_thread(
                lambda: db.table("telegram_accounts")
                    .update({"locked_by": self._instance_id, "locked_until": lease_until})
                    .eq("phone", phone)
                    .or_("locked_until.is.null,locked_until.lt.now()")
                    .execute()
            )
            if updated.data:
                self._current_phone = phone
                return True
            return False
        except Exception as e:
            logger.warning(f"    ⚠️ [UserAgent] DB lease failed: {e}")
            return True

    async def _release_db_lease(self):
        if not self._current_phone:
            return
        try:
            from app.core.database import db
            await asyncio.to_thread(
                lambda: db.table("telegram_accounts")
                    .update({"locked_by": None, "locked_until": None})
                    .eq("phone", self._current_phone)
                    .eq("locked_by", self._instance_id)
                    .execute()
            )
        except Exception as e:
            logger.warning(f"    ⚠️ [UserAgent] DB lease release failed: {e}")
        finally:
            self._current_phone = None

    async def stop(self):
        """Graceful shutdown -- disconnect and cancel background tasks."""
        async with self.lock:
            await self._disconnect()
            if self._refresher_task and not self._refresher_task.done():
                self._refresher_task.cancel()
                try:
                    await self._refresher_task
                except asyncio.CancelledError:
                    pass
                self._refresher_task = None

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
                bot_entity = await self.client.get_entity(bot_username)
                if str(group_id).lstrip('-').isdigit(): 
                    target = int(group_id)
                else:
                    target = group_id
                group_entity = await self.client.get_entity(target)

                logger.info(f"    🚀 [UserAgent] Inviting {bot_username} to group...")
                from telethon.tl.functions.channels import InviteToChannelRequest
                from telethon.tl.functions.messages import AddChatUserRequest
                
                try:
                    await self.client(InviteToChannelRequest(channel=group_entity, users=[bot_entity]))
                    logger.info("    ✅ [UserAgent] Invite successful (Channel/Supergroup).")
                    return True
                except Exception:
                    try:
                        await self.client(AddChatUserRequest(chat_id=group_entity.id, user_id=bot_entity, fwd_limit=0))
                        logger.info("    ✅ [UserAgent] Invite successful (Basic Chat).")
                        return True
                    except Exception as e_chat:
                        logger.error(f"    ❌ [UserAgent] Invite failed: {e_chat}")
                        return False
            except errors.FloodWaitError as e:
                await self._handle_flood_error(e)
                return False
            except Exception as e:
                logger.error(f"    ❌ [UserAgent] Error: {e}")
                return False
            finally:
                await self._disconnect()

    async def kick_bot_from_group(self, bot_username: str, group_id: int | str) -> bool:
        """
        Kicks/bans then unbans a bot from the monitor group.
        Used after Matkap-style forwarding to remove the victim bot
        so it cannot see further group messages (OPSEC cleanup).
        """
        async with self.lock:
            if not await self.start():
                return False
            try:
                bot_entity = await self.client.get_entity(bot_username)
                if str(group_id).lstrip("-").isdigit():
                    target = int(group_id)
                else:
                    target = group_id
                group_entity = await self.client.get_entity(target)

                from telethon.tl.functions.channels import EditBannedRequest
                from telethon.tl.types import ChatBannedRights
                from datetime import datetime, timezone, timedelta

                # Ban (kicks non-admin bots immediately)
                await self.client(EditBannedRequest(
                    channel=group_entity,
                    participant=bot_entity,
                    banned_rights=ChatBannedRights(
                        until_date=datetime.now(timezone.utc) + timedelta(seconds=30),
                        view_messages=True,
                    )
                ))
                # Unban so the bot can be re-invited in the future if needed
                await self.client(EditBannedRequest(
                    channel=group_entity,
                    participant=bot_entity,
                    banned_rights=ChatBannedRights(until_date=None)
                ))
                logger.info(f"    ✅ [UserAgent] Kicked @{bot_username} from group (ban+unban).")
                return True
            except errors.FloodWaitError as e:
                await self._handle_flood_error(e)
                return False
            except Exception as e:
                logger.warning(f"    ⚠️ [UserAgent] kick_bot_from_group failed for @{bot_username}: {e}")
                return False
            finally:
                await self._disconnect()

    async def _handle_flood_error(self, e):
        """Logs and sets persistent cooldown for FloodWaitError (Per Session)."""
        from app.core.redis_srv import redis_srv
        wait_seconds = e.seconds
        current_session = getattr(self, 'current_session_name', 'unknown')
        # Must use the same namespace as the cooldown check in start()
        cooldown_key = f"user_agent:cooldown:{current_session}"
        if wait_seconds > 300:
            logger.warning(f"\n🛑 [UserAgent] SEVERE FLOOD WAIT for '{current_session}': {wait_seconds}s.")
            redis_srv.set_cooldown(cooldown_key, wait_seconds + 60)
        else:
            logger.warning(f"    🛑 [UserAgent] FLOOD WAIT for '{current_session}': {wait_seconds}s.")
            redis_srv.set_cooldown(cooldown_key, wait_seconds + 10)

    async def find_topic_id(self, group_id: int | str, topic_name: str) -> int | None:
        async with self.lock:
            if not await self.start(): return None
            try:
                if str(group_id).lstrip('-').isdigit(): target = int(group_id)
                else: target = group_id
                entity = await self.client.get_entity(target)
                from telethon.tl.functions.channels import GetForumTopicsRequest
                res = await self.client(GetForumTopicsRequest(channel=entity, q=topic_name, offset_date=0, offset_id=0, offset_topic=0, limit=10))
                if res.topics:
                    for topic in res.topics:
                        if topic.title == topic_name:
                            logger.info(f"    🔍 [UserAgent] Found existing topic: {topic.title} ({topic.id})")
                            return topic.id
                return None
            except Exception as e:
                logger.warning(f"    ⚠️ [UserAgent] Find topic failed: {e}")
                return None
            finally: await self._disconnect()

    async def check_membership(self, group_id: int | str, user_identifier: str | int) -> dict | None:
        async with self.lock:
            if not await self.start(): return None
            try:
                if str(group_id).lstrip('-').isdigit(): target = int(group_id)
                else: target = group_id
                group_entity = await self.client.get_entity(target)
                if str(user_identifier).lstrip('-').isdigit(): user_target = int(user_identifier)
                else: user_target = user_identifier
                try: user_entity = await self.client.get_entity(user_target)
                except Exception: return None
                from telethon.tl.functions.channels import GetParticipantRequest
                try:
                    result = await self.client(GetParticipantRequest(channel=group_entity, participant=user_entity))
                    return {
                        "id": getattr(user_entity, 'id', 0),
                        "username": getattr(user_entity, 'username', None),
                        "is_admin": hasattr(result.participant, 'admin_rights') and result.participant.admin_rights is not None
                    }
                except Exception as e:
                    if "USER_NOT_PARTICIPANT" in str(e) or "400" in str(e): return None
                    return None
            except Exception: return None
            finally: await self._disconnect()

    async def promote_to_admin(self, group_id: int | str, user_identifier: str | int, title: str = "Admin", anonymous: bool = True) -> bool:
        async with self.lock:
            if not await self.start(): return False
            try:
                if str(group_id).lstrip('-').isdigit(): target = int(group_id)
                else: target = group_id
                group_entity = await self.client.get_entity(target)
                if str(user_identifier).lstrip('-').isdigit(): user_target = int(user_identifier)
                else: user_target = user_identifier
                user_entity = await self.client.get_entity(user_target)
                from telethon.tl.functions.channels import EditAdminRequest
                from telethon.tl.types import ChatAdminRights
                admin_rights = ChatAdminRights(
                    change_info=True, post_messages=True, edit_messages=True, delete_messages=True,
                    ban_users=True, invite_users=True, pin_messages=True, manage_call=True,
                    other=True, manage_topics=True, anonymous=anonymous
                )
                await self.client(EditAdminRequest(channel=group_entity, user_id=user_entity, admin_rights=admin_rights, rank=title))
                logger.info(f"    👑 [UserAgent] Promoted {user_identifier} to admin (anon={anonymous}) in group.")
                return True
            except errors.FloodWaitError as e:
                await self._handle_flood_error(e)
                return False
            except Exception as e:
                logger.error(f"    ❌ [UserAgent] Promote failed for {user_identifier}: {e}")
                return False
            finally: await self._disconnect()

    async def _connect_to_session(self, session_path: str) -> bool:
        """Internal helper to connect to a specific session file."""
        session_name = os.path.splitext(os.path.basename(session_path))[0]
        import shutil
        import sqlite3
        TEMP_SESSION_PATH = f"/tmp/setup_{session_name}"
        try:
            if os.path.exists(session_path):
                shutil.copy2(session_path, f"{TEMP_SESSION_PATH}.session")
            conn = sqlite3.connect(f"{TEMP_SESSION_PATH}.session")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.close()
            self.client = TelegramClient(TEMP_SESSION_PATH, self.api_id, self.api_hash)
            await self.client.connect()
            return await self.client.is_user_authorized()
        except Exception: return False

    async def _ensure_monitor_bots_membership(self):
        """Checks and ensures all broadcaster bots and user accounts are in the monitor group."""
        try:
            tokens = settings.bot_tokens
            group_id = settings.MONITOR_GROUP_ID
            if not tokens or not group_id: return
            logger.info("    🐶 [UserAgent] Syncing Hub memberships and permissions...")
            for token in tokens:
                try:
                    bot_id = int(token.split(':')[0])
                    member = await self.check_membership(group_id, bot_id)
                    if not member:
                        from telegram import Bot
                        temp_bot = Bot(token)
                        me = await temp_bot.get_me()
                        if await self.invite_bot_to_group(me.username, group_id):
                            await self.promote_to_admin(group_id, me.username, anonymous=False)
                    elif not member.get("is_admin"):
                        await self.promote_to_admin(group_id, bot_id, anonymous=False)
                except Exception: pass
            if not self.sessions: self._discover_sessions()
            for session_path in self.sessions:
                if not await self._connect_to_session(session_path): continue
                try:
                    me = await self.client.get_me()
                    await self._disconnect()
                    member = await self.check_membership(group_id, me.id)
                    if member:
                        if not member.get("is_admin"):
                            await self.promote_to_admin(group_id, me.id, anonymous=True)
                    else:
                        logger.warning(f"    ⚠️ User @{me.username} is NOT in Hub. Please add manually.")
                except Exception: pass
        except Exception as e: logger.error(f"    ❌ [UserAgent] Membership sync fatal error: {e}")

    async def send_message(self, target: int | str, message: str, thread_id: int | None = None) -> bool:
        """Sends a text message to a target (group/user) as the User Agent."""
        async with self.lock:
            if not await self.start(): return False
            try:
                if str(target).lstrip('-').isdigit(): entity = int(target)
                else: entity = target
                await self.client.send_message(entity, message, reply_to=thread_id)
                logger.info(f"    🗣️ [UserAgent] Sent (session={self.current_session_name}): '{message[:30]}...'")
                return True
            except Exception as e:
                logger.error(f"    ❌ [UserAgent] Send failed: {e}")
                return False
            finally: await self._disconnect()

    async def clear_removed_users(self, group_id: int | str) -> int:
        async with self.lock:
            if not await self.start(): return 0
            cleared_count = 0
            try:
                if str(group_id).lstrip('-').isdigit(): target = int(group_id)
                else: target = group_id
                entity = await self.client.get_entity(target)
                from telethon.tl.types import ChannelParticipantsKicked, ChatBannedRights
                from telethon.tl.functions.channels import EditBannedRequest
                async for user in self.client.iter_participants(entity, filter=ChannelParticipantsKicked()):
                    try:
                        await self.client(EditBannedRequest(channel=entity, participant=user, banned_rights=ChatBannedRights(until_date=None, view_messages=False)))
                        cleared_count += 1
                    except Exception: pass
                return cleared_count
            except Exception: return 0
            finally: await self._disconnect()

    async def delete_old_messages(self, group_id: int | str, age_hours: int, topic_id: int | None = None) -> int:
        async with self.lock:
            if not await self.start(): return 0
            import datetime
            from telethon.tl.types import Message
            deleted_count = 0
            try:
                if str(group_id).lstrip('-').isdigit(): target = int(group_id)
                else: target = group_id
                entity = await self.client.get_entity(target)
                now = datetime.datetime.now(datetime.timezone.utc)
                cutoff = now - datetime.timedelta(hours=age_hours)
                async for message in self.client.iter_messages(entity, reply_to=topic_id):
                    if not isinstance(message, Message): continue
                    if message.date < cutoff:
                        try:
                            await self.client.delete_messages(entity, [message.id])
                            deleted_count += 1
                        except Exception: pass
                return deleted_count
            except Exception: return 0
            finally: await self._disconnect()

    async def get_last_message_id(self, group_id: int | str, topic_id: int) -> int | None:
        async with self.lock:
            if not await self.start(): return None
            try:
                if str(group_id).lstrip('-').isdigit(): target = int(group_id)
                else: target = group_id
                entity = await self.client.get_entity(target)
                messages = await self.client.get_messages(entity, limit=1, reply_to=topic_id)
                if messages: return messages[0].id
                return None
            except Exception: return None
            finally: await self._disconnect()

    async def get_history(self, group_id: int | str, limit: int) -> list[dict]:
        from telethon.tl.types import Message, MessageMediaPhoto, MessageMediaDocument
        from telethon.errors import FloodWaitError
        import os as _os
        # Minimum sleep between successive get_history calls on the same session.
        # Prevents back-to-back MTProto requests across concurrent Celery tasks from
        # triggering Telegram FloodWait. Tune via MTPROTO_INTER_REQUEST_SLEEP (default 3s).
        INTER_SLEEP = float(_os.getenv("MTPROTO_INTER_REQUEST_SLEEP", 3.0))
        async with self.lock:
            if not await self.start(): return []
            msgs = []
            try:
                if str(group_id).lstrip('-').isdigit(): target = int(group_id)
                else: target = group_id
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
                        else: media_type = "other"
                    sender_name = "Unknown"
                    if message.sender:
                        if hasattr(message.sender, 'username') and message.sender.username: sender_name = message.sender.username
                        elif hasattr(message.sender, 'first_name'): sender_name = message.sender.first_name
                    msgs.append({
                        "telegram_msg_id": message.id, "sender_name": sender_name, "content": content,
                        "media_type": media_type, "file_meta": file_meta, "chat_id": entity.id if hasattr(entity, 'id') else group_id
                    })
            except FloodWaitError as fwe:
                # Surface FloodWait so caller and logs know -- swallowing it hides the signal
                logger.warning(f"    🛑 [UserAgent] FloodWait in get_history for {group_id}: {fwe.seconds}s")
                from app.core.redis_srv import redis_srv
                session_name = self.current_session_name or "unknown"
                cooldown_key = f"user_agent:cooldown:{session_name}"
                wait = fwe.seconds + 60  # buffer
                if wait > 3600:
                    logger.error(f"    🛑 [UserAgent] SEVERE FLOOD WAIT for '{session_name}': {wait}s.")
                redis_srv.set_cooldown(cooldown_key, wait)
            except Exception as e:
                logger.debug(f"    ⚠️ [UserAgent] get_history error for {group_id}: {e}")
            finally:
                # Inter-request sleep INSIDE the lock so concurrent workers naturally queue
                # behind each other with spacing instead of all firing at once.
                await asyncio.sleep(INTER_SLEEP)
                await self._disconnect()
            return msgs

    async def search_messages(self, query: str, limit: int = 100) -> list[dict]:
        """
        Telegram global search via MTProto SearchGlobalRequest.

        Searches public channels Telegram has indexed (different result space
        from any web scanner). Same lock + cooldown discipline as get_history:
        FloodWait -> redis cooldown on the session, no client kept hot.

        Returns: list of {"text", "chat_id", "chat_name", "message_id", "date"}.
        Empty list if FloodWait, no sessions, or search disabled.
        """
        from telethon.tl.functions.messages import SearchGlobalRequest
        from telethon.tl.types import InputMessagesFilterEmpty, InputPeerEmpty
        from telethon.errors import FloodWaitError
        import os as _os

        INTER_SLEEP = float(_os.getenv("MTPROTO_INTER_REQUEST_SLEEP", 3.0))
        results: list[dict] = []

        async with self.lock:
            if not await self.start():
                return []
            try:
                # SearchGlobalRequest needs an InputPeer for offset_peer; use empty.
                res = await self.client(SearchGlobalRequest(
                    q=query,
                    filter=InputMessagesFilterEmpty(),
                    min_date=None,
                    max_date=None,
                    offset_rate=0,
                    offset_peer=InputPeerEmpty(),
                    offset_id=0,
                    limit=limit,
                ))

                # Build chat_id -> chat_name map from res.chats
                chat_map = {}
                for chat in (getattr(res, "chats", []) or []):
                    cid = getattr(chat, "id", None)
                    if cid is None:
                        continue
                    chat_map[cid] = (
                        getattr(chat, "title", None)
                        or getattr(chat, "username", None)
                        or "unknown"
                    )

                for msg in (getattr(res, "messages", []) or []):
                    text = getattr(msg, "message", None)
                    if not text:
                        continue

                    chat_id = None
                    chat_name = None
                    pid = getattr(msg, "peer_id", None)
                    if pid is not None:
                        if hasattr(pid, "channel_id"):
                            raw_id = pid.channel_id
                            chat_id = -1000000000000 - raw_id  # supergroup convention
                            chat_name = chat_map.get(raw_id)
                        elif hasattr(pid, "chat_id"):
                            raw_id = pid.chat_id
                            chat_id = -raw_id
                            chat_name = chat_map.get(raw_id)
                        elif hasattr(pid, "user_id"):
                            chat_id = pid.user_id
                            chat_name = chat_map.get(pid.user_id)

                    results.append({
                        "text": text,
                        "chat_id": chat_id,
                        "chat_name": chat_name,
                        "message_id": getattr(msg, "id", None),
                        "date": str(getattr(msg, "date", None)) if getattr(msg, "date", None) else None,
                    })

                logger.info(f"    🔎 [UserAgent] SearchGlobal('{query[:40]}') -> {len(results)} messages")

            except FloodWaitError as fwe:
                logger.warning(f"    🛑 [UserAgent] FloodWait on search: {fwe.seconds}s -- marking session cooldown")
                from app.core.redis_srv import redis_srv
                session_name = self.current_session_name or "unknown"
                wait = fwe.seconds + 60
                redis_srv.set_cooldown(f"user_agent:cooldown:{session_name}", wait)
            except Exception as e:
                logger.error(f"    ❌ [UserAgent] search_messages failed: {e}")
            finally:
                await asyncio.sleep(INTER_SLEEP)
                await self._disconnect()

        return results

user_agent = UserAgentService()
