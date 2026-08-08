import logging
from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.core.database import db
from app.core.auth import require_monitor_key
from app.core.audit import AuditEvent, AuditLogger
from app.schemas.models import CredentialOut, MessageOut, StatsOut

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/monitor",
    tags=["Monitor"],
    dependencies=[Depends(require_monitor_key)],
)


@router.get("/stats", response_model=StatsOut)
async def get_stats():
    """Get system stats. Requires X-Monitor-Key header."""
    try:
        c_res = db.table("discovered_credentials").select("*", count="exact").execute()
        total_creds = c_res.count if c_res.count is not None else len(c_res.data)

        ca_res = db.table("discovered_credentials").select("*", count="exact").eq("status", "active").execute()
        active_creds = ca_res.count if ca_res.count is not None else len(ca_res.data)

        m_res = db.table("exfiltrated_messages").select("*", count="exact").execute()
        total_msgs = m_res.count if m_res.count is not None else len(m_res.data)

        b_res = db.table("exfiltrated_messages").select("*", count="exact").eq("is_broadcasted", True).execute()
        bc_msgs = b_res.count if b_res.count is not None else len(b_res.data)

        return StatsOut(
            credentials_total=total_creds,
            credentials_active=active_creds,
            messages_exfiltrated=total_msgs,
            messages_broadcasted=bc_msgs
        )
    except Exception:
        logger.exception("monitor/stats query failed")
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/credentials", response_model=List[CredentialOut])
async def list_credentials(
    limit: int = 100,
    sort_by: str = "created_at",
    order: str = "desc",
):
    """List recent credentials.

    Args:
        limit: 1-1000 (clamped). Default 100.
        sort_by: one of 'created_at' (default), 'updated_at', 'confidence_score',
                 'chat_member_count'. The latter two read from meta jsonb.
        order: 'desc' (default) or 'asc'.

    Requires X-Monitor-Key header.
    """
    limit = max(1, min(limit, 1000))
    desc = order.lower() != "asc"

    # Whitelist sort keys — never trust user input as a column reference.
    # confidence_score and chat_member_count are STORED generated columns
    # (see migration 004) so sorts are real INT, not jsonb-string lex sort.
    allowed_sorts = {"created_at", "updated_at", "confidence_score", "chat_member_count"}
    sort_expr = sort_by if sort_by in allowed_sorts else "created_at"

    try:
        q = db.table("discovered_credentials").select("*")
        if sort_expr in ("confidence_score", "chat_member_count"):
            try:
                # nullslast keeps unscored legacy rows out of the way on desc.
                res = q.order(sort_expr, desc=desc, nullsfirst=not desc).limit(limit).execute()
            except Exception as e:
                # Migration 004 not applied yet — column doesn't exist. Fall back.
                msg = str(e).lower()
                if "confidence_score" in msg or "chat_member_count" in msg or "column" in msg:
                    res = (
                        db.table("discovered_credentials")
                        .select("*")
                        .order("created_at", desc=True)
                        .limit(limit)
                        .execute()
                    )
                else:
                    raise
        else:
            res = q.order(sort_expr, desc=desc).limit(limit).execute()
        return res.data
    except Exception:
        logger.exception("monitor/credentials query failed")
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/messages", response_model=List[MessageOut])
async def list_messages(limit: int = 100):
    """List recent exfiltrated messages. Requires X-Monitor-Key header."""
    limit = max(1, min(limit, 1000))  # Clamp to [1, 1000]
    try:
        res = db.table("exfiltrated_messages").select("*").order("created_at", desc=True).limit(limit).execute()
        return res.data
    except Exception:
        logger.exception("monitor/messages query failed")
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/broadcasts/pending")
def list_pending_broadcasts(limit: int = 100, failed_only: bool = False):
    """List unbroadcasted messages and retry metadata for operator triage."""
    limit = max(1, min(limit, 1000))
    columns = (
        "id, credential_id, telegram_msg_id, sender_name, media_type, "
        "is_broadcasted, broadcast_error, broadcast_attempts, next_retry_at, "
        "broadcast_claimed_at, created_at"
    )
    legacy_columns = (
        "id, credential_id, telegram_msg_id, sender_name, media_type, "
        "is_broadcasted, broadcast_claimed_at, created_at"
    )
    try:
        query = (
            db.table("exfiltrated_messages")
            .select(columns)
            .eq("is_broadcasted", False)
        )
        if failed_only:
            query = query.not_.is_("broadcast_error", "null")
        res = query.order("created_at", desc=False).limit(limit).execute()
        return {
            "status": "ok",
            "schema": "broadcast_reliability",
            "messages": res.data or [],
        }
    except Exception as exc:
        text = str(exc).lower()
        if not any(
            column in text
            for column in ("broadcast_error", "broadcast_attempts", "next_retry_at")
        ):
            logger.exception("monitor/broadcasts/pending query failed")
            raise HTTPException(status_code=500, detail="Internal error") from exc

        if failed_only:
            return {
                "status": "schema_missing",
                "schema": "legacy",
                "messages": [],
                "warning": "broadcast reliability columns are not available",
            }
        try:
            res = (
                db.table("exfiltrated_messages")
                .select(legacy_columns)
                .eq("is_broadcasted", False)
                .order("created_at", desc=False)
                .limit(limit)
                .execute()
            )
            return {
                "status": "ok",
                "schema": "legacy",
                "messages": res.data or [],
                "warning": "broadcast reliability columns are not available",
            }
        except Exception as fallback_exc:
            logger.exception("monitor/broadcasts/pending legacy query failed")
            raise HTTPException(status_code=500, detail="Internal error") from fallback_exc


@router.post("/broadcasts/{message_id}/retry")
def retry_pending_broadcast(message_id: str):
    """Clear retry delay/claim for one unbroadcasted message and dispatch broadcaster."""
    try:
        res = (
            db.table("exfiltrated_messages")
            .update({"broadcast_claimed_at": None, "next_retry_at": None})
            .eq("id", message_id)
            .eq("is_broadcasted", False)
            .execute()
        )
    except Exception as exc:
        text = str(exc).lower()
        if "next_retry_at" not in text:
            logger.exception("monitor/broadcasts retry update failed")
            raise HTTPException(status_code=500, detail="Internal error") from exc
        try:
            res = (
                db.table("exfiltrated_messages")
                .update({"broadcast_claimed_at": None})
                .eq("id", message_id)
                .eq("is_broadcasted", False)
                .execute()
            )
        except Exception as fallback_exc:
            logger.exception("monitor/broadcasts retry legacy update failed")
            raise HTTPException(status_code=500, detail="Internal error") from fallback_exc

    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="unbroadcasted message not found")
    row = rows[0]

    dispatched = False
    try:
        from app.workers.celery_app import app as celery_app

        celery_app.send_task("flow.broadcast_pending")
        dispatched = True
    except Exception as exc:
        logger.warning("failed to dispatch broadcast retry task: %s", exc)

    AuditLogger.log(
        AuditEvent.BROADCAST_RETRY_REQUESTED,
        credential_id=row.get("credential_id"),
        user="monitor_api",
        details={
            "message_id": message_id,
            "broadcast_dispatched": dispatched,
            "mode": "single",
        },
    )

    return {
        "status": "ok",
        "message_id": message_id,
        "updated": True,
        "broadcast_dispatched": dispatched,
    }


@router.get("/webhooks")
async def list_captured_webhooks(limit: int = 200):
    """List credentials with a captured webhook URL (someone else's C2 / research endpoint).

    Surfaces `meta.webhook_url` and related fields that `validation_tasks.py` records
    when it hits a bot with an active webhook. Useful for OSINT pivoting on third-party
    infrastructure hosting the stolen tokens.

    Requires X-Monitor-Key header.
    """
    limit = max(1, min(limit, 1000))
    try:
        # Fetch a bounded slice; population is small (~2k credentials) so
        # a Python-side filter on meta->>webhook_url is cheap.
        res = (
            db.table("discovered_credentials")
            .select("id, bot_username, bot_id, chat_name, status, meta, created_at, updated_at")
            .order("created_at", desc=True)
            .limit(2000)
            .execute()
        )
        out = []
        for row in res.data or []:
            meta = row.get("meta") or {}
            webhook_url = meta.get("webhook_url")
            if not webhook_url:
                continue
            out.append(
                {
                    "credential_id": row.get("id"),
                    "bot_username": row.get("bot_username"),
                    "bot_id": row.get("bot_id"),
                    "chat_name": row.get("chat_name"),
                    "status": row.get("status"),
                    "webhook_url": webhook_url,
                    "webhook_last_error": meta.get("webhook_last_error"),
                    "webhook_pending_updates": meta.get("webhook_pending_update_count"),
                    "webhook_ip_address": meta.get("webhook_ip_address"),
                    "webhook_captured_at": meta.get("webhook_captured_at"),
                    "webhook_probe": meta.get("webhook_probe"),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )
            if len(out) >= limit:
                break
        return out
    except Exception:
        logger.exception("monitor/webhooks query failed")
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/search")
def search_messages(
    q: str,
    limit: int = 50,
    media_only: bool = False,
    since_hours: int | None = None,
):
    """Full-text search over exfiltrated_messages.content + sender_name.

    Uses pg_trgm GIN index (migration 20260803000010_message_fts.sql) for
    fast LIKE '%pattern%' queries at scale (283k+ messages).

    Params:
    - q: search term (case-insensitive substring match)
    - limit: max results (default 50, cap 500)
    - media_only: if True, only rows with media_type != 'text'
    - since_hours: filter to messages created in last N hours
    """
    if not q or len(q.strip()) < 2:
        raise HTTPException(status_code=400, detail="query must be at least 2 chars")

    # Whitelist: only allow alphanumeric + space + safe punctuation. This
    # blocks PostgREST metacharacters that could smuggle extra .or_() clauses
    # into the filter (e.g. ',is.null),content.eq.X('). Wildcards and quotes
    # explicitly rejected.
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9 _.@\-]{2,80}", q.strip()):
        raise HTTPException(
            status_code=400,
            detail="query must match [A-Za-z0-9 _.@-]{2,80} — no wildcards, punctuation, or spaces at limits",
        )

    limit = min(max(limit, 1), 500)
    q_pattern = f"%{q.strip()}%"

    try:
        # PostgREST syntax: content=ilike.*bitcoin*
        query = (
            db.table("exfiltrated_messages")
            .select(
                "id, credential_id, telegram_msg_id, sender_name, content, "
                "media_type, is_broadcasted, created_at, broadcasted_at"
            )
            .or_(f"content.ilike.{q_pattern},sender_name.ilike.{q_pattern}")
            .order("created_at", desc=True)
            .limit(limit)
        )

        if media_only:
            query = query.neq("media_type", "text")

        if since_hours and since_hours > 0:
            from datetime import datetime, timedelta, timezone

            since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
            query = query.gte("created_at", since)

        res = query.execute()
    except Exception:
        logger.exception("monitor/search query failed")
        raise HTTPException(status_code=500, detail="Internal error")

    matches = res.data or []
    return {
        "query": q,
        "match_count": len(matches),
        "limit": limit,
        "matches": matches,
    }



@router.get("/operators")
def get_c2_operators(limit: int = 20):
    """Third-party operator identification via webhook fingerprints.

    Clusters captured webhook URLs by:
    - Root TLS SAN pattern (e.g. *.up.railway.app, *.onrender.com)
    - Shodan organization (from IP resolution)
    - URL path pattern (e.g. /hook/{id}/*, /webhook/bot/*)
    - Hostname base (e.g. ssh.inkognit.org for :port variants)

    Returns clusters ranked by member bot count — largest cluster = most
    prolific third-party operator.
    """
    from collections import defaultdict
    from urllib.parse import urlparse

    try:
        res = (
            db.table("discovered_credentials")
            .select("id, bot_username, meta")
            .not_.is_("meta->>webhook_url", "null")
            .limit(2000)
            .execute()
        )
    except Exception:
        logger.exception("monitor/operators query failed")
        raise HTTPException(status_code=500, detail="Internal error")

    # Multi-dimensional clustering
    by_san: dict = defaultdict(list)
    by_org: dict = defaultdict(list)
    by_hostname: dict = defaultdict(list)
    by_path_pattern: dict = defaultdict(list)

    import re

    for row in res.data or []:
        meta = row.get("meta") or {}
        url = meta.get("webhook_url")
        if not url:
            continue
        bot_username = row.get("bot_username") or "?"
        probe = meta.get("webhook_probe") or {}

        # Parse URL
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname or ""
            path = parsed.path or ""
        except Exception:
            continue

        # By TLS SAN root pattern (wildcard cluster)
        for san in probe.get("tls_san", []) or []:
            if san.startswith("*."):
                by_san[san].append({"bot": bot_username, "url": url})
                break  # first wildcard is enough

        # By Shodan org
        shodan = probe.get("shodan") or {}
        orgs = set()
        for _ip, info in shodan.items():
            if isinstance(info, dict) and info.get("org"):
                orgs.add(str(info["org"]))
        for org in orgs:
            by_org[org].append({"bot": bot_username, "url": url})

        # By hostname (strip port)
        if hostname:
            by_hostname[hostname].append({"bot": bot_username, "url": url})

        # By URL path pattern — normalize IDs to {id} placeholder
        pattern = re.sub(r"/\d{6,}", "/{id}", path)
        pattern = re.sub(r"/[a-f0-9]{32,}", "/{hash}", pattern)
        pattern = re.sub(r"/\d+:[A-Za-z0-9_-]{30,}", "/{token}", pattern)
        if pattern and pattern != "/":
            by_path_pattern[pattern].append({"bot": bot_username, "url": url})

    def _rank(bucket: dict, top: int) -> list:
        ranked = [
            {
                "key": k,
                "count": len(v),
                "sample_bots": [x["bot"] for x in v[:5]],
                "sample_url": v[0]["url"] if v else None,
            }
            for k, v in bucket.items()
            if len(v) >= 2
        ]
        ranked.sort(key=lambda x: x["count"], reverse=True)
        return ranked[:top]

    return {
        "total_webhook_rows": len(res.data or []),
        "clusters_by_tls_san": _rank(by_san, limit),
        "clusters_by_shodan_org": _rank(by_org, limit),
        "clusters_by_hostname": _rank(by_hostname, limit),
        "clusters_by_url_path_pattern": _rank(by_path_pattern, limit),
    }
