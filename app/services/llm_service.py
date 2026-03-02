import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.core.config import get_settings
from app.models.webhook import WhatsAppMessage

logger = logging.getLogger(__name__)


def _determine_message_type(message: WhatsAppMessage) -> str:
    if message.type == "text":
        return "text"
    if message.type == "interactive":
        if message.interactive:
            if message.interactive.button_reply:
                return "button"
            if message.interactive.list_reply:
                return "list_reply"
        return "interactive"
    if message.type == "button":
        return "button"
    if message.type == "image":
        return "image"
    return message.type


def _extract_text_content(message: WhatsAppMessage) -> Optional[str]:
    if message.text:
        return message.text.body
    return None


def _extract_button_payload(message: WhatsAppMessage) -> Optional[str]:
    if message.interactive:
        if message.interactive.button_reply:
            return message.interactive.button_reply.id
        if message.interactive.list_reply:
            return message.interactive.list_reply.id
    if message.button:
        return message.button.payload
    return None


def build_llm_payload(
    message: WhatsAppMessage,
    session: dict,
) -> dict:
    msg_type = _determine_message_type(message)

    payload = {
        "message_id": message.id,
        "message_type": msg_type,
        "timestamp": datetime.fromtimestamp(
            int(message.timestamp), tz=timezone.utc
        ).isoformat() if message.timestamp else datetime.now(timezone.utc).isoformat(),
        "session": session,
    }

    text_content = _extract_text_content(message)
    if text_content:
        payload["text_content"] = text_content

    button_payload = _extract_button_payload(message)
    if button_payload:
        payload["button_payload"] = button_payload

    if message.image:
        payload["image_url"] = message.image.id

    return payload


async def call_llm(payload: dict) -> Optional[dict]:
    settings = get_settings()

    if not settings.LLM_API_URL:
        logger.error("LLM_API_URL is not configured")
        return None

    url = f"{settings.LLM_API_URL}/api/v1/webhook"

    try:
        async with httpx.AsyncClient(timeout=float(settings.LLM_API_TIMEOUT)) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            try:
                response_data = response.json()
            except Exception:
                logger.error(f"LLM returned non-JSON response: HTTP {response.status_code} - {response.text[:500]}")
                return None

            if response.status_code == 200 and response_data.get("success"):
                logger.info(f"LLM response received for message {payload.get('message_id')}")
                return response_data
            else:
                logger.error(f"LLM error: HTTP {response.status_code} - {response_data}")
                return None
    except httpx.TimeoutException:
        logger.error(f"LLM request timeout after {settings.LLM_API_TIMEOUT}s")
        return None
    except httpx.ConnectError:
        logger.error(f"LLM connection failed: {url}")
        return None
    except Exception as e:
        logger.error(f"LLM request error: {e}")
        return None
