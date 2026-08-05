from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.core.config import settings
from app.api.routers import monitor, scan, ingest
import logging
import sys
import asyncio

# ==============================================
# LOGGING CONFIGURATION
# ==============================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True
)
logging.getLogger("app").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────
    logger.info("🚀 API starting up...")
    try:
        from app.services.broadcaster_srv import BroadcasterService
        broadcaster = BroadcasterService()
        await asyncio.wait_for(
            broadcaster.send_log(f"🟢 **API Service** Started ({settings.ENV})"),
            timeout=5.0
        )
        logger.info("✅ Startup notification sent to Telegram")
    except asyncio.TimeoutError:
        logger.warning("⚠️ Startup notification timed out (Telegram slow)")
    except Exception as e:
        logger.warning(f"⚠️ Startup notification failed: {e}")

    yield  # ── Application runs ──────────────

    # ── Shutdown ─────────────────────────────
    logger.info("🛑 API shutting down...")
    try:
        from app.services.broadcaster_srv import BroadcasterService
        broadcaster = BroadcasterService()
        await asyncio.wait_for(
            broadcaster.send_log("🔴 **API Service** Stopping..."),
            timeout=3.0
        )
    except Exception:
        pass


app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan,
    docs_url=None if settings.ENV == "production" else "/docs",
    redoc_url=None if settings.ENV == "production" else "/redoc",
    openapi_url=None if settings.ENV == "production" else "/openapi.json"
)  # Don't block shutdown

# ── Rate limiting ─────────────────────────────────────────────────────
# Uses Redis for cross-worker limits (all uvicorn workers share the same
# counters). Keyed on X-Monitor-Key when present, else remote IP.
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address


def _rate_key(request):
    """Prefer X-Monitor-Key so a leaked key can't outrun the per-IP budget,
    fall back to remote IP for unauth endpoints (honeypot receiver)."""
    hdr = request.headers.get("X-Monitor-Key")
    if hdr:
        # Bucket by first 12 chars — enough entropy to distinguish keys
        # without dumping the whole key into Redis
        return f"key:{hdr[:12]}"
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(
    key_func=_rate_key,
    storage_uri=settings.REDIS_URL,
    default_limits=["120/minute"],  # global default; endpoints can override
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
# ─────────────────────────────────────────────────────────────────────

# Allow browser-based clients (including the Chrome extension) to call the API.
# This API should rely on explicit API keys for sensitive operations.
# CORS: always use an explicit allowlist — never wildcard, even in dev.
# Dev origins are included by default; add extra domains via EXTRA_CORS_ORIGINS
# in .env (comma-separated, e.g. "https://my-tunnel.ngrok.io").
import os as _os
_extra_origins = [o.strip() for o in _os.getenv("EXTRA_CORS_ORIGINS", "").split(",") if o.strip()]
_cors_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8001",
    "http://127.0.0.1:8001",
] + _extra_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    # Explicit allowlist — matches what the API actually reads. Wildcards
    # here don't add credentials risk (allow_credentials=False), but keep
    # the surface narrow so future header-based side channels are visible.
    allow_headers=["Content-Type", "X-Monitor-Key", "Accept"],
)

app.include_router(monitor.router)
app.include_router(scan.router)
app.include_router(ingest.router)

# Health check endpoints
from app.api.routers import health
app.include_router(health.router)

# Media proxy endpoint
from app.api.routers import media
app.include_router(media.router, prefix="/media", tags=["media"])

# Honeypot webhook receiver — only active when HONEYPOT_MODE=True
from app.api.routers import honeypot
app.include_router(honeypot.router)

@app.get("/")
def read_root():
    if settings.ENV == "production":
        return {"status": "active"}
    return {"status": "ok", "version": "2.0-unified", "env": settings.ENV}
