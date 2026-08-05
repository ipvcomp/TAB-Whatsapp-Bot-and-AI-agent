import logging
from datetime import datetime
from typing import Optional

from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload
from app.core.test_overrides import get_msisdn
import app.services.ipurvey_service as _ipurvey_svc

logger = logging.getLogger(__name__)

DRAFT_POLICIES_FLOW_KEY = "draft_policies_flow"

_UTILITY = (
    "0 ↩️ Back  |  9 🆘 Help  |  00 🏠 Main menu\n"
    "99 ❌ Cancel/Exit"
)


def is_in_draft_policies_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    return bool(
        session.get("temp_data", {})
        .get(DRAFT_POLICIES_FLOW_KEY, {})
        .get("active")
    )


def _dash(value) -> str:
    """Return value or '—' when empty/None."""
    return (str(value) if value else "").strip() or "—"


def _fmt_date(raw: str) -> str:
    """Format date string to DD-Mon-YYYY (e.g. 23-May-2026). Returns '—' on failure."""
    if not raw:
        return "—"
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw[:10], fmt).strftime("%d-%b-%Y")
        except (ValueError, TypeError):
            pass
    return raw


def _fmt_last_saved(raw: str) -> str:
    """Format ISO timestamp to '20 May 2025, 10:30 AM'. Returns '—' on failure."""
    if not raw:
        return "—"
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            dt = datetime.strptime(raw[:26].rstrip("Z"), fmt.rstrip("Z"))
            return dt.strftime("%-d %b %Y, %-I:%M %p")
        except (ValueError, TypeError):
            pass
    return raw


def _extract_flight_date(draft: dict) -> tuple[str, str]:
    """Return (flight_number, dep_date_formatted) from itinerary. Supports flat and legs shapes."""
    itin = draft.get("itinerary") or {}
    legs = itin.get("legs", [])
    leg = legs[0] if legs else {}
    flight = leg.get("flightNumber") or itin.get("flightNumber") or ""
    dep_date_raw = leg.get("departureDate") or itin.get("departureDate") or ""
    return _dash(flight), _fmt_date(dep_date_raw)


def _extract_traveller(draft: dict) -> str:
    for p in (draft.get("passengers") or []):
        if isinstance(p, dict) and (p.get("firstName") or p.get("surname")):
            return f"{p.get('firstName', '')} {p.get('surname', '')}".strip()
    return "—"


def _extract_last_saved(draft: dict) -> str:
    raw = (
        draft.get("updatedAt")
        or draft.get("lastModifiedAt")
        or draft.get("lastSaved")
        or draft.get("createdAt")
        or ""
    )
    return _fmt_last_saved(raw)


def _extract_policy_code(draft: dict) -> str:
    return _dash(
        draft.get("policyCode")
        or draft.get("policyReference")
        or ""
    )


def _fmt_draft_entry(draft: dict, idx: int) -> str:
    code = _extract_policy_code(draft)
    flight, dep_date = _extract_flight_date(draft)
    traveller = _extract_traveller(draft)
    last_saved = _extract_last_saved(draft)
    return (
        f"{idx}. {code}\n"
        f"   ✈️ {flight}  🗓️ {dep_date}\n"
        f"   🧑 {traveller}\n"
        f"   Last saved: {last_saved}"
    )


def _build_list_body(drafts: list) -> str:
    """Build the full numbered-list message body per spec."""
    n = len(drafts)
    header = f"You have {n} draft {'policy' if n == 1 else 'policies'}.\nWhich one would you like to resume?"
    entries = "\n\n".join(_fmt_draft_entry(d, i) for i, d in enumerate(drafts, 1))
    return f"{header}\n\n{entries}\n\n_Reply with the list number to resume (e.g. 1 or 2)_"


def _build_detail_card(draft: dict) -> str:
    code = _extract_policy_code(draft)
    flight, dep_date = _extract_flight_date(draft)
    traveller = _extract_traveller(draft)
    last_saved = _extract_last_saved(draft)
    return (
        f"You selected:\n\n"
        f"{code}\n"
        f"✈️ {flight}  🗓️ {dep_date}\n"
        f"🧑 {traveller}\n"
        f"Last saved: {last_saved}\n\n"
        f"What would you like to do?"
    )


async def _send_buttons(
    wa_id: str,
    body: str,
    buttons: list,
    phone_number_id: Optional[str],
) -> Optional[dict]:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": wa_id,
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
    return await send_whatsapp_payload(
        whatsapp_payload=payload,
        phone_number_id=phone_number_id,
        source="draft_policies_flow",
    )


async def _send_list(wa_id: str, drafts: list, phone_number_id: Optional[str], include_utility: bool = True) -> None:
    body = _build_list_body(drafts)
    if include_utility:
        body = f"{body}\n\n\n{_UTILITY}"
    await send_text_message(
        to=wa_id,
        body=body,
        phone_number_id=phone_number_id,
        source="draft_policies_flow",
    )


async def _send_no_drafts(wa_id: str, phone_number_id: Optional[str]) -> None:
    """Send the 'no drafts' screen with Buy Cover + Main Menu buttons."""
    await _send_buttons(
        wa_id,
        "You have no draft policies.\n\nStart a new cover or return to the main menu.",
        [
            {"id": "welcome_purchase_policy", "title": "✈️ Buy Cover"},
            {"id": "go_main", "title": "🏠 Main Menu"},
        ],
        phone_number_id,
    )


async def start_draft_policies_flow(
    wa_id: str,
    phone_number_id: Optional[str],
) -> None:
    session = await get_session(wa_id) or {}
    msisdn = get_msisdn(wa_id)

    drafts = await _ipurvey_svc.resume_all_drafts(msisdn)

    if not drafts:
        session.setdefault("temp_data", {})[DRAFT_POLICIES_FLOW_KEY] = {}
        await save_session(session)
        await _send_no_drafts(wa_id, phone_number_id)
        return

    session.setdefault("temp_data", {})[DRAFT_POLICIES_FLOW_KEY] = {
        "active": True,
        "step": "select_draft",
        "data": {"drafts": drafts},
    }
    await save_session(session)
    await _send_list(wa_id, drafts, phone_number_id)


async def handle_draft_policies_input(
    message,
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
) -> None:
    session = await get_session(wa_id) or {}
    flow = session.setdefault("temp_data", {}).setdefault(DRAFT_POLICIES_FLOW_KEY, {})
    step = flow.get("step", "select_draft")
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

    # ── Step: select_draft ─────────────────────────────────────────────────────
    if step == "select_draft":
        drafts = data.get("drafts") or []
        if not drafts:
            msisdn = get_msisdn(wa_id)
            drafts = await _ipurvey_svc.resume_all_drafts(msisdn)
            data["drafts"] = drafts

        if not drafts:
            flow["active"] = False
            await save_session(session)
            await _send_no_drafts(wa_id, phone_number_id)
            return

        n = len(drafts)
        valid = False
        chosen_idx = None
        if text:
            try:
                val = int(text)
                if 1 <= val <= n:
                    valid = True
                    chosen_idx = val
            except (ValueError, TypeError):
                pass

        if not valid:
            await send_text_message(
                to=wa_id,
                body=f"Please reply with a number between 1 and {n}.\n\n{_build_list_body(drafts)}\n\n\n{_UTILITY}",
                phone_number_id=phone_number_id,
                source="draft_policies_flow",
            )
            return

        selected = drafts[chosen_idx - 1]
        data["selected_idx"] = chosen_idx
        flow["step"] = "draft_action"
        await save_session(session)

        await _send_buttons(
            wa_id,
            f"{_build_detail_card(selected)}\n\n\n{_UTILITY}",
            [
                {"id": "draft_continue", "title": "✅ Continue policy"},
                {"id": "draft_delete",   "title": "🗑️ Delete policy"},
            ],
            phone_number_id,
        )
        return

    # ── Step: draft_action ─────────────────────────────────────────────────────
    if step == "draft_action":
        drafts = data.get("drafts") or []
        idx = data.get("selected_idx", 1)

        if reply_id == "draft_continue":
            if not drafts or idx < 1 or idx > len(drafts):
                await send_text_message(
                    to=wa_id,
                    body="⚠️ Could not find the selected draft. Please try again.",
                    phone_number_id=phone_number_id,
                    source="draft_policies_flow",
                )
                flow["step"] = "select_draft"
                await save_session(session)
                return

            selected_raw = drafts[idx - 1]
            policy_id = (
                selected_raw.get("policyId")
                or selected_raw.get("id")
                or selected_raw.get("policy_id")
            )

            code = _extract_policy_code(selected_raw)
            flight, dep_date = _extract_flight_date(selected_raw)
            traveller = _extract_traveller(selected_raw)
            last_saved = _extract_last_saved(selected_raw)
            policy_card = (
                f"{code}\n"
                f"✈️ {flight}  🗓️ {dep_date}\n"
                f"🧑 {traveller}\n"
                f"Last saved: {last_saved}"
            )
            await send_text_message(
                to=wa_id,
                body=(
                    f"✅ Great!\n"
                    f"You will now continue with:\n\n"
                    f"{policy_card}\n\n"
                    f"Please wait while we load your policy..."
                ),
                phone_number_id=phone_number_id,
                source="draft_policies_flow",
            )

            msisdn = get_msisdn(wa_id)
            fresh_raw = await _ipurvey_svc.resume_draft_policy(msisdn, preferred_id=policy_id)
            if fresh_raw:
                normalised = fresh_raw
            else:
                normalised = _ipurvey_svc._normalise_draft(selected_raw)

            creation_state = normalised.get("creation_state", "DRAFT")
            pid = normalised.get("policy_id") or policy_id
            passengers = normalised.get("passengers") or []
            email = normalised.get("email") or ""

            pax_names = [
                f"{p.get('firstName', '')} {p.get('surname', '')}".strip()
                for p in passengers
                if isinstance(p, dict) and (p.get("firstName") or p.get("surname"))
            ]
            pax_ids = [p["id"] for p in passengers if isinstance(p, dict) and p.get("id")]
            primary = next(
                (p for p in passengers if isinstance(p, dict) and p.get("isPrimary")),
                passengers[0] if passengers else None,
            )
            primary_id = (primary or {}).get("id") if isinstance(primary, dict) else None

            itin = normalised.get("itinerary") or {}
            legs_r = itin.get("legs", [])
            leg_r = legs_r[0] if legs_r else {}
            trip_raw = normalised.get("trip_type", "ONE_WAY")
            dep_code = (leg_r.get("departureAirport") or itin.get("departureAirport") or "").strip()
            arr_code = (leg_r.get("arrivalAirport") or itin.get("arrivalAirport") or "").strip()

            bc_data: dict = {"policy_id": pid}
            if pax_names:
                bc_data["name"] = pax_names[0]
                if len(pax_names) > 1:
                    bc_data["who"] = "me_and_others"
                    bc_data["travelers"] = pax_names
                    bc_data["others_count"] = len(pax_names) - 1
                else:
                    bc_data["who"] = "just_me"
            if email:
                bc_data["email"] = email
            bc_data["booking_ref"] = itin.get("bookingReference", "")
            bc_data["flight_num"] = leg_r.get("flightNumber") or itin.get("flightNumber") or ""
            bc_data["depart_airport"] = f"{dep_code} — {dep_code}" if dep_code else ""
            bc_data["arrive_airport"] = f"{arr_code} — {arr_code}" if arr_code else ""
            bc_data["date"] = leg_r.get("departureDate") or itin.get("departureDate") or ""
            bc_data["depart_time"] = leg_r.get("departureTime") or itin.get("departureTime") or ""
            bc_data["arrive_time"] = leg_r.get("arrivalTime") or itin.get("arrivalTime") or ""
            bc_data["trip_type"] = "Return 🔄" if (trip_raw or "").upper() == "RETURN" else "One-way 🗺️"

            from app.services.buy_cover_flow_service import BUY_COVER_FLOW_KEY, resume_at_current_step as bc_resume
            from app.services.kyc_flow_service import KYC_FLOW_KEY, resume_at_current_step as kyc_resume
            from app.services.payment_flow_service import PAYMENT_FLOW_KEY, resume_at_current_step as pay_resume

            # ── Exact-step fingerprint restore via paused_context ─────────────
            # When the user pressed 00 mid-flow, pause_buy_cover_flow() saved a
            # full snapshot in session["paused_context"]:
            #   active_flow, buy_cover_step/data, kyc_step/data, payment_step/data,
            #   policy_id, quotes, payout_method_id, passenger_ids, user_id.
            # If that snapshot belongs to the policy the user just selected, we
            # use it to land them on the EXACT step they left — no data loss.
            # Only when there is no matching snapshot do we fall back to the
            # coarse API creation_state mapping.
            pc = session.get("paused_context") or {}
            use_paused = bool(pc and pc.get("policy_id") == pid)

            session.pop("paused_context", None)

            # Build api_data — restore cached values from paused_context so
            # downstream steps (KYC submit, payment submit) still have them.
            new_api_data: dict = {
                "policy_id": pid,
                "resume_draft": normalised,
                "passenger_ids": pax_ids,
                "passenger_id": primary_id,
                "user_exists": True,
            }
            if use_paused:
                if pc.get("quotes"):
                    new_api_data["quotes"] = pc["quotes"]
                if pc.get("payout_method_id"):
                    new_api_data["payout_method_id"] = pc["payout_method_id"]
                if pc.get("user_id"):
                    new_api_data["user_id"] = pc["user_id"]
                if pc.get("passenger_id"):
                    new_api_data["passenger_id"] = pc["passenger_id"]
                if pc.get("passenger_ids"):
                    new_api_data["passenger_ids"] = pc["passenger_ids"]
                if pc.get("policy_code"):
                    new_api_data["policy_code"] = pc["policy_code"]

            session["api_data"] = new_api_data
            session["temp_data"][DRAFT_POLICIES_FLOW_KEY] = {}

            if use_paused:
                # ── Path A: exact-step restore ────────────────────────────────
                active_flow = pc.get("active_flow", BUY_COVER_FLOW_KEY)

                # Always restore buy_cover_flow state (background context for
                # KYC and payment flows which run on top of it).
                bc_step = pc.get("buy_cover_step") or "buy_cover_select_cover"
                bc_step_data = dict(pc.get("buy_cover_data") or bc_data)
                session["temp_data"][BUY_COVER_FLOW_KEY] = {
                    "active": active_flow == BUY_COVER_FLOW_KEY,
                    "step": bc_step,
                    "data": bc_step_data,
                }

                if active_flow == PAYMENT_FLOW_KEY:
                    session["temp_data"][KYC_FLOW_KEY] = {
                        "active": False,
                        "step": pc.get("kyc_step") or "",
                        "data": dict(pc.get("kyc_data") or {}),
                    }
                    session["temp_data"][PAYMENT_FLOW_KEY] = {
                        "active": True,
                        "step": pc.get("payment_step") or "pay_payout_options",
                        "data": dict(pc.get("payment_data") or {}),
                    }
                    await save_session(session)
                    logger.info(
                        f"[draft_policies] exact resume → PAYMENT "
                        f"step={pc.get('payment_step')} policy={pid}"
                    )
                    await pay_resume(wa_id, phone_number_id)

                elif active_flow == KYC_FLOW_KEY:
                    session["temp_data"][KYC_FLOW_KEY] = {
                        "active": True,
                        "step": pc.get("kyc_step") or "kyc_intro",
                        "data": dict(pc.get("kyc_data") or {}),
                    }
                    await save_session(session)
                    logger.info(
                        f"[draft_policies] exact resume → KYC "
                        f"step={pc.get('kyc_step')} policy={pid}"
                    )
                    await kyc_resume(wa_id, phone_number_id)

                else:
                    # buy_cover_flow — already set active=True above
                    await save_session(session)
                    logger.info(
                        f"[draft_policies] exact resume → BUY_COVER "
                        f"step={bc_step} policy={pid}"
                    )
                    await bc_resume(wa_id, phone_number_id)

            else:
                # ── Path B: coarse fallback from API creation_state ───────────
                # Used when there is no paused_context (e.g. user resumed from a
                # different device / session expired).
                if creation_state in ("AWAITING_KYC", "DETAILS_COLLECTED"):
                    target_step = "buy_cover_select_cover"
                elif creation_state in ("AWAITING_PAYMENT", "AWAITING_BOARDING_PASS"):
                    # Payment data is not returned by the resume API, so we
                    # cannot restore the exact payment screen.  Drop the user
                    # back at cover selection — the cheapest safe restart point.
                    target_step = "buy_cover_select_cover"
                elif creation_state == "AWAITING_ITINERARY":
                    target_step = "buy_cover_trip_type"
                elif creation_state == "DRAFT" and not email:
                    target_step = "buy_cover_email"
                elif creation_state == "DRAFT":
                    target_step = "buy_cover_trip_type"
                else:
                    target_step = "buy_cover_select_cover"

                session["temp_data"][BUY_COVER_FLOW_KEY] = {
                    "active": True,
                    "step": target_step,
                    "data": bc_data,
                }
                await save_session(session)
                logger.info(
                    f"[draft_policies] fallback resume → creation_state={creation_state} "
                    f"target_step={target_step} policy={pid}"
                )
                await bc_resume(wa_id, phone_number_id)

            return

        if reply_id == "draft_delete":
            if not drafts or idx < 1 or idx > len(drafts):
                await send_text_message(
                    to=wa_id,
                    body="⚠️ Could not find the selected draft. Please try again.",
                    phone_number_id=phone_number_id,
                    source="draft_policies_flow",
                )
                flow["step"] = "select_draft"
                await save_session(session)
                return

            selected_raw = drafts[idx - 1]
            policy_id = (
                selected_raw.get("policyId")
                or selected_raw.get("id")
                or selected_raw.get("policy_id")
            )
            policy_code = _extract_policy_code(selected_raw)

            ok = False
            if policy_id:
                ok = await _ipurvey_svc.cancel_draft_policy(policy_id)

            if ok:
                await send_text_message(
                    to=wa_id,
                    body=(
                        f"🗑️ Draft policy deleted.\n\n"
                        f"| {policy_code}\n\n"
                        f"has been removed from your drafts."
                        f"\n\n\n{_UTILITY}"
                    ),
                    phone_number_id=phone_number_id,
                    source="draft_policies_flow",
                )
                msisdn = get_msisdn(wa_id)
                new_drafts = await _ipurvey_svc.resume_all_drafts(msisdn)
                if new_drafts:
                    data["drafts"] = new_drafts
                    data.pop("selected_idx", None)
                    flow["step"] = "select_draft"
                    await save_session(session)
                    await _send_list(wa_id, new_drafts, phone_number_id, include_utility=False)
                else:
                    flow["active"] = False
                    await save_session(session)
                    await _send_no_drafts(wa_id, phone_number_id)
            else:
                await send_text_message(
                    to=wa_id,
                    body=f"⚠️ Could not delete the draft. Please try again later.\n\n\n{_UTILITY}",
                    phone_number_id=phone_number_id,
                    source="draft_policies_flow",
                )
            return

        # Unknown input at draft_action — re-show action buttons for selected draft
        if drafts and 0 < idx <= len(drafts):
            await _send_buttons(
                wa_id,
                f"{_build_detail_card(drafts[idx - 1])}\n\n\n{_UTILITY}",
                [
                    {"id": "draft_continue", "title": "✅ Continue policy"},
                    {"id": "draft_delete",   "title": "🗑️ Delete policy"},
                ],
                phone_number_id,
            )
        return

    # Unknown step — restart flow
    msisdn = get_msisdn(wa_id)
    drafts_r = await _ipurvey_svc.resume_all_drafts(msisdn)
    if drafts_r:
        data["drafts"] = drafts_r
        flow["step"] = "select_draft"
        await save_session(session)
        await _send_list(wa_id, drafts_r, phone_number_id)
    else:
        flow["active"] = False
        await save_session(session)
        await _send_no_drafts(wa_id, phone_number_id)


async def go_back_one_step(
    wa_id: str,
    phone_number_id: Optional[str],
) -> None:
    """Handle 0 / #back within the draft policies flow.

    select_draft → exit flow, show main menu
    draft_action → go back to select_draft, re-show list
    """
    session = await get_session(wa_id) or {}
    flow = session.setdefault("temp_data", {}).setdefault(DRAFT_POLICIES_FLOW_KEY, {})
    step = flow.get("step", "select_draft")
    data = flow.get("data", {})

    if step == "draft_action":
        flow["step"] = "select_draft"
        data.pop("selected_idx", None)
        await save_session(session)
        drafts = data.get("drafts") or []
        if not drafts:
            msisdn = get_msisdn(wa_id)
            drafts = await _ipurvey_svc.resume_all_drafts(msisdn)
            data["drafts"] = drafts
            await save_session(session)
        if drafts:
            await _send_list(wa_id, drafts, phone_number_id)
        else:
            flow["active"] = False
            await save_session(session)
            await _send_no_drafts(wa_id, phone_number_id)
        return

    # select_draft or any other step → exit to main menu
    flow["active"] = False
    await save_session(session)
    from app.services.auto_reply_service import send_main_menu
    await send_main_menu(to=wa_id, phone_number_id=phone_number_id, wa_id=wa_id)
