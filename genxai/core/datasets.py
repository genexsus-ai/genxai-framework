"""Durable datasets: rows that workflows accumulate across runs.

Run results are operational and capped; datasets are the *data product* of a
workflow — e.g. every scheduled poll appends its items to one named dataset,
which analytics can then query long after individual runs are pruned.

Backed by a single SQLite database (stdlib, no dependencies): a registry
table plus one rows table storing each row as JSON with its timestamp.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_\-]{0,63}$")

# Aggregations scan at most this many rows (newest first)
MAX_SCAN_ROWS = 50_000
ALLOWED_METRICS = ("count", "sum", "avg", "min", "max")


def _validate_name(name: str) -> str:
    if not _NAME_RE.match(name or ""):
        raise ValueError(
            f"Invalid dataset name {name!r} — use letters, digits, '_' or '-'"
        )
    return name


class DatasetStore:
    """SQLite-backed store of named datasets of JSON rows."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS datasets (
                    name TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dataset TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rows_dataset
                    ON rows (dataset, created_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def append(self, dataset: str, rows: list[dict[str, Any]]) -> int:
        """Append rows (each a JSON-serializable dict); returns count written."""
        _validate_name(dataset)
        clean: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(
                    f"Dataset rows must be objects, got {type(row).__name__}"
                )
            clean.append(json.dumps(row, default=str))
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO datasets (name, created_at) VALUES (?, ?)",
                (dataset, now),
            )
            conn.executemany(
                "INSERT INTO rows (dataset, created_at, data) VALUES (?, ?, ?)",
                [(dataset, now, data) for data in clean],
            )
        return len(clean)

    def replace(self, dataset: str, rows: list[dict[str, Any]]) -> int:
        """Replace the dataset's rows with the given rows."""
        _validate_name(dataset)
        with self._connect() as conn:
            conn.execute("DELETE FROM rows WHERE dataset = ?", (dataset,))
        return self.append(dataset, rows)

    def list_datasets(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                SELECT d.name, d.created_at,
                       COUNT(r.id) AS row_count, MAX(r.created_at) AS last_at
                FROM datasets d LEFT JOIN rows r ON r.dataset = d.name
                GROUP BY d.name ORDER BY last_at DESC
                """
            )
            return [
                {
                    "name": name,
                    "created_at": created_at,
                    "rows": row_count,
                    "last_written_at": last_at,
                }
                for name, created_at, row_count, last_at in cursor.fetchall()
            ]

    def rows(
        self, dataset: str, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """Newest-first page of rows plus the dataset's total count."""
        _validate_name(dataset)
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM rows WHERE dataset = ?", (dataset,)
            ).fetchone()[0]
            cursor = conn.execute(
                "SELECT id, created_at, data FROM rows WHERE dataset = ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (dataset, limit, offset),
            )
            page = [
                {"_id": row_id, "_created_at": created_at, **json.loads(data)}
                for row_id, created_at, data in cursor.fetchall()
            ]
        return {"rows": page, "total": total}

    def aggregate(
        self,
        dataset: str,
        metric: str = "count",
        field: str | None = None,
        group_by: str | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate rows: ``metric`` over ``field``, optionally per ``group_by``.

        Aggregation happens in Python over the newest MAX_SCAN_ROWS rows —
        rows are schemaless JSON, so this stays correct without SQL JSON
        extensions. Non-numeric values are skipped for numeric metrics.
        """
        _validate_name(dataset)
        if metric not in ALLOWED_METRICS:
            raise ValueError(f"metric must be one of {ALLOWED_METRICS}")
        if metric != "count" and not field:
            raise ValueError(f"metric '{metric}' requires a field")

        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT data FROM rows WHERE dataset = ? ORDER BY id DESC LIMIT ?",
                (dataset, MAX_SCAN_ROWS),
            )
            raw_rows = [json.loads(data) for (data,) in cursor.fetchall()]

        groups: dict[str, list[float]] = {}
        counts: dict[str, int] = {}
        for row in raw_rows:
            key = str(row.get(group_by, "—")) if group_by else "all"
            counts[key] = counts.get(key, 0) + 1
            if metric != "count":
                value = row.get(field or "")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                groups.setdefault(key, []).append(float(value))

        results = []
        keys = counts.keys() if metric == "count" else groups.keys()
        for key in keys:
            if metric == "count":
                value: float = counts[key]
            else:
                values = groups[key]
                if metric == "sum":
                    value = sum(values)
                elif metric == "avg":
                    value = sum(values) / len(values)
                elif metric == "min":
                    value = min(values)
                else:
                    value = max(values)
            results.append({"group": key, "value": value, "rows": counts[key]})
        results.sort(key=lambda r: r["value"], reverse=True)
        return results

    def delete_dataset(self, dataset: str) -> bool:
        _validate_name(dataset)
        with self._connect() as conn:
            existed = conn.execute(
                "SELECT 1 FROM datasets WHERE name = ?", (dataset,)
            ).fetchone()
            conn.execute("DELETE FROM rows WHERE dataset = ?", (dataset,))
            conn.execute("DELETE FROM datasets WHERE name = ?", (dataset,))
        return existed is not None


_store: DatasetStore | None = None


def configure_dataset_store(db_path: Path) -> DatasetStore:
    global _store
    _store = DatasetStore(db_path)
    return _store


def get_dataset_store() -> DatasetStore:
    global _store
    if _store is None:
        base = os.environ.get("GENXAI_DATASET_DB")
        _store = DatasetStore(
            Path(base) if base else Path.home() / ".genxai" / "datasets.db"
        )
    return _store


def reset_dataset_store() -> None:
    global _store
    _store = None
