import asyncio

import pytest

from app.services._scraper.lifecycle import TelegramClientLifecycle


@pytest.mark.asyncio
async def test_lifecycle_disconnects_on_timeout():
    disconnected = []

    async def operation():
        await asyncio.sleep(1)

    async def disconnect():
        disconnected.append(True)

    with pytest.raises(asyncio.TimeoutError):
        await TelegramClientLifecycle.run_with_disconnect(
            operation,
            disconnect=disconnect,
            timeout=0.01,
        )

    assert disconnected == [True]


@pytest.mark.asyncio
async def test_lifecycle_disconnects_on_exception():
    disconnected = []

    async def operation():
        raise RuntimeError("network dropped")

    async def disconnect():
        disconnected.append(True)

    with pytest.raises(RuntimeError):
        await TelegramClientLifecycle.run_with_disconnect(operation, disconnect=disconnect)

    assert disconnected == [True]


@pytest.mark.asyncio
async def test_lifecycle_disconnects_on_cancellation():
    disconnected = []

    async def operation():
        await asyncio.sleep(10)

    async def disconnect():
        disconnected.append(True)

    task = asyncio.create_task(
        TelegramClientLifecycle.run_with_disconnect(operation, disconnect=disconnect)
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert disconnected == [True]


@pytest.mark.asyncio
async def test_bot_manager_disconnects_partial_client_on_start_timeout(monkeypatch):
    from app.services import bot_manager_srv

    disconnected = []

    class FakeClient:
        async def start(self, bot_token):
            await asyncio.sleep(1)

        async def disconnect(self):
            disconnected.append(True)

    monkeypatch.setattr(bot_manager_srv, "_CLIENT_START_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        bot_manager_srv,
        "TelegramClient",
        lambda *args, **kwargs: FakeClient(),
    )

    manager = bot_manager_srv.BotClientManager()

    with pytest.raises(asyncio.TimeoutError):
        await manager.get_client("123456:ABCdefGhijk")

    assert disconnected == [True]
    assert manager._clients == {}
