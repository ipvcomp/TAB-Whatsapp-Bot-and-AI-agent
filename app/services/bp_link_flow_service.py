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

# WhatsApp list max = 10 rows total.
# Page 0 has no "Prev" row → 9 policy slots + 1 "Next" = 10.
# Pages 1+ have "Prev" → 8 policy slots + "Prev" + "Next" = 10.
_FIRST_PAGE_SIZE = 9
_REST_PAGE_SIZE  = 8


def _page_window(page: int, total: int) -> tuple:
    """Return (start, end, has_prev, has_next)."""
    if page == 0:
        start, end = 0, min(_FIRST_PAGE_SIZE, total)
    else:
        start = _FIRST_PAGE_SIZE + (page - 1) * _REST_PAGE_SIZE
        end   = min(start + _REST_PAGE_SIZE, total)
    return start, end, page > 0, end < total


def _total_pages(total: int) -> int:
    if total <= _FIRST_PAGE_SIZE:
        return 1
    return 1 + -(-( total - _FIRST_PAGE_SIZE) // _REST_PAGE_SIZE)


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
    await send_text_message(to=to, body=f"{body}\n\n\n{_UTILITY}", phone_number_id=phone_number_id, source="bp_link_flow")


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
        "body": {"text": f"{body}\n\n\n{_UTILITY}"},
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


async def _send_buttons(
    to: str,
    body: str,
    buttons: list,
    phone_number_id: Optional[str],
    header: Optional[str] = None,
):
    interactive = {
        "type": "button",
        "body": {"text": f"{body}\n\n\n{_UTILITY}"},
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


async def show_cancel_bp_confirm(wa_id: str, phone_number_id: Optional[str], session: Optional[dict] = None):
    """Show cancel boarding pass confirmation screen.
    Shows 'Cancel Eligibility Check' if user is at the eligibility step."""
    step = ""
    if session:
        step = session.get("temp_data", {}).get(BP_LINK_FLOW_KEY, {}).get("step", "")
    if "eligib" in step:
        await _send_buttons(
            wa_id,
            "❌ *Cancel Eligibility Check*\n\nAre you sure you want to cancel?\nIs there anything else we can help you with?",
            [
                {"id": "cx_yes_elig", "title": "❌ Yes, cancel"},
                {"id": "cx_no_elig",  "title": "↩️ No, go back"},
            ],
            phone_number_id,
        )
    else:
        await _send_buttons(
            wa_id,
            "❌ *Cancel Proof Upload*\n\nAre you sure you want to cancel?\nYour proof will not be uploaded or verified.",
            [
                {"id": "cx_yes_bp", "title": "❌ Yes, cancel"},
                {"id": "cx_no_bp",  "title": "↩️ No, continue"},
            ],
            phone_number_id,
        )


async def _go_home(wa_id: str, session: dict, phone_number_id: Optional[str]):
    session["temp_data"][BP_LINK_FLOW_KEY]   = {}
    session["temp_data"][PAYMENT_FLOW_KEY]   = {}
    session["temp_data"][BUY_COVER_FLOW_KEY] = {}
    session["temp_data"][KYC_FLOW_KEY]       = {}
    await save_session(session)
    from app.services.auto_reply_service import send_main_menu
    await send_main_menu(to=wa_id, phone_number_id=phone_number_id)


async def _show_policy_list(
    wa_id: str,
    session: dict,
    flow: dict,
    action: str,
    phone_number_id: Optional[str],
    page: int = 0,
):
    data = flow.setdefault("data", {})
    flow["step"] = "bp_policy"
    data["bp_page"] = page
    await save_session(session)

    policies = data.get("policies", [])
    total = len(policies)

    _action_labels = {
        "upload": "upload a boarding pass for:",
        "eligibility": "check eligibility for:",
        "link": "link:",
    }
    action_label = _action_labels.get(action, "link:")

    if not policies:
        await _send_text(
            wa_id,
            "⚠️ We couldn't find any active policies linked to your number.\n\n"
            "Please contact support if you believe this is an error.",
            phone_number_id,
        )
        return

    start, end, has_prev, has_next = _page_window(page, total)
    n_pages = _total_pages(total)

    rows: list = []
    if has_prev:
        rows.append({
            "id":          "bpp_prev",
            "title":       "⬅️ Previous page",
            "description": f"Back · policies {start - _REST_PAGE_SIZE + 1}–{start} of {total}",
        })

    for i, pol in enumerate(policies[start:end]):
        rows.append({
            "id":          f"bpp_{start + i}",
            "title":       str(pol.get("name") or pol.get("productName") or "Policy")[:24],
            "description": (
                f"{pol.get('status', 'Active')} · "
                f"{pol.get('ref') or pol.get('policyCode') or pol.get('id', '')}"
            ),
        })

    if has_next:
        rows.append({
            "id":          "bpp_next",
            "title":       "➡️ Next page",
            "description": f"Showing {start + 1}–{end} of {total}",
        })

    body = (
        f"We found *{total} {'policy' if total == 1 else 'policies'}* linked to your number.\n\n"
        f"Please select the policy you would like to {action_label}"
    )
    if n_pages > 1:
        body += f"\n\n_Page {page + 1} of {n_pages}_"

    await _send_list(
        wa_id,
        body,
        "Select policy",
        [{"title": "📋 Your Policies", "rows": rows}],
        phone_number_id,
        header="📋 Your Policies",
    )


async def _ask_upload(wa_id: str, session: dict, flow: dict, pol: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_awaiting_doc"
    await save_session(session)
    await _send_text(wa_id,
        "🖇️ *Please upload a clear image or PDF of your boarding pass*\n\n"
        "*Accepted formats:*\n"
        "`JPEG`  `PDF`  `GIF`  `TIFF`  `PNG`\n\n"
        "📦 *Maximum size: 20 MB*\n\n"
        + UPLOAD_INSTRUCTIONS,
        phone_number_id)


async def _ask_upload_for_passenger(
    wa_id: str,
    session: dict,
    flow: dict,
    pax_idx: int,
    phone_number_id: Optional[str],
):
    """Ask user to upload boarding pass for a specific passenger by index."""
    flow["step"] = "bp_awaiting_doc"
    data = flow.setdefault("data", {})
    data["bp_current_pax_idx"] = pax_idx
    await save_session(session)

    passengers = data.get("bp_passengers", [])
    if pax_idx < len(passengers):
        pax  = passengers[pax_idx]
        name = pax.get("name", "Passenger")
        if pax.get("is_primary", pax_idx == 0):
            passenger_line = "*MAIN PASSENGER:*\n👤 " + name
        else:
            passenger_line = f"*ADDITIONAL PASSENGER {pax_idx + 1}:*\n👤 " + name
        heading = f"the boarding pass for the\n{passenger_line}"
    else:
        heading = "your boarding pass"

    await _send_text(
        wa_id,
        f"🖇️ *Please upload a clear image or PDF of {heading}*\n\n"
        "*Accepted formats:*\n"
        "`JPEG`  `PDF`  `GIF`  `TIFF`  `PNG`\n\n"
        "📦 *Maximum size: 20 MB*\n\n"
        + UPLOAD_INSTRUCTIONS,
        phone_number_id,
    )


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
            f"Please upload a clearer image.\n\n"
            "🖇️ *Please upload a clear image or PDF of your boarding pass*\n\n"
            "*Accepted formats:*\n"
            "`JPEG`  `PDF`  `GIF`  `TIFF`  `PNG`\n\n"
            "📦 *Maximum size: 20 MB*\n\n"
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

    from_payment = data.get("bp_from_payment", False)
    buttons = (
        [{"id": "bp_home", "title": "🏠 Main menu"}]
        if from_payment
        else [
            {"id": "bp_check_eligibility", "title": "🔍 Check Eligibility"},
            {"id": "bp_home",              "title": "🏠 Main menu"},
        ]
    )

    await _send_buttons(wa_id,
        f"*Boarding pass upload confirmed*\n"
        f"Policy No: {ref}   ✅ Active\n\n"
        f"✈️ Airline      {airline}\n"
        f"🛫 Flight        {flight}\n"
        f"📅 Date           {date}\n"
        f"🧑 Traveller   {traveler}\n\n"
        f"What would you like to do next?",
        buttons,
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
        f"😊  Traveller: {traveler}",
        [
            {"id": "bp_home", "title": "🏠 Main menu"},
        ],
        phone_number_id,
        header="📋 Your Policy")


async def _show_eligibility(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_eligibility_result"
    data = flow.get("data", {})
    await save_session(session)
    ref     = data.get("bp_sel_ref",       "")
    airline = data.get("bp_sel_airline",   "")
    flight  = data.get("bp_sel_flight",    "")
    pol_id  = data.get("bp_sel_policy_id", "")

    eligibility = None
    if pol_id:
        try:
            eligibility = await ipurvey_service.check_eligibility(pol_id)
            logger.info(
                "[bp_link][eligibility] pol_id=%s ref=%s raw_response=%s",
                pol_id, ref, eligibility,
            )
        except Exception as exc:
            logger.error("[bp_link][eligibility] check_eligibility failed pol_id=%s: %s", pol_id, exc)
    else:
        logger.warning("[bp_link][eligibility] no pol_id available — skipping API call ref=%s", ref)

    if eligibility and isinstance(eligibility, dict):
        eval_status   = eligibility.get("evaluationStatus", "")
        eligible      = (eval_status == "ELIGIBLE")
        trigger_type  = eligibility.get("triggerType", "")
        payout_amt    = eligibility.get("payoutAmount")
        currency      = eligibility.get("payoutCurrency", "NGN")
        delay_mins    = eligibility.get("delayMinutes")
        justification = eligibility.get("justification", "")
        pol_code      = eligibility.get("policyCode", "") or ref

        currency_sym = "₦" if currency == "NGN" else f"{currency} "
        try:
            payout_fmt = (
                f"{currency_sym}{float(payout_amt):,.0f}"
                if payout_amt is not None else "—"
            )
        except (ValueError, TypeError):
            payout_fmt = f"{currency_sym}{payout_amt}"

        trigger_label = trigger_type.replace("_", " ").title() if trigger_type else "—"
        delay_str = (
            f"{delay_mins} min{'s' if delay_mins != 1 else ''}"
            if delay_mins is not None else ""
        )

        # Persist for payout-confirmation screen
        data["eligibility_result"] = {
            "payout_fmt":    payout_fmt,
            "pol_code":      pol_code,
            "trigger":       trigger_label,
            "justification": justification,
        }
        await save_session(session)

        bp_already_uploaded = data.get("bp_uploaded", False)

        if not eligible:
            # Pre-flight: no triggerType means the flight has not yet occurred
            pre_flight = not trigger_type
            if pre_flight:
                body_lines = [
                    "Your flight is currently within its normal schedule. No delays or "
                    "cancellations have been reported that meet the policy threshold for "
                    "a payout just yet.\n",
                    "Don't worry, we will continue monitoring your flight and will "
                    "automatically notify you if any disruptions occur!\n",
                    f"✈️  Flight         {flight}" + (f" — {airline}" if airline else ""),
                    f"📋  Policy         {pol_code}",
                    "\nWe've got you covered—no further action is needed right now.",
                ]
            else:
                body_lines = [
                    "Your policy has been evaluated but does not qualify for payout yet.\n",
                    f"✈️  Flight         {flight}" + (f" — {airline}" if airline else ""),
                    f"🔔  Trigger        {trigger_label}",
                ]
                if delay_str:
                    body_lines.append(f"⏱️  Delay          {delay_str}")
                body_lines.append(f"📋  Policy         {pol_code}")
                if justification:
                    body_lines.append(f"\n_{justification}_")

            # Build up to 3 buttons in one card — no separate "More options" message
            not_elig_buttons = [{"id": "bp_keep_alerts", "title": "🔔 Keep alerts on"}]
            if not bp_already_uploaded:
                not_elig_buttons.append({"id": "bp_upload_first", "title": "📤 Upload pass"})
                not_elig_buttons.append({"id": "bp_home",         "title": "🏠 Main menu"})
            else:
                not_elig_buttons.append({"id": "bp_get_help", "title": "🧑 Get help"})
                not_elig_buttons.append({"id": "bp_home",     "title": "🏠 Main menu"})

            await _send_buttons(
                wa_id,
                "\n".join(body_lines),
                not_elig_buttons,
                phone_number_id,
                header="✈️ Flight On Schedule" if pre_flight else "⏳ Not yet eligible",
            )
            return

        # ELIGIBLE
        body_lines = [
            "✅ *You are eligible for a payout!*",
            f"_Your policy qualifies for a "
            f"{trigger_label.lower() if trigger_label != '—' else 'payout'}_\n",
            f"✈️  Flight         {flight}" + (f" — {airline}" if airline else ""),
            f"🔔  Trigger        {trigger_label}",
        ]
        if delay_str:
            body_lines.append(f"⏱️  Delay          {delay_str}")
        body_lines.extend([
            f"📋  Policy         {pol_code}",
            f"💰  Payout amount  *{payout_fmt}*",
        ])
        if justification:
            body_lines.append(f"\n_{justification}_")
        eligible_flag = True

    else:
        # API unavailable — show generic fallback
        body_lines = [
            "ℹ️ *Eligibility Check*\n",
            f"✈️  Flight   {flight}" + (f" — {airline}" if airline else ""),
            f"📋  Policy   {ref}\n",
            "We're unable to check eligibility right now. Please try again shortly.",
        ]
        eligible_flag = False
        bp_already_uploaded = data.get("bp_uploaded", False)

    if eligible_flag:
        # D-123: Remove Confirm Payout — show pending-requirement button instead
        kyc_verified = True
        if pol_id:
            try:
                kyc_resp = await ipurvey_service.check_kyc_status(pol_id)
                if isinstance(kyc_resp, dict):
                    ks = (kyc_resp.get("status") or kyc_resp.get("kycStatus") or "").upper()
                    kyc_verified = ks in ("VERIFIED", "APPROVED", "CONFIRMED", "SUCCESS", "PASSED")
                elif kyc_resp is None:
                    kyc_verified = False
            except Exception:
                pass
        result_buttons = []
        if not kyc_verified:
            result_buttons.append({"id": "bp_kyc_verify", "title": "🪪 KYC Verification"})
        if not bp_already_uploaded:
            result_buttons.append({"id": "bp_upload_first", "title": "📤 Upload Boarding Pass"})
        # Always include Main menu (max 3 buttons total)
        if len(result_buttons) < 3:
            result_buttons.append({"id": "bp_home", "title": "🏠 Main menu"})
    else:
        result_buttons = []
        if not bp_already_uploaded:
            result_buttons.append({"id": "bp_upload_first", "title": "📤 Upload pass"})
        result_buttons.append({"id": "bp_home", "title": "🏠 Main menu"})

    await _send_buttons(
        wa_id,
        "\n".join(body_lines),
        result_buttons,
        phone_number_id,
        header="✅ Eligible for payout" if eligible_flag else "ℹ️ Eligibility Result",
    )


async def _show_payout_initiated(wa_id: str, session: dict, flow: dict, phone_number_id: Optional[str]):
    flow["step"] = "bp_payout_done"
    data    = flow.get("data", {})
    elig    = data.get("eligibility_result", {})
    payout_fmt  = elig.get("payout_fmt", "") or "—"
    pol_code    = elig.get("pol_code", "") or data.get("bp_sel_ref", "")
    trigger     = elig.get("trigger", "")
    await save_session(session)

    payout_line = f"{payout_fmt} is on its way to your account" if payout_fmt != "—" else "Your payout is being processed"
    body = (
        f"💰 *Payout Initiated!*\n\n"
        f"{payout_line}\n"
        f"⏱️ _Expected: within 24 hours_"
    )
    if pol_code:
        body += f"\n\n📋 Policy: *{pol_code}*"
    if trigger and trigger != "—":
        body += f"\n🔔 Trigger: *{trigger}*"

    await _send_buttons(wa_id,
        body,
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
    direct_policy: Optional[dict] = None,
):
    session = await get_session(wa_id) or {}

    # ── Fast path: called from payment success with a known policy ────────────
    # Skip the intro screen and policy list — go straight to upload prompt.
    if direct_policy:
        logger.info(
            "[bp_link] direct_policy path — skipping intro+list for %s ref=%s",
            wa_id[:4] + "****", direct_policy.get("ref", "?"),
        )
        passengers = direct_policy.get("passengers") or []
        if not passengers:
            # Fallback: single-passenger from traveler name field
            passengers = [{
                "name":         direct_policy.get("traveler", "—"),
                "passenger_id": direct_policy.get("passenger_id", ""),
                "is_primary":   True,
            }]

        flow_data = {
            "bp_action":          "upload",
            "bp_sel_name":        direct_policy.get("name",      "—"),
            "bp_sel_ref":         direct_policy.get("ref",       "—"),
            "bp_sel_airline":     direct_policy.get("airline",   "—"),
            "bp_sel_flight":      direct_policy.get("flight",    "—"),
            "bp_sel_date":        direct_policy.get("date",      "—"),
            "bp_sel_traveler":    direct_policy.get("traveler",  "—"),
            "bp_sel_policy_id":   direct_policy.get("policy_id", ""),
            "bp_passengers":      passengers,
            "bp_current_pax_idx": 0,
            "bp_uploads_done":    [],
            "bp_from_payment":    True,
            "policies":           [],
        }
        session.setdefault("temp_data", {})[BP_LINK_FLOW_KEY] = {
            "active": True,
            "step":   "bp_awaiting_doc",
            "data":   flow_data,
        }
        session["temp_data"].get(PAYMENT_FLOW_KEY, {}).update({"active": False})
        if "user_id" not in session:
            session["user_id"] = wa_id
        await save_session(session)
        flow = session["temp_data"][BP_LINK_FLOW_KEY]
        await _ask_upload_for_passenger(wa_id, session, flow, 0, phone_number_id)
        return

    # ── Generic path: from welcome "Submit Boarding Pass" button ─────────────
    # Always fetch fresh when stale to avoid showing empty list.
    policies, is_stale = get_policy_cache_allow_stale(session)
    if policies is None or is_stale:
        policies = await fetch_policies_by_msisdn(wa_id)
        set_policy_cache(session, policies)
        logger.info(
            "[bp_link] Fetched fresh %d policies for %s",
            len(policies), wa_id[:4] + "****",
        )
    else:
        logger.info(
            "[bp_link] Using cached %d policies for %s",
            len(policies), wa_id[:4] + "****",
        )
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
        current_page = data.get("bp_page", 0)

        # ── Pagination navigation ─────────────────────────────────────────────
        if reply_id == "bpp_next":
            await _show_policy_list(sender_wa_id, session, flow, action, phone_number_id, page=current_page + 1)
        elif reply_id == "bpp_prev":
            await _show_policy_list(sender_wa_id, session, flow, action, phone_number_id, page=max(0, current_page - 1))

        # ── Policy selection ─────────────────────────────────────────────────
        elif reply_id and reply_id.startswith("bpp_"):
            try:
                idx = int(reply_id.split("_", 1)[1])   # global policy index
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
                    pol_status = (pol.get("status") or "").upper()
                    if action == "link":
                        await _show_link_confirm(sender_wa_id, session, flow, phone_number_id)
                    elif action == "eligibility":
                        await _show_eligibility(sender_wa_id, session, flow, phone_number_id)
                    elif pol_status in ("EXPIRED", "CANCELLED", "LAPSED"):
                        await _send_buttons(
                            sender_wa_id,
                            f"⚠️ *This policy has expired*\n\n"
                            f"Policy: *{data.get('bp_sel_ref', '—')}*\n\n"
                            f"Boarding pass upload is only available for active policies. "
                            f"Please select an active policy or return to the main menu.",
                            [
                                {"id": "bp_back_to_list", "title": "↩️ Choose another"},
                                {"id": "bp_home",         "title": "🏠 Main menu"},
                            ],
                            phone_number_id,
                            header="⚠️ Policy Expired",
                        )
                    else:
                        await _ask_upload(sender_wa_id, session, flow, pol, phone_number_id)
                else:
                    await _show_policy_list(sender_wa_id, session, flow, action, phone_number_id, page=current_page)
            except (ValueError, IndexError):
                await _show_policy_list(sender_wa_id, session, flow, action, phone_number_id, page=current_page)

        elif reply_id == "bp_home":
            await _go_home(sender_wa_id, session, phone_number_id)
        elif reply_id == "bp_cancel":
            await show_cancel_bp_confirm(sender_wa_id, phone_number_id, session)
        elif reply_id == "bp_back_to_list":
            await _show_policy_list(sender_wa_id, session, flow, action, phone_number_id, page=0)
        else:
            await _show_policy_list(sender_wa_id, session, flow, action, phone_number_id, page=current_page)

    # ── Screen 3 (Path A): Awaiting file ─────────────────────────────────────
    elif step == "bp_awaiting_doc":
        if media:
            media_id   = media.get("id") or ""
            filename   = media.get("filename") or f"boarding_pass_{data.get('bp_sel_flight','')}.jpg"
            data["bp_filename"] = filename
            pol_id     = data.get("bp_sel_policy_id") or ""
            pol_code   = data.get("bp_sel_ref") or ""

            # ── Resolve current passenger ────────────────────────────────────
            pax_list    = data.get("bp_passengers", [])
            pax_idx     = data.get("bp_current_pax_idx", 0)
            total_pax   = len(pax_list)

            # Get passenger_id for current passenger
            passenger_id = ""
            if pax_list and pax_idx < len(pax_list):
                passenger_id = pax_list[pax_idx].get("passenger_id", "")

            if media_id and (pol_id or pol_code):
                try:
                    media_result = await download_whatsapp_media(media_id)
                    file_bytes   = media_result["bytes"] if media_result else None
                    if file_bytes:
                        effective_pol_id = pol_id
                        # Resolve policy_id from code if missing
                        if not effective_pol_id:
                            api_pol = await ipurvey_service.get_policy_by_code(pol_code)
                            if api_pol and isinstance(api_pol, dict):
                                effective_pol_id = api_pol.get("id") or api_pol.get("policyId") or ""
                                if not passenger_id:
                                    api_pax = api_pol.get("passengers") or []
                                    if pax_idx < len(api_pax):
                                        passenger_id = api_pax[pax_idx].get("id") or api_pax[pax_idx].get("passengerId") or ""
                                    elif api_pax:
                                        passenger_id = api_pax[0].get("id") or api_pax[0].get("passengerId") or ""
                        elif not passenger_id:
                            api_pol = await ipurvey_service.get_policy_by_code(pol_code or pol_id)
                            if api_pol and isinstance(api_pol, dict):
                                api_pax = api_pol.get("passengers") or []
                                if pax_idx < len(api_pax):
                                    passenger_id = api_pax[pax_idx].get("id") or api_pax[pax_idx].get("passengerId") or ""
                                elif api_pax:
                                    passenger_id = api_pax[0].get("id") or api_pax[0].get("passengerId") or ""

                        logger.info(f"[bp_link] upload pax[{pax_idx}/{total_pax}] → pol_id={effective_pol_id} pax_id={passenger_id}")

                        if effective_pol_id and passenger_id:
                            upload_resp = await ipurvey_service.upload_boarding_pass(
                                policy_id=effective_pol_id,
                                passenger_id=passenger_id,
                                file_bytes=file_bytes,
                                file_name=filename,
                            )
                            if upload_resp is not None:
                                bp_status = upload_resp.get("status", "PENDING").upper()
                                logger.info(f"[bp_link] pax[{pax_idx}] upload OK for {pol_code} → status={bp_status}")

                                # Track uploads done
                                uploads_done = data.get("bp_uploads_done", [])
                                uploads_done.append({
                                    "pax_idx":      pax_idx,
                                    "passenger_id": passenger_id,
                                    "status":       bp_status,
                                })
                                data["bp_uploads_done"]  = uploads_done
                                data["bp_passenger_id"]  = passenger_id
                                data["bp_policy_id"]     = effective_pol_id
                                data["bp_uploaded"]      = True
                                invalidate_policy_cache(session)
                                await save_session(session)

                                next_idx = pax_idx + 1
                                if next_idx < total_pax:
                                    # More passengers — ask for next one
                                    await _ask_upload_for_passenger(
                                        sender_wa_id, session, flow, next_idx, phone_number_id
                                    )
                                else:
                                    # All passengers done — show final confirmation
                                    await _show_bp_status(sender_wa_id, session, flow, bp_status, phone_number_id)
                                return
                            else:
                                logger.warning(f"[bp_link] boarding pass upload failed for {pol_code} pax[{pax_idx}]")
                        else:
                            logger.warning(f"[bp_link] missing pol_id={effective_pol_id} or passenger_id={passenger_id} pax[{pax_idx}]")
                    else:
                        logger.warning(f"[bp_link] could not download media {media_id}")
                except Exception as exc:
                    logger.error(f"[bp_link] boarding pass upload failed pax[{pax_idx}]: {exc}")

            await save_session(session)
            await _show_upload_confirmed(sender_wa_id, session, flow, phone_number_id)
        else:
            await _send_text(sender_wa_id,
                "⚠️ Please *send an image or PDF* of your boarding pass.\n\n"
                + UPLOAD_INSTRUCTIONS,
                phone_number_id)

    # ── Screen 4 (Path A): After upload confirmed ─────────────────────────────
    elif step == "bp_upload_done":
        if reply_id == "bp_check_eligibility":
            await _show_eligibility(sender_wa_id, session, flow, phone_number_id)
        elif reply_id == "bp_home":
            await _go_home(sender_wa_id, session, phone_number_id)
        elif reply_id == "bp_cancel":
            await show_cancel_bp_confirm(sender_wa_id, phone_number_id, session)
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
        elif reply_id == "bp_home":
            await _go_home(sender_wa_id, session, phone_number_id)
        elif reply_id == "bp_cancel":
            await show_cancel_bp_confirm(sender_wa_id, phone_number_id, session)
        else:
            await _show_bp_status(sender_wa_id, session, flow, "PENDING", phone_number_id)

    # ── Screen 3 (Path B): Link confirmation ─────────────────────────────────
    elif step == "bp_link_confirm":
        if reply_id == "bp_link_yes":
            await _show_linked(sender_wa_id, session, flow, phone_number_id)
        elif reply_id == "bp_back":
            await _show_policy_list(sender_wa_id, session, flow, "link", phone_number_id)
        elif reply_id == "bp_home":
            await _go_home(sender_wa_id, session, phone_number_id)
        elif reply_id == "bp_cancel":
            await show_cancel_bp_confirm(sender_wa_id, phone_number_id, session)
        else:
            await _show_link_confirm(sender_wa_id, session, flow, phone_number_id)

    # ── Screen 4 (Path B): Linked success ────────────────────────────────────
    elif step == "bp_linked_done":
        if reply_id == "bp_view_policy":
            await _show_policy_card(sender_wa_id, session, flow, phone_number_id)
        elif reply_id == "bp_home":
            await _go_home(sender_wa_id, session, phone_number_id)
        elif reply_id == "bp_cancel":
            await show_cancel_bp_confirm(sender_wa_id, phone_number_id, session)
        else:
            await _show_linked(sender_wa_id, session, flow, phone_number_id)

    # ── Policy mini-card ──────────────────────────────────────────────────────
    elif step == "bp_policy_card":
        await _go_home(sender_wa_id, session, phone_number_id)

    # ── Eligibility result ────────────────────────────────────────────────────
    elif step == "bp_eligibility_result":
        if reply_id == "bp_kyc_verify":
            await send_text_message(
                to=sender_wa_id,
                body=(
                    "🪪 *KYC Verification Required*\n\n"
                    "To process your payout, your KYC verification must be completed.\n\n"
                    "Please contact our support team for assistance completing your verification.\n\n"
                    "_Tap *9 Help* below to reach support._"
                ),
                phone_number_id=phone_number_id,
                source="bp_link_flow",
            )
        elif reply_id == "bp_upload_first":
            await _ask_upload(sender_wa_id, session, flow, {}, phone_number_id)
        elif reply_id == "bp_home":
            await _go_home(sender_wa_id, session, phone_number_id)
        elif reply_id == "bp_cancel":
            await show_cancel_bp_confirm(sender_wa_id, phone_number_id, session)
        else:
            await _show_eligibility(sender_wa_id, session, flow, phone_number_id)

    # ── Payout initiated ──────────────────────────────────────────────────────
    elif step == "bp_payout_done":
        if reply_id == "bp_view_policy":
            await _show_policy_card(sender_wa_id, session, flow, phone_number_id)
        elif reply_id == "bp_home":
            await _go_home(sender_wa_id, session, phone_number_id)
        elif reply_id == "bp_cancel":
            await show_cancel_bp_confirm(sender_wa_id, phone_number_id, session)
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
        "bp_awaiting_doc":       "bp_policy_card",
        "bp_pending_status":     "bp_policy_card",
        "bp_eligibility_result": "bp_policy_card",
        "bp_upload_done":        "bp_policy_card",
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
