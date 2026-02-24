import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.database import get_database
from app.models.webhook import WhatsAppMessage, WhatsAppMetadata

logger = logging.getLogger(__name__)

COLLECTION = "messages"


async def ensure_indexes():
    db = get_database()
    if db is None:
        return
    collection = db[COLLECTION]
    await collection.create_index("message_id", unique=True)
    await collection.create_index("contact_wa_id")
    await collection.create_index("phone_number_id")
    await collection.create_index("direction")
    await collection.create_index("type")
    await collection.create_index("wa_timestamp")
    await collection.create_index([("contact_wa_id", 1), ("wa_timestamp", -1)])
    logger.info("Message indexes ensured")


def _extract_content(message: WhatsAppMessage) -> dict:
    content = {}

    if message.text:
        content["text"] = message.text.body

    if message.image:
        content["image"] = {
            "media_id": message.image.id,
            "mime_type": message.image.mime_type,
            "sha256": message.image.sha256,
            "caption": message.image.caption,
        }

    if message.audio:
        content["audio"] = {
            "media_id": message.audio.id,
            "mime_type": message.audio.mime_type,
            "sha256": message.audio.sha256,
            "voice": message.audio.voice,
        }

    if message.video:
        content["video"] = {
            "media_id": message.video.id,
            "mime_type": message.video.mime_type,
            "sha256": message.video.sha256,
            "caption": message.video.caption,
        }

    if message.document:
        content["document"] = {
            "media_id": message.document.id,
            "mime_type": message.document.mime_type,
            "sha256": message.document.sha256,
            "filename": message.document.filename,
            "caption": message.document.caption,
        }

    if message.location:
        content["location"] = {
            "latitude": message.location.latitude,
            "longitude": message.location.longitude,
            "name": message.location.name,
            "address": message.location.address,
        }

    if message.reaction:
        content["reaction"] = {
            "emoji": message.reaction.emoji,
            "reacted_message_id": message.reaction.message_id,
        }

    if message.sticker:
        content["sticker"] = {
            "media_id": message.sticker.id,
            "mime_type": message.sticker.mime_type,
            "sha256": message.sticker.sha256,
            "animated": message.sticker.animated,
        }

    if message.interactive:
        interactive_data = {"type": message.interactive.type}
        if message.interactive.button_reply:
            interactive_data["button_reply"] = {
                "id": message.interactive.button_reply.id,
                "title": message.interactive.button_reply.title,
            }
        if message.interactive.list_reply:
            interactive_data["list_reply"] = {
                "id": message.interactive.list_reply.id,
                "title": message.interactive.list_reply.title,
            }
        content["interactive"] = interactive_data

    if message.button:
        content["button"] = {
            "payload": message.button.payload,
            "text": message.button.text,
        }

    return content


def _extract_context(message: WhatsAppMessage) -> Optional[dict]:
    if not message.context:
        return None

    ctx = {
        "replied_to_message_id": message.context.id,
        "replied_to_sender": message.context.from_,
    }
    if message.context.referred_product:
        ctx["referred_product"] = message.context.referred_product

    return ctx


async def save_inbound_message(
    message: WhatsAppMessage,
    metadata: WhatsAppMetadata,
    contact_wa_id: str,
) -> Optional[dict]:
    db = get_database()
    if db is None:
        logger.error("Database not available for message save")
        return None

    collection = db[COLLECTION]
    now = datetime.now(timezone.utc)

    try:
        wa_ts = datetime.fromtimestamp(int(message.timestamp), tz=timezone.utc)
    except (ValueError, OSError):
        wa_ts = now

    content = _extract_content(message)
    context = _extract_context(message)

    doc = {
        "message_id": message.id,
        "contact_wa_id": contact_wa_id,
        "phone_number_id": metadata.phone_number_id,
        "business_phone": metadata.display_phone_number,
        "direction": "inbound",
        "type": message.type,
        "content": content,
        "context": context,
        "wa_timestamp": wa_ts,
        "created_at": now,
        "errors": message.errors,
    }

    try:
        result = await collection.update_one(
            {"message_id": message.id},
            {"$setOnInsert": doc},
            upsert=True,
        )

        is_new = result.upserted_id is not None

        if is_new:
            logger.info(f"Saved new message: {message.id} from {contact_wa_id}")
        else:
            logger.info(f"Duplicate message skipped: {message.id}")

        return {"doc": doc, "is_new": is_new}
    except Exception as e:
        logger.error(f"Failed to save message {message.id}: {e}")
        return None


async def get_messages_by_contact(
    contact_wa_id: str,
    limit: int = 50,
    skip: int = 0,
) -> list[dict]:
    db = get_database()
    if db is None:
        return []

    collection = db[COLLECTION]
    cursor = collection.find(
        {"contact_wa_id": contact_wa_id}
    ).sort("wa_timestamp", -1).skip(skip).limit(limit)

    return await cursor.to_list(length=limit)
