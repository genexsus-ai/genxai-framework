"""AWS S3 connector: list, download, and upload bucket objects.

Downloads land in the workflow file store as file references (the same
refs excel_read / file_content consume); uploads accept a file reference
or inline text. ``endpoint_url`` supports S3-compatible stores (MinIO,
Cloudflare R2, DigitalOcean Spaces).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import Connector

logger = logging.getLogger(__name__)

MAX_OBJECT_BYTES = 50 * 1024 * 1024


class S3Connector(Connector):
    """S3 (and S3-compatible) object storage connector."""

    def __init__(
        self,
        connector_id: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(connector_id=connector_id, name=name)
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region = region or "us-east-1"
        self.endpoint_url = endpoint_url or None
        self._client: Any | None = None

    async def validate_config(self) -> None:
        if not self.access_key_id or not self.secret_access_key:
            raise ValueError("access_key_id and secret_access_key must be provided")

    async def _start(self) -> None:
        return None

    async def _stop(self) -> None:
        if self._client is not None:
            client = self._client
            self._client = None
            await asyncio.to_thread(client.close)

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "s3",
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name=self.region,
                endpoint_url=self.endpoint_url,
            )
        return self._client

    # ------------------------------------------------------------- actions

    async def list_objects(
        self, bucket: str, prefix: str = "", max_keys: int = 100
    ) -> dict[str, Any]:
        """List objects in a bucket (optionally under a prefix)."""

        def _run() -> dict[str, Any]:
            response = self._get_client().list_objects_v2(
                Bucket=bucket, Prefix=prefix, MaxKeys=max(1, min(int(max_keys), 1000))
            )
            return {
                "objects": [
                    {
                        "key": item["Key"],
                        "size": item["Size"],
                        "last_modified": str(item.get("LastModified", "")),
                    }
                    for item in response.get("Contents", [])
                ],
                "truncated": bool(response.get("IsTruncated")),
            }

        return await asyncio.to_thread(_run)

    async def get_object(self, bucket: str, key: str) -> dict[str, Any]:
        """Download an object into the workflow file store; returns a file ref."""
        from genxai.core.files import get_file_store

        def _run() -> dict[str, Any]:
            response = self._get_client().get_object(Bucket=bucket, Key=key)
            data = response["Body"].read(MAX_OBJECT_BYTES + 1)
            if len(data) > MAX_OBJECT_BYTES:
                raise ValueError(
                    f"Object too large: over {MAX_OBJECT_BYTES} bytes"
                )
            media_type = response.get("ContentType") or None
            ref = get_file_store().save_bytes(
                data, name=key.rsplit("/", 1)[-1] or key, media_type=media_type
            )
            return {"file": ref, "bucket": bucket, "key": key}

        return await asyncio.to_thread(_run)

    async def put_object(
        self,
        bucket: str,
        key: str,
        file: Any = None,
        content: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """Upload a file reference (or inline text) to a bucket key."""
        from genxai.core.files import get_file_store, is_file_ref

        if (file is None) == (content is None):
            raise ValueError("Provide exactly one of file / content")
        if file is not None:
            if not (is_file_ref(file) or isinstance(file, str)):
                raise ValueError("'file' must be a file reference or file id")
            data = get_file_store().read_bytes(file)
            if content_type is None and is_file_ref(file):
                content_type = file.get("media_type")
        else:
            data = str(content).encode("utf-8")
            content_type = content_type or "text/plain"

        def _run() -> dict[str, Any]:
            response = self._get_client().put_object(
                Bucket=bucket,
                Key=key,
                Body=data,
                ContentType=content_type or "application/octet-stream",
            )
            return {
                "bucket": bucket,
                "key": key,
                "size": len(data),
                "etag": str(response.get("ETag", "")).strip('"'),
            }

        return await asyncio.to_thread(_run)
