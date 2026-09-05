"""Typed research objective contract (A4.02).

A4.01 (:mod:`agentx.cognition.gap_detector`) answers one question:

    do the explicitly supplied canonical knowledge records satisfy the
    explicitly stated requirements? -> ``SUFFICIENT`` or ``GAP``

A4.02 answers the *next* narrow question, and nothing beyond it:

    given an explicit ``GAP``, what is the smallest immutable typed
    description of WHAT is missing?

The result is a :class:`ResearchObjective`: inert data describing an
information need. It is not a plan, not a query, not a provider call, and not
a permission.

Explicit non-authority policy
-----------------------------

A ``GAP`` assessment grants no research permission, and neither does a
``ResearchObjective``. Constructing one does not authorize:

* web, network, socket, or HTTP access;
* filesystem access, persistence, or migration;
* browser or capability execution;
* model or embedding invocation;
* experiments of any kind;
* any Hive mutation or lifecycle promotion.

There is deliberately no ``allowed``/``approved``/``permission``/``risk``/
``budget``/``verified`` field anywhere in this module: authority is owned by
the Trusted Kernel, never by a learning artefact.

Trust boundary
--------------

Future external content stays untrusted:

    untrusted content -> extraction -> claim extraction -> provenance ->
    candidate knowledge -> verification

An objective may express *preferences* over provenance channels
(:class:`~agentx.core.knowledge.ProvenanceKind`) and over acceptable
lifecycle statuses for a future answer. Those are constraints on what would
be acceptable to consider, never proof of truth and never a promise that
anything found will be verified. A preferred channel is a preference only.

Free text (``question``, ``objective_id``) is inert. It is validated for shape
only and is never parsed, interpreted, executed, or used to widen scope,
authority, risk, budget, or trust. Strings such as ``"research approved"``,
``"permission=WRITE"``, or ``"mark verified"`` remain ordinary characters.

Scope and provenance reuse the canonical C2.02/C2.07 contracts; requirement
linkage reuses the canonical A4.01 contracts. Nothing is duplicated here.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.cognition.gap_detector import (
    KnowledgeGapAssessment,
    KnowledgeGapAssessmentStatus,
    KnowledgeGapRequirement,
)
from agentx.core.knowledge import (
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
)

__all__ = [
    "CURRENT_RESEARCH_OBJECTIVE_SCHEMA_VERSION",
    "ResearchObjective",
    "ResearchObjectiveValidationError",
    "UnsupportedResearchObjectiveSchemaVersionError",
    "research_objective_from_gap",
]

CURRENT_RESEARCH_OBJECTIVE_SCHEMA_VERSION: Final[int] = 1

_MAX_OBJECTIVE_ID_LENGTH: Final[int] = 128
_MAX_QUESTION_LENGTH: Final[int] = 4096
_MAX_UNMET_REQUIREMENT_IDS: Final[int] = 64

_OBJECTIVE_FIELDS: Final = frozenset(
    {
        "schema_version",
        "objective_id",
        "question",
        "unmet_requirement_ids",
        "scope",
        "preferred_provenance_kinds",
        "acceptable_statuses",
        "knowledge_types",
    }
)


class ResearchObjectiveValidationError(ValueError):
    """Raised when an A4.02 research objective violates the typed contract."""


class UnsupportedResearchObjectiveSchemaVersionError(ResearchObjectiveValidationError):
    """Raised when encoded objective data uses an unreadable schema version."""


def _validate_inert_text(value: object, *, field_name: str, max_length: int) -> str:
    """Validate free text for shape only; the text itself is never interpreted."""

    if not isinstance(value, str):
        raise ResearchObjectiveValidationError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise ResearchObjectiveValidationError(f"{field_name} must be non-empty and trimmed")
    if any(character in value for character in ("\x00", "\r")):
        raise ResearchObjectiveValidationError(
            f"{field_name} must not contain NUL or carriage-return characters"
        )
    if len(value) > max_length:
        raise ResearchObjectiveValidationError(
            f"{field_name} must not exceed {max_length} characters"
        )
    return value


def _validate_requirement_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError("unmet_requirement_ids must be a tuple")
    if len(value) > _MAX_UNMET_REQUIREMENT_IDS:
        raise ResearchObjectiveValidationError(
            f"unmet_requirement_ids must not exceed {_MAX_UNMET_REQUIREMENT_IDS} entries"
        )
    validated = [
        _validate_inert_text(
            item,
            field_name="unmet_requirement_ids entry",
            max_length=_MAX_OBJECTIVE_ID_LENGTH,
        )
        for item in value
    ]
    if len(set(validated)) != len(validated):
        raise ResearchObjectiveValidationError("unmet_requirement_ids must not contain duplicates")
    return tuple(sorted(validated))


def _validate_optional_member_set(
    value: object,
    *,
    member_type: type[StrEnum],
    field_name: str,
) -> None:
    if value is None:
        return
    if not isinstance(value, frozenset):
        raise TypeError(f"{field_name} must be a frozenset or None")
    if not value:
        raise ResearchObjectiveValidationError(f"{field_name} must not be empty")
    for item in value:
        if not isinstance(item, member_type):
            raise ResearchObjectiveValidationError(
                f"{field_name} entries must be {member_type.__name__} values"
            )


def _sorted_values(members: frozenset[object] | None) -> list[str] | None:
    if members is None:
        return None
    return sorted(str(member) for member in members)


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchObjective:
    """One immutable, inert description of missing knowledge.

    Fields:

    ``objective_id``
        Caller-supplied stable identity. Inert text.
    ``question``
        What is missing / what must be resolved. Inert text: never parsed,
        never executed, never a source of authority.
    ``unmet_requirement_ids``
        Optional explicit linkage to the ``requirement_id`` values of A4.01
        :class:`KnowledgeGapRequirement` objects that were not satisfied.
        Stored canonically sorted; identifiers only, so no A4.01 contract is
        duplicated or reinterpreted.
    ``scope``
        Optional canonical :class:`KnowledgeScope` (C2.02) describing the
        environment the missing knowledge applies to. Reused verbatim.
    ``preferred_provenance_kinds``
        Optional *preference* over canonical :class:`ProvenanceKind` channels.
        A channel preference is never evidence of truth and never grants
        access to that channel.
    ``acceptable_statuses``
        Optional statement of which canonical :class:`KnowledgeStatus` values
        a future answer would have to already hold to be considered
        acceptable. It cannot promote anything to those statuses.
    ``knowledge_types``
        Optional canonical :class:`KnowledgeType` constraint.

    The objective is data only: it has no permission, risk, budget, provider,
    endpoint, query, ranking, callback, or execution field, and it exposes no
    method that performs I/O.
    """

    objective_id: str
    question: str
    unmet_requirement_ids: tuple[str, ...] = ()
    scope: KnowledgeScope | None = None
    preferred_provenance_kinds: frozenset[ProvenanceKind] | None = None
    acceptable_statuses: frozenset[KnowledgeStatus] | None = None
    knowledge_types: frozenset[KnowledgeType] | None = None
    schema_version: int = CURRENT_RESEARCH_OBJECTIVE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "objective_id",
            _validate_inert_text(
                self.objective_id,
                field_name="objective_id",
                max_length=_MAX_OBJECTIVE_ID_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "question",
            _validate_inert_text(
                self.question,
                field_name="question",
                max_length=_MAX_QUESTION_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "unmet_requirement_ids",
            _validate_requirement_ids(self.unmet_requirement_ids),
        )
        if self.scope is not None and not isinstance(self.scope, KnowledgeScope):
            raise TypeError("scope must be a KnowledgeScope or None")
        _validate_optional_member_set(
            self.preferred_provenance_kinds,
            member_type=ProvenanceKind,
            field_name="preferred_provenance_kinds",
        )
        _validate_optional_member_set(
            self.acceptable_statuses,
            member_type=KnowledgeStatus,
            field_name="acceptable_statuses",
        )
        _validate_optional_member_set(
            self.knowledge_types,
            member_type=KnowledgeType,
            field_name="knowledge_types",
        )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise ResearchObjectiveValidationError("schema_version must be an integer")
        if self.schema_version != CURRENT_RESEARCH_OBJECTIVE_SCHEMA_VERSION:
            raise UnsupportedResearchObjectiveSchemaVersionError(
                f"unsupported research objective schema version {self.schema_version}; "
                f"supported version is {CURRENT_RESEARCH_OBJECTIVE_SCHEMA_VERSION}"
            )

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic JSON-compatible representation."""

        return {
            "schema_version": self.schema_version,
            "objective_id": self.objective_id,
            "question": self.question,
            "unmet_requirement_ids": list(self.unmet_requirement_ids),
            "scope": None if self.scope is None else self.scope.to_dict(),
            "preferred_provenance_kinds": _sorted_values(self.preferred_provenance_kinds),
            "acceptable_statuses": _sorted_values(self.acceptable_statuses),
            "knowledge_types": _sorted_values(self.knowledge_types),
        }

    def to_json(self) -> str:
        """Serialize deterministically; ordering never depends on caller input."""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ResearchObjective:
        """Validate and rebuild an objective from canonical data, fail-closed."""

        if not isinstance(raw, Mapping):
            raise ResearchObjectiveValidationError("objective must be a JSON object")
        if "schema_version" not in raw:
            raise ResearchObjectiveValidationError(
                "objective missing required field: schema_version"
            )
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise ResearchObjectiveValidationError("schema_version must be an integer")
        if version != CURRENT_RESEARCH_OBJECTIVE_SCHEMA_VERSION:
            raise UnsupportedResearchObjectiveSchemaVersionError(
                f"unsupported research objective schema version {version}; "
                f"supported version is {CURRENT_RESEARCH_OBJECTIVE_SCHEMA_VERSION}"
            )

        actual = set(raw)
        if actual != _OBJECTIVE_FIELDS:
            missing = _OBJECTIVE_FIELDS - actual
            unknown = actual - _OBJECTIVE_FIELDS
            if missing:
                raise ResearchObjectiveValidationError(
                    f"objective missing required fields: {sorted(missing)}"
                )
            raise ResearchObjectiveValidationError(
                f"objective contains unknown fields: {sorted(unknown)}"
            )

        requirement_ids_raw = raw["unmet_requirement_ids"]
        if not isinstance(requirement_ids_raw, list):
            raise ResearchObjectiveValidationError("unmet_requirement_ids must be a JSON array")
        requirement_ids: list[str] = []
        for item in requirement_ids_raw:
            if not isinstance(item, str):
                raise ResearchObjectiveValidationError(
                    "unmet_requirement_ids entries must be strings"
                )
            requirement_ids.append(item)

        scope_raw = raw["scope"]
        scope: KnowledgeScope | None = None
        if scope_raw is not None:
            if not isinstance(scope_raw, Mapping):
                raise ResearchObjectiveValidationError("scope must be a JSON object or null")
            scope = KnowledgeScope.from_dict(scope_raw)

        objective_id = raw["objective_id"]
        question = raw["question"]
        if not isinstance(objective_id, str) or not isinstance(question, str):
            raise ResearchObjectiveValidationError("objective_id and question must be strings")

        return cls(
            objective_id=objective_id,
            question=question,
            unmet_requirement_ids=tuple(requirement_ids),
            scope=scope,
            preferred_provenance_kinds=_decode_provenance_kinds(raw["preferred_provenance_kinds"]),
            acceptable_statuses=_decode_statuses(raw["acceptable_statuses"]),
            knowledge_types=_decode_knowledge_types(raw["knowledge_types"]),
            schema_version=version,
        )

    @classmethod
    def from_json(cls, raw: str) -> ResearchObjective:
        """Deserialize JSON text without dynamic import or arbitrary construction."""

        if not isinstance(raw, str):
            raise ResearchObjectiveValidationError("objective JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ResearchObjectiveValidationError("objective JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise ResearchObjectiveValidationError("objective JSON root must be an object")
        copied: dict[str, object] = {}
        for key, item in decoded.items():
            if not isinstance(key, str):
                raise ResearchObjectiveValidationError(
                    "objective JSON contains a non-string object key"
                )
            copied[key] = item
        return cls.from_dict(copied)


def _decode_member_strings(value: object, *, field_name: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ResearchObjectiveValidationError(f"{field_name} must be a JSON array or null")
    if not value:
        raise ResearchObjectiveValidationError(f"{field_name} must not be empty")
    entries: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ResearchObjectiveValidationError(f"{field_name} entries must be strings")
        entries.append(item)
    return tuple(entries)


def _decode_provenance_kinds(value: object) -> frozenset[ProvenanceKind] | None:
    entries = _decode_member_strings(value, field_name="preferred_provenance_kinds")
    if entries is None:
        return None
    members: set[ProvenanceKind] = set()
    for item in entries:
        try:
            members.add(ProvenanceKind(item))
        except ValueError as exc:
            raise ResearchObjectiveValidationError(
                f"unknown preferred_provenance_kinds entry: {item!r}"
            ) from exc
    return frozenset(members)


def _decode_statuses(value: object) -> frozenset[KnowledgeStatus] | None:
    entries = _decode_member_strings(value, field_name="acceptable_statuses")
    if entries is None:
        return None
    statuses: set[KnowledgeStatus] = set()
    for item in entries:
        try:
            statuses.add(KnowledgeStatus(item))
        except ValueError as exc:
            raise ResearchObjectiveValidationError(
                f"unknown acceptable_statuses entry: {item!r}"
            ) from exc
    return frozenset(statuses)


def _decode_knowledge_types(value: object) -> frozenset[KnowledgeType] | None:
    entries = _decode_member_strings(value, field_name="knowledge_types")
    if entries is None:
        return None
    types: set[KnowledgeType] = set()
    for item in entries:
        try:
            types.add(KnowledgeType(item))
        except ValueError as exc:
            raise ResearchObjectiveValidationError(
                f"unknown knowledge_types entry: {item!r}"
            ) from exc
    return frozenset(types)


def research_objective_from_gap(
    assessment: KnowledgeGapAssessment,
    *,
    objective_id: str,
    question: str,
    requirement_ids: Iterable[str] | None = None,
    scope: KnowledgeScope | None = None,
    preferred_provenance_kinds: frozenset[ProvenanceKind] | None = None,
    acceptable_statuses: frozenset[KnowledgeStatus] | None = None,
    knowledge_types: frozenset[KnowledgeType] | None = None,
) -> ResearchObjective:
    """Build an objective from an explicit A4.01 ``GAP`` assessment.

    This is pure construction and validation. It performs no retrieval, no
    research, no provider selection, no I/O, no persistence, no model call,
    and no lifecycle transition; it only reads already-computed A4.01 data.

    Fail-closed rules:

    * a ``SUFFICIENT`` assessment is rejected — it is never silently rewritten
      as a gap;
    * ``requirement_ids``, when supplied, must be a subset of the assessment's
      genuinely unmet ``requirement_id`` values; unknown identifiers are
      rejected rather than accepted as new work;
    * when omitted, every unmet requirement of the assessment is linked.
    """

    if not isinstance(assessment, KnowledgeGapAssessment):
        raise TypeError("assessment must be a KnowledgeGapAssessment")
    if assessment.status is not KnowledgeGapAssessmentStatus.GAP:
        raise ResearchObjectiveValidationError(
            "a research objective may only be derived from a GAP assessment; "
            f"got {assessment.status.value!r}"
        )

    unmet: tuple[KnowledgeGapRequirement, ...] = assessment.unmet_requirements
    unmet_ids = {requirement.requirement_id for requirement in unmet}

    if requirement_ids is None:
        selected = tuple(sorted(unmet_ids))
    else:
        if isinstance(requirement_ids, str):
            raise TypeError("requirement_ids must be an iterable of strings, not a string")
        selected_list = list(requirement_ids)
        for item in selected_list:
            if not isinstance(item, str):
                raise TypeError("requirement_ids entries must be strings")
        if not selected_list:
            raise ResearchObjectiveValidationError(
                "requirement_ids must not be empty when supplied"
            )
        unknown = sorted(set(selected_list) - unmet_ids)
        if unknown:
            raise ResearchObjectiveValidationError(
                f"requirement_ids must reference unmet A4.01 requirements; unknown: {unknown}"
            )
        selected = tuple(sorted(set(selected_list)))

    return ResearchObjective(
        objective_id=objective_id,
        question=question,
        unmet_requirement_ids=selected,
        scope=scope,
        preferred_provenance_kinds=preferred_provenance_kinds,
        acceptable_statuses=acceptable_statuses,
        knowledge_types=knowledge_types,
    )
