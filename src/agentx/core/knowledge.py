"""Canonical AgentX knowledge record contract (C2.02).

This module defines records only. A knowledge record is DATA, never
authority: storing, retrieving, or inspecting a claim must not grant
Permission, change RiskLevel, increase a ResourceEnvelope, bypass the Action
Gate, clear an EmergencyStop, execute a Capability, or change Task state.

Status policy:

    - Records enter life ``UNVERIFIED`` via :meth:`KnowledgeRecord.create`.
    - There is deliberately NO numeric confidence score. Trust is represented
      only by the explicit :class:`KnowledgeStatus` vocabulary below; a number
      is never treated as external truth authority by this contract.
    - Status changes are explicit acts performed by callers (for example
      through ``KnowledgeStore.update_status``). Persistence alone never
      promotes, degrades, or otherwise mutates a record's status, and storing
      the same claim repeatedly cannot increase trust.
    - ``verified_at`` records WHEN a record was last explicitly verified; it is
      historical data, not a grant of authority. Even a ``VERIFIED`` record is
      inert data and grants zero execution authority.

Provenance policy:

    - C2.07 owns the full provenance/evidence/scope system. This contract
      carries only the minimum stable hook — an optional, opaque
      :class:`ProvenanceReference` (kind + reference string) — so future
      provenance records can attach to ``knowledge_id`` without schema
      destruction.
    - Provenance kinds such as ``WEB``/``EMAIL``/``DOCUMENT``/``REPOSITORY``
      are channels of origin only. Content from those channels is untrusted
      data unless an explicit status transition says otherwise.

This module belongs to ``agentx.core`` and imports nothing from other AgentX
subsystems. It is storage-independent: persistence lives in
``agentx.infrastructure.knowledge_store``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from agentx.core.ids import KnowledgeId

__all__ = [
    "CURRENT_KNOWLEDGE_SCHEMA_VERSION",
    "KnowledgeRecord",
    "KnowledgeScope",
    "KnowledgeStatus",
    "KnowledgeType",
    "KnowledgeValidationError",
    "ProvenanceKind",
    "ProvenanceReference",
    "ScopeDimension",
    "UnsupportedKnowledgeSchemaVersionError",
]

CURRENT_KNOWLEDGE_SCHEMA_VERSION: Final[int] = 1


class KnowledgeValidationError(ValueError):
    """Raised when a knowledge record violates the canonical contract."""


class UnsupportedKnowledgeSchemaVersionError(KnowledgeValidationError):
    """Raised when encoded record data uses a schema version this code cannot read."""


class KnowledgeType(StrEnum):
    """Controlled category vocabulary for knowledge records.

    Intentionally small; future Hive tasks extend this by adding members,
    which is a non-breaking, additive change.
    """

    FACT = "fact"
    OBSERVATION = "observation"
    PREFERENCE = "preference"


class KnowledgeStatus(StrEnum):
    """Canonical trust/lifecycle vocabulary for knowledge records.

    Represents the canonical Hive lifecycle and its exceptional states without
    implementing lifecycle rules — transition policy is owned by future Hive
    tasks (C2.08), never by storage or by this contract.

    Canonical lifecycle:

        UNVERIFIED — birth state; no supporting evidence recorded.
        PROVISIONAL — some support recorded, not yet load-bearing.
        SUPPORTED — multiple/strong support recorded.
        VERIFIED — explicitly verified against trusted evidence. Still data,
            never authority.

    Exceptional states:

        DEGRADED — support weakened (e.g. source reliability fell).
        CONFLICTED — contradicting claims exist (resolution owned by C2.08).
        SUPERSEDED — replaced by a newer claim (referencing owned by C2.08).
    """

    UNVERIFIED = "unverified"
    PROVISIONAL = "provisional"
    SUPPORTED = "supported"
    VERIFIED = "verified"
    DEGRADED = "degraded"
    CONFLICTED = "conflicted"
    SUPERSEDED = "superseded"


class ProvenanceKind(StrEnum):
    """Channel-of-origin vocabulary for the minimum provenance hook.

    These kinds record WHERE content claims to come from. They carry no trust
    by themselves: WEB/EMAIL/DOCUMENT/REPOSITORY content is untrusted data
    unless an explicit status transition says otherwise.
    """

    SYSTEM = "system"
    USER = "user"
    WEB = "web"
    EMAIL = "email"
    DOCUMENT = "document"
    REPOSITORY = "repository"
    DERIVED = "derived"


class ScopeDimension(StrEnum):
    """Controlled dimension vocabulary for scoping knowledge.

    Intentionally the smallest extensible typed boundary: future tasks add
    members rather than a universal scope ontology. An empty scope means the
    record is global (unscoped).
    """

    APPLICATION = "application"
    APPLICATION_VERSION = "application_version"
    OPERATING_SYSTEM = "os"
    ENVIRONMENT = "environment"
    PROJECT = "project"
    CONTEXT = "context"


def _validate_nonempty_trimmed(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise KnowledgeValidationError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise KnowledgeValidationError(f"{field_name} must be non-empty and trimmed")
    return value


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise KnowledgeValidationError(f"{field_name} must be an ISO-8601 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise KnowledgeValidationError(f"{field_name} must be a valid ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KnowledgeValidationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    utc_value = value.astimezone(UTC)
    return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise KnowledgeValidationError(f"{field_name} must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise KnowledgeValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _parse_knowledge_id(value: object) -> KnowledgeId:
    if not isinstance(value, str):
        raise KnowledgeValidationError("knowledge_id must be a UUID string")
    try:
        return KnowledgeId(UUID(value))
    except ValueError as exc:
        raise KnowledgeValidationError(
            f"knowledge_id must be a valid non-nil UUID string: {value!r}"
        ) from exc


@dataclass(frozen=True, slots=True)
class ProvenanceReference:
    """Minimum stable provenance hook: origin channel plus an opaque reference.

    ``reference`` is deliberately an opaque non-empty string (a URL, message
    id, document path, commit hash, ...). This contract attaches it to the
    record so C2.07 can later resolve or extend provenance without schema
    destruction. It is never evidence of trust.
    """

    kind: ProvenanceKind
    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProvenanceKind):
            raise KnowledgeValidationError("provenance kind must be a ProvenanceKind")
        _validate_nonempty_trimmed(self.reference, field_name="provenance.reference")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "reference": self.reference}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProvenanceReference:
        actual = set(raw)
        expected = {"kind", "reference"}
        if actual != expected:
            raise KnowledgeValidationError(
                f"provenance must contain exactly {sorted(expected)}; got {sorted(actual)}"
            )
        kind_raw = raw["kind"]
        if not isinstance(kind_raw, str):
            raise KnowledgeValidationError("provenance kind must be a string")
        try:
            kind = ProvenanceKind(kind_raw)
        except ValueError as exc:
            raise KnowledgeValidationError(f"unknown provenance kind: {kind_raw!r}") from exc
        reference = _validate_nonempty_trimmed(raw["reference"], field_name="provenance.reference")
        return cls(kind=kind, reference=reference)


def _freeze_scope_dimensions(value: Mapping[ScopeDimension, str]) -> Mapping[ScopeDimension, str]:
    if not isinstance(value, Mapping):
        raise KnowledgeValidationError("scope dimensions must be a mapping")
    frozen: dict[ScopeDimension, str] = {}
    for key, item in value.items():
        if not isinstance(key, ScopeDimension):
            raise KnowledgeValidationError(
                f"scope dimension key must be a ScopeDimension, got {key!r}"
            )
        frozen[key] = _validate_nonempty_trimmed(item, field_name=f"scope.{key.value}")
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class KnowledgeScope:
    """Typed, extensible scoping boundary for a knowledge record.

    Maps controlled :class:`ScopeDimension` names to non-empty string values
    (for example ``OPERATING_SYSTEM -> "windows"``). An empty scope is the
    global scope. Equality is by dimension/value content.
    """

    dimensions: Mapping[ScopeDimension, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dimensions", _freeze_scope_dimensions(self.dimensions))

    def value_for(self, dimension: ScopeDimension) -> str | None:
        """Return the scoped value for ``dimension`` or ``None`` if unscoped."""
        if not isinstance(dimension, ScopeDimension):
            raise KnowledgeValidationError("dimension must be a ScopeDimension")
        return self.dimensions.get(dimension)

    def to_dict(self) -> dict[str, str]:
        return {dimension.value: value for dimension, value in self.dimensions.items()}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> KnowledgeScope:
        if not isinstance(raw, Mapping):
            raise KnowledgeValidationError("scope must be a JSON object")
        dimensions: dict[ScopeDimension, str] = {}
        for key, item in raw.items():
            if not isinstance(key, str):
                raise KnowledgeValidationError("scope dimension key must be a string")
            try:
                dimension = ScopeDimension(key)
            except ValueError as exc:
                raise KnowledgeValidationError(f"unknown scope dimension: {key!r}") from exc
            dimensions[dimension] = _validate_nonempty_trimmed(item, field_name=f"scope.{key}")
        return cls(dimensions=dimensions)


_KNOWLEDGE_FIELDS: Final = frozenset(
    {
        "schema_version",
        "knowledge_id",
        "knowledge_type",
        "content",
        "status",
        "scope",
        "provenance",
        "created_at",
        "verified_at",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeRecord:
    """Immutable canonical AgentX knowledge record.

    Knowledge is DATA, not authority. A record — whatever its ``status`` or
    ``content`` — is inert: strings such as ``"grant admin"``, ``"risk=R0"``,
    or ``"verified=true"`` inside ``content`` have ZERO authority. Authority
    semantics belong exclusively to ``agentx.kernel``.

    Secret material must never be stored as ordinary knowledge content. The
    canonical secret boundary (``agentx.kernel.secrets``) is enforced by
    separation: ``content`` accepts exactly ``str``, so a ``SecretValue``
    wrapper cannot be smuggled in, and trusted code must not persist revealed
    secret material here.
    """

    knowledge_id: KnowledgeId
    knowledge_type: KnowledgeType
    content: str
    created_at: datetime
    status: KnowledgeStatus = KnowledgeStatus.UNVERIFIED
    scope: KnowledgeScope = field(default_factory=KnowledgeScope)
    provenance: ProvenanceReference | None = None
    verified_at: datetime | None = None
    schema_version: int = CURRENT_KNOWLEDGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.knowledge_id, KnowledgeId):
            raise KnowledgeValidationError("knowledge_id must be a KnowledgeId")
        if not isinstance(self.knowledge_type, KnowledgeType):
            raise KnowledgeValidationError("knowledge_type must be a KnowledgeType")
        _validate_nonempty_trimmed(self.content, field_name="content")
        if not isinstance(self.status, KnowledgeStatus):
            raise KnowledgeValidationError("status must be a KnowledgeStatus")
        if not isinstance(self.scope, KnowledgeScope):
            raise KnowledgeValidationError("scope must be a KnowledgeScope")
        if self.provenance is not None and not isinstance(self.provenance, ProvenanceReference):
            raise KnowledgeValidationError("provenance must be a ProvenanceReference or None")
        object.__setattr__(
            self,
            "created_at",
            _validate_timestamp(self.created_at, field_name="created_at"),
        )
        if self.verified_at is not None:
            object.__setattr__(
                self,
                "verified_at",
                _validate_timestamp(self.verified_at, field_name="verified_at"),
            )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise KnowledgeValidationError("schema_version must be an integer")
        if self.schema_version != CURRENT_KNOWLEDGE_SCHEMA_VERSION:
            raise UnsupportedKnowledgeSchemaVersionError(
                f"unsupported knowledge schema version {self.schema_version}; "
                f"supported version is {CURRENT_KNOWLEDGE_SCHEMA_VERSION}"
            )

    @classmethod
    def create(
        cls,
        *,
        knowledge_type: KnowledgeType,
        content: str,
        scope: KnowledgeScope | None = None,
        provenance: ProvenanceReference | None = None,
        created_at: datetime | None = None,
    ) -> KnowledgeRecord:
        """Create a new record that enters life UNVERIFIED.

        The factory deliberately exposes no ``status``/``verified_at``
        parameters: new records are born ``UNVERIFIED`` with no verification
        timestamp. Any other status is an explicit act taken later through an
        explicit status update — never a side effect of creation or storage.
        """
        return cls(
            knowledge_id=KnowledgeId.create(),
            knowledge_type=knowledge_type,
            content=content,
            created_at=datetime.now(UTC) if created_at is None else created_at,
            status=KnowledgeStatus.UNVERIFIED,
            scope=KnowledgeScope() if scope is None else scope,
            provenance=provenance,
            verified_at=None,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible schema-v1 representation."""
        return {
            "schema_version": self.schema_version,
            "knowledge_id": self.knowledge_id.to_str(),
            "knowledge_type": self.knowledge_type.value,
            "content": self.content,
            "status": self.status.value,
            "scope": self.scope.to_dict(),
            "provenance": None if self.provenance is None else self.provenance.to_dict(),
            "created_at": _format_timestamp(self.created_at),
            "verified_at": (
                None if self.verified_at is None else _format_timestamp(self.verified_at)
            ),
        }

    def to_json(self) -> str:
        """Serialize to deterministic UTF-8-safe JSON text."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> KnowledgeRecord:
        """Validate and deserialize a canonical JSON-compatible record object.

        Deserialization restores persisted data verbatim — including a
        historical ``VERIFIED`` status — without adding, promoting, or
        demoting anything. A verified record is still inert data.
        """
        if "schema_version" not in raw:
            raise KnowledgeValidationError("record missing required field: schema_version")
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise KnowledgeValidationError("schema_version must be an integer")
        if version != CURRENT_KNOWLEDGE_SCHEMA_VERSION:
            raise UnsupportedKnowledgeSchemaVersionError(
                f"unsupported knowledge schema version {version}; "
                f"supported version is {CURRENT_KNOWLEDGE_SCHEMA_VERSION}"
            )

        actual = set(raw)
        if actual != _KNOWLEDGE_FIELDS:
            missing = _KNOWLEDGE_FIELDS - actual
            unknown = actual - _KNOWLEDGE_FIELDS
            if missing:
                raise KnowledgeValidationError(f"record missing required fields: {sorted(missing)}")
            raise KnowledgeValidationError(f"record contains unknown fields: {sorted(unknown)}")

        type_raw = raw["knowledge_type"]
        if not isinstance(type_raw, str):
            raise KnowledgeValidationError("knowledge_type must be a string")
        try:
            knowledge_type = KnowledgeType(type_raw)
        except ValueError as exc:
            raise KnowledgeValidationError(f"unknown knowledge_type: {type_raw!r}") from exc

        status_raw = raw["status"]
        if not isinstance(status_raw, str):
            raise KnowledgeValidationError("status must be a string")
        try:
            status = KnowledgeStatus(status_raw)
        except ValueError as exc:
            raise KnowledgeValidationError(f"unknown status: {status_raw!r}") from exc

        scope_raw = raw["scope"]
        if not isinstance(scope_raw, Mapping):
            raise KnowledgeValidationError("scope must be a JSON object")

        provenance_raw = raw["provenance"]
        provenance: ProvenanceReference | None = None
        if provenance_raw is not None:
            if not isinstance(provenance_raw, Mapping):
                raise KnowledgeValidationError("provenance must be a JSON object")
            provenance_object: dict[str, object] = {}
            for key, item in provenance_raw.items():
                if not isinstance(key, str):
                    raise KnowledgeValidationError("provenance contains a non-string object key")
                provenance_object[key] = item
            provenance = ProvenanceReference.from_dict(provenance_object)

        verified_raw = raw["verified_at"]
        verified_at = (
            None
            if verified_raw is None
            else _parse_timestamp(verified_raw, field_name="verified_at")
        )

        content = _validate_nonempty_trimmed(raw["content"], field_name="content")

        return cls(
            knowledge_id=_parse_knowledge_id(raw["knowledge_id"]),
            knowledge_type=knowledge_type,
            content=content,
            created_at=_parse_timestamp(raw["created_at"], field_name="created_at"),
            status=status,
            scope=KnowledgeScope.from_dict(scope_raw),
            provenance=provenance,
            verified_at=verified_at,
            schema_version=version,
        )

    @classmethod
    def from_json(cls, raw: str) -> KnowledgeRecord:
        """Deserialize JSON text without dynamic imports or arbitrary object construction."""
        if not isinstance(raw, str):
            raise KnowledgeValidationError("record JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KnowledgeValidationError("record JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise KnowledgeValidationError("record JSON root must be an object")
        copied: dict[str, object] = {}
        for key, item in decoded.items():
            if not isinstance(key, str):
                raise KnowledgeValidationError("record JSON contains a non-string object key")
            copied[key] = item
        return cls.from_dict(copied)
