"""Tests for template interpolation between workflow nodes."""

from unittest.mock import MagicMock, patch

import pytest

from genxai.core.graph.engine import Graph, GraphExecutionError
from genxai.core.graph.interpolation import (
    TemplateResolutionError,
    resolve_templates,
)
from genxai.core.graph.nodes import Node, NodeConfig, NodeType


STATE = {
    "input": {"topic": "AI", "count": 3},
    "calc1": {"success": True, "data": {"result": 42}},
    "items": ["a", "b", "c"],
}


class TestResolveTemplates:
    def test_full_expression_preserves_type(self):
        assert resolve_templates("{{ calc1.data.result }}", STATE) == 42
        assert resolve_templates("{{ input }}", STATE) == {"topic": "AI", "count": 3}

    def test_embedded_expression_interpolates_as_text(self):
        assert resolve_templates("{{ calc1.data.result }} + 8", STATE) == "42 + 8"
        assert (
            resolve_templates("Write about {{ input.topic }} x{{ input.count }}", STATE)
            == "Write about AI x3"
        )

    def test_recursive_dict_and_list(self):
        value = {"params": [{"expr": "{{ calc1.data.result }}"}], "n": 1}
        assert resolve_templates(value, STATE) == {"params": [{"expr": 42}], "n": 1}

    def test_list_index_lookup(self):
        assert resolve_templates("{{ items.1 }}", STATE) == "b"

    def test_non_string_passthrough(self):
        assert resolve_templates(7, STATE) == 7
        assert resolve_templates(None, STATE) is None

    def test_plain_string_untouched(self):
        assert resolve_templates("no templates here", STATE) == "no templates here"

    def test_missing_path_raises_named_error(self):
        with pytest.raises(TemplateResolutionError, match="ghost.result"):
            resolve_templates("{{ ghost.result }}", STATE)

    def test_out_of_range_index_raises(self):
        with pytest.raises(TemplateResolutionError):
            resolve_templates("{{ items.9 }}", STATE)


class TestToolNodeInterpolation:
    @pytest.mark.asyncio
    async def test_tool_params_resolve_from_upstream_state(self):
        calls = []
        fake_tool = MagicMock()

        async def execute(**kwargs):
            calls.append(kwargs)
            result = MagicMock()
            result.model_dump.return_value = {"ok": True}
            return result

        fake_tool.execute = execute

        graph = Graph("templated")
        graph.add_node(
            Node(
                id="t1",
                type=NodeType.TOOL,
                config=NodeConfig(
                    type=NodeType.TOOL,
                    data={
                        "tool_name": "probe",
                        "tool_params": {"expression": "{{ input.value }} * 2"},
                    },
                ),
            )
        )
        with patch("genxai.core.graph.engine.ToolRegistry.get", return_value=fake_tool):
            await graph.run(input_data={"value": 21})

        assert calls == [{"expression": "21 * 2"}]

    @pytest.mark.asyncio
    async def test_unresolvable_template_fails_node_with_clear_error(self):
        fake_tool = MagicMock()
        graph = Graph("bad-template")
        graph.add_node(
            Node(
                id="t1",
                type=NodeType.TOOL,
                config=NodeConfig(
                    type=NodeType.TOOL,
                    data={"tool_name": "probe", "tool_params": {"x": "{{ nope.value }}"}},
                ),
            )
        )
        with patch("genxai.core.graph.engine.ToolRegistry.get", return_value=fake_tool):
            with pytest.raises(GraphExecutionError, match="nope.value"):
                await graph.run(input_data={})
