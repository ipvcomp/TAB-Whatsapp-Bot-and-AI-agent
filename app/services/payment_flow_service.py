import logging
import random
import string
from typing import Optional

from app.services.session_service import get_session, save_session
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


def _gen_ref()    -> str: return "TRV" + "".join(random.choices(string.digits, k=10))
def _gen_policy() -> str: return "POL" + "".join(random.choices(string.digits, k=5))
def _gen_otp()    -> str: return str(random.randint(100000, 999999))
def _gen_ussd()   -> str: return f"*737*{random.randint(100000, 999999)}#"


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
    await send_text_message(to=to, body=f"{body}\n\n{_UTILITY}", phone_number_id=phone_number_id, source="payment_flow")


async def _send_buttons(to: str, body: str, buttons: list, phone_number_id: Optional[str]):
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": f"{body}\n\n{_UTILITY}"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in buttons
                ]
            },
        },
    }
    await send_whatsapp_payload(whatsapp_payload=payload, phone_number_id=phone_number_id, source="payment_flow")


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
        "body": {"text": f"{body}\n\n{_UTILITY}"},
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

    await _send_list(
        wa_id,
        f"🧾 PAYMENT SUMMARY\n"
        f"✈️ Policy      {cname}\n"
        f"✈️ Flight      {origin} → {dest}\n"
        f"📅 Date        {dep}\n"
        f"👥 Travellers  {travelers}\n"
        f"🔒 KYC         ✅ Verified\n"
        f"💰 Amount      ₦{amount:,}\n\n"
        f"Choose a payment method below:",
        "Select method",
        [{"title": "Payment Method", "rows": [
            {"id": "pay_m_bank",   "title": "🏦 Bank transfer"},
            {"id": "pay_m_card",   "title": "💳 Card payment"},
            {"id": "pay_m_wallet", "title": "👛 Wallet"},
            {"id": "pay_m_ussd",   "title": "📲 USSD"},
        ]}],
        phone_number_id,
        header="🎫 You're one step away from activating your cover",
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
    await _send_list(
        wa_id,
        "What would you like to do next?",
        "Choose",
        [{"title": "Options", "rows": [
            {"id": "pay_view_policy", "title": "📄 View my policy doc"},
            {"id": "pay_upload_bp",   "title": "⬆️ Upload boarding pass"},
            {"id": "pay_home",        "title": "🏠 Main menu"},
            {"id": "pay_new",         "title": "✈️ Buy new cover"},
        ]}],
        phone_number_id,
        header="📎 Got your boarding pass? Upload it now 👍",
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
    amount    = COVER_PRICES.get(cover_key, 2500)
    cname     = COVER_NAMES.get(cover_key, "Local Travel Basic")
    origin    = bc_data.get("depart_airport", "—").split("—")[0].strip()
    dest      = bc_data.get("arrive_airport",  "—").split("—")[0].strip()
    dep_date  = bc_data.get("date",  "—")
    travelers_list = bc_data.get("travelers", [])
    travelers = len(travelers_list) if travelers_list else 1
    policy_no = _gen_policy()

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
            "pay_policy":    policy_no,
        },
    }
    session["temp_data"].get(KYC_FLOW_KEY, {}).update({"active": False})
    if "user_id" not in session:
        session["user_id"] = wa_id
    await save_session(session)

    await _send_list(
        wa_id,
        "Choose how you would like to receive money for any future payouts:",
        "Select option",
        [{"title": "Payout options", "rows": [
            {"id": "pay_bank",          "title": "🏦 Bank transfer"},
            {"id": "pay_wallet_payout", "title": "👛 Wallet"},
        ]}],
        phone_number_id,
        header="Payout options",
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
    policy_no = data.get("pay_policy",   "POL00000")

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
            await _send_list(sender_wa_id, "Choose wallet option:", "Select wallet",
                [{"title": "👛 Wallet", "rows": WALLET_OPTIONS}], phone_number_id, header="👛 Wallet")
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
                    data["pay_bank_name"] = banks[idx]
                    flow["step"] = "pay_wallet_payout_select"
                    await save_session(session)
                    await _send_list(sender_wa_id, "Choose wallet option:", "Select wallet",
                        [{"title": "👛 Wallet", "rows": WALLET_OPTIONS}], phone_number_id, header="👛 Wallet")
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
            await save_session(session)
            await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)
        else:
            await _send_text(sender_wa_id,
                "⚠️ Enter a valid *phone number*:\n_Example: 08012345678_",
                phone_number_id)

    # ── Payment method choice ─────────────────────────────────────────────────
    elif step == "pay_method_choice":
        if reply_id == "pay_m_bank":
            ref = "TA" + "".join(random.choices(string.digits, k=6))
            data["pay_m_bank_ref"] = ref
            flow["step"] = "pay_m_bank_pending"
            await save_session(session)
            await _send_list(sender_wa_id,
                f"Please transfer *₦{amount:,}* to:\n\n"
                f"Bank             Example Bank\n"
                f"Account Name     TravelAssist Payments\n"
                f"Account No.      0123456789\n\n"
                f"🔑 Reference: {ref}\n\nAfter payment, reply with:",
                "Select",
                [{"title": "Action", "rows": [
                    {"id": "pay_m_done",    "title": "✅ I have paid"},
                    {"id": "pay_m_refresh", "title": "🔄 Refresh status"},
                ]}],
                phone_number_id,
                header="🏦 Bank Transfer")

        elif reply_id == "pay_m_card":
            flow["step"] = "pay_m_card_number"
            await save_session(session)
            await _send_text(sender_wa_id,
                f"💳 *Card Payment*\n\n💰 Amount: *₦{amount:,}*\n\n"
                "🔒 Your details are encrypted & secure.\n\n"
                "Enter your *16-digit card number*:\n_Example: 5399 8300 0000 0000_",
                phone_number_id)

        elif reply_id == "pay_m_wallet":
            flow["step"] = "pay_m_wallet_select"
            await save_session(session)
            await _send_list(sender_wa_id, "Choose wallet option:", "Select wallet",
                [{"title": "👛 Wallet", "rows": WALLET_OPTIONS}], phone_number_id, header="👛 Wallet")

        elif reply_id == "pay_m_ussd":
            ussd = _gen_ussd()
            data["pay_ussd_code"] = ussd
            flow["step"] = "pay_m_ussd_confirm"
            await save_session(session)
            await _send_text(sender_wa_id,
                f"📲 *USSD Payment*\n\n💰 Amount: *₦{amount:,}*\n\n"
                f"Dial this code:\n\n*{ussd}*\n\n⏰ Expires in *30 minutes*",
                phone_number_id)
            await _send_list(sender_wa_id, "Once payment is done, tap below:", "Confirm",
                [{"title": "Action", "rows": [
                    {"id": "pay_m_done", "title": "✅ Payment done"},
                    {"id": "pay_m_help", "title": "❓ Need help"},
                ]}],
                phone_number_id)
        else:
            await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)

    # ── Bank pending ──────────────────────────────────────────────────────────
    elif step == "pay_m_bank_pending":
        ref = data.get("pay_m_bank_ref", "TA000000")
        if reply_id == "pay_m_done":
            policy = _gen_policy()
            await _send_success(sender_wa_id, session, flow, amount, ref, policy, cname, phone_number_id)
        else:
            await _send_list(sender_wa_id,
                f"Please transfer *₦{amount:,}* to:\n\n"
                f"Bank             Example Bank\n"
                f"Account Name     TravelAssist Payments\n"
                f"Account No.      0123456789\n\n"
                f"🔑 Reference: {ref}\n\nAfter payment, reply with:",
                "Select",
                [{"title": "Action", "rows": [
                    {"id": "pay_m_done",    "title": "✅ I have paid"},
                    {"id": "pay_m_refresh", "title": "🔄 Refresh status"},
                ]}],
                phone_number_id,
                header="🏦 Bank Transfer")

    # ── Card — number ─────────────────────────────────────────────────────────
    elif step == "pay_m_card_number":
        digits = text.replace(" ", "").replace("-", "")
        if digits.isdigit() and len(digits) == 16:
            data["card_last4"] = digits[-4:]
            flow["step"] = "pay_m_card_expiry"
            await save_session(session)
            await _send_text(sender_wa_id,
                f"💳 Card: *•••• •••• •••• {digits[-4:]}* ✅\n\n"
                "Enter card *expiry date*:\n_Format: MM/YY  Example: 12/26_",
                phone_number_id)
        else:
            await _send_text(sender_wa_id,
                "⚠️ Enter your *16-digit card number*:\n_Example: 5399 8300 0000 0000_",
                phone_number_id)

    # ── Card — expiry ─────────────────────────────────────────────────────────
    elif step == "pay_m_card_expiry":
        t = text
        if "/" in t and len(t) in (4, 5):
            data["card_expiry"] = t
            flow["step"] = "pay_m_card_cvv"
            await save_session(session)
            await _send_text(sender_wa_id,
                f"📅 Expiry: *{t}* ✅\n\nEnter your card *CVV*:\n_(3-digit code on back)_",
                phone_number_id)
        else:
            await _send_text(sender_wa_id,
                "⚠️ Enter expiry in *MM/YY* format.\n_Example: 12/26_",
                phone_number_id)

    # ── Card — CVV ────────────────────────────────────────────────────────────
    elif step == "pay_m_card_cvv":
        t = text
        if t.isdigit() and len(t) == 3:
            last4 = data.get("card_last4", "****")
            otp   = _gen_otp()
            data["pay_otp"] = otp
            flow["step"] = "pay_m_card_otp"
            await save_session(session)
            await _send_text(sender_wa_id,
                f"💳 *Card Payment*\n\n💰 Amount: *₦{amount:,}*\n"
                f"🔢 Card: *•••• •••• •••• {last4}*\n\n"
                f"🔐 *OTP Sent!*\nCheck phone linked to card *{last4}*.\n\n"
                f"Enter your *6-digit OTP*:\n\n⏰ Expires in *5 minutes*\n_Your OTP: *{otp}*_",
                phone_number_id)
        else:
            await _send_text(sender_wa_id, "⚠️ CVV must be *3 digits*. Try again:", phone_number_id)

    # ── Card — OTP ────────────────────────────────────────────────────────────
    elif step == "pay_m_card_otp":
        if text == data.get("pay_otp", ""):
            ref = _gen_ref()
            await _send_success(sender_wa_id, session, flow, amount, ref, policy_no, cname, phone_number_id)
        else:
            await _send_text(sender_wa_id, "❌ Incorrect OTP. Try again:", phone_number_id)

    # ── Wallet payment — select ───────────────────────────────────────────────
    elif step == "pay_m_wallet_select":
        wallet_map = {"wallet_9psb": "9PSB", "wallet_smartcash": "SmartCash", "wallet_opay": "OPay"}
        if reply_id in wallet_map:
            wtype = wallet_map[reply_id]
            data["pay_m_wallet_type"] = wtype
            flow["step"] = "pay_m_wallet_phone"
            await save_session(session)
            await _send_text(sender_wa_id,
                f"📱 *{wtype} Wallet Payment*\n\n💰 Amount: *₦{amount:,}*\n\n"
                "Enter the phone number linked to your wallet:\n\n_Example: 08012345678_",
                phone_number_id)
        else:
            await _show_payment_summary(sender_wa_id, session, flow, phone_number_id)

    # ── Wallet payment — phone ────────────────────────────────────────────────
    elif step == "pay_m_wallet_phone":
        digits = text.replace(" ", "").replace("-", "")
        if digits.isdigit() and len(digits) >= 10:
            wtype  = data.get("pay_m_wallet_type", "Wallet")
            masked = digits[:4] + "****" + digits[-3:]
            otp    = _gen_otp()
            data["pay_otp"]          = otp
            data["pay_wallet_phone"] = digits
            flow["step"] = "pay_m_wallet_otp"
            await save_session(session)
            await _send_text(sender_wa_id,
                f"📱 *{wtype} Wallet Payment*\n\n💰 Amount: *₦{amount:,}*\n"
                f"📞 Wallet: *{masked}*\n\n🔐 OTP sent to *{masked}*\n\n"
                f"Enter your *6-digit OTP*:\n\n⏰ Expires in *5 minutes*\n_Your OTP: *{otp}*_",
                phone_number_id)
        else:
            await _send_text(sender_wa_id,
                "⚠️ Enter a valid *phone number*:\n_Example: 08012345678_",
                phone_number_id)

    # ── Wallet OTP ────────────────────────────────────────────────────────────
    elif step == "pay_m_wallet_otp":
        if text == data.get("pay_otp", ""):
            ref = _gen_ref()
            await _send_success(sender_wa_id, session, flow, amount, ref, policy_no, cname, phone_number_id)
        else:
            await _send_text(sender_wa_id, "❌ Incorrect OTP. Try again:", phone_number_id)

    # ── USSD confirm ──────────────────────────────────────────────────────────
    elif step == "pay_m_ussd_confirm":
        if reply_id == "pay_m_done":
            ref = _gen_ref()
            await _send_success(sender_wa_id, session, flow, amount, ref, policy_no, cname, phone_number_id)
        elif reply_id == "pay_m_help":
            ussd = data.get("pay_ussd_code", "*737*000000#")
            await _send_text(sender_wa_id,
                f"📲 *USSD Help*\n\nDial *{ussd}* on your phone.\n\n"
                f"Follow prompts to pay *₦{amount:,}*.\n\n📞 Support: *+234 800 000 0000*",
                phone_number_id)

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
            await _send_list(sender_wa_id,
                "What would you like to do next?", "Choose",
                [{"title": "Options", "rows": [
                    {"id": "pay_view_policy", "title": "📄 View my policy doc"},
                    {"id": "pay_upload_bp",   "title": "⬆️ Upload boarding pass"},
                    {"id": "pay_home",        "title": "🏠 Main menu"},
                    {"id": "pay_new",         "title": "✈️ Buy new cover"},
                ]}],
                phone_number_id,
                header="📎 Got your boarding pass? Upload it now 👍")

    # ── Catch-all ─────────────────────────────────────────────────────────────
    else:
        session["temp_data"][PAYMENT_FLOW_KEY] = {}
        await save_session(session)
        from app.services.auto_reply_service import send_main_menu
        await send_main_menu(to=sender_wa_id, phone_number_id=phone_number_id)
