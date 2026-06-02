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


def _draft_summary_line(draft: dict, idx: int) -> str:
    itin = draft.get("itinerary") or {}
    legs = itin.get("legs", [])
    leg = legs[0] if legs else {}
    dep = leg.get("departureAirport", "") or leg.get("departure_airport", "")
    arr = leg.get("arrivalAirport", "") or leg.get("arrival_airport", "")
    dep_date = leg.get("departureDate", "") or leg.get("departure_date", "")

    pax_name = ""
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
    state_label = state_labels.get(state, f"📋 {state.replace('_', ' ').title()}")

    parts = []
    if dep and arr:
        parts.append(f"{dep} → {arr}")
    if dep_date:
        try:
            from datetime import datetime as _dt
            parts.append(_dt.strptime(dep_date, "%Y-%m-%d").strftime("%d %b %Y"))
        except ValueError:
            parts.append(dep_date)
    if pax_name:
        parts.append(pax_name)

    detail = "  •  ".join(parts) if parts else "(no details yet)"
    return f"{idx}. {state_label} — {detail}"


def _format_draft_detail(draft: dict, idx: int, total: int) -> str:
    itin = draft.get("itinerary") or {}
    legs = itin.get("legs", [])
    leg = legs[0] if legs else {}
    dep = leg.get("departureAirport", "") or ""
    arr = leg.get("arrivalAirport", "") or ""
    dep_date = leg.get("departureDate", "") or ""
    dep_time = leg.get("departureTime", "") or ""
    flight_num = leg.get("flightNumber", "") or ""
    booking_ref = itin.get("bookingReference", "") or ""

    pax_names = []
    for p in (draft.get("passengers") or []):
        if isinstance(p, dict):
            name = f"{p.get('firstName', '')} {p.get('surname', '')}".strip()
            if name:
                pax_names.append(name)

    state = draft.get("creationState") or draft.get("creation_state") or "DRAFT"
    state_labels = {
        "DRAFT": "🔄 Draft — just started",
        "AWAITING_ITINERARY": "✈️ Flight details needed",
        "AWAITING_KYC": "🔐 Identity verification needed",
        "DETAILS_COLLECTED": "📋 Awaiting cover selection",
        "KYC_COMPLETED": "✅ Ready for payment",
    }
    state_label = state_labels.get(state, f"📋 {state.replace('_', ' ').title()}")

    lines = [f"📋 *Draft {idx} of {total}*", ""]
    if dep and arr:
        lines.append(f"Route        {dep} → {arr}")
    if flight_num:
        lines.append(f"Flight       {flight_num}")
    if booking_ref:
        lines.append(f"Booking Ref  {booking_ref}")
    if dep_date:
        try:
            from datetime import datetime as _dt
            dep_date_fmt = _dt.strptime(dep_date, "%Y-%m-%d").strftime("%d %b %Y")
        except ValueError:
            dep_date_fmt = dep_date
        dep_time_fmt = ""
        if dep_time:
            try:
                from datetime import datetime as _dt
                dep_time_fmt = " · " + _dt.strptime(dep_time, "%H:%M").strftime("%I:%M %p")
            except ValueError:
                dep_time_fmt = f" · {dep_time}"
        lines.append(f"Departure    {dep_date_fmt}{dep_time_fmt}")
    if pax_names:
        lines.append(f"Passenger    {pax_names[0]}")
        for n in pax_names[1:]:
            lines.append(f"             {n}")
    lines.append("")
    lines.append(f"Status       {state_label}")
    return "\n".join(lines)


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
    intro: str = "",
) -> None:
    lines = []
    if intro:
        lines.append(intro)
        lines.append("")
    lines.append(f"📑 *Your Draft Policies* ({len(drafts)} found)")
    lines.append("")
    for i, draft in enumerate(drafts, 1):
        lines.append(_draft_summary_line(draft, i))
    lines.append("")
    lines.append("Reply with the *number* to select a draft.")
    lines.append("\nType *#cancel* at any time to exit.")
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
            "You have no saved draft policies.\n\n"
            "Start a new policy purchase from the main menu.",
            [{"id": "welcome_purchase_policy", "title": "✈️ Buy Cover"}],
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
                body="You have no saved draft policies.",
                phone_number_id=phone_number_id,
                source="draft_policies_flow",
            )
            return

        try:
            idx = int(text)
        except (ValueError, TypeError):
            await send_text_message(
                to=wa_id,
                body=f"Please reply with a number between 1 and {len(drafts)}.\n"
                     f"_Example: 1_",
                phone_number_id=phone_number_id,
                source="draft_policies_flow",
            )
            return

        if idx < 1 or idx > len(drafts):
            await send_text_message(
                to=wa_id,
                body=f"⚠️ Please choose a number between 1 and {len(drafts)}.",
                phone_number_id=phone_number_id,
                source="draft_policies_flow",
            )
            return

        selected = drafts[idx - 1]
        data["selected_idx"] = idx
        flow["step"] = "draft_action"
        await save_session(session)

        detail_text = _format_draft_detail(selected, idx, len(drafts))
        await _send_buttons(
            wa_id,
            detail_text + "\n\nWhat would you like to do?",
            [
                {"id": "dp_continue", "title": "✅ Continue"},
                {"id": "dp_delete", "title": "🗑️ Delete"},
            ],
            phone_number_id,
        )
        return

    # ── Step: draft_action ─────────────────────────────────────────────────────
    if step == "draft_action":
        drafts = data.get("drafts") or []
        idx = data.get("selected_idx", 1)

        if reply_id == "dp_continue":
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

            # Clear paused_context so resume_yes uses api_data["resume_draft"] (section B)
            session.pop("paused_context", None)

            session["api_data"] = {
                "policy_id": policy_id,
                "resume_draft": normalised,
                "passenger_ids": pax_ids,
                "passenger_id": primary_id,
                "user_exists": True,
            }

            itin = normalised.get("itinerary") or {}
            legs = itin.get("legs", [])
            leg = legs[0] if legs else {}
            dep_code = leg.get("departureAirport", "") or ""
            arr_code = leg.get("arrivalAirport", "") or ""
            dep_date = leg.get("departureDate", "") or ""
            flight_num = leg.get("flightNumber", "") or ""

            hint_lines = []
            if pax_names:
                hint_lines.append(f"👤 {pax_names[0]}")
            if flight_num:
                route_part = f"  •  {dep_code} → {arr_code}" if (dep_code or arr_code) else ""
                hint_lines.append(f"✈️ {flight_num}{route_part}")
            elif dep_code or arr_code:
                hint_lines.append(f"🛫 {dep_code} → {arr_code}")
            if dep_date:
                try:
                    from datetime import datetime as _dt
                    hint_lines.append(f"📅 {_dt.strptime(dep_date, '%Y-%m-%d').strftime('%d %b %Y')}")
                except ValueError:
                    hint_lines.append(f"📅 {dep_date}")

            state_hints = {
                "AWAITING_KYC": "\n🔐 Identity verification was in progress",
                "DETAILS_COLLECTED": "\n📋 Details collected — cover selection needed",
                "KYC_COMPLETED": "\n✅ Almost done — payment needed",
                "AWAITING_ITINERARY": "\n✈️ Flight details still needed",
            }
            if creation_state in state_hints:
                hint_lines.append(state_hints[creation_state])

            hint = "\n".join(hint_lines) if hint_lines else "Some details were already saved."

            from app.services.buy_cover_flow_service import BUY_COVER_FLOW_KEY
            session.setdefault("temp_data", {})[BUY_COVER_FLOW_KEY] = {
                "active": True,
                "step": "buy_cover_resume_choice",
                "data": bc_data,
            }
            session["temp_data"][DRAFT_POLICIES_FLOW_KEY] = {}
            await save_session(session)

            await _send_buttons(
                wa_id,
                "📋 *Incomplete Application Found*\n\n"
                "We've loaded your saved draft:\n\n"
                f"{hint}\n\n"
                "Would you like to continue where you left off?",
                [
                    {"id": "resume_yes", "title": "▶️ Resume"},
                    {"id": "resume_fresh", "title": "🆕 Start Fresh"},
                ],
                phone_number_id,
            )
            return

        if reply_id == "dp_delete":
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
                        intro="✅ Draft deleted successfully.",
                    )
                else:
                    flow["active"] = False
                    await save_session(session)
                    await _send_buttons(
                        wa_id,
                        "✅ Draft deleted successfully.\n\n"
                        "You have no more saved draft policies.",
                        [{"id": "welcome_purchase_policy", "title": "✈️ Buy Cover"}],
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

        # Unknown input at draft_action — re-show buttons
        selected_raw_r = drafts[idx - 1] if drafts and 0 < idx <= len(drafts) else {}
        detail_text_r = _format_draft_detail(selected_raw_r, idx, len(drafts))
        await _send_buttons(
            wa_id,
            detail_text_r + "\n\nWhat would you like to do?",
            [
                {"id": "dp_continue", "title": "✅ Continue"},
                {"id": "dp_delete", "title": "🗑️ Delete"},
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
            body="You have no saved draft policies.",
            phone_number_id=phone_number_id,
            source="draft_policies_flow",
        )
