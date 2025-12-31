from fastapi import APIRouter, HTTPException, Request, Depends
from app.schemas.models import ScanRequest
from app.workers.celery_app import app as celery_app
from app.core.config import settings
from app.services.broadcaster_srv import broadcaster_service

router = APIRouter(prefix="/scan", tags=["Scanner"])

@router.post("/trigger")
async def trigger_scan(request: ScanRequest):
    """
    Manually trigger an OSINT scan task.
    DISABLED in Production to prevent public abuse.
    """
    if settings.ENV == "production":
         raise HTTPException(status_code=403, detail="Manual triggering is disabled in production via POST. Scheduled tasks only.")

    task_name = f"scanner.scan_{request.source.lower()}"
    
    # Simple validation of supported sources
    if request.source.lower() not in ["shodan", "fofa", "github", "censys", "hybrid"]:
        raise HTTPException(status_code=400, detail="Unsupported source. Use 'shodan', 'fofa', 'github', 'censys', or 'hybrid'.")

    try:
        # Send task
        task = celery_app.send_task(task_name, args=[request.query])
        
        # Log to Telegram
        await broadcaster_service.send_log(f"🚀 **API Trigger**: Queued `{task_name}` for query: `{request.query}`")
        
        return {"status": "triggered", "task_id": str(task.id), "source": request.source, "query": request.query}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to queue task: {str(e)}")

@router.get("/trigger-dev/{source}")
def trigger_scan_dev(source: str, request: Request):
    """
    Dev-friendly GET endpoint.
    DISABLED by user request. Use manual script or POST (if authenticated) during dev.
    """
    raise HTTPException(status_code=403, detail="GET triggering is disabled. Use 'run_local_scan.bat' or authenticated POST.")
    
    # Previous Logic (Preserved for reference but unreachable):
    # if settings.ENV == "production": ...
    # if host not in ["localhost", "127.0.0.1"]: ...
        
    source = source.lower()
    default_queries = {
        "shodan": "product:Telegram",
        "fofa": 'body="api.telegram.org"',
        "github": "filename:.env api.telegram.org",
        "censys": "services.port: 443",
        "hybrid": "api.telegram.org"
    }

    if source not in default_queries:
        raise HTTPException(status_code=400, detail=f"Unknown source. Options: {list(default_queries.keys())}")
    
    query = default_queries[source]
    task_name = f"scanner.scan_{source}"
    
    try:
        task = celery_app.send_task(task_name, args=[query])
        return {
            "status": "triggered (DEV MODE)", 
            "task_id": str(task.id), 
            "source": source, 
            "default_query": query,
            "note": "Production POST endpoint is disabled."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
