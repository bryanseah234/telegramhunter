import logging
import asyncio
import os
import sys
import signal
import redis.asyncio as redis
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, Application
from telegram.constants import ParseMode

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.core.config import settings
from app.core.database import db

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
