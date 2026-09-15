"""AX-122 strict schema validation."""

from __future__ import annotations

import pytest

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import KnowledgeValidationError
from agentx.core.knowledge_assurance import KnowledgeAssuranceMetadata


def test_assurance_unknown_fields_fail_closed() -> None:
    raw = KnowledgeAssuranceMetadata(knowledge_id=KnowledgeId.create()).to_dict()
    raw["authority"] = "ADMIN"
    with pytest.raises(KnowledgeValidationError):
        KnowledgeAssuranceMetadata.from_dict(raw)
