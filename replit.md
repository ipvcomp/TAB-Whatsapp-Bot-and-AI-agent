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
│   ├── contact_service.py  # Contact upsert, retrieval with dedup logic
│   └── message_service.py  # Message persistence with content extraction
└── utils/               # Shared utilities (to be implemented)
main.py                  # Entry point (imports app from app.main)
```

## Key Endpoints
- `GET /` - App info
- `GET /api/v1/health` - Health check (includes database connection status)
- `GET /api/v1/webhook` - Meta webhook verification (hub.mode, hub.verify_token, hub.challenge)
- `POST /api/v1/webhook` - Receives WhatsApp message/status webhook events

## Environment Variables / Secrets
- `WHATSAPP_VERIFY_TOKEN` - Token for Meta webhook verification
- `WHATSAPP_API_TOKEN` - Meta Graph API access token (to be configured)
- `WHATSAPP_PHONE_NUMBER_ID` - WhatsApp Business phone number ID (to be configured)
- `MONGODB_URI` - MongoDB connection string (secret)
- `MONGODB_DB_NAME` - Database name (defaults to tab_wappbot_ai_stg_db)

## Running
```
uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

## Tech Stack
- Python 3.11, FastAPI, Uvicorn, Gunicorn (production)
- pydantic-settings for configuration
- motor (async MongoDB driver) + pymongo
- httpx for outbound HTTP calls
- Meta WhatsApp Business Cloud API (Graph API v21.0)

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
- **messages** — Stores all inbound messages (unique on `message_id`)
  - Fields: message_id, contact_wa_id, phone_number_id, business_phone, direction, type, content, context, wa_timestamp, created_at, errors
  - Indexes: message_id (unique), contact_wa_id, phone_number_id, direction, type, wa_timestamp, compound (contact_wa_id + wa_timestamp)
  - Supports all message types: text, image, audio, video, document, location, reaction, sticker, interactive, button

## Meta Webhook Setup
1. Go to Meta Developer Console > Your App > WhatsApp > Configuration
2. Set Callback URL to: `https://tab-whatsapp-bot-and-ai-agent.replit.app/api/v1/webhook`
3. Set Verify Token to match `WHATSAPP_VERIFY_TOKEN` env var
4. Subscribe to: messages, message_echoes, account_alerts, phone_number_quality_update, message_template_status_update, security

## Deployment
- Published URL: https://tab-whatsapp-bot-and-ai-agent.replit.app
- Deployment target: autoscale
- Production server: gunicorn with uvicorn workers
