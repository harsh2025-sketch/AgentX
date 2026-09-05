"""Canonical knowledge-integrity contracts for AgentX (C2.08).

Knowledge lifecycle state, contradiction relationships, and supersession
relationships are DATA, never authority. None of these contracts grants
Permission, creates AuthorityContext, bypasses ActionGate, lowers risk,
enlarges budgets, clears stop signals, executes capabilities, mutates tasks,
or decides whether a claim is true.

C2.08 deliberately reuses the canonical C2.02 ``KnowledgeStatus`` and
``KnowledgeId`` contracts. C2.07 provenance/evidence may motivate an explicit
lifecycle operation in higher-level trusted code, but evidence presence,
provenance kind, wording, source count, timestamps, or model output never
perform a transition here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import KnowledgeStatus, KnowledgeValidationError

__all__ = [
    "IllegalKnowledgeStatusTransitionError",
    "KnowledgeContradiction",
    "KnowledgeRelationshipValidationError",
    "KnowledgeStatusTransitionKind",
    "KnowledgeSupersession",
    "SelfContradictionError",
    "SelfSupersessionError",
    "validate_knowledge_status_transition",
]


class IllegalKnowledgeStatusTransitionError(KnowledgeValidationError):
    """Raised when a requested lifecycle transition is not canonical."""


class KnowledgeRelationshipValidationError(KnowledgeValidationError):
    """Raised when a contradiction/supersession relationship is malformed."""


class SelfContradictionError(KnowledgeRelationshipValidationError):
    """Raised when one record is declared contradictory with itself."""


class SelfSupersessionError(KnowledgeRelationshipValidationError):
    """Raised when one record is declared to supersede itself."""


class KnowledgeStatusTransitionKind(StrEnum):
    """Integrity context for a status change; never an authorization token."""

    EXPLICIT = "explicit"
    CONTRADICTION = "contradiction"
    SUPERSESSION = "supersession"


# Explicit lifecycle changes intentionally allow externally-authorized callers
# to advance more than one support stage at a time. C2.08 validates state
# integrity; it does not decide whether the evidence behind an explicit request
# is good enough. Exceptional states are relationship-bound and therefore are
# excluded from this table.
_EXPLICIT_TRANSITIONS: Final[dict[KnowledgeStatus, frozenset[KnowledgeStatus]]] = {
    KnowledgeStatus.UNVERIFIED: frozenset(
        {
            KnowledgeStatus.PROVISIONAL,
            KnowledgeStatus.SUPPORTED,
            KnowledgeStatus.VERIFIED,
        }
    ),
    KnowledgeStatus.PROVISIONAL: frozenset(
        {
            KnowledgeStatus.SUPPORTED,
            KnowledgeStatus.VERIFIED,
            KnowledgeStatus.DEGRADED,
        }
    ),
    KnowledgeStatus.SUPPORTED: frozenset(
        {
            KnowledgeStatus.VERIFIED,
            KnowledgeStatus.DEGRADED,
        }
    ),
    KnowledgeStatus.VERIFIED: frozenset({KnowledgeStatus.DEGRADED}),
    KnowledgeStatus.DEGRADED: frozenset(
        {
            KnowledgeStatus.PROVISIONAL,
            KnowledgeStatus.SUPPORTED,
            KnowledgeStatus.VERIFIED,
        }
    ),
    # Leaving CONFLICTED is an explicit resolution decision made outside this
    # contract. The conservative canonical re-entry point is DEGRADED so prior
    # support is never silently restored.
    KnowledgeStatus.CONFLICTED: frozenset({KnowledgeStatus.DEGRADED}),
    KnowledgeStatus.SUPERSEDED: frozenset(),
}


def validate_knowledge_status_transition(
    current: KnowledgeStatus,
    target: KnowledgeStatus,
    *,
    kind: KnowledgeStatusTransitionKind = KnowledgeStatusTransitionKind.EXPLICIT,
) -> None:
    """Validate one deterministic, fail-closed knowledge-status transition.

    Same-state requests are canonical no-ops. ``CONFLICTED`` may otherwise be
    entered only by an explicit contradiction operation, and ``SUPERSEDED``
    only by an explicit supersession operation. Relationship operations do not
    select truth or grant authority; they merely satisfy the integrity
    precondition for those exceptional states.
    """
    if not isinstance(current, KnowledgeStatus):
        raise KnowledgeValidationError("current status must be a KnowledgeStatus")
    if not isinstance(target, KnowledgeStatus):
        raise KnowledgeValidationError("target status must be a KnowledgeStatus")
    if not isinstance(kind, KnowledgeStatusTransitionKind):
        raise KnowledgeValidationError("transition kind must be a KnowledgeStatusTransitionKind")

    if current is target:
        return

    if kind is KnowledgeStatusTransitionKind.EXPLICIT and target in _EXPLICIT_TRANSITIONS[current]:
        return
    if (
        kind is KnowledgeStatusTransitionKind.CONTRADICTION
        and current is not KnowledgeStatus.SUPERSEDED
        and target is KnowledgeStatus.CONFLICTED
    ):
        return
    if (
        kind is KnowledgeStatusTransitionKind.SUPERSESSION
        and current is not KnowledgeStatus.SUPERSEDED
        and target is KnowledgeStatus.SUPERSEDED
    ):
        return

    raise IllegalKnowledgeStatusTransitionError(
        f"illegal knowledge status transition: {current.value} -> {target.value} ({kind.value})"
    )


def _require_knowledge_id(value: object, *, field_name: str) -> KnowledgeId:
    if not isinstance(value, KnowledgeId):
        raise KnowledgeRelationshipValidationError(f"{field_name} must be a KnowledgeId")
    return value


def _parse_knowledge_id(value: object, *, field_name: str) -> KnowledgeId:
    if not isinstance(value, str):
        raise KnowledgeRelationshipValidationError(f"{field_name} must be a UUID string")
    try:
        return KnowledgeId.parse(value)
    except ValueError as exc:
        raise KnowledgeRelationshipValidationError(
            f"{field_name} must be a valid non-nil UUID string"
        ) from exc


def _validate_exact_fields(
    raw: Mapping[str, object], *, expected: frozenset[str], field_name: str
) -> None:
    actual = set(raw)
    if actual == expected:
        return
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise KnowledgeRelationshipValidationError(
            f"{field_name} missing required fields: {sorted(missing)}"
        )
    raise KnowledgeRelationshipValidationError(
        f"{field_name} contains unknown fields: {sorted(unknown)}"
    )


def _decode_json_object(raw: str, *, field_name: str) -> dict[str, object]:
    if not isinstance(raw, str):
        raise KnowledgeRelationshipValidationError(f"{field_name} JSON must be a string")
    try:
        decoded: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KnowledgeRelationshipValidationError(f"{field_name} JSON is malformed") from exc
    if not isinstance(decoded, Mapping):
        raise KnowledgeRelationshipValidationError(f"{field_name} JSON root must be an object")
    copied: dict[str, object] = {}
    for key, value in decoded.items():
        if not isinstance(key, str):
            raise KnowledgeRelationshipValidationError(
                f"{field_name} JSON has a non-string object key"
            )
        copied[key] = value
    return copied


_CONTRADICTION_FIELDS: Final = frozenset({"first_knowledge_id", "second_knowledge_id"})
_SUPERSESSION_FIELDS: Final = frozenset({"replacement_knowledge_id", "superseded_knowledge_id"})


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeContradiction:
    """Symmetric contradiction between two distinct knowledge records.

    Order is canonicalized lexicographically by UUID string, so ``A,B`` and
    ``B,A`` are the same relationship. The relationship states only that the
    claims are mutually incompatible; it never chooses a winner or changes
    either record by itself.
    """

    first_knowledge_id: KnowledgeId
    second_knowledge_id: KnowledgeId

    def __post_init__(self) -> None:
        first = _require_knowledge_id(self.first_knowledge_id, field_name="first_knowledge_id")
        second = _require_knowledge_id(self.second_knowledge_id, field_name="second_knowledge_id")
        if first == second:
            raise SelfContradictionError("a knowledge record cannot contradict itself")
        if first.to_str() > second.to_str():
            object.__setattr__(self, "first_knowledge_id", second)
            object.__setattr__(self, "second_knowledge_id", first)

    def to_dict(self) -> dict[str, str]:
        return {
            "first_knowledge_id": self.first_knowledge_id.to_str(),
            "second_knowledge_id": self.second_knowledge_id.to_str(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> KnowledgeContradiction:
        _validate_exact_fields(raw, expected=_CONTRADICTION_FIELDS, field_name="contradiction")
        return cls(
            first_knowledge_id=_parse_knowledge_id(
                raw["first_knowledge_id"], field_name="first_knowledge_id"
            ),
            second_knowledge_id=_parse_knowledge_id(
                raw["second_knowledge_id"], field_name="second_knowledge_id"
            ),
        )

    @classmethod
    def from_json(cls, raw: str) -> KnowledgeContradiction:
        return cls.from_dict(_decode_json_object(raw, field_name="contradiction"))


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeSupersession:
    """Directional replacement relationship: replacement supersedes historical record."""

    replacement_knowledge_id: KnowledgeId
    superseded_knowledge_id: KnowledgeId

    def __post_init__(self) -> None:
        replacement = _require_knowledge_id(
            self.replacement_knowledge_id, field_name="replacement_knowledge_id"
        )
        superseded = _require_knowledge_id(
            self.superseded_knowledge_id, field_name="superseded_knowledge_id"
        )
        if replacement == superseded:
            raise SelfSupersessionError("a knowledge record cannot supersede itself")

    def to_dict(self) -> dict[str, str]:
        return {
            "replacement_knowledge_id": self.replacement_knowledge_id.to_str(),
            "superseded_knowledge_id": self.superseded_knowledge_id.to_str(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> KnowledgeSupersession:
        _validate_exact_fields(raw, expected=_SUPERSESSION_FIELDS, field_name="supersession")
        return cls(
            replacement_knowledge_id=_parse_knowledge_id(
                raw["replacement_knowledge_id"], field_name="replacement_knowledge_id"
            ),
            superseded_knowledge_id=_parse_knowledge_id(
                raw["superseded_knowledge_id"], field_name="superseded_knowledge_id"
            ),
        )

    @classmethod
    def from_json(cls, raw: str) -> KnowledgeSupersession:
        return cls.from_dict(_decode_json_object(raw, field_name="supersession"))
