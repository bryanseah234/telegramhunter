import logging
import asyncio
import os
import sys
import redis
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
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

# Redis for Pause State
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
PAUSE_KEY = "system:paused"

# Admin IDs
ANONYMOUS_ADMIN_ID = 1087968824

def _get_whitelisted_usernames():
    raw = settings.WHITELISTED_BOT_IDS or ""
    return [u.strip().lower().replace("@", "") for u in raw.split(",") if u.strip()]

def is_admin(update: Update) -> bool:
    """Checks if the user is an admin (Whitelisted Username or Group Anonymous Bot)"""
    user = update.effective_user
    chat = update.effective_chat
    
    if not user:
        return False
        
    # 1. Check ID (Anonymous Admin)
    if user.id == ANONYMOUS_ADMIN_ID:
        # If sent as anonymous admin in a group, we assume it's an admin of that group.
        # Ideally we check if it's OUR monitor group, but for now this ID is specific enough.
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
    await update.message.reply_text("🤖 **Telegram Hunter Bot** is online.\nUse /status, /pause, /resume, /restart.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    # 1. Check Redis
    redis_status = "jq ✅ Online"
    try:
        if not redis_client.ping():
            redis_status = "❌ Unreachable"
    except:
        redis_status = "❌ Unreachable"

    # 2. Check DB / Pending Queue
    queue_count = "?"
    try:
        res = db.table("exfiltrated_messages").select("id", count="exact").eq("is_broadcasted", False).execute()
        queue_count = res.count
    except Exception as e:
        queue_count = f"❌ Error: {str(e)[:20]}"

    # 3. Check System Pause State
    is_paused = redis_client.get(PAUSE_KEY)
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
    
    redis_client.set(PAUSE_KEY, "true")
    await update.message.reply_text("jg ⏸️ **System Paused**.\nScanners and Broadcaster will skip their next run.")

async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    redis_client.delete(PAUSE_KEY)
    await update.message.reply_text("▶️ **System Resumed**.\nOperations returning to normal.")

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    await update.message.reply_text("🔄 **Restarting Bot Process**...\n(Expect a brief downtime)")
    # Exit process - Docker will restart it
    sys.exit(0)

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
    
    while True:
        await asyncio.sleep(60)
        
        # 1. Check Redis
        try:
            redis_client.ping()
            if not state["redis"]:
                state["redis"] = True
                await _send_alert(bot, "✅ **RECOVERY**: Redis connection restored.")
        except Exception as e:
            if state["redis"]:
                state["redis"] = False
                await _send_alert(bot, f"❌ **CRITICAL**: Redis connection LOST! ({str(e)[:20]})")
            
            # If Redis is down, we can't check worker stats from Redis
            continue 

        # 2. Check Worker Heartbeat
        # Worker writes timestamp to system:heartbeat:last_seen every 30 mins
        # Alert if silent for > 45 mins
        try:
            last_seen = redis_client.get("system:heartbeat:last_seen")
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

async def _send_alert(bot, msg):
    try:
        await bot.send_message(chat_id=settings.MONITOR_GROUP_ID, message_thread_id=None, text=f"🚨 [Watchdog]\n{msg}")
    except Exception as e:
        logger.error(f"Failed to send watchdog alert: {e}")

async def post_init(application):
    """
    Post-initialization hook.
    - Start Watchdog
    - Log success
    """
    logger.info("🤖 Bot Listener starting polling...")
    
    # Start Watchdog
    asyncio.create_task(watchdog_loop(application.bot))

def run_bot():
    token = settings.MONITOR_BOT_TOKEN
    if not token:
        logger.error("MONITOR_BOT_TOKEN not set!")
        return

    # Build Application
    app = ApplicationBuilder().token(token).post_init(post_init).build()

    # Add Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("pause", pause))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(CommandHandler("restart", restart))

    # Run Polling (Blocking)
    # drop_pending_updates=True prevents flood of old commands on restart
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    try:
        run_bot()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
