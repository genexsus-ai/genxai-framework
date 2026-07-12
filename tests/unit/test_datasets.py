"""Tests for the durable dataset store and dataset tools."""

import pytest

from genxai.core.datasets import (
    configure_dataset_store,
    get_dataset_store,
    reset_dataset_store,
)
from genxai.core.graph.executor import WorkflowExecutor


@pytest.fixture(autouse=True)
def isolated_store(tmp_path):
    configure_dataset_store(tmp_path / "datasets.db")
    yield
    reset_dataset_store()


def test_append_list_rows_roundtrip():
    store = get_dataset_store()
    written = store.append("events", [{"kind": "a", "value": 1}, {"kind": "b", "value": 2}])
    assert written == 2

    listing = store.list_datasets()
    assert listing[0]["name"] == "events"
    assert listing[0]["rows"] == 2

    page = store.rows("events", limit=10)
    assert page["total"] == 2
    assert page["rows"][0]["kind"] == "b"  # newest first
    assert page["rows"][0]["_id"] > page["rows"][1]["_id"]


def test_replace_mode_resets_rows():
    store = get_dataset_store()
    store.append("d", [{"n": 1}, {"n": 2}])
    store.replace("d", [{"n": 3}])
    page = store.rows("d")
    assert page["total"] == 1
    assert page["rows"][0]["n"] == 3


def test_aggregate_count_sum_avg():
    store = get_dataset_store()
    store.append(
        "sales",
        [
            {"region": "east", "amount": 10},
            {"region": "east", "amount": 30},
            {"region": "west", "amount": 5},
            {"region": "west", "amount": "not-a-number"},
        ],
    )

    counts = store.aggregate("sales", metric="count", group_by="region")
    assert {c["group"]: c["value"] for c in counts} == {"east": 2, "west": 2}

    sums = store.aggregate("sales", metric="sum", field="amount", group_by="region")
    assert {s["group"]: s["value"] for s in sums} == {"east": 40.0, "west": 5.0}

    avg_all = store.aggregate("sales", metric="avg", field="amount")
    assert avg_all[0]["group"] == "all"
    assert avg_all[0]["value"] == 15.0  # non-numeric row skipped


def test_aggregate_validation():
    store = get_dataset_store()
    with pytest.raises(ValueError, match="metric"):
        store.aggregate("x", metric="median")
    with pytest.raises(ValueError, match="requires a field"):
        store.aggregate("x", metric="sum")


def test_invalid_names_rejected():
    store = get_dataset_store()
    with pytest.raises(ValueError):
        store.append("../evil", [{"a": 1}])
    with pytest.raises(ValueError):
        store.rows("bad name")


def test_delete_dataset():
    store = get_dataset_store()
    store.append("temp", [{"a": 1}])
    assert store.delete_dataset("temp") is True
    assert store.delete_dataset("temp") is False
    assert store.list_datasets() == []


async def test_dataset_tools_through_workflow():
    from genxai.tools.builtin.data.dataset_tools import (
        DatasetQueryTool,
        DatasetWriteTool,
    )
    from genxai.tools.registry import ToolRegistry

    for tool in (DatasetWriteTool(), DatasetQueryTool()):
        if ToolRegistry.get(tool.metadata.name) is None:
            ToolRegistry.register(tool)

    nodes = [
        {"id": "start", "type": "input", "config": {}},
        {
            "id": "collect",
            "type": "tool",
            "config": {
                "tool_name": "dataset_write",
                "tool_params": {"dataset": "items", "rows": "{{ input.items }}"},
            },
        },
        {
            "id": "readback",
            "type": "tool",
            "config": {
                "tool_name": "dataset_query",
                "tool_params": {"dataset": "items", "limit": 10},
            },
        },
    ]
    edges = [
        {"source": "start", "target": "collect"},
        {"source": "collect", "target": "readback"},
    ]

    result = await WorkflowExecutor().execute(
        nodes=nodes,
        edges=edges,
        input_data={"items": [{"title": "one"}, {"title": "two"}]},
    )

    assert result["status"] == "success"
    collect = result["result"]["node_results"]["collect"]["output"]["data"]
    assert collect == {"dataset": "items", "written": 2, "total_rows": 2}
    readback = result["result"]["node_results"]["readback"]["output"]["data"]
    assert [r["title"] for r in readback["rows"]] == ["two", "one"]
