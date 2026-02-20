import logging
import asyncio
import os
import sys
import signal
import redis.asyncio as redis
from telegram import Update
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

# Login flow states
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

# Admin IDs
ANONYMOUS_ADMIN_ID = 1087968824

# Global Stop Event
stop_event = asyncio.Event()

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return # Silent ignore
    await update.message.reply_text("🤖 **Telegram Hunter Bot** is online.\nUse /help to see all available commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    
    help_text = (
        "📖 **Telegram Hunter Bot Help**\n\n"
        "Here are the available commands:\n"
        "• /status - Check system health and pending broadcasts\n"
        "• /pause - Pause scanners and broadcaster\n"
        "• /resume - Resume operations\n"
        "• /restart - Restart the bot service\n"
        "• /commands - List all commands (Alias for /help)\n\n"
        "Only authorized administrators can use these commands."
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

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
    
    msg = (
        f"📊 **System Status**\n\n"
        f"**State**: {system_status}\n"
        f"**Redis**: {redis_status}\n"
        f"**Pending Broadcasts**: `{queue_count}`\n"
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
    
    try:
        me = await client.get_me()
        
        # Determine filename
        filename = me.username if me.username else me.phone
        # Ensure it doesn't start with + (though me.phone usually doesn't, but just in case)
        filename = filename.lstrip('+')
        
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        final_path = os.path.join(base_dir, filename + ".session")
        
        # Delete bot messages we sent during the flow
        for msg_id in context.user_data.get('bot_messages', []):
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except:
                pass

        # Use the USER account to clear history with the bot itself (last 24 hours)
        try:
            bot_entity = await client.get_entity(context.bot.username)
            await client.delete_dialog(bot_entity)
            logger.info(f"Deleted dialog with bot {context.bot.username} for logged in user {filename}")
        except Exception as e:
            logger.error(f"Failed to delete history with bot: {e}")
        
        await client.disconnect()

        # Copy to final destination
        saved_successfully = False
        try:
            if os.path.exists(final_path):
                os.remove(final_path)
            shutil.copy2(temp_session_path + ".session", final_path)
            saved_successfully = True
        except PermissionError:
            logger.warning(f"File {final_path} is locked. Attempting sqlite3 injection...")
            try:
                import sqlite3
                src_conn = sqlite3.connect(temp_session_path + ".session")
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
            except Exception as e:
                logger.error(f"Sqlite injection failed: {e}")

        if not saved_successfully:
            raise Exception("Could not save session file because it is locked by another process.")
        
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

async def post_init(application: Application):
    """
    Post-initialization hook.
    """
    logger.info("🤖 Bot Listener starting polling...")
    
    # Start Watchdog as a background task, track it in the application
    application.watchdog_task = asyncio.create_task(watchdog_loop(application.bot))

async def main():
    global redis_client
    
    token = settings.MONITOR_BOT_TOKEN
    if not token:
        logger.error("MONITOR_BOT_TOKEN not set!")
        return

    # Initialize Redis inside the event loop
    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

    # Build Application
    application = ApplicationBuilder().token(token).post_init(post_init).build()

    # Add Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("pause", pause))
    application.add_handler(CommandHandler("resume", resume))
    application.add_handler(CommandHandler("restart", restart))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("commands", help_command))

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

    # Handle signals for graceful shutdown (Unix only, Windows ignored)
    if os.name != 'nt':
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: stop_event.set())
    
    # Use context manager for correct lifecycle management
    async with application:
        await application.updater.start_polling(drop_pending_updates=True)
        
        logger.info("🚀 Bot Listener Started (Async Context Manager)")

        # Wait until stop_event is set (by /restart or signal)
        while not stop_event.is_set():
            await asyncio.sleep(1)
        
        logger.info("Stopping bot...")
        
        # Cleanup Watchdog
        if hasattr(application, 'watchdog_task'):
            application.watchdog_task.cancel()
            try:
                await application.watchdog_task
            except asyncio.CancelledError:
                pass
        
        # Context manager exit handles application.stop() and application.shutdown()
    
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
