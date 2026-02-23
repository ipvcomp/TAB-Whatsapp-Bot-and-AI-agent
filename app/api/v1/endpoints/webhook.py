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
    print(f"[WEBHOOK] {json.dumps(log_entry, default=str)}", flush=True)


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
            value = change.value

            log_event("WEBHOOK_CHANGE", {
                "entry_id": entry.id,
                "field": change.field,
                "business_phone": value.metadata.display_phone_number,
                "phone_number_id": value.metadata.phone_number_id,
                "contacts_count": len(value.contacts) if value.contacts else 0,
                "messages_count": len(value.messages) if value.messages else 0,
                "statuses_count": len(value.statuses) if value.statuses else 0,
            })

            if value.contacts:
                for contact in value.contacts:
                    log_event("CONTACT_INFO", {
                        "wa_id": contact.wa_id,
                        "profile_name": contact.profile.name,
                    })

            if value.messages:
                for message in value.messages:
                    msg_data = {
                        "from": message.sender,
                        "message_id": message.id,
                        "type": message.type,
                        "timestamp": message.timestamp,
                    }

                    if message.text:
                        msg_data["text"] = message.text.body
                    if message.image:
                        msg_data["image_id"] = message.image.id
                        msg_data["image_caption"] = message.image.caption
                    if message.audio:
                        msg_data["audio_id"] = message.audio.id
                        msg_data["is_voice"] = message.audio.voice
                    if message.video:
                        msg_data["video_id"] = message.video.id
                        msg_data["video_caption"] = message.video.caption
                    if message.document:
                        msg_data["document_id"] = message.document.id
                        msg_data["filename"] = message.document.filename
                    if message.location:
                        msg_data["latitude"] = message.location.latitude
                        msg_data["longitude"] = message.location.longitude
                        msg_data["location_name"] = message.location.name
                    if message.reaction:
                        msg_data["reaction_emoji"] = message.reaction.emoji
                        msg_data["reaction_to"] = message.reaction.message_id
                    if message.interactive:
                        msg_data["interactive_type"] = message.interactive.type
                        if message.interactive.button_reply:
                            msg_data["button_id"] = message.interactive.button_reply.id
                            msg_data["button_title"] = message.interactive.button_reply.title
                        if message.interactive.list_reply:
                            msg_data["list_id"] = message.interactive.list_reply.id
                            msg_data["list_title"] = message.interactive.list_reply.title
                    if message.context:
                        msg_data["reply_to_message"] = message.context.id
                        msg_data["reply_to_sender"] = message.context.from_

                    log_event("MESSAGE_RECEIVED", msg_data)

            if value.statuses:
                for status in value.statuses:
                    status_data = {
                        "message_id": status.id,
                        "status": status.status,
                        "recipient_id": status.recipient_id,
                        "timestamp": status.timestamp,
                    }
                    if status.conversation:
                        status_data["conversation_id"] = status.conversation.id
                        status_data["conversation_origin"] = status.conversation.origin
                    if status.pricing:
                        status_data["billable"] = status.pricing.billable
                        status_data["pricing_category"] = status.pricing.category

                    log_event("STATUS_UPDATE", status_data)

            if value.errors:
                for error in value.errors:
                    log_event("WEBHOOK_ERROR", {"error": error})

    return Response(status_code=200)
