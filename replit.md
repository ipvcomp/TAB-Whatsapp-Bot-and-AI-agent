# WhatsApp Bot SaaS Backend

## Overview
FastAPI-based SaaS backend for WhatsApp Business API bot integration using Meta's Cloud API.

## Project Structure
```
app/
├── main.py              # FastAPI application factory
├── core/
│   └── config.py        # Settings via pydantic-settings
├── api/
│   └── v1/
│       ├── router.py    # V1 API router
│       └── endpoints/
│           ├── webhook.py   # Meta WhatsApp webhook (GET verify + POST events)
│           └── health.py    # Health check endpoint
├── models/
│   └── webhook.py       # Pydantic models for webhook payloads
├── services/            # Business logic (to be implemented)
└── utils/               # Shared utilities (to be implemented)
main.py                  # Entry point (imports app from app.main)
```

## Key Endpoints
- `GET /` - App info
- `GET /api/v1/health` - Health check
- `GET /api/v1/webhook` - Meta webhook verification (hub.mode, hub.verify_token, hub.challenge)
- `POST /api/v1/webhook` - Receives WhatsApp message/status webhook events

## Environment Variables
- `WHATSAPP_VERIFY_TOKEN` - Token for Meta webhook verification
- `WHATSAPP_API_TOKEN` - Meta Graph API access token (to be configured)
- `WHATSAPP_PHONE_NUMBER_ID` - WhatsApp Business phone number ID (to be configured)

## Running
```
uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

## Tech Stack
- Python 3.11, FastAPI, Uvicorn
- pydantic-settings for configuration
- httpx for outbound HTTP calls
- Meta WhatsApp Business Cloud API (Graph API v21.0)

## Meta Webhook Setup
1. Go to Meta Developer Console > Your App > WhatsApp > Configuration
2. Set Callback URL to: `https://<your-replit-domain>/api/v1/webhook`
3. Set Verify Token to match `WHATSAPP_VERIFY_TOKEN` env var
4. Subscribe to messages webhook field
