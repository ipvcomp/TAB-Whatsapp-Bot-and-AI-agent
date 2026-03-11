import logging
import re
from typing import Optional

import httpx

from app.core.config import get_settings
from app.models.webhook import WhatsAppMessage
from app.services.session_service import get_session, save_session, build_default_session
from app.services.whatsapp_service import send_whatsapp_payload, send_text_message
from app.services.llm_service import call_extract
from app.services.policy_service import (
    create_policy, get_active_draft, set_product_selection, cancel_policy,
    set_personal_details, set_payment_method, set_country,
    set_bank_details, set_msisdn_info, set_channel_info, set_airport_info,
)

logger = logging.getLogger(__name__)

PRODUCTS_API_BASE_URL = "https://dev-ilekun-ipv.ipurvey.com/api/v1/tab-pc/products/getByCountry"
PAYOUT_METHODS_API_URL = "https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/payout-method/types"

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
FLOW_STEP_COUNTRY = "country_input"
FLOW_STEP_PRODUCT_LIST = "product_list"
FLOW_STEP_PRODUCT_SELECTED = "product_selected"
FLOW_STEP_PD_FIRST_NAME = "pd_first_name"
FLOW_STEP_PD_LAST_NAME = "pd_last_name"
FLOW_STEP_PD_EMAIL = "pd_email"
FLOW_STEP_PD_NIN = "pd_nin"
FLOW_STEP_PD_ACCOUNT_NUMBER = "pd_account_number"
FLOW_STEP_PAYMENT_METHOD = "payment_method"
FLOW_STEP_BANK_SELECTION = "bank_selection"
FLOW_STEP_MSISDN_WALLET = "msisdn_wallet"
FLOW_STEP_MSISDN_WALLET_INPUT = "msisdn_wallet_input"
FLOW_STEP_AIRPORT_INPUT = "airport_input"
FLOW_STEP_AIRPORT_SELECT = "airport_select"

BUTTON_CREATE_NEW = "policy_create_new"
BUTTON_SUBMIT_ITINERARY = "policy_submit_itinerary"
BUTTON_VIEW_PRODUCTS = "policy_view_products"
PRODUCT_ID_PREFIX = "product_"
PAYMENT_METHOD_PREFIX = "payout_"
BANK_ID_PREFIX = "bank_"
BANK_NAV_NEXT = "bank_nav_next"
BANK_NAV_PREV = "bank_nav_prev"
BUTTON_WALLET_SAME = "wallet_same_number"
BUTTON_WALLET_DIFF = "wallet_diff_number"
NAV_NEXT = "policy_nav_next"
NAV_PREV = "policy_nav_prev"
BUTTON_RETRY = "policy_retry"
BUTTON_START_OVER = "policy_start_over"

BANKS_API_URL = "https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/payout-method/banks"
AIRPORTS_API_URL = "https://dev-ilekun-ipv.ipurvey.com/api/v2/airports/search"
BANKS_PER_PAGE = 8
AIRPORT_ID_PREFIX = "airport_"

PERSONAL_DETAIL_STEPS = [
    {"step": FLOW_STEP_PD_FIRST_NAME, "field": "first_name", "prompt": "Please enter your *first name*:", "expected_format": "text"},
    {"step": FLOW_STEP_PD_LAST_NAME, "field": "last_name", "prompt": "Please enter your *last name*:", "expected_format": "text"},
    {"step": FLOW_STEP_PD_EMAIL, "field": "email", "prompt": "Please enter your *email address*:", "expected_format": "email"},
    {"step": FLOW_STEP_PD_NIN, "field": "nin", "prompt": "Please enter your *NIN (National Identification Number)*:", "expected_format": "text"},
    {"step": FLOW_STEP_PD_ACCOUNT_NUMBER, "field": "account_number", "prompt": "Please enter your *account number*:", "expected_format": "text"},
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

    if _is_cancel_command(message):
        flow_state = _get_flow_state(session)
        active_policy_id = flow_state.get("policy_id")
        if active_policy_id:
            await cancel_policy(active_policy_id)
        session["active_policy_id"] = None
        await send_text_message(
            to=sender_wa_id,
            body="Policy flow cancelled. Send any message to continue or type 'policy' to start again.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _clear_flow_state(session, sender_wa_id)
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
            await _send_payment_methods(sender_wa_id, phone_number_id, in_reply_to)
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
                    await _show_final_summary(
                        sender_wa_id, phone_number_id, in_reply_to,
                        session, flow_state, policy_id, airport_info,
                    )
                else:
                    rows = []
                    for idx, airport in enumerate(airports[:10]):
                        iata = airport.get("iata_code", "")
                        name = airport.get("name", "Unknown")
                        country = airport.get("country", "")
                        rows.append({
                            "id": f"{AIRPORT_ID_PREFIX}{idx}",
                            "title": str(name)[:24],
                            "description": f"{iata} - {country}"[:72],
                        })
                    payload = {
                        "messaging_product": "whatsapp",
                        "recipient_type": "individual",
                        "to": sender_wa_id,
                        "type": "interactive",
                        "interactive": {
                            "type": "list",
                            "header": {"type": "text", "text": "Select Airport"},
                            "body": {"text": f"Multiple airports found for *\"{saved_search}\"*. Please select one:"},
                            "action": {
                                "button": "View Airports",
                                "sections": [{"title": "Airports", "rows": rows}]
                            }
                        }
                    }
                    await send_whatsapp_payload(payload, phone_number_id=phone_number_id, in_reply_to=in_reply_to, source="policy_flow")
                    await _update_flow_state(session, sender_wa_id, {
                        **flow_state,
                        "step": FLOW_STEP_AIRPORT_SELECT,
                        "available_airports": airports[:10],
                        "retry_step": None,
                        "retry_data": None,
                    })
                return

            await send_text_message(
                to=sender_wa_id,
                body="Please enter your *city or state name* to search for an airport (e.g. Ilorin, Kano, Port Harcourt):",
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
    elif current_step in [s["step"] for s in PERSONAL_DETAIL_STEPS]:
        await _handle_personal_detail_input(
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
            current_step=current_step,
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
    elif current_step == FLOW_STEP_BANK_SELECTION:
        await _handle_bank_selection(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_MSISDN_WALLET:
        await _handle_msisdn_wallet_choice(
            reply_id=reply_id,
            message=message,
            sender_wa_id=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            session=session,
        )
    elif current_step == FLOW_STEP_MSISDN_WALLET_INPUT:
        await _handle_msisdn_wallet_input(
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
    else:
        await _send_policy_menu(sender_wa_id, phone_number_id, in_reply_to)
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": FLOW_STEP_MENU,
        })


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
        await send_text_message(
            to=sender_wa_id,
            body="Great! Let's create a new policy for you.\n\nPlease enter your *country name* (e.g. Nigeria, Kenya, Ghana):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": FLOW_STEP_COUNTRY,
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
                "text": f"Country set to *{country_name}*.\n\nNow let's find the right product for you. Tap the button below to view available products."
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
    try:
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
            logger.error(f"Products API error: HTTP {response.status_code} for {country_code}, body={response.text[:500]}")
            return None
    except httpx.TimeoutException as e:
        logger.error(f"Products API timeout for {country_code}: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch products for {country_code}: {type(e).__name__}: {e}")
        return None


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
    price_str = _get_product_price_display(product, country_code)
    validity = product.get("validityDays", "")
    validity_str = f"{validity} day{'s' if validity != 1 else ''}" if validity else ""
    coverage = ", ".join(product.get("coverageTypes", []))

    parts = []
    if price_str:
        parts.append(price_str)
    if validity_str:
        parts.append(validity_str)
    desc = " | ".join(parts)
    if coverage:
        desc = f"{desc}\n{coverage}"
    return desc[:72]


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
        f"Here are our available insurance products{page_info}.\n"
        f"Showing {start + 1}-{end} of {total} products.\n\n"
        f"Select a product to proceed with your policy."
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
            f"Price: {price_display}\n"
            f"Validity: {validity} day{'s' if validity != 1 else ''}\n"
            f"Coverage: {coverage}\n"
            f"Provider: {selected_product.get('providerName', '')}\n\n"
            f"Now let's capture your personal details."
        )

        await send_text_message(
            to=sender_wa_id,
            body=confirm_text,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

        first_step = PERSONAL_DETAIL_STEPS[0]
        await send_text_message(
            to=sender_wa_id,
            body=first_step["prompt"],
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

        session["active_policy_id"] = policy_id
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": first_step["step"],
            "action": "create_new",
            "selected_product": product_data,
            "policy_id": policy_id,
            "country_code": flow_state.get("country_code"),
            "country_name": flow_state.get("country_name"),
            "personal_details": {},
        })
    else:
        await send_text_message(
            to=sender_wa_id,
            body="Please select a product from the list above.",
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

    extract_result = await _extract_value(
        sender_wa_id=sender_wa_id,
        field_name=current_field,
        question_asked=current_step_info["prompt"],
        user_response=text_input,
        expected_format=current_step_info.get("expected_format", "text"),
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
        error_msg = extract_result.get("validation_message", f"Please enter a valid {current_field.replace('_', ' ')}.")
        await send_text_message(
            to=sender_wa_id,
            body=error_msg,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    extracted_value = extract_result.get("value", text_input)

    if current_field == "email" and not extract_result.get("fallback") and not _validate_email(extracted_value):
        await send_text_message(
            to=sender_wa_id,
            body="That doesn't look like a valid email address. Please enter a valid email (e.g. name@example.com):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    if current_field == "email" and extract_result.get("fallback") and not _validate_email(text_input):
        await send_text_message(
            to=sender_wa_id,
            body="That doesn't look like a valid email address. Please enter a valid email (e.g. name@example.com):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    personal_details[current_field] = extracted_value

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
            f"NIN: {personal_details.get('nin', '')}\n"
            f"Account Number: {personal_details.get('account_number', '')}\n\n"
            f"Now let's select your preferred payment method."
        )
        await send_text_message(
            to=sender_wa_id,
            body=summary,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

        await _send_payment_methods(sender_wa_id, phone_number_id, in_reply_to)
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_PAYMENT_METHOD,
            "personal_details": personal_details,
        })


async def _fetch_payment_methods() -> Optional[list]:
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=True) as client:
            logger.info(f"Fetching payment methods from {PAYOUT_METHODS_API_URL}")
            response = await client.get(PAYOUT_METHODS_API_URL)
            logger.info(f"Payment methods API response: HTTP {response.status_code}, size={len(response.content)} bytes")
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    logger.info(f"Fetched {len(data)} payment methods")
                    return data
            logger.error(f"Payment methods API error: HTTP {response.status_code}, body={response.text[:500]}")
            return None
    except httpx.TimeoutException as e:
        logger.error(f"Payment methods API timeout: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch payment methods: {type(e).__name__}: {e}")
        return None


PAYMENT_METHOD_LABELS = {
    "BANK_TRANSFER": "Bank Transfer",
    "WALLET": "Wallet",
    "MOBILE_MONEY": "Mobile Money",
}


async def _send_payment_methods(to: str, phone_number_id: str, in_reply_to: str) -> None:
    methods = await _fetch_payment_methods()
    if not methods:
        methods = ["BANK_TRANSFER", "WALLET", "MOBILE_MONEY"]

    buttons = []
    for method in methods[:3]:
        buttons.append({
            "type": "reply",
            "reply": {
                "id": f"{PAYMENT_METHOD_PREFIX}{method}",
                "title": PAYMENT_METHOD_LABELS.get(method, method.replace("_", " ").title())[:20],
            }
        })

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": "Please select your preferred payment method:"
            },
            "action": {
                "buttons": buttons
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
            body=f"Payment method selected: *{label}*\n\nNow let's select your bank.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

        country_code = flow_state.get("country_code", "NG")
        banks = await _fetch_banks(country_code)
        if not banks:
            country_name = flow_state.get("country_name", country_code)
            await _send_retry_options(
                to=sender_wa_id,
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                error_message=f"We couldn't fetch available banks for *{country_name}*. This could be a temporary issue with the banking service.",
                retry_label="Retry Banks",
            )
            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_PAYMENT_METHOD,
                "payment_method": method,
                "retry_step": FLOW_STEP_BANK_SELECTION,
            })
            return

        banks.sort(key=lambda b: b.get("name", "").lower())
        page = 0
        await _send_banks_page(sender_wa_id, phone_number_id, in_reply_to, banks, page)
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_BANK_SELECTION,
            "payment_method": method,
            "available_banks": banks,
            "bank_page": page,
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
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=True) as client:
            logger.info(f"Fetching banks for country_code={country_code} from {BANKS_API_URL}")
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
            logger.error(f"Banks API error: HTTP {response.status_code} for {country_code}, body={response.text[:500]}")
            return None
    except httpx.TimeoutException as e:
        logger.error(f"Banks API timeout for {country_code}: {type(e).__name__}: {e}")
        return None
    except httpx.ConnectError as e:
        logger.error(f"Banks API connection error for {country_code}: {type(e).__name__}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch banks for {country_code}: {type(e).__name__}: {e}")
        return None


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

    page_info = f" (Page {page + 1}/{total_pages})" if total_pages > 1 else ""
    body_text = (
        f"Please select your bank{page_info}.\n"
        f"Showing {start + 1}-{end} of {total} banks."
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
            "footer": {
                "text": "Banks sorted alphabetically"
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

        if policy_id:
            await set_bank_details(policy_id, bank_details)
            logger.info(f"Bank '{bank_details['bank_name']}' saved to policy {policy_id}")

        payment_method = flow_state.get("payment_method", "")
        country_code = flow_state.get("country_code", "")

        msisdn_info = {
            "phone_number": sender_wa_id,
            "country_code": country_code,
        }

        if payment_method == "WALLET":
            await send_text_message(
                to=sender_wa_id,
                body=(
                    f"Bank selected: *{bank_details['bank_name']}*\n\n"
                    f"Your MSISDN is set to your WhatsApp number: *{sender_wa_id}* (Country: {country_code})\n\n"
                    f"Since you selected *Wallet* as your payment method, do you have a different phone number for your wallet?"
                ),
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )

            wallet_payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": sender_wa_id,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {
                        "text": "Is your wallet number different from your WhatsApp number?"
                    },
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": BUTTON_WALLET_DIFF,
                                    "title": "Yes, different"
                                }
                            },
                            {
                                "type": "reply",
                                "reply": {
                                    "id": BUTTON_WALLET_SAME,
                                    "title": "No, same number"
                                }
                            }
                        ]
                    }
                }
            }
            await send_whatsapp_payload(
                wallet_payload,
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )

            await _update_flow_state(session, sender_wa_id, {
                **flow_state,
                "step": FLOW_STEP_MSISDN_WALLET,
                "bank_details": bank_details,
                "msisdn_info": msisdn_info,
            })
        else:
            if policy_id:
                await set_msisdn_info(policy_id, msisdn_info)
                logger.info(f"MSISDN info saved to policy {policy_id}")

            await _finalize_channel_and_airport_prompt(
                sender_wa_id, phone_number_id, in_reply_to,
                session, flow_state, policy_id,
                bank_details, msisdn_info,
            )
    else:
        await send_text_message(
            to=sender_wa_id,
            body="Please select a bank from the list. Tap the 'View Banks' button to see the options.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _send_banks_page(sender_wa_id, phone_number_id, in_reply_to, banks, current_page)


async def _handle_msisdn_wallet_choice(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")
    msisdn_info = flow_state.get("msisdn_info", {})
    bank_details = flow_state.get("bank_details", {})

    if reply_id == BUTTON_WALLET_DIFF:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter your *wallet phone number* (include country code, e.g. 2348012345678):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_MSISDN_WALLET_INPUT,
        })

    elif reply_id == BUTTON_WALLET_SAME:
        msisdn_info["wallet_number"] = sender_wa_id

        if policy_id:
            await set_msisdn_info(policy_id, msisdn_info)
            logger.info(f"MSISDN info (same wallet) saved to policy {policy_id}")

        await _finalize_channel_and_airport_prompt(
            sender_wa_id, phone_number_id, in_reply_to,
            session, flow_state, policy_id,
            bank_details, msisdn_info,
        )
    else:
        await send_text_message(
            to=sender_wa_id,
            body="Please select one of the options above.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )


async def _handle_msisdn_wallet_input(message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")
    msisdn_info = flow_state.get("msisdn_info", {})
    bank_details = flow_state.get("bank_details", {})

    text_input = _get_text_input(message)
    if not text_input:
        await send_text_message(
            to=sender_wa_id,
            body="Please enter your wallet phone number as text (e.g. 2348012345678):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    extract_result = await _extract_value(
        sender_wa_id=sender_wa_id,
        field_name="phone_number",
        question_asked="Please enter your wallet phone number (include country code, e.g. 2348012345678):",
        user_response=text_input,
        expected_format="phone",
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

    if extract_result.get("is_valid"):
        cleaned = extract_result.get("value", text_input)
    else:
        cleaned = re.sub(r"[^0-9+]", "", text_input)

    cleaned = re.sub(r"[^0-9+]", "", cleaned)
    if len(cleaned) < 7 or len(cleaned) > 20:
        await send_text_message(
            to=sender_wa_id,
            body="That doesn't look like a valid phone number. Please enter a valid wallet number (e.g. 2348012345678):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    msisdn_info["wallet_number"] = cleaned

    if policy_id:
        await set_msisdn_info(policy_id, msisdn_info)
        logger.info(f"MSISDN info (wallet: {cleaned}) saved to policy {policy_id}")

    await _finalize_channel_and_airport_prompt(
        sender_wa_id, phone_number_id, in_reply_to,
        session, flow_state, policy_id,
        bank_details, msisdn_info,
    )


async def _finalize_channel_and_airport_prompt(
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

    await send_text_message(
        to=sender_wa_id,
        body="Now let's set your departure airport.\n\nPlease enter your *city or state name* (e.g. Ilorin, Kano, Port Harcourt):",
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )

    await _update_flow_state(session, sender_wa_id, {
        **flow_state,
        "step": FLOW_STEP_AIRPORT_INPUT,
        "bank_details": bank_details,
        "msisdn_info": msisdn_info,
        "channel_info": channel_info,
    })


async def _fetch_airports(search_term: str) -> Optional[list]:
    try:
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
            logger.error(f"Airports API error: HTTP {response.status_code} for '{search_term}', body={response.text[:500]}")
            return None
    except httpx.TimeoutException as e:
        logger.error(f"Airports API timeout for '{search_term}': {type(e).__name__}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to fetch airports for '{search_term}': {type(e).__name__}: {e}")
        return None


async def _handle_airport_input(message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")

    text_input = _get_text_input(message)
    if not text_input:
        await send_text_message(
            to=sender_wa_id,
            body="Please type a *city or state name* to search for an airport (e.g. Ilorin, Kano):",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )
        return

    extract_result = await _extract_value(
        sender_wa_id=sender_wa_id,
        field_name="city",
        question_asked="Please enter your city or state name (e.g. Ilorin, Kano, Port Harcourt):",
        user_response=text_input,
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

    cleaned_input = extract_result.get("value", text_input) if extract_result.get("is_valid") else text_input
    search_term = cleaned_input.strip().title()

    airports = await _fetch_airports(search_term)

    if airports is None:
        await _send_retry_options(
            to=sender_wa_id,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            error_message=f"We couldn't search for airports for *\"{cleaned_input}\"* at the moment. The airport service may be temporarily unavailable.",
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
            body=f"No airports found for *\"{text_input}\"*.\n\nPlease try a different city or state name:",
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
            "country": airport.get("country", ""),
        }

        if policy_id:
            await set_airport_info(policy_id, airport_info)
            logger.info(f"Airport '{airport_info['name']}' ({airport_info['iata_code']}) saved to policy {policy_id}")

        await _show_final_summary(
            sender_wa_id, phone_number_id, in_reply_to,
            session, flow_state, policy_id, airport_info,
        )
    else:
        rows = []
        for idx, airport in enumerate(airports[:10]):
            iata = airport.get("iata_code", "")
            name = airport.get("name", "Unknown")
            country = airport.get("country", "")
            rows.append({
                "id": f"{AIRPORT_ID_PREFIX}{idx}",
                "title": str(name)[:24],
                "description": f"{iata} - {country}"[:72],
            })

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": sender_wa_id,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {
                    "type": "text",
                    "text": "Select Airport"
                },
                "body": {
                    "text": f"Multiple airports found for *\"{text_input}\"*. Please select one:"
                },
                "action": {
                    "button": "View Airports",
                    "sections": [
                        {
                            "title": "Airports",
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

        await _update_flow_state(session, sender_wa_id, {
            **flow_state,
            "step": FLOW_STEP_AIRPORT_SELECT,
            "available_airports": airports[:10],
        })


async def _handle_airport_selection(reply_id, message, sender_wa_id, phone_number_id, in_reply_to, session):
    flow_state = _get_flow_state(session)
    policy_id = flow_state.get("policy_id")
    airports = flow_state.get("available_airports", [])

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
            "country": airport.get("country", ""),
        }

        if policy_id:
            await set_airport_info(policy_id, airport_info)
            logger.info(f"Airport '{airport_info['name']}' ({airport_info['iata_code']}) saved to policy {policy_id}")

        await _show_final_summary(
            sender_wa_id, phone_number_id, in_reply_to,
            session, flow_state, policy_id, airport_info,
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
                body="Please select an airport from the list, or type a different city/state name to search again.",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )


async def _show_final_summary(
    sender_wa_id, phone_number_id, in_reply_to,
    session, flow_state, policy_id, airport_info,
):
    personal_details = flow_state.get("personal_details", {})
    selected_product = flow_state.get("selected_product", {})
    payment_method = flow_state.get("payment_method", "")
    country_name = flow_state.get("country_name", "")
    country_code = flow_state.get("country_code", "")
    bank_details = flow_state.get("bank_details", {})
    msisdn_info = flow_state.get("msisdn_info", {})
    payment_label = PAYMENT_METHOD_LABELS.get(payment_method, payment_method)

    wallet_line = ""
    if payment_method == "WALLET" and msisdn_info.get("wallet_number"):
        wallet_line = f"\nWallet Number: {msisdn_info['wallet_number']}"

    summary = (
        f"Here's a summary of your policy details:\n\n"
        f"*Country:* {country_name} ({country_code})\n"
        f"*Product:* {selected_product.get('name', '')}\n"
        f"*Price:* {selected_product.get('currency', '')} {selected_product.get('price', '')}\n\n"
        f"*Personal Details:*\n"
        f"Name: {personal_details.get('first_name', '')} {personal_details.get('last_name', '')}\n"
        f"Email: {personal_details.get('email', '')}\n"
        f"NIN: {personal_details.get('nin', '')}\n"
        f"Account Number: {personal_details.get('account_number', '')}\n\n"
        f"*Payment:*\n"
        f"Method: {payment_label}\n"
        f"Bank: {bank_details.get('bank_name', '')}\n"
        f"MSISDN: {msisdn_info.get('phone_number', '')} ({country_code}){wallet_line}\n\n"
        f"*Airport:*\n"
        f"{airport_info.get('name', '')} ({airport_info.get('iata_code', '')})\n\n"
        f"*Settings:*\n"
        f"Channel Payout: Bank\n"
        f"Source: Passenger\n"
        f"Consent: Yes\n\n"
        f"All details have been saved. The remaining steps (itinerary and policy submission) will be available soon.\n\n"
        f"Type 'policy' anytime to start a new policy."
    )

    await send_text_message(
        to=sender_wa_id,
        body=summary,
        phone_number_id=phone_number_id,
        in_reply_to=in_reply_to,
        source="policy_flow",
    )

    await _update_flow_state(session, sender_wa_id, {
        "active": False,
        "step": "completed_details",
        "action": "create_new",
        "policy_id": policy_id,
        "selected_product": selected_product,
        "personal_details": personal_details,
        "payment_method": payment_method,
        "bank_details": bank_details,
        "msisdn_info": msisdn_info,
        "channel_info": flow_state.get("channel_info", {}),
        "airport_info": airport_info,
        "country_code": country_code,
        "country_name": country_name,
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
