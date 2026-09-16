"""Evidence-backed, non-authoritative knowledge assurance contracts (AX-122/123).

Assurance is metadata about evidence and revalidation history. It is DATA,
never authority: no state in this module grants permissions, changes risk,
bypasses ActionGate, authorizes a capability, or proves task success.

The canonical ``KnowledgeRecord`` schema deliberately remains unchanged.
Assurance composes over ``KnowledgeId`` and can therefore evolve without
rewriting historical knowledge records.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid4

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeValidationError,
    _format_timestamp,
    _parse_timestamp,
)
from agentx.core.provenance import EvidenceReference

__all__ = [
    "KnowledgeAssuranceMetadata",
    "KnowledgeConfidenceState",
    "KnowledgeRevalidation",
    "KnowledgeRevalidationOutcome",
    "KnowledgeRevalidationRequest",
    "assurance_after_revalidation",
]


class KnowledgeConfidenceState(StrEnum):
    """Coarse evidence state; never a probability or authorization signal."""

    RESEARCH_ONLY = "research_only"
    PROVISIONAL = "provisional"
    SUPPORTED = "supported"
    VERIFIED_EVIDENCE = "verified_evidence"
    DEGRADED = "degraded"
    STALE = "stale"
    CONFLICTED = "conflicted"
    SUPERSEDED = "superseded"


class KnowledgeRevalidationOutcome(StrEnum):
    """Closed result vocabulary for explicit knowledge revalidation."""

    SUCCESS = "success"
    FAILURE = "failure"
    INCONCLUSIVE = "inconclusive"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    CONTRADICTION_DISCOVERED = "contradiction_discovered"
    SUPERSEDING_EVIDENCE_DISCOVERED = "superseding_evidence_discovered"


def _validate_time(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise KnowledgeValidationError(f"{field_name} must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise KnowledgeValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _parse_id(value: object, *, field_name: str) -> KnowledgeId:
    if not isinstance(value, str):
        raise KnowledgeValidationError(f"{field_name} must be a UUID string")
    try:
        return KnowledgeId.parse(value)
    except ValueError as exc:
        raise KnowledgeValidationError(f"{field_name} must be a valid non-nil UUID") from exc


def _parse_nonnegative_int(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise KnowledgeValidationError(f"{field_name} must be a non-negative integer")
    return value


def _parse_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, str):
        raise KnowledgeValidationError(f"{field_name} must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise KnowledgeValidationError(f"{field_name} must be a UUID string") from exc
    if parsed.int == 0:
        raise KnowledgeValidationError(f"{field_name} must not be the nil UUID")
    return parsed


def _validate_evidence(value: object) -> tuple[EvidenceReference, ...]:
    if not isinstance(value, tuple):
        raise KnowledgeValidationError("evidence must be a tuple")
    for item in value:
        if not isinstance(item, EvidenceReference):
            raise KnowledgeValidationError("evidence must contain EvidenceReference values")
    return value


def _evidence_from_json(
    value: object,
    *,
    field_name: str,
) -> tuple[EvidenceReference, ...]:
    # ``from_dict`` is used both on direct JSON-decoded objects (list) and on
    # EventJournal payloads after their canonical immutable freeze (tuple).
    # Accept only those two sequence shapes; strings/mappings remain rejected.
    if not isinstance(value, (list, tuple)):
        raise KnowledgeValidationError(f"{field_name} must be a JSON array")
    result: list[EvidenceReference] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise KnowledgeValidationError(f"{field_name}[{index}] must be a JSON object")
        copied: dict[str, object] = {}
        for key, nested in item.items():
            if not isinstance(key, str):
                raise KnowledgeValidationError(f"{field_name}[{index}] has a non-string key")
            copied[key] = nested
        result.append(EvidenceReference.from_dict(copied))
    return tuple(result)


def _require_exact(
    raw: Mapping[str, object],
    expected: frozenset[str],
    *,
    name: str,
) -> None:
    actual = set(raw)
    if actual != expected:
        missing = expected - actual
        unknown = actual - expected
        if missing:
            raise KnowledgeValidationError(f"{name} missing required fields: {sorted(missing)}")
        raise KnowledgeValidationError(f"{name} contains unknown fields: {sorted(unknown)}")


def _load_object(raw: str, *, name: str) -> dict[str, object]:
    if not isinstance(raw, str):
        raise KnowledgeValidationError(f"{name} JSON must be a string")
    try:
        decoded: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KnowledgeValidationError(f"{name} JSON is malformed") from exc
    if not isinstance(decoded, Mapping):
        raise KnowledgeValidationError(f"{name} JSON root must be an object")
    result: dict[str, object] = {}
    for key, value in decoded.items():
        if not isinstance(key, str):
            raise KnowledgeValidationError(f"{name} JSON has a non-string key")
        result[key] = value
    return result


_ASSURANCE_FIELDS: Final = frozenset(
    {
        "knowledge_id",
        "source_observed_at",
        "verification_count",
        "failure_count",
        "environment_valid",
        "fresh_until",
        "last_verification",
        "evidence",
        "confidence_state",
        "contradiction_count",
        "superseded",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeAssuranceMetadata:
    """Deterministic evidence summary attached to one knowledge record."""

    knowledge_id: KnowledgeId
    source_observed_at: datetime | None = None
    verification_count: int = 0
    failure_count: int = 0
    environment_valid: bool | None = None
    fresh_until: datetime | None = None
    last_verification: datetime | None = None
    evidence: tuple[EvidenceReference, ...] = ()
    confidence_state: KnowledgeConfidenceState = KnowledgeConfidenceState.RESEARCH_ONLY
    contradiction_count: int = 0
    superseded: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.knowledge_id, KnowledgeId):
            raise KnowledgeValidationError("knowledge_id must be a KnowledgeId")
        for name in ("verification_count", "failure_count", "contradiction_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise KnowledgeValidationError(f"{name} must be a non-negative integer")
        if self.environment_valid is not None and not isinstance(self.environment_valid, bool):
            raise KnowledgeValidationError("environment_valid must be bool or None")
        for name in ("source_observed_at", "fresh_until", "last_verification"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(
                    self,
                    name,
                    _validate_time(value, field_name=name),
                )
        _validate_evidence(self.evidence)
        if not isinstance(self.confidence_state, KnowledgeConfidenceState):
            raise KnowledgeValidationError("confidence_state must be KnowledgeConfidenceState")
        if not isinstance(self.superseded, bool):
            raise KnowledgeValidationError("superseded must be bool")

    def is_fresh_at(self, now: datetime) -> bool:
        """Return freshness without mutating confidence or granting authority."""
        current = _validate_time(now, field_name="now")
        return self.fresh_until is None or current < self.fresh_until

    def to_dict(self) -> dict[str, object]:
        return {
            "knowledge_id": self.knowledge_id.to_str(),
            "source_observed_at": (
                None
                if self.source_observed_at is None
                else _format_timestamp(self.source_observed_at)
            ),
            "verification_count": self.verification_count,
            "failure_count": self.failure_count,
            "environment_valid": self.environment_valid,
            "fresh_until": (
                None if self.fresh_until is None else _format_timestamp(self.fresh_until)
            ),
            "last_verification": (
                None
                if self.last_verification is None
                else _format_timestamp(self.last_verification)
            ),
            "evidence": [item.to_dict() for item in self.evidence],
            "confidence_state": self.confidence_state.value,
            "contradiction_count": self.contradiction_count,
            "superseded": self.superseded,
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
    def from_dict(cls, raw: Mapping[str, object]) -> KnowledgeAssuranceMetadata:
        _require_exact(raw, _ASSURANCE_FIELDS, name="knowledge assurance")
        source_raw = raw["source_observed_at"]
        fresh_raw = raw["fresh_until"]
        verified_raw = raw["last_verification"]
        state_raw = raw["confidence_state"]
        if not isinstance(state_raw, str):
            raise KnowledgeValidationError("confidence_state must be a string")
        try:
            state = KnowledgeConfidenceState(state_raw)
        except ValueError as exc:
            raise KnowledgeValidationError(f"unknown confidence_state: {state_raw!r}") from exc
        environment = raw["environment_valid"]
        if environment is not None and not isinstance(environment, bool):
            raise KnowledgeValidationError("environment_valid must be bool or null")
        superseded = raw["superseded"]
        if not isinstance(superseded, bool):
            raise KnowledgeValidationError("superseded must be bool")
        return cls(
            knowledge_id=_parse_id(raw["knowledge_id"], field_name="knowledge_id"),
            source_observed_at=(
                None
                if source_raw is None
                else _parse_timestamp(
                    source_raw,
                    field_name="source_observed_at",
                )
            ),
            verification_count=_parse_nonnegative_int(
                raw["verification_count"],
                field_name="verification_count",
            ),
            failure_count=_parse_nonnegative_int(
                raw["failure_count"],
                field_name="failure_count",
            ),
            environment_valid=environment,
            fresh_until=(
                None if fresh_raw is None else _parse_timestamp(fresh_raw, field_name="fresh_until")
            ),
            last_verification=(
                None
                if verified_raw is None
                else _parse_timestamp(
                    verified_raw,
                    field_name="last_verification",
                )
            ),
            evidence=_evidence_from_json(raw["evidence"], field_name="evidence"),
            confidence_state=state,
            contradiction_count=_parse_nonnegative_int(
                raw["contradiction_count"],
                field_name="contradiction_count",
            ),
            superseded=superseded,
        )

    @classmethod
    def from_json(cls, raw: str) -> KnowledgeAssuranceMetadata:
        return cls.from_dict(_load_object(raw, name="knowledge assurance"))


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeRevalidationRequest:
    """Explicit request to re-check one knowledge record against new evidence."""

    revalidation_id: UUID
    knowledge_id: KnowledgeId
    requested_at: datetime
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.revalidation_id, UUID) or self.revalidation_id.int == 0:
            raise KnowledgeValidationError("revalidation_id must be a non-nil UUID")
        if not isinstance(self.knowledge_id, KnowledgeId):
            raise KnowledgeValidationError("knowledge_id must be a KnowledgeId")
        object.__setattr__(
            self,
            "requested_at",
            _validate_time(self.requested_at, field_name="requested_at"),
        )
        if (
            not isinstance(self.source, str)
            or not self.source
            or self.source != self.source.strip()
        ):
            raise KnowledgeValidationError("source must be a non-empty trimmed string")

    @classmethod
    def create(
        cls,
        *,
        knowledge_id: KnowledgeId,
        source: str,
        requested_at: datetime | None = None,
    ) -> KnowledgeRevalidationRequest:
        return cls(
            revalidation_id=uuid4(),
            knowledge_id=knowledge_id,
            requested_at=datetime.now(UTC) if requested_at is None else requested_at,
            source=source,
        )


_REVALIDATION_FIELDS: Final = frozenset(
    {
        "revalidation_id",
        "knowledge_id",
        "requested_at",
        "completed_at",
        "source",
        "outcome",
        "evidence",
        "related_knowledge_id",
        "environment_reference",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeRevalidation:
    """Completed revalidation with independently attributable evidence."""

    revalidation_id: UUID
    knowledge_id: KnowledgeId
    requested_at: datetime
    completed_at: datetime
    source: str
    outcome: KnowledgeRevalidationOutcome
    evidence: tuple[EvidenceReference, ...]
    related_knowledge_id: KnowledgeId | None = None
    environment_reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.revalidation_id, UUID) or self.revalidation_id.int == 0:
            raise KnowledgeValidationError("revalidation_id must be a non-nil UUID")
        if not isinstance(self.knowledge_id, KnowledgeId):
            raise KnowledgeValidationError("knowledge_id must be a KnowledgeId")
        object.__setattr__(
            self,
            "requested_at",
            _validate_time(self.requested_at, field_name="requested_at"),
        )
        object.__setattr__(
            self,
            "completed_at",
            _validate_time(self.completed_at, field_name="completed_at"),
        )
        if self.completed_at < self.requested_at:
            raise KnowledgeValidationError("completed_at must not precede requested_at")
        if (
            not isinstance(self.source, str)
            or not self.source
            or self.source != self.source.strip()
        ):
            raise KnowledgeValidationError("source must be a non-empty trimmed string")
        if not isinstance(self.outcome, KnowledgeRevalidationOutcome):
            raise KnowledgeValidationError("outcome must be KnowledgeRevalidationOutcome")
        _validate_evidence(self.evidence)
        if not self.evidence:
            raise KnowledgeValidationError("completed revalidation requires attributable evidence")
        if self.related_knowledge_id is not None and not isinstance(
            self.related_knowledge_id, KnowledgeId
        ):
            raise KnowledgeValidationError("related_knowledge_id must be KnowledgeId or None")
        if (
            self.outcome
            in {
                KnowledgeRevalidationOutcome.CONTRADICTION_DISCOVERED,
                KnowledgeRevalidationOutcome.SUPERSEDING_EVIDENCE_DISCOVERED,
            }
            and self.related_knowledge_id is None
        ):
            raise KnowledgeValidationError(
                "relationship-discovery outcome requires related_knowledge_id"
            )
        if self.environment_reference is not None and (
            not isinstance(self.environment_reference, str)
            or not self.environment_reference
            or self.environment_reference != self.environment_reference.strip()
        ):
            raise KnowledgeValidationError("environment_reference must be non-empty and trimmed")

    def to_dict(self) -> dict[str, object]:
        return {
            "revalidation_id": str(self.revalidation_id),
            "knowledge_id": self.knowledge_id.to_str(),
            "requested_at": _format_timestamp(self.requested_at),
            "completed_at": _format_timestamp(self.completed_at),
            "source": self.source,
            "outcome": self.outcome.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "related_knowledge_id": (
                None if self.related_knowledge_id is None else self.related_knowledge_id.to_str()
            ),
            "environment_reference": self.environment_reference,
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
    def from_dict(cls, raw: Mapping[str, object]) -> KnowledgeRevalidation:
        _require_exact(raw, _REVALIDATION_FIELDS, name="knowledge revalidation")
        source = raw["source"]
        if not isinstance(source, str):
            raise KnowledgeValidationError("source must be a string")
        outcome_raw = raw["outcome"]
        if not isinstance(outcome_raw, str):
            raise KnowledgeValidationError("outcome must be a string")
        try:
            outcome = KnowledgeRevalidationOutcome(outcome_raw)
        except ValueError as exc:
            raise KnowledgeValidationError(
                f"unknown revalidation outcome: {outcome_raw!r}"
            ) from exc
        related_raw = raw["related_knowledge_id"]
        environment = raw["environment_reference"]
        if environment is not None and not isinstance(environment, str):
            raise KnowledgeValidationError("environment_reference must be a string or null")
        return cls(
            revalidation_id=_parse_uuid(
                raw["revalidation_id"],
                field_name="revalidation_id",
            ),
            knowledge_id=_parse_id(
                raw["knowledge_id"],
                field_name="knowledge_id",
            ),
            requested_at=_parse_timestamp(
                raw["requested_at"],
                field_name="requested_at",
            ),
            completed_at=_parse_timestamp(
                raw["completed_at"],
                field_name="completed_at",
            ),
            source=source,
            outcome=outcome,
            evidence=_evidence_from_json(raw["evidence"], field_name="evidence"),
            related_knowledge_id=(
                None
                if related_raw is None
                else _parse_id(
                    related_raw,
                    field_name="related_knowledge_id",
                )
            ),
            environment_reference=environment,
        )

    @classmethod
    def from_json(cls, raw: str) -> KnowledgeRevalidation:
        return cls.from_dict(_load_object(raw, name="knowledge revalidation"))


def assurance_after_revalidation(
    current: KnowledgeAssuranceMetadata,
    result: KnowledgeRevalidation,
) -> KnowledgeAssuranceMetadata:
    """Apply deterministic evidence rules without conferring authority."""
    if current.knowledge_id != result.knowledge_id:
        raise KnowledgeValidationError(
            "revalidation knowledge_id does not match assurance metadata"
        )

    verification_count = current.verification_count
    failure_count = current.failure_count
    environment_valid = current.environment_valid
    state = current.confidence_state
    contradiction_count = current.contradiction_count
    superseded = current.superseded
    last_verification = current.last_verification

    if result.outcome is KnowledgeRevalidationOutcome.SUCCESS:
        verification_count += 1
        environment_valid = True
        last_verification = result.completed_at
        state = (
            KnowledgeConfidenceState.VERIFIED_EVIDENCE
            if verification_count >= 2 and failure_count == 0
            else KnowledgeConfidenceState.SUPPORTED
        )
    elif result.outcome is KnowledgeRevalidationOutcome.FAILURE:
        failure_count += 1
        last_verification = result.completed_at
        state = KnowledgeConfidenceState.DEGRADED
    elif result.outcome is KnowledgeRevalidationOutcome.INCONCLUSIVE:
        state = KnowledgeConfidenceState.PROVISIONAL if verification_count == 0 else state
    elif result.outcome is KnowledgeRevalidationOutcome.ENVIRONMENT_MISMATCH:
        failure_count += 1
        environment_valid = False
        last_verification = result.completed_at
        state = KnowledgeConfidenceState.DEGRADED
    elif result.outcome is KnowledgeRevalidationOutcome.CONTRADICTION_DISCOVERED:
        contradiction_count += 1
        state = KnowledgeConfidenceState.CONFLICTED
    elif result.outcome is KnowledgeRevalidationOutcome.SUPERSEDING_EVIDENCE_DISCOVERED:
        superseded = True
        state = KnowledgeConfidenceState.SUPERSEDED

    return KnowledgeAssuranceMetadata(
        knowledge_id=current.knowledge_id,
        source_observed_at=current.source_observed_at,
        verification_count=verification_count,
        failure_count=failure_count,
        environment_valid=environment_valid,
        fresh_until=current.fresh_until,
        last_verification=last_verification,
        evidence=current.evidence + result.evidence,
        confidence_state=state,
        contradiction_count=contradiction_count,
        superseded=superseded,
    )
