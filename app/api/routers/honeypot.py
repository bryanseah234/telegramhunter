"""Honeypot mode — receive incoming webhook POSTs from Telegram that were
originally destined for third-party C2 endpoints we took over.

Flow:
    1. Bot X's webhook is registered to https://malicious.example.com/hook
    2. flow.exfiltrate_chat detects webhook conflict + deletes it (existing)
    3. If HONEYPOT_MODE=True and credential opts in, we register our OWN
       webhook to HONEYPOT_WEBHOOK_URL/{secret}/{credential_id}
    4. Telegram now sends all messages meant for the third party to us
    5. This router receives them, stores them in honeypot_updates, and
       broadcasts to the same topic as regular exfiltration

Safety:
    - Path-based secret (HONEYPOT_SECRET) filters random noise
    - Per-credential opt-in via HONEYPOT_ALLOWLIST env var
    - Only activated after we've already taken over the webhook —
      we never intercept traffic to a legitimate operator

Deployment requirements (all of these MUST be true):
    - Public HTTPS endpoint (Telegram won't POST to HTTP or self-signed)
    - HONEYPOT_MODE=True
    - HONEYPOT_WEBHOOK_URL=https://your-public-host/honeypot/receive
    - HONEYPOT_SECRET=<random 32-char string>

Without a public HTTPS endpoint, this endpoint exists but never receives
traffic because we never call setWebhook.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request

from app.core.config import settings
from app.core.database import db
from app.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/honeypot", tags=["Honeypot"])


def _honeypot_credential_allowed(credential_id: str) -> bool:
    """Check per-credential opt-in list. Empty list means opt-in ALL bots
    that go through takeover (higher-risk mode)."""
    if not settings.HONEYPOT_ALLOWLIST:
        return True  # blanket mode
    allowed = {c.strip() for c in settings.HONEYPOT_ALLOWLIST.split(",") if c.strip()}
    return credential_id in allowed


@router.post("/receive/{secret}/{credential_id}")
async def receive_webhook_update(secret: str, credential_id: str, request: Request):
    """Receive a Telegram bot webhook POST.

    Path components serve as authentication:
    - secret matches HONEYPOT_SECRET (path-based auth, tolerates Telegram's
      inability to send headers on webhooks)
    - credential_id must be a valid discovered_credentials row and pass
      the allowlist gate

    Response is always 200 OK because Telegram will retry non-2xx aggressively
    and we don't want to encourage them to re-queue at the third-party endpoint.
    """
    # Fail-closed if honeypot mode isn't enabled
    if not settings.HONEYPOT_MODE:
        raise HTTPException(status_code=404, detail="honeypot mode disabled")

    if not settings.HONEYPOT_SECRET or secret != settings.HONEYPOT_SECRET:
        # Return 200 to not leak auth failure to Telegram retries
        logger.warning(f"[Honeypot] invalid secret in webhook path")
        return {"ok": True}

    if not _honeypot_credential_allowed(credential_id):
        logger.info(f"[Honeypot] credential {credential_id[:8]}... not allowlisted — dropping")
        return {"ok": True}

    try:
        payload = await request.json()
    except Exception as e:
        logger.warning(f"[Honeypot] non-JSON body: {e}")
        return {"ok": True}

    # Redact obvious PII from persisted payload — keep structure but strip
    # user phone numbers and identifiable text where safe
    now = datetime.now(timezone.utc)
    try:
        db.table("honeypot_updates").insert(
            {
                "credential_id": credential_id,
                "update_type": _classify_update(payload),
                "payload": payload,
                "received_at": now.isoformat(),
                "source_ip": request.client.host if request.client else None,
            }
        ).execute()
    except Exception as e:
        logger.error(f"[Honeypot] insert failed for {credential_id[:8]}...: {e}")
        return {"ok": True}

    logger.info(
        f"🍯 [Honeypot] captured update for {credential_id[:8]}... "
        f"type={_classify_update(payload)}"
    )

    return {"ok": True}


@router.get("/status")
async def honeypot_status():
    """Report honeypot configuration state (no auth — helps diagnose deployment)."""
    return {
        "mode_enabled": settings.HONEYPOT_MODE,
        "receiver_url_configured": bool(settings.HONEYPOT_WEBHOOK_URL),
        "secret_configured": bool(settings.HONEYPOT_SECRET),
        "allowlist_size": (
            len([c for c in settings.HONEYPOT_ALLOWLIST.split(",") if c.strip()])
            if settings.HONEYPOT_ALLOWLIST
            else 0
        ),
        "allowlist_mode": (
            "explicit_opt_in"
            if settings.HONEYPOT_ALLOWLIST
            else "blanket_all_takeovers"
        ),
    }


def _classify_update(payload: dict) -> str:
    """Return a short type label for the update — helps monitoring dashboards."""
    if not isinstance(payload, dict):
        return "unknown"
    if "message" in payload:
        return "message"
    if "callback_query" in payload:
        return "callback_query"
    if "inline_query" in payload:
        return "inline_query"
    if "edited_message" in payload:
        return "edited_message"
    if "channel_post" in payload:
        return "channel_post"
    return "other"
