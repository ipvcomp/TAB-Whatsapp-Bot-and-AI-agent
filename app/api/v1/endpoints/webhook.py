import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, HTTPException, Request, Response

from app.core.config import get_settings
from app.models.webhook import WebhookPayload
from app.services import contact_service, message_service
from app.services.auto_reply_service import handle_auto_reply
from app.services.session_service import get_session, save_session, build_default_session
from app.services.llm_service import call_generic
from app.services.whatsapp_service import send_text_message
from app.services.llm_log_service import save_llm_log
from app.services.policy_flow_service import (
    is_policy_trigger, is_in_policy_flow, handle_policy_flow, get_shortcuts_text, SHORTCUT_COMMANDS,
    is_in_bp_upload_flow, handle_boarding_pass_upload_flow,
)

WELCOME_BUTTON_IDS = {
    "welcome_purchase_policy", "welcome_submit_boarding", "welcome_get_support",
    "buy_cover", "check_policy", "update_details", "boarding_pass", "help",
}

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

                welcome_reply_id = _get_welcome_button_id(message)
                if welcome_reply_id:
                    await _handle_welcome_button(
                        reply_id=welcome_reply_id,
                        message=message,
                        sender_wa_id=sender_wa_id,
                        profile_name=resolved_profile,
                        phone_number_id=msg_phone_number_id,
                    )
                    continue

                if message.type == "text" and message.text:
                    text_lower = message.text.body.lower().strip()
                    if text_lower == "#shortcuts":
                        await send_text_message(
                            to=sender_wa_id,
                            body=get_shortcuts_text(),
                            phone_number_id=msg_phone_number_id,
                            in_reply_to=message.id,
                            source="auto_reply",
                        )
                        log_event("SHORTCUTS", {"to": sender_wa_id})
                        continue

                user_session = await get_session(sender_wa_id)

                if message.type == "text" and message.text:
                    from app.services.auto_reply_service import is_greeting, send_welcome_message
                    if is_greeting(message.text.body) and not is_in_policy_flow(user_session) and not is_in_bp_upload_flow(user_session):
                        await send_welcome_message(
                            to=sender_wa_id,
                            phone_number_id=msg_phone_number_id,
                            in_reply_to=message.id,
                        )
                        log_event("GREETING_WELCOME", {"to": sender_wa_id})
                        continue

                    text_lower = message.text.body.lower().strip()
                    cancel_words = ("cancel", "/cancel", "exit", "/exit", "stop", "/stop", "#cancel", "#exit")
                    if text_lower in cancel_words and not is_in_policy_flow(user_session) and not is_in_bp_upload_flow(user_session):
                        await send_welcome_message(
                            to=sender_wa_id,
                            phone_number_id=msg_phone_number_id,
                            in_reply_to=message.id,
                        )
                        log_event("CANCEL_NO_FLOW", {"to": sender_wa_id})
                        continue

                    text_norm = " ".join(text_lower.split())
                    welcome_text_map = {
                        "purchase policy": "welcome_purchase_policy",
                        "submit boarding pass": "welcome_submit_boarding",
                        "get support": "welcome_get_support",
                    }
                    matched_welcome = welcome_text_map.get(text_norm)
                    if matched_welcome and not is_in_policy_flow(user_session) and not is_in_bp_upload_flow(user_session):
                        await _handle_welcome_button(
                            reply_id=matched_welcome,
                            message=message,
                            sender_wa_id=sender_wa_id,
                            profile_name=resolved_profile,
                            phone_number_id=msg_phone_number_id,
                        )
                        log_event("WELCOME_TEXT_MATCH", {"to": sender_wa_id, "action": matched_welcome})
                        continue

                if is_in_bp_upload_flow(user_session):
                    log_event("BP_UPLOAD_FLOW", {
                        "message_id": message.id,
                        "from": sender_wa_id,
                        "trigger": "active_bp_flow",
                    })
                    await handle_boarding_pass_upload_flow(
                        message=message,
                        sender_wa_id=sender_wa_id,
                        profile_name=resolved_profile,
                        phone_number_id=msg_phone_number_id,
                        in_reply_to=message.id,
                    )
                elif is_policy_trigger(message) and not is_in_policy_flow(user_session):
                    log_event("POLICY_KEYWORD_WELCOME", {
                        "message_id": message.id,
                        "from": sender_wa_id,
                    })
                    await send_welcome_message(
                        to=sender_wa_id,  
                        phone_number_id=msg_phone_number_id,
                        in_reply_to=message.id,
                    )
                elif is_in_policy_flow(user_session):
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

    user_message = message.text.body if message.text else ""
    current_node = session.get("current_node", "N01")
    phone_number = session.get("phone_number", sender_wa_id)
    user_name = session.get("first_name", profile_name or "")

    request_payload = {
        "user_id": sender_wa_id,
        "phone_number": phone_number,
        "message": user_message,
        "user_name": user_name,
        "current_node": current_node,
    }

    log_event("LLM_GENERIC_REQUEST", {
        "message_id": inbound_message_id,
        "from": sender_wa_id,
        "current_node": current_node,
    })

    llm_response = await call_generic(
        user_id=sender_wa_id,
        phone_number=phone_number,
        message=user_message,
        user_name=user_name,
        current_node=current_node,
    )

    if not llm_response:
        log_event("LLM_GENERIC_FAILED", {
            "message_id": inbound_message_id,
            "from": sender_wa_id,
            "fallback": "auto_reply",
        })

        await save_llm_log(
            inbound_message_id=inbound_message_id,
            contact_wa_id=sender_wa_id,
            request_payload=request_payload,
            raw_response=None,
            success=False,
            error="LLM generic unreachable or returned error",
        )

        from app.services.auto_reply_service import handle_auto_reply
        await handle_auto_reply(
            to_wa_id=sender_wa_id,
            incoming_text=user_message,
            message_type=message.type,
            phone_number_id=phone_number_id,
            in_reply_to=inbound_message_id,
        )
        return

    response_text = llm_response.get("response", "")
    suggested_node = llm_response.get("suggested_node")
    detected_intent = llm_response.get("detected_intent")
    confidence = llm_response.get("confidence")

    log_event("LLM_GENERIC_RESPONSE", {
        "message_id": inbound_message_id,
        "suggested_node": suggested_node,
        "detected_intent": detected_intent,
        "confidence": confidence,
        "tokens_used": llm_response.get("tokens_used"),
        "processing_time_ms": llm_response.get("processing_time_ms"),
    })

    if not response_text:
        log_event("LLM_GENERIC_EMPTY_RESPONSE", {
            "message_id": inbound_message_id,
            "from": sender_wa_id,
            "fallback": "auto_reply",
        })

        await save_llm_log(
            inbound_message_id=inbound_message_id,
            contact_wa_id=sender_wa_id,
            request_payload=request_payload,
            raw_response=llm_response,
            success=False,
            error="LLM returned empty response text",
        )

        from app.services.auto_reply_service import handle_auto_reply
        await handle_auto_reply(
            to_wa_id=sender_wa_id,
            incoming_text=user_message,
            message_type=message.type,
            phone_number_id=phone_number_id,
            in_reply_to=inbound_message_id,
        )
        return

    if suggested_node or detected_intent:
        session["last_node"] = current_node
        if suggested_node:
            session["current_node"] = suggested_node
        if detected_intent:
            session["last_intent"] = detected_intent
        if "user_id" not in session:
            session["user_id"] = sender_wa_id
        await save_session(session)

    outbound_message_id = None

    send_result = await send_text_message(
        to=sender_wa_id,
        body=response_text,
        phone_number_id=phone_number_id,
        in_reply_to=inbound_message_id,
        source="llm",
    )

    if send_result:
        outbound_message_id = send_result.get("_wamid")

    log_event("LLM_GENERIC_REPLY_SENT", {
        "to": sender_wa_id,
        "sent": send_result is not None,
        "outbound_message_id": outbound_message_id,
    })

    log_error = None if outbound_message_id else "Meta API send failed"

    await save_llm_log(
        inbound_message_id=inbound_message_id,
        contact_wa_id=sender_wa_id,
        request_payload=request_payload,
        raw_response=llm_response,
        outbound_message_id=outbound_message_id,
        success=outbound_message_id is not None,
        error=log_error,
    )


def _get_welcome_button_id(message) -> str | None:
    if message.type != "interactive":
        return None
    interactive = getattr(message, "interactive", None)
    if not interactive:
        return None

    # Handle button_reply (old 3-button style)
    button_reply = interactive.get("button_reply") if isinstance(interactive, dict) else getattr(interactive, "button_reply", None)
    if button_reply:
        reply_id = button_reply.get("id") if isinstance(button_reply, dict) else getattr(button_reply, "id", None)
        if reply_id in WELCOME_BUTTON_IDS:
            return reply_id

    # Handle list_reply (new list menu style)
    list_reply = interactive.get("list_reply") if isinstance(interactive, dict) else getattr(interactive, "list_reply", None)
    if list_reply:
        reply_id = list_reply.get("id") if isinstance(list_reply, dict) else getattr(list_reply, "id", None)
        if reply_id in WELCOME_BUTTON_IDS:
            return reply_id

    return None


async def _handle_welcome_button(
    reply_id: str,
    message,
    sender_wa_id: str,
    profile_name: str,
    phone_number_id: str,
):
    in_reply_to = message.id

    from app.services.session_service import get_session, save_session
    session = await get_session(sender_wa_id)
    if session:
        flow_state = session.get("policy_flow", {})
        if flow_state.get("active"):
            old_policy_id = flow_state.get("policy_id")
            if old_policy_id:
                from app.services.policy_service import cancel_policy
                await cancel_policy(old_policy_id)
            session["policy_flow"] = {}
            session["active_policy_id"] = None
            await save_session(session)
        bp_state = session.get("bp_upload_flow", {})
        if bp_state.get("active"):
            session["bp_upload_flow"] = {}
            await save_session(session)

    if reply_id in ("welcome_purchase_policy", "buy_cover"):
        log_event("WELCOME_BUTTON", {"action": "purchase_policy", "from": sender_wa_id})
        await handle_policy_flow(
            message=message,
            sender_wa_id=sender_wa_id,
            profile_name=profile_name,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            skip_menu=True,
        )
    elif reply_id in ("welcome_submit_boarding", "boarding_pass"):
        log_event("WELCOME_BUTTON", {"action": "submit_boarding", "from": sender_wa_id})
        await handle_boarding_pass_upload_flow(
            message=message,
            sender_wa_id=sender_wa_id,
            profile_name=profile_name,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
        )
    elif reply_id in ("welcome_get_support", "help", "check_policy", "update_details"):
        log_event("WELCOME_BUTTON", {"action": "get_support", "from": sender_wa_id})
        await send_text_message(
            to=sender_wa_id,
            body=get_shortcuts_text(),
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="auto_reply",
        )
