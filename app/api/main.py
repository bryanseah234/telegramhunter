from fastapi import FastAPI
from app.core.config import settings
from app.api.routers import monitor, scan
import logging
import sys

# ==============================================
# LOGGING CONFIGURATION
# ==============================================
# Configure root logger for all app.* modules
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True  # Override any existing config
)

# Set specific loggers
logging.getLogger("app").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # Reduce access log noise

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    docs_url=None if settings.ENV == "production" else "/docs",
    redoc_url=None if settings.ENV == "production" else "/redoc",
    openapi_url=None if settings.ENV == "production" else "/openapi.json"
)

from app.services.broadcaster_srv import broadcaster_service
import asyncio

@app.on_event("startup")
async def startup_event():
    logger.info("🚀 API starting up...")
    # Non-blocking: don't let Telegram timeout slow down API startup
    try:
        await asyncio.wait_for(
            broadcaster_service.send_log(f"🟢 **API Service** Started ({settings.ENV})"),
            timeout=5.0
        )
        logger.info("✅ Startup notification sent to Telegram")
    except asyncio.TimeoutError:
        logger.warning("⚠️ Startup notification timed out (Telegram slow)")
    except Exception as e:
        logger.warning(f"⚠️ Startup notification failed: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🛑 API shutting down...")
    try:
        await asyncio.wait_for(
            broadcaster_service.send_log(f"🔴 **API Service** Stopping..."),
            timeout=3.0
        )
    except Exception:
        pass  # Don't block shutdown

app.include_router(monitor.router)
app.include_router(scan.router)

@app.get("/")
def read_root():
    if settings.ENV == "production":
        return {"status": "active"}
    return {"status": "ok", "version": "2.0-unified", "env": settings.ENV}
