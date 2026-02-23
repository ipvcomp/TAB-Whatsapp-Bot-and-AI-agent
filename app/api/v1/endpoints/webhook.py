import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, HTTPException, Request, Response

from app.core.config import get_settings
from app.models.webhook import WebhookPayload

logger = logging.getLogger(__name__)
router = APIRouter()


def log_event(event_type: str, data: dict):
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        **data,
    }
    print(f"[WEBHOOK] {json.dumps(log_entry)}", flush=True)


@router.get("/webhook")
async def verify_webhook(
    request: Request,
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
):
    settings = get_settings()

    log_event("VERIFICATION_REQUEST", {
        "client_ip": request.client.host if request.client else "unknown",
        "hub_mode": hub_mode,
        "token_provided": bool(hub_verify_token),
        "challenge_length": len(hub_challenge),
    })

    if not settings.WHATSAPP_VERIFY_TOKEN:
        log_event("VERIFICATION_ERROR", {"reason": "VERIFY_TOKEN not configured on server"})
        raise HTTPException(status_code=500, detail="Verify token not configured")

    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        log_event("VERIFICATION_SUCCESS", {"challenge": hub_challenge})
        return Response(content=hub_challenge, media_type="text/plain")

    log_event("VERIFICATION_FAILED", {
        "reason": "token mismatch",
        "hub_mode": hub_mode,
    })
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def handle_webhook(request: Request):
    body = await request.json()

    log_event("INCOMING_WEBHOOK", {
        "client_ip": request.client.host if request.client else "unknown",
        "object_type": body.get("object", "unknown"),
        "raw_payload": body,
    })

    try:
        payload = WebhookPayload(**body)
    except Exception as e:
        log_event("PARSE_ERROR", {"error": str(e), "raw_body": body})
        return Response(status_code=200)

    for entry in payload.entry:
        for change in entry.changes:
            if change.field == "messages":
                value = change.value

                log_event("WEBHOOK_CHANGE", {
                    "entry_id": entry.id,
                    "field": change.field,
                    "phone_number_id": value.metadata.get("phone_number_id", "unknown"),
                    "contacts_count": len(value.contacts) if value.contacts else 0,
                    "messages_count": len(value.messages) if value.messages else 0,
                    "statuses_count": len(value.statuses) if value.statuses else 0,
                })

                if value.messages:
                    for message in value.messages:
                        log_event("MESSAGE_RECEIVED", {
                            "from": message.from_,
                            "message_id": message.id,
                            "type": message.type,
                            "timestamp": message.timestamp,
                            "text": message.text.body if message.text else None,
                        })

                if value.statuses:
                    for status in value.statuses:
                        log_event("STATUS_UPDATE", {
                            "message_id": status.id,
                            "status": status.status,
                            "recipient_id": status.recipient_id,
                            "timestamp": status.timestamp,
                        })

    return Response(status_code=200)
