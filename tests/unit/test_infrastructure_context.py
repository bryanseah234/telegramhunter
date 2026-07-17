from app.services.scanners import extract_infrastructure_context


def test_extract_infrastructure_context_keeps_remote_adjacent_endpoints():
    token = "1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    code_text = f"""
TELEGRAM_BOT_TOKEN={token}
C2_API_URL="https://gateway.remote.net"
DB_HOST=localhost
EDGE_DOMAIN=api.remote.net
LOCAL_IP=127.0.0.1
"""

    context = extract_infrastructure_context(
        code_text,
        {"path": ".env", "repository": "owner/repo"},
        token=token,
    )

    assert context["source_file_path"] == ".env"
    assert context["repository_uri"] == "owner/repo"
    assert context["co_located_endpoints"] == [
        "api.remote.net",
        "https://gateway.remote.net",
    ]


def test_extract_infrastructure_context_returns_empty_when_only_local_values_exist():
    token = "1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    code_text = f"""
TELEGRAM_BOT_TOKEN={token}
API_URL=http://localhost:8000
BIND_IP=0.0.0.0
"""

    assert extract_infrastructure_context(code_text, {}, token=token) == {}
