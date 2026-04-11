import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, HTTPException, Request, Response

from app.core.config import get_settings
from app.models.webhook import WebhookPayload
from app.services import contact_service, message_service
from app.services.auto_reply_service import handle_auto_reply
from app.services.session_service import get_session, save_session, build_default_session
from app.services.llm_service import build_llm_payload, call_llm
from app.services.whatsapp_service import send_whatsapp_payload
from app.services.llm_log_service import save_llm_log
from app.services.policy_flow_service import is_policy_trigger, is_in_policy_flow, handle_policy_flow
from app.services.travelassist_flow_service import is_travelassist_trigger, is_in_travelassist_flow, handle_travelassist

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
    })

    try:
        payload = WebhookPayload(**body)
    except Exception as e:
        log_event("PARSE_ERROR", {"error": str(e), "raw_body": body})
        return Response(status_code=200)

    for entry in payload.entry:
        for change in entry.changes:
            try:
                await _process_change(entry.id, change)
            except Exception as e:
                log_event("PROCESSING_ERROR", {
                    "entry_id": entry.id,
                    "error": str(e),
                })

    return Response(status_code=200)


async def _process_change(entry_id: str, change):
    settings = get_settings()
    value = change.value

    log_event("WEBHOOK_CHANGE", {
        "entry_id": entry_id,
        "field": change.field,
        "business_phone": value.metadata.display_phone_number,
        "phone_number_id": value.metadata.phone_number_id,
        "contacts_count": len(value.contacts) if value.contacts else 0,
        "messages_count": len(value.messages) if value.messages else 0,
        "statuses_count": len(value.statuses) if value.statuses else 0,
    })

    contact_map = {}
    if value.contacts:
        for contact in value.contacts:
            contact_map[contact.wa_id] = contact.profile.name

    if value.messages:
        for message in value.messages:
            sender_wa_id = message.sender
            if not sender_wa_id:
                log_event("MESSAGE_SKIPPED", {
                    "message_id": message.id,
                    "reason": "missing sender wa_id",
                })
                continue

            profile_name = contact_map.get(sender_wa_id, "")

            saved_msg = await message_service.save_inbound_message(
                message=message,
                metadata=value.metadata,
                contact_wa_id=sender_wa_id,
            )

            is_new_message = saved_msg and saved_msg.get("is_new", False)

            saved_contact = await contact_service.upsert_contact(
                wa_id=sender_wa_id,
                profile_name=profile_name,
                phone_number_id=value.metadata.phone_number_id,
                business_phone=value.metadata.display_phone_number,
                increment_message_count=is_new_message,
            )

            log_event("CONTACT_SAVED", {
                "wa_id": sender_wa_id,
                "profile_name": saved_contact.get("profile_name", "Unknown") if saved_contact else profile_name or "Unknown",
                "message_count": saved_contact.get("message_count", 0) if saved_contact else 0,
            })

            log_event("MESSAGE_SAVED", {
                "message_id": message.id,
                "from": sender_wa_id,
                "type": message.type,
                "text": message.text.body if message.text else None,
                "is_new": is_new_message,
            })

            if is_new_message:
                resolved_profile = profile_name or (saved_contact.get("profile_name", "") if saved_contact else "")
                msg_phone_number_id = value.metadata.phone_number_id

                user_session = await get_session(sender_wa_id)
                if is_travelassist_trigger(message) or is_in_travelassist_flow(user_session):
                    log_event("TRAVELASSIST_FLOW", {
                        "message_id": message.id,
                        "from": sender_wa_id,
                        "trigger": "keyword" if is_travelassist_trigger(message) else "active_flow",
                    })
                    await handle_travelassist(
                        message=message.dict() if hasattr(message, 'dict') else message,
                        sender_wa_id=sender_wa_id,
                        phone_number_id=msg_phone_number_id,
                        msg_id=message.id,
                        session=user_session,
                    )
                elif is_policy_trigger(message) or is_in_policy_flow(user_session):
                    log_event("POLICY_FLOW", {
                        "message_id": message.id,
                        "from": sender_wa_id,
                        "trigger": "keyword" if is_policy_trigger(message) else "active_flow",
                    })
                    await handle_policy_flow(
                        message=message,
                        sender_wa_id=sender_wa_id,
                        profile_name=resolved_profile,
                        phone_number_id=msg_phone_number_id,
                        in_reply_to=message.id,
                    )
                elif settings.LLM_API_URL:
                    await _handle_llm_reply(
                        message=message,
                        sender_wa_id=sender_wa_id,
                        profile_name=resolved_profile,
                        phone_number_id=msg_phone_number_id,
                    )
                else:
                    reply_result = await handle_auto_reply(
                        to_wa_id=sender_wa_id,
                        incoming_text=message.text.body if message.text else None,
                        message_type=message.type,
                        phone_number_id=msg_phone_number_id,
                        in_reply_to=message.id,
                    )
                    log_event("AUTO_REPLY", {
                        "to": sender_wa_id,
                        "sent": reply_result is not None,
                    })

    if value.statuses:
        for status in value.statuses:
            log_event("STATUS_UPDATE", {
                "message_id": status.id,
                "status": status.status,
                "recipient_id": status.recipient_id,
                "timestamp": status.timestamp,
            })

    if value.errors:
        for error in value.errors:
            log_event("WEBHOOK_ERROR", {"error": error})


async def _handle_llm_reply(message, sender_wa_id, profile_name, phone_number_id):
    inbound_message_id = message.id

    session = await get_session(sender_wa_id)
    if not session:
        session = build_default_session(
            user_id=sender_wa_id,
            phone_number=sender_wa_id,
            first_name=profile_name,
        )

    llm_payload = build_llm_payload(message=message, session=session)

    log_event("LLM_REQUEST", {
        "message_id": inbound_message_id,
        "from": sender_wa_id,
        "message_type": llm_payload.get("message_type"),
        "current_node": session.get("current_node"),
    })

    llm_response = await call_llm(llm_payload)

    if not llm_response:
        log_event("LLM_FAILED", {
            "message_id": inbound_message_id,
            "from": sender_wa_id,
            "fallback": "auto_reply",
        })

        await save_llm_log(
            inbound_message_id=inbound_message_id,
            contact_wa_id=sender_wa_id,
            request_payload=llm_payload,
            raw_response=None,
            success=False,
            error="LLM unreachable or returned error",
        )

        from app.services.auto_reply_service import handle_auto_reply
        await handle_auto_reply(
            to_wa_id=sender_wa_id,
            incoming_text=message.text.body if message.text else None,
            message_type=message.type,
            phone_number_id=phone_number_id,
            in_reply_to=inbound_message_id,
        )
        return

    whatsapp_payload = llm_response.get("whatsapp_payload")
    updated_session = llm_response.get("updated_session")
    metadata = llm_response.get("processing_metadata", {})

    log_event("LLM_RESPONSE", {
        "message_id": inbound_message_id,
        "intent": metadata.get("intent_code"),
        "confidence": metadata.get("intent_confidence"),
        "target_node": metadata.get("target_node"),
        "previous_node": metadata.get("previous_node"),
    })

    if updated_session:
        if "user_id" not in updated_session:
            updated_session["user_id"] = sender_wa_id
        if "phone_number" not in updated_session:
            updated_session["phone_number"] = session.get("phone_number", sender_wa_id)
        await save_session(updated_session)

    outbound_message_id = None

    if whatsapp_payload:
        if not whatsapp_payload.get("to") or not whatsapp_payload.get("type"):
            log_event("LLM_INVALID_PAYLOAD", {
                "message_id": inbound_message_id,
                "missing_to": not whatsapp_payload.get("to"),
                "missing_type": not whatsapp_payload.get("type"),
            })

            await save_llm_log(
                inbound_message_id=inbound_message_id,
                contact_wa_id=sender_wa_id,
                request_payload=llm_payload,
                raw_response=llm_response,
                success=False,
                error="Invalid whatsapp_payload: missing 'to' or 'type'",
            )
            return

        send_result = await send_whatsapp_payload(
            whatsapp_payload=whatsapp_payload,
            phone_number_id=phone_number_id,
            in_reply_to=inbound_message_id,
            source="llm",
        )

        if send_result:
            outbound_message_id = send_result.get("_wamid")

        log_event("LLM_REPLY_SENT", {
            "to": sender_wa_id,
            "type": whatsapp_payload.get("type"),
            "sent": send_result is not None,
            "outbound_message_id": outbound_message_id,
        })
    else:
        log_event("LLM_NO_PAYLOAD", {
            "message_id": inbound_message_id,
            "from": sender_wa_id,
        })

    send_succeeded = outbound_message_id is not None
    log_error = None
    if whatsapp_payload and not send_result:
        log_error = "Meta API send failed"
    elif not whatsapp_payload:
        log_error = "LLM returned no whatsapp_payload"

    await save_llm_log(
        inbound_message_id=inbound_message_id,
        contact_wa_id=sender_wa_id,
        request_payload=llm_payload,
        raw_response=llm_response,
        outbound_message_id=outbound_message_id,
        success=send_succeeded,
        error=log_error,
    )
