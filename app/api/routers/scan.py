from fastapi import APIRouter, HTTPException, Request, Depends
from app.schemas.models import ScanRequest
from app.workers.celery_app import app as celery_app
from app.core.config import settings

router = APIRouter(prefix="/scan", tags=["Scanner"])

@router.post("/trigger")
def trigger_scan(request: ScanRequest):
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
        return {"status": "triggered", "task_id": str(task.id), "source": request.source, "query": request.query}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to queue task: {str(e)}")

@router.get("/trigger-dev/{source}")
def trigger_scan_dev(source: str, request: Request):
    """
    Dev-friendly GET endpoint to trigger standard scans from browser address bar.
    Only enabled in Development mode AND from Localhost.
    """
    if settings.ENV == "production":
        raise HTTPException(status_code=403, detail="Dev endpoints disabled in production.")
    
    # Strict Host Check
    host = request.headers.get("host", "").split(":")[0]
    if host not in ["localhost", "127.0.0.1"]:
        raise HTTPException(status_code=403, detail="Dev endpoints only accessible from localhost.")
        
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
