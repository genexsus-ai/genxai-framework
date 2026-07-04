"""Webhook connector implementation."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from .base import Connector

logger = logging.getLogger(__name__)


class WebhookConnector(Connector):
    """HTTP webhook connector.

    Provides a handler that can be mounted in FastAPI or other ASGI frameworks.
    """

    def __init__(
        self,
        connector_id: str,
        secret: str | None = None,
        name: str | None = None,
        header_name: str = "X-GenXAI-Signature",
        hash_alg: str = "sha256",
    ) -> None:
        super().__init__(connector_id=connector_id, name=name)
        self.secret = secret
        self.header_name = header_name
        self.hash_alg = hash_alg

    async def _start(self) -> None:
        logger.debug("Webhook connector %s ready for requests", self.connector_id)

    async def _stop(self) -> None:
        logger.debug("Webhook connector %s stopped", self.connector_id)

    async def validate_config(self) -> None:
        if self.secret is not None and not isinstance(self.secret, str):
            raise ValueError("Webhook secret must be a string")
        if not self.header_name:
            raise ValueError("Webhook header_name must be set")
        if not hasattr(hashlib, self.hash_alg):
            raise ValueError(f"Unsupported hash algorithm: {self.hash_alg}")

    def validate_signature(self, payload: bytes, signature: str | None) -> bool:
        if not self.secret:
            return True
        if not signature:
            return False

        digest = hmac.new(self.secret.encode(), payload, getattr(hashlib, self.hash_alg)).hexdigest()
        expected = f"{self.hash_alg}={digest}"
        return hmac.compare_digest(expected, signature)

    async def handle_request(
        self,
        payload: dict[str, Any],
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = headers or {}
        signature = headers.get(self.header_name)

        if self.secret and raw_body is not None:
            if not self.validate_signature(raw_body, signature):
                logger.warning("Webhook signature validation failed for %s", self.connector_id)
                return {"status": "rejected", "reason": "invalid signature"}

        await self.emit(payload=payload, metadata={"headers": headers})
        return {"status": "accepted", "connector_id": self.connector_id}
