import logging
from typing import Optional

import app.services.ipurvey_service as ipurvey_service

from app.core.test_overrides import get_msisdn
from app.services.session_service import (
    get_session,
    save_session,
    get_policy_cache_allow_stale,
    set_policy_cache,
)
from app.services.policy_refresh import schedule_policy_cache_refresh
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload
from app.services.ipurvey_api import fetch_policies_by_msisdn, _normalize_policy

logger = logging.getLogger(__name__)

CHECK_POLICY_FLOW_KEY = "check_policy_flow"


def _match_flight(flight: str, policies: list) -> list:
    f = flight.strip().upper()
    matched = [p for p in policies if f in p["flight"].upper()]
    return matched if matched else policies


def _match_ref(ref: str, policies: list) -> Optional[dict]:
    r = ref.strip().upper().replace(" ", "")
    for p in policies:
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
    policies, is_stale = get_policy_cache_allow_stale(session)
    if policies is None:
        msisdn_for_fetch = get_msisdn(wa_id)
        policies = await fetch_policies_by_msisdn(msisdn_for_fetch)
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
    session.setdefault("temp_data", {})[CHECK_POLICY_FLOW_KEY] = {
        "active": True, "step": "pol_menu", "data": {"policies": policies},
    }
    if "user_id" not in session:
        session["user_id"] = wa_id
    await save_session(session)

    await _send_buttons(wa_id,
        "How would you like to find your policy?",
        [
            {"id": "pol_by_phone",  "title": "📱 My phone number"},
            {"id": "pol_by_number", "title": "🔢 Policy number"},
            {"id": "pol_by_flight", "title": "✈️ Flight number"},
        ],
        phone_number_id,
        header="📋 Check my policy")


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

    policies = data.get("policies", [])

    def _get_selected_pol() -> dict:
        return data.get("pol_selected") or (policies[0] if policies else {})

    # ── Entry menu ─────────────────────────────────────────────────────────────
    if step == "pol_menu":
        if reply_id == "pol_by_phone":
            await _show_phone_policies(session, sender_wa_id, policies, phone_number_id)
        elif reply_id == "pol_by_number":
            await _set_step(session, "pol_ref_input")
            await _send_text(sender_wa_id,
                "🔢 *Search by Policy Number*\n\n"
                "Please type your policy number below.\n\n"
                "_Example: TA-238491_",
                phone_number_id)
        elif reply_id == "pol_by_flight":
            await _ask_flight_number(session, sender_wa_id, phone_number_id)
        else:
            await _reset(session)
            await start_check_policy_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)

    # ── Phone: select from list ────────────────────────────────────────────────
    elif step == "pol_phone_list":
        phone_policies = data.get("pol_phone_results", policies)
        cur_page = data.get("pol_page", 0)
        if reply_id == "pol_next_page":
            await _show_phone_policies(session, sender_wa_id, phone_policies, phone_number_id, page=cur_page + 1)
        elif reply_id == "pol_prev_page":
            await _show_phone_policies(session, sender_wa_id, phone_policies, phone_number_id, page=max(0, cur_page - 1))
        elif reply_id and reply_id.startswith("psel_"):
            try:
                idx = int(reply_id.split("_")[1])
                if 0 <= idx < len(phone_policies):
                    pol = _normalize_policy(phone_policies[idx])
                    await _save_data(session, "pol_selected", pol)
                    await _show_detail(session, sender_wa_id, pol, phone_number_id)
                else:
                    await _show_phone_policies(session, sender_wa_id, phone_policies, phone_number_id, page=cur_page)
            except (ValueError, IndexError):
                await _show_phone_policies(session, sender_wa_id, phone_policies, phone_number_id, page=cur_page)
        elif reply_id == "pol_home":
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_phone_policies(session, sender_wa_id, phone_policies, phone_number_id, page=cur_page)

    # ── Flight number input ────────────────────────────────────────────────────
    elif step == "pol_flight_input":
        flight = text.strip().upper()
        if len(flight) < 3:
            await _send_text(sender_wa_id,
                "⚠️ Please enter a valid flight number.\n\n_Example: P47123_",
                phone_number_id)
            return
        await _save_data(session, "pol_flight_search", flight)

        matched = []
        msisdn = get_msisdn(sender_wa_id)
        try:
            all_pols = await ipurvey_service.search_policies(msisdn)
            if all_pols:
                matched = [
                    _normalize_policy(p) for p in all_pols
                    if flight in (p.get("flight") or p.get("flightNumber") or "").upper()
                ] or [_normalize_policy(p) for p in all_pols]
        except Exception:
            pass
            
        if not matched:
            matched = _match_flight(flight, policies)

        if len(matched) == 1:
            await _save_data(session, "pol_selected", matched[0])
            await _show_detail(session, sender_wa_id, matched[0], phone_number_id)
        else:
            await _save_data(session, "pol_flight_results", matched)
            await _set_step(session, "pol_date_input")
            await _send_text(sender_wa_id,
                f"✈️ Flight *{flight}* matched *{len(matched)}* {'policy' if len(matched) == 1 else 'policies'}.\n\n"
                "📅 *What is your departure date?*\n\n"
                "_Enter the date to narrow down the results — e.g. 12 April 2026_",
                phone_number_id)

    # ── Date input (narrow results) ────────────────────────────────────────────
    elif step == "pol_date_input":
        date = text.strip()
        if len(date) < 4:
            await _send_text(sender_wa_id,
                "⚠️ Please enter your departure date.\n\n_Example: 12 April 2026_",
                phone_number_id)
            return
        results = data.get("pol_flight_results") or policies
        date_lower = date.lower()
        matched = [p for p in results if any(w in p["date"].lower() for w in date_lower.split())]
        pol = matched[0] if matched else (results[0] if results else {})
        if not pol:
            await _send_text(sender_wa_id,
                "⚠️ No matching policy found. Please try again or use a different search method.",
                phone_number_id)
            return
        await _save_data(session, "pol_selected", pol)
        await _show_detail(session, sender_wa_id, pol, phone_number_id)

    # ── Policy number manual input ─────────────────────────────────────────────
    elif step == "pol_ref_input":
        ref = text.strip()
        if len(ref) < 3:
            await _send_text(sender_wa_id,
                "⚠️ Please enter a valid policy number.\n\n_Example: TA-238491_",
                phone_number_id)
            return
        
        pol = None
        try:
            api_pol = await ipurvey_service.get_policy_by_code(ref)
            if api_pol and isinstance(api_pol, dict):
                pol = _normalize_policy(api_pol)
        except Exception:
            pass
            
        if not pol:
            pol = _match_ref(ref, policies)

        if pol:
            await _save_data(session, "pol_selected", pol)
            await _show_detail(session, sender_wa_id, pol, phone_number_id)
        else:
            await _send_text(sender_wa_id,
                f"⚠️ No policy found for *{ref.upper()}*.\n\n"
                "Please double-check the number and try again.\n\n"
                "_Example: TA-238491_",
                phone_number_id)

    # ── Policy detail page ─────────────────────────────────────────────────────
    elif step == "pol_detail":
        pol = _get_selected_pol()
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
            await _show_all_policies(session, sender_wa_id, policies, phone_number_id)
        elif reply_id == "pol_home":
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_detail(session, sender_wa_id, pol, phone_number_id)

    # ── All policies list ──────────────────────────────────────────────────────
    elif step == "pol_all_list":
        cur_all_page = data.get("pol_all_page", 0)
        if reply_id == "pall_next_page":
            await _show_all_policies(session, sender_wa_id, policies, phone_number_id, page=cur_all_page + 1)
        elif reply_id == "pall_prev_page":
            await _show_all_policies(session, sender_wa_id, policies, phone_number_id, page=max(0, cur_all_page - 1))
        elif reply_id and reply_id.startswith("pall_"):
            try:
                idx = int(reply_id.split("_")[1])
                if 0 <= idx < len(policies):
                    pol = policies[idx]
                    await _save_data(session, "pol_selected", pol)
                    await _show_detail(session, sender_wa_id, pol, phone_number_id)
                else:
                    await _show_all_policies(session, sender_wa_id, policies, phone_number_id, page=cur_all_page)
            except (ValueError, IndexError):
                await _show_all_policies(session, sender_wa_id, policies, phone_number_id, page=cur_all_page)
        elif reply_id == "pol_home":
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_all_policies(session, sender_wa_id, policies, phone_number_id, page=cur_all_page)

    # ── Download / policy doc subflow ──────────────────────────────────────────
    elif step == "pol_download":
        pol = _get_selected_pol()
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
        pol = _get_selected_pol()
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
        pol = _get_selected_pol()
        if reply_id == "pol_back_detail":
            await _show_detail(session, sender_wa_id, pol, phone_number_id)
        elif reply_id in ("pol_cancel", "pol_home"):
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_detail(session, sender_wa_id, pol, phone_number_id)

    # ── Turn off alerts confirmation ───────────────────────────────────────────
    elif step == "pol_alerts_off_confirm":
        pol = _get_selected_pol()
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
        pol = _get_selected_pol()
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
        pol = _get_selected_pol()
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
        pol = _get_selected_pol()
        if reply_id == "pol_eligibility":
            await _show_eligibility(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_back_detail":
            await _show_detail(session, sender_wa_id, pol, phone_number_id)
        elif reply_id == "pol_all":
            await _show_all_policies(session, sender_wa_id, policies, phone_number_id)
        elif reply_id == "pol_home":
            await _go_home(session, sender_wa_id, phone_number_id)
        else:
            await _show_linked(session, sender_wa_id, pol, phone_number_id)

    # ── Eligibility result ─────────────────────────────────────────────────────
    elif step == "pol_eligibility":
        pol = _get_selected_pol()
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
        pol = _get_selected_pol()
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


_PAGE_SIZE = 8


async def _show_phone_policies(
    session: dict,
    wa_id: str,
    policies: list,
    phone_number_id: Optional[str],
    page: int = 0,
):
    await _set_step(session, "pol_phone_list")

    msisdn = get_msisdn(wa_id)
    phone_pols = session.get("temp_data", {}).get(CHECK_POLICY_FLOW_KEY, {}).get("data", {}).get("pol_phone_results")
    if phone_pols is None:
        try:
            phone_pols = await fetch_policies_by_msisdn(msisdn)
        except Exception:
            phone_pols = []
        if not phone_pols:
            phone_pols = policies
        await _save_data(session, "pol_phone_results", phone_pols)

    await _save_data(session, "pol_page", page)

    if not phone_pols:
        await send_text_message(
            to=wa_id,
            body="⚠️ No policies found linked to your phone number.",
            phone_number_id=phone_number_id,
            source="check_policy_flow",
        )
        return

    total  = len(phone_pols)
    start  = page * _PAGE_SIZE
    end    = min(start + _PAGE_SIZE, total)
    slice_ = phone_pols[start:end]

    rows = []
    for i, p in enumerate(slice_):
        pol = _normalize_policy(p)
        abs_idx = start + i
        rows.append({
            "id":          f"psel_{abs_idx}",
            "title":       pol["name"][:24],
            "description": f"{pol['ref']} · {pol['status']}"[:72],
        })
    if page > 0:
        rows.append({"id": "pol_prev_page", "title": "⬅️ Previous page",
                     "description": f"Back to {start - _PAGE_SIZE + 1}–{start}"})
    if end < total:
        rows.append({"id": "pol_next_page", "title": "➡️ Next page",
                     "description": f"Show {end + 1}–{min(end + _PAGE_SIZE, total)} of {total}"})

    if page == 0:
        await send_text_message(
            to=wa_id,
            body=(
                f"🔍 Here are the *{total} {'policy' if total == 1 else 'policies'}* "
                f"linked to your WhatsApp number.\n\n"
                "Tap any policy below to view its details."
            ),
            phone_number_id=phone_number_id,
            source="check_policy_flow",
        )

    await _send_list(
        wa_id,
        f"Showing *{start + 1}–{end}* of *{total}*. Select a policy to view details:",
        "Select policy",
        [{"title": "Your Policies", "rows": rows}],
        phone_number_id,
        header="📱 My Policies",
    )


async def _ask_flight_number(session: dict, wa_id: str, phone_number_id: Optional[str]):
    await _set_step(session, "pol_flight_input")
    await _send_text(wa_id,
        "✈️ *Search by Flight Number*\n\n"
        "Please enter your flight number to find the matching policy.\n\n"
        "_Example: P47123_",
        phone_number_id)


async def _show_detail(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_detail")
    p = _normalize_policy(pol)

    status_emoji = "✅" if p["status"].lower() == "active" else "ℹ️"
    status_label = p["status"].capitalize() if p["status"] else "—"

    body = (
        f"📋 *Policy No:* {p['ref']}\n"
        f"🏷️ *Status:* {status_emoji} {status_label}\n\n"
        f"✈️ *Flight:* {p['airline']} {p['flight']}\n"
        f"📅 *Departure:* {p['date']}\n"
        f"📍 *Route:* {p['origin']} → {p['dest']}\n\n"
        f"🛡️ *Product:* {p['name']}\n"
        f"💰 *Cover:* {p['cover']}\n"
        f"👤 *Traveler:* {p['travelers'][0] if p['travelers'] else '—'}\n"
    )

    await _send_buttons(wa_id, body,
        [
            {"id": "pol_download", "title": "📄 Download Doc"},
            {"id": "pol_all",      "title": "📋 View all policies"},
            {"id": "pol_home",     "title": "🏠 Main Menu"},
        ],
        phone_number_id,
        header="Your Policy Details")


async def _show_all_policies(
    session: dict,
    wa_id: str,
    policies: list,
    phone_number_id: Optional[str],
    page: int = 0,
):
    await _set_step(session, "pol_all_list")
    await _save_data(session, "pol_all_page", page)

    if not policies:
        await _send_text(wa_id, "⚠️ No policies found.", phone_number_id)
        return

    total  = len(policies)
    start  = page * _PAGE_SIZE
    end    = min(start + _PAGE_SIZE, total)
    slice_ = policies[start:end]

    rows = []
    for i, p in enumerate(slice_):
        pol = _normalize_policy(p)
        abs_idx = start + i
        rows.append({
            "id":          f"pall_{abs_idx}",
            "title":       pol["name"][:24],
            "description": f"{pol['ref']} · {pol['status']}"[:72],
        })
    if page > 0:
        rows.append({"id": "pall_prev_page", "title": "⬅️ Previous page",
                     "description": f"Back to {start - _PAGE_SIZE + 1}–{start}"})
    if end < total:
        rows.append({"id": "pall_next_page", "title": "➡️ Next page",
                     "description": f"Show {end + 1}–{min(end + _PAGE_SIZE, total)} of {total}"})

    await _send_list(
        wa_id,
        f"You have *{total} {'policy' if total == 1 else 'policies'}* in total.\n"
        f"Showing *{start + 1}–{end}*. Tap a policy to view its details:",
        "Select policy",
        [{"title": "All Policies", "rows": rows}],
        phone_number_id,
        header="📋 All My Policies",
    )


async def _show_document(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_download")
    p = _normalize_policy(pol)

    if p.get("doc_url"):
        await _send_cta_document(wa_id, p, phone_number_id)
    else:
        await _send_text(wa_id,
            f"📄 *Policy Document*\n\n"
            f"Policy No: *{p['ref']}*\n\n"
            "Your policy document is being prepared. "
            "A download link will be sent to you shortly.",
            phone_number_id)

    await _send_buttons(wa_id,
        "What would you like to do next?",
        [
            {"id": "pol_upload_bp",   "title": "📤 Upload Boarding Pass"},
            {"id": "pol_back_detail", "title": "↩️ Back to Details"},
            {"id": "pol_home",        "title": "🏠 Main Menu"},
        ],
        phone_number_id)


async def _show_manage_alerts(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_alerts_manage")
    p = _normalize_policy(pol)
    
    await _send_buttons(wa_id,
        f"🔔 *Manage Alerts*\nPolicy: {p['ref']}\n\n"
        "Flight delay alerts are currently *ACTIVE* for this policy.",
        [
            {"id": "pol_alerts_keep", "title": "✅ Keep Alerts On"},
            {"id": "pol_alerts_off",  "title": "🔕 Turn Off Alerts"},
            {"id": "pol_back_detail", "title": "↩️ Back to Details"},
        ],
        phone_number_id)


async def _show_alerts_kept(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_alerts_kept")
    await _send_text(wa_id, "✅ Flight delay alerts will remain *active*. We'll notify you of any disruptions.", phone_number_id)
    await _show_detail(session, wa_id, pol, phone_number_id)


async def _show_alerts_off_confirm(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_alerts_off_confirm")
    await _send_buttons(wa_id,
        "🔕 *Turn off alerts?*\n\n"
        "You will no longer receive real-time notifications for flight delays on this policy.",
        [
            {"id": "pol_alerts_off_yes", "title": "🔕 Yes, turn off"},
            {"id": "pol_alerts_keep",    "title": "✅ No, keep on"},
        ],
        phone_number_id)


async def _show_alerts_off_done(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_alerts_off_done")
    await _send_buttons(wa_id,
        "🔕 *Alerts Turned Off*\n\nYou will not receive notifications for this flight.",
        [
            {"id": "pol_alerts_turn_back", "title": "🔔 Turn back on"},
            {"id": "pol_back_detail",      "title": "↩️ Back to Details"},
        ],
        phone_number_id)


async def _show_link_confirm(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_link_confirm")
    p = _normalize_policy(pol)
    await _send_buttons(wa_id,
        f"🔗 *Link Boarding Pass*\n\nConfirm you want to link your boarding pass to policy *{p['ref']}*?",
        [
            {"id": "pol_link_yes",    "title": "✅ Yes, link it"},
            {"id": "pol_back_detail", "title": "↩️ No, go back"},
        ],
        phone_number_id)


async def _show_linked(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_linked")
    await _send_text(wa_id, "✅ *Boarding pass linked successfully!*", phone_number_id)
    
    await _send_buttons(wa_id, "What would you like to do next?",
        [
            {"id": "pol_eligibility", "title": "💰 Check Eligibility"},
            {"id": "pol_back_detail", "title": "↩️ Back to Details"},
            {"id": "pol_home",        "title": "🏠 Main Menu"},
        ],
        phone_number_id)


async def _show_eligibility(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_eligibility")
    p = _normalize_policy(pol)
    
    await _send_text(wa_id, "🔍 *Checking eligibility...*", phone_number_id)
    
    # In a real app, call eligibility API
    # Here we show a generic result or call a service if available
    
    await _send_buttons(wa_id,
        f"ℹ️ *Eligibility Status*\nPolicy: {p['ref']}\n\n"
        "Your flight is currently on time. No payout eligibility detected yet.",
        [
            {"id": "pol_upload_first", "title": "📤 Re-upload pass"},
            {"id": "pol_back_detail",  "title": "↩️ Back to Details"},
        ],
        phone_number_id)


async def _show_payout_initiated(session: dict, wa_id: str, pol: dict, phone_number_id: Optional[str]):
    await _set_step(session, "pol_payout_done")
    await _send_text(wa_id, "💰 *Payout Initiated!*\n\nYour payout is being processed and will be sent to your account soon.", phone_number_id)
    await _show_detail(session, wa_id, pol, phone_number_id)


async def go_back_one_step(wa_id: str, phone_number_id: Optional[str]):
    """Go back exactly one step in the check policy flow instead of restarting."""
    session, flow = await _get_flow_state(wa_id)
    step = flow.get("step", "pol_menu")
    data = flow.get("data", {})

    # Steps that are sub-screens of pol_detail — go back to pol_detail
    _DETAIL_SUB = {"pol_download", "pol_eligibility", "pol_payout_done"}

    # Steps that go back to pol_menu
    _TO_MENU = {"pol_phone_list", "pol_ref_input", "pol_flight_input",
                "pol_detail", "pol_all_list"}

    if step in _DETAIL_SUB:
        pol = data.get("pol_selected") or {}
        if pol:
            await _show_detail(session, wa_id, pol, phone_number_id)
        else:
            # No selection cached — fall back to menu
            flow["step"] = "pol_menu"
            await save_session(session)
            await _send_buttons(wa_id,
                "How would you like to find your policy?",
                [
                    {"id": "pol_by_phone",  "title": "📱 My phone number"},
                    {"id": "pol_by_number", "title": "🔢 Policy number"},
                    {"id": "pol_by_flight", "title": "✈️ Flight number"},
                ],
                phone_number_id,
                header="📋 Check my policy")
        return

    if step == "pol_date_input":
        # Back from date input → ask flight number again
        await _ask_flight_number(session, wa_id, phone_number_id)
        return

    if step in _TO_MENU or step == "pol_menu":
        # At menu or any search-entry step → exit to main menu
        await _reset(session)
        from app.services.auto_reply_service import send_main_menu
        await send_main_menu(to=wa_id, phone_number_id=phone_number_id, wa_id=wa_id)
        return

    # Unknown step — reset and go to pol_menu
    flow["step"] = "pol_menu"
    await save_session(session)
    await _send_buttons(wa_id,
        "How would you like to find your policy?",
        [
            {"id": "pol_by_phone",  "title": "📱 My phone number"},
            {"id": "pol_by_number", "title": "🔢 Policy number"},
            {"id": "pol_by_flight", "title": "✈️ Flight number"},
        ],
        phone_number_id,
        header="📋 Check my policy")
