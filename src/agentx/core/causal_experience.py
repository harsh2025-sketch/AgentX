"""Canonical inert causal-experience model for AgentX (C2.10).

The model records one observed execution transition as historical data:

    state_before -> action -> observation -> state_after -> verification -> outcome

It does not prove scientific/statistical causation. It preserves the causal
execution chain AgentX observed so later systems may reason over it. Records
never grant authority, execute behavior, mutate Task state, route work, retry,
promote knowledge, activate procedures, or invoke models.

The contract deliberately reuses the inward C1.02 core evidence payloads used
by A1.10: ActionPayload, ObservationPayload, and VerificationPayload. This
keeps agentx.core independent of the outward Capability ABI while preserving
the same inert evidence representation A1.10 emits into canonical Events.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.core.events import (
    ActionPayload,
    EventValidationError,
    ObservationPayload,
    VerificationPayload,
)
from agentx.core.ids import EpisodeId, TaskId

__all__ = [
    "CAUSAL_EXPERIENCE_SCHEMA_VERSION",
    "CausalExperience",
    "CausalExperienceDeserializationError",
    "CausalExperienceValidationError",
    "CausalOutcome",
    "ExperienceState",
    "UnsupportedCausalExperienceSchemaVersionError",
]

CAUSAL_EXPERIENCE_SCHEMA_VERSION: Final[int] = 1
_MAX_OUTCOME_DETAIL_LENGTH: Final[int] = 4_096
_EXPERIENCE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "task_id",
        "correlation_id",
        "episode_id",
        "state_before",
        "action",
        "action_at",
        "observation",
        "observation_at",
        "state_after",
        "verification",
        "verification_at",
        "outcome",
        "outcome_at",
        "outcome_detail",
    }
)
_STATE_FIELDS: Final[frozenset[str]] = frozenset({"captured_at", "observation"})


class CausalExperienceValidationError(ValueError):
    """Raised when causal-experience data violates the canonical contract."""


class CausalExperienceDeserializationError(CausalExperienceValidationError):
    """Raised when encoded causal-experience data cannot be reconstructed safely."""


class UnsupportedCausalExperienceSchemaVersionError(CausalExperienceDeserializationError):
    """Raised when encoded data uses an unsupported causal-experience schema."""


class CausalOutcome(StrEnum):
    """Historical terminal outcome for one causal execution attempt.

    This is descriptive history, not a routing/retry/policy instruction.
    ``VERIFIED`` is the only value that means canonical verification explicitly
    passed. ``CANCELLED`` and ``TIMED_OUT`` describe observed termination only;
    neither forbids a future retry. ``DENIED`` means the requested action was
    refused before capability execution.
    """

    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    EXECUTION_FAILED = "execution_failed"
    DENIED = "denied"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise CausalExperienceValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise CausalExperienceDeserializationError(f"{field_name} must be a string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CausalExperienceDeserializationError(
            f"{field_name} is not a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CausalExperienceDeserializationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_optional_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, TaskId):
        raise TypeError(f"task_id must be a TaskId or None, got {type(value).__name__}")
    return value


def _parse_optional_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CausalExperienceDeserializationError("task_id must be a string or null")
    try:
        return TaskId.parse(value)
    except ValueError as exc:
        raise CausalExperienceDeserializationError("task_id is not a valid TaskId") from exc


def _validate_optional_episode_id(value: object) -> EpisodeId | None:
    if value is None:
        return None
    if not isinstance(value, EpisodeId):
        raise TypeError(f"episode_id must be an EpisodeId or None, got {type(value).__name__}")
    return value


def _parse_optional_episode_id(value: object) -> EpisodeId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CausalExperienceDeserializationError("episode_id must be a string or null")
    try:
        return EpisodeId.parse(value)
    except ValueError as exc:
        raise CausalExperienceDeserializationError("episode_id is not a valid EpisodeId") from exc


def _validate_correlation_id(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"correlation_id must be a UUID, got {type(value).__name__}")
    if value.int == 0:
        raise CausalExperienceValidationError("correlation_id must not be the nil UUID")
    return value


def _parse_correlation_id(value: object) -> UUID:
    if not isinstance(value, str):
        raise CausalExperienceDeserializationError("correlation_id must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise CausalExperienceDeserializationError("correlation_id is not a valid UUID") from exc
    if parsed.int == 0:
        raise CausalExperienceDeserializationError("correlation_id must not be the nil UUID")
    return parsed


def _validate_optional_detail(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"outcome_detail must be a string or None, got {type(value).__name__}")
    if not value or value != value.strip():
        raise CausalExperienceValidationError(
            "outcome_detail must be non-empty and trimmed when provided"
        )
    if len(value) > _MAX_OUTCOME_DETAIL_LENGTH:
        raise CausalExperienceValidationError(
            f"outcome_detail must not exceed {_MAX_OUTCOME_DETAIL_LENGTH} characters"
        )
    return value


def _parse_outcome(value: object) -> CausalOutcome:
    if not isinstance(value, str):
        raise CausalExperienceDeserializationError("outcome must be a string")
    try:
        return CausalOutcome(value)
    except ValueError as exc:
        raise CausalExperienceDeserializationError(
            f"outcome must be one of {[member.value for member in CausalOutcome]}"
        ) from exc


def _require_exact_fields(
    raw: Mapping[str, object], *, expected: frozenset[str], context: str
) -> None:
    actual = set(raw)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise CausalExperienceDeserializationError(
            f"{context} missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise CausalExperienceDeserializationError(
            f"{context} contains unknown fields: {sorted(unknown)}"
        )


def _require_mapping(value: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CausalExperienceDeserializationError(f"{field_name} must be a JSON object")
    return value


def _parse_action(value: object) -> ActionPayload:
    try:
        return ActionPayload.from_dict(_require_mapping(value, field_name="action"))
    except EventValidationError as exc:
        raise CausalExperienceDeserializationError("action payload is invalid") from exc


def _parse_optional_observation(value: object) -> ObservationPayload | None:
    if value is None:
        return None
    try:
        return ObservationPayload.from_dict(_require_mapping(value, field_name="observation"))
    except EventValidationError as exc:
        raise CausalExperienceDeserializationError("observation payload is invalid") from exc


def _parse_optional_verification(value: object) -> VerificationPayload | None:
    if value is None:
        return None
    try:
        return VerificationPayload.from_dict(_require_mapping(value, field_name="verification"))
    except EventValidationError as exc:
        raise CausalExperienceDeserializationError("verification payload is invalid") from exc


@dataclass(frozen=True, slots=True)
class ExperienceState:
    """Inert state snapshot captured at one point in the observed transition.

    ``observation`` reuses the canonical core ObservationPayload. It may contain
    untrusted JSON-compatible external content, but it is only historical
    evidence and is never interpreted as code, permission, policy, or verified
    success.
    """

    captured_at: datetime
    observation: ObservationPayload

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "captured_at",
            _validate_timestamp(self.captured_at, field_name="state.captured_at"),
        )
        if not isinstance(self.observation, ObservationPayload):
            raise TypeError(
                "state.observation must be an ObservationPayload, "
                f"got {type(self.observation).__name__}"
            )

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic JSON-compatible state representation."""
        return {
            "captured_at": _format_timestamp(self.captured_at),
            "observation": self.observation.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ExperienceState:
        """Reconstruct one state snapshot and fail closed on malformed data."""
        _require_exact_fields(raw, expected=_STATE_FIELDS, context="state")
        try:
            observation = ObservationPayload.from_dict(
                _require_mapping(raw["observation"], field_name="state.observation")
            )
        except EventValidationError as exc:
            raise CausalExperienceDeserializationError(
                "state observation payload is invalid"
            ) from exc
        return cls(
            captured_at=_parse_timestamp(raw["captured_at"], field_name="state.captured_at"),
            observation=observation,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CausalExperience:
    """One immutable historical causal execution transition/attempt.

    The record describes sequence, not scientific causation. ``action`` is the
    requested/attempted action description; its mere presence does not prove
    execution. ``observation`` and ``state_after`` likewise do not prove
    success. The only verified-success state is ``outcome == VERIFIED`` with a
    present canonical VerificationPayload whose ``passed`` value is true.
    """

    correlation_id: UUID
    state_before: ExperienceState
    action: ActionPayload
    action_at: datetime
    outcome: CausalOutcome
    outcome_at: datetime
    task_id: TaskId | None = None
    episode_id: EpisodeId | None = None
    observation: ObservationPayload | None = None
    observation_at: datetime | None = None
    state_after: ExperienceState | None = None
    verification: VerificationPayload | None = None
    verification_at: datetime | None = None
    outcome_detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "correlation_id", _validate_correlation_id(self.correlation_id))
        object.__setattr__(self, "task_id", _validate_optional_task_id(self.task_id))
        object.__setattr__(self, "episode_id", _validate_optional_episode_id(self.episode_id))
        if not isinstance(self.state_before, ExperienceState):
            raise TypeError(
                f"state_before must be an ExperienceState, got {type(self.state_before).__name__}"
            )
        if not isinstance(self.action, ActionPayload):
            raise TypeError(f"action must be an ActionPayload, got {type(self.action).__name__}")
        if self.observation is not None and not isinstance(self.observation, ObservationPayload):
            raise TypeError(
                "observation must be an ObservationPayload or None, "
                f"got {type(self.observation).__name__}"
            )
        if self.state_after is not None and not isinstance(self.state_after, ExperienceState):
            raise TypeError(
                "state_after must be an ExperienceState or None, "
                f"got {type(self.state_after).__name__}"
            )
        if self.verification is not None and not isinstance(self.verification, VerificationPayload):
            raise TypeError(
                "verification must be a VerificationPayload or None, "
                f"got {type(self.verification).__name__}"
            )
        if not isinstance(self.outcome, CausalOutcome):
            raise TypeError(f"outcome must be a CausalOutcome, got {type(self.outcome).__name__}")

        action_at = _validate_timestamp(self.action_at, field_name="action_at")
        outcome_at = _validate_timestamp(self.outcome_at, field_name="outcome_at")
        observation_at = (
            None
            if self.observation_at is None
            else _validate_timestamp(self.observation_at, field_name="observation_at")
        )
        verification_at = (
            None
            if self.verification_at is None
            else _validate_timestamp(self.verification_at, field_name="verification_at")
        )
        object.__setattr__(self, "action_at", action_at)
        object.__setattr__(self, "outcome_at", outcome_at)
        object.__setattr__(self, "observation_at", observation_at)
        object.__setattr__(self, "verification_at", verification_at)
        object.__setattr__(self, "outcome_detail", _validate_optional_detail(self.outcome_detail))

        if action_at < self.state_before.captured_at:
            raise CausalExperienceValidationError(
                "action_at must not be earlier than state_before.captured_at"
            )
        if (self.observation is None) != (observation_at is None):
            raise CausalExperienceValidationError(
                "observation and observation_at must either both be present or both be absent"
            )
        if observation_at is not None and observation_at < action_at:
            raise CausalExperienceValidationError(
                "observation_at must not be earlier than action_at"
            )

        prior_to_after = observation_at if observation_at is not None else action_at
        if self.state_after is not None and self.state_after.captured_at < prior_to_after:
            raise CausalExperienceValidationError(
                "state_after.captured_at must not be earlier than the preceding observed stage"
            )

        if (self.verification is None) != (verification_at is None):
            raise CausalExperienceValidationError(
                "verification and verification_at must either both be present or both be absent"
            )
        if self.verification is not None:
            if self.observation is None:
                raise CausalExperienceValidationError(
                    "verification requires a preceding observation"
                )
            if self.state_after is None:
                raise CausalExperienceValidationError(
                    "verification requires a preceding state_after snapshot"
                )
            if verification_at is None:  # pragma: no cover - paired-presence guard above
                raise AssertionError("verification timestamp unexpectedly absent")
            if verification_at < self.state_after.captured_at:
                raise CausalExperienceValidationError(
                    "verification_at must not be earlier than state_after.captured_at"
                )

        latest_stage = action_at
        if observation_at is not None:
            latest_stage = observation_at
        if self.state_after is not None:
            latest_stage = self.state_after.captured_at
        if verification_at is not None:
            latest_stage = verification_at
        if outcome_at < latest_stage:
            raise CausalExperienceValidationError(
                "outcome_at must not be earlier than any preceding recorded stage"
            )

        self._validate_outcome_evidence()

    def _validate_outcome_evidence(self) -> None:
        if self.outcome is CausalOutcome.VERIFIED:
            if self.verification is None or not self.verification.passed:
                raise CausalExperienceValidationError(
                    "VERIFIED outcome requires an explicit passing verification"
                )
            return

        if self.outcome is CausalOutcome.VERIFICATION_FAILED:
            if self.verification is None or self.verification.passed:
                raise CausalExperienceValidationError(
                    "VERIFICATION_FAILED outcome requires an explicit failing verification"
                )
            return

        if self.verification is not None:
            raise CausalExperienceValidationError(
                f"{self.outcome.value} outcome must not carry fabricated verification evidence"
            )
        if self.outcome is CausalOutcome.DENIED and (
            self.observation is not None or self.state_after is not None
        ):
            raise CausalExperienceValidationError(
                "DENIED outcome must not fabricate capability observation or state_after"
            )

    @property
    def verified(self) -> bool:
        """Whether this historical attempt contains explicit passing verification."""
        return self.outcome is CausalOutcome.VERIFIED

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "schema_version": CAUSAL_EXPERIENCE_SCHEMA_VERSION,
            "task_id": None if self.task_id is None else self.task_id.to_str(),
            "correlation_id": str(self.correlation_id),
            "episode_id": None if self.episode_id is None else self.episode_id.to_str(),
            "state_before": self.state_before.to_dict(),
            "action": self.action.to_dict(),
            "action_at": _format_timestamp(self.action_at),
            "observation": None if self.observation is None else self.observation.to_dict(),
            "observation_at": (
                None if self.observation_at is None else _format_timestamp(self.observation_at)
            ),
            "state_after": None if self.state_after is None else self.state_after.to_dict(),
            "verification": None if self.verification is None else self.verification.to_dict(),
            "verification_at": (
                None if self.verification_at is None else _format_timestamp(self.verification_at)
            ),
            "outcome": self.outcome.value,
            "outcome_at": _format_timestamp(self.outcome_at),
            "outcome_detail": self.outcome_detail,
        }

    def to_json(self) -> str:
        """Serialize deterministically without object hooks or executable types."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> CausalExperience:
        """Validate and reconstruct one canonical causal-experience record."""
        if "schema_version" not in raw:
            raise CausalExperienceDeserializationError(
                "causal experience missing required field: schema_version"
            )
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise CausalExperienceDeserializationError("schema_version must be an integer")
        if version != CAUSAL_EXPERIENCE_SCHEMA_VERSION:
            raise UnsupportedCausalExperienceSchemaVersionError(
                f"unsupported causal experience schema version {version}; "
                f"supported version is {CAUSAL_EXPERIENCE_SCHEMA_VERSION}"
            )
        _require_exact_fields(raw, expected=_EXPERIENCE_FIELDS, context="causal experience")

        state_before = ExperienceState.from_dict(
            _require_mapping(raw["state_before"], field_name="state_before")
        )
        state_after_raw = raw["state_after"]
        state_after = (
            None
            if state_after_raw is None
            else ExperienceState.from_dict(
                _require_mapping(state_after_raw, field_name="state_after")
            )
        )

        observation_raw = raw["observation"]
        observation_at_raw = raw["observation_at"]
        verification_raw = raw["verification"]
        verification_at_raw = raw["verification_at"]
        outcome_detail = raw["outcome_detail"]
        if outcome_detail is not None and not isinstance(outcome_detail, str):
            raise CausalExperienceDeserializationError("outcome_detail must be a string or null")

        return cls(
            task_id=_parse_optional_task_id(raw["task_id"]),
            correlation_id=_parse_correlation_id(raw["correlation_id"]),
            episode_id=_parse_optional_episode_id(raw["episode_id"]),
            state_before=state_before,
            action=_parse_action(raw["action"]),
            action_at=_parse_timestamp(raw["action_at"], field_name="action_at"),
            observation=_parse_optional_observation(observation_raw),
            observation_at=(
                None
                if observation_at_raw is None
                else _parse_timestamp(observation_at_raw, field_name="observation_at")
            ),
            state_after=state_after,
            verification=_parse_optional_verification(verification_raw),
            verification_at=(
                None
                if verification_at_raw is None
                else _parse_timestamp(verification_at_raw, field_name="verification_at")
            ),
            outcome=_parse_outcome(raw["outcome"]),
            outcome_at=_parse_timestamp(raw["outcome_at"], field_name="outcome_at"),
            outcome_detail=outcome_detail,
        )

    @classmethod
    def from_json(cls, text: str) -> CausalExperience:
        """Decode deterministic JSON and fail closed on malformed/non-object data."""
        if not isinstance(text, str):
            raise TypeError(f"causal experience JSON must be a string, got {type(text).__name__}")
        try:
            decoded = json.loads(text)
        except ValueError as exc:
            raise CausalExperienceDeserializationError(
                "causal experience JSON is malformed"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise CausalExperienceDeserializationError(
                "causal experience JSON root must be an object"
            )
        return cls.from_dict(decoded)
