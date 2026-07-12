"""Tests for the S3 connector (stubbed boto3 client)."""

import pytest

from genxai.connectors.s3 import S3Connector
from genxai.core.files import configure_file_store, get_file_store, reset_file_store


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, limit: int) -> bytes:
        return self._data[:limit]


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {
            ("reports", "q2.csv"): b"region,total\neast,10\n",
        }
        self.puts: list[dict] = []

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        contents = [
            {"Key": key, "Size": len(data), "LastModified": "2026-07-12"}
            for (bucket, key), data in self.objects.items()
            if bucket == Bucket and key.startswith(Prefix)
        ]
        return {"Contents": contents[:MaxKeys], "IsTruncated": False}

    def get_object(self, Bucket, Key):
        data = self.objects[(Bucket, Key)]
        return {"Body": FakeBody(data), "ContentType": "text/csv"}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.puts.append(
            {"bucket": Bucket, "key": Key, "body": Body, "content_type": ContentType}
        )
        return {"ETag": '"abc123"'}

    def close(self):
        return None


@pytest.fixture
def connector(tmp_path, monkeypatch):
    configure_file_store(tmp_path / "files")
    conn = S3Connector(
        connector_id="test", access_key_id="ak", secret_access_key="sk"
    )
    fake = FakeS3Client()
    monkeypatch.setattr(conn, "_get_client", lambda: fake)
    conn._fake = fake  # type: ignore[attr-defined]
    yield conn
    reset_file_store()


async def test_list_objects(connector):
    result = await connector.list_objects("reports", prefix="q")
    assert result["objects"] == [
        {"key": "q2.csv", "size": 21, "last_modified": "2026-07-12"}
    ]


async def test_get_object_lands_in_file_store(connector):
    result = await connector.get_object("reports", "q2.csv")
    ref = result["file"]
    assert ref["name"] == "q2.csv"
    assert get_file_store().read_bytes(ref) == b"region,total\neast,10\n"


async def test_put_object_from_file_ref_and_text(connector):
    ref = get_file_store().save_bytes(b"payload", name="out.txt", media_type="text/plain")
    result = await connector.put_object("reports", "out.txt", file=ref)
    assert result["etag"] == "abc123"
    assert connector._fake.puts[0]["body"] == b"payload"
    assert connector._fake.puts[0]["content_type"] == "text/plain"

    await connector.put_object("reports", "note.txt", content="hello")
    assert connector._fake.puts[1]["body"] == b"hello"

    with pytest.raises(ValueError, match="exactly one"):
        await connector.put_object("reports", "x", file=ref, content="both")


async def test_validate_config():
    connector = S3Connector(connector_id="x", access_key_id="", secret_access_key="")
    with pytest.raises(ValueError):
        await connector.validate_config()
