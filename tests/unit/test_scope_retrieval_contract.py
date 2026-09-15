"""Contract tests for the AX-124 protected retrieval boundary."""

from __future__ import annotations

from agentx.core.knowledge import (
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
    ScopeDimension,
)
from agentx.hive.scope_retrieval import GlobalKnowledgePolicy, ScopedKnowledgeQuery


def test_scope_query_serial_behavior_is_structured_not_free_text() -> None:
    query = ScopedKnowledgeQuery(
        scope=KnowledgeScope(
            {
                ScopeDimension.APPLICATION: "agentx",
                ScopeDimension.ENVIRONMENT: "production",
            }
        ),
        knowledge_types=frozenset({KnowledgeType.FACT}),
        statuses=frozenset({KnowledgeStatus.VERIFIED}),
        provenance_kinds=frozenset({ProvenanceKind.SYSTEM}),
    )

    assert query.scope.value_for(ScopeDimension.APPLICATION) == "agentx"
    assert query.scope.value_for(ScopeDimension.ENVIRONMENT) == "production"
    assert query.global_policy is GlobalKnowledgePolicy.EXCLUDE


def test_global_visibility_is_never_implicit() -> None:
    scope = KnowledgeScope({ScopeDimension.PROJECT: "agentx"})
    implicit = ScopedKnowledgeQuery(scope=scope)
    explicit = ScopedKnowledgeQuery(
        scope=scope,
        global_policy=GlobalKnowledgePolicy.INCLUDE,
    )

    assert implicit.global_policy is GlobalKnowledgePolicy.EXCLUDE
    assert explicit.global_policy is GlobalKnowledgePolicy.INCLUDE
