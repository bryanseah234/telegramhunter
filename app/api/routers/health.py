"""
Health check router for monitoring system status.
Provides endpoints to check database, Redis, and service health.
"""
from fastapi import APIRouter, HTTPException
from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/")
async def health_check():
    """
    Basic health check endpoint.
    Returns 200 if API is responsive.
    """
    return {"status": "healthy", "service": "telegram-hunter-api"}


@router.get("/detailed")
async def detailed_health():
    """
    Detailed health check with dependency status.
    Checks database, Redis, and optional services.
    """
    health_status = {
        "status": "healthy",
        "checks": {}
    }
    
    # Check Database
    try:
        from app.core.db_retry import DatabaseHealth
        DatabaseHealth.check_connection()
        health_status["checks"]["database"] = {"status": "healthy"}
    except Exception as e:
        health_status["checks"]["database"] = {"status": "unhealthy", "error": str(e)}
        health_status["status"] = "degraded"
    
    # Check Redis
    try:
        import redis
        client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        client.ping()
        health_status["checks"]["redis"] = {"status": "healthy"}
    except Exception as e:
        health_status["checks"]["redis"] = {"status": "unhealthy", "error": str(e)}
        health_status["status"] = "degraded"
    
    # Check Telegram Bot API
    try:
        import requests
        url = f"https://api.telegram.org/bot{settings.MONITOR_BOT_TOKEN}/getMe"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            health_status["checks"]["telegram_bot"] = {"status": "healthy"}
        else:
            health_status["checks"]["telegram_bot"] = {"status": "unhealthy", "error": "API unreachable"}
            health_status["status"] = "degraded"
    except Exception as e:
        health_status["checks"]["telegram_bot"] = {"status": "unhealthy", "error": str(e)}
        health_status["status"] = "degraded"
    
    # Return 503 if any critical service is down
    if health_status["status"] == "degraded":
        raise HTTPException(status_code=503, detail=health_status)
    
    return health_status


@router.get("/metrics")
async def get_metrics():
    """
    Get system metrics.
    Returns performance statistics for tracked operations.
    """
    from app.core.metrics import metrics
    
    return {
        "summary": metrics.get_summary(),
        "metrics": metrics.get_all_metrics()
    }


@router.get("/circuit-breakers")
async def get_circuit_breakers():
    """
    Get circuit breaker status for all external services.
    """
    from app.core.circuit_breaker import get_all_circuit_status
    
    return {
        "circuit_breakers": get_all_circuit_status()
    }


@router.post("/circuit-breakers/{service}/reset")
async def reset_circuit_breaker(service: str):
    """
    Manually reset a circuit breaker.
    Use this to force-enable a service after fixing issues.
    """
    from app.core.circuit_breaker import get_circuit_breaker
    
    try:
        breaker = get_circuit_breaker(service)
        breaker.reset()
        return {"status": "success", "message": f"Circuit breaker for {service} reset"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
