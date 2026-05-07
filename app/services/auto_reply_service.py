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
    # policy_id = internal UUID from API ("id" field)
    # policy_code = human-readable reference ("ref" / policyCode field)
    policy_id   = policy.get("id") or ""
    policy_code = policy.get("ref") or ""
    # Show policy_id (UUID) as primary; fall back to policy_code if id absent
    display_id  = policy_id if policy_id and policy_id != policy_code else policy_code or "—"

    flight  = policy.get("flight") or ""
    airline = policy.get("airline") or ""
    date    = policy.get("date") or ""
    origin  = policy.get("origin") or ""
    dest    = policy.get("dest") or ""
    status  = (policy.get("status") or "Active").upper()

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
        badge = "🔄 In Progress"
    elif status in ("NONE", ""):
        badge = "📋 No active draft"
    else:
        badge = f"📋 {status.title()}"

    lines = ["*YOUR ACTIVE POLICY*", f"Policy ID   {display_id}"]

    # Show policy code separately only when it differs from id
    if policy_code and policy_code != policy_id and policy_code != "—":
        lines.append(f"Policy No.  {policy_code}")

    if flight:
        fl_line = f"{flight} · {airline}".strip(" ·") if airline else flight
        lines.append(f"Flight        {fl_line}")
    else:
        lines.append("Flight        —")

    if date:
        lines.append(f"Date          {date}")
    else:
        lines.append("Date          —")

    if origin or dest:
        route = f"{origin} → {dest}".strip(" →") if (origin and dest) else (origin or dest)
        lines.append(f"Route         {route}")

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

    # 2. Determine existing vs fresh user, and get draft policy ID
    # Run both API calls in parallel for speed
    lookup_id = wa_id or to
    is_existing_user = False
    draft_policy_id  = ""
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

            # ANY policy on record = returning/existing user → show welcome-back card
            is_existing_user = bool(policies)

            # Extract policyId from draft resume response
            if draft_info and isinstance(draft_info, dict):
                draft_policy_id = draft_info.get("policy_id") or ""

            logger.info(
                f"[welcome] existing={is_existing_user} draft_id={draft_policy_id!r} "
                f"policies={len(policies)}"
            )
        except Exception as exc:
            logger.warning(f"[welcome] lookup failed for {lookup_id}: {exc}")

    # 2b. Build card data — policy ID from draft, flight/date from session
    if is_existing_user:
        card_data: dict = {
            "id":      draft_policy_id or "—",
            "ref":     "",
            "flight":  "",
            "airline": "",
            "date":    "",
            "origin":  "",
            "dest":    "",
            "status":  "DRAFT" if draft_policy_id else "NONE",
        }
        try:
            session = await get_session(lookup_id)
            if session:
                api_data = session.get("api_data") or {}
                bc_data  = (
                    session.get("temp_data", {}).get("buy_cover_flow", {}).get("data") or {}
                )
                pc_bc    = (session.get("paused_context") or {}).get("buy_cover_data") or {}
                if not isinstance(pc_bc, dict):
                    pc_bc = {}

                def _first(*values):
                    for v in values:
                        sv = str(v).strip() if v else ""
                        if sv and sv != "—":
                            return sv
                    return ""

                card_data["flight"]  = _first(
                    api_data.get("flight_no"), bc_data.get("flight_no"), pc_bc.get("flight_no")
                )
                card_data["date"]    = _first(
                    api_data.get("dep_date"), bc_data.get("dep_date"), pc_bc.get("dep_date")
                )
                card_data["origin"]  = _first(
                    api_data.get("dep_airport_code"), bc_data.get("dep_airport_code"),
                    pc_bc.get("dep_airport_code")
                )
                card_data["dest"]    = _first(
                    api_data.get("arr_airport_code"), bc_data.get("arr_airport_code"),
                    pc_bc.get("arr_airport_code")
                )
                card_data["airline"] = _first(
                    api_data.get("airline"), bc_data.get("airline"), pc_bc.get("airline")
                )
        except Exception as exc:
            logger.warning(f"[welcome] session enrichment failed for {lookup_id}: {exc}")

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
