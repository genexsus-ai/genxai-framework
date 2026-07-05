"""Trigger-driven workflow execution utilities."""

from __future__ import annotations

import logging
from typing import Any

from genxai.core.graph.executor import WorkflowExecutor
from genxai.triggers.base import TriggerEvent

logger = logging.getLogger(__name__)


class TriggerWorkflowRunner:
    """Bind trigger events to workflow execution."""

    def __init__(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        openai_api_key: str | None = None,
        anthropic_api_key: str | None = None,
    ) -> None:
        self.nodes = nodes
        self.edges = edges
        self.executor = WorkflowExecutor(
            openai_api_key=openai_api_key,
            anthropic_api_key=anthropic_api_key,
        )

    async def handle_event(self, event: TriggerEvent) -> dict[str, Any]:
        """Execute the workflow using the trigger event payload as input."""
        logger.info("Trigger event received: %s", event.trigger_id)
        input_data = event.payload or {}
        result = await self.executor.execute(
            nodes=self.nodes,
            edges=self.edges,
            input_data=input_data,
        )
        return result
