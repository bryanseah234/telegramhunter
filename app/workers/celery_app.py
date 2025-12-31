from celery import Celery
from app.core.config import settings

app = Celery("telegram_hunter", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

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
        "scan-censys-30min": {
            "task": "scanner.scan_censys",
            "schedule": 1800.0, # Every 30 minutes
        }
    }
)
