"""Canonical explicit user-preference model contract (M6.04).

This module defines typed, immutable, explicitly-sourced user preference
records. It is DATA ONLY: it performs no inference, no persistence, no
learning, no routing, no confirmation-policy change, and no proactive action.

A :class:`UserPreference` states that a human explicitly expressed a
preference value for a controlled :class:`PreferenceKey` within a canonical
applicability :class:`~agentx.core.knowledge.KnowledgeScope`. It never states
that the preference is true, optimal, permanent, or authorized to act.

Explicit preference != inferred preference != authority:

    * ``USER_EXPLICIT`` records a directly stated preference.
    * ``USER_CORRECTION`` records a directly stated correction. A correction
      is a NEW record that may point at an earlier preference via
      ``supersedes``; it never mutates history in place.
    * Nothing here infers a preference from behavior, clicks, or model output.
      There is deliberately no inferred/research source in this contract.
    * Preference data is inert. Strings such as ``"permission=ADMIN"``,
      ``"risk=R0"``, ``"skip confirmation"``, or ``"verified=true"`` inside a
      value or note have ZERO authority. A preference can never override
      Trusted Kernel security rules, grant Permission, bypass ActionGate, set
      RiskLevel, change a budget, run a capability, alter a Task, call a
      model, or activate a Procedure.

Scope is applicability, NOT permission. A preference scoped to an application
does not authorize actions in that application. An empty/global scope means
"not restricted to a named dimension value", never "permitted everywhere".

Conflicts are never silently resolved. Two records with the same key and
scope but different values remain two separate historical/evidence values.
``supersedes`` is an inert directional reference that a FUTURE lifecycle
policy may interpret; this contract performs no resolution, ranking, or
promotion.

Strength and lifecycle status are intentionally absent. A strength score
would imply ranking/inference and a status would imply lifecycle policy, both
of which are explicit non-goals. Corrections and supersession references are
represented; truth and currency are decided elsewhere, later, explicitly.

This module belongs to ``agentx.core`` and imports nothing from other AgentX
subsystems. It is storage-independent: persistence, if ever added, lives
outside ``agentx.core``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID, uuid4

from agentx.core.knowledge import KnowledgeScope, KnowledgeValidationError
from agentx.core.provenance import EvidenceReference

__all__ = [
    "CANONICAL_PREFERENCE_KEYS",
    "CANONICAL_PREFERENCE_SOURCES",
    "MAX_PREFERENCE_EVIDENCE_REFERENCES",
    "MAX_PREFERENCE_KEY_LENGTH",
    "MAX_PREFERENCE_NOTE_LENGTH",
    "MAX_PREFERENCE_SCOPE_VALUE_LENGTH",
    "MAX_PREFERENCE_VALUE_DEPTH",
    "MAX_PREFERENCE_VALUE_NODES",
    "MAX_PREFERENCE_VALUE_STRING_LENGTH",
    "USER_PREFERENCE_SCHEMA_VERSION",
    "PreferenceKey",
    "PreferenceSource",
    "PreferenceValue",
    "UnsupportedUserPreferenceSchemaVersionError",
    "UserPreference",
    "UserPreferenceDeserializationError",
    "UserPreferenceValidationError",
]

USER_PREFERENCE_SCHEMA_VERSION: Final[int] = 1

#: Maximum serialized length of a controlled preference-key value.
MAX_PREFERENCE_KEY_LENGTH: Final[int] = 64
#: Maximum nesting depth of a preference value (root depth 0).
MAX_PREFERENCE_VALUE_DEPTH: Final[int] = 8
#: Maximum number of JSON values (containers + scalars) in one value.
MAX_PREFERENCE_VALUE_NODES: Final[int] = 256
#: Maximum length of any single string (or object key) inside a value.
MAX_PREFERENCE_VALUE_STRING_LENGTH: Final[int] = 4_096
#: Maximum length of any single scope dimension value on a preference.
MAX_PREFERENCE_SCOPE_VALUE_LENGTH: Final[int] = 256
#: Maximum number of evidence references carried by one preference.
MAX_PREFERENCE_EVIDENCE_REFERENCES: Final[int] = 8
#: Maximum length of the optional human note.
MAX_PREFERENCE_NOTE_LENGTH: Final[int] = 512


class UserPreferenceValidationError(ValueError):
    """Raised when a user-preference value violates the canonical contract."""


class UserPreferenceDeserializationError(UserPreferenceValidationError):
    """Raised when encoded user-preference data cannot be decoded safely."""


class UnsupportedUserPreferenceSchemaVersionError(UserPreferenceDeserializationError):
    """Raised when encoded data uses an unsupported user-preference schema."""


class PreferenceKey(StrEnum):
    """Closed controlled vocabulary of explicit preference categories.

    Members name categories of explicitly stated preference, never
    product-specific freeform keys. A record carries exactly one key and one
    bounded JSON-compatible value; there is no universal mutable dict.
    """

    INTERACTION_STYLE = "interaction_style"
    DEFAULT_APPLICATION = "default_application"
    WORKFLOW_PREFERENCE = "workflow_preference"
    NOTIFICATION_PREFERENCE = "notification_preference"
    CONFIRMATION_PREFERENCE = "confirmation_preference"
    FORMAT_PREFERENCE = "format_preference"


class PreferenceSource(StrEnum):
    """Closed provenance/source vocabulary for explicit preferences.

    Only directly stated human sources exist here. There is deliberately no
    inferred, researched, or model-derived member: this contract must never
    label inference as explicit.
    """

    USER_EXPLICIT = "user_explicit"
    USER_CORRECTION = "user_correction"


#: The canonical preference keys in declaration order.
CANONICAL_PREFERENCE_KEYS: Final[tuple[PreferenceKey, ...]] = tuple(PreferenceKey)

#: The canonical preference sources in declaration order.
CANONICAL_PREFERENCE_SOURCES: Final[tuple[PreferenceSource, ...]] = tuple(PreferenceSource)

PreferenceValue = (
    bool | int | float | str | list["PreferenceValue"] | dict[str, "PreferenceValue"] | None
)

_PREFERENCE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "preference_id",
        "key",
        "value",
        "source",
        "scope",
        "recorded_at",
        "supersedes",
        "evidence",
        "note",
    }
)


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise UserPreferenceValidationError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise UserPreferenceValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise UserPreferenceDeserializationError(f"{field_name} must be a string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise UserPreferenceDeserializationError(
            f"{field_name} is not a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UserPreferenceDeserializationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise UserPreferenceValidationError(f"{field_name} must be a UUID")
    if value.int == 0:
        raise UserPreferenceValidationError(f"{field_name} must not be the nil UUID")
    return value


def _parse_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, str):
        raise UserPreferenceDeserializationError(f"{field_name} must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise UserPreferenceDeserializationError(
            f"{field_name} is not a valid UUID: {value!r}"
        ) from exc
    if parsed.int == 0:
        raise UserPreferenceDeserializationError(f"{field_name} must not be the nil UUID")
    return parsed


def _parse_optional_uuid(value: object, *, field_name: str) -> UUID | None:
    if value is None:
        return None
    return _parse_uuid(value, field_name=field_name)


def _validate_note(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise UserPreferenceValidationError("note must be a string or None")
    if not value or value != value.strip():
        raise UserPreferenceValidationError("note must be non-empty and trimmed when provided")
    if len(value) > MAX_PREFERENCE_NOTE_LENGTH:
        raise UserPreferenceValidationError(
            f"note must be at most {MAX_PREFERENCE_NOTE_LENGTH} characters"
        )
    return value


def _freeze_preference_value(
    value: object,
    *,
    path: str,
    depth: int,
    active: set[int],
    count: list[int],
) -> object:
    """Validate JSON compatibility and bounds; return an immutable copy."""
    count[0] += 1
    if count[0] > MAX_PREFERENCE_VALUE_NODES:
        raise UserPreferenceValidationError(
            f"{path} exceeds the maximum of {MAX_PREFERENCE_VALUE_NODES} JSON values"
        )
    if depth > MAX_PREFERENCE_VALUE_DEPTH:
        raise UserPreferenceValidationError(
            f"{path} exceeds the maximum value depth of {MAX_PREFERENCE_VALUE_DEPTH}"
        )
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UserPreferenceValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, str):
        if len(value) > MAX_PREFERENCE_VALUE_STRING_LENGTH:
            raise UserPreferenceValidationError(
                f"{path} exceeds the maximum string length of {MAX_PREFERENCE_VALUE_STRING_LENGTH}"
            )
        return value
    if isinstance(value, Mapping):
        ident = id(value)
        if ident in active:
            raise UserPreferenceValidationError(f"{path} contains a reference cycle")
        active.add(ident)
        try:
            frozen: dict[str, object] = {}
            for raw_key, item in value.items():
                if not isinstance(raw_key, str):
                    raise UserPreferenceValidationError(f"{path} contains a non-string object key")
                if len(raw_key) > MAX_PREFERENCE_VALUE_STRING_LENGTH:
                    raise UserPreferenceValidationError(
                        f"{path} contains an object key exceeding the maximum string "
                        f"length of {MAX_PREFERENCE_VALUE_STRING_LENGTH}"
                    )
                frozen[raw_key] = _freeze_preference_value(
                    item,
                    path=f"{path}.{raw_key}",
                    depth=depth + 1,
                    active=active,
                    count=count,
                )
            return MappingProxyType(frozen)
        finally:
            active.remove(ident)
    if isinstance(value, list | tuple):
        ident = id(value)
        if ident in active:
            raise UserPreferenceValidationError(f"{path} contains a reference cycle")
        active.add(ident)
        try:
            return tuple(
                _freeze_preference_value(
                    item,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                    active=active,
                    count=count,
                )
                for index, item in enumerate(value)
            )
        finally:
            active.remove(ident)
    raise UserPreferenceValidationError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _freeze_value_root(value: object) -> object:
    return _freeze_preference_value(value, path="value", depth=0, active=set(), count=[0])


def _to_json_value(value: object, *, path: str) -> PreferenceValue:
    """Convert an internally frozen value back to mutable JSON primitives."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):  # defensive; construction already rejects this
            raise UserPreferenceValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        result: dict[str, PreferenceValue] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):  # defensive; construction rejects this
                raise UserPreferenceValidationError(f"{path} contains a non-string object key")
            result[raw_key] = _to_json_value(item, path=f"{path}.{raw_key}")
        return result
    if isinstance(value, tuple | list):
        return [_to_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise UserPreferenceValidationError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _parse_key(value: object) -> PreferenceKey:
    if not isinstance(value, str):
        raise UserPreferenceDeserializationError("key must be a string")
    try:
        return PreferenceKey(value)
    except ValueError as exc:
        raise UserPreferenceDeserializationError(f"unknown preference key: {value!r}") from exc


def _parse_source(value: object) -> PreferenceSource:
    if not isinstance(value, str):
        raise UserPreferenceDeserializationError("source must be a string")
    try:
        return PreferenceSource(value)
    except ValueError as exc:
        raise UserPreferenceDeserializationError(f"unknown preference source: {value!r}") from exc


def _parse_scope(value: object) -> KnowledgeScope:
    if not isinstance(value, Mapping):
        raise UserPreferenceDeserializationError("scope must be a JSON object")
    try:
        return KnowledgeScope.from_dict(value)
    except KnowledgeValidationError as exc:
        raise UserPreferenceDeserializationError(f"scope is invalid: {exc}") from exc


def _parse_evidence(value: object) -> tuple[EvidenceReference, ...]:
    if not isinstance(value, list):
        raise UserPreferenceDeserializationError("evidence must be a JSON array")
    references: list[EvidenceReference] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise UserPreferenceDeserializationError(f"evidence[{index}] must be a JSON object")
        copied: dict[str, object] = {}
        for raw_key, entry in item.items():
            if not isinstance(raw_key, str):
                raise UserPreferenceDeserializationError(
                    f"evidence[{index}] contains a non-string object key"
                )
            copied[raw_key] = entry
        try:
            references.append(EvidenceReference.from_dict(copied))
        except KnowledgeValidationError as exc:
            raise UserPreferenceDeserializationError(
                f"evidence[{index}] is invalid: {exc}"
            ) from exc
    return tuple(references)


@dataclass(frozen=True, slots=True, kw_only=True)
class UserPreference:
    """One immutable explicit user-preference record.

    The record is inert DATA. Constructing, serializing, or decoding it
    chooses no application, routes nothing, grants no confirmation bypass,
    modifies no ActionGate, sets no RiskLevel, changes no budget, runs no
    capability, alters no Task, calls no model, learns nothing from clicks,
    and observes no behavior. Hostile content in ``value`` or ``note``
    (``"permission=ADMIN"``, ``"risk=R0"``, ``"skip confirmation"``,
    ``"verified=true"``) is stored and returned as characters and is
    interpreted by nothing.
    """

    preference_id: UUID
    key: PreferenceKey
    value: object
    source: PreferenceSource
    recorded_at: datetime
    scope: KnowledgeScope = field(default_factory=KnowledgeScope)
    supersedes: UUID | None = None
    evidence: tuple[EvidenceReference, ...] = ()
    note: str | None = None
    schema_version: int = USER_PREFERENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "preference_id",
            _validate_uuid(self.preference_id, field_name="preference_id"),
        )
        if not isinstance(self.key, PreferenceKey):
            raise UserPreferenceValidationError("key must be a PreferenceKey member")
        if len(self.key.value) > MAX_PREFERENCE_KEY_LENGTH:
            raise UserPreferenceValidationError(
                f"key exceeds the maximum length of {MAX_PREFERENCE_KEY_LENGTH}"
            )
        object.__setattr__(self, "value", _freeze_value_root(self.value))
        if not isinstance(self.source, PreferenceSource):
            raise UserPreferenceValidationError("source must be a PreferenceSource member")
        object.__setattr__(
            self,
            "recorded_at",
            _validate_timestamp(self.recorded_at, field_name="recorded_at"),
        )
        if not isinstance(self.scope, KnowledgeScope):
            raise UserPreferenceValidationError("scope must be a KnowledgeScope")
        for dimension, dimension_value in self.scope.dimensions.items():
            if len(dimension_value) > MAX_PREFERENCE_SCOPE_VALUE_LENGTH:
                raise UserPreferenceValidationError(
                    f"scope.{dimension.value} exceeds the maximum length of "
                    f"{MAX_PREFERENCE_SCOPE_VALUE_LENGTH}"
                )
        if self.supersedes is not None:
            _validate_uuid(self.supersedes, field_name="supersedes")
            if self.supersedes == self.preference_id:
                raise UserPreferenceValidationError(
                    "supersedes must not reference the record itself"
                )
        if not isinstance(self.evidence, tuple):
            raise UserPreferenceValidationError("evidence must be a tuple")
        if len(self.evidence) > MAX_PREFERENCE_EVIDENCE_REFERENCES:
            raise UserPreferenceValidationError(
                f"evidence exceeds the maximum of {MAX_PREFERENCE_EVIDENCE_REFERENCES} references"
            )
        for reference in self.evidence:
            if not isinstance(reference, EvidenceReference):
                raise UserPreferenceValidationError(
                    "evidence must contain only EvidenceReference values"
                )
        object.__setattr__(self, "note", _validate_note(self.note))
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise UserPreferenceValidationError("schema_version must be an integer")
        if self.schema_version != USER_PREFERENCE_SCHEMA_VERSION:
            raise UnsupportedUserPreferenceSchemaVersionError(
                f"unsupported user preference schema version {self.schema_version}; "
                f"supported version is {USER_PREFERENCE_SCHEMA_VERSION}"
            )

    @classmethod
    def create(
        cls,
        *,
        key: PreferenceKey,
        value: object,
        source: PreferenceSource,
        scope: KnowledgeScope | None = None,
        recorded_at: datetime | None = None,
        supersedes: UUID | None = None,
        evidence: tuple[EvidenceReference, ...] = (),
        note: str | None = None,
    ) -> UserPreference:
        """Create one explicit preference record with a fresh identity."""
        return cls(
            preference_id=uuid4(),
            key=key,
            value=value,
            source=source,
            recorded_at=datetime.now(UTC) if recorded_at is None else recorded_at,
            scope=KnowledgeScope() if scope is None else scope,
            supersedes=supersedes,
            evidence=evidence,
            note=note,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible schema-v1 representation."""
        return {
            "schema_version": self.schema_version,
            "preference_id": str(self.preference_id),
            "key": self.key.value,
            "value": _to_json_value(self.value, path="value"),
            "source": self.source.value,
            "scope": self.scope.to_dict(),
            "recorded_at": _format_timestamp(self.recorded_at),
            "supersedes": None if self.supersedes is None else str(self.supersedes),
            "evidence": [reference.to_dict() for reference in self.evidence],
            "note": self.note,
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
    def from_dict(cls, raw: Mapping[str, object]) -> UserPreference:
        """Validate and reconstruct one canonical preference object."""
        if not isinstance(raw, Mapping):
            raise UserPreferenceDeserializationError("preference must be a JSON object")
        actual = set(raw)
        missing = _PREFERENCE_FIELDS - actual
        unknown = actual - _PREFERENCE_FIELDS
        if missing:
            raise UserPreferenceDeserializationError(
                f"preference missing required fields: {sorted(missing)}"
            )
        if unknown:
            raise UserPreferenceDeserializationError(
                f"preference contains unknown fields: {sorted(unknown)}"
            )
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise UserPreferenceDeserializationError("schema_version must be an integer")
        if version != USER_PREFERENCE_SCHEMA_VERSION:
            raise UnsupportedUserPreferenceSchemaVersionError(
                f"unsupported user preference schema version {version}; "
                f"supported version is {USER_PREFERENCE_SCHEMA_VERSION}"
            )
        note_raw = raw["note"]
        if note_raw is not None and not isinstance(note_raw, str):
            raise UserPreferenceDeserializationError("note must be a string or null")
        return cls(
            preference_id=_parse_uuid(raw["preference_id"], field_name="preference_id"),
            key=_parse_key(raw["key"]),
            value=raw["value"],
            source=_parse_source(raw["source"]),
            recorded_at=_parse_timestamp(raw["recorded_at"], field_name="recorded_at"),
            scope=_parse_scope(raw["scope"]),
            supersedes=_parse_optional_uuid(raw["supersedes"], field_name="supersedes"),
            evidence=_parse_evidence(raw["evidence"]),
            note=note_raw,
            schema_version=version,
        )

    @classmethod
    def from_json(cls, raw: str) -> UserPreference:
        """Deserialize canonical JSON without object hooks or dynamic types."""
        if not isinstance(raw, str):
            raise UserPreferenceDeserializationError("preference JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UserPreferenceDeserializationError("preference JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise UserPreferenceDeserializationError("preference JSON root must be an object")
        copied: dict[str, object] = {}
        for raw_key, item in decoded.items():
            if not isinstance(raw_key, str):
                raise UserPreferenceDeserializationError(
                    "preference JSON contains a non-string object key"
                )
            copied[raw_key] = item
        return cls.from_dict(copied)
