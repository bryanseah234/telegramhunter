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
    allow_headers=["*"],
)

app.include_router(monitor.router)
app.include_router(scan.router)
app.include_router(ingest.router)

# Health check endpoints
from app.api.routers import health
app.include_router(health.router)

@app.get("/")
def read_root():
    if settings.ENV == "production":
        return {"status": "active"}
    return {"status": "ok", "version": "2.0-unified", "env": settings.ENV}
