"""Tests for per-item node execution (for_each)."""

from genxai.core.graph.executor import WorkflowExecutor


async def _run(nodes, edges, input_data):
    return await WorkflowExecutor().execute(
        nodes=nodes, edges=edges, input_data=input_data
    )


async def test_for_each_runs_node_per_item():
    nodes = [
        {"id": "start", "type": "input", "config": {}},
        {
            "id": "double",
            "type": "tool",
            "config": {
                "tool_name": "calculator",
                "tool_params": {"expression": "{{ item }} * 2"},
                "for_each": "{{ input.numbers }}",
            },
        },
    ]
    edges = [{"source": "start", "target": "double"}]

    result = await _run(nodes, edges, {"numbers": [1, 2, 3]})

    assert result["status"] == "success"
    output = result["result"]["node_results"]["double"]["output"]
    assert output["count"] == 3
    assert [entry["data"]["result"] for entry in output["items"]] == [2.0, 4.0, 6.0]


async def test_for_each_exposes_item_index():
    nodes = [
        {
            "id": "indexed",
            "type": "tool",
            "config": {
                "tool_name": "calculator",
                "tool_params": {"expression": "{{ item }} + {{ item_index }}"},
                "for_each": "{{ input.numbers }}",
            },
        },
    ]

    result = await _run(nodes, [], {"numbers": [10, 20]})

    output = result["result"]["node_results"]["indexed"]["output"]
    assert [entry["data"]["result"] for entry in output["items"]] == [10.0, 21.0]


async def test_for_each_empty_list_completes_without_running():
    nodes = [
        {
            "id": "noop",
            "type": "tool",
            "config": {
                "tool_name": "calculator",
                "tool_params": {"expression": "{{ item }}"},
                "for_each": "{{ input.numbers }}",
            },
        },
    ]

    result = await _run(nodes, [], {"numbers": []})

    assert result["status"] == "success"
    output = result["result"]["node_results"]["noop"]["output"]
    assert output == {"items": [], "count": 0}


async def test_for_each_non_list_fails_with_clear_error():
    nodes = [
        {
            "id": "bad",
            "type": "tool",
            "config": {
                "tool_name": "calculator",
                "tool_params": {"expression": "{{ item }}"},
                "for_each": "{{ input.numbers }}",
            },
        },
    ]

    result = await _run(nodes, [], {"numbers": 5})

    assert result["status"] == "error"
    assert "must resolve to a list" in result["error"]


async def test_for_each_downstream_can_reference_collected_items():
    nodes = [
        {
            "id": "double",
            "type": "tool",
            "config": {
                "tool_name": "calculator",
                "tool_params": {"expression": "{{ item }} * 2"},
                "for_each": "{{ input.numbers }}",
            },
        },
        {
            "id": "total",
            "type": "tool",
            "config": {
                "tool_name": "calculator",
                "tool_params": {
                    "expression": "{{ double.items.0.data.result }} + {{ double.items.1.data.result }}"
                },
            },
        },
    ]
    edges = [{"source": "double", "target": "total"}]

    result = await _run(nodes, edges, {"numbers": [3, 4]})

    output = result["result"]["node_results"]["total"]["output"]
    assert output["data"]["result"] == 14.0


async def test_for_each_cursor_removed_after_loop():
    nodes = [
        {
            "id": "loop",
            "type": "tool",
            "config": {
                "tool_name": "calculator",
                "tool_params": {"expression": "{{ item }}"},
                "for_each": "{{ input.numbers }}",
            },
        },
        {"id": "end", "type": "output", "config": {}},
    ]
    edges = [{"source": "loop", "target": "end"}]

    result = await _run(nodes, edges, {"numbers": [1]})

    snapshot = result["result"]["node_results"]["end"]["output"]
    assert "item" not in snapshot
    assert "item_index" not in snapshot
