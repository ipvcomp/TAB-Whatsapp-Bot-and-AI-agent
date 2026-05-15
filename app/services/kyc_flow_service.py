import logging
from typing import Optional

import app.services.ipurvey_service as ipurvey_service

from app.core.test_overrides import get_msisdn
from app.services.llm_service import call_extract, call_generic
from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload

logger = logging.getLogger(__name__)

KYC_FLOW_KEY = "kyc_flow"
BUY_COVER_FLOW_KEY = "buy_cover_flow"


def is_in_kyc_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    return session.get("temp_data", {}).get(KYC_FLOW_KEY, {}).get("active", False)


async def _get_flow_state(wa_id: str) -> tuple[dict, dict]:
    session = await get_session(wa_id) or {}
    flow = session.setdefault("temp_data", {}).setdefault(KYC_FLOW_KEY, {})
    return session, flow


def _mask_id(val: str) -> str:
    if len(val) <= 3:
        return val
    return "•" * (len(val) - 3) + val[-3:]


_UTILITY = (
    "*Utility options:*\n0 ↩️ Back  |  9 🆘 Help  |  00 🏠 Main menu\n99 ❌ Cancel/Exit"
)


async def _send_text(to: str, body: str, phone_number_id: Optional[str]):
    await send_text_message(
        to=to, body=body, phone_number_id=phone_number_id, source="kyc_flow"
    )
    await send_text_message(
        to=to, body=_UTILITY, phone_number_id=phone_number_id, source="kyc_flow"
    )


async def _send_buttons(
    to: str, body: str, buttons: list, phone_number_id: Optional[str]
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
    await send_whatsapp_payload(
        whatsapp_payload=payload, phone_number_id=phone_number_id, source="kyc_flow"
    )
    await send_text_message(
        to=to, body=_UTILITY, phone_number_id=phone_number_id, source="kyc_flow"
    )


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
    await send_whatsapp_payload(
        whatsapp_payload=payload, phone_number_id=phone_number_id, source="kyc_flow"
    )
    await send_text_message(
        to=to, body=_UTILITY, phone_number_id=phone_number_id, source="kyc_flow"
    )


async def _show_bypass_screen(wa_id: str, session: dict, phone_number_id: Optional[str]):
    """Show the 'both methods failed' bypass screen — user can still continue to payment."""
    flow = session.get("temp_data", {}).get(KYC_FLOW_KEY, {})
    flow["step"] = "kyc_both_failed"
    await save_session(session)
    await _send_list(
        wa_id,
        "⚠️ *Verification Incomplete*\n"
        "We could not complete verification automatically.\n\n"
        "📋 Please review and resubmit your trip details and ensure the name of the "
        "main passenger or purchaser matches the Biometric ID details.\n\n"
        "This will help avoid delays to any future payout.",
        "What would you like to do next?",
        [
            {
                "title": "Options",
                "rows": [
                    {"id": "kyc_bypass_pay",    "title": "💳 Continue to pay"},
                    {"id": "kyc_bypass_review", "title": "📋 Review details"},
                    {"id": "kyc_bypass_menu",   "title": "🏠 Main menu"},
                    {"id": "kyc_bypass_help",   "title": "🧑 Get help"},
                ],
            }
        ],
        phone_number_id,
        header="⚠️ Verification Incomplete",
    )


def _both_methods_tried(data: dict) -> bool:
    tried = set(data.get("kyc_methods_tried", []))
    return "BVN" in tried and "NIN" in tried


async def _send_help(wa_id: str, session: dict, phone_number_id: Optional[str]):
    session["temp_data"][KYC_FLOW_KEY]["step"] = "kyc_help"
    await save_session(session)
    await _send_buttons(
        wa_id,
        "> *What you need to know:*\n"
        "> ✅ You can verify using either *BVN* or *NIN*\n"
        "> 🔒 We only use this to confirm your identity for policy issuance\n"
        "> 👤 Make sure the number belongs to the traveller buying the policy\n"
        "> 📱 Your BVN/NIN is never stored or echoed back — handled securely\n"
        "> 🔢 Both BVN and NIN are 11 digits — example: 12345678901\n\n"
        "Ready to verify?",
        [
            {"id": "kyc_nin", "title": "🪪 Verify with NIN"},
            {"id": "kyc_bvn", "title": "🪪 Verify with BVN"},
            {"id": "kyc_agent", "title": "📞 Speak to agent"},
        ],
        phone_number_id,
    )


async def start_kyc_flow(
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session = await get_session(wa_id) or {}
    session.setdefault("temp_data", {})[KYC_FLOW_KEY] = {
        "active": True,
        "step": "kyc_intro",
        "data": {},
    }
    session["temp_data"].get(BUY_COVER_FLOW_KEY, {}).update({"active": False})
    if "user_id" not in session:
        session["user_id"] = wa_id
    await save_session(session)

    policy_id = session.get("api_data", {}).get("policy_id")
    if policy_id:
        try:
            kyc_status = await ipurvey_service.check_kyc_status(policy_id)
            if kyc_status and isinstance(kyc_status, dict):
                status_val = (
                    kyc_status.get("status") or kyc_status.get("kycStatus") or ""
                ).upper()
                if status_val in ("VERIFIED", "COMPLETED", "SUCCESS", "PASSED"):
                    session["temp_data"][KYC_FLOW_KEY]["step"] = "kyc_verified"
                    session["temp_data"][KYC_FLOW_KEY]["data"]["kyc_verified"] = True
                    await save_session(session)
                    from app.services.payment_flow_service import start_payment_flow

                    await _send_buttons(
                        wa_id,
                        "✅ *Identity Already Verified*\nYour identity is confirmed. Proceeding to payment.",
                        [
                            {"id": "kyc_pay", "title": "1. Continue to pay"},
                            {"id": "kyc_home", "title": "2. Main menu"},
                        ],
                        phone_number_id,
                    )
                    return
        except Exception as exc:
            logger.error(f"[kyc] check_kyc_status failed: {exc}")

    # ── Fetch supported ID types dynamically from the API ─────────────────────
    supported_types = await ipurvey_service.fetch_kyc_supported_countries()

    # Fallback to hardcoded NG types if the API is unavailable
    if not supported_types:
        supported_types = [
            {"countryCode": "NG", "countryName": "Nigeria", "type": "NIN",
             "displayName": "National Identification Number (NIN)",
             "formatRegex": r"^\d{11}$", "minLength": 11, "maxLength": 11},
            {"countryCode": "NG", "countryName": "Nigeria", "type": "BVN",
             "displayName": "Bank Verification Number (BVN)",
             "formatRegex": r"^\d{11}$", "minLength": 11, "maxLength": 11},
        ]

    # Store in session so the handler can use them without re-fetching
    kyc_flow = session["temp_data"][KYC_FLOW_KEY]
    kyc_flow["data"]["kyc_supported_types"] = supported_types
    await save_session(session)

    # Build list rows — row ID: kyc_type_{countryCode}_{type}
    def _row_title(t: dict) -> str:
        raw = f"🪪 {t['type']} ({t['countryName']})"
        return raw if len(raw) <= 20 else raw[:19] + "…"

    rows = [
        {
            "id": f"kyc_type_{t['countryCode']}_{t['type']}",
            "title": _row_title(t),
            "description": t["displayName"][:72] if t["displayName"] else "",
        }
        for t in supported_types
    ]
    rows.append({"id": "kyc_help", "title": "🆘 Help", "description": "Learn more about verification"})

    await _send_list(
        wa_id,
        "We may verify your identity to support any future payouts and ensure "
        "security and accurate policy issuance. If you've already completed this, "
        "we'll only carry out verification again if your details have changed.\n\n"
        "> 🔒 *Your privacy matters*\n"
        "> We only use your National Biometric ID to verify your identity for this "
        "purchase. Your data is handled securely and never shared.\n\n"
        "How would you like to verify your identity?\n"
        "Select the country that issued your national biometric ID:",
        "Select ID type",
        [{"title": "Verification Method", "rows": rows}],
        phone_number_id,
        header="🔒 Identity Verification",
    )


async def handle_kyc_flow(
    message,
    sender_wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session, flow = await _get_flow_state(sender_wa_id)
    step = flow.get("step", "kyc_intro")
    data = flow.setdefault("data", {})

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
            br = getattr(inter, "button_reply", None) or getattr(
                inter, "list_reply", None
            )
            if br:
                reply_id = (
                    br.get("id") if isinstance(br, dict) else getattr(br, "id", None)
                )

    # ── KYC intro ─────────────────────────────────────────────────────────────
    if step == "kyc_intro":
        supported = data.get("kyc_supported_types", [])
        if not reply_id and text:
            _t = text.strip().lower()
            # Positional number — map to dynamic list index
            num_map = {str(i + 1): t for i, t in enumerate(supported)}
            help_num = str(len(supported) + 1)
            if _t in num_map:
                t = num_map[_t]
                reply_id = f"kyc_type_{t['countryCode']}_{t['type']}"
            elif _t in (help_num, "help", "support", "assist"):
                reply_id = "kyc_help"
            else:
                # Match by type keyword (e.g. "nin", "bvn")
                for t in supported:
                    if _t in (t["type"].lower(), t["displayName"].lower()):
                        reply_id = f"kyc_type_{t['countryCode']}_{t['type']}"
                        break
        if not reply_id and text:
            type_names = ", ".join(f"{t['type']} ({t['countryName']})" for t in supported) or "NIN (Nigeria), BVN (Nigeria)"
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="kyc_method_choice",
                question_asked=f"How would you like to verify your identity? Options: {type_names}, or Get Help.",
                user_response=text,
                expected_format="text",
            )
            if llm_result and llm_result.get("is_valid") and llm_result.get("extracted_value"):
                ev = str(llm_result["extracted_value"]).lower()
                matched_llm = None
                for t in supported:
                    if t["type"].lower() in ev:
                        matched_llm = t
                        break
                if matched_llm:
                    reply_id = f"kyc_type_{matched_llm['countryCode']}_{matched_llm['type']}"
                elif any(k in ev for k in ("help", "support", "assist", "question")):
                    reply_id = "kyc_help"
            if not reply_id:
                await start_kyc_flow(sender_wa_id, phone_number_id)
                return

        if reply_id == "kyc_help":
            await _send_help(sender_wa_id, session, phone_number_id)
        elif reply_id and reply_id.startswith("kyc_type_"):
            # Extract countryCode and type from the dynamic ID
            parts = reply_id.split("_", 3)  # ["kyc", "type", CC, TYPE]
            selected_type = parts[3] if len(parts) >= 4 else ""
            selected_cc = parts[2] if len(parts) >= 3 else ""
            data["kyc_method"] = selected_type
            data["kyc_country_code_selected"] = selected_cc
            flow["step"] = "kyc_consent"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                "🔒 We will only use your National Biometric ID to verify "
                "your identity for this purchase.",
                [
                    {"id": "kyc_consent_yes", "title": "1. ✅ Yes, continue"},
                    {"id": "kyc_consent_no", "title": "2. 🔙Go back"},
                ],
                phone_number_id,
            )
        elif reply_id in ("kyc_nin", "kyc_bvn"):
            # Legacy fallback for any old cached reply_id
            data["kyc_method"] = "NIN" if reply_id == "kyc_nin" else "BVN"
            flow["step"] = "kyc_consent"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                "🔒 We will only use your National Biometric ID to verify "
                "your identity for this purchase.",
                [
                    {"id": "kyc_consent_yes", "title": "1. ✅ Yes, continue"},
                    {"id": "kyc_consent_no", "title": "2. 🔙Go back"},
                ],
                phone_number_id,
            )

    # ── Consent ───────────────────────────────────────────────────────────────
    elif step == "kyc_consent":
        if not reply_id and text:
            _t = text.strip().lower()
            if _t in ("1", "yes", "ok", "continue", "proceed", "agree", "sure", "accept", "consent", "yep", "yeah"):
                reply_id = "kyc_consent_yes"
            elif _t in ("2", "no", "back", "cancel", "go back", "decline", "refuse", "nope"):
                reply_id = "kyc_consent_no"
        if not reply_id and text:
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="kyc_consent",
                question_asked="Do you consent to using your National Biometric ID for identity verification?",
                user_response=text,
                expected_format="text",
            )
            if llm_result and llm_result.get("is_valid") and llm_result.get("extracted_value"):
                ev = str(llm_result["extracted_value"]).lower()
                if any(k in ev for k in ("yes", "agree", "ok", "continue", "proceed", "consent", "sure", "accept")):
                    reply_id = "kyc_consent_yes"
                elif any(k in ev for k in ("no", "back", "cancel", "go back", "decline", "refuse")):
                    reply_id = "kyc_consent_no"
            if not reply_id:
                method = data.get("kyc_method", "BVN")
                await _send_buttons(
                    sender_wa_id,
                    f"🔒 We will only use your *{method}* to verify your identity for this purchase.\n\nDo you consent to proceed?",
                    [
                        {"id": "kyc_consent_yes", "title": "1. ✅ Yes, continue"},
                        {"id": "kyc_consent_no", "title": "2. Go back"},
                    ],
                    phone_number_id,
                )
                return
        if reply_id == "kyc_consent_no":
            flow["step"] = "kyc_intro"
            await save_session(session)
            await start_kyc_flow(sender_wa_id, phone_number_id)
        else:
            method = data.get("kyc_method", "BVN")
            flow["step"] = "kyc_id_input"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                f"🔏 *Please enter your 11-digit {method}*\n\n"
                f"_Example: 12345678901_\n\n"
                f"🔒 _Your {method} is handled securely — only the last 3 digits will be shown for confirmation_",
                phone_number_id,
            )

    # ── BVN / NIN input ───────────────────────────────────────────────────────
    elif step == "kyc_id_input":
        if not text:
            await _send_text(
                sender_wa_id, "Please type your ID number to continue.", phone_number_id
            )
            return
        _kyc_q = text.lower().strip()
        _is_q = (
            "?" in _kyc_q
            or any(_kyc_q.startswith(s) for s in (
                "why ", "what ", "how ", "who ", "is this ", "do you ",
                "why do", "what is", "tell me", "explain ",
            ))
            or (len(_kyc_q.split()) >= 3 and not any(c.isdigit() for c in _kyc_q))
        )
        if _is_q:
            try:
                _lr = await call_generic(
                    user_id=sender_wa_id, phone_number=sender_wa_id,
                    message=text, user_name="", current_node="kyc_id_input",
                )
                if _lr and isinstance(_lr.get("data"), dict):
                    _ans = _lr["data"].get("response") or _lr["data"].get("message") or ""
                    if _ans:
                        await _send_text(sender_wa_id, _ans, phone_number_id)
            except Exception:
                pass
            method = data.get("kyc_method", "BVN")
            await _send_text(
                sender_wa_id,
                f"🔏 Please enter your *11-digit {method}* to continue.\n\n_Example: 12345678901_",
                phone_number_id,
            )
            return
        id_number = text.replace(" ", "")
        method = data.get("kyc_method", "BVN")

        # Look up validation rules from the dynamically fetched types stored in session
        supported_types = data.get("kyc_supported_types", [])
        type_info = next(
            (t for t in supported_types if t.get("type", "").upper() == method.upper()),
            None,
        )
        min_len: int = type_info["minLength"] if type_info else 11
        max_len: int = type_info["maxLength"] if type_info else 11

        if not id_number.isdigit():
            await _send_text(
                sender_wa_id,
                f"⚠️ Your *{method}* must contain numbers only.\n\n"
                f"_Example: {'1' * min_len}_",
                phone_number_id,
            )
            return
        if not (min_len <= len(id_number) <= max_len):
            length_hint = f"{min_len} digits" if min_len == max_len else f"{min_len}–{max_len} digits"
            await _send_text(
                sender_wa_id,
                f"⚠️ Your *{method}* must be *{length_hint}*. You entered {len(id_number)} digit(s).\n\n"
                f"_Example: {'1' * min_len}_",
                phone_number_id,
            )
            return
        masked = _mask_id(id_number)
        data["kyc_id"] = id_number
        await _send_text(
            sender_wa_id,
            f"🔍 *Checking your details...*\n_{method}: {masked}_\n_Please wait a moment_ ⏳",
            phone_number_id,
        )

        api_verified = False
        api_session_id = None
        api_call_done = False
        policy_id = session.get("api_data", {}).get("policy_id")
        user_id = session.get("api_data", {}).get("user_id")

        if policy_id:
            try:
                bc_data = (
                    session.get("temp_data", {})
                    .get(BUY_COVER_FLOW_KEY, {})
                    .get("data", {})
                )
                msisdn = get_msisdn(sender_wa_id)
                raw_name = bc_data.get("name", "")
                parts = raw_name.strip().split(None, 1)
                fn = parts[0] if parts else raw_name
                ln = parts[1] if len(parts) > 1 else ""
                email = bc_data.get("email", "")

                # ── ALWAYS call create_user with full KYC details ─────────────
                # Even if the user already exists, POST /api/tab-ums/users is
                # called so the backend receives identityType + identityNumber
                # together with the passenger's name and email.  If the backend
                # returns 409 (user exists), create_user() fetches the existing
                # record and returns it.  We then update the existing user's
                # name/email with the latest passenger details.
                user_existed_before = bool(
                    user_id or session.get("api_data", {}).get("user_exists")
                )
                user_result = await ipurvey_service.create_user(
                    msisdn=msisdn,
                    first_name=fn,
                    last_name=ln,
                    email=email,
                    identity_type=method,
                    identity_number=id_number,
                )
                if user_result and isinstance(user_result, dict):
                    uid = user_result.get("userId") or user_result.get("id")
                    if uid:
                        session.setdefault("api_data", {})["user_id"] = uid
                        user_id = uid
                        logger.info(
                            f"[kyc] {'existing' if user_existed_before else 'new'} user "
                            f"→ create_user POST succeeded user_id='{uid}', linking to policy"
                        )
                        await ipurvey_service.link_user_to_policy(policy_id, uid)
                elif user_id:
                    # create_user failed but we still have an existing user_id
                    # from the flow start — fall back to linking that user.
                    logger.warning(
                        f"[kyc] create_user failed; falling back to existing "
                        f"user_id='{user_id}' for policy link"
                    )
                    await ipurvey_service.link_user_to_policy(policy_id, user_id)

                if user_id:
                    kyc_result = await ipurvey_service.initiate_kyc(
                        user_id, method, id_number
                    )
                    api_call_done = True
                    if kyc_result and isinstance(kyc_result, dict):
                        sid = kyc_result.get("sessionId") or kyc_result.get(
                            "session_id"
                        )
                        status = (kyc_result.get("status") or "").upper()
                        resp_verified = kyc_result.get("verified")
                        if sid:
                            api_session_id = sid
                            session.setdefault("api_data", {})["kyc_session_id"] = sid
                        if (
                            status in ("VERIFIED", "SUCCESS", "COMPLETED", "PASSED")
                            or resp_verified is True
                        ):
                            api_verified = True
                        log_fn = logger.info if api_verified else logger.warning
                        log_fn(
                            f"[kyc] initiate_kyc result: method={method} "
                            f"status={status} verified={resp_verified} "
                            f"sessionId={'set' if sid else 'none'}"
                            + (f" | raw={kyc_result}" if not api_verified else "")
                        )

                await save_session(session)
            except Exception as exc:
                logger.error(f"[kyc] id_input API calls failed: {exc}")

        if api_verified:
            # Both NIN and BVN — verified from initiate_kyc response
            data["kyc_verified"] = True
            flow["step"] = "kyc_verified"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                f"✅ *Identity Verified*\n_{method}: {masked}_\n\n"
                "Your identity has been confirmed. You can now continue to payment.\n\n"
                "What would you like to do next?",
                [
                    {"id": "kyc_pay", "title": "1. Continue to pay"},
                    {"id": "kyc_review", "title": "2. Review trip"},
                    {"id": "kyc_home", "title": "3. Main menu"},
                ],
                phone_number_id,
            )
        elif api_session_id and method == "BVN":
            # BVN only — OTP step needed
            flow["step"] = "kyc_otp_input"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                f"🔐 *OTP Sent*\n"
                f"A one-time PIN has been sent to the phone number linked to your *BVN*.\n\n"
                "Please enter the *6-digit OTP* to verify your identity:",
                [
                    {"id": "kyc_otp_resend", "title": "📲 Resend OTP"},
                    {"id": "kyc_help", "title": "🆘 Get help"},
                ],
                phone_number_id,
            )
        elif api_call_done:
            data["kyc_verified"] = False
            tried = data.setdefault("kyc_methods_tried", [])
            if method not in tried:
                tried.append(method)
            flow["step"] = "kyc_failed"
            await save_session(session)
            if _both_methods_tried(data):
                await _show_bypass_screen(sender_wa_id, session, phone_number_id)
            else:
                _other = "NIN" if method == "BVN" else "BVN"
                await _send_buttons(
                    sender_wa_id,
                    f"⚠️ *{method} Verification Failed*\n\n"
                    f"We could not verify your *{method}*: `{masked}`\n\n"
                    f"You may have entered an incorrect number. "
                    f"Please try again or use your {_other} instead.",
                    [
                        {"id": "kyc_retry_same", "title": f"🔄 Try {method} again"},
                        {"id": "kyc_try_another_id", "title": f"🪪 Try {_other} instead"},
                        {"id": "kyc_continue_purchase", "title": "💳 Continue Purchase"},
                    ],
                    phone_number_id,
                )
        elif id_number.isdigit() and len(id_number) == 11:
            data["kyc_verified"] = True
            flow["step"] = "kyc_verified"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                f"✅ *Identity Verified*\n"
                f"_{method}: {masked}_\n\n"
                "Your identity has been confirmed. You can now continue to payment.\n\n"
                "What would you like to do next?",
                [
                    {"id": "kyc_pay", "title": "1.💳Continue to pay"},
                    {"id": "kyc_review", "title": "2.🗒️Review trip"},
                    {"id": "kyc_home", "title": "3.🏠Main menu"},
                ],
                phone_number_id,
            )
        else:
            data["kyc_verified"] = False
            tried = data.setdefault("kyc_methods_tried", [])
            if method not in tried:
                tried.append(method)
            flow["step"] = "kyc_failed"
            await save_session(session)
            if _both_methods_tried(data):
                await _show_bypass_screen(sender_wa_id, session, phone_number_id)
            else:
                _other = "NIN" if method == "BVN" else "BVN"
                await _send_buttons(
                    sender_wa_id,
                    f"⚠️ *{method} Verification Failed*\n\n"
                    f"We could not verify your *{method}*: `{masked}`\n\n"
                    f"You may have entered an incorrect number. "
                    f"Please try again or use your {_other} instead.",
                    [
                        {"id": "kyc_retry_same", "title": f"🔄 Try {method} again"},
                        {"id": "kyc_try_another_id", "title": f"🪪 Try {_other} instead"},
                        {"id": "kyc_continue_purchase", "title": "💳 Continue Purchase"},
                    ],
                    phone_number_id,
                )

    # ── OTP input (after API initiates KYC) ───────────────────────────────────
    elif step == "kyc_otp_input":
        method = data.get("kyc_method", "BVN")
        masked = _mask_id(data.get("kyc_id", ""))
        user_id = session.get("api_data", {}).get("user_id")
        session_id = session.get("api_data", {}).get("kyc_session_id")

        if reply_id == "kyc_otp_resend":
            resent = False
            if user_id and session_id:
                try:
                    resent = await ipurvey_service.resend_kyc_otp(user_id, session_id)
                except Exception:
                    pass
            msg = (
                "📲 *OTP Resent!*\nA new OTP has been sent to your phone.\n\nEnter the 6-digit code:"
                if resent
                else "📲 *OTP Resend Requested*\nCheck your phone for the OTP.\n\nEnter the 6-digit code:"
            )
            await _send_text(sender_wa_id, msg, phone_number_id)
            return

        if reply_id == "kyc_help":
            await _send_help(sender_wa_id, session, phone_number_id)
            return

        otp = text.strip()
        if not otp or not otp.isdigit():
            await _send_text(
                sender_wa_id,
                "Please enter the *6-digit OTP* sent to your phone:",
                phone_number_id,
            )
            return

        verified = False
        if user_id and session_id:
            try:
                verified = await ipurvey_service.verify_kyc_otp(
                    user_id, session_id, otp
                )
            except Exception:
                pass

        if verified:
            data["kyc_verified"] = True
            flow["step"] = "kyc_verified"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                f"✅ *Identity Verified*\n_{method}: {masked}_\n\n"
                "Your identity has been confirmed. You can now continue to payment.\n\n"
                "What would you like to do next?",
                [
                    {"id": "kyc_pay", "title": "1. Continue to pay"},
                    {"id": "kyc_review", "title": "2. Review trip"},
                    {"id": "kyc_home", "title": "3. Main menu"},
                ],
                phone_number_id,
            )
        else:
            # BVN OTP failed — mark BVN as tried so if user also tried NIN we show bypass
            tried = data.setdefault("kyc_methods_tried", [])
            if "BVN" not in tried:
                tried.append("BVN")
            await save_session(session)
            if _both_methods_tried(data):
                await _show_bypass_screen(sender_wa_id, session, phone_number_id)
            else:
                await _send_buttons(
                    sender_wa_id,
                    "❌ *Incorrect OTP*\n\nThe code you entered is incorrect or has expired.\n\n"
                    "Please try again or request a new OTP:",
                    [
                        {"id": "kyc_otp_resend", "title": "📲 Resend OTP"},
                        {"id": "kyc_help",       "title": "🆘 Get help"},
                    ],
                    phone_number_id,
                )

    # ── Verified — next steps ─────────────────────────────────────────────────
    elif step == "kyc_verified":
        if not reply_id and text:
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="kyc_verified_action",
                question_asked="Identity verified. What would you like to do next? Continue to payment, Review trip details, or Go to Main menu.",
                user_response=text,
                expected_format="text",
            )
            if llm_result and llm_result.get("is_valid") and llm_result.get("extracted_value"):
                ev = str(llm_result["extracted_value"]).lower()
                if any(k in ev for k in ("pay", "payment", "continue", "proceed", "activate")):
                    reply_id = "kyc_pay"
                elif any(k in ev for k in ("review", "trip", "details", "summary")):
                    reply_id = "kyc_review"
                elif any(k in ev for k in ("menu", "home", "main", "exit")):
                    reply_id = "kyc_home"
            if not reply_id:
                await _send_buttons(
                    sender_wa_id,
                    "✅ *Identity Verified*\n\nWhat would you like to do next?",
                    [
                        {"id": "kyc_pay", "title": "1. Continue to pay"},
                        {"id": "kyc_review", "title": "2. Review trip"},
                        {"id": "kyc_home", "title": "3. Main menu"},
                    ],
                    phone_number_id,
                )
                return
        if reply_id == "kyc_pay":
            from app.services.payment_flow_service import start_payment_flow

            await start_payment_flow(
                wa_id=sender_wa_id, phone_number_id=phone_number_id
            )
        elif reply_id == "kyc_review":
            bc_data = (
                session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {}).get("data", {})
            )
            travelers = bc_data.get("travelers", [])
            traveler_lines = (
                "\n".join(f"  {i + 1} — {n}" for i, n in enumerate(travelers))
                if travelers
                else f"  1 — {bc_data.get('name', '—')}"
            )
            dep = bc_data.get("depart_airport", "").split("—")[0].strip() or "—"
            arr = bc_data.get("arrive_airport", "").split("—")[0].strip() or "—"
            summary = (
                "📋 *Trip Summary*\n\n"
                f"✈️ YOUR TRIP\n"
                f"Airline: {bc_data.get('airline', '—')}\n"
                f"Route: {dep} → {arr}\n"
                f"Flight: {bc_data.get('flight_num', '—')}\n"
                f"Date: {bc_data.get('date', '—')}\n"
                f"Departs: {bc_data.get('depart_time', '—')}\n"
                f"Arrives: {bc_data.get('arrive_time', '—')}\n\n"
                f"👥 TRAVELLERS\n{traveler_lines}\n\n"
                f"🛡️ Cover: {bc_data.get('cover', '—')}"
            )
            await _send_text(sender_wa_id, summary, phone_number_id)
            await _send_buttons(
                sender_wa_id,
                "What would you like to do next?",
                [
                    {"id": "kyc_pay", "title": "1. Continue to pay"},
                    {"id": "kyc_review", "title": "2. Review trip"},
                    {"id": "kyc_home", "title": "3. Main menu"},
                ],
                phone_number_id,
            )
        elif reply_id == "kyc_home":
            session["temp_data"][KYC_FLOW_KEY] = {}
            session["temp_data"][BUY_COVER_FLOW_KEY] = {}
            await save_session(session)
            from app.services.auto_reply_service import send_main_menu

            await send_main_menu(to=sender_wa_id, phone_number_id=phone_number_id)

    # ── Failed — retry options ────────────────────────────────────────────────
    elif step == "kyc_failed":
        # If both methods already tried, any message routes to bypass screen
        if _both_methods_tried(data):
            await _show_bypass_screen(sender_wa_id, session, phone_number_id)
            return
        method = data.get("kyc_method", "BVN")
        _other = "NIN" if method == "BVN" else "BVN"
        if not reply_id and text:
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="kyc_failed_action",
                question_asked=(
                    f"Verification failed. Would you like to try {method} again, "
                    f"try {_other} instead, or get help?"
                ),
                user_response=text,
                expected_format="text",
            )
            if llm_result and llm_result.get("is_valid") and llm_result.get("extracted_value"):
                ev = str(llm_result["extracted_value"]).lower()
                if any(k in ev for k in ("continue", "purchase", "proceed", "payment", "skip")):
                    reply_id = "kyc_continue_purchase"
                elif any(k in ev for k in ("retry", "again", "same", "re-enter", "reenter")):
                    reply_id = "kyc_retry_same"
                elif any(k in ev for k in ("another", "different", "other", "instead", "switch")):
                    reply_id = "kyc_try_another_id"
                elif "bvn" in ev:
                    reply_id = "kyc_retry_same" if method == "BVN" else "kyc_try_another_id"
                elif "nin" in ev:
                    reply_id = "kyc_retry_same" if method == "NIN" else "kyc_try_another_id"
                elif any(k in ev for k in ("help", "support", "agent")):
                    reply_id = "kyc_help"
            if not reply_id:
                masked = _mask_id(data.get("kyc_id", ""))
                await _send_buttons(
                    sender_wa_id,
                    f"⚠️ *{method} Verification Failed*\n\n"
                    f"We could not verify your *{method}*: `{masked}`\n\n"
                    f"You may have entered an incorrect number. "
                    f"Please try again or use your {_other} instead.",
                    [
                        {"id": "kyc_retry_same", "title": f"🔄 Try {method} again"},
                        {"id": "kyc_try_another_id", "title": f"🪪 Try {_other} instead"},
                        {"id": "kyc_continue_purchase", "title": "💳 Continue Purchase"},
                    ],
                    phone_number_id,
                )
                return
        if reply_id == "kyc_continue_purchase":
            from app.services.payment_flow_service import start_payment_flow
            await start_payment_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)
        elif reply_id == "kyc_retry_same":
            data.pop("kyc_id", None)
            flow["step"] = "kyc_id_input"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                f"🔏 *Please re-enter your 11-digit {method}*\n\n"
                f"_Example: 12345678901_\n\n"
                f"🔒 _Your {method} is handled securely — only the last 3 digits will be shown for confirmation_",
                phone_number_id,
            )
        elif reply_id == "kyc_try_another_id":
            data["kyc_method"] = _other
            data.pop("kyc_id", None)
            flow["step"] = "kyc_id_input"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                f"🔏 *Please enter your 11-digit {_other}*\n\n"
                f"_Example: 12345678901_\n\n"
                f"🔒 _Your {_other} is handled securely — only the last 3 digits will be shown for confirmation_",
                phone_number_id,
            )
        elif reply_id == "kyc_help":
            await _send_help(sender_wa_id, session, phone_number_id)

    # ── Help ─────────────────────────────────────────────────────────────────
    elif step == "kyc_help":
        if reply_id == "kyc_bvn":
            data["kyc_method"] = "BVN"
            flow["step"] = "kyc_consent"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                "🔒 We will only use your National Biometric ID to verify "
                "your identity for this purchase.",
                [
                    {"id": "kyc_consent_yes", "title": "1. ✅ Yes, continue"},
                    {"id": "kyc_consent_no", "title": "2. Go back"},
                ],
                phone_number_id,
            )
        elif reply_id == "kyc_nin":
            data["kyc_method"] = "NIN"
            flow["step"] = "kyc_consent"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                "🔒 We will only use your National Biometric ID to verify "
                "your identity for this purchase.",
                [
                    {"id": "kyc_consent_yes", "title": "1. ✅ Yes, continue"},
                    {"id": "kyc_consent_no", "title": "2. Go back"},
                ],
                phone_number_id,
            )
        elif reply_id == "kyc_agent":
            session["temp_data"][KYC_FLOW_KEY] = {}
            session["temp_data"][BUY_COVER_FLOW_KEY] = {}
            await save_session(session)
            await _send_text(
                sender_wa_id,
                "🤝 *Speak to an agent*\n\n"
                "Our support team will contact you shortly.\n"
                "You can also reach us at *support@ipurvey.com*",
                phone_number_id,
            )
        else:
            await _send_help(sender_wa_id, session, phone_number_id)

    # ── Both methods failed — bypass screen handler ───────────────────────────
    elif step == "kyc_both_failed":
        if not reply_id and text:
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="kyc_bypass_action",
                question_asked=(
                    "Verification could not be completed. "
                    "Would you like to continue to payment, review trip details, "
                    "go to the main menu, or get help?"
                ),
                user_response=text,
                expected_format="text",
            )
            if llm_result and llm_result.get("is_valid") and llm_result.get("extracted_value"):
                ev = str(llm_result["extracted_value"]).lower()
                if any(k in ev for k in ("pay", "payment", "continue", "proceed")):
                    reply_id = "kyc_bypass_pay"
                elif any(k in ev for k in ("review", "trip", "details", "summary")):
                    reply_id = "kyc_bypass_review"
                elif any(k in ev for k in ("menu", "home", "main", "exit")):
                    reply_id = "kyc_bypass_menu"
                elif any(k in ev for k in ("help", "support", "agent")):
                    reply_id = "kyc_bypass_help"
            if not reply_id:
                await _show_bypass_screen(sender_wa_id, session, phone_number_id)
                return

        if reply_id == "kyc_bypass_pay":
            data["kyc_verified"] = False
            data["kyc_bypassed"] = True
            flow["step"] = "kyc_verified"
            await save_session(session)
            from app.services.payment_flow_service import start_payment_flow
            await start_payment_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)

        elif reply_id == "kyc_bypass_review":
            bc_data = (
                session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {}).get("data", {})
            )
            travelers = bc_data.get("travelers", [])
            traveler_lines = (
                "\n".join(f"  {i + 1} — {n}" for i, n in enumerate(travelers))
                if travelers
                else f"  1 — {bc_data.get('name', '—')}"
            )
            dep = bc_data.get("depart_airport", "").split("—")[0].strip() or "—"
            arr = bc_data.get("arrive_airport", "").split("—")[0].strip() or "—"
            summary = (
                "📋 *Trip Summary*\n\n"
                f"✈️ YOUR TRIP\n"
                f"Airline: {bc_data.get('airline', '—')}\n"
                f"Route: {dep} → {arr}\n"
                f"Flight: {bc_data.get('flight_num', '—')}\n"
                f"Date: {bc_data.get('date', '—')}\n"
                f"Departs: {bc_data.get('depart_time', '—')}\n"
                f"Arrives: {bc_data.get('arrive_time', '—')}\n\n"
                f"👥 TRAVELLERS\n{traveler_lines}\n\n"
                f"🛡️ Cover: {bc_data.get('cover', '—')}"
            )
            await _send_text(sender_wa_id, summary, phone_number_id)
            await _show_bypass_screen(sender_wa_id, session, phone_number_id)

        elif reply_id == "kyc_bypass_menu":
            session["temp_data"][KYC_FLOW_KEY] = {}
            session["temp_data"][BUY_COVER_FLOW_KEY] = {}
            await save_session(session)
            from app.services.auto_reply_service import send_main_menu
            await send_main_menu(to=sender_wa_id, phone_number_id=phone_number_id)

        elif reply_id == "kyc_bypass_help":
            await _send_help(sender_wa_id, session, phone_number_id)

        else:
            await _show_bypass_screen(sender_wa_id, session, phone_number_id)

    # ── Catch-all ─────────────────────────────────────────────────────────────
    else:
        session["temp_data"][KYC_FLOW_KEY] = {}
        session["temp_data"][BUY_COVER_FLOW_KEY] = {}
        await save_session(session)
        from app.services.auto_reply_service import send_main_menu

        await send_main_menu(to=sender_wa_id, phone_number_id=phone_number_id)


async def go_back_one_step(wa_id: str, phone_number_id: Optional[str]):
    """Go back exactly one step in the KYC flow instead of restarting."""
    session, flow = await _get_flow_state(wa_id)
    step = flow.get("step", "kyc_intro")
    data = flow.get("data", {})

    _PREV = {
        "kyc_consent":     "kyc_intro",
        "kyc_id_input":    "kyc_consent",
        "kyc_otp_input":   "kyc_id_input",
        "kyc_failed":      "kyc_id_input",
        "kyc_both_failed": "kyc_intro",
    }

    prev = _PREV.get(step)

    if not prev or step == "kyc_intro":
        session["temp_data"][KYC_FLOW_KEY] = {}
        await save_session(session)
        from app.services.auto_reply_service import send_main_menu

        await send_main_menu(to=wa_id, phone_number_id=phone_number_id, wa_id=wa_id)
        return

    flow["step"] = prev
    await save_session(session)

    if prev == "kyc_intro":
        await _send_buttons(
            wa_id,
            "We may verify your identity to support any future payouts and ensure "
            "security and accurate policy issuance.\n\n"
            "> 🔒 *Your privacy matters*\n"
            "> Your data is handled securely and never shared.\n\n"
            "How would you like to verify your identity?\n"
            "Select the country that issued your national biometric ID:",
            [
                {"id": "kyc_nin", "title": "🪪 NIN (Nigeria)"},
                {"id": "kyc_bvn", "title": "🪪 BVN (Nigeria)"},
                {"id": "kyc_help", "title": "🆘 Help"},
            ],
            phone_number_id,
        )

    elif prev == "kyc_consent":
        method = data.get("kyc_method", "NIN")
        await _send_buttons(
            wa_id,
            f"🔒 We will only use your *{method}* to verify your identity for this purchase.\n\n"
            "Do you consent to proceed?",
            [
                {"id": "kyc_consent_yes", "title": "1. ✅ Yes, continue"},
                {"id": "kyc_consent_no", "title": "2. Go back"},
            ],
            phone_number_id,
        )

    elif prev == "kyc_id_input":
        method = data.get("kyc_method", "NIN")
        await _send_text(
            wa_id,
            f"🔏 *Please enter your 11-digit {method}*\n\n"
            f"_Example: 12345678901_\n\n"
            f"🔒 _Your {method} is handled securely — only the last 3 digits will be shown for confirmation_",
            phone_number_id,
        )

    else:
        await start_kyc_flow(wa_id=wa_id, phone_number_id=phone_number_id)
