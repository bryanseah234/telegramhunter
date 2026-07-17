import types

import pytest

from app.workers.tasks import flow_tasks


class _FakeTable:
    def __init__(self):
        self.upsert_calls = []

    def upsert(self, rows, **kwargs):
        self.upsert_calls.append((rows, kwargs))
        return {"rows": rows, "kwargs": kwargs}


class _FakeDb:
    def __init__(self):
        self.telemetry_table = _FakeTable()

    def table(self, name):
        assert name == "telemetry_indicators"
        return self.telemetry_table


@pytest.mark.asyncio
async def test_index_telemetry_indicators_bounds_upsert_with_wait_for(monkeypatch):
    fake_db = _FakeDb()
    observed = {}

    async def fake_async_execute(query_builder):
        observed["query_builder"] = query_builder
        return types.SimpleNamespace(data=[{"id": "indicator-id"}])

    async def fake_wait_for(awaitable, timeout):
        observed["timeout"] = timeout
        return await awaitable

    monkeypatch.setattr(flow_tasks, "db", fake_db)
    monkeypatch.setattr(flow_tasks, "async_execute", fake_async_execute)
    monkeypatch.setattr(flow_tasks.asyncio, "wait_for", fake_wait_for)

    count = await flow_tasks._index_telemetry_indicators(
        [
            {
                "id": "message-id",
                "credential_id": "credential-id",
                "telegram_msg_id": 7,
                "content": "https://Example.com/Path/#frag",
                "media_type": "text",
            }
        ]
    )

    assert count == 1
    assert observed["timeout"] == 10.0
    assert fake_db.telemetry_table.upsert_calls[0][1] == {
        "on_conflict": "message_id,indicator_type,indicator_value",
        "ignore_duplicates": True,
    }


@pytest.mark.asyncio
async def test_index_telemetry_indicators_alerts_on_wallet_insert(monkeypatch):
    fake_db = _FakeDb()
    alerts = []

    async def fake_async_execute(_query_builder):
        return types.SimpleNamespace(
            data=[
                {
                    "id": "indicator-id",
                    "indicator_type": "wallet_address",
                    "indicator_value": "0x1111111111111111111111111111111111111111",
                }
            ]
        )

    async def fake_send_alert(message):
        alerts.append(message)

    monkeypatch.setattr(flow_tasks, "db", fake_db)
    monkeypatch.setattr(flow_tasks, "async_execute", fake_async_execute)
    monkeypatch.setattr(flow_tasks, "_send_alert", fake_send_alert)

    count = await flow_tasks._index_telemetry_indicators(
        [
            {
                "id": "message-id",
                "credential_id": "credential-id",
                "telegram_msg_id": 7,
                "content": "wallet 0x1111111111111111111111111111111111111111",
                "media_type": "text",
            }
        ]
    )

    assert count == 1
    assert alerts == [
        "**Telemetry Entity Indexed**\n"
        "New financial or high-priority infrastructure strings: `0x1111111111111111111111111111111111111111`"
    ]
