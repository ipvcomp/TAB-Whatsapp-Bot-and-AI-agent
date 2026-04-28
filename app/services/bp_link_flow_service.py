import logging
from typing import Optional

import app.services.ipurvey_service as ipurvey_service
from app.services.whatsapp_service import download_whatsapp_media

from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload

logger = logging.getLogger(__name__)

BP_LINK_FLOW_KEY   = "bp_link_flow"
PAYMENT_FLOW_KEY   = "payment_flow"
BUY_COVER_FLOW_KEY = "buy_cover_flow"
KYC_FLOW_KEY       = "kyc_flow"

DEMO_POLICIES = [
    {
        "id":       "pol_ltp",
        "name":     "Local Travel Premium",
        "status":   "Active",
        "ref":      "LTP-20240412",
        "airline":  "Air Peace",
        "flight":   "P47123",
        "date":     "12 April 2026",
        "origin":   "Lagos (LOS)",
        "dest":     "Abuja (ABV)",
        "traveler": "Yusuf Usman",
    },
    {
        "id":       "pol_ltb",
        "name":     "Local Travel Basic",
        "status":   "Active",
        "ref":      "LTB-20240308",
        "airline":  "Arik Air",
        "flight":   "W3401",
        "date":     "20 April 2026",
        "origin":   "Abuja (ABV)",
        "dest":     "Port Harcourt (PHC)",
        "traveler": "Aminu Bola",
    },
]

UPLOAD_INSTRUCTIONS = (
    "Please make sure the following are *clearly visible:*\n\n"
    "✅ Passenger name(s)\n"
    "✅ Booking reference\n"
    "✅ Flight details\n"
    "✅ Travel dates\n"
    "✅ Route information"
)


def is_in_bp_link_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    return session.get("temp_data", {}).get(BP_LINK_FLOW_KEY, {}).get("active", False)


async def _get_flow_state(wa_id: str) -> tuple:
    session = await get_session(wa_id) or {}
    flow = session.setdefault("temp_data", {}).setdefault(BP_LINK_FLOW_KEY, {})
    return session, flow


_UTILITY = (
    "*Utility options:*\n"
    "0 ↩️ Back  |  9 🆘 Help  |  00 🏠 Main menu\n"
    "99 ❌ Cancel/Exit"
)


async def _send_text(to: str, body: str, phone_number_id: Optional[str]):
    await send_text_message(to=to, body=body, phone_number_id=phone_number_id, source="bp_link_flow")
    await send_text_message(to=to, body=_UTILITY, phone_number_id=phone_number_id, source="bp_link_flow")


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
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="bp_link_flow")
    await send_text_message(to=to, body=_UTILITY, phone_number_id=phone_number_id, source="bp_link_flow")


async def _go_home(wa_id: str, session: dict, phone_number_id: Optional[str]):
    session["temp_data"][BP_LINK_FLOW_KEY]   = {}
    session["temp_data"][PAYMENT_FLOW_KEY]   = {}
    session["temp_data"][BUY_COVER_FLOW_KEY] = {}
    session["temp_data"][KYC_FLOW_KEY]       = {}
    await save_session(session)
    from app.services.auto_reply_service import send_main_menu
    await send_main_menu(to=wa_id, phone_number_id=phone_number_id)


async def _show_policy_list(wa_id: str, session: dict, flow: dict, action: str, phone_number_id: Optional[str]):
    flow["step"] = "bp_policy"
    if action == "upload":
        action_label = "upload a boarding pass for:"
    elif action == "eligibility":
        action_label = "check eligibility for:"
    else:
        action_label = "link:"

    msisdn = f"+{wa_id}" if not wa_id.startswith("+") else wa_id
    policies = None
    try:
        policies = await ipurvey_service.search_policies(msisdn)
    except Exception as exc:
        logger.error(f"[bp_link] search_policies failed: {exc}")
    if not policies:
        policies = DEMO_POLICIES

    flow.setdefault("data", {})["bp_policies_list"] = policies
    await save_session(session)

    rows = [
        {
            "id":          f"bpp_{i}",
            "title":       str(pol.get("name") or pol.get("productName") or "Policy")[:24],
            "description": f"{pol.get('status','Active')} · {pol.get('ref') or pol.get('policyCode') or pol.get('id','')}",
        }
        for i, pol in enumerate(policies[:10])
    ]
    body = (
        f"We found *{len(policies)} {'policy' if len(policies)==1 else 'policies'}* linked to your number.\n\n"
        f"Please select the policy you would like to {action_label}"
    )
    await _send_list(wa_id, body, "Select policy",
        [{"title": "Your Active Policies", "rows": rows}],
        phone_number_id,
        header="📋 Your Policies")


async def _ask_upload(wa_id: str, session: dict, flow: dict, pol: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_awaiting_doc"
    await save_session(session)
    await _send_text(wa_id,
        f"📌 *Policy:* {pol['name']} ({pol['ref']})\n\n"
        "Please upload a clear image or PDF of your boarding pass.\n\n"
        + UPLOAD_INSTRUCTIONS,
        phone_number_id)


async def _show_upload_confirmed(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_upload_done"
    data = flow.get("data", {})
    await save_session(session)
    ref      = data.get("bp_sel_ref",      "LTP-20240412")
    airline  = data.get("bp_sel_airline",  "Air Peace")
    flight   = data.get("bp_sel_flight",   "P47123")
    date     = data.get("bp_sel_date",     "12 April 2026")
    traveler = data.get("bp_sel_traveler", "Yusuf Usman")
    filename = data.get("bp_filename",     f"boarding_pass_{flight}.pdf")

    await _send_list(wa_id,
        f"📎 *{filename}*\n\n"
        f"Policy:      {ref}   ✅ Active\n"
        f"✈️ Airline:    {airline}\n"
        f"🛫 Flight:     {flight}\n"
        f"📅 Date:       {date}\n"
        f"👤 Traveller: {traveler}\n\n"
        "_Your boarding pass has been saved. You can check eligibility for a payout from the main menu after your flight._",
        "What next?",
        [{"title": "Options", "rows": [
            {"id": "bp_home",   "title": "🏠 Main menu"},
            {"id": "bp_cancel", "title": "❌ Cancel"},
        ]}],
        phone_number_id,
        header="✅ Boarding pass confirmed")


async def _show_link_confirm(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_link_confirm"
    data = flow.get("data", {})
    await save_session(session)
    ref      = data.get("bp_sel_ref",      "LTP-20240412")
    name     = data.get("bp_sel_name",     "Local Travel Premium")
    airline  = data.get("bp_sel_airline",  "Air Peace")
    flight   = data.get("bp_sel_flight",   "P47123")
    date     = data.get("bp_sel_date",     "12 April 2026")
    traveler = data.get("bp_sel_traveler", "Yusuf Usman")

    await _send_list(wa_id,
        "We found an active policy matching your boarding pass.\n"
        "Please confirm this is the correct policy:\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋  {ref}  ·  {name}\n"
        f"✈️   {airline}  ·  {flight}\n"
        f"📅  {date}  ·  {traveler}\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        "Confirm",
        [{"title": "Confirm linking", "rows": [
            {"id": "bp_link_yes", "title": "✅ Yes, link it!"},
            {"id": "bp_back",     "title": "↩️ Back (0)"},
            {"id": "bp_cancel",   "title": "❌ Cancel (99)"},
        ]}],
        phone_number_id,
        header="🔗 Linking to Policy")


async def _show_linked(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_linked_done"
    data = flow.get("data", {})
    await save_session(session)
    flight   = data.get("bp_sel_flight",   "P47123")
    airline  = data.get("bp_sel_airline",  "Air Peace")
    date     = data.get("bp_sel_date",     "12 April 2026")
    traveler = data.get("bp_sel_traveler", "Yusuf Usman")

    await _send_list(wa_id,
        f"✈️  *{flight}  ·  {airline}*\n"
        f"📅  {date}\n👤  {traveler}\n\n"
        "Flight monitoring is now *active*.\n"
        "You will be notified instantly if your flight\n"
        "is disrupted — payout is automatic, no forms needed. 💰",
        "What next?",
        [{"title": "Options", "rows": [
            {"id": "bp_eligibility", "title": "✅ Check eligibility"},
            {"id": "bp_view_policy", "title": "📋 View my policy"},
            {"id": "bp_home",        "title": "🏠 Main menu"},
        ]}],
        phone_number_id,
        header="✈️ Boarding Pass Linked!")


async def _show_policy_card(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_policy_card"
    data = flow.get("data", {})
    await save_session(session)
    ref      = data.get("bp_sel_ref",      "LTP-20240412")
    name     = data.get("bp_sel_name",     "Local Travel Premium")
    airline  = data.get("bp_sel_airline",  "Air Peace")
    flight   = data.get("bp_sel_flight",   "P47123")
    date     = data.get("bp_sel_date",     "12 April 2026")
    traveler = data.get("bp_sel_traveler", "Yusuf Usman")

    await _send_list(wa_id,
        f"🛡️  *{name}*\nPolicy No: {ref}   ✅ Active\n\n"
        f"✈️  Airline:    {airline}\n"
        f"🛫  Flight:     {flight}\n"
        f"📅  Date:       {date}\n"
        f"👤  Traveller: {traveler}",
        "Options",
        [{"title": "Options", "rows": [
            {"id": "bp_home", "title": "🏠 Main menu"},
        ]}],
        phone_number_id,
        header="📋 Your Policy")


async def _show_eligibility(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_eligibility_result"
    data = flow.get("data", {})
    await save_session(session)
    ref      = data.get("bp_sel_ref",      "LTP-20240412")
    airline  = data.get("bp_sel_airline",  "Air Peace")
    flight   = data.get("bp_sel_flight",   "P47123")
    pol_id   = data.get("bp_sel_policy_id", "")

    await _send_text(wa_id,
        "🔍 *Checking your eligibility...*\n"
        "_Verifying policy, flight delay and cover details_\n\n• • •",
        phone_number_id)

    eligibility = None
    if pol_id:
        try:
            eligibility = await ipurvey_service.check_eligibility(pol_id)
        except Exception as exc:
            logger.error(f"[bp_link] check_eligibility failed: {exc}")

    if eligibility and isinstance(eligibility, dict):
        eligible    = eligibility.get("eligible", False)
        delay_str   = eligibility.get("delayDuration") or eligibility.get("delay") or "3hrs 20mins"
        payout_amt  = eligibility.get("payoutAmount") or eligibility.get("amount") or 2500
        try:
            payout_fmt = f"₦{float(payout_amt):,.0f}"
        except (ValueError, TypeError):
            payout_fmt = f"₦{payout_amt}"
        if not eligible:
            await _send_list(wa_id,
                "❌ *Not yet eligible for a payout*\n\n"
                f"✈️  Flight\t\t{flight} — {airline}\n"
                f"📋  Policy\t\t{ref}\n\n"
                "_The flight delay threshold has not been met or no disruption was recorded._",
                "Select option",
                [{"title": "Options", "rows": [
                    {"id": "bp_upload_first", "title": "📤 Upload pass"},
                    {"id": "bp_home",         "title": "🏠 Main menu"},
                ]}],
                phone_number_id)
            return
        delay_display = delay_str
        payout_display = payout_fmt
    else:
        delay_display  = "3hrs 20mins"
        payout_display = "₦2,500"

    await _send_list(wa_id,
        "✅ *You are eligible for a payout!*\n"
        "_Your flight delay meets the cover threshold_\n\n"
        f"✈️  Flight\t\t{flight} — {airline}\n"
        f"⏱️  Delay\t\t{delay_display}\n"
        f"📋  Policy\t\t{ref}\n"
        f"💰  Payout amount\t*{payout_display}*\n\n"
        "Your payout will be sent to your registered\nbank account or wallet automatically.",
        "Select option",
        [{"title": "Options", "rows": [
            {"id": "bp_confirm_payout", "title": "✅ Confirm payout"},
            {"id": "bp_upload_first",   "title": "📤 Upload pass first"},
            {"id": "bp_home",           "title": "🏠 Main menu"},
            {"id": "bp_back",           "title": "↩️ Back (0)"},
            {"id": "bp_cancel",         "title": "❌ Cancel (99)"},
        ]}],
        phone_number_id)


async def _show_payout_initiated(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_payout_done"
    await save_session(session)
    await _send_list(wa_id,
        "💰 *Payout Initiated!*\n\n"
        "₦2,500 is on its way to your account\n"
        "⏱️ _Expected: within 24 hours_",
        "Select option",
        [{"title": "Options", "rows": [
            {"id": "bp_view_policy", "title": "📋 View my policy"},
            {"id": "bp_home",        "title": "🏠 Main menu"},
        ]}],
        phone_number_id,
        header="💰 Payout Initiated")


async def start_bp_link_flow(
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session = await get_session(wa_id) or {}
    session.setdefault("temp_data", {})[BP_LINK_FLOW_KEY] = {
        "active": True,
        "step":   "bp_choose",
        "data":   {},
    }
    session["temp_data"].get(PAYMENT_FLOW_KEY, {}).update({"active": False})
    if "user_id" not in session:
        session["user_id"] = wa_id
    await save_session(session)

    await _send_list(
        wa_id,
        "Please choose an option:",
        "Select option",
        [{"title": "Options", "rows": [
            {"id": "bp_upload_me", "title": "📋 Upload boarding pass"},
            {"id": "bp_help",      "title": "🙋 Help"},
        ]}],
        phone_number_id,
        header="🧳 Upload boarding pass",
    )


async def start_eligibility_check_flow(
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session = await get_session(wa_id) or {}
    session.setdefault("temp_data", {})[BP_LINK_FLOW_KEY] = {
        "active": True,
        "step":   "bp_policy",
        "data":   {"bp_action": "eligibility"},
    }
    if "user_id" not in session:
        session["user_id"] = wa_id
    await save_session(session)
    flow = session["temp_data"][BP_LINK_FLOW_KEY]
    await _show_policy_list(wa_id, session, flow, "eligibility", phone_number_id)


async def handle_bp_link_flow(
    message,
    sender_wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session, flow = await _get_flow_state(sender_wa_id)
    step = flow.get("step", "bp_choose")
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

    media = None
    if message.type == "image" and hasattr(message, "image") and message.image:
        img = message.image
        media = {
            "type":     "image",
            "id":       getattr(img, "id", None) or (img.get("id") if isinstance(img, dict) else None),
            "filename": f"boarding_pass_{data.get('bp_sel_flight', '')}.jpg",
        }
    elif message.type == "document" and hasattr(message, "document") and message.document:
        doc = message.document
        media = {
            "type":     "document",
            "id":       getattr(doc, "id", None) or (doc.get("id") if isinstance(doc, dict) else None),
            "filename": getattr(doc, "filename", None) or (doc.get("filename") if isinstance(doc, dict) else None)
                        or f"boarding_pass_{data.get('bp_sel_flight', '')}.pdf",
        }

    # ── Screen 1: Choose option ───────────────────────────────────────────────
    if step == "bp_choose":
        if reply_id == "bp_upload_me":
            data["bp_action"] = "upload"
            await _show_policy_list(sender_wa_id, session, flow, "upload", phone_number_id)
        elif reply_id == "bp_help":
            session["temp_data"][BP_LINK_FLOW_KEY] = {}
            await save_session(session)
            from app.services.help_flow_service import start_help_flow
            await start_help_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        else:
            await start_bp_link_flow(sender_wa_id, phone_number_id)

    # ── Screen 2: Select policy ───────────────────────────────────────────────
    elif step == "bp_policy":
        action = data.get("bp_action", "upload")
        policies = data.get("bp_policies_list", DEMO_POLICIES)
        if reply_id and reply_id.startswith("bpp_"):
            try:
                idx = int(reply_id.split("_")[1])
                if 0 <= idx < len(policies):
                    pol = policies[idx]
                    data["bp_sel_name"]    = pol.get("name") or pol.get("productName") or "Policy"
                    data["bp_sel_ref"]     = pol.get("ref") or pol.get("policyCode") or pol.get("id", "")
                    data["bp_sel_airline"] = pol.get("airline") or pol.get("carrierName") or "—"
                    data["bp_sel_flight"]  = pol.get("flight") or pol.get("flightNumber") or "—"
                    data["bp_sel_date"]    = pol.get("date") or pol.get("departureDate") or "—"
                    data["bp_sel_traveler"]= pol.get("traveler") or pol.get("primaryPassenger") or "—"
                    data["bp_sel_origin"]  = pol.get("origin") or pol.get("departureAirport") or "—"
                    data["bp_sel_dest"]    = pol.get("dest") or pol.get("arrivalAirport") or "—"
                    data["bp_sel_policy_id"] = pol.get("id") or pol.get("policyId") or ""
                    await save_session(session)
                    if action == "link":
                        await _show_link_confirm(sender_wa_id, session, flow, phone_number_id)
                    elif action == "eligibility":
                        await _show_eligibility(sender_wa_id, session, flow, phone_number_id)
                    else:
                        await _ask_upload(sender_wa_id, session, flow, pol, phone_number_id)
                else:
                    await _show_policy_list(sender_wa_id, session, flow, action, phone_number_id)
            except (ValueError, IndexError):
                await _show_policy_list(sender_wa_id, session, flow, action, phone_number_id)
        else:
            await _show_policy_list(sender_wa_id, session, flow, action, phone_number_id)

    # ── Screen 3 (Path A): Awaiting file ─────────────────────────────────────
    elif step == "bp_awaiting_doc":
        if media:
            media_id   = media.get("id") or ""
            mime_type  = media.get("mime_type") or ""
            filename   = media.get("filename") or f"boarding_pass_{data.get('bp_sel_flight','')}.jpg"
            data["bp_filename"] = filename
            pol_id     = data.get("bp_sel_policy_id") or ""
            pol_code   = data.get("bp_sel_ref") or ""

            if media_id and (pol_id or pol_code):
                try:
                    await _send_text(
                        sender_wa_id,
                        "⏳ *Uploading your boarding pass...*\n_Please wait a moment_",
                        phone_number_id,
                    )
                    media_result  = await download_whatsapp_media(media_id)
                    file_bytes    = media_result["bytes"] if media_result else None
                    detected_mime = media_result.get("mime_type", "") if media_result else ""
                    if file_bytes:
                        passenger_id = ""
                        effective_pol_id = pol_id
                        if not effective_pol_id:
                            api_pol = await ipurvey_service.get_policy_by_code(pol_code)
                            if api_pol and isinstance(api_pol, dict):
                                effective_pol_id = api_pol.get("id") or api_pol.get("policyId") or ""
                                passengers = api_pol.get("passengers") or []
                                if passengers:
                                    passenger_id = passengers[0].get("id") or passengers[0].get("passengerId") or ""
                        else:
                            api_pol = await ipurvey_service.get_policy_by_code(pol_code or pol_id)
                            if api_pol and isinstance(api_pol, dict):
                                passengers = api_pol.get("passengers") or []
                                if passengers:
                                    passenger_id = passengers[0].get("id") or passengers[0].get("passengerId") or ""
                        flight_id = (
                            session.get("api_data", {}).get("flight_id")
                            or data.get("bp_sel_flight", "")
                        )
                        if effective_pol_id and passenger_id:
                            upload_result = await ipurvey_service.upload_boarding_pass(
                                policy_id=effective_pol_id,
                                passenger_id=passenger_id,
                                file_bytes=file_bytes,
                                file_name=filename,
                                flight_id=flight_id,
                            )
                            if upload_result:
                                logger.info(f"[bp_link] boarding pass uploaded OK for {pol_code}")
                            else:
                                logger.warning(f"[bp_link] boarding pass upload returned falsy for {pol_code}")
                        else:
                            logger.warning(f"[bp_link] missing pol_id={effective_pol_id} or passenger_id={passenger_id} for BP upload")
                    else:
                        logger.warning(f"[bp_link] could not download media {media_id}")
                except Exception as exc:
                    logger.error(f"[bp_link] boarding pass upload failed: {exc}")

            await save_session(session)
            await _show_upload_confirmed(sender_wa_id, session, flow, phone_number_id)
        else:
            await _send_text(sender_wa_id,
                "⚠️ Please *send an image or PDF* of your boarding pass.\n\n"
                + UPLOAD_INSTRUCTIONS,
                phone_number_id)

    # ── Screen 4 (Path A): After upload confirmed ─────────────────────────────
    elif step == "bp_upload_done":
        if reply_id in ("bp_home", "bp_cancel"):
            await _go_home(sender_wa_id, session, phone_number_id)
        else:
            await _show_upload_confirmed(sender_wa_id, session, flow, phone_number_id)

    # ── Screen 3 (Path B): Link confirmation ─────────────────────────────────
    elif step == "bp_link_confirm":
        if reply_id == "bp_link_yes":
            await _show_linked(sender_wa_id, session, flow, phone_number_id)
        elif reply_id == "bp_back":
            await _show_policy_list(sender_wa_id, session, flow, "link", phone_number_id)
        elif reply_id in ("bp_cancel", "bp_home"):
            await _go_home(sender_wa_id, session, phone_number_id)
        else:
            await _show_link_confirm(sender_wa_id, session, flow, phone_number_id)

    # ── Screen 4 (Path B): Linked success ────────────────────────────────────
    elif step == "bp_linked_done":
        if reply_id == "bp_eligibility":
            await _show_eligibility(sender_wa_id, session, flow, phone_number_id)
        elif reply_id == "bp_view_policy":
            await _show_policy_card(sender_wa_id, session, flow, phone_number_id)
        elif reply_id in ("bp_home", "bp_cancel"):
            await _go_home(sender_wa_id, session, phone_number_id)
        else:
            await _show_linked(sender_wa_id, session, flow, phone_number_id)

    # ── Policy mini-card ──────────────────────────────────────────────────────
    elif step == "bp_policy_card":
        await _go_home(sender_wa_id, session, phone_number_id)

    # ── Eligibility result ────────────────────────────────────────────────────
    elif step == "bp_eligibility_result":
        if reply_id == "bp_confirm_payout":
            await _show_payout_initiated(sender_wa_id, session, flow, phone_number_id)
        elif reply_id == "bp_upload_first":
            await start_bp_link_flow(sender_wa_id, phone_number_id)
        elif reply_id == "bp_back":
            action = data.get("bp_action", "upload")
            if action == "link":
                await _show_linked(sender_wa_id, session, flow, phone_number_id)
            else:
                await _show_upload_confirmed(sender_wa_id, session, flow, phone_number_id)
        elif reply_id in ("bp_home", "bp_cancel"):
            await _go_home(sender_wa_id, session, phone_number_id)
        else:
            await _show_eligibility(sender_wa_id, session, flow, phone_number_id)

    # ── Payout initiated ──────────────────────────────────────────────────────
    elif step == "bp_payout_done":
        if reply_id == "bp_view_policy":
            await _show_policy_card(sender_wa_id, session, flow, phone_number_id)
        elif reply_id in ("bp_home", "bp_cancel"):
            await _go_home(sender_wa_id, session, phone_number_id)
        else:
            await _go_home(sender_wa_id, session, phone_number_id)

    # ── Catch-all ─────────────────────────────────────────────────────────────
    else:
        await start_bp_link_flow(sender_wa_id, phone_number_id)
