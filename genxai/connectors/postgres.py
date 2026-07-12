"""PostgreSQL connector: extract from and load into SQL databases.

Uses SQLAlchemy under the hood, so any SQLAlchemy URL works
(``postgresql://user:pass@host:5432/db`` is the primary target; tests use
``sqlite:///...``). Synchronous DB calls run in a worker thread so the
event loop is never blocked.

Safety model:
- ``query`` accepts SELECT/WITH statements only and caps returned rows.
- ``execute`` is the explicit write path (INSERT/UPDATE/DDL).
- ``insert_rows`` bulk-loads a list of objects with table/column names
  validated as identifiers and values always bound as parameters.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from .base import Connector

logger = logging.getLogger(__name__)

MAX_QUERY_ROWS = 1000
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _validate_identifier(name: str, kind: str) -> str:
    if not _IDENTIFIER_RE.match(name or ""):
        raise ValueError(
            f"Invalid {kind} name {name!r} — letters, digits, and '_' only"
        )
    return name


class PostgresConnector(Connector):
    """SQL database connector (PostgreSQL-first, any SQLAlchemy URL)."""

    def __init__(
        self,
        connector_id: str,
        connection_string: str,
        name: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(connector_id=connector_id, name=name)
        self.connection_string = connection_string
        self.timeout = timeout
        self._engine: Any | None = None

    async def validate_config(self) -> None:
        if not self.connection_string:
            raise ValueError("connection_string must be provided")

    async def _start(self) -> None:
        # The engine is created lazily on first use; nothing to do here.
        return None

    def _get_engine(self) -> Any:
        if self._engine is None:
            from sqlalchemy import create_engine

            self._engine = create_engine(
                self.connection_string, pool_pre_ping=True
            )
        return self._engine

    async def _stop(self) -> None:
        if self._engine is not None:
            engine = self._engine
            self._engine = None
            await asyncio.to_thread(engine.dispose)

    # ------------------------------------------------------------- actions

    async def query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        max_rows: int = 500,
    ) -> dict[str, Any]:
        """Run a read-only SELECT/WITH query; returns rows as objects."""
        first_word = (sql or "").lstrip().split(None, 1)
        if not first_word or first_word[0].lower() not in ("select", "with"):
            raise ValueError(
                "query only accepts SELECT/WITH statements — use the "
                "'execute' action for writes"
            )
        limit = max(1, min(int(max_rows), MAX_QUERY_ROWS))

        def _run() -> dict[str, Any]:
            from sqlalchemy import text

            with self._get_engine().connect() as conn:
                result = conn.execute(text(sql), params or {})
                columns = list(result.keys())
                fetched = result.fetchmany(limit + 1)
            truncated = len(fetched) > limit
            rows = [
                {column: value for column, value in zip(columns, row)}
                for row in fetched[:limit]
            ]
            return {
                "rows": rows,
                "row_count": len(rows),
                "columns": columns,
                "truncated": truncated,
            }

        return await asyncio.to_thread(_run)

    async def execute(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Run a write statement (INSERT/UPDATE/DELETE/DDL); returns rowcount."""

        def _run() -> dict[str, Any]:
            from sqlalchemy import text

            with self._get_engine().begin() as conn:
                result = conn.execute(text(sql), params or {})
                rowcount = result.rowcount
            return {"rowcount": rowcount if rowcount is not None else -1}

        return await asyncio.to_thread(_run)

    async def insert_rows(
        self, table: str, rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Bulk-insert a list of objects into a table.

        Column set is the union of row keys; identifiers are validated,
        values are always bound parameters (never interpolated).
        """
        _validate_identifier(table, "table")
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list) or not rows:
            raise ValueError("'rows' must be a non-empty list of objects")
        columns: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("every row must be an object")
            for key in row:
                if key not in columns:
                    columns.append(_validate_identifier(key, "column"))

        column_list = ", ".join(columns)
        placeholders = ", ".join(f":{column}" for column in columns)
        statement = f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})"
        payload = [{column: row.get(column) for column in columns} for row in rows]

        def _run() -> dict[str, Any]:
            from sqlalchemy import text

            with self._get_engine().begin() as conn:
                conn.execute(text(statement), payload)
            return {"table": table, "inserted": len(payload), "columns": columns}

        return await asyncio.to_thread(_run)

    async def list_tables(self) -> dict[str, Any]:
        """List table names visible to this connection."""

        def _run() -> dict[str, Any]:
            from sqlalchemy import inspect

            inspector = inspect(self._get_engine())
            return {"tables": sorted(inspector.get_table_names())}

        return await asyncio.to_thread(_run)
