"""Integration-owned durable UNVERIFIED research ingestion (AX-366).

Research findings are encoded as deterministic inert payloads inside canonical
KnowledgeRecord values and committed through the existing KnowledgeStore. This
module is deliberately outside ``agentx.cognition`` because persistence is an
outer integration concern; cognition therefore keeps no outward dependency on
infrastructure. Research evidence never promotes itself, grants authority, or
declares task success.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceReference,
    ScopeDimension,
)
from agentx.infrastructure.knowledge_store import KnowledgeStore

__all__ = [
    "CURRENT_RESEARCH_FINDING_SCHEMA_VERSION",
    "ResearchConfidence",
    "ResearchFinding",
    "ResearchFindingValidationError",
    "ResearchKnowledgeIngestor",
    "decode_research_finding",
]

CURRENT_RESEARCH_FINDING_SCHEMA_VERSION: Final[int] = 1
_MAX_CLAIM_LENGTH: Final[int] = 32_768
_MAX_EVIDENCE: Final[int] = 64


class ResearchFindingValidationError(ValueError):
    """Raised when research evidence violates the bounded ingestion contract."""


class ResearchConfidence(StrEnum):
    """Inert source-confidence annotation; never canonical knowledge trust."""

    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ResearchFindingValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > _MAX_CLAIM_LENGTH:
        raise ResearchFindingValidationError(f"{field_name} exceeds the bounded length")
    return value


def _timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("retrieved_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ResearchFindingValidationError("retrieved_at must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchFinding:
    """One bounded untrusted research claim plus provenance and applicability."""

    claim: str
    knowledge_type: KnowledgeType
    evidence: tuple[ProvenanceReference, ...]
    scope: KnowledgeScope
    retrieved_at: datetime
    confidence: ResearchConfidence = ResearchConfidence.UNKNOWN
    schema_version: int = CURRENT_RESEARCH_FINDING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim", _text(self.claim, field_name="claim"))
        if not isinstance(self.knowledge_type, KnowledgeType):
            raise TypeError("knowledge_type must be a KnowledgeType")
        if not isinstance(self.evidence, tuple) or not self.evidence:
            raise ResearchFindingValidationError("evidence must be a non-empty tuple")
        if len(self.evidence) > _MAX_EVIDENCE:
            raise ResearchFindingValidationError("evidence exceeds the bounded evidence limit")
        if any(not isinstance(item, ProvenanceReference) for item in self.evidence):
            raise TypeError("evidence must contain ProvenanceReference values")
        if not isinstance(self.scope, KnowledgeScope):
            raise TypeError("scope must be a KnowledgeScope")
        if self.scope.value_for(ScopeDimension.ENVIRONMENT) is None:
            raise ResearchFindingValidationError(
                "research ingestion requires explicit environment applicability in scope"
            )
        object.__setattr__(self, "retrieved_at", _timestamp(self.retrieved_at))
        if not isinstance(self.confidence, ResearchConfidence):
            raise TypeError("confidence must be a ResearchConfidence")
        if type(self.schema_version) is not int:
            raise TypeError("schema_version must be an int")
        if self.schema_version != CURRENT_RESEARCH_FINDING_SCHEMA_VERSION:
            raise ResearchFindingValidationError("unsupported research finding schema version")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "claim": self.claim,
            "knowledge_type": self.knowledge_type.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "scope": self.scope.to_dict(),
            "retrieved_at": self.retrieved_at.isoformat().replace("+00:00", "Z"),
            "confidence": self.confidence.value,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ResearchFinding":
        expected = {
            "schema_version",
            "claim",
            "knowledge_type",
            "evidence",
            "scope",
            "retrieved_at",
            "confidence",
        }
        if set(raw) != expected:
            raise ResearchFindingValidationError("research finding fields are incomplete or unknown")
        version = raw["schema_version"]
        if type(version) is not int or version != CURRENT_RESEARCH_FINDING_SCHEMA_VERSION:
            raise ResearchFindingValidationError("unsupported research finding schema version")
        claim = _text(raw["claim"], field_name="claim")
        kind_raw = raw["knowledge_type"]
        if not isinstance(kind_raw, str):
            raise ResearchFindingValidationError("knowledge_type must be a string")
        try:
            knowledge_type = KnowledgeType(kind_raw)
        except ValueError as exc:
            raise ResearchFindingValidationError("unknown knowledge_type") from exc
        evidence_raw = raw["evidence"]
        if not isinstance(evidence_raw, list):
            raise ResearchFindingValidationError("evidence must be an array")
        evidence = tuple(
            ProvenanceReference.from_dict(item)
            for item in evidence_raw
            if isinstance(item, Mapping)
        )
        if len(evidence) != len(evidence_raw):
            raise ResearchFindingValidationError("evidence entries must be objects")
        scope_raw = raw["scope"]
        if not isinstance(scope_raw, Mapping):
            raise ResearchFindingValidationError("scope must be an object")
        scope = KnowledgeScope.from_dict(scope_raw)
        retrieved_raw = raw["retrieved_at"]
        if not isinstance(retrieved_raw, str):
            raise ResearchFindingValidationError("retrieved_at must be a string")
        normalized = f"{retrieved_raw[:-1]}+00:00" if retrieved_raw.endswith("Z") else retrieved_raw
        try:
            retrieved_at = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ResearchFindingValidationError("retrieved_at is invalid") from exc
        confidence_raw = raw["confidence"]
        if not isinstance(confidence_raw, str):
            raise ResearchFindingValidationError("confidence must be a string")
        try:
            confidence = ResearchConfidence(confidence_raw)
        except ValueError as exc:
            raise ResearchFindingValidationError("unknown confidence") from exc
        return cls(
            claim=claim,
            knowledge_type=knowledge_type,
            evidence=evidence,
            scope=scope,
            retrieved_at=retrieved_at,
            confidence=confidence,
            schema_version=version,
        )


class ResearchKnowledgeIngestor:
    """Persist findings as canonical UNVERIFIED knowledge and nothing more."""

    __slots__ = ("_store",)

    def __init__(self, store: KnowledgeStore) -> None:
        if not isinstance(store, KnowledgeStore):
            raise TypeError("store must be a KnowledgeStore")
        self._store = store

    def ingest(self, finding: ResearchFinding) -> KnowledgeRecord:
        if not isinstance(finding, ResearchFinding):
            raise TypeError("finding must be a ResearchFinding")
        record = KnowledgeRecord.create(
            knowledge_type=finding.knowledge_type,
            content=finding.to_json(),
            scope=finding.scope,
            provenance=finding.evidence[0],
            created_at=finding.retrieved_at,
        )
        if record.status is not KnowledgeStatus.UNVERIFIED:
            raise AssertionError("new research knowledge must be born UNVERIFIED")
        self._store.insert(record)
        return record


def decode_research_finding(record: KnowledgeRecord) -> ResearchFinding:
    """Decode research evidence without changing lifecycle or trust state."""
    if not isinstance(record, KnowledgeRecord):
        raise TypeError("record must be a KnowledgeRecord")
    try:
        raw = json.loads(record.content)
    except json.JSONDecodeError as exc:
        raise ResearchFindingValidationError("record does not contain a research finding") from exc
    if not isinstance(raw, Mapping):
        raise ResearchFindingValidationError("research finding root must be an object")
    return ResearchFinding.from_dict(raw)
