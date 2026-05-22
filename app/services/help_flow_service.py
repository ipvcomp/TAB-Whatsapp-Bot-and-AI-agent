import logging
from typing import Optional

from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload

logger = logging.getLogger(__name__)

HELP_FLOW_KEY = "help_flow"


def is_in_help_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    return session.get("temp_data", {}).get(HELP_FLOW_KEY, {}).get("active", False)


async def _get_flow_state(wa_id: str) -> tuple[dict, dict]:
    session = await get_session(wa_id) or {}
    flow = session.setdefault("temp_data", {}).setdefault(HELP_FLOW_KEY, {})
    return session, flow


async def _set_step(session: dict, step: str):
    session["temp_data"][HELP_FLOW_KEY]["step"] = step
    session["temp_data"][HELP_FLOW_KEY]["active"] = True
    await save_session(session)


async def _save_data(session: dict, key: str, value):
    session["temp_data"][HELP_FLOW_KEY].setdefault("data", {})[key] = value
    await save_session(session)


async def _reset(session: dict):
    session["temp_data"][HELP_FLOW_KEY] = {}
    await save_session(session)


async def _send_text(to: str, body: str, phone_number_id: Optional[str]):
    await send_text_message(
        to=to, body=body, phone_number_id=phone_number_id, source="help_flow"
    )


async def _send_buttons(
    to: str,
    body: str,
    buttons: list,
    phone_number_id: Optional[str],
    header: Optional[str] = None,
):
    interactive: dict = {
        "type": "button",
        "body": {"text": body},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                for b in buttons
            ]
        },
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
    await send_whatsapp_payload(
        whatsapp_payload=payload, phone_number_id=phone_number_id, source="help_flow"
    )


async def _send_list(
    to: str,
    body: str,
    button_label: str,
    sections: list,
    phone_number_id: Optional[str],
    header: Optional[str] = None,
):
    interactive: dict = {
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
    await send_whatsapp_payload(
        whatsapp_payload=payload, phone_number_id=phone_number_id, source="help_flow"
    )


_TOPIC_META = {
    "hlp_buying": {
        "emoji":      "✈️",
        "title":      "Buying Cover",
        "action_id":  "hlp_act_buy",
        "action_btn": "Start buying cover →",
        "action_body": "Ready to get covered?",
        "info": (
            "✈️ *Help — Buying Cover*\n\n"
            "*What you need to know:*\n\n"
            "• Buy your policy for yourself or a group\n\n"
            "• You'll need your flight details — number,\n"
            "  date, departure & destination\n\n"
            "• Choose from available cover types\n"
            "  and see pricing upfront\n\n"
            "• You'll receive your policy on WhatsApp\n"
            "  and email within seconds of payment 🎉"
        ),
        "faqs": [
            ("faq_buy_details",  "What flight details do I need?"),
            ("faq_buy_cover",    "How do I choose a cover type?"),
            ("faq_buy_group",    "Can I buy cover for a group?"),
            ("faq_buy_receive",  "How do I receive my policy?"),
        ],
    },
    "hlp_kyc": {
        "emoji":      "✅",
        "title":      "KYC Verification",
        "action_id":  "hlp_act_kyc",
        "action_btn": "Continue to KYC →",
        "action_body": "Ready to verify your identity?",
        "info": (
            "✅ *Help — KYC Verification*\n\n"
            "*What you need to know:*\n\n"
            "• Submit your *NIN* or *BVN* number\n\n"
            "• Make sure the information you submit\n"
            "  matches your travel document\n\n"
            "• Keep your 11-digit number ready\n"
            "  before starting verification\n\n"
            "• Your details are encrypted and\n"
            "  stored securely 🔒"
        ),
        "faqs": [
            ("faq_kyc_what",     "What is KYC / why is it needed?"),
            ("faq_kyc_types",    "What ID types are accepted?"),
            ("faq_kyc_failed",   "My KYC was rejected — what now?"),
            ("faq_kyc_time",     "How long does KYC take?"),
        ],
    },
    "hlp_payment": {
        "emoji":      "💳",
        "title":      "Payment Issues",
        "action_id":  "hlp_act_pay",
        "action_btn": "Resume pending payment →",
        "action_body": "Need to complete a payment?",
        "info": (
            "💳 *Help — Payment Issues*\n\n"
            "*What you need to know:*\n\n"
            "• *Bank details* — account number\n"
            "• *Bank name* — account name\n"
            "• *Bank code* — payment reference\n\n"
            "• Payment issues are sometimes temporary —\n"
            "  no worries, you can always retry\n\n"
            "• Your progress is saved, so you won't\n"
            "  need to re-enter your details again"
        ),
        "faqs": [
            ("faq_pay_declined", "Why was my payment declined?"),
            ("faq_pay_details",  "What bank details do I need?"),
            ("faq_pay_change",   "Can I change my bank details?"),
            ("faq_pay_time",     "How long does payment take?"),
        ],
    },
    "hlp_policy": {
        "emoji":      "📋",
        "title":      "My Policy",
        "action_id":  "hlp_act_policy",
        "action_btn": "Check my policy →",
        "action_body": "View your active policy?",
        "info": (
            "📋 *Help — My Policy*\n\n"
            "*What you need to know:*\n\n"
            "• Your policy number starts with *TA-*\n"
            "  _(e.g. TA-NG-TAIN-260501-E5F5B0)_\n\n"
            "• Your policy covers all travelers\n"
            "  listed at the time of purchase\n\n"
            "• Policy documents are sent to your\n"
            "  email and saved here on WhatsApp\n\n"
            "• You can check status and details\n"
            "  anytime using the button below 👇"
        ),
        "faqs": [
            ("faq_pol_number",   "How do I find my policy number?"),
            ("faq_pol_covers",   "What does my policy cover?"),
            ("faq_pol_download", "How do I download my policy?"),
            ("faq_pol_change",   "Can I change policy details?"),
        ],
    },
    "hlp_boarding": {
        "emoji":      "🛫",
        "title":      "Boarding Pass",
        "action_id":  "hlp_act_boarding",
        "action_btn": "Upload boarding pass →",
        "action_body": "Ready to upload?",
        "info": (
            "🛫 *Help — Boarding Pass Upload*\n\n"
            "*What you need to know:*\n\n"
            "• Upload your boarding pass directly\n"
            "  in this chat as an image or PDF\n\n"
            "• *Accepted formats:*\n"
            "  JPG, PNG, WebP, PDF\n"
            "  _(max file size: 5 MB)_\n\n"
            "• Make sure these are clearly visible:\n"
            "  ✓ Passenger name\n"
            "  ✓ Booking reference\n"
            "  ✓ Flight number & date"
        ),
        "faqs": [
            ("faq_bp_formats",   "What file formats are accepted?"),
            ("faq_bp_visible",   "What must be visible on the pass?"),
            ("faq_bp_later",     "Can I upload my pass later?"),
            ("faq_bp_replace",   "How do I replace my boarding pass?"),
        ],
    },
    "hlp_claim": {
        "emoji":      "🏥",
        "title":      "Claim Support",
        "action_id":  "hlp_act_agent",
        "action_btn": "Send to an agent →",
        "action_body": "Need to speak to someone?",
        "info": (
            "🏥 *Help — Claim Support*\n\n"
            "*How TravelAssist works:*\n\n"
            "🔍 We monitor your flight automatically\n\n"
            "⚡ If a disruption is detected, we check\n"
            "   your eligibility instantly\n\n"
            "💰 Payout is triggered automatically —\n"
            "   no claim forms needed\n\n"
            "📱 You'll be notified here on WhatsApp\n\n"
            "_No forms. No stress.\n"
            "TravelAssist handles everything for you._"
        ),
        "faqs": [
            ("faq_clm_auto",     "How does automatic claiming work?"),
            ("faq_clm_covered",  "What disruptions are covered?"),
            ("faq_clm_payout",   "When will I receive my payout?"),
            ("faq_clm_tracked",  "What if my flight isn't tracked?"),
        ],
    },
}

_FAQ_ANSWERS: dict[str, str] = {
    "faq_buy_details": (
        "✈️ *Flight Details Needed*\n\n"
        "To buy cover you will need:\n\n"
        "• *Flight number* — e.g. P41234\n"
        "• *Departure airport* — search by city or code\n"
        "• *Arrival airport* — search by city or code\n"
        "• *Departure date & time*\n"
        "• *Arrival date & time*\n"
        "• *Booking reference* — from your e-ticket\n\n"
        "_Tip: Have your e-ticket open before you start._"
    ),
    "faq_buy_cover": (
        "✈️ *Choosing a Cover Type*\n\n"
        "After entering your flight details, available\n"
        "cover plans are shown for your route.\n\n"
        "Each plan shows:\n"
        "• Cover name and provider\n"
        "• Premium amount (₦)\n"
        "• Coverage benefit amount\n"
        "• Trigger type (e.g. 20-min delay)\n\n"
        "_Select the one that suits you best._"
    ),
    "faq_buy_group": (
        "✈️ *Buying Cover for a Group*\n\n"
        "Currently, each policy covers one traveller.\n\n"
        "To cover multiple passengers on the same flight,\n"
        "complete the purchase flow once per person.\n\n"
        "Each traveller will receive their own policy\n"
        "number and document."
    ),
    "faq_buy_receive": (
        "✈️ *Receiving Your Policy*\n\n"
        "After payment is confirmed:\n\n"
        "• Your policy document is sent to your *email*\n"
        "  within seconds\n\n"
        "• You can also *download it here on WhatsApp*\n"
        "  using 'Check my policy' → your policy → Download\n\n"
        "• Your *policy number* (starts with TA-) is shown\n"
        "  in the confirmation message"
    ),
    "faq_kyc_what": (
        "✅ *What is KYC?*\n\n"
        "KYC stands for *Know Your Customer*.\n\n"
        "It is a one-time identity verification required\n"
        "by financial regulations before a policy can be\n"
        "issued to you.\n\n"
        "It ensures your policy is linked to a verified\n"
        "identity and protects against fraud."
    ),
    "faq_kyc_types": (
        "✅ *Accepted ID Types*\n\n"
        "We accept two types of biometric ID:\n\n"
        "• *NIN* — National Identification Number\n"
        "  _(11 digits, issued by NIMC)_\n\n"
        "• *BVN* — Bank Verification Number\n"
        "  _(11 digits, linked to your bank account)_\n\n"
        "Make sure the name on your ID matches the\n"
        "name you provided for the policy."
    ),
    "faq_kyc_failed": (
        "✅ *KYC Was Rejected — What to Do*\n\n"
        "Common reasons for rejection:\n\n"
        "• Incorrect NIN or BVN number\n"
        "• Name mismatch with ID records\n"
        "• Expired or unregistered ID\n\n"
        "*What to do:*\n"
        "Double-check your 11-digit number and try again.\n"
        "You can also try the other ID type (NIN vs BVN).\n\n"
        "If the issue persists, contact support."
    ),
    "faq_kyc_time": (
        "✅ *How Long Does KYC Take?*\n\n"
        "KYC verification is *instant* in most cases.\n\n"
        "The system checks your ID against the national\n"
        "database in real time.\n\n"
        "If there is a delay (>30 seconds), it may be\n"
        "due to a temporary network issue — tap retry\n"
        "and it will complete normally."
    ),
    "faq_pay_declined": (
        "💳 *Why Was My Payment Declined?*\n\n"
        "Common reasons:\n\n"
        "• Insufficient funds in the account\n"
        "• Incorrect account number entered\n"
        "• Bank restriction on online transfers\n"
        "• Network timeout during processing\n\n"
        "*What to do:*\n"
        "Tap 'Resume pending payment', check your\n"
        "account details and retry. Your progress\n"
        "is saved — no need to re-enter everything."
    ),
    "faq_pay_details": (
        "💳 *Bank Details You Need*\n\n"
        "To complete payment you will need:\n\n"
        "• *Account number* — your 10-digit NUBAN\n"
        "• *Bank name* — search by typing the first\n"
        "  3 letters of your bank's name\n"
        "• *Account name* — auto-confirmed after\n"
        "  you enter the account number\n\n"
        "_Tip: Use the account you want the payout\n"
        "sent to if a claim is triggered._"
    ),
    "faq_pay_change": (
        "💳 *Changing Bank Details*\n\n"
        "You can update your bank details any time\n"
        "before you submit the final policy.\n\n"
        "While in the payment step, tap *0 (Back)*\n"
        "to return to the account number entry and\n"
        "re-enter your details.\n\n"
        "Once the policy is submitted, bank details\n"
        "cannot be changed. Contact support if needed."
    ),
    "faq_pay_time": (
        "💳 *Payment Processing Time*\n\n"
        "After you enter your bank details:\n\n"
        "• Bank confirmation usually takes *1–5 minutes*\n"
        "• Policy is issued *within seconds* of confirmation\n\n"
        "If you don't receive confirmation after 10 minutes:\n"
        "1. Check your bank account for a debit\n"
        "2. If debited, contact support with your\n"
        "   policy reference number\n"
        "3. If not debited, retry the payment"
    ),
    "faq_pol_number": (
        "📋 *Finding Your Policy Number*\n\n"
        "Your policy number starts with *TA-*\n"
        "_(e.g. TA-NG-TAIN-260501-E5F5B0)_\n\n"
        "You can find it:\n\n"
        "• In the confirmation message sent after purchase\n"
        "• In the policy document emailed to you\n"
        "• By tapping 'Check my policy' → 📱 My phone number"
    ),
    "faq_pol_covers": (
        "📋 *What Your Policy Covers*\n\n"
        "Your TravelAssist policy covers flight disruptions\n"
        "such as delays, cancellations, and diversions.\n\n"
        "Coverage details are shown on your policy document:\n"
        "• Trigger type (e.g. 20-min delay)\n"
        "• Benefit amount (₦)\n"
        "• Coverage window (departure to arrival)\n\n"
        "No action is needed from you — payouts are\n"
        "triggered automatically when eligible."
    ),
    "faq_pol_download": (
        "📋 *Downloading Your Policy Document*\n\n"
        "To download your policy PDF:\n\n"
        "1. Tap *Check my policy* from the main menu\n"
        "2. Find your policy (by phone, number, or flight)\n"
        "3. On the policy detail screen, tap\n"
        "   *Download Policy Document*\n\n"
        "The document is also emailed to you automatically\n"
        "at the email address provided during purchase."
    ),
    "faq_pol_change": (
        "📋 *Changing Policy Details*\n\n"
        "Once a policy is submitted and active, the core\n"
        "details (flight, traveller, cover) cannot be changed.\n\n"
        "If you made an error, please contact support:\n"
        "📧 support@ipurvey.com\n\n"
        "Minor corrections may be possible within\n"
        "24 hours of purchase."
    ),
    "faq_bp_formats": (
        "🛫 *Accepted Boarding Pass Formats*\n\n"
        "We accept the following file types:\n\n"
        "• *JPG / JPEG* — photo of physical boarding pass\n"
        "• *PNG* — screenshot of e-boarding pass\n"
        "• *WebP* — modern image format\n"
        "• *PDF* — airline-issued e-boarding pass\n\n"
        "_Maximum file size: 5 MB_\n\n"
        "Make sure the image is clear and not blurry."
    ),
    "faq_bp_visible": (
        "🛫 *What Must Be Visible on the Boarding Pass*\n\n"
        "The following information must be clearly readable:\n\n"
        "✓ *Passenger name* — must match policy\n"
        "✓ *Booking reference / PNR*\n"
        "✓ *Flight number* — e.g. P41234\n"
        "✓ *Departure date*\n"
        "✓ *Departure airport*\n\n"
        "_Tip: Avoid cropping out any of these fields._"
    ),
    "faq_bp_later": (
        "🛫 *Uploading Your Boarding Pass Later*\n\n"
        "Yes — you can upload your boarding pass\n"
        "at any time before or after your flight.\n\n"
        "To upload later:\n"
        "1. Return to the main menu\n"
        "2. Tap *Submit Boarding Pass*\n"
        "3. Select your policy from the list\n"
        "4. Upload your pass\n\n"
        "_Your eligibility check runs automatically\n"
        "once the boarding pass is linked._"
    ),
    "faq_bp_replace": (
        "🛫 *Replacing Your Boarding Pass*\n\n"
        "If you need to replace a previously uploaded\n"
        "boarding pass (e.g. wrong file uploaded):\n\n"
        "1. Tap *Submit Boarding Pass* from main menu\n"
        "2. Select the policy\n"
        "3. The system will show the existing upload\n"
        "   and offer a *Replace* option\n"
        "4. Upload the correct file\n\n"
        "_The old file is replaced immediately._"
    ),
    "faq_clm_auto": (
        "🏥 *How Automatic Claiming Works*\n\n"
        "TravelAssist monitors your flight in real time.\n\n"
        "When a disruption (delay, cancellation) is detected:\n\n"
        "1️⃣ Your eligibility is checked automatically\n"
        "2️⃣ If eligible, payout is triggered immediately\n"
        "3️⃣ You receive a WhatsApp notification\n"
        "4️⃣ Funds are transferred to your linked account\n\n"
        "_No forms, no calls, no waiting._"
    ),
    "faq_clm_covered": (
        "🏥 *What Disruptions Are Covered*\n\n"
        "Coverage depends on your selected plan, but\n"
        "typically includes:\n\n"
        "• *Flight delay* — 20 min, 1 hr, or 2 hr+\n"
        "• *Flight cancellation* — airline-initiated\n"
        "• *Diversion* — flight diverted to another airport\n\n"
        "Check your policy document for the exact trigger\n"
        "condition and benefit amount for your plan."
    ),
    "faq_clm_payout": (
        "🏥 *When Will I Receive My Payout?*\n\n"
        "Once eligibility is confirmed:\n\n"
        "• You receive a *WhatsApp notification* immediately\n"
        "• Bank transfer is initiated *within 1 business day*\n"
        "• Funds typically arrive *within 24–48 hours*\n\n"
        "If you haven't received your payout after 48 hours,\n"
        "contact our support team with your policy number."
    ),
    "faq_clm_tracked": (
        "🏥 *What If My Flight Isn't Tracked?*\n\n"
        "Our system tracks most scheduled commercial flights.\n\n"
        "If your flight isn't appearing:\n\n"
        "• Ensure your boarding pass is uploaded\n"
        "• Check that your flight number is correct\n"
        "• Contact support if the issue persists\n\n"
        "📧 support@ipurvey.com\n\n"
        "_Charter and private flights are not covered._"
    ),
}


async def show_exit_help_confirm(wa_id: str, phone_number_id: Optional[str]):
    await _send_buttons(
        wa_id,
        "❌ *Exit Help*\n\nAre you sure you want to exit help?",
        [
            {"id": "cx_yes_help", "title": "❌ Yes, exit help"},
            {"id": "cx_no_help",  "title": "↩️ No, continue"},
        ],
        phone_number_id,
    )


async def start_help_flow(
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session = await get_session(wa_id) or {}
    session.setdefault("temp_data", {})[HELP_FLOW_KEY] = {
        "active": True,
        "step": "hlp_menu",
    }
    if "user_id" not in session:
        session["user_id"] = wa_id
    await save_session(session)

    await _send_text(
        to=wa_id,
        body="🆘 *Help*\n\n🙋Where do you need help today?\nChoose a topic below 👇",
        phone_number_id=phone_number_id,
    )

    await _send_buttons(
        to=wa_id,
        body="📦 *Coverage & Verification*",
        buttons=[
            {"id": "hlp_buying",   "title": "✈️ Buying cover"},
            {"id": "hlp_kyc",      "title": "💳 KYC verification"},
            {"id": "hlp_payment",  "title": "💳 Payment issues"},
        ],
        phone_number_id=phone_number_id,
    )

    await _send_buttons(
        to=wa_id,
        body="📋 *Policy & Travel Docs*",
        buttons=[
            {"id": "hlp_policy",   "title": "📋 My policy"},
            {"id": "hlp_boarding", "title": "🛫 Boarding pass"},
            {"id": "hlp_claim",    "title": "🏥 Claim support"},
        ],
        phone_number_id=phone_number_id,
    )


async def _show_topic(
    session: dict,
    wa_id: str,
    topic_key: str,
    phone_number_id: Optional[str],
):
    meta = _TOPIC_META[topic_key]
    await _set_step(session, "hlp_topic")
    await _save_data(session, "current_topic", topic_key)

    await _send_text(wa_id, meta["info"], phone_number_id)

    rows = [
        {"id": faq_id, "title": faq_title}
        for faq_id, faq_title in meta["faqs"]
    ]
    await _send_list(
        wa_id,
        "Tap a question below for a detailed answer 👇",
        "View FAQs",
        [{"title": f"{meta['emoji']} FAQs", "rows": rows}],
        phone_number_id,
        header=f"{meta['emoji']} {meta['title']} — FAQs",
    )

    if topic_key != "hlp_claim":
        await _send_buttons(
            wa_id,
            meta["action_body"],
            [
                {"id": meta["action_id"], "title": meta["action_btn"]},
                {"id": "hlp_back",        "title": "↩️ Back to topics"},
            ],
            phone_number_id,
        )
    else:
        await _send_buttons(
            wa_id,
            "What would you like to do?",
            [{"id": "hlp_back", "title": "↩️ Back to topics"}],
            phone_number_id,
        )


async def _show_faq_answer(
    session: dict,
    wa_id: str,
    faq_id: str,
    topic_key: str,
    phone_number_id: Optional[str],
):
    answer = _FAQ_ANSWERS.get(faq_id)
    if not answer:
        await _show_topic(session, wa_id, topic_key, phone_number_id)
        return

    await _set_step(session, "hlp_faq")
    await _save_data(session, "current_faq", faq_id)

    meta = _TOPIC_META[topic_key]

    await _send_text(wa_id, answer, phone_number_id)
    if topic_key != "hlp_claim":
        await _send_buttons(
            wa_id,
            "What would you like to do next?",
            [
                {"id": meta["action_id"],    "title": meta["action_btn"]},
                {"id": "hlp_back_to_topic",  "title": f"↩️ Back to {meta['title']}"},
            ],
            phone_number_id,
        )
    else:
        await _send_buttons(
            wa_id,
            "What would you like to do next?",
            [{"id": "hlp_back_to_topic", "title": f"↩️ Back to {meta['title']}"}],
            phone_number_id,
        )


async def handle_help_flow(
    message,
    sender_wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session, flow = await _get_flow_state(sender_wa_id)
    step = flow.get("step", "hlp_menu")
    data = flow.get("data", {})

    reply_id = None
    if message.type == "interactive" and message.interactive:
        if message.interactive.type == "list_reply" and message.interactive.list_reply:
            reply_id = message.interactive.list_reply.id
        elif (
            message.interactive.type == "button_reply"
            and message.interactive.button_reply
        ):
            reply_id = message.interactive.button_reply.id

    if step == "hlp_menu":
        await _handle_menu_selection(session, sender_wa_id, reply_id, phone_number_id)

    elif step == "hlp_topic":
        topic_key = data.get("current_topic", "")
        if reply_id and reply_id in _FAQ_ANSWERS:
            await _show_faq_answer(session, sender_wa_id, reply_id, topic_key, phone_number_id)
        elif reply_id in ("hlp_back", "hlp_back_to_menu"):
            await _reset(session)
            await start_help_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        elif reply_id and reply_id.startswith("hlp_act_"):
            await _handle_action(session, sender_wa_id, reply_id, phone_number_id)
        else:
            if topic_key in _TOPIC_META:
                await _show_topic(session, sender_wa_id, topic_key, phone_number_id)
            else:
                await _reset(session)
                await start_help_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)

    elif step == "hlp_faq":
        topic_key = data.get("current_topic", "")
        if reply_id == "hlp_back_to_topic" and topic_key in _TOPIC_META:
            await _show_topic(session, sender_wa_id, topic_key, phone_number_id)
        elif reply_id in ("hlp_back", "hlp_back_to_menu"):
            await _reset(session)
            await start_help_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        elif reply_id and reply_id.startswith("hlp_act_"):
            await _handle_action(session, sender_wa_id, reply_id, phone_number_id)
        else:
            faq_id = data.get("current_faq", "")
            if faq_id and topic_key in _TOPIC_META:
                await _show_faq_answer(session, sender_wa_id, faq_id, topic_key, phone_number_id)
            elif topic_key in _TOPIC_META:
                await _show_topic(session, sender_wa_id, topic_key, phone_number_id)
            else:
                await _reset(session)
                await start_help_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)

    elif step == "hlp_agent_wait":
        await _handle_action(session, sender_wa_id, reply_id, phone_number_id)

    else:
        await _reset(session)
        await start_help_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)


async def _handle_menu_selection(
    session: dict,
    wa_id: str,
    reply_id: Optional[str],
    phone_number_id: Optional[str],
):
    if reply_id in _TOPIC_META:
        await _show_topic(session, wa_id, reply_id, phone_number_id)

    elif reply_id == "hlp_agent":
        await _set_step(session, "hlp_agent_wait")
        await _send_text(
            wa_id,
            "🤝 *Connecting you to an agent...*\n\n"
            "Please hold on — a live support agent\n"
            "will be with you shortly.",
            phone_number_id,
        )
        await _send_text(
            wa_id,
            "📞 *Live Agent Support*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "WhatsApp: *+234 800 000 0000*\n"
            "Email:       *support@ipurvey.com*\n"
            "Hours:      *Mon–Fri, 8am–6pm WAT*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "While you wait, you can:\n"
            "• Check your policy status\n"
            "• Review your cover details",
            phone_number_id,
        )
        await _send_buttons(
            wa_id,
            "What would you like to do?",
            [
                {"id": "hlp_act_policy", "title": "📋 Check my policy"},
                {"id": "hlp_home",       "title": "🏠 Close chat"},
            ],
            phone_number_id,
        )

    else:
        await _reset(session)
        await start_help_flow(wa_id=wa_id, phone_number_id=phone_number_id)


async def _handle_action(
    session: dict,
    wa_id: str,
    reply_id: Optional[str],
    phone_number_id: Optional[str],
):
    if reply_id == "hlp_act_buy":
        await _reset(session)
        from app.services.buy_cover_flow_service import start_buy_cover_flow
        await start_buy_cover_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_act_kyc":
        await _reset(session)
        from app.services.kyc_flow_service import start_kyc_flow
        await start_kyc_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_act_pay":
        await _reset(session)
        from app.services.payment_flow_service import start_payment_flow
        await start_payment_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_act_policy":
        await _reset(session)
        from app.services.check_policy_flow_service import start_check_policy_flow
        await start_check_policy_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_act_boarding":
        await _reset(session)
        from app.services.bp_link_flow_service import start_bp_link_flow
        await start_bp_link_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_act_agent":
        await _set_step(session, "hlp_agent_wait")
        await _send_text(
            wa_id,
            "🤝 *Connecting you to an agent...*\n\n"
            "Please hold on — a live support agent\n"
            "will be with you shortly.",
            phone_number_id,
        )
        await _send_text(
            wa_id,
            "📞 *Live Agent Support*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "WhatsApp: *+234 800 000 0000*\n"
            "Email:       *support@ipurvey.com*\n"
            "Hours:      *Mon–Fri, 8am–6pm WAT*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "While you wait, you can:\n"
            "• Check your policy status\n"
            "• Review your cover details",
            phone_number_id,
        )
        await _send_buttons(
            wa_id,
            "What would you like to do?",
            [
                {"id": "hlp_act_policy", "title": "📋 Check my policy"},
                {"id": "hlp_home",       "title": "🏠 Close chat"},
            ],
            phone_number_id,
        )

    elif reply_id == "hlp_home":
        await _reset(session)
        from app.services.auto_reply_service import send_main_menu
        await send_main_menu(to=wa_id, phone_number_id=phone_number_id)

    elif reply_id == "hlp_back":
        await _reset(session)
        await start_help_flow(wa_id=wa_id, phone_number_id=phone_number_id)

    else:
        await _reset(session)
        await start_help_flow(wa_id=wa_id, phone_number_id=phone_number_id)
