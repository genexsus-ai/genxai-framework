"""RSS/Atom feed reader tool — credential-free news and update ingestion."""

import logging
import xml.etree.ElementTree as ET
from typing import Any

from genxai.tools.base import Tool, ToolCategory, ToolMetadata, ToolParameter

logger = logging.getLogger(__name__)

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _text(element: Any) -> str:
    return (element.text or "").strip() if element is not None else ""


def parse_feed(xml_text: str, limit: int) -> list[dict[str, str]]:
    """Parse RSS 2.0 or Atom XML into a list of item dicts."""
    root = ET.fromstring(xml_text)
    items: list[dict[str, str]] = []

    # RSS 2.0: <rss><channel><item>...
    for item in root.iter("item"):
        items.append(
            {
                "title": _text(item.find("title")),
                "link": _text(item.find("link")),
                "published": _text(item.find("pubDate")),
                "summary": _text(item.find("description")),
            }
        )
        if len(items) >= limit:
            return items

    # Atom: <feed><entry>...
    for entry in root.iter(f"{_ATOM_NS}entry"):
        link_el = entry.find(f"{_ATOM_NS}link")
        items.append(
            {
                "title": _text(entry.find(f"{_ATOM_NS}title")),
                "link": link_el.get("href", "") if link_el is not None else "",
                "published": _text(entry.find(f"{_ATOM_NS}updated"))
                or _text(entry.find(f"{_ATOM_NS}published")),
                "summary": _text(entry.find(f"{_ATOM_NS}summary")),
            }
        )
        if len(items) >= limit:
            break
    return items


class RSSReaderTool(Tool):
    """Fetch and parse an RSS or Atom feed into structured items."""

    def __init__(self) -> None:
        metadata = ToolMetadata(
            name="rss_reader",
            description=(
                "Fetch an RSS or Atom feed and return its items (title, link, "
                "published date, summary) — e.g. news headlines or blog updates"
            ),
            category=ToolCategory.WEB,
            tags=["rss", "atom", "feed", "news", "headlines", "web"],
            version="1.0.0",
        )
        parameters = [
            ToolParameter(
                name="url",
                type="string",
                description="Feed URL",
                required=True,
                pattern=r"^https?://",
            ),
            ToolParameter(
                name="limit",
                type="number",
                description="Maximum number of items to return",
                required=False,
                default=10,
                min_value=1,
                max_value=50,
            ),
        ]
        super().__init__(metadata, parameters)

    async def _execute(self, **kwargs: Any) -> dict[str, Any]:
        import httpx

        url: str = kwargs["url"]
        limit = int(kwargs.get("limit") or 10)

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()

        items = parse_feed(response.text, limit)
        return {"url": url, "count": len(items), "items": items}
