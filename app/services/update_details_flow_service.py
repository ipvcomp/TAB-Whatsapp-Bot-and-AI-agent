import logging
from typing import Optional

import app.services.ipurvey_service as ipurvey_service

from app.core.test_overrides import get_msisdn
from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload

logger = logging.getLogger(__name__)

UPDATE_DETAILS_FLOW_KEY = "update_details_flow"

NIGERIAN_BANKS = sorted([
    "Access Bank", "Carbon", "Citibank Nigeria",
    "Coronation Bank", "Ecobank Nigeria", "Fidelity Bank",
    "First Bank", "First City Monument Bank", "Globus Bank",
    "GT Bank", "Heritage Bank", "Jaiz Bank",
    "Keystone Bank", "Kuda Bank", "Lotus Bank",
    "Moniepoint", "Opay", "Palmpay", "Parallex Bank",
    "Polaris Bank", "Providus Bank", "Stanbic IBTC Bank",
    "Standard Chartered", "Sterling Bank", "SunTrust Bank",
    "Taj Bank", "Titan Trust Bank", "Union Bank",
    "United Bank for Africa", "Unity Bank",
    "VFD Microfinance Bank", "Wema Bank", "Zenith Bank",
])

BANKS_PER_PAGE = 8


def _filter_banks(query: str) -> list:
    q = query.strip().lower()
    matched = [b for b in NIGERIAN_BANKS if q in b.lower()]
    return matched if matched else NIGERIAN_BANKS[:]


def _bank_pages(banks: list) -> list:
    return [banks[i:i + BANKS_PER_PAGE] for i in range(0, len(banks), BANKS_PER_PAGE)]


def is_in_update_details_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    return session.get("temp_data", {}).get(UPDATE_DETAILS_FLOW_KEY, {}).get("active", False)


async def _get_flow_state(wa_id: str) -> tuple[dict, dict]:
    session = await get_session(wa_id) or {}
    flow = session.setdefault("temp_data", {}).setdefault(UPDATE_DETAILS_FLOW_KEY, {})
    return session, flow


async def _set_step(session: dict, step: str):
    session["temp_data"][UPDATE_DETAILS_FLOW_KEY]["step"] = step
    session["temp_data"][UPDATE_DETAILS_FLOW_KEY]["active"] = True
    await save_session(session)


async def _save_data(session: dict, key: str, value):
    session["temp_data"][UPDATE_DETAILS_FLOW_KEY].setdefault("data", {})[key] = value
    await save_session(session)


async def _reset(session: dict):
    session["temp_data"][UPDATE_DETAILS_FLOW_KEY] = {}
    await save_session(session)


_UTILITY = (
    "*Utility options:*\n"
    "0 ↩️ Back  |  9 🆘 Help  |  00 🏠 Main menu\n"
    "99 ❌ Cancel/Exit"
)


async def _send_text(to: str, body: str, phone_number_id: Optional[str]):
    await send_text_message(to=to, body=body, phone_number_id=phone_number_id, source="update_details_flow")
    await send_text_message(to=to, body=_UTILITY, phone_number_id=phone_number_id, source="update_details_flow")


async def _send_list(
    to: str,
    body: str,
    button_label: str,
    sections: list,
    phone_number_id: Optional[str],
    header: Optional[str] = None,
):
    interactive = {
        "type": "list",
        "body": {"text": body},
        "action": {"button": button_label, "sections": sections},
    }
    if header:
        interactive["header"] = {"type": "text", "text": header}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="update_details_flow")
    await send_text_message(to=to, body=_UTILITY, phone_number_id=phone_number_id, source="update_details_flow")


async def _send_buttons(
    to: str,
    body: str,
    buttons: list,
    phone_number_id: Optional[str],
):
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
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in buttons
                ]
            },
        },
    }
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="update_details_flow")
    await send_text_message(to=to, body=_UTILITY, phone_number_id=phone_number_id, source="update_details_flow")


# ── Entry point ────────────────────────────────────────────────────────────────

async def start_update_details_flow(
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session = await get_session(wa_id) or {}
    session.setdefault("temp_data", {})[UPDATE_DETAILS_FLOW_KEY] = {
        "active": True, "step": "upd_menu", "data": {},
    }
    if "user_id" not in session:
        session["user_id"] = wa_id

    # ── Fetch user profile so we have user_id and current values ──────────────
    msisdn = get_msisdn(wa_id)
    api_data = session.setdefault("api_data", {})
    if not api_data.get("user_id"):
        try:
            user = await ipurvey_service.check_user_exists(msisdn)
            if user and isinstance(user, dict):
                uid = user.get("userId") or user.get("id") or user.get("user_id")
                api_data["user_id"] = uid
                # Cache current profile values for display in prompts
                api_data["profile_first_name"] = user.get("firstName") or user.get("first_name") or ""
                api_data["profile_last_name"]  = user.get("lastName")  or user.get("last_name")  or ""
                api_data["profile_email"]      = user.get("email") or ""
        except Exception as exc:
            logger.warning(f"[upd_details] check_user_exists failed: {exc}")

    await save_session(session)

    await _send_list(
        to=wa_id,
        header="✏️ Update your details",
        body="What would you like to update?",
        button_label="Select option",
        sections=[{"title": "Update your details", "rows": [
            {"id": "upd_opt_name",   "title": "👤 Name"},
            {"id": "upd_opt_email",  "title": "✉️ Email address"},
            {"id": "upd_opt_bank",   "title": "🏦 Bank payout details"},
            {"id": "upd_opt_wallet", "title": "👛 Wallet payout"},
            {"id": "upd_opt_kyc",    "title": "🔒 KYC (BVN / NIN)"},
        ]}],
        phone_number_id=phone_number_id,
    )


# ── Main handler ───────────────────────────────────────────────────────────────

async def handle_update_details_flow(
    message,
    sender_wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session, flow = await _get_flow_state(sender_wa_id)
    step = flow.get("step", "upd_menu")
    data = flow.setdefault("data", {})

    text = ""
    reply_id = None
    if message.type == "text" and message.text:
        text = message.text.body.strip()
    if message.type == "interactive" and message.interactive:
        if message.interactive.type == "list_reply" and message.interactive.list_reply:
            reply_id = message.interactive.list_reply.id
        elif message.interactive.type == "button_reply" and message.interactive.button_reply:
            reply_id = message.interactive.button_reply.id

    # ── Main menu ──────────────────────────────────────────────────────────────
    if step == "upd_menu":
        if reply_id == "upd_opt_name":
            # Show sub-menu: which part of name to update
            api_data  = session.get("api_data", {})
            cur_fn    = api_data.get("profile_first_name", "")
            cur_ln    = api_data.get("profile_last_name", "")
            name_line = f"Current name: *{cur_fn} {cur_ln}*\n\n" if (cur_fn or cur_ln) else ""
            await _set_step(session, "upd_name_which")
            await _send_buttons(sender_wa_id,
                f"👤 *Update Name*\n\n{name_line}"
                "Which part of your name would you like to update?",
                [
                    {"id": "upd_n_first", "title": "First name"},
                    {"id": "upd_n_last",  "title": "Last name"},
                    {"id": "upd_n_both",  "title": "Both names"},
                ],
                phone_number_id)

        elif reply_id == "upd_opt_email":
            api_data  = session.get("api_data", {})
            cur_email = api_data.get("profile_email", "")
            email_line = f"Current email: *{cur_email}*\n\n" if cur_email else ""
            await _set_step(session, "upd_email_input")
            await _send_text(sender_wa_id,
                f"✉️ *Update Email Address*\n\n{email_line}"
                "Please input your new email address.\n"
                "This email will be used to send you\n"
                "documents, receipts and updates.\n\n"
                "_Example: john.doe@gmail.com_",
                phone_number_id)

        elif reply_id == "upd_opt_bank":
            await _set_step(session, "upd_bank_acct")
            await _send_text(sender_wa_id,
                "🏦 *Update Bank Payout Details*\n\n"
                "Please enter your account number.\n"
                "This will be used to receive funds\n"
                "in the event of a claim.\n\n"
                "_Example: 0123456789_",
                phone_number_id)

        elif reply_id == "upd_opt_wallet":
            await _set_step(session, "upd_wallet_select")
            await _send_buttons(sender_wa_id,
                "👛 *Update Wallet Payout*\n\nSelect your wallet provider:",
                [
                    {"id": "upd_w_9psb",      "title": "9PSB"},
                    {"id": "upd_w_smartcash", "title": "SmartCash"},
                    {"id": "upd_w_opay",      "title": "OPay"},
                ],
                phone_number_id)

        elif reply_id == "upd_opt_kyc":
            await _reset(session)
            from app.services.kyc_flow_service import start_kyc_flow
            await start_kyc_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)

        else:
            await _reset(session)
            await start_update_details_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)

    # ── Name: which field? (first / last / both) ──────────────────────────────
    elif step == "upd_name_which":
        api_data = session.get("api_data", {})
        cur_fn   = api_data.get("profile_first_name", "")
        cur_ln   = api_data.get("profile_last_name", "")
        if reply_id == "upd_n_first":
            await _save_data(session, "upd_name_field", "first")
            await _set_step(session, "upd_fname_input")
            fn_line = f"Current first name: *{cur_fn}*\n\n" if cur_fn else ""
            await _send_text(sender_wa_id,
                f"👤 *Update First Name*\n\n{fn_line}"
                "Please enter your new first name as it\n"
                "appears on your travel document:\n\n"
                "_Example: Samuel_",
                phone_number_id)
        elif reply_id == "upd_n_last":
            await _save_data(session, "upd_name_field", "last")
            await _set_step(session, "upd_lname_input")
            ln_line = f"Current last name: *{cur_ln}*\n\n" if cur_ln else ""
            await _send_text(sender_wa_id,
                f"👤 *Update Last Name*\n\n{ln_line}"
                "Please enter your new last name as it\n"
                "appears on your travel document:\n\n"
                "_Example: Olamide_",
                phone_number_id)
        elif reply_id == "upd_n_both":
            await _save_data(session, "upd_name_field", "both")
            await _set_step(session, "upd_fname_input")
            fn_line = f"Current first name: *{cur_fn}*\n\n" if cur_fn else ""
            await _send_text(sender_wa_id,
                f"👤 *Update First Name*\n\n{fn_line}"
                "Please enter your new first name:\n\n"
                "_Example: Samuel_",
                phone_number_id)
        else:
            await _reset(session)
            await start_update_details_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)

    # ── Name: new first name ───────────────────────────────────────────────────
    elif step == "upd_fname_input":
        fn = text.strip()
        if len(fn) < 2:
            await _send_text(sender_wa_id,
                "⚠️ Please enter a valid *first name* (at least 2 characters):\n"
                "_Example: Samuel_",
                phone_number_id)
            return
        await _save_data(session, "new_first_name", fn)
        name_field = data.get("upd_name_field", "first")
        user_id    = session.get("api_data", {}).get("user_id")
        if name_field == "both":
            # First name done — now ask for last name
            await _set_step(session, "upd_lname_input")
            api_data = session.get("api_data", {})
            cur_ln   = api_data.get("profile_last_name", "")
            ln_line  = f"Current last name: *{cur_ln}*\n\n" if cur_ln else ""
            await _send_text(sender_wa_id,
                f"👤 *Update Last Name*\n\n{ln_line}"
                "Now enter your new last name:\n\n"
                "_Example: Olamide_",
                phone_number_id)
        else:
            # Only first name — update now
            cur_ln = session.get("api_data", {}).get("profile_last_name", "")
            if user_id:
                try:
                    await ipurvey_service.update_user(user_id, {"firstName": fn, "lastName": cur_ln})
                    session.setdefault("api_data", {})["profile_first_name"] = fn
                    await save_session(session)
                except Exception as exc:
                    logger.error(f"[upd_details] update_user (firstName) failed: {exc}")
            await _send_success(session, sender_wa_id,
                "✅ First name updated successfully!",
                f"First name: *{fn}*",
                phone_number_id)

    # ── Name: new last name ────────────────────────────────────────────────────
    elif step == "upd_lname_input":
        ln = text.strip()
        if len(ln) < 2:
            await _send_text(sender_wa_id,
                "⚠️ Please enter a valid *last name* (at least 2 characters):\n"
                "_Example: Olamide_",
                phone_number_id)
            return
        await _save_data(session, "new_last_name", ln)
        fn      = data.get("new_first_name", session.get("api_data", {}).get("profile_first_name", ""))
        user_id = session.get("api_data", {}).get("user_id")
        if user_id:
            try:
                await ipurvey_service.update_user(user_id, {"firstName": fn, "lastName": ln})
                session.setdefault("api_data", {})["profile_first_name"] = fn
                session.setdefault("api_data", {})["profile_last_name"]  = ln
                await save_session(session)
            except Exception as exc:
                logger.error(f"[upd_details] update_user (lastName) failed: {exc}")
        name_field = data.get("upd_name_field", "last")
        if name_field == "both":
            await _send_success(session, sender_wa_id,
                "✅ Name updated successfully!",
                f"First name: *{fn}*\nLast name: *{ln}*",
                phone_number_id)
        else:
            await _send_success(session, sender_wa_id,
                "✅ Last name updated successfully!",
                f"Last name: *{ln}*",
                phone_number_id)

    # ── Name: which traveler? ──────────────────────────────────────────────────
    elif step == "upd_name_who":
        travelers: list = data.get("travelers", [])
        if reply_id and reply_id.startswith("upd_who_"):
            idx = int(reply_id.split("_")[2])
            if 0 <= idx < len(travelers):
                traveler = travelers[idx]
                await _save_data(session, "upd_name_idx", idx)
                await _save_data(session, "upd_name_target", traveler)
                await _set_step(session, "upd_name_input")
                await _send_text(sender_wa_id,
                    f"👤 *Update {traveler}'s name*\n\n"
                    "Please input the new full name as it\n"
                    "appears on their travel document:\n\n"
                    "_Example: John Adewale Doe_",
                    phone_number_id)
            else:
                await _start_name_flow(session, sender_wa_id, data, phone_number_id)
        else:
            await _start_name_flow(session, sender_wa_id, data, phone_number_id)

    # ── Name: type new name ────────────────────────────────────────────────────
    elif step == "upd_name_input":
        name = text.strip()
        if len(name) < 3:
            await _send_text(sender_wa_id,
                "⚠️ Please enter the *full name* (at least 3 characters):\n"
                "_Example: John Adewale Doe_",
                phone_number_id)
            return

        travelers: list = data.get("travelers", [])
        idx = data.get("upd_name_idx")
        parts = name.strip().split(None, 1)
        fn    = parts[0] if parts else name
        ln    = parts[1] if len(parts) > 1 else ""
        user_id    = session.get("api_data", {}).get("user_id")
        policy_id  = session.get("api_data", {}).get("policy_id")
        pax_ids    = session.get("api_data", {}).get("passenger_ids") or []

        if idx is not None and travelers:
            old_name = travelers[int(idx)]
            travelers[int(idx)] = name
            await _save_data(session, "travelers", travelers)
            if int(idx) == 0:
                await _save_data(session, "name", name)
                if user_id:
                    try:
                        await ipurvey_service.update_user(user_id, {"firstName": fn, "lastName": ln})
                    except Exception:
                        pass
            if policy_id and pax_ids and int(idx) < len(pax_ids):
                try:
                    await ipurvey_service.update_passenger(policy_id, pax_ids[int(idx)], fn, ln)
                except Exception:
                    pass
            await _send_success(session, sender_wa_id,
                f"✅ {old_name} successfully updated!",
                f"New name: *{name}*",
                phone_number_id,
                multi_traveler=True)
        else:
            await _save_data(session, "name", name)
            if user_id:
                try:
                    await ipurvey_service.update_user(user_id, {"firstName": fn, "lastName": ln})
                except Exception:
                    pass
            if policy_id and pax_ids:
                try:
                    await ipurvey_service.update_passenger(policy_id, pax_ids[0], fn, ln)
                except Exception:
                    pass
            await _send_success(session, sender_wa_id,
                "✅ Name updated successfully!",
                f"New name: *{name}*",
                phone_number_id,
                multi_traveler=False)

    # ── Email: type new email ──────────────────────────────────────────────────
    elif step == "upd_email_input":
        email = text.strip().lower()
        if "@" not in email or "." not in email or len(email) < 5:
            await _send_text(sender_wa_id,
                "⚠️ Please enter a valid *email address*:\n"
                "_Example: john.doe@gmail.com_",
                phone_number_id)
            return
        await _save_data(session, "email", email)
        api_data  = session.get("api_data", {})
        user_id   = api_data.get("user_id")
        policy_id = api_data.get("policy_id")
        # Update user profile email
        if user_id:
            try:
                await ipurvey_service.update_user(user_id, {"email": email})
                session.setdefault("api_data", {})["profile_email"] = email
                await save_session(session)
            except Exception as exc:
                logger.error(f"[upd_details] update_user (email) failed: {exc}")
        # Also update active policy email if one is linked
        if policy_id:
            try:
                await ipurvey_service.set_policy_email(policy_id, email)
            except Exception:
                pass
        await _send_success(session, sender_wa_id,
            "✅ Email updated successfully!",
            f"Email: *{email}*",
            phone_number_id)

    # ── Bank: account number ───────────────────────────────────────────────────
    elif step == "upd_bank_acct":
        acct = text.strip().replace(" ", "")
        if not acct.isdigit() or not (9 <= len(acct) <= 11):
            await _send_text(sender_wa_id,
                "⚠️ Enter a valid *10-digit account number*:\n"
                "_Example: 0123456789_",
                phone_number_id)
            return
        await _save_data(session, "upd_acct", acct)
        await _set_step(session, "upd_bank_search")
        await _send_text(sender_wa_id,
            "🔍 Enter at least 2 characters of your\n"
            "bank name to search:\n\n_Example: Zen, GT, Wem_",
            phone_number_id)

    # ── Bank: search ───────────────────────────────────────────────────────────
    elif step == "upd_bank_search":
        query = text.strip()
        if len(query) < 2:
            await _send_text(sender_wa_id,
                "⚠️ Enter *at least 2 characters*.\n_Example: Zen, GT_",
                phone_number_id)
            return
        banks = _filter_banks(query)
        pages = _bank_pages(banks)
        await _save_data(session, "upd_blist", banks)
        await _save_data(session, "upd_bpage", 0)
        await _set_step(session, "upd_bank_select")
        await _send_bank_page(session, sender_wa_id, pages, 0, banks, phone_number_id)

    # ── Bank: select from paginated list ──────────────────────────────────────
    elif step == "upd_bank_select":
        banks    = data.get("upd_blist", NIGERIAN_BANKS[:])
        pages    = _bank_pages(banks)
        cur_page = int(data.get("upd_bpage", 0))

        if reply_id == "upd_bnext":
            nxt = cur_page + 1
            if nxt < len(pages):
                await _save_data(session, "upd_bpage", nxt)
                await _send_bank_page(session, sender_wa_id, pages, nxt, banks, phone_number_id)

        elif reply_id == "upd_bprev":
            prv = cur_page - 1
            if prv >= 0:
                await _save_data(session, "upd_bpage", prv)
                await _send_bank_page(session, sender_wa_id, pages, prv, banks, phone_number_id)

        elif reply_id == "upd_bsearch":
            await _set_step(session, "upd_bank_search")
            await _send_text(sender_wa_id,
                "🔍 Enter at least 2 characters of your\n"
                "bank name to search:\n\n_Example: Zen, GT, Wem_",
                phone_number_id)

        elif reply_id and reply_id.startswith("upd_bk_"):
            idx = int(reply_id.split("_")[2])
            if 0 <= idx < len(banks):
                bank_name = banks[idx]
                bank_code = ipurvey_service.get_bank_code(bank_name)
                acct      = data.get("upd_acct", "0000000000")
                await _save_data(session, "bank_name", bank_name)
                await _save_data(session, "bank_acct", acct)
                api_data     = session.get("api_data", {})
                user_id      = api_data.get("user_id") or ""
                account_name = session.get("data", {}).get("name") or ""
                if user_id:
                    try:
                        payout_result = await ipurvey_service.create_payout_method_bank(
                            user_id=user_id,
                            account_number=acct,
                            account_name=account_name,
                            bank_code=bank_code,
                            bank_name=bank_name,
                        )
                        if payout_result and isinstance(payout_result, dict):
                            pm_id = payout_result.get("id") or payout_result.get("payoutMethodId")
                            if pm_id:
                                session.setdefault("api_data", {})["payout_method_id"] = pm_id
                    except Exception as exc:
                        logger.error(f"[upd_details] create_payout_method_bank failed: {exc}")
                await _send_success(session, sender_wa_id,
                    "✅ Payout details updated successfully!",
                    f"Bank: *{bank_name}*\nAccount: ****{acct[-4:]}",
                    phone_number_id)
            else:
                await _send_text(sender_wa_id,
                    "⚠️ Please select a bank from the list.", phone_number_id)
        else:
            await _send_text(sender_wa_id,
                "⚠️ Please select a bank from the list.", phone_number_id)

    # ── Wallet: select provider ────────────────────────────────────────────────
    elif step == "upd_wallet_select":
        wallet_map = {
            "upd_w_9psb":      "9PSB",
            "upd_w_smartcash": "SmartCash",
            "upd_w_opay":      "OPay",
        }
        if reply_id in wallet_map:
            wtype = wallet_map[reply_id]
            await _save_data(session, "upd_wallet_type", wtype)
            await _set_step(session, "upd_wallet_phone")
            await _send_text(sender_wa_id,
                f"📱 *{wtype} Wallet*\n\n"
                "Enter the phone number linked\n"
                "to your wallet account:\n\n"
                "_Example: 08012345678_",
                phone_number_id)
        else:
            await _reset(session)
            await start_update_details_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)

    # ── Wallet: phone number ───────────────────────────────────────────────────
    elif step == "upd_wallet_phone":
        digits = text.strip().replace(" ", "").replace("-", "")
        if not digits.isdigit() or len(digits) < 10:
            await _send_text(sender_wa_id,
                "⚠️ Enter a valid *phone number*:\n_Example: 08012345678_",
                phone_number_id)
            return
        wtype  = data.get("upd_wallet_type", "Wallet")
        masked = digits[:4] + "****" + digits[-3:]
        await _save_data(session, "wallet_phone", digits)
        api_data     = session.get("api_data", {})
        user_id      = api_data.get("user_id") or ""
        account_name = session.get("data", {}).get("name") or ""
        if user_id:
            try:
                payout_result = await ipurvey_service.create_payout_method_wallet(
                    user_id=user_id,
                    phone_number=digits,
                    account_name=account_name,
                    network=wtype,
                )
                if payout_result and isinstance(payout_result, dict):
                    pm_id = payout_result.get("id") or payout_result.get("payoutMethodId")
                    if pm_id:
                        session.setdefault("api_data", {})["payout_method_id"] = pm_id
            except Exception as exc:
                logger.error(f"[upd_details] create_payout_method_wallet failed: {exc}")
        await _send_success(session, sender_wa_id,
            "✅ Wallet payout updated successfully!",
            f"Wallet: *{wtype}*\nPhone: *{masked}*",
            phone_number_id)

    # ── Post-success navigation ────────────────────────────────────────────────
    elif step == "upd_done":
        if reply_id == "upd_more":
            await _reset(session)
            await start_update_details_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        elif reply_id == "upd_home":
            await _reset(session)
            from app.services.auto_reply_service import send_main_menu
            await send_main_menu(to=sender_wa_id, phone_number_id=phone_number_id)
        else:
            await _reset(session)
            await start_update_details_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)

    else:
        await _reset(session)
        await start_update_details_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _start_name_flow(session: dict, wa_id: str, data: dict, phone_number_id: Optional[str]):
    travelers: list = data.get("travelers", [])
    if len(travelers) > 1:
        rows = [
            {"id": f"upd_who_{i}", "title": t[:24]}
            for i, t in enumerate(travelers)
        ]
        await _set_step(session, "upd_name_who")
        await _send_list(wa_id,
            "Which traveler would you like to update?\n\n"
            "Select a traveler from your active policy:",
            "Select traveler",
            [{"title": "Travelers", "rows": rows}],
            phone_number_id,
            header="👤 Update Name")
    else:
        await _save_data(session, "upd_name_idx", None)
        await _set_step(session, "upd_name_input")
        primary = data.get("name", "")
        prompt  = f"👤 *Update name for {primary}*\n\n" if primary else "👤 *Update your name*\n\n"
        await _send_text(wa_id,
            prompt
            + "Please input your full name as it appears\n"
              "on your travel document:\n\n"
              "_Example: John Adewale Doe_",
            phone_number_id)


async def _send_bank_page(
    session: dict,
    wa_id: str,
    pages: list,
    page_idx: int,
    all_banks: list,
    phone_number_id: Optional[str],
):
    total_pages = len(pages)
    total_banks = len(all_banks)
    page_banks  = pages[page_idx]
    start_num   = page_idx * BANKS_PER_PAGE + 1
    end_num     = start_num + len(page_banks) - 1

    rows = []
    for bank in page_banks:
        idx   = all_banks.index(bank)
        title = f"🏦 {bank}"
        if len(title) > 24:
            title = title[:23] + "…"
        rows.append({"id": f"upd_bk_{idx}", "title": title})

    if page_idx < total_pages - 1:
        rows.append({"id": "upd_bnext", "title": f"→ Next (Page {page_idx+2}/{total_pages})"[:24]})
    if page_idx > 0:
        rows.append({"id": "upd_bprev", "title": f"← Back (Page {page_idx}/{total_pages})"[:24]})
    rows.append({"id": "upd_bsearch", "title": "🔍 Search again"})

    await _send_list(wa_id,
        f"Select Bank — Page {page_idx+1} of {total_pages}\n"
        f"Showing {start_num}–{end_num} of {total_banks} banks",
        "Select bank",
        [{"title": "Banks", "rows": rows}],
        phone_number_id,
        header=f"🏦 Page {page_idx+1}/{total_pages}")


async def _send_success(
    session: dict,
    wa_id: str,
    title: str,
    detail: str,
    phone_number_id: Optional[str],
    multi_traveler: bool = False,
):
    await _set_step(session, "upd_done")
    await _send_text(wa_id,
        f"{title}\n\n{detail}\n\n"
        "Your details have been updated successfully. ✅",
        phone_number_id)
    more_label = "✏️ Update another transfer" if multi_traveler else "✏️ Update another detail"
    await _send_buttons(wa_id,
        "What would you like to do next?",
        [
            {"id": "upd_more", "title": more_label[:20]},
            {"id": "upd_home", "title": "🏠 Main menu"},
        ],
        phone_number_id)


async def go_back_one_step(wa_id: str, phone_number_id: Optional[str]):
    """Go back exactly one step in the update details flow instead of restarting."""
    session, flow = await _get_flow_state(wa_id)
    step = flow.get("step", "upd_menu")
    data = flow.get("data", {})

    async def _show_menu():
        flow["step"] = "upd_menu"
        await save_session(session)
        await _send_list(
            to=wa_id,
            header="✏️ Update your details",
            body="What would you like to update?",
            button_label="Select option",
            sections=[{"title": "Update your details", "rows": [
                {"id": "upd_opt_name",   "title": "👤 Name"},
                {"id": "upd_opt_email",  "title": "✉️ Email address"},
                {"id": "upd_opt_bank",   "title": "🏦 Bank payout details"},
                {"id": "upd_opt_wallet", "title": "👛 Wallet payout"},
                {"id": "upd_opt_kyc",    "title": "🔒 KYC (BVN / NIN)"},
            ]}],
            phone_number_id=phone_number_id,
        )

    # At the menu or done screen — exit to main menu
    if step in ("upd_menu", "upd_done"):
        await _reset(session)
        from app.services.auto_reply_service import send_main_menu
        await send_main_menu(to=wa_id, phone_number_id=phone_number_id, wa_id=wa_id)
        return

    # Name: new first/last name → back to which-field sub-menu
    if step in ("upd_fname_input", "upd_lname_input"):
        api_data = session.get("api_data", {})
        cur_fn   = api_data.get("profile_first_name", "")
        cur_ln   = api_data.get("profile_last_name", "")
        name_line = f"Current name: *{cur_fn} {cur_ln}*\n\n" if (cur_fn or cur_ln) else ""
        flow["step"] = "upd_name_which"
        await save_session(session)
        await _send_buttons(wa_id,
            f"👤 *Update Name*\n\n{name_line}"
            "Which part of your name would you like to update?",
            [
                {"id": "upd_n_first", "title": "First name"},
                {"id": "upd_n_last",  "title": "Last name"},
                {"id": "upd_n_both",  "title": "Both names"},
            ],
            phone_number_id)
        return

    # Name: which-field sub-menu → main menu
    if step == "upd_name_which":
        await _show_menu()
        return

    # Name sub-flow: input → who selector (or menu if single traveler)
    if step == "upd_name_input":
        travelers: list = data.get("travelers", [])
        if len(travelers) > 1:
            await _start_name_flow(session, wa_id, data, phone_number_id)
        else:
            await _show_menu()
        return

    # Name sub-flow: who selector → menu
    if step == "upd_name_who":
        await _show_menu()
        return

    # Email → menu
    if step == "upd_email_input":
        await _show_menu()
        return

    # Bank: select → search prompt
    if step == "upd_bank_select":
        flow["step"] = "upd_bank_search"
        await save_session(session)
        await _send_text(wa_id,
            "🔍 Enter at least 2 characters of your\n"
            "bank name to search:\n\n_Example: Zen, GT, Wem_",
            phone_number_id)
        return

    # Bank: search → account number prompt
    if step == "upd_bank_search":
        flow["step"] = "upd_bank_acct"
        await save_session(session)
        await _send_text(wa_id,
            "🏦 *Update Bank Payout Details*\n\n"
            "Please enter your account number.\n"
            "This will be used to receive funds\n"
            "in the event of a claim.\n\n"
            "_Example: 0123456789_",
            phone_number_id)
        return

    # Bank: account number → menu
    if step == "upd_bank_acct":
        await _show_menu()
        return

    # Wallet: phone input → wallet provider select
    if step == "upd_wallet_phone":
        flow["step"] = "upd_wallet_select"
        await save_session(session)
        await _send_buttons(wa_id,
            "👛 *Update Wallet Payout*\n\nSelect your wallet provider:",
            [
                {"id": "upd_w_9psb",      "title": "9PSB"},
                {"id": "upd_w_smartcash", "title": "SmartCash"},
                {"id": "upd_w_opay",      "title": "OPay"},
            ],
            phone_number_id)
        return

    # Wallet: provider select → menu
    if step == "upd_wallet_select":
        await _show_menu()
        return

    # Fallback — go back to menu
    await _show_menu()
