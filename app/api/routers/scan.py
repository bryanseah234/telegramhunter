from fastapi import APIRouter, HTTPException, Request
from app.schemas.models import ScanRequest
from app.workers.celery_app import app as celery_app
from app.core.config import settings

router = APIRouter(prefix="/scan", tags=["Scanner"])


@router.post("/trigger")
async def trigger_scan(request: ScanRequest):
    """
    Manually trigger an OSINT scan task.
    DISABLED in Production to prevent public abuse.
    Valid sources: shodan, fofa, github, gitlab, urlscan
    """
    if settings.ENV == "production":
        raise HTTPException(
            status_code=403,
            detail="Manual triggering is disabled in production. Scheduled tasks only."
        )

    task_name = f"scanner.scan_{request.source.lower()}"

    # Valid sources — censys and hybrid removed (no tasks exist for them)
    if request.source.lower() not in ["shodan", "fofa", "github", "gitlab", "urlscan"]:
        raise HTTPException(
            status_code=400,
            detail="Unsupported source. Use 'shodan', 'fofa', 'github', 'gitlab', or 'urlscan'."
        )

    try:
        task = celery_app.send_task(task_name, args=[request.query])

        from app.workers.tasks.flow_tasks import get_broadcaster
        await get_broadcaster().send_log(
            f"🚀 **API Trigger**: Queued `{task_name}` for query: `{request.query}`"
        )

        return {
            "status": "triggered",
            "task_id": str(task.id),
            "source": request.source,
            "query": request.query,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to queue task: {str(e)}")


@router.get("/trigger-dev/{source}")
def trigger_scan_dev(source: str, request: Request):
    """Dev GET endpoint — disabled. Use POST /scan/trigger instead."""
    raise HTTPException(
        status_code=403,
        detail="GET triggering is disabled. Use authenticated POST /scan/trigger."
    )
