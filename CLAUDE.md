# TravelAssist WhatsApp AI Bot — Project State & Agent Guide

## What This Project Is

A WhatsApp Business API bot for iPurvey TravelAssist — an insurance/travel product. Users interact via WhatsApp to buy travel cover, manage policies, complete KYC, make payments, and update personal details. An LLM service handles natural-language intent routing.

---

## How to Run Locally

### 1. Start the FastAPI server

```powershell
cd "<your-project-directory>"
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Expose local server via ngrok (for Meta webhook)

```bash
ngrok http --domain=alienable-displace-undoing.ngrok-free.dev 8000
```

> **Note:** The ngrok domain is fixed (free static domain). Keep this running alongside the server.

### 3. Which env file loads automatically

The app picks env file based on `DEBUG` env var (see `app/core/config.py:_get_env_file()`):

| DEBUG value | Env file loaded |
|---|---|
| `LOCAL` | `env-local` |
| `FALSE` | `env-prod` |
| `DEV` (default) | `env-stage` (if env-local absent) |

**On another machine:** `env-local` is in `.gitignore` and will NOT be cloned. The server will automatically fall back to `env-stage`. Set `DEBUG=LOCAL` only if you create your own `env-local`.

---

## Environment Files

| File | Purpose | In Git? |
|---|---|---|
| `env-local` | Local dev only — your machine | NO (.gitignore) |
| `env-stage` | Staging server credentials | YES |
| `env-prod` | Production credentials | YES |

### env-stage key values
- MongoDB: `tab_wappbot_ai_stg_db` (staging database)
- WhatsApp number: `+234 916 796 6000` (AeroFlow test number)
- LLM URL: `https://staging-tab-whatsappllm.ipurvey.com`
- App URL: `https://staging-tab-whatsappbot.ipurvey.com`

### env-prod key values
- MongoDB: `tab_wappbot_ai_prod_db` (production database)
- WhatsApp Phone ID: `970590362802994` (+44 7457 406270 — iPurvey TravelAssist)
- LLM URL: `https://tab-whatsappllm.ipurvey.com`
- App URL: `https://tab-whatsappbot.ipurvey.com`

---

## Deployment (Production)

- **Bot server:** `https://tab-whatsappbot.ipurvey.com` (deployed by mabdullah.my via Docker)
- **LLM server:** `https://tab-whatsappllm.ipurvey.com` (deployed by mabdullah.my)
- **Policy/Insurance API:** `https://ilekun-ipv.ipurvey.com`
- **Git repo:** `https://github.com/ipvcomp/TAB-Whatsapp-Bot-and-AI-agent`
- **Branches:** `dev` = staging work, `main` = production
- **Docker:** Dockerfile uses `gunicorn` on port 5000, loads `env-prod` or `env-stage`
- **To deploy production:** mabdullah.my pulls `main` branch and runs with `APP_ENV=prod`

### Meta Webhook (Production)
- Callback URL: `https://tab-whatsappbot.ipurvey.com/api/v1/webhook`
- Verify token: `wa_bot_verify_token_2026`

### Meta Webhook (Staging)
- Callback URL: `https://staging-tab-whatsappbot.ipurvey.com/api/v1/webhook`
- Verify token: `wa_bot_verify_token_2026`

---

## Project Structure

```
TravelAssistant-Whatsapp-Bot-and-AI-agent/
├── main.py                          # Entry point — imports app from app/main.py
├── app/
│   ├── main.py                      # FastAPI app creation, lifespan, CORS
│   ├── core/
│   │   ├── config.py                # Settings — reads env file based on DEBUG var
│   │   └── database.py              # MongoDB connection
│   ├── api/v1/
│   │   ├── router.py                # API router
│   │   └── endpoints/
│   │       ├── webhook.py           # WhatsApp webhook GET (verify) + POST (messages)
│   │       └── admin.py             # Admin endpoints
│   └── services/                    # All business logic flows
│       ├── buy_cover_flow_service.py        # Main flow — buying travel insurance
│       ├── update_details_flow_service.py   # Update policy/traveller details
│       ├── kyc_flow_service.py              # KYC identity verification flow
│       ├── payment_flow_service.py          # Payment handling flow
│       ├── check_policy_flow_service.py     # Check existing policy
│       ├── help_flow_service.py             # Help/FAQ flow
│       ├── bp_link_flow_service.py          # Boarding pass link flow
│       ├── draft_policies_flow_service.py   # Draft policies flow
│       ├── auto_reply_service.py            # Auto-reply handler
│       ├── llm_service.py                   # LLM API calls for intent routing
│       ├── whatsapp_service.py              # WhatsApp API (send messages, media)
│       ├── ipurvey_service.py               # iPurvey insurance API integration
│       ├── ipurvey_api.py                   # iPurvey API HTTP client
│       ├── session_service.py               # User session management (MongoDB)
│       ├── message_service.py               # Message history
│       ├── document_poll_service.py         # Document polling
│       └── policy_refresh.py               # Policy status refresh
├── env-local                        # LOCAL only — NOT in git
├── env-stage                        # Staging credentials — in git
├── env-prod                         # Production credentials — in git
├── Dockerfile                       # Docker build — copies env-prod, env-stage
├── requirements.txt                 # Python dependencies
└── docs/
    └── bot_error_messages.md        # Static error message definitions
```

---

## How the Bot Works (Flow Architecture)

### Message entry point
`app/api/v1/endpoints/webhook.py` receives all WhatsApp messages and routes to appropriate service based on session state.

### Step-based flow system
Each user has a **session** stored in MongoDB with a `step` field. Every service function checks `step` to know where the user is in the conversation.

### LLM routing
For free-text messages, `llm_service.py` calls the LLM API which returns:
- `answer` — direct reply text
- `clarify` — ask user to clarify
- `reply_id` — intent to route to (e.g. `buy_cover`, `check_policy`)

The LLM contract: flow steps surface `answer`/`clarify` text via `get_llm_guidance` then re-show the same step.

### Key flow steps

**Buy Cover Flow** (`buy_cover_flow_service.py`):
- Collects flight details (airline, route, flight number, booking ref, dates, times)
- Validates departure/arrival (domestic vs international warning)
- Shows trip summary in WhatsApp monospace block
- Fetches insurance quotes from iPurvey API
- KYC → Payment

**KYC Flow** (`kyc_flow_service.py`):
- Steps: `kyc_id_input` → `kyc_otp_input` → `kyc_verified` / `kyc_failed`
- "Review trip" button was removed from all screens

**Payment Flow** (`payment_flow_service.py`):
- Payment prompt screen: only "I have paid" button (`pay_m_done`)
- Payment pending screen: only "Refresh status" button (`pay_m_refresh`)
- Both handlers check same payment status logic

**Update Details Flow** (`update_details_flow_service.py`):
- Allows user to update traveller name, passport, email etc.
- "More traveller" button removed from all screens
- Success screen button renamed to "Update My Details"

---

## Recent Fixes Applied (Session History)

### Trip Summary Alignment (buy_cover_flow_service.py ~line 672)
- Problem: Tabs broke on mobile proportional fonts
- Fix: WhatsApp triple-backtick monospace block + UPPERCASE labels + `str.ljust(11)` padding
- Bold `*text*` does NOT work inside code blocks — UPPERCASE used instead

### Duration Warning Message (buy_cover_flow_service.py ~line 2945, ~line 3807)
- Problem: Warning only showed dates, not times or duration
- Fix: Added `_fmt_time_display()` for dep/arr times + calculated duration string (e.g. "14h 30m")

### Travellers Button Removed (update_details_flow_service.py)
- 3 locations removed: `upd_policy_select` handler (~line 432), `_show_menu()` helper (~line 851)
- Success screen: "More traveller" button removed, "Update details" renamed to "Update My Details"

### KYC Review Trip Button Removed (kyc_flow_service.py)
- Removed from all 8+ button lists
- Removed 2 full `elif reply_id == "kyc_review"` handler blocks
- LLM `allowed_values` and routing for `review_trip` removed

### Payment Screen Fix (payment_flow_service.py)
- Removed "Refresh status" from payment PROMPT screen (only "I have paid" remains)
- Restored "Refresh status" on payment PENDING screen (client requirement)
- Handler: `if reply_id in ("pay_m_done", "pay_m_refresh"):` handles both

### env-prod Updated with Production Credentials
- MongoDB: `tab_wappbot_ai_prod_db` (same cluster `cluster0.efcvr.mongodb.net`)
- WhatsApp Phone ID: `970590362802994` (+44 7457 406270)
- LLM URL: `https://tab-whatsappllm.ipurvey.com` (production)
- App Base URL: `https://tab-whatsappbot.ipurvey.com`

---

## WhatsApp Message Formatting Rules

| Format | Works? | Notes |
|---|---|---|
| `*bold*` | Yes | Outside code blocks only |
| `` `code` `` | Yes | Inline monospace |
| ` ```block``` ` | Yes | Forces fixed-width font — use for alignment |
| Bold inside code block | NO | Use UPPERCASE instead |
| Tab `\t` for alignment | NO | Inconsistent on mobile — use monospace block |
| Max buttons | 3 | Use list message for 4+ options |

---

## External Services

| Service | URL | Purpose |
|---|---|---|
| Meta Graph API | `https://graph.facebook.com/v22.0` | Send/receive WhatsApp messages |
| iPurvey Insurance API | `https://ilekun-ipv.ipurvey.com` (prod) / `https://dev-ilekun-ipv.ipurvey.com` (staging) | Fetch quotes, policies, KYC |
| LLM API | `https://tab-whatsappllm.ipurvey.com` (prod) / `https://staging-tab-whatsappllm.ipurvey.com` (staging) | Intent routing |
| MongoDB | `cluster0.efcvr.mongodb.net` | Sessions, messages, policies |

---

## Team & Contacts

| Person | Role |
|---|---|
| Dev Getaichatbots | WhatsApp Bot development (us) |
| mabdullah.my | LLM service + deployment (Docker/server) |
| sanket pawar | LLM code deployment |
| Ope Onimole | Client project manager (Dev_Travel_Assist Steel) |
| Abdul Isiaq | Client technical lead |

---

## Current Production State (as of last session)

- [x] Bot code merged to `main` branch (542 commits)
- [x] `env-prod` has all production credentials
- [x] Meta webhook verified for production URL
- [x] LLM deployed to production (`https://tab-whatsappllm.ipurvey.com`)
- [ ] mabdullah.my to confirm bot service deployed from `main` with `APP_ENV=prod`
- [ ] Production WhatsApp number (+44 7457 406270) end-to-end test pending
