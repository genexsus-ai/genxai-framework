"""Tools that read and write durable datasets (see ``genxai.core.datasets``).

``dataset_write`` is the sink that turns a workflow into a data collector:
each run appends its rows, so the dataset accumulates across runs.
"""

from __future__ import annotations

from typing import Any

from genxai.core.datasets import get_dataset_store
from genxai.tools.base import Tool, ToolCategory, ToolMetadata, ToolParameter


class DatasetWriteTool(Tool):
    """Append (or replace) rows in a named dataset."""

    def __init__(self) -> None:
        super().__init__(
            metadata=ToolMetadata(
                name="dataset_write",
                description=(
                    "Write rows into a named durable dataset that accumulates "
                    "across runs — the workflow's data product, used by the "
                    "Analytics app"
                ),
                category=ToolCategory.DATA_PROCESSING,
                tags=["dataset", "data", "analytics", "sink"],
                version="1.0.0",
            ),
            parameters=[
                ToolParameter(
                    name="dataset",
                    type="string",
                    description="Dataset name, e.g. news_articles",
                    required=True,
                ),
                ToolParameter(
                    name="rows",
                    type="array",
                    description=(
                        "Rows to write — a list of objects, e.g. "
                        "{{ poll_feed.data.items }} (a single object is accepted)"
                    ),
                    required=True,
                ),
                ToolParameter(
                    name="mode",
                    type="string",
                    description="append (default) or replace",
                    required=False,
                    default="append",
                    enum=["append", "replace"],
                ),
            ],
        )

    async def _execute(self, **kwargs: Any) -> dict[str, Any]:
        dataset: str = kwargs["dataset"]
        rows = kwargs["rows"]
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise ValueError("'rows' must be a list of objects")

        store = get_dataset_store()
        if kwargs.get("mode") == "replace":
            written = store.replace(dataset, rows)
        else:
            written = store.append(dataset, rows)
        total = store.rows(dataset, limit=1)["total"]
        return {"dataset": dataset, "written": written, "total_rows": total}


class DatasetQueryTool(Tool):
    """Read rows back out of a dataset (newest first)."""

    def __init__(self) -> None:
        super().__init__(
            metadata=ToolMetadata(
                name="dataset_query",
                description=(
                    "Read the newest rows of a durable dataset so a workflow "
                    "can consume data collected by other workflows"
                ),
                category=ToolCategory.DATA_PROCESSING,
                tags=["dataset", "data", "analytics", "query"],
                version="1.0.0",
            ),
            parameters=[
                ToolParameter(
                    name="dataset",
                    type="string",
                    description="Dataset name",
                    required=True,
                ),
                ToolParameter(
                    name="limit",
                    type="number",
                    description="Maximum rows to return",
                    required=False,
                    default=50,
                    min_value=1,
                    max_value=1000,
                ),
            ],
        )

    async def _execute(self, **kwargs: Any) -> dict[str, Any]:
        result = get_dataset_store().rows(
            kwargs["dataset"], limit=int(kwargs.get("limit") or 50)
        )
        return {
            "dataset": kwargs["dataset"],
            "rows": result["rows"],
            "total": result["total"],
        }
