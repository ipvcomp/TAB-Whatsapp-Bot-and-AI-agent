import asyncio
import logging
import re
from typing import Optional

import app.services.ipurvey_service as ipurvey_service

from app.core.test_overrides import get_msisdn
from app.services.llm_service import call_policy_flow_validate, get_llm_guidance
from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload

logger = logging.getLogger(__name__)

UPDATE_DETAILS_FLOW_KEY = "update_details_flow"

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


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
    "0 ↩️ Back  |  9 🆘 Help  |  00 🏠 Main menu\n"
    "99 ❌ Cancel/Exit"
)


async def _send_text(to: str, body: str, phone_number_id: Optional[str]):
    await send_text_message(to=to, body=f"{body}\n\n\n{_UTILITY}", phone_number_id=phone_number_id, source="update_details_flow")


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
        "body": {"text": f"{body}\n\n\n{_UTILITY}"},
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
            "body": {"text": f"{body}\n\n\n{_UTILITY}"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in buttons
                ]
            },
        },
    }
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="update_details_flow")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _pax_full_name(pax: dict) -> str:
    return f"{pax.get('firstName', '')} {pax.get('surname', '')}".strip()


async def _start_name_or_traveller_flow(
    session: dict,
    wa_id: str,
    passengers: list,
    phone_number_id: Optional[str],
):
    """Route to traveller selection list (multi) or single name input."""
    if len(passengers) > 1:
        await _set_step(session, "upd_travellers")
        rows = []
        for i, pax in enumerate(passengers):
            full_name = _pax_full_name(pax) or f"Traveller {i + 1}"
            is_primary = pax.get("isPrimaryTraveller", False)
            label = f"Traveller {i + 1}" + (" · Policy holder" if is_primary else "")
            rows.append({
                "id": f"upd_trav_{i}",
                "title": full_name[:24],
                "description": label[:72],
            })
        await _send_list(
            wa_id,
            "Select a traveller from your active policy:",
            "Select traveller",
            [{"title": "Travellers", "rows": rows}],
            phone_number_id,
            header="👥 Which traveller name would you like to update?",
        )
    elif len(passengers) == 1:
        pax = passengers[0]
        full_name = _pax_full_name(pax)
        pax_id = pax.get("passengerId") or pax.get("id") or ""
        await _save_data(session, "upd_trav_idx", 0)
        await _save_data(session, "upd_trav_id", pax_id)
        await _save_data(session, "upd_trav_name", full_name)
        await _save_data(session, "upd_is_primary", pax.get("isPrimaryTraveller", False))
        await _set_step(session, "upd_name_input")
        await _send_text(
            wa_id,
            f"👤 *Update your name*\n\n"
            f"Current name: *{full_name}*\n\n"
            "Please enter your new full name as it appears\n"
            "on your travel document:\n\n"
            "_e.g. John Adewale Doe_",
            phone_number_id,
        )
    else:
        # No passengers — update user profile name
        await _save_data(session, "upd_trav_idx", None)
        await _save_data(session, "upd_trav_id", None)
        api_data = session.get("api_data", {})
        cur_fn = api_data.get("profile_first_name", "")
        cur_ln = api_data.get("profile_last_name", "")
        cur_name = f"{cur_fn} {cur_ln}".strip()
        cur_line = f"Current name: *{cur_name}*\n\n" if cur_name else ""
        await _set_step(session, "upd_name_input")
        await _send_text(
            wa_id,
            f"👤 *Update your name*\n\n{cur_line}"
            "Please enter your new full name as it appears\n"
            "on your travel document:\n\n"
            "_e.g. John Adewale Doe_",
            phone_number_id,
        )


async def _send_bank_results(wa_id: str, banks: list, phone_number_id: Optional[str]):
    rows = []
    for idx, bank in enumerate(banks[:9]):
        title = f"🏦 {bank['name']}"
        if len(title) > 24:
            title = title[:23] + "…"
        rows.append({"id": f"upd_bk_{idx}", "title": title})
    rows.append({"id": "upd_bsearch", "title": "🔍 Search again"})
    await _send_list(
        wa_id,
        "🔍 *We found some banks*\n\nNot seeing your bank? You can search again.",
        "Select bank",
        [{"title": "Banks", "rows": rows}],
        phone_number_id,
    )


async def show_cancel_update_confirm(wa_id: str, phone_number_id: Optional[str]):
    await _send_buttons(
        wa_id,
        "❌ *Cancel Update*\n\nAre you sure you want to cancel?\nAny changes you've made will not be saved.",
        [
            {"id": "cx_yes_upd", "title": "❌ Yes, discard"},
            {"id": "cx_no_upd",  "title": "↩️ No, continue"},
        ],
        phone_number_id,
    )


async def _send_success(
    session: dict,
    wa_id: str,
    title: str,
    detail: str,
    phone_number_id: Optional[str],
    multi_traveler: bool = False,
):
    await _set_step(session, "upd_done")
    body = title
    if detail:
        body += f"\n\n{detail}"
    body += "\n\nWhat would you like to do next?"
    if multi_traveler:
        buttons = [
            {"id": "upd_more_trav", "title": "✏️ More traveller"},
            {"id": "upd_more",      "title": "✏️ Update details"},
            {"id": "upd_home",      "title": "🏠 Main menu"},
        ]
    else:
        buttons = [
            {"id": "upd_more", "title": "✏️ Update details"},
            {"id": "upd_home", "title": "🏠 Main menu"},
        ]
    await _send_buttons(wa_id, body, buttons, phone_number_id)


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

    msisdn  = get_msisdn(wa_id)
    api_data = session.setdefault("api_data", {})

    # Parallel: user profile + resume draft
    async def _fetch_user():
        if api_data.get("user_id"):
            return None
        return await ipurvey_service.check_user_exists(msisdn)

    try:
        results = await asyncio.gather(
            _fetch_user(),
            ipurvey_service.resume_draft_policy(msisdn),
            return_exceptions=True,
        )
        user_result, draft_result = results
        if not isinstance(user_result, Exception) and isinstance(user_result, dict):
            uid = user_result.get("userId") or user_result.get("id") or user_result.get("user_id")
            api_data["user_id"] = uid
            api_data["profile_first_name"] = user_result.get("firstName") or user_result.get("first_name") or ""
            api_data["profile_last_name"]  = user_result.get("lastName")  or user_result.get("last_name")  or ""
            api_data["profile_email"]      = user_result.get("email") or ""
        if not isinstance(draft_result, Exception) and isinstance(draft_result, dict):
            draft_pid = draft_result.get("policy_id") or ""
            if draft_pid and not api_data.get("policy_id"):
                api_data["policy_id"] = draft_pid
    except Exception as exc:
        logger.warning(f"[upd_details] parallel lookup failed: {exc}")

    # Fetch passengers from active policy
    passengers: list = []
    policy_id = api_data.get("policy_id") or ""
    if policy_id:
        try:
            passengers = await ipurvey_service.get_policy_passengers(policy_id)
        except Exception as exc:
            logger.warning(f"[upd_details] get_policy_passengers failed: {exc}")
    api_data["policy_passengers"] = passengers
    await save_session(session)

    # Build menu rows — Travellers only when policy has passengers
    menu_rows = [
        {"id": "upd_opt_name",   "title": "👤 Name"},
        {"id": "upd_opt_email",  "title": "✉️ Email address"},
    ]
    if passengers:
        menu_rows.append({"id": "upd_opt_travellers", "title": "👥 Travellers"})
    menu_rows += [
        {"id": "upd_opt_bank",   "title": "🏦 Bank payout details"},
        {"id": "upd_opt_wallet", "title": "👛 Wallet payout"},
        {"id": "upd_opt_kyc",    "title": "🔒 KYC (Biometric ID)"},
    ]
    await _send_list(
        to=wa_id,
        header="✏️ Update your details",
        body="What would you like to update?",
        button_label="Select option",
        sections=[{"title": "Update your details", "rows": menu_rows}],
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

    passengers = session.get("api_data", {}).get("policy_passengers") or []

    # ── Main menu ──────────────────────────────────────────────────────────────
    if step == "upd_menu":
        if reply_id in ("upd_opt_name", "upd_opt_travellers"):
            await _start_name_or_traveller_flow(session, sender_wa_id, passengers, phone_number_id)

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

    # ── Traveller selection ────────────────────────────────────────────────────
    elif step == "upd_travellers":
        if reply_id and reply_id.startswith("upd_trav_"):
            try:
                idx = int(reply_id.split("_")[2])
                if 0 <= idx < len(passengers):
                    pax       = passengers[idx]
                    full_name = _pax_full_name(pax) or f"Traveller {idx + 1}"
                    pax_id    = pax.get("passengerId") or pax.get("id") or ""
                    is_prim   = pax.get("isPrimaryTraveller", False)
                    await _save_data(session, "upd_trav_idx",  idx)
                    await _save_data(session, "upd_trav_id",   pax_id)
                    await _save_data(session, "upd_trav_name", full_name)
                    await _save_data(session, "upd_is_primary", is_prim)
                    await _set_step(session, "upd_name_input")
                    num   = idx + 1
                    label = f"Traveller {num}" + (" · Policy holder" if is_prim else "")
                    await _send_text(sender_wa_id,
                        f"👤 *Update {full_name}'s name*\n\n"
                        f"Current name: *{full_name}*\n\n"
                        "Please enter the new full name as it appears\n"
                        "on the travel document:\n\n"
                        "_e.g. Amina Sule Bello_",
                        phone_number_id)
                else:
                    await _start_name_or_traveller_flow(session, sender_wa_id, passengers, phone_number_id)
            except (ValueError, IndexError):
                await _start_name_or_traveller_flow(session, sender_wa_id, passengers, phone_number_id)
        else:
            await _start_name_or_traveller_flow(session, sender_wa_id, passengers, phone_number_id)

    # ── Name: full name input ──────────────────────────────────────────────────
    elif step == "upd_name_input":
        name  = text.strip()
        parts = name.split()
        # Must be at least 2 words, each at least 2 characters
        if len(parts) < 2 or any(len(p) < 2 for p in parts):
            await _send_text(sender_wa_id,
                "⚠️ Please enter your *full name* (first name and last name):\n"
                "_e.g. John Adewale Doe_",
                phone_number_id)
            return

        fn = parts[0]
        ln = " ".join(parts[1:])

        user_id    = session.get("api_data", {}).get("user_id")
        policy_id  = session.get("api_data", {}).get("policy_id")
        trav_idx   = data.get("upd_trav_idx")
        trav_id    = data.get("upd_trav_id")
        is_primary = data.get("upd_is_primary", False)
        is_multi   = trav_id is not None

        if trav_id and policy_id:
            try:
                ok = await ipurvey_service.update_passenger(policy_id, trav_id, fn, ln, is_primary)
                if ok:
                    # Update cached passengers list
                    pax_list = session.get("api_data", {}).get("policy_passengers") or []
                    if trav_idx is not None and 0 <= int(trav_idx) < len(pax_list):
                        pax_list[int(trav_idx)]["firstName"] = fn
                        pax_list[int(trav_idx)]["surname"]   = ln
                        session["api_data"]["policy_passengers"] = pax_list
                        await save_session(session)
            except Exception as exc:
                logger.error(f"[upd_details] update_passenger failed: {exc}")
        elif user_id:
            try:
                await ipurvey_service.update_user(user_id, {"firstName": fn, "lastName": ln})
                session.setdefault("api_data", {})["profile_first_name"] = fn
                session.setdefault("api_data", {})["profile_last_name"]  = ln
                await save_session(session)
            except Exception as exc:
                logger.error(f"[upd_details] update_user (name) failed: {exc}")

        num      = (int(trav_idx) + 1) if trav_idx is not None else 1
        is_prim  = data.get("upd_is_primary", False)
        trav_lbl = f"Traveller {num}" + (" · Policy holder" if is_prim else "")
        if is_multi:
            await _send_success(session, sender_wa_id,
                f"✅ *Name updated successfully*\n_{trav_lbl}: {name}_",
                "",
                phone_number_id,
                multi_traveler=True)
        else:
            await _send_success(session, sender_wa_id,
                "✅ *Name updated successfully*",
                f"New name: *{name}*",
                phone_number_id,
                multi_traveler=False)

    # ── Email: type new email ──────────────────────────────────────────────────
    elif step == "upd_email_input":
        email = text.strip().lower()
        if not _EMAIL_RE.match(email):
            await _send_text(sender_wa_id,
                "⚠️ Please enter a valid *email address*:\n"
                "_Example: john.doe@gmail.com_",
                phone_number_id)
            return
        await _save_data(session, "email", email)
        api_data  = session.get("api_data", {})
        user_id   = api_data.get("user_id")
        policy_id = api_data.get("policy_id")
        if user_id:
            try:
                await ipurvey_service.update_user(user_id, {"email": email})
                session.setdefault("api_data", {})["profile_email"] = email
                await save_session(session)
            except Exception as exc:
                logger.error(f"[upd_details] update_user (email) failed: {exc}")
        if policy_id:
            try:
                await ipurvey_service.set_policy_email(policy_id, email)
            except Exception:
                pass
        await _send_success(session, sender_wa_id,
            "✅ *Email updated successfully*",
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
            "🔍 Enter at least 3 characters of your\n"
            "bank name to search:\n\n_Example: Zen, GT, Wem_",
            phone_number_id)

    # ── Bank: search ───────────────────────────────────────────────────────────
    elif step == "upd_bank_search":
        query = text.strip()
        if len(query) < 3:
            await _send_text(sender_wa_id,
                "⚠️ Enter *at least 3 characters* of your bank name:\n_Example: Zen, GT, Wem_",
                phone_number_id)
            return
        banks = await ipurvey_service.search_banks(query)
        if not banks:
            logger.info(f"[upd_bank_search] No results for '{query}', calling LLM to extract clean bank term")
            llm_resp = await call_policy_flow_validate(
                step_id=27,
                context="Bank name search",
                field_name="bank_name",
                question_asked="Which bank do you use? Please type the bank name or abbreviation (e.g. GTB, Zenith, First Bank).",
                user_response=query,
                step_type="free_text",
                expected_format="Full Nigerian bank name or abbreviation (e.g. GTB, Zenith, First Bank)",
                validation_rules={"min_chars": 2},
            )
            normalized = (llm_resp or {}).get("normalized_value", "")
            extracted_value = (llm_resp or {}).get("extracted_value", "")
            selected = normalized or extracted_value
            logger.info(
                f"[upd_bank_search] LLM result: is_valid={(llm_resp or {}).get('is_valid')}, "
                f"extracted='{extracted_value}', "
                f"normalized='{normalized}', "
                f"selected='{selected}', "
                f"guidance='{(llm_resp or {}).get('guidance_message')}'"
            )
            if llm_resp and llm_resp.get("is_valid") and selected and len(selected.strip()) >= 2:
                banks = await ipurvey_service.search_banks_resilient(
                    normalized,
                    extracted_value,
                    country_code="NG",
                )
            if not banks:
                guidance = get_llm_guidance(llm_resp)
                if guidance:
                    # User asked a question instead of a bank name — answer
                    # it, then re-show the original search prompt.
                    await _send_text(sender_wa_id, guidance, phone_number_id)
                    await _send_text(sender_wa_id,
                        "🔍 Enter at least 3 characters of your\n"
                        "bank name to search:\n\n_Example: Zen, GT, Wem_",
                        phone_number_id)
                    return
                await _send_buttons(sender_wa_id,
                    f"❌ *No banks found matching \"{query}\"*\n\n"
                    "We couldn't find any bank matching your entry.\n"
                    "Please check the spelling or try searching again.",
                    [{"id": "upd_bsearch", "title": "🔍 Search again"}],
                    phone_number_id)
                return
        await _save_data(session, "upd_blist", banks)
        await _set_step(session, "upd_bank_select")
        await _send_bank_results(sender_wa_id, banks, phone_number_id)

    # ── Bank: select from results list ────────────────────────────────────────
    elif step == "upd_bank_select":
        banks = data.get("upd_blist", [])
        if reply_id == "upd_bsearch":
            await _set_step(session, "upd_bank_search")
            await _send_text(sender_wa_id,
                "🔍 Enter at least 3 characters of your\n"
                "bank name to search:\n\n_Example: Zen, GT, Wem_",
                phone_number_id)
        elif reply_id and reply_id.startswith("upd_bk_"):
            try:
                idx = int(reply_id.split("_")[2])
                if 0 <= idx < len(banks):
                    bank      = banks[idx]
                    bank_name = bank["name"]
                    bank_code = bank["code"]
                    acct      = data.get("upd_acct", "0000000000")
                    await _save_data(session, "bank_name", bank_name)
                    await _save_data(session, "bank_acct", acct)
                    api_data     = session.get("api_data", {})
                    user_id      = api_data.get("user_id") or ""
                    account_name = (
                        f"{api_data.get('profile_first_name', '')} "
                        f"{api_data.get('profile_last_name', '')}"
                    ).strip()
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
                        "✅ *Payout details updated successfully*",
                        f"Bank: *{bank_name}*\nAccount: ****{acct[-4:]}",
                        phone_number_id)
                else:
                    await _send_text(sender_wa_id,
                        "⚠️ Please select a bank from the list.", phone_number_id)
            except (ValueError, IndexError):
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
        account_name = (
            f"{api_data.get('profile_first_name', '')} "
            f"{api_data.get('profile_last_name', '')}"
        ).strip()
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
            "✅ *Wallet payout updated successfully*",
            f"Wallet: *{wtype}*\nPhone: *{masked}*",
            phone_number_id)

    # ── Post-success navigation ────────────────────────────────────────────────
    elif step == "upd_done":
        if reply_id == "upd_more_trav":
            # Go back to traveller selection (clear traveller selection data)
            data.pop("upd_trav_idx",  None)
            data.pop("upd_trav_id",   None)
            data.pop("upd_trav_name", None)
            data.pop("upd_is_primary", None)
            await save_session(session)
            await _start_name_or_traveller_flow(session, sender_wa_id, passengers, phone_number_id)
        elif reply_id == "upd_more":
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


async def go_back_one_step(wa_id: str, phone_number_id: Optional[str]):
    session, flow = await _get_flow_state(wa_id)
    step = flow.get("step", "upd_menu")
    data = flow.get("data", {})
    passengers = session.get("api_data", {}).get("policy_passengers") or []

    async def _show_menu():
        flow["step"] = "upd_menu"
        await save_session(session)
        menu_rows = [
            {"id": "upd_opt_name",   "title": "👤 Name"},
            {"id": "upd_opt_email",  "title": "✉️ Email address"},
        ]
        if passengers:
            menu_rows.append({"id": "upd_opt_travellers", "title": "👥 Travellers"})
        menu_rows += [
            {"id": "upd_opt_bank",   "title": "🏦 Bank payout details"},
            {"id": "upd_opt_wallet", "title": "👛 Wallet payout"},
            {"id": "upd_opt_kyc",    "title": "🔒 KYC (Biometric ID)"},
        ]
        await _send_list(
            to=wa_id,
            header="✏️ Update your details",
            body="What would you like to update?",
            button_label="Select option",
            sections=[{"title": "Update your details", "rows": menu_rows}],
            phone_number_id=phone_number_id,
        )

    if step in ("upd_menu", "upd_done"):
        await _reset(session)
        from app.services.auto_reply_service import send_main_menu
        await send_main_menu(to=wa_id, phone_number_id=phone_number_id, wa_id=wa_id)
        return

    # Name input → back to traveller selection (if multi) or menu
    if step == "upd_name_input":
        if len(passengers) > 1:
            flow["step"] = "upd_travellers"
            await save_session(session)
            await _start_name_or_traveller_flow(session, wa_id, passengers, phone_number_id)
        else:
            await _show_menu()
        return

    # Traveller selection → back to menu
    if step == "upd_travellers":
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
            "🔍 Enter at least 3 characters of your\n"
            "bank name to search:\n\n_Example: Zen, GT, Wem_",
            phone_number_id)
        return

    # Bank: search → account number
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

    # Wallet: phone → provider select
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

    await _show_menu()
