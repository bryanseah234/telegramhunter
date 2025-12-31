from celery import Celery
from app.core.config import settings

app = Celery("telegram_hunter", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Auto-discover tasks in these modules
    imports=[
        "app.workers.tasks.flow_tasks",
        "app.workers.tasks.scanner_tasks"
    ]
)
