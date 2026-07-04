try:
    from enterprise.genxai.connectors.base import Connector, ConnectorEvent, ConnectorStatus
    from enterprise.genxai.connectors.github import GitHubConnector
    from enterprise.genxai.connectors.google_workspace import GoogleWorkspaceConnector
    from enterprise.genxai.connectors.jira import JiraConnector
    from enterprise.genxai.connectors.kafka import KafkaConnector
    from enterprise.genxai.connectors.notion import NotionConnector
    from enterprise.genxai.connectors.postgres_cdc import PostgresCDCConnector
    from enterprise.genxai.connectors.registry import ConnectorRegistry
    from enterprise.genxai.connectors.slack import SlackConnector
    from enterprise.genxai.connectors.sqs import SQSConnector
    from enterprise.genxai.connectors.webhook import WebhookConnector
except ModuleNotFoundError:
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
