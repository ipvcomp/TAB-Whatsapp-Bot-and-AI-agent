import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class MongoDB:
    client: AsyncIOMotorClient = None
    db: AsyncIOMotorDatabase = None


mongodb = MongoDB()


async def connect_to_mongodb():
    settings = get_settings()
    if not settings.MONGODB_URI:
        logger.error("MONGODB_URI is not configured")
        return

    mongodb.client = AsyncIOMotorClient(
        settings.MONGODB_URI,
        maxPoolSize=50,
        minPoolSize=10,
        serverSelectionTimeoutMS=5000,
    )
    mongodb.db = mongodb.client[settings.MONGODB_DB_NAME]

    try:
        await mongodb.client.admin.command("ping")
        logger.info(f"Connected to MongoDB: {settings.MONGODB_DB_NAME}")
        print(f"[DATABASE] Connected to MongoDB: {settings.MONGODB_DB_NAME}", flush=True)
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        print(f"[DATABASE] Connection failed: {e}", flush=True)


async def close_mongodb_connection():
    if mongodb.client:
        mongodb.client.close()
        logger.info("MongoDB connection closed")
        print("[DATABASE] MongoDB connection closed", flush=True)


def get_database() -> AsyncIOMotorDatabase:
    return mongodb.db
