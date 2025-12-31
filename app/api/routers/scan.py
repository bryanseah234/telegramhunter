from fastapi import APIRouter, HTTPException
from app.schemas.models import ScanRequest
from app.workers.celery_app import app as celery_app

router = APIRouter(prefix="/scan", tags=["Scanner"])

@router.post("/trigger")
def trigger_scan(request: ScanRequest):
    """
    Manually trigger an OSINT scan task.
    """
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
