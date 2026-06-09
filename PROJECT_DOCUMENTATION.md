# TravelAssist WhatsApp Bot — Project Documentation

**Last updated:** April 2026
**Stack:** FastAPI · Python 3.11 · MongoDB Atlas (Motor) · Meta WhatsApp Cloud API v22.0

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Directory Structure](#2-directory-structure)
3. [Complete Bot Flow](#3-complete-bot-flow)
4. [Service File Breakdown](#4-service-file-breakdown)
5. [Static / Hardcoded Data](#5-static--hardcoded-data)
6. [Session Management](#6-session-management)
7. [WhatsApp Message Handling](#7-whatsapp-message-handling)
8. [All Flow States](#8-all-flow-states)
9. [Integration Points](#9-integration-points)
10. [Environment & Deployment](#10-environment--deployment)

---

## 1. Project Overview

**TravelAssist** is a WhatsApp-based travel disruption insurance bot. It allows users to:

- **Buy travel cover** (Local Travel Basic / Local Travel Premium) for themselves or a group
- **Verify identity** via BVN or NIN (KYC)
- **Complete payment** via bank transfer, card, wallet, or USSD
- **Upload a boarding pass** and link it to an active policy
- **Check policies** by phone number, policy number, or flight number
- **Update payout details** (bank account, wallet, name, email, KYC)
- **Get help** on any topic through a structured help menu

The bot is entirely **conversational** — no web app or portal. All interactions happen inside a WhatsApp chat.

### Architecture at a Glance

```
User WhatsApp ──► Meta Graph API ──► /api/v1/webhook (FastAPI)
                                              │
                  ┌───────────────────────────┤
                  │                           │
             Session (MongoDB)          Flow Services
                  │                           │
             Contact / Message          WhatsApp Service
             persistence (MongoDB)      ──► Meta Graph API
                  │
             LLM Service (optional)
             ──► External LLM API
```

### Key Design Rules

- Every bot message is followed by a **separate utility bar** message:
  `0 ↩️ Back  |  9 🆘 Help  |  00 🏠 Main menu / 99 ❌ Cancel/Exit`
- All flows are **session-driven** — state lives in MongoDB, keyed by `wa_id`
- Flows are mutually exclusive — only one flow can be `active` at a time
- The main menu shows **two button groups** (max 3 buttons each, WhatsApp limit)
- All list row titles are capped at **24 characters** (WhatsApp hard limit)
- All button titles are capped at **20 characters** (WhatsApp hard limit)

---

## 2. Directory Structure

```
/
├── main.py                          # Entry point: imports app from app.main
├── pyproject.toml                   # Poetry dependency manifest
├── requirements.txt                 # pip-compatible requirements
├── Dockerfile                       # Production container build
├── .dockerignore
├── env-stage                        # Staging environment file (not committed to prod)
├── env-prod                         # Production environment file
├── replit.md                        # Living architecture reference (auto-loaded by agent)
├── PROJECT_DOCUMENTATION.md         # This file
├── Developer_Guide_Architecture_Documentation.md  # Legacy guide (pre-refactor)
│
└── app/
    ├── main.py                      # FastAPI app factory + lifespan handler
    ├── __init__.py
    │
    ├── core/
    │   ├── config.py                # pydantic-settings Settings class + get_settings()
    │   ├── database.py              # Motor MongoDB connect/disconnect + index setup
    │   └── __init__.py
    │
    ├── api/
    │   └── v1/
    │       ├── router.py            # Mounts health + webhook routers under /api/v1
    │       └── endpoints/
    │           ├── health.py        # GET /api/v1/health
    │           └── webhook.py       # GET+POST /api/v1/webhook (all bot logic entry)
    │
    ├── models/
    │   ├── webhook.py               # Pydantic models for Meta webhook payloads
    │   └── __init__.py
    │
    ├── services/
    │   ├── auto_reply_service.py    # Welcome screen, main menu, static auto-replies
    │   ├── buy_cover_flow_service.py  # Policy purchase flow (trip details → cover select)
    │   ├── kyc_flow_service.py      # KYC identity verification flow (BVN / NIN)
    │   ├── payment_flow_service.py  # Payment flow (payout setup → pay method → confirm)
    │   ├── bp_link_flow_service.py  # Boarding pass upload / eligibility check flow
    │   ├── check_policy_flow_service.py  # Policy lookup & management flow
    │   ├── help_flow_service.py     # Structured help topics flow
    │   ├── update_details_flow_service.py  # Update name/email/bank/wallet/KYC flow
    │   ├── contact_service.py       # Upsert & retrieve contact records
    │   ├── message_service.py       # Save inbound messages to MongoDB
    │   ├── session_service.py       # Get/save/build user conversation sessions
    │   ├── whatsapp_service.py      # Send messages via Meta Graph API + media upload
    │   ├── llm_service.py           # LLM generic Q&A + field extraction calls
    │   ├── llm_log_service.py       # Log LLM requests/responses to MongoDB
    │   ├── Image.jpeg               # Welcome header image (uploaded to WhatsApp on start)
    │   └── __init__.py
    │
    └── utils/
        └── __init__.py
```

---

## 3. Complete Bot Flow

### 3.1 Welcome / Main Menu

Triggered when: user sends a greeting, types `hi/hello/start/menu`, sends a cancel outside a flow, or types `00`/`#menu`/`main menu`.

**Messages sent (in order):**
1. Welcome image (`app/services/Image.jpeg`) — uploaded once at startup and cached in memory
2. Text: `👋 Welcome to TravelAssist` + description
3. Interactive buttons (Group 1): ✈️ Buy Cover | 🛫 Boarding Pass | 📋 Check My Policy
4. Interactive buttons (Group 2): 🔍 Check Eligibility | ✏️ Update Details | 🆘 Help
5. Utility bar text

### 3.2 Buy Cover Flow (`buy_cover_flow_service.py`)

**Purpose:** Collect trip details, present cover options, confirm.

| Step | State Key | Input Type | Description |
|------|-----------|------------|-------------|
| 1 | `buy_cover_who` | Buttons | Just me / Me & Others |
| 2a | `buy_cover_traveler_count` | List | Number of additional travelers (1–4) |
| 2b | `buy_cover_name` | Text | Lead traveler's full name |
| 3 | `buy_cover_other_name` | Text | Additional traveler names (loops) |
| 4 | `buy_cover_email` | Text | Email address |
| 5 | `buy_cover_trip_type` | Buttons | One-way / Return |
| 6 | `buy_cover_booking_ref` | Text | Booking reference |
| 7 | `buy_cover_flight_num` | Text | Flight number |
| 8 | `buy_cover_date` | Text | Departure date |
| 9 | `buy_cover_depart_time` | Text | Departure time |
| 10 | `buy_cover_depart_airport_pick` | List | Departure airport (from hardcoded AIRPORTS) |
| 11 | `buy_cover_arrive_time` | Text | Arrival time |
| 12 | `buy_cover_arrive_airport_pick` | List | Arrival airport (from hardcoded AIRPORTS) |
| 13 | `buy_cover_airline` | Text | Airline name |
| 14 | `buy_cover_summary` | List | Review trip details — Confirm / Edit |
| 15 | `buy_cover_next_steps` | List | Continue to KYC / Ask a question / Cancel |
| 16 | `buy_cover_ask_question` | Text | Free-text question (currently captured, no LLM response wired) |
| 17 | `buy_cover_cancel_confirm` | List | Confirm cancellation |

On "Continue to KYC": calls `start_kyc_flow()` — deactivates buy_cover_flow.

### 3.3 KYC Flow (`kyc_flow_service.py`)

**Purpose:** Collect and (mock-)verify user's BVN or NIN.

| Step | State Key | Input Type | Description |
|------|-----------|------------|-------------|
| 1 | `kyc_intro` | List | Select BVN / NIN / Help |
| 2 | `kyc_consent` | Buttons | Consent to data use |
| 3 | `kyc_id_input` | Text | 11-digit BVN or NIN |
| 4a | `kyc_verified` | List | Verification passed → Continue to payment / Review trip / Main menu |
| 4b | `kyc_failed` | List | Verification failed → Retry BVN / Try NIN / Get help |
| 5 | `kyc_help` | List | Help text → Verify with BVN / NIN / Speak to agent |

**Verification logic (current):** If the input is 11 digits → verified. Any other input → failed. No external API call is currently wired.

On "Continue to payment": calls `start_payment_flow()` — deactivates kyc_flow.

### 3.4 Payment Flow (`payment_flow_service.py`)

**Purpose:** Collect payout bank/wallet details, then collect payment method.

| Step | State Key | Input Type | Description |
|------|-----------|------------|-------------|
| 1 | `pay_payout_options` | List | Payout via Bank transfer / Wallet |
| 2a | `pay_acct_number` | Text | 10-digit bank account number |
| 2b | `pay_bank_search` | Text | 2+ char bank name search |
| 2c | `pay_bank_select` | List | Paginated bank list (8 per page) |
| 2d | `pay_wallet_payout_select` | List | 9PSB / SmartCash / OPay |
| 2e | `pay_wallet_payout_phone` | Text | Wallet phone number |
| 3 | `pay_method_choice` | List | Bank transfer / Card / Wallet / USSD |
| 4a | `pay_m_bank_pending` | List | Transfer to account, confirm payment |
| 4b | `pay_m_card_number` | Text | 16-digit card number |
| `pay_m_card_expiry` | | Text | MM/YY expiry |
| `pay_m_card_cvv` | | Text | 3-digit CVV |
| `pay_m_card_otp` | | Text | 6-digit OTP (simulated) |
| 4c | `pay_m_wallet_select` | List | Wallet provider |
| `pay_m_wallet_phone` | | Text | Wallet phone number |
| `pay_m_wallet_otp` | | Text | 6-digit OTP (simulated) |
| 4d | `pay_m_ussd_confirm` | List | Dial USSD code, confirm done |
| 5 | `pay_success` | List | View policy / Upload boarding pass / Main menu / Buy new |

**Note:** All payment methods are currently **simulated** — no real payment gateway is integrated. Policy numbers, transaction references, and OTPs are randomly generated.

### 3.5 Boarding Pass Flow (`bp_link_flow_service.py`)

**Purpose:** Upload a boarding pass image/PDF and link it to an active policy.

| Step | State Key | Input Type | Description |
|------|-----------|------------|-------------|
| 1 | `bp_choose` | List | Upload boarding pass / Help |
| 2 | `bp_policy` | List | Select policy from DEMO_POLICIES list |
| 3a | `bp_awaiting_doc` | Media | Awaiting image or PDF upload |
| 4a | `bp_upload_done` | List | Upload confirmed — main menu / cancel |
| 3b | `bp_link_confirm` | List | Confirm linking boarding pass to policy |
| 4b | `bp_linked_done` | List | Linked — Check eligibility / View policy / Main menu |
| 5 | `bp_policy_card` | List | Policy detail card → home |
| 6 | `bp_eligibility_result` | List | Eligibility check result → Confirm payout / Upload first / Home |
| 7 | `bp_payout_done` | List | Payout initiated — view policy / home |

**Note:** `DEMO_POLICIES` is hardcoded static data. The "Check Eligibility" button on the main menu (`check_eligibility`) triggers `start_eligibility_check_flow()` which skips the `bp_choose` step and goes straight to policy selection.

### 3.6 Check Policy Flow (`check_policy_flow_service.py`)

**Purpose:** Let users look up and view their policies.

| Step | State Key | Input Type | Description |
|------|-----------|------------|-------------|
| 1 | `pol_menu` | List | By phone / Policy number / Flight number |
| 2a | `pol_phone_list` | List | List of DEMO_POLICIES — select one |
| 2b | `pol_ref_input` | Text | Enter policy number (e.g. TA-238491) |
| 2c | `pol_flight_input` | Text | Enter flight number |
| `pol_date_input` | | Text | Narrow by date if multiple matches |
| 3 | `pol_detail` | List | Policy card — Download doc / Manage alerts / Help / All policies |
| 4a | `pol_download` | List | CTA button to download PDF doc / Upload BP / Link boarding / Manage alerts |
| 4b | `pol_alerts_manage` | List | Keep alerts / Turn off alerts / Back |
| `pol_alerts_kept` | | List | Alerts confirmed active |
| `pol_alerts_off_confirm` | | List | Confirm turning off |
| `pol_alerts_off_done` | | List | Alerts off — turn back on / back |
| 5 | `pol_link_confirm` | List | Confirm linking boarding pass |
| 6 | `pol_linked` | List | Linked — check eligibility / back to detail / all policies |
| 7 | `pol_eligibility` | List | Eligibility result → Confirm payout / Upload first |
| 8 | `pol_payout_done` | List | Payout initiated |
| `pol_all_list` | | List | All policies list |

**Note:** `DEMO_POLICIES` is hardcoded static data. No real API call is made.

### 3.7 Help Flow (`help_flow_service.py`)

**Purpose:** Provide structured help by topic.

| Step | State Key | Input Type | Description |
|------|-----------|------------|-------------|
| 1 | `hlp_menu` | List | Select topic: Buying cover / KYC / Payment / Policy / Boarding pass / Claim / Agent |
| 2 | `hlp_topic` | List | Topic-specific help text + action button + nav (Back / Home) |
| 3 | `hlp_agent_wait` | List | Agent contact details + nav |

Actions from topic screens bridge into other flows (`start_buy_cover_flow`, `start_kyc_flow`, `start_payment_flow`, `start_bp_link_flow`).

### 3.8 Update Details Flow (`update_details_flow_service.py`)

**Purpose:** Allow users to update their profile and payout details.

| Step | State Key | Input Type | Description |
|------|-----------|------------|-------------|
| 1 | `upd_menu` | List | Name / Email / Bank payout / Wallet payout / KYC |
| 2a | `upd_name_who` | List | Which traveler (if multiple in session) |
| `upd_name_input` | | Text | New full name |
| 2b | `upd_email_input` | Text | New email address |
| 2c | `upd_bank_acct` | Text | 10-digit account number |
| `upd_bank_search` | | Text | 2+ char bank name search |
| `upd_bank_select` | | List | Paginated bank selection (8 per page) |
| 2d | `upd_wallet_select` | List | 9PSB / SmartCash / OPay |
| `upd_wallet_phone` | | Text | Wallet phone number |
| 2e | (redirect) | — | KYC option: resets flow and calls `start_kyc_flow()` |
| 3 | `upd_done` | Buttons | Success — Update another / Main menu |

**Note:** Updates are stored only in the session's `temp_data`. No persistent profile update API call is made.

---

## 4. Service File Breakdown

### `app/core/config.py`

- Uses `pydantic-settings` `BaseSettings` to load config from environment files
- Selects `env-stage` unless `DEBUG=FALSE`, in which case it loads `env-prod`
- Cached with `@lru_cache()` via `get_settings()`
- Key settings: `WHATSAPP_API_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `MONGODB_URI`, `MONGODB_DB_NAME`, `LLM_API_URL`, `META_API_VERSION`, `META_API_BASE_URL`

### `app/core/database.py`

- `connect_to_mongodb()` — opens Motor async client, pings, sets up all indexes
- `close_mongodb_connection()` — gracefully closes on app shutdown
- `get_database()` — returns the Motor database or `None` if not connected
- Pool: `maxPoolSize=50`, `minPoolSize=10`, `serverSelectionTimeoutMS=5000`
- Index setup delegated to each service's `ensure_indexes()` function

### `app/main.py`

- `create_app()` — builds and returns the FastAPI application
- `lifespan()` — async context manager:
  - Startup: connects MongoDB, pre-uploads the welcome image
  - Shutdown: closes MongoDB connection
- Mounts all routes under `/api/v1`
- CORS middleware allows all origins (configurable via `APP_BASE_URL`)

### `app/api/v1/endpoints/webhook.py`

This is the **brain** of the bot. All inbound WhatsApp messages pass through here.

**Key responsibilities:**
- `GET /api/v1/webhook` — Meta webhook verification handshake
- `POST /api/v1/webhook` — receives all message and status events
- `_process_change()` — per-message routing logic
- `_handle_welcome_button()` — routes each `WELCOME_BUTTON_IDS` to the correct flow
- `_handle_llm_reply()` — calls LLM for generic conversation; falls back to auto-reply
- `_get_welcome_button_id()` — extracts button/list reply ID and checks against `WELCOME_BUTTON_IDS`

**`WELCOME_BUTTON_IDS` (exhaustive list):**
```python
{
  "welcome_purchase_policy", "welcome_submit_boarding", "welcome_get_support",
  "buy_cover", "check_policy", "check_eligibility", "update_details",
  "boarding_pass", "help", "restart_buy", "go_main",
}
```

### `app/services/auto_reply_service.py`

- `send_welcome_message()` — sends image + text + 2 button groups + utility bar
- `send_main_menu()` — alias for `send_welcome_message()`
- `handle_auto_reply()` — static fallback: greeting → welcome, thanks/bye/help → short reply, media → media ack, default → DEFAULT_REPLY
- `is_greeting(text)` — regex match against: `hi|hello|hey|assalam|salam|aoa|start|menu`

### `app/services/buy_cover_flow_service.py`

- `is_in_buy_cover_flow(session)` — checks `temp_data.buy_cover_flow.active`
- `start_buy_cover_flow()` — initialises flow, sends "who is covered" buttons
- `handle_buy_cover_flow()` — 17-step state machine

### `app/services/kyc_flow_service.py`

- `is_in_kyc_flow(session)`
- `start_kyc_flow()` — initialises flow, deactivates buy_cover_flow, sends verification method list
- `handle_kyc_flow()` — 5-step state machine; `_mask_id()` shows only last 3 digits

### `app/services/payment_flow_service.py`

- `is_in_payment_flow(session)`
- `start_payment_flow()` — reads buy_cover flow data to pre-fill summary; deactivates kyc_flow
- `handle_payment_flow()` — multi-branch state machine for 4 payment methods
- Helper generators: `_gen_ref()`, `_gen_policy()`, `_gen_otp()`, `_gen_ussd()`
- Bank filtering: `_filter_banks(query)` — case-insensitive local filter over `NIGERIAN_BANKS`
- Pagination: 8 banks per page, prev/next navigation rows in list

### `app/services/bp_link_flow_service.py`

- `is_in_bp_link_flow(session)`
- `start_bp_link_flow()` — upload boarding pass entry (step `bp_choose`)
- `start_eligibility_check_flow()` — eligibility shortcut, skips choose step
- `handle_bp_link_flow()` — handles both upload path and link path; accepts `image` and `document` message types

### `app/services/check_policy_flow_service.py`

- `is_in_check_policy_flow(session)`
- `start_check_policy_flow()` — entry: find by phone / policy number / flight
- `handle_check_policy_flow()` — 14+ step state machine
- `_send_cta_document()` — sends a CTA URL button for downloading policy PDF

### `app/services/help_flow_service.py`

- `is_in_help_flow(session)`
- `start_help_flow()` — shows 7-topic help menu
- `handle_help_flow()` — 3-step state machine; bridges to other flows via action IDs

### `app/services/update_details_flow_service.py`

- `is_in_update_details_flow(session)`
- `start_update_details_flow()` — 5-option update menu
- `handle_update_details_flow()` — handles name/email/bank/wallet/KYC paths
- Bank search and pagination mirrors `payment_flow_service.py`

### `app/services/session_service.py`

- `build_default_session()` — creates a blank session dict (not persisted)
- `get_session(user_id)` — MongoDB find; strips `_id`, `created_at`, `updated_at`
- `save_session(session)` — upsert by `user_id`; sets `updated_at`; `created_at` only on insert
- Collections: `sessions` — unique index on `user_id`

### `app/services/whatsapp_service.py`

- `send_text_message()` — wraps `send_whatsapp_payload()` for text type
- `send_whatsapp_payload()` — POSTs to Meta Graph API; 3 retries with backoff (1/2/4 s) on 429/502/503/504; strips leading `+` from phone numbers; saves outbound message to MongoDB
- `upload_media_to_whatsapp()` — POSTs multipart to `/media` endpoint
- `get_welcome_image_media_id()` — uploads `Image.jpeg` once; caches result in module-level variable `_welcome_image_media_id`
- `download_whatsapp_media()` — fetches media metadata then downloads bytes; 3-attempt retry
- `_save_outbound_message()` — saves to `messages` collection with `direction=outbound`

### `app/services/llm_service.py`

- `call_route()` — POSTs to `LLM_API_URL/api/v1/route`; this is the only HTTP LLM endpoint used by the bot
- `call_generic()`, `call_extract()`, and `call_policy_flow_validate()` are compatibility wrappers that all dispatch through `call_route()`
- Timeout: 120 seconds for all LLM route-backed calls

### `app/services/contact_service.py`

- `upsert_contact()` — upsert by `wa_id`; updates `profile_name`, `phone_number_id`, `business_phone`, optionally increments `message_count`
- `ensure_indexes()` — unique index on `wa_id`; index on `created_at`

### `app/services/message_service.py`

- `save_inbound_message()` — saves to `messages` collection with `direction=inbound`; deduplication via `message_id`; returns `{"is_new": True/False}`
- `ensure_indexes()` — index on `message_id`, `contact_wa_id`, `created_at`

### `app/services/llm_log_service.py`

- `save_llm_log()` — saves each LLM call + response to `llm_logs` collection
- `ensure_indexes()` — index on `contact_wa_id`, `created_at`

---

## 5. Static / Hardcoded Data

These values are embedded directly in service files and must be updated in code if they change.

### Airports (`buy_cover_flow_service.py`)

```python
AIRPORTS = [
    ("LOS", "Lagos Murtala Muhammed"),
    ("ABV", "Abuja Nnamdi Azikiwe"),
    ("PHC", "Port Harcourt Intl"),
    ("KAN", "Kano Mallam Aminu"),
    ("ENU", "Enugu Akanu Ibiam"),
    ("ILR", "Ilorin"),
    ("CBQ", "Calabar Ekpo Intl"),
    ("SKO", "Sokoto"),
    ("YOL", "Yola"),
    ("QOW", "Owerri Sam Mbakwe"),
]
```

### Cover Products (`buy_cover_flow_service.py`)

```
Local Travel Basic   — ₦2,500 — Tangerine Insurance, single trip
Local Travel Premium — ₦3,500 — Tangerine Insurance, multi trip (POPULAR)
```

### Cover Prices (`payment_flow_service.py`)

```python
COVER_PRICES = {"local_basic": 2500, "local_premium": 3500}
```

### Nigerian Banks (`payment_flow_service.py` and `update_details_flow_service.py`)

33 banks, alphabetically sorted. Used for local bank-name search and pagination. Both files contain the same list and must be kept in sync manually.

### Wallet Providers (`payment_flow_service.py`)

```python
WALLET_OPTIONS = [9PSB, SmartCash, OPay]
```

### Demo Policies — Boarding Pass (`bp_link_flow_service.py`)

```python
DEMO_POLICIES = [
    {"id": "pol_ltp", "name": "Local Travel Premium", "ref": "LTP-20240412",
     "airline": "Air Peace", "flight": "P47123", ...},
    {"id": "pol_ltb", "name": "Local Travel Basic", "ref": "LTB-20240308",
     "airline": "Arik Air", "flight": "W3401", ...},
]
```
**Status:** Hardcoded demo data. Must be replaced with real API call to `/api/tab-plc/policies/by-msisdn/{msisdn}`.

### Demo Policies — Check Policy (`check_policy_flow_service.py`)

```python
DEMO_POLICIES = [
    {"ref": "TA-238491", "name": "Local Travel Premium", "price": "₦14,500",
     "doc_url": "https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/TA-238491/document", ...},
    {"ref": "TA-119823", "name": "Local Travel Basic", "price": "₦7,200", ...},
]
```
**Status:** Hardcoded demo data. Must be replaced with real API call.

### Welcome Text and Button Labels (`auto_reply_service.py`)

```python
WELCOME_TEXT = "👋 *Welcome to TravelAssist*\n..."

MENU_GROUP1_BUTTONS = [
    {"id": "buy_cover",    "title": "✈️ Buy Cover"},
    {"id": "boarding_pass","title": "🛫 Boarding Pass"},
    {"id": "check_policy", "title": "📋 Check My Policy"},
]

MENU_GROUP2_BUTTONS = [
    {"id": "check_eligibility", "title": "🔍 Check Eligibility"},
    {"id": "update_details",    "title": "✏️ Update Details"},
    {"id": "help",              "title": "🆘 Help"},
]
```

### Utility Bar (`all flow services`)

```
*Utility options:*
0 ↩️ Back  |  9 🆘 Help  |  00 🏠 Main menu
99 ❌ Cancel/Exit
```

Each service holds its own copy of this string as `_UTILITY` and sends it as a **separate second message** after every flow message.

---

## 6. Session Management

### Session Document Schema

```json
{
  "user_id": "2348012345678",
  "phone_number": "+2348012345678",
  "current_node": "N01",
  "last_node": null,
  "first_name": "Yusuf",
  "tags": [],
  "active_trip_id": null,
  "active_policy_id": null,
  "active_policy_code": null,
  "active_claim_id": null,
  "last_intent": null,
  "temp_data": {
    "buy_cover_flow": { "active": false },
    "kyc_flow":       { "active": false },
    "payment_flow":   { "active": false },
    "bp_link_flow":   { "active": false },
    "help_flow":      { "active": false },
    "check_policy_flow":    { "active": false },
    "update_details_flow":  { "active": false }
  },
  "updated_at": "...",
  "created_at": "..."
}
```

### Active Flow Object Shape

When a flow is running, its key in `temp_data` looks like:

```json
{
  "active": true,
  "step": "buy_cover_name",
  "data": {
    "who": "just_me",
    "name": "Yusuf Usman",
    "email": "yusuf@example.com"
  }
}
```

### Flow Priority / Mutual Exclusion

The webhook checks flows in this priority order (highest first):

1. `update_details_flow`
2. `check_policy_flow`
3. `help_flow`
4. `bp_link_flow`
5. `payment_flow`
6. `kyc_flow`
7. `buy_cover_flow`

Only the first active flow is routed. Flows deactivate the previous flow when they start (e.g. `kyc_flow` sets `buy_cover_flow.active = False`).

### Main Menu Reset

Typing `00`, `main menu`, `#menu`, `#main`, or `#home` clears ALL flow states and sends the welcome screen, regardless of which flow is active.

### Session Persistence

- MongoDB collection: `sessions`
- Unique index on `user_id`
- `save_session()` uses `update_one` with `upsert=True` — creates on first message
- Sessions are never deleted; they are reused and updated

---

## 7. WhatsApp Message Handling

### Inbound Message Routing Priority

```
POST /api/v1/webhook
    │
    ├─1─ Is it a WELCOME_BUTTON_IDS interactive reply?
    │        └──► _handle_welcome_button() → start the appropriate flow
    │
    ├─2─ Is it a greeting AND not in any active flow?
    │        └──► send_welcome_message()
    │
    ├─3─ Is it a cancel word AND not in any active flow?
    │        └──► send_welcome_message()
    │
    ├─4─ Is it a text-to-button match (purchase policy / submit boarding / get support)?
    │        └──► _handle_welcome_button() as if button was tapped
    │
    ├─5─ Is it a main menu trigger (00, #menu, main menu, #main, #home)?
    │        └──► clear all flows → send_main_menu()
    │
    ├─6─ is_in_update_details_flow?  → handle_update_details_flow()
    ├─7─ is_in_check_policy_flow?    → handle_check_policy_flow()
    ├─8─ is_in_help_flow?            → handle_help_flow()
    ├─9─ is_in_bp_link_flow?         → handle_bp_link_flow()
    ├─10─ is_in_payment_flow?        → handle_payment_flow()
    ├─11─ is_in_kyc_flow?            → handle_kyc_flow()
    ├─12─ is_in_buy_cover_flow?      → handle_buy_cover_flow()
    │
    ├─13─ LLM_API_URL configured?    → _handle_llm_reply() (generic Q&A)
    │         └─ on LLM failure      → handle_auto_reply() (fallback)
    │
    └─14─ (default)                  → handle_auto_reply() (static replies)
```

### Interactive Reply Extraction

Interactive messages carry either `button_reply` or `list_reply` in the `interactive` field. Each flow service extracts the `id` from the reply in the same pattern:

```python
inter = message.interactive
if isinstance(inter, dict):
    br = inter.get("button_reply") or inter.get("list_reply")
    reply_id = br.get("id") if br else None
else:
    br = getattr(inter, "button_reply", None) or getattr(inter, "list_reply", None)
    reply_id = br.get("id") if isinstance(br, dict) else getattr(br, "id", None)
```

### Supported Message Types

| Type | Handling |
|------|----------|
| `text` | Primary input for all flows |
| `interactive` (button_reply) | Button selections |
| `interactive` (list_reply) | List menu selections |
| `image` | Accepted in `bp_link_flow` (boarding pass upload) |
| `document` | Accepted in `bp_link_flow` (boarding pass upload) |
| `audio`, `video`, `sticker`, `location`, `reaction` | Routed to auto_reply: "Thanks for sending that!" |

### Deduplication

Every inbound `message_id` is checked against the `messages` collection before processing. `save_inbound_message()` returns `{"is_new": False}` for duplicates — the message is skipped if not new.

### Outbound Message Persistence

Every outbound API call (success only) is saved to `messages` with `direction=outbound`, `source` set to the calling service name, and `context.in_reply_to` set to the original inbound `message_id` where applicable.

---

## 8. All Flow States

### Complete State Reference

| Flow | State Key | Description |
|------|-----------|-------------|
| **buy_cover_flow** | `buy_cover_who` | Who is covered: just me / me & others |
| | `buy_cover_traveler_count` | Number of additional travelers |
| | `buy_cover_name` | Lead traveler name |
| | `buy_cover_other_name` | Additional traveler names (repeats) |
| | `buy_cover_email` | Email address |
| | `buy_cover_trip_type` | One-way or return |
| | `buy_cover_booking_ref` | Booking reference |
| | `buy_cover_flight_num` | Flight number |
| | `buy_cover_date` | Departure date |
| | `buy_cover_depart_time` | Departure time |
| | `buy_cover_depart_airport_pick` | Departure airport list |
| | `buy_cover_arrive_time` | Arrival time |
| | `buy_cover_arrive_airport_pick` | Arrival airport list |
| | `buy_cover_airline` | Airline name |
| | `buy_cover_summary` | Trip summary confirm/edit |
| | `buy_cover_next_steps` | Continue KYC / Ask question / Cancel |
| | `buy_cover_ask_question` | Free-text question input |
| | `buy_cover_cancel_confirm` | Cancel confirmation |
| **kyc_flow** | `kyc_intro` | Select BVN / NIN / Help |
| | `kyc_consent` | Consent to data use |
| | `kyc_id_input` | Enter 11-digit ID |
| | `kyc_verified` | ID verified — next steps |
| | `kyc_failed` | ID not verified — retry options |
| | `kyc_help` | Help — retry or speak to agent |
| **payment_flow** | `pay_payout_options` | Bank or wallet payout |
| | `pay_acct_number` | Account number input |
| | `pay_bank_search` | Bank name search input |
| | `pay_bank_select` | Paginated bank selection |
| | `pay_wallet_payout_select` | Wallet provider selection |
| | `pay_wallet_payout_phone` | Wallet phone input |
| | `pay_method_choice` | Payment method selection |
| | `pay_m_bank_pending` | Awaiting bank transfer confirmation |
| | `pay_m_card_number` | Card number input |
| | `pay_m_card_expiry` | Card expiry input |
| | `pay_m_card_cvv` | CVV input |
| | `pay_m_card_otp` | OTP input (simulated) |
| | `pay_m_wallet_select` | Wallet provider for payment |
| | `pay_m_wallet_phone` | Wallet phone for payment |
| | `pay_m_wallet_otp` | Wallet OTP (simulated) |
| | `pay_m_ussd_confirm` | USSD payment confirmation |
| | `pay_success` | Payment successful — post-actions |
| **bp_link_flow** | `bp_choose` | Upload / Help |
| | `bp_policy` | Select policy |
| | `bp_awaiting_doc` | Awaiting image/PDF |
| | `bp_upload_done` | Upload confirmed |
| | `bp_link_confirm` | Confirm linking |
| | `bp_linked_done` | Successfully linked |
| | `bp_policy_card` | View policy details |
| | `bp_eligibility_result` | Eligibility check result |
| | `bp_payout_done` | Payout initiated |
| **check_policy_flow** | `pol_menu` | Search method selection |
| | `pol_phone_list` | Policies by phone |
| | `pol_flight_input` | Flight number input |
| | `pol_date_input` | Date narrowing input |
| | `pol_ref_input` | Policy reference input |
| | `pol_detail` | Policy detail view |
| | `pol_all_list` | All policies list |
| | `pol_download` | Policy document view |
| | `pol_alerts_manage` | Manage flight alerts |
| | `pol_alerts_kept` | Alerts kept active confirm |
| | `pol_alerts_off_confirm` | Turn off alerts confirm |
| | `pol_alerts_off_done` | Alerts turned off |
| | `pol_link_confirm` | Link boarding pass confirm |
| | `pol_linked` | Boarding pass linked |
| | `pol_eligibility` | Eligibility check result |
| | `pol_payout_done` | Payout initiated |
| **help_flow** | `hlp_menu` | Help topic selection |
| | `hlp_topic` | Topic content + action |
| | `hlp_agent_wait` | Agent contact info |
| **update_details_flow** | `upd_menu` | Update option selection |
| | `upd_name_who` | Which traveler (multi) |
| | `upd_name_input` | New name input |
| | `upd_email_input` | New email input |
| | `upd_bank_acct` | Account number input |
| | `upd_bank_search` | Bank search input |
| | `upd_bank_select` | Paginated bank selection |
| | `upd_wallet_select` | Wallet provider selection |
| | `upd_wallet_phone` | Wallet phone input |
| | `upd_done` | Update successful |

---

## 9. Integration Points

### 9.1 Meta WhatsApp Business Cloud API

| Purpose | Method | Endpoint |
|---------|--------|----------|
| Send message (text/interactive/image) | POST | `https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages` |
| Upload media | POST | `https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/media` |
| Download media metadata | GET | `https://graph.facebook.com/v22.0/{media_id}` |
| Download media file | GET | `{url from metadata}` |
| Webhook verification | GET | `/api/v1/webhook?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...` |
| Webhook events | POST | `/api/v1/webhook` |

**Auth:** Bearer token (`WHATSAPP_API_TOKEN`)

**Retries:** 3 attempts, backoff 1/2/4 seconds on 429/502/503/504/timeout

### 9.2 LLM Service (Optional)

| Purpose | Method | Endpoint |
|---------|--------|----------|
| All bot LLM routing, extraction, clarification, and answer flows | POST | `{LLM_API_URL}/api/v1/route` |

**Request payload (generic):**
```json
{
  "user_id": "wa_id",
  "phone_number": "+234...",
  "message": "user's message",
  "user_name": "first name",
  "current_node": "N01"
}
```

**Response shape (generic):**
```json
{
  "success": true,
  "response": "bot reply text",
  "suggested_node": "N02",
  "detected_intent": "buy_policy",
  "confidence": 0.92,
  "tokens_used": 120,
  "processing_time_ms": 340
}
```

**Request payload (extract):**
```json
{
  "user_id": "wa_id",
  "field_name": "first_name",
  "question_asked": "Please enter your first name",
  "user_response": "My name is Yusuf Usman",
  "expected_format": "text"
}
```

**Response shape (extract):**
```json
{
  "success": true,
  "is_valid": true,
  "extracted_value": "Yusuf"
}
```

**Fallback:** If `LLM_API_URL` is not set, or if the LLM call fails, all messages route to `handle_auto_reply()`.

### 9.3 MongoDB Atlas

**Database:** `tab_wappbot_ai_stg_db` (staging) / configurable via `MONGODB_DB_NAME`

| Collection | Purpose | Key Indexes |
|------------|---------|-------------|
| `contacts` | User profiles | `wa_id` (unique), `created_at` |
| `messages` | All inbound + outbound messages | `message_id` (unique), `contact_wa_id`, `created_at` |
| `sessions` | Conversation state per user | `user_id` (unique), `updated_at` |
| `llm_logs` | LLM call audit log | `contact_wa_id`, `created_at` |

### 9.4 Ipurvey APIs (Pending Integration)

The following external APIs are referenced in `replit.md` and the developer guide but are **not yet integrated** in the current service code. They use hardcoded demo data instead:

| Purpose | Method | Endpoint |
|---------|--------|----------|
| Fetch products by country | GET | `https://dev-ilekun-ipv.ipurvey.com/api/v1/tab-pc/products/getByCountry/{COUNTRY_CODE}` |
| Fetch payment method types | GET | `https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/payout-method/types` |
| Fetch banks by country | GET | `https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/payout-method/banks?countryCode={COUNTRY_CODE}` |
| Search airports | GET | `https://dev-ilekun-ipv.ipurvey.com/api/v2/airports/search?search={CITY_OR_STATE}` |
| Fetch policies by MSISDN | GET | `https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/by-msisdn/{msisdn}` |
| Submit policy | POST | `https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies` |
| Upload boarding pass | POST | `https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/upload-boarding-pass` |

---

## 10. Environment & Deployment

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `WHATSAPP_API_TOKEN` | Yes | Meta Graph API bearer token |
| `WHATSAPP_PHONE_NUMBER_ID` | Yes | WhatsApp Business phone number ID |
| `WHATSAPP_VERIFY_TOKEN` | Yes | Webhook verification token |
| `MONGODB_URI` | Yes | MongoDB Atlas connection string (SRV format) |
| `MONGODB_DB_NAME` | No | Database name (default: `tab_wappbot_ai_stg_db`) |
| `LLM_API_URL` | No | LLM service base URL (omit to disable LLM, use auto-reply only) |
| `LLM_API_TIMEOUT` | No | LLM request timeout in seconds (default: 120) |
| `APP_BASE_URL` | No | App public URL (used for CORS and logging) |
| `APP_ENV` | No | `staging` or `production` |
| `DEBUG` | No | Set to `FALSE` to load `env-prod` instead of `env-stage` |
| `META_API_VERSION` | No | Meta API version (default: `v22.0`) |

### Environment Files

- `env-stage` — staging values, loaded by default (`DEBUG` ≠ `FALSE`)
- `env-prod` — production values, loaded when `DEBUG=FALSE`

### Running Locally

```bash
uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

On Replit, this is managed as the `Start application` workflow.

### Health Check

```
GET /api/v1/health
```

Returns:
```json
{
  "status": "healthy",
  "database": "connected"
}
```

### Webhook Registration

Meta requires the webhook URL to be registered in the Meta Developer Console:

1. Set `Callback URL` to `https://{YOUR_DOMAIN}/api/v1/webhook`
2. Set `Verify Token` to match `WHATSAPP_VERIFY_TOKEN`
3. Subscribe to `messages` field

### Dockerfile

Multi-stage not currently used — single stage. Runs:
```
uvicorn main:app --host 0.0.0.0 --port 5000
```

### API Documentation

FastAPI auto-generated docs are available at:
- Swagger UI: `/docs`
- ReDoc: `/redoc`

### Logging

All structured events are printed to stdout as JSON via `log_event()` in `webhook.py`. Format:

```json
{"timestamp": "...", "event": "MESSAGE_SAVED", "message_id": "...", "from": "...", "type": "text"}
```

Standard Python logging (`logging.getLogger(...)`) is used in all service files at `INFO` level.

---

*End of PROJECT_DOCUMENTATION.md*
