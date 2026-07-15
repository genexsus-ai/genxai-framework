"""WhatsApp connector implementation (Meta WhatsApp Business Cloud API)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .base import Connector

logger = logging.getLogger(__name__)


class WhatsAppConnector(Connector):
    """WhatsApp connector using Meta's Business Cloud API.

    Notes:
        - Outgoing messages are sent through the Graph API using a permanent
          (or long-lived) access token and the business phone number id.
        - Inbound messages arrive via Meta webhooks; call `handle_event` from
          your webhook route to emit them downstream.
        - Free-form text can only be sent inside the 24-hour customer service
          window; outside it, use `send_template` with an approved template.
    """

    def __init__(
        self,
        connector_id: str,
        access_token: str,
        phone_number_id: str,
        name: str | None = None,
        base_url: str = "https://graph.facebook.com/v21.0",
        timeout: float = 10.0,
    ) -> None:
        super().__init__(connector_id=connector_id, name=name)
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def _start(self) -> None:
        if not self._client:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )

    async def _stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def validate_config(self) -> None:
        if not self.access_token:
            raise ValueError("WhatsApp access_token must be provided")
        if not self.phone_number_id:
            raise ValueError("WhatsApp phone_number_id must be provided")

    async def send_message(
        self,
        to: str,
        text: str,
        preview_url: bool = False,
    ) -> dict[str, Any]:
        """Send a free-form text message (24h customer-service window only)."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text, "preview_url": preview_url},
        }
        return await self._post(f"/{self.phone_number_id}/messages", payload)

    async def send_template(
        self,
        to: str,
        template: str,
        language: str = "en_US",
        components: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send an approved template message (works outside the 24h window)."""
        template_payload: dict[str, Any] = {
            "name": template,
            "language": {"code": language},
        }
        if components:
            template_payload["components"] = components
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": template_payload,
        }
        return await self._post(f"/{self.phone_number_id}/messages", payload)

    async def mark_read(self, message_id: str) -> dict[str, Any]:
        """Mark an inbound message as read (shows the blue ticks)."""
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        return await self._post(f"/{self.phone_number_id}/messages", payload)

    async def handle_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> None:
        """Handle an inbound Meta webhook payload and emit it downstream."""
        await self.emit(payload=payload, metadata={"headers": headers or {}})

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_client()
        assert self._client is not None
        response = await self._client.post(path, json=payload)
        if response.status_code >= 400:
            # Graph API errors carry a JSON body worth surfacing verbatim.
            raise ValueError(
                f"WhatsApp API error ({response.status_code}): {response.text}"
            )
        return response.json()

    async def _ensure_client(self) -> None:
        if self._client is not None:
            return
        async with self._lock:
            if self._client is None:
                await self._start()
