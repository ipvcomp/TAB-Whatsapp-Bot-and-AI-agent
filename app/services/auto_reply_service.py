import logging
import re
from typing import Optional

from app.services.whatsapp_service import send_text_message

logger = logging.getLogger(__name__)

STATIC_REPLIES = [
    {
        "patterns": [r"\b(hi|hello|hey|assalam|salam|aoa)\b"],
        "reply": "Hello! 👋 Welcome to our service. How can we help you today?",
    },
    {
        "patterns": [r"\b(help|support|assist)\b"],
        "reply": "We're here to help! Please describe your issue and our team will get back to you shortly.",
    },
    {
        "patterns": [r"\b(price|pricing|cost|plan|package)\b"],
        "reply": "Thank you for your interest in our pricing! Our team will share the details with you shortly. Stay tuned! 📋",
    },
    {
        "patterns": [r"\b(thank|thanks|shukria|shukriya)\b"],
        "reply": "You're welcome! 😊 If you need anything else, feel free to reach out anytime.",
    },
    {
        "patterns": [r"\b(bye|goodbye|see you|khuda hafiz)\b"],
        "reply": "Goodbye! 👋 Have a great day. We're always here when you need us!",
    },
    {
        "patterns": [r"\b(order|track|delivery|shipping)\b"],
        "reply": "For order and delivery inquiries, please share your order number and we'll check the status for you. 📦",
    },
    {
        "patterns": [r"\b(complaint|issue|problem|bug)\b"],
        "reply": "We're sorry to hear about the issue. 🙏 Please describe the problem in detail and our support team will resolve it as soon as possible.",
    },
    {
        "patterns": [r"\b(info|information|detail|about)\b"],
        "reply": "We'd be happy to provide more information! Please let us know what you'd like to learn about. 📌",
    },
]

DEFAULT_REPLY = "Thank you for your message! 🙏 Our team will get back to you shortly. If it's urgent, please type 'help' for immediate assistance."


def _match_reply(text: str) -> str:
    if not text:
        return DEFAULT_REPLY

    text_lower = text.lower().strip()

    for rule in STATIC_REPLIES:
        for pattern in rule["patterns"]:
            if re.search(pattern, text_lower, re.IGNORECASE):
                return rule["reply"]

    return DEFAULT_REPLY


async def handle_auto_reply(
    to_wa_id: str,
    incoming_text: Optional[str],
    message_type: str,
    phone_number_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
) -> Optional[dict]:
    if message_type != "text":
        reply_text = "Thanks for sending that! 📎 We've received your media. Our team will review it shortly."
    else:
        reply_text = _match_reply(incoming_text)

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
