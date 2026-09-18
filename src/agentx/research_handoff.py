"""Verified research handoff into canonical task planning context (AX-368).

Research enters durable memory as UNVERIFIED evidence.  This module permits a
handoff to planning/execution context only after the canonical KnowledgeRecord
has independently reached VERIFIED.  The resulting Task metadata is inert
JSON context: it carries claim/provenance identifiers only and cannot carry
permission, risk, budget, stop, or verification authority.

The handoff does not execute a plan, mutate knowledge status, call a model, or
grant capability access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from agentx.core.knowledge import KnowledgeRecord, KnowledgeStatus
from agentx.core.tasks import Task
from agentx.research_ingestion import decode_research_finding

__all__ = [
    "RESEARCH_CONTEXT_KEY",
    "ResearchContextEntry",
    "ResearchExecutionHandoff",
]

RESEARCH_CONTEXT_KEY: Final[str] = "research_context"
_MAX_HANDOFF_RECORDS: Final[int] = 32


@dataclass(frozen=True, slots=True)
class ResearchContextEntry:
    knowledge_id: str
    claim: str
    provenance_source: str

    def __post_init__(self) -> None:
        for name, value in (
            ("knowledge_id", self.knowledge_id),
            ("claim", self.claim),
            ("provenance_source", self.provenance_source),
        ):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"{name} must be a non-empty trimmed string")

    def to_dict(self) -> dict[str, str]:
        return {
            "knowledge_id": self.knowledge_id,
            "claim": self.claim,
            "provenance_source": self.provenance_source,
        }


class ResearchExecutionHandoff:
    """Build one new canonical Task carrying only verified research context."""

    __slots__ = ()

    def attach(
        self,
        task: Task,
        records: tuple[KnowledgeRecord, ...],
    ) -> Task:
        if not isinstance(task, Task):
            raise TypeError("task must be a Task")
        if not isinstance(records, tuple):
            raise TypeError("records must be a tuple")
        if not records or len(records) > _MAX_HANDOFF_RECORDS:
            raise ValueError("records must contain 1..32 verified research records")

        entries: list[ResearchContextEntry] = []
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, KnowledgeRecord):
                raise TypeError("records must contain KnowledgeRecord values")
            if record.status is not KnowledgeStatus.VERIFIED:
                raise ValueError("research handoff requires VERIFIED canonical knowledge")
            knowledge_id = record.knowledge_id.to_str()
            if knowledge_id in seen:
                raise ValueError("research handoff records must be unique")
            seen.add(knowledge_id)
            finding = decode_research_finding(record)
            entries.append(
                ResearchContextEntry(
                    knowledge_id=knowledge_id,
                    claim=finding.claim,
                    provenance_source=(
                        record.provenance.reference
                        if record.provenance is not None
                        else "missing-provenance"
                    ),
                )
            )

        metadata = task.to_dict()["metadata"]
        assert isinstance(metadata, dict)
        if RESEARCH_CONTEXT_KEY in metadata:
            raise ValueError("task already carries research_context")
        metadata[RESEARCH_CONTEXT_KEY] = [entry.to_dict() for entry in entries]
        return Task.create(
            task_id=task.task_id,
            objective=task.objective,
            status=task.status,
            priority=task.priority,
            parent_task_id=task.parent_task_id,
            created_at=task.created_at,
            metadata=metadata,
        )
