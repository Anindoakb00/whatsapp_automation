# WhatsApp Business Chatbot — v1.0.0

A modular, production-ready, industry-agnostic WhatsApp chatbot built with Python,
FastAPI, and the official Meta WhatsApp Cloud API (Graph API v26.0).

Switch industries by changing one line in `.env` (`ACTIVE_PROFILE`) — no code changes.

## How it works

**Hybrid Intelligence Router** (`app/engine/router.py`):

1. **Algorithmic (deterministic, runs first)** — exact menu/button matches, resets
   (`menu`, `hi`, `start`), FAQ keywords, and structured multi-step flows
   (Name → Date/Order ID → Confirmation) with per-user sessions.
2. **Artificial (LLM fallback)** — anything unmatched goes to an OpenAI-compatible
   LLM with the business profile context + conversation history, which answers
   naturally and suggests the next menu action.

## Project structure

```text
whatsapp-chatbot/
├── app/
│   ├── main.py            # FastAPI: GET/POST /webhook, /health
│   ├── config.py          # Pydantic BaseSettings from .env
│   ├── whatsapp_client.py # Async HTTPX client (text/buttons/list/mark_as_read)
│   └── engine/
│       ├── router.py      # Hybrid router + BusinessProfile loader
│       ├── session.py     # SessionStore interface + in-memory impl (swap for Redis in v2)
│       └── llm.py         # OpenAI-compatible LLM fallback (OpenAI or Gemini)
├── profiles/
│   ├── ecommerce.json     # ShopKart demo store
│   └── clinic.json        # CityCare Clinic demo
├── requirements.txt  .env.example  .gitignore  README.md
```

## Setup

```powershell
cd whatsapp-chatbot
C:\Python313\python.exe -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env with your Meta credentials (see below)
uvicorn app.main:app --reload --port 8000
```

Check `http://localhost:8000/health` — it reports version, active profile,
and whether WhatsApp/LLM are configured (no secrets exposed).

## Meta + ngrok webhook setup

1. **Meta Developers** ([developers.facebook.com](https://developers.facebook.com)):
   create an app → add the **WhatsApp** product → from **API Setup** copy the
   temporary access token and **Phone number ID** into `.env`
   (`WHATSAPP_TOKEN`, `PHONE_NUMBER_ID`), and set any `VERIFY_TOKEN` string.
2. **Expose local server:** `ngrok http 8000` → copy the `https://...` URL.
3. **Configure webhook** (WhatsApp → Configuration → Webhook):
   Callback URL `https://<ngrok-url>/webhook`, Verify token = your
   `VERIFY_TOKEN` → Verify and save → Subscribe to the **messages** field.
4. **Test number:** add your phone as a recipient in API Setup, then message
   the test number from WhatsApp.

To receive events from real customers later, complete business verification
and replace the temporary token with a permanent system-user token.

## v1 testing steps

1. `GET /health` returns `{"status": "ok", ...}`.
2. Webhook handshake: open
   `/webhook?hub.mode=subscribe&hub.verify_token=<VERIFY_TOKEN>&hub.challenge=test123`
   → responds `test123`.
3. WhatsApp → send `hi` → bot replies with the main-menu buttons.
4. Tap **Track Order** → send an order ID → reply `YES` → completion message.
5. Send `what is your return policy?` → deterministic FAQ answer.
6. Send free text (`do you sell headphones?`) → LLM answer + next-step nudge
   (or static fallback when `LLM_API_KEY` is unset).
7. Switch profile: set `ACTIVE_PROFILE=clinic` in `.env`, restart, send `menu`.

## Notes

- Every valid POST webhook returns `200 OK` immediately; delivery receipts
  (`statuses`) are ignored so the bot never loops.
- Sessions key on phone `from`, falling back to Meta's Business-Scoped User
  ID (`user_id`) when present.
- Set `APP_SECRET` to enforce `X-Hub-Signature-256` webhook verification.
- v2 roadmap: Redis/SQLite sessions, media templates, analytics, multi-number.
