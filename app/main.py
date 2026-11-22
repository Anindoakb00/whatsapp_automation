"""FastAPI entrypoint: webhook verification, event handling, health check."""

import hashlib
import hmac
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Query, Request, Response

from app import __version__
from app.config import settings
from app.engine.llm import LLMClient
from app.engine.router import BusinessProfile, OutgoingMessage, Router
from app.engine.session import InMemorySessionStore
from app.whatsapp_client import WhatsAppClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("whatsapp_bot")

BASE_DIR = Path(__file__).resolve().parent.parent

profile = BusinessProfile.load(BASE_DIR / "profiles", settings.ACTIVE_PROFILE)
logger.info("Active profile: %s (%s)", profile.business_name, profile.industry)

sessions = InMemorySessionStore()
llm = LLMClient(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL,
    model=settings.LLM_MODEL,
    max_history=settings.LLM_MAX_HISTORY,
)
router = Router(profile, sessions, llm, reset_keywords=settings.RESET_KEYWORDS)
wa_client = WhatsAppClient(base_url=settings.graph_base_url, token=settings.WHATSAPP_TOKEN)

app = FastAPI(title="WhatsApp Business Chatbot", version=__version__)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness check with configuration status (no secrets exposed)."""
    return {
        "status": "ok",
        "version": __version__,
        "profile": profile.business_name,
        "industry": profile.industry,
        "whatsapp_configured": settings.whatsapp_configured,
        "llm_enabled": llm.enabled,
    }


@app.get("/webhook")
async def verify_webhook(
    hub_mode: Optional[str] = Query(default=None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(default=None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(default=None, alias="hub.challenge"),
) -> Response:
    """Meta webhook verification handshake (GET)."""
    if (
        hub_mode == "subscribe"
        and hub_verify_token
        and hub_verify_token == settings.VERIFY_TOKEN
        and hub_challenge
    ):
        logger.info("Webhook verified successfully.")
        return Response(content=hub_challenge, media_type="text/plain")
    logger.warning("Webhook verification failed (mode=%r).", hub_mode)
    return Response(content="Verification failed.", status_code=403)


def _signature_valid(raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Validate X-Hub-Signature-256; skipped (valid) when APP_SECRET is unset."""
    if not settings.APP_SECRET:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(settings.APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


def _session_key(message: dict[str, Any]) -> str:
    """Sender identity: phone `from`, or Meta's Business-Scoped User ID (`user_id`)."""
    key = message.get("from") or message.get("user_id") or ""
    return str(key)


def _extract_incoming(value: dict[str, Any]) -> Optional[tuple[str, dict[str, Any]]]:
    """Return (session_key, message) for the first user message, else None.

    Status-only updates (`statuses` array: sent/delivered/read receipts) return
    None so the bot never loops on delivery receipts.
    """
    for entry in value.get("entry", []):
        for change in entry.get("changes", []):
            change_value = change.get("value", {})
            if "statuses" in change_value and "messages" not in change_value:
                logger.debug("Ignoring status-only update.")
                continue
            for message in change_value.get("messages", []):
                key = _session_key(message)
                if key:
                    return key, message
    return None


def _parse_user_input(message: dict[str, Any]) -> tuple[str, Optional[str]]:
    """Return (text, interactive_id) from an incoming message dict."""
    msg_type = message.get("type", "")
    if msg_type == "text":
        return str(message.get("text", {}).get("body", "")), None
    if msg_type == "interactive":
        interactive = message.get("interactive", {})
        if interactive.get("type") == "button_reply":
            reply = interactive.get("button_reply", {})
            return str(reply.get("title", "")), str(reply.get("id", "")) or None
        if interactive.get("type") == "list_reply":
            reply = interactive.get("list_reply", {})
            return str(reply.get("title", "")), str(reply.get("id", "")) or None
    logger.info("Unsupported message type %r; treating as empty text.", msg_type)
    return "", None


async def _send_reply(to: str, reply: OutgoingMessage) -> None:
    """Render one router reply through the WhatsApp client."""
    if reply.kind == "buttons":
        await wa_client.send_buttons(to, reply.body, reply.buttons, header=reply.header)
    elif reply.kind == "list":
        await wa_client.send_list(
            to, reply.body, reply.list_button_text, reply.list_sections, header=reply.header
        )
    else:
        await wa_client.send_text(to, reply.body)


@app.post("/webhook")
async def receive_webhook(request: Request) -> Response:
    """Receive WhatsApp events; always 200 OK so Meta never retries a storm."""
    raw_body = await request.body()
    if not _signature_valid(raw_body, request.headers.get("X-Hub-Signature-256")):
        logger.warning("Dropping webhook with invalid signature.")
        return Response(content="Invalid signature.", status_code=403)

    try:
        payload: dict[str, Any] = await request.json()
    except Exception as exc:
        logger.warning("Dropping non-JSON webhook body: %s", exc)
        return Response(content="OK")  # Still 200: nothing actionable arrived.

    if payload.get("object") != "whatsapp_business_account":
        return Response(content="OK")  # Not for us; acknowledge and ignore.

    try:
        incoming = _extract_incoming(payload)
        if incoming is None:
            return Response(content="OK")  # Status receipt or empty update.
        session_key, message = incoming

        message_id = message.get("id")
        if message_id:
            await wa_client.mark_as_read(str(message_id))

        user_text, interactive_id = _parse_user_input(message)
        replies = await router.route(session_key, user_text, interactive_id)
        for reply in replies:
            await _send_reply(session_key, reply)
    except Exception:
        logger.exception("Error handling webhook; returning 200 to avoid Meta retries.")

    return Response(content="OK")


@app.on_event("shutdown")
async def _shutdown() -> None:
    await wa_client.close()
