import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.database import get_database

logger = logging.getLogger(__name__)

COLLECTION = "llm_logs"


async def ensure_indexes():
    db = get_database()
    if db is None:
        return
    collection = db[COLLECTION]
    await collection.create_index("inbound_message_id")
    await collection.create_index("outbound_message_id")
    await collection.create_index("contact_wa_id")
    await collection.create_index("created_at")
    await collection.create_index([("contact_wa_id", 1), ("created_at", -1)])
    logger.info("LLM log indexes ensured")


async def save_llm_log(
    inbound_message_id: str,
    contact_wa_id: str,
    request_payload: dict,
    raw_response: Optional[dict],
    outbound_message_id: Optional[str] = None,
    success: bool = False,
    error: Optional[str] = None,
) -> Optional[str]:
    db = get_database()
    if db is None:
        logger.error("Database not available for LLM log save")
        return None

    collection = db[COLLECTION]
    now = datetime.now(timezone.utc)

    processing_metadata = {}
    if raw_response:
        processing_metadata = raw_response.get("processing_metadata", {})

    doc = {
        "inbound_message_id": inbound_message_id,
        "outbound_message_id": outbound_message_id,
        "contact_wa_id": contact_wa_id,
        "request_payload": request_payload,
        "raw_response": raw_response,
        "intent_code": processing_metadata.get("intent_code"),
        "intent_confidence": processing_metadata.get("intent_confidence"),
        "target_node": processing_metadata.get("target_node"),
        "previous_node": processing_metadata.get("previous_node"),
        "success": success,
        "error": error,
        "created_at": now,
    }

    try:
        result = await collection.insert_one(doc)
        logger.info(f"LLM log saved: inbound={inbound_message_id}, outbound={outbound_message_id}")
        return str(result.inserted_id)
    except Exception as e:
        logger.error(f"Failed to save LLM log: {e}")
        return None


async def update_outbound_message_id(
    inbound_message_id: str,
    outbound_message_id: str,
) -> None:
    db = get_database()
    if db is None:
        return

    collection = db[COLLECTION]
    try:
        await collection.update_one(
            {"inbound_message_id": inbound_message_id, "outbound_message_id": None},
            {"$set": {"outbound_message_id": outbound_message_id}},
        )
    except Exception as e:
        logger.error(f"Failed to update LLM log outbound_message_id: {e}")
