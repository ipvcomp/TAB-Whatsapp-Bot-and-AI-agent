from fastapi import APIRouter

from app.core.config import get_settings
from app.core.database import get_database

router = APIRouter()


@router.get("/health")
async def health_check():
    settings = get_settings()
    db = get_database()

    db_status = "disconnected"
    if db is not None:
        try:
            await db.command("ping")
            db_status = "connected"
        except Exception:
            db_status = "error"

    overall = "healthy" if db_status == "connected" else "degraded"

    return {
        "status": overall,
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "database": db_status,
    }
