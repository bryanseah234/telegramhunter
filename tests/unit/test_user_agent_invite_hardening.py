from types import SimpleNamespace

import pytest

from app.services import user_agent_srv
from app.services.user_agent_srv import UserAgentService


class _FakeFloodWaitError(Exception):
    pass


class _FloodingClient:
    def __init__(self):
        self.request_count = 0

    async def get_entity(self, _target):
        return SimpleNamespace(id=12345)

    async def __call__(self, _request):
        self.request_count += 1
        raise _FakeFloodWaitError("rate limited")


@pytest.mark.asyncio
async def test_invite_bot_to_group_does_not_fallback_on_flood_wait(monkeypatch):
    service = UserAgentService()
    client = _FloodingClient()
    handled_errors = []
    disconnected = []

    async def start():
        return True

    async def disconnect():
        disconnected.append(True)

    async def handle_flood_error(exc):
        handled_errors.append(exc)

    monkeypatch.setattr(user_agent_srv.errors, "FloodWaitError", _FakeFloodWaitError)
    monkeypatch.setattr(service, "start", start)
    monkeypatch.setattr(service, "_disconnect", disconnect)
    monkeypatch.setattr(service, "_handle_flood_error", handle_flood_error)
    service.client = client

    result = await service.invite_bot_to_group("example_bot", -100123)

    assert result is False
    assert client.request_count == 1
    assert len(handled_errors) == 1
    assert disconnected == [True]
