import asyncio
import logging
import os
import sys

from celery import Celery
from celery.signals import worker_ready, worker_shutdown
from celery.schedules import crontab

from app.core.config import settings

# ==============================================
# WORKER LOGGING CONFIGURATION
# ==============================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

app = Celery("telegram_hunter", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

# ==============================================
# PERSISTENT EVENT LOOP (BUG-008)
# One loop per worker process — avoids asyncio.run() creating a new loop per task,
# which broke asyncio.Lock objects and defeated Telethon connection pooling.
# ==============================================
_worker_loop: asyncio.AbstractEventLoop | None = None


def get_worker_loop() -> asyncio.AbstractEventLoop:
    """
    Returns the persistent event loop for this worker process.
    Creates one if it doesn't exist or was closed.
    All Celery tasks must use this loop via loop.run_until_complete()
    instead of asyncio.run().
    """
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
        logger.info(f"[Worker] Created persistent event loop (pid={os.getpid()})")
    return _worker_loop


# ==============================================
# WORKER LIFECYCLE SIGNALS
# ==============================================

def _send_signal_log(msg: str):
    """Send a startup/shutdown notification to Telegram using the persistent loop."""
    loop = get_worker_loop()
    try:
        from app.services.broadcaster_srv import BroadcasterService
        broadcaster = BroadcasterService()
        loop.run_until_complete(asyncio.wait_for(broadcaster.send_log(msg), timeout=5.0))
    except TimeoutError:
        logger.warning(f"Signal notification timed out: {msg[:30]}...")
    except Exception as e:
        logger.warning(f"Signal notification failed: {e}")


@worker_ready.connect
def on_worker_ready(**kwargs):
    get_worker_loop()  # Ensure loop is initialized before any task runs
    _send_signal_log("🟢 **Worker Service** Started (Celery)")


@worker_shutdown.connect
def on_worker_shutdown(**kwargs):
    _send_signal_log("🔴 **Worker Service** Stopping...")
    global _worker_loop
    if _worker_loop and not _worker_loop.is_closed():
        _worker_loop.close()
        logger.info("[Worker] Persistent event loop closed.")


# ==============================================
# CELERY CONFIGURATION
# ==============================================
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    # ============================================
    # Local Docker Deployment (Aggressive Mode)
    # ============================================
    result_expires=1800,
    task_ignore_result=True,
    worker_max_memory_per_child=800000,  # 800MB per worker
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_soft_time_limit=1200,  # 20 minutes soft limit
    task_time_limit=1300,       # Hard limit > soft limit
    broker_pool_limit=10,
    # Auto-discover tasks in these modules
    imports=[
        "app.workers.tasks.flow_tasks",
        "app.workers.tasks.scanner_tasks",
        "app.workers.tasks.audit_tasks",
        "app.workers.tasks.import_tasks",   # MISSING-001: CSV import pipeline
    ],
    # ============================================
    # QUEUE SEGREGATION
    # ============================================
    task_routes={
        "flow.exfiltrate_chat": {"queue": "scrape"},
        "flow.rescrape_active": {"queue": "scrape"},
        "scanner.*": {"queue": "scanners"},
    },
    beat_schedule={
        # ============================================
        # BROADCAST & RESCRAPE
        # ============================================
        "broadcast-hourly": {
            "task": "flow.broadcast_pending",
            "schedule": crontab(minute=f"*/{int(os.getenv('BROADCAST_INTERVAL_MINUTES', 60))}"),
        },
        "rescrape-active-hourly": {
            "task": "flow.rescrape_active",
            "schedule": crontab(minute=0, hour=f"*/{int(os.getenv('RESCRAPE_INTERVAL_HOURS', 1))}"),
        },
        # Heartbeat every 30 minutes
        "system-heartbeat-30min": {
            "task": "flow.system_heartbeat",
            "schedule": crontab(minute="*/30"),
        },
        # Periodic Help Guide (Every 6 hours)
        "system-help-6hours": {
            "task": "flow.system_help",
            "schedule": crontab(minute=30, hour="*/6"),
        },
        # ============================================
        # STAGGERED SCANS
        # ============================================
        "scan-github-4hours": {
            "task": "scanner.scan_github",
            "schedule": crontab(minute=0, hour=f"*/{int(os.getenv('SCAN_INTERVAL_HOURS', 4))}"),
        },
        "scan-shodan-4hours": {
            "task": "scanner.scan_shodan",
            "schedule": crontab(minute=20, hour=f"*/{int(os.getenv('SCAN_INTERVAL_HOURS', 4))}"),
        },
        "scan-urlscan-4hours": {
            "task": "scanner.scan_urlscan",
            "schedule": crontab(minute=40, hour=f"*/{int(os.getenv('SCAN_INTERVAL_HOURS', 4))}"),
        },
        "scan-fofa-4hours": {
            "task": "scanner.scan_fofa",
            "schedule": crontab(minute=0, hour=f"1-23/{int(os.getenv('SCAN_INTERVAL_HOURS', 4))}"),
        },
        "scan-gitlab-6hours": {
            "task": "scanner.scan_gitlab",
            "schedule": crontab(minute=10, hour="*/6"),
        },
        "scan-grepapp-6hours": {
            "task": "scanner.scan_grepapp",
            "schedule": crontab(minute=25, hour="*/6"),
        },
        "scan-gist-6hours": {
            "task": "scanner.scan_gist",
            "schedule": crontab(minute=45, hour="*/6"),
        },
        "scan-pastebin-12hours": {
            "task": "scanner.scan_pastebin",
            "schedule": crontab(minute=15, hour="*/12"),
        },
        "scan-serper-12hours": {
            "task": "scanner.scan_serper",
            "schedule": crontab(minute=35, hour="*/12"),
        },
        "scan-google-12hours": {
            "task": "scanner.scan_google",
            "schedule": crontab(minute=50, hour="*/12"),
        },
        "scan-bitbucket-8hours": {
            "task": "scanner.scan_bitbucket",
            "schedule": crontab(minute=30, hour="*/8"),
        },
        "scan-shodan-c2-6hours": {
            "task": "scanner.scan_shodan_c2",
            "schedule": crontab(minute=10, hour="*/6"),
        },
        # Netlas — once daily (budget: 45+90=135 req/day across 2 accounts)
        "scan-netlas-daily": {
            "task": "scanner.scan_netlas",
            "schedule": crontab(minute=0, hour=3),
        },
        # ============================================
        # RETRY COLD TOKENS
        # ============================================
        "retry-cold-12hours": {
            "task": "scanner.retry_cold",
            "schedule": crontab(minute=50, hour="*/12"),
        },
        # ============================================
        # SYSTEM AUDIT, SELF-HEAL & FAILSAFES
        # ============================================
        "audit-active-topics-hourly": {
            "task": "audit.audit_active_topics",
            "schedule": crontab(minute=15, hour=f"*/{int(os.getenv('AUDIT_INTERVAL_HOURS', 1))}"),
        },
        "system-self-heal-6hours": {
            "task": "system.self_heal",
            "schedule": crontab(minute=45, hour="*/6"),
        },
        "system-enforce-whitelist-6hours": {
            "task": "system.enforce_whitelist",
            "schedule": crontab(minute=0, hour="1-23/6"),
        },
        "cleanup-general-topic-hourly": {
            "task": "system.cleanup_general_topic",
            "schedule": crontab(minute=30),
        },
        # ============================================
        # CSV IMPORT PIPELINE (MISSING-001)
        # ============================================
        "import-csv-5min": {
            "task": "system.import_csv",
            "schedule": crontab(minute="*/5"),
        },
    },
)
