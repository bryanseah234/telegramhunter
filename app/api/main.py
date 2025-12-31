from fastapi import FastAPI
from app.core.config import settings
from app.api.routers import monitor, scan

app = FastAPI(title=settings.PROJECT_NAME)

app.include_router(monitor.router)
app.include_router(scan.router)

@app.get("/")
def read_root():
    return {"status": "ok", "version": "2.0-unified", "env": settings.ENV}
