from celery import Celery
from app.core.config import settings

app = Celery("telegram_hunter", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

from celery.signals import worker_ready, worker_shutdown
from app.services.broadcaster_srv import broadcaster_service
import asyncio

def _send_signal_log(msg):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # 5 second timeout to prevent blocking
        loop.run_until_complete(
            asyncio.wait_for(broadcaster_service.send_log(msg), timeout=5.0)
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

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    
    # ============================================
    # Railway Free Tier Optimization (512MB RAM)
    # ============================================
    result_expires=1800, # Results expire after 30 min (was 1 hour)
    task_ignore_result=True, # Do not store results by default (saves space)
    worker_max_memory_per_child=80000, # Restart worker if memory exceeds ~80MB (was 100MB)
    worker_prefetch_multiplier=1, # Fetch 1 task at a time (prevents memory spikes)
    task_acks_late=True, # Acknowledge after task completes (prevents lost tasks on crash)
    task_soft_time_limit=300, # 5 min soft limit (raises exception)
    task_time_limit=360, # 6 min hard limit (kills task)
    broker_pool_limit=1, # Minimal Redis connections
    
    # Auto-discover tasks in these modules
    imports=[
        "app.workers.tasks.flow_tasks",
        "app.workers.tasks.scanner_tasks"
    ],
    beat_schedule={
        "broadcast-every-minute": {
            "task": "flow.broadcast_pending",
            "schedule": 60.0, # Every 60 seconds
        },
        "scan-github-4hours": {
            "task": "scanner.scan_github",
            "schedule": 14400.0, # Every 4 hours
        },
        "scan-shodan-4hours": {
            "task": "scanner.scan_shodan",
            "schedule": 14400.0, # Every 4 hours
        },
        "scan-urlscan-4hours": {
            "task": "scanner.scan_urlscan",
            "schedule": 14400.0, # Every 4 hours
        },
        "system-heartbeat-30min": {
            "task": "flow.system_heartbeat",
            "schedule": 1800.0, # Every 30 minutes
        },
        "rescrape-active-4hours": {
            "task": "flow.rescrape_active",
            "schedule": 14400.0, # Every 4 hours
        }
    }
)
