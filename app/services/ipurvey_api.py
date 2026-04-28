import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

IPURVEY_BASE_URL = "https://dev-ilekun-ipv.ipurvey.com/api/tab-plc"
REQUEST_TIMEOUT = 10.0


def _normalize_policy(raw: dict) -> dict:
    travelers_raw = raw.get("travelers") or raw.get("insuredPersons") or raw.get("passengers") or []
    if isinstance(travelers_raw, list):
        travelers = [
            t if isinstance(t, str) else (t.get("name") or t.get("fullName") or t.get("firstName", "") + " " + t.get("lastName", "")).strip()
            for t in travelers_raw
        ]
        travelers = [t for t in travelers if t]
    else:
        travelers = [str(travelers_raw)] if travelers_raw else []

    if not travelers:
        first = raw.get("firstName", "")
        last = raw.get("lastName", "")
        full = (first + " " + last).strip() or raw.get("name", "") or raw.get("traveler", "")
        if full:
            travelers = [full]

    ref = raw.get("policyNumber") or raw.get("ref") or raw.get("id") or raw.get("policyRef") or ""
    name = raw.get("planName") or raw.get("productName") or raw.get("name") or raw.get("policyType") or "Travel Policy"
    status = raw.get("status") or raw.get("policyStatus") or "Active"
    airline = raw.get("airline") or raw.get("airlineName") or raw.get("carrier") or ""
    flight = raw.get("flightNumber") or raw.get("flight") or raw.get("flightNo") or ""
    date = raw.get("travelDate") or raw.get("departureDate") or raw.get("date") or raw.get("startDate") or ""
    origin = raw.get("originAirport") or raw.get("origin") or raw.get("departureAirport") or ""
    dest = raw.get("destinationAirport") or raw.get("destination") or raw.get("dest") or raw.get("arrivalAirport") or ""
    cover = raw.get("coverType") or raw.get("cover") or raw.get("planType") or ""
    price = raw.get("premium") or raw.get("price") or raw.get("amount") or ""
    if price and not str(price).startswith("₦"):
        price = f"₦{price}"
    doc_url = (
        raw.get("documentUrl")
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


async def fetch_policies_by_msisdn(msisdn: str) -> list:
    url = f"{IPURVEY_BASE_URL}/policies/by-msisdn/{msisdn}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        if isinstance(data, list):
            raw_list = data
        elif isinstance(data, dict):
            raw_list = (
                data.get("data")
                or data.get("policies")
                or data.get("results")
                or data.get("items")
                or []
            )
            if not isinstance(raw_list, list):
                raw_list = []
        else:
            raw_list = []

        policies = [_normalize_policy(p) for p in raw_list]
        masked = msisdn[:4] + "****" + msisdn[-2:] if len(msisdn) > 6 else "****"
        logger.info("Fetched %d policies for msisdn %s", len(policies), masked)
        return policies

    except httpx.HTTPStatusError as exc:
        masked = msisdn[:4] + "****" + msisdn[-2:] if len(msisdn) > 6 else "****"
        logger.warning("Ipurvey API HTTP error for %s: %s", masked, exc.response.status_code)
        return []
    except httpx.RequestError as exc:
        masked = msisdn[:4] + "****" + msisdn[-2:] if len(msisdn) > 6 else "****"
        logger.warning("Ipurvey API request error for %s: %s", masked, exc)
        return []
    except Exception as exc:
        masked = msisdn[:4] + "****" + msisdn[-2:] if len(msisdn) > 6 else "****"
        logger.exception("Unexpected error fetching policies for %s: %s", masked, exc)
        return []
