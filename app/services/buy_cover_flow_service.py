import logging
from typing import Optional

import httpx

from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload

logger = logging.getLogger(__name__)

BUY_COVER_FLOW_KEY = "buy_cover_flow"

AIRPORTS = [
    ("LOS", "Lagos Murtala Muhammed"),
    ("ABV", "Abuja Nnamdi Azikiwe"),
    ("PHC", "Port Harcourt Intl"),
    ("KAN", "Kano Mallam Aminu"),
    ("ENU", "Enugu Akanu Ibiam"),
    ("ILR", "Ilorin"),
    ("CBQ", "Calabar Ekpo Intl"),
    ("SKO", "Sokoto"),
    ("YOL", "Yola"),
    ("QOW", "Owerri Sam Mbakwe"),
]


def is_in_buy_cover_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    return session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {}).get("active", False)


async def _get_flow_state(wa_id: str) -> tuple[dict, dict]:
    session = await get_session(wa_id) or {}
    flow = session.setdefault("temp_data", {}).setdefault(BUY_COVER_FLOW_KEY, {})
    return session, flow


async def _set_step(session: dict, step: str, wa_id: str):
    session["temp_data"][BUY_COVER_FLOW_KEY]["step"] = step
    session["temp_data"][BUY_COVER_FLOW_KEY]["active"] = True
    await save_session(session)


async def _save_data(session: dict, key: str, value, wa_id: str):
    session["temp_data"][BUY_COVER_FLOW_KEY].setdefault("data", {})[key] = value
    await save_session(session)


async def _reset(session: dict, wa_id: str):
    session["temp_data"][BUY_COVER_FLOW_KEY] = {}
    await save_session(session)


async def _send_text(to: str, body: str, phone_number_id: Optional[str]):
    await send_text_message(to=to, body=body, phone_number_id=phone_number_id, source="buy_cover_flow")


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
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="buy_cover_flow")


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
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="buy_cover_flow")


async def start_buy_cover_flow(
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session = await get_session(wa_id) or {}
    session.setdefault("temp_data", {})[BUY_COVER_FLOW_KEY] = {"active": True, "step": "buy_cover_who", "data": {}}
    if "user_id" not in session:
        session["user_id"] = wa_id
    await save_session(session)

    await _send_buttons(
        to=wa_id,
        body=(
            "✈️ Great choice — let's protect your trip!\n"
            "This will only take a few steps 😊\n\n"
            "Is this cover for:"
        ),
        buttons=[
            {"id": "cover_just_me", "title": "1. 🧑 Just me"},
            {"id": "cover_others",  "title": "2. 👥 Me & Others"},
        ],
        phone_number_id=phone_number_id,
    )


async def handle_buy_cover_flow(
    message,
    sender_wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session, flow = await _get_flow_state(sender_wa_id)
    step = flow.get("step", "buy_cover_who")
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

    # ── Who is covered ────────────────────────────────────────────────────────
    if step == "buy_cover_who":
        if reply_id == "cover_just_me":
            data["who"] = "just_me"
            flow["step"] = "buy_cover_name"
            await save_session(session)
            await _send_text(sender_wa_id, (
                "*👤 Please enter your name*\n"
                "Enter your first name and surname, as it appears on your ticket\n\n"
                "_Example: Yusuf Usman_"
            ), phone_number_id)
        else:
            data["who"] = "me_and_others"
            flow["step"] = "buy_cover_traveler_count"
            await save_session(session)
            await _send_list(
                sender_wa_id,
                "👨‍👩‍👧 *How many additional travelers are joining you?*\n_(Not counting yourself)_",
                "Select number",
                [{"title": "Additional Travelers", "rows": [
                    {"id": "others_1", "title": "1 — One other person"},
                    {"id": "others_2", "title": "2 — Two other people"},
                    {"id": "others_3", "title": "3 — Three others"},
                    {"id": "others_4", "title": "4 — Four others"},
                ]}],
                phone_number_id,
            )

    # ── Traveler count ────────────────────────────────────────────────────────
    elif step == "buy_cover_traveler_count":
        count_map = {"others_1": 1, "others_2": 2, "others_3": 3, "others_4": 4}
        others_count = count_map.get(reply_id, 1)
        data["others_count"] = others_count
        data["travelers"] = []
        flow["step"] = "buy_cover_name"
        await save_session(session)
        await _send_text(sender_wa_id, (
            "*👤 Lead traveler — please enter your name*\n"
            "Enter your first name and surname, as it appears on your ticket\n\n"
            "_Example: Yusuf Usman_"
        ), phone_number_id)

    # ── Name ──────────────────────────────────────────────────────────────────
    elif step == "buy_cover_name":
        if not text:
            await _send_text(sender_wa_id, "Please type your name to continue.", phone_number_id)
            return
        data["name"] = text
        if data.get("who") == "me_and_others":
            travelers = data.get("travelers", [])
            travelers.append(text)
            data["travelers"] = travelers
            data["others_collected"] = 0
            flow["step"] = "buy_cover_other_name"
            await save_session(session)
            others_count = data.get("others_count", 1)
            await _send_text(sender_wa_id, (
                f"*👤 Traveler 2 of {others_count + 1} — please enter their name*\n"
                "Enter first name and surname, as it appears on their ticket\n\n"
                "_Example: Amina Bello_"
            ), phone_number_id)
        else:
            flow["step"] = "buy_cover_email"
            await save_session(session)
            await _send_text(sender_wa_id, (
                "*📧 Please enter your email address*\n"
                "So we can send your policy documents\n\n"
                "_Example: yusuf@email.com_"
            ), phone_number_id)

    # ── Additional traveler names ──────────────────────────────────────────────
    elif step == "buy_cover_other_name":
        if not text:
            await _send_text(sender_wa_id, "Please type the traveler's name to continue.", phone_number_id)
            return
        travelers = data.get("travelers", [])
        travelers.append(text)
        data["travelers"] = travelers
        others_count = data.get("others_count", 1)
        others_collected = len(travelers) - 1
        if others_collected < others_count:
            next_num = others_collected + 2
            total = others_count + 1
            await save_session(session)
            await _send_text(sender_wa_id, (
                f"*👤 Traveler {next_num} of {total} — please enter their name*\n"
                "Enter first name and surname, as it appears on their ticket\n\n"
                "_Example: Amina Bello_"
            ), phone_number_id)
        else:
            flow["step"] = "buy_cover_email"
            await save_session(session)
            names_list = "\n".join(f"  {i+1}. {n}" for i, n in enumerate(travelers))
            await _send_text(sender_wa_id, (
                f"✅ *Got all {others_count + 1} travelers:*\n{names_list}\n\n"
                "*📧 Please enter your email address*\n"
                "So we can send your policy documents\n\n"
                "_Example: yusuf@email.com_"
            ), phone_number_id)

    # ── Email ─────────────────────────────────────────────────────────────────
    elif step == "buy_cover_email":
        if not text:
            await _send_text(sender_wa_id, "Please type your email address to continue.", phone_number_id)
            return
        data["email"] = text
        flow["step"] = "buy_cover_trip_type"
        await save_session(session)
        await _send_buttons(
            sender_wa_id,
            "🗺️ What type of trip is this?",
            [
                {"id": "trip_oneway", "title": "1. 🗺️ One-way"},
                {"id": "trip_return", "title": "2. 🔄 Return"},
            ],
            phone_number_id,
        )

    # ── Trip type ─────────────────────────────────────────────────────────────
    elif step == "buy_cover_trip_type":
        trip = "One-way 🗺️" if reply_id == "trip_oneway" else "Return 🔄"
        data["trip_type"] = trip
        flow["step"] = "buy_cover_booking_ref"
        await save_session(session)
        await _send_text(sender_wa_id,
            "*🎫 Please enter your booking reference*\n\n_Examples: AB1XY2, 2990FA62_",
            phone_number_id)

    # ── Booking reference ─────────────────────────────────────────────────────
    elif step == "buy_cover_booking_ref":
        if not text:
            await _send_text(sender_wa_id, "Please enter your booking reference to continue.", phone_number_id)
            return
        data["booking_ref"] = text.upper()
        flow["step"] = "buy_cover_flight_num"
        await save_session(session)
        await _send_text(sender_wa_id,
            "*✈️ Please enter your flight number*\n\n_Example: P47123, Q1402_",
            phone_number_id)

    # ── Flight number ─────────────────────────────────────────────────────────
    elif step == "buy_cover_flight_num":
        if not text:
            await _send_text(sender_wa_id, "Please enter your flight number to continue.", phone_number_id)
            return
        data["flight_num"] = text.upper()
        flow["step"] = "buy_cover_date"
        await save_session(session)
        await _send_text(sender_wa_id,
            "*📅 What date are you flying?*\n\n_Example: 12 April 2026, 12/04/2026_",
            phone_number_id)

    # ── Flying date ───────────────────────────────────────────────────────────
    elif step == "buy_cover_date":
        if not text:
            await _send_text(sender_wa_id, "Please enter your flying date to continue.", phone_number_id)
            return
        data["date"] = text
        flow["step"] = "buy_cover_depart_time"
        await save_session(session)
        await _send_text(sender_wa_id,
            "*⏰ What time is your flight scheduled to depart?*\nExample: 13:40, 1:40 PM",
            phone_number_id)

    # ── Departure time ────────────────────────────────────────────────────────
    elif step == "buy_cover_depart_time":
        if not text:
            await _send_text(sender_wa_id, "Please enter your departure time to continue.", phone_number_id)
            return
        data["depart_time"] = text
        flow["step"] = "buy_cover_depart_airport_pick"
        await save_session(session)
        await _send_list(
            sender_wa_id,
            "*✈️ What airport are you flying from?*",
            "Select airport",
            [{"title": "🛫 Departure Airports", "rows": [
                {"id": f"dep_{code}", "title": f"{code}  {name}"[:24]}
                for code, name in AIRPORTS
            ]}],
            phone_number_id,
        )

    # ── Departure airport ─────────────────────────────────────────────────────
    elif step == "buy_cover_depart_airport_pick":
        if not reply_id or not reply_id.startswith("dep_"):
            await _send_list(
                sender_wa_id,
                "*✈️ What airport are you flying from?*",
                "Select airport",
                [{"title": "🛫 Departure Airports", "rows": [
                    {"id": f"dep_{code}", "title": f"{code}  {name}"[:24]}
                    for code, name in AIRPORTS
                ]}],
                phone_number_id,
            )
            return
        code = reply_id.replace("dep_", "")
        name = next((n for c, n in AIRPORTS if c == code), code)
        data["depart_airport"] = f"{code} — {name}"
        flow["step"] = "buy_cover_arrive_time"
        await save_session(session)
        await _send_text(sender_wa_id,
            "*⏰ What time is your flight scheduled to arrive?*\nExample: 15:00, 3:00 PM",
            phone_number_id)

    # ── Arrival time ──────────────────────────────────────────────────────────
    elif step == "buy_cover_arrive_time":
        if not text:
            await _send_text(sender_wa_id, "Please enter your arrival time to continue.", phone_number_id)
            return
        data["arrive_time"] = text
        flow["step"] = "buy_cover_arrive_airport_pick"
        await save_session(session)
        await _send_list(
            sender_wa_id,
            "*✈️ What airport are you arriving at?*",
            "Select airport",
            [{"title": "🛬 Arrival Airports", "rows": [
                {"id": f"arr_{code}", "title": f"{code}  {name}"[:24]}
                for code, name in AIRPORTS
            ]}],
            phone_number_id,
        )

    # ── Arrival airport ───────────────────────────────────────────────────────
    elif step == "buy_cover_arrive_airport_pick":
        if not reply_id or not reply_id.startswith("arr_"):
            await _send_list(
                sender_wa_id,
                "*✈️ What airport are you arriving at?*",
                "Select airport",
                [{"title": "🛬 Arrival Airports", "rows": [
                    {"id": f"arr_{code}", "title": f"{code}  {name}"[:24]}
                    for code, name in AIRPORTS
                ]}],
                phone_number_id,
            )
            return
        code = reply_id.replace("arr_", "")
        name = next((n for c, n in AIRPORTS if c == code), code)
        data["arrive_airport"] = f"{code} — {name}"
        flow["step"] = "buy_cover_airline"
        await save_session(session)
        await _send_text(sender_wa_id,
            "*🏢 Who are you flying with?*\n\n_Example: Itoim Air, Air Peace_",
            phone_number_id)

    # ── Airline ───────────────────────────────────────────────────────────────
    elif step == "buy_cover_airline":
        if not text:
            await _send_text(sender_wa_id, "Please enter the airline name to continue.", phone_number_id)
            return
        data["airline"] = text
        dep = data.get("depart_airport", "").split("—")[0].strip()
        arr = data.get("arrive_airport", "").split("—")[0].strip()
        travelers = data.get("travelers", [])
        traveler_line = (
            "  ".join(f"{i+1} — {n}" for i, n in enumerate(travelers))
            if travelers else f"1 — {data.get('name', '')}"
        )
        summary = (
            "*✈️ YOUR TRIP*\n\n"
            f"Airline\t\t\t*{data.get('airline', '')}*\n"
            f"Route\t\t\t*{dep} → {arr}*\n"
            f"Flight\t\t\t*{data.get('flight_num', '')}*\n"
            f"Date\t\t\t*{data.get('date', '')}*\n"
            f"Departs\t\t\t*{data.get('depart_time', '')}*\n"
            f"Arrives\t\t\t*{data.get('arrive_time', '')}*\n"
            f"Travellers\t*{traveler_line}*"
        )
        flow["step"] = "buy_cover_summary"
        await save_session(session)
        await _send_list(
            sender_wa_id,
            summary,
            "Select option",
            [{"title": "Options", "rows": [
                {"id": "summary_confirm", "title": "✅ Confirm"},
                {"id": "summary_edit",    "title": "✏️ Edit trip details"},
            ]}],
            phone_number_id,
            header="📋 Trip Summary",
        )

    # ── Trip summary ──────────────────────────────────────────────────────────
    elif step == "buy_cover_summary":
        if reply_id == "summary_edit":
            await _reset(session, sender_wa_id)
            await start_buy_cover_flow(sender_wa_id, phone_number_id)
            return
        data["cover"] = "Local Travel Premium 🔥"
        flow["step"] = "buy_cover_next_steps"
        await save_session(session)
        await _send_list(
            sender_wa_id,
            (
                "📋 *Local Travel Basic*\n"
                "🛡️ Your trip can be protected against:\n"
                "✅ Major delay\n✅ Cancellation\n✅ Covered travel disruption\n\n"
                "*₦2,500*\n🏢 Tangerine Insurance  •  ⏱️ Single trip\n\n"
                "📋 *Local Travel Premium* 🔥 *POPULAR*\n"
                "🛡️ Your trip can be protected against:\n"
                "✅ Major delay\n✅ Cancellation\n✅ Covered travel disruption\n\n"
                "*₦3,500*\n🏢 Tangerine Insurance  •  ⏱️ Multi Trip\n\n"
                "🎁 *With TravelAssist you get:*\n"
                "📄 Policy on WhatsApp\n🔔 Flight alerts\n🤝 Support if disruption happens\n\n"
                "What would you like to do next?"
            ),
            "Select option",
            [{"title": "Next Steps", "rows": [
                {"id": "next_kyc",    "title": "1. 🗂️ Continue to KYC"},
                {"id": "next_ask",    "title": "2. ❓ Ask a question"},
                {"id": "next_cancel", "title": "3. ❌ Cancel purchase"},
            ]}],
            phone_number_id,
            header="🛡️ Select from available cover(s)",
        )

    # ── Next steps ────────────────────────────────────────────────────────────
    elif step == "buy_cover_next_steps":
        if reply_id == "next_kyc":
            from app.services.kyc_flow_service import start_kyc_flow
            await start_kyc_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        elif reply_id == "next_ask":
            flow["step"] = "buy_cover_ask_question"
            await save_session(session)
            await _send_text(sender_wa_id, (
                "❓ *What would you like to know?*\n\n"
                "💬 *Common questions:*\n"
                "🛡️ What does the cover include?\n"
                "⏰ How long does a delay need to be?\n"
                "💰 How and when will I get paid out?\n"
                "📋 Can I get a refund on my policy?\n\n"
                "_Type your question here..._"
            ), phone_number_id)
        elif reply_id == "next_cancel":
            flow["step"] = "buy_cover_cancel_confirm"
            await save_session(session)
            await _send_list(
                sender_wa_id,
                "Are you sure you want to cancel? Your trip details will not be saved.\n\nPlease confirm:",
                "Choose",
                [{"title": "Confirm", "rows": [
                    {"id": "cancel_yes", "title": "❌ Yes, cancel purchase"},
                    {"id": "cancel_no",  "title": "↩️ No, go back to quote"},
                ]}],
                phone_number_id,
                header="❌ Cancel Purchase",
            )
        else:
            await _send_list(
                sender_wa_id,
                "What would you like to do next?",
                "Select option",
                [{"title": "Next Steps", "rows": [
                    {"id": "next_kyc",    "title": "1. 🗂️ Continue to KYC"},
                    {"id": "next_ask",    "title": "2. ❓ Ask another"},
                    {"id": "next_cancel", "title": "3. ❌ Cancel purchase"},
                ]}],
                phone_number_id,
                header="🛡️ Select from available cover(s)",
            )

    # ── Ask a question ────────────────────────────────────────────────────────
    elif step == "buy_cover_ask_question":
        q = text.lower()
        if "delay" in q or "hours" in q or "long" in q:
            answer = (
                "💬 *Great question!*\n\nFor Local Travel Basic and Premium, a payout is triggered "
                "when your flight is delayed by *3 hours or more* from the scheduled departure time.\n\n"
                "TravelAssist monitors your flight automatically.\n\nReady to continue?"
            )
        elif "include" in q or "cover" in q:
            answer = (
                "💬 *Great question!*\n\nYour cover includes:\n"
                "✅ Major flight delay (3+ hours)\n✅ Flight cancellation\n"
                "✅ Covered travel disruption\n\nReady to continue?"
            )
        elif "refund" in q:
            answer = (
                "💬 *Great question!*\n\nYou can request a refund within 14 days of purchase "
                "if your flight has not departed.\n\nReady to continue?"
            )
        elif "paid" in q or "payout" in q or "when" in q:
            answer = (
                "💬 *Great question!*\n\nPayouts are processed automatically within 24–48 hours "
                "of a confirmed disruption. No claim forms needed.\n\nReady to continue?"
            )
        else:
            answer = (
                "💬 *Great question!*\n\nFor detailed policy information, please visit *www.ipurvey.com*\n\n"
                "Ready to continue?"
            )
        flow["step"] = "buy_cover_next_steps"
        await save_session(session)
        await _send_list(
            sender_wa_id,
            answer,
            "Choose an option",
            [{"title": "Next Steps", "rows": [
                {"id": "next_kyc",    "title": "1. 🗂️ Continue to KYC"},
                {"id": "next_ask",    "title": "2. ❓ Ask another"},
                {"id": "next_cancel", "title": "3. ❌ Cancel purchase"},
            ]}],
            phone_number_id,
        )

    # ── Cancel confirm ────────────────────────────────────────────────────────
    elif step == "buy_cover_cancel_confirm":
        if reply_id == "cancel_yes":
            await _reset(session, sender_wa_id)
            await _send_list(
                sender_wa_id,
                "No worries — you can come back anytime to protect your trip.",
                "Choose",
                [{"title": "Options", "rows": [
                    {"id": "restart_buy", "title": "1. ✈️ Start a new cover"},
                    {"id": "go_main",     "title": "2. 🏠 Main menu"},
                ]}],
                phone_number_id,
                header="✅ Purchase cancelled",
            )
        else:
            flow["step"] = "buy_cover_next_steps"
            await save_session(session)
            await _send_list(
                sender_wa_id,
                "What would you like to do next?",
                "Choose an option",
                [{"title": "Next Steps", "rows": [
                    {"id": "next_kyc",    "title": "1. 🗂️ Continue to KYC"},
                    {"id": "next_ask",    "title": "2. ❓ Ask a question"},
                    {"id": "next_cancel", "title": "3. ❌ Cancel purchase"},
                ]}],
                phone_number_id,
            )
