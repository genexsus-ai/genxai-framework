"""Tests for per-node execution policies (retry, timeout, continue_on_error)."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from genxai.core.graph.engine import Graph, GraphExecutionError
from genxai.core.graph.nodes import Node, NodeConfig, NodeStatus, NodeType


def _tool_node(node_id: str, execution: dict | None = None, params: dict | None = None) -> Node:
    data = {"tool_name": "probe", "tool_params": params or {}}
    if execution:
        data["execution"] = execution
    return Node(id=node_id, type=NodeType.TOOL, config=NodeConfig(type=NodeType.TOOL, data=data))


def _flaky_tool(fail_times: int, delay: float = 0.0):
    calls = {"n": 0}
    tool = MagicMock()

    async def execute(**kwargs):
        calls["n"] += 1
        if delay:
            await asyncio.sleep(delay)
        if calls["n"] <= fail_times:
            raise RuntimeError(f"boom {calls['n']}")
        result = MagicMock()
        result.model_dump.return_value = {"ok": True, "attempts": calls["n"]}
        return result

    tool.execute = execute
    return tool, calls


@pytest.mark.asyncio
async def test_retry_recovers_from_transient_failure():
    tool, calls = _flaky_tool(fail_times=2)
    graph = Graph("retry")
    graph.add_node(_tool_node("t1", execution={"retry_count": 3, "backoff_seconds": 0.01}))
    with patch("genxai.core.graph.engine.ToolRegistry.get", return_value=tool):
        state = await graph.run(input_data={})
    assert calls["n"] == 3
    assert state["t1"]["attempts"] == 3
    assert graph.nodes["t1"].status == NodeStatus.COMPLETED


@pytest.mark.asyncio
async def test_retry_exhaustion_still_fails():
    tool, calls = _flaky_tool(fail_times=10)
    graph = Graph("retry-fail")
    graph.add_node(_tool_node("t1", execution={"retry_count": 1, "backoff_seconds": 0.01}))
    with patch("genxai.core.graph.engine.ToolRegistry.get", return_value=tool):
        with pytest.raises(GraphExecutionError):
            await graph.run(input_data={})
    assert calls["n"] == 2  # initial + 1 retry


@pytest.mark.asyncio
async def test_timeout_fails_slow_node():
    tool, _ = _flaky_tool(fail_times=0, delay=0.5)
    graph = Graph("timeout")
    graph.add_node(_tool_node("t1", execution={"timeout_seconds": 0.05}))
    with patch("genxai.core.graph.engine.ToolRegistry.get", return_value=tool):
        with pytest.raises(GraphExecutionError):
            await graph.run(input_data={})


@pytest.mark.asyncio
async def test_continue_on_error_keeps_workflow_going():
    tool, _ = _flaky_tool(fail_times=10)
    graph = Graph("continue")
    graph.add_node(_tool_node("bad", execution={"continue_on_error": True}))

    downstream_ran = {"yes": False}

    class DownstreamGraph(Graph):
        pass

    graph.add_node(
        Node(id="after", type=NodeType.CONDITION, config=NodeConfig(type=NodeType.CONDITION))
    )
    from genxai.core.graph.edges import Edge

    graph.add_edge(Edge(source="bad", target="after"))

    original_logic = Graph._execute_node_logic

    async def spy_logic(self, node, state, max_iterations):
        if node.id == "after":
            downstream_ran["yes"] = True
        return await original_logic(self, node, state, max_iterations)

    with patch("genxai.core.graph.engine.ToolRegistry.get", return_value=tool):
        with patch.object(Graph, "_execute_node_logic", spy_logic):
            state = await graph.run(input_data={})

    assert downstream_ran["yes"], "downstream node did not run after continue_on_error"
    assert state["bad"]["success"] is False
    assert "boom" in state["bad"]["error"]
    assert graph.nodes["bad"].status == NodeStatus.FAILED
    assert graph.nodes["after"].status == NodeStatus.COMPLETED
    assert state["node_results"]["bad"]["output"]["success"] is False
