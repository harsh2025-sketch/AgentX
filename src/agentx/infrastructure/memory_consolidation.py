"""Infrastructure shim for deterministic consolidation (C6.04).

Infrastructure may depend on core contracts only. This shim re-exports the
pure core consolidation logic so callers that compose via
``agentx.infrastructure.memory_consolidation`` can obtain it without
establishing a forbidden ``infrastructure -> hive`` edge.
"""

from __future__ import annotations

from agentx.core.memory_consolidation import (
    ConsolidatedKnowledgeGroup,
    ConsolidationConfig,
    ConsolidationResult,
    MemoryConsolidation,
    MemoryConsolidationConfig,
    MemoryConsolidationResult,
    are_knowledge_records_equivalent,
    build_consolidated_knowledge_groups,
    canonical_for_knowledge_group,
    check_knowledge_compatibility,
    knowledge_consolidation_key,
    provenance_union_for_knowledge_group,
    select_knowledge_candidates,
)

__all__ = [
    "ConsolidatedKnowledgeGroup",
    "ConsolidationConfig",
    "ConsolidationResult",
    "MemoryConsolidation",
    "MemoryConsolidationConfig",
    "MemoryConsolidationResult",
    "are_knowledge_records_equivalent",
    "build_consolidated_knowledge_groups",
    "canonical_for_knowledge_group",
    "check_knowledge_compatibility",
    "knowledge_consolidation_key",
    "provenance_union_for_knowledge_group",
    "select_knowledge_candidates",
]
