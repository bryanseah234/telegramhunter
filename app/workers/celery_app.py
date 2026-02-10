from celery import Celery
from app.core.config import settings
import logging
import sys
import os

# ==============================================
# WORKER LOGGING CONFIGURATION
# ==============================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger(__name__)

app = Celery("telegram_hunter", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

from celery.signals import worker_ready, worker_shutdown
from app.services.broadcaster_srv import BroadcasterService
import asyncio

def _send_signal_log(msg):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # 5 second timeout to prevent blocking
        broadcaster = BroadcasterService()
        loop.run_until_complete(
            asyncio.wait_for(broadcaster.send_log(msg), timeout=5.0)
        )
    except asyncio.TimeoutError:
        print(f"⚠️ Signal notification timed out: {msg[:30]}...")
    except Exception as e:
        print(f"⚠️ Signal notification failed: {e}")
    finally:
        loop.close()

@worker_ready.connect
def on_worker_ready(**kwargs):
    _send_signal_log("🟢 **Worker Service** Started (Celery)")

@worker_shutdown.connect
def on_worker_shutdown(**kwargs):
    _send_signal_log("🔴 **Worker Service** Stopping...")

from celery.schedules import crontab

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
    broker_pool_limit=10,       # More connections for concurrency
    
    # Auto-discover tasks in these modules
    imports=[
        "app.workers.tasks.flow_tasks",
        "app.workers.tasks.scanner_tasks",
        "app.workers.tasks.audit_tasks"
    ],
    beat_schedule={
        # ============================================
        # AGGRESSIVE BROADCAST & RESCRAPE
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
        # ============================================
        # AGGRESSIVE STAGGERED SCANS
        # Default: Every 4 hours
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
            # Fofa offset by +1 hour from base interval to stagger
            "schedule": crontab(minute=0, hour=f"1-23/{int(os.getenv('SCAN_INTERVAL_HOURS', 4))}"), 
        },
        # ============================================
        # SYSTEM AUDIT & FAILSAFES
        # ============================================
        "audit-active-topics-2hours": {
            "task": "audit.audit_active_topics",
            "schedule": crontab(minute=15, hour=f"*/{int(os.getenv('AUDIT_INTERVAL_HOURS', 2))}"),
        },
    }
)
