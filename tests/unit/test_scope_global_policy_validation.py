"""AX-124 fail-closed global-policy validation."""

from __future__ import annotations

import pytest

from agentx.core.knowledge import KnowledgeScope, ScopeDimension
from agentx.hive.scope_retrieval import ScopedKnowledgeQuery, ScopedKnowledgeRetrievalError


def test_untyped_global_policy_is_rejected() -> None:
    with pytest.raises(ScopedKnowledgeRetrievalError):
        ScopedKnowledgeQuery(
            scope=KnowledgeScope({ScopeDimension.PROJECT: "agentx"}),
            global_policy="include",  # type: ignore[arg-type]
        )
