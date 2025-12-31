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
        "scan-github-30min": {
            "task": "scanner.scan_github",
            "schedule": 1800.0, # Every 30 minutes
        },
        "scan-shodan-30min": {
            "task": "scanner.scan_shodan",
            "schedule": 1800.0, # Every 30 minutes
        },
        "scan-urlscan-30min": {
            "task": "scanner.scan_urlscan",
            "schedule": 1800.0, # Every 30 minutes
        },
        "system-heartbeat-30min": {
            "task": "flow.system_heartbeat",
            "schedule": 1800.0, # Every 30 minutes
        }
    }
)
