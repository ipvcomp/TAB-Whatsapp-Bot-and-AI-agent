import logging
from typing import Optional

from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload

logger = logging.getLogger(__name__)

HELP_FLOW_KEY = "help_flow"


def is_in_help_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    return session.get("temp_data", {}).get(HELP_FLOW_KEY, {}).get("active", False)


async def _get_flow_state(wa_id: str) -> tuple[dict, dict]:
    session = await get_session(wa_id) or {}
    flow = session.setdefault("temp_data", {}).setdefault(HELP_FLOW_KEY, {})
    return session, flow


async def _set_step(session: dict, step: str):
    session["temp_data"][HELP_FLOW_KEY]["step"] = step
    session["temp_data"][HELP_FLOW_KEY]["active"] = True
    await save_session(session)


async def _reset(session: dict):
    session["temp_data"][HELP_FLOW_KEY] = {}
    await save_session(session)


async def _send_text(to: str, body: str, phone_number_id: Optional[str]):
    await send_text_message(to=to, body=body, phone_number_id=phone_number_id, source="help_flow")


async def _send_buttons(
    to: str,
    body: str,
    buttons: list,
    phone_number_id: Optional[str],
    header: Optional[str] = None,
):
    interactive: dict = {
        "type": "button",
        "body": {"text": body},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                for b in buttons
            ]
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="help_flow")


async def start_help_flow(
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session = await get_session(wa_id) or {}
    session.setdefault("temp_data", {})[HELP_FLOW_KEY] = {"active": True, "step": "hlp_menu"}
    if "user_id" not in session:
        session["user_id"] = wa_id
    await save_session(session)

    await _send_text(
        to=wa_id,
        body="🆘 *Help*\n\nWhere do you need help today?\nChoose a topic below 👇",
        phone_number_id=phone_number_id,
    )

    await _send_buttons(
        to=wa_id,
        body="📦 *Coverage & Verification*",
        buttons=[
            {"id": "hlp_buying", "title": "🛍️ Buying cover"},
            {"id": "hlp_kyc",    "title": "✅ KYC verification"},
            {"id": "hlp_payment","title": "💳 Payment issues"},
        ],
        phone_number_id=phone_number_id,
    )

    await _send_buttons(
        to=wa_id,
        body="📋 *Policy & Travel Docs*",
        buttons=[
            {"id": "hlp_policy",  "title": "📋 My policy"},
            {"id": "hlp_boarding","title": "🛫 Boarding pass"},
            {"id": "hlp_claim",   "title": "🏥 Claim support"},
        ],
        phone_number_id=phone_number_id,
    )

    await _send_buttons(
        to=wa_id,
        body="🤝 *Need a human?*",
        buttons=[
            {"id": "hlp_agent", "title": "👤 Speak to an agent"},
        ],
        phone_number_id=phone_number_id,
    )


async def handle_help_flow(
    message,
    sender_wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session, flow = await _get_flow_state(sender_wa_id)
    step = flow.get("step", "hlp_menu")

    reply_id = None
    if message.type == "interactive" and message.interactive:
        if message.interactive.type == "list_reply" and message.interactive.list_reply:
            reply_id = message.interactive.list_reply.id
        elif message.interactive.type == "button_reply" and message.interactive.button_reply:
            reply_id = message.interactive.button_reply.id

    if step == "hlp_menu":
        await _handle_menu_selection(session, sender_wa_id, reply_id, phone_number_id)

    elif step in ("hlp_topic", "hlp_agent_wait"):
        await _handle_action(session, sender_wa_id, reply_id, phone_number_id)

    else:
        await _reset(session)
        await start_help_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)


async def _handle_menu_selection(
    session: dict,
    wa_id: str,
    reply_id: Optional[str],
    phone_number_id: Optional[str],
):
    if reply_id == "hlp_buying":
        await _set_step(session, "hlp_topic")
        await _send_text(
            wa_id,
            "🛍️ *Help — Buying Cover*\n\n"
            "*What you need to know:*\n\n"
            "• Buy your policy for yourself or a group\n\n"
            "• You'll need your flight details — number,\n"
            "  date, departure & destination\n\n"
            "• Choose from available cover types\n"
            "  and see pricing upfront\n\n"
            "• You'll receive your policy on WhatsApp\n"
            "  and email within seconds of payment 🎉",
            phone_number_id,
        )
        await _send_buttons(
            wa_id,
            "Ready to get covered?",
            [{"id": "hlp_act_buy", "title": "Start buying cover →"}],
            phone_number_id,
        )

    elif reply_id == "hlp_kyc":
        await _set_step(session, "hlp_topic")
        await _send_text(
            wa_id,
            "✅ *Help — KYC Verification*\n\n"
            "*What you need to know:*\n\n"
            "• Submit your *NIN* or *BVN* number\n\n"
            "• Make sure the information you submit\n"
            "  matches your travel document\n\n"
            "• Keep your 11-digit number ready\n"
            "  before starting verification\n\n"
            "• Your details are encrypted and\n"
            "  stored securely 🔒",
            phone_number_id,
        )
        await _send_buttons(
            wa_id,
            "Ready to verify your identity?",
            [{"id": "hlp_act_kyc", "title": "Continue to KYC →"}],
            phone_number_id,
        )

    elif reply_id == "hlp_payment":
        await _set_step(session, "hlp_topic")
        await _send_text(
            wa_id,
            "💳 *Help — Payment Issues*\n\n"
            "*What you need to know:*\n\n"
            "• *Bank details* — account number\n"
            "• *Bank name* — account name\n"
            "• *Bank code* — payment reference\n\n"
            "• Payment issues are sometimes temporary —\n"
            "  no worries, you can always retry\n\n"
            "• Your progress is saved, so you won't\n"
            "  need to re-enter your details again",
            phone_number_id,
        )
        await _send_buttons(
            wa_id,
            "Need to complete a payment?",
            [{"id": "hlp_act_pay", "title": "Resume pending payment →"}],
            phone_number_id,
        )

    elif reply_id == "hlp_policy":
        await _set_step(session, "hlp_topic")
        await _send_text(
            wa_id,
            "📋 *Help — My Policy*\n\n"
            "*What you need to know:*\n\n"
            "• Your policy number starts with *TA-*\n"
            "  _(e.g. TA-NG-TAIN-260501-E5F5B0)_\n\n"
            "• Your policy covers all travelers\n"
            "  listed at the time of purchase\n\n"
            "• Policy documents are sent to your\n"
            "  email and saved here on WhatsApp\n\n"
            "• You can check status and details\n"
            "  anytime using the button below 👇",
            phone_number_id,
        )
        await _send_buttons(
            wa_id,
            "View your active policy?",
            [{"id": "hlp_act_policy", "title": "Check my policy →"}],
            phone_number_id,
        )

    elif reply_id == "hlp_boarding":
        await _set_step(session, "hlp_topic")
        await _send_text(
            wa_id,
            "🛫 *Help — Boarding Pass Upload*\n\n"
            "*What you need to know:*\n\n"
            "• Upload your boarding pass directly\n"
            "  in this chat as an image or PDF\n\n"
            "• *Accepted formats:*\n"
            "  JPG, PNG, WebP, PDF\n"
            "  _(max file size: 5 MB)_\n\n"
            "• Make sure these are clearly visible:\n"
            "  ✓ Passenger name\n"
            "  ✓ Booking reference\n"
            "  ✓ Flight number & date",
            phone_number_id,
        )
        await _send_buttons(
            wa_id,
            "Ready to upload?",
            [{"id": "hlp_act_boarding", "title": "Upload boarding pass →"}],
            phone_number_id,
        )

    elif reply_id == "hlp_claim":
        await _set_step(session, "hlp_topic")
        await _send_text(
            wa_id,
            "🏥 *Help — Claim Support*\n\n"
            "*How TravelAssist works:*\n\n"
            "🔍 We monitor your flight automatically\n\n"
            "⚡ If a disruption is detected, we check\n"
            "   your eligibility instantly\n\n"
            "💰 Payout is triggered automatically —\n"
            "   no claim forms needed\n\n"
            "📱 You'll be notified here on WhatsApp\n\n"
            "_No forms. No stress.\n"
            "TravelAssist handles everything for you._",
            phone_number_id,
        )
        await _send_buttons(
            wa_id,
            "Need to speak to someone?",
            [{"id": "hlp_act_agent", "title": "Send to an agent →"}],
            phone_number_id,
        )

    elif reply_id == "hlp_agent":
        await _set_step(session, "hlp_agent_wait")
        await _send_text(
            wa_id,
            "🤝 *Connecting you to an agent...*\n\n"
            "Please hold on — a live support agent\n"
            "will be with you shortly.",
            phone_number_id,
        )
        await _send_text(
            wa_id,
            "📞 *Live Agent Support*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "WhatsApp: *+234 800 000 0000*\n"
            "Email:       *support@ipurvey.com*\n"
            "Hours:      *Mon–Fri, 8am–6pm WAT*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "While you wait, you can:\n"
            "• Check your policy status\n"
            "• Review your cover details",
            phone_number_id,
        )
        await _send_buttons(
            wa_id,
            "What would you like to do?",
            [
                {"id": "hlp_act_policy", "title": "📋 Check my policy"},
                {"id": "hlp_home",       "title": "🏠 Close chat"},
            ],
            phone_number_id,
        )

    else:
        await _reset(session)
        await start_help_flow(wa_id=wa_id, phone_number_id=phone_number_id)


async def _handle_action(
    session: dict,
    wa_id: str,
    reply_id: Optional[str],
    phone_number_id: Optional[str],
):
    if reply_id == "hlp_act_buy":
        await _reset(session)
        from app.services.buy_cover_flow_service import start_buy_cover_flow
        await start_buy_cover_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_act_kyc":
        await _reset(session)
        from app.services.kyc_flow_service import start_kyc_flow
        await start_kyc_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_act_pay":
        await _reset(session)
        from app.services.payment_flow_service import start_payment_flow
        await start_payment_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_act_policy":
        await _reset(session)
        from app.services.check_policy_flow_service import start_check_policy_flow
        await start_check_policy_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_act_boarding":
        await _reset(session)
        from app.services.bp_link_flow_service import start_bp_link_flow
        await start_bp_link_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_act_agent":
        await _set_step(session, "hlp_agent_wait")
        await _send_text(
            wa_id,
            "🤝 *Connecting you to an agent...*\n\n"
            "Please hold on — a live support agent\n"
            "will be with you shortly.",
            phone_number_id,
        )
        await _send_text(
            wa_id,
            "📞 *Live Agent Support*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "WhatsApp: *+234 800 000 0000*\n"
            "Email:       *support@ipurvey.com*\n"
            "Hours:      *Mon–Fri, 8am–6pm WAT*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "While you wait, you can:\n"
            "• Check your policy status\n"
            "• Review your cover details",
            phone_number_id,
        )
        await _send_buttons(
            wa_id,
            "What would you like to do?",
            [
                {"id": "hlp_act_policy", "title": "📋 Check my policy"},
                {"id": "hlp_home",       "title": "🏠 Close chat"},
            ],
            phone_number_id,
        )

    elif reply_id == "hlp_home":
        await _reset(session)
        from app.services.auto_reply_service import send_main_menu
        await send_main_menu(to=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_back":
        await _reset(session)
        await start_help_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    else:
        await _reset(session)
        await start_help_flow(wa_id=wa_id, phone_number_id=phone_number_id)
