"""Async client for the Meta WhatsApp Cloud API (Graph API v26.0).

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api
"""

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class WhatsAppClient:
    """Sends messages via `https://graph.facebook.com/{version}/{phone_id}/messages`."""

    def __init__(self, base_url: str, token: str, timeout: float = 15.0) -> None:
        self._base_url = base_url
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(headers=self._headers, timeout=timeout)

    async def _post(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        """POST a payload to the messages endpoint; returns parsed JSON or None."""
        try:
            response = await self._client.post(self._base_url, json=payload)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            logger.info("WhatsApp API ok: type=%s", payload.get("type"))
            return data
        except httpx.HTTPStatusError as exc:
            logger.error(
                "WhatsApp API error: status=%s body=%s payload_type=%s",
                exc.response.status_code,
                exc.response.text[:500],
                payload.get("type"),
            )
        except httpx.HTTPError as exc:
            logger.error("WhatsApp request failed: %s", exc)
        return None

    async def send_text(self, to: str, body: str, preview_url: bool = False) -> Optional[dict[str, Any]]:
        """Send a plain text message."""
        return await self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": preview_url, "body": body},
            }
        )

    async def send_buttons(
        self, to: str, body: str, buttons: list[dict[str, str]], header: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """Send an interactive reply-button message (max 3 buttons).

        Each button is `{"id": "<callback-id>", "title": "<label ≤20 chars>"}`.
        """
        action_buttons = [
            {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
            for b in buttons[:3]
        ]
        interactive: dict[str, Any] = {
            "type": "button",
            "body": {"text": body},
            "action": {"buttons": action_buttons},
        }
        if header:
            interactive["header"] = {"type": "text", "text": header}
        return await self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": interactive,
            }
        )

    async def send_list(
        self,
        to: str,
        body: str,
        button_text: str,
        sections: list[dict[str, Any]],
        header: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Send an interactive list menu.

        Each section is `{"title": str, "rows": [{"id": str, "title": str,
        "description": str}]}`.
        """
        interactive: dict[str, Any] = {
            "type": "list",
            "body": {"text": body},
            "action": {"button": button_text[:20], "sections": sections},
        }
        if header:
            interactive["header"] = {"type": "text", "text": header}
        return await self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": interactive,
            }
        )

    async def mark_as_read(self, message_id: str) -> Optional[dict[str, Any]]:
        """Mark an incoming message as read (blue ticks, stops Meta retries)."""
        return await self._post(
            {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
            }
        )

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()
