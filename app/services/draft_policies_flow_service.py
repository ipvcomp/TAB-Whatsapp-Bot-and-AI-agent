import logging
from typing import Optional

from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload
from app.core.test_overrides import get_msisdn
import app.services.ipurvey_service as _ipurvey_svc

logger = logging.getLogger(__name__)

DRAFT_POLICIES_FLOW_KEY = "draft_policies_flow"


def is_in_draft_policies_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    return bool(
        session.get("temp_data", {})
        .get(DRAFT_POLICIES_FLOW_KEY, {})
        .get("active")
    )


def _dash(value: str) -> str:
    """Return value or '—' when empty/None."""
    return (value or "").strip() or "—"


def _fmt_date(raw: str) -> str:
    if not raw:
        return "—"
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            from datetime import datetime as _dt
            return _dt.strptime(raw, fmt).strftime("%d %b %Y")
        except ValueError:
            pass
    return raw


def _draft_summary_line(draft: dict, idx: int) -> str:
    """Format a single line for the draft list screen.

    Format: #{n}. [PolicyCode]  Flight [flt]  Route [dep→arr]  Date [date]  Traveller [name]
    Nulls shown as —.
    """
    itin = draft.get("itinerary") or {}
    legs = itin.get("legs", [])
    leg = legs[0] if legs else {}

    policy_code = _dash(
        draft.get("policyCode") or draft.get("policyReference") or ""
    )
    flight_num = _dash(leg.get("flightNumber", ""))
    dep = (leg.get("departureAirport", "") or "").strip()
    arr = (leg.get("arrivalAirport", "") or "").strip()
    route = f"{dep} → {arr}" if (dep and arr) else "—"
    dep_date = _fmt_date(leg.get("departureDate", ""))

    pax_name = "—"
    for p in (draft.get("passengers") or []):
        if isinstance(p, dict) and (p.get("firstName") or p.get("surname")):
            pax_name = f"{p.get('firstName', '')} {p.get('surname', '')}".strip()
            break

    state = draft.get("creationState") or draft.get("creation_state") or "DRAFT"
    state_labels = {
        "DRAFT": "🔄 Draft",
        "AWAITING_ITINERARY": "✈️ Trip pending",
        "AWAITING_KYC": "🔐 KYC pending",
        "DETAILS_COLLECTED": "📋 Review pending",
        "KYC_COMPLETED": "✅ Ready to pay",
    }
    status = state_labels.get(state, f"📋 {state.replace('_', ' ').title()}")

    return (
        f"*{idx}.* {policy_code}  |  {status}\n"
        f"   Flight {flight_num}  ·  {route}\n"
        f"   Date {dep_date}  ·  Traveller {pax_name}"
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


async def _show_draft_list(
    wa_id: str,
    drafts: list,
    phone_number_id: Optional[str],
    prefix: str = "",
) -> None:
    lines = []
    if prefix:
        lines.append(prefix)
        lines.append("")
    lines.append(f"📑 *Your Draft Policies* — {len(drafts)} found")
    lines.append("")
    for i, draft in enumerate(drafts, 1):
        lines.append(_draft_summary_line(draft, i))
        if i < len(drafts):
            lines.append("")
    lines.append("")
    lines.append("Reply with the *number* to select a draft  |  *#cancel* to exit")
    await send_text_message(
        to=wa_id,
        body="\n".join(lines),
        phone_number_id=phone_number_id,
        source="draft_policies_flow",
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
        await _send_buttons(
            wa_id,
            "📑 *Draft Policies*\n\n"
            "You have no saved draft policies at the moment.\n\n"
            "Tap below to start a new policy application.",
            [{"id": "buy_cover", "title": "✈️ Buy Cover"}],
            phone_number_id,
        )
        return

    session.setdefault("temp_data", {})[DRAFT_POLICIES_FLOW_KEY] = {
        "active": True,
        "step": "select_draft",
        "data": {"drafts": drafts},
    }
    await save_session(session)
    await _show_draft_list(wa_id, drafts, phone_number_id)


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
            await send_text_message(
                to=wa_id,
                body="📑 You have no saved draft policies.",
                phone_number_id=phone_number_id,
                source="draft_policies_flow",
            )
            return

        try:
            idx = int(text)
        except (ValueError, TypeError):
            await _show_draft_list(
                wa_id, drafts, phone_number_id,
                prefix=f"⚠️ Please reply with a number between 1 and {len(drafts)}.",
            )
            return

        if idx < 1 or idx > len(drafts):
            await _show_draft_list(
                wa_id, drafts, phone_number_id,
                prefix=f"⚠️ Please choose a number between 1 and {len(drafts)}.",
            )
            return

        selected = drafts[idx - 1]
        data["selected_idx"] = idx
        flow["step"] = "draft_action"
        await save_session(session)

        itin = selected.get("itinerary") or {}
        legs = itin.get("legs", [])
        leg = legs[0] if legs else {}
        policy_code = _dash(
            selected.get("policyCode") or selected.get("policyReference") or ""
        )
        flight_num = _dash(leg.get("flightNumber", ""))
        dep = (leg.get("departureAirport", "") or "").strip()
        arr = (leg.get("arrivalAirport", "") or "").strip()
        route = f"{dep} → {arr}" if (dep and arr) else "—"
        dep_date = _fmt_date(leg.get("departureDate", ""))

        pax_name = "—"
        for p in (selected.get("passengers") or []):
            if isinstance(p, dict) and (p.get("firstName") or p.get("surname")):
                pax_name = f"{p.get('firstName', '')} {p.get('surname', '')}".strip()
                break

        state = selected.get("creationState") or selected.get("creation_state") or "DRAFT"
        state_labels = {
            "DRAFT": "🔄 Draft — just started",
            "AWAITING_ITINERARY": "✈️ Flight details needed",
            "AWAITING_KYC": "🔐 Identity verification needed",
            "DETAILS_COLLECTED": "📋 Awaiting cover selection",
            "KYC_COMPLETED": "✅ Ready for payment",
        }
        status = state_labels.get(state, f"📋 {state.replace('_', ' ').title()}")

        detail_lines = [
            f"📋 *Draft {idx} of {len(drafts)}*",
            "",
            f"Policy      {policy_code}",
            f"Flight      {flight_num}",
            f"Route       {route}",
            f"Date        {dep_date}",
            f"Traveller   {pax_name}",
            "",
            f"Status      {status}",
        ]
        await _send_buttons(
            wa_id,
            "\n".join(detail_lines) + "\n\nWhat would you like to do?",
            [
                {"id": "draft_continue", "title": "▶️ Continue"},
                {"id": "draft_delete",   "title": "🗑️ Delete"},
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
            normalised = _ipurvey_svc._normalise_draft(selected_raw)
            policy_id = normalised.get("policy_id")
            creation_state = normalised.get("creation_state", "DRAFT")
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
            dep_code = (leg_r.get("departureAirport", "") or "").strip()
            arr_code = (leg_r.get("arrivalAirport", "") or "").strip()

            bc_data: dict = {}
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
            bc_data["flight_num"] = leg_r.get("flightNumber", "")
            bc_data["depart_airport"] = f"{dep_code} — {dep_code}" if dep_code else ""
            bc_data["arrive_airport"] = f"{arr_code} — {arr_code}" if arr_code else ""
            bc_data["date"] = leg_r.get("departureDate", "")
            bc_data["depart_time"] = leg_r.get("departureTime", "")
            bc_data["arrive_time"] = leg_r.get("arrivalTime", "")
            bc_data["trip_type"] = "Return 🔄" if (trip_raw or "").upper() == "RETURN" else "One-way 🗺️"

            session.pop("paused_context", None)
            session["api_data"] = {
                "policy_id": policy_id,
                "resume_draft": normalised,
                "passenger_ids": pax_ids,
                "passenger_id": primary_id,
                "user_exists": True,
            }

            from app.services.buy_cover_flow_service import BUY_COVER_FLOW_KEY
            flow_bc = session.setdefault("temp_data", {})
            flow_bc[BUY_COVER_FLOW_KEY] = {
                "active": True,
                "step": "buy_cover_who",
                "data": bc_data,
            }
            flow_bc[DRAFT_POLICIES_FLOW_KEY] = {}
            await save_session(session)

            # Directly jump to the right step (no Resume/Fresh prompt)
            await send_text_message(
                to=wa_id,
                body="✅ *Welcome back!* Picking up your application...",
                phone_number_id=phone_number_id,
                source="draft_policies_flow",
            )

            bc_flow = session["temp_data"][BUY_COVER_FLOW_KEY]
            bc_flow_data = bc_flow["data"]

            if creation_state in ("AWAITING_KYC", "DETAILS_COLLECTED"):
                try:
                    quotes = await _ipurvey_svc.fetch_quotes(policy_id)
                except Exception as exc:
                    logger.error(f"[draft_policies] fetch_quotes failed: {exc}")
                    quotes = None
                if quotes:
                    session["api_data"]["quotes"] = quotes
                    bc_flow["step"] = "buy_cover_select_cover"
                    await save_session(session)
                    from app.services.buy_cover_flow_service import _send_cover_page
                    await _send_cover_page(
                        wa_id, quotes, 0, phone_number_id,
                        intro_body=(
                            "🎁 *Your trip details are already saved!*\n\n"
                            "👇 Just pick your cover plan to continue:"
                        ),
                    )
                else:
                    bc_flow["step"] = "buy_cover_trip_type"
                    await save_session(session)
                    from app.services.buy_cover_flow_service import _send_buttons as _bc_send_buttons
                    await _bc_send_buttons(
                        wa_id,
                        "⚠️ Covers unavailable — let's continue from your trip details.\n\n"
                        "🗺️ What type of trip is this?",
                        [{"id": "trip_oneway", "title": "1. 🗺️ One-way"}],
                        phone_number_id,
                    )

            elif creation_state == "AWAITING_ITINERARY":
                bc_flow["step"] = "buy_cover_trip_type"
                await save_session(session)
                from app.services.buy_cover_flow_service import _send_buttons as _bc_send_buttons
                await _bc_send_buttons(
                    wa_id,
                    "🗺️ What type of trip is this?",
                    [{"id": "trip_oneway", "title": "1. 🗺️ One-way"}],
                    phone_number_id,
                )

            else:
                if not bc_flow_data.get("email"):
                    bc_flow["step"] = "buy_cover_email"
                    await save_session(session)
                    from app.services.whatsapp_service import send_text_message as _st
                    await _st(
                        to=wa_id,
                        body=(
                            "*📧 Please enter your email address*\n"
                            "So we can send your policy documents\n\n"
                            "_Example: yusuf@email.com_"
                        ),
                        phone_number_id=phone_number_id,
                        source="draft_policies_flow",
                    )
                else:
                    bc_flow["step"] = "buy_cover_trip_type"
                    await save_session(session)
                    from app.services.buy_cover_flow_service import _send_buttons as _bc_send_buttons
                    await _bc_send_buttons(
                        wa_id,
                        "🗺️ What type of trip is this?",
                        [{"id": "trip_oneway", "title": "1. 🗺️ One-way"}],
                        phone_number_id,
                    )
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

            ok = False
            if policy_id:
                ok = await _ipurvey_svc.cancel_draft_policy(policy_id)

            if ok:
                msisdn = get_msisdn(wa_id)
                new_drafts = await _ipurvey_svc.resume_all_drafts(msisdn)
                if new_drafts:
                    data["drafts"] = new_drafts
                    data.pop("selected_idx", None)
                    flow["step"] = "select_draft"
                    await save_session(session)
                    await _show_draft_list(
                        wa_id, new_drafts, phone_number_id,
                        prefix="✅ Draft deleted successfully.",
                    )
                else:
                    flow["active"] = False
                    await save_session(session)
                    await _send_buttons(
                        wa_id,
                        "✅ Draft deleted successfully.\n\n"
                        "You have no more saved draft policies.",
                        [{"id": "buy_cover", "title": "✈️ Buy Cover"}],
                        phone_number_id,
                    )
            else:
                await send_text_message(
                    to=wa_id,
                    body="⚠️ Could not delete the draft. Please try again later.",
                    phone_number_id=phone_number_id,
                    source="draft_policies_flow",
                )
            return

        # Unknown input at draft_action — re-show action buttons
        if drafts and 0 < idx <= len(drafts):
            selected_raw_r = drafts[idx - 1]
            state_r = selected_raw_r.get("creationState") or selected_raw_r.get("creation_state") or "DRAFT"
            state_labels_r = {
                "DRAFT": "🔄 Draft — just started",
                "AWAITING_ITINERARY": "✈️ Flight details needed",
                "AWAITING_KYC": "🔐 Identity verification needed",
                "DETAILS_COLLECTED": "📋 Awaiting cover selection",
                "KYC_COMPLETED": "✅ Ready for payment",
            }
            status_r = state_labels_r.get(state_r, f"📋 {state_r.replace('_', ' ').title()}")
            await _send_buttons(
                wa_id,
                f"Draft {idx}: {status_r}\n\nWhat would you like to do?",
                [
                    {"id": "draft_continue", "title": "▶️ Continue"},
                    {"id": "draft_delete",   "title": "🗑️ Delete"},
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
        await _show_draft_list(wa_id, drafts_r, phone_number_id)
    else:
        flow["active"] = False
        await save_session(session)
        await send_text_message(
            to=wa_id,
            body="📑 You have no saved draft policies.",
            phone_number_id=phone_number_id,
            source="draft_policies_flow",
        )
