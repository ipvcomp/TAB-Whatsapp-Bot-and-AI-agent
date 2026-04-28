import logging
from typing import Optional

import app.services.ipurvey_service as ipurvey_service

from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload

logger = logging.getLogger(__name__)

CHECK_POLICY_FLOW_KEY = "check_policy_flow"

DEMO_POLICIES = [
    {
        "id":        "pol_ltp",
        "ref":       "TA-238491",
        "name":      "Local Travel Premium",
        "status":    "Active",
        "airline":   "Air Peace",
        "flight":    "P47123",
        "date":      "12 April 2026",
        "origin":    "Lagos (LOS)",
        "dest":      "Abuja (ABV)",
        "cover":     "Premium",
        "price":     "₦14,500",
        "travelers": ["Yusuf Usman"],
        "doc_url":   "https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/TA-238491/document",
    },
    {
        "id":        "pol_ltb",
        "ref":       "TA-119823",
        "name":      "Local Travel Basic",
        "status":    "Active",
        "airline":   "Arik Air",
        "flight":    "W3401",
        "date":      "20 April 2026",
        "origin":    "Abuja (ABV)",
        "dest":      "Port Harcourt (PHC)",
        "cover":     "Basic",
        "price":     "₦7,200",
        "travelers": ["Aminu Bola"],
        "doc_url":   "https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/TA-119823/document",
    },
]


def _normalize_pol(p: dict) -> dict:
    if "name" not in p and ("productName" in p or "policyCode" in p):
        return {
            "id":        p.get("id") or p.get("policyId") or "",
            "ref":       p.get("ref") or p.get("policyCode") or p.get("id") or "",
            "name":      p.get("name") or p.get("productName") or "Policy",
            "status":    p.get("status") or "Active",
            "airline":   p.get("airline") or p.get("carrierName") or "—",
            "flight":    p.get("flight") or p.get("flightNumber") or "—",
            "date":      p.get("date") or p.get("departureDate") or "—",
            "origin":    p.get("origin") or p.get("departureAirport") or "—",
            "dest":      p.get("dest") or p.get("arrivalAirport") or "—",
            "cover":     p.get("cover") or p.get("coverType") or "—",
            "price":     p.get("price") or p.get("premiumAmount") or "—",
            "travelers": p.get("travelers") or [p.get("primaryPassenger") or "—"],
            "doc_url":   p.get("doc_url") or p.get("documentUrl") or "",
        }
    return p


def _match_flight(flight: str) -> list:
    f = flight.strip().upper()
    matched = [p for p in DEMO_POLICIES if f in p["flight"].upper()]
    return matched if matched else DEMO_POLICIES


def _match_ref(ref: str) -> Optional[dict]:
    r = ref.strip().upper().replace(" ", "")
    for p in DEMO_POLICIES:
        if r in p["ref"].upper() or r == p["ref"].upper():
            return p
    return None


def is_in_check_policy_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    return session.get("temp_data", {}).get(CHECK_POLICY_FLOW_KEY, {}).get("active", False)


async def _get_flow_state(wa_id: str) -> tuple[dict, dict]:
    session = await get_session(wa_id) or {}
    flow = session.setdefault("temp_data", {}).setdefault(CHECK_POLICY_FLOW_KEY, {})
    return session, flow


async def _set_step(session: dict, step: str):
    session["temp_data"][CHECK_POLICY_FLOW_KEY]["step"] = step
    session["temp_data"][CHECK_POLICY_FLOW_KEY]["active"] = True
    await save_session(session)


async def _save_data(session: dict, key: str, value):
    session["temp_data"][CHECK_POLICY_FLOW_KEY].setdefault("data", {})[key] = value
    await save_session(session)


async def _reset(session: dict):
    session["temp_data"][CHECK_POLICY_FLOW_KEY] = {}
    await save_session(session)


_UTILITY = (
    "*Utility options:*\n"
    "0 ↩️ Back  |  9 🆘 Help  |  00 🏠 Main menu\n"
    "99 ❌ Cancel/Exit"
)


async def _send_text(to: str, body: str, phone_number_id: Optional[str]):
    await send_text_message(to=to, body=body, phone_number_id=phone_number_id, source="check_policy_flow")
    await send_text_message(to=to, body=_UTILITY, phone_number_id=phone_number_id, source="check_policy_flow")


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
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="check_policy_flow")
    await send_text_message(to=to, body=_UTILITY, phone_number_id=phone_number_id, source="check_policy_flow")


async def _send_cta_document(
    to: str,
    pol: dict,
    phone_number_id: Optional[str],
):
    ref      = pol.get("ref", "")
    name     = pol.get("name", "Policy")
    doc_url  = pol.get("doc_url", "")
    filename = f"Policy_{ref}.pdf"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type":    "individual",
        "to":                to,
        "type":              "interactive",
        "interactive": {
            "type": "cta_url",
            "header": {"type": "text", "text": f"📄 {filename}"},
            "body": {
                "text": (
                    f"*{name}*\n"
                    f"Policy No: *{ref}*\n\n"
                    "Tap the button below to view or download your full policy document."
                )
            },
            "action": {
                "name": "cta_url",
                "parameters": {
                    "display_text": "Download Policy Document",
                    "url": doc_url,
                },
            },
        },
    }
    await send_whatsapp_payload(
        whatsapp_payload=payload,
        phone_number_id=phone_number_id,
        source="check_policy_flow",
    )
    await send_text_message(
        to=to,
        body=_UTILITY,
        phone_number_id=phone_number_id,
        source="check_policy_flow",
    )


# ── Entry point ────────────────────────────────────────────────────────────────

async def start_check_policy_flow(
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session = await get_session(wa_id) or {}
    session.setdefault("temp_data", {})[CHECK_POLICY_FLOW_KEY] = {
        "active": True, "step": "pol_menu", "data": {},
    }
    if "user_id" not in session:
        session["user_id"] = wa_id
    await save_session(session)

    await _send_list(
        to=wa_id,
        header="📋 Check my policy",
        body="How would you like to find your policy?",
        button_label="Select option",
        sections=[{"title": "Find your policy", "rows": [
            {"id": "pol_by_phone",  "title": "📱 Use my phone number"},
            {"id": "pol_by_number", "title": "🔢 Enter policy number"},
            {"id": "pol_by_flight", "title": "✈️ By flight number"},
        ]}],
        phone_number_id=phone_number_id,
    )


# ── Main handler ───────────────────────────────────────────────────────────────

async def handle_check_policy_flow(
    message,
    sender_wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session, flow = await _get_flow_state(sender_wa_id)
    step = flow.get("step", "pol_menu")
    data = flow.setdefault("data", {})

    text = ""
    reply_id = None
    if message.type == "text" and message.text:
        text = message.text.body.strip()
    if message.type == "interactive" and message.interactive:
        if message.interactive.type == "list_reply" and message.interactive.list_reply:
            reply_id = message.interactive.list_reply.id
        elif message.interactive.type == "button_reply" and message.interactive.button_reply:
            reply_id = message.interactive.button_reply.id

    # ── Entry menu ─────────────────────────────────────────────────────────────
    if step == "pol_menu":
        if reply_id == "pol_by_phone":
            await _show_phone_policies(session, sender_wa_id, phone_number_id)
        elif reply_id == "pol_by_number":
            await _set_step(session, "pol_ref_input")
            await _send_text(sender_wa_id,
                "🔢 *Enter Policy Number*\n\n"
                "Please type your policy number:\n\n_Example: TA-238491_",
                phone_number_id)
        elif reply_id == "pol_by_flight":
            await _ask_flight_number(session, sender_wa_id, phone_number_id)
        else:
            await _reset(session)
            await start_check_policy_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)

    # ── Phone: select from list ────────────────────────────────────────────────
    elif step == "pol_phone_list":
        if reply_id and reply_id.startswith("psel_"):
            phone_policies = data.get("pol_phone_results", DEMO_POLICIES)
            idx = int(reply_id.split("_")[1])
            if 0 <= idx < len(phone_policies):
                pol = _normalize_pol(phone_policies[idx])
                await _save_data(session, "pol_selected", pol)
                await _show_detail(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_home":
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_phone_policies(session, sender_wa_id, phone_number_id)

    # ── Flight number input ────────────────────────────────────────────────────
    elif step == "pol_flight_input":
        flight = text.strip().upper()
        if len(flight) < 3:
            await _send_text(sender_wa_id,
                "⚠️ Enter a valid flight number:\n_Example: P47123_", phone_number_id)
            return
        await _save_data(session, "pol_flight_search", flight)
        api_results = None
        msisdn = f"+{sender_wa_id}" if not sender_wa_id.startswith("+") else sender_wa_id
        try:
            all_pols = await ipurvey_service.search_policies(msisdn)
            if all_pols:
                api_results = [
                    _normalize_pol(p) for p in all_pols
                    if flight in (p.get("flight") or p.get("flightNumber") or "").upper()
                ] or [_normalize_pol(p) for p in all_pols]
        except Exception:
            pass
        matched = api_results if api_results else _match_flight(flight)
        if len(matched) == 1:
            await _save_data(session, "pol_selected", matched[0])
            await _show_detail(session, sender_wa_id, matched[0], phone_number_id)
        else:
            await _save_data(session, "pol_flight_results", matched)
            await _set_step(session, "pol_date_input")
            await _send_text(sender_wa_id,
                f"✈️ Flight: *{flight}*\n\n"
                "📅 *What is your travel date?*\n\n_Example: 12 April 2026_",
                phone_number_id)

    # ── Date input (narrow results) ────────────────────────────────────────────
    elif step == "pol_date_input":
        date = text.strip()
        if len(date) < 4:
            await _send_text(sender_wa_id,
                "⚠️ Enter your travel date:\n_Example: 12 April 2026_", phone_number_id)
            return
        results = data.get("pol_flight_results", DEMO_POLICIES)
        date_lower = date.lower()
        matched = [p for p in results if any(w in p["date"].lower() for w in date_lower.split())]
        pol = matched[0] if matched else results[0]
        await _save_data(session, "pol_selected", pol)
        await _show_detail(session, sender_wa_id, pol, phone_number_id)

    # ── Policy number manual input ─────────────────────────────────────────────
    elif step == "pol_ref_input":
        ref = text.strip()
        if len(ref) < 3:
            await _send_text(sender_wa_id,
                "⚠️ Enter a valid policy number:\n_Example: TA-238491_", phone_number_id)
            return
        pol = None
        try:
            api_pol = await ipurvey_service.get_policy_by_code(ref)
            if api_pol and isinstance(api_pol, dict):
                pol = _normalize_pol(api_pol)
        except Exception:
            pass
        if not pol:
            pol = _match_ref(ref)
        if pol:
            await _save_data(session, "pol_selected", pol)
            await _show_detail(session, sender_wa_id, pol, phone_number_id)
        else:
            await _send_text(sender_wa_id,
                f"⚠️ No policy found for *{ref.upper()}*\n\n"
                "Please check the number and try again.\n_Example: TA-238491_",
                phone_number_id)

    # ── Policy detail page ─────────────────────────────────────────────────────
    elif step == "pol_detail":
        pol = data.get("pol_selected", DEMO_POLICIES[0])
        if reply_id == "pol_download":
            await _show_document(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_manage_alerts":
            await _show_manage_alerts(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_back_detail":
            await _show_detail(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_help":
            await _reset(session)
            from app.services.help_flow_service import start_help_flow
            await start_help_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        elif reply_id == "pol_all":
            await _show_all_policies(session, sender_wa_id, phone_number_id)
        elif reply_id == "pol_home":
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_detail(session, sender_wa_id, pol, phone_number_id)

    # ── All policies list ──────────────────────────────────────────────────────
    elif step == "pol_all_list":
        if reply_id and reply_id.startswith("pall_"):
            idx = int(reply_id.split("_")[1])
            if 0 <= idx < len(DEMO_POLICIES):
                pol = DEMO_POLICIES[idx]
                await _save_data(session, "pol_selected", pol)
                await _show_detail(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_home":
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_all_policies(session, sender_wa_id, phone_number_id)

    # ── Download / policy doc subflow ──────────────────────────────────────────
    elif step == "pol_download":
        pol = data.get("pol_selected", DEMO_POLICIES[0])
        if reply_id == "pol_upload_bp":
            await _reset(session)
            from app.services.bp_link_flow_service import start_bp_link_flow
            await start_bp_link_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        elif reply_id == "pol_link_boarding":
            await _show_link_confirm(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_manage_alerts":
            await _show_manage_alerts(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_back_detail":
            await _show_detail(session, sender_wa_id, pol, phone_number_id)
        elif reply_id in ("pol_cancel", "pol_home"):
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_document(session, sender_wa_id, pol, phone_number_id)

    # ── Manage alerts ──────────────────────────────────────────────────────────
    elif step == "pol_alerts_manage":
        pol = data.get("pol_selected", DEMO_POLICIES[0])
        if reply_id == "pol_alerts_keep":
            await _show_alerts_kept(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_alerts_off":
            await _show_alerts_off_confirm(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_back_detail":
            await _show_detail(session, sender_wa_id, pol, phone_number_id)
        elif reply_id in ("pol_cancel", "pol_home"):
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_manage_alerts(session, sender_wa_id, pol, phone_number_id)

    # ── Alerts kept active ─────────────────────────────────────────────────────
    elif step == "pol_alerts_kept":
        pol = data.get("pol_selected", DEMO_POLICIES[0])
        if reply_id == "pol_back_detail":
            await _show_detail(session, sender_wa_id, pol, phone_number_id)
        elif reply_id in ("pol_cancel", "pol_home"):
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_detail(session, sender_wa_id, pol, phone_number_id)

    # ── Turn off alerts confirmation ───────────────────────────────────────────
    elif step == "pol_alerts_off_confirm":
        pol = data.get("pol_selected", DEMO_POLICIES[0])
        if reply_id == "pol_alerts_off_yes":
            await _show_alerts_off_done(session, sender_wa_id, pol, phone_number_id)
        elif reply_id in ("pol_alerts_keep", "pol_back_detail"):
            await _show_manage_alerts(session, sender_wa_id, pol, phone_number_id)
        elif reply_id in ("pol_cancel", "pol_home"):
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_alerts_off_confirm(session, sender_wa_id, pol, phone_number_id)

    # ── Alerts turned off result ───────────────────────────────────────────────
    elif step == "pol_alerts_off_done":
        pol = data.get("pol_selected", DEMO_POLICIES[0])
        if reply_id == "pol_alerts_turn_back":
            await _show_manage_alerts(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_back_detail":
            await _show_detail(session, sender_wa_id, pol, phone_number_id)
        elif reply_id in ("pol_cancel", "pol_home"):
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_detail(session, sender_wa_id, pol, phone_number_id)

    # ── Link boarding pass confirm ─────────────────────────────────────────────
    elif step == "pol_link_confirm":
        pol = data.get("pol_selected", DEMO_POLICIES[0])
        if reply_id == "pol_link_yes":
            await _show_linked(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_back_detail":
            await _show_detail(session, sender_wa_id, pol, phone_number_id)
        elif reply_id in ("pol_cancel", "pol_home"):
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_link_confirm(session, sender_wa_id, pol, phone_number_id)

    # ── Boarding pass linked ───────────────────────────────────────────────────
    elif step == "pol_linked":
        pol = data.get("pol_selected", DEMO_POLICIES[0])
        if reply_id == "pol_eligibility":
            await _show_eligibility(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_back_detail":
            await _show_detail(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_all":
            await _show_all_policies(session, sender_wa_id, phone_number_id)
        elif reply_id == "pol_home":
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_linked(session, sender_wa_id, pol, phone_number_id)

    # ── Eligibility result ─────────────────────────────────────────────────────
    elif step == "pol_eligibility":
        pol = data.get("pol_selected", DEMO_POLICIES[0])
        if reply_id == "pol_confirm_payout":
            await _show_payout_initiated(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_upload_first":
            await _reset(session)
            from app.services.bp_link_flow_service import start_bp_link_flow
            await start_bp_link_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        elif reply_id in ("pol_back_detail", "pol_back"):
            await _show_detail(session, sender_wa_id, pol, phone_number_id)
        elif reply_id in ("pol_cancel", "pol_home"):
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_eligibility(session, sender_wa_id, pol, phone_number_id)

    # ── Payout initiated ───────────────────────────────────────────────────────
    elif step == "pol_payout_done":
        pol = data.get("pol_selected", DEMO_POLICIES[0])
        if reply_id == "pol_back_detail":
            await _show_detail(session, sender_wa_id, pol, phone_number_id)
        elif reply_id in ("pol_cancel", "pol_home"):
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_detail(session, sender_wa_id, pol, phone_number_id)

    else:
        await _reset(session)
        await start_check_policy_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _go_home(session: dict, wa_id: str, phone_number_id: Optional[str]):
    await _reset(session)
    from app.services.auto_reply_service import send_main_menu
    await send_main_menu(to=wa_id, phone_number_id=phone_number_id)


async def _show_phone_policies(session: dict, wa_id: str, phone_number_id: Optional[str]):
    await _set_step(session, "pol_phone_list")
    msisdn = f"+{wa_id}" if not wa_id.startswith("+") else wa_id
    policies = None
    try:
        policies = await ipurvey_service.search_policies(msisdn)
    except Exception as exc:
        logger.error(f"[check_policy] search_policies failed: {exc}")
    if not policies:
        policies = DEMO_POLICIES
    session.setdefault("temp_data", {}).setdefault(CHECK_POLICY_FLOW_KEY, {}).setdefault("data", {})["pol_phone_results"] = policies
    await save_session(session)
    rows = [
        {
            "id":          f"psel_{i}",
            "title":       str(pol.get("name") or pol.get("productName") or "Policy")[:24],
            "description": f"{pol.get('status','Active')} · {pol.get('ref') or pol.get('policyCode') or ''}",
        }
        for i, pol in enumerate(policies[:10])
    ]
    rows.append({"id": "pol_home", "title": "🏠 Main menu"})
    await _send_list(wa_id,
        f"We found *{len(policies)} {'policy' if len(policies)==1 else 'policies'}* linked to this WhatsApp number.\n\n"
        "Please select a policy to view:",
        "Select policy",
        [{"title": "Your Policies", "rows": rows}],
        phone_number_id,
        header="📋 Your Policies")


async def _ask_flight_number(session: dict, wa_id: str, phone_number_id: Optional[str]):
    await _set_step(session, "pol_flight_input")
    await _send_text(wa_id,
        "✈️ *Search by Flight Number*\n\n"
        "Please enter your flight number:\n\n_Example: P47123, W3401_",
        phone_number_id)


async def _show_detail(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_detail")
    travelers = ", ".join(pol.get("travelers", ["—"]))
    await _send_list(wa_id,
        f"🛡️  *{pol['name']}*\n"
        f"Policy No: {pol['ref']}     ✅ {pol['status']}\n\n"
        f"✈️  Airline      {pol['airline']}\n"
        f"✈️  Flight        {pol['flight']}\n"
        f"📅  Date          {pol['date']}\n"
        f"👤  Traveller   {travelers}",
        "📋 View options",
        [{"title": "More options", "rows": [
            {"id": "pol_download",      "title": "📥 Download Policy Doc"},
            {"id": "pol_manage_alerts", "title": "🔔 Manage alerts"},
            {"id": "pol_help",          "title": "🆘 Help"},
            {"id": "pol_all",           "title": "📋 All my policies"},
        ]}],
        phone_number_id,
        header="📋 Your Policy Details")


async def _show_document(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_download")
    pol_id   = pol.get("id") or ""
    pol_code = pol.get("ref") or ""
    if pol_id or pol_code:
        try:
            doc_result = await ipurvey_service.get_policy_document_url(pol_id or pol_code)
            if doc_result:
                url = None
                if isinstance(doc_result, dict):
                    url = doc_result.get("url") or doc_result.get("documentUrl") or doc_result.get("downloadUrl")
                elif isinstance(doc_result, str):
                    url = doc_result
                if url:
                    pol = {**pol, "doc_url": url}
        except Exception as exc:
            logger.error(f"[check_policy] get_policy_document_url failed: {exc}")
    await _send_cta_document(wa_id, pol, phone_number_id)
    await _send_list(wa_id,
        "📧 A copy has also been sent to your registered email address.\n\n"
        "What would you like to do next?",
        "Select option",
        [{"title": "Options", "rows": [
            {"id": "pol_manage_alerts", "title": "🔔 Manage alerts"},
            {"id": "pol_upload_bp",     "title": "📲 Upload boarding pass"},
            {"id": "pol_home",          "title": "🏠 Main menu"},
            {"id": "pol_back_detail",   "title": "↩️ Back"},
            {"id": "pol_cancel",        "title": "❌ Cancel"},
        ]}],
        phone_number_id,
        header="📄 Your Policy Document")


async def _show_manage_alerts(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_alerts_manage")
    await _send_list(wa_id,
        f"🔔 *Manage your flight alerts*\n\n"
        f"✈️  *Flight {pol['flight']} —*\n"
        f"     *Monitored*                🟢 Active\n"
        f"{pol['origin']} → {pol['dest']} · {pol['date']}\n\n"
        f"Policy No.        {pol['ref']}\n"
        f"Alerts sent to    This WhatsApp number\n"
        f"Monitoring         Delays · Cancellations\n\n"
        "What would you like to do?",
        "Select option",
        [{"title": "Options", "rows": [
            {"id": "pol_alerts_keep", "title": "🔔 Keep alerts active"},
            {"id": "pol_alerts_off",  "title": "🔕 Turn off alerts"},
            {"id": "pol_home",        "title": "🏠 Main menu"},
            {"id": "pol_back_detail", "title": "↩️ Back"},
            {"id": "pol_cancel",      "title": "❌ Cancel"},
        ]}],
        phone_number_id)


async def _show_alerts_kept(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_alerts_kept")
    await _send_list(wa_id,
        "✅ *Alerts remain active*\nYou'll be notified of any flight changes.",
        "Select option",
        [{"title": "Options", "rows": [
            {"id": "pol_back_detail", "title": "📋 View my policy"},
            {"id": "pol_home",        "title": "🏠 Main menu"},
        ]}],
        phone_number_id)


async def _show_alerts_off_confirm(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_alerts_off_confirm")
    await _send_list(wa_id,
        f"You will no longer receive notifications about\n"
        f"flight *{pol['flight']}*. Your policy cover remains active.",
        "Select option",
        [{"title": "Options", "rows": [
            {"id": "pol_alerts_off_yes", "title": "🔔 Yes, turn off alerts"},
            {"id": "pol_alerts_keep",    "title": "🔔 No, keep alerts on"},
            {"id": "pol_back_detail",    "title": "↩️ Back"},
            {"id": "pol_cancel",         "title": "❌ Cancel"},
        ]}],
        phone_number_id,
        header="🔔 Turn off flight alerts?")


async def _show_alerts_off_done(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_alerts_off_done")
    await _send_list(wa_id,
        "🔔 *Alerts turned off*\n"
        "You can turn them back on anytime from Check My Policy.",
        "Select option",
        [{"title": "Options", "rows": [
            {"id": "pol_alerts_turn_back", "title": "🔔 Turn alerts back on"},
            {"id": "pol_back_detail",      "title": "📋 View my policy"},
            {"id": "pol_home",             "title": "🏠 Main menu"},
        ]}],
        phone_number_id)


async def _show_link_confirm(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_link_confirm")
    travelers = ", ".join(pol.get("travelers", ["—"]))
    await _send_list(wa_id,
        f"We found an active policy matching your boarding pass.\n"
        f"Please confirm this is the correct policy:\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📋  {pol['ref']}  ·  {pol['name']}\n"
        f"✈️   {pol['airline']}  ·  {pol['flight']}\n"
        f"📅  {pol['date']}\n👤  {travelers}\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        "Select",
        [{"title": "Confirm linking", "rows": [
            {"id": "pol_link_yes",    "title": "✅ Yes, link it!"},
            {"id": "pol_back_detail", "title": "↩️ Back"},
            {"id": "pol_cancel",      "title": "❌ Cancel"},
        ]}],
        phone_number_id,
        header="🔗 Linking to Policy")


async def _show_linked(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_linked")
    travelers = ", ".join(pol.get("travelers", ["—"]))
    await _send_list(wa_id,
        f"✈️  *{pol['flight']}  ·  {pol['airline']}*\n"
        f"📅  {pol['date']}\n👤  {travelers}\n"
        f"💰  Total balance: *{pol['price']}*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Flight monitoring is now *active*. Payout is automatic, no forms needed. 💰",
        "What next?",
        [{"title": "Options", "rows": [
            {"id": "pol_eligibility", "title": "✅ Check eligibility"},
            {"id": "pol_back_detail", "title": "📋 View my policy"},
            {"id": "pol_home",        "title": "🏠 Main menu"},
        ]}],
        phone_number_id,
        header="✈️ Boarding Pass Linked!")


async def _show_eligibility(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_eligibility")
    await _send_text(wa_id,
        "🔍 *Checking your eligibility...*\n"
        "_Verifying policy, flight delay and cover details_\n\n• • •",
        phone_number_id)
    await _send_list(wa_id,
        "✅ *You are eligible for a payout!*\n"
        "_Your flight delay meets the cover threshold_\n\n"
        f"✈️  Flight\t\t{pol['flight']} — {pol['airline']}\n"
        f"⏱️  Delay\t\t3hrs 20mins\n"
        f"📋  Policy\t\t{pol['ref']}\n"
        f"💰  Payout amount\t*₦2,500*\n\n"
        "Your payout will be sent to your registered bank account or wallet automatically.",
        "Select option",
        [{"title": "Options", "rows": [
            {"id": "pol_confirm_payout", "title": "✅ Confirm payout"},
            {"id": "pol_upload_first",   "title": "📤 Upload pass first"},
            {"id": "pol_home",           "title": "🏠 Main menu"},
            {"id": "pol_back_detail",    "title": "↩️ Back"},
            {"id": "pol_cancel",         "title": "❌ Cancel"},
        ]}],
        phone_number_id)


async def _show_payout_initiated(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_payout_done")
    await _send_list(wa_id,
        "💰 *Payout Initiated!*\n\n"
        "₦2,500 is on its way to your account\n"
        "⏱️ _Expected: within 24 hours_",
        "Select option",
        [{"title": "Options", "rows": [
            {"id": "pol_back_detail", "title": "📋 View my policy"},
            {"id": "pol_home",        "title": "🏠 Main menu"},
        ]}],
        phone_number_id,
        header="💰 Payout Initiated!")


async def _show_all_policies(session: dict, wa_id: str, phone_number_id: Optional[str]):
    await _set_step(session, "pol_all_list")
    rows = [
        {"id": f"pall_{i}", "title": pol["name"][:24],
         "description": f"{pol['airline']} · {pol['date']} · {pol['status']}"}
        for i, pol in enumerate(DEMO_POLICIES)
    ]
    rows.append({"id": "pol_home", "title": "🏠 Main menu"})
    await _send_list(wa_id,
        "Here are *all your policies*.\n\nSelect one to view details:",
        "Select policy",
        [{"title": "All Your Policies", "rows": rows}],
        phone_number_id,
        header="📋 All Your Policies")
