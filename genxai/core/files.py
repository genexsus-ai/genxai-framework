"""Content-addressed file storage for binary data in workflows.

Workflow state is JSON end-to-end, so files never travel through it as
bytes. Instead the bytes land in a FileStore once (content-addressed by
sha256) and a small JSON-safe *reference* dict flows through node results,
templates, persistence, and events:

    {"__genxai_file__": True, "id": "<sha256>", "name": "report.pdf",
     "media_type": "application/pdf", "size": 48213}
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FILE_REF_KEY = "__genxai_file__"


def is_file_ref(value: Any) -> bool:
    """Whether a value is a workflow file reference."""
    return isinstance(value, dict) and value.get(FILE_REF_KEY) is True and "id" in value


class FileStore:
    """Stores file bytes under ``<base>/<id[:2]>/<id>`` with a metadata sidecar."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    def _content_path(self, file_id: str) -> Path:
        safe = "".join(c for c in file_id if c in "0123456789abcdef")
        if len(safe) != 64 or safe != file_id:
            raise ValueError(f"Invalid file id: {file_id!r}")
        return self.base_dir / safe[:2] / safe

    def save_bytes(
        self, data: bytes, name: str, media_type: str | None = None
    ) -> dict[str, Any]:
        """Store bytes (deduplicated by content hash); returns the file ref."""
        file_id = hashlib.sha256(data).hexdigest()
        path = self._content_path(file_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        metadata = {
            "id": file_id,
            "name": name,
            "media_type": media_type or "application/octet-stream",
            "size": len(data),
            "created_at": datetime.now(UTC).isoformat(),
        }
        path.with_suffix(".json").write_text(json.dumps(metadata, indent=2))
        return {
            FILE_REF_KEY: True,
            "id": file_id,
            "name": name,
            "media_type": metadata["media_type"],
            "size": len(data),
        }

    def _resolve_id(self, ref_or_id: Any) -> str:
        if is_file_ref(ref_or_id):
            return str(ref_or_id["id"])
        if isinstance(ref_or_id, str):
            return ref_or_id
        raise ValueError(f"Not a file reference or id: {ref_or_id!r}")

    def open_path(self, ref_or_id: Any) -> Path:
        path = self._content_path(self._resolve_id(ref_or_id))
        if not path.exists():
            raise FileNotFoundError(f"File not in store: {self._resolve_id(ref_or_id)}")
        return path

    def read_bytes(self, ref_or_id: Any) -> bytes:
        return self.open_path(ref_or_id).read_bytes()

    def get_metadata(self, ref_or_id: Any) -> dict[str, Any] | None:
        try:
            sidecar = self._content_path(self._resolve_id(ref_or_id)).with_suffix(".json")
        except ValueError:
            return None
        if not sidecar.exists():
            return None
        return json.loads(sidecar.read_text())


_store: FileStore | None = None


def configure_file_store(base_dir: Path) -> FileStore:
    """Point the process-global store at a directory (studio: data_dir/files)."""
    global _store
    _store = FileStore(base_dir)
    return _store


def get_file_store() -> FileStore:
    global _store
    if _store is None:
        base = os.environ.get("GENXAI_FILE_STORE_DIR")
        _store = FileStore(
            Path(base) if base else Path.home() / ".genxai" / "files"
        )
    return _store


def reset_file_store() -> None:
    global _store
    _store = None
