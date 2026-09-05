"""Deterministic knowledge/execution gap detector (A4.01).

This module owns one narrow boundary:

    explicit knowledge requirements + explicitly supplied AgentX evidence
    -> deterministic SUFFICIENT/GAP assessment

It does not retrieve, research, plan, route, execute, learn, or persist. The
caller must supply already-existing canonical :class:`KnowledgeRecord` objects
(for example records previously returned by the C2.09 deterministic Hive
retrieval boundary). The detector never opens storage, never performs a Hive
query on its own, and never inspects arbitrary filesystem, network, model,
capability, procedure, or cache state.

Requirement matching is intentionally structural and conservative:

* every requirement names one or more acceptable canonical ``KnowledgeId``
  values;
* every requirement explicitly names the lifecycle statuses acceptable for that
  requirement;
* optional type, scope, and provenance predicates reuse the canonical C2.02 /
  C2.07 contracts and are exact deterministic comparisons;
* record ``content`` is never interpreted. Text such as ``"ADMIN"``,
  ``"verified=true"``, ``"permission=WRITE"``, ``"research approved"``,
  ``"risk=R0"``, or ``"budget=unlimited"`` remains inert data.

C2.08 lifecycle statuses retain their canonical meanings. A4.01 never promotes,
demotes, verifies, degrades, resolves conflicts, chooses a truth winner, or
reactivates superseded knowledge. A ``VERIFIED`` record is evidence quality, not
machine authority; a ``SUFFICIENT`` assessment grants no permission to act, and
a ``GAP`` assessment grants no permission to research.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
)

__all__ = [
    "KnowledgeGapAssessment",
    "KnowledgeGapAssessmentRequest",
    "KnowledgeGapAssessmentStatus",
    "KnowledgeGapDetector",
    "KnowledgeGapRequirement",
    "KnowledgeGapValidationError",
    "KnowledgeRequirementAssessment",
]

_MAX_REQUIREMENT_ID_LENGTH: Final[int] = 128


class KnowledgeGapValidationError(ValueError):
    """Raised when an A4.01 gap-detection input violates the typed contract."""


class KnowledgeGapAssessmentStatus(StrEnum):
    """The complete A4.01 outcome vocabulary."""

    SUFFICIENT = "sufficient"
    GAP = "gap"


def _validate_requirement_id(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("requirement_id must be a string")
    if value == "" or value != value.strip():
        raise KnowledgeGapValidationError("requirement_id must be non-empty and trimmed")
    if "\x00" in value:
        raise KnowledgeGapValidationError("requirement_id must not contain NUL characters")
    if len(value) > _MAX_REQUIREMENT_ID_LENGTH:
        raise KnowledgeGapValidationError(
            f"requirement_id must not exceed {_MAX_REQUIREMENT_ID_LENGTH} characters"
        )
    return value


def _validate_optional_reference(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    if value == "" or value != value.strip():
        raise KnowledgeGapValidationError(f"{field_name} must be non-empty and trimmed")
    if "\x00" in value:
        raise KnowledgeGapValidationError(f"{field_name} must not contain NUL characters")
    return value


def _require_knowledge_id_set(value: object, *, field_name: str) -> frozenset[KnowledgeId]:
    if not isinstance(value, frozenset):
        raise TypeError(f"{field_name} must be a frozenset")
    if not value:
        raise KnowledgeGapValidationError(f"{field_name} must not be empty")
    for item in value:
        if not isinstance(item, KnowledgeId):
            raise KnowledgeGapValidationError(f"{field_name} entries must be KnowledgeId values")
    return value


def _require_status_set(value: object, *, field_name: str) -> frozenset[KnowledgeStatus]:
    if not isinstance(value, frozenset):
        raise TypeError(f"{field_name} must be a frozenset")
    if not value:
        raise KnowledgeGapValidationError(f"{field_name} must not be empty")
    for item in value:
        if not isinstance(item, KnowledgeStatus):
            raise KnowledgeGapValidationError(
                f"{field_name} entries must be KnowledgeStatus values"
            )
    return value


def _validate_optional_type_set(
    value: object,
    *,
    field_name: str,
) -> frozenset[KnowledgeType] | None:
    if value is None:
        return None
    if not isinstance(value, frozenset):
        raise TypeError(f"{field_name} must be a frozenset or None")
    if not value:
        raise KnowledgeGapValidationError(f"{field_name} must not be empty")
    for item in value:
        if not isinstance(item, KnowledgeType):
            raise KnowledgeGapValidationError(f"{field_name} entries must be KnowledgeType values")
    return value


def _require_requirement_tuple(value: object) -> tuple[KnowledgeGapRequirement, ...]:
    if not isinstance(value, tuple):
        raise TypeError("requirements must be a tuple")
    if not value:
        raise KnowledgeGapValidationError("requirements must not be empty")
    for requirement in value:
        if not isinstance(requirement, KnowledgeGapRequirement):
            raise KnowledgeGapValidationError(
                "requirements entries must be KnowledgeGapRequirement values"
            )
    return value


def _require_evidence_tuple(value: object) -> tuple[KnowledgeRecord, ...]:
    if not isinstance(value, tuple):
        raise TypeError("evidence must be a tuple")
    for record in value:
        if not isinstance(record, KnowledgeRecord):
            raise KnowledgeGapValidationError("evidence entries must be KnowledgeRecord values")
    return value


def _require_assessment_tuple(
    value: object,
) -> tuple[KnowledgeRequirementAssessment, ...]:
    if not isinstance(value, tuple):
        raise TypeError("requirement_assessments must be a tuple")
    if not value:
        raise KnowledgeGapValidationError("requirement_assessments must not be empty")
    for assessment in value:
        if not isinstance(assessment, KnowledgeRequirementAssessment):
            raise KnowledgeGapValidationError(
                "requirement_assessments entries must be KnowledgeRequirementAssessment values"
            )
    return value


def _require_knowledge_id_tuple(value: object, *, field_name: str) -> tuple[KnowledgeId, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple")
    for item in value:
        if not isinstance(item, KnowledgeId):
            raise KnowledgeGapValidationError(f"{field_name} entries must be KnowledgeId values")
    return value


def _scope_satisfies_requirement(
    record_scope: KnowledgeScope,
    required_scope: KnowledgeScope,
) -> bool:
    """Return whether ``record_scope`` carries every required dimension exactly."""

    return all(
        record_scope.dimensions.get(dimension) == value
        for dimension, value in required_scope.dimensions.items()
    )


def _knowledge_id_sort_key(knowledge_id: KnowledgeId) -> str:
    return knowledge_id.to_str()


def _record_sort_key(record: KnowledgeRecord) -> tuple[str, str]:
    # Match the canonical C2.09 / KnowledgeStore order: created_at ascending,
    # then stable identity. ``created_at`` is already UTC-normalized by the
    # KnowledgeRecord contract, and its ISO representation sorts chronologically.
    return (record.created_at.isoformat(), record.knowledge_id.to_str())


def _require_unique_requirement_ids(requirements: tuple[KnowledgeGapRequirement, ...]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for requirement in requirements:
        if requirement.requirement_id in seen:
            duplicates.append(requirement.requirement_id)
        seen.add(requirement.requirement_id)
    if duplicates:
        raise KnowledgeGapValidationError(
            f"requirements must have unique requirement_id values: {sorted(set(duplicates))}"
        )


def _require_unique_evidence_ids(records: tuple[KnowledgeRecord, ...]) -> None:
    seen: set[KnowledgeId] = set()
    duplicates: list[str] = []
    for record in records:
        if record.knowledge_id in seen:
            duplicates.append(record.knowledge_id.to_str())
        seen.add(record.knowledge_id)
    if duplicates:
        raise KnowledgeGapValidationError(
            f"evidence must not contain duplicate KnowledgeId values: {sorted(set(duplicates))}"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeGapRequirement:
    """One explicit structural requirement for existing knowledge evidence.

    A requirement is satisfied by at least one supplied canonical
    :class:`KnowledgeRecord` whose identity is explicitly accepted and whose
    lifecycle/status, type, scope, and provenance fields meet every configured
    predicate.

    ``acceptable_statuses`` is required rather than defaulted so A4.01 never
    decides which lifecycle states are load-bearing for a caller. Include
    ``KnowledgeStatus.VERIFIED`` only when the caller explicitly requires
    verified knowledge; include exceptional statuses only when the caller
    explicitly intends to assess those inert records.
    """

    requirement_id: str
    acceptable_knowledge_ids: frozenset[KnowledgeId]
    acceptable_statuses: frozenset[KnowledgeStatus]
    knowledge_types: frozenset[KnowledgeType] | None = None
    scope: KnowledgeScope | None = None
    provenance_kind: ProvenanceKind | None = None
    provenance_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirement_id", _validate_requirement_id(self.requirement_id))
        _require_knowledge_id_set(
            self.acceptable_knowledge_ids,
            field_name="acceptable_knowledge_ids",
        )
        _require_status_set(self.acceptable_statuses, field_name="acceptable_statuses")
        _validate_optional_type_set(self.knowledge_types, field_name="knowledge_types")
        if self.scope is not None and not isinstance(self.scope, KnowledgeScope):
            raise TypeError("scope must be a KnowledgeScope or None")
        if self.provenance_kind is not None and not isinstance(
            self.provenance_kind,
            ProvenanceKind,
        ):
            raise TypeError("provenance_kind must be a ProvenanceKind or None")
        object.__setattr__(
            self,
            "provenance_reference",
            _validate_optional_reference(
                self.provenance_reference,
                field_name="provenance_reference",
            ),
        )

    def is_satisfied_by(self, record: KnowledgeRecord) -> bool:
        """Return whether ``record`` satisfies this requirement exactly.

        The predicate reads only canonical structured fields. It deliberately
        ignores ``record.content`` and performs no retrieval, ranking,
        verification, contradiction resolution, or lifecycle transition.
        """

        if not isinstance(record, KnowledgeRecord):
            raise TypeError("record must be a KnowledgeRecord")
        if record.knowledge_id not in self.acceptable_knowledge_ids:
            return False
        if record.status not in self.acceptable_statuses:
            return False
        if self.knowledge_types is not None and record.knowledge_type not in self.knowledge_types:
            return False
        if self.scope is not None and not _scope_satisfies_requirement(record.scope, self.scope):
            return False
        if self.provenance_kind is not None or self.provenance_reference is not None:
            provenance = record.provenance
            if provenance is None:
                return False
            if self.provenance_kind is not None and provenance.kind is not self.provenance_kind:
                return False
            if (
                self.provenance_reference is not None
                and provenance.reference != self.provenance_reference
            ):
                return False
        return True


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeGapAssessmentRequest:
    """Explicit immutable input to the gap detector.

    ``requirements`` are canonicalized by ``requirement_id`` and ``evidence`` by
    the canonical knowledge order (``created_at``, then ``KnowledgeId``). This
    makes results independent of incidental caller ordering while preserving
    C2.09-compatible deterministic semantics.
    """

    requirements: tuple[KnowledgeGapRequirement, ...]
    evidence: tuple[KnowledgeRecord, ...] = ()

    def __post_init__(self) -> None:
        requirements = _require_requirement_tuple(self.requirements)
        _require_unique_requirement_ids(requirements)
        evidence = _require_evidence_tuple(self.evidence)
        _require_unique_evidence_ids(evidence)
        object.__setattr__(
            self,
            "requirements",
            tuple(sorted(requirements, key=lambda item: item.requirement_id)),
        )
        object.__setattr__(self, "evidence", tuple(sorted(evidence, key=_record_sort_key)))


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeRequirementAssessment:
    """Deterministic match result for one explicit requirement."""

    requirement: KnowledgeGapRequirement
    satisfying_knowledge_ids: tuple[KnowledgeId, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.requirement, KnowledgeGapRequirement):
            raise TypeError("requirement must be a KnowledgeGapRequirement")
        ids = _require_knowledge_id_tuple(
            self.satisfying_knowledge_ids,
            field_name="satisfying_knowledge_ids",
        )
        object.__setattr__(
            self,
            "satisfying_knowledge_ids",
            tuple(sorted(ids, key=_knowledge_id_sort_key)),
        )

    @property
    def satisfied(self) -> bool:
        """Return whether at least one explicitly supplied record satisfied it."""

        return bool(self.satisfying_knowledge_ids)


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeGapAssessment:
    """Ephemeral deterministic A4.01 result.

    The assessment is data only. It has no permission, authority, risk, budget,
    stop, task-transition, model, research, capability, procedure, persistence,
    or lifecycle-promotion fields.
    """

    status: KnowledgeGapAssessmentStatus
    requirement_assessments: tuple[KnowledgeRequirementAssessment, ...]
    supplied_knowledge_ids: tuple[KnowledgeId, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, KnowledgeGapAssessmentStatus):
            raise TypeError("status must be a KnowledgeGapAssessmentStatus")
        assessments = _require_assessment_tuple(self.requirement_assessments)
        supplied_ids = _require_knowledge_id_tuple(
            self.supplied_knowledge_ids,
            field_name="supplied_knowledge_ids",
        )
        expected_status = (
            KnowledgeGapAssessmentStatus.SUFFICIENT
            if all(assessment.satisfied for assessment in assessments)
            else KnowledgeGapAssessmentStatus.GAP
        )
        if self.status is not expected_status:
            raise KnowledgeGapValidationError(
                f"status {self.status.value!r} is inconsistent with requirement assessments; "
                f"expected {expected_status.value!r}"
            )
        object.__setattr__(
            self,
            "requirement_assessments",
            tuple(sorted(assessments, key=lambda item: item.requirement.requirement_id)),
        )
        object.__setattr__(
            self,
            "supplied_knowledge_ids",
            tuple(sorted(supplied_ids, key=_knowledge_id_sort_key)),
        )

    @property
    def is_sufficient(self) -> bool:
        """Return ``True`` only when every explicit requirement was satisfied."""

        return self.status is KnowledgeGapAssessmentStatus.SUFFICIENT

    @property
    def assessed_requirements(self) -> tuple[KnowledgeGapRequirement, ...]:
        """Return every assessed requirement in deterministic order."""

        return tuple(assessment.requirement for assessment in self.requirement_assessments)

    @property
    def unmet_requirements(self) -> tuple[KnowledgeGapRequirement, ...]:
        """Return the explicit requirements that remain unmet."""

        return tuple(
            assessment.requirement
            for assessment in self.requirement_assessments
            if not assessment.satisfied
        )


class KnowledgeGapDetector:
    """Pure stateless A4.01 detector for explicit knowledge requirements."""

    __slots__ = ()

    def assess(self, request: KnowledgeGapAssessmentRequest) -> KnowledgeGapAssessment:
        """Assess explicit evidence against explicit requirements.

        No side effects occur: the method performs no I/O, persistence,
        retrieval, model invocation, capability execution, procedure activation,
        routing, planning, research, cache lookup, status transition, or event
        publication.
        """

        if not isinstance(request, KnowledgeGapAssessmentRequest):
            raise TypeError("request must be a KnowledgeGapAssessmentRequest")

        assessments = tuple(
            KnowledgeRequirementAssessment(
                requirement=requirement,
                satisfying_knowledge_ids=tuple(
                    record.knowledge_id
                    for record in request.evidence
                    if requirement.is_satisfied_by(record)
                ),
            )
            for requirement in request.requirements
        )
        status = (
            KnowledgeGapAssessmentStatus.SUFFICIENT
            if all(assessment.satisfied for assessment in assessments)
            else KnowledgeGapAssessmentStatus.GAP
        )
        return KnowledgeGapAssessment(
            status=status,
            requirement_assessments=assessments,
            supplied_knowledge_ids=tuple(record.knowledge_id for record in request.evidence),
        )
