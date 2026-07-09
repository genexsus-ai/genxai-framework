"""Email (SMTP) connector — send mail through any mailbox provider."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from .base import Connector

logger = logging.getLogger(__name__)


class EmailConnector(Connector):
    """Outbound email over SMTP (works with Gmail, Outlook, or any provider).

    Credentials are standard SMTP settings; `use_tls` selects STARTTLS on
    the given port (typically 587). Sending runs in a worker thread since
    smtplib is blocking.
    """

    def __init__(
        self,
        connector_id: str,
        host: str,
        from_email: str,
        port: int = 587,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        name: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        super().__init__(connector_id=connector_id, name=name)
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.from_email = from_email
        self.use_tls = bool(use_tls)
        self.timeout = timeout

    async def _start(self) -> None:  # connections are per-send
        return

    async def _stop(self) -> None:
        return

    async def validate_config(self) -> None:
        if not self.host:
            raise ValueError("Email connector requires an SMTP host")
        if not self.from_email:
            raise ValueError("Email connector requires a from_email address")

    def _send_sync(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            if self.use_tls:
                smtp.starttls()
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(message)

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        cc: str = "",
    ) -> dict[str, Any]:
        """Send an email. `to`/`cc` accept comma-separated addresses."""
        message = EmailMessage()
        message["From"] = self.from_email
        message["To"] = to
        message["Subject"] = subject
        if cc:
            message["Cc"] = cc
        if html:
            message.set_content("This message requires an HTML-capable client.")
            message.add_alternative(body, subtype="html")
        else:
            message.set_content(body)

        await asyncio.to_thread(self._send_sync, message)
        logger.info("Email sent to %s (subject: %s)", to, subject[:60])
        return {"sent": True, "to": to, "cc": cc or None, "subject": subject}
