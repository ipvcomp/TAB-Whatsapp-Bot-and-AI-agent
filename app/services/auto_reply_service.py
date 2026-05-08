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
from app.services.ipurvey_api import fetch_policies_by_msisdn
from app.services.session_service import get_session
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
    """Format a rich draft policy card for the welcome-back message."""
    raw_id     = policy.get("id") or "—"
    display_id = (raw_id[:20] + "...") if len(raw_id) > 20 else raw_id

    booking_ref = policy.get("booking_ref") or ""
    trip_type   = policy.get("trip_type") or ""
    origin      = policy.get("origin") or ""
    dest        = policy.get("dest") or ""
    departure   = policy.get("departure") or ""
    passengers  = policy.get("passengers") or []
    status      = (policy.get("status") or "DRAFT").upper()

    if status in ("ACTIVE", "APPROVED", "ISSUED"):
        badge = "✅ Active"
    elif status in ("SUBMITTED", "CONFIRMED", "PAID"):
        badge = "✅ Submitted"
    elif status in ("PENDING", "PROCESSING", "PENDING_PAYMENT"):
        badge = "⏳ Pending"
    elif status == "CANCELLED":
        badge = "❌ Cancelled"
    elif status in ("EXPIRED", "LAPSED"):
        badge = "⏰ Expired"
    elif status == "NEW":
        badge = "🆕 New"
    elif status == "DRAFT":
        badge = "🔄 Draft"
    else:
        badge = f"📋 {status.title()}"

    lines = ["*YOUR SAVED POLICY (DRAFT)*", ""]
    lines.append(f"Policy ID        {display_id}")
    if booking_ref:
        lines.append(f"Booking Ref      {booking_ref}")
    if trip_type:
        lines.append(f"Trip Type        {trip_type}")
    if origin:
        lines.append(f"From             {origin}")
    if dest:
        lines.append(f"To               {dest}")
    if departure:
        lines.append(f"Departure        {departure}")
    if passengers:
        lines.append(f"Passengers       👤 {passengers[0]}")
        for pname in passengers[1:]:
            lines.append(f"                 👤 {pname}")
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

    # 2. Determine if user has an active draft — only then show welcome-back card
    lookup_id       = wa_id or to
    is_existing_user = False
    draft_policy_id  = ""
    draft_info: dict | None = None
    if lookup_id:
        try:
            msisdn = get_msisdn(lookup_id)
            results = await asyncio.gather(
                fetch_policies_by_msisdn(msisdn),
                _ipurvey_svc.resume_draft_policy(msisdn),
                return_exceptions=True,
            )
            policies   = results[0] if not isinstance(results[0], Exception) else []
            draft_info = results[1] if not isinstance(results[1], Exception) else None

            # Only show welcome-back card when there is an active draft policy
            if draft_info and isinstance(draft_info, dict):
                draft_policy_id  = draft_info.get("policy_id") or ""
                is_existing_user = bool(draft_policy_id)

            logger.info(
                f"[welcome] has_draft={is_existing_user} draft_id={draft_policy_id!r} "
                f"policies={len(policies) if isinstance(policies, list) else 0}"
            )
        except Exception as exc:
            logger.warning(f"[welcome] lookup failed for {lookup_id}: {exc}")

    # 2b. Build card data from draft API response
    if is_existing_user and draft_info:
        itinerary  = draft_info.get("itinerary") or {}
        legs       = itinerary.get("legs", [])
        leg        = legs[0] if legs else {}
        pax_list   = draft_info.get("passengers") or []
        pax_names  = [
            f"{p.get('firstName', '')} {p.get('surname', '')}".strip()
            for p in pax_list
            if p.get("firstName") or p.get("surname")
        ]
        trip_raw   = draft_info.get("trip_type", "")
        dep_date   = leg.get("departureDate", "")
        dep_time   = leg.get("departureTime", "")
        # Format departure as "15 May 2026 · 08:30" when both available
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
            "One Way" if "ONE" in trip_raw.upper()
            else "Return" if trip_raw
            else ""
        )
        card_data: dict = {
            "id":          draft_policy_id,
            "booking_ref": itinerary.get("bookingReference", ""),
            "trip_type":   trip_label,
            "origin":      leg.get("departureAirport", ""),
            "dest":        leg.get("arrivalAirport", ""),
            "departure":   dep_display,
            "passengers":  pax_names,
            "status":      "DRAFT",
        }

        welcome_body = (
            "👋 *Welcome back!*\n"
            "*Welcome back to TravelAssist*\n"
            "We've resumed your saved policy draft.\n\n"
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

    # 3. Send button group 1: Buy Cover, Boarding Pass, Check My Policy
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

    # 4. Send button group 2: Check Eligibility, Update Details, Help
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

    # 5. Send utility bar
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
