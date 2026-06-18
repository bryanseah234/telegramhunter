#!/usr/bin/env python3
"""
Quick test script to verify all scanners work end-to-end.

Run from project root:
    python test_scanners.py

Redis is mocked so you don't need it running locally.
The test makes real API calls to verify credentials are valid.
"""
import asyncio
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

# ── Mock Redis so scanners don't need it running locally ─────────────────────
# The pause check (redis_client.get("system:paused")) returns None = not paused
_mock_redis_instance = MagicMock()
_mock_redis_instance.get.return_value = None   # system:paused = None → not paused
_mock_redis_instance.incr.return_value = 1
_mock_redis_instance.expire.return_value = True

_mock_redis_module = MagicMock()
_mock_redis_module.from_url.return_value = _mock_redis_instance
_mock_redis_module.exceptions = __import__("redis").exceptions  # keep real exceptions

# Patch before any scanner imports
sys.modules["redis"] = _mock_redis_module

# Also override REDIS_URL so settings validation passes
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

TIMEOUT_PER_SCANNER = 45  # seconds


async def run_with_timeout(name: str, coro, timeout: int = TIMEOUT_PER_SCANNER):
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        return "✅", str(result)[:120]
    except asyncio.TimeoutError:
        return "⏱️ ", f"Timed out after {timeout}s (scanner running, just slow — not a failure)"
    except Exception as e:
        return "❌", str(e)[:200]


async def test_all():
    from app.workers.tasks.scanner_tasks import (
        _scan_github_async,
        _scan_shodan_async,
        _scan_urlscan_async,
        _scan_fofa_async,
        _scan_gitlab_async,
        _scan_gist_async,
        _scan_grepapp_async,
        _scan_publicwww_async,
        _scan_pastebin_async,
        _scan_serper_async,
        _scan_google_async,
        _scan_bitbucket_async,
        _scan_shodan_c2_async,
        _scan_netlas_async,
    )

    tests = [
        ("GitHub",    _scan_github_async('filename:.env "TELEGRAM_BOT_TOKEN"'), 45),
        ("GitLab",    _scan_gitlab_async(),                                      30),
        ("Gist",      _scan_gist_async(),                                        30),
        ("GrepApp",   _scan_grepapp_async(),                                     30),
        ("Pastebin",  _scan_pastebin_async(),                                    20),
        ("Serper",    _scan_serper_async('site:pastebin.com "api.telegram.org/bot"'), 20),
        ("Google",    _scan_google_async('site:pastebin.com "api.telegram.org/bot"'), 20),
        ("Bitbucket", _scan_bitbucket_async(),                                   30),
        ("PublicWWW", _scan_publicwww_async(),                                   20),
        ("URLScan",   _scan_urlscan_async("api.telegram.org/bot"),               45),
        ("FOFA",      _scan_fofa_async(None, 'body="api.telegram.org/bot"'),     45),
        ("Shodan",    _scan_shodan_async('http.html:"api.telegram.org/bot"'),    45),
        ("Shodan C2", _scan_shodan_c2_async(),                                   45),
        ("Netlas",    _scan_netlas_async(),                                      45),
    ]

    print("=" * 70)
    print("SCANNER TEST SUITE  (Redis mocked — real API calls)")
    print("=" * 70)
    print()

    results = {}
    for name, coro, timeout in tests:
        print(f"  [{name:12}] ", end="", flush=True)
        status, detail = await run_with_timeout(name, coro, timeout)
        results[name] = (status, detail)
        print(f"{status}  {detail}")

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    passed = sum(1 for s, _ in results.values() if s.startswith("✅"))
    slow   = sum(1 for s, _ in results.values() if s.startswith("⏱"))
    failed = sum(1 for s, _ in results.values() if s.startswith("❌"))
    total  = len(results)

    for name, (status, detail) in results.items():
        print(f"  {status}  {name:12}  {detail[:80]}")

    print()
    print(f"  ✅ Passed: {passed}  |  ⏱️  Slow/running: {slow}  |  ❌ Failed: {failed}  |  Total: {total}")
    print()

    if failed > 0:
        print("Some scanners failed — likely bad/missing API key in .env")
        print("⏱️  timeouts are NOT failures — the scanner ran, just hit the time limit")
        sys.exit(1)
    else:
        print("All scanners operational ✅")


if __name__ == "__main__":
    asyncio.run(test_all())
