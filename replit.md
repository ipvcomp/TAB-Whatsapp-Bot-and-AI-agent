# WhatsApp Bot SaaS Backend

## Overview
FastAPI-based SaaS backend for WhatsApp Business API bot integration using Meta's Cloud API with MongoDB for data persistence.

## Project Structure
```
app/
├── main.py              # FastAPI application factory with lifespan management
├── core/
│   ├── config.py        # Settings via pydantic-settings
│   └── database.py      # MongoDB async connection (motor driver)
├── api/
│   └── v1/
│       ├── router.py    # V1 API router
│       └── endpoints/
│           ├── webhook.py   # Meta WhatsApp webhook (GET verify + POST events)
│           └── health.py    # Health check endpoint (includes DB status)
├── models/
│   └── webhook.py       # Pydantic models for webhook payloads
├── services/
│   ├── contact_service.py    # Contact upsert, retrieval with dedup logic
│   ├── message_service.py    # Message persistence with content extraction
│   ├── whatsapp_service.py   # Meta Graph API client for sending messages
│   ├── auto_reply_service.py # Static auto-reply logic (temporary, to be replaced with LLM)
│   ├── llm_service.py        # LLM integration - call_generic (Q&A) + call_extract (input parsing)
│   ├── llm_log_service.py    # LLM raw response logging for traceability
│   ├── session_service.py    # User session persistence for LLM conversation state
│   ├── policy_flow_service.py # Static policy creation flow (no LLM)
│   └── policy_service.py     # Policy CRUD operations (policies collection)
└── utils/               # Shared utilities (to be implemented)
main.py                  # Entry point (imports app from app.main)
```

## Key Endpoints
- `GET /` - App info
- `GET /api/v1/health` - Health check (includes database connection status)
- `GET /api/v1/webhook` - Meta webhook verification (hub.mode, hub.verify_token, hub.challenge)
- `POST /api/v1/webhook` - Receives WhatsApp message/status webhook events, triggers auto-reply

## Environment Variables / Secrets
- `WHATSAPP_VERIFY_TOKEN` - Token for Meta webhook verification
- `WHATSAPP_API_TOKEN` - Meta Graph API access token (secret)
- `WHATSAPP_PHONE_NUMBER_ID` - WhatsApp Business phone number ID (secret)
- `MONGODB_URI` - MongoDB connection string (secret)
- `MONGODB_DB_NAME` - Database name (defaults to tab_wappbot_ai_stg_db)
- `APP_ENV` - Environment: "staging" or "production" (defaults to staging)
- `LLM_API_URL` - LLM service base URL (e.g. ngrok URL for testing)
- `LLM_API_TIMEOUT` - LLM request timeout in seconds (defaults to 30)
- `APP_BASE_URL` - Base URL of this app (differs per environment)

## Running
```
uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

## Docker
```bash
# Build
docker build -t whatsapp-bot .

# Run (create .env file with secrets first)
docker run -p 5000:5000 --env-file .env whatsapp-bot

# Or use docker-compose
docker-compose up -d
```
- Multi-stage build with python:3.11-slim (small image)
- Non-root user for security
- Pinned dependency versions in requirements.txt
- Health check on /api/v1/health
- Gunicorn with 4 uvicorn workers in production

## Tech Stack
- Python 3.11, FastAPI, Uvicorn, Gunicorn (production)
- pydantic-settings for configuration
- motor (async MongoDB driver) + pymongo
- httpx for outbound HTTP calls
- Meta WhatsApp Business Cloud API (Graph API v22.0)

## Database
- MongoDB Atlas (cluster0.efcvr.mongodb.net)
- Database: tab_wappbot_ai_stg_db
- Async driver via motor with connection pooling (max 50, min 10)
- Connection managed via FastAPI lifespan events (connect on startup, close on shutdown)
- Indexes ensured automatically on startup

### Collections
- **contacts** — Stores WhatsApp contacts (unique on `wa_id`)
  - Fields: wa_id, profile_name, phone_number_id, business_phone, message_count, is_blocked, tags, metadata, created_at, updated_at, last_message_at
  - Indexes: wa_id (unique), phone_number_id, last_message_at, created_at
- **messages** — Stores all inbound AND outbound messages (unique on `message_id`)
  - Fields: message_id, contact_wa_id, phone_number_id, business_phone, direction (inbound/outbound), type, content, context, source, wa_timestamp, created_at, errors
  - `context.in_reply_to` — links outbound message to the inbound message_id it replied to
  - `source` — origin of outbound message: "llm", "auto_reply"
  - Indexes: message_id (unique), contact_wa_id, phone_number_id, direction, type, wa_timestamp, compound (contact_wa_id + wa_timestamp)
  - Supports all message types: text, image, audio, video, document, location, reaction, sticker, interactive, button
- **sessions** — Stores LLM conversation state per user (unique on `user_id`)
  - Fields: user_id, phone_number, current_node, first_name, tags, active_trip_id, active_policy_id, active_policy_code, active_claim_id, temp_data, created_at, updated_at
  - Indexes: user_id (unique), updated_at
- **llm_logs** — Stores raw LLM API request/response for traceability
  - Fields: inbound_message_id, outbound_message_id, contact_wa_id, request_payload, raw_response, intent_code, intent_confidence, target_node, previous_node, success, error, created_at
  - `inbound_message_id` — the user message that triggered the LLM call
  - `outbound_message_id` — the Meta wamid of the reply sent back (null if send failed or no payload)
  - `raw_response` — complete unmodified LLM API response for full traceability
  - Indexes: inbound_message_id, outbound_message_id, contact_wa_id, created_at, compound (contact_wa_id + created_at)
- **policies** — Stores policy creation records per user (one record per policy attempt)
  - Fields: user_id, phone_number, country_code, country_name, status, selected_product, personal_details, payment_method, bank_details, msisdn_info, channel_info, airport_info, itinerary, submitted_policy, created_at, updated_at
  - `status` — lifecycle: "draft" → "product_selected" → "pending" → "submitted" → "completed" (or "cancelled")
  - `country_code` — ISO 3166-1 alpha-2 code (e.g. "NG", "KE", "GH"), resolved from user's country name input
  - `selected_product` — stores product_id, name, description, price, currency, validity_days, coverage_types, product_type, provider_name
  - Future fields (bank_details, msisdn_info, etc.) are null until those flow steps are implemented
  - Indexes: user_id, status, created_at, updated_at, compound (user_id + status), compound (user_id + created_at)

## Message Flow
1. User sends WhatsApp message → Meta webhook delivers to POST /api/v1/webhook
2. App saves contact + inbound message to MongoDB (with message_id = Meta wamid)
3. If LLM_API_URL is configured and not in policy flow:
   - Retrieves user's session (or creates default with node N01)
   - POSTs to LLM generic endpoint (`/api/v1/generic`) with user_id, phone_number, message, user_name, current_node
   - Wraps LLM's `response` text in a WhatsApp text message and sends to Meta API
   - Updates session with `suggested_node` and `detected_intent` if returned
   - Saves outbound message to MongoDB with `context.in_reply_to` = inbound wamid, `source` = "llm"
   - Saves raw LLM response to `llm_logs` collection
   - Falls back to static auto-reply if LLM is unreachable (logged to llm_logs with success=false)
4. If in policy flow:
   - All free-form text inputs (country, names, email, NIN, account number, airport, wallet number) go through LLM extract endpoint (`/api/v1/extract`)
   - Extract returns cleaned `extracted_value` + validation + clarification prompts
   - Graceful fallback: if extract API is down, raw user input is used
5. If LLM_API_URL not configured: uses static auto-reply (outbound saved with `source` = "auto_reply")

### LLM API Endpoints
- **Base URL**: `https://staging-tab-whatsappllm.ipurvey.com`
- **`POST /api/v1/generic`** — Free-form Q&A (user asks questions outside static flows)
  - Request: `{user_id, phone_number, message, user_name, current_node}`
  - Response: `{success, response, suggested_node, detected_intent, confidence, tokens_used, processing_time_ms, error}`
- **`POST /api/v1/extract`** — Extract/validate user input from static flow text fields
  - Request: `{user_id, field_name, question_asked, user_response, expected_format}`
  - Response: `{success, extracted_value, is_valid, validation_message, needs_clarification, clarification_prompt, original_response, field_name, error}`
  - Used for: country, first_name, last_name, email, nin, account_number, city (airport), phone_number (wallet)
  - Graceful fallback: if extract API is unreachable, raw user input is used

### Traceability Chain
- Inbound message (wamid) → outbound message (wamid) via `context.in_reply_to`
- Outbound message → LLM log via `llm_logs.outbound_message_id`
- LLM log → inbound message via `llm_logs.inbound_message_id`
- LLM log contains full `raw_response` for debugging and audit

## Auto-Reply System (Fallback)
Static pattern-based auto-replies used when LLM is not configured or unreachable.
- Greeting patterns (hi, hello, hey, salam) → Welcome message
- Help/support → Support acknowledgment
- Pricing → Pricing interest acknowledgment
- Thanks → Thank you response
- Goodbye → Farewell message
- Order/delivery → Order inquiry prompt
- Complaint/issue → Issue acknowledgment
- Info/about → Information offer
- Non-text media → Media received acknowledgment
- Default → General acknowledgment with help prompt

## Policy Flow (Static — No LLM)
Triggered by keywords: policy, create policy, /policy, /createpolicy, "i want to create policy", etc.
- Runs independently from LLM — takes priority in webhook routing
- Uses session `temp_data.policy_flow` to track flow state per user
- Flow state fields: active, step, action, selected_product, available_products

### Flow Steps
1. **Policy Menu** — User sends policy keyword → interactive buttons: "Create New Policy" / "Submit Itinerary"
2. **Submit Itinerary** — Static placeholder message (feature coming soon), clears flow state
3. **Country Input** — User taps "Create New Policy" → asked to type country name → validated and resolved to ISO code → saved to policy
4. **View Products** — Country confirmed → prompt with "View Products" button
5. **Product List** — Fetches products from API by country code → WhatsApp list with pagination (8 per page, Next/Previous at positions 9-10)
6. **Product Selected** — User picks a product → confirmation with name, description, price, validity, coverage, provider
7. **Personal Details** — Collects one-by-one via text: first_name → last_name → email (validated) → nin → account_number
8. **Payment Method** — Fetches payout methods from API → shows interactive buttons (Bank Transfer / Wallet / Mobile Money)
9. **Bank Selection** — Fetches banks from API by country code → sorted alphabetically → WhatsApp list with pagination (8 per page, Next/Previous)
10. **MSISDN Setup** — Auto-sets user's WhatsApp number + country code as MSISDN. For Wallet users: asks if wallet number is different; if yes, collects alternate number
11. **Channel/Source/Consent** — Auto-set (no user input): channel_payout_method = "Bank", source = "passenger", consent = true
12. **Airport Selection** — User enters city/state name → searched via airports API (handles case variants) → if single result, auto-confirmed; if multiple, shown as WhatsApp list menu → saved to policy
13. **Summary** — Displays combined summary of all collected policy details

### External APIs
- Products: `https://dev-ilekun-ipv.ipurvey.com/api/v1/tab-pc/products/getByCountry/{COUNTRY_CODE}`
- Payment Methods: `https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/payout-method/types`
- Banks: `https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/payout-method/banks?countryCode={COUNTRY_CODE}`
- Airports: `https://dev-ilekun-ipv.ipurvey.com/api/v2/airports/search?search={CITY_OR_STATE}`

### User Selection Storage
- Country saved in `policies.country_code` (ISO 3166-1 alpha-2) and `policies.country_name`
- Selected product saved in `policies.selected_product` and `session.temp_data.policy_flow.selected_product`
- Personal details saved in `policies.personal_details`: first_name, last_name, email, nin, account_number
- Payment method saved in `policies.payment_method`: BANK_TRANSFER, WALLET, or MOBILE_MONEY
- Bank details saved in `policies.bank_details`: bank_id, bank_code, bank_name
- MSISDN info saved in `policies.msisdn_info`: phone_number, country_code, wallet_number (for Wallet users)
- Channel info saved in `policies.channel_info`: channel_payout_method, source, consent (auto-set)
- Airport info saved in `policies.airport_info`: name, iata_code, country
- All data persists in the `policies` collection per user per policy attempt

### Message Routing Priority
1. Policy flow (keyword match OR active flow in session)
2. LLM integration (if LLM_API_URL configured)
3. Static auto-reply fallback

## Meta Webhook Setup
1. Go to Meta Developer Console > Your App > WhatsApp > Configuration
2. Set Callback URL to: `https://staging-tab-whatsappbot.ipurvey.com/api/v1/webhook`
3. Set Verify Token to match `WHATSAPP_VERIFY_TOKEN` env var
4. Subscribe to: messages, message_echoes, account_alerts, phone_number_quality_update, message_template_status_update, security

## Deployment
- Staging URL: https://staging-tab-whatsappbot.ipurvey.com
- Replit Published URL: https://tab-whatsapp-bot-and-ai-agent.replit.app
- Deployment target: autoscale (Replit) / Docker (self-hosted)
- Production server: gunicorn with 4 uvicorn workers
