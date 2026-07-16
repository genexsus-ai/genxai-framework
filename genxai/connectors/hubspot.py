"""HubSpot connector implementation (CRM v3 API, private-app token)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .base import Connector

logger = logging.getLogger(__name__)


class HubSpotConnector(Connector):
    """HubSpot CRM connector using a private-app access token.

    Notes:
        - Create a private app in HubSpot (Settings -> Integrations ->
          Private Apps) with the crm.objects.contacts / deals scopes and use
          its access token.
        - `create_or_update_contact` upserts by email: it searches first and
          patches the existing contact, otherwise creates a new one.
    """

    def __init__(
        self,
        connector_id: str,
        access_token: str,
        name: str | None = None,
        base_url: str = "https://api.hubapi.com",
        timeout: float = 15.0,
    ) -> None:
        super().__init__(connector_id=connector_id, name=name)
        self.access_token = access_token
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
            raise ValueError("HubSpot access_token must be provided")

    # ------------------------------------------------------------- contacts

    async def find_contact_by_email(self, email: str) -> dict[str, Any] | None:
        """Return the contact with this email, or None."""
        data = await self._request(
            "POST",
            "/crm/v3/objects/contacts/search",
            {
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": "email",
                                "operator": "EQ",
                                "value": email,
                            }
                        ]
                    }
                ],
                "limit": 1,
            },
        )
        results = data.get("results") or []
        return results[0] if results else None

    async def create_or_update_contact(
        self,
        email: str,
        properties: dict[str, Any] | None = None,
        firstname: str | None = None,
        lastname: str | None = None,
        phone: str | None = None,
    ) -> dict[str, Any]:
        """Upsert a contact by email; scalar params merge into properties."""
        props: dict[str, Any] = {"email": email, **(properties or {})}
        if firstname:
            props["firstname"] = firstname
        if lastname:
            props["lastname"] = lastname
        if phone:
            props["phone"] = phone

        existing = await self.find_contact_by_email(email)
        if existing:
            contact_id = existing["id"]
            updated = await self._request(
                "PATCH",
                f"/crm/v3/objects/contacts/{contact_id}",
                {"properties": props},
            )
            updated["_action"] = "updated"
            return updated
        created = await self._request(
            "POST", "/crm/v3/objects/contacts", {"properties": props}
        )
        created["_action"] = "created"
        return created

    async def search_contacts(
        self, query: str, limit: int = 10
    ) -> dict[str, Any]:
        """Free-text search over contacts (name, email, phone, company)."""
        return await self._request(
            "POST",
            "/crm/v3/objects/contacts/search",
            {"query": query, "limit": max(1, min(int(limit), 100))},
        )

    # ---------------------------------------------------------------- deals

    async def create_deal(
        self,
        dealname: str,
        amount: float | str | None = None,
        pipeline: str | None = None,
        dealstage: str | None = None,
        contact_id: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a deal, optionally associated with a contact."""
        props: dict[str, Any] = {"dealname": dealname, **(properties or {})}
        if amount is not None:
            props["amount"] = str(amount)
        if pipeline:
            props["pipeline"] = pipeline
        if dealstage:
            props["dealstage"] = dealstage

        payload: dict[str, Any] = {"properties": props}
        if contact_id:
            # 3 = HubSpot-defined deal->contact association type
            payload["associations"] = [
                {
                    "to": {"id": str(contact_id)},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 3,
                        }
                    ],
                }
            ]
        return await self._request("POST", "/crm/v3/objects/deals", payload)

    # ------------------------------------------------------------- plumbing

    async def handle_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> None:
        """Handle an inbound HubSpot webhook payload and emit it downstream."""
        await self.emit(payload=payload, metadata={"headers": headers or {}})

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        await self._ensure_client()
        assert self._client is not None
        response = await self._client.request(method, path, json=payload)
        if response.status_code >= 400:
            raise ValueError(
                f"HubSpot API error ({response.status_code}): {response.text}"
            )
        return response.json() if response.content else {}

    async def _ensure_client(self) -> None:
        if self._client is not None:
            return
        async with self._lock:
            if self._client is None:
                await self._start()
