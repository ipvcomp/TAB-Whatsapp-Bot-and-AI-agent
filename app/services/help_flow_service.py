import logging
from typing import Optional

from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload

logger = logging.getLogger(__name__)

HELP_FLOW_KEY = "help_flow"

MENU_ROWS = [
    {"id": "hlp_buying",   "title": "🛍️ Buying cover"},
    {"id": "hlp_kyc",      "title": "✅ KYC verification"},
    {"id": "hlp_payment",  "title": "💳 Payment issues"},
    {"id": "hlp_policy",   "title": "📋 My policy"},
    {"id": "hlp_boarding", "title": "🛫 Boarding pass"},
    {"id": "hlp_claim",    "title": "🏥 Claim support"},
    {"id": "hlp_agent",    "title": "👤 Speak to an agent"},
]

NAV_ROWS = [
    {"id": "hlp_back", "title": "↩️ Back to Help menu"},
    {"id": "hlp_home", "title": "🏠 Main menu"},
]


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


async def _send_list(
    to: str,
    body: str,
    button_label: str,
    sections: list,
    phone_number_id: Optional[str],
    header: Optional[str] = None,
):
    interactive = {
        "type": "list",
        "body": {"text": body},
        "action": {"button": button_label, "sections": sections},
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

    await _send_list(
        to=wa_id,
        header="🆘 Help",
        body="Where do you need help today?",
        button_label="Select topic",
        sections=[{"title": "Help topics", "rows": MENU_ROWS}],
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
        await _send_text(wa_id,
            "🆘 *Help — Buying Cover*\n\n"
            "*What you need to know:*\n\n"
            "• You can buy cover for yourself or\n"
            "  a group of up to 5 travelers\n\n"
            "• You'll need to enter your travel\n"
            "  details — flight number, date,\n"
            "  departure & destination\n\n"
            "• Choose from available cover types\n"
            "  and see pricing upfront\n\n"
            "• You'll receive your policy on\n"
            "  WhatsApp and email within seconds\n"
            "  of completing payment 🎉",
            phone_number_id)
        await _send_list(wa_id, "Ready to get covered?", "Choose",
            [{"title": "Options", "rows": [
                {"id": "hlp_act_buy", "title": "✈️ Start buying cover"},
            ] + NAV_ROWS}],
            phone_number_id)

    elif reply_id == "hlp_kyc":
        await _set_step(session, "hlp_topic")
        await _send_text(wa_id,
            "🆘 *Help — KYC Verification*\n\n"
            "*What you need to know:*\n\n"
            "• *BVN* (Bank Verification Number)\n"
            "  is an 11-digit number linked to\n"
            "  all your bank accounts in Nigeria\n\n"
            "• *NIN* (National ID Number) is\n"
            "  your unique government-issued ID\n\n"
            "• Keep your 11-digit number ready\n"
            "  before starting verification\n\n"
            "• Your details are encrypted and\n"
            "  stored securely 🔒",
            phone_number_id)
        await _send_list(wa_id, "Ready to verify your identity?", "Choose",
            [{"title": "Options", "rows": [
                {"id": "hlp_act_kyc", "title": "🔒 Continue to KYC"},
            ] + NAV_ROWS}],
            phone_number_id)

    elif reply_id == "hlp_payment":
        await _set_step(session, "hlp_topic")
        await _send_text(wa_id,
            "🆘 *Help — Payment Issues*\n\n"
            "*What you need to know:*\n\n"
            "• We support *4 payment methods:*\n"
            "  🏦 Bank transfer\n"
            "  💳 Card payment\n"
            "  👛 Wallet (9PSB/SmartCash/OPay)\n"
            "  📲 USSD code\n\n"
            "• If your payment was interrupted,\n"
            "  you can resume from where you\n"
            "  stopped using the button below\n\n"
            "• Payment team is available 24/7\n"
            "  to assist with failed transactions",
            phone_number_id)
        await _send_list(wa_id, "Need to complete a payment?", "Choose",
            [{"title": "Options", "rows": [
                {"id": "hlp_act_pay", "title": "💳 Resume payment"},
            ] + NAV_ROWS}],
            phone_number_id)

    elif reply_id == "hlp_policy":
        await _set_step(session, "hlp_topic")
        await _send_text(wa_id,
            "🆘 *Help — My Policy*\n\n"
            "*What you need to know:*\n\n"
            "• Your policy number starts with\n"
            "  *TA-* followed by 6 digits\n"
            "  _(e.g. TA-238491)_\n\n"
            "• Your policy covers all travelers\n"
            "  listed at the time of purchase\n\n"
            "• Policy documents are sent to\n"
            "  your email and saved here on\n"
            "  WhatsApp for easy access\n\n"
            "• To update traveler details or\n"
            "  payout info, use *Update my\n"
            "  details* from the main menu",
            phone_number_id)
        await _send_list(wa_id, "View your active policy?", "Choose",
            [{"title": "Options", "rows": [
                {"id": "hlp_act_policy", "title": "📋 Check my policy"},
            ] + NAV_ROWS}],
            phone_number_id)

    elif reply_id == "hlp_boarding":
        await _set_step(session, "hlp_topic")
        await _send_text(wa_id,
            "🆘 *Help — Boarding Pass Upload*\n\n"
            "*What you need to know:*\n\n"
            "• Upload your boarding pass as an\n"
            "  image or PDF directly in this chat\n\n"
            "• *Accepted formats:*\n"
            "  JPEG, JPG, PNG, PDF\n"
            "  _(max file size: 20 MB)_\n\n"
            "• Make sure these are clearly visible:\n"
            "  ✓ Passenger name(s)\n"
            "  ✓ Booking reference\n"
            "  ✓ Flight number & date\n"
            "  ✓ Origin & destination\n\n"
            "• Your boarding pass is used to\n"
            "  verify eligibility for claims",
            phone_number_id)
        await _send_list(wa_id, "Ready to upload?", "Choose",
            [{"title": "Options", "rows": [
                {"id": "hlp_act_boarding", "title": "🛫 Upload boarding pass"},
            ] + NAV_ROWS}],
            phone_number_id)

    elif reply_id == "hlp_claim":
        await _set_step(session, "hlp_topic")
        await _send_text(wa_id,
            "📋 *Help — Claim Support*\n\n"
            "*How TravelAssist works:*\n\n"
            "🔍 We monitor your flight\n"
            "    automatically\n\n"
            "⚡ If disruption is detected,\n"
            "    we check eligibility instantly\n\n"
            "💰 Payout is triggered\n"
            "    automatically — no claim needed\n\n"
            "📱 You'll be notified here\n"
            "    on WhatsApp\n\n"
            "No forms. No claims. No stress —\n"
            "TravelAssist handles everything automatically.",
            phone_number_id)
        await _send_list(wa_id, "What would you like to do?", "Choose",
            [{"title": "Options", "rows": [
                {"id": "hlp_act_policy", "title": "📄 View my policy"},
                {"id": "hlp_act_agent",  "title": "📞 Speak to an agent"},
                {"id": "hlp_home",       "title": "🏠 Main menu"},
            ]}],
            phone_number_id)

    elif reply_id == "hlp_agent":
        await _set_step(session, "hlp_agent_wait")
        await _send_text(wa_id,
            "👤 *Connecting you to an agent...*\n\n"
            "Please hold on — a live support\n"
            "agent will be with you shortly.",
            phone_number_id)
        await _send_text(wa_id,
            "📞 *Live Agent Support*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "WhatsApp: *+234 800 000 0000*\n"
            "Email:       *support@ipurvey.com*\n"
            "Hours:      *Mon–Fri, 8am–6pm WAT*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\nWhile you wait, you can:",
            phone_number_id)
        await _send_list(wa_id, "Quick actions:", "Choose",
            [{"title": "Options", "rows": [
                {"id": "hlp_act_policy", "title": "📋 Check my policy"},
                {"id": "hlp_back",       "title": "↩️ Back to Help menu"},
                {"id": "hlp_home",       "title": "🏠 Main menu"},
            ]}],
            phone_number_id)

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
        from app.services.auto_reply_service import send_welcome_message
        await send_welcome_message(to=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_act_boarding":
        await _reset(session)
        from app.services.bp_link_flow_service import start_bp_link_flow
        await start_bp_link_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_act_agent":
        await _set_step(session, "hlp_agent_wait")
        await _send_text(wa_id,
            "👤 *Connecting you to an agent...*\n\n"
            "Please hold on — a live support agent will be with you shortly.",
            phone_number_id)
        await _send_text(wa_id,
            "📞 *Live Agent Support*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "WhatsApp: *+234 800 000 0000*\n"
            "Email:       *support@ipurvey.com*\n"
            "Hours:      *Mon–Fri, 8am–6pm WAT*\n"
            "━━━━━━━━━━━━━━━━━━━━",
            phone_number_id)
        await _send_list(wa_id, "Quick actions:", "Choose",
            [{"title": "Options", "rows": [
                {"id": "hlp_act_policy", "title": "📋 Check my policy"},
                {"id": "hlp_back",       "title": "↩️ Back to Help menu"},
                {"id": "hlp_home",       "title": "🏠 Main menu"},
            ]}],
            phone_number_id)

    elif reply_id == "hlp_back":
        await _reset(session)
        await start_help_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_home":
        await _reset(session)
        from app.services.auto_reply_service import send_welcome_message
        await send_welcome_message(to=wa_id, phone_number_id=phone_number_id)

    else:
        await _reset(session)
        await start_help_flow(wa_id=wa_id, phone_number_id=phone_number_id)
