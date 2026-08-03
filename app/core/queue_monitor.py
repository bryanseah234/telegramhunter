import contextlib
import json
import time
from datetime import UTC, datetime
from typing import Any

DEFAULT_QUEUES = ("celery", "scrape", "scanners", "validation")


def queue_tracking_key(queue_name: str) -> str:
    return f"queue_monitor:{queue_name}:enqueued"


def record_task_enqueued(redis_client: Any, queue_name: str, task_id: str, timestamp: float | None = None) -> None:
    if not redis_client or not task_id:
        return
    queue = queue_name or "celery"
    redis_client.zadd(queue_tracking_key(queue), {task_id: timestamp or time.time()})


def record_task_started(redis_client: Any, task_id: str, queues: tuple[str, ...] = DEFAULT_QUEUES) -> None:
    if not redis_client or not task_id:
        return
    for queue in queues:
        try:
            redis_client.zrem(queue_tracking_key(queue), task_id)
        except Exception:
            continue


def get_queue_snapshot(redis_client: Any, queues: tuple[str, ...] = DEFAULT_QUEUES) -> dict[str, dict[str, Any]]:
    now = time.time()
    snapshot: dict[str, dict[str, Any]] = {}
    for queue in queues:
        length = 0
        oldest_enqueued_at = None
        oldest_age_seconds = None
        try:
            length = int(redis_client.llen(queue))
        except Exception:
            length = 0

        if length <= 0:
            with contextlib.suppress(Exception):
                redis_client.delete(queue_tracking_key(queue))
            snapshot[queue] = {
                "length": 0,
                "oldest_job_age_seconds": None,
                "oldest_enqueued_at": None,
            }
            continue

        try:
            oldest = redis_client.zrange(queue_tracking_key(queue), 0, 0, withscores=True)
            if oldest:
                _task_id, score = oldest[0]
                oldest_enqueued_at = datetime.fromtimestamp(float(score), tz=UTC).isoformat()
                oldest_age_seconds = max(0, int(now - float(score)))
        except Exception:
            oldest_enqueued_at = None
            oldest_age_seconds = None

        if oldest_age_seconds is None and length > 0:
            oldest_age_seconds = _best_effort_oldest_age_from_queue(redis_client, queue, now)

        snapshot[queue] = {
            "length": length,
            "oldest_job_age_seconds": oldest_age_seconds,
            "oldest_enqueued_at": oldest_enqueued_at,
        }
    return snapshot


def _best_effort_oldest_age_from_queue(redis_client: Any, queue: str, now: float) -> int | None:
    for index in (-1, 0):
        try:
            raw = redis_client.lindex(queue, index)
        except Exception:
            continue
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        timestamp = _extract_timestamp(payload)
        if timestamp is not None:
            return max(0, int(now - timestamp))
    return None


def _extract_timestamp(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    candidates = [
        payload.get("created_at"),
        payload.get("timestamp"),
        (payload.get("headers") or {}).get("created_at") if isinstance(payload.get("headers"), dict) else None,
        (payload.get("headers") or {}).get("timestamp") if isinstance(payload.get("headers"), dict) else None,
        (payload.get("properties") or {}).get("timestamp") if isinstance(payload.get("properties"), dict) else None,
    ]
    for value in candidates:
        if value is None:
            continue
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
    return None
