import os
from telethon import TelegramClient, functions, types
from app.core.config import settings

SESSION_FILE = "user_session.session"

class UserAgentService:
    """
    Service acting as a real Telegram User (not a bot).
    Used for actions bots cannot perform, like inviting other bots to groups.
    """
    def __init__(self):
        self.api_id = settings.TELEGRAM_API_ID
        self.api_hash = settings.TELEGRAM_API_HASH
        self.session_path = os.path.abspath(SESSION_FILE)
        self.client = None

    async def start(self):
        """Starts the user client. Requires existing session file or env var."""
        
        # 1. Check for Env Var Fallback (Railway)
        if not os.path.exists(self.session_path) and not os.path.exists(f"{self.session_path}.session"):
            if settings.USER_SESSION_STRING:
                print("    ✨ [UserAgent] Recovering session from Environment Variable...")
                try:
                    import base64
                    decoded = base64.b64decode(settings.USER_SESSION_STRING)
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
                group_entity = await self.client.get_entity(int(group_id))
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
