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
    await broadcaster_service.send_log(f"🟢 **API Service** Started ({settings.ENV})")

@app.on_event("shutdown")
async def shutdown_event():
    await broadcaster_service.send_log(f"🔴 **API Service** Stopping...")

app.include_router(monitor.router)
app.include_router(scan.router)

@app.get("/")
def read_root():
    if settings.ENV == "production":
        return {"status": "active"}
    return {"status": "ok", "version": "2.0-unified", "env": settings.ENV}
