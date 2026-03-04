import logging
import re
from typing import Optional

import httpx

from app.core.config import get_settings
from app.models.webhook import WhatsAppMessage
from app.services.session_service import get_session, save_session, build_default_session
from app.services.whatsapp_service import send_whatsapp_payload, send_text_message
from app.services.policy_service import (
    create_policy, get_active_draft, set_product_selection, cancel_policy,
)

logger = logging.getLogger(__name__)

PRODUCTS_API_URL = "https://dev-ilekun-ipv.ipurvey.com/api/v1/tab-pc/products/by-channel/APP"
PRODUCTS_API_COUNTRY = "NG"

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
FLOW_STEP_PRODUCT_LIST = "product_list"
FLOW_STEP_PRODUCT_SELECTED = "product_selected"

BUTTON_CREATE_NEW = "policy_create_new"
BUTTON_SUBMIT_ITINERARY = "policy_submit_itinerary"
BUTTON_VIEW_PRODUCTS = "policy_view_products"
PRODUCT_ID_PREFIX = "product_"


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

    if current_step == FLOW_STEP_MENU:
        await _handle_menu_selection(
            reply_id=reply_id,
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
                            "title": "Create New Policy"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": BUTTON_SUBMIT_ITINERARY,
                            "title": "Submit Itinerary"
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
        await _send_view_products_prompt(sender_wa_id, phone_number_id, in_reply_to)
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": FLOW_STEP_PRODUCT_LIST,
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
            body="Please select one of the options from the menu above. Tap on 'Create New Policy' or 'Submit Itinerary'.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )


async def _send_view_products_prompt(to: str, phone_number_id: str, in_reply_to: str) -> None:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": "Great! Let's create a new policy for you. First, you'll need to select a product.\n\nTap the button below to view available products."
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

    if reply_id == BUTTON_VIEW_PRODUCTS:
        products = await _fetch_products()
        if not products:
            await send_text_message(
                to=sender_wa_id,
                body="Sorry, we couldn't fetch the available products at the moment. Please try again later by typing 'policy'.",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            await _clear_flow_state(session, sender_wa_id)
            return

        await _send_products_list(sender_wa_id, phone_number_id, in_reply_to, products)
        await _update_flow_state(session, sender_wa_id, {
            "active": True,
            "step": FLOW_STEP_PRODUCT_SELECTED,
            "action": "create_new",
            "available_products": products,
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


async def _fetch_products() -> Optional[list]:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                PRODUCTS_API_URL,
                params={"country": PRODUCTS_API_COUNTRY},
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    return data.get("data", [])
            logger.error(f"Products API error: HTTP {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Failed to fetch products: {e}")
        return None


async def _send_products_list(to: str, phone_number_id: str, in_reply_to: str, products: list) -> None:
    rows = []
    for product in products[:10]:
        coverage = ", ".join(product.get("coverageTypes", []))
        price_str = f"{product.get('currency', '')} {product.get('price', '')}"
        validity_str = f"{product.get('validityDays', '')} days"
        description = f"{price_str} | {validity_str}"
        if coverage:
            description = f"{description}\n{coverage}"

        rows.append({
            "id": f"{PRODUCT_ID_PREFIX}{product.get('productId', '')}",
            "title": str(product.get("name", "Unknown"))[:24],
            "description": description[:72],
        })

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {
                "type": "text",
                "text": "Available Products"
            },
            "body": {
                "text": "Here are our available insurance products. Please select one to proceed with your new policy."
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
    if reply_id and reply_id.startswith(PRODUCT_ID_PREFIX):
        product_id = reply_id[len(PRODUCT_ID_PREFIX):]
        flow_state = _get_flow_state(session)
        products = flow_state.get("available_products", [])
        policy_id = flow_state.get("policy_id")

        selected_product = None
        for p in products:
            if str(p.get("productId", "")) == product_id:
                selected_product = p
                break

        if not selected_product:
            await send_text_message(
                to=sender_wa_id,
                body="Sorry, we couldn't find that product. Please try again by typing 'policy'.",
                phone_number_id=phone_number_id,
                in_reply_to=in_reply_to,
                source="policy_flow",
            )
            await _clear_flow_state(session, sender_wa_id)
            return

        product_data = {
            "product_id": str(selected_product.get("productId", "")),
            "name": selected_product.get("name", ""),
            "price": selected_product.get("price"),
            "currency": selected_product.get("currency", ""),
            "validity_days": selected_product.get("validityDays"),
            "coverage_types": selected_product.get("coverageTypes", []),
        }

        if policy_id:
            await set_product_selection(policy_id, product_data)
            logger.info(f"Product saved to policy {policy_id} for user {sender_wa_id}")

        coverage = ", ".join(selected_product.get("coverageTypes", []))
        confirm_text = (
            f"You've selected *{selected_product.get('name', '')}*\n\n"
            f"Price: {selected_product.get('currency', '')} {selected_product.get('price', '')}\n"
            f"Validity: {selected_product.get('validityDays', '')} days\n"
            f"Coverage: {coverage}\n\n"
            f"Your selection has been saved. The next steps (personal details, payment method, and itinerary) will be available soon.\n\n"
            f"Type 'policy' anytime to start a new policy."
        )

        await send_text_message(
            to=sender_wa_id,
            body=confirm_text,
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )

        await _update_flow_state(session, sender_wa_id, {
            "active": False,
            "step": FLOW_STEP_PRODUCT_SELECTED,
            "action": "create_new",
            "selected_product": product_data,
            "policy_id": policy_id,
        })

        session["active_policy_id"] = policy_id
        await save_session(session)
    else:
        await send_text_message(
            to=sender_wa_id,
            body="Please select a product from the list above.",
            phone_number_id=phone_number_id,
            in_reply_to=in_reply_to,
            source="policy_flow",
        )


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
