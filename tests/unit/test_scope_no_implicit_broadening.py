"""AX-124 no implicit broadening proof."""

from __future__ import annotations

from agentx.core.knowledge import KnowledgeScope, ScopeDimension
from agentx.hive.scope_retrieval import GlobalKnowledgePolicy, ScopedKnowledgeQuery


def test_scope_query_has_no_string_or_boolean_escape_hatch() -> None:
    query = ScopedKnowledgeQuery(
        scope=KnowledgeScope({ScopeDimension.PROJECT: "agentx"})
    )
    assert query.global_policy is GlobalKnowledgePolicy.EXCLUDE
    assert not hasattr(query, "scope_text")
    assert not hasattr(query, "allow_all")
