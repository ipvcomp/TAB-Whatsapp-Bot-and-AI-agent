import logging
from typing import Optional

from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload

logger = logging.getLogger(__name__)

KYC_FLOW_KEY = "kyc_flow"
BUY_COVER_FLOW_KEY = "buy_cover_flow"


def is_in_kyc_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    return session.get("temp_data", {}).get(KYC_FLOW_KEY, {}).get("active", False)


async def _get_flow_state(wa_id: str) -> tuple[dict, dict]:
    session = await get_session(wa_id) or {}
    flow = session.setdefault("temp_data", {}).setdefault(KYC_FLOW_KEY, {})
    return session, flow


def _mask_id(val: str) -> str:
    if len(val) <= 3:
        return val
    return "•" * (len(val) - 3) + val[-3:]


_UTILITY = (
    "*Utility options:*\n"
    "0 ↩️ Back  |  9 🆘 Help  |  00 🏠 Main menu\n"
    "99 ❌ Cancel/Exit"
)


async def _send_text(to: str, body: str, phone_number_id: Optional[str]):
    await send_text_message(to=to, body=body, phone_number_id=phone_number_id, source="kyc_flow")
    await send_text_message(to=to, body=_UTILITY, phone_number_id=phone_number_id, source="kyc_flow")


async def _send_buttons(to: str, body: str, buttons: list, phone_number_id: Optional[str]):
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in buttons
                ]
            },
        },
    }
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="kyc_flow")
    await send_text_message(to=to, body=_UTILITY, phone_number_id=phone_number_id, source="kyc_flow")


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
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="kyc_flow")
    await send_text_message(to=to, body=_UTILITY, phone_number_id=phone_number_id, source="kyc_flow")


async def _send_help(wa_id: str, session: dict, phone_number_id: Optional[str]):
    session["temp_data"][KYC_FLOW_KEY]["step"] = "kyc_help"
    await save_session(session)
    await _send_list(
        wa_id,
        "> *What you need to know:*\n"
        "> ✅ You can verify using either *BVN* or *NIN*\n"
        "> 🔒 We only use this to confirm your identity for policy issuance\n"
        "> 👤 Make sure the number belongs to the traveller buying the policy\n"
        "> 📱 Your BVN/NIN is never stored or echoed back — handled securely\n"
        "> 🔢 Both BVN and NIN are 11 digits — example: 12345678901\n\n"
        "Ready to verify?",
        "Choose",
        [{"title": "Options", "rows": [
            {"id": "kyc_bvn",   "title": "🪪 Verify with BVN"},
            {"id": "kyc_nin",   "title": "🪪 Verify with NIN"},
            {"id": "kyc_agent", "title": "📞 Speak to an agent"},
        ]}],
        phone_number_id,
    )


async def start_kyc_flow(
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session = await get_session(wa_id) or {}
    session.setdefault("temp_data", {})[KYC_FLOW_KEY] = {
        "active": True,
        "step": "kyc_intro",
        "data": {},
    }
    session["temp_data"].get(BUY_COVER_FLOW_KEY, {}).update({"active": False})
    if "user_id" not in session:
        session["user_id"] = wa_id
    await save_session(session)

    await _send_list(
        wa_id,
        "We may verify your identity to support any future payouts and ensure "
        "security and accurate policy issuance. If you've already completed this, "
        "we'll only carry out verification again if your details have changed.\n\n"
        "> 🔒 *Your privacy matters*\n"
        "> We only use your National Biometric ID to verify your identity for this "
        "purchase. Your data is handled securely and never shared.\n\n"
        "How would you like to verify your identity?\n"
        "Select the country that issued your national biometric ID:",
        "Select method",
        [{"title": "Verification Method", "rows": [
            {"id": "kyc_bvn",  "title": "🪪 BVN (Nigeria)"},
            {"id": "kyc_nin",  "title": "🪪 NIN (Nigeria)"},
            {"id": "kyc_help", "title": "🆘 Help"},
        ]}],
        phone_number_id,
    )


async def handle_kyc_flow(
    message,
    sender_wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session, flow = await _get_flow_state(sender_wa_id)
    step = flow.get("step", "kyc_intro")
    data = flow.setdefault("data", {})

    text = ""
    if message.type == "text" and message.text:
        text = message.text.body.strip()

    reply_id = None
    if message.type == "interactive" and message.interactive:
        inter = message.interactive
        if isinstance(inter, dict):
            br = inter.get("button_reply") or inter.get("list_reply")
            reply_id = br.get("id") if br else None
        else:
            br = getattr(inter, "button_reply", None) or getattr(inter, "list_reply", None)
            if br:
                reply_id = br.get("id") if isinstance(br, dict) else getattr(br, "id", None)

    # ── KYC intro ─────────────────────────────────────────────────────────────
    if step == "kyc_intro":
        if reply_id == "kyc_help":
            await _send_help(sender_wa_id, session, phone_number_id)
        elif reply_id == "kyc_nin":
            data["kyc_method"] = "NIN"
            flow["step"] = "kyc_consent"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                "🔒 We will only use your National Biometric ID to verify "
                "your identity for this purchase.",
                [
                    {"id": "kyc_consent_yes", "title": "1. ✅ Yes, continue"},
                    {"id": "kyc_consent_no",  "title": "2. Go back"},
                ],
                phone_number_id,
            )
        else:
            data["kyc_method"] = "BVN"
            flow["step"] = "kyc_consent"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                "🔒 We will only use your National Biometric ID to verify "
                "your identity for this purchase.",
                [
                    {"id": "kyc_consent_yes", "title": "1. ✅ Yes, continue"},
                    {"id": "kyc_consent_no",  "title": "2. Go back"},
                ],
                phone_number_id,
            )

    # ── Consent ───────────────────────────────────────────────────────────────
    elif step == "kyc_consent":
        if reply_id == "kyc_consent_no":
            flow["step"] = "kyc_intro"
            await save_session(session)
            await start_kyc_flow(sender_wa_id, phone_number_id)
        else:
            method = data.get("kyc_method", "BVN")
            flow["step"] = "kyc_id_input"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                f"🔏 *Please enter your 11-digit {method}*\n\n"
                f"_Example: 12345678901_\n\n"
                f"🔒 _Your {method} is handled securely — only the last 3 digits will be shown for confirmation_",
                phone_number_id,
            )

    # ── BVN / NIN input ───────────────────────────────────────────────────────
    elif step == "kyc_id_input":
        if not text:
            await _send_text(sender_wa_id, "Please type your ID number to continue.", phone_number_id)
            return
        id_number = text.replace(" ", "")
        method = data.get("kyc_method", "BVN")
        masked = _mask_id(id_number)
        data["kyc_id"] = id_number
        await _send_text(sender_wa_id, f"🔍 *Checking your details...*\n_{method}: {masked}_\n_Please wait a moment_ ⏳", phone_number_id)

        if id_number.isdigit() and len(id_number) == 11:
            data["kyc_verified"] = True
            flow["step"] = "kyc_verified"
            await save_session(session)
            await _send_list(
                sender_wa_id,
                f"✅ *Identity Verified*\n"
                f"_{method}: {masked}_\n\n"
                "Your identity has been confirmed. You can now continue to payment.\n\n"
                "What would you like to do next?",
                "Choose an option",
                [{"title": "Next Steps", "rows": [
                    {"id": "kyc_pay",    "title": "1. Continue to payment"},
                    {"id": "kyc_review", "title": "2. Review trip details"},
                    {"id": "kyc_home",   "title": "3. Main menu"},
                ]}],
                phone_number_id,
            )
        else:
            data["kyc_verified"] = False
            flow["step"] = "kyc_failed"
            await save_session(session)
            await _send_list(
                sender_wa_id,
                "⚠️ *Verification Incomplete*\n"
                "> We could not complete verification automatically.\n\n"
                "Please choose:",
                "Choose",
                [{"title": "Options", "rows": [
                    {"id": "kyc_try_bvn", "title": "🪪 Try BVN again"},
                    {"id": "kyc_try_nin", "title": "🪪 Try NIN instead"},
                    {"id": "kyc_help",    "title": "🆘 Get help"},
                ]}],
                phone_number_id,
            )

    # ── Verified — next steps ─────────────────────────────────────────────────
    elif step == "kyc_verified":
        if reply_id == "kyc_pay":
            from app.services.payment_flow_service import start_payment_flow
            await start_payment_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        elif reply_id == "kyc_review":
            bc_data = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {}).get("data", {})
            travelers = bc_data.get("travelers", [])
            traveler_lines = (
                "\n".join(f"  {i+1} — {n}" for i, n in enumerate(travelers))
                if travelers else f"  1 — {bc_data.get('name', '—')}"
            )
            dep = bc_data.get("depart_airport", "").split("—")[0].strip() or "—"
            arr = bc_data.get("arrive_airport", "").split("—")[0].strip() or "—"
            summary = (
                "📋 *Trip Summary*\n\n"
                f"✈️ YOUR TRIP\n"
                f"Airline: {bc_data.get('airline', '—')}\n"
                f"Route: {dep} → {arr}\n"
                f"Flight: {bc_data.get('flight_num', '—')}\n"
                f"Date: {bc_data.get('date', '—')}\n"
                f"Departs: {bc_data.get('depart_time', '—')}\n"
                f"Arrives: {bc_data.get('arrive_time', '—')}\n\n"
                f"👥 TRAVELLERS\n{traveler_lines}\n\n"
                f"🛡️ Cover: {bc_data.get('cover', '—')}"
            )
            await _send_text(sender_wa_id, summary, phone_number_id)
            await _send_list(
                sender_wa_id,
                "What would you like to do next?",
                "Choose an option",
                [{"title": "Next Steps", "rows": [
                    {"id": "kyc_pay",    "title": "1. Continue to payment"},
                    {"id": "kyc_review", "title": "2. Review trip details"},
                    {"id": "kyc_home",   "title": "3. Main menu"},
                ]}],
                phone_number_id,
            )
        elif reply_id == "kyc_home":
            session["temp_data"][KYC_FLOW_KEY] = {}
            session["temp_data"][BUY_COVER_FLOW_KEY] = {}
            await save_session(session)
            from app.services.auto_reply_service import send_main_menu
            await send_main_menu(to=sender_wa_id, phone_number_id=phone_number_id)

    # ── Failed — retry options ────────────────────────────────────────────────
    elif step == "kyc_failed":
        if reply_id == "kyc_try_bvn":
            data["kyc_method"] = "BVN"
            flow["step"] = "kyc_consent"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                "🔒 We will only use your National Biometric ID to verify "
                "your identity for this purchase.",
                [
                    {"id": "kyc_consent_yes", "title": "1. ✅ Yes, continue"},
                    {"id": "kyc_consent_no",  "title": "2. Go back"},
                ],
                phone_number_id,
            )
        elif reply_id == "kyc_try_nin":
            data["kyc_method"] = "NIN"
            flow["step"] = "kyc_consent"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                "🔒 We will only use your National Biometric ID to verify "
                "your identity for this purchase.",
                [
                    {"id": "kyc_consent_yes", "title": "1. ✅ Yes, continue"},
                    {"id": "kyc_consent_no",  "title": "2. Go back"},
                ],
                phone_number_id,
            )
        elif reply_id == "kyc_help":
            await _send_help(sender_wa_id, session, phone_number_id)

    # ── Help ─────────────────────────────────────────────────────────────────
    elif step == "kyc_help":
        if reply_id == "kyc_bvn":
            data["kyc_method"] = "BVN"
            flow["step"] = "kyc_consent"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                "🔒 We will only use your National Biometric ID to verify "
                "your identity for this purchase.",
                [
                    {"id": "kyc_consent_yes", "title": "1. ✅ Yes, continue"},
                    {"id": "kyc_consent_no",  "title": "2. Go back"},
                ],
                phone_number_id,
            )
        elif reply_id == "kyc_nin":
            data["kyc_method"] = "NIN"
            flow["step"] = "kyc_consent"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                "🔒 We will only use your National Biometric ID to verify "
                "your identity for this purchase.",
                [
                    {"id": "kyc_consent_yes", "title": "1. ✅ Yes, continue"},
                    {"id": "kyc_consent_no",  "title": "2. Go back"},
                ],
                phone_number_id,
            )
        elif reply_id == "kyc_agent":
            session["temp_data"][KYC_FLOW_KEY] = {}
            session["temp_data"][BUY_COVER_FLOW_KEY] = {}
            await save_session(session)
            await _send_text(
                sender_wa_id,
                "🤝 *Speak to an agent*\n\n"
                "Our support team will contact you shortly.\n"
                "You can also reach us at *support@ipurvey.com*",
                phone_number_id,
            )
        else:
            await _send_help(sender_wa_id, session, phone_number_id)

    # ── Catch-all ─────────────────────────────────────────────────────────────
    else:
        session["temp_data"][KYC_FLOW_KEY] = {}
        session["temp_data"][BUY_COVER_FLOW_KEY] = {}
        await save_session(session)
        from app.services.auto_reply_service import send_main_menu
        await send_main_menu(to=sender_wa_id, phone_number_id=phone_number_id)
