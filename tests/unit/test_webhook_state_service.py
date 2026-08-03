import pytest

from app.services._scraper.results import ScrapeReason
from app.services._scraper.strategies import BotApiUpdateReader, WebhookStateService


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(self._payload)

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.post_calls = []

    async def get(self, *_args, **_kwargs):
        return self.responses.pop(0)

    async def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_webhook_present_delete_denied_is_conflict():
    client = _FakeClient([
        _Resp(payload={"ok": True, "result": {"url": "https://example.test/hook"}})
    ])
    service = WebhookStateService(allow_delete=False)

    decision = await service.prepare_polling("123:ABC", client)

    assert decision.can_poll is False
    assert decision.attempt.reason == ScrapeReason.WEBHOOK_CONFLICT
    assert client.post_calls == []


@pytest.mark.asyncio
async def test_webhook_present_delete_allowed_retries_polling():
    client = _FakeClient([
        _Resp(payload={"ok": True, "result": {"url": "https://example.test/hook"}}),
        _Resp(payload={"ok": True, "result": True}),
    ])
    service = WebhookStateService(allow_delete=True)

    decision = await service.prepare_polling("123:ABC", client)

    assert decision.can_poll is True
    assert decision.attempt.success is True
    assert len(client.post_calls) == 1


@pytest.mark.asyncio
async def test_webhook_delete_failure_is_conflict():
    client = _FakeClient([
        _Resp(payload={"ok": True, "result": {"url": "https://example.test/hook"}}),
        _Resp(status_code=500, payload={"ok": False, "description": "failed"}),
    ])
    service = WebhookStateService(allow_delete=True)

    decision = await service.prepare_polling("123:ABC", client)

    assert decision.can_poll is False
    assert decision.attempt.reason == ScrapeReason.WEBHOOK_CONFLICT


@pytest.mark.asyncio
async def test_bot_api_reader_classifies_getupdates_409(monkeypatch):
    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.client = _FakeClient([
                _Resp(payload={"ok": True, "result": {}}),
                _Resp(status_code=409, payload={"ok": False}, text="Conflict: webhook"),
            ])

        async def __aenter__(self):
            return self.client

        async def __aexit__(self, *_args):
            return False

    from app.services._scraper import strategies

    monkeypatch.setattr(strategies.httpx, "AsyncClient", _FakeAsyncClient)
    reader = BotApiUpdateReader(
        webhook_service=WebhookStateService(allow_delete=False),
        media_formatter=lambda _payload: ("text", {}),
        is_monitor_bot=lambda _token: False,
        is_monitor_group=lambda _chat_id: False,
    )

    outcome = await reader.read("123:ABC")

    assert outcome.terminal is True
    assert outcome.attempt.reason == ScrapeReason.WEBHOOK_CONFLICT
