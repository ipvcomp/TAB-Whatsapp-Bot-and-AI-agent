import logging
from fastapi import APIRouter, Query, HTTPException, Request, Response

from app.core.config import get_settings
from app.models.webhook import WebhookPayload

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
):
    settings = get_settings()

    if not settings.WHATSAPP_VERIFY_TOKEN:
        logger.error("WHATSAPP_VERIFY_TOKEN is not configured")
        raise HTTPException(status_code=500, detail="Verify token not configured")

    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verification successful")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning("Webhook verification failed: token mismatch")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def handle_webhook(request: Request):
    body = await request.json()
    logger.info(f"Received webhook event: {body.get('object', 'unknown')}")

    try:
        payload = WebhookPayload(**body)
    except Exception as e:
        logger.error(f"Failed to parse webhook payload: {e}")
        return Response(status_code=200)

    for entry in payload.entry:
        for change in entry.changes:
            if change.field == "messages":
                value = change.value
                if value.messages:
                    for message in value.messages:
                        logger.info(
                            f"Message from {message.from_}: "
                            f"type={message.type}, "
                            f"text={message.text.body if message.text else 'N/A'}"
                        )
                if value.statuses:
                    for status in value.statuses:
                        logger.info(
                            f"Status update: {status.status} for {status.recipient_id}"
                        )

    return Response(status_code=200)
