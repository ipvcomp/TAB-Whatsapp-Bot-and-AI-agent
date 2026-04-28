# TravelAssist WhatsApp Bot — API Integration Summary

**Date:** April 2026
**Base URL:** `https://dev-ilekun-ipv.ipurvey.com`
**Channel:** `WHATSAPP`
**Total APIs Integrated:** 31

---

## Table of Contents

1. [Overview](#overview)
2. [API Integration by Flow](#api-integration-by-flow)
   - [Buy Cover Flow](#1-buy-cover-flow)
   - [KYC Flow](#2-kyc-flow)
   - [Payment Flow](#3-payment-flow)
   - [Check Policy Flow](#4-check-policy-flow)
   - [Boarding Pass Link Flow](#5-boarding-pass-link-flow)
   - [Update Details Flow](#6-update-details-flow)
3. [Complete API Reference](#complete-api-reference)
4. [Infrastructure Changes](#infrastructure-changes)
5. [What Changed vs. Before](#what-changed-vs-before)
6. [UX Changes](#ux-changes)

---

## Overview

All 31 Ipurvey API endpoints have been integrated into the existing WhatsApp conversation flows. Before this work, the bot used hardcoded fake data (demo policies, fake payment references, static airport lists). After this work, every piece of data comes from and goes to real Ipurvey backend APIs.

Two service files handle all API calls:
- `app/services/ipurvey_service.py` — core API client (28 functions)
- `app/services/ipurvey_api.py` — policy fetch & normalisation for check/boarding pass flows

---

## API Integration by Flow

---

### 1. Buy Cover Flow
**File:** `app/services/buy_cover_flow_service.py`

This is the main policy purchase flow. APIs are called in sequence as the user progresses through the conversation.

#### Step-by-step API calls:

| Conversation Step | API Called | Method | Endpoint |
|---|---|---|---|
| Flow starts (check if user exists) | `check_user_exists` | GET | `/api/tab-ums/users/{msisdn}` |
| MSISDN confirmed | `create_draft_policy` | POST | `/api/tab-plc/policies/draft` |
| Traveler count set | `set_traveler_count` | PUT | `/api/tab-plc/policies/{id}/traveler-count` |
| Primary traveler name entered | `add_passenger` (is_primary=True) | POST | `/api/tab-plc/policies/{id}/passengers` |
| Additional traveler name entered | `add_passenger` (is_primary=False) | POST | `/api/tab-plc/policies/{id}/passengers` |
| Email entered | `set_policy_email` | PUT | `/api/tab-plc/policies/{id}/email` |
| Departure airport search (3+ chars typed) | `search_airports` | GET | `/api/v2/airports/search?search={query}` |
| Arrival airport search (3+ chars typed) | `search_airports` | GET | `/api/v2/airports/search?search={query}` |
| Summary confirmed | `submit_itinerary` | PUT | `/api/tab-plc/policies/{id}/itinerary` |
| After itinerary submitted | `fetch_quotes` | GET | `/api/tab-plc/policies/{id}/quotes` |
| Cover option selected | `select_cover` | PUT | `/api/tab-plc/policies/{id}/selected-cover` |

#### How airport search works now:
1. User types 3+ characters (e.g. "lag", "abu", "lon")
2. Bot calls `search_airports` — live results returned from Ipurvey
3. Results shown as a WhatsApp list with **airport name + country name** as description
4. User selects from list — airport code and name saved to session
5. Results are **cached for 5 minutes** (LRU cache, max 256 entries) so repeated searches don't hit the API again

#### Airport cache details:
- Cache key: query string (lowercased, whitespace stripped) — "Lagos", "LAGOS", " lagos" all share the same entry
- TTL: 5 minutes (`_AIRPORT_CACHE_TTL = 300`)
- Size cap: 256 entries, with LRU eviction
- Only successful API responses are cached; errors return [] without polluting the cache

---

### 2. KYC Flow
**File:** `app/services/kyc_flow_service.py`

Handles identity verification. If the user is already KYC-verified, this flow is skipped entirely.

| Conversation Step | API Called | Method | Endpoint |
|---|---|---|---|
| KYC flow starts | `check_kyc_status` | GET | `/api/tab-plc/policies/{id}/kyc-status` |
| If already verified — skips to payment | *(no API call, flow advances)* | — | — |
| ID number entered (NIN/BVN) | `create_user` | POST | `/api/tab-ums/users` |
| After user created | `link_user_to_policy` | PUT | `/api/tab-plc/policies/{id}/user-id` |
| After user linked | `initiate_kyc` | POST | `/api/tab-ums/users/{id}/kyc/initiate` |
| OTP entered | `verify_kyc_otp` | POST | `/api/tab-ums/users/{id}/kyc/verify` |
| Resend OTP requested | `resend_kyc_otp` | POST | `/api/tab-ums/users/{id}/kyc/resend-otp` |

#### create_user payload:
```json
{
  "msisdn": "+234...",
  "firstName": "...",
  "lastName": "...",
  "email": "...",
  "marketingConsent": true,
  "policyUpdatesConsent": true,
  "payoutAlertsConsent": true,
  "kycConsent": true,
  "nationalityCountryCode": "NG",
  "identityType": "BVN" or "NIN",
  "identityNumber": "..."
}
```

---

### 3. Payment Flow
**File:** `app/services/payment_flow_service.py`

All fake/simulated payment logic has been removed. Payment is fully real.

| Conversation Step | API Called | Method | Endpoint |
|---|---|---|---|
| Bank selected | `create_payout_method_bank` | POST | `/api/tab-ums/users/{id}/payout-methods` |
| Wallet phone entered | `create_payout_method_wallet` | POST | `/api/tab-ums/users/{id}/payout-methods` |
| "Pay by Bank Transfer" selected | `initiate_payment` | POST | `/api/tab-plc/policies/{id}/payment/initiate` |
| "Refresh status" pressed | `get_payment_status` | GET | `/api/tab-plc/policies/{id}/payment-status` |
| Payment confirmed | `submit_policy` | POST | `/api/tab-plc/policies` |

#### How bank transfer works now:
1. `initiate_payment` is called → API returns a real payment reference, bank name, account number, account holder name
2. These **real bank details** are shown to the user (no more placeholder "Example Bank / 0123456789")
3. After user claims payment, `get_payment_status` is called to verify
4. **Only on confirmed payment** does the flow advance to policy submission
5. If payment not confirmed, user stays on the status screen

#### submit_policy payload (multipart/form-data if boarding pass attached, else JSON):
```
channel=WHATSAPP
msisdn=+234...
productId=...
draftPolicyId=...
firstName=...
lastName=...
email=...
identityType=BVN/NIN
identityNumber=...
bookingReference=...
flightNumber=...
tripType=ONE_WAY
departureAirport=LOS
arrivalAirport=ABJ
departureDateLocal=DD-MM-YYYY
departureTimeLocal=HH:MM
arrivalDateLocal=DD-MM-YYYY
arrivalTimeLocal=HH:MM
bankCode=...
accountNumber=...
accountName=...
payoutMethodId=...
boardingPass=(file, optional)
```

#### On successful submission:
- Policy reference saved to session (`session["active_policy_code"]`)
- Policy ID saved (`session["active_policy_id"]`)
- Policy status set to `"submitted"`
- Policy cache invalidated so next "Check Policy" fetch gets fresh data

#### On failed submission:
- User shown error message with actual API error text
- Retry button offered — brings user back to submission without losing data

---

### 4. Check Policy Flow
**File:** `app/services/check_policy_flow_service.py`

Replaced all hardcoded `DEMO_POLICIES` with live API data.

| Conversation Step | API Called | Method | Endpoint |
|---|---|---|---|
| Flow starts | `fetch_policies_by_msisdn` | GET | `/api/tab-plc/policies/by-msisdn/{msisdn}` |
| User searches by policy code | `get_policy_by_code` | GET | `/api/tab-plc/policies/search/user?policyCode=...` |
| Policy document requested | `get_policy_document_url` | GET | `/api/tab-plc/policies/{code}/document` |

#### Policy cache (stale-while-revalidate):
- First flow start: fetches live from API, stores in MongoDB session under `policy_cache`
- Subsequent starts within 5 minutes: serves cached data instantly, schedules background refresh
- After successful boarding pass upload: cache is invalidated
- After policy submission: cache is invalidated
- Cache TTL configurable via `POLICY_CACHE_TTL_SECONDS` environment variable (default: 300 seconds)

#### Policy field normalisation (`_normalize_policy`):
All API responses are normalised into a consistent internal format. The function handles multiple possible field name variants (e.g. `policyCode` / `policyNumber` / `policyReference` all map to `ref`). Confirmed API field names:
- `policyCode` — human-readable reference (e.g. "TAB-001234")
- `productName` — plan label
- `status` — policy state
- `flightNumber`, `airlineName`
- `departureAirport`, `arrivalAirport` — IATA codes
- `departureDateLocal` — departure date
- `passengers` — list with `firstName`, `surname`
- `premiumAmount` — cost in NGN
- `documentUrl` / `downloadUrl` — PDF link

---

### 5. Boarding Pass Link Flow
**File:** `app/services/bp_link_flow_service.py`

Replaced `DEMO_POLICIES` with live policy data. Boarding pass upload uses real API.

| Conversation Step | API Called | Method | Endpoint |
|---|---|---|---|
| Flow starts | `fetch_policies_by_msisdn` | GET | `/api/tab-plc/policies/by-msisdn/{msisdn}` |
| Eligibility check flow starts | `fetch_policies_by_msisdn` | GET | `/api/tab-plc/policies/by-msisdn/{msisdn}` |
| Eligibility checked | `check_eligibility` | GET | `/api/tab-plc/policies/{id}/checkEligibility` |
| Boarding pass file received | `get_policy_by_code` | search | (to resolve passenger ID) |
| After resolving passenger ID | `upload_boarding_pass` | POST | `/api/tab-plc/policies/{id}/passengers/{pid}/boarding-pass` |

#### Boarding pass upload process:
1. User sends image/PDF via WhatsApp
2. Bot downloads the file from WhatsApp Media API
3. Bot fetches the policy from Ipurvey to resolve `passenger_id`
4. File is uploaded as multipart/form-data with `flightId`
5. On success: policy cache is invalidated; confirmation shown
6. Supported formats: JPG, PNG, WebP, PDF

---

### 6. Update Details Flow
**File:** `app/services/update_details_flow_service.py`

When a user updates their details, the changes are pushed to the API in real-time.

| What User Updates | API Called | Method | Endpoint |
|---|---|---|---|
| Name (primary traveler) | `update_user` | PATCH | `/api/tab-ums/users/{id}` |
| Name (any passenger) | `update_passenger` | PUT | `/api/tab-plc/policies/{id}/passengers/{pid}` |
| Email | `set_policy_email` | PUT | `/api/tab-plc/policies/{id}/email` |
| Bank account | `create_payout_method_bank` | POST | `/api/tab-ums/users/{id}/payout-methods` |
| Wallet | `create_payout_method_wallet` | POST | `/api/tab-ums/users/{id}/payout-methods` |

---

## Complete API Reference

All 31 API functions integrated:

| # | Function | Method | Endpoint | Used In |
|---|---|---|---|---|
| 1 | `search_airports` | GET | `/api/v2/airports/search?search={q}` | Buy Cover |
| 2 | `check_user_exists` | GET | `/api/tab-ums/users/{msisdn}` | Buy Cover |
| 3 | `create_user` | POST | `/api/tab-ums/users` | KYC |
| 4 | `update_user` | PATCH | `/api/tab-ums/users/{id}` | Update Details |
| 5 | `get_payout_methods` | GET | `/api/tab-ums/users/{id}/payout-methods` | Payment |
| 6 | `create_payout_method_bank` | POST | `/api/tab-ums/users/{id}/payout-methods` | Payment, Update Details |
| 7 | `create_payout_method_wallet` | POST | `/api/tab-ums/users/{id}/payout-methods` | Payment, Update Details |
| 8 | `initiate_kyc` | POST | `/api/tab-ums/users/{id}/kyc/initiate` | KYC |
| 9 | `verify_kyc_otp` | POST | `/api/tab-ums/users/{id}/kyc/verify` | KYC |
| 10 | `resend_kyc_otp` | POST | `/api/tab-ums/users/{id}/kyc/resend-otp` | KYC |
| 11 | `create_draft_policy` | POST | `/api/tab-plc/policies/draft` | Buy Cover |
| 12 | `resume_draft_policy` | GET | `/api/tab-plc/policies/draft/resume` | Buy Cover |
| 13 | `set_traveler_count` | PUT | `/api/tab-plc/policies/{id}/traveler-count` | Buy Cover |
| 14 | `add_passenger` | POST | `/api/tab-plc/policies/{id}/passengers` | Buy Cover |
| 15 | `update_passenger` | PUT | `/api/tab-plc/policies/{id}/passengers/{pid}` | Update Details |
| 16 | `set_policy_email` | PUT | `/api/tab-plc/policies/{id}/email` | Buy Cover, Update Details |
| 17 | `submit_itinerary` | PUT | `/api/tab-plc/policies/{id}/itinerary` | Buy Cover |
| 18 | `fetch_quotes` | GET | `/api/tab-plc/policies/{id}/quotes` | Buy Cover |
| 19 | `select_cover` | PUT | `/api/tab-plc/policies/{id}/selected-cover` | Buy Cover |
| 20 | `link_user_to_policy` | PUT | `/api/tab-plc/policies/{id}/user-id` | KYC |
| 21 | `check_kyc_status` | GET | `/api/tab-plc/policies/{id}/kyc-status` | KYC |
| 22 | `search_policies` | GET | `/api/tab-plc/policies/search/user` | Check Policy |
| 23 | `get_policy_by_code` | GET | `/api/tab-plc/policies/search/user?policyCode=...` | Check Policy, BP Link |
| 24 | `get_policy_document_url` | GET | `/api/tab-plc/policies/{code}/document` | Check Policy |
| 25 | `check_eligibility` | GET | `/api/tab-plc/policies/{id}/checkEligibility` | BP Link |
| 26 | `initiate_payment` | POST | `/api/tab-plc/policies/{id}/payment/initiate` | Payment |
| 27 | `get_payment_status` | GET | `/api/tab-plc/policies/{id}/payment-status` | Payment |
| 28 | `submit_policy` | POST | `/api/tab-plc/policies` | Payment |
| 29 | `upload_boarding_pass` | POST | `/api/tab-plc/policies/{id}/passengers/{pid}/boarding-pass` | BP Link |
| 30 | `poll_boarding_pass_status` | GET | `/api/tab-plc/policies/{id}/passengers/{pid}/boarding-pass/status` | BP Link |
| 31 | `fetch_policies_by_msisdn` | GET | `/api/tab-plc/policies/by-msisdn/{msisdn}` | Check Policy, BP Link |

---

## Infrastructure Changes

### New Files Created:
| File | Purpose |
|---|---|
| `app/services/ipurvey_service.py` | Main API client — all 30 API functions |
| `app/services/ipurvey_api.py` | Policy fetch + normalisation for check/BP flows |
| `app/services/policy_refresh.py` | Background stale-while-revalidate cache refresh |

### Files Modified:
| File | What Changed |
|---|---|
| `app/services/buy_cover_flow_service.py` | All API calls integrated; airport search now live |
| `app/services/kyc_flow_service.py` | create_user, link_user, initiate_kyc, verify/resend OTP |
| `app/services/payment_flow_service.py` | Real payment initiation, status check, policy submission |
| `app/services/bp_link_flow_service.py` | Live policy fetch, real boarding pass upload |
| `app/services/check_policy_flow_service.py` | Live policy fetch, removed DEMO_POLICIES |
| `app/services/update_details_flow_service.py` | update_user, update_passenger, payout methods |
| `app/services/session_service.py` | Policy cache (get/set/invalidate/stale-while-revalidate) |
| `app/core/config.py` | Added `IPURVEY_BASE_URL` config setting |

### Session Data Added:
All API IDs are stored in `session["api_data"]` during a flow:

| Key | What it stores |
|---|---|
| `policy_id` | Draft policy UUID from Ipurvey |
| `user_id` | User UUID from Ipurvey |
| `passenger_ids` | List of passenger UUIDs |
| `kyc_session_id` | KYC session ID for OTP verification |
| `quotes` | List of available cover quotes |
| `payment_id` | Payment transaction ID |
| `payout_method_id` | Saved payout method ID |
| `flight_id` | Flight leg ID |

Policy cache stored in `session["policy_cache"]`:
```json
{
  "policies": [...],
  "cached_at": "2026-04-28T10:00:00"
}
```

---

## What Changed vs. Before

| Feature | Before | After |
|---|---|---|
| Airport list | 10 hardcoded Nigerian airports | Live search from Ipurvey API — any airport worldwide |
| Airport display | Just airport code | Airport name + country name in list |
| Airport speed | Instant (static) | First search: live API call; repeat: instant from cache |
| Policy list (Check Policy) | 3 fake DEMO_POLICIES | Real policies fetched by user's WhatsApp number |
| Policy list (Submit Boarding Pass) | Same fake DEMO_POLICIES | Real policies from API |
| Policy details display | Hardcoded fake data | Real fields mapped from API response |
| KYC | Simulated — always passed | Real user creation + OTP verification via Ipurvey |
| Payment reference | "TA" + 6 random digits | Real reference from `initiate_payment` API |
| Bank details shown | "Example Bank / 0123456789" | Real account name, number, bank from API |
| Payment confirmation | User clicks "I have paid" — bypass | `get_payment_status` API must return confirmed status |
| Policy submission | Fake success message | Real POST to `/api/tab-plc/policies` with all data |
| Policy reference | Randomly generated string | Real reference from Ipurvey submission response |
| Boarding pass upload | Simulated (no file actually sent) | Real download from WhatsApp + upload to Ipurvey |
| Update name | Saved locally only | PATCH to `/api/tab-ums/users/{id}` + PUT passenger |
| Update bank | Saved locally only | POST to payout-methods API |
| Error handling | Silent failures | All errors logged; user shown actionable retry messages |

---

## UX Changes

Only 3 conversation steps changed. All other steps, messages, button labels, and flow sequences are identical to before.

### 1. Airport Search — Step Behaviour Changed
- **Before:** Static list of 10 airports shown immediately
- **After:** Bot asks user to type 3+ characters, then shows live results with country name
- **Why:** Necessary to support live search — a list of thousands of airports cannot be shown upfront

### 2. Payment Confirmation — Bypass Removed
- **Before:** User could click "I have paid" to advance regardless of actual payment status
- **After:** `get_payment_status` API must confirm payment before flow advances
- **Why:** The bypass was only possible with fake/simulated payment; real payment requires real confirmation

### 3. Card/Wallet/USSD — Removed Fake Flows
- **Before:** Fake multi-step flows for card number/expiry/CVV/OTP, wallet selection, USSD
- **After:** These options show "not available" message and redirect to payment summary
- **Why:** These were entirely simulated; only Bank Transfer is wired to a real API endpoint

---

## Error Handling Strategy

All API calls follow this pattern:
- Wrapped in `try/except` — no API failure can crash the bot
- On failure: returns `None` / `False` / `[]` depending on return type
- Failures logged with `[ipurvey]` prefix and HTTP status code + first 200 chars of response body
- Critical failures (payment, submission) show the user a friendly error with retry option
- Non-critical failures (caching, update) fail silently so the flow continues

### Debug Logging:
Set `LOG_LEVEL=DEBUG` in environment to enable:
- Raw API response field names (PII masked — names replaced with `***`)
- Top-level response shape for policy fetches
- Airport cache hits/misses

---

*Document generated April 2026. Base URL: `https://dev-ilekun-ipv.ipurvey.com`*
