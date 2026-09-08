"""Bounded, inert evidence assembly for one task/correlation attempt (M2.05).

The caller selects evidence; this module only validates and packages it. It
performs no retrieval, relevance ranking, prompt construction, model call, I/O,
or authority decision. Canonical records, their statuses, and their provenance
remain DATA, including historical VERIFIED/ACTIVE/SUCCEEDED values.

Order is caller order. Duplicate *known identities* are refused, never merged
or silently removed. Causal experiences, environment observations, and user
payloads have no canonical record ID: without an explicit record-level evidence
reference their identity is unknown, not a hash of their content/correlation.

See docs/context_assembly.md for the wire contract and exact bound accounting.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.environment_change import (
    EnvironmentFactKey,
    EnvironmentFactKind,
    EnvironmentFactValue,
    EnvironmentFactValueKind,
    EnvironmentObservation,
    EnvironmentSnapshot,
)
from agentx.core.episodes import EpisodeOutcome, EpisodeRecord
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.core.ids import EpisodeId, KnowledgeId, NegativeExperienceId, ProcedureId, TaskId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
    ProvenanceReference,
    ScopeDimension,
)
from agentx.core.negative_experience import (
    AttemptKind,
    AttemptReference,
    FailureReference,
    NegativeExperienceRecord,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.core.provenance import EvidenceKind, EvidenceReference

CONTEXT_SCHEMA_VERSION: Final[int] = 1
MAX_CONTEXT_ITEMS: Final[int] = 64
MAX_CONTEXT_BYTES: Final[int] = 262_144
MAX_CONTEXT_ITEM_BYTES: Final[int] = 16_384
MAX_CONTEXT_METADATA_ENTRIES: Final[int] = 16
MAX_CONTEXT_METADATA_BYTES: Final[int] = 4_096
MAX_CONTEXT_DETAIL_DEPTH: Final[int] = 12
MAX_CONTEXT_DETAIL_NODES: Final[int] = 4_096
MAX_CONTEXT_INTEGER_BITS: Final[int] = 1_024


class ContextValidationError(ValueError):
    """Malformed, non-canonical, or unsafe context data; nothing was assembled."""


class ContextLimitError(ContextValidationError):
    """A hard ceiling or a caller's tighter assembly limit was exceeded."""


class ContextBindingError(ContextValidationError):
    """The envelope does not belong to the expected task/correlation attempt."""


class ContextDuplicateError(ContextValidationError):
    """A known source-record identity occurs more than once, even if identical."""


class ContextScopeError(ContextValidationError):
    """Explicit canonical scope dimensions carry contradictory values."""


class UnsupportedContextSchemaVersionError(ContextValidationError):
    """The context wire version is not supported."""


class ContextItemKind(StrEnum):
    """Closed evidence classes, each backed by an existing inward record type."""

    KNOWLEDGE = "knowledge"
    EPISODE = "episode"
    NEGATIVE_EXPERIENCE = "negative_experience"
    CAUSAL_EXPERIENCE = "causal_experience"
    PROCEDURE_REFERENCE = "procedure_reference"
    ENVIRONMENT_FACT = "environment_fact"
    USER_SUPPLIED_CONTEXT = "user_supplied_context"


type ContextRecord = (
    KnowledgeRecord
    | EpisodeRecord
    | NegativeExperienceRecord
    | CausalExperience
    | ProcedureRecord
    | EnvironmentSnapshot
    | ObservationPayload
)

_LIMIT_CEILINGS: Final = {
    "max_items": MAX_CONTEXT_ITEMS,
    "max_total_bytes": MAX_CONTEXT_BYTES,
    "max_item_bytes": MAX_CONTEXT_ITEM_BYTES,
    "max_metadata_entries": MAX_CONTEXT_METADATA_ENTRIES,
    "max_metadata_bytes": MAX_CONTEXT_METADATA_BYTES,
}
_CONTEXT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "task_id",
        "correlation_id",
        "created_at",
        "scope",
        "items",
        "metadata",
        "limits",
    }
)
_ITEM_FIELDS: Final = frozenset({"kind", "record", "record_reference"})
_RECORD_KINDS: Final = (
    (KnowledgeRecord, ContextItemKind.KNOWLEDGE),
    (EpisodeRecord, ContextItemKind.EPISODE),
    (NegativeExperienceRecord, ContextItemKind.NEGATIVE_EXPERIENCE),
    (CausalExperience, ContextItemKind.CAUSAL_EXPERIENCE),
    (ProcedureRecord, ContextItemKind.PROCEDURE_REFERENCE),
    (EnvironmentSnapshot, ContextItemKind.ENVIRONMENT_FACT),
    (ObservationPayload, ContextItemKind.USER_SUPPLIED_CONTEXT),
)
# Closed allowlists, not subclass/protocol acceptance or dynamic object hooks.
_CANONICAL_RECORD_TYPES: Final = (
    *(record_type for record_type, _ in _RECORD_KINDS),
    ExperienceState,
    ActionPayload,
    VerificationPayload,
    AttemptReference,
    FailureReference,
    KnowledgeScope,
    ProvenanceReference,
    ProcedureScope,
    ProcedurePayload,
    EnvironmentObservation,
    EnvironmentFactKey,
    EnvironmentFactValue,
    EvidenceReference,
)
_CANONICAL_ENUM_TYPES: Final = (
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
    ScopeDimension,
    EpisodeOutcome,
    AttemptKind,
    CausalOutcome,
    ProcedureStatus,
    ProcedurePayloadKind,
    ProcedureScopeDimension,
    EnvironmentFactKind,
    EnvironmentFactValueKind,
    EvidenceKind,
)
_ID_TYPES: Final = (TaskId, KnowledgeId, EpisodeId, NegativeExperienceId, ProcedureId)
# Only these five dimensions have an explicit counterpart in both vocabularies.
_PROCEDURE_DIMENSIONS: Final = (
    (ProcedureScopeDimension.APPLICATION, ScopeDimension.APPLICATION),
    (ProcedureScopeDimension.APPLICATION_VERSION, ScopeDimension.APPLICATION_VERSION),
    (ProcedureScopeDimension.OPERATING_SYSTEM, ScopeDimension.OPERATING_SYSTEM),
    (ProcedureScopeDimension.ENVIRONMENT, ScopeDimension.ENVIRONMENT),
    (ProcedureScopeDimension.PROJECT, ScopeDimension.PROJECT),
)


def _object(value: object) -> dict[str, object]:
    # Public raw data must be built-ins, not arbitrary Mapping implementations
    # (including proxies around caller-defined mappings with executable hooks).
    if type(value) is not dict:
        raise ContextValidationError("expected a plain string-keyed JSON object")
    if len(value) > MAX_CONTEXT_DETAIL_NODES:
        raise ContextLimitError("JSON object member limit exceeded")
    if any(type(key) is not str for key in value):
        raise ContextValidationError("expected a plain string-keyed JSON object")
    return value


def _exact(raw: Mapping[str, object], expected: frozenset[str]) -> None:
    if set(raw) != expected:
        raise ContextValidationError("missing or unknown fields in context data")


def _json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ContextValidationError("context data is not serializable JSON") from exc


def _utf8_size(text: str, maximum: int) -> int:
    # Bound the allocation before UTF-8 encoding, including multi-byte text.
    if len(text) > maximum:
        raise ContextLimitError("serialized byte limit exceeded")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError as exc:
        raise ContextValidationError("context text must be valid UTF-8") from exc
    if size > maximum:
        raise ContextLimitError("serialized byte limit exceeded")
    return size


def _encoded_size(value: object, maximum: int) -> int:
    return _utf8_size(_json(value), maximum)


@dataclass(frozen=True, slots=True, kw_only=True)
class ContextLimits:
    """Assembly limits, never execution budgets; values may only tighten ceilings.

    Zero items or metadata entries is allowed. Byte limits must be positive.
    Serialized limits cannot override a receiving caller's tighter limits.
    """

    max_items: int = MAX_CONTEXT_ITEMS
    max_total_bytes: int = MAX_CONTEXT_BYTES
    max_item_bytes: int = MAX_CONTEXT_ITEM_BYTES
    max_metadata_entries: int = MAX_CONTEXT_METADATA_ENTRIES
    max_metadata_bytes: int = MAX_CONTEXT_METADATA_BYTES

    def __post_init__(self) -> None:
        for name, ceiling in _LIMIT_CEILINGS.items():
            value = getattr(self, name)
            if type(value) is not int:
                raise ContextValidationError("context limits must be exact integers")
            minimum = 0 if name in {"max_items", "max_metadata_entries"} else 1
            if not minimum <= value <= ceiling:
                raise ContextLimitError(f"{name} must be between {minimum} and {ceiling}")

    def to_dict(self) -> dict[str, int]:
        """Return the exact, non-authoritative limit declaration."""
        return {name: getattr(self, name) for name in _LIMIT_CEILINGS}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ContextLimits:
        """Reject unknown fields and non-integer/raised/unlimited limits."""
        data = _object(raw)
        _exact(data, frozenset(_LIMIT_CEILINGS))
        values: dict[str, int] = {}
        for name, value in data.items():
            if type(value) is not int:
                raise ContextValidationError("context limits must be exact integers")
            values[name] = value
        return cls(**values)

    def _within(self, ceiling: ContextLimits) -> None:
        for name, value in self.to_dict().items():
            if value > getattr(ceiling, name):
                raise ContextLimitError(f"serialized {name} exceeds the receiving limit")


DEFAULT_CONTEXT_LIMITS: Final = ContextLimits()


@dataclass(slots=True)
class _Inspection:
    """Bound work before any canonical serializer or recursive copy is called."""

    max_bytes: int
    max_nodes: int = MAX_CONTEXT_DETAIL_NODES
    max_depth: int = MAX_CONTEXT_DETAIL_DEPTH
    max_metadata_entries: int | None = None
    nodes: int = 0
    scalar_bytes: int = 0
    metadata_entries: int = 0

    def scalar(self, value: object) -> None:
        if type(value) is int and value.bit_length() > MAX_CONTEXT_INTEGER_BITS:
            raise ContextLimitError("JSON integer bit limit exceeded")
        if type(value) is float and not math.isfinite(value):
            raise ContextValidationError("non-finite JSON numbers are forbidden")
        if type(value) is str and len(value) > self.max_bytes:
            raise ContextLimitError("detail byte limit exceeded")
        self.scalar_bytes += _utf8_size(_json(value), self.max_bytes)
        if self.scalar_bytes > self.max_bytes:
            raise ContextLimitError("detail byte limit exceeded")

    def visit(self, value: object, *, canonical: bool = False, depth: int = 0) -> None:
        self.nodes += 1
        if self.nodes > self.max_nodes or depth > self.max_depth:
            raise ContextLimitError("detail node or depth limit exceeded (cycles are forbidden)")
        value_type = type(value)
        if value is None or any(
            value_type is scalar_type for scalar_type in (bool, int, float, str)
        ):
            self.scalar(value)
        elif any(value_type is container_type for container_type in (dict, list, tuple)) or (
            canonical and value_type is MappingProxyType
        ):
            # A canonical record's proxy was created by its own defensive
            # constructor. Public JSON/metadata never accepts arbitrary proxies.
            if not isinstance(value, Mapping | list | tuple):  # pragma: no cover
                raise AssertionError("validated container type")
            if len(value) > self.max_nodes - self.nodes:
                raise ContextLimitError("detail node limit exceeded")
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if type(key) is str:
                        self.scalar(key)
                    elif canonical and (
                        type(key) is ScopeDimension or type(key) is ProcedureScopeDimension
                    ):
                        self.scalar(key.value)
                    else:
                        raise ContextValidationError("JSON keys must be plain strings")
                    self.metadata_entries += 1
                    if (
                        self.max_metadata_entries is not None
                        and self.metadata_entries > self.max_metadata_entries
                    ):
                        raise ContextLimitError("metadata entry limit exceeded")
                    self.visit(item, canonical=canonical, depth=depth + 1)
            else:
                for item in value:
                    self.visit(item, canonical=canonical, depth=depth + 1)
        elif canonical and any(value_type is allowed for allowed in _CANONICAL_RECORD_TYPES):
            # No asdict/deepcopy, arbitrary dataclasses, protocols, or subclasses.
            for definition in fields(value):  # type: ignore[arg-type]
                self.visit(getattr(value, definition.name), canonical=True, depth=depth + 1)
        elif canonical and any(value_type is allowed for allowed in _CANONICAL_ENUM_TYPES):
            if not isinstance(value, StrEnum):  # pragma: no cover
                raise AssertionError("validated enum type")
            self.scalar(value.value)
        elif canonical and any(value_type is allowed for allowed in _ID_TYPES):
            if not isinstance(
                value, TaskId | KnowledgeId | EpisodeId | NegativeExperienceId | ProcedureId
            ):
                raise AssertionError("validated identity type")  # pragma: no cover
            _uuid(value.value)
            self.scalar(value.to_str())
        elif canonical and value_type is UUID:
            _uuid(value)
            self.scalar(str(value))
        elif canonical and type(value) is datetime:
            if value.tzinfo is not UTC:
                raise ContextValidationError("canonical record timestamps must already be UTC")
            self.scalar(_timestamp(value))
        elif canonical and type(value) is timedelta:
            self.scalar(value // timedelta(microseconds=1))
        else:
            raise ContextValidationError(
                "only exact canonical types and plain JSON data are allowed"
            )


def _thaw(value: object) -> object:
    """Copy only previously inspected or privately frozen data; never deepcopy."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_thaw(item) for item in value]
    return value


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _bounded_object(
    value: object,
    *,
    maximum: int = MAX_CONTEXT_ITEM_BYTES,
    max_nodes: int = MAX_CONTEXT_DETAIL_NODES,
    max_depth: int = MAX_CONTEXT_DETAIL_DEPTH,
    metadata_entries: int | None = None,
) -> dict[str, object]:
    raw = _object(value)
    _Inspection(maximum, max_nodes, max_depth, metadata_entries).visit(raw)
    copied = _object(_thaw(raw))
    _encoded_size(copied, maximum)
    return copied


def _uuid(value: object) -> UUID:
    if type(value) is not UUID or value.int == 0:
        raise ContextValidationError("correlation/event/ID values must be exact non-nil UUIDs")
    return value


def _task_id(value: object) -> TaskId:
    if type(value) is not TaskId:
        raise ContextValidationError("task_id must be an exact canonical TaskId")
    _uuid(value.value)
    return value


def _time(value: object) -> datetime:
    if type(value) is not datetime or not (
        type(value.tzinfo) is timezone or type(value.tzinfo) is ZoneInfo
    ):
        raise ContextValidationError(
            "created_at must be an aware datetime with a standard timezone"
        )
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _kind(record: ContextRecord) -> ContextItemKind:
    for record_type, kind in _RECORD_KINDS:
        if type(record) is record_type:
            return kind
    raise ContextValidationError("record must be an exact supported canonical record type")


def _decode_record(kind: ContextItemKind, raw: dict[str, object]) -> ContextRecord:
    try:
        match kind:
            case ContextItemKind.KNOWLEDGE:
                return KnowledgeRecord.from_dict(raw)
            case ContextItemKind.EPISODE:
                return EpisodeRecord.from_dict(raw)
            case ContextItemKind.NEGATIVE_EXPERIENCE:
                return NegativeExperienceRecord.from_dict(raw)
            case ContextItemKind.CAUSAL_EXPERIENCE:
                return CausalExperience.from_dict(raw)
            case ContextItemKind.PROCEDURE_REFERENCE:
                return ProcedureRecord.from_dict(raw)
            case ContextItemKind.ENVIRONMENT_FACT:
                return EnvironmentSnapshot.from_dict(raw)
            case ContextItemKind.USER_SUPPLIED_CONTEXT:
                return ObservationPayload.from_dict(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ContextValidationError(f"invalid canonical {kind.value} record") from exc
    raise ContextValidationError("unknown context item kind")  # pragma: no cover


def _same_fields(raw: object, canonical: object) -> None:
    """Require even optional canonical wire fields explicitly; never fill gaps silently."""
    if isinstance(canonical, dict):
        data = _object(raw)
        _exact(data, frozenset(canonical))
        for key, value in canonical.items():
            _same_fields(data[key], value)
    elif isinstance(canonical, list):
        if type(raw) is not list or len(raw) != len(canonical):
            raise ContextValidationError("canonical arrays must retain all entries")
        for item, value in zip(raw, canonical, strict=True):
            _same_fields(item, value)


def _reference(raw: object) -> EvidenceReference | None:
    if raw is None:
        return None
    try:
        return EvidenceReference.from_dict(_object(raw))
    except (TypeError, ValueError) as exc:
        raise ContextValidationError("invalid canonical record_reference") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class ContextItem:
    """One selected canonical record, independently snapshotted and bounded.

    ``kind`` is derived from the exact record type, never from its text. An
    ENVIRONMENT_FACT contains a canonical EnvironmentSnapshot with exactly one
    observation, retaining its scope and evidence. USER_SUPPLIED_CONTEXT reuses
    the canonical inert ObservationPayload, without inventing user identity.

    Optional ``record_reference`` names this exact evidence record, not its
    containing document or a relevance label. It is never resolved and does not
    replace embedded provenance. Absence remains None. For records with no
    canonical ID it is the only available deduplication identity.
    """

    record: ContextRecord
    record_reference: EvidenceReference | None = None
    kind: ContextItemKind = field(init=False)

    def __post_init__(self) -> None:
        kind = _kind(self.record)
        if (
            self.record_reference is not None
            and type(self.record_reference) is not EvidenceReference
        ):
            raise ContextValidationError(
                "record_reference must be an exact EvidenceReference or None"
            )
        inspection = _Inspection(MAX_CONTEXT_ITEM_BYTES)
        inspection.visit(self.record, canonical=True)
        inspection.visit(self.record_reference, canonical=True)
        # Only closed, preflighted canonical serializers are ever invoked.
        raw = _bounded_object(self.record.to_dict())
        record = _decode_record(kind, raw)
        reference = _reference(
            None
            if self.record_reference is None
            else _bounded_object(self.record_reference.to_dict())
        )
        if isinstance(record, EnvironmentSnapshot) and len(record.observations) != 1:
            raise ContextValidationError(
                "an environment fact item must contain exactly one observation"
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "record", record)
        object.__setattr__(self, "record_reference", reference)
        # The complete wire item has the same structural limits as decoding;
        # no constructor-valid item may fail its own round trip at the edge.
        _bounded_object(self.to_dict())

    @property
    def identity(self) -> tuple[str, ...] | None:
        """Known, kind-namespaced record identity; None means genuinely unknown."""
        record = self.record
        if isinstance(record, KnowledgeRecord):
            return (self.kind.value, record.knowledge_id.to_str())
        if isinstance(record, EpisodeRecord):
            return (self.kind.value, record.episode_id.to_str())
        if isinstance(record, NegativeExperienceRecord):
            return (self.kind.value, record.negative_experience_id.to_str())
        if isinstance(record, ProcedureRecord):
            return (self.kind.value, record.procedure_id.to_str(), str(record.revision))
        reference = self.record_reference
        if reference is None:
            return None
        return (
            self.kind.value,
            reference.kind.value,
            reference.provenance.kind.value,
            reference.provenance.reference,
            reference.reference,
        )

    @property
    def scope(self) -> KnowledgeScope | ProcedureScope | None:
        """Original typed applicability scope, not an inferred or merged scope."""
        if isinstance(
            self.record,
            KnowledgeRecord | NegativeExperienceRecord | EnvironmentSnapshot | ProcedureRecord,
        ):
            return self.record.scope
        return None

    def to_dict(self) -> dict[str, object]:
        """Return a detached canonical record representation, including all provenance."""
        return {
            "kind": self.kind.value,
            "record": self.record.to_dict(),
            "record_reference": None
            if self.record_reference is None
            else self.record_reference.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ContextItem:
        """Decode one bounded exact-field item with a closed canonical decoder."""
        data = _bounded_object(raw)
        _exact(data, _ITEM_FIELDS)
        kind_raw = data["kind"]
        if type(kind_raw) is not str:
            raise ContextValidationError("context item kind must be a string")
        try:
            kind = ContextItemKind(kind_raw)
        except (TypeError, ValueError) as exc:
            raise ContextValidationError("unknown context item kind") from exc
        record_raw = _object(data["record"])
        record = _decode_record(kind, record_raw)
        _same_fields(record_raw, record.to_dict())
        return cls(record=record, record_reference=_reference(data["record_reference"]))


def _scope_bindings(scope: KnowledgeScope | ProcedureScope) -> dict[ScopeDimension, str]:
    if isinstance(scope, KnowledgeScope):
        return dict(scope.dimensions)
    return {
        target: scope.dimensions[source]
        for source, target in _PROCEDURE_DIMENSIONS
        if source in scope.dimensions
    }


def _scope(raw: object) -> KnowledgeScope:
    try:
        return KnowledgeScope.from_dict(_object(raw))
    except (TypeError, ValueError) as exc:
        raise ContextValidationError("invalid canonical context scope") from exc


def _schema_version(value: object) -> None:
    if type(value) is not int:
        raise ContextValidationError("schema_version must be an exact integer")
    if value != CONTEXT_SCHEMA_VERSION:
        raise UnsupportedContextSchemaVersionError("unsupported context schema version")


def _limits(value: object) -> ContextLimits:
    if type(value) is not ContextLimits:
        raise ContextValidationError("limits must be exact ContextLimits")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentContext:
    """Immutable, task-bound evidence for exactly one orchestration attempt.

    No Task object or lifecycle is owned here. Historical record task and
    correlation IDs are provenance, not a demand that history belong to the
    current task. ``require_binding`` must be checked by consumers; decoding
    always requires the receiving task/correlation identities explicitly.
    """

    task_id: TaskId
    correlation_id: UUID
    created_at: datetime
    items: tuple[ContextItem, ...] = ()
    scope: KnowledgeScope = field(default_factory=KnowledgeScope)
    metadata: Mapping[str, object] = field(default_factory=dict)
    limits: ContextLimits = DEFAULT_CONTEXT_LIMITS
    schema_version: int = CONTEXT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _schema_version(self.schema_version)
        _task_id(self.task_id)
        _uuid(self.correlation_id)
        limits = _limits(self.limits)
        object.__setattr__(self, "created_at", _time(self.created_at))
        if type(self.items) is not tuple:
            raise ContextValidationError("items must be a caller-ordered tuple")
        if len(self.items) > limits.max_items:
            raise ContextLimitError("context item count limit exceeded")
        if type(self.scope) is not KnowledgeScope:
            raise ContextValidationError("scope must be an exact KnowledgeScope")
        _Inspection(MAX_CONTEXT_ITEM_BYTES).visit(self.scope, canonical=True)
        object.__setattr__(self, "scope", _scope(_bounded_object(self.scope.to_dict())))
        metadata = _bounded_object(
            self.metadata,
            maximum=limits.max_metadata_bytes,
            metadata_entries=limits.max_metadata_entries,
        )
        object.__setattr__(self, "metadata", _freeze(metadata))
        seen: set[tuple[str, ...]] = set()
        bindings = _scope_bindings(self.scope)
        for item in self.items:
            if type(item) is not ContextItem:
                raise ContextValidationError("items must contain only exact ContextItem values")
            _encoded_size(item.to_dict(), limits.max_item_bytes)
            identity = item.identity
            if identity is not None:
                if identity in seen:
                    raise ContextDuplicateError("duplicate context source-record identity")
                seen.add(identity)
            if item.scope is not None:
                for dimension, value in _scope_bindings(item.scope).items():
                    if dimension in bindings and bindings[dimension] != value:
                        raise ContextScopeError(
                            f"contradictory scope binding for {dimension.value}"
                        )
                    bindings[dimension] = value
        _encoded_size(self.to_dict(), limits.max_total_bytes)

    def require_binding(self, *, task_id: TaskId, correlation_id: UUID) -> None:
        """Refuse reuse for any different task or attempt; never implicitly rebind."""
        if _task_id(task_id) != self.task_id or _uuid(correlation_id) != self.correlation_id:
            raise ContextBindingError("context belongs to a different task/correlation attempt")

    def to_dict(self) -> dict[str, object]:
        """Return detached exact-field JSON data, not messages or a prompt."""
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id.to_str(),
            "correlation_id": str(self.correlation_id),
            "created_at": _timestamp(self.created_at),
            "scope": self.scope.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "metadata": _thaw(self.metadata),
            "limits": self.limits.to_dict(),
        }

    def to_json(self) -> str:
        """Deterministic compact JSON; byte accounting uses this exact UTF-8 form."""
        return _json(self.to_dict())

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, object],
        *,
        expected_task_id: TaskId,
        expected_correlation_id: UUID,
        limits: ContextLimits = DEFAULT_CONTEXT_LIMITS,
    ) -> AgentContext:
        """Decode with independent receiver binding and ceiling; refuse evidence loss."""
        _task_id(expected_task_id)
        _uuid(expected_correlation_id)
        _limits(limits)
        root = _object(raw)
        items_raw = root.get("items")
        if type(items_raw) is list and len(items_raw) > limits.max_items:
            raise ContextLimitError("context item count limit exceeded")
        data = _bounded_object(
            root,
            maximum=limits.max_total_bytes,
            max_nodes=MAX_CONTEXT_DETAIL_NODES * (MAX_CONTEXT_ITEMS + 1),
            max_depth=MAX_CONTEXT_DETAIL_DEPTH + 4,
        )
        _exact(data, _CONTEXT_FIELDS)
        _schema_version(data["schema_version"])
        declared_limits = ContextLimits.from_dict(_object(data["limits"]))
        declared_limits._within(limits)
        try:
            task_raw, correlation_raw, time_raw = (
                data["task_id"],
                data["correlation_id"],
                data["created_at"],
            )
            if (
                type(task_raw) is not str
                or type(correlation_raw) is not str
                or type(time_raw) is not str
            ):
                raise ContextValidationError("serialized identities/timestamps must be strings")
            task_id = TaskId.parse(task_raw)
            correlation_id = _uuid(UUID(correlation_raw))
            created_at = _time(datetime.fromisoformat(time_raw))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ContextValidationError("invalid context identity or timestamp") from exc
        if task_id != expected_task_id or correlation_id != expected_correlation_id:
            raise ContextBindingError("context belongs to a different task/correlation attempt")
        items = data["items"]
        if type(items) is not list:
            raise ContextValidationError("items must be a JSON array")
        if len(items) > declared_limits.max_items:
            raise ContextLimitError("context item count limit exceeded")
        return cls(
            task_id=task_id,
            correlation_id=correlation_id,
            created_at=created_at,
            items=tuple(ContextItem.from_dict(_object(item)) for item in items),
            scope=_scope(data["scope"]),
            metadata=_object(data["metadata"]),
            limits=declared_limits,
        )

    @classmethod
    def from_json(
        cls,
        text: str,
        *,
        expected_task_id: TaskId,
        expected_correlation_id: UUID,
        limits: ContextLimits = DEFAULT_CONTEXT_LIMITS,
    ) -> AgentContext:
        """Decode plain JSON only, rejecting duplicate keys before ordinary json.loads."""
        _limits(limits)
        if type(text) is not str:
            raise ContextValidationError("context JSON must be plain text, not bytes or objects")
        _utf8_size(text, limits.max_total_bytes)
        try:
            _json_structure(text)
            raw: object = json.loads(text)
        except (ValueError, RecursionError) as exc:
            if isinstance(exc, ContextValidationError):
                raise
            raise ContextValidationError("malformed context JSON") from exc
        return cls.from_dict(
            _object(raw),
            expected_task_id=expected_task_id,
            expected_correlation_id=expected_correlation_id,
            limits=limits,
        )


def _json_structure(text: str) -> None:
    """Bound parser nesting and reject repeated member names without object hooks.

    This is only a structural preflight. The stdlib JSON decoder still owns all
    syntax validation. Quoted keys are decoded as plain JSON strings so escaped
    aliases (e.g. a Unicode escape in a repeated key) cannot hide duplicates.
    """
    stack: list[tuple[str, set[str]]] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == '"':
            start = index
            index += 1
            while index < len(text):
                if text[index] == "\\":
                    index += 2
                elif text[index] == '"':
                    break
                else:
                    index += 1
            end = index + 1
            index = end
            while index < len(text) and text[index] in " \r\n\t":
                index += 1
            if index < len(text) and text[index] == ":" and stack and stack[-1][0] == "{":
                key: object = json.loads(text[start:end])
                if type(key) is not str:
                    raise ContextValidationError("JSON member names must be strings")
                if key in stack[-1][1]:
                    raise ContextValidationError("duplicate JSON member name")
                stack[-1][1].add(key)
            continue
        if character in "{[":
            stack.append((character, set()))
            if len(stack) > MAX_CONTEXT_DETAIL_DEPTH + 4:
                raise ContextLimitError("JSON nesting limit exceeded")
        elif character in "}]":
            if not stack or stack.pop()[0] != ("{" if character == "}" else "["):
                raise ContextValidationError("malformed JSON containers")
        index += 1


__all__ = [
    "CONTEXT_SCHEMA_VERSION",
    "DEFAULT_CONTEXT_LIMITS",
    "MAX_CONTEXT_BYTES",
    "MAX_CONTEXT_DETAIL_DEPTH",
    "MAX_CONTEXT_DETAIL_NODES",
    "MAX_CONTEXT_INTEGER_BITS",
    "MAX_CONTEXT_ITEMS",
    "MAX_CONTEXT_ITEM_BYTES",
    "MAX_CONTEXT_METADATA_BYTES",
    "MAX_CONTEXT_METADATA_ENTRIES",
    "AgentContext",
    "ContextBindingError",
    "ContextDuplicateError",
    "ContextItem",
    "ContextItemKind",
    "ContextLimitError",
    "ContextLimits",
    "ContextRecord",
    "ContextScopeError",
    "ContextValidationError",
    "UnsupportedContextSchemaVersionError",
]
