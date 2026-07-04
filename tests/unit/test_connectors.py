"""Unit tests for connectors (base, registry, webhook, HTTP connectors, Kafka, SQS, config store)."""

import asyncio
import hashlib
import hmac
import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from genxai.connectors import (
    Connector,
    ConnectorEvent,
    ConnectorRegistry,
    ConnectorStatus,
    GitHubConnector,
    GoogleWorkspaceConnector,
    JiraConnector,
    KafkaConnector,
    NotionConnector,
    PostgresCDCConnector,
    SlackConnector,
    SQSConnector,
    WebhookConnector,
)
from genxai.connectors.config_store import ConnectorConfigEntry, ConnectorConfigStore


class DummyConnector(Connector):
    """Minimal concrete connector for base lifecycle tests."""

    def __init__(self, connector_id: str, fail_validate: bool = False, **kwargs):
        super().__init__(connector_id=connector_id, **kwargs)
        self.fail_validate = fail_validate
        self.started = 0
        self.stopped = 0

    async def validate_config(self) -> None:
        if self.fail_validate:
            raise ValueError("bad config")

    async def _start(self) -> None:
        self.started += 1

    async def _stop(self) -> None:
        self.stopped += 1


@pytest.fixture(autouse=True)
def clean_registry():
    ConnectorRegistry._connectors.clear()
    yield
    ConnectorRegistry._connectors.clear()


def _mock_client(handler, base_url):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)


# ---------------------------------------------------------------- base


def test_connector_event_defaults():
    event = ConnectorEvent(connector_id="c1", payload={"a": 1})
    assert event.connector_id == "c1"
    assert event.metadata == {}
    assert event.timestamp is not None


@pytest.mark.asyncio
async def test_lifecycle_and_health_check():
    connector = DummyConnector("c1")
    health = await connector.health_check()
    assert health["status"] == "not_running"

    await connector.start()
    assert connector.status == ConnectorStatus.RUNNING
    await connector.start()  # idempotent
    assert connector.started == 1

    health = await connector.health_check()
    assert health == {
        "connector_id": "c1",
        "status": "ok",
        "lifecycle": "running",
        "last_error": None,
    }

    await connector.stop()
    assert connector.status == ConnectorStatus.STOPPED
    await connector.stop()  # idempotent
    assert connector.stopped == 1


@pytest.mark.asyncio
async def test_validate_config_failure_sets_error():
    connector = DummyConnector("c1", fail_validate=True)
    with pytest.raises(ValueError):
        await connector.start()
    assert connector.status == ConnectorStatus.ERROR
    assert connector._last_error == "bad config"
    health = await connector.health_check()
    assert health["last_error"] == "bad config"


@pytest.mark.asyncio
async def test_emit_delivers_to_callbacks():
    connector = DummyConnector("c1")
    received = []

    async def cb(event):
        received.append(event.payload)

    connector.on_event(cb)
    await connector.emit(payload={"x": 1})
    await connector.emit(payload={"y": 2})
    assert received == [{"x": 1}, {"y": 2}]


@pytest.mark.asyncio
async def test_emit_without_subscribers_is_noop():
    connector = DummyConnector("c1")
    await connector.emit(payload={"x": 1})  # should not raise


# ---------------------------------------------------------------- registry


@pytest.mark.asyncio
async def test_registry_crud_and_lifecycle():
    c1 = DummyConnector("c1")
    c2 = DummyConnector("c2")
    ConnectorRegistry.register(c1)
    ConnectorRegistry.register(c2)
    ConnectorRegistry.register(c2)  # duplicate warns, keeps working

    assert ConnectorRegistry.get("c1") is c1
    assert set(ConnectorRegistry.list_all()) == {c1, c2}

    await ConnectorRegistry.start_all()
    assert c1.status == ConnectorStatus.RUNNING
    assert c2.status == ConnectorStatus.RUNNING

    await ConnectorRegistry.stop_all()
    assert c1.status == ConnectorStatus.STOPPED

    ConnectorRegistry.unregister("c1")
    assert ConnectorRegistry.get("c1") is None
    ConnectorRegistry.unregister("missing")  # no error


# ---------------------------------------------------------------- webhook


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_webhook_connector_validate_config():
    with pytest.raises(ValueError, match="Unsupported hash"):
        await WebhookConnector("wh", hash_alg="nope").validate_config()
    with pytest.raises(ValueError, match="header_name"):
        await WebhookConnector("wh", header_name="").validate_config()
    await WebhookConnector("wh").validate_config()  # defaults are valid


@pytest.mark.asyncio
async def test_webhook_connector_accept_and_reject():
    connector = WebhookConnector("wh", secret="s3cret")
    received = []

    async def cb(event):
        received.append(event)

    connector.on_event(cb)
    body = b'{"a": 1}'

    ok = await connector.handle_request(
        payload={"a": 1}, raw_body=body, headers={"X-GenXAI-Signature": _sign("s3cret", body)}
    )
    assert ok == {"status": "accepted", "connector_id": "wh"}

    bad = await connector.handle_request(
        payload={"a": 1}, raw_body=body, headers={"X-GenXAI-Signature": "sha256=bogus"}
    )
    assert bad["status"] == "rejected"
    assert len(received) == 1


# ---------------------------------------------------------------- slack


@pytest.mark.asyncio
async def test_slack_connector_send_message():
    def handler(request):
        assert request.url.path == "/api/chat.postMessage"
        assert request.headers.get("content-type", "").startswith("application/json")
        body = json.loads(request.content)
        assert body == {"channel": "#general", "text": "hello"}
        return httpx.Response(200, json={"ok": True, "ts": "123.456"})

    connector = SlackConnector("slack", bot_token="xoxb-test")
    connector._client = _mock_client(handler, "https://slack.com/api")

    result = await connector.send_message("#general", "hello")
    assert result["ok"] is True
    await connector._client.aclose()


@pytest.mark.asyncio
async def test_slack_connector_api_error_raises():
    def handler(request):
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    connector = SlackConnector("slack", bot_token="xoxb-test")
    connector._client = _mock_client(handler, "https://slack.com/api")

    with pytest.raises(ValueError, match="Slack API error"):
        await connector.send_message("#missing", "hello")
    await connector._client.aclose()


@pytest.mark.asyncio
async def test_slack_connector_list_channels_and_events():
    def handler(request):
        assert request.url.path == "/api/conversations.list"
        return httpx.Response(200, json={"ok": True, "channels": [{"name": "general"}]})

    connector = SlackConnector("slack", bot_token="xoxb-test")
    connector._client = _mock_client(handler, "https://slack.com/api")

    data = await connector.list_channels()
    assert data["channels"][0]["name"] == "general"

    received = []

    async def cb(event):
        received.append(event)

    connector.on_event(cb)
    await connector.handle_event({"type": "message"}, headers={"X-Slack-Signature": "sig"})
    assert received[0].payload == {"type": "message"}
    await connector._client.aclose()


@pytest.mark.asyncio
async def test_slack_connector_requires_token():
    connector = SlackConnector("slack", bot_token="")
    with pytest.raises(ValueError, match="bot_token"):
        await connector.start()
    assert connector.status == ConnectorStatus.ERROR


# ---------------------------------------------------------------- github


@pytest.mark.asyncio
async def test_github_connector_get_repo_and_create_issue():
    def handler(request):
        if request.method == "GET" and request.url.path == "/repos/octo/repo":
            return httpx.Response(200, json={"full_name": "octo/repo", "stargazers_count": 42})
        if request.method == "POST" and request.url.path == "/repos/octo/repo/issues":
            body = json.loads(request.content)
            return httpx.Response(201, json={"number": 7, "title": body["title"]})
        return httpx.Response(404, json={"message": "Not Found"})

    connector = GitHubConnector("gh", token="ghp_test")
    connector._client = _mock_client(handler, "https://api.github.com")

    repo = await connector.get_repo("octo", "repo")
    assert repo["stargazers_count"] == 42

    issue = await connector.create_issue("octo", "repo", title="Bug", body="Details")
    assert issue["number"] == 7
    await connector._client.aclose()


@pytest.mark.asyncio
async def test_github_connector_http_error_propagates():
    def handler(request):
        return httpx.Response(401, json={"message": "Bad credentials"})

    connector = GitHubConnector("gh", token="bad")
    connector._client = _mock_client(handler, "https://api.github.com")

    with pytest.raises(httpx.HTTPStatusError):
        await connector.get_repo("octo", "repo")
    await connector._client.aclose()


@pytest.mark.asyncio
async def test_github_connector_requires_token():
    connector = GitHubConnector("gh", token="")
    with pytest.raises(ValueError, match="token"):
        await connector.start()


# ---------------------------------------------------------------- jira / notion / google workspace


@pytest.mark.asyncio
async def test_jira_connector_search_and_validation():
    def handler(request):
        assert request.url.path == "/rest/api/3/search"
        return httpx.Response(200, json={"issues": [{"key": "PROJ-1"}]})

    connector = JiraConnector("jira", email="a@b.c", api_token="tok", base_url="https://x.atlassian.net")
    connector._client = _mock_client(handler, "https://x.atlassian.net")

    data = await connector.search_issues("project = PROJ")
    assert data["issues"][0]["key"] == "PROJ-1"
    await connector._client.aclose()

    with pytest.raises(ValueError, match="email"):
        await JiraConnector("jira", email="", api_token="t", base_url="https://x").validate_config()
    with pytest.raises(ValueError, match="api_token"):
        await JiraConnector("jira", email="e", api_token="", base_url="https://x").validate_config()


@pytest.mark.asyncio
async def test_notion_connector_get_page_and_validation():
    def handler(request):
        assert request.url.path == "/v1/pages/page-1"
        return httpx.Response(200, json={"id": "page-1", "object": "page"})

    connector = NotionConnector("notion", token="secret")
    connector._client = _mock_client(handler, "https://api.notion.com/v1")

    page = await connector.get_page("page-1")
    assert page["id"] == "page-1"
    await connector._client.aclose()

    with pytest.raises(ValueError, match="token"):
        await NotionConnector("notion", token="").validate_config()


@pytest.mark.asyncio
async def test_google_workspace_connector_sheet_and_validation():
    def handler(request):
        return httpx.Response(200, json={"spreadsheetId": "sheet-1"})

    connector = GoogleWorkspaceConnector("gws", access_token="ya29.test")
    connector._client = _mock_client(handler, "https://www.googleapis.com")

    sheet = await connector.get_sheet("sheet-1")
    assert sheet["spreadsheetId"] == "sheet-1"
    await connector._client.aclose()

    with pytest.raises(ValueError, match="access_token"):
        await GoogleWorkspaceConnector("gws", access_token="").validate_config()


# ---------------------------------------------------------------- kafka


@pytest.mark.asyncio
async def test_kafka_connector_consumes_and_emits(monkeypatch):
    messages = [
        types.SimpleNamespace(
            value={"order": 1}, topic="orders", partition=0, offset=5, timestamp=1234
        )
    ]

    class FakeConsumer:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
            self._messages = list(messages)

        async def start(self):
            pass

        async def stop(self):
            pass

        async def getone(self):
            if self._messages:
                return self._messages.pop(0)
            await asyncio.sleep(3600)  # block until cancelled

    fake_aiokafka = types.ModuleType("aiokafka")
    fake_aiokafka.AIOKafkaConsumer = FakeConsumer
    monkeypatch.setitem(sys.modules, "aiokafka", fake_aiokafka)

    connector = KafkaConnector("kafka", topic="orders", bootstrap_servers="localhost:9092")
    received = []

    async def cb(event):
        received.append(event)

    connector.on_event(cb)
    await connector.start()
    try:
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0.01)
    finally:
        await connector.stop()

    assert received[0].payload == {"order": 1}
    assert received[0].metadata["topic"] == "orders"
    assert received[0].metadata["offset"] == 5
    assert connector.status == ConnectorStatus.STOPPED


@pytest.mark.asyncio
async def test_kafka_connector_validation_and_deserializer():
    with pytest.raises(ValueError, match="topic"):
        await KafkaConnector("k", topic="", bootstrap_servers="x").validate_config()
    with pytest.raises(ValueError, match="bootstrap_servers"):
        await KafkaConnector("k", topic="t", bootstrap_servers="").validate_config()

    connector = KafkaConnector("k", topic="t", bootstrap_servers="x")
    assert connector._default_deserializer(b'{"a": 1}') == {"a": 1}
    assert connector._default_deserializer(b"\xff\xfe") == b"\xff\xfe"  # non-JSON passthrough

    received = []

    async def cb(event):
        received.append(event.payload)

    connector.on_event(cb)
    await connector.handle_message({"manual": True})
    assert received == [{"manual": True}]


# ---------------------------------------------------------------- sqs


@pytest.mark.asyncio
async def test_sqs_connector_polls_and_deletes(monkeypatch):
    deleted = []

    class FakeSQSClient:
        def __init__(self):
            self._delivered = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def receive_message(self, **kwargs):
            if not self._delivered:
                self._delivered = True
                return {
                    "Messages": [
                        {
                            "Body": '{"job": 9}',
                            "MessageId": "m-1",
                            "ReceiptHandle": "rh-1",
                        }
                    ]
                }
            await asyncio.sleep(3600)

        async def delete_message(self, **kwargs):
            deleted.append(kwargs["ReceiptHandle"])

    fake_client = FakeSQSClient()

    class FakeSession:
        def client(self, service, region_name=None):
            return fake_client

    fake_aioboto3 = types.ModuleType("aioboto3")
    fake_aioboto3.Session = FakeSession
    monkeypatch.setitem(sys.modules, "aioboto3", fake_aioboto3)

    connector = SQSConnector("sqs", queue_url="https://sqs.test/queue")
    received = []

    async def cb(event):
        received.append(event)

    connector.on_event(cb)
    await connector.start()
    try:
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0.01)
    finally:
        await connector.stop()

    assert received[0].payload == {"job": 9}
    assert received[0].metadata["message_id"] == "m-1"
    assert deleted == ["rh-1"]


@pytest.mark.asyncio
async def test_sqs_connector_validation_and_deserialize():
    with pytest.raises(ValueError, match="queue_url"):
        await SQSConnector("sqs", queue_url="").validate_config()

    connector = SQSConnector("sqs", queue_url="https://sqs.test/queue")
    assert connector._deserialize('{"a": 1}') == {"a": 1}
    assert connector._deserialize("plain") == "plain"
    assert connector._deserialize(None) is None


# ---------------------------------------------------------------- postgres cdc


@pytest.mark.asyncio
async def test_postgres_cdc_validation():
    with pytest.raises(ValueError, match="dsn"):
        await PostgresCDCConnector("pg", dsn="", slot_name="s", publication="p").validate_config()
    with pytest.raises(ValueError, match="slot_name"):
        await PostgresCDCConnector("pg", dsn="d", slot_name="", publication="p").validate_config()
    with pytest.raises(ValueError, match="publication"):
        await PostgresCDCConnector("pg", dsn="d", slot_name="s", publication="").validate_config()


# ---------------------------------------------------------------- config store


def test_config_store_roundtrip(tmp_path):
    store = ConnectorConfigStore(path=tmp_path / "connectors.json")
    entry = ConnectorConfigEntry(name="slack-main", connector_type="slack", config={"bot_token": "x"})

    store.save(entry)
    loaded = store.get("slack-main")
    assert loaded is not None
    assert loaded.connector_type == "slack"
    assert loaded.config == {"bot_token": "x"}

    assert "slack-main" in store.list()
    assert store.delete("slack-main") is True
    assert store.get("slack-main") is None
    assert store.delete("slack-main") is False
