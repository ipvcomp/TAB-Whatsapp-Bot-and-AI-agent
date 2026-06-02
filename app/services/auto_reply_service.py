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

GREETING_PATTERNS = [r"\b(hi+|hello+|hey+|hlo|helo|h[iy]+|assalam+|salam+|aoa|start|menu)\b"]
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
    {"type": "reply", "reply": {"id": "check_policy", "title": "📋 Check My Policy"}},
    {"type": "reply", "reply": {"id": "update_details","title": "✏️ Update My Details"}},
]

MENU_GROUP2_BUTTONS = [
    {"type": "reply", "reply": {"id": "welcome_draft_policies", "title": "📑 Draft Policies"}},
    {"type": "reply", "reply": {"id": "boarding_pass",          "title": "🛂 Boarding Pass"}},
    {"type": "reply", "reply": {"id": "help",                   "title": "🙋 Help"}},
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
    booking_ref = policy.get("booking_ref") or ""
    trip_type   = policy.get("trip_type") or ""
    origin      = policy.get("origin") or ""
    dest        = policy.get("dest") or ""
    departure   = policy.get("departure") or ""
    flight_num  = policy.get("flight_num") or ""
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
    lines.append(f"Flight No.          {flight_num or '—'}")
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

    # 2. Determine user existence and ACTIVE policy
    lookup_id = wa_id or to
    is_existing_user = False
    active_policy: dict | None = None

    if lookup_id:
        try:
            msisdn = get_msisdn(lookup_id)
            contact_rec = await get_contact_by_wa_id(lookup_id)
            is_existing_user = bool(contact_rec)

            if is_existing_user:
                try:
                    from app.services.ipurvey_api import fetch_policies_by_msisdn as _fetch_pols
                    policies = await _fetch_pols(msisdn)
                    active_pols = [
                        p for p in (policies or [])
                        if (p.get("status") or "").upper() in ("ACTIVE", "APPROVED", "ISSUED")
                    ]
                    if active_pols:
                        active_policy = active_pols[0]
                except Exception as exc:
                    logger.warning(f"[welcome] policy fetch failed for {lookup_id}: {exc}")
        except Exception as exc:
            logger.warning(f"[welcome] lookup failed for {lookup_id}: {exc}")

    # 3. Build welcome body
    if is_existing_user and active_policy:
        pol_code = active_policy.get("policyCode") or active_policy.get("policyReference") or ""
        product = active_policy.get("productName") or ""
        dep = active_policy.get("departureAirport") or ""
        arr = active_policy.get("arrivalAirport") or ""
        flight = active_policy.get("flightNumber") or ""
        dep_date_raw = (
            active_policy.get("departureDateLocal")
            or active_policy.get("departureDate")
            or ""
        )
        dep_date_fmt = dep_date_raw
        if dep_date_raw:
            for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
                try:
                    from datetime import datetime as _dt
                    dep_date_fmt = _dt.strptime(dep_date_raw, fmt).strftime("%d %b %Y")
                    break
                except ValueError:
                    pass

        card_lines = ["*YOUR LATEST POLICY*", ""]
        if pol_code:
            card_lines.append(f"Policy No.  {pol_code}")
        if product:
            card_lines.append(f"Product     {product}")
        if dep and arr:
            card_lines.append(f"Route       {dep} → {arr}")
        if flight:
            card_lines.append(f"Flight      {flight}")
        if dep_date_fmt:
            card_lines.append(f"Date        {dep_date_fmt}")
        card_lines.append("")
        card_lines.append("✅ Active")

        welcome_body = (
            "👋 *Welcome back!*\n"
            "*Welcome back to TravelAssist*\n\n"
            + "\n".join(card_lines)
        )
        logger.info(f"[welcome] is_existing=True active_policy={pol_code!r}")
    else:
        welcome_body = WELCOME_TEXT
        logger.info(f"[welcome] is_existing={is_existing_user} active_policy=None")

    await send_text_message(
        to=to,
        body=welcome_body,
        phone_number_id=phone_number_id,
        source="auto_reply",
    )

    # 4. Send full main menu buttons (always shown)
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
