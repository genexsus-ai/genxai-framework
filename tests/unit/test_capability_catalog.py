"""Unit tests for the capability catalog builder."""

import pytest

from genxai.builder.catalog import build_capability_catalog
from genxai.tools.base import Tool, ToolCategory, ToolMetadata, ToolParameter, ToolResult
from genxai.tools.registry import ToolRegistry


class _EchoTool(Tool):
    """Minimal tool for registry-backed catalog tests."""

    def __init__(self) -> None:
        super().__init__(
            metadata=ToolMetadata(
                name="echo_tool",
                description="Echo the given message back",
                category=ToolCategory.CUSTOM,
            ),
            parameters=[
                ToolParameter(
                    name="message",
                    type="string",
                    description="Message to echo",
                    required=True,
                ),
                ToolParameter(
                    name="loud",
                    type="boolean",
                    description="Uppercase the echo",
                    required=False,
                ),
            ],
        )

    async def _execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, data=kwargs.get("message"))


@pytest.fixture()
def echo_registry():
    ToolRegistry.clear()
    ToolRegistry.register(_EchoTool())
    yield
    ToolRegistry.clear()


def test_catalog_includes_registered_tools_and_flows(echo_registry) -> None:
    catalog = build_capability_catalog()

    assert "echo_tool" in catalog.names("tool")
    assert "coordinator_worker" in catalog.names("flow")
    assert "critic_review" in catalog.names("flow")

    context = catalog.to_prompt_context()
    assert "echo_tool" in context
    assert "message*" in context  # required params marked
    assert "coordinator_worker" in context


def test_catalog_extra_sections_and_unknown_capabilities(echo_registry) -> None:
    catalog = build_capability_catalog(
        extra_sections={
            "connector": [
                {"name": "github.create_issue", "description": "Open a GitHub issue"},
            ]
        }
    )

    assert "github.create_issue" in catalog.names("connector")
    assert catalog.unknown_capabilities({"echo_tool", "github.create_issue"}) == set()
    assert catalog.unknown_capabilities({"made_up_tool"}) == {"made_up_tool"}


def test_prompt_context_truncation(echo_registry) -> None:
    catalog = build_capability_catalog()
    context = catalog.to_prompt_context(max_chars=80)

    assert len(context) < 200
    assert context.endswith("(... catalog truncated; more capabilities exist)")


def test_catalog_summary(echo_registry) -> None:
    catalog = build_capability_catalog()
    summary = catalog.summary()

    from genxai.flows import FLOW_TYPES

    assert summary["tool"] == 1
    assert summary["flow"] == len(FLOW_TYPES)
