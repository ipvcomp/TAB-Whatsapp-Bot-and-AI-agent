import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.database import get_database

logger = logging.getLogger(__name__)

OUTBOUND_COLLECTION = "messages"


async def send_text_message(
    to: str,
    body: str,
    phone_number_id: Optional[str] = None,
) -> Optional[dict]:
    settings = get_settings()

    if not settings.WHATSAPP_API_TOKEN:
        logger.error("WHATSAPP_API_TOKEN is not configured")
        return None

    pid = phone_number_id or settings.WHATSAPP_PHONE_NUMBER_ID
    if not pid:
        logger.error("WHATSAPP_PHONE_NUMBER_ID is not configured")
        return None

    url = f"{settings.META_API_BASE_URL}/{settings.META_API_VERSION}/{pid}/messages"

    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": body,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response_data = response.json()

            if response.status_code == 200:
                wamid = None
                if "messages" in response_data and response_data["messages"]:
                    wamid = response_data["messages"][0].get("id")

                await _save_outbound_message(
                    message_id=wamid,
                    to_wa_id=to,
                    phone_number_id=pid,
                    msg_type="text",
                    content={"text": body},
                )

                logger.info(f"Message sent to {to}: wamid={wamid}")
                return response_data
            else:
                logger.error(f"Failed to send message to {to}: {response.status_code} - {response_data}")
                return None
    except httpx.TimeoutException:
        logger.error(f"Timeout sending message to {to}")
        return None
    except Exception as e:
        logger.error(f"Error sending message to {to}: {e}")
        return None


async def _save_outbound_message(
    message_id: Optional[str],
    to_wa_id: str,
    phone_number_id: str,
    msg_type: str,
    content: dict,
) -> None:
    db = get_database()
    if db is None:
        logger.error("Database not available for outbound message save")
        return

    if not message_id:
        logger.warning("No message_id returned from Meta API, skipping DB save")
        return

    collection = db[OUTBOUND_COLLECTION]
    now = datetime.now(timezone.utc)

    doc = {
        "message_id": message_id,
        "contact_wa_id": to_wa_id,
        "phone_number_id": phone_number_id,
        "business_phone": "",
        "direction": "outbound",
        "type": msg_type,
        "content": content,
        "context": None,
        "wa_timestamp": now,
        "created_at": now,
        "errors": None,
    }

    try:
        await collection.update_one(
            {"message_id": message_id},
            {"$setOnInsert": doc},
            upsert=True,
        )
        logger.info(f"Saved outbound message: {message_id} to {to_wa_id}")
    except Exception as e:
        logger.error(f"Failed to save outbound message {message_id}: {e}")
