from fastapi import FastAPI

from app.api.webhook import router as webhook_router
from app.core.config import settings

app = FastAPI(title=settings.app_name, version="0.1.0")


@app.get("/", tags=["health"])
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "status": "ok"}


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(webhook_router)