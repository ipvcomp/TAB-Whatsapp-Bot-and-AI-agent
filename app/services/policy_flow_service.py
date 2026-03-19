import asyncio
import logging
import re
from typing import Optional

import httpx

from app.core.config import get_settings
from app.models.webhook import WhatsAppMessage
from app.services.session_service import get_session, save_session, build_default_session
from app.services.whatsapp_service import send_whatsapp_payload, send_text_message, download_whatsapp_media, SUPPORTED_BOARDING_PASS_TYPES
from app.services.llm_service import call_extract
from app.services.policy_service import (
    create_policy, get_active_draft, set_product_selection, cancel_policy,
    set_personal_details, set_id_verification, set_payment_method,
    set_account_number, set_country,
    set_bank_details, set_msisdn_info, set_channel_info, set_airport_info,
    set_itinerary, set_boarding_pass, set_policy_submitted, get_policy_by_id,
)

logger = logging.getLogger(__name__)

API_RETRY_MAX_ATTEMPTS = 3
API_RETRY_BACKOFF_SECONDS = [1, 2, 4]

async def _api_call_with_retry(
    api_name: str,
    coro_factory,
    max_attempts: int = API_RETRY_MAX_ATTEMPTS,
    backoff_seconds: list = None,
):
    if backoff_seconds is None:
        backoff_seconds = API_RETRY_BACKOFF_SECONDS
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = await coro_factory()
            if result is not None:
                return result
            if attempt < max_attempts:
                wait = backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
                logger.warning(f"{api_name}: attempt {attempt}/{max_attempts} returned None, retrying in {wait}s...")
                await asyncio.sleep(wait)
            else:
                logger.error(f"{api_name}: all {max_attempts} attempts returned None")
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_error = e
            if attempt < max_attempts:
                wait = backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
                logger.warning(f"{api_name}: attempt {attempt}/{max_attempts} failed ({type(e).__name__}), retrying in {wait}s...")
                await asyncio.sleep(wait)
            else:
                logger.error(f"{api_name}: all {max_attempts} attempts failed. Last error: {type(e).__name__}: {e}")
        except Exception as e:
            last_error = e
            if attempt < max_attempts:
                wait = backoff_seconds[min(attempt - 1, len(backoff_seconds) - 1)]
                logger.warning(f"{api_name}: attempt {attempt}/{max_attempts} unexpected error ({type(e).__name__}: {e}), retrying in {wait}s...")
                await asyncio.sleep(wait)
            else:
                logger.error(f"{api_name}: all {max_attempts} attempts failed. Last error: {type(e).__name__}: {e}")
    return None

PRODUCTS_API_BASE_URL = "https://dev-ilekun-ipv.ipurvey.com/api/v1/tab-pc/products/getByCountry"
PAYMENT_METHODS_API_URL = "https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/payment-method/types"

PRODUCTS_PER_PAGE = 8

POLICY_KEYWORDS = [
    r"\b(policy|create\s*policy|new\s*policy|purchase\s*policy|buy\s*policy)\b",
    r"^/policy$",
    r"^/createpolicy$",
    r"\bi\s*want\s*to\s*create\s*(a\s*)?policy\b",
    r"\bi\s*want\s*(a\s*)?policy\b",
    r"\bcreate\s*new\s*policy\b",
]

FLOW_STATE_KEY = "policy_flow"
FLOW_STEP_MENU = "policy_menu"
FLOW_STEP_MSISDN_CONFIRM = "msisdn_confirm"
FLOW_STEP_COUNTRY = "country_input"
FLOW_STEP_PRODUCT_LIST = "product_list"
FLOW_STEP_PRODUCT_SELECTED = "product_selected"
FLOW_STEP_PRODUCT_CONFIRM = "product_confirm"
FLOW_STEP_PD_FIRST_NAME = "pd_first_name"
FLOW_STEP_PD_LAST_NAME = "pd_last_name"
FLOW_STEP_PD_EMAIL = "pd_email"
FLOW_STEP_ID_TYPE = "id_type_selection"
FLOW_STEP_ID_NUMBER = "id_number_input"
FLOW_STEP_DETAILS_CONFIRM = "details_confirmation"
FLOW_STEP_DETAILS_EDIT_SELECT = "details_edit_select"
FLOW_STEP_DETAILS_EDIT_INPUT = "details_edit_input"
FLOW_STEP_PAYMENT_METHOD = "payment_method"
FLOW_STEP_PD_ACCOUNT_NUMBER = "pd_account_number"
FLOW_STEP_BANK_NAME_INPUT = "bank_name_input"
FLOW_STEP_BANK_SELECTION = "bank_selection"
FLOW_STEP_AIRPORT_INPUT = "airport_input"
FLOW_STEP_AIRPORT_SELECT = "airport_select"
FLOW_STEP_ITIN_BOOKING_REF = "itin_booking_ref"
FLOW_STEP_ITIN_FLIGHT_NO = "itin_flight_no"
FLOW_STEP_ITIN_CARRIER = "itin_carrier"
FLOW_STEP_ITIN_DEP_DATE = "itin_dep_date"
FLOW_STEP_ITIN_DEP_TIME = "itin_dep_time"
FLOW_STEP_ITIN_ARR_AIRPORT_INPUT = "itin_arr_airport_input"
FLOW_STEP_ITIN_ARR_AIRPORT_SELECT = "itin_arr_airport_select"
FLOW_STEP_ITIN_ARR_DATE = "itin_arr_date"
FLOW_STEP_ITIN_ARR_TIME = "itin_arr_time"
FLOW_STEP_BOARDING_PASS = "boarding_pass"
FLOW_STEP_BOARDING_PASS_CHOICE = "boarding_pass_choice"
FLOW_STEP_POLICY_SUMMARY = "policy_summary"
FLOW_STEP_EXIT_CONFIRM = "exit_confirmation"

ARR_AIRPORT_ID_PREFIX = "arr_airport_"

BUTTON_CREATE_NEW = "policy_create_new"
BUTTON_SUBMIT_ITINERARY = "policy_submit_itinerary"
BUTTON_VIEW_PRODUCTS = "policy_view_products"
BUTTON_RETRY_SUBMISSION = "policy_retry_submission"
BUTTON_MSISDN_YES = "msisdn_confirm_yes"
BUTTON_MSISDN_NO = "msisdn_confirm_no"
BUTTON_PRODUCT_CONFIRM = "product_confirm"
BUTTON_PRODUCT_CHANGE = "product_change"
BUTTON_ID_NIN = "id_type_nin"
BUTTON_ID_BVN = "id_type_bvn"
BUTTON_DETAILS_CONFIRM = "details_confirm_yes"
BUTTON_DETAILS_CHANGE = "details_confirm_change"
BUTTON_BP_UPLOAD_NOW = "bp_upload_now"
BUTTON_BP_UPLOAD_LATER = "bp_upload_later"
BUTTON_SUMMARY_SUBMIT = "policy_summary_submit"
BUTTON_SUMMARY_CHANGE = "policy_summary_change"
BUTTON_EXIT_YES = "exit_confirm_yes"
BUTTON_EXIT_NO = "exit_confirm_no"
PRODUCT_ID_PREFIX = "product_"
PAYMENT_METHOD_PREFIX = "pay_method_"
BANK_ID_PREFIX = "bank_"
BANK_NAV_NEXT = "bank_nav_next"
BANK_NAV_PREV = "bank_nav_prev"
BANK_SEARCH_AGAIN = "bank_search_again"
NAV_NEXT = "policy_nav_next"
NAV_PREV = "policy_nav_prev"
BUTTON_RETRY = "policy_retry"
BUTTON_START_OVER = "policy_start_over"

BANKS_API_URL = "https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/payout-method/banks"
AIRPORTS_API_URL = "https://dev-ilekun-ipv.ipurvey.com/api/v2/airports/search"
BANKS_PER_PAGE = 8
AIRPORTS_PER_PAGE = 8
AIRPORT_ID_PREFIX = "airport_"
AIRPORT_NAV_NEXT = "airport_nav_next"
AIRPORT_NAV_PREV = "airport_nav_prev"
ARR_AIRPORT_NAV_NEXT = "arr_airport_nav_next"
ARR_AIRPORT_NAV_PREV = "arr_airport_nav_prev"

SHORTCUT_COMMANDS = {
    "#shortcuts": "shortcuts",
    "#menu": "menu",
    "#home": "menu",
    "#back": "back",
    "#exit": "exit",
    "#cancel": "exit",
    "#restart": "restart",
    "#products": "products",
}

SHORTCUTS_TEXT = (
    "*Navigation Shortcuts* \u2328\uFE0F\n\n"
    "Type any of these commands anytime:\n\n"
    "*#menu* \u2014 Go to main policy menu\n"
    "*#back* \u2014 Go back one step\n"
    "*#products* \u2014 Change product selection\n"
    "*#restart* \u2014 Start a new policy from scratch\n"
    "*#exit* or *#cancel* \u2014 Exit policy flow\n"
    "*#shortcuts* \u2014 Show this menu\n\n"
    "*Trigger Words:*\n"
    "\u2022 _hi, hello, hey, start, menu_ \u2014 Welcome message\n"
    "\u2022 _policy, purchase policy, /policy_ \u2014 Start policy flow\n"
    "\u2022 _help, support_ \u2014 Get help\n"
    "\u2022 _cancel, exit, stop_ \u2014 Exit current flow"
)

BACK_STEP_MAP = {
    FLOW_STEP_MSISDN_CONFIRM: FLOW_STEP_MENU,
    FLOW_STEP_COUNTRY: FLOW_STEP_MENU,
    FLOW_STEP_AIRPORT_INPUT: FLOW_STEP_MSISDN_CONFIRM,
    FLOW_STEP_AIRPORT_SELECT: FLOW_STEP_AIRPORT_INPUT,
    FLOW_STEP_PRODUCT_LIST: FLOW_STEP_AIRPORT_INPUT,
    FLOW_STEP_PRODUCT_SELECTED: FLOW_STEP_AIRPORT_INPUT,
    FLOW_STEP_PRODUCT_CONFIRM: FLOW_STEP_PRODUCT_SELECTED,
    FLOW_STEP_ITIN_DEP_DATE: FLOW_STEP_PRODUCT_CONFIRM,
    FLOW_STEP_ITIN_DEP_TIME: FLOW_STEP_ITIN_DEP_DATE,
    FLOW_STEP_ITIN_ARR_AIRPORT_INPUT: FLOW_STEP_ITIN_DEP_TIME,
    FLOW_STEP_ITIN_ARR_AIRPORT_SELECT: FLOW_STEP_ITIN_ARR_AIRPORT_INPUT,
    FLOW_STEP_ITIN_ARR_DATE: FLOW_STEP_ITIN_ARR_AIRPORT_INPUT,
    FLOW_STEP_ITIN_ARR_TIME: FLOW_STEP_ITIN_ARR_DATE,
    FLOW_STEP_ITIN_BOOKING_REF: FLOW_STEP_ITIN_ARR_TIME,
    FLOW_STEP_ITIN_FLIGHT_NO: FLOW_STEP_ITIN_BOOKING_REF,
    FLOW_STEP_PD_FIRST_NAME: FLOW_STEP_ITIN_FLIGHT_NO,
    FLOW_STEP_PD_LAST_NAME: FLOW_STEP_PD_FIRST_NAME,
    FLOW_STEP_PD_EMAIL: FLOW_STEP_PD_LAST_NAME,
    FLOW_STEP_ID_TYPE: FLOW_STEP_PD_EMAIL,
    FLOW_STEP_ID_NUMBER: FLOW_STEP_ID_TYPE,
    FLOW_STEP_DETAILS_CONFIRM: FLOW_STEP_ID_NUMBER,
    FLOW_STEP_DETAILS_EDIT_SELECT: FLOW_STEP_DETAILS_CONFIRM,
    FLOW_STEP_PAYMENT_METHOD: FLOW_STEP_DETAILS_CONFIRM,
    FLOW_STEP_PD_ACCOUNT_NUMBER: FLOW_STEP_PAYMENT_METHOD,
    FLOW_STEP_BANK_NAME_INPUT: FLOW_STEP_PD_ACCOUNT_NUMBER,
    FLOW_STEP_BANK_SELECTION: FLOW_STEP_BANK_NAME_INPUT,
    FLOW_STEP_BOARDING_PASS_CHOICE: FLOW_STEP_BANK_SELECTION,
    FLOW_STEP_BOARDING_PASS: FLOW_STEP_BOARDING_PASS_CHOICE,
    FLOW_STEP_POLICY_SUMMARY: FLOW_STEP_BOARDING_PASS_CHOICE,
}

PERSONAL_DETAIL_STEPS = [
    {"step": FLOW_STEP_PD_FIRST_NAME, "field": "first_name", "prompt": "Please enter your *first name*:", "expected_format": "text"},
    {"step": FLOW_STEP_PD_LAST_NAME, "field": "last_name", "prompt": "Please enter your *last name*:", "expected_format": "text"},
    {"step": FLOW_STEP_PD_EMAIL, "field": "email", "prompt": "Please enter your *email address*:", "expected_format": "email"},
]

DETAILS_EDITABLE_FIELDS = [
    {"num": 1, "label": "Departure Date", "field": "itin_dep_date", "step": FLOW_STEP_ITIN_DEP_DATE},
    {"num": 2, "label": "Departure Time", "field": "itin_dep_time", "step": FLOW_STEP_ITIN_DEP_TIME},
    {"num": 3, "label": "Arrival Airport", "field": "itin_arr_airport", "step": FLOW_STEP_ITIN_ARR_AIRPORT_INPUT},
    {"num": 4, "label": "Arrival Date", "field": "itin_arr_date", "step": FLOW_STEP_ITIN_ARR_DATE},
    {"num": 5, "label": "Arrival Time", "field": "itin_arr_time", "step": FLOW_STEP_ITIN_ARR_TIME},
    {"num": 6, "label": "Booking Reference", "field": "itin_booking_ref", "step": FLOW_STEP_ITIN_BOOKING_REF},
    {"num": 7, "label": "Flight Number", "field": "itin_flight_no", "step": FLOW_STEP_ITIN_FLIGHT_NO},
    {"num": 8, "label": "First Name", "field": "pd_first_name", "step": FLOW_STEP_PD_FIRST_NAME},
    {"num": 9, "label": "Last Name", "field": "pd_last_name", "step": FLOW_STEP_PD_LAST_NAME},
    {"num": 10, "label": "Email", "field": "pd_email", "step": FLOW_STEP_PD_EMAIL},
    {"num": 11, "label": "ID Type & Number", "field": "id_type_number", "step": FLOW_STEP_ID_TYPE},
]

COUNTRY_MAP = {
    "nigeria": "NG", "ng": "NG",
    "kenya": "KE", "ke": "KE",
    "ghana": "GH", "gh": "GH",
    "south africa": "ZA", "za": "ZA",
    "tanzania": "TZ", "tz": "TZ",
    "uganda": "UG", "ug": "UG",
    "rwanda": "RW", "rw": "RW",
    "ethiopia": "ET", "et": "ET",
    "cameroon": "CM", "cm": "CM",
    "senegal": "SN", "sn": "SN",
    "ivory coast": "CI", "cote d'ivoire": "CI", "ci": "CI",
    "egypt": "EG", "eg": "EG",
    "morocco": "MA", "ma": "MA",
    "algeria": "DZ", "dz": "DZ",
    "tunisia": "TN", "tn": "TN",
    "zambia": "ZM", "zm": "ZM",
    "zimbabwe": "ZW", "zw": "ZW",
    "mozambique": "MZ", "mz": "MZ",
    "angola": "AO", "ao": "AO",
    "mali": "ML", "ml": "ML",
    "niger": "NE", "ne": "NE",
    "burkina faso": "BF", "bf": "BF",
    "benin": "BJ", "bj": "BJ",
    "togo": "TG", "tg": "TG",
    "liberia": "LR", "lr": "LR",
    "sierra leone": "SL", "sl": "SL",
    "gambia": "GM", "gm": "GM",
    "guinea": "GN", "gn": "GN",
    "congo": "CD", "cd": "CD",
    "united states": "US", "usa": "US", "us": "US",
    "united kingdom": "GB", "uk": "GB", "gb": "GB",
    "canada": "CA", "ca": "CA",
    "india": "IN", "in": "IN",
    "pakistan": "PK", "pk": "PK",
    "bangladesh": "BD", "bd": "BD",
    "sri lanka": "LK", "lk": "LK",
    "uae": "AE", "united arab emirates": "AE", "ae": "AE",
    "saudi arabia": "SA", "sa": "SA",
    "qatar": "QA", "qa": "QA",
    "kuwait": "KW", "kw": "KW",
    "bahrain": "BH", "bh": "BH",
    "oman": "OM", "om": "OM",
    "australia": "AU", "au": "AU",
    "new zealand": "NZ", "nz": "NZ",
    "germany": "DE", "de": "DE",
    "france": "FR", "fr": "FR",
    "italy": "IT", "it": "IT",
    "spain": "ES", "es": "ES",
    "portugal": "PT", "pt": "PT",
    "netherlands": "NL", "nl": "NL",
    "belgium": "BE", "be": "BE",
    "switzerland": "CH", "ch": "CH",
    "sweden": "SE", "se": "SE",
    "norway": "NO", "no": "NO",
    "denmark": "DK", "dk": "DK",
    "finland": "FI", "fi": "FI",
    "ireland": "IE", "ie": "IE",
    "poland": "PL", "pl": "PL",
    "brazil": "BR", "br": "BR",
    "mexico": "MX", "mx": "MX",
    "argentina": "AR", "ar": "AR",
    "chile": "CL", "cl": "CL",
    "colombia": "CO", "co": "CO",
    "peru": "PE", "pe": "PE",
    "china": "CN", "cn": "CN",
    "japan": "JP", "jp": "JP",
    "south korea": "KR", "kr": "KR",
    "singapore": "SG", "sg": "SG",
    "malaysia": "MY", "my": "MY",
    "indonesia": "ID", "id": "ID",
    "thailand": "TH", "th": "TH",
    "philippines": "PH", "ph": "PH",
    "vietnam": "VN", "vn": "VN",
    "turkey": "TR", "tr": "TR",
    "russia": "RU", "ru": "RU",
    "israel": "IL", "il": "IL",
    "jordan": "JO", "jo": "JO",
    "lebanon": "LB", "lb": "LB",
    "iraq": "IQ", "iq": "IQ",
    "iran": "IR", "ir": "IR",
}


PHONE_CALLING_CODE_TO_COUNTRY = {
    "234": "NG", "254": "KE", "233": "GH", "27": "ZA", "255": "TZ",
    "256": "UG", "250": "RW", "251": "ET", "237": "CM", "221": "SN",
    "225": "CI", "20": "EG", "212": "MA", "213": "DZ", "216": "TN",
    "260": "ZM", "263": "ZW", "258": "MZ", "244": "AO", "223": "ML",
    "227": "NE", "226": "BF", "229": "BJ", "228": "TG", "231": "LR",
    "232": "SL", "220": "GM", "224": "GN", "243": "CD", "242": "CG",
    "1": "US", "44": "GB", "91": "IN", "92": "PK", "880": "BD",
    "94": "LK", "971": "AE", "966": "SA", "974": "QA", "965": "KW",
    "973": "BH", "968": "OM", "61": "AU", "64": "NZ", "49": "DE",
    "33": "FR", "39": "IT", "34": "ES", "351": "PT", "31": "NL",
    "32": "BE", "41": "CH", "46": "SE", "47": "NO", "45": "DK",
    "358": "FI", "353": "IE", "48": "PL", "55": "BR", "52": "MX",
    "54": "AR", "56": "CL", "57": "CO", "51": "PE", "86": "CN",
    "81": "JP", "82": "KR", "65": "SG", "60": "MY", "62": "ID",
    "66": "TH", "63": "PH", "84": "VN", "90": "TR", "7": "RU",
    "972": "IL", "962": "JO", "961": "LB", "964": "IQ", "98": "IR",
}

COUNTRY_CODE_TO_NAME = {}
for _name, _code in COUNTRY_MAP.items():
    if len(_name) > 2 and _code not in COUNTRY_CODE_TO_NAME:
        COUNTRY_CODE_TO_NAME[_code] = _name.title()


TEST_PHONE_COUNTRY_OVERRIDES: dict[str, str] = {
    "923176811061": "NG",
    "13055083815": "NG",
}


def _derive_country_from_phone(wa_id: str) -> tuple[Optional[str], Optional[str]]:
    digits = wa_id.lstrip("+")

    if digits in TEST_PHONE_COUNTRY_OVERRIDES:
        code = TEST_PHONE_COUNTRY_OVERRIDES[digits]
        name = COUNTRY_CODE_TO_NAME.get(code, code)
        logger.info(f"Test country override applied for {digits}: {code}")
        return code, name

    for length in (3, 2, 1):
        prefix = digits[:length]
        if prefix in PHONE_CALLING_CODE_TO_COUNTRY:
            code = PHONE_CALLING_CODE_TO_COUNTRY[prefix]
            name = COUNTRY_CODE_TO_NAME.get(code, code)
            return code, name
    return None, None


def _format_phone_display(wa_id: str) -> str:
    digits = wa_id.lstrip("+")
    if len(digits) >= 10:
        return f"+{digits[:3]} {digits[3:6]} {digits[6:9]} {digits[9:]}"
    return f"+{digits}"


def _resolve_country_code(text: str) -> Optional[str]:
    cleaned = text.lower().strip()
    if cleaned in COUNTRY_MAP:
        return COUNTRY_MAP[cleaned]
    if len(cleaned) == 2 and cleaned.upper() in {v for v in COUNTRY_MAP.values()}:
        return cleaned.upper()
    for name, code in COUNTRY_MAP.items():
        if name in cleaned or cleaned in name:
            return code
    return None


def _is_cancel_command(message: WhatsAppMessage) -> bool:
    if message.type == "text" and message.text:
        text = message.text.body.lower().strip()
        if text in ("cancel", "/cancel", "exit", "/exit", "stop", "/stop"):
            return True
    return False


def _get_shortcut_command(message: WhatsAppMessage) -> Optional[str]:
    if message.type == "text" and message.text:
        text = message.text.body.lower().strip()
        return SHORTCUT_COMMANDS.get(text)
    return None


def get_shortcuts_text() -> str:
    return SHORTCUTS_TEXT


def is_policy_trigger(message: WhatsAppMessage) -> bool:
    if message.type == "text" and message.text:
        text = message.text.body.lower().strip()
        for pattern in POLICY_KEYWORDS:
            if re.search(pattern, text, re.IGNORECASE):
                return True
    return False


def is_in_policy_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    temp_data = session.get("temp_data", {})
    return temp_data.get(FLOW_STATE_KEY, {}).get("active", False)


def _get_flow_state(session: dict) -> dict:
    return session.get("temp_data", {}).get(FLOW_STATE_KEY, {})


def _get_interactive_reply_id(message: WhatsAppMessage) -> Optional[str]:
    if message.type == "interactive" and message.interactive:
        if message.interactive.button_reply:
            return message.interactive.button_reply.id
        if message.interactive.list_reply:
            return message.interactive.list_reply.id
    if message.type == "button" and message.button:
        return message.button.payload
    return None


async def _handle_shortcut(
    shortcut: str,
    message: WhatsAppMessage,
    sender_wa_id: str,
    phone_number_id: str,
    in_reply_to: str,
    session: dict,
) -> bool:
    flow_state = _get_flow_state(session)
    current_step = flow_state.get("step")
    policy_id = flow_state.get("policy_id")

    if shortcut == "shortcuts":
        await send_text_message(
            to=sender_wa_id,
            body=SHORTCUTS_TEXT,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return True

    if shortcut == "exit":
        if current_step == FLOW_STEP_DETAILS_EDIT_SELECT or flow_state.get("editing_field"):
            new_state = {**flow_state, "step": FLOW_STEP_DETAILS_CONFIRM}
            new_state.pop("editing_field", None)
            await _send_details_confirmation(sender_wa_id, phone_number_id, in_reply_to, new_state)
            await _update_flow_state(session, sender_wa_id, new_state)
            return True
        if current_step == FLOW_STEP_EXIT_CONFIRM:
            await send_text_message(
                to=sender_wa_id,
                body="Please tap one of the buttons: *Yes, Cancel* or *No, Let's Resume*.",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            return True
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": sender_wa_id,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {
                    "text": (
                        "Are you sure you want to cancel? All your current progress will be lost.\n\n"
                        "You can always start a new policy later by typing *policy*."
                    )
                },
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": BUTTON_EXIT_YES, "title": "Yes, Cancel"}},
                        {"type": "reply", "reply": {"id": BUTTON_EXIT_NO, "title": "No, Let's Resume"}},
                    ]
                },
            },
        }
        await send_whatsapp_payload(
            payload,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_EXIT_CONFIRM,
            "pre_exit_step": current_step,
        })
        return True

    if shortcut == "restart":
        if policy_id:
            await cancel_policy(policy_id)
        await _clear_flow_state(session, sender_wa_id)
        phone_number = session.get("phone_number", sender_wa_id)
        policy = await create_policy(user_id=sender_wa_id, phone_number=phone_number)
        new_policy_id = policy.get("policy_id") if policy else None
        session["active_policy_id"] = new_policy_id
        await send_text_message(
            to=sender_wa_id,
            body="Starting fresh! \U0001F504",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _send_policy_menu(sender_wa_id, phone_number_id, in_reply_to)
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": FLOW_STEP_MENU,
            "policy_id": new_policy_id,
        })
        return True

    if shortcut == "menu":
        await _send_policy_menu(sender_wa_id, phone_number_id, in_reply_to)
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_MENU,
        })
        return True

    if shortcut == "products":
        if not flow_state.get("msisdn_confirmed"):
            phone_display = _format_phone_display(sender_wa_id)
            await _send_msisdn_confirm_buttons(
                to=sender_wa_id,
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                body=(
                    f"Before viewing products, please confirm your WhatsApp number "
                    f"*[{phone_display}]* as your unique customer identifier.\n\n"
                    f"Tap *Yes, Proceed* to continue or *No, Cancel* to stop."
                ),
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_MSISDN_CONFIRM,
            })
            return True
        country_code = flow_state.get("country_code")
        if not country_code:
            country_code, country_name = _derive_country_from_phone(sender_wa_id)
            if not country_code:
                country_code = "NG"
                country_name = "Nigeria"
            flow_state["country_code"] = country_code
            flow_state["country_name"] = country_name
        products = await _fetch_products(country_code)
        if not products:
            await _send_retry_options(
                to=sender_wa_id,
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                error_message=f"Couldn't fetch products for *{flow_state.get('country_name', country_code)}*. Please try again.",
                retry_label="Retry Products",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "retry_step": FLOW_STEP_PRODUCT_LIST,
            })
            return True
        page = 0
        await _send_products_page(sender_wa_id, phone_number_id, in_reply_to, products, page, country_code)
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_PRODUCT_SELECTED,
            "available_products": products,
            "product_page": page,
        })
        return True

    if shortcut == "back":
        if current_step == FLOW_STEP_DETAILS_EDIT_SELECT or flow_state.get("editing_field"):
            new_state = {**flow_state, "step": FLOW_STEP_DETAILS_CONFIRM}
            new_state.pop("editing_field", None)
            await _send_details_confirmation(sender_wa_id, phone_number_id, in_reply_to, new_state)
            await _update_flow_state(session, sender_wa_id, new_state)
            return True
        if not current_step or current_step == FLOW_STEP_MENU:
            await send_text_message(
                to=sender_wa_id,
                body="You're already at the beginning. \U0001F60A",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            return True
        prev_step = BACK_STEP_MAP.get(current_step)
        if not prev_step:
            await _send_policy_menu(sender_wa_id, phone_number_id, in_reply_to)
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_MENU,
            })
            return True
        await _send_step_prompt(prev_step, sender_wa_id, phone_number_id, in_reply_to, session, flow_state)
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": prev_step,
        })
        return True

    return False


async def _send_step_prompt(
    step: str,
    sender_wa_id: str,
    phone_number_id: str,
    in_reply_to: str,
    session: dict,
    flow_state: dict,
) -> None:
    if step == FLOW_STEP_MENU:
        await _send_policy_menu(sender_wa_id, phone_number_id, in_reply_to)

    elif step == FLOW_STEP_MSISDN_CONFIRM:
        phone_display = _format_phone_display(sender_wa_id)
        confirm_text = (
            f"To create your customer profile, we will use your WhatsApp number "
            f"*[{phone_display}]* as your unique customer identifier.\n\n"
            f"For security reasons, you can only proceed using this WhatsApp number "
            f"and it cannot be changed during this process.\n\n"
            f"Please confirm that you understand and wish to continue."
        )
        await _send_msisdn_confirm_buttons(
            to=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            body=confirm_text,
        )

    elif step == FLOW_STEP_COUNTRY:
        await send_text_message(
            to=sender_wa_id,
            body="Please type your *country name* (e.g. Nigeria, Kenya, Ghana):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_PRODUCT_SELECTED:
        country_code = flow_state.get("country_code", "NG")
        products = flow_state.get("available_products") or await _fetch_products(country_code)
        if products:
            await _send_products_page(sender_wa_id, phone_number_id, in_reply_to, products, 0, country_code)
            flow_state["available_products"] = products
            flow_state["product_page"] = 0

    elif step == FLOW_STEP_PRODUCT_CONFIRM:
        selected_product = flow_state.get("selected_product", {})
        coverage = ", ".join(selected_product.get("coverage_types", []))
        validity = selected_product.get("validity_days", "")
        price_str = f"{selected_product.get('currency', '')} {selected_product.get('price', '')}".strip()
        confirm_text = (
            f"You've selected *{selected_product.get('name', '')}*\n\n"
            f"*Product details*\n"
            f"\u2022 Product: {selected_product.get('name', '')}\n"
            f"\u2022 Coverage: {coverage}\n"
            f"\u2022 Price: {price_str}\n"
            f"\u2022 Validity: {validity} day{'s' if validity != 1 else ''}\n"
            f"\u2022 Provider: {selected_product.get('provider_name', '')}\n\n"
            f"Please confirm if this selection is correct or choose to change it."
        )
        await send_whatsapp_payload(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": sender_wa_id,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": confirm_text},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": BUTTON_PRODUCT_CONFIRM, "title": "Confirm"}},
                            {"type": "reply", "reply": {"id": BUTTON_PRODUCT_CHANGE, "title": "Change Product"}},
                        ]
                    },
                },
            },
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_ID_TYPE:
        await _send_id_type_selection(sender_wa_id, phone_number_id, in_reply_to)

    elif step == FLOW_STEP_ID_NUMBER:
        id_type = flow_state.get("id_type", "NIN")
        await send_text_message(
            to=sender_wa_id,
            body=f"Please enter your *11-digit {id_type}*:",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_DETAILS_CONFIRM:
        await _send_details_confirmation(sender_wa_id, phone_number_id, in_reply_to, flow_state)

    elif step == FLOW_STEP_PAYMENT_METHOD:
        await _send_payment_methods(sender_wa_id, phone_number_id, in_reply_to, country_code=flow_state.get("country_code", "NG"))

    elif step == FLOW_STEP_PD_ACCOUNT_NUMBER:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter your *10-digit account number* for future payouts:",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_BANK_NAME_INPUT:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter the first 3 characters of your payout bank name (e.g. Zen, Wem):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_BANK_SELECTION:
        banks = flow_state.get("available_banks", [])
        if banks:
            banks.sort(key=lambda b: b.get("name", "").lower())
            await _send_banks_page(sender_wa_id, phone_number_id, in_reply_to, banks, 0)
            flow_state["bank_page"] = 0

    elif step == FLOW_STEP_BOARDING_PASS_CHOICE:
        await _send_boarding_pass_choice(sender_wa_id, phone_number_id, in_reply_to)

    elif step == FLOW_STEP_BOARDING_PASS:
        await send_text_message(
            to=sender_wa_id,
            body=(
                "Please upload a clear image of your *boarding pass*.\n\n"
                "Accepted formats: JPG, PNG, WebP, PDF\n"
                "Maximum size: 20MB\n"
                "Make sure the name, flight details, barcode and date are clearly visible."
            ),
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_AIRPORT_INPUT:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter the first 3 characters of the *departure airport name* or *airport code* (e.g. LOS, Mur, KAN, Enu, PHC):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_ITIN_DEP_DATE:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter your scheduled *departure date* (e.g. 25/12/2026, 25-12-2026):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_ITIN_DEP_TIME:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter your scheduled *departure time* (e.g. 14:30):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_ITIN_ARR_AIRPORT_INPUT:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter the first 3 characters of the *arrival airport name* or *airport code* (e.g. LOS, Mur, KAN, Enu, PHC):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_ITIN_ARR_DATE:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter your scheduled *arrival date* (e.g. 25/12/2026 or 25-12-2026):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_ITIN_ARR_TIME:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter your scheduled *arrival time* (e.g. 16:30):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_ITIN_BOOKING_REF:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter your *booking reference* (e.g. ABC123):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_ITIN_FLIGHT_NO:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter your *flight number* (e.g. BA1234):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

    elif step == FLOW_STEP_POLICY_SUMMARY:
        await _send_policy_summary_confirmation(sender_wa_id, phone_number_id, in_reply_to, flow_state)

    elif step == FLOW_STEP_DETAILS_EDIT_SELECT:
        await _send_edit_field_menu(sender_wa_id, phone_number_id, in_reply_to, flow_state)

    elif step == FLOW_STEP_AIRPORT_SELECT:
        airports = flow_state.get("available_airports", [])
        if airports:
            page = flow_state.get("airport_page", 0)
            search_term = flow_state.get("airport_search_term", "")
            await _send_dep_airports_page(sender_wa_id, phone_number_id, in_reply_to, airports, page, search_term)
        else:
            await send_text_message(
                to=sender_wa_id,
                body="Please enter the first 3 characters of the *departure airport name* or *airport code* (e.g. LOS, Mur, KAN, Enu, PHC):",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )

    elif step == FLOW_STEP_ITIN_ARR_AIRPORT_SELECT:
        airports = flow_state.get("available_arr_airports", [])
        if airports:
            page = flow_state.get("arr_airport_page", 0)
            search_term = flow_state.get("arr_airport_search_term", "")
            await _send_arr_airports_page(sender_wa_id, phone_number_id, in_reply_to, airports, page, search_term)
        else:
            await send_text_message(
                to=sender_wa_id,
                body="Please enter the first 3 characters of the *arrival airport name* or *airport code* (e.g. LOS, Mur, KAN, Enu, PHC):",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )

    else:
        for pd_step in PERSONAL_DETAIL_STEPS:
            if pd_step["step"] == step:
                await send_text_message(
                    to=sender_wa_id,
                    body=pd_step["prompt"],
                    phone_number_id=phone_number_id,
                    in_reply_to=in_reply_to,
                    source="policy_flow",
                )
                return
        await _send_policy_menu(sender_wa_id, phone_number_id, in_reply_to)


async def handle_policy_flow(
    message: WhatsAppMessage,
    sender_wa_id: str,
    profile_name: str,
    phone_number_id: str,
    in_reply_to: str,
) -> None:
    session = await get_session(sender_wa_id)
    if not session:
        session = build_default_session(
            user_id=sender_wa_id,
            phone_number=sender_wa_id,
            first_name=profile_name,
        )

    shortcut = _get_shortcut_command(message)
    if shortcut:
        handled = await _handle_shortcut(
            shortcut=shortcut,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
        if handled:
            return

    if _is_cancel_command(message):
        flow_state = _get_flow_state(session)
        current_step = flow_state.get("step")
        if current_step == FLOW_STEP_EXIT_CONFIRM:
            pass
        elif current_step == FLOW_STEP_DETAILS_EDIT_SELECT or flow_state.get("editing_field"):
            new_state = {**flow_state, "step": FLOW_STEP_DETAILS_CONFIRM}
            new_state.pop("editing_field", None)
            await _send_details_confirmation(sender_wa_id, phone_number_id, in_reply_to, new_state)
            await _update_flow_state(session, sender_wa_id, new_state)
            return
        else:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": sender_wa_id,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {
                        "text": (
                            "Are you sure you want to cancel? All your current progress will be lost.\n\n"
                            "You can always start a new policy later by typing *policy*."
                        )
                    },
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": BUTTON_EXIT_YES, "title": "Yes, Cancel"}},
                            {"type": "reply", "reply": {"id": BUTTON_EXIT_NO, "title": "No, Let's Resume"}},
                        ]
                    },
                },
            }
            await send_whatsapp_payload(
                payload,
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_EXIT_CONFIRM,
                "pre_exit_step": current_step,
            })
            return

    if is_policy_trigger(message):
        flow_state = _get_flow_state(session)
        old_policy_id = flow_state.get("policy_id")
        if old_policy_id:
            await cancel_policy(old_policy_id)

        phone_number = session.get("phone_number", sender_wa_id)
        policy = await create_policy(user_id=sender_wa_id, phone_number=phone_number)
        policy_id = policy.get("policy_id") if policy else None

        session["active_policy_id"] = policy_id
        await _send_policy_menu(sender_wa_id, phone_number_id, in_reply_to)
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": FLOW_STEP_MENU,
            "policy_id": policy_id,
        })
        return

    flow_state = _get_flow_state(session)
    current_step = flow_state.get("step")

    early_reply_id = _get_interactive_reply_id(message)

    if early_reply_id == BUTTON_RETRY_SUBMISSION:
        policy_id = flow_state.get("policy_id")
        if not policy_id:
            await send_text_message(
                to=sender_wa_id,
                body="No active policy found to retry. Type 'policy' to start a new one.",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            return

        await send_text_message(
            to=sender_wa_id,
            body="Retrying policy submission... please wait.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

        policy_doc = await get_policy_by_id(policy_id)
        boarding_pass_data = (policy_doc or {}).get("boarding_pass", {})
        boarding_pass_bytes = boarding_pass_data.get("file_data", b"")
        boarding_pass_mime = boarding_pass_data.get("mime_type", "image/jpeg")

        if not boarding_pass_bytes:
            await send_text_message(
                to=sender_wa_id,
                body=(
                    "We couldn't retrieve your boarding pass for resubmission.\n\n"
                    "Please upload your boarding pass again:"
                ),
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "active": True,
                "step": FLOW_STEP_BOARDING_PASS,
            })
            return

        success, err_msg, resp_data = await _submit_policy_to_api(
            flow_state, policy_id, bytes(boarding_pass_bytes), boarding_pass_mime,
        )

        policy_reference = ""
        if success and policy_id:
            policy_reference = (
                resp_data.get("data", {}).get("policyId", "")
                or resp_data.get("data", {}).get("id", "")
                or resp_data.get("policyId", "")
                or resp_data.get("id", "")
                or ""
            )
            await set_policy_submitted(policy_id, resp_data)
            logger.info(f"Policy {policy_id} resubmitted successfully. Reference: {policy_reference}")
        else:
            logger.error(f"Policy {policy_id} resubmission failed: {err_msg}")

        airport_info = flow_state.get("airport_info", {})
        await _show_final_summary(
            sender_wa_id, phone_number_id, in_reply_to,
            session, flow_state, policy_id, airport_info,
            submission_success=success,
            policy_reference=str(policy_reference) if policy_reference else "",
            submission_error=err_msg,
        )
        return

    if not is_in_policy_flow(session):
        existing_draft = await get_active_draft(sender_wa_id)
        if existing_draft:
            policy_id = existing_draft["policy_id"]
        else:
            phone_number = session.get("phone_number", sender_wa_id)
            policy = await create_policy(user_id=sender_wa_id, phone_number=phone_number)
            policy_id = policy.get("policy_id") if policy else None

        session["active_policy_id"] = policy_id
        await _send_policy_menu(sender_wa_id, phone_number_id, in_reply_to)
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": FLOW_STEP_MENU,
            "policy_id": policy_id,
        })
        return

    reply_id = _get_interactive_reply_id(message)

    if reply_id == BUTTON_START_OVER:
        flow_state_data = _get_flow_state(session)
        old_policy_id = flow_state_data.get("policy_id")
        if old_policy_id:
            await cancel_policy(old_policy_id)
        await _clear_flow_state(session, sender_wa_id)
        phone_number = session.get("phone_number", sender_wa_id)
        policy = await create_policy(user_id=sender_wa_id, phone_number=phone_number)
        new_policy_id = policy.get("policy_id") if policy else None
        session["active_policy_id"] = new_policy_id
        await _send_policy_menu(sender_wa_id, phone_number_id, in_reply_to)
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": FLOW_STEP_MENU,
            "policy_id": new_policy_id,
        })
        return

    if reply_id == BUTTON_RETRY:
        retry_step = flow_state.get("retry_step", current_step)
        retry_data = flow_state.get("retry_data", {})

        if retry_step == FLOW_STEP_PRODUCT_LIST:
            country_code = flow_state.get("country_code", "NG")
            products = await _fetch_products(country_code)
            if not products:
                await _send_retry_options(
                    to=sender_wa_id,
                    phone_number_id=phone_number_id,
                    in_reply_to=in_reply_to,
                    error_message=f"Still unable to fetch products for *{flow_state.get('country_name', country_code)}*. The service may be temporarily unavailable.",
                    retry_label="Retry Products",
                )
                return
            page = 0
            await _send_products_page(sender_wa_id, phone_number_id, in_reply_to, products, page, country_code)
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_PRODUCT_SELECTED,
                "available_products": products,
                "product_page": page,
                "retry_step": None,
                "retry_data": None,
            })
            return

        if retry_step == FLOW_STEP_BANK_NAME_INPUT:
            await send_text_message(
                to=sender_wa_id,
                body="Please enter the first 3 characters of your payout bank name (e.g. Zen, Wem):",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_BANK_NAME_INPUT,
                "retry_step": None,
                "retry_data": None,
            })
            return

        if retry_step == FLOW_STEP_BANK_SELECTION:
            country_code = flow_state.get("country_code", "NG")
            banks = await _fetch_banks(country_code)
            if not banks:
                await _send_retry_options(
                    to=sender_wa_id,
                    phone_number_id=phone_number_id,
                    in_reply_to=in_reply_to,
                    error_message=f"Still unable to fetch banks for *{flow_state.get('country_name', country_code)}*. The service may be temporarily unavailable.",
                    retry_label="Retry Banks",
                )
                return
            banks.sort(key=lambda b: b.get("name", "").lower())
            page = 0
            await _send_banks_page(sender_wa_id, phone_number_id, in_reply_to, banks, page)
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_BANK_SELECTION,
                "available_banks": banks,
                "bank_page": page,
                "retry_step": None,
                "retry_data": None,
            })
            return

        if retry_step == FLOW_STEP_PAYMENT_METHOD:
            await _send_payment_methods(sender_wa_id, phone_number_id, in_reply_to, country_code=flow_state.get("country_code", "NG"))
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_PAYMENT_METHOD,
                "retry_step": None,
                "retry_data": None,
            })
            return

        if retry_step == FLOW_STEP_AIRPORT_INPUT:
            saved_search = retry_data.get("search_term") if retry_data else None
            if saved_search:
                airports = await _fetch_airports(saved_search)
                if airports is None:
                    await _send_retry_options(
                        to=sender_wa_id,
                        phone_number_id=phone_number_id,
                        in_reply_to=in_reply_to,
                        error_message=f"Still unable to search airports for *\"{saved_search}\"*. The airport service may be temporarily unavailable.",
                        retry_label="Retry Search",
                    )
                    return
                if not airports:
                    await send_text_message(
                        to=sender_wa_id,
                        body=f"No airports found for *\"{saved_search}\"*.\n\nPlease try a different city or state name:",
                        phone_number_id=phone_number_id,
                        in_reply_to=in_reply_to,
                        source="policy_flow",
                    )
                    await _update_flow_state(session, sender_wa_id, {
                        **flow_state,
                        "step": FLOW_STEP_AIRPORT_INPUT,
                        "retry_step": None,
                        "retry_data": None,
                    })
                    return
                policy_id = flow_state.get("policy_id")
                if len(airports) == 1:
                    airport = airports[0]
                    airport_info = {
                        "name": airport.get("name", ""),
                        "iata_code": airport.get("iata_code", ""),
                        "country": airport.get("country", ""),
                    }
                    if policy_id:
                        await set_airport_info(policy_id, airport_info)
                    await _start_itinerary_flow(
                        sender_wa_id, phone_number_id, in_reply_to,
                        session, flow_state, airport_info,
                    )
                else:
                    await _send_dep_airports_page(sender_wa_id, phone_number_id, in_reply_to, airports, 0, saved_search)
                    await _update_flow_state(session, sender_wa_id, {
                        **flow_state,
                        "step": FLOW_STEP_AIRPORT_SELECT,
                        "available_airports": airports,
                        "airport_page": 0,
                        "airport_search_term": saved_search,
                        "retry_step": None,
                        "retry_data": None,
                    })
                return

            await send_text_message(
                to=sender_wa_id,
                body="Please enter the first 3 characters of the *departure airport name* or *airport code* (e.g. LOS, Mur, KAN, Enu, PHC):",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_AIRPORT_INPUT,
                "retry_step": None,
                "retry_data": None,
            })
            return

        if retry_step == FLOW_STEP_ITIN_ARR_AIRPORT_INPUT:
            saved_search = retry_data.get("search_term") if retry_data else None
            if saved_search:
                airports = await _fetch_airports(saved_search)
                if airports is None:
                    await _send_retry_options(
                        to=sender_wa_id,
                        phone_number_id=phone_number_id,
                        in_reply_to=in_reply_to,
                        error_message=f"Still unable to search airports for *\"{saved_search}\"*. The airport service may be temporarily unavailable.",
                        retry_label="Retry Search",
                    )
                    return
                if not airports:
                    await send_text_message(
                        to=sender_wa_id,
                        body=f"No airports found for *\"{saved_search}\"*.\n\nPlease try a different city or state name:",
                        phone_number_id=phone_number_id,
                        in_reply_to=in_reply_to,
                        source="policy_flow",
                    )
                    await _update_flow_state(session, sender_wa_id, {
                        **flow_state,
                        "step": FLOW_STEP_ITIN_ARR_AIRPORT_INPUT,
                        "retry_step": None,
                        "retry_data": None,
                    })
                    return
                itinerary = flow_state.get("itinerary", {})
                if len(airports) == 1:
                    airport = airports[0]
                    arr_airport_info = {
                        "name": airport.get("name", ""),
                        "iata_code": airport.get("iata_code", ""),
                        "country": airport.get("country", ""),
                    }
                    if "arrival" not in itinerary:
                        itinerary["arrival"] = {}
                    itinerary["arrival"]["airport"] = arr_airport_info.get("iata_code", "")
                    itinerary["arrival"]["airportName"] = arr_airport_info.get("name", "")
                    await send_text_message(
                        to=sender_wa_id,
                        body=(
                            f"Arrival airport selected: *{arr_airport_info['name']}* ({arr_airport_info['iata_code']})\n\n"
                            f"Please enter your *arrival date* in DD/MM/YYYY format (e.g. 25/12/2026):"
                        ),
                        phone_number_id=phone_number_id,
                        in_reply_to=in_reply_to,
                        source="policy_flow",
                    )
                    await _update_flow_state(session, sender_wa_id, {
                        **flow_state,
                        "step": FLOW_STEP_ITIN_ARR_DATE,
                        "itinerary": itinerary,
                        "retry_step": None,
                        "retry_data": None,
                    })
                else:
                    await _send_arr_airports_page(sender_wa_id, phone_number_id, in_reply_to, airports, 0, saved_search)
                    await _update_flow_state(session, sender_wa_id, {
                        **flow_state,
                        "step": FLOW_STEP_ITIN_ARR_AIRPORT_SELECT,
                        "itinerary": itinerary,
                        "available_arr_airports": airports,
                        "arr_airport_page": 0,
                        "arr_airport_search_term": saved_search,
                        "retry_step": None,
                        "retry_data": None,
                    })
                return

            await send_text_message(
                to=sender_wa_id,
                body="Please enter your *city or state name* to search for an arrival airport (e.g. London, Dubai, Accra):",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_ITIN_ARR_AIRPORT_INPUT,
                "retry_step": None,
                "retry_data": None,
            })
            return

        await _send_policy_menu(sender_wa_id, phone_number_id, in_reply_to)
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": FLOW_STEP_MENU,
            "policy_id": flow_state.get("policy_id"),
        })
        return

    if current_step == FLOW_STEP_MENU:
        await _handle_menu_selection(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_MSISDN_CONFIRM:
        await _handle_msisdn_confirm(
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_COUNTRY:
        await _handle_country_input(
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_PRODUCT_LIST:
        await _handle_product_list_response(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_PRODUCT_SELECTED:
        await _handle_product_selected_response(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_PRODUCT_CONFIRM:
        await _handle_product_confirm_response(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step in [s["step"] for s in PERSONAL_DETAIL_STEPS]:
        await _handle_personal_detail_input(
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
            current_step=current_step,
        )
    elif current_step == FLOW_STEP_ID_TYPE:
        await _handle_id_type_selection(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_ID_NUMBER:
        await _handle_id_number_input(
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_EXIT_CONFIRM:
        await _handle_exit_confirm_response(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_DETAILS_EDIT_SELECT:
        await _handle_details_edit_select(
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_DETAILS_CONFIRM:
        await _handle_details_confirm_response(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_PAYMENT_METHOD:
        await _handle_payment_method_selection(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_PD_ACCOUNT_NUMBER:
        await _handle_account_number_input(
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_BANK_NAME_INPUT:
        await _handle_bank_name_input(
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_BANK_SELECTION:
        await _handle_bank_selection(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_AIRPORT_INPUT:
        await _handle_airport_input(
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_AIRPORT_SELECT:
        await _handle_airport_selection(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step in (
        FLOW_STEP_ITIN_DEP_DATE, FLOW_STEP_ITIN_DEP_TIME,
        FLOW_STEP_ITIN_ARR_DATE, FLOW_STEP_ITIN_ARR_TIME,
        FLOW_STEP_ITIN_BOOKING_REF, FLOW_STEP_ITIN_FLIGHT_NO,
    ):
        await _handle_itinerary_text_input(
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
            current_step=current_step,
        )
    elif current_step == FLOW_STEP_ITIN_ARR_AIRPORT_INPUT:
        await _handle_arr_airport_input(
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_ITIN_ARR_AIRPORT_SELECT:
        await _handle_arr_airport_selection(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_BOARDING_PASS_CHOICE:
        await _handle_boarding_pass_choice(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_BOARDING_PASS:
        await _handle_boarding_pass_upload(
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_POLICY_SUMMARY:
        await _handle_policy_summary_response(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    else:
        await _send_policy_menu(sender_wa_id, phone_number_id, in_reply_to)
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": FLOW_STEP_MENU,
        })


async def _send_msisdn_confirm_buttons(
    to: str,
    phone_number_id: str,
    in_reply_to: str,
    body: str,
) -> None:
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
                    {"type": "reply", "reply": {"id": BUTTON_MSISDN_YES, "title": "Yes, Proceed"}},
                    {"type": "reply", "reply": {"id": BUTTON_MSISDN_NO, "title": "No, Cancel"}},
                ]
            },
        },
    }
    await send_whatsapp_payload(
        whatsapp_payload=payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _send_policy_menu(to: str, phone_number_id: str, in_reply_to: str) -> None:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "text",
                "text": "Policy Management"
            },
            "body": {
                "text": "Welcome to our Policy Management service! What would you like to do today?"
            },
            "footer": {
                "text": "Select an option below"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": BUTTON_CREATE_NEW,
                            "title": "Purchase Policy"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": BUTTON_SUBMIT_ITINERARY,
                            "title": "Submit Boarding Pass"
                        }
                    }
                ]
            }
        }
    }
    await send_whatsapp_payload(
        payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _send_retry_options(
    to: str,
    phone_number_id: str,
    in_reply_to: str,
    error_message: str,
    retry_label: str = "Try Again",
) -> None:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": error_message
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": BUTTON_RETRY,
                            "title": retry_label[:20]
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": BUTTON_START_OVER,
                            "title": "Start New Policy"
                        }
                    }
                ]
            }
        }
    }
    await send_whatsapp_payload(
        payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _handle_menu_selection(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")

    if reply_id == BUTTON_CREATE_NEW:
        phone_display = _format_phone_display(sender_wa_id)
        confirm_text = (
            f"To create your customer profile, we will use your WhatsApp number "
            f"*[{phone_display}]* as your unique customer identifier.\n\n"
            f"For security reasons, you can only proceed using this WhatsApp number "
            f"and it cannot be changed during this process.\n\n"
            f"Please confirm that you understand and wish to continue."
        )
        await _send_msisdn_confirm_buttons(
            to=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            body=confirm_text,
        )
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": FLOW_STEP_MSISDN_CONFIRM,
            "action": "create_new",
            "policy_id": policy_id,
        })
    elif reply_id == BUTTON_SUBMIT_ITINERARY:
        await send_text_message(
            to=sender_wa_id,
            body="Thank you for choosing to submit an itinerary for an existing policy. This feature is coming soon! Our team will reach out to assist you shortly. In the meantime, you can type 'policy' to start a new policy.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _clear_flow_state(session, sender_wa_id)
    else:
        await send_text_message(
            to=sender_wa_id,
            body="Please select one of the options from the menu above. Tap on 'Purchase Policy' or 'Submit Boarding Pass'.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )


async def _handle_msisdn_confirm(message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")

    reply_id = _get_interactive_reply_id(message)
    if reply_id == BUTTON_MSISDN_YES:
        user_input = "yes"
    elif reply_id == BUTTON_MSISDN_NO:
        user_input = "no"
    elif message.type == "text" and message.text:
        user_input = message.text.body.strip().lower()
    else:
        await _send_msisdn_confirm_buttons(
            to=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            body="Please tap one of the buttons below to confirm or cancel.",
        )
        return

    if user_input in ("no", "n", "nah", "nope"):
        if policy_id:
            await cancel_policy(policy_id)
        session["active_policy_id"] = None
        await send_text_message(
            to=sender_wa_id,
            body="No problem! Policy flow has been cancelled.\n\nType *policy* to start again or *hi* for the main menu.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _clear_flow_state(session, sender_wa_id)
        return

    if user_input not in ("yes", "y", "yeah", "yep", "sure", "ok", "okay"):
        await _send_msisdn_confirm_buttons(
            to=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            body="Please tap one of the buttons below to confirm or cancel.",
        )
        return

    country_code, country_name = _derive_country_from_phone(sender_wa_id)

    if not country_code:
        country_code = "NG"
        country_name = "Nigeria"
        logger.warning(f"Could not derive country from phone {sender_wa_id}, defaulting to {country_code}")

    if policy_id:
        await set_country(policy_id, country_code, country_name)
        logger.info(f"Country {country_code} ({country_name}) auto-derived from phone for policy {policy_id}")

    msisdn_info = {
        "phone_number": sender_wa_id,
        "country_code": country_code,
    }
    if policy_id:
        await set_msisdn_info(policy_id, msisdn_info)
        logger.info(f"MSISDN auto-set from WhatsApp number for policy {policy_id}")

    phone_display = _format_phone_display(sender_wa_id)
    await send_text_message(
        to=sender_wa_id,
        body=(
            f"Great! \U0001F44D Your WhatsApp number *[{phone_display}]* has been confirmed.\n\n"
            f"Please enter the first 3 characters of the *departure airport name* or *airport code* (e.g. LOS, Mur, KAN, Enu, PHC):"
        ),
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )

    await _update_flow_state(session, sender_wa_id, {
        "active": True,
        "step": FLOW_STEP_AIRPORT_INPUT,
        "action": "create_new",
        "policy_id": policy_id,
        "country_code": country_code,
        "country_name": country_name,
        "msisdn_info": msisdn_info,
        "msisdn_confirmed": True,
    })


async def _handle_country_input(message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")

    if message.type != "text" or not message.text:
        await send_text_message(
            to=sender_wa_id,
            body="Please type your *country name* (e.g. Nigeria, Kenya, Ghana):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    user_input = message.text.body.strip()

    extract_result = await _extract_value(
        sender_wa_id=sender_wa_id,
        field_name="country",
        question_asked="Please enter your country name (e.g. Nigeria, Kenya, Ghana):",
        user_response=user_input,
        expected_format="text",
    )

    if extract_result.get("needs_clarification"):
        await send_text_message(
            to=sender_wa_id,
            body=extract_result["clarification_prompt"],
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    if not extract_result.get("is_valid"):
        error_msg = extract_result.get("validation_message", "Please enter a valid country name.")
        await send_text_message(
            to=sender_wa_id,
            body=error_msg,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    cleaned_input = extract_result.get("value", user_input)
    country_code = _resolve_country_code(cleaned_input)

    if not country_code:
        await send_text_message(
            to=sender_wa_id,
            body=f"Sorry, we couldn't recognize *\"{user_input}\"* as a valid country.\n\nPlease enter a valid country name (e.g. Nigeria, Kenya, Ghana) or its 2-letter code (e.g. NG, KE, GH):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    country_name = user_input.title()
    for name, code in COUNTRY_MAP.items():
        if code == country_code and len(name) > 2:
            country_name = name.title()
            break

    if policy_id:
        await set_country(policy_id, country_code, country_name)
        logger.info(f"Country {country_code} ({country_name}) saved to policy {policy_id}")

    await _send_view_products_prompt(sender_wa_id, phone_number_id, in_reply_to, country_name)
    await _update_flow_state(session, sender_wa_id, {
        "active": True,
        "step": FLOW_STEP_PRODUCT_LIST,
        "action": "create_new",
        "policy_id": policy_id,
        "country_code": country_code,
        "country_name": country_name,
    })


async def _send_view_products_prompt(to: str, phone_number_id: str, in_reply_to: str, country_name: str) -> None:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": f"Country set to *{country_name}*.\n\nTap the button below to browse the available products."
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": BUTTON_VIEW_PRODUCTS,
                            "title": "View Products"
                        }
                    }
                ]
            }
        }
    }
    await send_whatsapp_payload(
        payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _handle_product_list_response(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")
    country_code = flow_state.get("country_code", "NG")

    if reply_id == BUTTON_VIEW_PRODUCTS:
        products = await _fetch_products(country_code)
        if not products:
            country_name = flow_state.get("country_name", country_code)
            await _send_retry_options(
                to=sender_wa_id,
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                error_message=f"We couldn't find any available products for *{country_name}*. This could be a temporary issue or there may be no products for this country yet.",
                retry_label="Retry Products",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "retry_step": FLOW_STEP_PRODUCT_LIST,
            })
            return

        page = 0
        await _send_products_page(sender_wa_id, phone_number_id, in_reply_to, products, page, country_code)
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": FLOW_STEP_PRODUCT_SELECTED,
            "action": "create_new",
            "available_products": products,
            "product_page": page,
            "country_code": country_code,
            "country_name": flow_state.get("country_name"),
            "policy_id": policy_id,
        })
    else:
        await send_text_message(
            to=sender_wa_id,
            body="Please tap the 'View Products' button to see available products.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )


async def _fetch_products(country_code: str) -> Optional[list]:
    url = f"{PRODUCTS_API_BASE_URL}/{country_code}"

    async def _single_attempt():
        async with httpx.AsyncClient(timeout=30.0, verify=True) as client:
            logger.info(f"Fetching products for country_code={country_code} from {url}")
            response = await client.get(url)
            logger.info(f"Products API response: HTTP {response.status_code}, size={len(response.content)} bytes")
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    products = data.get("data", [])
                    active = [p for p in products if p.get("status") == "ACTIVE"]
                    logger.info(f"Fetched {len(active)} active products for {country_code}")
                    return active
            logger.warning(f"Products API error: HTTP {response.status_code} for {country_code}, body={response.text[:500]}")
            return None

    return await _api_call_with_retry(f"Products({country_code})", _single_attempt)


def _get_product_pricing(product: dict, country_code: str = None) -> dict:
    pricing = product.get("pricing", [])
    if not pricing:
        return {}
    if country_code:
        for p in pricing:
            if p.get("country") == country_code:
                return p
    return pricing[0]


def _get_product_price_display(product: dict, country_code: str = None) -> str:
    entry = _get_product_pricing(product, country_code)
    if not entry:
        return "Price on request"
    price = entry.get("price")
    currency = entry.get("currency", "")
    if price is not None:
        return f"{currency} {price:,.2f}"
    return "Price on request"


def _get_product_row_description(product: dict, country_code: str = None) -> str:
    coverage = ", ".join(product.get("coverageTypes", []))
    price_str = _get_product_price_display(product, country_code)
    provider = product.get("providerName", "")

    parts = []
    if coverage:
        parts.append(f"Coverage: {coverage}")
    if price_str:
        parts.append(f"Price: {price_str}")
    if provider:
        parts.append(f"Provider: {provider}")
    return "\n".join(parts)[:72]


async def _send_products_page(to: str, phone_number_id: str, in_reply_to: str, products: list, page: int, country_code: str = None) -> None:
    total = len(products)
    total_pages = (total + PRODUCTS_PER_PAGE - 1) // PRODUCTS_PER_PAGE
    start = page * PRODUCTS_PER_PAGE
    end = min(start + PRODUCTS_PER_PAGE, total)
    page_products = products[start:end]

    rows = []
    for product in page_products:
        description = _get_product_row_description(product, country_code)
        rows.append({
            "id": f"{PRODUCT_ID_PREFIX}{product.get('id', '')}",
            "title": str(product.get("name", "Unknown"))[:24],
            "description": description,
        })

    if total_pages > 1:
        if page < total_pages - 1:
            rows.append({
                "id": NAV_NEXT,
                "title": "Next \u25b6",
                "description": f"View more products (page {page + 2} of {total_pages})",
            })
        if page > 0:
            rows.append({
                "id": NAV_PREV,
                "title": "\u25c0 Previous",
                "description": f"Go back (page {page} of {total_pages})",
            })

    page_info = f" (Page {page + 1}/{total_pages})" if total_pages > 1 else ""
    body_text = (
        f"Here are the available insurance products{page_info}.\n"
        f"Showing {start + 1}-{end} of {total} products.\n\n"
        f"Select a product to proceed with your policy.\n"
        f"Tap to select a product."
    )

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {
                "type": "text",
                "text": f"Products{page_info}"
            },
            "body": {
                "text": body_text
            },
            "footer": {
                "text": "Tap to select a product"
            },
            "action": {
                "button": "View Products",
                "sections": [
                    {
                        "title": "Insurance Products",
                        "rows": rows,
                    }
                ]
            }
        }
    }

    await send_whatsapp_payload(
        payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _handle_product_selected_response(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    products = flow_state.get("available_products", [])
    policy_id = flow_state.get("policy_id")
    current_page = flow_state.get("product_page", 0)
    country_code = flow_state.get("country_code")

    if reply_id == NAV_NEXT:
        total_pages = (len(products) + PRODUCTS_PER_PAGE - 1) // PRODUCTS_PER_PAGE
        new_page = min(current_page + 1, total_pages - 1)
        await _send_products_page(sender_wa_id, phone_number_id, in_reply_to, products, new_page, country_code)
        flow_state["product_page"] = new_page
        await _update_flow_state(session, sender_wa_id, flow_state)
        return

    if reply_id == NAV_PREV:
        new_page = max(current_page - 1, 0)
        await _send_products_page(sender_wa_id, phone_number_id, in_reply_to, products, new_page, country_code)
        flow_state["product_page"] = new_page
        await _update_flow_state(session, sender_wa_id, flow_state)
        return

    if reply_id and reply_id.startswith(PRODUCT_ID_PREFIX):
        product_id = reply_id[len(PRODUCT_ID_PREFIX):]

        selected_product = None
        for p in products:
            if str(p.get("id", "")) == product_id:
                selected_product = p
                break

        if not selected_product:
            await _send_retry_options(
                to=sender_wa_id,
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                error_message="We couldn't find that product. It may no longer be available.",
                retry_label="View Products",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "retry_step": FLOW_STEP_PRODUCT_LIST,
            })
            return

        price_display = _get_product_price_display(selected_product, country_code)
        price_entry = _get_product_pricing(selected_product, country_code)

        product_data = {
            "product_id": str(selected_product.get("id", "")),
            "name": selected_product.get("name", ""),
            "description": selected_product.get("description", ""),
            "price": price_entry.get("price"),
            "currency": price_entry.get("currency", ""),
            "validity_days": selected_product.get("validityDays"),
            "coverage_types": selected_product.get("coverageTypes", []),
            "product_type": selected_product.get("productType", ""),
            "provider_name": selected_product.get("providerName", ""),
        }

        if policy_id:
            await set_product_selection(policy_id, product_data)
            logger.info(f"Product saved to policy {policy_id} for user {sender_wa_id}")

        coverage = ", ".join(selected_product.get("coverageTypes", []))
        validity = selected_product.get("validityDays", "")
        confirm_text = (
            f"You've selected *{selected_product.get('name', '')}*\n\n"
            f"_{selected_product.get('description', '')}_\n\n"
            f"*Product details*\n"
            f"\u2022 Product: {selected_product.get('name', '')}\n"
            f"\u2022 Coverage: {coverage}\n"
            f"\u2022 Price: {price_display}\n"
            f"\u2022 Validity: {validity} day{'s' if validity != 1 else ''}\n"
            f"\u2022 Provider: {selected_product.get('providerName', '')}\n\n"
            f"Please confirm if this selection is correct or choose to change it."
        )

        await send_whatsapp_payload(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": sender_wa_id,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": confirm_text},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": BUTTON_PRODUCT_CONFIRM, "title": "Confirm"}},
                            {"type": "reply", "reply": {"id": BUTTON_PRODUCT_CHANGE, "title": "Change Product"}},
                        ]
                    },
                },
            },
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

        session["active_policy_id"] = policy_id
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": FLOW_STEP_PRODUCT_CONFIRM,
            "action": "create_new",
            "selected_product": product_data,
            "available_products": products,
            "product_page": current_page,
            "policy_id": policy_id,
            "country_code": flow_state.get("country_code"),
            "country_name": flow_state.get("country_name"),
            "msisdn_confirmed": flow_state.get("msisdn_confirmed"),
            "msisdn_info": flow_state.get("msisdn_info", {}),
            "channel_info": flow_state.get("channel_info", {}),
        })
    else:
        await send_text_message(
            to=sender_wa_id,
            body="Please select a product from the list above.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )


async def _handle_product_confirm_response(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")

    if reply_id == BUTTON_PRODUCT_CONFIRM:
        first_itin_step = ITINERARY_STEPS[0]
        await send_text_message(
            to=sender_wa_id,
            body=f"Product confirmed! Now let's capture the rest of your itinerary and passenger details.\n\n{first_itin_step['prompt']}",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        existing_itin = flow_state.get("itinerary")
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": first_itin_step["step"],
            "itinerary": existing_itin if existing_itin else {},
        })
        return

    if reply_id == BUTTON_PRODUCT_CHANGE:
        country_code = flow_state.get("country_code", "NG")
        products = flow_state.get("available_products") or await _fetch_products(country_code)
        if products:
            await _send_products_page(sender_wa_id, phone_number_id, in_reply_to, products, 0, country_code)
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_PRODUCT_SELECTED,
                "available_products": products,
                "product_page": 0,
            })
        else:
            await _send_retry_options(
                to=sender_wa_id,
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                error_message="Couldn't fetch products. Please try again.",
                retry_label="Retry Products",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "retry_step": FLOW_STEP_PRODUCT_LIST,
            })
        return

    if message.type == "text" and message.text:
        user_text = message.text.body.strip()
        settings = get_settings()
        if settings.LLM_API_URL:
            from app.services.llm_service import call_generic
            user_session_data = session or {}
            llm_response = await call_generic(
                user_id=sender_wa_id,
                phone_number=user_session_data.get("phone_number", sender_wa_id),
                message=user_text,
                user_name=user_session_data.get("first_name", ""),
                current_node=user_session_data.get("current_node", "N01"),
            )
            if llm_response and llm_response.get("response"):
                await send_text_message(
                    to=sender_wa_id,
                    body=llm_response["response"],
                    phone_number_id=phone_number_id,
                    in_reply_to=in_reply_to,
                    source="llm",
                )

    selected_product = flow_state.get("selected_product", {})
    coverage = ", ".join(selected_product.get("coverage_types", []))
    validity = selected_product.get("validity_days", "")
    price_str = f"{selected_product.get('currency', '')} {selected_product.get('price', '')}".strip()
    confirm_text = (
        f"You've selected *{selected_product.get('name', '')}*\n\n"
        f"*Product details*\n"
        f"\u2022 Product: {selected_product.get('name', '')}\n"
        f"\u2022 Coverage: {coverage}\n"
        f"\u2022 Price: {price_str}\n"
        f"\u2022 Validity: {validity} day{'s' if validity != 1 else ''}\n"
        f"\u2022 Provider: {selected_product.get('provider_name', '')}\n\n"
        f"Please confirm if this selection is correct or choose to change it."
    )
    await send_whatsapp_payload(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": sender_wa_id,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": confirm_text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": BUTTON_PRODUCT_CONFIRM, "title": "Confirm"}},
                        {"type": "reply", "reply": {"id": BUTTON_PRODUCT_CHANGE, "title": "Change Product"}},
                    ]
                },
            },
        },
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


def _get_text_input(message: WhatsAppMessage) -> Optional[str]:
    if message.type == "text" and message.text:
        return message.text.body.strip()
    return None


async def _extract_value(
    sender_wa_id: str,
    field_name: str,
    question_asked: str,
    user_response: str,
    expected_format: str = "text",
) -> dict:
    result = await call_extract(
        user_id=sender_wa_id,
        field_name=field_name,
        question_asked=question_asked,
        user_response=user_response,
        expected_format=expected_format,
    )

    if not result:
        logger.warning(f"LLM extract unavailable for field={field_name}, using raw input")
        return {
            "value": user_response,
            "is_valid": True,
            "needs_clarification": False,
            "fallback": True,
        }

    if result.get("needs_clarification"):
        return {
            "value": None,
            "is_valid": False,
            "needs_clarification": True,
            "clarification_prompt": result.get("clarification_prompt", "Could you please clarify your input?"),
            "fallback": False,
        }

    if result.get("is_valid"):
        return {
            "value": result.get("extracted_value", user_response),
            "is_valid": True,
            "needs_clarification": False,
            "fallback": False,
        }

    validation_msg = result.get("validation_message", "")
    return {
        "value": None,
        "is_valid": False,
        "needs_clarification": False,
        "validation_message": validation_msg or "The input doesn't seem valid. Please try again.",
        "fallback": False,
    }


def _validate_email(email: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email))


async def _handle_personal_detail_input(message, sender_wa_id, phone_number_id, in_reply_to, session, current_step):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")
    personal_details = flow_state.get("personal_details", {})

    text_input = _get_text_input(message)
    if not text_input:
        await send_text_message(
            to=sender_wa_id,
            body="Please send a text message with the requested information.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    current_idx = None
    current_field = None
    current_step_info = None
    for idx, step_info in enumerate(PERSONAL_DETAIL_STEPS):
        if step_info["step"] == current_step:
            current_idx = idx
            current_field = step_info["field"]
            current_step_info = step_info
            break

    if current_idx is None:
        return

    extracted_value = text_input.strip()

    if current_field in ("first_name", "last_name"):
        words = extracted_value.split()
        if len(words) == 1:
            extracted_value = extracted_value.title()
        else:
            settings = get_settings()
            if settings.LLM_API_URL:
                try:
                    from app.services.llm_service import call_extract
                    extract_result = await call_extract(
                        user_id=sender_wa_id,
                        field_name=current_field,
                        question_asked=current_step_info["prompt"],
                        user_response=extracted_value,
                        expected_format="text",
                    )
                    if extract_result and extract_result.get("value"):
                        extracted_value = extract_result["value"].strip().title()
                    else:
                        extracted_value = words[0].title()
                except Exception as e:
                    logger.warning(f"LLM extract failed for {current_field}, using first word: {e}")
                    extracted_value = words[0].title()
            else:
                extracted_value = words[0].title()

        if not extracted_value or not extracted_value.strip():
            label = "first name" if current_field == "first_name" else "last name"
            await send_text_message(
                to=sender_wa_id,
                body=f"Please enter a valid {label}:",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            return

    elif current_field == "email":
        if not _validate_email(extracted_value):
            await send_text_message(
                to=sender_wa_id,
                body="That doesn't look like a valid email address. Please enter a valid email (e.g. name@example.com):",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            return

    personal_details[current_field] = extracted_value

    if flow_state.get("editing_field"):
        if policy_id:
            await set_personal_details(policy_id, personal_details)
        new_state = {**flow_state, "step": FLOW_STEP_DETAILS_CONFIRM, "personal_details": personal_details}
        new_state.pop("editing_field", None)
        await _send_details_confirmation(sender_wa_id, phone_number_id, in_reply_to, new_state)
        await _update_flow_state(session, sender_wa_id, new_state)
        return

    next_idx = current_idx + 1
    if next_idx < len(PERSONAL_DETAIL_STEPS):
        next_step = PERSONAL_DETAIL_STEPS[next_idx]
        await send_text_message(
            to=sender_wa_id,
            body=next_step["prompt"],
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": next_step["step"],
            "personal_details": personal_details,
        })
    else:
        if policy_id:
            await set_personal_details(policy_id, personal_details)
            logger.info(f"Personal details saved to policy {policy_id}")

        summary = (
            f"Personal details saved:\n\n"
            f"Name: {personal_details.get('first_name', '')} {personal_details.get('last_name', '')}\n"
            f"Email: {personal_details.get('email', '')}\n"
        )
        await send_text_message(
            to=sender_wa_id,
            body=summary,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

        await _send_id_type_selection(sender_wa_id, phone_number_id, in_reply_to)
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_ID_TYPE,
            "personal_details": personal_details,
        })


async def _send_id_type_selection(to: str, phone_number_id: str, in_reply_to: str) -> None:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": (
                    "To continue with your policy purchase, we need a valid digital id number "
                    "to verify your details.\n\n"
                    "Please choose the identification number you would like to provide for verification:"
                )
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": BUTTON_ID_NIN,
                            "title": "NIN (11 digit)"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": BUTTON_ID_BVN,
                            "title": "BVN (11 digit)"
                        }
                    }
                ]
            }
        }
    }
    await send_whatsapp_payload(
        payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _handle_id_type_selection(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)

    if reply_id == BUTTON_ID_NIN:
        id_type = "NIN"
        id_label = "NIN (National Identification Number)"
    elif reply_id == BUTTON_ID_BVN:
        id_type = "BVN"
        id_label = "BVN (Bank Verification Number)"
    else:
        await send_text_message(
            to=sender_wa_id,
            body="Please select one of the options: *NIN* or *BVN*.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    await send_text_message(
        to=sender_wa_id,
        body=f"You selected *{id_label}*.\n\nPlease enter your *11-digit {id_type}*:",
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )
    await _update_flow_state(session, sender_wa_id, {
        **flow_state,
        "step": FLOW_STEP_ID_NUMBER,
        "id_type": id_type,
    })


async def _handle_id_number_input(message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")
    id_type = flow_state.get("id_type", "NIN")

    text_input = _get_text_input(message)
    if not text_input:
        await send_text_message(
            to=sender_wa_id,
            body=f"Please enter your *11-digit {id_type}* as a text message.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    cleaned = re.sub(r"[^0-9]", "", text_input.strip())

    if len(cleaned) != 11:
        await send_text_message(
            to=sender_wa_id,
            body=f"Your {id_type} must be exactly *11 digits*. Please enter a valid *11-digit {id_type}*:",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    if policy_id:
        await set_id_verification(policy_id, id_type, cleaned)
        logger.info(f"{id_type} '***{cleaned[-4:]}' saved to policy {policy_id}")

    flow_state["id_number"] = cleaned
    flow_state.pop("editing_field", None)
    await _send_details_confirmation(sender_wa_id, phone_number_id, in_reply_to, flow_state)
    new_state = {**flow_state, "step": FLOW_STEP_DETAILS_CONFIRM, "id_number": cleaned}
    new_state.pop("editing_field", None)
    await _update_flow_state(session, sender_wa_id, new_state)


async def _send_details_confirmation(sender_wa_id, phone_number_id, in_reply_to, flow_state):
    itinerary = flow_state.get("itinerary", {})
    personal_details = flow_state.get("personal_details", {})
    selected_product = flow_state.get("selected_product", {})
    id_type = flow_state.get("id_type", "")
    id_number = flow_state.get("id_number", "")

    dep = itinerary.get("departure", {})
    arr = itinerary.get("arrival", {})

    summary_lines = ["*Please review your itinerary and passenger details:*\n"]

    product_name = selected_product.get("name", "")
    if product_name:
        price_str = f"{selected_product.get('currency', '')} {selected_product.get('price', '')}".strip()
        summary_lines.append(f"*Product:* {product_name} ({price_str})")

    summary_lines.append("")
    summary_lines.append("*Itinerary:*")
    if dep.get("airportName"):
        summary_lines.append(f"\u2022 Departure Airport: {dep['airportName']} ({dep.get('airport', '')})")
    elif dep.get("airport"):
        summary_lines.append(f"\u2022 Departure Airport: {dep['airport']}")
    if dep.get("scheduledDateLocal"):
        summary_lines.append(f"\u2022 Departure Date: {dep['scheduledDateLocal']}")
    if dep.get("scheduledTimeLocal"):
        summary_lines.append(f"\u2022 Departure Time: {dep['scheduledTimeLocal']}")
    if arr.get("airportName"):
        summary_lines.append(f"\u2022 Arrival Airport: {arr['airportName']} ({arr.get('airport', '')})")
    elif arr.get("airport"):
        summary_lines.append(f"\u2022 Arrival Airport: {arr['airport']}")
    if arr.get("scheduledDateLocal"):
        summary_lines.append(f"\u2022 Arrival Date: {arr['scheduledDateLocal']}")
    if arr.get("scheduledTimeLocal"):
        summary_lines.append(f"\u2022 Arrival Time: {arr['scheduledTimeLocal']}")
    if itinerary.get("bookingReference"):
        summary_lines.append(f"\u2022 Booking Ref: {itinerary['bookingReference']}")
    if itinerary.get("flightNo"):
        summary_lines.append(f"\u2022 Flight No: {itinerary['flightNo']}")

    summary_lines.append("")
    summary_lines.append("*Passenger Details:*")
    first_name = personal_details.get("first_name", "")
    last_name = personal_details.get("last_name", "")
    if first_name or last_name:
        summary_lines.append(f"\u2022 Name: {first_name} {last_name}")
    if personal_details.get("email"):
        summary_lines.append(f"\u2022 Email: {personal_details['email']}")
    if id_type and id_number:
        masked = f"***{id_number[-4:]}" if len(id_number) >= 4 else id_number
        summary_lines.append(f"\u2022 {id_type}: {masked}")

    summary_lines.append("\nAre these details correct?")

    summary_text = "\n".join(summary_lines)

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": sender_wa_id,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": summary_text},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": BUTTON_DETAILS_CONFIRM, "title": "Yes, Proceed"}},
                    {"type": "reply", "reply": {"id": BUTTON_DETAILS_CHANGE, "title": "No, Change details"}},
                ]
            },
        },
    }

    await send_whatsapp_payload(
        payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _handle_details_confirm_response(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)

    if reply_id == BUTTON_DETAILS_CONFIRM:
        await send_text_message(
            to=sender_wa_id,
            body=(
                "Now let's capture a few more details so we can set up your payment and payout preferences.\n\n"
                "We'll ask for your payment method, payout method, and bank account details to complete your purchase.\n\n"
                "Please select your preferred payment method:"
            ),
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _send_payment_methods(sender_wa_id, phone_number_id, in_reply_to, country_code=flow_state.get("country_code", "NG"))
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_PAYMENT_METHOD,
        })
        return

    if reply_id == BUTTON_DETAILS_CHANGE:
        await _send_edit_field_menu(sender_wa_id, phone_number_id, in_reply_to, flow_state)
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_DETAILS_EDIT_SELECT,
        })
        return

    if message.type == "text" and message.text:
        user_text = message.text.body.strip()
        settings = get_settings()
        if settings.LLM_API_URL:
            from app.services.llm_service import call_generic
            user_session_data = session or {}
            llm_response = await call_generic(
                user_id=sender_wa_id,
                phone_number=user_session_data.get("phone_number", sender_wa_id),
                message=user_text,
                user_name=user_session_data.get("first_name", ""),
                current_node=user_session_data.get("current_node", "N01"),
            )
            if llm_response and llm_response.get("response"):
                await send_text_message(
                    to=sender_wa_id,
                    body=llm_response["response"],
                    phone_number_id=phone_number_id,
                    in_reply_to=in_reply_to,
                    source="llm",
                )

    await _send_details_confirmation(sender_wa_id, phone_number_id, in_reply_to, flow_state)


async def _handle_exit_confirm_response(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")
    pre_exit_step = flow_state.get("pre_exit_step")

    if reply_id == BUTTON_EXIT_YES:
        if policy_id:
            await cancel_policy(policy_id)
        session["active_policy_id"] = None
        await send_text_message(
            to=sender_wa_id,
            body="Policy flow cancelled. No worries!\n\nType *policy* to start a new one or *hi* for the main menu.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _clear_flow_state(session, sender_wa_id)
        return

    if reply_id == BUTTON_EXIT_NO:
        resume_step = pre_exit_step or FLOW_STEP_MENU
        new_state = {**flow_state, "step": resume_step}
        new_state.pop("pre_exit_step", None)
        await send_text_message(
            to=sender_wa_id,
            body="Great, let's continue where you left off!",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _send_step_prompt(resume_step, sender_wa_id, phone_number_id, in_reply_to, session, new_state)
        await _update_flow_state(session, sender_wa_id, new_state)
        return

    await send_text_message(
        to=sender_wa_id,
        body="Please tap one of the buttons: *Yes, Cancel* or *No, Let's Resume*.",
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _send_edit_field_menu(sender_wa_id, phone_number_id, in_reply_to, flow_state):
    itinerary = flow_state.get("itinerary", {})
    personal_details = flow_state.get("personal_details", {})
    dep = itinerary.get("departure", {})
    arr = itinerary.get("arrival", {})

    current_values = {
        1: dep.get("scheduledDateLocal", "—"),
        2: dep.get("scheduledTimeLocal", "—"),
        3: f"{arr.get('airportName', '')} ({arr.get('airport', '')})".strip(" ()") or "—",
        4: arr.get("scheduledDateLocal", "—"),
        5: arr.get("scheduledTimeLocal", "—"),
        6: itinerary.get("bookingReference", "—"),
        7: itinerary.get("flightNo", "—"),
        8: personal_details.get("first_name", "—"),
        9: personal_details.get("last_name", "—"),
        10: personal_details.get("email", "—"),
        11: f"{flow_state.get('id_type', '')} ***{flow_state.get('id_number', '')[-4:]}" if flow_state.get("id_number") and len(flow_state.get("id_number", "")) >= 4 else flow_state.get("id_type", "—"),
    }

    lines = ["Which detail would you like to change? Reply with the *number*:\n"]
    for ef in DETAILS_EDITABLE_FIELDS:
        lines.append(f"*{ef['num']}.* {ef['label']}: {current_values.get(ef['num'], '—')}")

    lines.append("\nReply with a number (1-11) or type *#back* to go back.")

    await send_text_message(
        to=sender_wa_id,
        body="\n".join(lines),
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _handle_details_edit_select(message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    text_input = _get_text_input(message)

    if not text_input:
        await _send_edit_field_menu(sender_wa_id, phone_number_id, in_reply_to, flow_state)
        return

    cleaned = text_input.strip()

    if cleaned.lower() in ("#cancel", "#back"):
        await _send_details_confirmation(sender_wa_id, phone_number_id, in_reply_to, flow_state)
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_DETAILS_CONFIRM,
        })
        return

    try:
        choice = int(cleaned)
    except ValueError:
        await send_text_message(
            to=sender_wa_id,
            body="Please reply with a number between *1* and *11* to select which detail to change.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    selected = None
    for ef in DETAILS_EDITABLE_FIELDS:
        if ef["num"] == choice:
            selected = ef
            break

    if not selected:
        await send_text_message(
            to=sender_wa_id,
            body="Invalid choice. Please reply with a number between *1* and *11*.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    target_step = selected["step"]

    if target_step == FLOW_STEP_ID_TYPE:
        await _send_id_type_selection(sender_wa_id, phone_number_id, in_reply_to)
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_ID_TYPE,
            "editing_field": True,
        })
        return

    if target_step == FLOW_STEP_ITIN_ARR_AIRPORT_INPUT:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter the first 3 characters of the *arrival airport name* or *airport code* (e.g. LOS, Mur, KAN, Enu, PHC):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_ITIN_ARR_AIRPORT_INPUT,
            "editing_field": True,
        })
        return

    prompt_map = {
        FLOW_STEP_ITIN_DEP_DATE: "Please enter your scheduled *departure date* (e.g. 25/12/2026, 25-12-2026):",
        FLOW_STEP_ITIN_DEP_TIME: "Please enter your scheduled *departure time* (e.g. 14:30):",
        FLOW_STEP_ITIN_ARR_DATE: "Please enter your scheduled *arrival date* (e.g. 25/12/2026 or 25-12-2026):",
        FLOW_STEP_ITIN_ARR_TIME: "Please enter your scheduled *arrival time* (e.g. 16:30):",
        FLOW_STEP_ITIN_BOOKING_REF: "Please enter your *booking reference* (e.g. ABC123):",
        FLOW_STEP_ITIN_FLIGHT_NO: "Please enter your *flight number* (e.g. BA1234):",
        FLOW_STEP_PD_FIRST_NAME: "Please enter your *first name*:",
        FLOW_STEP_PD_LAST_NAME: "Please enter your *last name*:",
        FLOW_STEP_PD_EMAIL: "Please enter your *email address*:",
    }

    prompt = prompt_map.get(target_step, "Please enter the new value:")
    await send_text_message(
        to=sender_wa_id,
        body=prompt,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )
    await _update_flow_state(session, sender_wa_id, {
        **flow_state,
        "step": target_step,
        "editing_field": True,
    })


async def _handle_account_number_input(message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")

    text_input = _get_text_input(message)
    if not text_input:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter your *10-digit account number* as a text message.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    extract_result = await _extract_value(
        sender_wa_id=sender_wa_id,
        field_name="account_number",
        question_asked="Please enter your 10-digit bank account number.",
        user_response=text_input,
        expected_format="number",
    )

    if extract_result.get("needs_clarification"):
        await send_text_message(
            to=sender_wa_id,
            body=extract_result["clarification_prompt"],
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    extracted_raw = extract_result.get("value") or text_input
    cleaned = re.sub(r"[^0-9]", "", extracted_raw.strip())

    if len(cleaned) != 10:
        await send_text_message(
            to=sender_wa_id,
            body=f"Your account number must be exactly *10 digits*. Please enter a valid *10-digit account number*:",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    if policy_id:
        await set_account_number(policy_id, cleaned)
        logger.info(f"Account number '***{cleaned[-4:]}' saved to policy {policy_id}")

    await send_text_message(
        to=sender_wa_id,
        body=(
            f"Account number saved: *{cleaned}*\n\n"
            f"Please enter the first 3 characters of your payout bank name (e.g. Zen, Wem):"
        ),
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )

    await _update_flow_state(session, sender_wa_id, {
        **flow_state,
        "step": FLOW_STEP_BANK_NAME_INPUT,
        "account_number": cleaned,
    })


async def _fetch_payment_methods(country_code: str) -> Optional[list]:
    async def _single_attempt():
        async with httpx.AsyncClient(timeout=30.0, verify=True) as client:
            url = f"{PAYMENT_METHODS_API_URL}?countryCode={country_code}"
            logger.info(f"Fetching payment methods from {url}")
            response = await client.get(url)
            logger.info(f"Payment methods API response: HTTP {response.status_code}, size={len(response.content)} bytes")
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    logger.info(f"Fetched {len(data)} payment methods for country={country_code}")
                    return data
            logger.warning(f"Payment methods API error: HTTP {response.status_code}, body={response.text[:500]}")
            return None

    return await _api_call_with_retry(f"PaymentMethods({country_code})", _single_attempt)


PAYMENT_METHOD_LABELS = {
    "CARD": "Card",
    "BANK_TRANSFER": "Bank Transfer",
    "USSD": "USSD",
    "OPAY": "OPay",
    "MOMO_MOBILE_MONEY": "MTN MoMo",
    "SMARTCASH_MOBILE_MONEY": "SmartCash",
    "GOOGLE_PAY": "Google Pay",
    "APPLE_PAY": "Apple Pay",
    "BANK_ACCOUNT": "Bank Account",
    "MOBILE_MONEY": "Mobile Money",
    "WALLET": "Wallet",
}

PAYMENT_METHOD_SUBMISSION_MAP = {
    "CARD": "CARD",
    "BANK_TRANSFER": "BANK_TRANSFER",
    "USSD": "USSD",
    "OPAY": "OPAY",
    "MOMO_MOBILE_MONEY": "MOMO_MOBILE_MONEY",
    "SMARTCASH_MOBILE_MONEY": "SMARTCASH_MOBILE_MONEY",
    "GOOGLE_PAY": "GOOGLE_PAY",
    "APPLE_PAY": "APPLE_PAY",
    "BANK_ACCOUNT": "BANK_TRANSFER",
    "MOBILE_MONEY": "MOBILE_MONEY",
    "WALLET": "WALLET",
}


async def _send_payment_methods(to: str, phone_number_id: str, in_reply_to: str, country_code: str = "NG") -> None:
    methods = await _fetch_payment_methods(country_code)
    if not methods:
        methods = ["CARD", "BANK_TRANSFER", "USSD"]

    rows = []
    for method in methods:
        rows.append({
            "id": f"{PAYMENT_METHOD_PREFIX}{method}",
            "title": PAYMENT_METHOD_LABELS.get(method, method.replace("_", " ").title())[:24],
        })

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {
                "text": "Please select your preferred payment method:"
            },
            "action": {
                "button": "Select Method",
                "sections": [
                    {
                        "title": "Payment Methods",
                        "rows": rows,
                    }
                ]
            }
        }
    }

    await send_whatsapp_payload(
        payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _handle_payment_method_selection(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")

    if reply_id and reply_id.startswith(PAYMENT_METHOD_PREFIX):
        method = reply_id[len(PAYMENT_METHOD_PREFIX):]
        label = PAYMENT_METHOD_LABELS.get(method, method.replace("_", " ").title())

        if policy_id:
            await set_payment_method(policy_id, method)
            logger.info(f"Payment method '{method}' saved to policy {policy_id}")

        await send_text_message(
            to=sender_wa_id,
            body=f"Payment method selected: *{label}*\n\nPlease enter your *10-digit account number* for future payouts:",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_PD_ACCOUNT_NUMBER,
            "payment_method": method,
            "payout_method": "BANK_ACCOUNT",
        })
    else:
        await send_text_message(
            to=sender_wa_id,
            body="Please select one of the payment method options above.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )


async def _fetch_banks(country_code: str) -> Optional[list]:
    async def _single_attempt():
        async with httpx.AsyncClient(timeout=60.0, verify=True) as client:
            logger.info(f"Fetching banks for country_code={country_code}")
            response = await client.get(
                BANKS_API_URL,
                params={"countryCode": country_code},
            )
            logger.info(f"Banks API response: HTTP {response.status_code}, size={len(response.content)} bytes")
            if response.status_code == 200:
                data = response.json()
                bank_data = data.get("data", data)
                if isinstance(bank_data, dict):
                    bank_data = bank_data.get("data", [])
                if isinstance(bank_data, list):
                    logger.info(f"Fetched {len(bank_data)} banks for {country_code}")
                    return bank_data
            logger.warning(f"Banks API error: HTTP {response.status_code} for {country_code}, body={response.text[:500]}")
            return None

    return await _api_call_with_retry(f"Banks({country_code})", _single_attempt)


async def _send_banks_page(to: str, phone_number_id: str, in_reply_to: str, banks: list, page: int) -> None:
    total = len(banks)
    total_pages = (total + BANKS_PER_PAGE - 1) // BANKS_PER_PAGE
    start = page * BANKS_PER_PAGE
    end = min(start + BANKS_PER_PAGE, total)
    page_banks = banks[start:end]

    rows = []
    for bank in page_banks:
        rows.append({
            "id": f"{BANK_ID_PREFIX}{bank.get('id', '')}",
            "title": str(bank.get("name", "Unknown"))[:24],
            "description": f"Code: {bank.get('code', '')}"[:72],
        })

    if total_pages > 1:
        if page < total_pages - 1:
            rows.append({
                "id": BANK_NAV_NEXT,
                "title": "Next \u25b6",
                "description": f"View more banks (page {page + 2} of {total_pages})",
            })
        if page > 0:
            rows.append({
                "id": BANK_NAV_PREV,
                "title": "\u25c0 Previous",
                "description": f"Go back (page {page} of {total_pages})",
            })

    rows.append({
        "id": BANK_SEARCH_AGAIN,
        "title": "\U0001f50d Search Again",
        "description": "Search for a different bank",
    })

    page_info = f" (Page {page + 1}/{total_pages})" if total_pages > 1 else ""
    body_text = (
        f"Please select your bank{page_info}.\n"
        f"Showing {start + 1}-{end} of {total} banks.\n"
        f"Banks sorted alphabetically"
    )

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {
                "type": "text",
                "text": f"Select Bank{page_info}"
            },
            "body": {
                "text": body_text
            },
            "action": {
                "button": "View Banks",
                "sections": [
                    {
                        "title": "Banks",
                        "rows": rows,
                    }
                ]
            }
        }
    }

    await send_whatsapp_payload(
        payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _handle_bank_selection(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    banks = flow_state.get("available_banks", [])
    policy_id = flow_state.get("policy_id")
    current_page = flow_state.get("bank_page", 0)

    if reply_id == BANK_NAV_NEXT:
        total_pages = (len(banks) + BANKS_PER_PAGE - 1) // BANKS_PER_PAGE
        new_page = min(current_page + 1, total_pages - 1)
        await _send_banks_page(sender_wa_id, phone_number_id, in_reply_to, banks, new_page)
        flow_state["bank_page"] = new_page
        await _update_flow_state(session, sender_wa_id, flow_state)
        return

    if reply_id == BANK_NAV_PREV:
        new_page = max(current_page - 1, 0)
        await _send_banks_page(sender_wa_id, phone_number_id, in_reply_to, banks, new_page)
        flow_state["bank_page"] = new_page
        await _update_flow_state(session, sender_wa_id, flow_state)
        return

    if reply_id == BANK_SEARCH_AGAIN:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter the first 3 characters of your payout bank name (e.g. Zen, Wem, GTB):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_BANK_NAME_INPUT,
        })
        return

    if reply_id and reply_id.startswith(BANK_ID_PREFIX):
        bank_id_str = reply_id[len(BANK_ID_PREFIX):]

        selected_bank = None
        for b in banks:
            if str(b.get("id", "")) == bank_id_str:
                selected_bank = b
                break

        if not selected_bank:
            await _send_retry_options(
                to=sender_wa_id,
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                error_message="We couldn't find that bank. It may no longer be available.",
                retry_label="View Banks",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "retry_step": FLOW_STEP_BANK_SELECTION,
            })
            return

        bank_details = {
            "bank_id": selected_bank.get("id"),
            "bank_code": selected_bank.get("code", ""),
            "bank_name": selected_bank.get("name", ""),
        }
        if selected_bank.get("branch_code"):
            bank_details["branch_code"] = selected_bank["branch_code"]

        if policy_id:
            await set_bank_details(policy_id, bank_details)
            logger.info(f"Bank '{bank_details['bank_name']}' saved to policy {policy_id}")

        msisdn_info = flow_state.get("msisdn_info", {
            "phone_number": sender_wa_id,
            "country_code": flow_state.get("country_code", ""),
        })

        await _finalize_channel_and_boarding_pass_prompt(
            sender_wa_id, phone_number_id, in_reply_to,
            session, flow_state, policy_id,
            bank_details, msisdn_info,
        )
    else:
        text_input = _get_text_input(message)
        if text_input and len(text_input.strip()) >= 3:
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_BANK_NAME_INPUT,
            })
            await _handle_bank_name_input(
                message=message,
                sender_wa_id=sender_wa_id,
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                session=session,
            )
        else:
            await send_text_message(
                to=sender_wa_id,
                body="Please select a bank from the list, or type at least 3 characters to search for a different bank.",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            await _send_banks_page(sender_wa_id, phone_number_id, in_reply_to, banks, current_page)


async def _finalize_channel_and_boarding_pass_prompt(
    sender_wa_id, phone_number_id, in_reply_to,
    session, flow_state, policy_id,
    bank_details, msisdn_info,
):
    channel_info = {
        "channel_payout_method": "Bank",
        "source": "passenger",
        "consent": True,
    }

    if policy_id:
        await set_channel_info(policy_id, channel_info)
        logger.info(f"Channel info auto-set for policy {policy_id}")

    await _send_boarding_pass_choice(sender_wa_id, phone_number_id, in_reply_to)

    await _update_flow_state(session, sender_wa_id, {
        **flow_state,
        "step": FLOW_STEP_BOARDING_PASS_CHOICE,
        "bank_details": bank_details,
        "msisdn_info": msisdn_info,
        "channel_info": channel_info,
    })


async def _handle_bank_name_input(message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")

    text_input = _get_text_input(message)
    if not text_input:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter the first 3 characters of your payout bank name (e.g. Zen, Wem):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    search_term = text_input.strip()

    country_code = flow_state.get("country_code", "NG")
    all_banks = await _fetch_banks(country_code)
    if all_banks is None:
        await _send_retry_options(
            to=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            error_message="We couldn't fetch available banks at the moment. The banking service may be temporarily unavailable.",
            retry_label="Retry Search",
        )
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "retry_step": FLOW_STEP_BANK_NAME_INPUT,
            "retry_data": {"search_term": search_term},
        })
        return

    search_lower = search_term.lower()
    filtered_banks = [
        b for b in all_banks
        if search_lower in b.get("name", "").lower()
    ]

    if not filtered_banks:
        await send_text_message(
            to=sender_wa_id,
            body=f"No banks found matching *\"{text_input}\"*.\n\nPlease try again with a different bank name (e.g. Zen, Wem, GTB):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    filtered_banks.sort(key=lambda b: b.get("name", "").lower())
    page = 0
    await _send_banks_page(sender_wa_id, phone_number_id, in_reply_to, filtered_banks, page)

    await _update_flow_state(session, sender_wa_id, {
        **flow_state,
        "step": FLOW_STEP_BANK_SELECTION,
        "available_banks": filtered_banks,
        "bank_page": page,
    })


async def _send_boarding_pass_choice(sender_wa_id, phone_number_id, in_reply_to):
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": sender_wa_id,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": (
                    "*Boarding pass upload*\n\n"
                    "Do you have a clear image of your boarding pass to upload now?\n\n"
                    "You can also upload it later, but please note that payouts "
                    "cannot be processed until we receive it.\n\n"
                    "Accepted formats: JPG, PNG, WebP, PDF\n"
                    "Maximum size: 20MB\n"
                    "Make sure the name, flight details, barcode and date are clearly visible.\n\n"
                    "Would you like to upload it now?"
                )
            },
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": BUTTON_BP_UPLOAD_NOW, "title": "Upload now"}},
                    {"type": "reply", "reply": {"id": BUTTON_BP_UPLOAD_LATER, "title": "Upload later"}},
                ]
            },
        },
    }

    await send_whatsapp_payload(
        payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _handle_boarding_pass_choice(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)

    if reply_id == BUTTON_BP_UPLOAD_NOW:
        await send_text_message(
            to=sender_wa_id,
            body=(
                "Please upload a clear image of your *boarding pass*.\n\n"
                "Accepted formats: JPG, PNG, WebP, PDF\n"
                "Maximum size: 20MB\n"
                "Make sure the name, flight details, barcode and date are clearly visible."
            ),
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_BOARDING_PASS,
        })
    elif reply_id == BUTTON_BP_UPLOAD_LATER:
        logger.info(f"User {sender_wa_id} chose 'Upload later', proceeding to policy summary")
        await _send_policy_summary_confirmation(
            sender_wa_id, phone_number_id, in_reply_to,
            session, flow_state, boarding_pass_uploaded=False,
        )
        logger.info(f"Policy summary sent to {sender_wa_id} after 'Upload later'")
    elif reply_id is None and message.type in ("image", "document"):
        logger.info(f"User {sender_wa_id} sent media at boarding pass choice step, treating as direct upload")
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_BOARDING_PASS,
        })
        await _handle_boarding_pass_upload(
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    else:
        text_input = _get_text_input(message)
        if text_input and text_input.lower() in ("upload now", "now", "yes", "y"):
            await send_text_message(
                to=sender_wa_id,
                body=(
                    "Please upload a clear image of your *boarding pass*.\n\n"
                    "Accepted formats: JPG, PNG, WebP, PDF\n"
                    "Maximum size: 20MB\n"
                    "Make sure the name, flight details, barcode and date are clearly visible."
                ),
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_BOARDING_PASS,
            })
        elif text_input and text_input.lower() in ("upload later", "later", "no", "n"):
            await _send_policy_summary_confirmation(
                sender_wa_id, phone_number_id, in_reply_to,
                session, flow_state, boarding_pass_uploaded=False,
            )
        else:
            await _send_boarding_pass_choice(sender_wa_id, phone_number_id, in_reply_to)


async def _send_policy_summary_confirmation(
    sender_wa_id, phone_number_id, in_reply_to,
    session, flow_state, boarding_pass_uploaded=False,
):
    personal_details = flow_state.get("personal_details", {})
    selected_product = flow_state.get("selected_product", {})
    payment_method = flow_state.get("payment_method", "")
    country_code = flow_state.get("country_code", "")
    bank_details = flow_state.get("bank_details", {})
    msisdn_info = flow_state.get("msisdn_info", {})
    id_type = flow_state.get("id_type", "")
    id_number = flow_state.get("id_number", "")
    account_number = flow_state.get("account_number", "")
    payment_label = PAYMENT_METHOD_LABELS.get(payment_method, payment_method)
    itinerary = flow_state.get("itinerary", {})
    dep = itinerary.get("departure", {})
    arr = itinerary.get("arrival", {})

    id_line = f"{id_type}: {id_number}" if id_type and id_number else ""
    msisdn_display = msisdn_info.get("phone_number", "")
    if msisdn_display and not msisdn_display.startswith("+"):
        msisdn_display = f"+{msisdn_display}"

    bp_status = "Uploaded ✓" if boarding_pass_uploaded else "Not uploaded (will upload later)"

    itinerary_section = ""
    if itinerary:
        itinerary_section = (
            f"\n*Itinerary:*\n"
            f"Booking Ref: {itinerary.get('bookingReference', '')}\n"
            f"Flight: {itinerary.get('flightNo', '')}\n"
            f"Departure: {dep.get('airportName', '')} ({dep.get('airport', '')}) on {dep.get('scheduledDateLocal', '')} at {dep.get('scheduledTimeLocal', '')}\n"
            f"Arrival: {arr.get('airportName', '')} ({arr.get('airport', '')}) on {arr.get('scheduledDateLocal', '')} at {arr.get('scheduledTimeLocal', '')}\n"
        )

    summary = (
        f"*Policy Application Summary*\n\n"
        f"Product: {selected_product.get('name', '')}\n"
        f"Price: {selected_product.get('currency', '')} {selected_product.get('price', '')}\n\n"
        f"*Personal Details:*\n"
        f"Name: {personal_details.get('first_name', '')} {personal_details.get('last_name', '')}\n"
        f"Email: {personal_details.get('email', '')}\n"
        f"{id_line}\n"
        f"Mobile number: {msisdn_display} ({country_code})\n\n"
        f"*Payment & Payout Preference*\n"
        f"Method: {payment_label}\n"
        f"Payout Method: Bank Account\n"
        f"Account Number: {account_number}\n"
        f"Bank: {bank_details.get('bank_name', '')}\n"
        f"{itinerary_section}\n"
        f"Boarding Pass: {bp_status}"
    )

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": sender_wa_id,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": summary},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": BUTTON_SUMMARY_SUBMIT, "title": "Yes, Submit"}},
                    {"type": "reply", "reply": {"id": BUTTON_SUMMARY_CHANGE, "title": "No, Change details"}},
                ]
            },
        },
    }

    await send_whatsapp_payload(
        payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )

    await _update_flow_state(session, sender_wa_id, {
        **flow_state,
        "step": FLOW_STEP_POLICY_SUMMARY,
        "boarding_pass_uploaded": boarding_pass_uploaded,
    })


async def _handle_policy_summary_response(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")

    if reply_id == BUTTON_SUMMARY_SUBMIT:
        user_input = "yes"
    elif reply_id == BUTTON_SUMMARY_CHANGE:
        user_input = "no"
    elif message.type == "text" and message.text:
        user_input = message.text.body.strip().lower()
    else:
        await send_text_message(
            to=sender_wa_id,
            body="Please tap *Yes, Submit* to submit your policy or *No, Change details* to make corrections.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    if user_input in ("no", "n", "change"):
        await send_text_message(
            to=sender_wa_id,
            body="No problem! Let's go back so you can make changes.\n\nPlease enter your *departure date* (DD/MM/YYYY):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_ITIN_DEP_DATE,
        })
        return

    if user_input not in ("yes", "y", "submit", "sure", "ok", "okay"):
        await send_text_message(
            to=sender_wa_id,
            body="Please tap *Yes, Submit* to submit your policy or *No, Change details* to make corrections.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    await send_text_message(
        to=sender_wa_id,
        body="Submitting your policy... please wait.",
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )

    boarding_pass_uploaded = flow_state.get("boarding_pass_uploaded", False)
    boarding_pass_bytes = b""
    boarding_pass_mime = "image/jpeg"

    if boarding_pass_uploaded and policy_id:
        policy_doc = await get_policy_by_id(policy_id)
        boarding_pass_data = (policy_doc or {}).get("boarding_pass", {})
        boarding_pass_bytes = boarding_pass_data.get("file_data", b"")
        boarding_pass_mime = boarding_pass_data.get("mime_type", "image/jpeg")
        if isinstance(boarding_pass_bytes, bytes):
            pass
        else:
            boarding_pass_bytes = bytes(boarding_pass_bytes) if boarding_pass_bytes else b""

    success, err_msg, resp_data = await _submit_policy_to_api(
        flow_state, policy_id or "", boarding_pass_bytes, boarding_pass_mime,
    )

    policy_reference = ""
    if success and policy_id:
        policy_reference = (
            resp_data.get("data", {}).get("policyId", "")
            or resp_data.get("data", {}).get("id", "")
            or resp_data.get("policyId", "")
            or resp_data.get("id", "")
            or ""
        )
        await set_policy_submitted(policy_id, resp_data)
        logger.info(f"Policy {policy_id} submitted successfully. Reference: {policy_reference}")
    elif not success:
        logger.error(f"Policy {policy_id} submission failed: {err_msg}")

    airport_info = flow_state.get("airport_info", {})
    await _show_final_summary(
        sender_wa_id, phone_number_id, in_reply_to,
        session, flow_state, policy_id, airport_info,
        submission_success=success,
        policy_reference=str(policy_reference) if policy_reference else "",
        submission_error=err_msg,
        boarding_pass_uploaded=boarding_pass_uploaded,
    )


async def _fetch_airports(search_term: str) -> Optional[list]:
    async def _single_attempt():
        async with httpx.AsyncClient(timeout=30.0, verify=True) as client:
            logger.info(f"Searching airports for '{search_term}' from {AIRPORTS_API_URL}")
            response = await client.get(
                AIRPORTS_API_URL,
                params={"search": search_term},
            )
            logger.info(f"Airports API response: HTTP {response.status_code}, size={len(response.content)} bytes")
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    logger.info(f"Found {len(data)} airports for '{search_term}'")
                    return data
            logger.warning(f"Airports API error: HTTP {response.status_code} for '{search_term}', body={response.text[:500]}")
            return None

    return await _api_call_with_retry(f"Airports({search_term})", _single_attempt)


async def _send_dep_airports_page(to: str, phone_number_id: str, in_reply_to: str, airports: list, page: int, search_term: str) -> None:
    total = len(airports)
    total_pages = (total + AIRPORTS_PER_PAGE - 1) // AIRPORTS_PER_PAGE
    start = page * AIRPORTS_PER_PAGE
    end = min(start + AIRPORTS_PER_PAGE, total)
    page_airports = airports[start:end]

    rows = []
    for idx, airport in enumerate(page_airports):
        iata = airport.get("iata_code", "")
        name = airport.get("name", "Unknown")
        country = airport.get("country_name", "") or airport.get("country", "")
        rows.append({
            "id": f"{AIRPORT_ID_PREFIX}{start + idx}",
            "title": str(name)[:24],
            "description": f"{iata} - {country}"[:72],
        })

    if total_pages > 1:
        if page < total_pages - 1:
            rows.append({
                "id": AIRPORT_NAV_NEXT,
                "title": "Next \u25b6",
                "description": f"View more airports (page {page + 2} of {total_pages})",
            })
        if page > 0:
            rows.append({
                "id": AIRPORT_NAV_PREV,
                "title": "\u25c0 Previous",
                "description": f"Go back (page {page} of {total_pages})",
            })

    page_info = f" (Page {page + 1}/{total_pages})" if total_pages > 1 else ""
    body_text = (
        f"Found {total} airports for *\"{search_term}\"*{page_info}.\n"
        f"Showing {start + 1}-{end} of {total}. Please select one:"
    )

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": f"Select Airport{page_info}"},
            "body": {"text": body_text},
            "action": {
                "button": "View Airports",
                "sections": [{"title": "Airports", "rows": rows}]
            }
        }
    }

    await send_whatsapp_payload(
        payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _send_arr_airports_page(to: str, phone_number_id: str, in_reply_to: str, airports: list, page: int, search_term: str) -> None:
    total = len(airports)
    total_pages = (total + AIRPORTS_PER_PAGE - 1) // AIRPORTS_PER_PAGE
    start = page * AIRPORTS_PER_PAGE
    end = min(start + AIRPORTS_PER_PAGE, total)
    page_airports = airports[start:end]

    rows = []
    for idx, airport in enumerate(page_airports):
        iata = airport.get("iata_code", "")
        name = airport.get("name", "Unknown")
        country = airport.get("country_name", "") or airport.get("country", "")
        rows.append({
            "id": f"{ARR_AIRPORT_ID_PREFIX}{start + idx}",
            "title": str(name)[:24],
            "description": f"{iata} - {country}"[:72],
        })

    if total_pages > 1:
        if page < total_pages - 1:
            rows.append({
                "id": ARR_AIRPORT_NAV_NEXT,
                "title": "Next \u25b6",
                "description": f"View more airports (page {page + 2} of {total_pages})",
            })
        if page > 0:
            rows.append({
                "id": ARR_AIRPORT_NAV_PREV,
                "title": "\u25c0 Previous",
                "description": f"Go back (page {page} of {total_pages})",
            })

    page_info = f" (Page {page + 1}/{total_pages})" if total_pages > 1 else ""
    body_text = (
        f"Found {total} airports for *\"{search_term}\"*{page_info}.\n"
        f"Showing {start + 1}-{end} of {total}. Please select one:"
    )

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": f"Select Arrival Airport{page_info}"},
            "body": {"text": body_text},
            "action": {
                "button": "View Airports",
                "sections": [{"title": "Airports", "rows": rows}]
            }
        }
    }

    await send_whatsapp_payload(
        payload,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )


async def _handle_airport_input(message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")

    text_input = _get_text_input(message)
    if not text_input:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter the first 3 characters of the *departure airport name* or *airport code* (e.g. LOS, Mur, KAN, Enu, PHC):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    search_term = text_input.strip()

    airports = await _fetch_airports(search_term)

    if airports is None:
        await _send_retry_options(
            to=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            error_message=f"We couldn't search for airports for *\"{search_term}\"* at the moment. The airport service may be temporarily unavailable.",
            retry_label="Retry Search",
        )
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "retry_step": FLOW_STEP_AIRPORT_INPUT,
            "retry_data": {"search_term": search_term},
        })
        return

    if len(airports) == 0:
        search_lower = text_input.strip().lower()
        airports = await _fetch_airports(search_lower)
        if not airports:
            search_upper = text_input.strip().upper()
            airports = await _fetch_airports(search_upper)

    if not airports:
        await send_text_message(
            to=sender_wa_id,
            body=f"No airports found for *\"{text_input}\"*.\n\nPlease try again with a different airport name or code (e.g. LOS, Mur, KAN, Enu, PHC):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    if len(airports) == 1:
        airport = airports[0]
        airport_info = {
            "name": airport.get("name", ""),
            "iata_code": airport.get("iata_code", ""),
            "country": airport.get("country_name", "") or airport.get("country", ""),
            "country_iso2": airport.get("country_iso2", ""),
        }

        if policy_id:
            await set_airport_info(policy_id, airport_info)
            logger.info(f"Airport '{airport_info['name']}' ({airport_info['iata_code']}) saved to policy {policy_id}")

        await _start_itinerary_flow(
            sender_wa_id, phone_number_id, in_reply_to,
            session, flow_state, airport_info,
        )
    else:
        await _send_dep_airports_page(sender_wa_id, phone_number_id, in_reply_to, airports, 0, text_input)
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_AIRPORT_SELECT,
            "available_airports": airports,
            "airport_page": 0,
            "airport_search_term": text_input,
        })


async def _handle_airport_selection(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")
    airports = flow_state.get("available_airports", [])
    current_page = flow_state.get("airport_page", 0)
    search_term = flow_state.get("airport_search_term", "")

    if reply_id == AIRPORT_NAV_NEXT:
        total_pages = (len(airports) + AIRPORTS_PER_PAGE - 1) // AIRPORTS_PER_PAGE
        new_page = min(current_page + 1, total_pages - 1)
        await _send_dep_airports_page(sender_wa_id, phone_number_id, in_reply_to, airports, new_page, search_term)
        await _update_flow_state(session, sender_wa_id, {**flow_state, "airport_page": new_page})
        return

    if reply_id == AIRPORT_NAV_PREV:
        new_page = max(current_page - 1, 0)
        await _send_dep_airports_page(sender_wa_id, phone_number_id, in_reply_to, airports, new_page, search_term)
        await _update_flow_state(session, sender_wa_id, {**flow_state, "airport_page": new_page})
        return

    if reply_id and reply_id.startswith(AIRPORT_ID_PREFIX):
        idx_str = reply_id[len(AIRPORT_ID_PREFIX):]
        try:
            idx = int(idx_str)
            airport = airports[idx]
        except (ValueError, IndexError):
            await send_text_message(
                to=sender_wa_id,
                body="Sorry, we couldn't find that airport. Please try again by entering a city or state name:",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_AIRPORT_INPUT,
            })
            return

        airport_info = {
            "name": airport.get("name", ""),
            "iata_code": airport.get("iata_code", ""),
            "country": airport.get("country_name", "") or airport.get("country", ""),
            "country_iso2": airport.get("country_iso2", ""),
        }

        if policy_id:
            await set_airport_info(policy_id, airport_info)
            logger.info(f"Airport '{airport_info['name']}' ({airport_info['iata_code']}) saved to policy {policy_id}")

        await _start_itinerary_flow(
            sender_wa_id, phone_number_id, in_reply_to,
            session, flow_state, airport_info,
        )
    else:
        text_input = _get_text_input(message)
        if text_input:
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_AIRPORT_INPUT,
            })
            await _handle_airport_input(
                message, sender_wa_id, phone_number_id, in_reply_to, session,
            )
        else:
            await send_text_message(
                to=sender_wa_id,
                body="Please select an airport from the list, or type a different airport name or code to search again.",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )


ITINERARY_STEPS = [
    {
        "step": FLOW_STEP_ITIN_DEP_DATE,
        "field": "departure.scheduledDateLocal",
        "prompt": "Please enter your scheduled *departure date* (e.g. 25/12/2026, 25-12-2026):",
        "validation": "date",
    },
    {
        "step": FLOW_STEP_ITIN_DEP_TIME,
        "field": "departure.scheduledTimeLocal",
        "prompt": "Please enter your scheduled *departure time* (e.g. 14:30):",
        "validation": "time",
    },
    {
        "step": FLOW_STEP_ITIN_ARR_AIRPORT_INPUT,
        "field": "arrival.airport",
        "prompt": "Please enter the first 3 characters of the *arrival airport name* or *airport code* (e.g. LOS, Mur, KAN, Enu, PHC):",
        "validation": "airport_search",
    },
    {
        "step": FLOW_STEP_ITIN_ARR_DATE,
        "field": "arrival.scheduledDateLocal",
        "prompt": "Please enter your scheduled *arrival date* (e.g. 25/12/2026 or 25-12-2026):",
        "validation": "date",
    },
    {
        "step": FLOW_STEP_ITIN_ARR_TIME,
        "field": "arrival.scheduledTimeLocal",
        "prompt": "Please enter your scheduled *arrival time* (e.g. 16:30):",
        "validation": "time",
    },
    {
        "step": FLOW_STEP_ITIN_BOOKING_REF,
        "field": "bookingReference",
        "prompt": "Please enter your *booking reference* (e.g. ABC123):",
        "validation": "text",
    },
    {
        "step": FLOW_STEP_ITIN_FLIGHT_NO,
        "field": "flightNo",
        "prompt": "Please enter your *flight number* (e.g. BA1234):",
        "validation": "text",
    },
]


def _validate_date(text: str) -> Optional[str]:
    from datetime import datetime as _dt

    cleaned = text.strip()
    date_patterns = [
        (r"^(\d{1,2})/(\d{1,2})/(\d{4})$", "dmy"),
        (r"^(\d{1,2})-(\d{1,2})-(\d{4})$", "dmy"),
        (r"^(\d{4})-(\d{1,2})-(\d{1,2})$", "ymd"),
        (r"^(\d{4})/(\d{1,2})/(\d{1,2})$", "ymd"),
    ]
    for pattern, fmt in date_patterns:
        match = re.match(pattern, cleaned)
        if match:
            groups = match.groups()
            if fmt == "dmy":
                day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
            else:
                year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
            try:
                _dt(year, month, day)
            except ValueError:
                return None
            if year < 2024:
                return None
            return f"{day:02d}/{month:02d}/{year}"
    return None


def _validate_time(text: str) -> Optional[str]:
    cleaned = text.strip()
    match = re.match(r"^(\d{1,2}):(\d{2})$", cleaned)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    match = re.match(r"^(\d{1,2})\.(\d{2})$", cleaned)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return None


async def _start_itinerary_flow(
    sender_wa_id, phone_number_id, in_reply_to,
    session, flow_state, airport_info,
):
    dep_airport_display = f"{airport_info.get('name', '')} ({airport_info.get('iata_code', '')})"

    itinerary = flow_state.get("itinerary", {})
    if "departure" not in itinerary:
        itinerary["departure"] = {}
    itinerary["departure"]["airport"] = airport_info.get("iata_code", "")
    itinerary["departure"]["airportName"] = airport_info.get("name", "")

    airport_iso2 = airport_info.get("country_iso2", "")
    country_code = airport_iso2 if len(airport_iso2) == 2 else flow_state.get("country_code", "NG")
    country_name = airport_info.get("country", "") or flow_state.get("country_name", "")

    policy_id = flow_state.get("policy_id")
    if policy_id and country_code:
        await set_country(policy_id, country_code, country_name)
        logger.info(f"Country updated to {country_code} ({country_name}) from departure airport for policy {policy_id}")

    products = await _fetch_products(country_code)
    if not products:
        country_label = country_name or country_code
        await send_text_message(
            to=sender_wa_id,
            body=f"Departure airport selected: *{dep_airport_display}*\nCountry: *{country_label}*",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _send_retry_options(
            to=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            error_message=f"We couldn't find any available products for *{country_label}* at the moment. This could be a temporary issue or there may be no products for this country yet.",
            retry_label="Retry Products",
        )
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_PRODUCT_SELECTED,
            "airport_info": airport_info,
            "itinerary": itinerary,
            "country_code": country_code,
            "country_name": country_name,
            "retry_step": FLOW_STEP_PRODUCT_LIST,
        })
        return

    country_label = country_name or country_code
    await send_text_message(
        to=sender_wa_id,
        body=f"Departure airport selected: *{dep_airport_display}*\nCountry: *{country_label}*\n\nNow let's select a product.",
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )

    page = 0
    await _send_products_page(sender_wa_id, phone_number_id, in_reply_to, products, page, country_code)
    await _update_flow_state(session, sender_wa_id, {
        **flow_state,
        "step": FLOW_STEP_PRODUCT_SELECTED,
        "airport_info": airport_info,
        "itinerary": itinerary,
        "country_code": country_code,
        "country_name": country_name,
        "available_products": products,
        "product_page": page,
    })


def _set_itinerary_field(itinerary: dict, field_path: str, value: str) -> None:
    parts = field_path.split(".")
    if len(parts) == 1:
        itinerary[parts[0]] = value
    elif len(parts) == 2:
        if parts[0] not in itinerary:
            itinerary[parts[0]] = {}
        itinerary[parts[0]][parts[1]] = value


def _get_next_itinerary_step(current_step: str) -> Optional[dict]:
    for i, step_info in enumerate(ITINERARY_STEPS):
        if step_info["step"] == current_step:
            if i + 1 < len(ITINERARY_STEPS):
                return ITINERARY_STEPS[i + 1]
            return None
    return None


async def _handle_itinerary_text_input(message, sender_wa_id, phone_number_id, in_reply_to, session, current_step):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")
    itinerary = flow_state.get("itinerary", {})

    text_input = _get_text_input(message)
    if not text_input:
        current_info = None
        for s in ITINERARY_STEPS:
            if s["step"] == current_step:
                current_info = s
                break
        prompt = current_info["prompt"] if current_info else "Please enter the requested information as text."
        await send_text_message(
            to=sender_wa_id,
            body=prompt,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    current_info = None
    for s in ITINERARY_STEPS:
        if s["step"] == current_step:
            current_info = s
            break

    if not current_info:
        return

    validation = current_info.get("validation")
    value = text_input.strip()

    if validation == "date":
        validated = _validate_date(value)
        if not validated:
            await send_text_message(
                to=sender_wa_id,
                body=(
                    "Sorry, that doesn't look like a valid date.\n\n"
                    "Please enter the date in one of these formats:\n"
                    "\u2022 *DD/MM/YYYY* (e.g. 25/12/2026)\n"
                    "\u2022 *DD-MM-YYYY* (e.g. 25-12-2026)"
                ),
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            return
        value = validated

    elif validation == "time":
        validated = _validate_time(value)
        if not validated:
            await send_text_message(
                to=sender_wa_id,
                body=(
                    "Sorry, that doesn't look like a valid time.\n\n"
                    "Please enter the time in *HH:MM* 24-hour format (e.g. 14:30, 08:15)."
                ),
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            return
        value = validated

    elif validation == "text":
        if not value:
            field_label = current_info["field"].split(".")[-1].replace("_", " ")
            await send_text_message(
                to=sender_wa_id,
                body=f"Please enter a valid {field_label}.",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            return

    _set_itinerary_field(itinerary, current_info["field"], value)

    if flow_state.get("editing_field"):
        if policy_id:
            await set_itinerary(policy_id, itinerary)
        new_state = {**flow_state, "step": FLOW_STEP_DETAILS_CONFIRM, "itinerary": itinerary}
        new_state.pop("editing_field", None)
        await _send_details_confirmation(sender_wa_id, phone_number_id, in_reply_to, new_state)
        await _update_flow_state(session, sender_wa_id, new_state)
        return

    next_step_info = _get_next_itinerary_step(current_step)

    if next_step_info is None:
        if policy_id:
            await set_itinerary(policy_id, itinerary)
            logger.info(f"Itinerary saved to policy {policy_id}")

        first_pd_step = PERSONAL_DETAIL_STEPS[0]
        await send_text_message(
            to=sender_wa_id,
            body=f"Itinerary details saved.\n\nNow let's capture your personal details.\n\n{first_pd_step['prompt']}",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        existing_pd = flow_state.get("personal_details")
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": first_pd_step["step"],
            "itinerary": itinerary,
            "personal_details": existing_pd if existing_pd else {},
        })

    elif next_step_info["step"] == FLOW_STEP_ITIN_ARR_AIRPORT_INPUT:
        await send_text_message(
            to=sender_wa_id,
            body=next_step_info["prompt"],
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_ITIN_ARR_AIRPORT_INPUT,
            "itinerary": itinerary,
        })
    else:
        await send_text_message(
            to=sender_wa_id,
            body=next_step_info["prompt"],
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": next_step_info["step"],
            "itinerary": itinerary,
        })


async def _handle_arr_airport_input(message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")
    itinerary = flow_state.get("itinerary", {})

    text_input = _get_text_input(message)
    if not text_input:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter the first 3 characters of the *arrival airport name* or *airport code* (e.g. LOS, Mur, KAN, Enu, PHC):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    cleaned_input = text_input.strip()
    search_term = cleaned_input.title()
    airports = await _fetch_airports(search_term)

    if airports is None:
        await _send_retry_options(
            to=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            error_message=f"We couldn't search for airports for *\"{cleaned_input}\"* at the moment.",
            retry_label="Retry Search",
        )
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "retry_step": FLOW_STEP_ITIN_ARR_AIRPORT_INPUT,
            "retry_data": {"search_term": search_term},
        })
        return

    if len(airports) == 0:
        search_lower = cleaned_input.strip().lower()
        airports = await _fetch_airports(search_lower)
        if not airports:
            search_upper = cleaned_input.strip().upper()
            airports = await _fetch_airports(search_upper)

    if not airports:
        await send_text_message(
            to=sender_wa_id,
            body=f"No airports found for *\"{cleaned_input}\"*.\n\nPlease try a different city or state name:",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    if len(airports) == 1:
        airport = airports[0]
        arr_airport_info = {
            "name": airport.get("name", ""),
            "iata_code": airport.get("iata_code", ""),
            "country": airport.get("country", ""),
        }
        if "arrival" not in itinerary:
            itinerary["arrival"] = {}
        itinerary["arrival"]["airport"] = arr_airport_info.get("iata_code", "")
        itinerary["arrival"]["airportName"] = arr_airport_info.get("name", "")

        if flow_state.get("editing_field"):
            if policy_id:
                await set_itinerary(policy_id, itinerary)
            await send_text_message(
                to=sender_wa_id,
                body=f"Arrival airport updated to: *{arr_airport_info['name']}* ({arr_airport_info['iata_code']})",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            new_state = {**flow_state, "step": FLOW_STEP_DETAILS_CONFIRM, "itinerary": itinerary}
            new_state.pop("editing_field", None)
            await _send_details_confirmation(sender_wa_id, phone_number_id, in_reply_to, new_state)
            await _update_flow_state(session, sender_wa_id, new_state)
        else:
            await send_text_message(
                to=sender_wa_id,
                body=(
                    f"Arrival airport selected: *{arr_airport_info['name']}* ({arr_airport_info['iata_code']})\n\n"
                    f"Please enter your scheduled *arrival date* (e.g. 25/12/2026 or 25-12-2026):"
                ),
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_ITIN_ARR_DATE,
                "itinerary": itinerary,
            })
    else:
        await _send_arr_airports_page(sender_wa_id, phone_number_id, in_reply_to, airports, 0, cleaned_input)
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_ITIN_ARR_AIRPORT_SELECT,
            "itinerary": itinerary,
            "available_arr_airports": airports,
            "arr_airport_page": 0,
            "arr_airport_search_term": cleaned_input,
        })


async def _handle_arr_airport_selection(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    itinerary = flow_state.get("itinerary", {})
    airports = flow_state.get("available_arr_airports", [])
    current_page = flow_state.get("arr_airport_page", 0)
    search_term = flow_state.get("arr_airport_search_term", "")

    if reply_id == ARR_AIRPORT_NAV_NEXT:
        total_pages = (len(airports) + AIRPORTS_PER_PAGE - 1) // AIRPORTS_PER_PAGE
        new_page = min(current_page + 1, total_pages - 1)
        await _send_arr_airports_page(sender_wa_id, phone_number_id, in_reply_to, airports, new_page, search_term)
        await _update_flow_state(session, sender_wa_id, {**flow_state, "arr_airport_page": new_page})
        return

    if reply_id == ARR_AIRPORT_NAV_PREV:
        new_page = max(current_page - 1, 0)
        await _send_arr_airports_page(sender_wa_id, phone_number_id, in_reply_to, airports, new_page, search_term)
        await _update_flow_state(session, sender_wa_id, {**flow_state, "arr_airport_page": new_page})
        return

    if reply_id and reply_id.startswith(ARR_AIRPORT_ID_PREFIX):
        idx_str = reply_id[len(ARR_AIRPORT_ID_PREFIX):]
        try:
            idx = int(idx_str)
            airport = airports[idx]
        except (ValueError, IndexError):
            await send_text_message(
                to=sender_wa_id,
                body="Sorry, we couldn't find that airport. Please try again by entering a city or state name:",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_ITIN_ARR_AIRPORT_INPUT,
            })
            return

        arr_airport_info = {
            "name": airport.get("name", ""),
            "iata_code": airport.get("iata_code", ""),
            "country": airport.get("country", ""),
        }
        if "arrival" not in itinerary:
            itinerary["arrival"] = {}
        itinerary["arrival"]["airport"] = arr_airport_info.get("iata_code", "")
        itinerary["arrival"]["airportName"] = arr_airport_info.get("name", "")

        policy_id = flow_state.get("policy_id")
        if flow_state.get("editing_field"):
            if policy_id:
                await set_itinerary(policy_id, itinerary)
            await send_text_message(
                to=sender_wa_id,
                body=f"Arrival airport updated to: *{arr_airport_info['name']}* ({arr_airport_info['iata_code']})",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            new_state = {**flow_state, "step": FLOW_STEP_DETAILS_CONFIRM, "itinerary": itinerary}
            new_state.pop("editing_field", None)
            await _send_details_confirmation(sender_wa_id, phone_number_id, in_reply_to, new_state)
            await _update_flow_state(session, sender_wa_id, new_state)
        else:
            await send_text_message(
                to=sender_wa_id,
                body=(
                    f"Arrival airport selected: *{arr_airport_info['name']}* ({arr_airport_info['iata_code']})\n\n"
                    f"Please enter your scheduled *arrival date* (e.g. 25/12/2026 or 25-12-2026):"
                ),
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_ITIN_ARR_DATE,
                "itinerary": itinerary,
            })
    else:
        text_input = _get_text_input(message)
        if text_input:
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_ITIN_ARR_AIRPORT_INPUT,
            })
            await _handle_arr_airport_input(
                message, sender_wa_id, phone_number_id, in_reply_to, session,
            )
        else:
            await send_text_message(
                to=sender_wa_id,
                body="Please select an airport from the list, or type a different city/state name to search again.",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )


async def _submit_policy_to_api(
    flow_state: dict,
    policy_id: str,
    boarding_pass_bytes: bytes,
    boarding_pass_mime: str,
) -> tuple:
    import json as _json

    personal_details = flow_state.get("personal_details", {})
    selected_product = flow_state.get("selected_product", {})
    payment_method = flow_state.get("payment_method", "")
    payout_method = flow_state.get("payout_method", "BANK_ACCOUNT")
    country_code = flow_state.get("country_code", "")
    bank_details = flow_state.get("bank_details", {})
    msisdn_info = flow_state.get("msisdn_info", {})
    id_type = flow_state.get("id_type", "")
    id_number = flow_state.get("id_number", "")
    account_number = flow_state.get("account_number", "")
    itinerary = flow_state.get("itinerary", {})

    bvn = id_number if id_type == "BVN" else ""
    nin = id_number if id_type == "NIN" else ""

    msisdn = msisdn_info.get("phone_number", "")
    if msisdn and not msisdn.startswith("+"):
        msisdn = f"+{msisdn}"

    account_name = f"{personal_details.get('first_name', '')} {personal_details.get('last_name', '')}".strip()

    payout_config = {"bank_code": bank_details.get("bank_code", "")}
    if bank_details.get("branch_code"):
        payout_config["branch_code"] = bank_details["branch_code"]

    def _convert_date(d: str) -> str:
        return d.replace("/", "-") if d else d

    dep = itinerary.get("departure", {})
    arr = itinerary.get("arrival", {})

    legs = [{
        "flightNo": itinerary.get("flightNo", ""),
        "carrier": itinerary.get("carrier", "") if itinerary.get("carrier") else "",
        "departure": {
            "airport": dep.get("airport", ""),
            "scheduledDateLocal": _convert_date(dep.get("scheduledDateLocal", "")),
            "scheduledTimeLocal": dep.get("scheduledTimeLocal", ""),
        },
        "arrival": {
            "airport": arr.get("airport", ""),
            "scheduledDateLocal": _convert_date(arr.get("scheduledDateLocal", "")),
            "scheduledTimeLocal": arr.get("scheduledTimeLocal", ""),
        },
    }]

    policy_payload = {
        "productId": selected_product.get("product_id", ""),
        "channel": "WHATSAPP",
        "preferredPaymentMethod": PAYMENT_METHOD_SUBMISSION_MAP.get(payment_method, payment_method),
        "userRequestDto": {
            "msisdn": msisdn,
            "countryCode": country_code,
            "firstName": personal_details.get("first_name", ""),
            "lastName": personal_details.get("last_name", ""),
            "email": personal_details.get("email", ""),
            "bvn": bvn,
            "nin": nin,
            "marketingConsent": True,
            "policyUpdatesConsent": True,
            "payoutAlertsConsent": True,
            "kycConsent": True,
            "payoutMethod": {
                "type": "BANK_ACCOUNT",
                "accountNumber": account_number,
                "accountName": account_name,
                "config": payout_config,
            },
        },
        "itineraryRequest": {
            "bookingReference": itinerary.get("bookingReference", ""),
            "source": "OTA",
            "legs": legs,
        },
    }

    SUBMIT_POLICY_URL = "https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies"

    files_parts = [
        ("policy", (None, _json.dumps(policy_payload), "application/json")),
    ]

    if boarding_pass_bytes:
        mime_to_ext = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "application/pdf": "pdf",
        }
        ext = mime_to_ext.get(boarding_pass_mime, "jpg")
        filename = f"boarding_pass_{policy_id}.{ext}"
        files_parts.append(("boardingPassFile", (filename, boarding_pass_bytes, boarding_pass_mime)))

    bp_info = "with boarding pass" if boarding_pass_bytes else "without boarding pass"
    last_err_msg = "An unexpected error occurred. Please try again."

    for attempt in range(1, API_RETRY_MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                logger.info(f"Submitting policy {policy_id} to API ({bp_info}), attempt {attempt}/{API_RETRY_MAX_ATTEMPTS}")
                response = await client.post(
                    SUBMIT_POLICY_URL,
                    files=files_parts,
                )
                logger.info(f"Policy submission response: HTTP {response.status_code}, policy_id={policy_id}")

                if response.status_code in (200, 201):
                    try:
                        resp_data = response.json()
                    except Exception:
                        resp_data = {}
                    return True, "", resp_data

                try:
                    err_data = response.json()
                    last_err_msg = (
                        err_data.get("message")
                        or err_data.get("error")
                        or err_data.get("detail")
                        or response.text[:300]
                    )
                except Exception:
                    last_err_msg = response.text[:300]

                if response.status_code in (502, 503, 504) and attempt < API_RETRY_MAX_ATTEMPTS:
                    wait = API_RETRY_BACKOFF_SECONDS[min(attempt - 1, len(API_RETRY_BACKOFF_SECONDS) - 1)]
                    logger.warning(f"Policy submission got gateway error HTTP {response.status_code} (attempt {attempt}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    continue

                logger.error(f"Policy submission failed: HTTP {response.status_code}, body={response.text[:500]}")
                return False, str(last_err_msg), {}

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_err_msg = "The request timed out. Please try again."
            if attempt < API_RETRY_MAX_ATTEMPTS:
                wait = API_RETRY_BACKOFF_SECONDS[min(attempt - 1, len(API_RETRY_BACKOFF_SECONDS) - 1)]
                logger.warning(f"Policy submission {type(e).__name__} (attempt {attempt}), retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            logger.error(f"Policy submission failed after {API_RETRY_MAX_ATTEMPTS} attempts: {type(e).__name__}: {e}")

    return False, str(last_err_msg), {}


async def _handle_boarding_pass_upload(message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")
    itinerary = flow_state.get("itinerary", {})

    media_id = None
    mime_type = None
    sha256 = None
    caption = None

    if message.type == "image" and message.image:
        media_id = message.image.id
        mime_type = message.image.mime_type
        sha256 = message.image.sha256
        caption = message.image.caption
    elif message.type == "document" and message.document:
        media_id = message.document.id
        mime_type = message.document.mime_type
        sha256 = message.document.sha256
        caption = getattr(message.document, "caption", None)
    else:
        await send_text_message(
            to=sender_wa_id,
            body=(
                "Please send your boarding pass as an *image* (JPG, PNG, WebP) or *PDF*.\n\n"
                "You can take a photo of your physical boarding pass or send a screenshot of your e-boarding pass."
            ),
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    if mime_type and mime_type not in SUPPORTED_BOARDING_PASS_TYPES:
        await send_text_message(
            to=sender_wa_id,
            body=(
                f"File type *{mime_type}* is not supported.\n\n"
                "Please upload your boarding pass as JPG, PNG, WebP, or PDF."
            ),
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    await send_text_message(
        to=sender_wa_id,
        body="Uploading your boarding pass... please wait.",
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )

    media_data = await download_whatsapp_media(media_id)

    if not media_data:
        await send_text_message(
            to=sender_wa_id,
            body=(
                "We couldn't download your boarding pass at the moment.\n\n"
                "Please try sending it again:"
            ),
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    if policy_id:
        await set_boarding_pass(policy_id, {
            "media_id": media_id,
            "mime_type": mime_type,
            "sha256": sha256,
            "caption": caption,
            "file_size": media_data.get("file_size"),
            "bytes": media_data.get("bytes"),
        })
        logger.info(
            f"Boarding pass saved to policy {policy_id}: "
            f"type={mime_type}, size={media_data.get('file_size')} bytes"
        )

    await send_text_message(
        to=sender_wa_id,
        body="Boarding pass received ✓",
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )

    await _send_policy_summary_confirmation(
        sender_wa_id, phone_number_id, in_reply_to,
        session, flow_state, boarding_pass_uploaded=True,
    )


async def _show_final_summary(
    sender_wa_id, phone_number_id, in_reply_to,
    session, flow_state, policy_id, airport_info,
    submission_success: bool = False,
    policy_reference: str = "",
    submission_error: str = "",
    boarding_pass_uploaded: bool = True,
):
    personal_details = flow_state.get("personal_details", {})
    selected_product = flow_state.get("selected_product", {})
    payment_method = flow_state.get("payment_method", "")
    country_code = flow_state.get("country_code", "")
    bank_details = flow_state.get("bank_details", {})
    msisdn_info = flow_state.get("msisdn_info", {})
    id_type = flow_state.get("id_type", "")
    id_number = flow_state.get("id_number", "")
    account_number = flow_state.get("account_number", "")
    payment_label = PAYMENT_METHOD_LABELS.get(payment_method, payment_method)

    id_line = f"{id_type}: {id_number}" if id_type and id_number else ""
    itinerary = flow_state.get("itinerary", {})
    dep = itinerary.get("departure", {})
    arr = itinerary.get("arrival", {})

    msisdn_display = msisdn_info.get("phone_number", "")
    if msisdn_display and not msisdn_display.startswith("+"):
        msisdn_display = f"+{msisdn_display}"

    itinerary_section = ""
    if itinerary:
        itinerary_section = (
            f"\n*Itinerary:*\n"
            f"Booking Ref: {itinerary.get('bookingReference', '')}\n"
            f"Flight: {itinerary.get('flightNo', '')}\n"
            f"Departure: {dep.get('airportName', '')} ({dep.get('airport', '')}) on {dep.get('scheduledDateLocal', '')} at {dep.get('scheduledTimeLocal', '')}\n"
            f"Arrival: {arr.get('airportName', '')} ({arr.get('airport', '')}) on {arr.get('scheduledDateLocal', '')} at {arr.get('scheduledTimeLocal', '')}\n"
        )

    bp_status = "Uploaded ✓" if boarding_pass_uploaded else "Not uploaded"

    if submission_success:
        ref_line = f"\nPolicy Code: {policy_reference}" if policy_reference else ""
        status_block = (
            f"Boarding Pass: {bp_status}\n\n"
            f"Status: Policy Submitted Successfully ✓{ref_line}\n\n"
            f"You will receive further updates via email or your WhatsApp number.\n\n"
            f"Type 'policy' anytime to start a new policy."
        )
    else:
        error_detail = f"\nReason: {submission_error}" if submission_error else ""
        status_block = (
            f"Boarding Pass: {bp_status}\n\n"
            f"*Status:* Submission Failed{error_detail}\n\n"
            f"All your details have been saved. Please tap *Retry* to try submitting again, "
            f"or type '#back' repeatedly to correct any information.\n\n"
            f"Type 'policy' anytime to start a new policy."
        )

    summary = (
        f"*Policy Application Summary*\n\n"
        f"Product: {selected_product.get('name', '')}\n"
        f"Price: {selected_product.get('currency', '')} {selected_product.get('price', '')}\n\n"
        f"*Personal Details:*\n"
        f"Name: {personal_details.get('first_name', '')} {personal_details.get('last_name', '')}\n"
        f"Email: {personal_details.get('email', '')}\n"
        f"{id_line}\n"
        f"Mobile number: {msisdn_display} ({country_code})\n\n"
        f"*Payment & Payout Preference*\n"
        f"Method: {payment_label}\n"
        f"Payout Method: Bank Account\n"
        f"Account Number: {account_number}\n"
        f"Bank: {bank_details.get('bank_name', '')}\n"
        f"{itinerary_section}\n"
        f"{status_block}"
    )

    await send_text_message(
        to=sender_wa_id,
        body=summary,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )

    if not submission_success:
        await send_whatsapp_payload(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": sender_wa_id,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": "Would you like to retry the submission?"},
                    "action": {
                        "buttons": [
                            {"type": "reply", "reply": {"id": BUTTON_RETRY_SUBMISSION, "title": "Retry Submission"}},
                            {"type": "reply", "reply": {"id": BUTTON_CREATE_NEW, "title": "Start New Policy"}},
                        ]
                    },
                },
            },
            phone_number_id=phone_number_id,
            source="policy_flow",
        )

    final_step = "submitted" if submission_success else "submission_failed"
    await _update_flow_state(session, sender_wa_id, {
        "active": False,
        "step": final_step,
        "action": "create_new",
        "policy_id": policy_id,
        "selected_product": selected_product,
        "personal_details": personal_details,
        "id_type": id_type,
        "id_number": id_number,
        "payment_method": payment_method,
        "payout_method": flow_state.get("payout_method", "BANK_TRANSFER"),
        "account_number": account_number,
        "bank_details": bank_details,
        "msisdn_info": msisdn_info,
        "channel_info": flow_state.get("channel_info", {}),
        "airport_info": airport_info,
        "itinerary": itinerary,
        "country_code": country_code,
        "country_name": flow_state.get("country_name", ""),
        "submission_success": submission_success,
        "policy_reference": policy_reference,
    })


async def _update_flow_state(session: dict, user_id: str, flow_state: dict) -> None:
    if "temp_data" not in session:
        session["temp_data"] = {}
    session["temp_data"][FLOW_STATE_KEY] = flow_state
    if "user_id" not in session:
        session["user_id"] = user_id
    await save_session(session)


async def _clear_flow_state(session: dict, user_id: str) -> None:
    if "temp_data" not in session:
        session["temp_data"] = {}
    session["temp_data"][FLOW_STATE_KEY] = {"active": False}
    if "user_id" not in session:
        session["user_id"] = user_id
    await save_session(session)


# ============================================================
# BOARDING PASS UPLOAD FLOW (retrospective upload for existing policies)
# ============================================================

BP_UPLOAD_FLOW_KEY = "bp_upload_flow"
BP_STEP_POLICY_LIST = "bp_policy_list"
BP_STEP_POLICY_SELECTED = "bp_policy_selected"
BP_STEP_UPLOAD = "bp_upload"

BUTTON_BP_CANCEL = "bp_cancel"
BUTTON_BP_YES_UPDATE = "bp_yes_update"
BUTTON_BP_POLICY_PREFIX = "bp_policy_"
BUTTON_BP_NAV_NEXT = "bp_nav_next"
BUTTON_BP_NAV_PREV = "bp_nav_prev"

POLICIES_BY_MSISDN_API_URL = "https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/by-msisdn"
BOARDING_PASS_UPLOAD_API_URL = "https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/upload-boarding-pass"

BP_POLICIES_PER_PAGE = 6

ACTIVE_POLICY_STATUSES = {"ACTIVE", "ISSUED", "LINKED", "NEW", "PROCESSING"}

STATUS_EMOJI = {
    "ACTIVE": "✅", "ISSUED": "✅", "LINKED": "🔗",
    "NEW": "🆕", "PROCESSING": "⏳", "EXPIRED": "⏰", "CANCELLED": "❌",
}


def is_in_bp_upload_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    return session.get("temp_data", {}).get(BP_UPLOAD_FLOW_KEY, {}).get("active", False)


def _get_bp_flow_state(session: dict) -> dict:
    return session.get("temp_data", {}).get(BP_UPLOAD_FLOW_KEY, {})


async def _update_bp_flow_state(session: dict, user_id: str, bp_state: dict) -> None:
    if "temp_data" not in session:
        session["temp_data"] = {}
    session["temp_data"][BP_UPLOAD_FLOW_KEY] = bp_state
    if "user_id" not in session:
        session["user_id"] = user_id
    await save_session(session)


async def _clear_bp_flow_state(session: dict, user_id: str) -> None:
    if "temp_data" not in session:
        session["temp_data"] = {}
    session["temp_data"][BP_UPLOAD_FLOW_KEY] = {"active": False}
    if "user_id" not in session:
        session["user_id"] = user_id
    await save_session(session)


async def _fetch_policies_by_msisdn(msisdn: str) -> list:
    url = f"{POLICIES_BY_MSISDN_API_URL}/{msisdn}"

    async def _single_attempt():
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                return response.json().get("data", [])
            logger.warning(f"Policies by MSISDN API error: {response.status_code}")
            return None

    result = await _api_call_with_retry(f"PoliciesByMSISDN({msisdn})", _single_attempt)
    return result if result is not None else []


def _format_policy_date(date_str: Optional[str]) -> str:
    if not date_str:
        return ""
    try:
        parts = date_str[:10].split("-")
        if len(parts) == 3:
            return f"{parts[2]}/{parts[1]}/{parts[0]}"
    except Exception:
        pass
    return date_str[:10]


def _format_policy_summary(policy: dict) -> str:
    lines = ["*Policy Details*\n"]

    policy_code = policy.get("policyCode", "")
    if policy_code:
        lines.append(f"*Policy Code:* {policy_code}")

    status = policy.get("status", "")
    if status:
        emoji = STATUS_EMOJI.get(status, "")
        lines.append(f"*Status:* {emoji} {status}")

    product_name = policy.get("productName", "")
    if product_name:
        lines.append(f"*Product:* {product_name}")

    insurer = policy.get("insuranceProvider", "")
    if insurer:
        lines.append(f"*Insurer:* {insurer}")

    issue_date = _format_policy_date(policy.get("issueTimestamp", ""))
    if issue_date:
        lines.append(f"*Issued:* {issue_date}")

    itinerary = policy.get("itinerary") or {}
    legs = itinerary.get("legs", [])
    booking_ref = itinerary.get("bookingReference", "")
    if booking_ref:
        lines.append(f"\n*Booking Reference:* {booking_ref}")
    if legs:
        leg = legs[0]
        dep = leg.get("departureAirport", "")
        arr = leg.get("arrivalAirport", "")
        flight_no = leg.get("flightNo", "")
        carrier = leg.get("carrier", "")
        if dep and arr:
            lines.append(f"*Route:* {dep} → {arr}")
        if flight_no or carrier:
            lines.append(f"*Flight:* {' '.join(filter(None, [carrier, flight_no]))}")

    payment_amount = policy.get("paymentAmount")
    payment_currency = policy.get("paymentCurrency", "")
    if payment_amount is not None:
        lines.append(f"\n*Premium Paid:* {payment_currency} {payment_amount:,.0f}")

    coverage = policy.get("coverageLimit")
    if coverage is not None:
        lines.append(f"*Coverage:* {payment_currency} {coverage:,.0f}")

    trigger_events = policy.get("triggerEvents", [])
    if trigger_events:
        lines.append(f"*Covers:* {', '.join(e.title() for e in trigger_events)}")

    bp_keys = policy.get("boardingPassKeys", [])
    bp_uploaded_at = policy.get("boardingPassUploadedAt")
    if bp_keys or bp_uploaded_at:
        bp_date = _format_policy_date(bp_uploaded_at) if bp_uploaded_at else ""
        bp_line = "✅ Uploaded"
        if bp_date:
            bp_line += f" on {bp_date}"
        lines.append(f"\n*Boarding Pass:* {bp_line}")
    else:
        lines.append("\n*Boarding Pass:* ❌ Not yet uploaded")

    return "\n".join(lines)


async def _send_bp_policy_list_page(
    to: str,
    phone_number_id: str,
    in_reply_to: str,
    policies: list,
    page: int,
) -> None:
    total = len(policies)
    per_page = BP_POLICIES_PER_PAGE
    start = page * per_page
    end = min(start + per_page, total)
    page_policies = policies[start:end]
    total_pages = max(1, (total + per_page - 1) // per_page)

    rows = []
    for p in page_policies:
        policy_code = p.get("policyCode", "")
        status = p.get("status", "")
        bp_icon = "✅" if p.get("boardingPassKeys") else "❌"
        title = policy_code[:24] if policy_code else "Unknown"

        itinerary = p.get("itinerary") or {}
        legs = itinerary.get("legs", [])
        route = ""
        if legs:
            dep = legs[0].get("departureAirport", "")
            arr = legs[0].get("arrivalAirport", "")
            if dep and arr:
                route = f"{dep}→{arr}"

        desc_parts = [x for x in [route, status, f"BP:{bp_icon}"] if x]
        description = " | ".join(desc_parts)[:72]

        rows.append({
            "id": f"{BUTTON_BP_POLICY_PREFIX}{policy_code}",
            "title": title,
            "description": description,
        })

    if total > per_page:
        if page > 0:
            rows.append({"id": BUTTON_BP_NAV_PREV, "title": "⬆ Previous page", "description": f"Go to page {page}"})
        if end < total:
            rows.append({"id": BUTTON_BP_NAV_NEXT, "title": "⬇ Next page", "description": f"Go to page {page + 2}"})

    rows.append({"id": BUTTON_BP_CANCEL, "title": "✖ Cancel", "description": "Return to main menu"})

    page_info = f" (Page {page + 1}/{total_pages})" if total_pages > 1 else ""
    body_text = f"Found *{total}* policy/policies linked to your number.{page_info}\n\nSelect a policy to upload or update its boarding pass:"

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "Your Policies"},
            "body": {"text": body_text},
            "footer": {"text": "Tap a policy to continue"},
            "action": {
                "button": "Select Policy",
                "sections": [{"title": "Policies", "rows": rows}],
            },
        },
    }
    await send_whatsapp_payload(payload, phone_number_id=phone_number_id, source="bp_upload_flow")


async def _upload_boarding_pass_to_api(
    policy_code: str,
    file_bytes: bytes,
    mime_type: str,
    filename: str,
) -> tuple[bool, str]:
    import mimetypes
    ext = mimetypes.guess_extension(mime_type) or ".jpg"
    fname = filename or f"boarding_pass{ext}"
    last_err_msg = "Upload failed. Please try again."

    for attempt in range(1, API_RETRY_MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                logger.info(f"Uploading boarding pass for policy {policy_code}, attempt {attempt}/{API_RETRY_MAX_ATTEMPTS}")
                response = await client.post(
                    BOARDING_PASS_UPLOAD_API_URL,
                    files={"file": (fname, file_bytes, mime_type)},
                    data={"policyCode": policy_code},
                )
                resp_data = response.json() if response.content else {}
                if response.status_code in (200, 201) and resp_data.get("status") == "success":
                    return True, ""

                last_err_msg = resp_data.get("message", f"HTTP {response.status_code}")

                if response.status_code in (502, 503, 504) and attempt < API_RETRY_MAX_ATTEMPTS:
                    wait = API_RETRY_BACKOFF_SECONDS[min(attempt - 1, len(API_RETRY_BACKOFF_SECONDS) - 1)]
                    logger.warning(f"Boarding pass upload got gateway error HTTP {response.status_code} (attempt {attempt}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    continue

                return False, last_err_msg

        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_err_msg = "Upload request timed out. Please try again."
            if attempt < API_RETRY_MAX_ATTEMPTS:
                wait = API_RETRY_BACKOFF_SECONDS[min(attempt - 1, len(API_RETRY_BACKOFF_SECONDS) - 1)]
                logger.warning(f"Boarding pass upload {type(e).__name__} (attempt {attempt}), retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            logger.error(f"Boarding pass upload failed after {API_RETRY_MAX_ATTEMPTS} attempts: {e}")
        except Exception as e:
            last_err_msg = "Upload failed. Please try again."
            if attempt < API_RETRY_MAX_ATTEMPTS:
                wait = API_RETRY_BACKOFF_SECONDS[min(attempt - 1, len(API_RETRY_BACKOFF_SECONDS) - 1)]
                logger.warning(f"Boarding pass upload error (attempt {attempt}): {e}, retrying in {wait}s...")
                await asyncio.sleep(wait)
                continue
            logger.error(f"Boarding pass upload failed after {API_RETRY_MAX_ATTEMPTS} attempts: {e}")

    return False, last_err_msg


async def handle_boarding_pass_upload_flow(
    message: WhatsAppMessage,
    sender_wa_id: str,
    profile_name: str,
    phone_number_id: str,
    in_reply_to: str,
) -> None:
    session = await get_session(sender_wa_id)
    if not session:
        session = build_default_session(
            user_id=sender_wa_id,
            phone_number=sender_wa_id,
            first_name=profile_name,
        )

    bp_state = _get_bp_flow_state(session)
    current_step = bp_state.get("step")
    reply_id = _get_interactive_reply_id(message)

    is_cancel = (
        reply_id == BUTTON_BP_CANCEL
        or (
            message.type == "text"
            and message.text
            and message.text.body.lower().strip() in ("#cancel", "#exit", "#menu", "#home", "cancel", "exit")
        )
    )
    if is_cancel:
        await _clear_bp_flow_state(session, sender_wa_id)
        await send_text_message(
            to=sender_wa_id,
            body="Boarding pass upload cancelled. 👋\n\nType *hi* for the main menu or *policy* to purchase a new policy.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="bp_upload_flow",
        )
        return

    # ── Step 1: Initial entry — fetch & show policy list ──────────────────
    if not current_step or current_step == "start":
        msisdn = f"+{sender_wa_id.lstrip('+')}"
        await send_text_message(
            to=sender_wa_id,
            body="🔍 Searching for policies linked to your WhatsApp number, please wait...",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="bp_upload_flow",
        )

        policies = await _fetch_policies_by_msisdn(msisdn)

        if not policies:
            await send_text_message(
                to=sender_wa_id,
                body=(
                    "No policies found linked to your WhatsApp number. 😕\n\n"
                    "Type *policy* to purchase a new travel policy."
                ),
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="bp_upload_flow",
            )
            await _clear_bp_flow_state(session, sender_wa_id)
            return

        active = sorted(
            [p for p in policies if p.get("status", "") in ACTIVE_POLICY_STATUSES],
            key=lambda p: p.get("createdAt", ""),
            reverse=True,
        )
        others = sorted(
            [p for p in policies if p.get("status", "") not in ACTIVE_POLICY_STATUSES],
            key=lambda p: p.get("createdAt", ""),
            reverse=True,
        )
        policies_sorted = active + others

        await _send_bp_policy_list_page(
            to=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            policies=policies_sorted,
            page=0,
        )
        await _update_bp_flow_state(session, sender_wa_id, {
            "active": True,
            "step": BP_STEP_POLICY_LIST,
            "policies": policies_sorted,
            "page": 0,
        })
        return

    # ── Step 2: Policy list — pagination & selection ───────────────────────
    if current_step == BP_STEP_POLICY_LIST:
        policies = bp_state.get("policies", [])
        page = bp_state.get("page", 0)

        if reply_id == BUTTON_BP_NAV_NEXT:
            page += 1
            await _send_bp_policy_list_page(sender_wa_id, phone_number_id, in_reply_to, policies, page)
            await _update_bp_flow_state(session, sender_wa_id, {**bp_state, "page": page})
            return

        if reply_id == BUTTON_BP_NAV_PREV:
            page = max(0, page - 1)
            await _send_bp_policy_list_page(sender_wa_id, phone_number_id, in_reply_to, policies, page)
            await _update_bp_flow_state(session, sender_wa_id, {**bp_state, "page": page})
            return

        if reply_id and reply_id.startswith(BUTTON_BP_POLICY_PREFIX):
            selected_code = reply_id[len(BUTTON_BP_POLICY_PREFIX):]
            selected_policy = next((p for p in policies if p.get("policyCode") == selected_code), None)

            if not selected_policy:
                await send_text_message(
                    to=sender_wa_id,
                    body="Could not find that policy. Please select again.",
                    phone_number_id=phone_number_id,
                    in_reply_to=in_reply_to,
                    source="bp_upload_flow",
                )
                await _send_bp_policy_list_page(sender_wa_id, phone_number_id, in_reply_to, policies, page)
                return

            summary = _format_policy_summary(selected_policy)
            await send_text_message(
                to=sender_wa_id,
                body=summary,
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="bp_upload_flow",
            )

            has_bp = bool(selected_policy.get("boardingPassKeys"))
            if has_bp:
                await send_whatsapp_payload(
                    {
                        "messaging_product": "whatsapp",
                        "recipient_type": "individual",
                        "to": sender_wa_id,
                        "type": "interactive",
                        "interactive": {
                            "type": "button",
                            "body": {
                                "text": (
                                    "A boarding pass is already uploaded for this policy. ✅\n\n"
                                    "Would you like to upload a new one to replace it?"
                                )
                            },
                            "action": {
                                "buttons": [
                                    {"type": "reply", "reply": {"id": BUTTON_BP_YES_UPDATE, "title": "Yes, Replace"}},
                                    {"type": "reply", "reply": {"id": BUTTON_BP_CANCEL, "title": "Cancel"}},
                                ]
                            },
                        },
                    },
                    phone_number_id=phone_number_id,
                    source="bp_upload_flow",
                )
                await _update_bp_flow_state(session, sender_wa_id, {
                    **bp_state,
                    "step": BP_STEP_POLICY_SELECTED,
                    "selected_policy_code": selected_code,
                    "has_existing_bp": True,
                })
            else:
                await send_text_message(
                    to=sender_wa_id,
                    body=(
                        "Please upload your *boarding pass* for this policy. 📎\n\n"
                        "Accepted formats: JPG, PNG, WebP, or PDF.\n"
                        "You can take a photo or send a screenshot of your e-boarding pass.\n\n"
                        "Type *cancel* at any time to go back."
                    ),
                    phone_number_id=phone_number_id,
                    in_reply_to=in_reply_to,
                    source="bp_upload_flow",
                )
                await _update_bp_flow_state(session, sender_wa_id, {
                    **bp_state,
                    "step": BP_STEP_UPLOAD,
                    "selected_policy_code": selected_code,
                    "has_existing_bp": False,
                })
            return

        await _send_bp_policy_list_page(sender_wa_id, phone_number_id, in_reply_to, policies, page)
        return

    # ── Step 3: Confirm replacement of existing boarding pass ─────────────
    if current_step == BP_STEP_POLICY_SELECTED:
        if reply_id == BUTTON_BP_YES_UPDATE:
            await send_text_message(
                to=sender_wa_id,
                body=(
                    "Please upload your *new boarding pass*. 📎\n\n"
                    "Accepted formats: JPG, PNG, WebP, or PDF.\n\n"
                    "Type *cancel* at any time to go back."
                ),
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="bp_upload_flow",
            )
            await _update_bp_flow_state(session, sender_wa_id, {**bp_state, "step": BP_STEP_UPLOAD})
        else:
            policies = bp_state.get("policies", [])
            page = bp_state.get("page", 0)
            await _send_bp_policy_list_page(sender_wa_id, phone_number_id, in_reply_to, policies, page)
            await _update_bp_flow_state(session, sender_wa_id, {**bp_state, "step": BP_STEP_POLICY_LIST})
        return

    # ── Step 4: Receive & upload boarding pass ────────────────────────────
    if current_step == BP_STEP_UPLOAD:
        policy_code = bp_state.get("selected_policy_code", "")

        if message.type not in ("image", "document"):
            await send_text_message(
                to=sender_wa_id,
                body=(
                    "Please send your boarding pass as an *image or document*. 📎\n\n"
                    "Accepted: JPG, PNG, WebP, or PDF.\n"
                    "Type *cancel* to go back."
                ),
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="bp_upload_flow",
            )
            return

        media = (
            message.image if message.type == "image" and message.image
            else message.document if message.type == "document" and message.document
            else None
        )
        if not media:
            await send_text_message(
                to=sender_wa_id,
                body="Couldn't read the file. Please try again.",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="bp_upload_flow",
            )
            return

        media_id = getattr(media, "id", None)
        mime_type = getattr(media, "mime_type", "image/jpeg") or "image/jpeg"
        filename = getattr(media, "filename", None) or "boarding_pass.jpg"

        if mime_type not in SUPPORTED_BOARDING_PASS_TYPES:
            await send_text_message(
                to=sender_wa_id,
                body=(
                    f"Unsupported file type (*{mime_type}*).\n\n"
                    "Please upload a JPG, PNG, WebP, or PDF file."
                ),
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="bp_upload_flow",
            )
            return

        if not media_id:
            await send_text_message(
                to=sender_wa_id,
                body="Couldn't process your file. Please try uploading again.",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="bp_upload_flow",
            )
            return

        await send_text_message(
            to=sender_wa_id,
            body="📤 Uploading your boarding pass... please wait.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="bp_upload_flow",
        )

        media_data = await download_whatsapp_media(media_id, mime_type)
        if not media_data or not media_data.get("bytes"):
            await send_text_message(
                to=sender_wa_id,
                body="Failed to download your file from WhatsApp. Please try uploading again.",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="bp_upload_flow",
            )
            return

        file_bytes = bytes(media_data["bytes"])
        success, error_msg = await _upload_boarding_pass_to_api(policy_code, file_bytes, mime_type, filename)

        if success:
            await send_text_message(
                to=sender_wa_id,
                body=(
                    f"✅ *Boarding pass uploaded successfully!*\n\n"
                    f"*Policy:* {policy_code}\n\n"
                    f"Your boarding pass has been linked to this policy.\n\n"
                    f"Type *hi* for the main menu or *policy* to purchase a new policy."
                ),
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="bp_upload_flow",
            )
        else:
            await send_text_message(
                to=sender_wa_id,
                body=(
                    f"❌ *Boarding pass upload failed.*\n\n"
                    f"Reason: {error_msg}\n\n"
                    f"Please try again or contact support.\n\n"
                    f"Type *cancel* to go back or upload another file."
                ),
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="bp_upload_flow",
            )

        await _clear_bp_flow_state(session, sender_wa_id)
        return
