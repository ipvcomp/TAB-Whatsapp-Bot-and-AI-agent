import logging
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class MongoDB:
    client: Optional[AsyncIOMotorClient] = None
    db: Optional[AsyncIOMotorDatabase] = None
    connected: bool = False


mongodb = MongoDB()


async def connect_to_mongodb():
    settings = get_settings()
    if not settings.MONGODB_URI:
        logger.error("MONGODB_URI is not configured")
        raise RuntimeError("MONGODB_URI is not configured")

    mongodb.client = AsyncIOMotorClient(
        settings.MONGODB_URI,
        maxPoolSize=50,
        minPoolSize=10,
        serverSelectionTimeoutMS=5000,
    )

    try:
        await mongodb.client.admin.command("ping")
        mongodb.db = mongodb.client[settings.MONGODB_DB_NAME]
        mongodb.connected = True
        logger.info(f"Connected to MongoDB: {settings.MONGODB_DB_NAME}")
        print(f"[DATABASE] Connected to MongoDB: {settings.MONGODB_DB_NAME}", flush=True)

        await _ensure_all_indexes()
    except Exception as e:
        mongodb.client.close()
        mongodb.client = None
        mongodb.db = None
        mongodb.connected = False
        logger.error(f"Failed to connect to MongoDB: {e}")
        print(f"[DATABASE] Connection failed: {e}", flush=True)
        raise RuntimeError(f"Failed to connect to MongoDB: {e}")


async def close_mongodb_connection():
    if mongodb.client:
        mongodb.client.close()
        mongodb.connected = False
        logger.info("MongoDB connection closed")
        print("[DATABASE] MongoDB connection closed", flush=True)


def get_database() -> Optional[AsyncIOMotorDatabase]:
    if not mongodb.connected:
        return None
    return mongodb.db


async def _ensure_all_indexes():
    from app.services import contact_service, message_service, session_service
    await contact_service.ensure_indexes()
    await message_service.ensure_indexes()
    await session_service.ensure_indexes()
    logger.info("All database indexes ensured")
    print("[DATABASE] All indexes ensured", flush=True)
