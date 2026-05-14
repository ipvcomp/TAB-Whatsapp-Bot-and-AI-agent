import asyncio
import logging
import re
from typing import Optional

from app.services.whatsapp_service import (
    send_text_message,
    send_whatsapp_payload,
    get_welcome_image_media_id,
)
from app.core.test_overrides import get_msisdn
from app.services.session_service import get_session
from app.services.contact_service import get_contact_by_wa_id
import app.services.ipurvey_service as _ipurvey_svc

logger = logging.getLogger(__name__)

GREETING_PATTERNS = [r"\b(hi|hello|hey|assalam|salam|aoa|start|menu)\b"]
HELP_PATTERNS = [r"\b(help|support|assist)\b"]
THANKS_PATTERNS = [r"\b(thank|thanks|shukria|shukriya)\b"]
BYE_PATTERNS = [r"\b(bye|goodbye|see you|khuda hafiz)\b"]

WELCOME_TEXT = (
    "👋 *Welcome to TravelAssist*\n"
    "We help travelers:\n"
    "✈️ buy travel disruption cover\n"
    "🔔 get payout alerts for travel  disruptions"
)

MENU_GROUP1_BUTTONS = [
    {"type": "reply", "reply": {"id": "buy_cover",    "title": "✈️ Buy Cover"}},
    {"type": "reply", "reply": {"id": "boarding_pass","title": "🛫 Boarding Pass"}},
    {"type": "reply", "reply": {"id": "check_policy", "title": "📋 Check My Policy"}},
]

MENU_GROUP2_BUTTONS = [
    {"type": "reply", "reply": {"id": "check_eligibility", "title": "🔍 Check Eligibility"}},
    {"type": "reply", "reply": {"id": "update_details",    "title": "✏️ Update Details"}},
    {"type": "reply", "reply": {"id": "help",              "title": "🆘 Help"}},
]

UTILITY_TEXT = (
    "*Utility options:*\n"
    "0 ↩️ Back  |  9 🆘 Help  |  00 🏠 Main menu\n"
    "99 ❌ Cancel/Exit"
)

HELP_REPLY = (
    "We're here to help! \U0001f64f\n\n"
    "You can:\n"
    "\u2022 Type *policy* to create a new travel policy\n"
    "\u2022 Ask any question about travel insurance\n"
    "\u2022 Type *hi* for the main menu\n\n"
    "How can we assist you today?"
)

THANKS_REPLY = "You're welcome! \U0001f60a If you need anything else, feel free to reach out anytime."

BYE_REPLY = "Goodbye! \U0001f44b Have a great day. We're always here when you need us!"

DEFAULT_REPLY = (
    "Thank you for your message! \U0001f64f\n\n"
    "I'm *TravelAssist*, your travel insurance companion.\n"
    "You can:\n"
    "\u2022 Type *policy* to create a new travel policy\n"
    "\u2022 Ask me any question about travel insurance\n"
    "\u2022 Type *hi* to see the main menu\n\n"
    "How can I help you?"
)

MEDIA_REPLY = "Thanks for sending that! \U0001f4ce We've received your media. Our team will review it shortly."


def is_greeting(text: str) -> bool:
    if not text:
        return False
    for pattern in GREETING_PATTERNS:
        if re.search(pattern, text.lower().strip(), re.IGNORECASE):
            return True
    return False


def _match_simple_reply(text: str) -> Optional[str]:
    if not text:
        return None
    text_lower = text.lower().strip()
    for pattern in THANKS_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return THANKS_REPLY
    for pattern in BYE_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return BYE_REPLY
    for pattern in HELP_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return HELP_REPLY
    return None


def _format_policy_card(policy: dict) -> str:
    """Format a rich draft policy card for the welcome-back message.
    All fields are always shown; missing values display as —."""
    policy_id   = policy.get("policy_id") or ""
    booking_ref = policy.get("booking_ref") or ""
    trip_type   = policy.get("trip_type") or ""
    origin      = policy.get("origin") or ""
    dest        = policy.get("dest") or ""
    departure   = policy.get("departure") or ""
    passengers  = policy.get("passengers") or []
    status      = (policy.get("status") or "NONE").upper()

    if status == "DRAFT":
        badge = "🔄 Draft"
    elif status in ("ACTIVE", "APPROVED", "ISSUED"):
        badge = "✅ Active"
    elif status in ("SUBMITTED", "CONFIRMED", "PAID"):
        badge = "✅ Submitted"
    elif status in ("PENDING", "PROCESSING", "PENDING_PAYMENT"):
        badge = "⏳ Pending"
    elif status == "CANCELLED":
        badge = "❌ Cancelled"
    elif status in ("EXPIRED", "LAPSED"):
        badge = "⏰ Expired"
    elif status == "NONE":
        badge = "📋 No active draft"
    else:
        badge = f"📋 {status.title()}"

    lines = ["*YOUR SAVED POLICY (DRAFT)*", ""]
    lines.append(f"Booking Reference   {booking_ref or '—'}")
    lines.append(f"Trip Type           {trip_type or '—'}")
    lines.append(f"From                {origin or '—'}")
    lines.append(f"To                  {dest or '—'}")
    lines.append(f"Departure           {departure or '—'}")
    if passengers:
        lines.append(f"Passengers          👤 {passengers[0]}")
        for pname in passengers[1:]:
            lines.append(f"                    👤 {pname}")
    else:
        lines.append("Passengers          —")
    lines.append("")
    lines.append(badge)
    return "\n".join(lines)


async def send_welcome_message(
    to: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str],
    wa_id: Optional[str] = None,
) -> Optional[dict]:
    image_media_id = await get_welcome_image_media_id()
    logger.info(f"Welcome image media_id: {image_media_id}")

    # 1. Send image as a standalone message
    if image_media_id:
        image_payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "image",
            "image": {"id": image_media_id},
        }
        await send_whatsapp_payload(
            whatsapp_payload=image_payload,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="auto_reply",
        )
        await asyncio.sleep(1.5)

    # 2. Determine user existence (contact DB) and draft status in parallel
    lookup_id        = wa_id or to
    is_existing_user = False
    has_draft        = False
    draft_policy_id  = ""
    draft_info: dict | None = None
    if lookup_id:
        try:
            msisdn = get_msisdn(lookup_id)
            results = await asyncio.gather(
                get_contact_by_wa_id(lookup_id),
                _ipurvey_svc.resume_draft_policy(msisdn),
                return_exceptions=True,
            )
            contact_rec = results[0] if not isinstance(results[0], Exception) else None
            draft_info  = results[1] if not isinstance(results[1], Exception) else None

            # Existing user = contact record found in DB
            is_existing_user = bool(contact_rec)

            if draft_info and isinstance(draft_info, dict):
                draft_policy_id = draft_info.get("policy_id") or ""
                has_draft       = bool(draft_policy_id)
                # Fallback: treat as existing if only draft found (first-time edge case)
                if not is_existing_user and has_draft:
                    is_existing_user = True

            logger.info(
                f"[welcome] is_existing={is_existing_user} has_draft={has_draft} "
                f"draft_id={draft_policy_id!r}"
            )
        except Exception as exc:
            logger.warning(f"[welcome] lookup failed for {lookup_id}: {exc}")

    # 2b. Build card data (draft fields if available, else all — for existing users)
    card_data: dict | None = None
    if is_existing_user:
        if has_draft and draft_info:
            itinerary = draft_info.get("itinerary") or {}
            legs      = itinerary.get("legs", [])
            leg       = legs[0] if legs else {}
            pax_list  = draft_info.get("passengers") or []
            pax_names = [
                f"{p.get('firstName', '')} {p.get('surname', '')}".strip()
                for p in pax_list
                if p.get("firstName") or p.get("surname")
            ]
            trip_raw  = draft_info.get("trip_type", "")
            dep_date  = leg.get("departureDate", "")
            dep_time  = leg.get("departureTime", "")

            # Format departure as "15 May 2026 · 08:30"
            if dep_date and dep_time:
                try:
                    from datetime import datetime as _dt
                    dep_display = _dt.strptime(dep_date, "%Y-%m-%d").strftime("%d %b %Y") + f" · {dep_time}"
                except ValueError:
                    dep_display = f"{dep_date} · {dep_time}"
            elif dep_date:
                try:
                    from datetime import datetime as _dt
                    dep_display = _dt.strptime(dep_date, "%Y-%m-%d").strftime("%d %b %Y")
                except ValueError:
                    dep_display = dep_date
            else:
                dep_display = ""

            trip_label = (
                "One Way" if trip_raw and "ONE" in trip_raw.upper()
                else "Return" if trip_raw
                else ""
            )

            dep_code = leg.get("departureAirport", "")
            dep_name = leg.get("departureAirportName", "")
            arr_code = leg.get("arrivalAirport", "")
            arr_name = leg.get("arrivalAirportName", "")
            origin_display = f"{dep_code} ({dep_name})" if dep_name else dep_code
            dest_display   = f"{arr_code} ({arr_name})" if arr_name else arr_code

            card_data = {
                "policy_id":   draft_policy_id,
                "booking_ref": itinerary.get("bookingReference", ""),
                "trip_type":   trip_label,
                "origin":      origin_display,
                "dest":        dest_display,
                "departure":   dep_display,
                "passengers":  pax_names,
                "status":      "DRAFT",
            }
        # else: existing user but no draft — leave card_data as None → generic welcome

    # 2c. Build welcome body
    if is_existing_user and has_draft and card_data:
        welcome_body = (
            "👋 *Welcome back!*\n"
            "*Welcome back to TravelAssist*\n"
            "We've resumed your saved policy draft.\n\n"
            + _format_policy_card(card_data)
        )
    elif is_existing_user and card_data:
        welcome_body = (
            "👋 *Welcome back!*\n"
            "*Welcome back to TravelAssist*\n\n"
            + _format_policy_card(card_data)
        )
    else:
        welcome_body = WELCOME_TEXT

    await send_text_message(
        to=to,
        body=welcome_body,
        phone_number_id=phone_number_id,
        source="auto_reply",
    )

    # 3. When draft exists: show Continue / Discard buttons instead of main menu
    if is_existing_user and has_draft:
        draft_action_payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": "What would you like to do?"},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": "welcome_continue_draft", "title": "▶️ Continue"}},
                        {"type": "reply", "reply": {"id": "welcome_discard_draft",  "title": "🗑 Discard Draft"}},
                    ]
                },
            },
        }
        result = await send_whatsapp_payload(
            whatsapp_payload=draft_action_payload,
            phone_number_id=phone_number_id,
            source="auto_reply",
        )
        return result

    # 4. No draft: send full main menu (group 1 + group 2 + utility)
    group1_payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "What would you like to do?"},
            "action": {"buttons": MENU_GROUP1_BUTTONS},
        },
    }
    await send_whatsapp_payload(
        whatsapp_payload=group1_payload,
        phone_number_id=phone_number_id,
        source="auto_reply",
    )

    group2_payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "More options:"},
            "action": {"buttons": MENU_GROUP2_BUTTONS},
        },
    }
    result = await send_whatsapp_payload(
        whatsapp_payload=group2_payload,
        phone_number_id=phone_number_id,
        source="auto_reply",
    )

    await send_text_message(
        to=to,
        body=UTILITY_TEXT,
        phone_number_id=phone_number_id,
        source="auto_reply",
    )

    return result


async def send_main_menu(
    to: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
    wa_id: Optional[str] = None,
) -> Optional[dict]:
    return await send_welcome_message(
        to=to,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        wa_id=wa_id,
    )


async def handle_auto_reply(
    to_wa_id: str,
    incoming_text: Optional[str],
    message_type: str,
    phone_number_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
) -> Optional[dict]:
    if message_type != "text":
        result = await send_text_message(
            to=to_wa_id,
            body=MEDIA_REPLY,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="auto_reply",
        )
    elif is_greeting(incoming_text):
        result = await send_welcome_message(
            to=to_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            wa_id=to_wa_id,
        )
    else:
        simple_reply = _match_simple_reply(incoming_text)
        reply_text = simple_reply if simple_reply else DEFAULT_REPLY

        result = await send_text_message(
            to=to_wa_id,
            body=reply_text,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="auto_reply",
        )

    if result:
        logger.info(f"Auto-reply sent to {to_wa_id}")
    else:
        logger.error(f"Failed to send auto-reply to {to_wa_id}")

    return result
