"""Shim re-export for consolidation (C6.04).

Hive-internal shim so callers importing ``agentx.hive.consolidation`` or
``agentx.hive.memory_consolidation`` both resolve.
"""

from __future__ import annotations

from agentx.hive.memory_consolidation import (
    HiveConsolidationResult,
    HiveMemoryConsolidation,
    KnowledgeStoreConsolidationPort,
    MemoryConsolidation,
    MemoryConsolidationService,
)

__all__ = [
    "HiveConsolidationResult",
    "HiveMemoryConsolidation",
    "KnowledgeStoreConsolidationPort",
    "MemoryConsolidation",
    "MemoryConsolidationService",
]
