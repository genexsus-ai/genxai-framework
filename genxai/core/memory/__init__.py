"""Memory system for GenXAI agents."""

from genxai.core.memory.backends import (
    MemoryBackendPlugin,
    MemoryBackendRegistry,
    Neo4jMemoryBackendPlugin,
    RedisMemoryBackendPlugin,
    SqliteMemoryBackendPlugin,
)
from genxai.core.memory.base import Memory, MemoryConfig, MemoryType
from genxai.core.memory.long_term import LongTermMemory
from genxai.core.memory.manager import MemorySystem
from genxai.core.memory.persistence import JsonMemoryStore, MemoryPersistenceConfig
from genxai.core.memory.shared import SharedMemoryBus
from genxai.core.memory.short_term import ShortTermMemory

__all__ = [
    "Memory",
    "MemoryType",
    "MemoryConfig",
    "ShortTermMemory",
    "LongTermMemory",
    "MemorySystem",
    "MemoryPersistenceConfig",
    "JsonMemoryStore",
    "MemoryBackendPlugin",
    "MemoryBackendRegistry",
    "RedisMemoryBackendPlugin",
    "SqliteMemoryBackendPlugin",
    "Neo4jMemoryBackendPlugin",
]
