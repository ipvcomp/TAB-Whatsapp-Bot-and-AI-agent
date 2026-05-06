import asyncio
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
    in_reply_to: Optional[str] = None,
    source: str = "auto_reply",
) -> Optional[dict]:
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
    return await send_whatsapp_payload(
        payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source=source,
    )


async def send_whatsapp_payload(
    whatsapp_payload: dict,
    phone_number_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    source: str = "llm",
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

    to = whatsapp_payload.get("to", "")
    if to.startswith("+"):
        whatsapp_payload["to"] = to.lstrip("+")

    msg_type = whatsapp_payload.get("type", "unknown")

    for attempt in range(1, MEDIA_RETRY_MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=whatsapp_payload, headers=headers)
                response_data = response.json()

                if response.status_code == 200:
                    wamid = None
                    if "messages" in response_data and response_data["messages"]:
                        wamid = response_data["messages"][0].get("id")

                    content = {}
                    if msg_type == "text":
                        content = {"text": whatsapp_payload.get("text", {}).get("body", "")}
                    elif msg_type == "interactive":
                        content = {"interactive": whatsapp_payload.get("interactive", {})}
                    elif msg_type == "template":
                        content = {"template": whatsapp_payload.get("template", {})}
                    else:
                        content = {msg_type: whatsapp_payload.get(msg_type, {})}

                    await _save_outbound_message(
                        message_id=wamid,
                        to_wa_id=whatsapp_payload.get("to", ""),
                        phone_number_id=pid,
                        msg_type=msg_type,
                        content=content,
                        in_reply_to=in_reply_to,
                        source=source,
                    )

                    logger.info(f"Message sent to {to}: type={msg_type}, wamid={wamid}")
                    return {**response_data, "_wamid": wamid}

                error_msg = response_data.get("error", {}).get("message", "Unknown error")
                error_code = response_data.get("error", {}).get("code", "N/A")
                error_details = response_data.get("error", {})

                if response.status_code in (429, 502, 503, 504) and attempt < MEDIA_RETRY_MAX_ATTEMPTS:
                    wait = MEDIA_RETRY_BACKOFF[min(attempt - 1, len(MEDIA_RETRY_BACKOFF) - 1)]
                    logger.warning(f"WhatsApp API returned HTTP {response.status_code} to {to} (attempt {attempt}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    continue

                import json as _json
                safe_payload = {k: v for k, v in whatsapp_payload.items() if k != "to"}
                logger.error(
                    f"Failed to send message to {to}: HTTP {response.status_code} | "
                    f"Error #{error_code}: {error_msg} | "
                    f"Details: {error_details} | "
                    f"Payload: {_json.dumps(safe_payload, ensure_ascii=False)[:800]}"
                )
                return None

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt < MEDIA_RETRY_MAX_ATTEMPTS:
                wait = MEDIA_RETRY_BACKOFF[min(attempt - 1, len(MEDIA_RETRY_BACKOFF) - 1)]
                logger.warning(f"WhatsApp send {type(e).__name__} to {to} (attempt {attempt}), retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            logger.error(f"Failed to send message to {to} after {MEDIA_RETRY_MAX_ATTEMPTS} attempts: {e}")
        except Exception as e:
            if attempt < MEDIA_RETRY_MAX_ATTEMPTS:
                wait = MEDIA_RETRY_BACKOFF[min(attempt - 1, len(MEDIA_RETRY_BACKOFF) - 1)]
                logger.warning(f"WhatsApp send error to {to} (attempt {attempt}): {e}, retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            logger.error(f"Error sending message to {to} after {MEDIA_RETRY_MAX_ATTEMPTS} attempts: {e}")

    return None


SUPPORTED_BOARDING_PASS_TYPES = {
    "image/jpeg", "image/png", "image/gif",
    "image/tiff", "application/pdf",
}


MEDIA_RETRY_MAX_ATTEMPTS = 3
MEDIA_RETRY_BACKOFF = [1, 2, 4]

_welcome_image_media_id: Optional[str] = None


async def upload_media_to_whatsapp(file_path: str, mime_type: str = "image/jpeg") -> Optional[str]:
    settings = get_settings()
    token = settings.WHATSAPP_API_TOKEN
    pid = settings.WHATSAPP_PHONE_NUMBER_ID
    if not token or not pid:
        logger.error("WhatsApp credentials not configured for media upload")
        return None

    url = f"https://graph.facebook.com/v22.0/{pid}/media"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        import os
        if not os.path.isfile(file_path):
            logger.error(f"Media file not found: {file_path}")
            return None

        with open(file_path, "rb") as f:
            file_bytes = f.read()

        filename = os.path.basename(file_path)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                headers=headers,
                data={"messaging_product": "whatsapp", "type": mime_type},
                files={"file": (filename, file_bytes, mime_type)},
            )
            if response.status_code == 200:
                media_id = response.json().get("id")
                logger.info(f"Uploaded media '{filename}' to WhatsApp, media_id={media_id}")
                return media_id
            else:
                logger.error(f"Media upload failed: HTTP {response.status_code} — {response.text}")
                return None
    except Exception as e:
        logger.error(f"Media upload error: {e}")
        return None


async def get_welcome_image_media_id() -> Optional[str]:
    global _welcome_image_media_id
    if _welcome_image_media_id:
        return _welcome_image_media_id

    import os
    image_path = os.path.join(os.path.dirname(__file__), "Image.png")
    logger.info(f"Uploading welcome image from: {image_path} (exists={os.path.isfile(image_path)})")
    media_id = await upload_media_to_whatsapp(image_path, "image/png")
    if media_id:
        _welcome_image_media_id = media_id
        logger.info(f"Welcome image cached with media_id={media_id}")
    else:
        logger.error("Failed to upload welcome image — welcome messages will be sent without image header")
    return media_id


async def download_whatsapp_media(media_id: str) -> Optional[dict]:
    settings = get_settings()
    token = settings.WHATSAPP_API_TOKEN
    graph_url = f"https://graph.facebook.com/v22.0/{media_id}"
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(1, MEDIA_RETRY_MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                meta_resp = await client.get(graph_url, headers=headers)
                if meta_resp.status_code != 200:
                    logger.warning(f"Failed to fetch media metadata for {media_id}: HTTP {meta_resp.status_code} (attempt {attempt})")
                    if attempt < MEDIA_RETRY_MAX_ATTEMPTS:
                        await asyncio.sleep(MEDIA_RETRY_BACKOFF[min(attempt - 1, len(MEDIA_RETRY_BACKOFF) - 1)])
                        continue
                    return None

                meta = meta_resp.json()
                download_url = meta.get("url")
                mime_type = meta.get("mime_type", "application/octet-stream")
                file_size = meta.get("file_size")

                if not download_url:
                    logger.error(f"No download URL in media metadata for {media_id}")
                    return None

                media_resp = await client.get(download_url, headers=headers)
                if media_resp.status_code != 200:
                    logger.warning(f"Failed to download media {media_id}: HTTP {media_resp.status_code} (attempt {attempt})")
                    if attempt < MEDIA_RETRY_MAX_ATTEMPTS:
                        await asyncio.sleep(MEDIA_RETRY_BACKOFF[min(attempt - 1, len(MEDIA_RETRY_BACKOFF) - 1)])
                        continue
                    return None

                logger.info(f"Downloaded media {media_id}: {len(media_resp.content)} bytes, type={mime_type}")
                return {
                    "mime_type": mime_type,
                    "file_size": file_size or len(media_resp.content),
                    "bytes": media_resp.content,
                }
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            logger.warning(f"Media download {type(e).__name__} for {media_id} (attempt {attempt})")
            if attempt < MEDIA_RETRY_MAX_ATTEMPTS:
                await asyncio.sleep(MEDIA_RETRY_BACKOFF[min(attempt - 1, len(MEDIA_RETRY_BACKOFF) - 1)])
                continue
            logger.error(f"Media download failed after {MEDIA_RETRY_MAX_ATTEMPTS} attempts: {e}")
        except Exception as e:
            logger.warning(f"Media download error for {media_id} (attempt {attempt}): {e}")
            if attempt < MEDIA_RETRY_MAX_ATTEMPTS:
                await asyncio.sleep(MEDIA_RETRY_BACKOFF[min(attempt - 1, len(MEDIA_RETRY_BACKOFF) - 1)])
                continue
            logger.error(f"Media download failed after {MEDIA_RETRY_MAX_ATTEMPTS} attempts: {e}")

    return None


async def _save_outbound_message(
    message_id: Optional[str],
    to_wa_id: str,
    phone_number_id: str,
    msg_type: str,
    content: dict,
    in_reply_to: Optional[str] = None,
    source: str = "auto_reply",
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
        "context": {
            "in_reply_to": in_reply_to,
        } if in_reply_to else None,
        "source": source,
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
        logger.info(f"Saved outbound message: {message_id} to {to_wa_id} (reply_to={in_reply_to}, source={source})")
    except Exception as e:
        logger.error(f"Failed to save outbound message {message_id}: {e}")


