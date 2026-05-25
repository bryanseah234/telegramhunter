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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # Restrict to known frontend origins. Wildcard is intentionally avoided in production.
        # Add your frontend domain here, e.g. "https://dashboard.example.com"
        # In development, localhost variants are permitted.
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ] if settings.ENV == "production" else ["*"],
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
