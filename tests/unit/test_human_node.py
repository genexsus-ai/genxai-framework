"""Tests for HUMAN node execution (human-in-the-loop)."""

import asyncio

import pytest

from genxai.core.graph.executor import WorkflowExecutor


def _nodes(human_config: dict) -> tuple[list[dict], list[dict]]:
    nodes = [
        {"id": "start", "type": "input", "config": {}},
        {"id": "gate", "type": "human", "config": human_config},
        {"id": "end", "type": "output", "config": {}},
    ]
    edges = [
        {"source": "start", "target": "gate"},
        {"source": "gate", "target": "end"},
    ]
    return nodes, edges


async def test_human_node_receives_provider_response():
    asked: list[tuple[str, str]] = []

    async def provider(node_id: str, prompt: str):
        asked.append((node_id, prompt))
        return {"approved": True}

    nodes, edges = _nodes({"prompt": "Approve {{ input.doc }}?"})
    result = await WorkflowExecutor().execute(
        nodes=nodes,
        edges=edges,
        input_data={"doc": "budget.pdf"},
        human_input_provider=provider,
    )

    assert result["status"] == "success"
    assert asked == [("gate", "Approve budget.pdf?")]
    gate = result["result"]["node_results"]["gate"]["output"]
    assert gate["response"] == {"approved": True}
    assert gate["prompt"] == "Approve budget.pdf?"


async def test_human_node_timeout_uses_default_response():
    async def never_answers(node_id: str, prompt: str):
        await asyncio.sleep(30)

    nodes, edges = _nodes(
        {"prompt": "hurry", "timeout_seconds": 0.05, "default_response": "auto-ok"}
    )
    result = await WorkflowExecutor().execute(
        nodes=nodes, edges=edges, input_data={}, human_input_provider=never_answers
    )

    assert result["status"] == "success"
    gate = result["result"]["node_results"]["gate"]["output"]
    assert gate["response"] == "auto-ok"


async def test_human_node_timeout_without_default_fails():
    async def never_answers(node_id: str, prompt: str):
        await asyncio.sleep(30)

    nodes, edges = _nodes({"prompt": "hurry", "timeout_seconds": 0.05})
    result = await WorkflowExecutor().execute(
        nodes=nodes, edges=edges, input_data={}, human_input_provider=never_answers
    )

    assert result["status"] == "error"
    assert "timed out" in result["error"]


async def test_human_node_without_provider_uses_default():
    nodes, edges = _nodes({"prompt": "anyone?", "default_response": "nobody home"})
    result = await WorkflowExecutor().execute(nodes=nodes, edges=edges, input_data={})

    assert result["status"] == "success"
    gate = result["result"]["node_results"]["gate"]["output"]
    assert gate["response"] == "nobody home"


async def test_human_node_without_provider_or_default_fails():
    nodes, edges = _nodes({"prompt": "anyone?"})
    result = await WorkflowExecutor().execute(nodes=nodes, edges=edges, input_data={})

    assert result["status"] == "error"
    assert "human_input_provider" in result["error"]


async def test_output_snapshot_excludes_provider():
    async def provider(node_id: str, prompt: str):
        return "ok"

    nodes, edges = _nodes({"prompt": "p"})
    result = await WorkflowExecutor().execute(
        nodes=nodes, edges=edges, input_data={}, human_input_provider=provider
    )

    snapshot = result["result"]["node_results"]["end"]["output"]
    assert "human_input_provider" not in snapshot
    import json

    json.dumps(snapshot)  # must be JSON-serializable
