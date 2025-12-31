from fastapi import FastAPI
from app.core.config import settings
from app.api.routers import monitor, scan

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
    # Non-blocking: don't let Telegram timeout slow down API startup
    try:
        await asyncio.wait_for(
            broadcaster_service.send_log(f"🟢 **API Service** Started ({settings.ENV})"),
            timeout=5.0
        )
    except asyncio.TimeoutError:
        print("⚠️ Startup notification timed out (Telegram slow)")
    except Exception as e:
        print(f"⚠️ Startup notification failed: {e}")

@app.on_event("shutdown")
async def shutdown_event():
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
