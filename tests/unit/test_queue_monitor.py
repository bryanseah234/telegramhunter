import json
import time

from app.core.queue_monitor import (
    evaluate_queue_alerts,
    get_queue_snapshot,
    record_task_enqueued,
    record_task_started,
    summarize_queue_health,
)


class _FakeRedis:
    def __init__(self):
        self.lists = {}
        self.zsets = {}

    def llen(self, key):
        return len(self.lists.get(key, []))

    def lindex(self, key, index):
        values = self.lists.get(key, [])
        if not values:
            return None
        return values[index]

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zrem(self, key, member):
        self.zsets.get(key, {}).pop(member, None)

    def zrange(self, key, start, stop, withscores=False):
        values = sorted(self.zsets.get(key, {}).items(), key=lambda item: item[1])
        selected = values[start:stop + 1]
        return selected if withscores else [item[0] for item in selected]

    def delete(self, key):
        self.zsets.pop(key, None)


def test_queue_snapshot_tracks_length_and_oldest_age():
    fake = _FakeRedis()
    fake.lists["scrape"] = ["task-1", "task-2"]
    record_task_enqueued(fake, "scrape", "task-1", timestamp=time.time() - 120)

    snapshot = get_queue_snapshot(fake, queues=("scrape",))

    assert snapshot["scrape"]["length"] == 2
    assert snapshot["scrape"]["oldest_job_age_seconds"] >= 119


def test_record_task_started_removes_tracking_entry():
    fake = _FakeRedis()
    record_task_enqueued(fake, "validation", "task-1", timestamp=time.time() - 10)

    record_task_started(fake, "task-1", queues=("validation",))
    snapshot = get_queue_snapshot(fake, queues=("validation",))

    assert snapshot["validation"]["oldest_job_age_seconds"] is None


def test_queue_snapshot_falls_back_to_embedded_timestamp():
    fake = _FakeRedis()
    fake.lists["celery"] = [
        json.dumps({"headers": {"created_at": time.time() - 45}}),
    ]

    snapshot = get_queue_snapshot(fake, queues=("celery",))

    assert snapshot["celery"]["length"] == 1
    assert snapshot["celery"]["oldest_job_age_seconds"] >= 44


def test_queue_snapshot_clears_stale_tracking_when_queue_empty():
    fake = _FakeRedis()
    record_task_enqueued(fake, "scrape", "stale-task", timestamp=time.time() - 999)

    snapshot = get_queue_snapshot(fake, queues=("scrape",))

    assert snapshot["scrape"]["length"] == 0
    assert snapshot["scrape"]["oldest_job_age_seconds"] is None
    assert "queue_monitor:scrape:enqueued" not in fake.zsets


def test_queue_alerts_flag_length_and_oldest_age():
    snapshot = {
        "scrape": {"length": 101, "oldest_job_age_seconds": 901},
        "celery": {"length": 0, "oldest_job_age_seconds": None},
    }

    alerts = evaluate_queue_alerts(
        snapshot,
        length_threshold=100,
        oldest_age_threshold_seconds=900,
    )

    assert {alert["type"] for alert in alerts} == {"queue_length", "oldest_job_age"}
    assert all(alert["queue"] == "scrape" for alert in alerts)


def test_queue_health_summary_is_healthy_without_alerts():
    summary = summarize_queue_health(
        {"validation": {"length": 1, "oldest_job_age_seconds": 10}},
        length_threshold=100,
        oldest_age_threshold_seconds=900,
    )

    assert summary["status"] == "healthy"
    assert summary["alerts"] == []
