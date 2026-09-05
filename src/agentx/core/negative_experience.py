"""Canonical inert negative-experience record (C2.06).

AgentX invariant: FAILED APPROACHES MUST BE REMEMBERED.

A :class:`NegativeExperienceRecord` is the smallest useful representation of
"this approach was attempted and it did not work". It is *historical evidence*
only:

    * It does NOT mean "never try this again".
    * It never suppresses a retry, prohibits an action, or changes routing.
    * A stored failure grants no authority and removes no authority; authority
      belongs exclusively to ``agentx.kernel``.

Deliberate non-goals (owned elsewhere):

    * Failure taxonomy / classification vocabulary — C4.01. This module keeps a
      narrow *opaque* typed reason reference instead of inventing that
      taxonomy ahead of time.
    * Causal experience modelling (state_before -> action -> observation ->
      state_after -> verification -> outcome) — C2.10.
    * Semantic retrieval, similarity, ranking — C2.09.
    * Repair, learning, procedure synthesis — later days.

The observed outcome reuses the canonical :class:`EpisodeOutcome` vocabulary
from C2.01 rather than introducing a parallel one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.core.episodes import EpisodeOutcome
from agentx.core.ids import EpisodeId, NegativeExperienceId, TaskId
from agentx.core.knowledge import KnowledgeScope

__all__ = [
    "NEGATIVE_EXPERIENCE_SCHEMA_VERSION",
    "AttemptKind",
    "AttemptReference",
    "FailureReference",
    "NegativeExperienceDeserializationError",
    "NegativeExperienceRecord",
    "NegativeExperienceValidationError",
    "UnsupportedNegativeExperienceSchemaVersionError",
]

NEGATIVE_EXPERIENCE_SCHEMA_VERSION: Final[int] = 1

_MAX_TEXT_LENGTH: Final[int] = 4_096

_ATTEMPT_FIELDS: Final[frozenset[str]] = frozenset({"kind", "reference"})
_FAILURE_FIELDS: Final[frozenset[str]] = frozenset({"reason_code", "detail"})
_NEGATIVE_EXPERIENCE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "negative_experience_id",
        "attempt",
        "failure",
        "observed_outcome",
        "observed_at",
        "scope",
        "episode_id",
        "task_id",
        "correlation_id",
    }
)


class NegativeExperienceValidationError(ValueError):
    """Raised when a negative-experience value violates the canonical contract."""


class NegativeExperienceDeserializationError(NegativeExperienceValidationError):
    """Raised when encoded negative-experience data cannot be decoded safely."""


class UnsupportedNegativeExperienceSchemaVersionError(NegativeExperienceDeserializationError):
    """Raised when encoded data uses an unsupported negative-experience schema version."""


class AttemptKind(StrEnum):
    """Controlled vocabulary for *what kind of thing* was attempted.

    Intentionally tiny and additive. This is not a failure taxonomy: it only
    says whether the remembered attempt referenced a capability, a procedure,
    or an otherwise unstructured approach description.
    """

    CAPABILITY = "capability"
    PROCEDURE = "procedure"
    APPROACH = "approach"


def _validate_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise NegativeExperienceValidationError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise NegativeExperienceValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > _MAX_TEXT_LENGTH:
        raise NegativeExperienceValidationError(
            f"{field_name} must be at most {_MAX_TEXT_LENGTH} characters"
        )
    return value


def _validate_optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _validate_text(value, field_name=field_name)


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise NegativeExperienceValidationError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise NegativeExperienceValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise NegativeExperienceDeserializationError(f"{field_name} must be a string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise NegativeExperienceDeserializationError(
            f"{field_name} is not a valid ISO-8601 timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NegativeExperienceDeserializationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class AttemptReference:
    """Opaque, inert reference to the approach/capability/procedure attempted.

    ``reference`` is never dereferenced, resolved, executed, or trusted by this
    contract. For ``CAPABILITY``/``PROCEDURE`` it is expected to be a stable
    canonical identifier string; for ``APPROACH`` it is free-form descriptive
    text. Either way it is data.
    """

    kind: AttemptKind
    reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, AttemptKind):
            raise NegativeExperienceValidationError("attempt.kind must be an AttemptKind")
        object.__setattr__(
            self, "reference", _validate_text(self.reference, field_name="attempt.reference")
        )

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind.value, "reference": self.reference}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> AttemptReference:
        if not isinstance(raw, Mapping):
            raise NegativeExperienceDeserializationError("attempt must be a JSON object")
        unknown = set(raw) - _ATTEMPT_FIELDS
        if unknown:
            raise NegativeExperienceDeserializationError(
                f"attempt contains unknown fields: {sorted(unknown)}"
            )
        missing = _ATTEMPT_FIELDS - set(raw)
        if missing:
            raise NegativeExperienceDeserializationError(
                f"attempt is missing fields: {sorted(missing)}"
            )
        kind_raw = raw["kind"]
        if not isinstance(kind_raw, str):
            raise NegativeExperienceDeserializationError("attempt.kind must be a string")
        try:
            kind = AttemptKind(kind_raw)
        except ValueError as exc:
            raise NegativeExperienceDeserializationError(
                f"unknown attempt.kind: {kind_raw!r}"
            ) from exc
        return cls(
            kind=kind,
            reference=_validate_text(raw["reference"], field_name="attempt.reference"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class FailureReference:
    """Narrow, opaque typed reference to *why* the attempt failed.

    No canonical failure classifier exists yet; C4.01 owns the failure
    taxonomy. Until then ``reason_code`` is a deliberately opaque, caller-chosen
    short token (for example ``"timeout"`` or ``"tool_missing"``) that this
    module never interprets, ranks, groups, or acts on. ``detail`` is optional
    inert human-readable observation text.
    """

    reason_code: str
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reason_code",
            _validate_text(self.reason_code, field_name="failure.reason_code"),
        )
        object.__setattr__(
            self, "detail", _validate_optional_text(self.detail, field_name="failure.detail")
        )

    def to_dict(self) -> dict[str, object]:
        return {"reason_code": self.reason_code, "detail": self.detail}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> FailureReference:
        if not isinstance(raw, Mapping):
            raise NegativeExperienceDeserializationError("failure must be a JSON object")
        unknown = set(raw) - _FAILURE_FIELDS
        if unknown:
            raise NegativeExperienceDeserializationError(
                f"failure contains unknown fields: {sorted(unknown)}"
            )
        if "reason_code" not in raw:
            raise NegativeExperienceDeserializationError("failure is missing reason_code")
        detail = raw.get("detail")
        return cls(
            reason_code=_validate_text(raw["reason_code"], field_name="failure.reason_code"),
            detail=_validate_optional_text(detail, field_name="failure.detail"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class NegativeExperienceRecord:
    """Immutable historical evidence that one attempted approach did not work.

    Inertness: nothing in this record can authorize, prohibit, or suppress a
    future action. Text such as ``"never retry"``, ``"ALLOW"``, ``"ADMIN"``, or
    ``"risk=R0"`` stored in any field is inert content, exactly like every other
    string. Whether a remembered failure is *applicable* to a future decision is
    determined later by routing/reasoning, not here.
    """

    negative_experience_id: NegativeExperienceId
    attempt: AttemptReference
    failure: FailureReference
    observed_at: datetime
    observed_outcome: EpisodeOutcome = EpisodeOutcome.FAILED
    scope: KnowledgeScope = field(default_factory=KnowledgeScope)
    episode_id: EpisodeId | None = None
    task_id: TaskId | None = None
    correlation_id: UUID | None = None
    schema_version: int = NEGATIVE_EXPERIENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.negative_experience_id, NegativeExperienceId):
            raise NegativeExperienceValidationError(
                "negative_experience_id must be a NegativeExperienceId"
            )
        if not isinstance(self.attempt, AttemptReference):
            raise NegativeExperienceValidationError("attempt must be an AttemptReference")
        if not isinstance(self.failure, FailureReference):
            raise NegativeExperienceValidationError("failure must be a FailureReference")
        if not isinstance(self.observed_outcome, EpisodeOutcome):
            raise NegativeExperienceValidationError(
                "observed_outcome must be a canonical EpisodeOutcome"
            )
        if self.observed_outcome is EpisodeOutcome.SUCCEEDED:
            raise NegativeExperienceValidationError(
                "negative experience must not record a SUCCEEDED outcome"
            )
        if not isinstance(self.scope, KnowledgeScope):
            raise NegativeExperienceValidationError("scope must be a KnowledgeScope")
        if self.episode_id is not None and not isinstance(self.episode_id, EpisodeId):
            raise NegativeExperienceValidationError("episode_id must be an EpisodeId or None")
        if self.task_id is not None and not isinstance(self.task_id, TaskId):
            raise NegativeExperienceValidationError("task_id must be a TaskId or None")
        if self.correlation_id is not None:
            if not isinstance(self.correlation_id, UUID):
                raise NegativeExperienceValidationError("correlation_id must be a UUID or None")
            if self.correlation_id.int == 0:
                raise NegativeExperienceValidationError("correlation_id must not be the nil UUID")
        object.__setattr__(
            self, "observed_at", _validate_timestamp(self.observed_at, field_name="observed_at")
        )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise NegativeExperienceValidationError("schema_version must be an integer")
        if self.schema_version != NEGATIVE_EXPERIENCE_SCHEMA_VERSION:
            raise UnsupportedNegativeExperienceSchemaVersionError(
                f"unsupported negative experience schema version {self.schema_version}; "
                f"supported version is {NEGATIVE_EXPERIENCE_SCHEMA_VERSION}"
            )

    @classmethod
    def create(
        cls,
        *,
        attempt: AttemptReference,
        failure: FailureReference,
        observed_outcome: EpisodeOutcome = EpisodeOutcome.FAILED,
        scope: KnowledgeScope | None = None,
        episode_id: EpisodeId | None = None,
        task_id: TaskId | None = None,
        correlation_id: UUID | None = None,
        observed_at: datetime | None = None,
    ) -> NegativeExperienceRecord:
        """Create a new negative-experience record with a fresh identity."""
        return cls(
            negative_experience_id=NegativeExperienceId.create(),
            attempt=attempt,
            failure=failure,
            observed_outcome=observed_outcome,
            scope=KnowledgeScope() if scope is None else scope,
            episode_id=episode_id,
            task_id=task_id,
            correlation_id=correlation_id,
            observed_at=datetime.now(UTC) if observed_at is None else observed_at,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible schema-v1 representation."""
        return {
            "schema_version": self.schema_version,
            "negative_experience_id": self.negative_experience_id.to_str(),
            "attempt": self.attempt.to_dict(),
            "failure": self.failure.to_dict(),
            "observed_outcome": self.observed_outcome.value,
            "observed_at": _format_timestamp(self.observed_at),
            "scope": self.scope.to_dict(),
            "episode_id": None if self.episode_id is None else self.episode_id.to_str(),
            "task_id": None if self.task_id is None else self.task_id.to_str(),
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
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
    def from_dict(cls, raw: Mapping[str, object]) -> NegativeExperienceRecord:
        """Validate and reconstruct a canonical negative-experience object."""
        if not isinstance(raw, Mapping):
            raise NegativeExperienceDeserializationError(
                "negative experience must be a JSON object"
            )
        unknown = set(raw) - _NEGATIVE_EXPERIENCE_FIELDS
        if unknown:
            raise NegativeExperienceDeserializationError(
                f"negative experience contains unknown fields: {sorted(unknown)}"
            )
        missing = _NEGATIVE_EXPERIENCE_FIELDS - set(raw)
        if missing:
            raise NegativeExperienceDeserializationError(
                f"negative experience is missing fields: {sorted(missing)}"
            )

        schema_version = raw["schema_version"]
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise NegativeExperienceDeserializationError("schema_version must be an integer")
        if schema_version != NEGATIVE_EXPERIENCE_SCHEMA_VERSION:
            raise UnsupportedNegativeExperienceSchemaVersionError(
                f"unsupported negative experience schema version {schema_version}; "
                f"supported version is {NEGATIVE_EXPERIENCE_SCHEMA_VERSION}"
            )

        attempt_raw = raw["attempt"]
        failure_raw = raw["failure"]
        scope_raw = raw["scope"]
        if not isinstance(attempt_raw, Mapping):
            raise NegativeExperienceDeserializationError("attempt must be a JSON object")
        if not isinstance(failure_raw, Mapping):
            raise NegativeExperienceDeserializationError("failure must be a JSON object")
        if not isinstance(scope_raw, Mapping):
            raise NegativeExperienceDeserializationError("scope must be a JSON object")

        outcome_raw = raw["observed_outcome"]
        if not isinstance(outcome_raw, str):
            raise NegativeExperienceDeserializationError("observed_outcome must be a string")
        try:
            observed_outcome = EpisodeOutcome(outcome_raw)
        except ValueError as exc:
            raise NegativeExperienceDeserializationError(
                f"unknown observed_outcome: {outcome_raw!r}"
            ) from exc

        try:
            scope = KnowledgeScope.from_dict(scope_raw)
        except ValueError as exc:
            raise NegativeExperienceDeserializationError(f"scope is invalid: {exc}") from exc

        return cls(
            negative_experience_id=_parse_negative_experience_id(raw["negative_experience_id"]),
            attempt=AttemptReference.from_dict(attempt_raw),
            failure=FailureReference.from_dict(failure_raw),
            observed_outcome=observed_outcome,
            observed_at=_parse_timestamp(raw["observed_at"], field_name="observed_at"),
            scope=scope,
            episode_id=_parse_optional_episode_id(raw["episode_id"]),
            task_id=_parse_optional_task_id(raw["task_id"]),
            correlation_id=_parse_optional_uuid(raw["correlation_id"]),
            schema_version=schema_version,
        )

    @classmethod
    def from_json(cls, text: str) -> NegativeExperienceRecord:
        """Validate and reconstruct canonical negative-experience JSON."""
        if not isinstance(text, str):
            raise NegativeExperienceDeserializationError("negative experience JSON must be text")
        try:
            decoded = json.loads(text)
        except ValueError as exc:
            raise NegativeExperienceDeserializationError(
                f"negative experience JSON is malformed: {exc}"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise NegativeExperienceDeserializationError(
                "negative experience JSON root must be an object"
            )
        return cls.from_dict(decoded)


def _parse_negative_experience_id(value: object) -> NegativeExperienceId:
    if not isinstance(value, str):
        raise NegativeExperienceDeserializationError("negative_experience_id must be a string")
    try:
        return NegativeExperienceId.parse(value)
    except ValueError as exc:
        raise NegativeExperienceDeserializationError(
            f"negative_experience_id is not valid: {value!r}"
        ) from exc


def _parse_optional_episode_id(value: object) -> EpisodeId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise NegativeExperienceDeserializationError("episode_id must be a string or null")
    try:
        return EpisodeId.parse(value)
    except ValueError as exc:
        raise NegativeExperienceDeserializationError(f"episode_id is not valid: {value!r}") from exc


def _parse_optional_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise NegativeExperienceDeserializationError("task_id must be a string or null")
    try:
        return TaskId.parse(value)
    except ValueError as exc:
        raise NegativeExperienceDeserializationError(f"task_id is not valid: {value!r}") from exc


def _parse_optional_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise NegativeExperienceDeserializationError("correlation_id must be a string or null")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise NegativeExperienceDeserializationError(
            f"correlation_id is not a valid UUID: {value!r}"
        ) from exc
    if parsed.int == 0:
        raise NegativeExperienceDeserializationError("correlation_id must not be the nil UUID")
    return parsed
