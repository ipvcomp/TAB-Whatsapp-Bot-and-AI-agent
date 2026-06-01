import logging
from typing import Optional

import app.services.ipurvey_service as ipurvey_service

from app.core.test_overrides import get_msisdn
from app.services.llm_service import call_extract, call_generic, call_policy_flow_validate
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

WALLET_OPTIONS = [
    {"id": "wallet_9psb",      "title": "9PSB"},
    {"id": "wallet_smartcash", "title": "SmartCash"},
    {"id": "wallet_opay",      "title": "OPay"},
]

def is_in_payment_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    return session.get("temp_data", {}).get(PAYMENT_FLOW_KEY, {}).get("active", False)



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


async def _send_bank_results(wa_id: str, banks: list, phone_number_id: Optional[str]):
    """Display dynamic bank search results (airport-style, no pagination)."""
    rows = []
    for idx, bank in enumerate(banks[:9]):
        title = f"🏦 {bank['name']}"
        if len(title) > 24:
            title = title[:23] + "…"
        rows.append({"id": f"bank_{idx}", "title": title})
    rows.append({"id": "bank_search_again", "title": "🔍 Search again"})

    await _send_list(
        wa_id,
        "🔍 *We found some banks*\n\nNot seeing your bank? You can search again.",
        "Select bank",
        [{"title": "Banks", "rows": rows}],
        phone_number_id,
    )


async def _initiate_payment_with_link_retry(
    session: dict,
    policy_id: str,
    payment_method: str,
) -> Optional[dict]:
    """Call initiate_payment and, if the API returns 400 'User not linked',
    automatically link the user to the policy and retry once."""
    pay_result = await ipurvey_service.initiate_payment(
        policy_id=policy_id,
        payment_method=payment_method,
    )
    if pay_result and isinstance(pay_result, dict) and pay_result.get("_error") == "user_not_linked":
        user_id = session.get("api_data", {}).get("user_id")
        if user_id:
            logger.warning(
                f"[payment] user_not_linked on initiate_payment — "
                f"auto-linking user_id='{user_id}' to policy_id='{policy_id}' and retrying"
            )
            linked = await ipurvey_service.link_user_to_policy(policy_id, user_id)
            if linked:
                pay_result = await ipurvey_service.initiate_payment(
                    policy_id=policy_id,
                    payment_method=payment_method,
                )
            else:
                logger.error(f"[payment] link_user_to_policy failed — cannot retry initiate_payment")
                return None
        else:
            logger.error(f"[payment] user_not_linked but no user_id in session — cannot auto-link")
            return None
    if pay_result and isinstance(pay_result, dict) and pay_result.get("_error"):
        return None
    return pay_result


def _get_trip_details(session: dict, data: dict, ref: str) -> dict:
    """Pull policy/trip info for the payment status screens."""
    bc_data  = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {}).get("data", {})
    api_data = session.get("api_data", {})
    return {
        "pol_ref":   api_data.get("policy_code") or api_data.get("policy_id") or ref or "—",
        "flight":    bc_data.get("flight_num")   or data.get("pay_flight",   "—"),
        "date":      bc_data.get("date")          or data.get("pay_dep_date", "—"),
        "traveller": bc_data.get("name")          or data.get("pay_traveller","—"),
    }


async def _show_payment_pending_screen(
    wa_id: str, session: dict, data: dict, ref: str, phone_number_id: Optional[str]
):
    """'Payment Processing is Pending' screen (per design mockup)."""
    d = _get_trip_details(session, data, ref)
    await _send_buttons(
        wa_id,
        (
            "🕐 *Payment Processing is Pending*\n\n"
            "Your payment is being processed.\n"
            "We'll notify you once it's completed. 🔔\n\n"
            f"📋 *Policy No:*    {d['pol_ref']}\n"
            f"✈️ *Flight:*         {d['flight']}\n"
            f"📅 *Date:*           {d['date']}\n"
            f"🧑 *Traveller:*    {d['traveller']}\n\n"
            "ℹ️ Once the payment is completed, we will notify you "
            "and you can continue with your cover purchase."
        ),
        [{"id": "pay_m_refresh", "title": "🔄 Refresh status"}],
        phone_number_id,
    )


async def _show_payment_failed_screen(
    wa_id: str, session: dict, data: dict, ref: str, phone_number_id: Optional[str]
):
    """'Payment Not Successful' screen (per design mockup)."""
    d = _get_trip_details(session, data, ref)
    await _send_buttons(
        wa_id,
        (
            "❌ *Payment Not Successful*\n\n"
            "We couldn't complete your payment 😟\n\n"
            f"📋 *Policy No:*    {d['pol_ref']}\n"
            f"✈️ *Flight:*         {d['flight']}\n"
            f"📅 *Date:*           {d['date']}\n"
            f"🧑 *Traveller:*    {d['traveller']}\n\n"
            "⚠️ Your policy cannot be activated without completing the payment.\n"
            "*No payment, no cover will be in place.*"
        ),
        [{"id": "pay_m_done", "title": "🔄 Try again"}],
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

    # Build rows in priority order — use list (supports up to 10, no WhatsApp button cap)
    seen_ids: set[str] = set()
    rows: list[dict] = []
    for ptype in _PAYMENT_TYPE_PRIORITY:
        if ptype in api_types:
            btn = _PAYMENT_TYPE_MAP.get(ptype)
            if btn and btn["id"] not in seen_ids:
                rows.append({"id": btn["id"], "title": btn["title"]})
                seen_ids.add(btn["id"])
    if not rows:
        rows = [{"id": "pay_m_bank", "title": "🏦 Bank Transfer"}]

    await _send_list(
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
        "Select method",
        [{"title": "Payment Methods", "rows": rows}],
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
        f"✅ *Payment Successful!*\n"
        f"_Your cover is now active 🎉_\n"
        f"──────────────────────────\n"
        f"🗂️ *Policy No:* {policy}\n"
        f"✈️ *Flight:* {flight}\n"
        f"📅 *Date:* {date}\n"
        f"🧡 *Traveller:* {name}",
        phone_number_id,
    )
    await _send_list(
        wa_id,
        "🖇️ *Got your boarding pass handy? Upload it now* 👍\n"
        "If not, no worries — you can upload it later. "
        "We'll just need it before any payout.\n\n"
        "What would you like to do next?",
        "Choose an option",
        [
            {
                "title": "Next steps",
                "rows": [
                    {"id": "pay_view_doc",  "title": "📄 View policy document"},
                    {"id": "pay_upload_bp", "title": "📤 Upload boarding pass"},
                    {"id": "pay_home",      "title": "🏠 Main menu"},
                ],
            }
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

    # Fetch available payout method types; only show Wallet when API returns a wallet type
    try:
        payout_types = await ipurvey_service.get_payout_method_types(country="NG")
    except Exception:
        payout_types = ["BANK_ACCOUNT"]

    _WALLET_PAYOUT_TYPES = {"WALLET", "MOBILE_WALLET", "MOBILE_MONEY", "VIRTUAL_WALLET"}
    payout_buttons = [{"id": "pay_bank", "title": "🏦 Bank transfer"}]
    if any(pt in _WALLET_PAYOUT_TYPES for pt in payout_types):
        payout_buttons.append({"id": "pay_wallet_payout", "title": "👛 Wallet"})

    await _send_buttons(
        wa_id,
        "Payout options\n\nChoose how you would like to receive money for any future payouts:",
        payout_buttons,
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
        if not reply_id and text:
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="payout_method",
                question_asked="Choose how you would like to receive money for future payouts: Bank Transfer or Wallet.",
                user_response=text,
                expected_format="text",
            )
            if llm_result and llm_result.get("is_valid") and llm_result.get("extracted_value"):
                ev = str(llm_result["extracted_value"]).lower()
                if any(k in ev for k in ("bank", "transfer", "account", "bank transfer")):
                    reply_id = "pay_bank"
                elif any(k in ev for k in ("wallet", "mobile", "9psb", "opay", "smartcash", "mobile money")):
                    reply_id = "pay_wallet_payout"
            if not reply_id:
                await start_payment_flow(sender_wa_id, phone_number_id)
                return
        if reply_id == "pay_bank":
            flow["step"] = "pay_acct_number"
            await save_session(session)
            await _send_text(sender_wa_id,
                "🏦 *Bank Transfer*\n\nPlease enter your 10-digit account number:\n\n"
                "_Example: 0123456789_",
                phone_number_id)
        elif reply_id == "pay_wallet_payout":
            flow["step"] = "pay_wallet_payout_phone"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                "👛 *Wallet*\n\nEnter the phone number linked to your wallet:\n\n"
                "_Example: 08012345678_",
                phone_number_id,
            )
        else:
            await start_payment_flow(sender_wa_id, phone_number_id)

    # ── Bank — account number ─────────────────────────────────────────────────
    elif step == "pay_acct_number":
        _pay_q = (text or "").lower().strip()
        _is_q = (
            "?" in _pay_q
            or any(_pay_q.startswith(s) for s in (
                "why ", "what ", "how ", "who ", "is this ", "do you ",
                "why do", "what is", "tell me", "explain ",
            ))
            or (len(_pay_q.split()) >= 3 and not any(c.isdigit() for c in _pay_q))
        )
        if _is_q:
            try:
                _lr = await call_generic(
                    user_id=sender_wa_id, phone_number=sender_wa_id,
                    message=text, user_name="", current_node="pay_acct_number",
                )
                if _lr:
                    _db = _lr.get("data") if isinstance(_lr.get("data"), dict) else {}
                    _ans = _lr.get("response") or _db.get("response") or _db.get("message") or ""
                    logger.info(f"[LLM_GENERIC] node=pay_acct_number user={sender_wa_id} input={text!r} answer={_ans!r}")
                    if _ans:
                        await _send_text(sender_wa_id, _ans, phone_number_id)
                else:
                    logger.warning(f"[LLM_GENERIC] node=pay_acct_number user={sender_wa_id} input={text!r} → no response")
            except Exception as _exc:
                logger.error(f"[LLM_GENERIC] node=pay_acct_number user={sender_wa_id} error: {_exc}")
            await _send_text(
                sender_wa_id,
                "🏦 Please enter your *10-digit bank account number* to continue.\n\n_Example: 0123456789_",
                phone_number_id,
            )
            return
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
        query = text.strip()
        if len(query) < 3:
            await _send_text(sender_wa_id,
                "⚠️ Enter *at least 3 characters* of your bank name:\n_Example: Zen, GT, Wem_",
                phone_number_id)
            return
        banks = await ipurvey_service.search_banks(query)
        if not banks:
            logger.info(f"[bank_search] No results for '{query}', calling LLM to extract clean search term")
            llm_resp = await call_policy_flow_validate(
                step_id=23,
                context="Bank name search",
                field_name="bank_name",
                question_asked="Please enter at least the first 3 characters of your bank name.",
                user_response=query,
                step_type="free_text",
                expected_format="Bank name or abbreviation (3+ characters)",
            )
            extracted = (llm_resp or {}).get("extracted_value", "")
            logger.info(
                f"[bank_search] LLM result: is_valid={( llm_resp or {}).get('is_valid')}, "
                f"extracted='{extracted}', "
                f"guidance='{(llm_resp or {}).get('guidance_message')}'"
            )
            if llm_resp and llm_resp.get("is_valid") and extracted and len(extracted.strip()) >= 3:
                logger.info(f"[bank_search] Retrying with LLM extracted term '{extracted}'")
                banks = await ipurvey_service.search_banks(extracted.strip())
            if not banks:
                await _send_buttons(sender_wa_id,
                    f"❌ *No banks found matching \"{query}\"*\n\n"
                    "We couldn't find any bank matching your entry.\n"
                    "Please check the spelling or try searching again.\n\n"
                    "_Example: Zenith, GT, Access, First_",
                    [{"id": "bank_search_again", "title": "🔍 Search again"}],
                    phone_number_id)
                return
        data["pay_bank_list"] = banks
        flow["step"] = "pay_bank_select"
        await save_session(session)
        await _send_bank_results(sender_wa_id, banks, phone_number_id)

    # ── Bank — select ─────────────────────────────────────────────────────────
    elif step == "pay_bank_select":
        banks = data.get("pay_bank_list", [])

        if reply_id == "bank_search_again":
            flow["step"] = "pay_bank_search"
            await save_session(session)
            await _send_text(sender_wa_id,
                "Please enter at least the first 3 characters of your bank name:\n\n"
                "_Example: Zen, GT, Wem_",
                phone_number_id)
        elif reply_id and reply_id.startswith("bank_"):
            try:
                idx = int(reply_id.split("_")[1])
                if 0 <= idx < len(banks):
                    bank      = banks[idx]
                    bank_name = bank["name"]
                    bank_code = bank["code"]
                    data["pay_bank_name"] = bank_name
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
                                    logger.info(f"[payment] saved payout_method_id='{pm_id}'")
                        except Exception as exc:
                            logger.error(f"[payment] create_payout_method_bank failed: {exc}")
                    await save_session(session)
                    await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)
                else:
                    await _send_text(sender_wa_id, "⚠️ Please select a bank from the list.", phone_number_id)
            except (ValueError, IndexError):
                await _send_text(sender_wa_id, "⚠️ Please select a bank from the list.", phone_number_id)
        else:
            if text:
                banks = data.get("pay_bank_list", [])
                llm_result = await call_extract(
                    user_id=sender_wa_id,
                    field_name="bank_selection",
                    question_asked="Please select a bank from the list provided.",
                    user_response=text,
                    expected_format="text",
                )
                if llm_result and llm_result.get("is_valid") and llm_result.get("extracted_value"):
                    ev = str(llm_result["extracted_value"]).lower()
                    for i, b in enumerate(banks[:9]):
                        if ev in b["name"].lower() or b["name"].lower() in ev:
                            reply_id = f"bank_{i}"
                            break
                if reply_id and reply_id.startswith("bank_"):
                    try:
                        idx = int(reply_id.split("_")[1])
                        if 0 <= idx < len(banks):
                            bank = banks[idx]
                            bank_name = bank["name"]
                            bank_code = bank["code"]
                            data["pay_bank_name"] = bank_name
                            data["pay_bank_code"] = bank_code
                            api_data = session.get("api_data", {})
                            user_id = api_data.get("user_id") or ""
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
                                    logger.error(f"[payment] bank_select LLM payout failed: {exc}")
                            await save_session(session)
                            await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)
                            return
                    except (ValueError, IndexError):
                        pass
            await _send_text(sender_wa_id, "⚠️ Please select a bank from the list.", phone_number_id)

    # ── Wallet payout — select (legacy redirect) ──────────────────────────────
    elif step == "pay_wallet_payout_select":
        flow["step"] = "pay_wallet_payout_phone"
        await save_session(session)
        await _send_text(
            sender_wa_id,
            "👛 *Wallet*\n\nEnter the phone number linked to your wallet:\n\n"
            "_Example: 08012345678_",
            phone_number_id,
        )

    # ── Wallet payout — phone number ──────────────────────────────────────────
    elif step == "pay_wallet_payout_phone":
        raw    = text.replace(" ", "").replace("-", "")
        digits = raw.lstrip("+")
        # Normalise +234XXXXXXXXXX or 234XXXXXXXXXX → 0XXXXXXXXXX
        if digits.startswith("234") and len(digits) == 13:
            digits = "0" + digits[3:]
        valid = (
            digits.isdigit()
            and len(digits) in (10, 11)
            and (digits.startswith("0") or len(digits) == 10)
        )
        if not valid:
            await _send_text(
                sender_wa_id,
                "⚠️ Please enter a valid *Nigerian phone number*:\n_Example: 08012345678_",
                phone_number_id,
            )
            return

        data["pay_wallet_phone"] = digits
        api_data         = session.get("api_data", {})
        user_id          = api_data.get("user_id") or ""
        existing_pm_id   = api_data.get("payout_method_id") or ""

        if user_id:
            try:
                if existing_pm_id:
                    # User came back via 0 — update existing payout method (PUT)
                    payout_result = await ipurvey_service.update_payout_method_wallet(
                        user_id=user_id,
                        payout_method_id=existing_pm_id,
                        phone_number=digits,
                    )
                else:
                    # First time — create new payout method (POST)
                    payout_result = await ipurvey_service.create_payout_method_wallet(
                        user_id=user_id,
                        phone_number=digits,
                    )
                if payout_result and isinstance(payout_result, dict):
                    pm_id = payout_result.get("id") or payout_result.get("payoutMethodId")
                    if pm_id:
                        session.setdefault("api_data", {})["payout_method_id"] = pm_id
                        logger.info(f"[payment] wallet payout_method_id='{pm_id}'")
            except Exception as exc:
                logger.error(f"[payment] wallet payout method API failed: {exc}")

        # Initiate payment for Mobile Money
        policy_id      = session.get("api_data", {}).get("policy_id")
        initiate_error = False
        api_ref        = None
        if policy_id:
            try:
                pay_result = await _initiate_payment_with_link_retry(
                    session=session,
                    policy_id=policy_id,
                    payment_method="MOBILE_MONEY",
                )
                if pay_result and isinstance(pay_result, dict):
                    api_ref    = (
                        pay_result.get("reference")
                        or pay_result.get("paymentReference")
                        or pay_result.get("paymentRef")
                    )
                    payment_id = pay_result.get("paymentId") or pay_result.get("id")
                    pc         = pay_result.get("policyCode")
                    _ad        = session.setdefault("api_data", {})
                    if payment_id:
                        _ad["payment_id"] = payment_id
                    if pc:
                        _ad["policy_code"] = pc
                        logger.info(f"[payment] wallet policy_code='{pc}'")
                else:
                    initiate_error = True
            except Exception as exc:
                logger.error(f"[payment] initiate_payment wallet failed: {exc}")
                initiate_error = True
        else:
            initiate_error = True

        if initiate_error:
            await save_session(session)
            await _show_payment_failed_screen(
                sender_wa_id, session, data, data.get("pay_m_bank_ref", "—"), phone_number_id
            )
            return

        ref_display              = api_ref or "Check SMS/email for reference"
        data["pay_m_bank_ref"]   = ref_display
        data["pay_method_display"] = "Wallet"
        flow["step"]             = "pay_m_bank_pending"
        await save_session(session)
        await _send_buttons(
            sender_wa_id,
            "✅ *Payment method selected*\n"
            "You chose: Wallet\n\n"
            "ℹ️ *What happens next?*\n\n"
            "📱 You will receive a WhatsApp message shortly with a payment link to complete your payment securely.\n"
            "*Please check your WhatsApp inbox.*\n\n"
            "⚠️ Do not make any payment outside the WhatsApp link you receive.\n\n"
            "🛡️ *Important*\n\n"
            "📋 Your policy will only be activated after *successful payment confirmation.*\n\n"
            "❌ *Without a completed payment, no cover will be in place.*\n\n"
            "After payment, reply with:",
            [
                {"id": "pay_m_done",    "title": "✅ I have paid"},
                {"id": "pay_m_refresh", "title": "🔄 Refresh status"},
            ],
            phone_number_id,
        )

    # ── Payment method choice ─────────────────────────────────────────────────
    elif step == "pay_method_choice":
        if not reply_id and text:
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="payment_method",
                question_asked="Choose a payment method: Bank Transfer, Card Payment, USSD, or Mobile Money.",
                user_response=text,
                expected_format="text",
            )
            if llm_result and llm_result.get("is_valid") and llm_result.get("extracted_value"):
                ev = str(llm_result["extracted_value"]).lower()
                if any(k in ev for k in ("bank", "transfer", "bank transfer")):
                    reply_id = "pay_m_bank"
                elif "card" in ev:
                    reply_id = "pay_m_card"
                elif any(k in ev for k in ("wallet", "mobile money", "mobile")):
                    reply_id = "pay_m_wallet"
                elif "ussd" in ev:
                    reply_id = "pay_m_ussd"
            if not reply_id:
                await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)
                return
        if reply_id == "pay_m_bank":
            policy_id  = session.get("api_data", {}).get("policy_id")
            api_ref    = None
            payment_id = None
            initiate_error = False
            if policy_id:
                try:
                    pay_result = await _initiate_payment_with_link_retry(
                        session=session,
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
                        policy_code_from_api = pay_result.get("policyCode")
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
                        if policy_code_from_api:
                            api_data["policy_code"] = policy_code_from_api
                            logger.info(f"[payment] saved policy_code='{policy_code_from_api}' from initiate_payment")
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
                await _show_payment_failed_screen(sender_wa_id, session, data, data.get("pay_m_bank_ref", "—"), phone_number_id)
                return

            ref_display = api_ref or "Check SMS/email for reference"
            data["pay_m_bank_ref"]      = ref_display
            data["pay_method_display"]  = "Bank Transfer"
            flow["step"] = "pay_m_bank_pending"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                "✅ *Payment method selected*\n"
                "You chose: 1. Bank Transfer\n\n"
                "ℹ️ *What happens next?*\n\n"
                "📱 You will receive a WhatsApp message shortly with a payment link to complete your payment securely.\n"
                "*Please check your WhatsApp inbox.*\n\n"
                "⚠️ Do not make any payment outside the WhatsApp link you receive.\n\n"
                "🛡️ *Important*\n\n"
                "📋 Your policy will only be activated after *successful payment confirmation.*\n\n"
                "❌ *Without a completed payment, no cover will be in place.*\n\n"
                "After payment, reply with:",
                [
                    {"id": "pay_m_done",    "title": "✅ I have paid"},
                    {"id": "pay_m_refresh", "title": "🔄 Refresh status"},
                ],
                phone_number_id,
            )

        elif reply_id in ("pay_m_card", "pay_m_wallet", "pay_m_ussd"):
            _method_map = {
                "pay_m_card":   ("CARD",                   "Card Payment"),
                "pay_m_wallet": ("SMARTCASH_MOBILE_MONEY", "Mobile Money"),
                "pay_m_ussd":   ("USSD",                   "USSD"),
            }
            _api_method, _method_display = _method_map[reply_id]
            policy_id  = session.get("api_data", {}).get("policy_id")
            initiate_error = False
            payment_id = None
            api_ref    = None
            if policy_id:
                try:
                    pay_result = await _initiate_payment_with_link_retry(
                        session=session,
                        policy_id=policy_id,
                        payment_method=_api_method,
                    )
                    if pay_result and isinstance(pay_result, dict):
                        api_ref    = (
                            pay_result.get("reference")
                            or pay_result.get("paymentReference")
                            or pay_result.get("paymentRef")
                        )
                        payment_id = pay_result.get("paymentId") or pay_result.get("id")
                        policy_code_from_api = pay_result.get("policyCode")
                        api_data = session.setdefault("api_data", {})
                        if payment_id:
                            api_data["payment_id"] = payment_id
                        if policy_code_from_api:
                            api_data["policy_code"] = policy_code_from_api
                            logger.info(f"[payment] saved policy_code='{policy_code_from_api}' from initiate_payment")
                    else:
                        initiate_error = True
                except Exception as exc:
                    logger.error(f"[payment] initiate_payment {_api_method} failed: {exc}")
                    initiate_error = True
            else:
                initiate_error = True

            if initiate_error:
                await save_session(session)
                await _show_payment_failed_screen(sender_wa_id, session, data, data.get("pay_m_bank_ref", "—"), phone_number_id)
                return

            ref_display = api_ref or "Check SMS/email for reference"
            data["pay_m_bank_ref"]     = ref_display
            data["pay_method_display"] = _method_display
            flow["step"] = "pay_m_bank_pending"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                f"✅ *Payment method selected*\n"
                f"You chose: {_method_display}\n\n"
                "ℹ️ *What happens next?*\n\n"
                "📱 You will receive a WhatsApp message shortly with a payment link to complete your payment securely.\n"
                "*Please check your WhatsApp inbox.*\n\n"
                "⚠️ Do not make any payment outside the WhatsApp link you receive.\n\n"
                "🛡️ *Important*\n\n"
                "📋 Your policy will only be activated after *successful payment confirmation.*\n\n"
                "❌ *Without a completed payment, no cover will be in place.*\n\n"
                "After payment, reply with:",
                [
                    {"id": "pay_m_done",    "title": "✅ I have paid"},
                    {"id": "pay_m_refresh", "title": "🔄 Refresh status"},
                ],
                phone_number_id,
            )
        else:
            await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)

    # ── Bank pending ──────────────────────────────────────────────────────────
    elif step == "pay_m_bank_pending":
        if not reply_id and text:
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="payment_pending_action",
                question_asked="Payment is being processed. Have you paid or would you like to refresh the payment status?",
                user_response=text,
                expected_format="text",
            )
            if llm_result and llm_result.get("is_valid") and llm_result.get("extracted_value"):
                ev = str(llm_result["extracted_value"]).lower()
                if any(k in ev for k in ("paid", "done", "i have paid", "completed", "made payment", "already paid")):
                    reply_id = "pay_m_done"
                elif any(k in ev for k in ("refresh", "check", "status", "update", "verify")):
                    reply_id = "pay_m_refresh"
            if not reply_id:
                _method_display = data.get("pay_method_display", "Bank Transfer")
                await _send_buttons(
                    sender_wa_id,
                    f"✅ *Payment method selected*\nYou chose: 1. {_method_display}\n\nPlease make your payment and reply below:",
                    [
                        {"id": "pay_m_done", "title": "✅ I have paid"},
                        {"id": "pay_m_refresh", "title": "🔄 Refresh status"},
                    ],
                    phone_number_id,
                )
                return
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
            payment_pending   = False
            policy_ref = None
            api_data_  = session.get("api_data", {})
            policy_id  = api_data_.get("policy_id")
            payment_id = api_data_.get("payment_id")
            # Use policyCode (e.g. TA-NG-TAIN-...) for status check — API does not accept UUID
            policy_code_for_status = api_data_.get("policy_code") or policy_id
            if policy_code_for_status:
                msisdn = get_msisdn(sender_wa_id)
                logger.info(f"[payment] checking status for policy_code='{policy_code_for_status}'")
                try:
                    status_result = await ipurvey_service.get_payment_status(
                        policy_id=policy_code_for_status, msisdn=msisdn
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
                            logger.info(f"[payment] status={status_val} → payment confirmed")
                        elif status_val == "PENDING":
                            payment_pending = True
                            logger.info("[payment] status=PENDING → showing pending screen")
                        else:
                            logger.info(f"[payment] status={status_val!r} → showing failed screen")
                except Exception as exc:
                    logger.error(f"[payment] get_payment_status failed: {exc}")
            if payment_confirmed:
                api_data_ = session.get("api_data", {})
                policy_ref = (
                    api_data_.get("policy_code")
                    or api_data_.get("policy_id")
                    or ref
                )
                flow.setdefault("data", {})["pay_policy"] = policy_ref
                session["active_policy_code"]   = policy_ref
                session["active_policy_status"] = "submitted"
                if api_data_.get("policy_id"):
                    session["active_policy_id"] = api_data_["policy_id"]
                invalidate_policy_cache(session)
                await save_session(session)
                await _send_success(sender_wa_id, session, flow, amount, ref, policy_ref, cname, phone_number_id)
            elif payment_pending:
                await _show_payment_pending_screen(sender_wa_id, session, data, ref, phone_number_id)
            else:
                await _show_payment_failed_screen(sender_wa_id, session, data, ref, phone_number_id)
        else:
            _method_display = data.get("pay_method_display", "Bank Transfer")
            await _send_buttons(
                sender_wa_id,
                f"✅ *Payment method selected*\n"
                f"You chose: 1. {_method_display}\n\n"
                "ℹ️ *What happens next?*\n\n"
                "📱 You will receive a WhatsApp message shortly with a payment link to complete your payment securely.\n"
                "*Please check your WhatsApp inbox.*\n\n"
                "⚠️ Do not make any payment outside the WhatsApp link you receive.\n\n"
                "🛡️ *Important*\n\n"
                "📋 Your policy will only be activated after *successful payment confirmation.*\n\n"
                "❌ *Without a completed payment, no cover will be in place.*\n\n"
                "After payment, reply with:",
                [
                    {"id": "pay_m_done",    "title": "✅ I have paid"},
                    {"id": "pay_m_refresh", "title": "🔄 Refresh status"},
                ],
                phone_number_id,
            )

    # ── Legacy simulated payment steps — redirect to payment method choice ────
    elif step in (
        "pay_m_card_number", "pay_m_card_expiry", "pay_m_card_cvv", "pay_m_card_otp",
        "pay_m_wallet_select", "pay_m_wallet_phone", "pay_m_wallet_otp",
        "pay_m_ussd_confirm",
    ):
        flow["step"] = "pay_method_choice"
        await save_session(session)
        await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)

    # ── Post-success ──────────────────────────────────────────────────────────
    elif step == "pay_success":
        pol = data.get("pay_policy", "—")
        ref = data.get("pay_ref",    "—")
        if not reply_id and text:
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="pay_success_action",
                question_asked="Payment successful. What would you like to do next? View policy document, Upload boarding pass, or Go to main menu.",
                user_response=text,
                expected_format="text",
            )
            if llm_result and llm_result.get("is_valid") and llm_result.get("extracted_value"):
                ev = str(llm_result["extracted_value"]).lower()
                if any(k in ev for k in ("view", "document", "policy doc", "pdf")):
                    reply_id = "pay_view_doc"
                elif any(k in ev for k in ("upload", "boarding", "boarding pass")):
                    reply_id = "pay_upload_bp"
                elif any(k in ev for k in ("menu", "home", "main", "exit")):
                    reply_id = "pay_home"
        if reply_id in ("pay_view_policy", "pay_view_doc", "pay_pol_view_doc"):
            bc_data_v = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {}).get("data", {})
            flight_v  = bc_data_v.get("flight_num", "—")
            date_v    = bc_data_v.get("date", "—")
            name_v    = bc_data_v.get("name", "—")
            doc_url_v: Optional[str] = None
            try:
                doc_url_v = await ipurvey_service.get_policy_document_url(pol)
            except Exception:
                pass
            card_body_v = (
                f"*{cname}*\n"
                f"Policy No: *{pol}*   ✅ Active\n\n"
                f"🛫 Flight       {flight_v}\n"
                f"📅 Date          {date_v}\n"
                f"🧑 Traveller  {name_v}\n"
            )
            if doc_url_v:
                data["pay_doc_url"] = doc_url_v
                await save_session(session)
                cta_v = {
                    "messaging_product": "whatsapp",
                    "recipient_type":    "individual",
                    "to":                sender_wa_id,
                    "type":              "interactive",
                    "interactive": {
                        "type":   "cta_url",
                        "header": {"type": "text", "text": "📁 Your Policy Details"},
                        "body":   {"text": card_body_v},
                        "action": {
                            "name": "cta_url",
                            "parameters": {
                                "display_text": "📋 Download Policy Document",
                                "url": doc_url_v,
                            },
                        },
                    },
                }
                await send_whatsapp_payload(whatsapp_payload=cta_v, phone_number_id=phone_number_id, source="payment_flow")
                await send_text_message(to=sender_wa_id, body=_UTILITY, phone_number_id=phone_number_id, source="payment_flow")
                await _send_buttons(
                    sender_wa_id,
                    "What would you like to do?",
                    [
                        {"id": "pay_pol_download", "title": "📥 Download Policy"},
                        {"id": "pay_pol_alerts",   "title": "🔔 Manage alerts"},
                        {"id": "pay_pol_all",      "title": "📋 All my policies"},
                    ],
                    phone_number_id,
                )
            else:
                await _send_text(
                    sender_wa_id,
                    f"📁 *Your Policy Details*\n\n{card_body_v}",
                    phone_number_id,
                )
                await _send_buttons(
                    sender_wa_id,
                    "What would you like to do?",
                    [
                        {"id": "pay_pol_alerts", "title": "🔔 Manage alerts"},
                        {"id": "pay_pol_help",   "title": "🙋 Help"},
                        {"id": "pay_pol_all",    "title": "📋 All my policies"},
                    ],
                    phone_number_id,
                )
        elif reply_id == "pay_pol_download":
            cached_url = data.get("pay_doc_url", "")
            if not cached_url:
                try:
                    cached_url = await ipurvey_service.get_policy_document_url(data.get("pay_policy", pol))
                except Exception:
                    cached_url = ""
            if cached_url:
                cta_dl = {
                    "messaging_product": "whatsapp",
                    "recipient_type":    "individual",
                    "to":                sender_wa_id,
                    "type":              "interactive",
                    "interactive": {
                        "type":   "cta_url",
                        "header": {"type": "text", "text": "📁 Policy Document"},
                        "body":   {"text": f"*{cname}*\nPolicy No: *{pol}*\n\nTap the button to download your policy document."},
                        "action": {
                            "name": "cta_url",
                            "parameters": {
                                "display_text": "📋 Download Policy Document",
                                "url": cached_url,
                            },
                        },
                    },
                }
                await send_whatsapp_payload(whatsapp_payload=cta_dl, phone_number_id=phone_number_id, source="payment_flow")
                await send_text_message(to=sender_wa_id, body=_UTILITY, phone_number_id=phone_number_id, source="payment_flow")
            else:
                await _send_text(
                    sender_wa_id,
                    "⚠️ *Policy document not yet available.*\n\n"
                    "Your document will be ready shortly. Please check back later.",
                    phone_number_id,
                )
        elif reply_id == "pay_pol_alerts":
            await _send_text(
                sender_wa_id,
                "🔔 *Flight Monitoring Active*\n\n"
                "TravelAssist is automatically monitoring your flight for delays and cancellations.\n\n"
                "You will receive alerts on this WhatsApp number if anything changes.",
                phone_number_id,
            )
        elif reply_id == "pay_pol_help":
            from app.services.help_flow_service import start_help_flow
            await start_help_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        elif reply_id == "pay_pol_all":
            from app.services.check_policy_flow_service import start_check_policy_flow
            session["temp_data"][PAYMENT_FLOW_KEY] = {}
            await save_session(session)
            await start_check_policy_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        elif reply_id == "pay_upload_bp":
            from app.services.bp_link_flow_service import start_bp_link_flow
            flow["active"] = False
            await save_session(session)
            bc_data = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {}).get("data", {})
            flight_raw = bc_data.get("flight_num", "—")
            direct_policy = {
                "name":      bc_data.get("cover", "Your Policy"),
                "ref":       session.get("active_policy_code", "—"),
                "policy_id": session.get("active_policy_id", ""),
                "flight":    flight_raw,
                "date":      bc_data.get("date", "—"),
                "traveler":  bc_data.get("name", "—"),
                "airline":   flight_raw[:2] if flight_raw and flight_raw != "—" else "—",
            }
            await start_bp_link_flow(
                wa_id=sender_wa_id,
                phone_number_id=phone_number_id,
                direct_policy=direct_policy,
            )
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
                f"✅ *Payment Successful!*\n"
                f"_Your cover is now active 🎉_\n"
                f"──────────────────────────\n"
                f"🗂️ *Policy No:* {pol}\n"
                f"✈️ *Flight:* {flight}\n"
                f"📅 *Date:* {date}\n"
                f"🧡 *Traveller:* {name}",
                phone_number_id)
            await _send_list(sender_wa_id,
                "🖇️ *Got your boarding pass handy? Upload it now* 👍\n"
                "If not, no worries — you can upload it later. "
                "We'll just need it before any payout.\n\n"
                "What would you like to do next?",
                "Choose an option",
                [
                    {
                        "title": "Next steps",
                        "rows": [
                            {"id": "pay_view_doc",  "title": "📄 View policy document"},
                            {"id": "pay_upload_bp", "title": "📤 Upload boarding pass"},
                            {"id": "pay_home",      "title": "🏠 Main menu"},
                        ],
                    }
                ],
                phone_number_id,
            )

    # ── Submission retry (legacy — submission removed, redirect to success) ────
    elif step == "pay_submit_retry":
        err_ref    = data.get("submission_ref", "—")
        err_cname  = data.get("submission_cname", "—")
        err_amount = data.get("submission_amount", amount)
        if not reply_id and text:
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="submit_retry_action",
                question_asked="Policy submission failed. Would you like to retry submission or go to the main menu?",
                user_response=text,
                expected_format="text",
            )
            if llm_result and llm_result.get("is_valid") and llm_result.get("extracted_value"):
                ev = str(llm_result["extracted_value"]).lower()
                if any(k in ev for k in ("retry", "try again", "resubmit", "submit again")):
                    reply_id = "pay_retry_submit"
                elif any(k in ev for k in ("menu", "home", "main", "exit", "cancel")):
                    reply_id = "pay_home"
            if not reply_id:
                await _send_buttons(
                    sender_wa_id,
                    "⚠️ *Policy Submission Failed*\n\nWhat would you like to do?",
                    [
                        {"id": "pay_retry_submit", "title": "🔄 Retry Submission"},
                        {"id": "pay_home", "title": "🏠 Main menu"},
                    ],
                    phone_number_id,
                )
                return
        if reply_id == "pay_retry_submit":
            api_data_ = session.get("api_data", {})
            policy_ref = (
                api_data_.get("policy_code")
                or api_data_.get("policy_id")
                or err_ref
            )
            flow.setdefault("data", {})["pay_policy"] = policy_ref
            session["active_policy_code"]   = policy_ref
            session["active_policy_status"] = "submitted"
            if api_data_.get("policy_id"):
                session["active_policy_id"] = api_data_["policy_id"]
            invalidate_policy_cache(session)
            await save_session(session)
            await _send_success(sender_wa_id, session, flow, err_amount, err_ref, policy_ref, err_cname, phone_number_id)
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


async def go_back_one_step(wa_id: str, phone_number_id: Optional[str]):
    """Go back exactly one step in the payment flow instead of restarting."""
    session, flow = await _get_flow_state(wa_id)
    step = flow.get("step", "pay_payout_options")
    data = flow.get("data", {})

    _PREV = {
        "pay_acct_number":         "pay_payout_options",
        "pay_wallet_payout_phone": "pay_payout_options",
        "pay_bank_search":         "pay_acct_number",
        "pay_bank_select":         "pay_bank_search",
    }

    prev = _PREV.get(step)

    # For pay_m_bank_pending — if payout method was Wallet, allow going back to phone step
    if step == "pay_m_bank_pending":
        method = data.get("pay_method_display", "")
        if "wallet" in method.lower():
            flow["step"] = "pay_wallet_payout_phone"
            await save_session(session)
            await _send_text(
                wa_id,
                "👛 *Wallet*\n\nEnter the phone number linked to your wallet:\n\n"
                "_Example: 08012345678_",
                phone_number_id,
            )
            return

    # At the very first payment step, "0" should exit payment and return to
    # buy_cover_next_steps (the cover-selected screen), not restart payment.
    if step in ("pay_payout_options", "pay_method_choice"):
        session["temp_data"][PAYMENT_FLOW_KEY] = {"active": False}
        bc_flow = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {})
        bc_flow["active"] = True
        bc_flow["step"]   = "buy_cover_next_steps"
        session["temp_data"][BUY_COVER_FLOW_KEY] = bc_flow
        await save_session(session)
        from app.services.buy_cover_flow_service import resume_at_current_step as _bc_resume
        await _bc_resume(wa_id=wa_id, phone_number_id=phone_number_id)
        return

    if not prev or step in ("pay_m_bank_pending", "pay_success", "pay_submit_retry"):
        await start_payment_flow(wa_id=wa_id, phone_number_id=phone_number_id)
        return

    flow["step"] = prev
    await save_session(session)

    if prev == "pay_payout_options":
        await _send_buttons(
            wa_id,
            "Payout options\n\nChoose how you would like to receive money for any future payouts:",
            [
                {"id": "pay_bank",          "title": "🏦 Bank transfer"},
                {"id": "pay_wallet_payout", "title": "👛 Wallet"},
            ],
            phone_number_id,
        )

    elif prev == "pay_acct_number":
        await _send_text(
            wa_id,
            "🏦 *Bank Transfer*\n\nPlease enter your 10-digit account number:\n\n"
            "_Example: 0123456789_",
            phone_number_id,
        )

    elif prev == "pay_bank_search":
        await _send_text(
            wa_id,
            "Please enter at least the first 3 characters of your bank name:\n\n"
            "_Example: Zen, Wem, GT_",
            phone_number_id,
        )

    else:
        await start_payment_flow(wa_id=wa_id, phone_number_id=phone_number_id)


async def resume_at_current_step(wa_id: str, phone_number_id: Optional[str]) -> None:
    """Re-show the original prompt for whatever payment step the user is currently on.
    Called when user taps 'No, go back' on the Cancel Purchase confirm screen."""
    session, flow = await _get_flow_state(wa_id)
    step = flow.get("step", "pay_payout_options")

    if step in ("pay_payout_options", "pay_method_choice"):
        await start_payment_flow(wa_id=wa_id, phone_number_id=phone_number_id)
    elif step == "pay_acct_number":
        await _send_text(
            wa_id,
            "🏦 *Bank Transfer*\n\nPlease enter your 10-digit account number:\n\n"
            "_Example: 0123456789_",
            phone_number_id,
        )
    elif step in ("pay_bank_search", "pay_bank_select"):
        await _send_text(
            wa_id,
            "Please enter at least the first 3 characters of your bank name:\n\n"
            "_Example: Zen, Wem, GT_",
            phone_number_id,
        )
    elif step in ("pay_wallet_payout_select", "pay_wallet_payout_phone"):
        await _send_text(
            wa_id,
            "👛 *Wallet*\n\nEnter the phone number linked to your wallet:\n\n"
            "_Example: 08012345678_",
            phone_number_id,
        )
    else:
        await start_payment_flow(wa_id=wa_id, phone_number_id=phone_number_id)
