# TravelAssist WhatsApp Bot — Developer Guide & Architecture Documentation

**Project:** TravelAssist WhatsApp Bot SaaS Backend
**Version:** 0.1.0
**Last Updated:** April 2026
**Prepared for:** Technical Hiring Team & CEO
**Classification:** Internal — Confidential

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Overview](#2-system-overview)
3. [Technology Stack](#3-technology-stack)
4. [Project Structure](#4-project-structure)
5. [Application Architecture](#5-application-architecture)
6. [Configuration & Environment Management](#6-configuration--environment-management)
7. [Database Schema](#7-database-schema)
8. [Message Routing Engine](#8-message-routing-engine)
9. [Service Layer Deep Dive](#9-service-layer-deep-dive)
10. [Policy Purchase Flow](#10-policy-purchase-flow)
11. [Boarding Pass Upload Flow](#11-boarding-pass-upload-flow)
12. [External API Integrations](#12-external-api-integrations)
13. [LLM Integration](#13-llm-integration)
14. [Input Validation Rules](#14-input-validation-rules)
15. [Error Handling & Resilience](#15-error-handling--resilience)
16. [Deployment & Infrastructure](#16-deployment--infrastructure)
17. [Security Considerations](#17-security-considerations)
18. [Glossary](#18-glossary)

---

## 1. Executive Summary

TravelAssist is an enterprise-grade WhatsApp Bot backend that automates travel insurance policy creation and management through the Meta WhatsApp Business Cloud API. The system provides a conversational interface allowing end-users to:

- **Purchase travel insurance policies** via a guided, multi-step conversation
- **Upload boarding passes** as supporting documentation
- **Ask questions** answered by an integrated AI/LLM service
- **Manage policies** linked to their WhatsApp number

The backend is built with **Python/FastAPI**, uses **MongoDB** for persistence, and integrates with three external service providers: Meta (WhatsApp), iPurvey (insurance platform), and a custom LLM service for intelligent responses.

**Key Metrics:**
- ~6,300 lines of core business logic in the policy flow engine
- 27-step policy purchase flow with 5 major stages
- 5 MongoDB collections with 25+ indexes for performance
- Async-first architecture supporting high concurrency
- Docker-ready with Gunicorn/Uvicorn for production deployment

---

## 2. System Overview

### High-Level Architecture

```
┌──────────────┐     ┌──────────────────────────────────────────────────┐
│              │     │            TravelAssist Backend                  │
│   WhatsApp   │────▶│  ┌─────────┐  ┌─────────────┐  ┌────────────┐  │
│   Users      │     │  │ Webhook │──│  Routing    │──│  Services  │  │
│              │◀────│  │ Handler │  │  Engine     │  │  Layer     │  │
└──────────────┘     │  └─────────┘  └─────────────┘  └────────────┘  │
                     │       │              │               │          │
                     │  ┌────▼──────────────▼───────────────▼────┐     │
                     │  │              MongoDB                    │     │
                     │  │  contacts | messages | sessions |       │     │
                     │  │  policies | llm_logs                    │     │
                     │  └────────────────────────────────────────┘     │
                     └──────────────┬──────────────┬──────────────────┘
                                    │              │
                          ┌─────────▼──┐    ┌──────▼──────┐
                          │  iPurvey   │    │  LLM        │
                          │  Insurance │    │  Service    │
                          │  APIs      │    │             │
                          └────────────┘    └─────────────┘
```

### Data Flow Summary

1. **Inbound:** WhatsApp user sends a message → Meta Cloud API → `POST /api/v1/webhook`
2. **Processing:** Webhook handler identifies user, loads session, routes to appropriate service
3. **Business Logic:** Policy flow, boarding pass flow, LLM, or auto-reply processes the message
4. **Outbound:** Response sent via Meta Graph API → WhatsApp user
5. **Persistence:** All messages, sessions, policies, and LLM interactions are logged in MongoDB

---

## 3. Technology Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| **Runtime** | Python | 3.11 | Core language |
| **Framework** | FastAPI | 0.112.1 | Async web framework |
| **Server** | Uvicorn | 0.23.2 | ASGI server (development) |
| **Server** | Gunicorn | 25.1.0 | Process manager (production) |
| **Database** | MongoDB Atlas | — | Cloud-hosted NoSQL persistence |
| **DB Driver** | Motor | 3.7.1 | Async MongoDB driver |
| **DB Driver** | PyMongo | 4.16.0 | Synchronous MongoDB driver (indexes) |
| **HTTP Client** | HTTPX | 0.28.1 | Async HTTP for external APIs |
| **Validation** | Pydantic | 2.12.5 | Data models & request validation |
| **Config** | pydantic-settings | 2.13.1 | Environment-driven configuration |
| **Env** | python-dotenv | 1.2.1 | Environment file loading |
| **Container** | Docker | — | Containerized deployment |

---

## 4. Project Structure

```
whatsapp-bot-saas/
├── app/                              # Main application package
│   ├── __init__.py
│   ├── main.py                       # FastAPI app factory & lifespan hooks
│   │
│   ├── api/                          # API layer
│   │   └── v1/                       # Versioned API (v1)
│   │       ├── router.py             # Router aggregation
│   │       └── endpoints/
│   │           ├── health.py         # Health check endpoint
│   │           └── webhook.py        # WhatsApp webhook (GET verify + POST messages)
│   │
│   ├── core/                         # Core infrastructure
│   │   ├── __init__.py
│   │   ├── config.py                 # Pydantic Settings (env-driven)
│   │   └── database.py              # MongoDB connection lifecycle & index management
│   │
│   ├── models/                       # Data models
│   │   ├── __init__.py
│   │   └── webhook.py               # Pydantic models for WhatsApp webhook payloads
│   │
│   ├── services/                     # Business logic layer
│   │   ├── auto_reply_service.py     # Welcome message & static fallback replies
│   │   ├── contact_service.py        # Contact CRUD & deduplication
│   │   ├── llm_log_service.py        # LLM interaction audit logging
│   │   ├── llm_service.py            # LLM API client (generic + extract)
│   │   ├── message_service.py        # Message persistence & deduplication
│   │   ├── policy_flow_service.py    # Policy purchase state machine (~6,300 lines)
│   │   ├── policy_service.py         # Policy document CRUD
│   │   ├── session_service.py        # User session/state management
│   │   ├── whatsapp_service.py       # Meta Graph API client
│   │   └── Image.jpeg               # Welcome message header image
│   │
│   └── utils/                        # Shared utilities
│
├── main.py                           # Entry point (imports app.main:app)
├── Dockerfile                        # Container build configuration
├── requirements.txt                  # Python dependencies
├── env-stage                         # Staging environment variables
├── env-prod                          # Production environment variables
├── pyproject.toml                    # Project metadata
└── replit.md                         # Development environment documentation
```

### Key Design Decisions

- **Single `policy_flow_service.py`:** The policy flow logic is intentionally consolidated into one large file (~6,300 lines) to maintain a single source of truth for the state machine. All step handlers, validation, and API calls live together, preventing state management bugs from split-file coordination.
- **No ORM:** Direct MongoDB driver usage via Motor provides full control over queries and indexing without ORM overhead.
- **Lazy imports:** Heavy service imports (LLM, welcome message) use inline `from ... import` to avoid circular dependencies and reduce startup time.

---

## 5. Application Architecture

### 5.1 Application Factory Pattern

The application uses FastAPI's application factory pattern in `app/main.py`:

```python
def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        lifespan=lifespan,
    )
    application.include_router(api_router, prefix="/api/v1")
    return application

app = create_app()
```

### 5.2 Lifespan Events

The application lifespan manages:

1. **Startup:**
   - Establishes MongoDB connection
   - Ensures all database indexes
   - Pre-uploads the welcome image to WhatsApp Media API
   - Caches the `media_id` for reuse across all welcome messages

2. **Shutdown:**
   - Gracefully closes the MongoDB connection

### 5.3 API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Application info & status |
| `GET` | `/api/v1/health` | Health check with DB connection status |
| `GET` | `/api/v1/webhook` | WhatsApp webhook verification (challenge/response) |
| `POST` | `/api/v1/webhook` | Incoming WhatsApp message processing |

---

## 6. Configuration & Environment Management

### 6.1 Environment Selection Logic

```python
def _get_env_file():
    debug = os.getenv("DEBUG", "DEV").upper()
    if debug == "FALSE":
        return "env-prod"     # Production config
    return "env-stage"        # Staging config (default)
```

### 6.2 Configuration Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `APP_NAME` | str | "WhatsApp Bot SaaS" | Application display name |
| `APP_VERSION` | str | "0.1.0" | Semantic version |
| `APP_ENV` | str | "staging" | Environment identifier |
| `DEBUG` | str | "DEV" | Controls env file selection |
| `WHATSAPP_VERIFY_TOKEN` | str | — | Meta webhook verification token |
| `WHATSAPP_API_TOKEN` | str | — | Meta Graph API bearer token |
| `WHATSAPP_PHONE_NUMBER_ID` | str | — | WhatsApp Business phone number ID |
| `WHATSAPP_API_URL` | str | — | Full URL for message sending |
| `WHATSAPP_API_BASE_URL` | str | graph.facebook.com/v22.0 | Meta API base URL |
| `MONGODB_URI` | str | — | MongoDB connection string |
| `MONGODB_DB_NAME` | str | tab_wappbot_ai_stg_db | Database name |
| `META_API_VERSION` | str | "v22.0" | Meta API version |
| `LLM_API_URL` | str | — | LLM service base URL |
| `LLM_API_TIMEOUT` | int | 120 | LLM request timeout (seconds) |
| `APP_BASE_URL` | str | — | Application base URL (CORS) |

### 6.3 Settings Access

Settings are cached using `@lru_cache()` ensuring single instantiation:

```python
from app.core.config import get_settings
settings = get_settings()
```

---

## 7. Database Schema

The application uses MongoDB with 5 collections, all with programmatically ensured indexes at startup.

### 7.1 `contacts` Collection

Stores WhatsApp user profiles.

| Field | Type | Description |
|-------|------|-------------|
| `wa_id` | String | WhatsApp ID (unique identifier) |
| `profile_name` | String | User's display name |
| `phone_number_id` | String | Business phone number ID |
| `message_count` | Integer | Total messages from this contact |
| `last_message_at` | DateTime | Timestamp of last interaction |
| `created_at` | DateTime | First seen timestamp |
| `updated_at` | DateTime | Last update timestamp |
| `is_blocked` | Boolean | Block status |
| `tags` | Array | Custom categorization tags |
| `metadata` | Object | Flexible additional data |

**Indexes:** `wa_id` (unique), `phone_number_id`, `last_message_at`, `created_at`

### 7.2 `messages` Collection

Stores all inbound and outbound WhatsApp messages.

| Field | Type | Description |
|-------|------|-------------|
| `message_id` | String | WhatsApp message ID (wamid, unique) |
| `contact_wa_id` | String | Sender's WhatsApp ID |
| `phone_number_id` | String | Business phone number ID |
| `direction` | String | "inbound" or "outbound" |
| `type` | String | Message type (text, image, interactive, etc.) |
| `content` | Object | Structured message content |
| `context` | Object | Reply context (replied_to_message_id) |
| `wa_timestamp` | DateTime | Original WhatsApp timestamp |
| `created_at` | DateTime | System storage timestamp |

**Indexes:** `message_id` (unique), `contact_wa_id`, `direction`, `type`, `wa_timestamp`, compound(`contact_wa_id` + `wa_timestamp` desc)

### 7.3 `sessions` Collection

Maintains conversational state for each user.

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | String | WhatsApp ID (unique) |
| `current_node` | String | Current position in conversation tree |
| `last_node` | String | Previous node |
| `temp_data` | Object | Flow-specific volatile data (policy_flow, bp_upload_flow) |
| `active_policy_id` | String | Current policy being worked on |
| `first_name` | String | Cached user name |
| `phone_number` | String | User's phone number |
| `last_intent` | String | Last detected intent |
| `updated_at` | DateTime | Last activity timestamp |

**Indexes:** `user_id` (unique), `updated_at`

### 7.4 `policies` Collection

Stores insurance policy applications and their lifecycle data.

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | String | WhatsApp ID of the applicant |
| `status` | String | Lifecycle state (see below) |
| `country_code` | String | ISO-2 country code |
| `country_name` | String | Full country name |
| `selected_product` | Object | Product details (ID, name, price, coverage) |
| `personal_details` | Object | First name, last name, email |
| `itinerary` | Object | Flight details (airports, dates, times, booking ref) |
| `nin` / `bvn` | String | National ID numbers (top-level fields) |
| `kyc_country_code` | String | Country that issued the biometric ID |
| `payout_method` | String | Payment method type |
| `account_number` | String | Bank account number |
| `bank_details` | Object | Selected bank info (name, code) |
| `boarding_pass` | Object | Media ID, MIME type, file data (binary) |
| `policyCode` | String | Code returned after submission |
| `created_at` | DateTime | Policy creation timestamp |
| `updated_at` | DateTime | Last modification timestamp |

**Policy Status Lifecycle:**
```
draft → product_selected → pending → submitted → completed
                                  ↘ cancelled
```

**Indexes:** `user_id`, `status`, `created_at`, `updated_at`, compound(`user_id` + `status`), compound(`user_id` + `created_at` desc)

### 7.5 `llm_logs` Collection

Audit trail for all LLM interactions.

| Field | Type | Description |
|-------|------|-------------|
| `inbound_message_id` | String | Triggering message ID |
| `outbound_message_id` | String | Response message ID |
| `contact_wa_id` | String | User's WhatsApp ID |
| `request_payload` | Object | Data sent to LLM |
| `raw_response` | Object | Full LLM response |
| `intent_code` | String | Detected intent |
| `success` | Boolean | Whether the call succeeded |
| `created_at` | DateTime | Log timestamp |

**Indexes:** `inbound_message_id`, `outbound_message_id`, `contact_wa_id`, `created_at`, compound(`contact_wa_id` + `created_at` desc)

---

## 8. Message Routing Engine

The routing engine in `webhook.py` processes every incoming WhatsApp message through a priority-based evaluation system. Messages are evaluated top-to-bottom; the first matching rule handles the message.

### 8.1 Routing Priority Table

| Priority | Trigger | Handler | Description |
|----------|---------|---------|-------------|
| **1** | Interactive button click (welcome IDs) | `_handle_welcome_button` | Purchase Policy, Submit Boarding Pass, Get Support |
| **2** | Text = `#shortcuts` | Direct response | Shows navigation help guide |
| **3** | Greeting keyword (hi, hello, etc.) | `send_welcome_message` | Shows welcome screen with image + CTA buttons |
| **4** | Cancel/exit keywords (outside flows) | `send_welcome_message` | Resets to welcome screen |
| **5** | CTA text match ("purchase policy", etc.) | `_handle_welcome_button` | Text-to-button matching (case-insensitive) |
| **6** | Active boarding pass flow | `handle_boarding_pass_upload_flow` | Routes to BP upload state machine |
| **7** | Policy keyword (outside flows) | `send_welcome_message` | Shows welcome screen |
| **8** | Active policy flow | `handle_policy_flow` | Routes to policy state machine |
| **9** | LLM configured | `_handle_llm_reply` | Sends to LLM generic endpoint |
| **10** | Fallback | `handle_auto_reply` | Static auto-reply system |

### 8.2 Text-to-Button Matching

Users can type button labels instead of tapping interactive buttons. Matching is case-insensitive with whitespace normalization:

| Typed Text | Matched Button |
|-----------|---------------|
| "purchase policy" | `welcome_purchase_policy` |
| "submit boarding pass" | `welcome_submit_boarding` |
| "get support" | `welcome_get_support` |

### 8.3 In-Flow Navigation Shortcuts

When inside an active flow, users can type these commands:

| Command | Action |
|---------|--------|
| `#back` | Go to previous step |
| `#cancel` / `#exit` | Trigger cancellation confirmation |
| `#menu` / `#home` | Return to welcome screen |
| `#shortcuts` | Display shortcuts guide |

---

## 9. Service Layer Deep Dive

### 9.1 WhatsApp Service (`whatsapp_service.py`)

The low-level Meta Graph API client responsible for all outbound communication.

**Key Functions:**
- `send_text_message(to, body, ...)` — Sends plain text messages
- `send_whatsapp_payload(payload, ...)` — Sends any WhatsApp message type (interactive buttons, lists, templates)
- `upload_media_to_whatsapp(file_path, mime_type)` — Uploads files to Meta's media servers
- `download_whatsapp_media(media_id)` — Downloads user-sent media (boarding passes)
- `get_welcome_image_media_id()` — Returns cached media ID for the welcome image

**Welcome Image Lifecycle:**
1. At startup, `Image.jpeg` is uploaded to Meta's servers via the Media API
2. The returned `media_id` is cached in memory
3. Every welcome message references this cached ID (no re-upload needed)
4. If the upload fails at startup, it retries on first use

### 9.2 Contact Service (`contact_service.py`)

Manages user profiles with upsert semantics.

**Key Functions:**
- `upsert_contact(wa_id, profile_name, phone_number_id)` — Creates or updates contact, increments message count
- `get_contact_by_wa_id(wa_id)` — Retrieves contact document

### 9.3 Message Service (`message_service.py`)

Handles message persistence with deduplication.

**Key Functions:**
- `save_inbound_message(message, contact_wa_id, phone_number_id)` — Parses WhatsApp message model, extracts content, saves to DB. Returns `is_new=False` if message ID already exists (prevents duplicate processing).

### 9.4 Session Service (`session_service.py`)

Maintains per-user conversation state across webhook calls.

**Key Functions:**
- `get_session(user_id)` — Retrieves session or returns `None`
- `save_session(session)` — Persists updated session
- `build_default_session(user_id, phone_number, first_name)` — Creates fresh session with defaults

**Session Structure:**
```json
{
  "user_id": "234916...",
  "current_node": "N01",
  "first_name": "John",
  "phone_number": "234916...",
  "active_policy_id": "ObjectId(...)",
  "temp_data": {
    "policy_flow": { "active": true, "step": "pd_first_name", ... },
    "bp_upload_flow": { "active": false, ... }
  }
}
```

### 9.5 Policy Service (`policy_service.py`)

CRUD operations for the `policies` collection.

**Key Functions:**
- `create_policy(user_id, ...)` — Creates a new draft policy
- `get_active_draft(user_id)` — Finds the most recent unfinished policy
- `get_policy_by_id(policy_id)` — Retrieves by MongoDB ObjectId
- `set_country(policy_id, country_code, country_name)` — Updates country
- `set_personal_details(policy_id, details)` — Updates name/email
- `set_itinerary(policy_id, itinerary)` — Updates flight details
- `set_boarding_pass(policy_id, data)` — Stores boarding pass binary
- `set_policy_submitted(policy_id, response_data)` — Marks as submitted
- `cancel_policy(policy_id)` — Marks as cancelled

### 9.6 LLM Service (`llm_service.py`)

Client for the external AI service.

**Key Functions:**
- `call_generic(user_id, phone_number, message, user_name, current_node)` — General conversation. Sends user message with context, receives natural language response.
- `call_extract(user_id, field_name, question_asked, user_response, expected_format)` — Structured data extraction from natural language input.

### 9.7 LLM Log Service (`llm_log_service.py`)

Audit logging for all LLM interactions.

**Key Functions:**
- `save_llm_log(inbound_message_id, contact_wa_id, request_payload, raw_response, ...)` — Persists full request/response for debugging
- `update_outbound_message_id(log_id, outbound_message_id)` — Links the bot's response message to the log entry

### 9.8 Auto-Reply Service (`auto_reply_service.py`)

Handles the welcome message and fallback responses.

**Key Functions:**
- `send_welcome_message(to, phone_number_id, in_reply_to)` — Sends the branded welcome message with image header and 3 CTA buttons (Purchase Policy, Submit Boarding Pass, Get Support)
- `handle_auto_reply(to_wa_id, incoming_text, message_type, ...)` — Fallback handler for unrecognized messages
- `is_greeting(text)` — Pattern matching for greeting keywords

---

## 10. Policy Purchase Flow

The policy purchase flow is a 27-step guided conversation organized into 5 major stages. It is implemented as a state machine in `policy_flow_service.py`.

### 10.1 Flow Overview

```
Stage 1: Flight & Product          Stage 2: Travel Details
┌─────────────────────┐            ┌──────────────────────┐
│ 1. Welcome/Greeting │            │ 8.  Departure Date   │
│ 2. Service Offering │            │ 9.  Departure Time   │
│ 3. MSISDN Confirm   │            │ 10. Arrival Airport  │
│ 4. Dep Airport Search│           │ 11. Arr Airport Conf │
│ 5. Dep Airport Conf  │           │ 12. Arrival Date     │
│ 6. Product Selection │           │ 13. Arrival Time     │
│ 7. Product Confirm   │           │ 14. Booking Reference│
└─────────────────────┘            │ 15. Flight Number    │
                                   └──────────────────────┘

Stage 3: Personal Details          Stage 4: Payment & Bank
┌──────────────────────┐           ┌──────────────────────┐
│ 16. First Name       │           │ 21. Payment Intro    │
│ 17. Last Name        │           │ 22. Account Number   │
│ 18. Email            │           │ 23. Bank Search      │
│ 19. KYC Country      │           │ 24. Bank Selection   │
│ 20. ID Type + Number │           └──────────────────────┘
└──────────────────────┘

Stage 5: Almost Done
┌──────────────────────┐
│ 25. Boarding Pass    │
│ 26. Summary Confirm  │
│ 27. Submission       │
└──────────────────────┘
```

### 10.2 Step Constants

| Constant | Step Name | User Action |
|----------|-----------|-------------|
| `FLOW_STEP_MSISDN_CONFIRM` | MSISDN Confirmation | Tap Yes, Proceed / No, Cancel |
| `FLOW_STEP_AIRPORT_INPUT` | Departure Airport Search | Type 3+ characters |
| `FLOW_STEP_AIRPORT_LIST` | Airport List | Select from list (includes Search Again) |
| `FLOW_STEP_AIRPORT_CONFIRM` | Airport Confirmation | Tap Confirm / Change |
| `FLOW_STEP_PRODUCT_LIST` | Product List | Select from paginated list |
| `FLOW_STEP_PRODUCT_SELECTED` | Product Selected | Auto-populated |
| `FLOW_STEP_PRODUCT_CONFIRM` | Product Confirmation | Tap Confirm / Change Product |
| `FLOW_STEP_ITIN_DEP_DATE` | Departure Date | Enter DD/MM/YYYY |
| `FLOW_STEP_ITIN_DEP_TIME` | Departure Time | Enter HH:MM |
| `FLOW_STEP_ITIN_ARR_AIRPORT_INPUT` | Arrival Airport Search | Type 3+ characters |
| `FLOW_STEP_ITIN_ARR_AIRPORT_CONFIRM` | Arrival Airport Confirm | Tap Confirm / Change |
| `FLOW_STEP_ITIN_ARR_DATE` | Arrival Date | Enter DD/MM/YYYY |
| `FLOW_STEP_ITIN_ARR_TIME` | Arrival Time | Enter HH:MM |
| `FLOW_STEP_ITIN_BOOKING_REF` | Booking Reference | Enter alphanumeric code |
| `FLOW_STEP_ITIN_FLIGHT_NO` | Flight Number | Enter flight number |
| `FLOW_STEP_PD_FIRST_NAME` | First Name | Enter single name |
| `FLOW_STEP_PD_LAST_NAME` | Last Name | Enter single name |
| `FLOW_STEP_PD_EMAIL` | Email | Enter valid email |
| `FLOW_STEP_KYC_COUNTRY` | KYC Country | Enter country name |
| `FLOW_STEP_ID_TYPE` | ID Type & Number | Select NIN/BVN, then enter number |
| `FLOW_STEP_PAYMENT_METHOD` | Payment Method | Select from list |
| `FLOW_STEP_BANK_SEARCH` | Bank Search | Type 3+ characters |
| `FLOW_STEP_BANK_SELECTION` | Bank Selection | Select from paginated list |
| `FLOW_STEP_BOARDING_PASS_CHOICE` | Boarding Pass Choice | Upload Now / Upload Later |
| `FLOW_STEP_BOARDING_PASS` | Boarding Pass Upload | Send image/document |
| `FLOW_STEP_DETAILS_CONFIRM` | Summary Confirmation | Yes, Submit / No, Change Details |
| `FLOW_STEP_POLICY_SUMMARY` | Policy Summary | Final display |

### 10.3 State Management

Flow state is stored in the session under `temp_data["policy_flow"]`:

```json
{
  "active": true,
  "step": "itin_dep_date",
  "policy_id": "ObjectId(...)",
  "country_code": "NG",
  "country_name": "Nigeria",
  "selected_product": { "id": "...", "name": "...", "price": 200 },
  "personal_details": { "first_name": "John", "last_name": "Doe", "email": "..." },
  "itinerary": {
    "departure": { "airport": {...}, "scheduledDateLocal": "10/04/2026", "scheduledTimeLocal": "06:50" },
    "arrival": { "airport": {...}, "scheduledDateLocal": "10/04/2026", "scheduledTimeLocal": "22:30" },
    "bookingReference": "ABC123",
    "flightNo": "BF3421"
  },
  "id_type": "BVN",
  "id_number": "1234567890",
  "bank_details": { "name": "Zenith Bank PLC", "code": "057" },
  "account_number": "9876543212",
  "boarding_pass_uploaded": true
}
```

### 10.4 Navigation Features

- **Back Navigation:** `BACK_STEP_MAP` defines the previous step for every step, enabling `#back` command
- **Change Details (option 1-13):** At the summary screen, users can change any previously entered field:
  1. Departure Date
  2. Departure Time
  3. Arrival Airport
  4. Arrival Date
  5. Arrival Time
  6. Booking Reference (PNR)
  7. Flight Number
  8. First Name
  9. Last Name
  10. Email
  11. KYC Country
  12. ID Type & Number
  13. Boarding Pass

- **Search Again:** Airport and bank list menus include a "Search Again" option to restart the search

### 10.5 Policy Submission

On final confirmation, the system:

1. Sends "Submitting your policy... please wait."
2. Calls `POST /api/tab-plc/policies` as `multipart/form-data`
3. Maps collected data: `productId`, `msisdn` (with `+` prefix), `BVN`/`NIN`, dates converted from `DD/MM/YYYY` to `DD-MM-YYYY`
4. Includes boarding pass file if uploaded
5. On success: Updates policy status to `submitted`, displays policy code in monospace format
6. On failure: Shows error details with Retry Submission button
7. Shows welcome message for next interaction

---

## 11. Boarding Pass Upload Flow

A separate flow for uploading boarding passes to existing policies.

### 11.1 Flow Steps

| Step | Action | UI Element |
|------|--------|-----------|
| **1. Entry** | User taps "Submit Boarding Pass" | Welcome button |
| **2. Search** | System fetches policies by phone number | Loading message |
| **3. Policy List** | Paginated list (6 per page, active first) | Interactive list |
| **4. Policy Details** | Shows policy summary with boarding pass status | Text message |
| **5. Upload** | User sends image/document | File upload |
| **6. Confirmation** | Success message + welcome screen | Text + buttons |

### 11.2 Supported File Types

- JPEG (`image/jpeg`)
- PNG (`image/png`)
- WebP (`image/webp`)
- PDF (`application/pdf`)

### 11.3 Deduplication

The system tracks the SHA-256 hash of uploaded files to prevent accidental duplicate submissions within the same session.

### 11.4 Replace Flow

If a boarding pass already exists for the selected policy, the system asks the user to confirm replacement before proceeding.

---

## 12. External API Integrations

### 12.1 Meta WhatsApp Business API (Graph API v22.0)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/{phone_number_id}/messages` | POST | Send messages (text, interactive, media) |
| `/{phone_number_id}/media` | POST | Upload media files |
| `/{media_id}` | GET | Get media download URL |
| `{download_url}` | GET | Download media binary |

**Authentication:** Bearer token via `WHATSAPP_API_TOKEN`

### 12.2 iPurvey Insurance APIs

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v2/airports/search?term={term}` | GET | Search airports by name/code |
| `/v1/tab-pc/products/getByCountry/{code}` | GET | Fetch products by country |
| `/tab-plc/policies/payout-method/types` | GET | List payment methods |
| `/tab-plc/policies/payout-method/banks?countryCode={code}` | GET | List banks by country |
| `/tab-plc/policies` | POST | Submit new policy (multipart) |
| `/tab-plc/policies/by-msisdn/{msisdn}` | GET | Get policies by phone number |
| `/tab-plc/policies/upload-boarding-pass` | POST | Upload boarding pass (multipart) |

**Base URL:** `https://dev-ilekun-ipv.ipurvey.com/api`

### 12.3 LLM Service

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/route` | POST | Unified routing, extraction, clarification, and answer dispatch |

**Base URL:** `https://staging-tab-whatsappllm.ipurvey.com`
**Timeout:** 120 seconds

---

## 13. LLM Integration

### 13.1 Generic Endpoint Usage

The LLM generic endpoint is called in these scenarios:

| Context | Trigger | Behavior |
|---------|---------|----------|
| **Outside any flow** | Any unrecognized text | LLM responds, no buttons shown |
| **MSISDN Confirm step** | User types free text (not yes/no) | LLM responds, then re-shows confirm buttons |
| **Product Confirm step** | User types free text (not Confirm/Change) | LLM responds, then re-shows product buttons |
| **First/Last Name step** | User types multi-word input | LLM responds, then re-prompts for valid name |

### 13.2 Request Format

```json
{
  "user_id": "234916799997",
  "phone_number": "234916799997",
  "message": "What is your company about?",
  "user_name": "John",
  "current_node": "N01"
}
```

### 13.3 Response Handling

The webhook manages LLM session state including:
- `current_node` / `suggested_node` — Conversation flow position
- `detected_intent` — Intent classification for routing
- Session persistence after each LLM interaction

---

## 14. Input Validation Rules

All itinerary fields use direct code validation (no LLM).

### 14.1 Date Validation

- **Accepted formats:** `DD/MM/YYYY`, `DD-MM-YYYY`, `YYYY-MM-DD`, `YYYY/MM/DD`
- **Rules:** Must be a valid calendar date, year >= 2024, no past dates (except arrival can equal departure)
- **Arrival constraint:** Arrival date/time must be after departure date/time

### 14.2 Time Validation

- **Format:** `HH:MM` (24-hour)
- **Range:** 00:00 to 23:59

### 14.3 Booking Reference

- **Rule:** Alphanumeric or letters only; numbers-only are rejected
- **Length:** 3-10 characters
- **Must contain:** At least one letter
- **Processing:** Whitespace and dashes stripped, converted to uppercase

### 14.4 Flight Number

- **Format:** 1-3 letters followed by 2-4 digits (e.g., BA1234, QF34, AAL1234)
- **Processing:** Whitespace and dashes stripped, converted to uppercase

### 14.5 Names (First/Last)

- **Single word** (letters and hyphens only, e.g., "John", "Mary-Jane"): Accepted directly, title-cased
- **Multi-word input**: Routed to LLM generic endpoint, then re-prompts for valid name

### 14.6 Email

- Standard email format validation (regex-based)

---

## 15. Error Handling & Resilience

### 15.1 Message Deduplication

Every inbound message is checked against the `messages` collection by `message_id`. If the ID already exists, the message is flagged as `is_new=False` and skipped. This prevents duplicate processing from WhatsApp's retry mechanism.

### 15.2 API Retry Logic

External API calls (iPurvey) use exponential backoff retry:
- Configurable retry count
- Progressive delay between attempts
- Specific error handling for HTTP 413 (file too large)

### 15.3 LLM Fallback

If the LLM service is unavailable or returns an error:
- The system falls back to the static auto-reply handler
- No user-facing error is shown
- The failure is logged for monitoring

### 15.4 Graceful Cancellation

All flows support cancellation at any step:
- Policy status is updated to "cancelled" in the database
- Session state is cleared
- User receives a friendly message and the welcome screen

---

## 16. Deployment & Infrastructure

### 16.1 Docker Configuration

```dockerfile
FROM python:3.11
WORKDIR /code
COPY ./requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt
ENV DEBUG="DEV"
COPY ./app /code/app
COPY env-prod .
COPY env-stage .
COPY main.py .
CMD ["gunicorn", "--bind=0.0.0.0:5000", "--reuse-port",
     "--workers=4", "--worker-class=uvicorn.workers.UvicornWorker",
     "--timeout=120", "--access-logfile=-", "--error-logfile=-",
     "main:app"]
```

### 16.2 Production Server Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Server** | Gunicorn + Uvicorn workers | Production-grade process management with async support |
| **Workers** | 4 | Balanced for typical cloud VM (2-4 CPU cores) |
| **Port** | 5000 | Standard application port |
| **Timeout** | 120s | Matches LLM API timeout |
| **Worker Class** | `uvicorn.workers.UvicornWorker` | Required for async FastAPI |

### 16.3 Development Server

```bash
uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

### 16.4 Environment Promotion

```
Development (local) → Staging (env-stage) → Production (env-prod)
```

- `DEBUG=DEV` → loads `env-stage`
- `DEBUG=FALSE` → loads `env-prod`

### 16.5 Health Monitoring

The `/api/v1/health` endpoint provides:
- Application status
- Database connection status
- Environment information

---

## 17. Security Considerations

### 17.1 Webhook Verification

The `GET /api/v1/webhook` endpoint validates Meta's verification challenge using the `WHATSAPP_VERIFY_TOKEN`, preventing unauthorized webhook registrations.

### 17.2 Sensitive Data Handling

- **KYC Data (NIN/BVN):** Stored as top-level fields in the policy document, masked in user-facing messages (last 4 digits shown as `***3210`)
- **API Tokens:** Loaded from environment variables, never hardcoded
- **CORS:** Configurable allowed origins; defaults to permissive in development

### 17.3 Docker Security

- Non-root user execution capability
- No development tools in production image
- Dependencies pinned to specific versions

### 17.4 Input Sanitization

- All user inputs are stripped and validated before processing
- Regex-based validation for structured fields (dates, flight numbers, emails)
- Message deduplication prevents replay attacks

---

## 18. Glossary

| Term | Definition |
|------|-----------|
| **CTA** | Call To Action — interactive buttons in WhatsApp messages |
| **MSISDN** | Mobile Station International Subscriber Directory Number — the phone number |
| **KYC** | Know Your Customer — identity verification process |
| **NIN** | National Identification Number (Nigeria) |
| **BVN** | Bank Verification Number (Nigeria) |
| **PNR** | Passenger Name Record — booking reference code |
| **IATA** | International Air Transport Association — airport codes (e.g., LOS, ABV) |
| **wamid** | WhatsApp Message ID — unique identifier for each message |
| **wa_id** | WhatsApp ID — user's phone number in international format |
| **Graph API** | Meta's HTTP API for accessing WhatsApp Business features |
| **Motor** | Async Python driver for MongoDB |
| **Pydantic** | Python library for data validation using type annotations |
| **iPurvey** | External insurance platform providing product and policy APIs |
| **LLM** | Large Language Model — AI service for natural language processing |

---

*This document is maintained alongside the codebase and should be updated when architectural changes are made.*
