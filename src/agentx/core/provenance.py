"""Canonical provenance and evidence contracts for AgentX knowledge (C2.07).

External and remembered content is DATA, never authority. This module records
where knowledge came from and what data supports it. It does not grant
Permission, authorize actions, classify risk, enlarge budgets, clear stop
signals, execute capabilities, mutate tasks, publish events, promote knowledge
status, retrieve content, or decide whether evidence is true.

C2.02 deliberately placed a minimal ``ProvenanceReference`` and ``KnowledgeId``
hook on ``KnowledgeRecord``. C2.07 extends that design by composition rather
than changing the persisted C2.02 record schema: detailed provenance and
evidence records attach to a canonical ``KnowledgeId``. Existing C2.02 rows
therefore remain byte-for-byte compatible and require no persistence migration.

``KnowledgeScope`` and ``ScopeDimension`` remain owned by
``agentx.core.knowledge``. Their existing dimensions already cover the current
architecture's explicit applicability needs (environment, OS, application,
application version, project, and context). Scope is applicability data only;
an empty/unscoped scope never means unrestricted execution authority.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeValidationError,
    ProvenanceReference,
    _format_timestamp,
    _parse_timestamp,
    _validate_nonempty_trimmed,
    _validate_timestamp,
)

__all__ = [
    "EvidenceKind",
    "EvidenceReference",
    "KnowledgeEvidence",
    "ProvenanceRecord",
]

_PROVENANCE_FIELDS: Final = frozenset(
    {"knowledge_id", "source", "observed_at", "locator", "derived_from"}
)
_EVIDENCE_REFERENCE_FIELDS: Final = frozenset({"kind", "reference", "provenance", "observed_at"})
_KNOWLEDGE_EVIDENCE_FIELDS: Final = frozenset({"knowledge_id", "references"})


class EvidenceKind(StrEnum):
    """Controlled vocabulary for the smallest useful evidence-reference kinds."""

    OBSERVATION = "observation"
    ARTIFACT = "artifact"
    KNOWLEDGE_RECORD = "knowledge_record"


def _parse_knowledge_id(value: object, *, field_name: str) -> KnowledgeId:
    if not isinstance(value, str):
        raise KnowledgeValidationError(f"{field_name} must be a UUID string")
    try:
        return KnowledgeId.parse(value)
    except ValueError as exc:
        raise KnowledgeValidationError(f"{field_name} must be a valid non-nil UUID string") from exc


def _validate_optional_reference(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _validate_nonempty_trimmed(value, field_name=field_name)


def _validate_exact_fields(
    raw: Mapping[str, object],
    *,
    expected: frozenset[str],
    field_name: str,
) -> None:
    actual = set(raw)
    if actual == expected:
        return
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise KnowledgeValidationError(f"{field_name} missing required fields: {sorted(missing)}")
    raise KnowledgeValidationError(f"{field_name} contains unknown fields: {sorted(unknown)}")


def _parse_provenance_reference(value: object, *, field_name: str) -> ProvenanceReference:
    if not isinstance(value, Mapping):
        raise KnowledgeValidationError(f"{field_name} must be a JSON object")
    copied: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise KnowledgeValidationError(f"{field_name} contains a non-string object key")
        copied[key] = item
    return ProvenanceReference.from_dict(copied)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProvenanceRecord:
    """Detailed, non-authoritative origin data attached to one knowledge record.

    ``source`` provides the controlled source kind plus stable opaque identity
    already defined by C2.02. ``locator`` is optional inert location text such
    as a URL, path, message locator, or repository location; this module never
    dereferences it. ``observed_at`` records when the source was acquired or
    observed when that time is known. ``derived_from`` records zero or more
    opaque upstream provenance references and performs no truth inference.
    """

    knowledge_id: KnowledgeId
    source: ProvenanceReference
    observed_at: datetime | None = None
    locator: str | None = None
    derived_from: tuple[ProvenanceReference, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.knowledge_id, KnowledgeId):
            raise KnowledgeValidationError("knowledge_id must be a KnowledgeId")
        if not isinstance(self.source, ProvenanceReference):
            raise KnowledgeValidationError("source must be a ProvenanceReference")
        if self.observed_at is not None:
            object.__setattr__(
                self,
                "observed_at",
                _validate_timestamp(self.observed_at, field_name="observed_at"),
            )
        if self.locator is not None:
            _validate_nonempty_trimmed(self.locator, field_name="locator")
        if not isinstance(self.derived_from, tuple):
            raise KnowledgeValidationError("derived_from must be a tuple")
        for reference in self.derived_from:
            if not isinstance(reference, ProvenanceReference):
                raise KnowledgeValidationError(
                    "derived_from entries must be ProvenanceReference values"
                )

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible representation."""
        return {
            "knowledge_id": self.knowledge_id.to_str(),
            "source": self.source.to_dict(),
            "observed_at": (
                None if self.observed_at is None else _format_timestamp(self.observed_at)
            ),
            "locator": self.locator,
            "derived_from": [reference.to_dict() for reference in self.derived_from],
        }

    def to_json(self) -> str:
        """Serialize without executing or dereferencing any referenced source."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProvenanceRecord:
        """Strictly reconstruct provenance data without interpreting its content."""
        _validate_exact_fields(raw, expected=_PROVENANCE_FIELDS, field_name="provenance record")

        observed_raw = raw["observed_at"]
        observed_at = (
            None
            if observed_raw is None
            else _parse_timestamp(observed_raw, field_name="observed_at")
        )
        locator = _validate_optional_reference(raw["locator"], field_name="locator")

        derived_raw = raw["derived_from"]
        if not isinstance(derived_raw, list):
            raise KnowledgeValidationError("derived_from must be a JSON array")
        derived: list[ProvenanceReference] = []
        for index, item in enumerate(derived_raw):
            derived.append(_parse_provenance_reference(item, field_name=f"derived_from[{index}]"))

        return cls(
            knowledge_id=_parse_knowledge_id(raw["knowledge_id"], field_name="knowledge_id"),
            source=_parse_provenance_reference(raw["source"], field_name="source"),
            observed_at=observed_at,
            locator=locator,
            derived_from=tuple(derived),
        )

    @classmethod
    def from_json(cls, raw: str) -> ProvenanceRecord:
        """Deserialize deterministic JSON text as inert provenance data."""
        if not isinstance(raw, str):
            raise KnowledgeValidationError("provenance JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KnowledgeValidationError("provenance JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise KnowledgeValidationError("provenance JSON root must be an object")
        copied: dict[str, object] = {}
        for key, item in decoded.items():
            if not isinstance(key, str):
                raise KnowledgeValidationError("provenance JSON has a non-string object key")
            copied[key] = item
        return cls.from_dict(copied)


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceReference:
    """One typed, provenance-aware support reference for a knowledge claim.

    ``reference`` is an opaque identifier for an observation, artifact, or
    knowledge record. It is not fetched, opened, executed, verified, ranked, or
    trusted by this contract. ``provenance`` identifies where the supporting
    item claims to originate. ``observed_at`` is optional historical data.
    """

    kind: EvidenceKind
    reference: str
    provenance: ProvenanceReference
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EvidenceKind):
            raise KnowledgeValidationError("evidence kind must be an EvidenceKind")
        _validate_nonempty_trimmed(self.reference, field_name="evidence.reference")
        if not isinstance(self.provenance, ProvenanceReference):
            raise KnowledgeValidationError("evidence provenance must be a ProvenanceReference")
        if self.observed_at is not None:
            object.__setattr__(
                self,
                "observed_at",
                _validate_timestamp(self.observed_at, field_name="evidence.observed_at"),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "reference": self.reference,
            "provenance": self.provenance.to_dict(),
            "observed_at": (
                None if self.observed_at is None else _format_timestamp(self.observed_at)
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EvidenceReference:
        _validate_exact_fields(
            raw,
            expected=_EVIDENCE_REFERENCE_FIELDS,
            field_name="evidence reference",
        )
        kind_raw = raw["kind"]
        if not isinstance(kind_raw, str):
            raise KnowledgeValidationError("evidence kind must be a string")
        try:
            kind = EvidenceKind(kind_raw)
        except ValueError as exc:
            raise KnowledgeValidationError(f"unknown evidence kind: {kind_raw!r}") from exc

        observed_raw = raw["observed_at"]
        observed_at = (
            None
            if observed_raw is None
            else _parse_timestamp(observed_raw, field_name="evidence.observed_at")
        )
        return cls(
            kind=kind,
            reference=_validate_nonempty_trimmed(raw["reference"], field_name="evidence.reference"),
            provenance=_parse_provenance_reference(
                raw["provenance"], field_name="evidence.provenance"
            ),
            observed_at=observed_at,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeEvidence:
    """Immutable association of one or more evidence references with KnowledgeId.

    Presence, quantity, wording, or provenance of evidence has no status or
    authority effect. In particular, constructing this object never changes a
    ``KnowledgeRecord`` from ``UNVERIFIED`` and never creates permission for a
    future action. C2.08 owns any later explicit lifecycle policy.
    """

    knowledge_id: KnowledgeId
    references: tuple[EvidenceReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.knowledge_id, KnowledgeId):
            raise KnowledgeValidationError("knowledge_id must be a KnowledgeId")
        if not isinstance(self.references, tuple):
            raise KnowledgeValidationError("evidence references must be a tuple")
        if not self.references:
            raise KnowledgeValidationError("evidence references must not be empty")
        for reference in self.references:
            if not isinstance(reference, EvidenceReference):
                raise KnowledgeValidationError(
                    "evidence references must contain only EvidenceReference values"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "knowledge_id": self.knowledge_id.to_str(),
            "references": [reference.to_dict() for reference in self.references],
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
    def from_dict(cls, raw: Mapping[str, object]) -> KnowledgeEvidence:
        _validate_exact_fields(
            raw,
            expected=_KNOWLEDGE_EVIDENCE_FIELDS,
            field_name="knowledge evidence",
        )
        references_raw = raw["references"]
        if not isinstance(references_raw, list):
            raise KnowledgeValidationError("evidence references must be a JSON array")
        references: list[EvidenceReference] = []
        for index, item in enumerate(references_raw):
            if not isinstance(item, Mapping):
                raise KnowledgeValidationError(
                    f"evidence references[{index}] must be a JSON object"
                )
            copied: dict[str, object] = {}
            for key, value in item.items():
                if not isinstance(key, str):
                    raise KnowledgeValidationError(
                        f"evidence references[{index}] has a non-string object key"
                    )
                copied[key] = value
            references.append(EvidenceReference.from_dict(copied))

        return cls(
            knowledge_id=_parse_knowledge_id(raw["knowledge_id"], field_name="knowledge_id"),
            references=tuple(references),
        )

    @classmethod
    def from_json(cls, raw: str) -> KnowledgeEvidence:
        if not isinstance(raw, str):
            raise KnowledgeValidationError("evidence JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KnowledgeValidationError("evidence JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise KnowledgeValidationError("evidence JSON root must be an object")
        copied: dict[str, object] = {}
        for key, item in decoded.items():
            if not isinstance(key, str):
                raise KnowledgeValidationError("evidence JSON has a non-string object key")
            copied[key] = item
        return cls.from_dict(copied)
