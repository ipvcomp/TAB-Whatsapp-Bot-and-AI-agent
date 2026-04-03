import logging
from datetime import datetime, timezone
from typing import Optional

from pymongo import ReturnDocument

from app.core.database import get_database

logger = logging.getLogger(__name__)

COLLECTION = "contacts"


async def ensure_indexes():
    db = get_database()
    if db is None:
        return
    collection = db[COLLECTION]
    await collection.create_index("wa_id", unique=True)
    await collection.create_index("phone_number_id")
    await collection.create_index("last_message_at")
    await collection.create_index("created_at")
    logger.info("Contact indexes ensured")


async def upsert_contact(
    wa_id: str,
    profile_name: str,
    phone_number_id: str,
    business_phone: str,
    increment_message_count: bool = True,
) -> Optional[dict]:
    db = get_database()
    if db is None:
        logger.error("Database not available for contact upsert")
        return None

    if not wa_id or not wa_id.strip():
        logger.error("Cannot upsert contact with empty wa_id")
        return None

    collection = db[COLLECTION]
    now = datetime.now(timezone.utc)

    update_set = {
        "last_message_at": now,
        "updated_at": now,
    }
    if profile_name and profile_name != "Unknown":
        update_set["profile_name"] = profile_name

    set_on_insert = {
        "wa_id": wa_id,
        "phone_number_id": phone_number_id,
        "business_phone": business_phone,
        "created_at": now,
        "is_blocked": False,
        "tags": [],
        "metadata": {},
    }
    if "profile_name" not in update_set:
        set_on_insert["profile_name"] = profile_name or "Unknown"

    update_ops = {
        "$set": update_set,
        "$setOnInsert": set_on_insert,
    }

    if increment_message_count:
        update_ops["$inc"] = {"message_count": 1}

    try:
        result = await collection.find_one_and_update(
            {"wa_id": wa_id},
            update_ops,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return result
    except Exception as e:
        logger.error(f"Failed to upsert contact {wa_id}: {e}")
        return None


async def get_contact_by_wa_id(wa_id: str) -> Optional[dict]:
    db = get_database()
    if db is None:
        return None

    collection = db[COLLECTION]
    return await collection.find_one({"wa_id": wa_id})
