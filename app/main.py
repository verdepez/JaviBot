import os
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.webhook import router as webhook_router
from app.core.config import settings

app = FastAPI(title=settings.app_name, version="0.1.0")

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", tags=["health"])
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "status": "ok"}


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/avatar", tags=["branding"])
async def get_avatar() -> FileResponse:
    avatar_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "avatar.png")
    if not os.path.exists(avatar_path):
        avatar_path = os.path.join(os.path.dirname(__file__), "static", "avatar.png")
    return FileResponse(avatar_path, media_type="image/png")


app.include_router(webhook_router)