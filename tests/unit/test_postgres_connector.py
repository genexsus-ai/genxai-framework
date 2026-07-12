"""Tests for the SQL database connector (SQLite URL; same code path as PG)."""

import pytest

from genxai.connectors.postgres import PostgresConnector


@pytest.fixture
async def connector(tmp_path):
    conn = PostgresConnector(
        connector_id="test-db",
        connection_string=f"sqlite:///{tmp_path}/test.db",
    )
    await conn.execute(
        "CREATE TABLE articles (title TEXT, source TEXT, score INTEGER)"
    )
    yield conn
    await conn._stop()


async def test_insert_and_query_roundtrip(connector):
    inserted = await connector.insert_rows(
        "articles",
        [
            {"title": "one", "source": "hn", "score": 10},
            {"title": "two", "source": "reddit", "score": 5},
            {"title": "three", "source": "hn"},  # missing key -> NULL
        ],
    )
    assert inserted == {
        "table": "articles",
        "inserted": 3,
        "columns": ["title", "source", "score"],
    }

    result = await connector.query(
        "SELECT title, score FROM articles WHERE source = :src ORDER BY title",
        params={"src": "hn"},
    )
    assert result["columns"] == ["title", "score"]
    assert result["rows"] == [
        {"title": "one", "score": 10},
        {"title": "three", "score": None},
    ]
    assert result["truncated"] is False


async def test_query_rejects_writes(connector):
    with pytest.raises(ValueError, match="SELECT/WITH"):
        await connector.query("DELETE FROM articles")
    # WITH (CTE) is allowed as a read
    result = await connector.query("WITH t AS (SELECT 1 AS n) SELECT n FROM t")
    assert result["rows"] == [{"n": 1}]


async def test_query_row_cap_and_truncation(connector):
    await connector.insert_rows(
        "articles", [{"title": f"t{i}", "source": "s", "score": i} for i in range(10)]
    )
    result = await connector.query("SELECT title FROM articles", max_rows=4)
    assert result["row_count"] == 4
    assert result["truncated"] is True


async def test_execute_returns_rowcount(connector):
    await connector.insert_rows(
        "articles", [{"title": "a", "source": "x", "score": 1},
                     {"title": "b", "source": "x", "score": 2}]
    )
    result = await connector.execute(
        "UPDATE articles SET score = 0 WHERE source = :src", params={"src": "x"}
    )
    assert result["rowcount"] == 2


async def test_insert_rejects_bad_identifiers(connector):
    with pytest.raises(ValueError, match="table"):
        await connector.insert_rows("articles; DROP TABLE x", [{"a": 1}])
    with pytest.raises(ValueError, match="column"):
        await connector.insert_rows("articles", [{"bad-col": 1}])
    with pytest.raises(ValueError, match="non-empty"):
        await connector.insert_rows("articles", [])


async def test_list_tables(connector):
    result = await connector.list_tables()
    assert result == {"tables": ["articles"]}


async def test_validate_config():
    connector = PostgresConnector(connector_id="x", connection_string="")
    with pytest.raises(ValueError, match="connection_string"):
        await connector.validate_config()
