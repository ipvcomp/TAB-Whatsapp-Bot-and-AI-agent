import sys
import logging
import os
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from contextlib import asynccontextmanager

from app.core.config import get_settings
from app.core.database import connect_to_mongodb, close_mongodb_connection
from app.api.v1.router import api_router

os.makedirs("logs", exist_ok=True)

_log_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

_file_handler = RotatingFileHandler(
    "logs/app.log",
    maxBytes=5 * 1024 * 1024,  # 5 MB per file
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(_log_formatter)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(_log_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[_console_handler, _file_handler],
)
logger = logging.getLogger(__name__)

# ── Silence noisy low-value loggers ──────────────────────────────────────────
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("app.services.session_service").setLevel(logging.WARNING)
logging.getLogger("app.services.message_service").setLevel(logging.WARNING)
logging.getLogger("app.services.contact_service").setLevel(logging.WARNING)
logging.getLogger("app.services.whatsapp_service").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(application: FastAPI):
    await connect_to_mongodb()
    try:
        from app.services.whatsapp_service import get_welcome_image_media_id
        media_id = await get_welcome_image_media_id()
        if media_id:
            logger.info(f"Welcome image pre-uploaded: media_id={media_id}")
        else:
            logger.warning("Welcome image upload failed at startup — will retry on first use")
    except Exception as e:
        logger.warning(f"Welcome image pre-upload skipped: {e}")
    logger.info("Application startup complete")
    yield
    await close_mongodb_connection()
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()

    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    allowed_origins = ["*"]
    if settings.APP_BASE_URL:
        allowed_origins = [
            settings.APP_BASE_URL,
            "https://graph.facebook.com",
            "*",
        ]

    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(api_router, prefix="/api/v1")

    @application.get("/")
    async def root():
        return {
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "status": "running",
            "environment": settings.ENV,
            "endpoints": {
                "health": "/api/v1/health",
                "webhook": "/api/v1/webhook",
                "docs": "/docs",
            },
        }

    return application


app = create_app()
