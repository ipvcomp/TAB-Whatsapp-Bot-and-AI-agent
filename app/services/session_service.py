import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.database import get_database

logger = logging.getLogger(__name__)

COLLECTION = "sessions"

DEFAULT_SESSION_NODE = "N01"


async def ensure_indexes():
    db = get_database()
    if db is None:
        return
    collection = db[COLLECTION]
    await collection.create_index("user_id", unique=True)
    await collection.create_index("updated_at")
    logger.info("Session indexes ensured")


def build_default_session(
    user_id: str,
    phone_number: str,
    first_name: str = "",
) -> dict:
    return {
        "user_id": user_id,
        "phone_number": f"+{phone_number}" if not phone_number.startswith("+") else phone_number,
        "current_node": DEFAULT_SESSION_NODE,
        "last_node": None,
        "first_name": first_name,
        "tags": [],
        "active_trip_id": None,
        "active_policy_id": None,
        "active_policy_code": None,
        "active_claim_id": None,
        "temp_data": {},
        "last_intent": None,
    }


async def update_api_data(wa_id: str, updates: dict) -> None:
    session = await get_session(wa_id) or {"user_id": wa_id, "temp_data": {}}
    session.setdefault("api_data", {}).update(updates)
    await save_session(session)


async def get_session(user_id: str) -> Optional[dict]:
    db = get_database()
    if db is None:
        return None

    collection = db[COLLECTION]
    doc = await collection.find_one({"user_id": user_id})

    if doc:
        doc.pop("_id", None)
        doc.pop("created_at", None)
        doc.pop("updated_at", None)
        return doc

    return None


async def save_session(session: dict) -> Optional[dict]:
    db = get_database()
    if db is None:
        logger.error("Database not available for session save")
        return None

    user_id = session.get("user_id")
    if not user_id:
        logger.error("Cannot save session without user_id")
        return None

    collection = db[COLLECTION]
    now = datetime.now(timezone.utc)

    try:
        await collection.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    **session,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "created_at": now,
                },
            },
            upsert=True,
        )
        logger.info(f"Session saved for user {user_id}, node: {session.get('current_node')}")
        return session
    except Exception as e:
        logger.error(f"Failed to save session for {user_id}: {e}")
        return None
