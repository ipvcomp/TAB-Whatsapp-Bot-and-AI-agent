import logging
from typing import Optional

import app.services.ipurvey_service as ipurvey_service

from app.core.test_overrides import get_msisdn
from app.services.session_service import get_session, save_session, invalidate_policy_cache
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload

logger = logging.getLogger(__name__)

PAYMENT_FLOW_KEY = "payment_flow"
KYC_FLOW_KEY     = "kyc_flow"
BUY_COVER_FLOW_KEY = "buy_cover_flow"

COVER_PRICES = {
    "local_basic":   2500,
    "local_premium": 3500,
}
COVER_NAMES = {
    "local_basic":   "Local Travel Basic",
    "local_premium": "Local Travel Premium",
}

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

WALLET_OPTIONS = [
    {"id": "wallet_9psb",      "title": "9PSB"},
    {"id": "wallet_smartcash", "title": "SmartCash"},
    {"id": "wallet_opay",      "title": "OPay"},
]

BANKS_PER_PAGE = 8


def is_in_payment_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    return session.get("temp_data", {}).get(PAYMENT_FLOW_KEY, {}).get("active", False)


def _filter_banks(query: str) -> list:
    q = query.strip().lower()
    if not q:
        return NIGERIAN_BANKS
    matched   = [b for b in NIGERIAN_BANKS if q in b.lower()]
    unmatched = [b for b in NIGERIAN_BANKS if q not in b.lower()]
    return matched + unmatched if matched else NIGERIAN_BANKS


def _bank_pages(banks: list) -> list:
    return [banks[i:i + BANKS_PER_PAGE] for i in range(0, len(banks), BANKS_PER_PAGE)]



async def _get_flow_state(wa_id: str) -> tuple:
    session = await get_session(wa_id) or {}
    flow = session.setdefault("temp_data", {}).setdefault(PAYMENT_FLOW_KEY, {})
    return session, flow


_UTILITY = (
    "*Utility options:*\n"
    "0 ↩️ Back  |  9 🆘 Help  |  00 🏠 Main menu\n"
    "99 ❌ Cancel/Exit"
)


async def _send_text(to: str, body: str, phone_number_id: Optional[str]):
    await send_text_message(to=to, body=body, phone_number_id=phone_number_id, source="payment_flow")
    await send_text_message(to=to, body=_UTILITY, phone_number_id=phone_number_id, source="payment_flow")


async def _send_buttons(to: str, body: str, buttons: list, phone_number_id: Optional[str]):
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
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="payment_flow")
    await send_text_message(to=to, body=_UTILITY, phone_number_id=phone_number_id, source="payment_flow")


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
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="payment_flow")
    await send_text_message(to=to, body=_UTILITY, phone_number_id=phone_number_id, source="payment_flow")


async def _send_bank_page(wa_id: str, pages: list, page_idx: int, all_banks: list, phone_number_id: Optional[str]):
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
        rows.append({"id": f"bank_{idx}", "title": title})

    if page_idx < total_pages - 1:
        rows.append({"id": "bank_next", "title": f"→ Next (Page {page_idx+2}/{total_pages})"})
    if page_idx > 0:
        rows.append({"id": "bank_prev", "title": f"← Back (Page {page_idx}/{total_pages})"})
    rows.append({"id": "bank_search_again", "title": "↩️ Search again"})

    await _send_list(
        wa_id,
        f"Select Bank (Page {page_idx+1}/{total_pages})\n"
        f"Showing {start_num}-{end_num} of {total_banks} banks.",
        "Select bank",
        [{"title": "Select Main", "rows": rows}],
        phone_number_id,
    )


_PAYMENT_TYPE_MAP: dict[str, dict] = {
    "BANK_TRANSFER":          {"id": "pay_m_bank",   "title": "🏦 Bank Transfer"},
    "CARD":                   {"id": "pay_m_card",   "title": "💳 Card Payment"},
    "USSD":                   {"id": "pay_m_ussd",   "title": "📞 USSD"},
    "SMARTCASH_MOBILE_MONEY": {"id": "pay_m_wallet", "title": "📱 Mobile Money"},
    "MOBILE_MONEY":           {"id": "pay_m_wallet", "title": "📱 Mobile Money"},
}
_PAYMENT_TYPE_PRIORITY = [
    "BANK_TRANSFER", "CARD", "USSD", "SMARTCASH_MOBILE_MONEY", "MOBILE_MONEY"
]


async def _show_payment_summary(
    wa_id: str,
    session: dict,
    flow: dict,
    phone_number_id: Optional[str],
):
    data      = flow.get("data", {})
    amount    = data.get("pay_amount",    2500)
    cname     = data.get("pay_cover",     "Local Travel Basic")
    origin    = data.get("pay_origin",    "—")
    dest      = data.get("pay_dest",      "—")
    dep       = data.get("pay_dep_date",  "—")
    travelers = data.get("pay_travelers", 1)

    flow["step"] = "pay_method_choice"
    await save_session(session)

    # Fetch available payment types dynamically; fallback to BANK_TRANSFER only
    try:
        api_types = await ipurvey_service.get_payment_types(country="NG")
    except Exception:
        api_types = ["BANK_TRANSFER"]

    # Build buttons in priority order, max 3 (WhatsApp limit)
    seen_ids: set[str] = set()
    buttons: list[dict] = []
    for ptype in _PAYMENT_TYPE_PRIORITY:
        if ptype in api_types:
            btn = _PAYMENT_TYPE_MAP.get(ptype)
            if btn and btn["id"] not in seen_ids:
                buttons.append(btn)
                seen_ids.add(btn["id"])
            if len(buttons) == 3:
                break
    if not buttons:
        buttons = [{"id": "pay_m_bank", "title": "🏦 Bank Transfer"}]

    await _send_buttons(
        wa_id,
        f"🎫 *You're one step away from activating your cover*\n\n"
        f"🧾 PAYMENT SUMMARY\n"
        f"✈️ Policy      {cname}\n"
        f"✈️ Flight      {origin} → {dest}\n"
        f"📅 Date        {dep}\n"
        f"👥 Travellers  {travelers}\n"
        f"🔒 KYC         ✅ Verified\n"
        f"💰 Amount      ₦{amount:,}\n\n"
        f"Choose a payment method below:",
        buttons,
        phone_number_id,
    )


async def _send_success(
    wa_id: str,
    session: dict,
    flow: dict,
    amount: int,
    ref: str,
    policy: str,
    cname: str,
    phone_number_id: Optional[str],
):
    bc_data  = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {}).get("data", {})
    flight   = bc_data.get("flight_num", "—")
    date     = bc_data.get("date",       "—")
    name     = bc_data.get("name",       "—")

    data = flow.setdefault("data", {})
    data["pay_ref"]    = ref
    data["pay_policy"] = policy
    flow["step"] = "pay_success"
    await save_session(session)

    await _send_text(
        wa_id,
        f"✅ *Payment Successful!*\nYour cover is now active 🎉\n\n"
        f"🗓️ Policy No:   {policy}\n✈️ Flight:       {flight}\n"
        f"📅 Date:         {date}\n😊 Traveller:   *{name}*",
        phone_number_id,
    )
    await _send_buttons(
        wa_id,
        "📎 *Got your boarding pass? Upload it now 👍*\n\nWhat would you like to do next?",
        [
            {"id": "pay_upload_bp", "title": "⬆️ Upload boarding"},
            {"id": "pay_home",      "title": "🏠 Main menu"},
            {"id": "pay_new",       "title": "✈️ Buy new cover"},
        ],
        phone_number_id,
    )


async def _do_policy_submission(
    wa_id: str,
    session: dict,
) -> tuple[Optional[str], Optional[str]]:
    """Assemble all session data and call the real policy submission API.

    Returns (policy_ref, error_message).
    On success policy_ref is the reference string returned by the API.
    On failure policy_ref is None and error_message describes what went wrong.
    """
    bc_data  = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {}).get("data", {})
    kyc_data = session.get("temp_data", {}).get(KYC_FLOW_KEY,       {}).get("data", {})
    pay_data = session.get("temp_data", {}).get(PAYMENT_FLOW_KEY,   {}).get("data", {})
    api_data = session.get("api_data", {})

    msisdn = get_msisdn(wa_id)

    raw_name = bc_data.get("name", "")
    parts    = raw_name.strip().split(None, 1)
    fn       = parts[0] if parts else raw_name
    ln       = parts[1] if len(parts) > 1 else ""

    dep_raw = bc_data.get("depart_airport", "").split("—")[0].strip().split()
    dep_code = dep_raw[0] if dep_raw else ""
    arr_raw = bc_data.get("arrive_airport", "").split("—")[0].strip().split()
    arr_code = arr_raw[0] if arr_raw else ""

    trip_raw  = bc_data.get("trip_type", "One-way 🗺️")
    trip_type = "RETURN" if "return" in trip_raw.lower() else "ONE_WAY"

    dep_date = bc_data.get("date", "")
    arr_date = dep_date if trip_type == "ONE_WAY" else dep_date

    quotes     = api_data.get("quotes", [])
    product_id = None
    cover_name = bc_data.get("cover", "")
    for q in (quotes or []):
        q_name = q.get("name") or q.get("productName") or ""
        if q_name and q_name == cover_name:
            product_id = q.get("productId") or q.get("id")
            break
    if not product_id and quotes:
        product_id = quotes[0].get("productId") or quotes[0].get("id")

    bp_bytes = session.get("boarding_pass_bytes")
    _bp_fn_raw = session.get("boarding_pass_filename")
    bp_filename = str(_bp_fn_raw) if _bp_fn_raw else "boarding_pass.jpg"

    return await ipurvey_service.submit_policy(
        msisdn=msisdn,
        product_id=product_id,
        policy_id=api_data.get("policy_id"),
        first_name=fn,
        last_name=ln,
        email=bc_data.get("email", ""),
        id_type=kyc_data.get("kyc_method", "BVN"),
        id_number=kyc_data.get("kyc_id", ""),
        booking_ref=bc_data.get("booking_ref", ""),
        flight_num=bc_data.get("flight_num", ""),
        trip_type=trip_type,
        dep_airport=dep_code,
        arr_airport=arr_code,
        dep_date=dep_date,
        dep_time=bc_data.get("depart_time", ""),
        arr_date=arr_date,
        arr_time=bc_data.get("arrive_time", ""),
        bank_code=pay_data.get("pay_bank_code", ""),
        account_number=pay_data.get("pay_user_acct", ""),
        account_name=f"{fn} {ln}".strip(),
        payout_method_id=api_data.get("payout_method_id"),
        boarding_pass_bytes=bp_bytes if isinstance(bp_bytes, bytes) else None,
        boarding_pass_filename=bp_filename,
    )


async def _submit_and_confirm(
    wa_id: str,
    session: dict,
    flow: dict,
    amount: int,
    ref: str,
    cname: str,
    phone_number_id: Optional[str],
):
    """Call the policy submission API, then show success or a retry prompt."""
    await send_text_message(
        to=wa_id,
        body="⏳ *Submitting your policy...*\n_Please wait a moment_",
        phone_number_id=phone_number_id,
        source="payment_flow",
    )

    policy_ref, error = await _do_policy_submission(wa_id, session)

    if policy_ref:
        flow.setdefault("data", {})["pay_policy"] = policy_ref
        session["active_policy_code"]   = policy_ref
        session["active_policy_status"] = "submitted"
        api_data = session.get("api_data", {})
        if api_data.get("policy_id"):
            session["active_policy_id"] = api_data["policy_id"]
        invalidate_policy_cache(session)
        await save_session(session)
        await _send_success(wa_id, session, flow, amount, ref, policy_ref, cname, phone_number_id)
    else:
        err_msg = error or "Unknown error"
        data = flow.setdefault("data", {})
        data["submission_error"]  = err_msg
        data["submission_ref"]    = ref
        data["submission_cname"]  = cname
        data["submission_amount"] = amount
        flow["step"] = "pay_submit_retry"
        await save_session(session)
        await send_text_message(
            to=wa_id,
            body=(
                f"⚠️ *Policy Submission Failed*\n\n"
                f"We were unable to submit your policy at this time.\n\n"
                f"*Error:* {err_msg}\n\n"
                "Please tap *Retry* to try again, or contact support if the problem persists."
            ),
            phone_number_id=phone_number_id,
            source="payment_flow",
        )
        await _send_buttons(
            wa_id,
            "What would you like to do?",
            [
                {"id": "pay_retry_submit", "title": "🔄 Retry Submission"},
                {"id": "pay_home",         "title": "🏠 Main menu"},
            ],
            phone_number_id,
        )


async def start_payment_flow(
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session = await get_session(wa_id) or {}
    bc_data = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {}).get("data", {})

    cover_raw = bc_data.get("cover", "local_basic")
    cover_key = "local_premium" if "premium" in cover_raw.lower() else "local_basic"
    # Use actual premium from session quote data; fall back to static map
    _cover_price_raw = bc_data.get("cover_price")
    if _cover_price_raw:
        try:
            amount = int(float(_cover_price_raw))
        except (TypeError, ValueError):
            amount = COVER_PRICES.get(cover_key, 2500)
    else:
        amount = COVER_PRICES.get(cover_key, 2500)
    cname  = cover_raw if cover_raw and cover_raw != "local_basic" else COVER_NAMES.get(cover_key, "Local Travel Basic")
    origin    = bc_data.get("depart_airport", "—").split("—")[0].strip()
    dest      = bc_data.get("arrive_airport",  "—").split("—")[0].strip()
    dep_date  = bc_data.get("date",  "—")
    travelers_list = bc_data.get("travelers", [])
    travelers = len(travelers_list) if travelers_list else 1

    session.setdefault("temp_data", {})[PAYMENT_FLOW_KEY] = {
        "active": True,
        "step":   "pay_payout_options",
        "data": {
            "pay_amount":    amount,
            "pay_cover":     cname,
            "pay_origin":    origin,
            "pay_dest":      dest,
            "pay_dep_date":  dep_date,
            "pay_travelers": travelers,
        },
    }
    session["temp_data"].get(KYC_FLOW_KEY, {}).update({"active": False})
    if "user_id" not in session:
        session["user_id"] = wa_id
    await save_session(session)

    await _send_buttons(
        wa_id,
        "Payout options\n\nChoose how you would like to receive money for any future payouts:",
        [
            {"id": "pay_bank",          "title": "🏦 Bank transfer"},
            {"id": "pay_wallet_payout", "title": "👛 Wallet"},
        ],
        phone_number_id,
    )


async def handle_payment_flow(
    message,
    sender_wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session, flow = await _get_flow_state(sender_wa_id)
    step  = flow.get("step", "pay_payout_options")
    data  = flow.setdefault("data", {})

    amount    = data.get("pay_amount",    2500)
    cname     = data.get("pay_cover",     "Local Travel Basic")
    origin    = data.get("pay_origin",    "—")
    dest      = data.get("pay_dest",      "—")
    dep       = data.get("pay_dep_date",  "—")
    travelers = data.get("pay_travelers", 1)

    text = ""
    if message.type == "text" and message.text:
        text = message.text.body.strip()

    reply_id = None
    if message.type == "interactive" and message.interactive:
        inter = message.interactive
        if isinstance(inter, dict):
            br = inter.get("button_reply") or inter.get("list_reply")
            reply_id = br.get("id") if br else None
        else:
            br = getattr(inter, "button_reply", None) or getattr(inter, "list_reply", None)
            if br:
                reply_id = br.get("id") if isinstance(br, dict) else getattr(br, "id", None)

    # ── Payout options ────────────────────────────────────────────────────────
    if step == "pay_payout_options":
        if reply_id == "pay_bank":
            flow["step"] = "pay_acct_number"
            await save_session(session)
            await _send_text(sender_wa_id,
                "🏦 *Bank Transfer*\n\nPlease enter your 10-digit account number for future payouts:\n\n"
                "_Example: 0123456789_",
                phone_number_id)
        elif reply_id == "pay_wallet_payout":
            flow["step"] = "pay_wallet_payout_select"
            await save_session(session)
            await _send_buttons(sender_wa_id, "👛 *Wallet*\n\nChoose your wallet provider:",
                [{"id": w["id"], "title": w["title"]} for w in WALLET_OPTIONS], phone_number_id)
        else:
            await start_payment_flow(sender_wa_id, phone_number_id)

    # ── Bank — account number ─────────────────────────────────────────────────
    elif step == "pay_acct_number":
        acct = text.replace(" ", "")
        if acct.isdigit() and 9 <= len(acct) <= 11:
            data["pay_user_acct"] = acct
            flow["step"] = "pay_bank_search"
            await save_session(session)
            await _send_text(sender_wa_id,
                "Please enter at least the first 3 characters of your bank name:\n\n"
                "_Example: Zen, Wem, GT_",
                phone_number_id)
        else:
            await _send_text(sender_wa_id,
                "⚠️ Please enter a valid *10-digit account number*:\n_Example: 0123456789_",
                phone_number_id)

    # ── Bank — search ─────────────────────────────────────────────────────────
    elif step == "pay_bank_search":
        query = text
        if len(query) >= 2:
            banks = _filter_banks(query)
            pages = _bank_pages(banks)
            data["pay_bank_list"] = banks
            data["pay_bank_page"] = 0
            flow["step"] = "pay_bank_select"
            await save_session(session)
            await _send_bank_page(sender_wa_id, pages, 0, banks, phone_number_id)
        else:
            await _send_text(sender_wa_id,
                "⚠️ Enter *at least 2 characters*.\n_Example: Zen, GT_",
                phone_number_id)

    # ── Bank — select ─────────────────────────────────────────────────────────
    elif step == "pay_bank_select":
        banks    = data.get("pay_bank_list", NIGERIAN_BANKS)
        pages    = _bank_pages(banks)
        cur_page = data.get("pay_bank_page", 0)

        if reply_id == "bank_next":
            nxt = cur_page + 1
            if nxt < len(pages):
                data["pay_bank_page"] = nxt
                await save_session(session)
                await _send_bank_page(sender_wa_id, pages, nxt, banks, phone_number_id)
        elif reply_id == "bank_prev":
            prv = cur_page - 1
            if prv >= 0:
                data["pay_bank_page"] = prv
                await save_session(session)
                await _send_bank_page(sender_wa_id, pages, prv, banks, phone_number_id)
        elif reply_id == "bank_search_again":
            flow["step"] = "pay_bank_search"
            await save_session(session)
            await _send_text(sender_wa_id,
                "Enter at least 3 characters of your bank name:\n_Example: Zen, GT_",
                phone_number_id)
        elif reply_id and reply_id.startswith("bank_"):
            try:
                idx = int(reply_id.split("_")[1])
                if 0 <= idx < len(banks):
                    bank_name = banks[idx]
                    data["pay_bank_name"] = bank_name
                    bank_code = ipurvey_service.get_bank_code(bank_name)
                    data["pay_bank_code"] = bank_code
                    api_data   = session.get("api_data", {})
                    user_id    = api_data.get("user_id") or ""
                    account_no = data.get("pay_user_acct", "")
                    _bc = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {}).get("data", {})
                    account_name = _bc.get("name") or ""
                    if user_id:
                        try:
                            payout_result = await ipurvey_service.create_payout_method_bank(
                                user_id=user_id,
                                account_number=account_no,
                                account_name=account_name,
                                bank_code=bank_code,
                                bank_name=bank_name,
                            )
                            if payout_result and isinstance(payout_result, dict):
                                pm_id = payout_result.get("id") or payout_result.get("payoutMethodId")
                                if pm_id:
                                    session.setdefault("api_data", {})["payout_method_id"] = pm_id
                        except Exception as exc:
                            logger.error(f"[payment] create_payout_method_bank failed: {exc}")
                    await save_session(session)
                    await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)
            except (ValueError, IndexError):
                await _send_text(sender_wa_id, "⚠️ Please select a bank from the list.", phone_number_id)
        else:
            await _send_text(sender_wa_id, "⚠️ Please select a bank from the list.", phone_number_id)

    # ── Wallet payout — select ────────────────────────────────────────────────
    elif step == "pay_wallet_payout_select":
        wallet_map = {"wallet_9psb": "9PSB", "wallet_smartcash": "SmartCash", "wallet_opay": "OPay"}
        if reply_id in wallet_map:
            data["pay_wallet_type"] = wallet_map[reply_id]
            flow["step"] = "pay_wallet_payout_phone"
            await save_session(session)
            await _send_text(sender_wa_id,
                "Enter the phone number linked to your wallet:\n\n_Example: 08012345678_",
                phone_number_id)
        else:
            await start_payment_flow(sender_wa_id, phone_number_id)

    # ── Wallet payout — phone ─────────────────────────────────────────────────
    elif step == "pay_wallet_payout_phone":
        digits = text.replace(" ", "").replace("-", "")
        if digits.isdigit() and len(digits) >= 10:
            data["pay_wallet_phone"] = digits
            wallet_type  = data.get("pay_wallet_type", "")
            api_data     = session.get("api_data", {})
            user_id      = api_data.get("user_id") or ""
            _bc_w = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {}).get("data", {})
            account_name = _bc_w.get("name") or ""
            if user_id:
                try:
                    payout_result = await ipurvey_service.create_payout_method_wallet(
                        user_id=user_id,
                        phone_number=digits,
                        account_name=account_name,
                        network=wallet_type,
                    )
                    if payout_result and isinstance(payout_result, dict):
                        pm_id = payout_result.get("id") or payout_result.get("payoutMethodId")
                        if pm_id:
                            session.setdefault("api_data", {})["payout_method_id"] = pm_id
                except Exception as exc:
                    logger.error(f"[payment] create_payout_method_wallet failed: {exc}")
            await save_session(session)
            await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)
        else:
            await _send_text(sender_wa_id,
                "⚠️ Enter a valid *phone number*:\n_Example: 08012345678_",
                phone_number_id)

    # ── Payment method choice ─────────────────────────────────────────────────
    elif step == "pay_method_choice":
        if reply_id == "pay_m_bank":
            policy_id  = session.get("api_data", {}).get("policy_id")
            api_ref    = None
            payment_id = None
            initiate_error = False
            if policy_id:
                try:
                    pay_result = await ipurvey_service.initiate_payment(
                        policy_id=policy_id,
                        payment_method="BANK_TRANSFER",
                    )
                    if pay_result and isinstance(pay_result, dict):
                        api_ref = (
                            pay_result.get("reference")
                            or pay_result.get("paymentReference")
                            or pay_result.get("paymentRef")
                        )
                        payment_id = pay_result.get("paymentId") or pay_result.get("id")
                        bank_account_name = (
                            pay_result.get("accountName")
                            or pay_result.get("account_name")
                            or pay_result.get("accountHolderName")
                            or pay_result.get("beneficiaryName")
                        )
                        bank_account_number = (
                            pay_result.get("accountNumber")
                            or pay_result.get("account_number")
                            or pay_result.get("nuban")
                        )
                        bank_name = (
                            pay_result.get("bankName")
                            or pay_result.get("bank_name")
                            or pay_result.get("bank")
                        )
                        api_data = session.setdefault("api_data", {})
                        if payment_id:
                            api_data["payment_id"] = payment_id
                        api_data.pop("bank_account_name", None)
                        api_data.pop("bank_account_number", None)
                        api_data.pop("bank_name", None)
                        if bank_account_name:
                            api_data["bank_account_name"] = bank_account_name
                        if bank_account_number:
                            api_data["bank_account_number"] = bank_account_number
                        if bank_name:
                            api_data["bank_name"] = bank_name
                    else:
                        initiate_error = True
                except Exception as exc:
                    logger.error(f"[payment] initiate_payment bank failed: {exc}")
                    initiate_error = True
            else:
                initiate_error = True

            # api_ref may be None — the payment API is event-driven (data:null on success).
            # Do NOT treat missing api_ref as an error; HTTP 200 = payment event published.

            if initiate_error:
                await save_session(session)
                await send_text_message(
                    to=sender_wa_id,
                    body=(
                        "⚠️ *Payment initiation failed*\n\n"
                        "We could not start the bank transfer process. "
                        "Please try again or contact support."
                    ),
                    phone_number_id=phone_number_id,
                    source="payment_flow",
                )
                await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)
                return

            ref_display = api_ref or "Check SMS/email for reference"
            data["pay_m_bank_ref"] = ref_display
            flow["step"] = "pay_m_bank_pending"
            await save_session(session)
            _api_data = session.get("api_data", {})
            _bank_name    = _api_data.get("bank_name", "")
            _acct_name    = _api_data.get("bank_account_name", "")
            _acct_number  = _api_data.get("bank_account_number", "")
            if _bank_name or _acct_name or _acct_number:
                _bank_details = (
                    f"Bank             {_bank_name or 'N/A'}\n"
                    f"Account Name     {_acct_name or 'N/A'}\n"
                    f"Account No.      {_acct_number or 'N/A'}"
                )
            else:
                _bank_details = "Your payment is being processed. You will receive transfer details via SMS or email shortly."
            await _send_buttons(sender_wa_id,
                f"🏦 *Bank Transfer Initiated*\n\n"
                f"Amount: *₦{amount:,}*\n\n"
                f"{_bank_details}\n\n"
                f"🔑 Reference: {ref_display}\n\nOnce payment is complete, tap below:",
                [
                    {"id": "pay_m_done",    "title": "✅ I have paid"},
                    {"id": "pay_m_refresh", "title": "🔄 Refresh status"},
                ],
                phone_number_id,
            )

        elif reply_id in ("pay_m_card", "pay_m_wallet", "pay_m_ussd"):
            await send_text_message(
                to=sender_wa_id,
                body=(
                    "⚠️ *Payment method not currently available*\n\n"
                    "Bank transfer is the only supported payment method at this time.\n\n"
                    "Please select *Bank Transfer* to continue."
                ),
                phone_number_id=phone_number_id,
                source="payment_flow",
            )
            await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)
        else:
            await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)

    # ── Bank pending ──────────────────────────────────────────────────────────
    elif step == "pay_m_bank_pending":
        ref = data.get("pay_m_bank_ref", "TA000000")
        _pending_api_data    = session.get("api_data", {})
        _p_bank_name         = _pending_api_data.get("bank_name", "")
        _p_acct_name         = _pending_api_data.get("bank_account_name", "")
        _p_acct_number       = _pending_api_data.get("bank_account_number", "")
        if _p_bank_name or _p_acct_name or _p_acct_number:
            _p_bank_details = (
                f"Bank             {_p_bank_name or 'N/A'}\n"
                f"Account Name     {_p_acct_name or 'N/A'}\n"
                f"Account No.      {_p_acct_number or 'N/A'}"
            )
        else:
            _p_bank_details = "Please contact support for account details."
        if reply_id in ("pay_m_done", "pay_m_refresh"):
            payment_confirmed = False
            policy_ref = None
            policy_id  = session.get("api_data", {}).get("policy_id")
            payment_id = session.get("api_data", {}).get("payment_id")
            if policy_id:
                msisdn = get_msisdn(sender_wa_id)
                try:
                    status_result = await ipurvey_service.get_payment_status(
                        policy_id=policy_id, msisdn=msisdn
                    )
                    if status_result and isinstance(status_result, dict):
                        # API returns "paymentStatus" field (not "status")
                        status_val = (
                            status_result.get("paymentStatus")
                            or status_result.get("status")
                            or ""
                        ).upper()
                        if status_val in ("PAID", "SUCCESS", "COMPLETED", "CONFIRMED"):
                            payment_confirmed = True
                            policy_ref = (
                                status_result.get("policyCode")
                                or status_result.get("policyReference")
                                or status_result.get("policyNumber")
                            )
                except Exception as exc:
                    logger.error(f"[payment] get_payment_status failed: {exc}")
            if payment_confirmed:
                await _submit_and_confirm(sender_wa_id, session, flow, amount, ref, cname, phone_number_id)
            else:
                await _send_buttons(sender_wa_id,
                    f"🏦 *Bank Transfer*\n\n"
                    f"⏳ *Payment not yet confirmed*\n\nPlease transfer *₦{amount:,}* to:\n\n"
                    f"{_p_bank_details}\n\n"
                    f"🔑 Reference: {ref}\n\nAfter payment, tap below:",
                    [
                        {"id": "pay_m_done",    "title": "✅ I have paid"},
                        {"id": "pay_m_refresh", "title": "🔄 Refresh status"},
                    ],
                    phone_number_id,
                )
        else:
            await _send_buttons(sender_wa_id,
                f"🏦 *Bank Transfer*\n\n"
                f"Please transfer *₦{amount:,}* to:\n\n"
                f"{_p_bank_details}\n\n"
                f"🔑 Reference: {ref}\n\nAfter payment, tap below:",
                [
                    {"id": "pay_m_done",    "title": "✅ I have paid"},
                    {"id": "pay_m_refresh", "title": "🔄 Refresh status"},
                ],
                phone_number_id,
            )

    # ── Legacy simulated payment steps — redirect to payment summary ──────────
    elif step in (
        "pay_m_card_number", "pay_m_card_expiry", "pay_m_card_cvv", "pay_m_card_otp",
        "pay_m_wallet_select", "pay_m_wallet_phone", "pay_m_wallet_otp",
        "pay_m_ussd_confirm",
    ):
        await send_text_message(
            to=sender_wa_id,
            body=(
                "⚠️ *Payment method not currently available*\n\n"
                "Only Bank Transfer is supported. Please select it to continue."
            ),
            phone_number_id=phone_number_id,
            source="payment_flow",
        )
        flow["step"] = "pay_method_choice"
        await save_session(session)
        await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)

    # ── Post-success ──────────────────────────────────────────────────────────
    elif step == "pay_success":
        pol = data.get("pay_policy", "—")
        ref = data.get("pay_ref",    "—")
        if reply_id == "pay_view_policy":
            await _send_text(sender_wa_id,
                f"📄 *Your Policy Document*\n\n"
                f"🗓️ Policy No:  *{pol}*\n🔑 Reference:  *{ref}*\n"
                f"💰 Amount:     *₦{amount:,}*\n🛡️ Cover:      *{cname}*\n\n"
                f"Your policy is active. TravelAssist will monitor your flight automatically.",
                phone_number_id)
        elif reply_id == "pay_upload_bp":
            from app.services.bp_link_flow_service import start_bp_link_flow
            await start_bp_link_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        elif reply_id == "pay_home":
            session["temp_data"][PAYMENT_FLOW_KEY] = {}
            session["temp_data"][BUY_COVER_FLOW_KEY] = {}
            session["temp_data"][KYC_FLOW_KEY] = {}
            await save_session(session)
            from app.services.auto_reply_service import send_main_menu
            await send_main_menu(to=sender_wa_id, phone_number_id=phone_number_id)
        elif reply_id == "pay_new":
            session["temp_data"][PAYMENT_FLOW_KEY] = {}
            session["temp_data"][BUY_COVER_FLOW_KEY] = {}
            session["temp_data"][KYC_FLOW_KEY] = {}
            await save_session(session)
            from app.services.buy_cover_flow_service import start_buy_cover_flow
            await start_buy_cover_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        else:
            bc_data = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {}).get("data", {})
            flight  = bc_data.get("flight_num", "—")
            date    = bc_data.get("date",       "—")
            name    = bc_data.get("name",       "—")
            await _send_text(sender_wa_id,
                f"✅ *Payment Successful!*\nYour cover is now active 🎉\n\n"
                f"🗓️ Policy No:   {pol}\n✈️ Flight:       {flight}\n"
                f"📅 Date:         {date}\n😊 Traveller:   *{name}*",
                phone_number_id)
            await _send_buttons(sender_wa_id,
                "📎 *Got your boarding pass? Upload it now 👍*\n\nWhat would you like to do next?",
                [
                    {"id": "pay_upload_bp", "title": "⬆️ Upload boarding"},
                    {"id": "pay_home",      "title": "🏠 Main menu"},
                    {"id": "pay_new",       "title": "✈️ Buy new cover"},
                ],
                phone_number_id,
            )

    # ── Submission retry ──────────────────────────────────────────────────────
    elif step == "pay_submit_retry":
        err_ref    = data.get("submission_ref", "—")
        err_cname  = data.get("submission_cname", "—")
        err_amount = data.get("submission_amount", amount)
        err_msg    = data.get("submission_error", "Unknown error")
        if reply_id == "pay_retry_submit":
            await _submit_and_confirm(sender_wa_id, session, flow, err_amount, err_ref, err_cname, phone_number_id)
        elif reply_id == "pay_home":
            session["temp_data"][PAYMENT_FLOW_KEY] = {}
            session["temp_data"][BUY_COVER_FLOW_KEY] = {}
            session["temp_data"][KYC_FLOW_KEY] = {}
            await save_session(session)
            from app.services.auto_reply_service import send_main_menu
            await send_main_menu(to=sender_wa_id, phone_number_id=phone_number_id)
        else:
            await send_text_message(
                to=sender_wa_id,
                body=(
                    f"⚠️ *Policy Submission Failed*\n\n"
                    f"*Error:* {err_msg}\n\n"
                    "Tap Retry to try again or go to the main menu:"
                ),
                phone_number_id=phone_number_id,
                source="payment_flow",
            )
            await _send_buttons(
                sender_wa_id,
                "What would you like to do?",
                [
                    {"id": "pay_retry_submit", "title": "🔄 Retry Submission"},
                    {"id": "pay_home",         "title": "🏠 Main menu"},
                ],
                phone_number_id,
            )

    # ── Catch-all ─────────────────────────────────────────────────────────────
    else:
        session["temp_data"][PAYMENT_FLOW_KEY] = {}
        await save_session(session)
        from app.services.auto_reply_service import send_main_menu
        await send_main_menu(to=sender_wa_id, phone_number_id=phone_number_id)
