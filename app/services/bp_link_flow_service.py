import logging
from typing import Optional

import app.services.ipurvey_service as ipurvey_service
from app.services.whatsapp_service import download_whatsapp_media

from app.services.session_service import (
    get_session,
    save_session,
    get_policy_cache_allow_stale,
    set_policy_cache,
    invalidate_policy_cache,
)
from app.services.policy_refresh import schedule_policy_cache_refresh
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload
from app.services.ipurvey_api import fetch_policies_by_msisdn

logger = logging.getLogger(__name__)

BP_LINK_FLOW_KEY   = "bp_link_flow"
PAYMENT_FLOW_KEY   = "payment_flow"
BUY_COVER_FLOW_KEY = "buy_cover_flow"
KYC_FLOW_KEY       = "kyc_flow"

UPLOAD_INSTRUCTIONS = (
    "*Make sure we can see:*\n\n"
    "✅ Passenger name or names\n"
    "✅ Booking reference\n"
    "✅ Airport details\n"
    "✅ Flight number\n"
    "✅ Travel date"
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


async def _send_buttons(
    to: str,
    body: str,
    buttons: list,
    phone_number_id: Optional[str],
    header: Optional[str] = None,
):
    interactive = {
        "type": "button",
        "body": {"text": body},
        "action": {"buttons": [{"type": "reply", "reply": {"id": b["id"], "title": b["title"]}} for b in buttons]},
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
    await save_session(session)
    policies = flow.get("data", {}).get("policies", [])
    if action == "upload":
        action_label = "upload a boarding pass for:"
    elif action == "eligibility":
        action_label = "check eligibility for:"
    else:
        action_label = "link:"
    if not policies:
        await _send_text(wa_id,
            "⚠️ We couldn't find any active policies linked to your number.\n\n"
            "Please contact support if you believe this is an error.",
            phone_number_id)
        return
    body = (
        f"We found *{len(policies)} {'policy' if len(policies) == 1 else 'policies'}* linked to your number.\n\n"
        f"Please select the policy you would like to {action_label}"
    )
    rows = [
        {
            "id":          f"bpp_{i}",
            "title":       str(pol.get("name") or pol.get("productName") or "Policy")[:24],
            "description": f"{pol.get('status','Active')} · {pol.get('ref') or pol.get('policyCode') or pol.get('id','')}",
        }
        for i, pol in enumerate(policies[:10])
    ]
    await _send_list(wa_id, body, "Select policy",
        [{"title": "Your Active Policies", "rows": rows}],
        phone_number_id,
        header="📋 Your Policies")


async def _ask_upload(wa_id: str, session: dict, flow: dict, pol: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_awaiting_doc"
    await save_session(session)
    await _send_text(wa_id,
        "📎 *Please upload a clear image or PDF of your boarding pass*\n\n"
        "*Accepted formats:*\n"
        "JPEG  PDF  GIF  TIFF  PNG\n\n"
        "📦 *Maximum size: 20 MB*\n\n"
        + UPLOAD_INSTRUCTIONS,
        phone_number_id)


async def _show_bp_status(
    wa_id: str,
    session: dict,
    flow: dict,
    status: str,
    phone_number_id: Optional[str],
):
    """Show boarding pass verification status to user."""
    data = flow.get("data", {})
    ref  = data.get("bp_sel_ref", "—")

    if status == "VERIFIED":
        flow["step"] = "bp_upload_done"
        await save_session(session)
        await _send_buttons(
            wa_id,
            f"✅ *Boarding pass verified!*\n\n"
            f"Policy: *{ref}*\n\n"
            f"Your cover is now fully active. Enjoy your trip! ✈️",
            [{"id": "bp_home", "title": "🏠 Main menu"}],
            phone_number_id,
            header="✅ Verified",
        )
    elif status == "REJECTED":
        flow["step"] = "bp_awaiting_doc"
        await save_session(session)
        await _send_buttons(
            wa_id,
            f"❌ *Boarding pass rejected*\n\n"
            f"Policy: *{ref}*\n\n"
            f"Please upload a clearer image. Make sure all details are visible.\n\n"
            + UPLOAD_INSTRUCTIONS,
            [{"id": "bp_cancel", "title": "❌ Cancel"}],
            phone_number_id,
            header="❌ Rejected — Please Re-upload",
        )
    else:
        await _show_upload_confirmed(wa_id, session, flow, phone_number_id)


async def _show_upload_confirmed(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_upload_done"
    data = flow.get("data", {})
    await save_session(session)
    ref      = data.get("bp_sel_ref",      "—")
    airline  = data.get("bp_sel_airline",  "—")
    flight   = data.get("bp_sel_flight",   "—")
    date     = data.get("bp_sel_date",     "—")
    traveler = data.get("bp_sel_traveler", "—")

    await _send_buttons(wa_id,
        f"*Boarding pass upload confirmed*\n"
        f"Policy No: {ref}   ✅ Active\n\n"
        f"✈️ Airline      {airline}\n"
        f"🛫 Flight        {flight}\n"
        f"🗓️ Date           {date}\n"
        f"🧑 Traveller   {traveler}\n\n"
        f"What would you like to do next?",
        [
            {"id": "bp_eligibility", "title": "📋 Check eligibility"},
            {"id": "bp_home",        "title": "🏠 Main menu"},
        ],
        phone_number_id,
        header="✅ Boarding pass upload confirmed")


async def _show_link_confirm(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_link_confirm"
    data = flow.get("data", {})
    await save_session(session)
    ref      = data.get("bp_sel_ref",      "")
    name     = data.get("bp_sel_name",     "")
    airline  = data.get("bp_sel_airline",  "")
    flight   = data.get("bp_sel_flight",   "")
    date     = data.get("bp_sel_date",     "")
    traveler = data.get("bp_sel_traveler", "")

    await _send_buttons(wa_id,
        "We found an active policy matching your boarding pass.\n"
        "Please confirm this is the correct policy:\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋  {ref}  ·  {name}\n"
        f"✈️   {airline}  ·  {flight}\n"
        f"📅  {date}  ·  {traveler}\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        [
            {"id": "bp_link_yes", "title": "✅ Yes, link it!"},
            {"id": "bp_back",     "title": "↩️ Back"},
            {"id": "bp_cancel",   "title": "❌ Cancel"},
        ],
        phone_number_id,
        header="🔗 Linking to Policy")


async def _show_linked(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_linked_done"
    data = flow.get("data", {})
    await save_session(session)
    flight   = data.get("bp_sel_flight",   "")
    airline  = data.get("bp_sel_airline",  "")
    date     = data.get("bp_sel_date",     "")
    traveler = data.get("bp_sel_traveler", "")

    await _send_buttons(wa_id,
        f"✈️  *{flight}  ·  {airline}*\n"
        f"📅  {date}\n👤  {traveler}\n\n"
        "Flight monitoring is now *active*.\n"
        "You will be notified instantly if your flight\n"
        "is disrupted — payout is automatic, no forms needed. 💰",
        [
            {"id": "bp_eligibility", "title": "✅ Check eligibility"},
            {"id": "bp_view_policy", "title": "📋 View my policy"},
            {"id": "bp_home",        "title": "🏠 Main menu"},
        ],
        phone_number_id,
        header="✈️ Boarding Pass Linked!")


async def _show_policy_card(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_policy_card"
    data = flow.get("data", {})
    await save_session(session)
    ref      = data.get("bp_sel_ref",      "")
    name     = data.get("bp_sel_name",     "")
    airline  = data.get("bp_sel_airline",  "")
    flight   = data.get("bp_sel_flight",   "")
    date     = data.get("bp_sel_date",     "")
    traveler = data.get("bp_sel_traveler", "")

    await _send_buttons(wa_id,
        f"🛡️  *{name}*\nPolicy No: {ref}   ✅ Active\n\n"
        f"✈️  Airline:    {airline}\n"
        f"🛫  Flight:     {flight}\n"
        f"📅  Date:       {date}\n"
        f"👤  Traveller: {traveler}",
        [
            {"id": "bp_home", "title": "🏠 Main menu"},
        ],
        phone_number_id,
        header="📋 Your Policy")


async def _show_eligibility(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_eligibility_result"
    data = flow.get("data", {})
    await save_session(session)
    ref     = data.get("bp_sel_ref",      "")
    airline = data.get("bp_sel_airline",  "")
    flight  = data.get("bp_sel_flight",   "")
    pol_id  = data.get("bp_sel_policy_id", "")

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
            await _send_buttons(wa_id,
                "❌ *Not yet eligible for a payout*\n\n"
                f"✈️  Flight\t\t{flight} — {airline}\n"
                f"📋  Policy\t\t{ref}\n\n"
                "_The flight delay threshold has not been met or no disruption was recorded._",
                [
                    {"id": "bp_upload_first", "title": "📤 Upload pass"},
                    {"id": "bp_home",         "title": "🏠 Main menu"},
                ],
                phone_number_id)
            return
        delay_display = delay_str
        payout_display = payout_fmt
    else:
        delay_display  = "3hrs 20mins"
        payout_display = "₦2,500"

    await _send_buttons(wa_id,
        "✅ *You are eligible for a payout!*\n"
        "_Your flight delay meets the cover threshold_\n\n"
        f"✈️  Flight\t\t{flight} — {airline}\n"
        f"⏱️  Delay\t\t{delay_display}\n"
        f"📋  Policy\t\t{ref}\n"
        f"💰  Payout amount\t*{payout_display}*\n\n"
        "Your payout will be sent to your registered\nbank account or wallet automatically.",
        [
            {"id": "bp_confirm_payout", "title": "✅ Confirm payout"},
            {"id": "bp_upload_first",   "title": "📤 Upload pass first"},
            {"id": "bp_home",           "title": "🏠 Main menu"},
        ],
        phone_number_id)


async def _show_payout_initiated(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_payout_done"
    await save_session(session)
    await _send_buttons(wa_id,
        "💰 *Payout Initiated!*\n\n"
        "₦2,500 is on its way to your account\n"
        "⏱️ _Expected: within 24 hours_",
        [
            {"id": "bp_view_policy", "title": "📋 View my policy"},
            {"id": "bp_home",        "title": "🏠 Main menu"},
        ],
        phone_number_id,
        header="💰 Payout Initiated")


async def start_bp_link_flow(
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session = await get_session(wa_id) or {}
    policies, is_stale = get_policy_cache_allow_stale(session)
    if policies is None:
        policies = await fetch_policies_by_msisdn(wa_id)
        set_policy_cache(session, policies)
        logger.info("Fetched and cached %d policies for %s", len(policies), wa_id[:4] + "****")
    elif is_stale:
        logger.info(
            "Serving stale cached policies (%d) for %s; background refresh scheduled",
            len(policies), wa_id[:4] + "****",
        )
        schedule_policy_cache_refresh(wa_id)
    else:
        logger.info("Using cached policies (%d) for %s", len(policies), wa_id[:4] + "****")
    session.setdefault("temp_data", {})[BP_LINK_FLOW_KEY] = {
        "active": True,
        "step":   "bp_choose",
        "data":   {"policies": policies},
    }
    session["temp_data"].get(PAYMENT_FLOW_KEY, {}).update({"active": False})
    if "user_id" not in session:
        session["user_id"] = wa_id
    await save_session(session)

    await _send_buttons(wa_id,
        "Please choose an option:",
        [
            {"id": "bp_upload_me", "title": "📋 Upload pass"},
            {"id": "bp_help",      "title": "🙋 Help"},
        ],
        phone_number_id,
        header="🧳 Upload boarding pass")


async def start_eligibility_check_flow(
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session = await get_session(wa_id) or {}
    policies, is_stale = get_policy_cache_allow_stale(session)
    if policies is None:
        policies = await fetch_policies_by_msisdn(wa_id)
        set_policy_cache(session, policies)
        logger.info("Fetched and cached %d policies for %s", len(policies), wa_id[:4] + "****")
    elif is_stale:
        logger.info(
            "Serving stale cached policies (%d) for %s; background refresh scheduled",
            len(policies), wa_id[:4] + "****",
        )
        schedule_policy_cache_refresh(wa_id)
    else:
        logger.info("Using cached policies (%d) for %s", len(policies), wa_id[:4] + "****")
    session.setdefault("temp_data", {})[BP_LINK_FLOW_KEY] = {
        "active": True,
        "step":   "bp_policy",
        "data":   {"bp_action": "eligibility", "policies": policies},
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

    policies = data.get("policies", [])

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
        policies = data.get("policies", [])
        if reply_id and reply_id.startswith("bpp_"):
            try:
                idx = int(reply_id.split("_")[1])
                if 0 <= idx < len(policies):
                    pol = policies[idx]
                    data["bp_sel_name"]      = pol.get("name") or pol.get("productName") or "Policy"
                    data["bp_sel_ref"]       = pol.get("ref") or pol.get("policyCode") or pol.get("id", "")
                    data["bp_sel_airline"]   = pol.get("airline") or pol.get("carrierName") or "—"
                    data["bp_sel_flight"]    = pol.get("flight") or pol.get("flightNumber") or "—"
                    data["bp_sel_date"]      = pol.get("date") or pol.get("departureDate") or "—"
                    data["bp_sel_traveler"]  = pol.get("traveler") or pol.get("primaryPassenger") or "—"
                    data["bp_sel_origin"]    = pol.get("origin") or pol.get("departureAirport") or "—"
                    data["bp_sel_dest"]      = pol.get("dest") or pol.get("arrivalAirport") or "—"
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
                        # Use pre-stored passenger_id when jumping here from payment success
                        passenger_id = data.get("bp_sel_passenger_id") or ""
                        effective_pol_id = pol_id
                        if not effective_pol_id:
                            api_pol = await ipurvey_service.get_policy_by_code(pol_code)
                            if api_pol and isinstance(api_pol, dict):
                                effective_pol_id = api_pol.get("id") or api_pol.get("policyId") or ""
                                passengers = api_pol.get("passengers") or []
                                if passengers and not passenger_id:
                                    passenger_id = passengers[0].get("id") or passengers[0].get("passengerId") or ""
                        elif not passenger_id:
                            # Only fetch if we don't already have passenger_id
                            api_pol = await ipurvey_service.get_policy_by_code(pol_code or pol_id)
                            if api_pol and isinstance(api_pol, dict):
                                passengers = api_pol.get("passengers") or []
                                if passengers:
                                    passenger_id = passengers[0].get("id") or passengers[0].get("passengerId") or ""
                        logger.info(f"[bp_link] upload → pol_id={effective_pol_id} pax_id={passenger_id}")
                        if effective_pol_id and passenger_id:
                            upload_resp = await ipurvey_service.upload_boarding_pass(
                                policy_id=effective_pol_id,
                                passenger_id=passenger_id,
                                file_bytes=file_bytes,
                                file_name=filename,
                            )
                            if upload_resp is not None:
                                bp_status = upload_resp.get("status", "PENDING").upper()
                                logger.info(f"[bp_link] boarding pass uploaded OK for {pol_code} → status={bp_status}")
                                data["bp_passenger_id"] = passenger_id
                                data["bp_policy_id"]    = effective_pol_id
                                invalidate_policy_cache(session)
                                await save_session(session)
                                await _show_bp_status(sender_wa_id, session, flow, bp_status, phone_number_id)
                                return
                            else:
                                logger.warning(f"[bp_link] boarding pass upload failed for {pol_code}")
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
        if reply_id == "bp_eligibility":
            await _show_eligibility(sender_wa_id, session, flow, phone_number_id)
        elif reply_id in ("bp_home", "bp_cancel"):
            await _go_home(sender_wa_id, session, phone_number_id)
        else:
            await _show_upload_confirmed(sender_wa_id, session, flow, phone_number_id)

    # ── Boarding pass pending verification ────────────────────────────────────
    elif step == "bp_pending_status":
        if reply_id == "bp_check_status":
            pol_id = data.get("bp_policy_id") or data.get("bp_sel_policy_id", "")
            pax_id = data.get("bp_passenger_id") or data.get("bp_sel_passenger_id", "")
            if pol_id and pax_id:
                status_result = await ipurvey_service.poll_boarding_pass_status(pol_id, pax_id)
                if status_result and isinstance(status_result, dict):
                    bp_status = status_result.get("status", "PENDING").upper()
                    logger.info(f"[bp_link] poll status → {bp_status}")
                else:
                    bp_status = "PENDING"
                    logger.warning("[bp_link] poll_boarding_pass_status returned no data")
                await _show_bp_status(sender_wa_id, session, flow, bp_status, phone_number_id)
            else:
                await _send_text(
                    sender_wa_id,
                    "⚠️ Could not check status — policy details not found. Please contact support.",
                    phone_number_id,
                )
        elif reply_id in ("bp_home", "bp_cancel"):
            await _go_home(sender_wa_id, session, phone_number_id)
        else:
            await _show_bp_status(sender_wa_id, session, flow, "PENDING", phone_number_id)

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


async def go_back_one_step(wa_id: str, phone_number_id: Optional[str]):
    """Go back exactly one step in the boarding pass flow instead of restarting."""
    session, flow = await _get_flow_state(wa_id)
    step = flow.get("step", "bp_choose")

    _PREV = {
        "bp_policy":           "bp_choose",
        "bp_policy_card":      "bp_policy",
        "bp_link_confirm":     "bp_policy_card",
        "bp_awaiting_doc":     "bp_policy",
        "bp_pending_status":   "bp_policy",
        "bp_eligibility_result": "bp_policy",
        "bp_upload_done":      "bp_policy",
    }

    prev = _PREV.get(step)

    if not prev or step == "bp_choose":
        await _go_home(wa_id, session, phone_number_id)
        return

    flow["step"] = prev
    await save_session(session)

    if prev == "bp_choose":
        await _send_buttons(wa_id,
            "Please choose an option:",
            [
                {"id": "bp_upload_me", "title": "📋 Upload pass"},
                {"id": "bp_help",      "title": "🙋 Help"},
            ],
            phone_number_id,
            header="🧳 Upload boarding pass")

    elif prev == "bp_policy":
        await _show_policy_list(wa_id, session, flow,
                                flow.get("data", {}).get("bp_action", "upload"),
                                phone_number_id)

    elif prev == "bp_policy_card":
        await _show_policy_card(wa_id, session, flow, phone_number_id)

    else:
        await start_bp_link_flow(wa_id=wa_id, phone_number_id=phone_number_id)
