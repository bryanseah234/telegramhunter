import types

import pytest

from app.core.db_retry import DatabaseHealth
from app.services import bot_listener
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
