import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

IPURVEY_BASE_URL = "https://dev-ilekun-ipv.ipurvey.com/api/tab-plc"
REQUEST_TIMEOUT = 10.0


def _mask_pii(raw: dict) -> dict:
    """Return a shallow copy of *raw* with passenger/traveller names redacted.

    Only the fields that carry personal names are masked; all other fields
    (flight numbers, dates, codes, etc.) are left intact so the log is still
    useful for confirming API field names.
    """
    masked = dict(raw)
    # Redact top-level name fields
    for key in ("firstName", "surname", "lastName", "name", "fullName", "traveler"):
        if key in masked:
            masked[key] = "***"
    # Redact names inside passenger / traveller lists
    for list_key in ("passengers", "travelers", "insuredPersons"):
        if list_key in masked and isinstance(masked[list_key], list):
            redacted_list = []
            for item in masked[list_key]:
                if isinstance(item, dict):
                    item = dict(item)
                    for name_key in ("firstName", "surname", "lastName", "name", "fullName"):
                        if name_key in item:
                            item[name_key] = "***"
                elif isinstance(item, str):
                    item = "***"
                redacted_list.append(item)
            masked[list_key] = redacted_list
    return masked


def _normalize_policy(raw: dict) -> dict:
    """Normalise a raw Ipurvey policy dict into the canonical shape used by all flows.

    Confirmed Ipurvey ``by-msisdn`` / search response field names
    (updated against the dev-ilekun-ipv API, April 2026):
      - policyCode        – human-readable policy reference (e.g. "TAB-001234")
      - policyReference   – alternative reference field
      - id                – internal UUID
      - productName       – plan/product label (e.g. "Local Travel Premium")
      - status            – policy state (e.g. "ACTIVE")
      - flightNumber      – e.g. "P41234"
      - airlineName       – e.g. "Air Peace"
      - departureAirport  – IATA code of origin (e.g. "LOS")
      - arrivalAirport    – IATA code of destination (e.g. "ABJ")
      - departureDateLocal – departure date string (e.g. "15-05-2026")
      - passengers        – list of objects with firstName, surname, isPrimaryTraveller
      - premium / premiumAmount – policy cost (numeric, NGN)
      - documentUrl / downloadUrl – link to policy PDF

    The function also accepts legacy/alternative field names so it degrades
    gracefully when the API adds or renames fields.
    """
    # DEBUG: log the raw response with PII masked so field names can be confirmed
    # in production without exposing personal data.  Set LOG_LEVEL=DEBUG to see this.
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Raw policy dict (PII masked): %s", _mask_pii(raw))

    travelers_raw = raw.get("passengers") or raw.get("travelers") or raw.get("insuredPersons") or []
    if isinstance(travelers_raw, list):
        travelers = []
        for t in travelers_raw:
            if isinstance(t, str):
                name = t
            else:
                first = t.get("firstName") or ""
                surname = t.get("surname") or t.get("lastName") or ""
                name = ((first + " " + surname).strip()) or t.get("name") or t.get("fullName") or ""
            if name:
                travelers.append(name)
    else:
        travelers = [str(travelers_raw)] if travelers_raw else []

    if not travelers:
        first = raw.get("firstName", "")
        last = raw.get("surname") or raw.get("lastName", "")
        full = (first + " " + last).strip() or raw.get("name", "") or raw.get("traveler", "")
        if full:
            travelers = [full]

    ref = (
        raw.get("policyCode")
        or raw.get("policyNumber")
        or raw.get("policyReference")
        or raw.get("ref")
        or raw.get("id")
        or raw.get("policyRef")
        or ""
    )
    name = (
        raw.get("productName")
        or raw.get("planName")
        or raw.get("name")
        or raw.get("policyType")
        or "Travel Policy"
    )
    status = raw.get("status") or raw.get("policyStatus") or "Active"
    airline = (
        raw.get("airlineName")
        or raw.get("airline")
        or raw.get("carrier")
        or raw.get("carrierName")
        or ""
    )
    flight = (
        raw.get("flightNumber")
        or raw.get("flight")
        or raw.get("flightNo")
        or ""
    )
    date = (
        raw.get("departureDateLocal")
        or raw.get("departureDate")
        or raw.get("travelDate")
        or raw.get("date")
        or raw.get("startDate")
        or ""
    )
    origin = (
        raw.get("departureAirport")
        or raw.get("originAirport")
        or raw.get("origin")
        or ""
    )
    dest = (
        raw.get("arrivalAirport")
        or raw.get("destinationAirport")
        or raw.get("destination")
        or raw.get("dest")
        or ""
    )
    cover = raw.get("coverType") or raw.get("cover") or raw.get("planType") or ""
    price = raw.get("premium") or raw.get("premiumAmount") or raw.get("price") or raw.get("amount") or ""
    if price and not str(price).startswith("₦"):
        price = f"₦{price}"
    doc_url = (
        raw.get("documentUrl")
        or raw.get("downloadUrl")
        or raw.get("url")
        or raw.get("doc_url")
        or (f"{IPURVEY_BASE_URL}/policies/{ref}/document" if ref else "")
    )
    policy_id = raw.get("id") or raw.get("policyId") or ref

    return {
        "id":        str(policy_id),
        "ref":       str(ref),
        "name":      str(name),
        "status":    str(status),
        "airline":   str(airline),
        "flight":    str(flight),
        "date":      str(date),
        "origin":    str(origin),
        "dest":      str(dest),
        "cover":     str(cover),
        "price":     str(price),
        "travelers": travelers if travelers else ["—"],
        "traveler":  travelers[0] if travelers else "—",
        "doc_url":   str(doc_url),
    }


def _extract_policy_list(data) -> list:
    """Extract a list of raw policy dicts from any response shape the API may return."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            return inner
        if isinstance(inner, dict):
            content = inner.get("content")
            if isinstance(content, list):
                return content
        for key in ("policies", "results", "items", "content"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                return candidate
    return []


async def fetch_policies_by_msisdn(msisdn: str) -> list:
    if msisdn and not msisdn.startswith("+"):
        msisdn = f"+{msisdn}"
    url = f"{IPURVEY_BASE_URL}/policies/by-msisdn/{msisdn}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        raw_list = _extract_policy_list(data)
        policies = [_normalize_policy(p) for p in raw_list]
        logger.info("Fetched %d policies for msisdn %s", len(policies), msisdn)
        return policies

    except httpx.HTTPStatusError as exc:
        logger.warning("Ipurvey API HTTP error for %s: %s", msisdn, exc.response.status_code)
        return []
    except httpx.RequestError as exc:
        logger.warning("Ipurvey API request error for %s: %s", msisdn, exc)
        return []
    except Exception as exc:
        logger.exception("Unexpected error fetching policies for %s: %s", msisdn, exc)
        return []
