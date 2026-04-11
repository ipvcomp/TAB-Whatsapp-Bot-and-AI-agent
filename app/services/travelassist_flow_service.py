"""
TravelAssist WhatsApp Flow Service
===================================
All conversation flows for the TravelAssist travel-cover chatbot,
converted from the React prototype (src/context/ChatContext.js) to
WhatsApp API-compatible message payloads.

Integration (webhook.py):
    from app.services.travelassist_flow_service import (
        is_travelassist_trigger, is_in_travelassist_flow, handle_travelassist,
    )

    # Inside your message handler, before other flow checks:
    if is_travelassist_trigger(message) or is_in_travelassist_flow(user_session):
        await handle_travelassist(message, sender_wa_id, phone_number_id, msg_id, user_session)
        return

WhatsApp limits enforced:
    - Interactive button messages: max 3 buttons
    - Interactive list messages:   max 10 rows per section
"""

import logging
import re
import random
from typing import Optional

from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_whatsapp_payload

logger = logging.getLogger(__name__)

# ── Step constants ─────────────────────────────────────────────────────────────

TA_STEP_KEY   = "ta_step"
TA_DATA_KEY   = "ta_data"
TA_RETRY_KEY  = "ta_retry"

# Welcome / menu
S_MAIN_MENU           = "ta_main_menu"

# Buy cover
S_BUY_COVER_TYPE          = "ta_buy_cover_type"
S_BUY_TRAVELLER_COUNT     = "ta_buy_traveller_count"
S_BUY_TRAVELLER_NAMES     = "ta_buy_traveller_names"
S_BUY_EMAIL               = "ta_buy_email"
S_BUY_TRIP_TYPE           = "ta_buy_trip_type"
S_BUY_BOOKING_REF         = "ta_buy_booking_ref"
S_BUY_FLIGHT_NUMBER       = "ta_buy_flight_number"
S_BUY_TRAVEL_DATE         = "ta_buy_travel_date"
S_BUY_DEPART_TIME         = "ta_buy_depart_time"
S_BUY_DEPART_AIRPORT_Q    = "ta_buy_depart_airport_query"
S_BUY_DEPART_AIRPORT_SEL  = "ta_buy_depart_airport_select"
S_BUY_ARRIVE_TIME         = "ta_buy_arrive_time"
S_BUY_ARRIVE_AIRPORT_Q    = "ta_buy_arrive_airport_query"
S_BUY_ARRIVE_AIRPORT_SEL  = "ta_buy_arrive_airport_select"
S_BUY_CARRIER             = "ta_buy_carrier"
S_BUY_TRIP_SUMMARY        = "ta_buy_trip_summary"
S_BUY_SELECT_PLAN         = "ta_buy_select_plan"
S_BUY_CONFIRM             = "ta_buy_confirm"
S_FLIGHT_NOT_FOUND        = "ta_flight_not_found"

# KYC
S_KYC_INTRO     = "ta_kyc_intro"
S_KYC_CONSENT   = "ta_kyc_consent"
S_KYC_ENTER_ID  = "ta_kyc_enter_id"
S_KYC_CONFIRM   = "ta_kyc_confirm"

# Payment (premium)
S_PAYMENT_SELECT          = "ta_payment_select"
S_PAYMENT_BANK_DETAILS    = "ta_payment_bank_details"
S_PAYMENT_CARD_DETAILS    = "ta_payment_card_details"
S_PAYMENT_USSD            = "ta_payment_ussd"
S_PAYMENT_WALLET_PROVIDER = "ta_payment_wallet_provider"
S_PAYMENT_WALLET_PHONE    = "ta_payment_wallet_phone"
S_PAYMENT_AWAIT_CONFIRM   = "ta_payment_await_confirm"

# Payout options (how user receives money)
S_PAYOUT_SELECT          = "ta_payout_select"
S_PAYOUT_BANK_ACCOUNT    = "ta_payout_bank_account"
S_PAYOUT_BANK_NAME       = "ta_payout_bank_name"
S_PAYOUT_WALLET_PROVIDER = "ta_payout_wallet_provider"
S_PAYOUT_WALLET_PHONE    = "ta_payout_wallet_phone"

# Policy lookup
S_POLICY_LOOKUP_METHOD = "ta_policy_lookup_method"
S_POLICY_LOOKUP_VALUE  = "ta_policy_lookup_value"
S_POLICY_FLIGHT_DATE   = "ta_policy_flight_date"

# Update details — bank sub-step
S_UPDATE_BANK_NAME = "ta_update_bank_name"

# Boarding pass
S_BOARDING_UPLOAD = "ta_boarding_upload"

# Link flight
S_LINK_ENTER_FLIGHT = "ta_link_enter_flight"

# Update details
S_UPDATE_WHO              = "ta_update_who"
S_UPDATE_MENU             = "ta_update_menu"
S_UPDATE_MENU_GROUP       = "ta_update_menu_group"
S_UPDATE_NAME             = "ta_update_name"
S_UPDATE_EMAIL            = "ta_update_email"
S_UPDATE_PHONE            = "ta_update_phone"
S_UPDATE_BANK             = "ta_update_bank"
S_UPDATE_BANK_NAME        = "ta_update_bank_name"
S_UPDATE_TRAVELLER_SELECT = "ta_update_traveller_select"
S_UPDATE_TRAVELLER_NAME   = "ta_update_traveller_name"

# Help
S_HELP_MENU = "ta_help_menu"


# ── Static data ────────────────────────────────────────────────────────────────

NIGERIAN_AIRPORTS = [
    {"code": "LOS", "name": "Murtala Muhammed International", "city": "Lagos"},
    {"code": "ABV", "name": "Nnamdi Azikiwe International",   "city": "Abuja"},
    {"code": "PHC", "name": "Port Harcourt International",     "city": "Port Harcourt"},
    {"code": "KAN", "name": "Mallam Aminu Kano International", "city": "Kano"},
    {"code": "ENU", "name": "Akanu Ibiam International",       "city": "Enugu"},
    {"code": "ILR", "name": "Ilorin International",            "city": "Ilorin"},
    {"code": "ABB", "name": "Asaba International",             "city": "Asaba"},
    {"code": "QOW", "name": "Sam Mbakwe International",        "city": "Owerri"},
    {"code": "CBQ", "name": "Margaret Ekpo International",     "city": "Calabar"},
    {"code": "MIU", "name": "Maiduguri International",         "city": "Maiduguri"},
    {"code": "SKO", "name": "Sadiq Abubakar III International", "city": "Sokoto"},
    {"code": "IBA", "name": "Ibadan Airport",                  "city": "Ibadan"},
    {"code": "AKR", "name": "Akure Airport",                   "city": "Akure"},
    {"code": "YOL", "name": "Yola International",              "city": "Yola"},
    {"code": "LHR", "name": "Heathrow Airport",                "city": "London"},
    {"code": "DXB", "name": "Dubai International",             "city": "Dubai"},
    {"code": "JNB", "name": "OR Tambo International",          "city": "Johannesburg"},
    {"code": "NBO", "name": "Jomo Kenyatta International",     "city": "Nairobi"},
    {"code": "ACC", "name": "Kotoka International",            "city": "Accra"},
    {"code": "CDG", "name": "Charles de Gaulle Airport",       "city": "Paris"},
]

NIGERIAN_BANKS = [
    "Access Bank", "Zenith Bank", "GTBank", "First Bank", "UBA",
    "Stanbic IBTC", "Sterling Bank", "Fidelity Bank", "FCMB", "Wema Bank",
    "Polaris Bank", "Union Bank", "Keystone Bank", "Heritage Bank", "Providus Bank",
]

WALLET_PROVIDERS = [
    {"id": "wallet_9psb",       "label": "9PSB",       "emoji": "📱"},
    {"id": "wallet_smartcash",  "label": "SmartCash",  "emoji": "💚"},
    {"id": "wallet_opay",       "label": "OPay",       "emoji": "🟠"},
]

COVER_PLANS = {
    "basic": {
        "name": "Local Travel Basic",
        "emoji": "🛡️",
        "price": 2500,
        "provider": "Tangerine Insurance",
        "validity": "Single trip",
    },
    "premium": {
        "name": "Local Travel Premium",
        "emoji": "👑",
        "price": 3500,
        "provider": "Tangerine Insurance",
        "validity": "Multi Trip",
    },
}

FAQ_ITEMS = [
    {"id": "buying_cover",   "question": "🛒 Buy  cover",          "answer": "Tap \"Buy cover\" from the main menu. Enter your trip details, complete a quick identity check, then pay. The whole process takes under 3 minutes."},
    {"id": "kyc_needed",     "question": "🪪 KYC verification",       "answer": "You can verify using either your BVN or NIN.\n\nWe only use this to confirm your identity for policy issuance. Make sure the number belongs to the traveller buying the policy."},
    {"id": "payment_issues", "question": "💳 Payment issues",         "answer": "If your payment failed, try again or switch methods (card, bank transfer, USSD, or wallet). If the issue persists, type 00 for the main menu or speak to an agent."},
    {"id": "my_policy",      "question": "📄 My policy",              "answer": "Go to \"Check my policy\" from the main menu. You can look up by phone number, policy number, or flight number. You can also download your policy document or manage alerts."},
    {"id": "boarding_pass",  "question": "🛂 Boarding pass upload",   "answer": "Usually you don't need to upload a boarding pass — payouts are automatic. We may request it only if extra verification is needed. Accepted formats: JPEG, PDF, GIF, TIFF, PNG. Max size: 20MB."},
    {"id": "claim_support",  "question": "📋 Claim support",          "answer": "TravelAssist detects disruptions automatically and processes payouts without you needing to file a claim. If you believe you're eligible but haven't received a payout, please speak to an agent."},
    {"id": "contact_agent",  "question": "🤝 Speak to an agent",      "answer": "You can reach our support team 24/7:\n🌐 www.travelassist.ng\n📧 support@travelassist.ng\n📱 WhatsApp: +234 800 TRAVEL"},
]

TRIGGER_KEYWORDS = [
    r"\b(hi|hello|hey|start|menu|travelassist|travel assist)\b",
    r"^(00|0|1)$",
    r"\b(buy\s*cover|buy\s*insurance|flight\s*insurance|travel\s*cover)\b",
    r"\b(check\s*policy|my\s*policy|policy\s*details)\b",
]


# ── Message builders ───────────────────────────────────────────────────────────

def _msg_text(to: str, body: str) -> dict:
    """Plain text message payload."""
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }


def _msg_buttons(to: str, body: str, buttons: list) -> dict:
    """Interactive button message. WhatsApp max = 3 buttons.
    Each button: {"id": str, "title": str}
    """
    buttons = buttons[:3]  # hard enforce limit
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
                    for b in buttons
                ]
            },
        },
    }


def _msg_list(to: str, body: str, button_label: str, rows: list) -> dict:
    """Interactive list message. WhatsApp max = 10 rows per section.
    Each row: {"id": str, "title": str, "description"?: str}
    """
    rows = rows[:10]  # hard enforce limit
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button_label[:20],
                "sections": [
                    {
                        "rows": [
                            {
                                "id": r["id"],
                                "title": r["title"][:24],
                                **({"description": r["description"][:72]} if r.get("description") else {}),
                            }
                            for r in rows
                        ]
                    }
                ],
            },
        },
    }


async def _send(to: str, payload: dict, pid: str, msg_id: Optional[str] = None):
    await send_whatsapp_payload(payload, phone_number_id=pid, in_reply_to=msg_id, source="travelassist")


def _gen_policy_number() -> str:
    from datetime import datetime
    year = datetime.now().year
    num = random.randint(100000, 999999)
    return f"TA-{year}-{num:06d}"


def _gen_ref() -> str:
    return f"TA{random.randint(100000, 999999)}"


# ── Session helpers ────────────────────────────────────────────────────────────

def _get_step(session: dict) -> str:
    return session.get(TA_STEP_KEY, "")


def _set_step(session: dict, step: str):
    session[TA_STEP_KEY] = step


def _get_data(session: dict) -> dict:
    if TA_DATA_KEY not in session:
        session[TA_DATA_KEY] = {}
    return session[TA_DATA_KEY]


def _set_data(session: dict, updates: dict):
    if TA_DATA_KEY not in session:
        session[TA_DATA_KEY] = {}
    session[TA_DATA_KEY].update(updates)


def _reset_flow(session: dict):
    session[TA_STEP_KEY] = ""
    session[TA_DATA_KEY] = {}
    session[TA_RETRY_KEY] = {}


def _get_retry(session: dict, key: str) -> int:
    return session.get(TA_RETRY_KEY, {}).get(key, 0)


def _inc_retry(session: dict, key: str):
    if TA_RETRY_KEY not in session:
        session[TA_RETRY_KEY] = {}
    session[TA_RETRY_KEY][key] = session[TA_RETRY_KEY].get(key, 0) + 1


def _reset_retry(session: dict, key: str):
    if TA_RETRY_KEY in session:
        session[TA_RETRY_KEY].pop(key, None)


# ── Airport helpers ────────────────────────────────────────────────────────────

def _search_airports(query: str) -> list:
    q = query.lower()
    return [
        a for a in NIGERIAN_AIRPORTS
        if q in a["code"].lower() or q in a["city"].lower() or q in a["name"].lower()
    ][:5]


# ── Flow: Welcome / Main Menu ──────────────────────────────────────────────────

async def send_welcome(to: str, pid: str, msg_id: Optional[str], is_returning: bool = False):
    """Send welcome text then split 6-button main menu into two messages."""
    if is_returning:
        greeting = "👋 Welcome back to *TravelAssist*!\n\nYour smart travel cover assistant. What would you like to do?"
    else:
        greeting = (
            "👋 Welcome to *TravelAssist*!\n\n"
            "Your smart travel cover companion — buy cover, track flights, and get paid out automatically if things go wrong. ✈️\n\n"
            "💡 *Quick tip:*\n• *0* ↩️ Back\n• *9* 🏠 Main menu\n• *00* 🏠 Main menu\n• *99* ❌ Cancel"
        )
    await _send(to, _msg_text(to, greeting), pid, msg_id)
    await send_main_menu(to, pid, msg_id)


async def send_main_menu(to: str, pid: str, msg_id: Optional[str]):
    """Main menu — 6 options split across 2 button messages (WhatsApp max 3 per message)."""
    await _send(to, _msg_buttons(
        to,
        "What would you like to do?",
        [
            {"id": "ta_buy_cover",      "title": "🛡️ Buy cover"},
            {"id": "ta_check_policy",   "title": "📋 Check my policy"},
            {"id": "ta_update_details", "title": "✏️ Update my details"},
        ],
    ), pid, msg_id)
    await _send(to, _msg_buttons(
        to,
        "More options:",
        [
            {"id": "ta_upload_boarding", "title": "🛂 Upload boarding pass"},
            {"id": "ta_payout_options",  "title": "💰 Payment options"},
            {"id": "ta_help",            "title": "❓ Help"},
        ],
    ), pid, msg_id)


# ── Flow: Buy Cover ────────────────────────────────────────────────────────────

async def start_buy_cover(to: str, pid: str, msg_id: Optional[str], session: dict):
    _set_step(session, S_BUY_COVER_TYPE)
    _set_data(session, {})  # clear previous purchase data
    await _send(to, _msg_buttons(
        to,
        "🛡️ *Buy cover*\n\nWho are you buying cover for?",
        [
            {"id": "ta_cover_solo",  "title": "👤 Just me"},
            {"id": "ta_cover_group", "title": "👥 Me and others(Same Booking)"},
        ],
    ), pid, msg_id)


async def handle_cover_type(to: str, pid: str, msg_id: Optional[str], session: dict, btn_id: str):
    is_group = btn_id == "ta_cover_group"
    _set_data(session, {"cover_type": "group" if is_group else "solo"})

    if is_group:
        _set_step(session, S_BUY_TRAVELLER_COUNT)
        await _send(to, _msg_text(
            to,
            "👥 *How many travellers are you covering?*\n\n📌 Enter a number between 1 and 9"
        ), pid, msg_id)
    else:
        _set_data(session, {"traveller_count": 1, "traveller_names": []})
        _set_step(session, S_BUY_EMAIL)
        await _send(to, _msg_text(
            to,
            "📧 Please enter your *email address*\n\nWe'll send your policy documents here."
        ), pid, msg_id)


async def handle_traveller_count(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    try:
        count = int(text.strip())
    except ValueError:
        count = 0
    if count < 1 or count > 9:
        await _send(to, _msg_text(to, "⚠️ Please enter a valid number between 1 and 9\n\n📌 Example: 2"), pid, msg_id)
        return
    _set_data(session, {"traveller_count": count, "traveller_names": []})
    _set_step(session, S_BUY_TRAVELLER_NAMES)
    await _send(to, _msg_text(
        to,
        "👤 Please enter *Traveller 1's* full name as it appears on their ticket\n\n📌 Example: *Yusuf Usman*"
    ), pid, msg_id)


async def handle_traveller_name(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    d = _get_data(session)
    names = d.get("traveller_names", [])
    count = d.get("traveller_count", 1)
    names.append(text.strip())
    _set_data(session, {"traveller_names": names})

    if len(names) < count:
        await _send(to, _msg_text(
            to,
            f"👤 Please enter *Traveller {len(names) + 1}'s* full name as it appears on their ticket\n\n📌 Example: *Amina Bello*"
        ), pid, msg_id)
    else:
        _set_step(session, S_BUY_EMAIL)
        await _send(to, _msg_text(
            to,
            "📧 Please enter your *email address*\n\nWe'll send your policy documents here."
        ), pid, msg_id)


async def handle_email(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    email = text.strip()
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        await _send(to, _msg_text(to, "⚠️ Please enter a valid email address.\n\n📌 Example: *name@email.com*"), pid, msg_id)
        return
    _set_data(session, {"email": email})
    _set_step(session, S_BUY_TRIP_TYPE)
    await _send(to, _msg_buttons(
        to,
        "📍 What type of trip is this?",
        [
            {"id": "ta_trip_oneway", "title": "➡️ One-way"},
            {"id": "ta_trip_return", "title": "🔁 Return"},
        ],
    ), pid, msg_id)


async def handle_trip_type(to: str, pid: str, msg_id: Optional[str], session: dict, btn_id: str):
    is_return = btn_id == "ta_trip_return"
    _set_data(session, {"trip_type": "return" if is_return else "oneway"})
    _set_step(session, S_BUY_BOOKING_REF)
    await _send(to, _msg_text(
        to,
        "🎫 Please enter your *booking reference*\n\n📌 Examples: *AB1XY2*, *2990FA62*\n\nType *00* for main menu"
    ), pid, msg_id)


async def handle_booking_ref(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    ref = text.strip()
    if len(ref) < 4:
        await _send(to, _msg_text(to, "⚠️ Please enter a valid booking reference.\n\n📌 Examples: *AB1XY2*, *2990FA62*"), pid, msg_id)
        return
    _set_data(session, {"booking_ref": ref.upper()})
    _set_step(session, S_BUY_FLIGHT_NUMBER)
    await _send(to, _msg_text(
        to,
        "✈️ Please enter your *flight number*\n\n📌 Examples: *P47123*, *QI402*, *AA123*\n_(Just the flight number — no airline name)_\n\nType *00* for main menu"
    ), pid, msg_id)


async def handle_flight_number(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    cleaned = re.sub(r"[\s\-—–]", "", text.strip().upper())
    if not re.match(r"^(?=.*[A-Z])(?=.*[0-9])[A-Z0-9]{2,7}$", cleaned):
        await _send(to, _msg_text(to, "⚠️ I couldn't recognise that flight number\n\nPlease enter it like this: *P47123*"), pid, msg_id)
        return
    _set_data(session, {"flight_number": cleaned})
    _set_step(session, S_BUY_TRAVEL_DATE)
    await _send(to, _msg_text(
        to,
        "📅 *What date are you flying?*\n\n📌 Example: *12 April 2026*, *12/04/2026*"
    ), pid, msg_id)


async def handle_travel_date(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    val = text.strip()
    valid = (
        len(val) >= 6 and (
            re.search(r"\d{1,2}\s+[A-Za-z]+\s+\d{2,4}", val) or
            re.search(r"\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}", val)
        )
    )
    if not valid:
        await _send(to, _msg_text(to, "⚠️ Please enter the date like this: *12 April 2026*"), pid, msg_id)
        return
    _set_data(session, {"travel_date": val})
    _set_step(session, S_BUY_DEPART_TIME)
    await _send(to, _msg_text(
        to,
        "⏰ *What time is your flight scheduled to depart?*\n\n📌 Example: *13:40*, *1:40 PM*"
    ), pid, msg_id)


async def handle_depart_time(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    if len(text.strip()) < 3:
        await _send(to, _msg_text(to, "⚠️ Please enter a valid time.\n\n📌 Example: *13:40* or *1:40 PM*"), pid, msg_id)
        return
    _set_data(session, {"depart_time": text.strip()})
    _set_step(session, S_BUY_DEPART_AIRPORT_Q)
    await _send(to, _msg_text(
        to,
        "✈️ *What airport are you flying from?*\n\nEnter the first 3 characters of the airport name or code\n📌 Example: *LOS*, *Abj*, *PHC*"
    ), pid, msg_id)


async def handle_depart_airport_query(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    if len(text.strip()) < 3:
        await _send(to, _msg_text(to, "⚠️ Please enter at least 3 characters\n📌 Example: *LOS*, *Abj*, *PHC*"), pid, msg_id)
        return
    matches = _search_airports(text.strip())
    if not matches:
        await _send(to, _msg_text(
            to,
            f"⚠️ No airports found for \"*{text.strip()}*\"\n\nPlease try again\n📌 Example: *LOS*, *Abj*, *PHC*"
        ), pid, msg_id)
        return
    _set_step(session, S_BUY_DEPART_AIRPORT_SEL)
    await _send(to, _msg_buttons(
        to,
        "✈️ *Select your departure airport:*",
        [{"id": f"ta_dep_{a['code']}", "title": f"{a['code']} — {a['city']}"} for a in matches],
    ), pid, msg_id)


async def handle_depart_airport_select(to: str, pid: str, msg_id: Optional[str], session: dict, code: str):
    airport = next((a for a in NIGERIAN_AIRPORTS if a["code"] == code), None)
    if not airport:
        return
    _set_data(session, {"depart_airport": airport})
    _set_step(session, S_BUY_ARRIVE_TIME)
    await _send(to, _msg_text(
        to,
        "⏰ *What time is your flight scheduled to arrive?*\n\n📌 Example: *15:30*, *3:30 PM*"
    ), pid, msg_id)


async def handle_arrive_time(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    if len(text.strip()) < 3:
        await _send(to, _msg_text(to, "⚠️ Please enter a valid time.\n\n📌 Example: *15:30* or *3:30 PM*"), pid, msg_id)
        return
    _set_data(session, {"arrive_time": text.strip()})
    _set_step(session, S_BUY_ARRIVE_AIRPORT_Q)
    await _send(to, _msg_text(
        to,
        "✈️ *What airport are you arriving at?*\n\nEnter the first 3 characters of the airport name or code\n📌 Example: *ABV*, *PHC*, *Kan*"
    ), pid, msg_id)


async def handle_arrive_airport_query(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    if len(text.strip()) < 3:
        await _send(to, _msg_text(to, "⚠️ Please enter at least 3 characters\n📌 Example: *ABV*, *PHC*, *Kan*"), pid, msg_id)
        return
    matches = _search_airports(text.strip())
    if not matches:
        await _send(to, _msg_text(
            to,
            f"⚠️ No airports found for \"*{text.strip()}*\"\n\nPlease try again\n📌 Example: *ABV*, *PHC*, *Kan*"
        ), pid, msg_id)
        return
    _set_step(session, S_BUY_ARRIVE_AIRPORT_SEL)
    await _send(to, _msg_buttons(
        to,
        "✈️ *Select your arrival airport:*",
        [{"id": f"ta_arr_{a['code']}", "title": f"{a['code']} — {a['city']}"} for a in matches],
    ), pid, msg_id)


async def handle_arrive_airport_select(to: str, pid: str, msg_id: Optional[str], session: dict, code: str):
    airport = next((a for a in NIGERIAN_AIRPORTS if a["code"] == code), None)
    if not airport:
        return
    _set_data(session, {"arrive_airport": airport})
    _set_step(session, S_BUY_CARRIER)
    await _send(to, _msg_text(
        to,
        "✈️ *Who are you flying with?*\n\n📌 Example: *Ibom Air*, *Air Peace*, *Overland*"
    ), pid, msg_id)


async def handle_carrier(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    if len(text.strip()) < 2:
        await _send(to, _msg_text(to, "⚠️ Please enter the airline name\n📌 Example: *Ibom Air*, *Air Peace*"), pid, msg_id)
        return
    _set_data(session, {"carrier": text.strip()})
    await send_trip_summary(to, pid, msg_id, session)


async def send_trip_summary(to: str, pid: str, msg_id: Optional[str], session: dict):
    d = _get_data(session)
    count = d.get("traveller_count", 1)
    dep = d.get("depart_airport", {}).get("city", "—")
    arr = d.get("arrive_airport", {}).get("city", "—")
    _set_step(session, S_BUY_TRIP_SUMMARY)
    await _send(to, _msg_text(
        to,
        f"📍 *Trip Summary*\n\n"
        f"✈️ Airline: *{d.get('carrier', '—')}*\n"
        f"🛫 Route: *{dep} → {arr}*\n"
        f"✈️ Flight: *{d.get('flight_number', '—')}*\n"
        f"📅 Date: *{d.get('travel_date', '—')}*\n"
        f"⏰ Departure: *{d.get('depart_time', '—')}*\n"
        f"⏰ Arrival: *{d.get('arrive_time', '—')}*\n"
        f"🎫 Booking ref: *{d.get('booking_ref', '—')}*\n"
        f"📍 Trip type: *{'Return ↩️' if d.get('trip_type') == 'return' else 'One-way ➡️'}*\n"
        f"👤 Traveller{'s' if count > 1 else ''}: *{count}*\n"
        f"📧 Email: *{d.get('email', '—')}*\n\n"
        f"Please confirm:"
    ), pid, msg_id)
    await _send(to, _msg_buttons(
        to,
        "Ready to choose your cover plan?",
        [
            {"id": "ta_confirm_trip", "title": "✅ Confirm"},
            {"id": "ta_edit_trip",    "title": "✏️ Edit trip details"},
        ],
    ), pid, msg_id)


async def _flight_lookup_then_plan(to: str, pid: str, msg_id: Optional[str], session: dict):
    """
    Called at ta_confirm_trip. Simulates flight lookup with status guards
    (cancelled / already departed / duplicate), matching React's handleFlightLookup().
    On success falls through to send_plan_selection().
    """
    d = _get_data(session)
    flight_number = d.get("flight_number", "")
    await _send(to, _msg_text(to, "⏳ Looking up your flight..."), pid, msg_id)

    # --- Simulate lookup (80 % success rate). Replace with real API call. ---
    known_flights = {
        "P47123": {"airline": "Air Peace",         "origin": "LOS (Lagos)",   "destination": "ABV (Abuja)",         "status": "DELAYED",    "delay_minutes": 75,  "departure": "13:40"},
        "QI402":  {"airline": "Ibom Air",           "origin": "LOS (Lagos)",   "destination": "PHC (Port Harcourt)", "status": "ON TIME",    "delay_minutes": 0,   "departure": "09:15"},
        "W3501":  {"airline": "Overland Airways",   "origin": "ABV (Abuja)",   "destination": "KAN (Kano)",          "status": "CANCELLED",  "delay_minutes": 0,   "departure": "11:00"},
        "AA123":  {"airline": "American Airlines",  "origin": "LOS (Lagos)",   "destination": "ABV (Abuja)",         "status": "DELAYED",    "delay_minutes": 75,  "departure": "10:30"},
        "LH456":  {"airline": "Lufthansa",          "origin": "LOS (Lagos)",   "destination": "FRA (Frankfurt)",     "status": "ON TIME",    "delay_minutes": 0,   "departure": "23:15"},
    }
    flight = known_flights.get(flight_number.upper())

    if not flight:
        # Build synthetic flight from manually entered details (mirrors React behaviour)
        dep = d.get("depart_airport", {}).get("city", "—")
        arr = d.get("arrive_airport", {}).get("city", "—")
        carrier = d.get("carrier", "—")
        if dep != "—" and arr != "—" and carrier != "—":
            await _send(to, _msg_text(
                to,
                f"✅ *Trip confirmed*\n\n"
                f"✈️ *{flight_number}* — {carrier}\n"
                f"📍 {dep} → {arr}\n"
                f"📅 {d.get('travel_date', '—')}\n"
                f"⏰ Dep: {d.get('depart_time', '—')} · Arr: {d.get('arrive_time', '—')}"
            ), pid, msg_id)
            await send_plan_selection(to, pid, msg_id, session)
        else:
            _set_step(session, S_FLIGHT_NOT_FOUND)
            await _send(to, _msg_buttons(
                to,
                f"⚠️ I couldn't find that flight yet\n\nPlease check the number and try again",
                [
                    {"id": "ta_retry_flight", "title": "🔄 Try again"},
                    {"id": "ta_main_menu",    "title": "🏠 Main menu"},
                ],
            ), pid, msg_id)
        return

    # Guard: cancelled
    if flight["status"] == "CANCELLED":
        await _send(to, _msg_buttons(
            to,
            f"❌ *Flight {flight_number} is Cancelled*\n\n"
            f"✈️ {flight['airline']}\n"
            f"📍 {flight['origin']} → {flight['destination']}\n"
            f"🕐 Was scheduled: {flight['departure']}\n\n"
            "⚠️ Cover cannot be purchased for a cancelled flight.\n\nWhat would you like to do?",
            [
                {"id": "ta_buy_cover", "title": "✈️ Cover a different flight"},
                {"id": "ta_help",      "title": "💬 Get Support"},
                {"id": "ta_main_menu", "title": "🏠 Main Menu"},
            ],
        ), pid, msg_id)
        return

    # Guard: already departed (delay > 120 mins treated as departed, mirrors React)
    if flight["delay_minutes"] > 120:
        await _send(to, _msg_buttons(
            to,
            f"⚠️ *Flight {flight_number} Has Already Departed*\n\n"
            f"✈️ {flight['airline']}\n"
            f"📍 {flight['origin']} → {flight['destination']}\n"
            f"🕐 Departed: {flight['departure']}\n\n"
            "⚠️ Cover cannot be purchased after a flight has departed.\n\nWhat would you like to do?",
            [
                {"id": "ta_buy_cover", "title": "✈️ Cover a different flight"},
                {"id": "ta_help",      "title": "💬 Get Support"},
                {"id": "ta_main_menu", "title": "🏠 Main Menu"},
            ],
        ), pid, msg_id)
        return

    # Fix #9: Duplicate-policy guard — check for existing active policy on same flight
    existing = next(
        (p for p in _DEMO_POLICIES if p["status"] == "ACTIVE" and p["flight_number"] == flight_number.upper()),
        None
    )
    if existing:
        await err_duplicate_policy(to, pid, msg_id, session, flight_number)
        return

    # Success — show flight card then plan selection
    status_emoji = "✅" if flight["status"] == "ON TIME" else "⚠️"
    delay_txt = f" (+{flight['delay_minutes']} mins)" if flight["delay_minutes"] > 0 else ""
    await _send(to, _msg_text(
        to,
        f"✅ *Flight Found!*\n\n"
        f"✈️ *{flight_number}* — {flight['airline']}\n"
        f"📍 {flight['origin']} → {flight['destination']}\n"
        f"🕐 Departure: {flight['departure']}\n"
        f"📊 Status: {status_emoji} {flight['status']}{delay_txt}"
    ), pid, msg_id)
    await send_plan_selection(to, pid, msg_id, session)


async def send_plan_selection(to: str, pid: str, msg_id: Optional[str], session: dict):
    _set_step(session, S_BUY_SELECT_PLAN)
    await _send(to, _msg_text(
        to,
        "📋 *Select from the list of available cover(s) for your trip*\n\n"
        "🛡️ *Cover name: Local Travel Basic*\n"
        "🛡 Your trip can be protected against:\n"
        "⏰ Major delay\n"
        "✅ Cancellation\n"
        "💰 Premium: *₦2,500*\n"
        "🏢 Provider: *Tangerine Insurance*\n"
        "📅 Validity: *Single trip*\n\n"
        "━━━━━━━━━━━━━━━━\n\n"
        "👑 *Cover name: Local Travel Premium*\n"
        "🛡 Your trip can be protected against:\n"
        "⏰ Major delay\n"
        "✅ Cancellation\n"
        "✅ Missed connection cover\n"
        "💰 Premium: *₦3,500*\n"
        "🏢 Provider: *Tangerine Insurance*\n"
        "📅 Validity: *Multi Trip*"
    ), pid, msg_id)
    await _send(to, _msg_buttons(
        to,
        "Select your cover plan:",
        [
            {"id": "ta_plan_basic",   "title": "🛡️ Basic — ₦2,500"},
            {"id": "ta_plan_premium", "title": "👑 Premium — ₦3,500"},
        ],
    ), pid, msg_id)


async def handle_select_plan(to: str, pid: str, msg_id: Optional[str], session: dict, plan_key: str):
    plan = COVER_PLANS.get(plan_key)
    if not plan:
        return
    _set_data(session, {"selected_plan": plan_key})
    _set_step(session, S_BUY_CONFIRM)
    await _send(to, _msg_text(
        to,
        f"{plan['emoji']} *{plan['name']}* — ₦{plan['price']:,}\n\n"
        "With TravelAssist you get:\n"
        "✅ Policy on WhatsApp\n"
        "✅ Flight alerts\n"
        "✅ Support if disruption happens"
    ), pid, msg_id)
    await _send(to, _msg_buttons(
        to,
        "What would you like to do next?",
        [
            {"id": "ta_proceed_kyc",     "title": "✅ Continue to KYC"},
            {"id": "ta_ask_question",    "title": "❓ Ask a question"},
            {"id": "ta_cancel_purchase", "title": "✗ Cancel purchase"},
        ],
    ), pid, msg_id)


# ── Flow: KYC ─────────────────────────────────────────────────────────────────

async def start_kyc(to: str, pid: str, msg_id: Optional[str], session: dict):
    # Fix #8: Skip to payment if KYC already verified in this session
    if _get_data(session).get("kyc_verified"):
        await _send(to, _msg_text(
            to,
            "✅ Your identity is already verified!\n\nProceeding to payment..."
        ), pid, msg_id)
        await start_payment(to, pid, msg_id, session)
        return
    _set_step(session, S_KYC_INTRO)
    await _send(to, _msg_buttons(
        to,
        "🪪 For payment, we need to verify your identity to ensure security and accurate policy issuance.\n\n*How would you like to verify?*",
        [
            {"id": "ta_kyc_bvn", "title": "🏦 Verify with BVN"},
            {"id": "ta_kyc_nin", "title": "🆔 Verify with NIN"},
            {"id": "ta_help",    "title": "🙋 Help"},
        ],
    ), pid, msg_id)


async def handle_kyc_type(to: str, pid: str, msg_id: Optional[str], session: dict, kyc_type: str):
    label = "NIN" if kyc_type == "nin" else "BVN"
    _set_data(session, {"kyc_type": label})
    _set_step(session, S_KYC_CONSENT)
    await _send(to, _msg_buttons(
        to,
        f"🔒 *We will only use your {label} to verify your identity for this purchase.*\n\n"
        f"We do not store or share your {label} number.\n\nDo you want to continue?",
        [
            {"id": "ta_kyc_consent_yes", "title": "✅ Yes, continue"},
            {"id": "ta_kyc_consent_no",  "title": "↩️ Go back"},
        ],
    ), pid, msg_id)


async def handle_kyc_consent(to: str, pid: str, msg_id: Optional[str], session: dict, agreed: bool):
    if not agreed:
        await _send(to, _msg_buttons(
            to,
            "⚠️ We need identity verification before payment and policy issuance.",
            [
                {"id": "ta_proceed_kyc",     "title": "🪪 Continue with KYC"},
                {"id": "ta_cancel_purchase", "title": "❌ Cancel purchase"},
            ],
        ), pid, msg_id)
        return
    kyc_type = _get_data(session).get("kyc_type", "BVN")
    _set_step(session, S_KYC_ENTER_ID)
    await _send(to, _msg_text(
        to,
        f"{'🆔' if kyc_type == 'NIN' else '🏦'} Please enter your *11-digit {kyc_type}*\n\n📌 Example: *12345678901*"
    ), pid, msg_id)


async def handle_kyc_value(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    val = text.strip()
    if not re.match(r"^\d{11}$", val):
        await _send(to, _msg_text(to, "⚠️ Please enter an 11-digit BVN or NIN\n\n📌 Example: *12345678901*"), pid, msg_id)
        return
    kyc_type = _get_data(session).get("kyc_type", "BVN")
    _set_data(session, {"kyc_value": val})
    _set_step(session, S_KYC_CONFIRM)
    masked = f"{val[:3]}•••••{val[-3:]}"
    await _send(to, _msg_buttons(
        to,
        f"📋 *KYC Summary*\n\n🪪 ID Type: *{kyc_type}*\n🔑 Number: *{masked}*\n\nShall I proceed with verification?",
        [
            {"id": "ta_confirm_kyc", "title": "✅ Verify Identity"},
            {"id": "ta_change_kyc",  "title": "🔄 Change Details"},
        ],
    ), pid, msg_id)


async def process_kyc(to: str, pid: str, msg_id: Optional[str], session: dict):
    """Simulate KYC verification (80% pass rate). Replace with real API call."""
    await _send(to, _msg_text(to, "🔍 *Checking your details now...*\n\nPlease wait a moment"), pid, msg_id)

    success = random.random() > 0.2
    if success:
        _reset_retry(session, "kyc")
        _set_data(session, {"kyc_verified": True, "kyc_name": "Yusuf Usman"})  # Replace with real API name
        await _send(to, _msg_list(
            to,
            "✅ *Identity verified*\n\n👤 Name: *Yusuf Usman*\n🟢 Status: Verified\n\nYou can now continue to payment.",
            "Choose option",
            [
                {"id": "ta_proceed_payment", "title": "💳 Continue to payment"},
                {"id": "ta_review_trip",     "title": "✏️ Review trip details"},
                {"id": "ta_main_menu",       "title": "🏠 Main menu"},
            ],
        ), pid, msg_id)
    else:
        _inc_retry(session, "kyc")
        attempts = _get_retry(session, "kyc")
        if attempts >= 3:
            _reset_retry(session, "kyc")
            await _send(to, _msg_buttons(
                to,
                "⚠️ We could not verify your details automatically.",
                [
                    {"id": "ta_kyc_bvn",         "title": "🏦 Try BVN again"},
                    {"id": "ta_kyc_nin",          "title": "🆔 Try NIN instead"},
                    {"id": "ta_contact_support",  "title": "🙋 Get help"},
                ],
            ), pid, msg_id)
        else:
            await _send(to, _msg_buttons(
                to,
                "⚠️ We could not verify your details automatically.",
                [
                    {"id": "ta_kyc_bvn",        "title": "🏦 Try BVN again"},
                    {"id": "ta_kyc_nin",         "title": "🆔 Try NIN instead"},
                    {"id": "ta_contact_support", "title": "🙋 Get help"},
                ],
            ), pid, msg_id)


# ── Flow: Payment (premium collection) ────────────────────────────────────────

async def start_payment(to: str, pid: str, msg_id: Optional[str], session: dict):
    d = _get_data(session)
    plan_key = d.get("selected_plan", "basic")
    plan = COVER_PLANS.get(plan_key, COVER_PLANS["basic"])
    count = d.get("traveller_count", 1)
    ref = _gen_ref()
    _set_data(session, {"payment_ref": ref})
    _set_step(session, S_PAYMENT_SELECT)

    summary = (
        f"🔒 *You're one step away from activating your cover*\n\n"
        f"*Payment Summary*\n"
        f"✈️ Policy: *{plan['name']}*\n"
        f"✈️ Flight: *{d.get('flight_number', '—')}*\n"
        f"📅 Date: *{d.get('travel_date', '—')}*\n"
        f"👤 Traveller{'s' if count > 1 else ''}: *{count}*\n"
        f"🪪 KYC: *Verified*\n"
        f"💰 Amount: *₦{plan['price']:,}*\n\n"
        "Choose a payment method:"
    )
    # 4 payment options → use list (exceeds 3-button limit)
    await _send(to, _msg_list(
        to,
        summary,
        "Choose method",
        [
            {"id": "ta_pay_bank",   "title": "🏦 Bank transfer"},
            {"id": "ta_pay_card",   "title": "💳 Card payment"},
            {"id": "ta_pay_wallet", "title": "👛 Wallet"},
            {"id": "ta_pay_ussd",   "title": "#️⃣ USSD"},
        ],
    ), pid, msg_id)


async def handle_payment_method(to: str, pid: str, msg_id: Optional[str], session: dict, method: str):
    d = _get_data(session)
    plan = COVER_PLANS.get(d.get("selected_plan", "basic"), COVER_PLANS["basic"])
    amount = plan["price"]
    ref = d.get("payment_ref", _gen_ref())
    _set_data(session, {"payment_method": method})

    if method == "bank":
        _set_step(session, S_PAYMENT_BANK_DETAILS)
        await _send(to, _msg_buttons(
            to,
            f"🏦 *Bank Transfer*\n\nPlease transfer *₦{amount:,}* to:\n\n"
            f"Bank: *Example Bank*\n"
            f"Account Name: *TravelAssist Payments*\n"
            f"Account No: *0123456789*\n"
            f"Reference: *{ref}*\n\n"
            f"⚠️ Use the reference as your narration.\n\nAfter payment, tap below:",
            [
                {"id": "ta_confirm_bank_pay",      "title": "✅ I have paid"},
                {"id": "ta_check_payment_status",  "title": "🔄 Refresh status"},
            ],
        ), pid, msg_id)

    elif method == "card":
        _set_step(session, S_PAYMENT_CARD_DETAILS)
        await _send(to, _msg_buttons(
            to,
            f"💳 *Card Payment*\n\nClick the secure payment link below:\n\n"
            f"👉 *[Pay ₦{amount:,}]*\n\n"
            f"🔒 Powered by Paystack\n\nAfter payment, we'll confirm your cover here on WhatsApp.",
            [
                {"id": "ta_confirm_card_pay",  "title": f"✅ Pay ₦{amount:,}"},
                {"id": "ta_change_payment",    "title": "🔄 Change Method"},
            ],
        ), pid, msg_id)

    elif method == "ussd":
        _set_step(session, S_PAYMENT_USSD)
        await _send(to, _msg_buttons(
            to,
            f"#️⃣ *USSD Payment*\n\nDial the code below to complete payment:\n\n"
            f"📟 *GTBank:* *737*000*{amount}#\n"
            f"📟 *Access:* *901*000*{amount}#\n"
            f"📟 *UBA:* *919*000*{amount}#\n"
            f"📟 *Zenith:* *966*000*{amount}#\n\n"
            f"💰 Amount: *₦{amount:,}*\n"
            f"📝 Reference: *{ref}*\n\nAfter payment, tap below:",
            [
                {"id": "ta_confirm_ussd_pay",      "title": "✅ I have paid"},
                {"id": "ta_check_payment_status",  "title": "🔄 Refresh status"},
            ],
        ), pid, msg_id)

    elif method == "wallet":
        _set_step(session, S_PAYMENT_WALLET_PROVIDER)
        await _send(to, _msg_buttons(
            to,
            "👛 *Wallet Payment*\n\nChoose your wallet:",
            [{"id": f"ta_pay_wallet_{w['id']}", "title": f"{w['emoji']} {w['label']}"} for w in WALLET_PROVIDERS],
        ), pid, msg_id)


async def handle_wallet_provider(to: str, pid: str, msg_id: Optional[str], session: dict, provider_id: str):
    provider = next((w for w in WALLET_PROVIDERS if w["id"] == provider_id), None)
    label = provider["label"] if provider else provider_id
    _set_data(session, {"wallet_provider": label})
    _set_step(session, S_PAYMENT_WALLET_PHONE)
    await _send(to, _msg_text(
        to,
        f"📱 Enter the phone number linked to your *{label}* wallet\n\n📌 Example: *08012345678*"
    ), pid, msg_id)


async def handle_wallet_phone(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    val = text.strip()
    if not re.match(r"^0[7-9]\d{9}$", val):
        await _send(to, _msg_text(to, "⚠️ Please enter a valid Nigerian phone number.\n\n📌 Example: *08012345678*"), pid, msg_id)
        return
    _set_data(session, {"wallet_phone": val})
    d = _get_data(session)
    plan = COVER_PLANS.get(d.get("selected_plan", "basic"), COVER_PLANS["basic"])
    _set_step(session, S_PAYMENT_AWAIT_CONFIRM)
    await _send(to, _msg_buttons(
        to,
        f"📲 *A payment prompt has been sent to your {d.get('wallet_provider', 'wallet')}*\n\n"
        f"Please approve it on your device\n\n💰 Amount: *₦{plan['price']:,}*",
        [
            {"id": "ta_confirm_wallet_pay",   "title": "✅ I have approved"},
            {"id": "ta_check_payment_status", "title": "🔄 Refresh status"},
        ],
    ), pid, msg_id)


async def process_payment(to: str, pid: str, msg_id: Optional[str], session: dict):
    """Simulate payment processing. Replace with real payment gateway verification."""
    await _send(to, _msg_text(to, "⏳ *Processing your payment...*\n\nPlease wait."), pid, msg_id)

    rand = random.random()
    if rand > 0.15:
        # Success
        _reset_retry(session, "payment")
        policy_no = _gen_policy_number()
        d = _get_data(session)
        _set_data(session, {"policy_number": policy_no})
        count = d.get("traveller_count", 1)
        travellers = ", ".join(d.get("traveller_names", [])) or d.get("kyc_name", "Traveller")

        await _send(to, _msg_text(to, "✅ *Payment successful*\n\nYour cover is now active 🛡️"), pid, msg_id)
        await _send(to, _msg_list(
            to,
            f"📄 *Policy No: {policy_no}*\n"
            f"✈️ Flight: *{d.get('flight_number', '—')}*\n"
            f"📅 Date: *{d.get('travel_date', '—')}*\n"
            f"Traveller Name: *{travellers}*\n\n"
            "Got your boarding pass handy? You can upload it now 👍\n\n"
            "What would you like to do next?",
            "Choose option",
            [
                {"id": "ta_enable_alerts",   "title": "🔔 Turn on flight alerts"},
                {"id": "ta_view_policy",     "title": "📄 View my policy"},
                {"id": "ta_upload_boarding", "title": "🛂 Upload boarding pass"},
                {"id": "ta_main_menu",       "title": "🏠 Main menu"},
            ],
        ), pid, msg_id)
    elif rand > 0.05:
        # Pending
        _reset_retry(session, "payment")
        await _send(to, _msg_buttons(
            to,
            "⏳ We haven't confirmed your payment yet\n\nPlease wait a little and try again",
            [
                {"id": "ta_check_payment_status", "title": "🔄 Refresh status"},
                {"id": "ta_help",                 "title": "🙋 Help"},
            ],
        ), pid, msg_id)
    else:
        # Failed
        _inc_retry(session, "payment")
        attempts = _get_retry(session, "payment")
        # Fix #11: always return to payment method selection (mirrors React's handlePaymentFailure)
        _reset_retry(session, "payment")
        await _send(to, _msg_list(
            to,
            "❌ Payment was not successful\n\nPlease choose a payment method to try again:",
            "Choose option",
            [
                {"id": "ta_pay_card",       "title": "💳 Pay by card"},
                {"id": "ta_pay_bank",       "title": "🏦 Bank transfer"},
                {"id": "ta_pay_ussd",       "title": "#️⃣ Use USSD"},
                {"id": "ta_pay_wallet",     "title": "👛 Wallet payment"},
                {"id": "ta_contact_support","title": "🙋 Get help"},
            ],
        ), pid, msg_id)


# ── Flow: Payout Options (how user RECEIVES money) ─────────────────────────────

async def start_payout_options(to: str, pid: str, msg_id: Optional[str], session: dict):
    _set_step(session, S_PAYOUT_SELECT)
    await _send(to, _msg_buttons(
        to,
        "💰 *Payout options*\n\nChoose how you would like to *receive money* for any future payouts:",
        [
            {"id": "ta_payout_bank",   "title": "🏦 Bank transfer"},
            {"id": "ta_payout_wallet", "title": "👛 Wallet"},
        ],
    ), pid, msg_id)


async def handle_payout_method(to: str, pid: str, msg_id: Optional[str], session: dict, method: str):
    _set_data(session, {"payout_method": method})
    if method == "bank":
        _set_step(session, S_PAYOUT_BANK_ACCOUNT)
        await _send(to, _msg_text(
            to,
            "🏦 Please enter your *10-digit account number* for future payouts:"
        ), pid, msg_id)
    else:
        _set_step(session, S_PAYOUT_WALLET_PROVIDER)
        await _send(to, _msg_buttons(
            to,
            "👛 *Wallet*\n\nChoose wallet option:",
            [{"id": f"ta_payout_wallet_{w['id']}", "title": f"{w['emoji']} {w['label']}"} for w in WALLET_PROVIDERS],
        ), pid, msg_id)


async def handle_payout_bank_account(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    val = text.strip()
    if not re.match(r"^\d{10}$", val):
        await _send(to, _msg_text(to, "⚠️ Please enter a valid *10-digit account number*."), pid, msg_id)
        return
    _set_data(session, {"payout_bank_account": val})
    _set_step(session, S_PAYOUT_BANK_NAME)
    await _send(to, _msg_text(
        to,
        "🏦 Please enter at least the *first 3 characters* of your bank name\n\n📌 Examples: *Zen* (Zenith), *Wem* (Wema), *GT* (GTBank)"
    ), pid, msg_id)


async def handle_payout_bank_name(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    if len(text.strip()) < 3:
        await _send(to, _msg_text(
            to,
            "⚠️ Please enter at least *3 characters* of your bank name.\n\n📌 Examples: *Zen* (Zenith), *Wem* (Wema), *GT* (GTBank)"
        ), pid, msg_id)
        return

    # Show the full sorted bank list (paginated in groups of 10 via list message)
    all_banks = sorted(NIGERIAN_BANKS)
    _set_data(session, {"bank_search_done": True})
    rows = [{"id": f"ta_sel_bank_{b.replace(' ', '_')}", "title": b} for b in all_banks[:10]]
    footer_note = f"Showing 1–{min(10, len(all_banks))} of {len(all_banks)} banks (alphabetical). Reply with bank name if not listed."
    await _send(to, _msg_list(to, f"🏦 *Select your bank*\n\n{footer_note}", "Select bank", rows), pid, msg_id)


async def handle_payout_bank_select(to: str, pid: str, msg_id: Optional[str], session: dict, bank_name: str):
    acct = _get_data(session).get("payout_bank_account", "")
    masked = f"••••••••{acct[-2:]}" if acct else "—"
    is_updating = _get_data(session).get("updating_bank_details", False)
    needs_payment = _get_data(session).get("payout_from_buy_flow", False)
    _set_data(session, {"payout_bank_name": bank_name, "updating_bank_details": False, "payout_from_buy_flow": False})
    _set_step(session, "")
    if is_updating:
        await _send(to, _msg_buttons(
            to,
            f"✅ *Bank/payout details updated!*\n\n🏦 Bank: *{bank_name}*\n💰 Account: *{masked}*\n\nIs there anything else you'd like to update?",
            [
                {"id": "ta_update_details", "title": "✏️ Update another details"},
                {"id": "ta_main_menu",      "title": "🏠 Main menu"},
            ],
        ), pid, msg_id)
    elif needs_payment:
        await _send(to, _msg_buttons(
            to,
            f"✅ *Payout details saved!*\n\n🏦 Bank: *{bank_name}*\n💰 Account: *{masked}*\n\nWe'll use these details for any future payouts.",
            [
                {"id": "ta_start_payment", "title": "💳 Continue to payment"},
                {"id": "ta_main_menu",     "title": "🏠 Main menu"},
            ],
        ), pid, msg_id)
    else:
        await _send(to, _msg_buttons(
            to,
            f"✅ *Payout details saved!*\n\n🏦 Bank: *{bank_name}*\n💰 Account: *{masked}*\n\nWe'll use these details for any future payouts.",
            [{"id": "ta_main_menu", "title": "🏠 Main Menu"}],
        ), pid, msg_id)


async def handle_payout_wallet_provider(to: str, pid: str, msg_id: Optional[str], session: dict, provider_id: str):
    provider = next((w for w in WALLET_PROVIDERS if w["id"] == provider_id), None)
    label = provider["label"] if provider else provider_id
    _set_data(session, {"payout_wallet_provider": label})
    _set_step(session, S_PAYOUT_WALLET_PHONE)
    await _send(to, _msg_text(
        to,
        f"📱 Enter the *phone number* linked to your *{label}* wallet\n\n📌 Example: *08012345678*"
    ), pid, msg_id)


async def handle_payout_wallet_phone(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    val = text.strip()
    if not re.match(r"^0[7-9]\d{9}$", val):
        await _send(to, _msg_text(to, "⚠️ Please enter a valid Nigerian phone number.\n\n📌 Example: *08012345678*"), pid, msg_id)
        return
    provider = _get_data(session).get("payout_wallet_provider", "wallet")
    needs_payment = _get_data(session).get("payout_from_buy_flow", False)
    _set_data(session, {"payout_wallet_phone": val, "payout_from_buy_flow": False})
    _set_step(session, "")
    masked = f"{val[:4]}•••••{val[-3:]}"
    if needs_payment:
        await _send(to, _msg_buttons(
            to,
            f"✅ *Payout details saved!*\n\n👛 Wallet: *{provider}*\n📱 Phone: *{masked}*\n\nWe'll send payouts directly to your wallet.",
            [
                {"id": "ta_start_payment", "title": "💳 Continue to payment"},
                {"id": "ta_main_menu",     "title": "🏠 Main menu"},
            ],
        ), pid, msg_id)
    else:
        await _send(to, _msg_buttons(
            to,
            f"✅ *Payout details saved!*\n\n👛 Wallet: *{provider}*\n📱 Phone: *{masked}*\n\nWe'll send payouts directly to your wallet.",
            [{"id": "ta_main_menu", "title": "🏠 Main Menu"}],
        ), pid, msg_id)


# ── Flow: Policy Lookup ────────────────────────────────────────────────────────

async def show_policies_menu(to: str, pid: str, msg_id: Optional[str], session: dict):
    _set_step(session, S_POLICY_LOOKUP_METHOD)
    await _send(to, _msg_buttons(
        to,
        "📋 *Check my policy*\n\nHow would you like to find your policy?",
        [
            {"id": "ta_lookup_phone",  "title": "📱 Use my phone number"},
            {"id": "ta_lookup_policy", "title": "🔢 Enter policy number"},
            {"id": "ta_lookup_flight", "title": "✈️ Search by flight number"},
        ],
    ), pid, msg_id)


async def handle_policy_lookup_method(to: str, pid: str, msg_id: Optional[str], session: dict, method: str):
    _set_data(session, {"policy_lookup_method": method})
    _set_step(session, S_POLICY_LOOKUP_VALUE)

    if method == "phone":
        await _send(to, _msg_buttons(
            to,
            "📱 We'll check for active policies linked to this WhatsApp number",
            [
                {"id": "ta_lookup_phone_confirm", "title": "✅ Continue"},
                {"id": "ta_check_policy",         "title": "↩️ Back"},
            ],
        ), pid, msg_id)
    elif method == "policy":
        await _send(to, _msg_text(to, "🔢 Please enter your *policy number*:\n\n📌 Example: *TA-2026-001234*"), pid, msg_id)
    elif method == "flight":
        _set_step(session, S_POLICY_LOOKUP_VALUE)
        await _send(to, _msg_text(to, "✈️ Please enter your *flight number*:\n\n📌 Example: *P47123*, *QI402*"), pid, msg_id)


async def handle_policy_lookup_value(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    method = _get_data(session).get("policy_lookup_method", "phone")

    # Flight lookup: first collect flight number, then ask for travel date
    if method == "flight" and not _get_data(session).get("lookup_flight_number"):
        _set_data(session, {"lookup_flight_number": text.strip().upper()})
        _set_step(session, S_POLICY_FLIGHT_DATE)
        await _send(to, _msg_text(
            to,
            "📅 Please enter your *travel date*:\n\n📌 Example: *25 Dec 2025* or *25/12/2025*"
        ), pid, msg_id)
        return

    await _send(to, _msg_text(to, "⏳ Looking up your policy..."), pid, msg_id)

    val = text.strip().upper()

    # Fix #16: filter _DEMO_POLICIES by entered value based on selected method
    all_policies = [
        {**p, "idx": i} for i, p in enumerate(_DEMO_POLICIES)
    ]
    if method == "phone" or text == "whatsapp_number":
        # phone lookup — return all (in production, filter by linked number)
        mock_policies = all_policies
    elif method == "policy":
        mock_policies = [p for p in all_policies if p["policy_number"].upper() == val]
    elif method == "flight":
        flight_no = _get_data(session).get("lookup_flight_number", val)
        _set_data(session, {"lookup_flight_number": None})  # reset for next search
        mock_policies = [p for p in all_policies if p["flight_number"].upper() == flight_no.upper()]
    else:
        mock_policies = all_policies

    if not mock_policies:
        await _send(to, _msg_buttons(
            to,
            "⚠️ We couldn't find an active policy linked to this number",
            [
                {"id": "ta_buy_cover",      "title": "✈️ Buy cover"},
                {"id": "ta_lookup_policy",  "title": "🔢 Enter policy number"},
                {"id": "ta_help",           "title": "🙋 Help"},
            ],
        ), pid, msg_id)
        return

    rows = [
        {
            "id": f"ta_policy_{p['idx']}",
            "title": f"{'🟢' if p['status'] == 'ACTIVE' else '🔴'} {p['policy_number']}",
            "description": f"{p['plan']} · {p['flight_number']}",
        }
        for p in mock_policies
    ]
    n = len(mock_policies)
    # Fix #12: clear lookup step so next free-text input isn't misrouted
    _set_step(session, "")
    await _send(to, _msg_list(
        to,
        f"📋 *{'Your policy' if n == 1 else f'{n} policies found'}*\n\nSelect a policy to view details:",
        "View policy",
        rows,
    ), pid, msg_id)


async def show_policy_detail(to: str, pid: str, msg_id: Optional[str], session: dict, idx: int):
    """Show policy detail. Replace mock_policies with real DB lookup."""
    mock_policies = [
        {
            "policy_number": "TA-2026-001234",
            "plan": "Local Travel Basic",
            "status": "ACTIVE",
            "airline": "Lufthansa",
            "flight_number": "LH456",
            "travel_date": "12 April 2026",
        },
        {
            "policy_number": "TA-2026-000891",
            "plan": "Local Travel Premium",
            "status": "EXPIRED",
            "airline": "Air Peace",
            "flight_number": "P47100",
            "travel_date": "15 Feb 2026",
        },
    ]
    if idx >= len(mock_policies):
        return
    p = mock_policies[idx]
    status_emoji = "✅" if p["status"] == "ACTIVE" else "🔴"
    await _send(to, _msg_list(
        to,
        f"📄 *Your Policy Details*\n\n"
        f"Policy No: *{p['policy_number']}*\n"
        f"Status: *{p['status']}* {status_emoji}\n"
        f"Airline: *{p['airline']}*\n"
        f"Flight: *{p['flight_number']}*\n"
        f"Date: *{p['travel_date']}*",
        "Choose option",
        [
            {"id": f"ta_download_policy_{idx}", "title": "📥 Download policy"},
            {"id": "ta_manage_alerts",          "title": "🔔 Manage alerts"},
            {"id": "ta_check_policy",           "title": "📋 All my policies"},
            {"id": "ta_help",                   "title": "🙋 Help"},
        ],
    ), pid, msg_id)


# ── Demo policy table (replaces real DB in production) ───────────────────────
# Used by: duplicate-policy guard, policy cancel, link flight, boarding upload guard.
_DEMO_POLICIES = [
    {"policy_number": "TA-2026-001234", "plan": "Local Travel Basic",   "status": "ACTIVE",  "flight_number": "LH456",  "cover_amount": 75000},
    {"policy_number": "TA-2026-000891", "plan": "Local Travel Premium", "status": "EXPIRED", "flight_number": "P47100", "cover_amount": 25000},
]


async def _cancel_policy_confirm(to: str, pid: str, msg_id: Optional[str], session: dict, idx: int):
    if idx >= len(_DEMO_POLICIES):
        return
    p = _DEMO_POLICIES[idx]
    await _send(to, _msg_buttons(
        to,
        f"🚫 *Cancel policy?*\n\n"
        f"🎫 {p['policy_number']}\n"
        f"✈️ Flight: *{p['flight_number']}*\n"
        f"🛡️ Plan: *{p['plan']}*\n\n"
        f"⚠️ Cancelling will remove your cover. Any refund will be processed within *5-7 business days*.",
        [
            {"id": f"ta_confirm_cancel_{idx}", "title": "✅ Yes, cancel policy"},
            {"id": f"ta_policy_{idx}",          "title": "⬅️ Keep my policy"},
        ],
    ), pid, msg_id)


async def _cancel_policy_execute(to: str, pid: str, msg_id: Optional[str], session: dict, idx: int):
    if idx >= len(_DEMO_POLICIES):
        return
    p = _DEMO_POLICIES[idx]
    refund = int(p["cover_amount"] * 0.8)
    await _send(to, _msg_buttons(
        to,
        f"✅ *Policy cancelled*\n\n"
        f"🎫 {p['policy_number']}\n"
        f"🔴 Status: *CANCELLED*\n\n"
        f"💰 Refund of *₦{refund:,}* will be processed in *5-7 business days*.",
        [
            {"id": "ta_check_policy", "title": "📋 My Policies"},
            {"id": "ta_main_menu",    "title": "🏠 Main Menu"},
        ],
    ), pid, msg_id)


# ── Flow: Boarding Pass ────────────────────────────────────────────────────────

async def start_boarding_upload(to: str, pid: str, msg_id: Optional[str], session: dict):
    # Fix #1: No-active-policy guard (mirrors React's startBoardingUpload)
    has_active = any(p["status"] == "ACTIVE" for p in _DEMO_POLICIES)
    if not has_active:
        await _send(to, _msg_buttons(
            to,
            "⚠️ *No active policy found*\n\n"
            "You need an active travel cover policy before uploading a boarding pass.",
            [
                {"id": "ta_buy_cover",    "title": "🛡️ Buy cover"},
                {"id": "ta_check_policy", "title": "📋 My policies"},
                {"id": "ta_main_menu",    "title": "🏠 Main menu"},
            ],
        ), pid, msg_id)
        return

    # Fix #2: Intermediate screen — matches React's startBoardingUpload (after guard)
    await _send(to, _msg_buttons(
        to,
        "🛂 *Upload boarding pass*\n\nPlease choose an option:",
        [
            {"id": "ta_boarding_upload_start", "title": "📄 Upload for your policy"},
            {"id": "ta_help",                  "title": "🙋 Help"},
        ],
    ), pid, msg_id)


async def start_boarding_upload_prompt(to: str, pid: str, msg_id: Optional[str], session: dict):
    """Fix #2: The actual upload instructions screen (mirrors React's startBoardingUploadPrompt)."""
    _set_step(session, S_BOARDING_UPLOAD)
    await _send(to, _msg_buttons(
        to,
        "📎 *Please upload a clear image or PDF of your boarding pass*\n\n"
        "Accepted formats: *JPEG, PDF, GIF, TIFF, PNG*\n"
        "Maximum size: *20 MB*\n\n"
        "Make sure we can see:\n"
        "✅ Passenger name or names\n"
        "✅ Booking reference\n"
        "✅ Airport details\n"
        "✅ Flight number\n"
        "✅ Travel date\n\n"
        "Type *0* to go back",
        [{"id": "ta_boarding_choose_file", "title": "📎 Choose file to upload"}],
    ), pid, msg_id)


# Fix #15: accepted MIME types (mirrors React's allowedBoardingFormats)
_BOARDING_ALLOWED_MIMES = {
    "image/jpeg", "image/jpg", "image/png", "image/gif", "image/tiff",
    "application/pdf",
}


async def handle_boarding_pass_received(to: str, pid: str, msg_id: Optional[str], session: dict, media_type: str = "image", mime_type: str = ""):
    """Handle image/document upload from WhatsApp."""
    # Fix #15: validate MIME type before processing
    if mime_type and mime_type not in _BOARDING_ALLOWED_MIMES:
        await err_boarding_bad_file(to, pid, msg_id)
        return
    await _send(to, _msg_text(to, "✅ File received\n\n⏳ Verifying..."), pid, msg_id)

    # Simulate verification (85% pass). Replace with real OCR/verification.
    success = random.random() > 0.15
    if success:
        _set_data(session, {"boarding_pass_uploaded": True})
        _set_step(session, "")
        # Fix #14: success buttons match React (check_eligibility + 99)
        await _send(to, _msg_buttons(
            to,
            "✅ *Boarding pass upload confirmed*\n\n"
            "Boarding pass information:\n"
            "✈️ Flight: *P47123*\n"
            "📅 Date: *12 April 2026*\n\n"
            "What would you like to do next?",
            [
                {"id": "ta_link_flight",       "title": "🔗 Link to my policy"},
                {"id": "ta_check_eligibility", "title": "✅ Check eligibility"},
                {"id": "ta_main_menu",         "title": "🏠 Main menu"},
            ],
        ), pid, msg_id)
    else:
        await _send(to, _msg_buttons(
            to,
            "⚠️ We couldn't read the boarding pass clearly\n\n"
            "Please upload a clearer image showing:\n"
            "✅ Name\n✅ Flight number\n✅ Date",
            [
                {"id": "ta_upload_boarding", "title": "📎 Upload again"},
                {"id": "ta_help",            "title": "🙋 Help"},
            ],
        ), pid, msg_id)


# ── Flow: Link Flight ──────────────────────────────────────────────────────────

async def start_link_flight(to: str, pid: str, msg_id: Optional[str], session: dict):
    """Fix #3: Show policy selection list before entering flight number (mirrors React's startLinkFlight)."""
    active = [p for p in _DEMO_POLICIES if p["status"] == "ACTIVE"]
    if not active:
        await _send(to, _msg_buttons(
            to,
            "⚠️ *No active policies*\n\n"
            "You need an active travel cover policy to link a flight to.",
            [
                {"id": "ta_buy_cover",  "title": "🛡️ Buy cover"},
                {"id": "ta_main_menu", "title": "🏠 Main menu"},
            ],
        ), pid, msg_id)
        return
    rows = [
        {
            "id": f"ta_link_flight_policy_{i}",
            "title": p["policy_number"],
            "description": f"{p['plan']} · {p['flight_number']}",
        }
        for i, p in enumerate(active)
    ]
    await _send(to, _msg_list(
        to,
        "✈️ *Link a flight*\n\nSelect the policy to link a flight to:",
        "Select policy",
        rows,
    ), pid, msg_id)


async def handle_link_policy_select(to: str, pid: str, msg_id: Optional[str], session: dict, idx: int):
    """Fix #4: Handle ta_link_flight_policy_X button — store chosen policy and ask for flight number."""
    active = [p for p in _DEMO_POLICIES if p["status"] == "ACTIVE"]
    if idx >= len(active):
        await err_invalid_input(to, pid, msg_id)
        return
    policy = active[idx]
    _set_data(session, {"linking_policy_number": policy["policy_number"]})
    _set_step(session, S_LINK_ENTER_FLIGHT)
    await _send(to, _msg_text(
        to,
        f"✈️ *Link a flight to {policy['policy_number']}*\n\n"
        "Please enter the *flight number* you'd like to link\n\n"
        "📌 Example: *P47123*, *AA123*"
    ), pid, msg_id)


async def handle_link_flight_entered(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    """Fix #5: Validate flight number and do demo flight lookup before confirming."""
    fn = text.strip().upper()
    if not re.match(r"^[A-Z]{1,3}\d{1,4}[A-Z]?$", fn):
        await _send(to, _msg_text(
            to,
            f"⚠️ \"*{text.strip()}*\" doesn't look like a valid flight number.\n\n📌 Example: *P47123*, *AA123*"
        ), pid, msg_id)
        return

    # Demo flight lookup — simulate occasional not-found
    found = random.random() > 0.1
    if not found:
        await _send(to, _msg_buttons(
            to,
            f"⚠️ *Flight {fn} not found*\n\n"
            "We couldn't find this flight in our records. Please check the number and try again.",
            [
                {"id": "ta_retry_link_flight", "title": "🔄 Try again"},
                {"id": "ta_main_menu",         "title": "🏠 Main menu"},
            ],
        ), pid, msg_id)
        return

    _set_data(session, {"link_flight_number": fn})
    policy_no = _get_data(session).get("linking_policy_number", "—")
    await _send(to, _msg_buttons(
        to,
        f"✈️ *Confirm flight link*\n\n"
        f"✈️ Flight: *{fn}*\n"
        f"📄 Policy: *{policy_no}*\n\n"
        "Would you like to link this flight to your policy?",
        [
            {"id": "ta_confirm_link_flight", "title": "✅ Yes, link flight"},
            {"id": "ta_retry_link_flight",   "title": "🔄 Different flight"},
            {"id": "ta_main_menu",           "title": "❌ Cancel"},
        ],
    ), pid, msg_id)


async def confirm_link_flight(to: str, pid: str, msg_id: Optional[str], session: dict):
    """Fix #7: Use session-stored policy number instead of hardcoded value."""
    fn = _get_data(session).get("link_flight_number", "—")
    policy_no = _get_data(session).get("linking_policy_number", "—")
    _set_step(session, "")
    await _send(to, _msg_buttons(
        to,
        f"✅ *Flight linked!*\n\n"
        f"✈️ *{fn}* is now linked to policy *{policy_no}*\n\n"
        "🔔 We'll monitor this flight and alert you automatically if there's a disruption.",
        [
            {"id": "ta_check_policy", "title": "📋 View policy"},
            {"id": "ta_main_menu",    "title": "🏠 Main menu"},
        ],
    ), pid, msg_id)


# ── Flow: Update Details ───────────────────────────────────────────────────────

async def start_update_details(to: str, pid: str, msg_id: Optional[str], session: dict):
    """Entry point: ask who to update (just me / me and others)."""
    _set_step(session, S_UPDATE_WHO)
    await _send(to, _msg_buttons(
        to,
        "✏️ *Update your details*\n\nWho would you like to update details for?",
        [
            {"id": "ta_update_who_solo",  "title": "👤 Just me"},
            {"id": "ta_update_who_group", "title": "👥 Me and others (Same Booking)"},
            {"id": "ta_main_menu",        "title": "🏠 Main menu"},
        ],
    ), pid, msg_id)


async def show_update_menu_solo(to: str, pid: str, msg_id: Optional[str], session: dict):
    """Solo update menu — personal fields only."""
    _set_step(session, S_UPDATE_MENU)
    await _send(to, _msg_list(
        to,
        "✏️ *Update your details*\n\nWhat would you like to update?",
        "Choose field",
        [
            {"id": "ta_update_name",  "title": "👤 Name"},
            {"id": "ta_update_email", "title": "📧 Email address"},
            {"id": "ta_update_bank",  "title": "🏦 Bank/payout details"},
            {"id": "ta_main_menu",    "title": "🏠 Main menu"},
        ],
    ), pid, msg_id)


async def show_update_menu_group(to: str, pid: str, msg_id: Optional[str], session: dict):
    """Group update menu — personal fields + traveller names."""
    _set_step(session, S_UPDATE_MENU_GROUP)
    traveller_names = _get_data(session).get("traveller_names", [])
    traveller_label = (
        f"👥 Traveller names ({len(traveller_names)})"
        if traveller_names
        else "👥 Traveller names"
    )
    # buttons: max 3 per message; use list since we have 5 options
    rows = [
        {"id": "ta_update_name",        "title": "👤 My name"},
        {"id": "ta_update_email",        "title": "📧 Email address"},
        {"id": "ta_update_bank",         "title": "🏦 Bank/payout details"},
        {"id": "ta_update_travellers",   "title": traveller_label},
        {"id": "ta_main_menu",           "title": "🏠 Main menu"},
    ]
    await _send(to, _msg_list(
        to,
        "✏️ *Update details — Group*\n\nWhat would you like to update?",
        "Choose field",
        rows,
    ), pid, msg_id)


async def show_update_traveller_list(to: str, pid: str, msg_id: Optional[str], session: dict):
    """Show the list of travellers to select one for editing."""
    names = _get_data(session).get("traveller_names", [])
    kyc_name = _get_data(session).get("kyc_name", "")
    display_names = names if names else ([kyc_name] if kyc_name else [])

    if not display_names:
        await _send(to, _msg_buttons(
            to,
            "⚠️ No traveller names found.\n\nPlease complete a *Buy Cover* purchase first so we can see your travellers here.",
            [
                {"id": "ta_update_details", "title": "⬅️ Back"},
                {"id": "ta_main_menu",      "title": "🏠 Main menu"},
            ],
        ), pid, msg_id)
        return

    _set_step(session, S_UPDATE_TRAVELLER_SELECT)
    rows = [
        {"id": f"ta_update_traveller_{i}", "title": f"👤 Traveller {i+1}: {n}"}
        for i, n in enumerate(display_names)
    ]
    rows.append({"id": "ta_update_details", "title": "⬅️ Back"})
    await _send(to, _msg_list(
        to,
        "👥 *Select the traveller to update:*",
        "Select traveller",
        rows,
    ), pid, msg_id)


async def handle_update_traveller_select(to: str, pid: str, msg_id: Optional[str], session: dict, index: int):
    """User picked a traveller to edit."""
    names = _get_data(session).get("traveller_names", [])
    kyc_name = _get_data(session).get("kyc_name", "")
    display_names = names if names else ([kyc_name] if kyc_name else [])
    if index >= len(display_names):
        return
    name = display_names[index]
    is_kyc = len(names) == 0
    _set_data(session, {"updating_traveller_index": index, "updating_traveller_is_kyc": is_kyc})
    _set_step(session, S_UPDATE_TRAVELLER_NAME)
    await _send(to, _msg_text(
        to,
        f"✏️ Enter the new name for *Traveller {index+1}* (*{name}*):"
    ), pid, msg_id)


async def handle_update_traveller_name(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    """Save updated traveller name."""
    val = text.strip()
    if len(val) < 2:
        await _send(to, _msg_text(to, "⚠️ Please enter a valid full name."), pid, msg_id)
        return
    d = _get_data(session)
    idx = d.get("updating_traveller_index", 0)
    is_kyc = d.get("updating_traveller_is_kyc", False)
    if is_kyc:
        old_name = d.get("kyc_name", "Traveller")
        _set_data(session, {"kyc_name": val, "updating_traveller_index": None, "updating_traveller_is_kyc": False})
        _set_step(session, "")
        await _send(to, _msg_buttons(
            to,
            f"✅ *Name updated*\n\n*{old_name}* → *{val}*\n\nIs there anything else you'd like to update?",
            [
                {"id": "ta_update_details", "title": "✏️ Update another detail"},
                {"id": "ta_main_menu",      "title": "🏠 Main menu"},
            ],
        ), pid, msg_id)
    else:
        names = list(d.get("traveller_names", []))
        old_name = names[idx] if idx < len(names) else "Traveller"
        if idx < len(names):
            names[idx] = val
        _set_data(session, {"traveller_names": names, "updating_traveller_index": None, "updating_traveller_is_kyc": False})
        _set_step(session, "")
        await _send(to, _msg_buttons(
            to,
            f"✅ *Traveller {idx+1} updated*\n\n*{old_name}* → *{val}*\n\nIs there anything else you'd like to update?",
            [
                {"id": "ta_update_details", "title": "✏️ Update another detail"},
                {"id": "ta_main_menu",      "title": "🏠 Main menu"},
            ],
        ), pid, msg_id)


async def handle_update_field(to: str, pid: str, msg_id: Optional[str], session: dict, field: str):
    config = {
        "name":  (S_UPDATE_NAME,  "full name",       "Enter your full name"),
        "email": (S_UPDATE_EMAIL, "email address",    "e.g. you@example.com"),
        "phone": (S_UPDATE_PHONE, "phone number",     "e.g. 08012345678"),
        "bank":  (S_UPDATE_BANK,  "account number",   "Enter 10-digit account number"),
    }
    cfg = config.get(field)
    if not cfg:
        return
    step, label, hint = cfg
    _set_data(session, {"updating_field": field})
    _set_step(session, step)
    await _send(to, _msg_text(to, f"Please enter your new *{label}*:\n\n📌 {hint}"), pid, msg_id)


async def handle_update_value(to: str, pid: str, msg_id: Optional[str], session: dict, text: str, field: str):
    val = text.strip()
    if field == "phone" and not re.match(r"^0[7-9]\d{9}$", val):
        await _send(to, _msg_text(to, "⚠️ Please enter a valid Nigerian phone number (e.g. 08012345678)."), pid, msg_id)
        return
    if field == "email" and not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", val):
        await _send(to, _msg_text(to, "⚠️ Please enter a valid email address."), pid, msg_id)
        return
    if field == "bank":
        # Step 1: validate 10-digit account number, then ask for bank name
        if not re.match(r"^\d{10}$", val):
            await _send(to, _msg_text(to, "⚠️ Please enter a valid *10-digit account number*.\n\n📌 Example: *0123456789*"), pid, msg_id)
            return
        masked = f"••••••••{val[-2:]}"
        _set_data(session, {"payout_bank_account": val, "updating_bank_details": True})
        _set_step(session, S_UPDATE_BANK_NAME)
        await _send(to, _msg_text(
            to,
            "🏦 Please enter at least *3 characters* of your bank name:\n\n📌 Examples: *Zen* (Zenith), *Wem* (Wema), *GT* (GTBank)"
        ), pid, msg_id)
        return

    label_map = {"name": "Name", "email": "Email", "phone": "Phone number"}
    label = label_map.get(field, field)
    _set_data(session, {f"updated_{field}": val})
    _set_step(session, "")
    await _send(to, _msg_buttons(
        to,
        f"✅ *{label} updated successfully*\n\nIs there anything else you'd like to update?",
        [
            {"id": "ta_update_details", "title": "✏️ Update another detail"},
            {"id": "ta_main_menu",      "title": "🏠 Main menu"},
        ],
    ), pid, msg_id)


async def handle_update_bank_name(to: str, pid: str, msg_id: Optional[str], session: dict, text: str):
    """Step 2 of bank update: user entered 3 chars, show full bank list."""
    if len(text.strip()) < 3:
        await _send(to, _msg_text(
            to,
            "⚠️ Please enter at least *3 characters* of your bank name.\n\n📌 Examples: *Zen* (Zenith), *Wem* (Wema), *GT* (GTBank)"
        ), pid, msg_id)
        return
    all_banks = sorted(NIGERIAN_BANKS)
    rows = [{"id": f"ta_sel_bank_{b.replace(' ', '_')}", "title": b} for b in all_banks[:10]]
    footer_note = f"Showing 1–{min(10, len(all_banks))} of {len(all_banks)} banks (alphabetical). Reply with bank name if not listed."
    await _send(to, _msg_list(to, f"🏦 *Select your bank*\n\n{footer_note}", "Select bank", rows), pid, msg_id)


# ── Flow: Help / FAQ ───────────────────────────────────────────────────────────

async def show_help(to: str, pid: str, msg_id: Optional[str], session: dict):
    _set_step(session, S_HELP_MENU)
    rows = [{"id": f"ta_faq_{f['id']}", "title": f["question"]} for f in FAQ_ITEMS]
    await _send(to, _msg_list(
        to,
        "❓ *Help*\n\nWhat do you need help with?",
        "Choose topic",
        rows,
    ), pid, msg_id)


async def show_faq_answer(to: str, pid: str, msg_id: Optional[str], faq_id: str):
    faq = next((f for f in FAQ_ITEMS if f["id"] == faq_id), None)
    if not faq:
        return
    await _send(to, _msg_buttons(
        to,
        f"❓ {faq['answer']}",
        [
            {"id": "ta_back_to_help", "title": "⬅️ Back to help"},
            {"id": "ta_main_menu",    "title": "🏠 Main menu"},
        ],
    ), pid, msg_id)


async def show_contact_support(to: str, pid: str, msg_id: Optional[str]):
    await _send(to, _msg_buttons(
        to,
        "📞 *Contact Support*\n\n"
        "🌐 www.travelassist.ng\n"
        "📧 support@travelassist.ng\n"
        "📱 WhatsApp: +234 800 TRAVEL\n"
        "🕐 Available 24/7",
        [
            {"id": "ta_back_to_help", "title": "⬅️ Back to help"},
            {"id": "ta_main_menu",    "title": "🏠 Main menu"},
        ],
    ), pid, msg_id)


# ── Proactive Alerts ───────────────────────────────────────────────────────────

async def send_flight_delay_alert(to: str, pid: str, flight_number: str, new_departure: str):
    """
    Section 4.9 — Flight delay alert.
    Called externally by monitoring service, not user-initiated.
    """
    payload = _msg_buttons(
        to,
        f"⚠️ *Flight Alert*\n"
        f"Your flight *{flight_number}* has been delayed ⏰\n"
        f"New departure time: *{new_departure}*\n\n"
        "What would you like to do?",
        [
            {"id": "ta_check_eligibility", "title": "🧾 Check eligibility"},
            {"id": "ta_upload_boarding",   "title": "🛂 Upload boarding pass"},
            {"id": "ta_help",              "title": "🙋 Get help"},
        ],
    )
    await send_whatsapp_payload(payload, phone_number_id=pid, source="travelassist_alert")


async def send_policy_issued_alert(to: str, pid: str, policy_number: str, flight_number: str, travel_date: str):
    """
    Policy issued confirmation alert.
    Called after successful payment/policy creation.
    """
    payload = _msg_list(
        to,
        f"✅ *Policy Issued*\n"
        f"Your TravelAssist cover is active\n"
        f"📄 Policy No: *{policy_number}*\n"
        f"✈️ Flight: *{flight_number}*\n"
        f"📅 Date: *{travel_date}*",
        "Choose option",
        [
            {"id": "ta_view_policy",   "title": "📥 Download policy"},
            {"id": "ta_enable_alerts", "title": "🔔 Turn on alerts"},
            {"id": "ta_main_menu",     "title": "🏠 Main menu"},
        ],
    )
    await send_whatsapp_payload(payload, phone_number_id=pid, source="travelassist_alert")


async def send_disruption_pipeline(to: str, pid: str, flight_number: str, delay_mins: int, payout_amount: int):
    """
    5-stage automatic payout pipeline.
    Called by monitoring service when disruption threshold is reached.
    """
    # Stage 1 — Detection
    await send_whatsapp_payload(_msg_text(
        to,
        f"⚠️ *Flight disruption detected*\n\n"
        f"✈️ Your flight *{flight_number}* has been delayed by *{delay_mins} minutes*\n\n"
        f"🛡️ Good news — you have active TravelAssist cover!\n\n"
        f"We're checking your eligibility for a payout now..."
    ), phone_number_id=pid, source="travelassist_alert")

    # Stage 2 — Threshold reached
    await send_whatsapp_payload(_msg_text(
        to,
        f"📊 *Payout threshold reached*\n\n"
        f"Your delay of *{delay_mins} minutes* has crossed the *60-minute threshold*\n\n"
        f"💰 Estimated payout: *₦{payout_amount:,}*\n\nVerifying your eligibility..."
    ), phone_number_id=pid, source="travelassist_alert")

    # Stage 3 — Eligibility confirmed
    await send_whatsapp_payload(_msg_text(
        to,
        f"✅ *You're eligible for a payout!*\n\n"
        f"✈️ Flight: *{flight_number}* ({delay_mins}-min delay)\n"
        f"💰 Payout amount: *₦{payout_amount:,}*\n\nInitiating transfer to your account..."
    ), phone_number_id=pid, source="travelassist_alert")

    # Stage 4 — Payout initiated
    await send_whatsapp_payload(_msg_text(
        to,
        f"🏦 *Payout initiated*\n\n"
        f"💰 ₦{payout_amount:,} is on its way to your registered account\n"
        f"⏳ Processing time: *2–4 hours*\n\n"
        f"You'll receive a confirmation once the transfer is complete."
    ), phone_number_id=pid, source="travelassist_alert")

    # Stage 5 — Complete
    await send_whatsapp_payload(_msg_buttons(
        to,
        f"🎉 *Payout complete!*\n\n"
        f"✅ *₦{payout_amount:,}* has been transferred to your account\n"
        f"🕐 Transferred: just now\n\n"
        f"Thank you for flying with TravelAssist cover. ✈️",
        [
            {"id": "ta_check_policy", "title": "📋 View my policy"},
            {"id": "ta_main_menu",    "title": "🏠 Main menu"},
        ],
    ), phone_number_id=pid, source="travelassist_alert")


# ── Error Messages (spec 6.1 – 6.15) ──────────────────────────────────────────

async def err_invalid_input(to: str, pid: str, msg_id: Optional[str]):
    """6.1 — Invalid input"""
    await _send(to, _msg_buttons(
        to,
        "⚠️ Sorry, I didn't understand that\n\nPlease reply with one of the menu numbers\nor type *00* for main menu",
        [
            {"id": "ta_main_menu", "title": "🏠 Main menu"},
            {"id": "ta_help",      "title": "🙋 Help"},
        ],
    ), pid, msg_id)


async def err_flight_not_found(to: str, pid: str, msg_id: Optional[str], session: dict):
    """6.2 — Flight not found"""
    _set_step(session, S_FLIGHT_NOT_FOUND)
    await _send(to, _msg_buttons(
        to,
        "⚠️ I couldn't find that flight yet\n\nPlease check the number and try again",
        [
            {"id": "ta_retry_flight", "title": "🔄 Try again"},
            {"id": "ta_main_menu",    "title": "🏠 Main menu"},
        ],
    ), pid, msg_id)


async def err_flight_cancelled(to: str, pid: str, msg_id: Optional[str], session: dict, flight_number: str):
    """6.3 — Flight cancelled"""
    await _send(to, _msg_buttons(
        to,
        f"❌ *Flight {flight_number} is Cancelled*\n\n"
        "⚠️ Cover cannot be purchased for a cancelled flight.\n\nWhat would you like to do?",
        [
            {"id": "ta_buy_cover", "title": "✈️ Cover a different flight"},
            {"id": "ta_help",      "title": "💬 Get Support"},
            {"id": "ta_main_menu", "title": "🏠 Main Menu"},
        ],
    ), pid, msg_id)


async def err_flight_departed(to: str, pid: str, msg_id: Optional[str], session: dict, flight_number: str):
    """6.4 — Flight already departed"""
    await _send(to, _msg_buttons(
        to,
        f"⚠️ *Flight {flight_number} Has Already Departed*\n\n"
        "⚠️ Cover cannot be purchased after a flight has departed.\n\nWhat would you like to do?",
        [
            {"id": "ta_buy_cover", "title": "✈️ Cover a different flight"},
            {"id": "ta_help",      "title": "💬 Get Support"},
            {"id": "ta_main_menu", "title": "🏠 Main Menu"},
        ],
    ), pid, msg_id)


async def err_duplicate_policy(to: str, pid: str, msg_id: Optional[str], session: dict, flight_number: str):
    """6.5 — Duplicate policy"""
    await _send(to, _msg_buttons(
        to,
        f"⚠️ *You Already Have Cover for This Flight*\n\n"
        f"✈️ Flight *{flight_number}* already has an active policy.\n\nPurchasing a duplicate policy is not allowed.",
        [
            {"id": "ta_check_policy", "title": "📋 View Existing Policy"},
            {"id": "ta_buy_cover",    "title": "🛡️ Cover a Different Flight"},
            {"id": "ta_main_menu",    "title": "🏠 Main Menu"},
        ],
    ), pid, msg_id)


async def err_kyc_failed(to: str, pid: str, msg_id: Optional[str]):
    """6.6 — KYC verification failed"""
    await _send(to, _msg_buttons(
        to,
        "⚠️ We could not verify your details automatically.",
        [
            {"id": "ta_kyc_bvn",         "title": "🏦 Try BVN again"},
            {"id": "ta_kyc_nin",         "title": "🆔 Try NIN instead"},
            {"id": "ta_contact_support", "title": "🙋 Get help"},
        ],
    ), pid, msg_id)


async def err_kyc_max_retries(to: str, pid: str, msg_id: Optional[str]):
    """6.7 — KYC max retries exceeded"""
    await _send(to, _msg_buttons(
        to,
        "⚠️ We could not verify your details automatically.\n\nPlease contact support for assistance.",
        [
            {"id": "ta_kyc_bvn",         "title": "🏦 Try BVN again"},
            {"id": "ta_kyc_nin",         "title": "🆔 Try NIN instead"},
            {"id": "ta_contact_support", "title": "🙋 Get help"},
        ],
    ), pid, msg_id)


async def err_payment_failed(to: str, pid: str, msg_id: Optional[str]):
    """6.8 — Payment failed"""
    await _send(to, _msg_list(
        to,
        "❌ Payment was not successful\n\nPlease choose what to do next:",
        "Choose option",
        [
            {"id": "ta_pay_card",       "title": "💳 Try again"},
            {"id": "ta_pay_bank",       "title": "🏦 Use bank transfer"},
            {"id": "ta_pay_ussd",       "title": "#️⃣ Use USSD"},
            {"id": "ta_contact_support","title": "🙋 Get help"},
        ],
    ), pid, msg_id)


async def err_payment_pending(to: str, pid: str, msg_id: Optional[str]):
    """6.9 — Payment pending"""
    await _send(to, _msg_buttons(
        to,
        "⏳ We haven't confirmed your payment yet\n\nPlease wait a little and try again",
        [
            {"id": "ta_check_payment_status", "title": "🔄 Refresh status"},
            {"id": "ta_help",                 "title": "🙋 Help"},
        ],
    ), pid, msg_id)


async def err_payment_max_retries(to: str, pid: str, msg_id: Optional[str]):
    """6.10 — Payment max retries"""
    await _send(to, _msg_list(
        to,
        "❌ Payment was not successful\n\nPlease choose what to do next:",
        "Choose option",
        [
            {"id": "ta_pay_card",        "title": "💳 Try card again"},
            {"id": "ta_pay_bank",        "title": "🏦 Use bank transfer"},
            {"id": "ta_pay_ussd",        "title": "#️⃣ Use USSD"},
            {"id": "ta_contact_support", "title": "🙋 Get help"},
        ],
    ), pid, msg_id)


async def err_policy_not_found(to: str, pid: str, msg_id: Optional[str]):
    """6.11 — Policy not found"""
    await _send(to, _msg_buttons(
        to,
        "⚠️ We couldn't find an active policy linked to this number",
        [
            {"id": "ta_buy_cover",      "title": "✈️ Buy cover"},
            {"id": "ta_lookup_policy",  "title": "🔢 Enter policy number"},
            {"id": "ta_help",           "title": "🙋 Help"},
        ],
    ), pid, msg_id)


async def err_boarding_bad_file(to: str, pid: str, msg_id: Optional[str]):
    """6.12 — Unsupported file type"""
    await _send(to, _msg_buttons(
        to,
        "❌ *Unsupported file type*\n\n✅ Accepted: *JPEG, PDF, GIF, TIFF, PNG*",
        [
            {"id": "ta_upload_boarding", "title": "📎 Upload again"},
            {"id": "ta_help",            "title": "🙋 Help"},
        ],
    ), pid, msg_id)


async def err_boarding_failed(to: str, pid: str, msg_id: Optional[str]):
    """6.13 — Boarding pass unreadable"""
    await _send(to, _msg_buttons(
        to,
        "⚠️ We couldn't read the boarding pass clearly\n\n"
        "Please upload a clearer image showing:\n"
        "✅ Name\n✅ Flight number\n✅ Date",
        [
            {"id": "ta_upload_boarding", "title": "📎 Upload again"},
            {"id": "ta_help",            "title": "🙋 Help"},
        ],
    ), pid, msg_id)


async def err_session_expired(to: str, pid: str, msg_id: Optional[str]):
    """6.14 — Session expired"""
    await _send(to, _msg_buttons(
        to,
        "⏳ Your previous session expired\n\nWould you like to continue where you stopped?",
        [
            {"id": "ta_resume_session", "title": "✅ Yes, continue"},
            {"id": "ta_main_menu",      "title": "🏠 Start again"},
        ],
    ), pid, msg_id)


async def err_system_unavailable(to: str, pid: str, msg_id: Optional[str]):
    """6.15 — System unavailable"""
    await _send(to, _msg_buttons(
        to,
        "⚠️ We're unable to complete that right now\n\nPlease try again shortly",
        [
            {"id": "ta_system_retry", "title": "🔄 Try again"},
            {"id": "ta_help",         "title": "🙋 Help"},
        ],
    ), pid, msg_id)


# ── Button router ──────────────────────────────────────────────────────────────

async def _handle_button(
    btn_id: str,
    to: str,
    pid: str,
    msg_id: Optional[str],
    session: dict,
):
    d = _get_data(session)

    # ── Global navigation ─────────────────────────────────────────────────────
    if btn_id in ("ta_main_menu", "00", "0", "9"):
        _reset_flow(session)
        await send_main_menu(to, pid, msg_id)
        return

    if btn_id == "99":
        _reset_flow(session)
        await _send(to, _msg_text(to, "❌ Cancelled. Here's what you can do:"), pid, msg_id)
        await send_main_menu(to, pid, msg_id)
        return

    if btn_id == "ta_help" or btn_id == "ta_back_to_help":
        await show_help(to, pid, msg_id, session)
        return

    if btn_id == "ta_contact_support":
        await show_contact_support(to, pid, msg_id)
        return

    # ── Buy cover ─────────────────────────────────────────────────────────────
    if btn_id == "ta_buy_cover":
        await start_buy_cover(to, pid, msg_id, session)
        return

    if btn_id in ("ta_cover_solo", "ta_cover_group"):
        await handle_cover_type(to, pid, msg_id, session, btn_id)
        return

    if btn_id in ("ta_trip_oneway", "ta_trip_return"):
        await handle_trip_type(to, pid, msg_id, session, btn_id)
        return

    if btn_id == "ta_confirm_trip":
        await _flight_lookup_then_plan(to, pid, msg_id, session)
        return

    if btn_id == "ta_edit_trip":
        await start_buy_cover(to, pid, msg_id, session)
        return

    if btn_id == "ta_plan_basic":
        await handle_select_plan(to, pid, msg_id, session, "basic")
        return

    if btn_id == "ta_plan_premium":
        await handle_select_plan(to, pid, msg_id, session, "premium")
        return

    if btn_id == "ta_cancel_purchase":
        _reset_flow(session)
        await send_main_menu(to, pid, msg_id)
        return

    if btn_id == "ta_ask_question":
        await show_help(to, pid, msg_id, session)
        return

    if btn_id == "ta_retry_flight":
        _set_step(session, S_BUY_FLIGHT_NUMBER)
        await _send(to, _msg_text(to, "✈️ Please re-enter your flight number:\n\n📌 Example: *P47123*"), pid, msg_id)
        return

    if btn_id.startswith("ta_dep_"):
        code = btn_id.replace("ta_dep_", "")
        await handle_depart_airport_select(to, pid, msg_id, session, code)
        return

    if btn_id.startswith("ta_arr_"):
        code = btn_id.replace("ta_arr_", "")
        await handle_arrive_airport_select(to, pid, msg_id, session, code)
        return

    if btn_id == "ta_review_trip":
        await send_trip_summary(to, pid, msg_id, session)
        return

    # ── KYC ──────────────────────────────────────────────────────────────────
    if btn_id == "ta_proceed_kyc":
        await start_kyc(to, pid, msg_id, session)
        return

    if btn_id == "ta_kyc_bvn":
        await handle_kyc_type(to, pid, msg_id, session, "bvn")
        return

    if btn_id == "ta_kyc_nin":
        await handle_kyc_type(to, pid, msg_id, session, "nin")
        return

    if btn_id == "ta_kyc_consent_yes":
        await handle_kyc_consent(to, pid, msg_id, session, True)
        return

    if btn_id == "ta_kyc_consent_no":
        await handle_kyc_consent(to, pid, msg_id, session, False)
        return

    if btn_id == "ta_confirm_kyc":
        await process_kyc(to, pid, msg_id, session)
        return

    if btn_id in ("ta_change_kyc", "ta_retry_kyc"):
        await start_kyc(to, pid, msg_id, session)
        return

    # ── Payment ───────────────────────────────────────────────────────────────
    if btn_id == "ta_proceed_payment":
        # Mirror React: route through payout options first so user sets up
        # how they receive money before completing the premium payment.
        _set_data(session, {"payout_from_buy_flow": True})
        await start_payout_options(to, pid, msg_id, session)
        return

    if btn_id == "ta_start_payment":
        await start_payment(to, pid, msg_id, session)
        return

    if btn_id in ("ta_change_payment", "ta_retry_payment"):
        await start_payment(to, pid, msg_id, session)
        return

    if btn_id == "ta_pay_bank":
        await handle_payment_method(to, pid, msg_id, session, "bank")
        return

    if btn_id == "ta_pay_card":
        await handle_payment_method(to, pid, msg_id, session, "card")
        return

    if btn_id == "ta_pay_ussd":
        await handle_payment_method(to, pid, msg_id, session, "ussd")
        return

    if btn_id == "ta_pay_wallet":
        await handle_payment_method(to, pid, msg_id, session, "wallet")
        return

    if btn_id.startswith("ta_pay_wallet_"):
        provider_id = btn_id.replace("ta_pay_wallet_", "")
        await handle_wallet_provider(to, pid, msg_id, session, provider_id)
        return

    if btn_id in ("ta_confirm_bank_pay", "ta_confirm_card_pay", "ta_confirm_ussd_pay", "ta_confirm_wallet_pay"):
        await process_payment(to, pid, msg_id, session)
        return

    if btn_id == "ta_check_payment_status":
        # Fix #10: always confirm payment (mirrors React's always-succeed demo behavior)
        policy_no = _gen_policy_number()
        d = _get_data(session)
        _set_data(session, {"policy_number": policy_no})
        _reset_flow(session)
        await _send(to, _msg_text(to, "✅ *Payment confirmed!* Issuing your policy..."), pid, msg_id)
        await _send(to, _msg_list(
            to,
            f"📤 *Policy issued!*\n\n🎫 Policy No: *{policy_no}*\n✈️ Flight: *{d.get('flight_number', '—')}*\n📅 Date: *{d.get('travel_date', '—')}*\n\nWhat would you like to do next?",
            "Choose option",
            [
                {"id": "ta_enable_alerts",   "title": "🔔 Turn on flight alerts"},
                {"id": "ta_view_policy",     "title": "📤 View my policy"},
                {"id": "ta_upload_boarding", "title": "🛂 Upload boarding pass"},
                {"id": "ta_main_menu",       "title": "🏠 Main menu"},
            ],
        ), pid, msg_id)
        return

    if btn_id == "ta_enable_alerts" or btn_id == "ta_manage_alerts":
        await _send(to, _msg_buttons(
            to,
            "🔔 *Flight alerts enabled*\n\n"
            "We'll automatically monitor your flight and notify you of any disruptions. "
            "If you're eligible for a payout, we'll process it automatically — no action needed from you.",
            [{"id": "ta_main_menu", "title": "🏠 Main menu"}],
        ), pid, msg_id)
        return

    if btn_id == "ta_check_eligibility":
        await _send(to, _msg_buttons(
            to,
            "⏳ Your case needs further review",
            [
                {"id": "ta_contact_support", "title": "🙋 Speak to support"},
                {"id": "ta_view_policy",     "title": "📄 View my policy"},
            ],
        ), pid, msg_id)
        return

    # ── Payout options ────────────────────────────────────────────────────────
    if btn_id == "ta_payout_options":
        await start_payout_options(to, pid, msg_id, session)
        return

    if btn_id == "ta_payout_bank":
        await handle_payout_method(to, pid, msg_id, session, "bank")
        return

    if btn_id == "ta_payout_wallet":
        await handle_payout_method(to, pid, msg_id, session, "wallet")
        return

    if btn_id.startswith("ta_payout_wallet_"):
        provider_id = btn_id.replace("ta_payout_wallet_", "")
        await handle_payout_wallet_provider(to, pid, msg_id, session, provider_id)
        return

    if btn_id.startswith("ta_sel_bank_"):
        bank_name = btn_id.replace("ta_sel_bank_", "").replace("_", " ")
        await handle_payout_bank_select(to, pid, msg_id, session, bank_name)
        return

    # ── Policy ────────────────────────────────────────────────────────────────
    if btn_id in ("ta_check_policy", "ta_view_policies"):
        await show_policies_menu(to, pid, msg_id, session)
        return

    if btn_id == "ta_view_policy":
        await show_policy_detail(to, pid, msg_id, session, 0)
        return

    if btn_id == "ta_lookup_phone":
        await handle_policy_lookup_method(to, pid, msg_id, session, "phone")
        return

    if btn_id == "ta_lookup_policy":
        await handle_policy_lookup_method(to, pid, msg_id, session, "policy")
        return

    if btn_id == "ta_lookup_flight":
        await handle_policy_lookup_method(to, pid, msg_id, session, "flight")
        return

    if btn_id == "ta_lookup_phone_confirm":
        await handle_policy_lookup_value(to, pid, msg_id, session, "whatsapp_number")
        return

    if btn_id.startswith("ta_policy_"):
        try:
            idx = int(btn_id.replace("ta_policy_", ""))
            await show_policy_detail(to, pid, msg_id, session, idx)
        except ValueError:
            pass
        return

    if btn_id.startswith("ta_download_policy_"):
        await _send(to, _msg_text(
            to,
            "📥 *Your policy document is ready*\n\n"
            "📄 Policy document has been sent to your WhatsApp.\n\n"
            "If you don't receive it within a few minutes, please contact support."
        ), pid, msg_id)
        return

    if btn_id.startswith("ta_cancel_policy_"):
        try:
            idx = int(btn_id.replace("ta_cancel_policy_", ""))
            await _cancel_policy_confirm(to, pid, msg_id, session, idx)
        except ValueError:
            pass
        return

    if btn_id.startswith("ta_confirm_cancel_"):
        try:
            idx = int(btn_id.replace("ta_confirm_cancel_", ""))
            await _cancel_policy_execute(to, pid, msg_id, session, idx)
        except ValueError:
            pass
        return

    # ── Boarding pass ──────────────────────────────────────────────────────────
    if btn_id == "ta_upload_boarding":
        await start_boarding_upload(to, pid, msg_id, session)
        return

    if btn_id == "ta_boarding_upload_start":
        await start_boarding_upload_prompt(to, pid, msg_id, session)
        return

    # ── Link flight ────────────────────────────────────────────────────────────
    if btn_id == "ta_link_flight":
        await start_link_flight(to, pid, msg_id, session)
        return

    if btn_id.startswith("ta_link_flight_policy_"):
        try:
            idx = int(btn_id.replace("ta_link_flight_policy_", ""))
            await handle_link_policy_select(to, pid, msg_id, session, idx)
        except ValueError:
            pass
        return

    if btn_id == "ta_retry_link_flight":
        await start_link_flight(to, pid, msg_id, session)
        return

    if btn_id == "ta_confirm_link_flight":
        await confirm_link_flight(to, pid, msg_id, session)
        return

    # ── Update details ─────────────────────────────────────────────────────────
    if btn_id == "ta_update_details":
        await start_update_details(to, pid, msg_id, session)
        return

    if btn_id == "ta_update_who_solo":
        await show_update_menu_solo(to, pid, msg_id, session)
        return

    if btn_id == "ta_update_who_group":
        await show_update_menu_group(to, pid, msg_id, session)
        return

    if btn_id == "ta_update_travellers":
        await show_update_traveller_list(to, pid, msg_id, session)
        return

    if btn_id.startswith("ta_update_traveller_"):
        idx = int(btn_id.replace("ta_update_traveller_", ""))
        await handle_update_traveller_select(to, pid, msg_id, session, idx)
        return

    if btn_id in ("ta_update_name", "ta_update_email", "ta_update_phone", "ta_update_bank"):
        field = btn_id.replace("ta_update_", "")
        await handle_update_field(to, pid, msg_id, session, field)
        return

    # ── FAQ ────────────────────────────────────────────────────────────────────
    if btn_id.startswith("ta_faq_"):
        faq_id = btn_id.replace("ta_faq_", "")
        await show_faq_answer(to, pid, msg_id, faq_id)
        return

    if btn_id == "ta_faq_contact_agent":
        await show_contact_support(to, pid, msg_id)
        return

    # ── Session / system ───────────────────────────────────────────────────────
    if btn_id == "ta_resume_session":
        await send_main_menu(to, pid, msg_id)
        return

    if btn_id == "ta_system_retry":
        await send_main_menu(to, pid, msg_id)
        return

    # ── Unrecognised ───────────────────────────────────────────────────────────
    await err_invalid_input(to, pid, msg_id)


# ── Text router ────────────────────────────────────────────────────────────────

async def _handle_text(
    text: str,
    to: str,
    pid: str,
    msg_id: Optional[str],
    session: dict,
):
    val = text.strip()
    step = _get_step(session)

    # Quick commands — always active
    if val == "99":
        _reset_flow(session)
        await _send(to, _msg_text(to, "❌ Cancelled. Here's what you can do:"), pid, msg_id)
        await send_main_menu(to, pid, msg_id)
        return

    if val in ("00", "0", "9"):
        _reset_flow(session)
        await send_main_menu(to, pid, msg_id)
        return

    # Step-based text inputs
    if step == S_BUY_TRAVELLER_COUNT:
        await handle_traveller_count(to, pid, msg_id, session, val)
        return

    if step in (S_BUY_TRAVELLER_NAMES,):
        await handle_traveller_name(to, pid, msg_id, session, val)
        return

    if step == S_BUY_EMAIL:
        await handle_email(to, pid, msg_id, session, val)
        return

    if step == S_BUY_BOOKING_REF:
        await handle_booking_ref(to, pid, msg_id, session, val)
        return

    if step in (S_BUY_FLIGHT_NUMBER, S_FLIGHT_NOT_FOUND):
        await handle_flight_number(to, pid, msg_id, session, val)
        return

    if step == S_BUY_TRAVEL_DATE:
        await handle_travel_date(to, pid, msg_id, session, val)
        return

    if step == S_BUY_DEPART_TIME:
        await handle_depart_time(to, pid, msg_id, session, val)
        return

    if step in (S_BUY_DEPART_AIRPORT_Q, S_BUY_DEPART_AIRPORT_SEL):
        await handle_depart_airport_query(to, pid, msg_id, session, val)
        return

    if step == S_BUY_ARRIVE_TIME:
        await handle_arrive_time(to, pid, msg_id, session, val)
        return

    if step in (S_BUY_ARRIVE_AIRPORT_Q, S_BUY_ARRIVE_AIRPORT_SEL):
        await handle_arrive_airport_query(to, pid, msg_id, session, val)
        return

    if step == S_BUY_CARRIER:
        await handle_carrier(to, pid, msg_id, session, val)
        return

    if step == S_KYC_ENTER_ID:
        await handle_kyc_value(to, pid, msg_id, session, val)
        return

    if step == S_PAYMENT_WALLET_PHONE:
        await handle_wallet_phone(to, pid, msg_id, session, val)
        return

    if step == S_PAYOUT_BANK_ACCOUNT:
        await handle_payout_bank_account(to, pid, msg_id, session, val)
        return

    if step == S_PAYOUT_BANK_NAME:
        await handle_payout_bank_name(to, pid, msg_id, session, val)
        return

    if step == S_PAYOUT_WALLET_PHONE:
        await handle_payout_wallet_phone(to, pid, msg_id, session, val)
        return

    if step == S_POLICY_LOOKUP_VALUE:
        await handle_policy_lookup_value(to, pid, msg_id, session, val)
        return

    if step == S_POLICY_FLIGHT_DATE:
        await handle_policy_lookup_value(to, pid, msg_id, session, val)
        return

    if step == S_LINK_ENTER_FLIGHT:
        await handle_link_flight_entered(to, pid, msg_id, session, val)
        return

    if step == S_UPDATE_NAME:
        await handle_update_value(to, pid, msg_id, session, val, "name")
        return

    if step == S_UPDATE_EMAIL:
        await handle_update_value(to, pid, msg_id, session, val, "email")
        return

    if step == S_UPDATE_PHONE:
        await handle_update_value(to, pid, msg_id, session, val, "phone")
        return

    if step == S_UPDATE_BANK:
        await handle_update_value(to, pid, msg_id, session, val, "bank")
        return

    if step == S_UPDATE_BANK_NAME:
        await handle_update_bank_name(to, pid, msg_id, session, val)
        return

    if step == S_UPDATE_TRAVELLER_NAME:
        await handle_update_traveller_name(to, pid, msg_id, session, val)
        return

    # No active step — treat as fallback
    await err_invalid_input(to, pid, msg_id)


# ── Entry point ────────────────────────────────────────────────────────────────

async def handle_travelassist(
    message: dict,
    sender_wa_id: str,
    phone_number_id: str,
    msg_id: Optional[str],
    session: dict,
):
    """
    Main entry point. Call from webhook.py after loading the user session.

    Handles:
      - text messages
      - interactive button replies
      - interactive list replies
      - image / document uploads (boarding pass)
    """
    msg_type = message.get("type", "")

    if msg_type == "text":
        text = message.get("text", {}).get("body", "").strip()
        if not text:
            return

        # First-time or wake word → start session
        if is_travelassist_trigger(message) and not _get_step(session):
            is_returning = bool(session.get("ta_data"))
            await send_welcome(sender_wa_id, phone_number_id, msg_id, is_returning)
        else:
            await _handle_text(text, sender_wa_id, phone_number_id, msg_id, session)

    elif msg_type == "interactive":
        interactive = message.get("interactive", {})
        itype = interactive.get("type", "")

        if itype == "button_reply":
            btn_id = interactive.get("button_reply", {}).get("id", "")
            await _handle_button(btn_id, sender_wa_id, phone_number_id, msg_id, session)

        elif itype == "list_reply":
            row_id = interactive.get("list_reply", {}).get("id", "")
            await _handle_button(row_id, sender_wa_id, phone_number_id, msg_id, session)

    elif msg_type in ("image", "document"):
        # Boarding pass upload
        step = _get_step(session)
        if step == S_BOARDING_UPLOAD:
            media_obj = message.get(msg_type, {})
            mime_type = media_obj.get("mime_type", "")
            await handle_boarding_pass_received(sender_wa_id, phone_number_id, msg_id, session, media_type=msg_type, mime_type=mime_type)
        else:
            # Unsolicited media — gentle redirect
            await _send(sender_wa_id, _msg_buttons(
                sender_wa_id,
                "📎 Got your file! If you're uploading a boarding pass, use the option below.",
                [
                    {"id": "ta_upload_boarding", "title": "🛂 Upload boarding pass"},
                    {"id": "ta_main_menu",       "title": "🏠 Main menu"},
                ],
            ), phone_number_id, msg_id)

    await save_session(session)


# ── Trigger checkers ───────────────────────────────────────────────────────────

def is_travelassist_trigger(message: dict) -> bool:
    """
    Returns True if this message should start a TravelAssist session.
    Matches greetings, wake words, and quick-start commands.
    """
    if message.get("type") != "text":
        return False
    text = message.get("text", {}).get("body", "").strip().lower()
    for pattern in TRIGGER_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def is_in_travelassist_flow(session: dict) -> bool:
    """
    Returns True if the user's session has an active TravelAssist flow step.
    """
    return bool(session.get(TA_STEP_KEY, ""))
