web: uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
worker: celery -A app.workers.celery_app worker -B --loglevel=info --concurrency=1 --max-tasks-per-child=50
