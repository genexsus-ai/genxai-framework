"""Tests for the resume replay cache (retry-from-failure support)."""

from genxai.core.graph.executor import WorkflowExecutor


async def test_replayed_nodes_skip_execution_but_feed_downstream():
    nodes = [
        {
            "id": "expensive",
            "type": "tool",
            "config": {
                "tool_name": "calculator",
                # Would fail if actually executed: 'item' is not in state.
                # Replay must return the cached output without running this.
                "tool_params": {"expression": "{{ missing.path }}"},
            },
        },
        {
            "id": "add_one",
            "type": "tool",
            "config": {
                "tool_name": "calculator",
                "tool_params": {"expression": "{{ expensive.data.result }} + 1"},
            },
        },
    ]
    edges = [{"source": "expensive", "target": "add_one"}]

    cached = {"success": True, "data": {"expression": "6 * 7", "result": 42.0}}
    result = await WorkflowExecutor().execute(
        nodes=nodes,
        edges=edges,
        input_data={},
        extra_state={"_resume_results": {"expensive": cached}},
    )

    assert result["status"] == "success"
    node_results = result["result"]["node_results"]
    assert node_results["expensive"]["output"]["data"]["result"] == 42.0
    assert node_results["add_one"]["output"]["data"]["result"] == 43.0
    replay_events = [
        e for e in result["result"]["node_events"] if e.get("replayed")
    ]
    assert [e["node_id"] for e in replay_events] == ["expensive"]


async def test_without_cache_node_executes_normally():
    nodes = [
        {
            "id": "calc",
            "type": "tool",
            "config": {
                "tool_name": "calculator",
                "tool_params": {"expression": "2 + 2"},
            },
        },
    ]

    result = await WorkflowExecutor().execute(nodes=nodes, edges=[], input_data={})

    assert result["status"] == "success"
    assert not any(
        e.get("replayed") for e in result["result"]["node_events"]
    )


async def test_resume_cache_excluded_from_output_snapshot():
    nodes = [
        {
            "id": "calc",
            "type": "tool",
            "config": {
                "tool_name": "calculator",
                "tool_params": {"expression": "1 + 1"},
            },
        },
        {"id": "end", "type": "output", "config": {}},
    ]
    edges = [{"source": "calc", "target": "end"}]

    result = await WorkflowExecutor().execute(
        nodes=nodes,
        edges=edges,
        input_data={},
        extra_state={"_resume_results": {}},
    )

    snapshot = result["result"]["node_results"]["end"]["output"]
    assert "_resume_results" not in snapshot
