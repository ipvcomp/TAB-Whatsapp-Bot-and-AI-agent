import logging
import re
from datetime import datetime
from typing import Optional

import app.services.ipurvey_service as ipurvey_service

from app.core.test_overrides import get_msisdn
from app.services.llm_service import call_extract, call_generic, call_policy_flow_validate
from app.services.session_service import get_session, save_session
from app.services.whatsapp_service import send_text_message, send_whatsapp_payload

logger = logging.getLogger(__name__)

BUY_COVER_FLOW_KEY = "buy_cover_flow"
KYC_FLOW_KEY = "kyc_flow"
PAYMENT_FLOW_KEY = "payment_flow"


def _parse_date_to_iso(date_str: str) -> Optional[str]:
    """Parse user date input → ISO YYYY-MM-DD.  Returns None if unrecognised."""
    clean = date_str.strip()
    # Strip ordinal suffixes: "14th" → "14", "1st" → "1", "3rd" → "3"
    clean = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", clean, flags=re.IGNORECASE)
    for fmt in [
        "%d %B %Y",  # 12 April 2026 / 14th May 2026
        "%d %b %Y",  # 12 Apr 2026
        "%d/%m/%Y",  # 12/04/2026
        "%d-%m-%Y",  # 12-04-2026
        "%d/%m/%y",  # 12/04/26
        "%d-%m-%y",  # 12-04-26
        "%B %d, %Y",  # April 12, 2026
        "%Y-%m-%d",  # 2026-05-15 (ISO input)
    ]:
        try:
            return datetime.strptime(clean, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_time_to_hhmm(time_str: str) -> Optional[str]:
    """Parse user time input → 24-h HH:MM.  Returns None if unrecognised.

    Accepted formats (colon separator only):
      - 24h colon:  13:40, 01:40
      - 12h colon + AM/PM:  1:40 PM, 1:40PM, 12:00 am

    Rejected (dot separator always invalid — avoids "1.40 Am", "11.00", "13.40"):
      - Any input containing a dot: 1.40 AM, 1.40 PM, 11.00, 13.40
    The API strictly requires HH:MM with no am/pm suffix.
    """
    ts = time_str.strip()

    # Dot separator is never allowed (strict colon-only policy)
    if "." in ts:
        return None

    normalized = ts

    # Strip any trailing am/pm to get the bare H:MM part
    stripped = re.sub(r"\s*[AaPp][Mm]\s*$", "", normalized).strip()
    had_ampm = stripped != normalized.strip()

    if re.match(r"^\d{1,2}:\d{2}$", stripped):
        h, m = stripped.split(":")
        h_int, m_int = int(h), int(m)
        if 0 <= m_int <= 59:
            # Hour > 12 with am/pm = user wrote 24h clock + redundant pm suffix
            # e.g. "13:40 pm" → 13:40
            # No am/pm at all = plain 24h input: "13:40"
            if not had_ampm or h_int > 12:
                if 0 <= h_int <= 23:
                    return f"{h_int:02d}:{m_int:02d}"
                return None
            # Hour ≤ 12 with am/pm → fall through to 12h strptime below

    # 12-hour format (colon only): "1:40 PM", "1:40PM", "12:00 am"
    # Insert space before am/pm if missing: "1:40PM" → "1:40 PM"
    normalized = re.sub(r"([AaPp][Mm])$", r" \1", normalized).strip()
    for fmt in ["%I:%M %p", "%I %p"]:
        try:
            return datetime.strptime(normalized.upper(), fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


def _fmt_time_display(hhmm: str) -> str:
    """Convert stored HH:MM (24-hour) to 12-hour display format with AM/PM.

    Examples: "03:30" → "03:30 AM", "16:30" → "04:30 PM", "00:00" → "12:00 AM"
    Falls back to the raw string if parsing fails.
    """
    try:
        return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M %p")
    except (ValueError, TypeError):
        return hhmm


def _is_past_date(iso_date: str) -> bool:
    """Return True if the ISO date is strictly before today."""
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").date() < datetime.now().date()
    except ValueError:
        return False


def _is_too_far_future(iso_date: str, max_days: int = 365) -> bool:
    """Return True if the ISO date is more than max_days from today."""
    try:
        return (
            datetime.strptime(iso_date, "%Y-%m-%d").date() - datetime.now().date()
        ).days > max_days
    except ValueError:
        return False


def _is_valid_flight_number(fn: str) -> bool:
    """1–3 letters + optional space + 1–6 digits (e.g. P47123, QI402, AXE7120)."""
    return bool(re.match(r"^[A-Za-z]{1,3}\s?\d{1,6}$", fn.strip()))


def _is_valid_booking_ref(ref: str) -> bool:
    """Booking reference: 4–20 alphanumeric chars (letters, digits, hyphens, underscores).
    Must contain at least one letter (rejects all-digit/all-special inputs like 0000).
    Rejects plain sentences or freetext (spaces → invalid)."""
    cleaned = ref.strip().upper()
    if " " in cleaned:
        return False
    if not re.match(r"^[A-Z0-9][A-Z0-9_\-]{3,19}$", cleaned):
        return False
    # Must contain at least one letter
    return bool(re.search(r"[A-Z]", cleaned))


def _is_valid_email(email: str) -> bool:
    """Basic email sanity check before hitting the API."""
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip()))


def _split_name(full: str) -> tuple[str, str]:
    parts = full.strip().split(None, 1)
    return (parts[0], parts[1] if len(parts) > 1 else "")


def _contains_emoji(s: str) -> bool:
    """Return True if the string contains any emoji or symbol characters."""
    import unicodedata

    for ch in s:
        cp = ord(ch)
        # Common emoji Unicode ranges
        if (
            0x1F300 <= cp <= 0x1FAFF  # Misc symbols, emoticons, transport, etc.
            or 0x2600 <= cp <= 0x27BF  # Misc symbols & dingbats
            or 0x1F1E0 <= cp <= 0x1F1FF  # Regional indicator symbols (flags)
            or 0xFE00 <= cp <= 0xFE0F  # Variation selectors (emoji modifier)
            or 0x200D == cp  # Zero-width joiner (emoji sequences)
        ):
            return True
        # Unicode "Symbol" categories catch remaining pictographs
        cat = unicodedata.category(ch)
        if cat in ("So", "Cs"):  # So = Other Symbol, Cs = Surrogate
            return True
    return False


_NAME_STOP_WORDS = frozenset(
    {
        "not",
        "no",
        "yes",
        "ok",
        "okay",
        "i",
        "me",
        "my",
        "you",
        "your",
        "we",
        "they",
        "it",
        "this",
        "that",
        "is",
        "are",
        "do",
        "does",
        "need",
        "want",
        "get",
        "why",
        "what",
        "how",
        "who",
        "when",
        "which",
        "decided",
        "ye",
        "please",
        "just",
        "can",
        "will",
        "going",
        "have",
        "has",
        "was",
        "be",
        "been",
        "really",
        "still",
        "yet",
        "already",
        "here",
        "there",
        "then",
        "now",
        "also",
        "could",
        "would",
        "should",
        "never",
        "always",
        "maybe",
        "sure",
        "sorry",
        "dont",
        "doesn't",
        "don't",
        "didn't",
        "cannot",
        "cant",
        "haven't",
        "aren't",
    }
)


def _is_valid_name(value: str) -> bool:
    """Return True only if value looks like a real full name (first + surname required)."""
    v = value.strip()
    if not v or len(v) < 2:
        return False
    if "@" in v:  # email address
        return False
    if _contains_emoji(v):  # reject emoji in names
        return False
    if any(c.isdigit() for c in v) and not any(c.isalpha() for c in v):
        return False  # pure numbers
    if not any(c.isalpha() for c in v):
        return False  # no letters at all
    parts = [p for p in v.split() if p]
    if len(parts) < 2:
        return False  # must have at least first name + surname
    if len(parts) > 5:
        return False  # names don't have 6+ parts — likely a sentence
    stop_hits = sum(1 for p in parts if p.lower() in _NAME_STOP_WORDS)
    if stop_hits >= 2:
        return False  # 2+ stop/function words → not a real name
    return True


_QUESTION_STARTERS = (
    "why ",
    "what ",
    "how ",
    "who ",
    "when ",
    "where ",
    "which ",
    "do you ",
    "does the ",
    "is this ",
    "will you ",
    "can you ",
    "please explain",
    "tell me ",
    "explain ",
    "i don't",
    "i dont",
    "why do",
    "why does",
    "what is",
    "what are",
    "what does",
)


def _looks_like_question(text: str) -> bool:
    t = text.lower().strip()
    if "?" in t:
        return True
    for s in _QUESTION_STARTERS:
        if t.startswith(s):
            return True
    words = t.split()
    if len(words) > 6 and not any(c.isdigit() for c in t):
        return True
    return False


def _is_sentence_like(text: str, min_words: int = 3) -> bool:
    """Return True if text looks like a sentence rather than a field value."""
    t = text.lower().strip()
    words = t.split()
    return len(words) >= min_words and not any(c.isdigit() for c in t)


async def _call_llm_and_reprompt(
    text: str,
    sender_wa_id: str,
    phone_number_id: str,
    reprompt_msg: str,
    current_node: str = "buy_cover_flow",
) -> None:
    """Unconditionally call LLM generic to handle unexpected input, then re-prompt."""
    try:
        llm_resp = await call_generic(
            user_id=sender_wa_id,
            phone_number=sender_wa_id,
            message=text,
            user_name="",
            current_node=current_node,
        )
        if llm_resp:
            # API may return response at root level OR inside data{}
            data_block = (
                llm_resp.get("data") if isinstance(llm_resp.get("data"), dict) else {}
            )
            answer = (
                llm_resp.get("response")
                or data_block.get("response")
                or data_block.get("message")
                or ""
            )
            logger.info(
                f"[LLM_GENERIC] node={current_node} user={sender_wa_id} "
                f"input={text!r} answer={answer!r}"
            )
            if answer:
                await _send_text(sender_wa_id, answer, phone_number_id)
        else:
            logger.warning(
                f"[LLM_GENERIC] node={current_node} user={sender_wa_id} "
                f"input={text!r} → no response from LLM"
            )
    except Exception as exc:
        logger.error(
            f"[LLM_GENERIC] node={current_node} user={sender_wa_id} error: {exc}"
        )
    if reprompt_msg:
        await _send_text(sender_wa_id, reprompt_msg, phone_number_id)


async def _maybe_answer_question(
    text: str,
    sender_wa_id: str,
    phone_number_id: str,
    reprompt_msg: str,
    current_node: str = "buy_cover_flow",
) -> bool:
    if not _looks_like_question(text):
        return False
    await _call_llm_and_reprompt(
        text, sender_wa_id, phone_number_id, reprompt_msg, current_node
    )
    return True


def is_in_buy_cover_flow(session: Optional[dict]) -> bool:
    if not session:
        return False
    flow = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {})
    # If active is explicitly False the flow is paused (user pressed 00/9).
    # A paused flow keeps its step key for resume, but must NOT intercept
    # shortcut navigation — treat it as inactive.
    if flow.get("active") is False:
        return False
    # Return True if explicitly active OR if a step is set (active flag may be
    # missing when step was assigned directly without _set_step)
    return bool(flow.get("active") or flow.get("step"))


async def _get_flow_state(wa_id: str) -> tuple[dict, dict]:
    session = await get_session(wa_id) or {}
    flow = session.setdefault("temp_data", {}).setdefault(BUY_COVER_FLOW_KEY, {})
    return session, flow


async def _set_step(session: dict, step: str, wa_id: str):
    session["temp_data"][BUY_COVER_FLOW_KEY]["step"] = step
    session["temp_data"][BUY_COVER_FLOW_KEY]["active"] = True
    await save_session(session)


async def _save_data(session: dict, key: str, value, wa_id: str):
    session["temp_data"][BUY_COVER_FLOW_KEY].setdefault("data", {})[key] = value
    await save_session(session)


async def _reset(session: dict, wa_id: str):
    session["temp_data"][BUY_COVER_FLOW_KEY] = {}
    await save_session(session)


def _build_trip_summary_text(data: dict) -> str:
    dep = data.get("depart_airport", "").split("—")[0].strip()
    arr = data.get("arrive_airport", "").split("—")[0].strip()
    travelers = data.get("travelers", [])
    traveler_line = (
        "  ".join(f"{i + 1} — {n}" for i, n in enumerate(travelers))
        if travelers
        else f"1 — {data.get('name', '')}"
    )

    def _fmt_date(iso: str) -> str:
        try:
            return datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b %Y")
        except ValueError:
            return iso

    arrive_date_raw = data.get("arrive_date", "")
    arrive_date_disp = _fmt_date(arrive_date_raw) if arrive_date_raw else ""
    arrive_date_line = f"Arr Date\t\t*{arrive_date_disp}*\n" if arrive_date_disp else ""
    dep_date_disp = _fmt_date(data.get("date", ""))
    booking_ref = data.get("booking_ref", "")
    booking_ref_line = f"Booking Ref\t\t*{booking_ref}*\n" if booking_ref else ""
    return (
        "*✈️ YOUR TRIP*\n\n"
        f"Airline\t\t\t*{data.get('airline', '')}*\n"
        f"Route\t\t\t*{dep} → {arr}*\n"
        f"Flight\t\t\t*{data.get('flight_num', '')}*\n"
        f"{booking_ref_line}"
        f"Dep Date\t\t*{dep_date_disp}*\n"
        f"{arrive_date_line}"
        f"Departs\t\t\t*{_fmt_time_display(data.get('depart_time', ''))}*\n"
        f"Arrives\t\t\t*{_fmt_time_display(data.get('arrive_time', ''))}*\n"
        f"Travellers\t\t*{traveler_line}*"
    )


def _build_cover_card_body(data: dict) -> str:
    """Build the full cover-selected card body from session data."""
    cover_name  = data.get("cover", "Selected cover")
    cover_price = data.get("cover_price", 0)
    sq          = data.get("selected_quote") or {}
    trip_type   = sq.get("tripType") or sq.get("travelType") or "Single trip"
    insurer     = (
        sq.get("insurer") or sq.get("provider")
        or sq.get("providerName") or "Tangerine Insurance"
    )
    naicom_reg  = sq.get("naicomReg") or sq.get("regulatoryRef") or sq.get("regNumber") or ""
    payout_limit = (
        sq.get("disruptionPayout") or sq.get("coverageAmount") or sq.get("maxPayout")
        or sq.get("sumInsured") or sq.get("payoutLimit") or sq.get("maxCoverage")
        or sq.get("disruption_payout") or 0
    )
    _COVERAGE_LABEL_MAP = {
        "CANCELLATION":      "Trip Cancellation Cover",
        "DELAY":             "Trip Delay Cover",
        "MAJOR_DELAY":       "Trip Delay Cover",
        "TRAVEL_DISRUPTION": "Travel Disruption Cover",
        "BAGGAGE":           "Baggage Cover",
        "MEDICAL":           "Medical Cover",
    }
    raw_coverage  = sq.get("coverageTypes") or ["Trip Delay Cover", "Trip Cancellation Cover"]
    coverage_lines = "\n".join(
        f"✅ {_COVERAGE_LABEL_MAP.get(str(c).upper().replace(' ', '_'), c)}"
        for c in raw_coverage
    )
    naicom_line = f"📋 NAICOM Reg: {naicom_reg}\n" if naicom_reg else ""
    payout_line = (
        f"\n💰 *Disruption Payout: ₦{float(payout_limit):,.2f}*\n"
        f"_(Maximum payable for covered events)_"
        if payout_limit
        else ""
    )
    return (
        f"✅ *{cover_name}*\n"
        f"✅ _Cover selected_\n\n"
        f"✈️ Trip Type: {trip_type}\n"
        f"🏢 Insurer: {insurer}\n"
        f"{naicom_line}"
        f"\n*Your cover includes:*\n"
        f"{coverage_lines}\n\n"
        f"💳 *Insurance Premium: ₦{float(cover_price):,.2f}*\n"
        f"_(Inclusive of taxes & fees)_"
        f"{payout_line}\n\n"
        f"ℹ️ _This policy provides protection for covered travel disruption events only. "
        f"Terms, limits, exclusions and waiting periods apply._\n\n"
        f"📌 _Please review the Policy Terms before payment and activation._\n\n"
        f"What would you like to do next?"
    )


def _build_paused_context(session: dict) -> dict:
    """Snapshot the currently active buy/kyc/payment flow for resume later."""
    td = session.get("temp_data", {})
    api = session.get("api_data", {})
    active_flow = (
        PAYMENT_FLOW_KEY
        if td.get(PAYMENT_FLOW_KEY, {}).get("active")
        else KYC_FLOW_KEY
        if td.get(KYC_FLOW_KEY, {}).get("active")
        else BUY_COVER_FLOW_KEY
    )
    bc = td.get(BUY_COVER_FLOW_KEY, {})
    kyc = td.get(KYC_FLOW_KEY, {})
    pay = td.get(PAYMENT_FLOW_KEY, {})
    return {
        "active_flow": active_flow,
        "buy_cover_step": bc.get("step"),
        "buy_cover_data": dict(bc.get("data") or {}),
        "kyc_step": kyc.get("step"),
        "kyc_data": dict(kyc.get("data") or {}),
        "payment_step": pay.get("step"),
        "payment_data": dict(pay.get("data") or {}),
        "policy_id": api.get("policy_id"),
        "policy_code": api.get("policy_code"),
        "passenger_id": api.get("passenger_id"),
        "passenger_ids": list(api.get("passenger_ids") or []),
        "quotes": list(api.get("quotes") or []),
        "user_id": api.get("user_id"),
        "payout_method_id": api.get("payout_method_id"),
        "paused_at": datetime.utcnow().isoformat(),
    }


async def pause_buy_cover_flow(wa_id: str) -> None:
    """Save full flow snapshot then deactivate buy/kyc/payment flows.
    Called by 00 (main menu) and 9 (help) shortcuts so user can resume later.
    """
    session = await get_session(wa_id) or {}
    td = session.setdefault("temp_data", {})
    has_active = any(
        td.get(fk, {}).get("active")
        for fk in (BUY_COVER_FLOW_KEY, KYC_FLOW_KEY, PAYMENT_FLOW_KEY)
    )
    if not has_active:
        return
    session["paused_context"] = _build_paused_context(session)
    for fk in (BUY_COVER_FLOW_KEY, KYC_FLOW_KEY, PAYMENT_FLOW_KEY):
        if td.get(fk, {}).get("active"):
            td[fk]["active"] = False
    await save_session(session)
    logger.info(
        f"[buy_cover] flow paused for {wa_id}, "
        f"active_flow={session['paused_context']['active_flow']}, "
        f"bc_step={session['paused_context']['buy_cover_step']}"
    )


async def _redisplay_step(
    wa_id: str, step: str, data: dict, session: dict, phone_number_id: Optional[str]
) -> None:
    """Re-send the correct prompt for `step` so the user can continue after a resume."""
    name = data.get("name", "")

    if step == "buy_cover_who":
        await _send_buttons(
            wa_id,
            "Is this cover for:",
            [
                {"id": "cover_just_me", "title": "1. 🧑 Just me"},
                {"id": "cover_others", "title": "2. 👥 Me & Others"},
            ],
            phone_number_id,
        )

    elif step == "buy_cover_traveler_count":
        await _send_text(
            wa_id,
            "👥 *How many travellers are covered?*\n"
            "Please reply with a number\n\n"
            "_Example: 2_\n\n"
            "⚠️ Maximum number of travellers you can add is *10*.",
            phone_number_id,
        )

    elif step == "buy_cover_name":
        await _send_text(
            wa_id,
            "👤 👑 *Enter main passenger name*\n"
            "Enter first name and surname as it appears on the ticket.\n\n"
            "ℹ️ This person is the main passenger.\n"
            "\n"
            "_Example: Yusuf Usman_",
            phone_number_id,
        )

    elif step == "buy_cover_other_name":
        collected = len(data.get("travelers", []))
        total = data.get("others_count", 1) + 1
        await _send_text(
            wa_id,
            f"👤 *Traveller {collected + 1} of {total}*\n"
            "Enter first name and surname as it appears on their ticket.\n\n"
            "_Example: Amina Bello_",
            phone_number_id,
        )

    elif step == "buy_cover_email":
        n = f"*{name}*\n\n" if name else ""
        await _send_text(
            wa_id,
            f"{n}*📧 Please enter your email address*\n"
            "So we can send your policy documents\n\n"
            "_Example: yusuf@email.com_",
            phone_number_id,
        )

    elif step == "buy_cover_trip_type":
        await _send_trip_type_buttons(wa_id, phone_number_id)

    elif step in (
        "buy_cover_departure_airport",
        "buy_cover_depart_airport_pick",
        "buy_cover_confirm_departure",
    ):
        await _send_text(
            wa_id,
            "*🛫 What airport are you departing from?*\n\n"
            "Type at least 3 characters of the airport name or IATA code\n\n"
            "_Example: LOS, ABV, KAN_",
            phone_number_id,
        )

    elif step in ("buy_cover_depart_date", "buy_cover_date"):
        await _send_text(
            wa_id,
            "*📅 Please enter your departure date*\n\n"
            "_Example: 12 April 2026 or 12/04/2026_",
            phone_number_id,
        )

    elif step == "buy_cover_depart_time":
        await _send_text(
            wa_id,
            "*⏰ Please enter your departure time*\n\n_Example: 08:30 or 8:30 AM_",
            phone_number_id,
        )

    elif step in (
        "buy_cover_arrival_airport",
        "buy_cover_arrive_airport_pick",
        "buy_cover_confirm_arrival",
    ):
        await _send_text(
            wa_id,
            "*🛬 What airport are you arriving at?*\n\n"
            "Type at least 3 characters of the airport name or IATA code\n\n"
            "_Example: LOS, Lagos, ABV, Abuja_",
            phone_number_id,
        )

    elif step == "buy_cover_arrive_date":
        await _send_text(
            wa_id,
            "*📅 Please enter your arrival date*\n\n"
            "_Example: 16 April 2026 or 16/04/2026_",
            phone_number_id,
        )

    elif step == "buy_cover_arrive_time":
        await _send_text(
            wa_id,
            "*⏰ Please enter your arrival time*\n\n_Example: 10:00 or 10:00 AM_",
            phone_number_id,
        )

    elif step == "buy_cover_booking_ref":
        await _send_text(
            wa_id,
            "*🎫 Please enter your booking reference*\n\n_Example: ABC123_",
            phone_number_id,
        )

    elif step in ("buy_cover_flight_num", "buy_cover_flight_no"):
        await _send_text(
            wa_id,
            "*✈️ Please enter your flight number*\n\n_Example: W3101 or P47123_",
            phone_number_id,
        )

    elif step == "buy_cover_airline":
        await _send_text(
            wa_id,
            "*✈️ Who are you flying with?*\n\n_Example: Ibom Air, Air Peace_",
            phone_number_id,
        )

    elif step == "buy_cover_select_cover":
        quotes = session.get("api_data", {}).get("quotes") or []
        if not quotes:
            policy_id = session.get("api_data", {}).get("policy_id")
            if policy_id:
                try:
                    quotes = await ipurvey_service.fetch_quotes(policy_id) or []
                    if quotes:
                        session.setdefault("api_data", {})["quotes"] = quotes
                        await save_session(session)
                except Exception as exc:
                    logger.error(f"[buy_cover] _redisplay_step fetch_quotes: {exc}")
        if quotes:
            await _send_cover_page(
                wa_id,
                quotes,
                0,
                phone_number_id,
                intro_body="🎁 *Welcome back!* 👇 Please pick your cover plan:",
            )
        else:
            await _send_text(
                wa_id,
                "⚠️ Unable to load covers right now. Type anything to retry.",
                phone_number_id,
            )

    elif step == "buy_cover_next_steps":
        await _send_buttons(
            wa_id,
            _build_cover_card_body(data),
            [
                {"id": "next_kyc",   "title": "🛒 Buy Cover"},
                {"id": "next_terms", "title": "📄 View Policy Terms"},
                {"id": "next_ask",   "title": "❓ Ask a Question"},
            ],
            phone_number_id,
        )

    elif step == "buy_cover_summary":
        flow = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {})
        await _show_trip_summary(wa_id, data, flow, session, phone_number_id)

    else:
        flow = session.setdefault("temp_data", {}).setdefault(BUY_COVER_FLOW_KEY, {})
        flow["step"] = "buy_cover_who"
        await save_session(session)
        await _send_buttons(
            wa_id,
            "✈️ *Let's continue your application!*\n\nIs this cover for:",
            [
                {"id": "cover_just_me", "title": "1. 🧑 Just me"},
                {"id": "cover_others", "title": "2. 👥 Me & Others"},
            ],
            phone_number_id,
        )


async def _show_trip_summary(
    sender_wa_id: str,
    data: dict,
    flow: dict,
    session: dict,
    phone_number_id: Optional[str],
):
    flow["step"] = "buy_cover_summary"
    await save_session(session)
    await _send_buttons(
        sender_wa_id,
        "📋 *Trip Summary*\n\n" + _build_trip_summary_text(data),
        [
            {"id": "summary_confirm", "title": "✅ Confirm"},
            {"id": "summary_edit", "title": "✏️ Edit details"},
        ],
        phone_number_id,
    )


async def _send_edit_menu(to: str, phone_number_id: Optional[str], page: int = 1):
    if page == 2:
        rows = [
            {"id": "edit_arrive_date",   "title": "📅 Arrival date"},
            {"id": "edit_depart_time",   "title": "⏰ Departure time"},
            {"id": "edit_arrive_time",   "title": "⏰ Arrival time"},
            {"id": "edit_depart_airport","title": "🛫 Departure airport"},
            {"id": "edit_arrive_airport","title": "🛬 Arrival airport"},
            {"id": "edit_prev_fields",   "title": "⬅️ Back to page 1"},
        ]
        body = "✏️ *Edit details — page 2 of 2*\n\nSelect the field to update:"
    else:
        rows = [
            {"id": "edit_name",         "title": "👤 Passenger name"},
            {"id": "edit_email",        "title": "📧 Email address"},
            {"id": "edit_airline",      "title": "✈️ Airline"},
            {"id": "edit_booking_ref",  "title": "🎫 Booking reference"},
            {"id": "edit_flight_num",   "title": "✈️ Flight number"},
            {"id": "edit_date",         "title": "📅 Departure date"},
            {"id": "edit_more_fields",  "title": "➡️ More fields (page 2)"},
        ]
        body = "✏️ *Edit details — page 1 of 2*\n\nSelect the field to update:"
    await _send_list(
        to,
        body,
        "Select field",
        [{"title": " ", "rows": rows}],
        phone_number_id,
    )


_UTILITY = (
    "*Utility options:*\n0 ↩️ Back  |  9 🆘 Help  |  00 🏠 Main menu\n99 ❌ Cancel/Exit"
)


async def _send_text(to: str, body: str, phone_number_id: Optional[str]):
    await send_text_message(
        to=to, body=body, phone_number_id=phone_number_id, source="buy_cover_flow"
    )
    await send_text_message(
        to=to, body=_UTILITY, phone_number_id=phone_number_id, source="buy_cover_flow"
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
        whatsapp_payload=payload,
        phone_number_id=phone_number_id,
        source="buy_cover_flow",
    )
    await send_text_message(
        to=to, body=_UTILITY, phone_number_id=phone_number_id, source="buy_cover_flow"
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
        whatsapp_payload=payload,
        phone_number_id=phone_number_id,
        source="buy_cover_flow",
    )
    await send_text_message(
        to=to, body=_UTILITY, phone_number_id=phone_number_id, source="buy_cover_flow"
    )


_TRIP_TYPE_BTN_MAP = {
    "ONE_WAY": {"id": "trip_oneway", "title": "1. 🗺️ One-way"},
    "RETURN":  {"id": "trip_return", "title": "2. 🔄 Return"},
}


async def _send_trip_type_buttons(wa_id: str, phone_number_id: Optional[str]) -> None:
    trip_types = await ipurvey_service.get_trip_types()
    buttons = [
        _TRIP_TYPE_BTN_MAP[t["value"]]
        for t in trip_types
        if isinstance(t, dict) and t.get("value") in _TRIP_TYPE_BTN_MAP
    ]
    if not buttons:
        buttons = [
            {"id": "trip_oneway", "title": "1. 🗺️ One-way"},
            {"id": "trip_return", "title": "2. 🔄 Return"},
        ]
    await _send_buttons(wa_id, "🗺️ What type of trip is this?", buttons, phone_number_id)


_COVER_PAGE_SIZE = (
    9  # 9 products + optional "More covers" row = max 10 (WhatsApp limit)
)


def _make_cover_title(name: str) -> str:
    """Trim a product name to ≤24 chars at a word boundary (WhatsApp title limit)."""
    if len(name) <= 24:
        return name
    truncated = name[:24]
    last_space = truncated.rfind(" ")
    if last_space >= 16:
        truncated = truncated[:last_space]
    return truncated


async def _send_cover_page(
    wa_id: str,
    quotes: list,
    page: int,
    phone_number_id: Optional[str],
    *,
    intro_body: Optional[str] = None,
) -> None:
    """Send one paginated page of cover options (max 9 products + optional More row)."""
    start = page * _COVER_PAGE_SIZE
    end = start + _COVER_PAGE_SIZE
    page_quotes = quotes[start:end]

    rows = []
    for i, q in enumerate(page_quotes):
        full_name = str(q.get("name") or q.get("productName") or "Cover option")
        q_price = q.get("price") or q.get("premiumAmount") or 0
        insurer = q.get("insurer") or q.get("provider") or q.get("providerName") or ""
        coverage = q.get("coverageTypes") or []
        title = _make_cover_title(full_name)
        price_str = f"💰 ₦{float(q_price):,.0f}"
        insurer_str = f"🏢 {insurer}" if insurer else ""
        cover_count = f"✅ {len(coverage)} covers" if coverage else ""
        name_prefix = f"{full_name}  •  " if len(full_name) > len(title) else ""
        desc = name_prefix + "  •  ".join(
            filter(None, [price_str, insurer_str, cover_count])
        )
        rows.append(
            {"id": f"cov_{start + i}", "title": title, "description": desc[:72]}
        )

    remaining = len(quotes) - end
    if remaining > 0:
        rows.append(
            {
                "id": f"more_covers_{page + 1}",
                "title": "➡️ More covers...",
                "description": f"View {remaining} more option(s)",
            }
        )

    if intro_body:
        body = intro_body
    elif page == 0:
        body = (
            "🎁 *With TravelAssist you get:*\n"
            "📄 Policy on WhatsApp\n"
            "🔔 Real-time flight alerts\n"
            "🤝 Support if disruption happens\n"
            "💰 Automatic payout — no forms needed\n\n"
            "👇 Tap *Select cover* to choose your plan:"
        )
    else:
        body = (
            f"👇 Covers {start + 1}–{start + len(page_quotes)} of {len(quotes)}.\n"
            "Tap a plan to select it:"
        )

    await _send_list(
        wa_id,
        body,
        "Select cover",
        [{"title": "🛡️ Available Covers", "rows": rows}],
        phone_number_id,
        header="🛡️ Select from available cover(s)",
    )


async def start_buy_cover_flow(
    wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session = await get_session(wa_id) or {}
    msisdn = get_msisdn(wa_id)

    # ── 1. Paused context: user came back after 00 / help shortcut ───────────
    paused = session.get("paused_context")
    if paused and paused.get("policy_id"):
        resumed_api = None
        try:
            resumed_api = await ipurvey_service.resume_draft_policy(msisdn)
        except Exception:
            pass
        if resumed_api and resumed_api.get("policy_id"):
            # API still has the draft → restore data & offer resume / fresh
            session["api_data"] = {
                "policy_id": paused.get("policy_id"),
                "policy_code": paused.get("policy_code"),
                "passenger_id": paused.get("passenger_id"),
                "passenger_ids": paused.get("passenger_ids") or [],
                "quotes": paused.get("quotes") or [],
                "user_id": paused.get("user_id"),
                "payout_method_id": paused.get("payout_method_id"),
                "user_exists": True,
                "resume_draft": resumed_api,
            }
            bc_data = paused.get("buy_cover_data") or {}
            active_flow = paused.get("active_flow", BUY_COVER_FLOW_KEY)
            name = bc_data.get("name", "")
            cover = bc_data.get("cover", "")
            flight = bc_data.get("flight_num", "")
            date = bc_data.get("date", "")
            dep = (bc_data.get("depart_airport") or "").split("—")[0].strip()
            arr = (bc_data.get("arrive_airport") or "").split("—")[0].strip()

            h_lines = []
            if name:
                h_lines.append(f"👤 {name}")
            if flight:
                h_lines.append(
                    f"✈️ {flight}" + (f"  •  {dep} → {arr}" if dep or arr else "")
                )
            elif dep or arr:
                h_lines.append(f"🛫 {dep} → {arr}")
            if date:
                h_lines.append(f"📅 {date}")
            if cover:
                h_lines.append(f"🛡️ {cover}")

            if active_flow == KYC_FLOW_KEY:
                h_lines.append("\n🔐 Identity verification was in progress")
            elif active_flow == PAYMENT_FLOW_KEY:
                h_lines.append("\n💳 Payment setup was in progress")
            else:
                bc_step = paused.get("buy_cover_step", "")
                step_label = {
                    "buy_cover_name": "📝 Entering traveller name",
                    "buy_cover_email": "📧 Entering email address",
                    "buy_cover_trip_type": "🗺️ Selecting trip type",
                    "buy_cover_departure_airport": "🔍 Selecting departure airport",
                    "buy_cover_depart_date": "📅 Entering departure date",
                    "buy_cover_depart_time": "⏰ Entering departure time",
                    "buy_cover_arrival_airport": "🔍 Selecting arrival airport",
                    "buy_cover_arrive_date": "📅 Entering arrival date",
                    "buy_cover_arrive_time": "⏰ Entering arrival time",
                    "buy_cover_depart_ampm_confirm": "⏰ Confirming departure time",
                    "buy_cover_booking_ref": "🎫 Entering booking reference",
                    "buy_cover_flight_num": "✈️ Entering flight number",
                    "buy_cover_select_cover": "🛡️ Selecting cover plan",
                    "buy_cover_summary": "📋 Confirming trip summary",
                }.get(bc_step, "")
                if step_label:
                    h_lines.append(f"\n{step_label}")

            hint = "\n".join(h_lines)
            td = session.setdefault("temp_data", {})
            td[BUY_COVER_FLOW_KEY] = {
                "active": True,
                "step": "buy_cover_resume_choice",
                "data": bc_data,
            }
            if paused.get("kyc_step"):
                td[KYC_FLOW_KEY] = {
                    "active": False,
                    "step": paused["kyc_step"],
                    "data": paused.get("kyc_data") or {},
                }
            if paused.get("payment_step"):
                td[PAYMENT_FLOW_KEY] = {
                    "active": False,
                    "step": paused["payment_step"],
                    "data": paused.get("payment_data") or {},
                }
            await save_session(session)
            await _send_buttons(
                wa_id,
                "📋 *Incomplete Application Found*\n\n"
                "We found an unfinished policy from your last session:\n\n"
                f"{hint}\n\n"
                "Would you like to continue where you left off?",
                [
                    {"id": "resume_yes", "title": "▶️ Resume"},
                    {"id": "resume_fresh", "title": "🆕 Start Fresh"},
                ],
                phone_number_id,
            )
            return
        else:
            # API no longer has this draft → clear context and start fresh
            session.pop("paused_context", None)
            await save_session(session)

    # ── 2. Normal flow start ──────────────────────────────────────────────────
    session.setdefault("temp_data", {})[BUY_COVER_FLOW_KEY] = {
        "active": True,
        "step": "buy_cover_who",
        "data": {},
    }
    if "user_id" not in session:
        session["user_id"] = wa_id
    session["api_data"] = {}
    await save_session(session)

    try:
        api_data = session.setdefault("api_data", {})
        user = await ipurvey_service.check_user_exists(msisdn)
        if user and isinstance(user, dict):
            uid = user.get("userId") or user.get("id") or user.get("user_id")
            api_data["user_id"] = uid
            api_data["user_exists"] = True
        else:
            api_data["user_exists"] = False

        # ── Try to resume an existing API draft ───────────────────────────────
        resumed = await ipurvey_service.resume_draft_policy(msisdn)
        pax_api = resumed.get("passengers", []) if resumed else []
        has_named = any(p.get("firstName") or p.get("surname") for p in pax_api)
        if (
            resumed
            and resumed.get("policy_id")
            and (resumed.get("creation_state", "DRAFT") != "DRAFT" or has_named)
        ):
            state = resumed["creation_state"]
            api_data["resume_draft"] = resumed
            await save_session(session)

            passengers = resumed.get("passengers", [])
            names = [
                f"{p.get('firstName', '')} {p.get('surname', '')}".strip()
                for p in passengers
                if p.get("firstName") or p.get("surname")
            ]
            itinerary = resumed.get("itinerary", {})
            legs = itinerary.get("legs", [])
            leg = legs[0] if legs else {}
            flight_num = leg.get("flightNumber", "")
            dep_code = leg.get("departureAirport", "")
            arr_code = leg.get("arrivalAirport", "")
            dep_date = leg.get("departureDate", "")
            traveler_line = ", ".join(names) if names else ""

            if state in ("AWAITING_KYC", "DETAILS_COLLECTED"):
                hint = (
                    "✈️ You were almost done — just pick your cover plan!\n"
                    f"Flight: *{flight_num}*  •  Route: *{dep_code} → {arr_code}*\n"
                    f"Date: *{dep_date}*"
                )
            elif state == "AWAITING_ITINERARY":
                hint = (
                    f"👤 Traveler: *{traveler_line}*\n"
                    f"📧 Email: *{resumed.get('email', '')}*\n\n"
                    "Your flight details are next."
                )
            elif state == "DRAFT" and has_named:
                h_parts = [f"👤 Traveler: *{traveler_line}*"]
                email = resumed.get("email", "")
                if email:
                    h_parts.append(f"📧 Email: *{email}*")
                    h_parts.append("✈️ Flight details still needed.")
                else:
                    h_parts.append("📧 Email and flight details still needed.")
                hint = "\n".join(h_parts)
            else:
                hint = (
                    f"👤 Traveler: *{traveler_line}*"
                    if traveler_line
                    else "Some details were already saved."
                )

            flow = session["temp_data"][BUY_COVER_FLOW_KEY]
            flow["step"] = "buy_cover_resume_choice"
            await save_session(session)
            await _send_buttons(
                to=wa_id,
                body=(
                    "📋 *Incomplete Application Found*\n\n"
                    "We found an unfinished policy from your previous session:\n\n"
                    f"{hint}\n\n"
                    "Would you like to continue where you left off?"
                ),
                buttons=[
                    {"id": "resume_yes", "title": "▶️ Resume"},
                    {"id": "resume_fresh", "title": "🆕 Start Fresh"},
                ],
                phone_number_id=phone_number_id,
            )
            return

        # ── No resumable API draft — create a fresh one ───────────────────────
        draft = await ipurvey_service.create_draft_policy(msisdn)
        if draft:
            pid = draft["policy_id"]
            existing = draft.get("existing", False)
            state = draft.get("creation_state", "DRAFT")
            existing_pax = draft.get("passengers", [])
            has_named_d = any(
                p.get("firstName") or p.get("surname") for p in existing_pax
            )

            if existing and pid and (state != "DRAFT" or has_named_d):
                logger.info(
                    f"[buy_cover] create_draft returned existing '{pid}' "
                    f"state='{state}' has_named={has_named_d} — resume prompt"
                )
                api_data["resume_draft"] = {
                    "policy_id": pid,
                    "creation_state": state,
                    "passengers": existing_pax,
                    "email": draft.get("email", ""),
                    "itinerary": draft.get("itinerary", {}),
                }
                flow = session["temp_data"][BUY_COVER_FLOW_KEY]
                flow["step"] = "buy_cover_resume_choice"
                await save_session(session)
                await _send_buttons(
                    to=wa_id,
                    body=(
                        "📋 *Incomplete Application Found*\n\n"
                        "We found an unfinished policy from your previous session.\n\n"
                        "Would you like to continue where you left off?"
                    ),
                    buttons=[
                        {"id": "resume_yes", "title": "▶️ Resume"},
                        {"id": "resume_fresh", "title": "🆕 Start Fresh"},
                    ],
                    phone_number_id=phone_number_id,
                )
                return

            elif existing and pid:
                # Blank DRAFT — silently cancel and create fresh
                logger.info(
                    f"[buy_cover] existing blank draft '{pid}' (DRAFT, no names) "
                    f"— cancelling and creating fresh"
                )
                await ipurvey_service.cancel_draft_policy(pid)
                draft = await ipurvey_service.create_draft_policy(msisdn)
                if draft and draft.get("existing") and draft["policy_id"] == pid:
                    logger.warning(
                        f"[buy_cover] cancel had no effect, draft still '{pid}' "
                        f"— clearing policy_id"
                    )
                    pid = None
                    draft = None
                else:
                    pid = draft["policy_id"] if draft else None

            if pid:
                api_data["policy_id"] = pid
        await save_session(session)
    except Exception as exc:
        logger.error(f"[buy_cover] start API calls failed: {exc}")

    await _send_buttons(
        to=wa_id,
        body=(
            "✈️ Great choice — let's protect your trip!\n"
            "This will only take a few steps 😊\n\n"
            "Is this cover for:"
        ),
        buttons=[
            {"id": "cover_just_me", "title": "1. 🧑 Just me"},
            {"id": "cover_others", "title": "2. 👥 Me & Others"},
        ],
        phone_number_id=phone_number_id,
    )


async def handle_buy_cover_flow(
    message,
    sender_wa_id: str,
    phone_number_id: Optional[str],
    in_reply_to: Optional[str] = None,
):
    session, flow = await _get_flow_state(sender_wa_id)
    step = flow.get("step", "buy_cover_who")
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

    # ── Resume choice ─────────────────────────────────────────────────────────
    if step == "buy_cover_resume_choice":
        msisdn_r = get_msisdn(sender_wa_id)
        if reply_id == "resume_fresh":
            # Cancel paused context draft OR api resume_draft, then start fresh
            paused_f = session.get("paused_context")
            old_pid = paused_f.get("policy_id") if paused_f else None
            session.pop("paused_context", None)
            resume_data_f = session.get("api_data", {}).pop("resume_draft", {})
            if not old_pid:
                old_pid = resume_data_f.get("policy_id")
            if old_pid:
                try:
                    await ipurvey_service.cancel_draft_policy(old_pid)
                except Exception as exc:
                    logger.warning(f"[buy_cover] resume_fresh cancel failed: {exc}")
            session["api_data"] = {}
            draft = await ipurvey_service.create_draft_policy(msisdn_r)
            if draft:
                session.setdefault("api_data", {})["policy_id"] = draft["policy_id"]
            flow["step"] = "buy_cover_who"
            flow["data"] = {}
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                (
                    "✈️ Great choice — let's protect your trip!\n"
                    "This will only take a few steps 😊\n\n"
                    "Is this cover for:"
                ),
                [
                    {"id": "cover_just_me", "title": "1. 🧑 Just me"},
                    {"id": "cover_others", "title": "2. 👥 Me & Others"},
                ],
                phone_number_id,
            )
            return

        if reply_id == "resume_yes":
            # ── A. Paused-context restore (highest priority) ───────────────────
            paused_y = session.get("paused_context")
            if paused_y and paused_y.get("policy_id"):
                api_data_y = session.setdefault("api_data", {})
                api_data_y.update(
                    {
                        "policy_id": paused_y.get("policy_id"),
                        "policy_code": paused_y.get("policy_code"),
                        "passenger_id": paused_y.get("passenger_id"),
                        "passenger_ids": paused_y.get("passenger_ids") or [],
                        "quotes": paused_y.get("quotes") or [],
                        "user_id": paused_y.get("user_id"),
                        "payout_method_id": paused_y.get("payout_method_id"),
                    }
                )
                active_flow_y = paused_y.get("active_flow", BUY_COVER_FLOW_KEY)
                bc_step_y = paused_y.get("buy_cover_step") or "buy_cover_who"
                bc_data_y = paused_y.get("buy_cover_data") or {}
                flow["data"] = bc_data_y
                data = flow["data"]

                if active_flow_y == KYC_FLOW_KEY:
                    session.pop("paused_context", None)
                    bc_flow_y = session.setdefault("temp_data", {})[BUY_COVER_FLOW_KEY]
                    bc_flow_y.update(
                        {"step": bc_step_y, "active": False, "data": bc_data_y}
                    )
                    await save_session(session)
                    from app.services.kyc_flow_service import start_kyc_flow

                    await start_kyc_flow(sender_wa_id, phone_number_id, from_buy_cover=True)
                    return

                elif active_flow_y == PAYMENT_FLOW_KEY:
                    session.pop("paused_context", None)
                    bc_flow_y = session.setdefault("temp_data", {})[BUY_COVER_FLOW_KEY]
                    bc_flow_y.update(
                        {"step": bc_step_y, "active": False, "data": bc_data_y}
                    )
                    pay_flow_y = session.setdefault("temp_data", {}).setdefault(
                        PAYMENT_FLOW_KEY, {}
                    )
                    pay_flow_y.update(
                        {
                            "data": paused_y.get("payment_data") or {},
                            "step": paused_y.get("payment_step")
                            or "pay_payout_options",
                            "active": True,
                        }
                    )
                    await save_session(session)
                    from app.services.payment_flow_service import start_payment_flow

                    await start_payment_flow(
                        wa_id=sender_wa_id, phone_number_id=phone_number_id
                    )
                    return

                else:
                    # If no meaningful step was saved, resolve from API state
                    if not bc_step_y or bc_step_y == "buy_cover_who":
                        resume_d2 = (
                            session.get("api_data", {}).get("resume_draft") or {}
                        )
                        state2 = resume_d2.get("creation_state", "DRAFT")
                        # Also check API passengers in case bc_data_y is stale/empty
                        api_pax2 = resume_d2.get("passengers", [])
                        api_names2 = [
                            f"{p.get('firstName', '')} {p.get('surname', '')}".strip()
                            for p in api_pax2
                            if p.get("firstName") or p.get("surname")
                        ]
                        api_email2 = resume_d2.get("email", "")
                        if state2 in ("AWAITING_KYC", "DETAILS_COLLECTED"):
                            bc_step_y = "buy_cover_select_cover"
                        elif state2 == "AWAITING_ITINERARY":
                            bc_step_y = "buy_cover_trip_type"
                        elif bc_data_y.get("email") or api_email2:
                            bc_step_y = "buy_cover_trip_type"
                        elif bc_data_y.get("name") or api_names2:
                            bc_step_y = "buy_cover_email"
                        else:
                            bc_step_y = "buy_cover_who"
                        # If bc_data_y is empty/stale but API has passenger data,
                        # populate bc_data_y so the resumed flow has the right context
                        if api_names2 and not bc_data_y.get("name"):
                            bc_data_y["name"] = api_names2[0]
                            if api_email2:
                                bc_data_y["email"] = api_email2
                            if len(api_names2) > 1:
                                bc_data_y["who"] = "me_and_others"
                                bc_data_y["travelers"] = api_names2
                                bc_data_y["others_count"] = len(api_names2) - 1
                            else:
                                bc_data_y["who"] = "just_me"
                            # Restore passenger_ids from API into api_data
                            pax_ids2 = [p["id"] for p in api_pax2 if p.get("id")]
                            if pax_ids2:
                                session["api_data"]["passenger_ids"] = pax_ids2
                            prim2 = next(
                                (p for p in api_pax2 if p.get("isPrimary")),
                                api_pax2[0] if api_pax2 else None,
                            )
                            if prim2 and prim2.get("id"):
                                session["api_data"]["passenger_id"] = prim2["id"]
                        logger.info(
                            f"[buy_cover] paused_context had no step; resolved to "
                            f"'{bc_step_y}' from API state '{state2}'"
                        )
                    # Restore exact buy_cover step
                    session.pop("paused_context", None)
                    flow.update({"step": bc_step_y, "data": bc_data_y, "active": True})
                    await save_session(session)
                    await send_text_message(
                        to=sender_wa_id,
                        body="✅ *Welcome back!* Continuing from where you left off...",
                        phone_number_id=phone_number_id,
                        source="buy_cover_flow",
                    )
                    await _redisplay_step(
                        sender_wa_id, bc_step_y, bc_data_y, session, phone_number_id
                    )
                    return

            # ── B. API-based resume (no paused context) ────────────────────────
            resume_data = session.get("api_data", {}).get("resume_draft", {})
            state = resume_data.get("creation_state", "DRAFT")
            pid = resume_data.get("policy_id")
            session["api_data"]["policy_id"] = pid

            passengers = resume_data.get("passengers", [])
            passenger_ids = [p["id"] for p in passengers if p.get("id")]
            session["api_data"]["passenger_ids"] = passenger_ids
            names = [
                f"{p.get('firstName', '')} {p.get('surname', '')}".strip()
                for p in passengers
                if p.get("firstName") or p.get("surname")
            ]

            itinerary = resume_data.get("itinerary", {})
            legs = itinerary.get("legs", [])
            leg = legs[0] if legs else {}
            trip_raw = resume_data.get("trip_type", "ONE_WAY")
            data["name"] = names[0] if names else ""
            data["email"] = resume_data.get("email", "")
            data["booking_ref"] = itinerary.get("bookingReference", "")
            data["flight_num"] = leg.get("flightNumber", "")
            dep_code = leg.get("departureAirport", "")
            arr_code = leg.get("arrivalAirport", "")
            data["depart_airport"] = f"{dep_code} — {dep_code}" if dep_code else ""
            data["arrive_airport"] = f"{arr_code} — {arr_code}" if arr_code else ""
            data["date"] = leg.get("departureDate", "")
            data["depart_time"] = leg.get("departureTime", "")
            data["arrive_time"] = leg.get("arrivalTime", "")
            data["airline"] = leg.get("carrier", "")
            data["trip_type"] = "Return 🔄" if trip_raw == "RETURN" else "One-way 🗺️"
            if len(names) > 1:
                data["who"] = "me_and_others"
                data["travelers"] = names
                data["others_count"] = len(names) - 1
            else:
                data["who"] = "just_me"
            await save_session(session)

            if state in ("AWAITING_KYC", "DETAILS_COLLECTED"):
                quotes = None
                try:
                    quotes = await ipurvey_service.fetch_quotes(pid)
                except Exception as exc:
                    logger.error(f"[buy_cover] resume fetch_quotes failed: {exc}")
                if not quotes:
                    flow["step"] = "buy_cover_summary"
                    await save_session(session)
                    await _send_buttons(
                        sender_wa_id,
                        "⚠️ *We're unable to load covers right now*\n\nPlease try again shortly",
                        [
                            {"id": "summary_confirm", "title": "🔄 Try again"},
                            {"id": "summary_edit", "title": "✏️ Edit details"},
                        ],
                        phone_number_id,
                    )
                    return
                session.setdefault("api_data", {})["quotes"] = quotes
                flow["step"] = "buy_cover_select_cover"
                await save_session(session)
                await _send_cover_page(
                    sender_wa_id,
                    quotes,
                    0,
                    phone_number_id,
                    intro_body=(
                        "🎁 *Great news — your trip details are already saved!*\n\n"
                        "👇 Just pick your cover plan to continue:"
                    ),
                )
                return

            elif state == "AWAITING_ITINERARY":
                flow["step"] = "buy_cover_trip_type"
                await save_session(session)
                await _send_buttons(
                    sender_wa_id,
                    "🗺️ What type of trip is this?",
                    [
                        {"id": "trip_oneway", "title": "1. 🗺️ One-way"},
                    ],
                    phone_number_id,
                )
                return

            elif state == "DRAFT":
                # DRAFT with named passengers — route to first missing field
                if not data.get("email"):
                    flow["step"] = "buy_cover_email"
                    await save_session(session)
                    await _send_text(
                        sender_wa_id,
                        "*📧 Please enter your email address*\n"
                        "So we can send your policy documents\n\n"
                        "_Example: yusuf@email.com_",
                        phone_number_id,
                    )
                else:
                    flow["step"] = "buy_cover_trip_type"
                    await save_session(session)
                    await _send_buttons(
                        sender_wa_id,
                        "🗺️ What type of trip is this?",
                        [
                            {"id": "trip_oneway", "title": "1. 🗺️ One-way"},
                        ],
                        phone_number_id,
                    )
                return

            else:
                flow["step"] = "buy_cover_who"
                await save_session(session)
                await _send_buttons(
                    sender_wa_id,
                    "✈️ Let's continue your application!\n\nIs this cover for:",
                    [
                        {"id": "cover_just_me", "title": "1. 🧑 Just me"},
                        {"id": "cover_others", "title": "2. 👥 Me & Others"},
                    ],
                    phone_number_id,
                )
                return

        # No valid reply — re-prompt
        await _send_buttons(
            sender_wa_id,
            "📋 *Incomplete Application Found*\n\nWould you like to continue where you left off?",
            [
                {"id": "resume_yes", "title": "▶️ Resume"},
                {"id": "resume_fresh", "title": "🆕 Start Fresh"},
            ],
            phone_number_id,
        )
        return

    # ── Who is covered ────────────────────────────────────────────────────────
    elif step == "buy_cover_who":
        if not reply_id and text:
            _t = text.strip().lower()
            if _t in (
                "1",
                "just me",
                "just_me",
                "me",
                "solo",
                "alone",
                "single",
                "one",
                "only me",
                "myself",
                "me only",
            ):
                reply_id = "cover_just_me"
            elif _t in (
                "2",
                "others",
                "me and others",
                "group",
                "family",
                "friends",
                "more",
                "multiple",
                "me_and",
                "we",
            ):
                reply_id = "cover_others"
        if not reply_id and text:
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="cover_for",
                question_asked="Is this cover for just you or for you and others?",
                user_response=text,
                expected_format="text",
            )
            if (
                llm_result
                and llm_result.get("is_valid")
                and llm_result.get("extracted_value")
            ):
                ev = str(llm_result["extracted_value"]).lower()
                if any(
                    k in ev
                    for k in (
                        "just me",
                        "only me",
                        "myself",
                        "just_me",
                        "solo",
                        "alone",
                        "single",
                        "one person",
                    )
                ):
                    reply_id = "cover_just_me"
                elif any(
                    k in ev
                    for k in (
                        "others",
                        "group",
                        "family",
                        "friends",
                        "more",
                        "multiple",
                        "me and",
                        "me_and",
                    )
                ):
                    reply_id = "cover_others"
            if not reply_id:
                await _send_buttons(
                    sender_wa_id,
                    "✈️ *Who is this cover for?*\n\nPlease select an option:",
                    [
                        {"id": "cover_just_me", "title": "1. 🧑 Just me"},
                        {"id": "cover_others", "title": "2. 👥 Me & Others"},
                    ],
                    phone_number_id,
                )
                return
        if reply_id == "cover_just_me":
            data["who"] = "just_me"
            flow["step"] = "buy_cover_name"
            await save_session(session)
            policy_id = session.get("api_data", {}).get("policy_id")
            if policy_id:
                try:
                    pax_ids = await ipurvey_service.set_traveler_count(policy_id, 1)
                    if pax_ids:
                        api_data = session.setdefault("api_data", {})
                        # pax_ids is already a list of UUID strings e.g. ["492af597-..."]
                        api_data["passenger_ids"] = [
                            (
                                p
                                if isinstance(p, str)
                                else (p.get("passengerId") or p.get("id") or "")
                            )
                            for p in pax_ids
                            if p
                        ]
                        logger.info(
                            f"[BUY_COVER] pre-allocated passenger_ids: {api_data['passenger_ids']}"
                        )
                        await save_session(session)
                except Exception as exc:
                    logger.error(f"[BUY_COVER] set_traveler_count(1) failed: {exc}")
            await _send_text(
                sender_wa_id,
                (
                    "👤 👑 *Enter main passenger name*\n"
                    "Enter first name and surname as it appears on the ticket.\n\n"
                    "ℹ️ This person is the main passenger.\n\n"
                    "_Example: Yusuf Usman_"
                ),
                phone_number_id,
            )
        else:
            data["who"] = "me_and_others"
            flow["step"] = "buy_cover_traveler_count"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                "👥 *How many travellers are covered?*\n"
                "Please reply with a number\n\n"
                "_Example: 2_\n\n"
                "⚠️ Maximum number of travellers you can add is *10*.",
                phone_number_id,
            )

    # ── Traveler count ────────────────────────────────────────────────────────
    elif step == "buy_cover_traveler_count":
        raw = (text or "").strip()
        count_int: Optional[int] = None
        if raw:
            try:
                count_int = int(raw)
            except ValueError:
                pass

        if count_int is None:
            await _send_text(
                sender_wa_id,
                "⚠️ Please reply with a number.\n_Example: 2_",
                phone_number_id,
            )
            return

        if count_int > 10:
            await _send_text(
                sender_wa_id,
                "⚠️ *travelerCount cannot exceed 10*\n\nPlease enter a number from 1 to 10.",
                phone_number_id,
            )
            return

        if count_int < 0:
            await _send_text(
                sender_wa_id,
                "⚠️ Please enter a number from 1 to 10.\n_Type 0 to remove additional travellers._",
                phone_number_id,
            )
            return

        # 9 conflicts with the Help shortcut — ask user to confirm intent
        if count_int == 9:
            data["pending_traveler_count"] = 9
            flow["step"] = "buy_cover_tc_9_confirm"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                "👥 *Confirm traveller count*\n\n"
                "You entered *9*. What would you like to do?",
                [
                    {"id": "tc_9_confirm", "title": "✅ Add 9 travellers"},
                    {"id": "tc_9_help",    "title": "🆘 Get Help"},
                ],
                phone_number_id,
            )
            return

        # 0 → switch to "just me" mode (remove additional travellers)
        if count_int == 0:
            data["who"] = "just_me"
            data.pop("others_count", None)
            data["travelers"] = []
            flow["step"] = "buy_cover_name"
            await save_session(session)
            policy_id = session.get("api_data", {}).get("policy_id")
            if policy_id:
                try:
                    pax_ids = await ipurvey_service.set_traveler_count(policy_id, 1)
                    if pax_ids:
                        api_data = session.setdefault("api_data", {})
                        api_data["passenger_ids"] = [
                            (
                                p
                                if isinstance(p, str)
                                else (p.get("passengerId") or p.get("id") or "")
                            )
                            for p in pax_ids
                            if p
                        ]
                        logger.info(
                            f"[BUY_COVER] pre-allocated passenger_ids: {api_data['passenger_ids']}"
                        )
                        await save_session(session)
                except Exception as exc:
                    logger.error(f"[BUY_COVER] set_traveler_count(1) failed: {exc}")
            await _send_text(
                sender_wa_id,
                (
                    "👤 👑 *Enter main passenger name*\n"
                    "Enter first name and surname as it appears on the ticket.\n\n"
                    "ℹ️ This person is the main passenger.\n\n"
                    "_Example: Yusuf Usman_"
                ),
                phone_number_id,
            )
            return

        # count_int 1–10: total travellers including primary
        others_count = count_int - 1
        data["others_count"] = others_count
        data["travelers"] = []
        flow["step"] = "buy_cover_name"
        await save_session(session)
        policy_id = session.get("api_data", {}).get("policy_id")
        if policy_id:
            try:
                pax_ids = await ipurvey_service.set_traveler_count(policy_id, count_int)
                if pax_ids:
                    api_data = session.setdefault("api_data", {})
                    api_data["passenger_ids"] = [
                        (
                            p
                            if isinstance(p, str)
                            else (p.get("passengerId") or p.get("id") or "")
                        )
                        for p in pax_ids
                        if p
                    ]
                    logger.info(
                        f"[BUY_COVER] pre-allocated passenger_ids: {api_data['passenger_ids']}"
                    )
                    await save_session(session)
            except Exception as exc:
                logger.error(
                    f"[BUY_COVER] set_traveler_count({count_int}) failed: {exc}"
                )
        await _send_text(
            sender_wa_id,
            (
                "👤 👑 *Enter main passenger name*\n"
                "Enter first name and surname as it appears on the ticket.\n\n"
                "ℹ️ This person is the main passenger.\n\n"
                "_Example: Yusuf Usman_"
            ),
            phone_number_id,
        )

    # ── Traveler count 9 — disambiguation ────────────────────────────────────
    elif step == "buy_cover_tc_9_confirm":
        # Map typed "1"/"2" to button ids
        _tc9_raw = (text or "").strip()
        if _tc9_raw == "1":
            reply_id = "tc_9_confirm"
        elif _tc9_raw == "2":
            reply_id = "tc_9_help"

        if reply_id == "tc_9_confirm":
            count_int = data.pop("pending_traveler_count", 9)
            data["others_count"] = count_int - 1
            data["travelers"]    = []
            flow["step"] = "buy_cover_name"
            await save_session(session)
            policy_id = session.get("api_data", {}).get("policy_id")
            if policy_id:
                try:
                    pax_ids = await ipurvey_service.set_traveler_count(policy_id, count_int)
                    if pax_ids:
                        api_data = session.setdefault("api_data", {})
                        api_data["passenger_ids"] = [
                            p if isinstance(p, str) else (p.get("passengerId") or p.get("id") or "")
                            for p in pax_ids if p
                        ]
                        logger.info(f"[BUY_COVER] pre-allocated passenger_ids (9): {api_data['passenger_ids']}")
                        await save_session(session)
                except Exception as exc:
                    logger.error(f"[BUY_COVER] set_traveler_count(9) failed: {exc}")
            await _send_text(
                sender_wa_id,
                (
                    "👤 👑 *Enter main passenger name*\n"
                    "Enter first name and surname as it appears on the ticket.\n\n"
                    "ℹ️ This person is the main passenger.\n\n"
                    "_Example: Yusuf Usman_"
                ),
                phone_number_id,
            )

        elif reply_id == "tc_9_help":
            from app.services.help_flow_service import start_help_flow
            await pause_buy_cover_flow(sender_wa_id)
            await start_help_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id)

        else:
            # Unknown input — re-show the confirmation buttons
            await _send_buttons(
                sender_wa_id,
                "👥 *Confirm traveller count*\n\n"
                "You entered *9*. What would you like to do?\n\n"
                "_Type *1* to add 9 travellers or *2* for help_",
                [
                    {"id": "tc_9_confirm", "title": "✅ Add 9 travellers"},
                    {"id": "tc_9_help",    "title": "🆘 Get Help"},
                ],
                phone_number_id,
            )

    # ── Name ──────────────────────────────────────────────────────────────────
    elif step == "buy_cover_name":
        if text and await _maybe_answer_question(
            text,
            sender_wa_id,
            phone_number_id,
            "👤 Please enter your *full name* (first name and surname) as it appears on your ticket.\n\n_Example: Yusuf Abdullahi_",
            current_node="buy_cover_name",
        ):
            return
        if not text or not _is_valid_name(text):
            if text and "@" in text:
                await _send_text(
                    sender_wa_id,
                    "⚠️ That looks like an email address — please enter your *name* instead.\n\n_Example: Yusuf Abdullahi_",
                    phone_number_id,
                )
                return
            if _contains_emoji(text or ""):
                await _send_text(
                    sender_wa_id,
                    "⚠️ Name cannot contain emojis. Please enter your *full name*.\n\n_Example: Yusuf Abdullahi_",
                    phone_number_id,
                )
                return
            if not text or not any(c.isalpha() for c in text):
                await _send_text(
                    sender_wa_id,
                    "⚠️ Please enter a valid *full name* (first name and surname).\n\n_Example: Yusuf Abdullahi_",
                    phone_number_id,
                )
                return
            # Multi-word sentence that is not a valid name → call LLM generic to respond
            if (
                text
                and _is_sentence_like(text, min_words=2)
                and not any(c.isdigit() for c in text)
            ):
                await _call_llm_and_reprompt(
                    text,
                    sender_wa_id,
                    phone_number_id,
                    "👤 Please enter your *full name* (first name and surname) as it appears on your ticket.\n\n_Example: Yusuf Abdullahi_",
                    current_node="buy_cover_name",
                )
                return
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="passenger_name",
                question_asked="Please enter your full name (first name and surname) as it appears on your ticket",
                user_response=text,
                expected_format="full_name",
            )
            if (
                llm_result
                and llm_result.get("is_valid")
                and llm_result.get("extracted_value")
            ):
                extracted_name = str(llm_result["extracted_value"]).strip()
                if _is_valid_name(extracted_name):
                    data["_pending_name"] = extracted_name
                    data["_name_confirm_for"] = "main"
                    flow["step"] = "buy_cover_name_confirm"
                    await save_session(session)
                    await _send_buttons(
                        sender_wa_id,
                        f"Did you mean: *{extracted_name}*?",
                        [
                            {
                                "id": "name_confirm_yes",
                                "title": "✅ Yes, that's correct",
                            },
                            {"id": "name_confirm_no", "title": "✏️ Re-enter name"},
                        ],
                        phone_number_id,
                    )
                    return
            await _send_text(
                sender_wa_id,
                "⚠️ Please enter a valid *full name* (first name and surname).\n\n_Example: Yusuf Abdullahi_",
                phone_number_id,
            )
            return
        policy_id = session.get("api_data", {}).get("policy_id")
        existing_pid = session.get("api_data", {}).get("passenger_id")
        if policy_id:
            fn, ln = _split_name(text)
            try:
                if existing_pid:
                    ok = await ipurvey_service.update_passenger(
                        policy_id, existing_pid, fn, ln, is_primary=True
                    )
                    logger.info(
                        f"[BUY_COVER] update_passenger (primary) → {fn} {ln} ok={ok}"
                    )
                    if not ok:
                        await _send_text(
                            sender_wa_id,
                            (
                                "⚠️ We couldn't update your name — please try again.\n\n"
                                "_Example: Yusuf Abdullahi_"
                            ),
                            phone_number_id,
                        )
                        return
                else:
                    result = await ipurvey_service.add_passenger(
                        policy_id, fn, ln, is_primary=True
                    )
                    logger.info(
                        f"[BUY_COVER] add_passenger (primary) → {fn} {ln} result={result}"
                    )
                    if result is None:
                        logger.error(
                            f"[BUY_COVER] add_passenger (primary) returned None for '{fn} {ln}'"
                        )
                        await _send_text(
                            sender_wa_id,
                            (
                                "⚠️ We couldn't save your name — please enter your *full name* "
                                "(first name and surname) as it appears on your ticket.\n\n"
                                "_Example: Yusuf Abdullahi_"
                            ),
                            phone_number_id,
                        )
                        return
                    if result and result.get("passengerId"):
                        session.setdefault("api_data", {})["passenger_id"] = result[
                            "passengerId"
                        ]
                        logger.info(
                            f"[BUY_COVER] saved passenger_id='{result['passengerId']}'"
                        )
            except Exception as exc:
                logger.error(
                    f"[BUY_COVER] add/update_passenger (primary) failed: {exc}"
                )
                await _send_text(
                    sender_wa_id,
                    (
                        "⚠️ We couldn't save your name — please enter your *full name* "
                        "(first name and surname) as it appears on your ticket.\n\n"
                        "_Example: Yusuf Abdullahi_"
                    ),
                    phone_number_id,
                )
                return
        data["name"] = text
        if data.pop("_edit_mode", False):
            await _show_trip_summary(sender_wa_id, data, flow, session, phone_number_id)
            return
        if data.get("who") == "me_and_others":
            travelers = data.get("travelers", [])
            travelers.append(text)
            data["travelers"] = travelers
            others_count = data.get("others_count", 0)
            if others_count == 0:
                # Only the primary traveller — go straight to email
                flow["step"] = "buy_cover_email"
                await save_session(session)
                await _send_text(
                    sender_wa_id,
                    (
                        "*📧 Please enter your email address*\n"
                        "So we can send your policy documents\n\n"
                        "_Example: yusuf@email.com_"
                    ),
                    phone_number_id,
                )
            else:
                data["others_collected"] = 0
                flow["step"] = "buy_cover_other_name"
                await save_session(session)
                total = others_count + 1
                await _send_text(
                    sender_wa_id,
                    (
                        f"👤 *Traveller 2 of {total}*\n"
                        "Enter first name and surname as it appears on their ticket.\n\n"
                        "_Example: Amina Bello_"
                    ),
                    phone_number_id,
                )
        else:
            flow["step"] = "buy_cover_email"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                (
                    "*📧 Please enter your email address*\n"
                    "So we can send your policy documents\n\n"
                    "_Example: yusuf@email.com_"
                ),
                phone_number_id,
            )

    # ── Name confirmation (LLM-extracted) ─────────────────────────────────────
    elif step == "buy_cover_name_confirm":
        pending_name = data.get("_pending_name", "")
        confirm_for = data.get("_name_confirm_for", "main")
        if reply_id == "name_confirm_yes" and pending_name:
            name_to_use = pending_name
            data.pop("_pending_name", None)
            data.pop("_name_confirm_for", None)
            if confirm_for == "other":
                policy_id = session.get("api_data", {}).get("policy_id")
                if policy_id:
                    fn, ln = _split_name(name_to_use)
                    current_travelers_c = data.get("travelers", [])
                    pax_idx_c = len(current_travelers_c)
                    pax_ids_c = session.get("api_data", {}).get("passenger_ids", [])
                    existing_other_pid_c = (
                        pax_ids_c[pax_idx_c] if pax_idx_c < len(pax_ids_c) else None
                    )
                    try:
                        if existing_other_pid_c:
                            ok_c = await ipurvey_service.update_passenger(
                                policy_id,
                                existing_other_pid_c,
                                fn,
                                ln,
                                is_primary=False,
                            )
                            if not ok_c:
                                flow["step"] = "buy_cover_other_name"
                                await save_session(session)
                                await _send_text(
                                    sender_wa_id,
                                    "⚠️ We couldn't save this traveler's name — please enter their *full name*.\n\n_Example: Amina Bello_",
                                    phone_number_id,
                                )
                                return
                        else:
                            result = await ipurvey_service.add_passenger(
                                policy_id, fn, ln, is_primary=False
                            )
                            if result is None:
                                flow["step"] = "buy_cover_other_name"
                                await save_session(session)
                                await _send_text(
                                    sender_wa_id,
                                    "⚠️ We couldn't save this traveler's name — please enter their *full name*.\n\n_Example: Amina Bello_",
                                    phone_number_id,
                                )
                                return
                    except Exception as exc:
                        logger.error(
                            f"[BUY_COVER] name_confirm add/update_passenger (other) failed: {exc}"
                        )
                        flow["step"] = "buy_cover_other_name"
                        await save_session(session)
                        await _send_text(
                            sender_wa_id,
                            "⚠️ We couldn't save this traveler's name — please enter their *full name*.\n\n_Example: Amina Bello_",
                            phone_number_id,
                        )
                        return
                travelers = data.get("travelers", [])
                travelers.append(name_to_use)
                data["travelers"] = travelers
                others_count = data.get("others_count", 1)
                others_collected = len(travelers) - 1
                if others_collected < others_count:
                    next_num = others_collected + 2
                    total = others_count + 1
                    flow["step"] = "buy_cover_other_name"
                    await save_session(session)
                    await _send_text(
                        sender_wa_id,
                        f"👤 *Traveller {next_num} of {total}*\nEnter first name and surname as it appears on their ticket.\n\n_Example: Amina Bello_",
                        phone_number_id,
                    )
                else:
                    flow["step"] = "buy_cover_email"
                    await save_session(session)
                    summary_lines = []
                    for i, n in enumerate(travelers):
                        if i == 0:
                            summary_lines.append(
                                f"{i + 1}. {n}  👤👑 _(Main passenger)_"
                            )
                        else:
                            summary_lines.append(f"{i + 1}. {n}  👤")
                    await _send_text(
                        sender_wa_id,
                        f"✅ *All traveller names added*\n\n"
                        + "\n".join(summary_lines),
                        phone_number_id,
                    )
                    await _send_text(
                        sender_wa_id,
                        "*📧 Please enter your email address*\nSo we can send your policy documents\n\n_Example: yusuf@email.com_",
                        phone_number_id,
                    )
            else:
                policy_id = session.get("api_data", {}).get("policy_id")
                existing_pid = session.get("api_data", {}).get("passenger_id")
                if policy_id:
                    fn, ln = _split_name(name_to_use)
                    try:
                        if existing_pid:
                            ok = await ipurvey_service.update_passenger(
                                policy_id, existing_pid, fn, ln, is_primary=True
                            )
                            if not ok:
                                flow["step"] = "buy_cover_name"
                                await save_session(session)
                                await _send_text(
                                    sender_wa_id,
                                    "⚠️ We couldn't update your name — please try again.\n\n_Example: Yusuf Abdullahi_",
                                    phone_number_id,
                                )
                                return
                        else:
                            result = await ipurvey_service.add_passenger(
                                policy_id, fn, ln, is_primary=True
                            )
                            if result is None:
                                flow["step"] = "buy_cover_name"
                                await save_session(session)
                                await _send_text(
                                    sender_wa_id,
                                    "⚠️ We couldn't save your name — please enter your *full name*.\n\n_Example: Yusuf Abdullahi_",
                                    phone_number_id,
                                )
                                return
                            if result and result.get("passengerId"):
                                session.setdefault("api_data", {})["passenger_id"] = (
                                    result["passengerId"]
                                )
                    except Exception as exc:
                        logger.error(
                            f"[BUY_COVER] name_confirm add/update_passenger failed: {exc}"
                        )
                        flow["step"] = "buy_cover_name"
                        await save_session(session)
                        await _send_text(
                            sender_wa_id,
                            "⚠️ We couldn't save your name — please enter your *full name*.\n\n_Example: Yusuf Abdullahi_",
                            phone_number_id,
                        )
                        return
                data["name"] = name_to_use
                if data.pop("_edit_mode", False):
                    await _show_trip_summary(
                        sender_wa_id, data, flow, session, phone_number_id
                    )
                    return
                if data.get("who") == "me_and_others":
                    travelers = data.get("travelers", [])
                    travelers.append(name_to_use)
                    data["travelers"] = travelers
                    others_count = data.get("others_count", 0)
                    if others_count == 0:
                        flow["step"] = "buy_cover_email"
                        await save_session(session)
                        await _send_text(
                            sender_wa_id,
                            "*📧 Please enter your email address*\nSo we can send your policy documents\n\n_Example: yusuf@email.com_",
                            phone_number_id,
                        )
                    else:
                        data["others_collected"] = 0
                        flow["step"] = "buy_cover_other_name"
                        await save_session(session)
                        total = others_count + 1
                        await _send_text(
                            sender_wa_id,
                            f"👤 *Traveller 2 of {total}*\nEnter first name and surname as it appears on their ticket.\n\n_Example: Amina Bello_",
                            phone_number_id,
                        )
                else:
                    flow["step"] = "buy_cover_email"
                    await save_session(session)
                    await _send_text(
                        sender_wa_id,
                        "*📧 Please enter your email address*\nSo we can send your policy documents\n\n_Example: yusuf@email.com_",
                        phone_number_id,
                    )
        else:
            data.pop("_pending_name", None)
            data.pop("_name_confirm_for", None)
            if confirm_for == "other":
                flow["step"] = "buy_cover_other_name"
                others_count = data.get("others_count", 1)
                collected = max(len(data.get("travelers", [])) - 1, 0)
                total = others_count + 1
                next_num = collected + 2
                await save_session(session)
                await _send_text(
                    sender_wa_id,
                    f"👤 *Traveller {next_num} of {total}*\nEnter first name and surname as it appears on their ticket.\n\n_Example: Amina Bello_",
                    phone_number_id,
                )
            else:
                flow["step"] = "buy_cover_name"
                await save_session(session)
                await _send_text(
                    sender_wa_id,
                    "👤 *Please enter your full name*\nEnter first name and surname as it appears on the ticket.\n\n_Example: Yusuf Abdullahi_",
                    phone_number_id,
                )

    # ── Additional traveler names ──────────────────────────────────────────────
    elif step == "buy_cover_other_name":
        if not text or not _is_valid_name(text):
            if text and "@" in text:
                await _send_text(
                    sender_wa_id,
                    "⚠️ That looks like an email address — please enter the traveler's *name* instead.\n\n_Example: Amina Bello_",
                    phone_number_id,
                )
                return
            if _contains_emoji(text or ""):
                await _send_text(
                    sender_wa_id,
                    "⚠️ Name cannot contain emojis. Please enter the traveler's *full name*.\n\n_Example: Amina Bello_",
                    phone_number_id,
                )
                return
            if not text or not any(c.isalpha() for c in text):
                await _send_text(
                    sender_wa_id,
                    "⚠️ Please enter a valid *full name* (first name and surname).\n\n_Example: Amina Bello_",
                    phone_number_id,
                )
                return
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="passenger_name",
                question_asked="Please enter the traveler's full name (first name and surname) as it appears on their ticket",
                user_response=text,
                expected_format="full_name",
            )
            if (
                llm_result
                and llm_result.get("is_valid")
                and llm_result.get("extracted_value")
            ):
                extracted_name = str(llm_result["extracted_value"]).strip()
                if _is_valid_name(extracted_name):
                    data["_pending_name"] = extracted_name
                    data["_name_confirm_for"] = "other"
                    flow["step"] = "buy_cover_name_confirm"
                    await save_session(session)
                    await _send_buttons(
                        sender_wa_id,
                        f"Did you mean: *{extracted_name}*?",
                        [
                            {
                                "id": "name_confirm_yes",
                                "title": "✅ Yes, that's correct",
                            },
                            {"id": "name_confirm_no", "title": "✏️ Re-enter name"},
                        ],
                        phone_number_id,
                    )
                    return
            await _send_text(
                sender_wa_id,
                "⚠️ Please enter a valid *full name* (first name and surname).\n\n_Example: Amina Bello_",
                phone_number_id,
            )
            return
        policy_id = session.get("api_data", {}).get("policy_id")
        if policy_id:
            fn, ln = _split_name(text)
            # pax_idx: position in passenger_ids list for this additional traveller.
            # travelers[0] = main pax, so len(travelers before append) = slot index.
            current_travelers = data.get("travelers", [])
            pax_idx = len(current_travelers)
            pax_ids = session.get("api_data", {}).get("passenger_ids", [])
            existing_other_pid = pax_ids[pax_idx] if pax_idx < len(pax_ids) else None
            try:
                if existing_other_pid:
                    # Slot already pre-allocated (or previously filled via back-nav).
                    # Use update_passenger to avoid "all slots filled" 400 error.
                    ok = await ipurvey_service.update_passenger(
                        policy_id, existing_other_pid, fn, ln, is_primary=False
                    )
                    logger.info(
                        f"[BUY_COVER] update_passenger (additional) → {fn} {ln} "
                        f"pid={existing_other_pid} ok={ok}"
                    )
                    if not ok:
                        await _send_text(
                            sender_wa_id,
                            (
                                "⚠️ We couldn't save this traveler's name — please enter their *full name* "
                                "(first name and surname) as it appears on their ticket.\n\n"
                                "_Example: Amina Bello_"
                            ),
                            phone_number_id,
                        )
                        return
                else:
                    # No pre-allocated slot — fall back to add_passenger.
                    result = await ipurvey_service.add_passenger(
                        policy_id, fn, ln, is_primary=False
                    )
                    logger.info(
                        f"[BUY_COVER] add_passenger (additional) → {fn} {ln} result={result}"
                    )
                    if result is None:
                        logger.error(
                            f"[BUY_COVER] add_passenger (additional) returned None for '{fn} {ln}'"
                        )
                        await _send_text(
                            sender_wa_id,
                            (
                                "⚠️ We couldn't save this traveler's name — please enter their *full name* "
                                "(first name and surname) as it appears on their ticket.\n\n"
                                "_Example: Amina Bello_"
                            ),
                            phone_number_id,
                        )
                        return
            except Exception as exc:
                logger.error(
                    f"[BUY_COVER] add/update_passenger (additional) failed: {exc}"
                )
                await _send_text(
                    sender_wa_id,
                    (
                        "⚠️ We couldn't save this traveler's name — please enter their *full name* "
                        "(first name and surname) as it appears on their ticket.\n\n"
                        "_Example: Amina Bello_"
                    ),
                    phone_number_id,
                )
                return
        travelers = data.get("travelers", [])
        travelers.append(text)
        data["travelers"] = travelers
        others_count = data.get("others_count", 1)
        others_collected = len(travelers) - 1
        if others_collected < others_count:
            next_num = others_collected + 2
            total = others_count + 1
            await save_session(session)
            await _send_text(
                sender_wa_id,
                (
                    f"👤 *Traveller {next_num} of {total}*\n"
                    "Enter first name and surname as it appears on their ticket.\n\n"
                    "_Example: Amina Bello_"
                ),
                phone_number_id,
            )
        else:
            flow["step"] = "buy_cover_email"
            await save_session(session)
            summary_lines = []
            for i, n in enumerate(travelers):
                if i == 0:
                    summary_lines.append(f"{i + 1}. {n}  👤👑 _(Main passenger)_")
                else:
                    summary_lines.append(f"{i + 1}. {n}  👤")
            names_list = "\n".join(summary_lines)
            await _send_text(
                sender_wa_id,
                f"✅ *All traveller names added*\n\n{names_list}",
                phone_number_id,
            )
            await _send_text(
                sender_wa_id,
                (
                    "*📧 Please enter your email address*\n"
                    "So we can send your policy documents\n\n"
                    "_Example: yusuf@email.com_"
                ),
                phone_number_id,
            )

    # ── Email ─────────────────────────────────────────────────────────────────
    elif step == "buy_cover_email":
        if text and (
            _looks_like_question(text) or _is_sentence_like(text, min_words=3)
        ):
            await _call_llm_and_reprompt(
                text,
                sender_wa_id,
                phone_number_id,
                "📧 Please enter your *email address* so we can send your policy documents.\n\n_Example: yusuf@email.com_",
                current_node="buy_cover_email",
            )
            return
        if not text or not _is_valid_email(text):
            await _send_text(
                sender_wa_id,
                ("⚠️ Please enter a valid email address\n\n_Example: yusuf@email.com_"),
                phone_number_id,
            )
            return
        email_clean = text.strip().lower()
        data["email"] = email_clean
        policy_id = session.get("api_data", {}).get("policy_id")
        if policy_id:
            try:
                await ipurvey_service.set_policy_email(policy_id, email_clean)
            except Exception:
                pass
        if data.pop("_edit_mode", False):
            await _show_trip_summary(sender_wa_id, data, flow, session, phone_number_id)
            return
        flow["step"] = "buy_cover_trip_type"
        await save_session(session)
        await _send_trip_type_buttons(sender_wa_id, phone_number_id)

    # ── Trip type ─────────────────────────────────────────────────────────────
    elif step == "buy_cover_trip_type":
        valid_trip_types = await ipurvey_service.get_trip_types()
        valid_values = {t["value"] for t in valid_trip_types if isinstance(t, dict) and t.get("value")}
        if not valid_values:
            valid_values = {"ONE_WAY", "RETURN"}

        chosen_api_value = None
        if reply_id == "trip_oneway" or (text and text.strip() == "1"):
            chosen_api_value = "ONE_WAY"
        elif reply_id == "trip_return" or (text and text.strip() == "2"):
            chosen_api_value = "RETURN"
        elif text:
            _tl = text.strip().lower()
            if any(k in _tl for k in ("one", "oneway", "one-way", "one way")):
                chosen_api_value = "ONE_WAY"
            elif any(k in _tl for k in ("return", "round", "two", "twoway", "two-way")):
                chosen_api_value = "RETURN"

        if chosen_api_value and chosen_api_value not in valid_values:
            await _send_trip_type_buttons(sender_wa_id, phone_number_id)
            return

        chosen_type = None
        if chosen_api_value == "ONE_WAY":
            chosen_type = "One-way 🗺️"
        elif chosen_api_value == "RETURN":
            chosen_type = "Return 🔄"

        if chosen_type:
            data["trip_type"] = chosen_type
            flow["step"] = "buy_cover_booking_ref"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                "*🎫 Please enter your booking reference*\n\n_Examples: AB1XY2, 2990FA62_",
                phone_number_id,
            )
        else:
            await _send_trip_type_buttons(sender_wa_id, phone_number_id)

    # ── Booking reference ─────────────────────────────────────────────────────
    elif step == "buy_cover_booking_ref":
        if text and (
            _looks_like_question(text) or _is_sentence_like(text, min_words=3)
        ):
            await _call_llm_and_reprompt(
                text,
                sender_wa_id,
                phone_number_id,
                "🎫 Please enter your *booking reference* — a short alphanumeric code from your airline confirmation email.\n\n_Examples: AB1XY2, 2990FA62_",
                current_node="buy_cover_booking_ref",
            )
            return
        if not text or not _is_valid_booking_ref(text):
            await _send_text(
                sender_wa_id,
                (
                    "🎫 That doesn't look like a valid booking reference.\n\n"
                    "Your booking reference is a short alphanumeric code found in "
                    "your airline confirmation email or ticket.\n\n"
                    "_Examples: AB1XY2, 2990FA62, XYZ123_\n\n"
                    "Please enter your booking reference, or type *0* to go back."
                ),
                phone_number_id,
            )
            return
        data["booking_ref"] = text.strip().upper()
        if data.pop("_edit_mode", False):
            await _show_trip_summary(sender_wa_id, data, flow, session, phone_number_id)
            return
        flow["step"] = "buy_cover_flight_num"
        await save_session(session)
        await _send_text(
            sender_wa_id,
            "*✈️ Please enter your flight number*\n\n_Example: P47123, Q1402_",
            phone_number_id,
        )

    # ── Flight number ─────────────────────────────────────────────────────────
    elif step == "buy_cover_flight_num":
        if not text or not _is_valid_flight_number(text):
            await _send_text(
                sender_wa_id,
                (
                    "✈️ Invalid characters are not allowed. Only valid characters like *P47124* are allowed.\n\n"
                    "Please re-enter the flight number\n\n"
                    "_Examples: P47123 — Air Peace, QI402 — Ibom Air_"
                ),
                phone_number_id,
            )
            return
        data["flight_num"] = text.strip().upper().replace(" ", "")
        if data.pop("_edit_mode", False):
            await _show_trip_summary(sender_wa_id, data, flow, session, phone_number_id)
            return
        flow["step"] = "buy_cover_date"
        await save_session(session)
        await _send_text(
            sender_wa_id,
            "*📅 What date are you flying?*\n\n_Example: 12 April 2026, 12/04/2026, 12-04-2026_",
            phone_number_id,
        )

    # ── Flying date ───────────────────────────────────────────────────────────
    elif step == "buy_cover_date":
        iso_date = _parse_date_to_iso(text or "")
        if not iso_date and text:
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="departure_date",
                question_asked="What is your departure date? Please provide in DD/MM/YYYY format.",
                user_response=text,
                expected_format="date",
            )
            if (
                llm_result
                and llm_result.get("is_valid")
                and llm_result.get("extracted_value")
            ):
                iso_date = _parse_date_to_iso(str(llm_result["extracted_value"]))
        if not iso_date:
            await _send_text(
                sender_wa_id,
                (
                    "📅 Please enter the date like this: *12 April 2026*\n\n"
                    "_Other accepted formats: 12/04/2026, 12-04-2026, 12-04-26_"
                ),
                phone_number_id,
            )
            return
        if _is_past_date(iso_date):
            await _send_text(
                sender_wa_id,
                (
                    "⚠️ Please enter today's date or a future travel date\n\n"
                    "_Example: 12 April 2026_"
                ),
                phone_number_id,
            )
            return
        if _is_too_far_future(iso_date):
            await _send_text(
                sender_wa_id,
                (
                    "⚠️ Departure date cannot be more than 1 year in advance\n\n"
                    "Please enter a date within the next 12 months\n\n"
                    "_Example: 12 April 2026_"
                ),
                phone_number_id,
            )
            return
        old_dep_date = data.get("date", "")
        data["date"] = iso_date
        if data.pop("_edit_mode", False):
            existing_arr_date = data.get("arrive_date", "")
            if existing_arr_date and iso_date > existing_arr_date:
                data["date"] = old_dep_date
                try:
                    arr_date_fmt = datetime.strptime(existing_arr_date, "%Y-%m-%d").strftime("%d %B %Y")
                except ValueError:
                    arr_date_fmt = existing_arr_date
                await _send_text(
                    sender_wa_id,
                    (
                        f"⚠️ Departure date cannot be after your arrival date\n\n"
                        f"Your arrival is currently set to *{arr_date_fmt}* — "
                        f"please enter a departure date on or before that\n\n"
                        "_Example: 12 April 2026_"
                    ),
                    phone_number_id,
                )
                return
            await _show_trip_summary(sender_wa_id, data, flow, session, phone_number_id)
            return
        flow["step"] = "buy_cover_depart_time"
        await save_session(session)
        await _send_text(
            sender_wa_id,
            "*⏰ What time is your flight scheduled to depart?*\n\n_Example: 13:40 · 1:40 AM · 1:40 PM_",
            phone_number_id,
        )

    # ── Departure time ────────────────────────────────────────────────────────
    elif step == "buy_cover_depart_time":
        if reply_id == "dep_time_retry":
            await _send_text(
                sender_wa_id,
                "*⏰ Please enter your departure time*\n\n"
                "_Example: 13:40 · 1:40 AM · 3:30 PM_",
                phone_number_id,
            )
            return
        parsed_dep_time = _parse_time_to_hhmm(text or "")
        if not parsed_dep_time:
            await _send_text(
                sender_wa_id,
                (
                    "⏰ Please enter a valid departure time.\n\n"
                    "Use one of these formats:\n"
                    "• *13:40* — 24-hour format\n"
                    "• *1:40 AM* — morning (12-hour)\n"
                    "• *1:40 PM* — afternoon/evening (12-hour)"
                ),
                phone_number_id,
            )
            return
        # Same-day check: if editing dep_time and arr_time already set, validate order
        arr_time = data.get("arrive_time", "")
        dep_date_chk = data.get("date", "")
        arr_date_chk = data.get("arrive_date", dep_date_chk)
        if (
            arr_time
            and dep_date_chk
            and dep_date_chk == arr_date_chk
            and parsed_dep_time >= arr_time
        ):
            await _send_text(
                sender_wa_id,
                (
                    f"⚠️ Departure time must be *before* arrival time\n\n"
                    f"Your flight arrives at *{_fmt_time_display(arr_time)}* on the same day — "
                    f"please enter a departure time earlier than that\n\n"
                    "_Example: 13:40 · 1:40 AM · 1:40 PM_"
                ),
                phone_number_id,
            )
            return
        data["depart_time"] = parsed_dep_time
        # Track whether the user explicitly wrote "AM" so the arrival-time
        # sanity check can show a validation error instead of a PM suggestion.
        data["_dep_explicit_am"] = bool(re.search(r"\bam\b", (text or "").lower()))
        if data.pop("_repair_to_arrive_time", False):
            # Fixing dep_time after a duration error — all other data already
            # collected, so jump straight back to arrive_time validation.
            flow["step"] = "buy_cover_arrive_time"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                "*⏰ What time is your flight scheduled to arrive?*\n\n_Example: 15:00 · 3:00 AM · 3:00 PM_",
                phone_number_id,
            )
            return
        if data.pop("_edit_mode", False):
            await _show_trip_summary(sender_wa_id, data, flow, session, phone_number_id)
            return
        flow["step"] = "buy_cover_depart_airport_pick"
        await save_session(session)
        await _send_text(
            sender_wa_id,
            "*✈️ What airport are you flying from?*\n\nType at least 3 characters of the airport name or IATA code to search.\n\n_Example: LOS, ABV, KAD_",
            phone_number_id,
        )

    # ── Departure airport ─────────────────────────────────────────────────────
    elif step == "buy_cover_depart_airport_pick":
        if reply_id == "dep_search_again":
            await _send_text(
                sender_wa_id,
                "*✈️ What airport are you flying from?*\n\nType at least 3 characters of the airport name or IATA code to search.\n\n_Example: LOS, ABV, KAD_",
                phone_number_id,
            )
            return
        elif reply_id and reply_id.startswith("dep_"):
            parts = reply_id.replace("dep_", "", 1).split("|", 1)
            code = parts[0]
            name = parts[1] if len(parts) > 1 else code
            data["depart_airport"] = f"{code} — {name}"
        elif text and len(text.strip()) >= 3:
            search_term = text.strip()
            airports = await ipurvey_service.search_airports(
                search_term, country_code="NG"
            )
            if not airports:
                logger.info(f"[airport_dep] No results for '{search_term}', calling LLM to extract clean search term")
                llm_resp = await call_policy_flow_validate(
                    step_id=12,
                    context="Departure airport",
                    field_name="departure_airport",
                    question_asked="✈️ What airport are you flying from?\n\nType at least 3 characters of the airport name or IATA code to search.",
                    user_response=search_term,
                    step_type="airport",
                    expected_format="3+ alpha characters or IATA code",
                    validation_rules={"pattern": "alpha_only", "min_len": 3},
                )
                extracted = (llm_resp or {}).get("extracted_value", "")
                if llm_resp and llm_resp.get("is_valid") and extracted and len(extracted.strip()) >= 3:
                    logger.info(f"[airport_dep] LLM extracted '{extracted}', retrying airport search")
                    airports = await ipurvey_service.search_airports(
                        extracted.strip(), country_code="NG"
                    )
                if not airports:
                    await _send_buttons(
                        sender_wa_id,
                        (
                            f'❌ *No airports found matching "{search_term}"*\n\n'
                            "We couldn't find any Nigerian airport matching your entry.\n"
                            "Please check the spelling or try a different name or IATA code.\n\n"
                            "_Example: LOS, ABV, KAD, PHC_"
                        ),
                        [{"id": "dep_search_again", "title": "🔍 Search again"}],
                        phone_number_id,
                    )
                    return
            rows = [
                {
                    "id": f"dep_{a['code']}|{a['name']}",
                    "title": f"{a['code']}  {a['name']}"[:24],
                    "description": (a.get("country") or "")[:72],
                }
                for a in airports
            ]
            rows.append({"id": "dep_search_again", "title": "🔍 Search again"})
            await _send_list(
                sender_wa_id,
                "Found these matches! ✈️ Please select your airport below.\n\nNot on the list? Try searching again.",
                "Select airport",
                [{"title": "🛫 Departure Airports", "rows": rows}],
                phone_number_id,
            )
            return
        else:
            await _send_text(
                sender_wa_id,
                "*✈️ What airport are you flying from?*\n\nType at least 3 characters of the airport name or IATA code to search.\n\n_Example: LOS, ABV, KAD_",
                phone_number_id,
            )
            return
        if data.pop("_edit_mode", False):
            await _show_trip_summary(sender_wa_id, data, flow, session, phone_number_id)
            return
        flow["step"] = "buy_cover_arrive_date"
        await save_session(session)
        await _send_text(
            sender_wa_id,
            (
                "*📅 What date does your flight arrive?*\n\n"
                "_Example: 12 April 2026, 12/04/2026, 12-04-2026_"
            ),
            phone_number_id,
        )

    # ── Arrival date ──────────────────────────────────────────────────────────
    elif step == "buy_cover_arrive_date":
        iso_arr_date = _parse_date_to_iso(text or "")
        if not iso_arr_date and text:
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="arrival_date",
                question_asked="What is your arrival date? Please provide in DD/MM/YYYY format.",
                user_response=text,
                expected_format="date",
            )
            if (
                llm_result
                and llm_result.get("is_valid")
                and llm_result.get("extracted_value")
            ):
                iso_arr_date = _parse_date_to_iso(str(llm_result["extracted_value"]))
        if not iso_arr_date:
            await _send_text(
                sender_wa_id,
                (
                    "📅 Please enter the arrival date like this: *12 April 2026*\n\n"
                    "_Other accepted formats: 12/04/2026, 12-04-2026, 12-04-26_"
                ),
                phone_number_id,
            )
            return
        if _is_past_date(iso_arr_date):
            await _send_text(
                sender_wa_id,
                (
                    "⚠️ Arrival date cannot be in the past\n\n"
                    "Please enter today's date or a future arrival date\n\n"
                    "_Example: 12 April 2026_"
                ),
                phone_number_id,
            )
            return
        if _is_too_far_future(iso_arr_date):
            await _send_text(
                sender_wa_id,
                (
                    "⚠️ Arrival date cannot be more than 1 year in advance\n\n"
                    "Please enter a date within the next 12 months\n\n"
                    "_Example: 12 April 2026_"
                ),
                phone_number_id,
            )
            return
        dep_date = data.get("date", "")
        if dep_date and iso_arr_date < dep_date:
            try:
                dep_date_fmt = datetime.strptime(dep_date, "%Y-%m-%d").strftime(
                    "%d %B %Y"
                )
            except ValueError:
                dep_date_fmt = dep_date
            await _send_text(
                sender_wa_id,
                (
                    f"⚠️ Arrival date cannot be before your departure date\n\n"
                    f"Your flight departs on *{dep_date_fmt}* — "
                    f"please enter an arrival date on or after that\n\n"
                    "_Example: 12 April 2026_"
                ),
                phone_number_id,
            )
            return
        if dep_date:
            try:
                dep_dt = datetime.strptime(dep_date, "%Y-%m-%d")
                arr_dt = datetime.strptime(iso_arr_date, "%Y-%m-%d")
                if (arr_dt - dep_dt).days > 90:
                    dep_date_fmt = dep_dt.strftime("%d %B %Y")
                    await _send_text(
                        sender_wa_id,
                        (
                            f"⚠️ Arrival date seems too far from your departure date\n\n"
                            f"Your flight departs on *{dep_date_fmt}* — "
                            f"please enter an arrival date within 90 days of that\n\n"
                            "_Example: 12 April 2026_"
                        ),
                        phone_number_id,
                    )
                    return
            except ValueError:
                pass
        data["arrive_date"] = iso_arr_date
        if data.pop("_edit_mode", False):
            await _show_trip_summary(sender_wa_id, data, flow, session, phone_number_id)
            return
        flow["step"] = "buy_cover_arrive_time"
        await save_session(session)
        await _send_text(
            sender_wa_id,
            "*⏰ What time is your flight scheduled to arrive?*\n\n_Example: 15:00 · 3:00 AM · 3:00 PM_",
            phone_number_id,
        )

    # ── Arrival time ──────────────────────────────────────────────────────────
    elif step == "buy_cover_arrive_time":
        if reply_id == "arr_time_change_date":
            flow["step"] = "buy_cover_arrive_date"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                "*📅 What date does your flight arrive?*\n\n"
                "_Example: 12 April 2026, 12/04/2026, 12-04-2026_",
                phone_number_id,
            )
            return
        if reply_id == "arr_time_retry":
            await _send_text(
                sender_wa_id,
                "*⏰ What time is your flight scheduled to arrive?*\n\n_Example: 15:00 · 3:00 AM · 3:00 PM_",
                phone_number_id,
            )
            return
        if reply_id == "fix_dep_time":
            data.pop("depart_time", None)
            data["_repair_to_arrive_time"] = True
            flow["step"] = "buy_cover_depart_time"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                "*⏰ Please enter your corrected departure time*\n\n"
                "_Example: 13:40 · 1:40 PM · 3:30 PM_",
                phone_number_id,
            )
            return
        if reply_id == "fix_arr_date":
            flow["step"] = "buy_cover_arrive_date"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                "*📅 Please enter your corrected arrival date*\n\n"
                "_Example: 12 April 2026, 12/04/2026, 12-04-2026_",
                phone_number_id,
            )
            return
        parsed_arr_time = _parse_time_to_hhmm(text or "")
        if not parsed_arr_time:
            await _send_text(
                sender_wa_id,
                (
                    "⏰ Please enter a valid arrival time.\n\n"
                    "Use one of these formats:\n"
                    "• *15:00* — 24-hour format\n"
                    "• *3:00 AM* — morning (12-hour)\n"
                    "• *3:00 PM* — afternoon/evening (12-hour)"
                ),
                phone_number_id,
            )
            return
        dep_time = data.get("depart_time", "")
        dep_date = data.get("date", "")
        arr_date = data.get("arrive_date", dep_date)
        if arr_date == dep_date and dep_time and parsed_arr_time <= dep_time:
            await _send_buttons(
                sender_wa_id,
                (
                    f"⚠️ *Arrival time must be after departure time on the same day*\n\n"
                    f"Your flight departs at *{_fmt_time_display(dep_time)}* — "
                    f"please enter an arrival time later than that, or if this is an "
                    f"overnight flight tap *Change Arrival Date* to set the correct date first.\n\n"
                    "_Example: 15:00 · 3:00 AM · 3:00 PM_"
                ),
                [
                    {"id": "arr_time_change_date", "title": "🗓️ Change Arrival Date"},
                    {"id": "arr_time_retry", "title": "⏰ Enter Time Again"},
                ],
                phone_number_id,
            )
            return
        data["arrive_time"] = parsed_arr_time

        # ── AM/PM sanity check ───────────────────────────────────────────────
        # If departure is AM and the same-day flight would be > 6 h, the user
        # may have typed 24-h time when they meant PM.  Offer a quick confirm.
        if arr_date == dep_date and dep_time:
            try:
                dep_h, dep_m = map(int, dep_time.split(":"))
                arr_h, arr_m = map(int, parsed_arr_time.split(":"))
                duration_mins = (arr_h * 60 + arr_m) - (dep_h * 60 + dep_m)
                pm_dep_h = dep_h + 12
                pm_total = pm_dep_h * 60 + dep_m
                arr_total = arr_h * 60 + arr_m
                explicit_am = data.pop("_dep_explicit_am", False)
                if dep_h < 12 and duration_mins > 360 and pm_total < arr_total:
                    dur_h, dur_m = divmod(duration_mins, 60)
                    dur_str = f"{dur_h}h {dur_m}m" if dur_m else f"{dur_h}h"
                    if explicit_am:
                        # User wrote "AM" explicitly — trust them but flag the
                        # duration as unrealistic and send them back to fix it.
                        data.pop("depart_time", None)
                        data["_repair_to_arrive_time"] = True
                        flow["step"] = "buy_cover_depart_time"
                        await save_session(session)
                        await _send_buttons(
                            sender_wa_id,
                            (
                                f"⚠️ *Departure time issue*\n\n"
                                f"You entered departs *{_fmt_time_display(dep_time)}* → arrives "
                                f"*{_fmt_time_display(parsed_arr_time)}* on the same day — "
                                f"that's *{dur_str}*.\n\n"
                                f"Nigerian domestic flights don't exceed 6 hours. "
                                f"Please check your departure time and enter it again."
                            ),
                            [{"id": "dep_time_retry", "title": "⏰ Re-enter departure time"}],
                            phone_number_id,
                        )
                        return
                    else:
                        # Ambiguous input (no AM/PM suffix) — offer PM alternative.
                        pm_dep = f"{pm_dep_h:02d}:{dep_m:02d}"
                        data["_dep_pm_alt"] = pm_dep
                        flow["step"] = "buy_cover_depart_ampm_confirm"
                        await save_session(session)
                        await _send_buttons(
                            sender_wa_id,
                            (
                                f"⏰ *Departure time check*\n\n"
                                f"You entered departs *{_fmt_time_display(dep_time)}* → arrives "
                                f"*{_fmt_time_display(parsed_arr_time)}* on the same day — "
                                f"that's *{dur_str}*.\n\n"
                                f"Did you mean *{_fmt_time_display(pm_dep)}* (PM) for departure?\n\n"
                                f"_If yes, your trip would be "
                                f"{(arr_total - pm_total) // 60}h {(arr_total - pm_total) % 60}m._"
                            ),
                            [
                                {"id": "ampm_yes", "title": f"✅ Yes, {_fmt_time_display(pm_dep)}"},
                                {"id": "ampm_no",  "title": f"No, keep {_fmt_time_display(dep_time)}"},
                            ],
                            phone_number_id,
                        )
                        return
            except (ValueError, TypeError):
                pass

        # ── Cross-day duration check ──────────────────────────────────────────
        # Catches cases like dep 24-May 03:30 → arr 25-May 16:30 (37 h) that
        # slip past the same-day guard above because the dates differ.
        elif arr_date != dep_date and dep_date and dep_time:
            try:
                dep_dt = datetime.strptime(f"{dep_date} {dep_time}", "%Y-%m-%d %H:%M")
                arr_dt = datetime.strptime(f"{arr_date} {parsed_arr_time}", "%Y-%m-%d %H:%M")
                total_mins = int((arr_dt - dep_dt).total_seconds() / 60)
                explicit_am = data.pop("_dep_explicit_am", False)
                if total_mins > 360:
                    dep_h, dep_m = map(int, dep_time.split(":"))
                    dur_h, dur_m = divmod(total_mins, 60)
                    dur_str = f"{dur_h}h {dur_m}m" if dur_m else f"{dur_h}h"
                    # Check if switching departure to PM would bring it within limit
                    pm_dep_h = dep_h + 12
                    pm_fixes = False
                    pm_dep = None
                    pm_total_mins = None
                    if not explicit_am and dep_h < 12 and pm_dep_h < 24:
                        pm_dt = dep_dt.replace(hour=pm_dep_h)
                        pm_total_mins = int((arr_dt - pm_dt).total_seconds() / 60)
                        if 0 < pm_total_mins <= 360:
                            pm_fixes = True
                            pm_dep = f"{pm_dep_h:02d}:{dep_m:02d}"
                    if pm_fixes and pm_dep:
                        data["_dep_pm_alt"] = pm_dep
                        flow["step"] = "buy_cover_depart_ampm_confirm"
                        await save_session(session)
                        await _send_buttons(
                            sender_wa_id,
                            (
                                f"⏰ *Departure time check*\n\n"
                                f"You entered departs *{_fmt_time_display(dep_time)}* → arrives "
                                f"*{_fmt_time_display(parsed_arr_time)}* — "
                                f"that's *{dur_str}*.\n\n"
                                f"Did you mean *{_fmt_time_display(pm_dep)}* (PM) for departure?\n\n"
                                f"_If yes, your trip would be "
                                f"{pm_total_mins // 60}h {pm_total_mins % 60}m._"
                            ),
                            [
                                {"id": "ampm_yes", "title": f"✅ Yes, {_fmt_time_display(pm_dep)}"},
                                {"id": "ampm_no",  "title": f"No, keep {_fmt_time_display(dep_time)}"},
                            ],
                            phone_number_id,
                        )
                        return
                    else:
                        # PM doesn't fix it — offer user a choice of what to correct.
                        # Keep dep_time in data; stay on arrive_time step so the
                        # fix_dep_time / fix_arr_date buttons are handled here.
                        flow["step"] = "buy_cover_arrive_time"
                        await save_session(session)
                        await _send_buttons(
                            sender_wa_id,
                            (
                                f"⚠️ *Flight duration too long*\n\n"
                                f"You entered departs *{_fmt_time_display(dep_time)}* → arrives "
                                f"*{_fmt_time_display(parsed_arr_time)}* — "
                                f"that's *{dur_str}*.\n\n"
                                f"Nigerian domestic flights don't exceed 6 hours. "
                                f"What would you like to fix?"
                            ),
                            [
                                {"id": "fix_dep_time", "title": "⏰ Fix departure time"},
                                {"id": "fix_arr_date", "title": "📅 Fix arrival date"},
                            ],
                            phone_number_id,
                        )
                        return
            except (ValueError, TypeError):
                pass
        else:
            data.pop("_dep_explicit_am", None)
        # ── end duration checks ───────────────────────────────────────────────

        if data.pop("_edit_mode", False):
            await _show_trip_summary(sender_wa_id, data, flow, session, phone_number_id)
            return
        flow["step"] = "buy_cover_arrive_airport_pick"
        await save_session(session)
        await _send_text(
            sender_wa_id,
            "*✈️ What airport are you arriving at?*\n\nType at least 3 characters of the airport name or IATA code to search.\n\n_Example: LOS, Lagos, ABV, Abuja_",
            phone_number_id,
        )

    # ── Departure AM/PM confirmation ──────────────────────────────────────────
    elif step == "buy_cover_depart_ampm_confirm":
        pm_alt = data.pop("_dep_pm_alt", "")
        edit_mode = data.pop("_edit_mode", False)

        if reply_id == "ampm_yes" and pm_alt:
            data["depart_time"] = pm_alt
        elif reply_id == "ampm_no":
            pass  # keep departure time as-is
        else:
            # User typed something unexpected — send them back to re-enter dep time
            data.pop("depart_time", None)
            flow["step"] = "buy_cover_depart_time"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                "*⏰ Please re-enter your departure time*\n\n"
                "_Example: 13:40 · 1:40 AM · 3:30 PM_",
                phone_number_id,
            )
            return

        if edit_mode:
            await _show_trip_summary(sender_wa_id, data, flow, session, phone_number_id)
            return
        flow["step"] = "buy_cover_arrive_airport_pick"
        await save_session(session)
        await _send_text(
            sender_wa_id,
            "*✈️ What airport are you arriving at?*\n\nType at least 3 characters of the airport name or IATA code to search.\n\n_Example: LOS, Lagos, ABV, Abuja_",
            phone_number_id,
        )

    # ── Arrival airport ───────────────────────────────────────────────────────
    elif step == "buy_cover_arrive_airport_pick":
        if reply_id == "arr_search_again":
            await _send_text(
                sender_wa_id,
                "*✈️ What airport are you arriving at?*\n\nType at least 3 characters of the airport name or IATA code to search.\n\n_Example: ABV, LOS, KAN_",
                phone_number_id,
            )
            return
        elif reply_id and reply_id.startswith("arr_"):
            parts = reply_id.replace("arr_", "", 1).split("|", 1)
            code = parts[0]
            name = parts[1] if len(parts) > 1 else code
            dep_code = data.get("depart_airport", "").split("—")[0].strip().split()[0]
            if dep_code and code.upper() == dep_code.upper():
                await _send_buttons(
                    sender_wa_id,
                    (
                        f"⚠️ *Invalid route*\n\n"
                        f"Your departure and arrival airport are both *{code}*. "
                        f"Please select a different arrival airport."
                    ),
                    [{"id": "arr_search_again", "title": "🔍 Search again"}],
                    phone_number_id,
                )
                return
            data["arrive_airport"] = f"{code} — {name}"
        elif text and len(text.strip()) >= 3:
            search_term = text.strip()
            airports = await ipurvey_service.search_airports(
                search_term, country_code="NG"
            )
            if not airports:
                logger.info(f"[airport_arr] No results for '{search_term}', calling LLM to extract clean search term")
                llm_resp = await call_policy_flow_validate(
                    step_id=15,
                    context="Arrival airport",
                    field_name="arrival_airport",
                    question_asked="✈️ What airport are you arriving at?\n\nType at least 3 characters of the airport name or IATA code to search.",
                    user_response=search_term,
                    step_type="airport",
                    expected_format="3+ alpha characters or IATA code",
                    validation_rules={"pattern": "alpha_only", "min_len": 3},
                )
                extracted = (llm_resp or {}).get("extracted_value", "")
                if llm_resp and llm_resp.get("is_valid") and extracted and len(extracted.strip()) >= 3:
                    logger.info(f"[airport_arr] LLM extracted '{extracted}', retrying airport search")
                    airports = await ipurvey_service.search_airports(
                        extracted.strip(), country_code="NG"
                    )
                if not airports:
                    await _send_buttons(
                        sender_wa_id,
                        (
                            f'❌ *No airports found matching "{search_term}"*\n\n'
                            "We couldn't find any Nigerian airport matching your entry.\n"
                            "Please check the spelling or try a different name or IATA code.\n\n"
                            "_Example: ABV, LOS, KAN, PHC_"
                        ),
                        [{"id": "arr_search_again", "title": "🔍 Search again"}],
                        phone_number_id,
                    )
                    return
            rows = [
                {
                    "id": f"arr_{a['code']}|{a['name']}",
                    "title": f"{a['code']}  {a['name']}"[:24],
                    "description": (a.get("country") or "")[:72],
                }
                for a in airports
            ]
            rows.append({"id": "arr_search_again", "title": "🔍 Search again"})
            await _send_list(
                sender_wa_id,
                "Found these matches! ✈️ Please select your airport below.\n\nNot on the list? Try searching again.",
                "Select airport",
                [{"title": "🛬 Arrival Airports", "rows": rows}],
                phone_number_id,
            )
            return
        else:
            await _send_text(
                sender_wa_id,
                "*✈️ What airport are you arriving at?*\n\nType at least 3 characters of the airport name or IATA code to search.\n\n_Example: ABV, LOS, KAN_",
                phone_number_id,
            )
            return
        if data.pop("_edit_mode", False):
            await _show_trip_summary(sender_wa_id, data, flow, session, phone_number_id)
            return
        flow["step"] = "buy_cover_airline"
        await save_session(session)
        await _send_text(
            sender_wa_id,
            "*✈️  Who are you flying with?*\n\n_Example: Ibom Air, Air Peace_",
            phone_number_id,
        )

    # ── Airline ───────────────────────────────────────────────────────────────
    elif step == "buy_cover_airline":
        if not text:
            await _send_text(
                sender_wa_id,
                "Please enter the airline name to continue.",
                phone_number_id,
            )
            return
        llm_airline = await call_extract(
            user_id=sender_wa_id,
            field_name="airline_name",
            question_asked="Who are you flying with? Please enter the airline name.",
            user_response=text,
            expected_format="text",
        )
        if (
            llm_airline
            and llm_airline.get("is_valid")
            and llm_airline.get("extracted_value")
        ):
            data["airline"] = str(llm_airline["extracted_value"])
        else:
            data["airline"] = text
        await _show_trip_summary(sender_wa_id, data, flow, session, phone_number_id)

    # ── Edit field select ──────────────────────────────────────────────────────
    elif step == "buy_cover_edit_select":
        _EDIT_MAP = {
            "edit_name": "buy_cover_name",
            "edit_email": "buy_cover_email",
            "edit_booking_ref": "buy_cover_booking_ref",
            "edit_flight_num": "buy_cover_flight_num",
            "edit_date": "buy_cover_date",
            "edit_arrive_date": "buy_cover_arrive_date",
            "edit_depart_time": "buy_cover_depart_time",
            "edit_depart_airport": "buy_cover_depart_airport_pick",
            "edit_arrive_time": "buy_cover_arrive_time",
            "edit_arrive_airport": "buy_cover_arrive_airport_pick",
            "edit_airline": "buy_cover_airline",
        }
        if reply_id == "edit_more_fields":
            flow["step"] = "buy_cover_edit_select"
            await save_session(session)
            await _send_edit_menu(sender_wa_id, phone_number_id, page=2)
            return
        if reply_id == "edit_prev_fields":
            flow["step"] = "buy_cover_edit_select"
            await save_session(session)
            await _send_edit_menu(sender_wa_id, phone_number_id, page=1)
            return
        target = _EDIT_MAP.get(reply_id or "")
        if not target:
            flow["step"] = "buy_cover_edit_select"
            await save_session(session)
            await _send_edit_menu(sender_wa_id, phone_number_id)
            return
        data["_edit_mode"] = True
        flow["step"] = target
        await save_session(session)
        _EDIT_PROMPT = {
            "buy_cover_name": "*👤 Please enter your updated name*\n"
            "Enter first name and surname as it appears on your ticket\n\n"
            "_Example: Yusuf Abdullahi_",
            "buy_cover_email": "*📧 Please enter your updated email address*\n\n"
            "_Example: yusuf@email.com_",
            "buy_cover_booking_ref": "*🎫 Please enter your updated booking reference*\n\n"
            "_Examples: AB1XY2, 2990FA62_",
            "buy_cover_flight_num": "*✈️ Please enter your updated flight number*\n\n"
            "_Example: P47123, Q1402_",
            "buy_cover_date": "*📅 Please enter your updated departure date*\n\n"
            "_Example: 12 April 2026, 12/04/2026_",
            "buy_cover_arrive_date": "*📅 Please enter your updated arrival date*\n\n"
            "_Example: 12 April 2026, 12/04/2026_",
            "buy_cover_depart_time": "*⏰ Please enter your updated departure time*\n\n"
            "_Example: 13:40 · 1:40 AM · 1:40 PM_",
            "buy_cover_depart_airport_pick": "*🛫 Type at least 3 characters to search for your departure airport*\n\n"
            "_Example: LOS, ABV, KAD_",
            "buy_cover_arrive_time": "*⏰ Please enter your updated arrival time*\n\n"
            "_Example: 15:00 · 3:00 AM · 3:00 PM_",
            "buy_cover_arrive_airport_pick": "*🛬 Type at least 3 characters to search for your arrival airport*\n\n"
            "_Example: ABV, LOS, KAN_",
            "buy_cover_airline": "*✈️ Please enter your updated airline name*\n\n"
            "_Example: Ibom Air, Air Peace_",
        }
        await _send_text(
            sender_wa_id,
            _EDIT_PROMPT.get(target, "Please enter the updated value:"),
            phone_number_id,
        )

    # ── Trip summary ──────────────────────────────────────────────────────────
    elif step == "buy_cover_summary":
        if not reply_id and text:
            _t = text.strip().lower()
            if _t in (
                "1",
                "yes",
                "confirm",
                "ok",
                "correct",
                "proceed",
                "submit",
                "looks good",
                "continue",
            ):
                reply_id = "summary_confirm"
            elif _t in (
                "2",
                "no",
                "edit",
                "change",
                "wrong",
                "incorrect",
                "update",
                "modify",
            ):
                reply_id = "summary_edit"
        if not reply_id and text:
            llm_result = await call_extract(
                user_id=sender_wa_id,
                field_name="trip_summary_action",
                question_asked="Would you like to confirm your trip details or edit them?",
                user_response=text,
                expected_format="text",
            )
            if (
                llm_result
                and llm_result.get("is_valid")
                and llm_result.get("extracted_value")
            ):
                ev = str(llm_result["extracted_value"]).lower()
                if any(
                    k in ev
                    for k in (
                        "confirm",
                        "yes",
                        "proceed",
                        "correct",
                        "ok",
                        "looks good",
                        "continue",
                        "submit",
                    )
                ):
                    reply_id = "summary_confirm"
                elif any(
                    k in ev
                    for k in (
                        "edit",
                        "change",
                        "no",
                        "wrong",
                        "incorrect",
                        "update",
                        "modify",
                    )
                ):
                    reply_id = "summary_edit"

        if reply_id == "summary_edit":
            # Block editing once itinerary has been submitted to the API — editing
            # trip fields after submission causes an API error on re-submission.
            if data.get("itinerary_submitted"):
                await _send_buttons(
                    sender_wa_id,
                    "⚠️ *Trip details cannot be edited*\n\n"
                    "Your flight details have already been submitted.\n"
                    "Please continue to select your cover plan.",
                    [{"id": "summary_confirm", "title": "▶️ Continue to covers"}],
                    phone_number_id,
                )
                return
            flow["step"] = "buy_cover_edit_select"
            await save_session(session)
            await _send_edit_menu(sender_wa_id, phone_number_id)
            return

        if reply_id == "edit_booking_ref":
            data["_edit_mode"] = True
            flow["step"] = "buy_cover_booking_ref"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                "*🎫 Please enter your updated booking reference*\n\n"
                "_Examples: AB1XY2, 2990FA62_",
                phone_number_id,
            )
            return

        if reply_id != "summary_confirm":
            await _show_trip_summary(sender_wa_id, data, flow, session, phone_number_id)
            return

        policy_id = session.get("api_data", {}).get("policy_id")
        quotes = None
        if policy_id:
            try:
                # If itinerary already submitted, skip re-submission to avoid API error
                if data.get("itinerary_submitted"):
                    quotes = await ipurvey_service.fetch_quotes(policy_id)
                else:
                    dep_code = (
                        data.get("depart_airport", "").split("—")[0].strip().split()[0]
                        if data.get("depart_airport")
                        else ""
                    )
                    arr_code = (
                        data.get("arrive_airport", "").split("—")[0].strip().split()[0]
                        if data.get("arrive_airport")
                        else ""
                    )
                    dep_date = data.get(
                        "date", ""
                    )  # already ISO YYYY-MM-DD from validation
                    dep_time = data.get("depart_time", "")  # already HH:MM from validation
                    arr_time = data.get("arrive_time", "")  # already HH:MM from validation
                    flight_num = data.get("flight_num", "").upper().replace(" ", "")
                    carrier = flight_num[:2] if len(flight_num) >= 2 else flight_num
                    trip_raw = data.get("trip_type", "One-way 🗺️")
                    trip_type = "RETURN" if "return" in trip_raw.lower() else "ONE_WAY"
                    arr_date = data.get("arrive_date") or dep_date
                    flight_id = f"{flight_num}-{dep_date}T{dep_time}"
                    session.setdefault("api_data", {})["flight_id"] = flight_id
                    legs = [
                        {
                            "flightNumber": flight_num,
                            "carrier": carrier,
                            "departureAirport": dep_code,
                            "arrivalAirport": arr_code,
                            "departureDate": dep_date,
                            "departureTime": dep_time,
                            "arrivalDate": arr_date,
                            "arrivalTime": arr_time,
                        }
                    ]
                    itinerary_ok, iti_err = await ipurvey_service.submit_itinerary(
                        policy_id, trip_type, data.get("booking_ref", ""), legs
                    )
                    if not itinerary_ok:
                        flow["step"] = "buy_cover_summary"
                        await save_session(session)
                        if iti_err and "already exists with booking reference" in iti_err:
                            booking_ref = data.get("booking_ref", "")
                            await _send_buttons(
                                sender_wa_id,
                                (
                                    f"⚠️ *Booking Reference Already in Use*\n\n"
                                    f"The booking reference *{booking_ref}* is already linked to an active policy.\n\n"
                                    "Please enter a different booking reference."
                                ),
                                [
                                    {
                                        "id": "edit_booking_ref",
                                        "title": "✏️ Change Booking Ref",
                                    },
                                    {
                                        "id": "summary_edit",
                                        "title": "📝 Edit other details",
                                    },
                                ],
                                phone_number_id,
                            )
                        else:
                            err_line = f"\n\n_{iti_err}_" if iti_err else ""
                            await _send_buttons(
                                sender_wa_id,
                                (
                                    "⚠️ *We couldn't submit your trip details*"
                                    f"{err_line}\n\n"
                                    "Please check your flight details and try again, or edit them if something is incorrect."
                                ),
                                [
                                    {"id": "summary_confirm", "title": "🔄 Try again"},
                                    {"id": "summary_edit", "title": "✏️ Edit details"},
                                ],
                                phone_number_id,
                            )
                        return
                    # Mark itinerary as submitted so we never call submit_itinerary again
                    # for this policy (re-calling it causes an API error).
                    data["itinerary_submitted"] = True
                    await save_session(session)
                    quotes = await ipurvey_service.fetch_quotes(policy_id)
            except Exception as exc:
                logger.error(f"[buy_cover] itinerary/quotes API failed: {exc}")
                flow["step"] = "buy_cover_summary"
                await save_session(session)
                await _send_buttons(
                    sender_wa_id,
                    (
                        "⚠️ *We're unable to complete that right now*\n\n"
                        "Please try again shortly"
                    ),
                    [
                        {"id": "summary_confirm", "title": "🔄 Try again"},
                        {"id": "summary_edit", "title": "✏️ Edit details"},
                    ],
                    phone_number_id,
                )
                return

        if not quotes:
            flow["step"] = "buy_cover_summary"
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                (
                    "⚠️ *We're unable to load covers right now*\n\n"
                    "Please try again shortly"
                ),
                [
                    {"id": "summary_confirm", "title": "🔄 Try again"},
                    {"id": "summary_edit", "title": "✏️ Edit details"},
                ],
                phone_number_id,
            )
            return

        session.setdefault("api_data", {})["quotes"] = quotes
        flow["step"] = "buy_cover_select_cover"
        await save_session(session)
        await _send_cover_page(sender_wa_id, quotes, 0, phone_number_id)

    # ── Select cover (from real quotes) ───────────────────────────────────────
    elif step == "buy_cover_select_cover":
        quotes = session.get("api_data", {}).get("quotes") or []

        if reply_id and reply_id.startswith("more_covers_"):
            try:
                page = int(reply_id.split("_")[-1])
            except ValueError:
                page = 0
            await _send_cover_page(sender_wa_id, quotes, page, phone_number_id)
            return

        selected_q = None
        if reply_id and reply_id.startswith("cov_"):
            try:
                idx = int(reply_id.split("_")[1])
                if 0 <= idx < len(quotes):
                    selected_q = quotes[idx]
                    prod_id = selected_q.get("productId") or selected_q.get("id") or ""
                    q_name = str(
                        selected_q.get("name")
                        or selected_q.get("productName")
                        or "Selected cover"
                    )
                    q_price = (
                        selected_q.get("price") or selected_q.get("premiumAmount") or 0
                    )
                    data["cover"] = q_name
                    data["cover_price"] = q_price
                    data["selected_quote"] = selected_q
                    policy_id = session.get("api_data", {}).get("policy_id")
                    if policy_id and prod_id:
                        try:
                            policy_code = await ipurvey_service.select_cover(
                                policy_id, prod_id
                            )
                            if policy_code:
                                session.setdefault("api_data", {})["policy_code"] = (
                                    policy_code
                                )
                                logger.info(
                                    f"[buy_cover] saved policyCode='{policy_code}'"
                                )
                            else:
                                logger.error(
                                    f"[buy_cover] select_cover returned no policyCode for productId='{prod_id}'"
                                )
                                await _send_buttons(
                                    sender_wa_id,
                                    (
                                        "⚠️ *We couldn't confirm your cover selection*\n\n"
                                        "Please try selecting it again"
                                    ),
                                    [{"id": f"cov_{idx}", "title": "🔄 Try again"}],
                                    phone_number_id,
                                )
                                return
                        except Exception as exc:
                            logger.error(f"[buy_cover] select_cover API failed: {exc}")
                            await _send_buttons(
                                sender_wa_id,
                                (
                                    "⚠️ *We're unable to complete that right now*\n\n"
                                    "Please try again shortly"
                                ),
                                [{"id": f"cov_{idx}", "title": "🔄 Try again"}],
                                phone_number_id,
                            )
                            return
            except (ValueError, IndexError):
                pass
        if not selected_q:
            quotes = session.get("api_data", {}).get("quotes") or []
            if quotes:
                await _send_cover_page(
                    sender_wa_id,
                    quotes,
                    0,
                    phone_number_id,
                    intro_body=(
                        "❌ *Invalid selection*\n\n"
                        "Please tap *Select cover* below to pick your cover from the list.\n\n"
                        "_You can also use:  0 Back  •  9 Help  •  99 Cancel_"
                    ),
                )
            else:
                await _send_buttons(
                    sender_wa_id,
                    "⚠️ *No covers available*\n\nPlease try again shortly",
                    [{"id": "summary_confirm", "title": "🔄 Try again"}],
                    phone_number_id,
                )
            return
        flow["step"] = "buy_cover_next_steps"
        flow["active"] = True
        await save_session(session)
        await _send_buttons(
            sender_wa_id,
            _build_cover_card_body(data),
            [
                {"id": "next_kyc",   "title": "🛒 Buy Cover"},
                {"id": "next_terms", "title": "📄 View Policy Terms"},
                {"id": "next_ask",   "title": "❓ Ask a Question"},
            ],
            phone_number_id,
        )

    # ── Next steps ────────────────────────────────────────────────────────────
    elif step == "buy_cover_next_steps":
        if not reply_id and text:
            _t = text.strip().lower()
            if _t in (
                "1",
                "kyc",
                "continue",
                "proceed",
                "yes",
                "ok",
                "go ahead",
                "next",
            ):
                reply_id = "next_kyc"
            elif _t in ("2", "terms", "policy terms", "view terms", "view policy"):
                reply_id = "next_terms"
            elif _t in ("3", "ask", "question", "enquire", "more info", "know"):
                reply_id = "next_ask"
            elif _t in ("4", "cancel", "exit", "no", "stop", "quit"):
                reply_id = "next_cancel"
        if not reply_id and text:
            llm_next = await call_extract(
                user_id=sender_wa_id,
                field_name="next_action",
                question_asked="What would you like to do? Buy Cover, View Policy Terms, Ask a question, or Cancel purchase?",
                user_response=text,
                expected_format="text",
            )
            if (
                llm_next
                and llm_next.get("is_valid")
                and llm_next.get("extracted_value")
            ):
                ev = str(llm_next["extracted_value"]).lower()
                if any(
                    k in ev
                    for k in (
                        "kyc",
                        "continue",
                        "proceed",
                        "next",
                        "yes",
                        "ok",
                        "go ahead",
                    )
                ):
                    reply_id = "next_kyc"
                elif any(k in ev for k in ("terms", "policy terms", "view")):
                    reply_id = "next_terms"
                elif any(
                    k in ev for k in ("ask", "question", "know", "enquire", "more info")
                ):
                    reply_id = "next_ask"
                elif any(k in ev for k in ("cancel", "stop", "exit", "no")):
                    reply_id = "next_cancel"

        if reply_id == "next_kyc":
            from app.services.kyc_flow_service import start_kyc_flow

            await start_kyc_flow(wa_id=sender_wa_id, phone_number_id=phone_number_id, from_buy_cover=True)
        elif reply_id == "next_terms":
            await _send_text(
                sender_wa_id,
                (
                    "📄 *Policy Terms Summary*\n\n"
                    "This TravelAssist policy covers travel disruption events including delays and cancellations.\n\n"
                    "• Coverage begins from policy activation date\n"
                    "• Waiting periods and exclusions apply — see full terms\n"
                    "• Payout is automatic — no claim forms needed\n"
                    "• For the full policy document, visit *www.ipurvey.com*\n\n"
                    "_Please review the complete terms before proceeding to payment._"
                ),
                phone_number_id,
            )
            await _send_buttons(
                sender_wa_id,
                "What would you like to do next?",
                [
                    {"id": "next_kyc", "title": "🛒 Buy Policy"},
                    {"id": "next_ask", "title": "❓ Ask a Question"},
                    {"id": "next_cancel", "title": "❌ Cancel"},
                ],
                phone_number_id,
            )
        elif reply_id == "next_ask":
            flow["step"] = "buy_cover_ask_question"
            await save_session(session)
            await _send_text(
                sender_wa_id,
                (
                    "❓ *What would you like to know?*\n\n"
                    "💬 *Common questions:*\n"
                    "🛡️ What does the cover include?\n"
                    "⏰ How long does a delay need to be?\n"
                    "💰 How and when will I get paid out?\n"
                    "📋 Can I get a refund on my policy?\n\n"
                    "_Type your question here..._"
                ),
                phone_number_id,
            )
        elif reply_id == "next_cancel":
            await show_cancel_purchase_confirm(sender_wa_id, phone_number_id)
        else:
            await _send_buttons(
                sender_wa_id,
                "What would you like to do next?",
                [
                    {"id": "next_kyc",   "title": "🛒 Buy Cover"},
                    {"id": "next_terms", "title": "📄 View Policy Terms"},
                    {"id": "next_ask",   "title": "❓ Ask a Question"},
                ],
                phone_number_id,
            )

    # ── Ask a question ────────────────────────────────────────────────────────
    elif step == "buy_cover_ask_question":
        q = text.lower()
        if "delay" in q or "hours" in q or "long" in q:
            answer = (
                "💬 *Great question!*\n\nFor Local Travel Basic and Premium, a payout is triggered "
                "when your flight is delayed by *3 hours or more* from the scheduled departure time.\n\n"
                "TravelAssist monitors your flight automatically.\n\nReady to continue?"
            )
        elif "include" in q or "cover" in q:
            answer = (
                "💬 *Great question!*\n\nYour cover includes:\n"
                "✅ Major flight delay (3+ hours)\n✅ Flight cancellation\n"
                "✅ Covered travel disruption\n\nReady to continue?"
            )
        elif "refund" in q:
            answer = (
                "💬 *Great question!*\n\nYou can request a refund within 14 days of purchase "
                "if your flight has not departed.\n\nReady to continue?"
            )
        elif "paid" in q or "payout" in q or "when" in q:
            answer = (
                "💬 *Great question!*\n\nPayouts are processed automatically within 24–48 hours "
                "of a confirmed disruption. No claim forms needed.\n\nReady to continue?"
            )
        else:
            answer = (
                "💬 *Great question!*\n\nFor detailed policy information, please visit *www.ipurvey.com*\n\n"
                "Ready to continue?"
            )
        flow["step"] = "buy_cover_next_steps"
        flow["active"] = True
        await save_session(session)
        await _send_buttons(
            sender_wa_id,
            answer,
            [
                {"id": "next_kyc", "title": "🛒 Buy Policy"},
                {"id": "next_ask", "title": "❓ Ask another"},
                {"id": "next_cancel", "title": "❌ Cancel"},
            ],
            phone_number_id,
        )

    # ── Cancel confirm ────────────────────────────────────────────────────────
    elif step == "buy_cover_cancel_confirm":
        if reply_id == "cancel_yes":
            await _reset(session, sender_wa_id)
            await _send_buttons(
                sender_wa_id,
                "✅ *Purchase cancelled*\n\nNo worries — you can come back anytime to protect your trip.",
                [
                    {"id": "restart_buy", "title": "1. Start new cover"},
                    {"id": "go_main", "title": "2. Main menu"},
                ],
                phone_number_id,
            )
        else:
            flow["step"] = "buy_cover_next_steps"
            flow["active"] = True
            await save_session(session)
            await _send_buttons(
                sender_wa_id,
                "What would you like to do next?",
                [
                    {"id": "next_kyc", "title": "🛒 Buy Policy"},
                    {"id": "next_ask", "title": "❓ Ask a question"},
                    {"id": "next_cancel", "title": "❌ Cancel"},
                ],
                phone_number_id,
            )


async def go_back_one_step(wa_id: str, phone_number_id: Optional[str]):
    """Go back exactly one step in the buy cover flow instead of restarting."""
    session, flow = await _get_flow_state(wa_id)
    step = flow.get("step", "buy_cover_who")
    data = flow.get("data", {})

    _PREV: dict[str, Optional[str]] = {
        "buy_cover_name": "buy_cover_who",
        "buy_cover_traveler_count": "buy_cover_who",
        "buy_cover_tc_9_confirm": "buy_cover_traveler_count",
        "buy_cover_other_name": "buy_cover_name",
        "buy_cover_email": None,
        "buy_cover_trip_type": "buy_cover_email",
        "buy_cover_booking_ref": "buy_cover_trip_type",
        "buy_cover_flight_num": "buy_cover_booking_ref",
        "buy_cover_date": "buy_cover_flight_num",
        "buy_cover_depart_time": "buy_cover_date",
        "buy_cover_depart_airport_pick": "buy_cover_depart_time",
        "buy_cover_arrive_date": "buy_cover_depart_airport_pick",
        "buy_cover_arrive_time": "buy_cover_arrive_date",
        "buy_cover_depart_ampm_confirm": "buy_cover_depart_time",
        "buy_cover_arrive_airport_pick": "buy_cover_arrive_time",
        "buy_cover_airline": "buy_cover_arrive_airport_pick",
        "buy_cover_edit_select": "buy_cover_summary",
        "buy_cover_summary": "buy_cover_airline",
        "buy_cover_select_cover": "buy_cover_summary",
        "buy_cover_next_steps": "buy_cover_select_cover",
        "buy_cover_cancel_confirm": "buy_cover_next_steps",
    }
    # clear any lingering edit_mode flag when navigating back
    data.pop("_edit_mode", None)

    if step == "buy_cover_email":
        prev: Optional[str] = (
            "buy_cover_other_name"
            if data.get("who") == "me_and_others"
            else "buy_cover_name"
        )
    else:
        prev = _PREV.get(step)

    # buy_cover_next_steps is a terminal state (cover already selected).
    # Pressing "0" there should exit the flow and show the main menu, not
    # navigate back inside the flow to the cover-selection list.
    if not prev or step in ("buy_cover_who", "buy_cover_resume_choice", "buy_cover_next_steps"):
        await _reset(session, wa_id)
        from app.services.auto_reply_service import send_main_menu

        await send_main_menu(to=wa_id, phone_number_id=phone_number_id, wa_id=wa_id)
        return

    flow["step"] = prev
    await save_session(session)

    if prev == "buy_cover_who":
        await _send_buttons(
            wa_id,
            "✈️ Great choice — let's protect your trip!\n"
            "This will only take a few steps 😊\n\n"
            "Is this cover for:",
            [
                {"id": "cover_just_me", "title": "1. 🧑 Just me"},
                {"id": "cover_others", "title": "2. 👥 Me & Others"},
            ],
            phone_number_id,
        )

    elif prev == "buy_cover_traveler_count":
        await _send_text(
            wa_id,
            "👥 *How many travellers are covered?*\n"
            "Please reply with a number\n\n"
            "_Example: 2_\n\n"
            "⚠️ Maximum number of travellers you can add is *10*.",
            phone_number_id,
        )

    elif prev == "buy_cover_name":
        # If coming back from buy_cover_other_name, the main passenger name was
        # already appended to travelers[0] — pop it so re-entry doesn't duplicate.
        if step == "buy_cover_other_name":
            travelers = data.get("travelers", [])
            if travelers:
                travelers.pop(0)
                data["travelers"] = travelers
                await save_session(session)
        await _send_text(
            wa_id,
            "👤 👑 *Enter main passenger name*\n"
            "Enter first name and surname as it appears on the ticket.\n\n"
            "ℹ️ This person is the main passenger.\n"
            "\n"
            "_Example: Yusuf Usman_",
            phone_number_id,
        )

    elif prev == "buy_cover_other_name":
        travelers = data.get("travelers", [])
        # Undo the last collected additional traveller so the user can re-enter it.
        # travelers[0] is always the main passenger — never remove that.
        if len(travelers) > 1:
            travelers.pop()
            data["travelers"] = travelers
            await save_session(session)
        others_count = data.get("others_count", 1)
        next_num = len(travelers) + 1
        total = others_count + 1
        await _send_text(
            wa_id,
            f"👤 *Traveller {next_num} of {total}*\n"
            "Enter first name and surname as it appears on their ticket.\n\n"
            "_Example: Amina Bello_",
            phone_number_id,
        )

    elif prev == "buy_cover_email":
        await _send_text(
            wa_id,
            "*📧 Please enter your email address*\n"
            "So we can send your policy documents\n\n"
            "_Example: yusuf@email.com_",
            phone_number_id,
        )

    elif prev == "buy_cover_trip_type":
        await _send_trip_type_buttons(wa_id, phone_number_id)

    elif prev == "buy_cover_booking_ref":
        await _send_text(
            wa_id,
            "*🎫 Please enter your booking reference*\n\n_Examples: AB1XY2, 2990FA62_",
            phone_number_id,
        )

    elif prev == "buy_cover_flight_num":
        await _send_text(
            wa_id,
            "*✈️ Please enter your flight number*\n\n_Example: P47123, Q1402_",
            phone_number_id,
        )

    elif prev == "buy_cover_date":
        await _send_text(
            wa_id,
            "*📅 What date are you flying?*\n\n_Example: 12 April 2026, 12/04/2026_",
            phone_number_id,
        )

    elif prev == "buy_cover_depart_time":
        await _send_text(
            wa_id,
            "*⏰ What time is your flight scheduled to depart?*\n"
            "Example: 13:40 · 1:40 AM · 1:40 PM",
            phone_number_id,
        )

    elif prev == "buy_cover_depart_airport_pick":
        await _send_text(
            wa_id,
            "*✈️ What airport are you flying from?*\n\n"
            "Type at least 3 characters of the airport name or IATA code to search.\n\n"
            "_Example: LOS, ABV, KAD_",
            phone_number_id,
        )

    elif prev == "buy_cover_arrive_date":
        await _send_text(
            wa_id,
            "*📅 What date does your flight arrive?*\n\n"
            "_Example: 12 April 2026, 12/04/2026, 12-04-2026_",
            phone_number_id,
        )

    elif prev == "buy_cover_arrive_time":
        await _send_text(
            wa_id,
            "*⏰ What time is your flight scheduled to arrive?*\n"
            "Example: 15:00, 3:00 PM",
            phone_number_id,
        )

    elif prev == "buy_cover_depart_ampm_confirm":
        await _send_text(
            wa_id,
            "*⏰ What time is your flight scheduled to depart?*\n\n"
            "_Example: 13:40 · 1:40 AM · 3:30 PM_",
            phone_number_id,
        )

    elif prev == "buy_cover_arrive_airport_pick":
        await _send_text(
            wa_id,
            "*✈️ What airport are you arriving at?*\n\n"
            "Type at least 3 characters of the airport name or IATA code to search.\n\n"
            "_Example: LOS, Lagos, ABV, Abuja_",
            phone_number_id,
        )

    elif prev == "buy_cover_airline":
        await _send_text(
            wa_id,
            "*✈️  Who are you flying with?*\n\n_Example: Ibom Air, Air Peace_",
            phone_number_id,
        )

    elif prev == "buy_cover_edit_select":
        await _send_edit_menu(wa_id, phone_number_id)

    elif prev == "buy_cover_summary":
        # If itinerary already submitted to the API, hide Edit details to prevent
        # a re-submission error. User can only go forward (re-fetch quotes).
        if data.get("itinerary_submitted"):
            await _send_buttons(
                wa_id,
                "📋 *Trip Summary*\n\n" + _build_trip_summary_text(data),
                [{"id": "summary_confirm", "title": "▶️ Continue to covers"}],
                phone_number_id,
            )
        else:
            await _send_buttons(
                wa_id,
                "📋 *Trip Summary*\n\n" + _build_trip_summary_text(data),
                [
                    {"id": "summary_confirm", "title": "✅ Confirm"},
                    {"id": "summary_edit",    "title": "✏️ Edit details"},
                ],
                phone_number_id,
            )

    elif prev == "buy_cover_select_cover":
        # Immediately fetch and show the covers list — no "type to reload" placeholder
        quotes = session.get("api_data", {}).get("quotes") or []
        if not quotes:
            policy_id = session.get("api_data", {}).get("policy_id")
            if policy_id:
                try:
                    quotes = await ipurvey_service.fetch_quotes(policy_id) or []
                    if quotes:
                        session.setdefault("api_data", {})["quotes"] = quotes
                        await save_session(session)
                except Exception as exc:
                    logger.error(f"[BUY_COVER] go_back fetch_quotes: {exc}")
        if quotes:
            await _send_cover_page(
                wa_id,
                quotes,
                0,
                phone_number_id,
                intro_body="🛡️ *Please select a cover plan:*",
            )
        else:
            await _send_text(
                wa_id,
                "⚠️ Unable to load covers right now. Type anything to retry.",
                phone_number_id,
            )

    elif prev == "buy_cover_next_steps":
        flow  = session.get("temp_data", {}).get(BUY_COVER_FLOW_KEY, {})
        _data = flow.get("data", {})
        await _send_buttons(
            wa_id,
            _build_cover_card_body(_data),
            [
                {"id": "next_kyc",   "title": "🛒 Buy Cover"},
                {"id": "next_terms", "title": "📄 View Policy Terms"},
                {"id": "next_ask",   "title": "❓ Ask a Question"},
            ],
            phone_number_id,
        )

    else:
        await start_buy_cover_flow(wa_id=wa_id, phone_number_id=phone_number_id)


async def resume_at_current_step(wa_id: str, phone_number_id: Optional[str]) -> None:
    """Re-show the original prompt for whatever buy-cover step the user is currently on.
    Called when user taps 'No, go back' on the Cancel Purchase confirm screen.
    Uses _redisplay_step which is purpose-built to re-send the correct prompt."""
    session, flow = await _get_flow_state(wa_id)
    step = flow.get("step", "")
    data = flow.get("data", {})
    if step:
        await _redisplay_step(wa_id, step, data, session, phone_number_id)
    else:
        await start_buy_cover_flow(wa_id=wa_id, phone_number_id=phone_number_id)


async def show_cancel_purchase_confirm(wa_id: str, phone_number_id: Optional[str]):
    """Show Cancel Purchase confirmation screen (Buy / KYC / Payment flows)."""
    await _send_buttons(
        wa_id,
        "❌ *Cancel Purchase*\n\nAre you sure you want to cancel?\nYour trip details will not be saved.",
        [
            {"id": "cx_yes_buy", "title": "❌ Yes, cancel"},
            {"id": "cx_no_buy", "title": "↩️ No, go back"},
        ],
        phone_number_id,
    )
