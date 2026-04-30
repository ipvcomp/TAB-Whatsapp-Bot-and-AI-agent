import logging
import time
from collections import OrderedDict
from typing import Optional
from urllib.parse import quote

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

TIMEOUT = 15

# ── AIRPORT SEARCH CACHE ───────────────────────────────────────────────────────
_AIRPORT_CACHE_TTL: int = 300        # seconds (5 minutes)
_AIRPORT_CACHE_MAX_SIZE: int = 256   # maximum number of cached queries

# OrderedDict preserves insertion order so we can evict the oldest entry when full.
# Each value is a tuple of (timestamp: float, results: list[dict]).
_airport_cache: OrderedDict[str, tuple[float, list]] = OrderedDict()

NIGERIAN_BANK_CODES: dict[str, str] = {
    "Access Bank":             "044",
    "Carbon":                  "565",
    "Citibank Nigeria":        "023",
    "Coronation Bank":         "559",
    "Ecobank Nigeria":         "050",
    "Fidelity Bank":           "070",
    "First Bank":              "011",
    "First City Monument Bank": "214",
    "Globus Bank":             "103",
    "GT Bank":                 "058",
    "Heritage Bank":           "030",
    "Jaiz Bank":               "301",
    "Keystone Bank":           "082",
    "Kuda Bank":               "50211",
    "Lotus Bank":              "303",
    "Moniepoint":              "50515",
    "Opay":                    "999992",
    "Palmpay":                 "999991",
    "Parallex Bank":           "526",
    "Polaris Bank":            "076",
    "Providus Bank":           "101",
    "Stanbic IBTC Bank":       "221",
    "Standard Chartered":      "068",
    "Sterling Bank":           "232",
    "SunTrust Bank":           "100",
    "Taj Bank":                "302",
    "Titan Trust Bank":        "102",
    "Union Bank":              "032",
    "United Bank for Africa":  "033",
    "Unity Bank":              "215",
    "VFD Microfinance Bank":   "566",
    "Wema Bank":               "035",
    "Zenith Bank":             "057",
}


def get_bank_code(bank_name: str) -> str:
    return NIGERIAN_BANK_CODES.get(bank_name, "")


def _base() -> str:
    return get_settings().IPURVEY_BASE_URL


def _extract(resp: dict):
    return resp.get("data") if resp.get("data") is not None else resp


# ── AIRPORT SEARCH ────────────────────────────────────────────────────────────

async def search_airports(query: str) -> list[dict]:
    """Search airports via the Ipurvey API.  Returns a list of dicts with keys:
    code, name, country.  Returns [] on error or no results.

    Results are cached in memory for _AIRPORT_CACHE_TTL seconds (default 5 min)
    with a maximum of _AIRPORT_CACHE_MAX_SIZE entries to bound memory growth.
    """
    cache_key = query.strip().lower()

    # ── cache lookup ──────────────────────────────────────────────────────────
    cached = _airport_cache.get(cache_key)
    if cached is not None:
        ts, results = cached
        if time.monotonic() - ts < _AIRPORT_CACHE_TTL:
            # Move to end so the entry is treated as recently used (LRU semantics).
            _airport_cache.move_to_end(cache_key)
            logger.debug(f"[ipurvey] airport cache hit for '{cache_key}'")
            return results
        # expired — remove stale entry
        del _airport_cache[cache_key]

    logger.info(f"[ipurvey] search_airports query='{query}'")
    try:
        encoded = quote(query.strip(), safe="")
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{_base()}/api/v2/airports/search?search={encoded}")
            if r.status_code != 200:
                logger.warning(f"[ipurvey] airport search {r.status_code} for '{query}'")
                return []
            payload = r.json()
            items = payload if isinstance(payload, list) else payload.get("data", payload.get("airports", []))
            if not isinstance(items, list):
                logger.warning(f"[ipurvey] search_airports → unexpected payload type for '{query}'")
                return []
            seen_codes: set = set()
            results = []
            for item in items[:10]:
                code = (
                    item.get("iataCode") or item.get("iata_code") or
                    item.get("code") or item.get("airportCode") or ""
                )
                name = (
                    item.get("name") or item.get("airportName") or
                    item.get("airport_name") or ""
                )
                country = (
                    item.get("country") or item.get("countryName") or
                    item.get("country_name") or ""
                )
                if not (code or name):
                    continue
                dedup_key = code.upper() if code else name.lower()
                if dedup_key in seen_codes:
                    continue
                seen_codes.add(dedup_key)
                results.append({"code": code, "name": name, "country": country})

        logger.info(f"[ipurvey] search_airports → {len(results)} result(s) for '{query}'")
        # ── cache store ───────────────────────────────────────────────────────
        if len(_airport_cache) >= _AIRPORT_CACHE_MAX_SIZE:
            _airport_cache.popitem(last=False)  # evict oldest (FIFO)
        _airport_cache[cache_key] = (time.monotonic(), results)

        return results
    except Exception as e:
        logger.error(f"[ipurvey] search_airports failed: {e}")
        return []


# ── USER MANAGEMENT ───────────────────────────────────────────────────────────

async def check_user_exists(msisdn: str) -> Optional[dict]:
    logger.info(f"[ipurvey] check_user_exists msisdn='{msisdn}'")
    try:
        encoded = quote(msisdn, safe="")
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{_base()}/api/tab-ums/users/{encoded}")
            if r.status_code == 200:
                logger.info(f"[ipurvey] check_user_exists → found (200)")
                return _extract(r.json())
            logger.info(f"[ipurvey] check_user_exists → not found ({r.status_code})")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] check_user_exists failed: {e}")
        return None


async def create_user(
    msisdn: str,
    first_name: str,
    last_name: str,
    email: str,
    identity_type: str,
    identity_number: str,
    country_code: str = "NG",
) -> Optional[dict]:
    logger.info(f"[ipurvey] create_user msisdn='{msisdn}' name='{first_name} {last_name}' id_type='{identity_type}'")
    try:
        body = {
            "msisdn": msisdn,
            "firstName": first_name,
            "lastName": last_name,
            "email": email,
            "marketingConsent": True,
            "policyUpdatesConsent": True,
            "payoutAlertsConsent": True,
            "kycConsent": True,
            "nationalityCountryCode": country_code,
            "identityType": identity_type,
            "identityNumber": identity_number,
        }
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(f"{_base()}/api/tab-ums/users", json=body)
            if r.status_code in (200, 201):
                logger.info(f"[ipurvey] create_user → success ({r.status_code})")
                return _extract(r.json())
            if r.status_code == 409:
                logger.warning(f"[ipurvey] create_user → 409 conflict, fetching existing user")
                existing = await check_user_exists(msisdn)
                if existing and isinstance(existing, dict):
                    logger.info(f"[ipurvey] create_user → resolved via check_user_exists")
                    return existing
                return None
            logger.error(f"[ipurvey] create_user {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] create_user failed: {e}")
        return None


async def update_user(user_id: str, fields: dict) -> Optional[dict]:
    logger.info(f"[ipurvey] update_user user_id='{user_id}' fields={list(fields.keys())}")
    try:
        clean = {k: v for k, v in fields.items() if v is not None}
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.patch(f"{_base()}/api/tab-ums/users/{user_id}", json=clean)
            if r.status_code in (200, 204):
                logger.info(f"[ipurvey] update_user → success ({r.status_code})")
                return _extract(r.json()) if r.text else {}
            logger.warning(f"[ipurvey] update_user {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] update_user failed: {e}")
        return None


# ── POLICY MANAGEMENT ─────────────────────────────────────────────────────────

async def create_draft_policy(msisdn: str) -> Optional[dict]:
    logger.info(f"[ipurvey] create_draft_policy msisdn='{msisdn}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(
                f"{_base()}/api/tab-plc/policies/draft",
                json={"msisdn": msisdn, "channel": "WHATSAPP"},
            )
            if r.status_code in (200, 201):
                data = _extract(r.json())
                if isinstance(data, dict):
                    pid = (
                        data.get("policyId")
                        or data.get("id")
                        or data.get("policy_id")
                    )
                    existing = bool(data.get("existing", False))
                    creation_state = (
                        data.get("creationState")
                        or data.get("creation_state")
                        or "DRAFT"
                    )
                    logger.info(
                        f"[ipurvey] create_draft_policy → policy_id='{pid}' "
                        f"existing={existing} state='{creation_state}'"
                    )
                    return {
                        "policy_id": pid,
                        "existing": existing,
                        "creation_state": creation_state,
                    }
            logger.error(f"[ipurvey] create_draft_policy {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] create_draft_policy failed: {e}")
        return None


async def resume_draft_policy(msisdn: str) -> Optional[dict]:
    """GET /api/tab-plc/policies/draft/resume?msisdn={msisdn}
    Returns a dict with draft data when an existing resumable draft is found,
    None when there is no draft (404) or on any error.
    """
    logger.info(f"[ipurvey] resume_draft_policy msisdn='{msisdn}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(
                f"{_base()}/api/tab-plc/policies/draft/resume",
                params={"msisdn": msisdn},
            )
            if r.status_code in (200, 201):
                body = r.json()
                data = _extract(body)
                if isinstance(data, dict):
                    pid = (
                        data.get("policyId")
                        or data.get("id")
                        or data.get("policy_id")
                    )
                    state = (
                        data.get("creationState")
                        or data.get("creation_state")
                        or "DRAFT"
                    )
                    logger.info(
                        f"[ipurvey] resume_draft_policy → policy_id='{pid}' "
                        f"state='{state}'"
                    )
                    return {
                        "policy_id":      pid,
                        "creation_state": state,
                        "current_step":   data.get("currentStep"),
                        "passengers":     data.get("passengers") or [],
                        "email":          data.get("email") or "",
                        "trip_type":      data.get("tripType") or "",
                        "itinerary":      data.get("itinerary") or {},
                        "missing_fields": data.get("missingFields") or [],
                    }
            elif r.status_code == 404:
                logger.info("[ipurvey] resume_draft_policy → 404 (no existing draft)")
                return None
            logger.warning(
                f"[ipurvey] resume_draft_policy {r.status_code}: {r.text[:200]}"
            )
            return None
    except Exception as e:
        logger.error(f"[ipurvey] resume_draft_policy failed: {e}")
        return None


async def cancel_draft_policy(policy_id: str) -> bool:
    logger.info(f"[ipurvey] cancel_draft_policy policy_id='{policy_id}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.delete(f"{_base()}/api/tab-plc/policies/{policy_id}/draft")
            ok = r.status_code in (200, 204)
            logger.info(
                f"[ipurvey] cancel_draft_policy → {'success' if ok else 'failed'} ({r.status_code})"
            )
            return ok
    except Exception as e:
        logger.error(f"[ipurvey] cancel_draft_policy failed: {e}")
        return False


async def set_traveler_count(policy_id: str, count: int) -> Optional[list]:
    logger.info(f"[ipurvey] set_traveler_count policy_id='{policy_id}' count={count}")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.put(
                f"{_base()}/api/tab-plc/policies/{policy_id}/traveler-count",
                json={"travelerCount": count},
            )
            if r.status_code in (200, 204):
                resp = r.json() if r.text else {}
                # Response shape: {"data": {"passengerIds": ["uuid1", ...]}, ...}
                data = _extract(resp)  # returns resp["data"] → {"passengerIds": [...]}
                pax_ids: list = []
                if isinstance(data, dict):
                    pax_ids = data.get("passengerIds") or data.get("passenger_ids") or []
                elif isinstance(data, list):
                    pax_ids = data
                if not isinstance(pax_ids, list):
                    pax_ids = []
                logger.info(
                    f"[ipurvey] set_traveler_count → success ({r.status_code}), "
                    f"passenger_ids={pax_ids}"
                )
                return pax_ids
            logger.warning(f"[ipurvey] set_traveler_count {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] set_traveler_count failed: {e}")
        return None


async def add_passenger(
    policy_id: str,
    first_name: str,
    surname: str,
    is_primary: bool = False,
) -> Optional[dict]:
    logger.info(f"[ipurvey] add_passenger policy_id='{policy_id}' name='{first_name} {surname}' primary={is_primary}")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(
                f"{_base()}/api/tab-plc/policies/{policy_id}/passengers",
                json={
                    "firstName": first_name,
                    "surname": surname,
                    "isPrimaryTraveller": is_primary,
                },
            )
            if r.status_code in (200, 201):
                logger.info(f"[ipurvey] add_passenger → success ({r.status_code})")
                return _extract(r.json())
            logger.error(f"[ipurvey] add_passenger {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] add_passenger failed: {e}")
        return None


async def update_passenger(
    policy_id: str,
    passenger_id: str,
    first_name: str,
    surname: str,
    is_primary: bool = False,
) -> bool:
    logger.info(f"[ipurvey] update_passenger policy_id='{policy_id}' passenger_id='{passenger_id}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.put(
                f"{_base()}/api/tab-plc/policies/{policy_id}/passengers/{passenger_id}",
                json={
                    "firstName": first_name,
                    "surname": surname,
                    "isPrimaryTraveller": is_primary,
                },
            )
            ok = r.status_code in (200, 204)
            logger.info(f"[ipurvey] update_passenger → {'success' if ok else 'failed'} ({r.status_code})")
            return ok
    except Exception as e:
        logger.error(f"[ipurvey] update_passenger failed: {e}")
        return False


async def set_policy_email(policy_id: str, email: str) -> bool:
    logger.info(f"[ipurvey] set_policy_email policy_id='{policy_id}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.put(
                f"{_base()}/api/tab-plc/policies/{policy_id}/email",
                json={"email": email},
            )
            ok = r.status_code in (200, 204)
            logger.info(f"[ipurvey] set_policy_email → {'success' if ok else 'failed'} ({r.status_code})")
            return ok
    except Exception as e:
        logger.error(f"[ipurvey] set_policy_email failed: {e}")
        return False


async def submit_itinerary(
    policy_id: str,
    trip_type: str,
    booking_ref: str,
    legs: list,
) -> bool:
    logger.info(f"[ipurvey] submit_itinerary policy_id='{policy_id}' trip_type='{trip_type}' booking_ref='{booking_ref}' legs={len(legs)}")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.put(
                f"{_base()}/api/tab-plc/policies/{policy_id}/itinerary",
                json={
                    "tripType": trip_type,
                    "bookingReference": booking_ref,
                    "legs": legs,
                },
            )
            ok = r.status_code in (200, 204)
            if ok:
                logger.info(f"[ipurvey] submit_itinerary → success ({r.status_code})")
            else:
                logger.error(
                    f"[ipurvey] submit_itinerary → failed ({r.status_code}): {r.text[:400]}"
                )
            return ok
    except Exception as e:
        logger.error(f"[ipurvey] submit_itinerary failed: {e}")
        return False


async def fetch_quotes(policy_id: str) -> Optional[list]:
    logger.info(f"[ipurvey] fetch_quotes policy_id='{policy_id}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{_base()}/api/tab-plc/policies/{policy_id}/quotes")
            if r.status_code == 200:
                data = _extract(r.json())
                if isinstance(data, list):
                    logger.info(f"[ipurvey] fetch_quotes → {len(data)} quote(s)")
                    return data
                if isinstance(data, dict):
                    quotes = data.get("products") or data.get("quotes") or None
                    logger.info(f"[ipurvey] fetch_quotes → {len(quotes) if quotes else 0} quote(s)")
                    return quotes
            logger.warning(f"[ipurvey] fetch_quotes {r.status_code}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] fetch_quotes failed: {e}")
        return None


async def select_cover(policy_id: str, product_id: str) -> bool:
    logger.info(f"[ipurvey] select_cover policy_id='{policy_id}' product_id='{product_id}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.put(
                f"{_base()}/api/tab-plc/policies/{policy_id}/selected-cover",
                json={"productId": product_id},
            )
            ok = r.status_code in (200, 204)
            logger.info(f"[ipurvey] select_cover → {'success' if ok else 'failed'} ({r.status_code})")
            return ok
    except Exception as e:
        logger.error(f"[ipurvey] select_cover failed: {e}")
        return False


async def link_user_to_policy(policy_id: str, user_id: str) -> bool:
    logger.info(f"[ipurvey] link_user_to_policy policy_id='{policy_id}' user_id='{user_id}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.put(
                f"{_base()}/api/tab-plc/policies/{policy_id}/user-id",
                json={"userId": user_id},
            )
            ok = r.status_code in (200, 204)
            logger.info(f"[ipurvey] link_user_to_policy → {'success' if ok else 'failed'} ({r.status_code})")
            return ok
    except Exception as e:
        logger.error(f"[ipurvey] link_user_to_policy failed: {e}")
        return False


async def search_policies(
    msisdn: Optional[str] = None,
    policy_code: Optional[str] = None,
    flight_number: Optional[str] = None,
) -> Optional[list]:
    logger.info(f"[ipurvey] search_policies msisdn='{msisdn}' code='{policy_code}' flight='{flight_number}'")
    try:
        params: dict = {"page": 0, "size": 10}
        if msisdn:
            params["msisdn"] = msisdn.lstrip("+")
        if policy_code:
            params["policyCode"] = policy_code
        if flight_number:
            params["flightNumber"] = flight_number
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(
                f"{_base()}/api/tab-plc/policies/search/user",
                params=params,
            )
            if r.status_code == 200:
                data = _extract(r.json())
                if isinstance(data, list):
                    logger.info(f"[ipurvey] search_policies → {len(data)} result(s)")
                    return data
                if isinstance(data, dict):
                    result = (
                        data.get("content")
                        or data.get("policies")
                        or data.get("items")
                        or []
                    )
                    logger.info(f"[ipurvey] search_policies → {len(result)} result(s)")
                    return result
            logger.info(f"[ipurvey] search_policies → {r.status_code}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] search_policies failed: {e}")
        return None


async def get_policy_by_code(policy_code: str) -> Optional[dict]:
    logger.info(f"[ipurvey] get_policy_by_code code='{policy_code}'")
    try:
        results = await search_policies(policy_code=policy_code)
        if results:
            logger.info(f"[ipurvey] get_policy_by_code → found via search")
            return results[0]
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{_base()}/api/tab-plc/policies/{policy_code}")
            if r.status_code == 200:
                logger.info(f"[ipurvey] get_policy_by_code → found via direct fetch (200)")
                return _extract(r.json())
            logger.info(f"[ipurvey] get_policy_by_code → not found ({r.status_code})")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] get_policy_by_code failed: {e}")
        return None


async def get_policy_document_url(policy_code: str) -> Optional[str]:
    logger.info(f"[ipurvey] get_policy_document_url code='{policy_code}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{_base()}/api/tab-plc/policies/{policy_code}/document")
            if r.status_code == 200:
                data = _extract(r.json())
                if isinstance(data, dict):
                    url = (
                        data.get("downloadUrl")
                        or data.get("url")
                        or data.get("documentUrl")
                    )
                    logger.info(f"[ipurvey] get_policy_document_url → {'found' if url else 'no url in response'}")
                    return url
                if isinstance(data, str):
                    logger.info(f"[ipurvey] get_policy_document_url → found (string)")
                    return data
            logger.info(f"[ipurvey] get_policy_document_url → {r.status_code}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] get_policy_document_url failed: {e}")
        return None


async def check_eligibility(
    policy_id: str,
    delay_minutes: int = 90,
) -> Optional[dict]:
    logger.info(f"[ipurvey] check_eligibility policy_id='{policy_id}' delay_minutes={delay_minutes}")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(
                f"{_base()}/api/tab-plc/policies/{policy_id}/checkEligibility",
                params={"triggerType": "DELAY", "delayMinutes": delay_minutes},
            )
            if r.status_code == 200:
                logger.info(f"[ipurvey] check_eligibility → found (200)")
                return _extract(r.json())
            logger.info(f"[ipurvey] check_eligibility → {r.status_code}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] check_eligibility failed: {e}")
        return None


# ── KYC ──────────────────────────────────────────────────────────────────────

async def check_kyc_status(policy_id: str) -> Optional[dict]:
    logger.info(f"[ipurvey] check_kyc_status policy_id='{policy_id}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(
                f"{_base()}/api/tab-plc/policies/{policy_id}/kyc-status"
            )
            if r.status_code == 200:
                logger.info(f"[ipurvey] check_kyc_status → found (200)")
                return _extract(r.json())
            logger.info(f"[ipurvey] check_kyc_status → {r.status_code}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] check_kyc_status failed: {e}")
        return None


async def initiate_kyc(
    user_id: str,
    identity_type: str,
    identity_number: str,
) -> Optional[dict]:
    logger.info(f"[ipurvey] initiate_kyc user_id='{user_id}' identity_type='{identity_type}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(
                f"{_base()}/api/tab-ums/users/{user_id}/kyc/initiate",
                json={"identityType": identity_type, "identityNumber": identity_number},
            )
            if r.status_code in (200, 201):
                logger.info(f"[ipurvey] initiate_kyc → success ({r.status_code})")
                return _extract(r.json())
            logger.error(f"[ipurvey] initiate_kyc {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] initiate_kyc failed: {e}")
        return None


async def verify_kyc_otp(user_id: str, session_id: str, otp: str) -> bool:
    logger.info(f"[ipurvey] verify_kyc_otp user_id='{user_id}' session_id='{session_id}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(
                f"{_base()}/api/tab-ums/users/{user_id}/kyc/verify",
                json={"sessionId": session_id, "otp": otp},
            )
            ok = r.status_code in (200, 204)
            try:
                body = r.json()
                data = body.get("data") or {}
                api_verified = data.get("verified")
                api_status   = data.get("status", "")
                logger.info(
                    f"[ipurvey] verify_kyc_otp → http={r.status_code} "
                    f"data.verified={api_verified} data.status={api_status}"
                )
            except Exception:
                logger.info(f"[ipurvey] verify_kyc_otp → http={r.status_code} (no JSON body)")
            return ok
    except Exception as e:
        logger.error(f"[ipurvey] verify_kyc_otp failed: {e}")
        return False


async def resend_kyc_otp(user_id: str, session_id: str) -> bool:
    logger.info(f"[ipurvey] resend_kyc_otp user_id='{user_id}' session_id='{session_id}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(
                f"{_base()}/api/tab-ums/users/{user_id}/kyc/resend-otp",
                json={"sessionId": session_id},
            )
            ok = r.status_code in (200, 204)
            logger.info(f"[ipurvey] resend_kyc_otp → {'success' if ok else 'failed'} ({r.status_code})")
            return ok
    except Exception as e:
        logger.error(f"[ipurvey] resend_kyc_otp failed: {e}")
        return False


# ── PAYOUT METHODS ─────────────────────────────────────────────────────────────

async def get_payout_methods(user_id: str) -> Optional[list]:
    logger.info(f"[ipurvey] get_payout_methods user_id='{user_id}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{_base()}/api/tab-ums/users/{user_id}/payout-methods")
            if r.status_code == 200:
                data = _extract(r.json())
                result = data if isinstance(data, list) else None
                logger.info(f"[ipurvey] get_payout_methods → {len(result) if result else 0} method(s)")
                return result
            logger.info(f"[ipurvey] get_payout_methods → {r.status_code}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] get_payout_methods failed: {e}")
        return None


async def create_payout_method_bank(
    user_id: str,
    account_number: str,
    account_name: str,
    bank_code: str,
    bank_name: str,
    is_default: bool = True,
) -> Optional[dict]:
    logger.info(f"[ipurvey] create_payout_method_bank user_id='{user_id}' bank='{bank_name}' code='{bank_code}'")
    try:
        body = {
            "type": "BANK_ACCOUNT",
            "accountNumber": account_number,
            "accountName": account_name,
            "isDefault": is_default,
            "active": True,
            "config": {
                "bank_code": bank_code,
                "bank_name": bank_name,
                "country": "NG",
                "currency": "NGN",
            },
        }
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(
                f"{_base()}/api/tab-ums/users/{user_id}/payout-methods",
                json=body,
            )
            if r.status_code in (200, 201):
                logger.info(f"[ipurvey] create_payout_method_bank → success ({r.status_code})")
                return _extract(r.json())
            logger.error(f"[ipurvey] create_payout_method_bank {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] create_payout_method_bank failed: {e}")
        return None


async def create_payout_method_wallet(
    user_id: str,
    phone_number: str,
    account_name: str,
    network: str,
    is_default: bool = False,
) -> Optional[dict]:
    logger.info(f"[ipurvey] create_payout_method_wallet user_id='{user_id}' network='{network}'")
    try:
        body = {
            "type": "MOBILE_MONEY",
            "accountNumber": phone_number,
            "accountName": account_name,
            "isDefault": is_default,
            "active": True,
            "config": {
                "network": network,
                "country": "NG",
                "currency": "NGN",
            },
        }
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(
                f"{_base()}/api/tab-ums/users/{user_id}/payout-methods",
                json=body,
            )
            if r.status_code in (200, 201):
                logger.info(f"[ipurvey] create_payout_method_wallet → success ({r.status_code})")
                return _extract(r.json())
            logger.error(f"[ipurvey] create_payout_method_wallet {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] create_payout_method_wallet failed: {e}")
        return None


# ── PAYMENT ───────────────────────────────────────────────────────────────────

async def initiate_payment(
    policy_id: str,
    payment_method: str = "CARD",
) -> Optional[dict]:
    logger.info(f"[ipurvey] initiate_payment policy_id='{policy_id}' method='{payment_method}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(
                f"{_base()}/api/tab-plc/policies/{policy_id}/payment/initiate",
                params={"preferredPaymentMethod": payment_method},
            )
            if r.status_code in (200, 201):
                logger.info(f"[ipurvey] initiate_payment → success ({r.status_code})")
                return _extract(r.json())
            logger.error(f"[ipurvey] initiate_payment {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] initiate_payment failed: {e}")
        return None


async def get_payment_status(policy_id: str, msisdn: str) -> Optional[dict]:
    logger.info(f"[ipurvey] get_payment_status policy_id='{policy_id}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(
                f"{_base()}/api/tab-plc/policies/{policy_id}/payment-status",
                params={"msisdn": msisdn},
            )
            if r.status_code == 200:
                logger.info(f"[ipurvey] get_payment_status → found (200)")
                return _extract(r.json())
            logger.info(f"[ipurvey] get_payment_status → {r.status_code}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] get_payment_status failed: {e}")
        return None


def _to_ddmmyyyy(date_str: str) -> str:
    """Convert various date string formats to DD-MM-YYYY for the submission API."""
    from datetime import datetime as _dt
    for fmt in ["%d %B %Y", "%d/%m/%Y", "%d-%m-%Y", "%B %d, %Y", "%d %b %Y", "%Y-%m-%d"]:
        try:
            return _dt.strptime(date_str.strip(), fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    return date_str.strip()


async def submit_policy(
    msisdn: str,
    product_id: Optional[str] = None,
    policy_id: Optional[str] = None,
    first_name: str = "",
    last_name: str = "",
    email: str = "",
    id_type: str = "BVN",
    id_number: str = "",
    booking_ref: str = "",
    flight_num: str = "",
    trip_type: str = "ONE_WAY",
    dep_airport: str = "",
    arr_airport: str = "",
    dep_date: str = "",
    dep_time: str = "",
    arr_date: str = "",
    arr_time: str = "",
    bank_code: str = "",
    account_number: str = "",
    account_name: str = "",
    payout_method_id: Optional[str] = None,
    boarding_pass_bytes: Optional[bytes] = None,
    boarding_pass_filename: str = "boarding_pass.jpg",
) -> tuple[Optional[str], Optional[str]]:
    """Submit a policy to the Ipurvey API.

    Returns a tuple of (policy_ref, error_message).
    On success policy_ref is the reference string and error_message is None.
    On failure policy_ref is None and error_message describes the problem.

    If boarding_pass_bytes is provided the request is sent as
    multipart/form-data with the file attached; otherwise plain JSON is used.
    """
    logger.info(f"[ipurvey] submit_policy msisdn='{msisdn}' product_id='{product_id}' policy_id='{policy_id}' boarding_pass={'yes' if boarding_pass_bytes else 'no'}")
    try:
        dep_date_fmt = _to_ddmmyyyy(dep_date) if dep_date else ""
        arr_date_fmt = _to_ddmmyyyy(arr_date) if arr_date else dep_date_fmt

        body: dict = {"channel": "WHATSAPP"}
        if msisdn:
            body["msisdn"] = msisdn if msisdn.startswith("+") else f"+{msisdn}"
        if product_id:
            body["productId"] = product_id
        if policy_id:
            body["draftPolicyId"] = policy_id
        if first_name:
            body["firstName"] = first_name
        if last_name:
            body["lastName"] = last_name
        if email:
            body["email"] = email
        if id_number:
            body["identityType"] = id_type
            body["identityNumber"] = id_number
        if booking_ref:
            body["bookingReference"] = booking_ref
        if flight_num:
            body["flightNumber"] = flight_num
        body["tripType"] = trip_type
        if dep_airport:
            body["departureAirport"] = dep_airport
        if arr_airport:
            body["arrivalAirport"] = arr_airport
        if dep_date_fmt:
            body["departureDateLocal"] = dep_date_fmt
        if dep_time:
            body["departureTimeLocal"] = dep_time
        if arr_date_fmt:
            body["arrivalDateLocal"] = arr_date_fmt
        if arr_time:
            body["arrivalTimeLocal"] = arr_time
        if bank_code:
            body["bankCode"] = bank_code
        if account_number:
            body["accountNumber"] = account_number
        if account_name:
            body["accountName"] = account_name
        if payout_method_id:
            body["payoutMethodId"] = payout_method_id

        async with httpx.AsyncClient(timeout=30) as c:
            if boarding_pass_bytes:
                ext = boarding_pass_filename.lower().rsplit(".", 1)[-1] if "." in boarding_pass_filename else "jpg"
                content_type_map = {
                    "pdf":  "application/pdf",
                    "png":  "image/png",
                    "jpg":  "image/jpeg",
                    "jpeg": "image/jpeg",
                    "webp": "image/webp",
                }
                bp_content_type = content_type_map.get(ext, "application/octet-stream")
                logger.info(f"[ipurvey] submit_policy: attaching boarding pass ({boarding_pass_filename}, {len(boarding_pass_bytes)} bytes)")
                r = await c.post(
                    f"{_base()}/api/tab-plc/policies",
                    data=body,
                    files={"boardingPass": (boarding_pass_filename, boarding_pass_bytes, bp_content_type)},
                )
            else:
                r = await c.post(f"{_base()}/api/tab-plc/policies", json=body)
            if r.status_code in (200, 201):
                data = _extract(r.json())
                if isinstance(data, dict):
                    ref = (
                        data.get("policyCode")
                        or data.get("policyReference")
                        or data.get("policyNumber")
                        or data.get("code")
                        or data.get("reference")
                        or data.get("ref")
                    )
                    if ref:
                        logger.info(f"[ipurvey] submit_policy → success ref='{ref}'")
                        return str(ref), None
                    return None, "Policy submitted but no reference was returned."
                return None, "Unexpected response format from submission API."
            try:
                err_body = r.json()
                msg = (
                    err_body.get("message")
                    or err_body.get("error")
                    or err_body.get("detail")
                    or f"HTTP {r.status_code}"
                )
            except Exception:
                msg = f"HTTP {r.status_code}"
            logger.error(f"[ipurvey] submit_policy {r.status_code}: {r.text[:300]}")
            return None, str(msg)
    except Exception as e:
        logger.error(f"[ipurvey] submit_policy failed: {e}")
        return None, str(e)


# ── BOARDING PASS ─────────────────────────────────────────────────────────────

async def upload_boarding_pass(
    policy_id: str,
    passenger_id: str,
    file_bytes: bytes,
    file_name: str,
    flight_id: str,
) -> bool:
    logger.info(f"[ipurvey] upload_boarding_pass policy_id='{policy_id}' passenger_id='{passenger_id}' file='{file_name}' size={len(file_bytes)}B")
    try:
        ext = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
        content_type_map = {
            "pdf":  "application/pdf",
            "png":  "image/png",
            "jpg":  "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
        }
        content_type = content_type_map.get(ext, "application/octet-stream")
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{_base()}/api/tab-plc/policies/{policy_id}/passengers/{passenger_id}/boarding-pass",
                files={"file": (file_name, file_bytes, content_type)},
                data={"flightId": flight_id},
            )
            ok = r.status_code in (200, 201, 204)
            logger.info(f"[ipurvey] upload_boarding_pass → {'success' if ok else 'failed'} ({r.status_code})")
            return ok
    except Exception as e:
        logger.error(f"[ipurvey] upload_boarding_pass failed: {e}")
        return False


async def poll_boarding_pass_status(
    policy_id: str,
    passenger_id: str,
) -> Optional[dict]:
    logger.info(f"[ipurvey] poll_boarding_pass_status policy_id='{policy_id}' passenger_id='{passenger_id}'")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(
                f"{_base()}/api/tab-plc/policies/{policy_id}/passengers/{passenger_id}/boarding-pass/status"
            )
            if r.status_code == 200:
                logger.info(f"[ipurvey] poll_boarding_pass_status → found (200)")
                return _extract(r.json())
            logger.info(f"[ipurvey] poll_boarding_pass_status → {r.status_code}")
            return None
    except Exception as e:
        logger.error(f"[ipurvey] poll_boarding_pass_status failed: {e}")
        return None


# ── POLICY FIELD MAPPER ───────────────────────────────────────────────────────

def map_api_policy(p: dict) -> dict:
    """Map an Ipurvey API policy object to the internal display dict format."""
    legs = []
    itin = p.get("itinerary") or {}
    if isinstance(itin, dict):
        legs = itin.get("legs") or []
    leg0 = legs[0] if legs else {}

    raw_status = (p.get("status") or "").upper()
    if raw_status in ("ACTIVE", "APPROVED", "ISSUED"):
        status = "Active"
    elif raw_status in ("EXPIRED", "LAPSED"):
        status = "Expired"
    else:
        status = raw_status.capitalize() or "Pending"

    flight   = (
        leg0.get("flightNo")
        or leg0.get("flightNumber")
        or p.get("flightNo")
        or p.get("flightNumber")
        or "—"
    )
    dep_date = (
        leg0.get("scheduledDepartureDateLocal")
        or leg0.get("departureDate")
        or p.get("departureDate")
        or "—"
    )
    dep_airport = (
        leg0.get("departureAirport") or "—"
    )
    arr_airport = (
        leg0.get("arrivalAirport") or "—"
    )
    first  = p.get("passengerFirstName") or ""
    last   = p.get("passengerSurname") or ""
    traveler = f"{first} {last}".strip() or "—"

    return {
        "id":        p.get("id") or p.get("policyId") or "",
        "ref":       p.get("policyCode") or p.get("ref") or "—",
        "name":      p.get("productName") or p.get("name") or "Policy",
        "status":    status,
        "airline":   p.get("airlineName") or p.get("airline") or "—",
        "flight":    flight,
        "date":      dep_date,
        "origin":    f"{dep_airport}" if dep_airport != "—" else "—",
        "dest":      f"{arr_airport}" if arr_airport != "—" else "—",
        "cover":     p.get("productName") or "—",
        "price":     f"₦{p['premiumAmount']:,.0f}" if p.get("premiumAmount") else "—",
        "travelers": [traveler],
        "doc_url":   (
            p.get("documentUrl")
            or p.get("doc_url")
            or f"{get_settings().IPURVEY_BASE_URL}/api/tab-plc/policies/{p.get('policyCode', '')}/document"
        ),
        "payment_status": p.get("paymentStatus") or "—",
        "policy_id_raw":  p.get("id") or p.get("policyId") or "",
    }
