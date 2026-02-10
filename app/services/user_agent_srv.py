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
        self.cooldown_until = 0 # Unix timestamp until which usage is blocked

    async def start(self):
        """Starts the user client. Requires existing session file or env var."""
        
        # Check cooldown
        if time.time() < self.cooldown_until:
             wait_left = int(self.cooldown_until - time.time())
             print(f"    ⏳ [UserAgent] In Cooldown for {wait_left}s. Skipping action.")
             return False

        # Check if already connected (Persistent Mode)
        if self.client and self.client.is_connected():
            return True

        # 1. Check for Env Var Fallback (Railway)
        if not os.path.exists(self.session_path) and not os.path.exists(f"{self.session_path}.session"):
            # Try single var logic
            session_b64 = settings.USER_SESSION_STRING
            
            # Try split var logic if single is missing
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
                    print(f"    ✨ [UserAgent] Detected {len(parts)} split session variables.")

            if session_b64:
                print("    ✨ [UserAgent] Recovering session from Environment Variable...")
                try:
                    import base64
                    decoded = base64.b64decode(session_b64)
                    # Telethon expects .session extension for the file path provided
                    # But the file on disk should be exactly what we write.
                    # Telethon client(path) -> adds .session if missing.
                    # We will write to 'user_session.session' explicitly.
                    real_path = f"{self.session_path}.session"
                    with open(real_path, "wb") as f:
                        f.write(decoded)
                    print(f"    ✅ [UserAgent] Session restored to {real_path}")
                except Exception as e:
                    print(f"    ❌ [UserAgent] Failed to decode session string: {e}")

        if not os.path.exists(f"{self.session_path}") and not os.path.exists(f"{self.session_path}.session"):
            print("    ⚠️ [UserAgent] No session file found. Run 'scripts/login_user.py' first.")
            return False
            
        self.client = TelegramClient(self.session_path, self.api_id, self.api_hash)
        await self.client.connect()
        
        if not await self.client.is_user_authorized():
            print("    ⚠️ [UserAgent] Session invalid or expired.")
            await self.client.disconnect()
            return False
            
        return True

    async def stop(self):
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
            except Exception as e:
                print(f"    ❌ [UserAgent] Could not resolve entities: {e}")
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
            print(f"    🛑 [UserAgent] FLOOD WAIT: Must wait {e.seconds} seconds.")
            self.cooldown_until = time.time() + e.seconds + 5
            return False
        except Exception as e:
            print(f"    ❌ [UserAgent] Error: {e}")
            return False
        finally:
            pass

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
            pass

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

user_agent = UserAgentService()
