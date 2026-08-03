#!/usr/bin/env python
"""
Run a controlled Telegram behavior matrix and print structured JSON.

Input cases come from --matrix JSON or TELEGRAM_PROBE_MATRIX. Each case:
{
  "name": "bot-in-group",
  "bot_token": "123:ABC",
  "chat_id": -100123,
  "allow_delete_webhook": false
}
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


sys.path.insert(0, str(_repo_root()))


def _redact_token(token: str | None) -> str | None:
    if not token or ":" not in token:
        return None
    bot_id = token.split(":", 1)[0]
    return f"{bot_id}:<redacted>"


def _load_matrix(path: str | None) -> list[dict[str, Any]]:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    raw = os.getenv("TELEGRAM_PROBE_MATRIX")
    if raw:
        return json.loads(raw)
    token = os.getenv("TELEGRAM_PROBE_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_PROBE_CHAT_ID")
    if token:
        return [
            {
                "name": "default",
                "bot_token": token,
                "chat_id": int(chat_id) if chat_id and chat_id.lstrip("-").isdigit() else chat_id,
                "allow_delete_webhook": False,
            }
        ]
    return []


async def _probe_case(case: dict[str, Any]) -> dict[str, Any]:
    token = case.get("bot_token")
    chat_id = case.get("chat_id")
    allow_delete = bool(case.get("allow_delete_webhook"))
    output: dict[str, Any] = {
        "name": case.get("name") or "unnamed",
        "bot_token": _redact_token(token),
        "chat_id": chat_id,
        "allow_delete_webhook": allow_delete,
        "started_at": datetime.now(UTC).isoformat(),
        "checks": {},
    }
    if not token:
        output["status"] = "skipped"
        output["reason"] = "missing_bot_token"
        return output

    base_url = f"https://api.telegram.org/bot{token}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            me = await client.get(f"{base_url}/getMe")
            me_body = me.json() if me.headers.get("content-type", "").startswith("application/json") else {}
            output["checks"]["getMe"] = {
                "status_code": me.status_code,
                "ok": bool(me_body.get("ok")),
                "bot_id": (me_body.get("result") or {}).get("id") if isinstance(me_body, dict) else None,
                "username": (me_body.get("result") or {}).get("username") if isinstance(me_body, dict) else None,
            }
        except Exception as exc:
            output["checks"]["getMe"] = {"error": str(exc)[:300]}

        try:
            webhook = await client.get(f"{base_url}/getWebhookInfo")
            webhook_body = webhook.json()
            webhook_result = webhook_body.get("result") or {}
            output["checks"]["getWebhookInfo"] = {
                "status_code": webhook.status_code,
                "ok": bool(webhook_body.get("ok")),
                "webhook_present": bool(webhook_result.get("url")),
                "pending_update_count": webhook_result.get("pending_update_count"),
                "last_error_message": webhook_result.get("last_error_message"),
            }
            if webhook_result.get("url") and allow_delete:
                deleted = await client.post(f"{base_url}/deleteWebhook")
                output["checks"]["deleteWebhook"] = {
                    "status_code": deleted.status_code,
                    "ok": bool(deleted.json().get("ok")) if deleted.content else False,
                }
        except Exception as exc:
            output["checks"]["getWebhookInfo"] = {"error": str(exc)[:300]}

        try:
            updates = await client.get(f"{base_url}/getUpdates", params={"limit": 100})
            updates_body = updates.json() if updates.content else {}
            update_rows = updates_body.get("result") if isinstance(updates_body, dict) else []
            output["checks"]["getUpdates"] = {
                "status_code": updates.status_code,
                "ok": bool(updates_body.get("ok")) if isinstance(updates_body, dict) else False,
                "update_count": len(update_rows or []),
                "chat_ids": sorted(
                    {
                        item.get("message", {}).get("chat", {}).get("id")
                        or item.get("channel_post", {}).get("chat", {}).get("id")
                        for item in (update_rows or [])
                        if isinstance(item, dict)
                    }
                    - {None}
                ),
            }
        except Exception as exc:
            output["checks"]["getUpdates"] = {"error": str(exc)[:300]}

        if chat_id:
            try:
                chat = await client.get(f"{base_url}/getChat", params={"chat_id": chat_id})
                chat_body = chat.json() if chat.content else {}
                chat_result = chat_body.get("result") if isinstance(chat_body, dict) else {}
                output["checks"]["getChat"] = {
                    "status_code": chat.status_code,
                    "ok": bool(chat_body.get("ok")) if isinstance(chat_body, dict) else False,
                    "type": (chat_result or {}).get("type") if isinstance(chat_result, dict) else None,
                    "title": (chat_result or {}).get("title") if isinstance(chat_result, dict) else None,
                    "description": (chat_body.get("description") or "")[:200]
                    if isinstance(chat_body, dict)
                    else None,
                }
            except Exception as exc:
                output["checks"]["getChat"] = {"error": str(exc)[:300]}

    output["finished_at"] = datetime.now(UTC).isoformat()
    output["status"] = "ok"
    return output


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", help="Path to JSON array of probe cases")
    args = parser.parse_args()
    cases = _load_matrix(args.matrix)
    if not cases:
        print(json.dumps({"status": "skipped", "reason": "no_probe_cases"}, indent=2))
        return 0
    results = [await _probe_case(case) for case in cases]
    print(json.dumps({"status": "ok", "results": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
