import logging
import re
from typing import Optional

from app.services.whatsapp_service import send_text_message, send_whatsapp_payload

logger = logging.getLogger(__name__)

GREETING_PATTERNS = [r"\b(hi|hello|hey|assalam|salam|aoa|start|menu)\b"]
HELP_PATTERNS = [r"\b(help|support|assist)\b"]
THANKS_PATTERNS = [r"\b(thank|thanks|shukria|shukriya)\b"]
BYE_PATTERNS = [r"\b(bye|goodbye|see you|khuda hafiz)\b"]

WELCOME_BODY = (
    "Hi, Welcome to *iPurvey!*\n"
    "\U0001F44B Your Trusted Partner for Travel Disruption Compensation!\n"
    "My name is *TravelAssist*.\n"
    "Please select an option below so I can assist you!\n"
    "\U0001F447\n\n"
    "Please type *#shortcuts*, for navigation menu\n"
    "iPurvey.com"
)

HELP_REPLY = (
    "We're here to help! \U0001F64F\n\n"
    "You can:\n"
    "\u2022 Type *policy* to create a new travel policy\n"
    "\u2022 Ask any question about travel insurance\n"
    "\u2022 Type *#shortcuts* for the full navigation menu\n\n"
    "How can we assist you today?"
)

THANKS_REPLY = "You're welcome! \U0001F60A If you need anything else, feel free to reach out anytime."

BYE_REPLY = "Goodbye! \U0001F44B Have a great day. We're always here when you need us!"

DEFAULT_REPLY = (
    "Thank you for your message! \U0001F64F\n\n"
    "I'm *TravelAssist*, your travel insurance companion.\n"
    "You can:\n"
    "\u2022 Type *policy* to create a new travel policy\n"
    "\u2022 Ask me any question about travel insurance\n"
    "\u2022 Type *hi* to see the main menu\n\n"
    "How can I help you?"
)

MEDIA_REPLY = "Thanks for sending that! \U0001F4CE We've received your media. Our team will review it shortly."


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


async def send_welcome_message(
    to: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str],
) -> Optional[dict]:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": WELCOME_BODY
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "welcome_purchase_policy",
                            "title": "Purchase Policy"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "welcome_submit_boarding",
                            "title": "Submit Boarding Pass"
                        }
                    }
                ]
            }
        }
    }
    return await send_whatsapp_payload(
        whatsapp_payload=payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="auto_reply",
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
