import types

import asyncio
import pytest

from app.core.db_retry import DatabaseHealth
from app.services import bot_listener
from app.workers import celery_app
from app.workers.tasks import validation_tasks


def test_database_health_probe_remains_callable():
    assert callable(DatabaseHealth.check_connection)


@pytest.mark.asyncio
async def test_log_update_uses_async_monitor_guard(monkeypatch):
    calls = {"async_guard": 0, "logged": 0}

    async def fake_resolve():
        calls["async_guard"] += 1
        return {"123"}

    def fail_sync_guard(_chat_id):
        raise AssertionError("sync guard should not be called from log_update")

    def fake_log(_message):
        calls["logged"] += 1

    monkeypatch.setattr("app.services.scraper_srv._resolve_monitor_group_ids_async", fake_resolve)
    monkeypatch.setattr("app.services.scraper_srv._is_monitor_group", fail_sync_guard)
    monkeypatch.setattr(bot_listener.logger, "info", fake_log)

    update = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=123),
        effective_user=types.SimpleNamespace(id=999),
        message=types.SimpleNamespace(text="hello", caption=None),
    )

    await bot_listener.log_update(update, context=None)

    assert calls["async_guard"] == 1
    assert calls["logged"] == 0


def test_backfill_scoring_does_not_update_top_level_confidence_score():
    source = open(validation_tasks.__file__, "r", encoding="utf-8").read()
    forbidden = '.update({\n                        "meta": new_meta,\n                        "confidence_score": score,'
    assert forbidden not in source


def test_task_failure_audit_uses_live_audit_schema(monkeypatch):
    inserted = []

    class FakeAuditTable:
        def insert(self, payload):
            inserted.append(payload)
            return self

        def execute(self):
            return types.SimpleNamespace(data=[{}])

    class FakeDb:
        def table(self, name):
            assert name == "audit_logs"
            return FakeAuditTable()

    class InlineLoop:
        def call_soon_threadsafe(self, callback):
            callback()

        def create_task(self, coro):
            asyncio.run(coro)
            return types.SimpleNamespace()

    class InlineThread:
        def __init__(self, target, daemon=False, **_kwargs):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    async def inline_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("app.core.database.db", FakeDb())
    monkeypatch.setattr(celery_app, "get_worker_loop", lambda: InlineLoop())
    monkeypatch.setattr(celery_app.asyncio, "to_thread", inline_to_thread)
    monkeypatch.setattr("threading.Thread", InlineThread)

    celery_app.on_task_failure(
        task_id="task-1",
        exception=RuntimeError("boom"),
        traceback=None,
        einfo=None,
        args=(),
        kwargs={},
        sender=types.SimpleNamespace(name="flow.example"),
    )

    assert inserted
    payload = inserted[0]
    assert "actor" not in payload
    assert "created_at" not in payload
    assert payload["user_agent"] == "celery_worker"
    assert payload["success"] is False
    assert payload["details"]["task_name"] == "flow.example"
