"""
Health check router for monitoring system status.
Provides endpoints to check database, Redis, and service health.
"""
from fastapi import APIRouter, HTTPException, Header
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
async def detailed_health(x_monitor_key: str | None = Header(None)):
    """
    Detailed health check with dependency status (protected if MONITOR_API_KEY set).
    """
    if not settings.MONITOR_API_KEY or x_monitor_key != settings.MONITOR_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing monitor API key")
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
        import httpx
        token = settings.bot_tokens[0]
        # Mask token in URL — only pass the bot_id prefix for logging safety
        url = f"https://api.telegram.org/bot{token}/getMe"
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
        if response.status_code == 200:
            health_status["checks"]["telegram_bot"] = {"status": "healthy"}
        else:
            health_status["checks"]["telegram_bot"] = {"status": "unhealthy", "error": "API unreachable"}
            health_status["status"] = "degraded"
    except Exception:
        # Do NOT include the exception string — it may contain the bot token in a URL
        health_status["checks"]["telegram_bot"] = {"status": "unhealthy", "error": "connection_failed"}
        health_status["status"] = "degraded"
    
    # Return 503 if any critical service is down
    if health_status["status"] == "degraded":
        raise HTTPException(status_code=503, detail=health_status)
    
    return health_status


@router.get("/metrics")
async def get_metrics(x_monitor_key: str | None = Header(None)):
    """
    Get system metrics (protected if MONITOR_API_KEY set).
    """
    if not settings.MONITOR_API_KEY or x_monitor_key != settings.MONITOR_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing monitor API key")
    from app.core.metrics import metrics

    return {
        "summary": metrics.get_summary(),
        "metrics": metrics.get_all_metrics()
    }


@router.get("/queues")
async def get_queue_health(x_monitor_key: str | None = Header(None)):
    """
    Get operational queue depth and oldest tracked job age.
    """
    if not settings.MONITOR_API_KEY or x_monitor_key != settings.MONITOR_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing monitor API key")
    try:
        import redis
        from app.core.queue_monitor import get_queue_snapshot

        client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        return {"queues": get_queue_snapshot(client)}
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={"status": "degraded", "error": str(e)},
        ) from e


@router.get("/circuit-breakers")
async def get_circuit_breakers(x_monitor_key: str | None = Header(None)):
    """
    Get circuit breaker status (protected if MONITOR_API_KEY set).
    """
    if not settings.MONITOR_API_KEY or x_monitor_key != settings.MONITOR_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing monitor API key")
    from app.core.circuit_breaker import get_all_circuit_status

    return {
        "circuit_breakers": get_all_circuit_status()
    }


@router.post("/circuit-breakers/{service}/reset")
async def reset_circuit_breaker(service: str, x_monitor_key: str | None = Header(None)):
    """
    Manually reset a circuit breaker (protected if MONITOR_API_KEY set).
    Use this to force-enable a service after fixing issues.
    """
    if not settings.MONITOR_API_KEY or x_monitor_key != settings.MONITOR_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing monitor API key")
    from app.core.circuit_breaker import get_circuit_breaker

    try:
        breaker = get_circuit_breaker(service)
        breaker.reset()
        return {"status": "success", "message": f"Circuit breaker for {service} reset"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ==============================================================================
# OBSERVABILITY — daily quota usage and bot pool state
# ==============================================================================

# Hardcoded daily budget per scanner. Populate via env if these ever need to
# vary per deployment; today they're stable across environments.
_QUOTA_LIMITS: dict[str, int] = {
    "shodan": 100,
    "netlas": 50,
    "github": 5000,
    "urlscan": 100,
}


@router.get("/quotas")
async def get_quotas(x_monitor_key: str | None = Header(None)):
    """Per-service daily API-budget usage.

    Reads Redis counters at ``quota:{service}:{yyyymmdd}`` (UTC date) for
    each supported scanner and returns ``{service: {used_today, limit,
    pct}}``. Services whose Redis counter is absent report ``used_today=0``.
    ``limit`` is null when no budget is known for a service.
    """
    if not settings.MONITOR_API_KEY or x_monitor_key != settings.MONITOR_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing monitor API key")

    import redis
    from datetime import datetime, timezone

    today_key = datetime.now(timezone.utc).strftime("%Y%m%d")
    try:
        client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        out: dict[str, dict] = {}
        for service, limit in _QUOTA_LIMITS.items():
            raw = client.get(f"quota:{service}:{today_key}")
            try:
                used_today = int(raw) if raw is not None else 0
            except (ValueError, TypeError):
                used_today = 0
            pct = round(100.0 * used_today / limit, 2) if limit else None
            out[service] = {
                "used_today": used_today,
                "limit": limit,
                "pct": pct,
            }
        return {"date_utc": today_key, "quotas": out}
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={"status": "degraded", "error": str(e)},
        ) from e


@router.get("/bot-pool")
async def get_bot_pool(x_monitor_key: str | None = Header(None)):
    """Bot pool state — total configured, active clients, and Redis lock view.

    ``total_bots`` counts tokens configured via ``MONITOR_BOT_TOKEN``.
    ``active_bots`` counts connected Telethon clients cached in
    ``BotClientManager`` in *this* API process — will typically be 0 since
    the API rarely opens Telethon sessions; the number reflects local cache
    warmth, not cluster-wide activity.

    ``locked_bots`` and ``oldest_lock_age_seconds`` derive from Redis keys
    matching ``bot_listener:poll_lock:*`` — the cross-process view of which
    bot IDs are currently held by an active poller. ``oldest_lock_age_seconds``
    is a *time-since-last-renew* proxy: ``LOCK_TTL_SECONDS − min(TTL)`` across
    all lock keys. bot_listener renews these on a fixed cadence, so a large
    value here indicates a lock nearing expiry (i.e. a poller that stopped
    renewing).
    """
    if not settings.MONITOR_API_KEY or x_monitor_key != settings.MONITOR_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing monitor API key")

    from app.core.constants import LOCK_TTL_SECONDS
    from app.services.bot_manager_srv import bot_manager

    total_bots = len(settings.bot_tokens)

    active_bots = 0
    for _token, client in list(bot_manager._clients.items()):
        try:
            if client.is_connected():
                active_bots += 1
        except Exception:
            # Client in an odd state — count as inactive rather than raise.
            continue

    locked_bots = 0
    oldest_lock_age_seconds: int | None = None
    try:
        import redis
        rc = redis.from_url(settings.REDIS_URL, decode_responses=True)
        lock_keys = list(rc.scan_iter(match="bot_listener:poll_lock:*", count=100))
        locked_bots = len(lock_keys)
        if lock_keys:
            ttls: list[int] = []
            for key in lock_keys:
                ttl = rc.ttl(key)
                # -1 = no expire (unexpected here), -2 = missing (raced). Skip both.
                if isinstance(ttl, int) and ttl >= 0:
                    ttls.append(ttl)
            if ttls:
                # Smallest TTL = oldest (nearest to expiry) = largest elapsed since renew.
                oldest_lock_age_seconds = max(0, LOCK_TTL_SECONDS - min(ttls))
    except Exception as e:
        logger.warning(f"[BotPool] Redis lock enumeration failed: {e}")

    return {
        "total_bots": total_bots,
        "active_bots": active_bots,
        "locked_bots": locked_bots,
        "oldest_lock_age_seconds": oldest_lock_age_seconds,
        "lock_ttl_seconds": LOCK_TTL_SECONDS,
    }
