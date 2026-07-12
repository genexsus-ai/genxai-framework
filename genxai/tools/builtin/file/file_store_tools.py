"""Tools that move binary data in and out of the workflow file store.

Files travel through workflow state as small JSON references (see
``genxai.core.files``); these tools are the producers and consumers.
"""

from __future__ import annotations

from typing import Any

from genxai.core.files import get_file_store, is_file_ref
from genxai.tools.base import Tool, ToolCategory, ToolMetadata, ToolParameter

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
MAX_CONTENT_CHARS = 200_000


class FileDownloadTool(Tool):
    """Download a URL into the file store; returns a file reference."""

    def __init__(self) -> None:
        super().__init__(
            metadata=ToolMetadata(
                name="file_download",
                description=(
                    "Download a file from a URL into the workflow file store "
                    "and return a file reference later nodes can use"
                ),
                category=ToolCategory.WEB,
                tags=["file", "download", "binary", "web"],
                version="1.0.0",
            ),
            parameters=[
                ToolParameter(
                    name="url",
                    type="string",
                    description="URL to download",
                    required=True,
                    pattern=r"^https?://",
                ),
                ToolParameter(
                    name="name",
                    type="string",
                    description="File name for the reference (default: from URL)",
                    required=False,
                ),
            ],
        )

    async def _execute(self, **kwargs: Any) -> dict[str, Any]:
        import httpx

        url: str = kwargs["url"]
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.content
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f"File too large: {len(data)} bytes (limit {MAX_DOWNLOAD_BYTES})"
            )
        name = kwargs.get("name") or url.rstrip("/").split("/")[-1] or "download"
        media_type = response.headers.get("content-type", "").split(";")[0] or None
        ref = get_file_store().save_bytes(data, name=name, media_type=media_type)
        return {"file": ref}


class FileWriteTool(Tool):
    """Write text content into the file store; returns a file reference."""

    def __init__(self) -> None:
        super().__init__(
            metadata=ToolMetadata(
                name="file_write",
                description=(
                    "Write text content (e.g. a generated CSV or report) into "
                    "the workflow file store and return a file reference"
                ),
                category=ToolCategory.FILE,
                tags=["file", "write", "binary"],
                version="1.0.0",
            ),
            parameters=[
                ToolParameter(
                    name="content",
                    type="string",
                    description="Text content to store",
                    required=True,
                ),
                ToolParameter(
                    name="name",
                    type="string",
                    description="File name, e.g. report.csv",
                    required=True,
                ),
                ToolParameter(
                    name="media_type",
                    type="string",
                    description="MIME type (default text/plain)",
                    required=False,
                ),
            ],
        )

    async def _execute(self, **kwargs: Any) -> dict[str, Any]:
        ref = get_file_store().save_bytes(
            str(kwargs["content"]).encode("utf-8"),
            name=kwargs["name"],
            media_type=kwargs.get("media_type") or "text/plain",
        )
        return {"file": ref}


class FileContentTool(Tool):
    """Read a stored file's content as text."""

    def __init__(self) -> None:
        super().__init__(
            metadata=ToolMetadata(
                name="file_content",
                description=(
                    "Read a stored workflow file (by reference or id) as text, "
                    "so agents and templates can use its content"
                ),
                category=ToolCategory.FILE,
                tags=["file", "read", "binary"],
                version="1.0.0",
            ),
            parameters=[
                ToolParameter(
                    name="file",
                    type="object",
                    description="File reference from an upstream node (or id string)",
                    required=True,
                ),
                ToolParameter(
                    name="encoding",
                    type="string",
                    description="Text encoding",
                    required=False,
                    default="utf-8",
                ),
                ToolParameter(
                    name="max_chars",
                    type="number",
                    description="Truncate content beyond this many characters",
                    required=False,
                    default=MAX_CONTENT_CHARS,
                ),
            ],
        )

    async def _execute(self, **kwargs: Any) -> dict[str, Any]:
        ref = kwargs["file"]
        if not (is_file_ref(ref) or isinstance(ref, str)):
            raise ValueError("'file' must be a file reference or file id")
        data = get_file_store().read_bytes(ref)
        text = data.decode(str(kwargs.get("encoding") or "utf-8"), errors="replace")
        max_chars = int(kwargs.get("max_chars") or MAX_CONTENT_CHARS)
        truncated = len(text) > max_chars
        return {
            "content": text[:max_chars],
            "truncated": truncated,
            "size": len(data),
        }
