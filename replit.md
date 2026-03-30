# WhatsApp Bot SaaS Backend

## Overview
The WhatsApp Bot SaaS Backend is a FastAPI-based application designed to integrate with the Meta WhatsApp Business API using Meta's Cloud API. It provides a robust backend for managing WhatsApp bot interactions, focusing on policy creation and general user inquiries. The system uses MongoDB for data persistence and incorporates an LLM for dynamic responses and intelligent input extraction, with a static auto-reply system as a fallback. The project aims to streamline customer interactions, automate policy purchasing, and provide a scalable, intelligent messaging solution.

## User Preferences
I prefer detailed explanations of complex functionalities and architectural decisions. For coding, I favor clear, maintainable Python code following FastAPI best practices. I expect the agent to prioritize the implementation of core features and integrate new functionalities iteratively. Before making any major architectural changes or introducing new external dependencies, please ask for my approval. Ensure that all database schema modifications are thoroughly discussed and documented.

## System Architecture
The backend is built with FastAPI, using `pydantic-settings` for configuration and `motor` for asynchronous MongoDB interactions.

### UI/UX Decisions
The bot primarily interacts via WhatsApp messages, supporting interactive buttons, lists, and rich media. The policy creation flow is designed as a guided conversation, breaking down complex inputs into simple, sequential steps.

### Technical Implementations
- **FastAPI Application:** Main application factory handles lifespan events for database connection management.
- **API Versioning:** Uses `/api/v1` for current API endpoints.
- **Health Checks:** Includes `/api/v1/health` with database connection status.
- **Webhook Handling:** Dedicated endpoint `/api/v1/webhook` for Meta WhatsApp events (verification and message/status events).
- **Core Services:**
    - `ContactService`: Manages WhatsApp contact information, ensuring uniqueness and updating details.
    - `MessageService`: Persists all inbound and outbound messages, including content extraction and linking replies to original messages.
    - `WhatsAppService`: Client for Meta Graph API to send messages.
    - `LLMService`: Integrates with an external Large Language Model for generic Q&A and specific input extraction within flows.
    - `LLMLogService`: Logs all LLM requests and raw responses for traceability and debugging.
    - `SessionService`: Maintains user conversation state for LLM and policy flows.
    - `PolicyFlowService`: Manages the multi-step policy creation process.
    - `PolicyService`: Handles CRUD operations for user policies.

### Feature Specifications
- **Dynamic Responses:** Integrates with an LLM for conversational AI, providing flexible answers and intent recognition.
- **Static Auto-Reply:** A robust fallback system for predefined greetings, help, and media acknowledgments, active when the LLM is unavailable or not configured.
- **Policy Creation Flow:** A guided, multi-step process for users to purchase insurance policies. MSISDN auto-derived from the user's WhatsApp number (confirmed at flow start). Flow: (1) Greetings → (2) Service offering → (3) MSISDN confirm → (4) Departure Airport (search by first 3 chars of name/code, always shows list menu even for single result) → (5) Airport confirm (Confirm/Change buttons showing airport name, code, country) → (6) Product selection (fetched by airport's country_iso2) → (7) Product confirmation → (8) Dep date → (9) Dep time → (10) Arrival airport → (11) Arr date → (12) Arr time → (13) Booking ref → (14) Flight no → (15) First name → (16) Last name → (17) Email → (18) KYC Country (which country issued biometric ID — saved as metadata, independent of product country) → (19) KYC ID type (NIN/BVN) + number → (20) Details confirmation → (21) Payment intro + method → (22) Account number → (23) Bank name search (3 chars) → (24) Bank selection → (25) Boarding pass choice (Upload now / Upload later) → (26) Policy summary confirmation (Yes, Submit / No, Change details) → (27) Policy submission + final summary. Progress indicators show "Step X of 5" at each major transition (1=Flight & Product, 2=Travel Details, 3=Personal Details, 4=Payment & Bank Details, 5=Almost done/Boarding Pass). Itinerary fields use direct code validation (no LLM). Names use LLM extract only when user provides a sentence instead of a single name; otherwise accepted directly. Bank search fetches all banks, filters locally by user's 3-char input, then shows matching banks. Boarding pass can be uploaded later via the "Submit Boarding Pass" welcome button.
- **Policy Submission:** Policy is submitted after user confirms the summary. If boarding pass was uploaded, it's included in the submission as multipart/form-data to `POST https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies` with `channel=WHATSAPP`. All collected data is mapped: productId from selected product, msisdn with `+` prefix, BVN/NIN based on id_type, accountName derived from firstName+lastName, bank_code from selected bank, optional branch_code if returned by bank API, dates converted from DD/MM/YYYY → DD-MM-YYYY for API. On success the policy status is updated to `submitted` and the policy reference shown to user. On failure the user receives a summary with error details and a Retry Submission button.
- **Submit Boarding Pass Flow:** A separate flow triggered by the "Submit Boarding Pass" welcome button. Fetches all policies linked to the user's WhatsApp number via `GET /api/tab-plc/policies/by-msisdn/{msisdn}`, shows a paginated scrollable list (6 per page, active policies first then by date desc), displays a detailed policy summary on selection (code, status, product, route, flight, premium, coverage, boarding pass status), handles both upload (new) and replace (existing) scenarios, downloads the file from WhatsApp and POSTs it to `POST /api/tab-plc/policies/upload-boarding-pass` as multipart/form-data with `policyCode` and `file`. Supported formats: JPG, PNG, WebP, PDF. Cancel supported at every step.
- **Traceability:** Comprehensive logging for all messages, LLM interactions, and policy creation steps, linked for end-to-end debugging.
- **Shortcuts & Navigation:** Supports in-chat commands for quick navigation within flows (e.g., `#menu`, `#back`, `#cancel`).

### System Design Choices
- **Asynchronous Operations:** Leverages Python's `asyncio` with FastAPI and `motor` for non-blocking I/O, ensuring high concurrency.
- **Containerization:** Designed for Docker deployment, including multi-stage builds and non-root user execution for security.
- **Configuration Management:** Uses `pydantic-settings` for environment-driven configuration.
- **Database Schema:** MongoDB collections are designed to store:
    - **contacts:** User profiles with `wa_id` as unique identifier.
    - **messages:** All communications, linked by `message_id` and `contact_wa_id`.
    - **sessions:** LLM conversation state per user.
    - **llm_logs:** Raw LLM request/response for auditing.
    - **policies:** Records of policy creation attempts, tracking flow status and collected data. Stores `nin` and `bvn` as separate top-level fields (not nested in `personal_details`). Includes `kyc_country_code` and `kyc_country_name` (country that issued the user's biometric ID, independent of product country). Includes `payout_method`, `account_number`, and `itinerary` (with departure/arrival airport, dates, times, booking ref, flight no) as separate fields.
- **Welcome Message Image:** The welcome message includes `app/services/Image.jpeg` as a header image, uploaded to WhatsApp Media API on first use and cached in memory.
- **No Policy Management Menu:** The intermediate "Policy Management" menu has been removed. Typing "policy" (or any policy keyword) shows the welcome message with CTA buttons (Purchase Policy, Submit Boarding Pass, Get Support). The flow always starts from the welcome screen.
- **Text-to-Button Matching:** Users can type button labels instead of tapping buttons (e.g., "purchase policy", "submit boarding pass", "view products", etc.) — matching is case-insensitive with whitespace normalization.
- **Message Routing Priority:** Welcome buttons → #shortcuts → greeting (only when NOT in active flow) → policy keyword (shows welcome when NOT in flow) → welcome text matching → shortcut commands (in-flow) → boarding pass flow → policy flow → LLM → static auto-reply fallback.

## External Dependencies
- **Meta WhatsApp Business Cloud API (Graph API v22.0):** Core integration for sending and receiving WhatsApp messages.
- **MongoDB Atlas:** Cloud-hosted NoSQL database for all data persistence.
- **External LLM Service:** An external API (e.g., `https://staging-tab-whatsappllm.ipurvey.com`) for generic Q&A (`/api/v1/generic`) and input extraction (`/api/v1/extract`).
- **Ipurvey APIs (for policy flow):**
    - Products API: `https://dev-ilekun-ipv.ipurvey.com/api/v1/tab-pc/products/getByCountry/{COUNTRY_CODE}`
    - Payment Methods API: `https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/payout-method/types`
    - Banks API: `https://dev-ilekun-ipv.ipurvey.com/api/tab-plc/policies/payout-method/banks?countryCode={COUNTRY_CODE}`
    - Airports API: `https://dev-ilekun-ipv.ipurvey.com/api/v2/airports/search?search={CITY_OR_STATE}`