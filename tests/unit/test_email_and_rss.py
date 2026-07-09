"""Unit tests for the Email (SMTP) connector and the RSS reader tool."""

from unittest.mock import MagicMock, patch

import pytest

from genxai.connectors import EmailConnector
from genxai.tools.builtin.web.rss_reader import RSSReaderTool, parse_feed

_RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Tech News</title>
  <item>
    <title>AI breakthrough</title>
    <link>https://example.com/ai</link>
    <pubDate>Wed, 08 Jul 2026 09:00:00 GMT</pubDate>
    <description>Big news in AI.</description>
  </item>
  <item>
    <title>Second story</title>
    <link>https://example.com/two</link>
    <pubDate>Wed, 08 Jul 2026 08:00:00 GMT</pubDate>
    <description>More news.</description>
  </item>
</channel></rss>"""

_ATOM_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Blog</title>
  <entry>
    <title>Atom entry</title>
    <link href="https://example.com/atom-1"/>
    <updated>2026-07-08T09:00:00Z</updated>
    <summary>An atom post.</summary>
  </entry>
</feed>"""


class TestParseFeed:
    def test_parses_rss(self):
        items = parse_feed(_RSS_XML, limit=10)
        assert len(items) == 2
        assert items[0] == {
            "title": "AI breakthrough",
            "link": "https://example.com/ai",
            "published": "Wed, 08 Jul 2026 09:00:00 GMT",
            "summary": "Big news in AI.",
        }

    def test_parses_atom(self):
        items = parse_feed(_ATOM_XML, limit=10)
        assert len(items) == 1
        assert items[0]["title"] == "Atom entry"
        assert items[0]["link"] == "https://example.com/atom-1"

    def test_limit_respected(self):
        assert len(parse_feed(_RSS_XML, limit=1)) == 1


class TestRSSReaderTool:
    @pytest.mark.asyncio
    async def test_execute_fetches_and_parses(self, monkeypatch):
        import httpx

        def fake_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_RSS_XML)

        original_client = httpx.AsyncClient

        def patched_client(**kwargs):
            kwargs["transport"] = httpx.MockTransport(fake_transport)
            return original_client(**kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", patched_client)

        result = await RSSReaderTool().execute(url="https://example.com/feed.xml", limit=1)

        assert result.success, result.error
        assert result.data["count"] == 1
        assert result.data["items"][0]["title"] == "AI breakthrough"


class TestEmailConnector:
    def _connector(self, **overrides) -> EmailConnector:
        config = {
            "connector_id": "test-email",
            "host": "smtp.example.com",
            "from_email": "bot@example.com",
            "username": "bot@example.com",
            "password": "secret",
        }
        config.update(overrides)
        return EmailConnector(**config)

    @pytest.mark.asyncio
    async def test_validate_config_requires_host_and_from(self):
        with pytest.raises(ValueError, match="SMTP host"):
            await self._connector(host="").validate_config()
        with pytest.raises(ValueError, match="from_email"):
            await self._connector(from_email="").validate_config()
        await self._connector().validate_config()  # no error

    @pytest.mark.asyncio
    async def test_send_email_uses_smtp(self):
        connector = self._connector()
        with patch("genxai.connectors.email_smtp.smtplib.SMTP") as smtp_cls:
            smtp = MagicMock()
            smtp_cls.return_value.__enter__.return_value = smtp

            result = await connector.send_email(to="me@example.com", subject="Digest", body="Hello")

        smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=15.0)
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("bot@example.com", "secret")
        message = smtp.send_message.call_args[0][0]
        assert message["To"] == "me@example.com"
        assert message["From"] == "bot@example.com"
        assert message["Subject"] == "Digest"
        assert result == {"sent": True, "to": "me@example.com", "cc": None, "subject": "Digest"}

    @pytest.mark.asyncio
    async def test_send_email_without_auth_or_tls(self):
        connector = self._connector(username="", password="", use_tls=False, port=25)
        with patch("genxai.connectors.email_smtp.smtplib.SMTP") as smtp_cls:
            smtp = MagicMock()
            smtp_cls.return_value.__enter__.return_value = smtp

            await connector.send_email(to="a@b.c", subject="s", body="b")

        smtp.starttls.assert_not_called()
        smtp.login.assert_not_called()
