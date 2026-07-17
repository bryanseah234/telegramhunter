import httpx
import pytest

from app.services import scraper_srv


class _FakeTelegramClient:
    def __init__(self, mode):
        self.mode = mode

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url):
        async def _request():
            if self.mode == "unauthorized":
                status = 401 if url.endswith("/getWebhookInfo") else 200
                return httpx.Response(
                    status,
                    json={"ok": status == 200, "result": []},
                    request=httpx.Request("GET", url),
                )
            if self.mode == "ok":
                result = {}
                if url.endswith("/getWebhookInfo"):
                    result = {"url": "https://Example.com/hook", "ip_address": "203.0.113.10"}
                if url.endswith("/getMyCommands"):
                    result = [{"command": "start", "description": "Start"}]
                if url.endswith("/getMyDescription"):
                    result = {"description": "Service bot"}
                return httpx.Response(
                    200,
                    json={"ok": True, "result": result},
                    request=httpx.Request("GET", url),
                )
            raise httpx.TimeoutException("timed out", request=httpx.Request("GET", url))

        return _request()


class _FakeDb:
    def __init__(self):
        self.rpc_calls = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        return {"name": name, "params": params}


@pytest.mark.asyncio
async def test_probe_gateway_telemetry_returns_cleanly_on_401(monkeypatch):
    monkeypatch.setattr(scraper_srv.security, "decrypt", lambda raw: "123:token")
    monkeypatch.setattr(
        scraper_srv,
        "get_async_http_client",
        lambda timeout: _FakeTelegramClient("unauthorized"),
    )

    async def fail_execute(_query_builder):
        raise AssertionError("401 webhook probe should not persist telemetry")

    monkeypatch.setattr(scraper_srv, "_async_execute", fail_execute)

    await scraper_srv.scraper_service._probe_gateway_telemetry("encrypted", "cred-id")


@pytest.mark.asyncio
async def test_probe_gateway_telemetry_uses_atomic_rpc(monkeypatch):
    fake_db = _FakeDb()
    monkeypatch.setattr(scraper_srv.security, "decrypt", lambda raw: "123:token")
    monkeypatch.setattr(scraper_srv, "db", fake_db)
    monkeypatch.setattr(
        scraper_srv,
        "get_async_http_client",
        lambda timeout: _FakeTelegramClient("ok"),
    )

    async def fake_execute(_query_builder):
        return None

    monkeypatch.setattr(scraper_srv, "_async_execute", fake_execute)

    await scraper_srv.scraper_service._probe_gateway_telemetry("encrypted", "cred-id")

    assert fake_db.rpc_calls == [
        (
            "patch_credential_meta",
            {
                "target_id": "cred-id",
                "patch_key": "gateway_telemetry",
                "patch_data": {
                    "configured_webhook_url": "https://Example.com/hook",
                    "resolved_ip_address": "203.0.113.10",
                    "command_dictionary": [{"command": "start", "description": "Start"}],
                    "service_description": "Service bot",
                    "last_error_info": None,
                    "last_error_date": None,
                    "allowed_updates": None,
                    "probed_at": fake_db.rpc_calls[0][1]["patch_data"]["probed_at"],
                },
            },
        )
    ]


@pytest.mark.asyncio
async def test_probe_gateway_telemetry_handles_rpc_failure_best_effort(monkeypatch):
    fake_db = _FakeDb()
    monkeypatch.setattr(scraper_srv.security, "decrypt", lambda raw: "123:token")
    monkeypatch.setattr(scraper_srv, "db", fake_db)
    monkeypatch.setattr(
        scraper_srv,
        "get_async_http_client",
        lambda timeout: _FakeTelegramClient("timeout"),
    )

    async def fail_execute(_query_builder):
        raise RuntimeError("rpc unavailable")

    monkeypatch.setattr(scraper_srv, "_async_execute", fail_execute)

    await scraper_srv.scraper_service._probe_gateway_telemetry("encrypted", "cred-id")

    assert fake_db.rpc_calls[0][0] == "patch_credential_meta"
