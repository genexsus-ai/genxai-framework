"""Connectors for external systems (Kafka, SQS, Slack, GitHub, etc.)."""

from .base import Connector, ConnectorEvent, ConnectorStatus
from .github import GitHubConnector
from .google_workspace import GoogleWorkspaceConnector
from .jira import JiraConnector
from .kafka import KafkaConnector
from .notion import NotionConnector
from .postgres_cdc import PostgresCDCConnector
from .registry import ConnectorRegistry
from .slack import SlackConnector
from .sqs import SQSConnector
from .webhook import WebhookConnector

__all__ = [
    "Connector",
    "ConnectorEvent",
    "ConnectorStatus",
    "ConnectorRegistry",
    "WebhookConnector",
    "KafkaConnector",
    "SQSConnector",
    "PostgresCDCConnector",
    "SlackConnector",
    "GitHubConnector",
    "NotionConnector",
    "JiraConnector",
    "GoogleWorkspaceConnector",
]
