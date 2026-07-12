"""Tests for the workflow file store and file tools."""

import pytest

from genxai.core.files import (
    FileStore,
    configure_file_store,
    get_file_store,
    is_file_ref,
    reset_file_store,
)
from genxai.core.graph.executor import WorkflowExecutor


@pytest.fixture(autouse=True)
def isolated_store(tmp_path):
    configure_file_store(tmp_path / "files")
    yield
    reset_file_store()


def test_save_and_read_roundtrip(tmp_path):
    store = get_file_store()
    ref = store.save_bytes(b"hello bytes", name="hello.txt", media_type="text/plain")

    assert is_file_ref(ref)
    assert ref["name"] == "hello.txt"
    assert ref["size"] == 11
    assert store.read_bytes(ref) == b"hello bytes"
    assert store.read_bytes(ref["id"]) == b"hello bytes"  # id string also works

    metadata = store.get_metadata(ref)
    assert metadata["media_type"] == "text/plain"


def test_content_addressing_deduplicates(tmp_path):
    store = get_file_store()
    ref1 = store.save_bytes(b"same content", name="a.txt")
    ref2 = store.save_bytes(b"same content", name="b.txt")

    assert ref1["id"] == ref2["id"]  # one blob, two names
    assert store.open_path(ref1) == store.open_path(ref2)


def test_invalid_ids_rejected():
    store = get_file_store()
    with pytest.raises(ValueError):
        store.open_path("../../etc/passwd")
    with pytest.raises(FileNotFoundError):
        store.open_path("a" * 64)


def test_is_file_ref():
    assert not is_file_ref({"id": "x"})
    assert not is_file_ref("abc")
    assert is_file_ref({"__genxai_file__": True, "id": "abc"})


async def test_file_write_then_content_through_workflow():
    nodes = [
        {
            "id": "write",
            "type": "tool",
            "config": {
                "tool_name": "file_write",
                "tool_params": {"content": "col1,col2\n1,2", "name": "data.csv"},
            },
        },
        {
            "id": "read",
            "type": "tool",
            "config": {
                "tool_name": "file_content",
                "tool_params": {"file": "{{ write.data.file }}"},
            },
        },
    ]
    edges = [{"source": "write", "target": "read"}]

    from genxai.tools.builtin.file.file_store_tools import (
        FileContentTool,
        FileWriteTool,
    )
    from genxai.tools.registry import ToolRegistry

    for tool in (FileWriteTool(), FileContentTool()):
        if ToolRegistry.get(tool.metadata.name) is None:
            ToolRegistry.register(tool)

    result = await WorkflowExecutor().execute(nodes=nodes, edges=edges, input_data={})

    assert result["status"] == "success"
    write_output = result["result"]["node_results"]["write"]["output"]["data"]
    assert is_file_ref(write_output["file"])
    read_output = result["result"]["node_results"]["read"]["output"]["data"]
    assert read_output["content"] == "col1,col2\n1,2"
    assert read_output["truncated"] is False
