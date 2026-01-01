import os
from telethon import TelegramClient, functions, types
from app.core.config import settings

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

    async def start(self):
        """Starts the user client. Requires existing session file or env var."""
        
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
                    
        except Exception as e:
            print(f"    ❌ [UserAgent] Error: {e}")
            return False
        finally:
            await self.stop()

user_agent = UserAgentService()
