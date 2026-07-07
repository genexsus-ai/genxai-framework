"""Unit tests for memory persistence backends."""

from pathlib import Path

from genxai.core.memory.persistence import (
    MemoryPersistenceConfig,
    JsonMemoryStore,
    SqliteMemoryStore,
    create_memory_store,
)


def test_json_memory_store_save_and_load(tmp_path: Path) -> None:
    config = MemoryPersistenceConfig(base_dir=tmp_path, enabled=True, backend="json")
    store = JsonMemoryStore(config)
    items = [{"id": "1", "value": "alpha"}]
    store.save_list("items.json", items)
    assert store.load_list("items.json") == items


def test_json_memory_store_mapping(tmp_path: Path) -> None:
    config = MemoryPersistenceConfig(base_dir=tmp_path, enabled=True, backend="json")
    store = JsonMemoryStore(config)
    mapping = {"foo": "bar"}
    store.save_mapping("map.json", mapping)
    assert store.load_mapping("map.json") == mapping


def test_sqlite_memory_store_roundtrip(tmp_path: Path) -> None:
    config = MemoryPersistenceConfig(base_dir=tmp_path, enabled=True, backend="sqlite")
    store = SqliteMemoryStore(config)
    items = [{"id": "1", "value": "alpha"}]
    store.save_list("items", items)
    assert store.load_list("items") == items
    mapping = {"foo": "bar"}
    store.save_mapping("map", mapping)
    assert store.load_mapping("map") == mapping


def test_create_memory_store_factory(tmp_path: Path) -> None:
    config = MemoryPersistenceConfig(base_dir=tmp_path, enabled=True, backend="sqlite")
    store = create_memory_store(config)
    assert isinstance(store, SqliteMemoryStore)


import pytest

from genxai.core.memory.base import MemoryConfig
from genxai.core.memory.manager import MemorySystem


def _memory_system(tmp_path: Path, agent_id: str = "agent-1") -> MemorySystem:
    # Keep the system lightweight: only short-term matters for these tests
    config = MemoryConfig(
        long_term_enabled=False,
        episodic_enabled=False,
        semantic_enabled=False,
        procedural_enabled=False,
        vector_db=None,
        graph_db=None,
    )
    return MemorySystem(
        agent_id=agent_id,
        config=config,
        persistence_enabled=True,
        persistence_path=tmp_path,
    )


@pytest.mark.asyncio
async def test_short_term_memory_survives_across_instances(tmp_path: Path) -> None:
    first = _memory_system(tmp_path)
    await first.add_to_short_term({"task": "What is 40 + 2?", "response": "42"})

    # A brand-new system with the same agent_id + path recalls the interaction
    second = _memory_system(tmp_path)
    context = await second.get_short_term_context()
    assert "40 + 2" in context
    assert "42" in context

    # ... while a different agent_id starts clean
    other = _memory_system(tmp_path, agent_id="agent-2")
    assert await other.get_short_term_context() == ""


@pytest.mark.asyncio
async def test_short_term_clear_also_clears_persisted_file(tmp_path: Path) -> None:
    first = _memory_system(tmp_path)
    await first.add_to_short_term({"task": "remember me", "response": "ok"})
    await first.clear_short_term()

    second = _memory_system(tmp_path)
    assert await second.get_short_term_context() == ""
