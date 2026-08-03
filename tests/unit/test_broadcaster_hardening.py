import asyncio
import logging
import sys
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, Forbidden, TimedOut


class _FakeBot:
    def __init__(self, *, error=None):
        self.error = error
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error


def _settings(bot_tokens=None, auto_archive=False):
    return SimpleNamespace(
        bot_tokens=bot_tokens or ["123:ABC"],
        AUTO_ARCHIVE_MEDIA=auto_archive,
    )


@pytest.mark.asyncio
async def test_resolve_chat_id_falls_back_to_credential_lookup(monkeypatch):
    from app.services import broadcaster_srv

    class _FakeQuery:
        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

    class _FakeDb:
        def __init__(self):
            self.table_name = None

        def table(self, table_name):
            self.table_name = table_name
            return _FakeQuery()

    fake_db = _FakeDb()
    monkeypatch.setitem(sys.modules, "app.core.database", SimpleNamespace(db=fake_db))

    async def fake_async_execute(_query):
        return SimpleNamespace(data=[{"chat_id": -100987654}])

    monkeypatch.setattr(broadcaster_srv, "_async_execute", fake_async_execute)
    monkeypatch.setattr(broadcaster_srv, "settings", _settings())

    service = broadcaster_srv.BroadcasterService()

    chat_id = await service._resolve_chat_id({"credential_id": "cred-1"})

    assert chat_id == -100987654
    assert fake_db.table_name == "discovered_credentials"


@pytest.mark.asyncio
async def test_resolve_chat_id_prefers_joined_bot_username(monkeypatch):
    from app.services import broadcaster_srv

    monkeypatch.setattr(broadcaster_srv, "settings", _settings())
    service = broadcaster_srv.BroadcasterService()

    chat_id = await service._resolve_chat_id(
        {
            "chat_id": 8940899601,
            "discovered_credentials": {
                "chat_id": 8940899601,
                "meta": {"bot_username": "ItsWatermarkBot"},
            },
        }
    )

    assert chat_id == "@ItsWatermarkBot"


@pytest.mark.asyncio
async def test_resolve_chat_id_prefers_db_bot_username(monkeypatch):
    from app.services import broadcaster_srv

    class _FakeQuery:
        selected = None

        def select(self, columns, *_args, **_kwargs):
            self.selected = columns
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

    class _FakeDb:
        def __init__(self):
            self.query = _FakeQuery()

        def table(self, _table_name):
            return self.query

    fake_db = _FakeDb()
    monkeypatch.setitem(sys.modules, "app.core.database", SimpleNamespace(db=fake_db))

    async def fake_async_execute(_query):
        return SimpleNamespace(
            data=[{"chat_id": 8940899601, "meta": {"bot_username": "@ItsWatermarkBot"}}]
        )

    monkeypatch.setattr(broadcaster_srv, "_async_execute", fake_async_execute)
    monkeypatch.setattr(broadcaster_srv, "settings", _settings())

    service = broadcaster_srv.BroadcasterService()

    chat_id = await service._resolve_chat_id({"credential_id": "cred-1"})

    assert chat_id == "@ItsWatermarkBot"
    assert fake_db.query.selected == "chat_id, meta"


@pytest.mark.asyncio
async def test_schedule_auto_archive_tracks_and_logs_task_failures(monkeypatch, caplog):
    from app.services import broadcaster_srv

    monkeypatch.setattr(broadcaster_srv, "settings", _settings())
    service = broadcaster_srv.BroadcasterService()

    async def crash_auto_archive(*_args, **_kwargs):
        raise RuntimeError("archive exploded")

    monkeypatch.setattr(service, "_auto_archive_media", crash_auto_archive)
    caplog.set_level(logging.ERROR, logger="broadcaster")

    service._schedule_auto_archive(-100123, 12, {"telegram_msg_id": 42}, 42)
    assert len(service._archive_tasks) == 1

    task = next(iter(service._archive_tasks))
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert service._archive_tasks == set()
    assert "Auto-archive task crashed for msg=42" in caplog.text


@pytest.mark.asyncio
async def test_send_message_marks_forbidden_bot_failed(monkeypatch):
    from app.services import broadcaster_srv

    monkeypatch.setattr(broadcaster_srv, "settings", _settings(bot_tokens=["123:ABC"]))
    service = broadcaster_srv.BroadcasterService()

    async def no_wait():
        return None

    fake_bot = _FakeBot(error=Forbidden("bot was kicked"))
    monkeypatch.setattr(service, "_wait_for_rate_limit", no_wait)
    monkeypatch.setattr(service, "_get_bot_instance", lambda _token: fake_bot)

    with pytest.raises(broadcaster_srv.BroadcastSendError) as exc_info:
        await service.send_message(
            -100123,
            1,
            {"content": "hello", "sender_name": "tester", "media_type": "text", "telegram_msg_id": 77},
        )

    assert "123:ABC" in service._failed_tokens
    assert exc_info.value.reason == "forbidden"


@pytest.mark.asyncio
async def test_send_message_schedules_auto_archive_for_supported_media(monkeypatch):
    from app.services import broadcaster_srv

    monkeypatch.setattr(
        broadcaster_srv,
        "settings",
        _settings(bot_tokens=["123:ABC"], auto_archive=True),
    )
    service = broadcaster_srv.BroadcasterService()
    scheduled = []

    async def no_wait():
        return None

    monkeypatch.setattr(service, "_wait_for_rate_limit", no_wait)
    monkeypatch.setattr(service, "_get_bot_instance", lambda _token: _FakeBot())
    monkeypatch.setattr(
        service,
        "_schedule_auto_archive",
        lambda *args: scheduled.append(args),
    )

    await service.send_message(
        -100123,
        4,
        {
            "id": "row-1",
            "content": "photo",
            "sender_name": "tester",
            "media_type": "photo",
            "telegram_msg_id": 88,
            "credential_id": "cred-1",
        },
    )

    assert scheduled == [(-100123, 4, {
        "id": "row-1",
        "content": "photo",
        "sender_name": "tester",
        "media_type": "photo",
        "telegram_msg_id": 88,
        "credential_id": "cred-1",
    }, 88)]


def test_broadcast_exception_classifier_maps_timeout_and_topic_missing():
    from app.services.broadcaster_srv import _classify_broadcast_exception

    timeout = _classify_broadcast_exception(TimedOut("slow"))
    topic = _classify_broadcast_exception(BadRequest("Message thread not found"))

    assert timeout.reason == "timeout"
    assert timeout.retryable is True
    assert topic.reason == "topic_missing"
    assert topic.retryable is True
