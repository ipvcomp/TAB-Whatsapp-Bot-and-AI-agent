import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, HTTPException, Request, Response

from app.core.config import get_settings
from app.models.webhook import WebhookPayload
from app.services import contact_service, message_service
from app.services.auto_reply_service import handle_auto_reply
from app.services.session_service import get_session, save_session, build_default_session
from app.services.llm_service import call_generic, call_extract
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload
from app.services.llm_log_service import save_llm_log
from app.services.buy_cover_flow_service import (
    is_in_buy_cover_flow, start_buy_cover_flow, handle_buy_cover_flow,
    pause_buy_cover_flow, resume_at_current_step as bc_resume,
)
from app.services.kyc_flow_service import resume_at_current_step as kyc_resume
from app.services.payment_flow_service import resume_at_current_step as pay_resume
import app.services.ipurvey_service as ipurvey_service
from app.services.kyc_flow_service import (
    is_in_kyc_flow, handle_kyc_flow, start_kyc_flow,
)
from app.services.payment_flow_service import (
    is_in_payment_flow, handle_payment_flow, start_payment_flow,
)
from app.services.bp_link_flow_service import (
    is_in_bp_link_flow, handle_bp_link_flow, start_bp_link_flow,
    start_eligibility_check_flow,
)
from app.services.help_flow_service import (
    is_in_help_flow, handle_help_flow, start_help_flow,
)
from app.services.check_policy_flow_service import (
    is_in_check_policy_flow, handle_check_policy_flow, start_check_policy_flow,
)
from app.services.update_details_flow_service import (
    is_in_update_details_flow, handle_update_details_flow, start_update_details_flow,
)
from app.services.draft_policies_flow_service import (
    is_in_draft_policies_flow, start_draft_policies_flow, handle_draft_policies_input,
    go_back_one_step as _draft_go_back,
)

WELCOME_BUTTON_IDS = {
    "welcome_purchase_policy", "welcome_submit_boarding", "welcome_get_support",
    "buy_cover", "check_policy", "check_eligibility", "update_details",
    "boarding_pass", "help", "restart_buy", "go_main",
    "welcome_continue_draft", "welcome_discard_draft",
    "welcome_resume_application",
    "welcome_draft_policies",
}

_CX_CONFIRM_IDS = frozenset({
    "cx_yes_exit",  "cx_no_exit",
    "cx_yes_buy",   "cx_no_buy",
    "cx_yes_pol",   "cx_no_pol",
    "cx_yes_upd",   "cx_no_upd",
    "cx_yes_bp",    "cx_no_bp",
    "cx_yes_help",  "cx_no_help",
    "cx_yes_elig",  "cx_no_elig",
    "cx_new_cover", "cx_main_menu",
})

logger = logging.getLogger(__name__)
router = APIRouter()

# ── TEMPORARY TEST OVERRIDE ───────────────────────────────────────────────────
# Sender override disabled — WhatsApp replies go to actual sender.
# Ipurvey MSISDN override is in app/core/test_overrides.py
TEST_OVERRIDE_WA_ID: str | None = None
# ─────────────────────────────────────────────────────────────────────────────


def log_event(event_type: str, data: dict):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    parts = [f"[{event_type}]"]
    priority_keys = ("from", "to", "action", "text", "input", "trigger", "status", "error")
    for k in priority_keys:
        if k in data and data[k] is not None:
            parts.append(f"{k}={data[k]!r}")
    extras = {k: v for k, v in data.items() if k not in priority_keys and v is not None}
    if extras:
        parts.append("| " + "  ".join(f"{k}={v!r}" for k, v in extras.items()))
    print(f"{ts} {' '.join(parts)}", flush=True)


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

    try:
        payload = WebhookPayload(**body)
    except Exception as e:
        raw = body or {}
        has_only_statuses = (
            not any(
                c.get("value", {}).get("messages")
                for e2 in raw.get("entry", [])
                for c in e2.get("changes", [])
            )
        )
        if not has_only_statuses:
            log_event("PARSE_ERROR", {"error": str(e)})
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

            if TEST_OVERRIDE_WA_ID:
                sender_wa_id = TEST_OVERRIDE_WA_ID

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

            if is_new_message:
                _input = None
                if message.type == "text" and message.text:
                    _input = message.text.body
                elif message.type == "interactive" and message.interactive:
                    inter = message.interactive
                    if isinstance(inter, dict):
                        br = inter.get("button_reply") or inter.get("list_reply")
                        _input = f"[button:{br.get('id')}]" if br else "[interactive]"
                    else:
                        br = getattr(inter, "button_reply", None) or getattr(inter, "list_reply", None)
                        if br:
                            bid = br.get("id") if isinstance(br, dict) else getattr(br, "id", None)
                            _input = f"[button:{bid}]"
                        else:
                            _input = "[interactive]"
                elif message.type in ("image", "document", "audio", "video"):
                    _input = f"[{message.type}]"
                log_event("MSG_IN", {"from": sender_wa_id, "input": _input})

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

                user_session = await get_session(sender_wa_id)

                if message.type == "text" and message.text:
                    from app.services.auto_reply_service import is_greeting, send_welcome_message
                    if is_greeting(message.text.body):
                        # Greeting always shows welcome — same behaviour as "00".
                        # Pause buy/kyc/payment so user can resume later, then
                        # clear non-resumable flows before showing welcome.
                        await pause_buy_cover_flow(sender_wa_id)
                        user_session = await get_session(sender_wa_id) or user_session
                        if user_session:
                            td = user_session.setdefault("temp_data", {})
                            for fk in ("bp_link_flow", "help_flow",
                                       "check_policy_flow", "update_details_flow"):
                                if td.get(fk, {}).get("active"):
                                    td[fk] = {}
                            await save_session(user_session)
                        await send_welcome_message(
                            to=sender_wa_id,
                            phone_number_id=msg_phone_number_id,
                            in_reply_to=message.id,
                            wa_id=sender_wa_id,
                        )
                        log_event("GREETING_WELCOME", {"to": sender_wa_id})
                        continue

                    text_lower = message.text.body.lower().strip()
                    cancel_words = ("cancel", "/cancel", "exit", "/exit", "stop", "/stop", "#cancel", "#exit", "99")
                    if text_lower in cancel_words:
                        from app.services.buy_cover_flow_service import show_cancel_purchase_confirm
                        from app.services.check_policy_flow_service import show_cancel_policy_check_confirm
                        from app.services.update_details_flow_service import show_cancel_update_confirm
                        from app.services.bp_link_flow_service import show_cancel_bp_confirm
                        from app.services.help_flow_service import show_exit_help_confirm
                        if (is_in_buy_cover_flow(user_session) or is_in_kyc_flow(user_session)
                                or is_in_payment_flow(user_session)):
                            await show_cancel_purchase_confirm(sender_wa_id, msg_phone_number_id)
                        elif is_in_bp_link_flow(user_session):
                            await show_cancel_bp_confirm(sender_wa_id, msg_phone_number_id, user_session)
                        elif is_in_check_policy_flow(user_session):
                            await show_cancel_policy_check_confirm(sender_wa_id, msg_phone_number_id)
                        elif is_in_update_details_flow(user_session):
                            await show_cancel_update_confirm(sender_wa_id, msg_phone_number_id)
                        elif is_in_help_flow(user_session):
                            await show_exit_help_confirm(sender_wa_id, msg_phone_number_id)
                        else:
                            await _show_exit_confirm(sender_wa_id, msg_phone_number_id)
                            log_event("CANCEL_NO_FLOW", {"to": sender_wa_id})
                        continue

                    text_norm = " ".join(text_lower.split())
                    welcome_text_map = {
                        "purchase policy": "welcome_purchase_policy",
                        "submit boarding pass": "welcome_submit_boarding",
                        "get support": "welcome_get_support",
                        "resume application": "welcome_resume_application",
                        "resume": "welcome_resume_application",
                        "draft policies": "welcome_draft_policies",
                        "my drafts": "welcome_draft_policies",
                        "view drafts": "welcome_draft_policies",
                    }
                    matched_welcome = welcome_text_map.get(text_norm)
                    _no_active_flow = (
                        not is_in_buy_cover_flow(user_session)
                        and not is_in_kyc_flow(user_session)
                        and not is_in_payment_flow(user_session)
                        and not is_in_bp_link_flow(user_session)
                        and not is_in_help_flow(user_session)
                        and not is_in_check_policy_flow(user_session)
                        and not is_in_update_details_flow(user_session)
                        and not is_in_draft_policies_flow(user_session)
                    )
                    if matched_welcome and _no_active_flow:
                        await _handle_welcome_button(
                            reply_id=matched_welcome,
                            message=message,
                            sender_wa_id=sender_wa_id,
                            profile_name=resolved_profile,
                            phone_number_id=msg_phone_number_id,
                        )
                        log_event("WELCOME_TEXT_MATCH", {"to": sender_wa_id, "action": matched_welcome})
                        continue

                    # ── Row 1: Wake-up / main menu — LLM intent detection ──────
                    # Pre-check: only call LLM when text contains an intent
                    # keyword. Avoids double-LLM for random/short messages.
                    _INTENT_TRIGGER_WORDS = {
                        "buy", "purchase", "cover", "policy", "insurance", "travel",
                        "boarding", "pass", "submit", "upload",
                        "support", "help", "assist", "question", "enquiry",
                        "kharidna", "chahiye", "bima", "ticket",
                        "draft", "existing", "saved", "check", "view", "status",
                    }
                    _has_intent_kw = any(
                        kw in message.text.body.lower() for kw in _INTENT_TRIGGER_WORDS
                    )
                    if not matched_welcome and _no_active_flow and _has_intent_kw:
                        _wakeup_llm = await call_extract(
                            user_id=sender_wa_id,
                            field_name="menu_intent",
                            question_asked=(
                                "Main menu options: "
                                "'check draft policy / view existing policy / policy status / saved policy / is there a policy', "
                                "'buy insurance / purchase new policy / travel cover', "
                                "'submit boarding pass / upload boarding pass', or 'get support / help'."
                            ),
                            user_response=message.text.body,
                            expected_format="text",
                        )
                        if _wakeup_llm and _wakeup_llm.get("is_valid") and _wakeup_llm.get("extracted_value"):
                            _wk_ev = str(_wakeup_llm["extracted_value"]).lower()
                            _wk_intent = None
                            if any(k in _wk_ev for k in ("draft", "existing", "check", "view", "saved", "status")):
                                _wk_intent = "check_policy"
                            elif any(k in _wk_ev for k in ("buy", "purchase", "cover", "insurance", "travel")):
                                _wk_intent = "welcome_purchase_policy"
                            elif any(k in _wk_ev for k in ("boarding", "pass", "submit", "upload")):
                                _wk_intent = "welcome_submit_boarding"
                            elif any(k in _wk_ev for k in ("support", "help", "question", "assist", "enquiry")):
                                _wk_intent = "welcome_get_support"
                            if _wk_intent:
                                await _handle_welcome_button(
                                    reply_id=_wk_intent,
                                    message=message,
                                    sender_wa_id=sender_wa_id,
                                    profile_name=resolved_profile,
                                    phone_number_id=msg_phone_number_id,
                                )
                                log_event("WELCOME_LLM_INTENT", {"to": sender_wa_id, "intent": _wk_intent})
                                continue
                    # ──────────────────────────────────────────────────────────

                if message.type == "text" and message.text:
                    text_lower_mm = message.text.body.lower().strip()
                    text_norm_mm = " ".join(text_lower_mm.split())
                    main_menu_triggers = {"00", "main menu", "#menu", "#main", "#home"}
                    if text_norm_mm in main_menu_triggers or text_lower_mm in main_menu_triggers:
                        if user_session:
                            # Pause buy/kyc/payment (save state for resume later)
                            await pause_buy_cover_flow(sender_wa_id)
                            # Reload session after pause to avoid overwriting paused_context
                            user_session = await get_session(sender_wa_id) or user_session
                            # Clear other flows entirely (not resumable)
                            td = user_session.setdefault("temp_data", {})
                            for fk in ("bp_link_flow", "help_flow",
                                       "check_policy_flow", "update_details_flow",
                                       "draft_policies_flow"):
                                if td.get(fk, {}).get("active"):
                                    td[fk] = {}
                            await save_session(user_session)
                        from app.services.auto_reply_service import send_main_menu
                        await send_main_menu(
                            to=sender_wa_id,
                            phone_number_id=msg_phone_number_id,
                            in_reply_to=message.id,
                            wa_id=sender_wa_id,
                        )
                        log_event("MAIN_MENU_TRIGGER", {"to": sender_wa_id, "text": text_lower_mm})
                        continue

                # ── Global shortcuts: 9 = help, 0 = go back ──────────────────
                if message.type == "text" and message.text:
                    _nav_text = message.text.body.strip()
                    _nav_norm = _nav_text.lower()

                    # 9 or #help → start help flow (works from anywhere)
                    # Exception: if user is in traveler-count step, "9" is a valid count
                    _bc_step_now = (
                        (user_session or {})
                        .get("temp_data", {})
                        .get("buy_cover_flow", {})
                        .get("step", "")
                    )
                    _nine_is_count = (
                        _nav_text == "9"
                        and _bc_step_now == "buy_cover_traveler_count"
                    )
                    if (_nav_text == "9" or _nav_norm == "#help") and not _nine_is_count:
                        if user_session:
                            # Pause buy/kyc/payment flows so user can resume after help
                            await pause_buy_cover_flow(sender_wa_id)
                            user_session = await get_session(sender_wa_id) or user_session
                            td = user_session.setdefault("temp_data", {})
                            for fk in ("bp_link_flow", "check_policy_flow",
                                       "update_details_flow", "draft_policies_flow"):
                                if td.get(fk, {}).get("active"):
                                    td[fk] = {}
                            await save_session(user_session)
                        await start_help_flow(
                            wa_id=sender_wa_id,
                            phone_number_id=msg_phone_number_id,
                        )
                        log_event("SHORTCUT_HELP", {"to": sender_wa_id})
                        continue

                    # 0 or #back → go back exactly ONE step in current flow
                    if _nav_text == "0" or _nav_norm in ("#back", "back"):
                        from app.services.auto_reply_service import send_main_menu
                        # Priority order for back navigation:
                        # KYC and Payment MUST come first — they run on top of buy_cover,
                        # which keeps active=False during those sub-flows, so buy_cover
                        # would incorrectly match if checked first.
                        # check_policy, bp_link, and update_details are fully independent
                        # flows and MUST be checked before buy_cover so that stale
                        # buy_cover session data cannot intercept their back navigation.
                        # Order: KYC → Payment → Check Policy → BP Link → Update Details
                        #        → Buy Cover → Help → (main menu)
                        if is_in_kyc_flow(user_session):
                            from app.services.kyc_flow_service import go_back_one_step as _kyc_back
                            await _kyc_back(
                                wa_id=sender_wa_id,
                                phone_number_id=msg_phone_number_id,
                            )
                            log_event("SHORTCUT_BACK", {"to": sender_wa_id, "flow": "kyc"})
                        elif is_in_payment_flow(user_session):
                            from app.services.payment_flow_service import go_back_one_step as _pay_back
                            await _pay_back(
                                wa_id=sender_wa_id,
                                phone_number_id=msg_phone_number_id,
                            )
                            log_event("SHORTCUT_BACK", {"to": sender_wa_id, "flow": "payment"})
                        elif is_in_check_policy_flow(user_session):
                            from app.services.check_policy_flow_service import go_back_one_step as _cp_back
                            await _cp_back(
                                wa_id=sender_wa_id,
                                phone_number_id=msg_phone_number_id,
                            )
                            log_event("SHORTCUT_BACK", {"to": sender_wa_id, "flow": "check_policy"})
                        elif is_in_bp_link_flow(user_session):
                            from app.services.bp_link_flow_service import go_back_one_step as _bp_back
                            await _bp_back(
                                wa_id=sender_wa_id,
                                phone_number_id=msg_phone_number_id,
                            )
                            log_event("SHORTCUT_BACK", {"to": sender_wa_id, "flow": "bp_link"})
                        elif is_in_update_details_flow(user_session):
                            from app.services.update_details_flow_service import go_back_one_step as _upd_back
                            await _upd_back(
                                wa_id=sender_wa_id,
                                phone_number_id=msg_phone_number_id,
                            )
                            log_event("SHORTCUT_BACK", {"to": sender_wa_id, "flow": "update_details"})
                        elif is_in_draft_policies_flow(user_session):
                            await _draft_go_back(
                                wa_id=sender_wa_id,
                                phone_number_id=msg_phone_number_id,
                            )
                            log_event("SHORTCUT_BACK", {"to": sender_wa_id, "flow": "draft_policies"})
                        elif is_in_buy_cover_flow(user_session):
                            from app.services.buy_cover_flow_service import go_back_one_step as _bc_back
                            await _bc_back(
                                wa_id=sender_wa_id,
                                phone_number_id=msg_phone_number_id,
                            )
                            log_event("SHORTCUT_BACK", {"to": sender_wa_id, "flow": "buy_cover"})
                        elif is_in_help_flow(user_session):
                            await start_help_flow(
                                wa_id=sender_wa_id,
                                phone_number_id=msg_phone_number_id,
                            )
                            log_event("SHORTCUT_BACK", {"to": sender_wa_id, "flow": "help"})
                        else:
                            await send_main_menu(
                                to=sender_wa_id,
                                phone_number_id=msg_phone_number_id,
                                wa_id=sender_wa_id,
                            )
                            log_event("SHORTCUT_BACK", {"to": sender_wa_id, "flow": "none→menu"})
                        continue

                # ── Row 34: Utility menu — LLM shortcut intent detection ──────
                # Pre-check: only call LLM when message looks like a navigation
                # command. This avoids firing on legitimate flow inputs (names,
                # emails, dates, flight numbers, etc.) and causing huge delays.
                _NAV_TRIGGER_WORDS = {
                    "back", "previous", "undo", "go back", "piche", "pichhe", "wapas",
                    "help", "assist", "madad",
                    "menu", "home", "restart", "start over",
                    "cancel", "exit", "quit", "band karo",
                }
                if message.type == "text" and message.text:
                    _util_text = message.text.body.strip()
                    _has_nav_kw = any(kw in _util_text.lower() for kw in _NAV_TRIGGER_WORDS)
                    if _util_text and len(_util_text) <= 80 and _has_nav_kw:
                        _util_llm = await call_extract(
                            user_id=sender_wa_id,
                            field_name="navigation_intent",
                            question_asked=(
                                "Navigation shortcuts available: go back (0), help (9), "
                                "main menu (00), cancel/exit (99)."
                            ),
                            user_response=_util_text,
                            expected_format="text",
                        )
                        if _util_llm and _util_llm.get("is_valid") and _util_llm.get("extracted_value"):
                            _nav_ev = str(_util_llm["extracted_value"]).lower()
                            _nav_matched = False
                            if any(k in _nav_ev for k in ("back", "previous", "undo", "return", "go back")):
                                from app.services.auto_reply_service import send_main_menu as _smm
                                if is_in_buy_cover_flow(user_session):
                                    from app.services.buy_cover_flow_service import go_back_one_step as _bc_b
                                    await _bc_b(wa_id=sender_wa_id, phone_number_id=msg_phone_number_id)
                                elif is_in_kyc_flow(user_session):
                                    from app.services.kyc_flow_service import go_back_one_step as _kyc_b
                                    await _kyc_b(wa_id=sender_wa_id, phone_number_id=msg_phone_number_id)
                                elif is_in_payment_flow(user_session):
                                    from app.services.payment_flow_service import go_back_one_step as _pay_b
                                    await _pay_b(wa_id=sender_wa_id, phone_number_id=msg_phone_number_id)
                                else:
                                    await _smm(to=sender_wa_id, phone_number_id=msg_phone_number_id, wa_id=sender_wa_id)
                                log_event("SHORTCUT_LLM_BACK", {"to": sender_wa_id, "text": _util_text})
                                _nav_matched = True
                            elif any(k in _nav_ev for k in ("help", "assist", "support")):
                                await pause_buy_cover_flow(sender_wa_id)
                                await start_help_flow(wa_id=sender_wa_id, phone_number_id=msg_phone_number_id)
                                log_event("SHORTCUT_LLM_HELP", {"to": sender_wa_id, "text": _util_text})
                                _nav_matched = True
                            elif any(k in _nav_ev for k in ("main menu", "home", "start over", "restart")):
                                await pause_buy_cover_flow(sender_wa_id)
                                from app.services.auto_reply_service import send_main_menu as _smm2
                                await _smm2(to=sender_wa_id, phone_number_id=msg_phone_number_id, wa_id=sender_wa_id)
                                log_event("SHORTCUT_LLM_MENU", {"to": sender_wa_id, "text": _util_text})
                                _nav_matched = True
                            elif any(k in _nav_ev for k in ("cancel", "exit", "quit", "stop")):
                                from app.services.buy_cover_flow_service import show_cancel_purchase_confirm
                                if is_in_buy_cover_flow(user_session) or is_in_kyc_flow(user_session) or is_in_payment_flow(user_session):
                                    await show_cancel_purchase_confirm(sender_wa_id, msg_phone_number_id)
                                else:
                                    from app.services.auto_reply_service import send_main_menu as _smm3
                                    await _smm3(to=sender_wa_id, phone_number_id=msg_phone_number_id, wa_id=sender_wa_id)
                                log_event("SHORTCUT_LLM_CANCEL", {"to": sender_wa_id, "text": _util_text})
                                _nav_matched = True
                            if _nav_matched:
                                continue
                # ─────────────────────────────────────────────────────────────

                # === Global cancel-confirmation response handler ===
                reply_id_for_cx = None
                if message.type == "interactive" and message.interactive:
                    inter = message.interactive
                    if isinstance(inter, dict):
                        br = inter.get("button_reply") or inter.get("list_reply")
                        reply_id_for_cx = br.get("id") if br else None
                    else:
                        br = getattr(inter, "button_reply", None) or getattr(inter, "list_reply", None)
                        if br:
                            reply_id_for_cx = br.get("id") if isinstance(br, dict) else getattr(br, "id", None)
                if reply_id_for_cx and reply_id_for_cx in _CX_CONFIRM_IDS:
                    await _handle_cx_confirm(reply_id_for_cx, sender_wa_id, msg_phone_number_id, user_session)
                    continue
                # ═══════════════════════════════════════════════════════════════

                if is_in_draft_policies_flow(user_session):
                    log_event("FLOW", {"from": sender_wa_id, "trigger": "DRAFT_POLICIES"})
                    await handle_draft_policies_input(
                        message=message,
                        wa_id=sender_wa_id,
                        phone_number_id=msg_phone_number_id,
                    )
                elif is_in_update_details_flow(user_session):
                    log_event("FLOW", {"from": sender_wa_id, "trigger": "UPDATE_DETAILS"})
                    await handle_update_details_flow(
                        message=message,
                        sender_wa_id=sender_wa_id,
                        phone_number_id=msg_phone_number_id,
                        in_reply_to=message.id,
                    )
                elif is_in_check_policy_flow(user_session):
                    log_event("FLOW", {"from": sender_wa_id, "trigger": "CHECK_POLICY"})
                    await handle_check_policy_flow(
                        message=message,
                        sender_wa_id=sender_wa_id,
                        phone_number_id=msg_phone_number_id,
                        in_reply_to=message.id,
                    )
                elif is_in_help_flow(user_session):
                    log_event("FLOW", {"from": sender_wa_id, "trigger": "HELP"})
                    await handle_help_flow(
                        message=message,
                        sender_wa_id=sender_wa_id,
                        phone_number_id=msg_phone_number_id,
                        in_reply_to=message.id,
                    )
                elif is_in_bp_link_flow(user_session):
                    log_event("FLOW", {"from": sender_wa_id, "trigger": "BP_LINK"})
                    await handle_bp_link_flow(
                        message=message,
                        sender_wa_id=sender_wa_id,
                        phone_number_id=msg_phone_number_id,
                        in_reply_to=message.id,
                    )
                elif is_in_payment_flow(user_session):
                    log_event("FLOW", {"from": sender_wa_id, "trigger": "PAYMENT"})
                    await handle_payment_flow(
                        message=message,
                        sender_wa_id=sender_wa_id,
                        phone_number_id=msg_phone_number_id,
                        in_reply_to=message.id,
                    )
                elif is_in_kyc_flow(user_session):
                    log_event("FLOW", {"from": sender_wa_id, "trigger": "KYC"})
                    await handle_kyc_flow(
                        message=message,
                        sender_wa_id=sender_wa_id,
                        phone_number_id=msg_phone_number_id,
                        in_reply_to=message.id,
                    )
                elif is_in_buy_cover_flow(user_session):
                    log_event("FLOW", {"from": sender_wa_id, "trigger": "BUY_COVER"})
                    await handle_buy_cover_flow(
                        message=message,
                        sender_wa_id=sender_wa_id,
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
        pass

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

    # ── Intent-based flow routing ─────────────────────────────────────────────
    # When the LLM returns a recognised flow intent AND the user is not
    # currently inside any flow, route them into the right flow instead of
    # just sending the conversational text reply.
    if detected_intent:
        _td = session.get("temp_data", {})
        _in_flow = any(
            _td.get(fk, {}).get("active")
            for fk in (
                "buy_cover_flow", "kyc_flow", "payment_flow",
                "bp_link_flow", "help_flow", "check_policy_flow",
                "update_details_flow",
            )
        )
        if not _in_flow:
            _di = detected_intent.lower()
            _INTENT_MAP = {
                "buy_policy":           "buy_cover",
                "buy_cover":            "buy_cover",
                "purchase_policy":      "buy_cover",
                "check_policy":         "check_policy",
                "view_policy":          "check_policy",
                "my_policy":            "check_policy",
                "update_details":       "update_details",
                "edit_details":         "update_details",
                "upload_boarding_pass": "bp_link",
                "boarding_pass":        "bp_link",
                "help":                 "help",
                "support":              "help",
                "get_support":          "help",
            }
            _routed = _INTENT_MAP.get(_di)

            if _routed:
                log_event("LLM_INTENT_ROUTE", {
                    "to": sender_wa_id,
                    "intent": detected_intent,
                    "flow": _routed,
                })
                if response_text and response_text.strip():
                    _route_send_result = await send_text_message(
                        to=sender_wa_id,
                        body=response_text,
                        phone_number_id=phone_number_id,
                        in_reply_to=inbound_message_id,
                        source="llm",
                    )
                    _route_wamid = _route_send_result.get("_wamid") if _route_send_result else None
                    log_event("LLM_INTENT_ROUTE_REPLY_SENT", {
                        "to": sender_wa_id,
                        "intent": detected_intent,
                        "flow": _routed,
                        "sent": _route_send_result is not None,
                        "outbound_message_id": _route_wamid,
                    })
                    if _route_wamid:
                        await message_service.save_outbound_message(
                            message_id=_route_wamid,
                            contact_wa_id=sender_wa_id,
                            phone_number_id=phone_number_id,
                            body=response_text,
                            source="llm",
                            in_reply_to=inbound_message_id,
                        )
                if _routed == "buy_cover":
                    from app.services.buy_cover_flow_service import start_buy_cover_flow
                    await start_buy_cover_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
                elif _routed == "check_policy":
                    from app.services.check_policy_flow_service import start_check_policy_flow
                    await start_check_policy_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
                elif _routed == "update_details":
                    from app.services.update_details_flow_service import start_update_details_flow
                    await start_update_details_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
                elif _routed == "bp_link":
                    from app.services.bp_link_flow_service import start_bp_link_flow
                    await start_bp_link_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
                elif _routed == "help":
                    from app.services.help_flow_service import start_help_flow
                    await start_help_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
                return
    # ─────────────────────────────────────────────────────────────────────────

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

    # Determine if the action is a buy-cover action
    _is_buy_action = reply_id in ("buy_cover", "welcome_purchase_policy", "restart_buy", "welcome_resume_application")

    if session:
        if _is_buy_action:
            # For buy actions: DON'T clear buy/kyc/payment.
            # start_buy_cover_flow will detect the paused context and offer resume/fresh.
            # Only clear non-purchase flows.
            td = session.setdefault("temp_data", {})
            for fk in ("bp_link_flow", "help_flow", "check_policy_flow", "update_details_flow"):
                if td.get(fk, {}).get("active"):
                    td[fk] = {}
            await save_session(session)
        else:
            # For non-buy actions: pause buy/kyc/payment (user may want to resume later)
            await pause_buy_cover_flow(sender_wa_id)
            session = await get_session(sender_wa_id) or session
            td = session.setdefault("temp_data", {})
            for fk in ("bp_link_flow", "help_flow", "check_policy_flow", "update_details_flow"):
                if td.get(fk, {}).get("active"):
                    td[fk] = {}
            await save_session(session)

    if reply_id in ("buy_cover", "restart_buy"):
        log_event("WELCOME_BUTTON", {"action": "buy_cover", "from": sender_wa_id})
        await start_buy_cover_flow(
            wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
        )
    elif reply_id == "welcome_resume_application":
        log_event("WELCOME_BUTTON", {"action": "resume_application", "from": sender_wa_id})
        await start_buy_cover_flow(
            wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
        )
    elif reply_id == "welcome_purchase_policy":
        log_event("WELCOME_BUTTON", {"action": "purchase_policy", "from": sender_wa_id})
        await start_buy_cover_flow(
            wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
        )
    elif reply_id == "go_main":
        log_event("WELCOME_BUTTON", {"action": "go_main", "from": sender_wa_id})
        from app.services.auto_reply_service import send_main_menu
        await send_main_menu(
            to=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            wa_id=sender_wa_id,
        )
    elif reply_id in ("welcome_draft_policies", "check_eligibility"):
        log_event("WELCOME_BUTTON", {"action": "draft_policies", "from": sender_wa_id})
        await start_draft_policies_flow(
            wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
        )
    elif reply_id in ("welcome_submit_boarding", "boarding_pass"):
        log_event("WELCOME_BUTTON", {"action": "submit_boarding", "from": sender_wa_id})
        await start_bp_link_flow(
            wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
        )
    elif reply_id == "check_policy":
        log_event("WELCOME_BUTTON", {"action": "check_policy", "from": sender_wa_id})
        await start_check_policy_flow(
            wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
        )
    elif reply_id == "update_details":
        log_event("WELCOME_BUTTON", {"action": "update_details", "from": sender_wa_id})
        await start_update_details_flow(
            wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
        )
    elif reply_id in ("welcome_get_support", "help"):
        log_event("WELCOME_BUTTON", {"action": "help", "from": sender_wa_id})
        await start_help_flow(
            wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
        )
    elif reply_id in ("welcome_continue_draft", "welcome_discard_draft"):
        log_event("WELCOME_BUTTON", {"action": "draft_policies", "from": sender_wa_id})
        await start_draft_policies_flow(
            wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
        )


async def _show_exit_confirm(wa_id: str, phone_number_id: str):
    """Show Exit confirmation screen for users not in any active flow."""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": wa_id,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": (
                    "❌ *Exit*\n\n"
                    "Are you sure you want to leave?\n"
                    "Is there anything else we can help you with?"
                )
            },
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "cx_yes_exit", "title": "❌ Yes, exit"}},
                    {"type": "reply", "reply": {"id": "cx_no_exit",  "title": "↩️ No, stay here"}},
                ]
            },
        },
    }
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="webhook")


async def _handle_cx_confirm(reply_id: str, wa_id: str, phone_number_id: str, session):
    """Handle all cancel-confirmation button responses globally."""
    from app.services.auto_reply_service import send_welcome_message, send_main_menu

    if reply_id == "cx_yes_exit":
        await send_welcome_message(to=wa_id, phone_number_id=phone_number_id, in_reply_to=None, wa_id=wa_id)

    elif reply_id == "cx_no_exit":
        await send_text_message(
            to=wa_id,
            body="Okay, no problem! 😊 Is there anything I can help you with?",
            phone_number_id=phone_number_id,
            source="webhook",
        )

    elif reply_id == "cx_yes_buy":
        if session:
            # Cancel the draft policy in the backend (99 = cancel)
            policy_id_cx = session.get("api_data", {}).get("policy_id")
            if not policy_id_cx:
                # Also check paused context
                _pc = session.get("paused_context")
                if _pc:
                    policy_id_cx = _pc.get("policy_id")
            if policy_id_cx:
                try:
                    await ipurvey_service.cancel_draft_policy(policy_id_cx)
                    logger.info(f"[webhook] cx_yes_buy: cancelled draft {policy_id_cx} for {wa_id}")
                except Exception as exc:
                    logger.error(f"[webhook] cx_yes_buy cancel_draft failed: {exc}")
            td = session.setdefault("temp_data", {})
            for fk in ("buy_cover_flow", "kyc_flow", "payment_flow"):
                td[fk] = {}
            session.pop("paused_context", None)
            session["api_data"] = {}
            await save_session(session)
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": wa_id,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {
                    "text": (
                        "✅ *Purchase cancelled*\n\n"
                        "No worries — you can come back anytime to protect your trip."
                    )
                },
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": "cx_new_cover", "title": "✈️ Start a new cover"}},
                        {"type": "reply", "reply": {"id": "cx_main_menu", "title": "🏠 Main menu"}},
                    ]
                },
            },
        }
        await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="webhook")

    elif reply_id == "cx_no_buy":
        # Re-show the original prompt for the step the user was on before pressing 99.
        # Each flow's resume_at_current_step uses _redisplay_step internally so the
        # correct original prompt (not a validation error) is re-sent.
        _resumed = False
        if session:
            td = session.get("temp_data", {})
            if td.get("buy_cover_flow", {}).get("active"):
                await bc_resume(wa_id, phone_number_id)
                _resumed = True
            elif td.get("kyc_flow", {}).get("active"):
                await kyc_resume(wa_id, phone_number_id)
                _resumed = True
            elif td.get("payment_flow", {}).get("active"):
                await pay_resume(wa_id, phone_number_id)
                _resumed = True
        if not _resumed:
            await send_text_message(
                to=wa_id,
                body="Okay, your purchase is still in progress. 👍",
                phone_number_id=phone_number_id,
                source="webhook",
            )

    elif reply_id == "cx_yes_pol":
        if session:
            session.setdefault("temp_data", {})["check_policy_flow"] = {}
            await save_session(session)
        await send_main_menu(to=wa_id, phone_number_id=phone_number_id, wa_id=wa_id)

    elif reply_id == "cx_no_pol":
        await send_text_message(
            to=wa_id,
            body="Okay, continuing your policy check. 👍",
            phone_number_id=phone_number_id,
            source="webhook",
        )

    elif reply_id == "cx_yes_upd":
        if session:
            session.setdefault("temp_data", {})["update_details_flow"] = {}
            await save_session(session)
        await send_main_menu(to=wa_id, phone_number_id=phone_number_id, wa_id=wa_id)

    elif reply_id == "cx_no_upd":
        await send_text_message(
            to=wa_id,
            body="Okay, continuing your update. 👍",
            phone_number_id=phone_number_id,
            source="webhook",
        )

    elif reply_id in ("cx_yes_bp", "cx_yes_elig"):
        if session:
            session.setdefault("temp_data", {})["bp_link_flow"] = {}
            await save_session(session)
        await send_main_menu(to=wa_id, phone_number_id=phone_number_id, wa_id=wa_id)

    elif reply_id in ("cx_no_bp", "cx_no_elig"):
        await send_text_message(
            to=wa_id,
            body="Okay, continuing. 👍",
            phone_number_id=phone_number_id,
            source="webhook",
        )

    elif reply_id == "cx_yes_help":
        if session:
            session.setdefault("temp_data", {})["help_flow"] = {}
            await save_session(session)
        await send_welcome_message(to=wa_id, phone_number_id=phone_number_id, wa_id=wa_id)

    elif reply_id == "cx_no_help":
        await send_text_message(
            to=wa_id,
            body="Okay, continuing with help. 👍",
            phone_number_id=phone_number_id,
            source="webhook",
        )

    elif reply_id == "cx_new_cover":
        if session:
            td = session.setdefault("temp_data", {})
            for fk in ("buy_cover_flow", "kyc_flow", "payment_flow"):
                td[fk] = {}
            await save_session(session)
        await start_buy_cover_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "cx_main_menu":
        await send_main_menu(to=wa_id, phone_number_id=phone_number_id, wa_id=wa_id)
