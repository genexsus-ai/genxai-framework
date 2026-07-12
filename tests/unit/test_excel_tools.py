"""Tests for the Excel read/write tools."""

import pytest

from genxai.core.files import configure_file_store, get_file_store, reset_file_store
from genxai.core.graph.executor import WorkflowExecutor
from genxai.tools.builtin.file.excel_tools import ExcelReadTool, ExcelWriteTool
from genxai.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def isolated_store(tmp_path):
    configure_file_store(tmp_path / "files")
    for tool in (ExcelReadTool(), ExcelWriteTool()):
        if ToolRegistry.get(tool.metadata.name) is None:
            ToolRegistry.register(tool)
    yield
    reset_file_store()


async def test_write_then_read_roundtrip_through_workflow():
    nodes = [
        {"id": "start", "type": "input", "config": {}},
        {
            "id": "write",
            "type": "tool",
            "config": {
                "tool_name": "excel_write",
                "tool_params": {
                    "rows": "{{ input.rows }}",
                    "name": "report",
                    "sheet": "Sales",
                },
            },
        },
        {
            "id": "read",
            "type": "tool",
            "config": {
                "tool_name": "excel_read",
                "tool_params": {"file": "{{ write.data.file }}", "sheet": "Sales"},
            },
        },
    ]
    edges = [
        {"source": "start", "target": "write"},
        {"source": "write", "target": "read"},
    ]

    result = await WorkflowExecutor().execute(
        nodes=nodes,
        edges=edges,
        input_data={
            "rows": [
                {"region": "east", "amount": 40},
                {"region": "west", "amount": 5, "note": "late"},
            ]
        },
    )

    assert result["status"] == "success"
    write_output = result["result"]["node_results"]["write"]["output"]["data"]
    assert write_output["rows"] == 2
    assert write_output["columns"] == ["region", "amount", "note"]
    assert write_output["file"]["name"] == "report.xlsx"

    read_output = result["result"]["node_results"]["read"]["output"]["data"]
    assert read_output["sheet"] == "Sales"
    assert read_output["rows"] == [
        {"region": "east", "amount": 40, "note": None},
        {"region": "west", "amount": 5, "note": "late"},
    ]
    assert read_output["truncated"] is False


async def test_read_without_header_row():
    write = ExcelWriteTool()
    ref = (await write._execute(rows=[{"a": 1, "b": 2}], name="raw"))["file"]

    read = ExcelReadTool()
    result = await read._execute(file=ref, header_row=False)

    # Header row becomes data when header_row is false
    assert result["rows"][0] == {"col_1": "a", "col_2": "b"}
    assert result["rows"][1] == {"col_1": 1, "col_2": 2}


async def test_read_unknown_sheet_names_available_sheets():
    write = ExcelWriteTool()
    ref = (await write._execute(rows=[{"a": 1}], sheet="Data"))["file"]

    read = ExcelReadTool()
    with pytest.raises(ValueError, match="workbook has"):
        await read._execute(file=ref, sheet="Nope")


async def test_read_max_rows_truncates():
    write = ExcelWriteTool()
    ref = (await write._execute(rows=[{"n": i} for i in range(20)]))["file"]

    read = ExcelReadTool()
    result = await read._execute(file=ref, max_rows=5)
    assert result["row_count"] == 5
    assert result["truncated"] is True


async def test_write_rejects_bad_rows():
    write = ExcelWriteTool()
    with pytest.raises(ValueError, match="non-empty"):
        await write._execute(rows=[])
    with pytest.raises(ValueError, match="object"):
        await write._execute(rows=["not-a-dict"])


async def test_written_file_is_valid_xlsx():
    write = ExcelWriteTool()
    ref = (await write._execute(rows=[{"x": 1}]))["file"]
    assert ref["media_type"].endswith("spreadsheetml.sheet")

    data = get_file_store().read_bytes(ref)
    assert data[:2] == b"PK"  # xlsx is a zip container
