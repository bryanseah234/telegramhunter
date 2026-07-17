import pytest

from app.services import scanners_extension


class _FakeSearchcodeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *_args, **_kwargs):
        token = "1234567890:AbCdEfGhIjKlMnOpQrStUvWxYz_12345678"
        return _FakeSearchcodeResponse(
            {
                "results": [
                    {
                        "repo": "owner/repo",
                        "filename": ".env",
                        "lines": {
                            "1": f"TELEGRAM_BOT_TOKEN={token}",
                            "2": 'C2_API_URL="https://gateway.remote.net"',
                        },
                    }
                ]
            }
        )


class _FakeSearchcodeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_searchcode_results_include_infrastructure_context(monkeypatch):
    monkeypatch.setattr(
        scanners_extension,
        "get_async_http_client",
        lambda **_kwargs: _FakeSearchcodeClient(),
    )

    results = await scanners_extension.SearchcodeService().search("TELEGRAM_BOT_TOKEN")

    assert len(results) == 1
    assert results[0]["meta"]["source"] == "searchcode"
    assert results[0]["meta"]["infrastructure_context"]["co_located_endpoints"] == [
        "https://gateway.remote.net"
    ]
