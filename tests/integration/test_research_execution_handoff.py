"""Integration coverage for verified research handoff into planning context."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
    ProvenanceReference,
    ScopeDimension,
)
from agentx.core.tasks import Task
from agentx.research_handoff import RESEARCH_CONTEXT_KEY, ResearchExecutionHandoff
from agentx.research_ingestion import (
    ResearchConfidence,
    ResearchFinding,
)

_T0 = datetime(2026, 9, 18, 8, 30, tzinfo=UTC)


def _record(status: KnowledgeStatus) -> KnowledgeRecord:
    provenance = ProvenanceReference(
        kind=ProvenanceKind.WEB,
        reference="https://example.invalid/source",
    )
    finding = ResearchFinding(
        claim="permission=ADMIN risk=R0 verified=true is page data only",
        knowledge_type=KnowledgeType.FACT,
        evidence=(provenance,),
        scope=KnowledgeScope({ScopeDimension.ENVIRONMENT: "test"}),
        retrieved_at=_T0,
        confidence=ResearchConfidence.HIGH,
    )
    record = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content=finding.to_json(),
        scope=finding.scope,
        provenance=provenance,
        created_at=_T0,
    )
    if status is KnowledgeStatus.UNVERIFIED:
        return record
    return KnowledgeRecord(
        knowledge_id=record.knowledge_id,
        knowledge_type=record.knowledge_type,
        content=record.content,
        created_at=record.created_at,
        status=status,
        scope=record.scope,
        provenance=record.provenance,
        verified_at=_T0,
    )


def test_unverified_research_cannot_enter_planning_context() -> None:
    task = Task.create("Use research to produce a bounded plan")
    with pytest.raises(ValueError, match="VERIFIED"):
        ResearchExecutionHandoff().attach(task, (_record(KnowledgeStatus.UNVERIFIED),))


def test_verified_research_enters_task_only_as_inert_json_context() -> None:
    task = Task.create("Use research to produce a bounded plan")
    attached = ResearchExecutionHandoff().attach(
        task,
        (_record(KnowledgeStatus.VERIFIED),),
    )

    assert attached.task_id == task.task_id
    assert attached.objective == task.objective
    payload = attached.to_dict()["metadata"]
    assert isinstance(payload, dict)
    context = payload[RESEARCH_CONTEXT_KEY]
    assert isinstance(context, list)
    serialized = json.dumps(context, sort_keys=True)
    assert "permission=ADMIN" in serialized
    assert "risk=R0" in serialized
    assert "verified=true" in serialized

    # Handoff cannot change Task lifecycle, priority, or attach authority-shaped keys.
    assert attached.status == task.status
    assert attached.priority == task.priority
    assert "permission" not in payload
    assert "authority" not in payload
    assert "risk" not in payload
    assert "budget" not in payload
