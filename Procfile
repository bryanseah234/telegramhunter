web: /bin/sh -c "uvicorn app.api.main:app --host 0.0.0.0 --port $PORT --workers 1 --limit-concurrency 10 --limit-max-requests 1000 --log-level info"
worker: celery -A app.workers.celery_app worker -B --loglevel=info --concurrency=1 --max-tasks-per-child=25 --without-heartbeat --without-mingle --without-gossip
