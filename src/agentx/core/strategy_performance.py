"""Canonical inert measured strategy-performance history record (A8.01).

A8.01 persists and queries the *measured* history of execution-strategy
performance so later tasks (A8.02+) can compare and optimize strategies. This
module owns the canonical record and the deterministic aggregation primitives
for that history. It owns nothing else:

    * It never selects a strategy, never routes work, and never changes the
      Router (A2.07). The record only names which execution level was actually
      executed, as an observed fact.
    * It never implements bandits/RL, caching, or automatic cost optimization.
    * It never verifies anything and never manufactures success. Verification
      evidence is embedded verbatim from the canonical inert payloads that
      A1.10 emits into Events; the record rules below only *require* such
      evidence, they never create it.
    * Records grant no authority, execute no behavior, mutate no Task state,
      and perform no persistence.

Record only observed/measured facts
-----------------------------------

Every field is either an observed identity (level, strategy name/version,
procedure/capability reference, task/episode/correlation reference, scope,
environment), a measured quantity (cost with its unit, latency), or an
observed terminal outcome with its canonical evidence. The record deliberately
has no free-text success claim: text is inert and can never flip an outcome.

No fake success
---------------

Success must require canonical verified evidence. The same evidence rule that
C2.10 causal experience enforces is enforced here structurally:

    * ``outcome is CausalOutcome.VERIFIED`` requires an embedded canonical
      :class:`~agentx.core.events.VerificationPayload` whose ``passed`` is
      ``True``. No payload, or a failing payload, is a validation error - a
      hostile string such as ``"verified=true"`` or ``"success"`` in any text
      field can never produce a VERIFIED record.
    * ``outcome is CausalOutcome.VERIFICATION_FAILED`` requires an embedded
      payload whose ``passed`` is ``False``.
    * Every other outcome (``EXECUTION_FAILED``, ``DENIED``, ``CANCELLED``,
      ``TIMED_OUT``) must NOT carry verification evidence: attaching a payload
      to such an outcome is rejected as fabricated evidence.

Outcome vocabulary is reused from the canonical
:class:`~agentx.core.causal_experience.CausalOutcome` so one historical outcome
vocabulary spans causal experience and strategy-performance history. The
strategy/execution-level identity values mirror the canonical A2.07 execution
levels (``agentx.cognition.router``); routing stays owned there and is never
changed here. A placement test pins the two vocabularies together so they
cannot drift.

Aggregation primitives
----------------------

:func:`aggregate_strategy_records` and :func:`group_strategy_records` are pure,
deterministic descriptive statistics over already-validated records: counts,
per-outcome and per-failure-category counts, and summary statistics of the
measured latency and per-unit costs. They deliberately stop short of strategy
comparison, ranking, selection, or optimization - A8.02+ builds on these
primitives and owns comparison.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.core.causal_experience import CausalOutcome
from agentx.core.events import EventValidationError, VerificationPayload
from agentx.core.failure_taxonomy import CANONICAL_FAILURE_CATEGORIES, FailureCategory
from agentx.core.ids import (
    CapabilityId,
    EpisodeId,
    ProcedureId,
    StrategyPerformanceId,
    TaskId,
)

__all__ = [
    "STRATEGY_PERFORMANCE_SCHEMA_VERSION",
    "ExecutionStrategyLevel",
    "MeasuredValueAggregate",
    "StrategyAggregationDimension",
    "StrategyPerformanceAggregate",
    "StrategyPerformanceDeserializationError",
    "StrategyPerformanceGroup",
    "StrategyPerformanceRecord",
    "StrategyPerformanceValidationError",
    "UnsupportedStrategyPerformanceSchemaVersionError",
    "aggregate_strategy_records",
    "group_strategy_records",
]


STRATEGY_PERFORMANCE_SCHEMA_VERSION: Final[int] = 1

_MAX_STRATEGY_NAME_LENGTH: Final[int] = 128
_MAX_STRATEGY_VERSION_LENGTH: Final[int] = 64
_MAX_SCOPE_LENGTH: Final[int] = 256
_MAX_ENVIRONMENT_LENGTH: Final[int] = 256
_MAX_COST_UNIT_LENGTH: Final[int] = 32

_PERFORMANCE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "record_id",
        "level",
        "outcome",
        "strategy_name",
        "strategy_version",
        "procedure_id",
        "procedure_revision",
        "capability_id",
        "task_id",
        "episode_id",
        "correlation_id",
        "scope",
        "environment",
        "verification",
        "failure_category",
        "cost",
        "cost_unit",
        "latency_seconds",
        "started_at",
        "ended_at",
        "recorded_at",
    }
)

JsonValue = bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None


class StrategyPerformanceValidationError(ValueError):
    """Raised when a strategy-performance record violates the A8.01 contract."""


class StrategyPerformanceDeserializationError(StrategyPerformanceValidationError):
    """Raised when encoded strategy-performance data cannot be decoded safely."""


class UnsupportedStrategyPerformanceSchemaVersionError(StrategyPerformanceDeserializationError):
    """Raised when encoded data uses an unsupported strategy-performance schema."""


class ExecutionStrategyLevel(StrEnum):
    """Executed strategy class identity: the canonical A2.07 execution levels.

    Member names and values deliberately mirror
    ``agentx.cognition.router.ExecutionLevel`` (L0 cached through L5
    exploratory). ``agentx.core`` cannot import ``agentx.cognition`` under the
    canonical boundary model, so this vocabulary is declared here and a
    placement test pins it to the A2.07 canonical tuple to prevent drift.
    The value records which strategy class *was executed*, as a measured fact;
    it is never a routing decision and never selects anything.
    """

    L0_CACHE = "L0_CACHE"
    L1_DIRECT = "L1_DIRECT"
    L2_COMPILED = "L2_COMPILED"
    L3_GUIDED = "L3_GUIDED"
    L4_PLANNED = "L4_PLANNED"
    L5_EXPLORATORY = "L5_EXPLORATORY"


class StrategyAggregationDimension(StrEnum):
    """Deterministic grouping dimension for strategy-performance records.

    Each dimension selects one identity attribute of the record. Records whose
    attribute is unset form their own ``None`` group, never a fabricated key.
    """

    LEVEL = "level"
    STRATEGY_NAME = "strategy_name"
    STRATEGY_VERSION = "strategy_version"
    SCOPE = "scope"
    ENVIRONMENT = "environment"


# --------------------------------------------------------------------------
# Validation helpers (construction-time; TypeError for wrong Python types,
# StrategyPerformanceValidationError for contract violations).
# --------------------------------------------------------------------------


def _has_control_characters(value: str) -> bool:
    return any(character < " " or character == "\x7f" for character in value)


def _validate_optional_label(value: object, *, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None, got {type(value).__name__}")
    if not value or value != value.strip():
        raise StrategyPerformanceValidationError(
            f"{field_name} must be non-empty and trimmed when provided"
        )
    if len(value) > max_length:
        raise StrategyPerformanceValidationError(
            f"{field_name} must not exceed {max_length} characters"
        )
    if _has_control_characters(value):
        raise StrategyPerformanceValidationError(
            f"{field_name} must not contain control characters"
        )
    return value


def _validate_record_id(value: object) -> StrategyPerformanceId:
    if not isinstance(value, StrategyPerformanceId):
        raise TypeError(f"record_id must be a StrategyPerformanceId, got {type(value).__name__}")
    return value


def _validate_optional_procedure_id(value: object) -> ProcedureId | None:
    if value is None:
        return None
    if not isinstance(value, ProcedureId):
        raise TypeError(f"procedure_id must be a ProcedureId or None, got {type(value).__name__}")
    return value


def _validate_optional_capability_id(value: object) -> CapabilityId | None:
    if value is None:
        return None
    if not isinstance(value, CapabilityId):
        raise TypeError(f"capability_id must be a CapabilityId or None, got {type(value).__name__}")
    return value


def _validate_optional_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, TaskId):
        raise TypeError(f"task_id must be a TaskId or None, got {type(value).__name__}")
    return value


def _validate_optional_episode_id(value: object) -> EpisodeId | None:
    if value is None:
        return None
    if not isinstance(value, EpisodeId):
        raise TypeError(f"episode_id must be an EpisodeId or None, got {type(value).__name__}")
    return value


def _validate_optional_uuid(value: object, *, field_name: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID or None, got {type(value).__name__}")
    if value.int == 0:
        raise StrategyPerformanceValidationError(f"{field_name} must not be the nil UUID")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise StrategyPerformanceValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_optional_timestamp(value: object, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _validate_timestamp(value, field_name=field_name)


def _validate_optional_revision(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise TypeError("procedure_revision must be a positive integer or None")
    return value


def _validate_measured_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} must be a finite number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise StrategyPerformanceValidationError(f"{field_name} must be finite")
    if number < 0:
        raise StrategyPerformanceValidationError(f"{field_name} must not be negative")
    return number


def _validate_optional_measured(value: object, *, field_name: str) -> float | None:
    if value is None:
        return None
    return _validate_measured_number(value, field_name=field_name)


def _validate_optional_cost_unit(value: object) -> str | None:
    return _validate_optional_label(value, field_name="cost_unit", max_length=_MAX_COST_UNIT_LENGTH)


# --------------------------------------------------------------------------
# Timestamp / payload parsing helpers.
# --------------------------------------------------------------------------


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise StrategyPerformanceDeserializationError(
            f"{field_name} is not a valid ISO-8601 timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StrategyPerformanceDeserializationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _parse_optional_timestamp(value: object, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _parse_timestamp(value, field_name=field_name)


def _parse_optional_verification(value: object) -> VerificationPayload | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("verification must be a JSON object or null")
    try:
        return VerificationPayload.from_dict(value)
    except EventValidationError as exc:
        raise StrategyPerformanceDeserializationError(
            f"verification payload is invalid: {exc}"
        ) from exc


def _parse_record_id(value: object) -> StrategyPerformanceId:
    if not isinstance(value, str):
        raise StrategyPerformanceDeserializationError(
            f"record_id must be a string, got {type(value).__name__}"
        )
    try:
        return StrategyPerformanceId.parse(value)
    except ValueError as exc:
        raise StrategyPerformanceDeserializationError(
            f"record_id is not a valid StrategyPerformanceId: {value!r}"
        ) from exc


def _parse_optional_id_value(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StrategyPerformanceDeserializationError(
            f"{field_name} must be a string or null, got {type(value).__name__}"
        )
    return value


def _parse_optional_procedure_id(value: object) -> ProcedureId | None:
    text = _parse_optional_id_value(value, field_name="procedure_id")
    if text is None:
        return None
    try:
        return ProcedureId.parse(text)
    except ValueError as exc:
        raise StrategyPerformanceDeserializationError(
            f"procedure_id is not a valid ProcedureId: {text!r}"
        ) from exc


def _parse_optional_capability_id(value: object) -> CapabilityId | None:
    text = _parse_optional_id_value(value, field_name="capability_id")
    if text is None:
        return None
    try:
        return CapabilityId.parse(text)
    except ValueError as exc:
        raise StrategyPerformanceDeserializationError(
            f"capability_id is not a valid CapabilityId: {text!r}"
        ) from exc


def _parse_optional_task_id(value: object) -> TaskId | None:
    text = _parse_optional_id_value(value, field_name="task_id")
    if text is None:
        return None
    try:
        return TaskId.parse(text)
    except ValueError as exc:
        raise StrategyPerformanceDeserializationError(
            f"task_id is not a valid TaskId: {text!r}"
        ) from exc


def _parse_optional_episode_id(value: object) -> EpisodeId | None:
    text = _parse_optional_id_value(value, field_name="episode_id")
    if text is None:
        return None
    try:
        return EpisodeId.parse(text)
    except ValueError as exc:
        raise StrategyPerformanceDeserializationError(
            f"episode_id is not a valid EpisodeId: {text!r}"
        ) from exc


def _parse_optional_uuid(value: object, *, field_name: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or null, got {type(value).__name__}")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise StrategyPerformanceDeserializationError(
            f"{field_name} is not a valid UUID: {value!r}"
        ) from exc
    if parsed.int == 0:
        raise StrategyPerformanceDeserializationError(f"{field_name} must not be the nil UUID")
    return parsed


def _parse_level(value: object) -> ExecutionStrategyLevel:
    if not isinstance(value, str):
        raise TypeError(f"level must be a string, got {type(value).__name__}")
    try:
        return ExecutionStrategyLevel(value)
    except ValueError as exc:
        raise StrategyPerformanceDeserializationError(
            "level must be one of "
            f"{[member.value for member in ExecutionStrategyLevel]}; got {value!r}"
        ) from exc


def _parse_outcome(value: object) -> CausalOutcome:
    if not isinstance(value, str):
        raise TypeError(f"outcome must be a string, got {type(value).__name__}")
    try:
        return CausalOutcome(value)
    except ValueError as exc:
        raise StrategyPerformanceDeserializationError(
            f"outcome must be one of {[member.value for member in CausalOutcome]}; got {value!r}"
        ) from exc


def _parse_failure_category(value: object) -> FailureCategory | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"failure_category must be a string or null, got {type(value).__name__}")
    try:
        return FailureCategory(value)
    except ValueError as exc:
        raise StrategyPerformanceDeserializationError(
            "failure_category must be one of "
            f"{[member.value for member in CANONICAL_FAILURE_CATEGORIES]}; got {value!r}"
        ) from exc


def _parse_optional_label(value: object, *, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StrategyPerformanceDeserializationError(
            f"{field_name} must be a string or null, got {type(value).__name__}"
        )
    return _validate_optional_label(value, field_name=field_name, max_length=max_length)


def _parse_measured_number(value: object, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StrategyPerformanceDeserializationError(f"{field_name} must be a JSON number or null")
    return _validate_measured_number(value, field_name=field_name)


def _parse_optional_revision(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise StrategyPerformanceDeserializationError(
            "procedure_revision must be a positive integer or null"
        )
    return value


@dataclass(frozen=True, slots=True)
class StrategyPerformanceRecord:
    """Immutable measured-fact record for one executed strategy attempt.

    Construction is the enforcement point for the A8.01 evidence rules (see
    the module docstring): a VERIFIED outcome is impossible without an embedded
    canonical passing :class:`~agentx.core.events.VerificationPayload`, and a
    non-verified outcome may not carry verification evidence.
    """

    record_id: StrategyPerformanceId
    level: ExecutionStrategyLevel
    outcome: CausalOutcome
    recorded_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    strategy_name: str | None = None
    strategy_version: str | None = None
    procedure_id: ProcedureId | None = None
    procedure_revision: int | None = None
    capability_id: CapabilityId | None = None
    task_id: TaskId | None = None
    episode_id: EpisodeId | None = None
    correlation_id: UUID | None = None
    scope: str | None = None
    environment: str | None = None
    verification: VerificationPayload | None = None
    failure_category: FailureCategory | None = None
    cost: float | None = None
    cost_unit: str | None = None
    latency_seconds: float | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", _validate_record_id(self.record_id))
        if not isinstance(self.level, ExecutionStrategyLevel):
            raise TypeError(
                f"level must be an ExecutionStrategyLevel, got {type(self.level).__name__}"
            )
        if not isinstance(self.outcome, CausalOutcome):
            raise TypeError(f"outcome must be a CausalOutcome, got {type(self.outcome).__name__}")
        object.__setattr__(
            self,
            "recorded_at",
            _validate_timestamp(self.recorded_at, field_name="recorded_at"),
        )
        object.__setattr__(
            self,
            "strategy_name",
            _validate_optional_label(
                self.strategy_name,
                field_name="strategy_name",
                max_length=_MAX_STRATEGY_NAME_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "strategy_version",
            _validate_optional_label(
                self.strategy_version,
                field_name="strategy_version",
                max_length=_MAX_STRATEGY_VERSION_LENGTH,
            ),
        )
        procedure_id = _validate_optional_procedure_id(self.procedure_id)
        revision = _validate_optional_revision(self.procedure_revision)
        if revision is not None and procedure_id is None:
            raise StrategyPerformanceValidationError("procedure_revision requires a procedure_id")
        object.__setattr__(self, "procedure_id", procedure_id)
        object.__setattr__(self, "procedure_revision", revision)
        object.__setattr__(
            self,
            "capability_id",
            _validate_optional_capability_id(self.capability_id),
        )
        object.__setattr__(self, "task_id", _validate_optional_task_id(self.task_id))
        object.__setattr__(self, "episode_id", _validate_optional_episode_id(self.episode_id))
        object.__setattr__(
            self,
            "correlation_id",
            _validate_optional_uuid(self.correlation_id, field_name="correlation_id"),
        )
        object.__setattr__(
            self,
            "scope",
            _validate_optional_label(self.scope, field_name="scope", max_length=_MAX_SCOPE_LENGTH),
        )
        object.__setattr__(
            self,
            "environment",
            _validate_optional_label(
                self.environment,
                field_name="environment",
                max_length=_MAX_ENVIRONMENT_LENGTH,
            ),
        )
        if self.verification is not None and not isinstance(self.verification, VerificationPayload):
            raise TypeError(
                "verification must be a VerificationPayload or None, "
                f"got {type(self.verification).__name__}"
            )
        if self.failure_category is not None and not isinstance(
            self.failure_category, FailureCategory
        ):
            raise TypeError(
                "failure_category must be a FailureCategory or None, "
                f"got {type(self.failure_category).__name__}"
            )
        cost = _validate_optional_measured(self.cost, field_name="cost")
        cost_unit = _validate_optional_cost_unit(self.cost_unit)
        if cost is not None and cost_unit is None:
            raise StrategyPerformanceValidationError("cost requires a cost_unit")
        if cost is None and cost_unit is not None:
            raise StrategyPerformanceValidationError("cost_unit requires a measured cost")
        object.__setattr__(self, "cost", cost)
        object.__setattr__(self, "cost_unit", cost_unit)
        object.__setattr__(
            self,
            "latency_seconds",
            _validate_optional_measured(self.latency_seconds, field_name="latency_seconds"),
        )
        started_at = _validate_optional_timestamp(self.started_at, field_name="started_at")
        ended_at = _validate_optional_timestamp(self.ended_at, field_name="ended_at")
        if ended_at is not None and started_at is None:
            raise StrategyPerformanceValidationError("ended_at requires started_at")
        if started_at is not None and ended_at is not None and ended_at < started_at:
            raise StrategyPerformanceValidationError("ended_at must not be earlier than started_at")
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "ended_at", ended_at)
        self._validate_outcome_evidence()

    def _validate_outcome_evidence(self) -> None:
        """Enforce canonical-verified-evidence rules (no fake success)."""
        if self.outcome is CausalOutcome.VERIFIED:
            if self.verification is None or not self.verification.passed:
                raise StrategyPerformanceValidationError(
                    "VERIFIED outcome requires an explicit passing verification"
                )
            if self.failure_category is not None:
                raise StrategyPerformanceValidationError(
                    "VERIFIED outcome must not carry a failure_category"
                )
            return
        if self.outcome is CausalOutcome.VERIFICATION_FAILED:
            if self.verification is None or self.verification.passed:
                raise StrategyPerformanceValidationError(
                    "VERIFICATION_FAILED outcome requires an explicit failing verification"
                )
            if self.failure_category is None:
                raise StrategyPerformanceValidationError(
                    "VERIFICATION_FAILED outcome requires a failure_category "
                    "(FailureCategory.UNKNOWN when the cause is not further classified)"
                )
            return
        if self.outcome is CausalOutcome.EXECUTION_FAILED:
            if self.verification is not None:
                raise StrategyPerformanceValidationError(
                    "EXECUTION_FAILED outcome must not carry fabricated verification evidence"
                )
            if self.failure_category is None:
                raise StrategyPerformanceValidationError(
                    "EXECUTION_FAILED outcome requires a failure_category "
                    "(FailureCategory.UNKNOWN when the cause is not further classified)"
                )
            return
        if self.verification is not None:
            raise StrategyPerformanceValidationError(
                f"{self.outcome.value} outcome must not carry fabricated verification evidence"
            )
        if self.failure_category is not None:
            raise StrategyPerformanceValidationError(
                f"{self.outcome.value} outcome must not carry a failure_category"
            )

    @property
    def verified(self) -> bool:
        """Whether this record carries an explicit canonical passing verification."""
        return self.outcome is CausalOutcome.VERIFIED

    @classmethod
    def create(
        cls,
        *,
        level: ExecutionStrategyLevel,
        outcome: CausalOutcome,
        record_id: StrategyPerformanceId | None = None,
        recorded_at: datetime | None = None,
        strategy_name: str | None = None,
        strategy_version: str | None = None,
        procedure_id: ProcedureId | None = None,
        procedure_revision: int | None = None,
        capability_id: CapabilityId | None = None,
        task_id: TaskId | None = None,
        episode_id: EpisodeId | None = None,
        correlation_id: UUID | None = None,
        scope: str | None = None,
        environment: str | None = None,
        verification: VerificationPayload | None = None,
        failure_category: FailureCategory | None = None,
        cost: float | None = None,
        cost_unit: str | None = None,
        latency_seconds: float | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> StrategyPerformanceRecord:
        """Create one record with fresh identity/time unless supplied."""
        return cls(
            record_id=StrategyPerformanceId.create() if record_id is None else record_id,
            level=level,
            outcome=outcome,
            recorded_at=datetime.now(UTC) if recorded_at is None else recorded_at,
            strategy_name=strategy_name,
            strategy_version=strategy_version,
            procedure_id=procedure_id,
            procedure_revision=procedure_revision,
            capability_id=capability_id,
            task_id=task_id,
            episode_id=episode_id,
            correlation_id=correlation_id,
            scope=scope,
            environment=environment,
            verification=verification,
            failure_category=failure_category,
            cost=cost,
            cost_unit=cost_unit,
            latency_seconds=latency_seconds,
            started_at=started_at,
            ended_at=ended_at,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "schema_version": STRATEGY_PERFORMANCE_SCHEMA_VERSION,
            "record_id": self.record_id.to_str(),
            "level": self.level.value,
            "outcome": self.outcome.value,
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "procedure_id": None if self.procedure_id is None else self.procedure_id.to_str(),
            "procedure_revision": self.procedure_revision,
            "capability_id": (None if self.capability_id is None else self.capability_id.to_str()),
            "task_id": None if self.task_id is None else self.task_id.to_str(),
            "episode_id": None if self.episode_id is None else self.episode_id.to_str(),
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
            "scope": self.scope,
            "environment": self.environment,
            "verification": (None if self.verification is None else self.verification.to_dict()),
            "failure_category": (
                None if self.failure_category is None else self.failure_category.value
            ),
            "cost": self.cost,
            "cost_unit": self.cost_unit,
            "latency_seconds": self.latency_seconds,
            "started_at": None if self.started_at is None else _format_timestamp(self.started_at),
            "ended_at": None if self.ended_at is None else _format_timestamp(self.ended_at),
            "recorded_at": _format_timestamp(self.recorded_at),
        }

    def to_json(self) -> str:
        """Serialize to deterministic JSON text without executable object hooks."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> StrategyPerformanceRecord:
        """Validate and reconstruct one canonical strategy-performance record."""
        if "schema_version" not in raw:
            raise StrategyPerformanceDeserializationError(
                "strategy-performance record missing required field: schema_version"
            )
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise StrategyPerformanceDeserializationError("schema_version must be an integer")
        if version != STRATEGY_PERFORMANCE_SCHEMA_VERSION:
            raise UnsupportedStrategyPerformanceSchemaVersionError(
                f"unsupported strategy-performance schema version {version}; "
                f"supported version is {STRATEGY_PERFORMANCE_SCHEMA_VERSION}"
            )

        actual = set(raw)
        missing = _PERFORMANCE_FIELDS - actual
        unknown = actual - _PERFORMANCE_FIELDS
        if missing:
            raise StrategyPerformanceDeserializationError(
                f"strategy-performance record missing required fields: {sorted(missing)}"
            )
        if unknown:
            raise StrategyPerformanceDeserializationError(
                f"strategy-performance record contains unknown fields: {sorted(unknown)}"
            )

        return cls(
            record_id=_parse_record_id(raw["record_id"]),
            level=_parse_level(raw["level"]),
            outcome=_parse_outcome(raw["outcome"]),
            recorded_at=_parse_timestamp(raw["recorded_at"], field_name="recorded_at"),
            strategy_name=_parse_optional_label(
                raw["strategy_name"],
                field_name="strategy_name",
                max_length=_MAX_STRATEGY_NAME_LENGTH,
            ),
            strategy_version=_parse_optional_label(
                raw["strategy_version"],
                field_name="strategy_version",
                max_length=_MAX_STRATEGY_VERSION_LENGTH,
            ),
            procedure_id=_parse_optional_procedure_id(raw["procedure_id"]),
            procedure_revision=_parse_optional_revision(raw["procedure_revision"]),
            capability_id=_parse_optional_capability_id(raw["capability_id"]),
            task_id=_parse_optional_task_id(raw["task_id"]),
            episode_id=_parse_optional_episode_id(raw["episode_id"]),
            correlation_id=_parse_optional_uuid(raw["correlation_id"], field_name="correlation_id"),
            scope=_parse_optional_label(
                raw["scope"], field_name="scope", max_length=_MAX_SCOPE_LENGTH
            ),
            environment=_parse_optional_label(
                raw["environment"],
                field_name="environment",
                max_length=_MAX_ENVIRONMENT_LENGTH,
            ),
            verification=_parse_optional_verification(raw["verification"]),
            failure_category=_parse_failure_category(raw["failure_category"]),
            cost=_parse_measured_number(raw["cost"], field_name="cost"),
            cost_unit=_parse_optional_label(
                raw["cost_unit"],
                field_name="cost_unit",
                max_length=_MAX_COST_UNIT_LENGTH,
            ),
            latency_seconds=_parse_measured_number(
                raw["latency_seconds"], field_name="latency_seconds"
            ),
            started_at=_parse_optional_timestamp(raw["started_at"], field_name="started_at"),
            ended_at=_parse_optional_timestamp(raw["ended_at"], field_name="ended_at"),
        )

    @classmethod
    def from_json(cls, text: str) -> StrategyPerformanceRecord:
        """Validate and reconstruct canonical strategy-performance JSON."""
        if not isinstance(text, str):
            raise TypeError(
                f"strategy-performance JSON must be a string, got {type(text).__name__}"
            )
        try:
            decoded = json.loads(text)
        except ValueError as exc:
            raise StrategyPerformanceDeserializationError(
                f"strategy-performance JSON is malformed: {exc}"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise StrategyPerformanceDeserializationError(
                "strategy-performance JSON root must be an object"
            )
        return cls.from_dict(decoded)


# --------------------------------------------------------------------------
# Deterministic aggregation primitives (A8.01; comparison is A8.02+).
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MeasuredValueAggregate:
    """Deterministic summary of one measured quantity over a record set.

    Only records that carry the measured value contribute; records with the
    value unset are counted nowhere in this summary (they remain visible in
    ``attempts`` of the containing aggregate). ``mean`` is ``None`` when no
    measured value exists; it is never a fabricated number.
    """

    measured_count: int
    total: float
    minimum: float | None
    maximum: float | None
    mean: float | None


@dataclass(frozen=True, slots=True)
class StrategyPerformanceAggregate:
    """Descriptive statistics over a set of validated performance records.

    Deterministic by construction: counts over canonical closed vocabularies
    include every member in canonical order (zero counts included), measured
    summaries never invent values, and per-unit cost summaries are kept apart
    because units are not comparable. No comparison, ranking, or selection is
    performed here.
    """

    attempts: int
    outcomes: tuple[tuple[CausalOutcome, int], ...]
    failure_categories: tuple[tuple[FailureCategory, int], ...]
    latency_seconds: MeasuredValueAggregate
    costs: tuple[tuple[str, MeasuredValueAggregate], ...]


@dataclass(frozen=True, slots=True)
class StrategyPerformanceGroup:
    """One deterministic group of an A8.01 aggregation.

    ``key`` is the group's identity attribute value, or ``None`` for the
    explicit "attribute unset" group. Groups with equal keys are merged.
    """

    key: str | None
    aggregate: StrategyPerformanceAggregate


def _summarize_measured(values: Iterable[float]) -> MeasuredValueAggregate:
    collected = tuple(values)
    if not collected:
        return MeasuredValueAggregate(
            measured_count=0,
            total=0.0,
            minimum=None,
            maximum=None,
            mean=None,
        )
    total = sum(collected)
    return MeasuredValueAggregate(
        measured_count=len(collected),
        total=total,
        minimum=min(collected),
        maximum=max(collected),
        mean=total / len(collected),
    )


def _as_records(
    records: Iterable[StrategyPerformanceRecord],
) -> tuple[StrategyPerformanceRecord, ...]:
    """Validate the input iterable and return an immutable ordered tuple."""
    collected: list[StrategyPerformanceRecord] = []
    for record in records:
        if not isinstance(record, StrategyPerformanceRecord):
            raise TypeError(
                "records must contain StrategyPerformanceRecord values, "
                f"got {type(record).__name__}"
            )
        collected.append(record)
    return tuple(collected)


def _outcome_counts(
    records: Iterable[StrategyPerformanceRecord],
) -> tuple[tuple[CausalOutcome, int], ...]:
    counts = {outcome: 0 for outcome in CausalOutcome}
    for record in records:
        counts[record.outcome] += 1
    return tuple((outcome, counts[outcome]) for outcome in CausalOutcome)


def _failure_category_counts(
    records: Iterable[StrategyPerformanceRecord],
) -> tuple[tuple[FailureCategory, int], ...]:
    counts = {category: 0 for category in CANONICAL_FAILURE_CATEGORIES}
    for record in records:
        if record.failure_category is not None:
            counts[record.failure_category] += 1
    return tuple((category, counts[category]) for category in CANONICAL_FAILURE_CATEGORIES)


def _cost_summaries(
    records: Iterable[StrategyPerformanceRecord],
) -> tuple[tuple[str, MeasuredValueAggregate], ...]:
    per_unit: dict[str, list[float]] = {}
    for record in records:
        if record.cost is not None and record.cost_unit is not None:
            per_unit.setdefault(record.cost_unit, []).append(record.cost)
    return tuple((unit, _summarize_measured(values)) for unit, values in sorted(per_unit.items()))


def aggregate_strategy_records(
    records: Iterable[StrategyPerformanceRecord],
) -> StrategyPerformanceAggregate:
    """Aggregate validated records into deterministic descriptive statistics.

    ``records`` may be any iterable; aggregation order does not affect the
    result. Empty input yields the deterministic zero aggregate.
    """
    collected = _as_records(records)
    return StrategyPerformanceAggregate(
        attempts=len(collected),
        outcomes=_outcome_counts(collected),
        failure_categories=_failure_category_counts(collected),
        latency_seconds=_summarize_measured(
            record.latency_seconds for record in collected if record.latency_seconds is not None
        ),
        costs=_cost_summaries(collected),
    )


def _dimension_key(
    record: StrategyPerformanceRecord, dimension: StrategyAggregationDimension
) -> str | None:
    if dimension is StrategyAggregationDimension.LEVEL:
        return record.level.value
    if dimension is StrategyAggregationDimension.STRATEGY_NAME:
        return record.strategy_name
    if dimension is StrategyAggregationDimension.STRATEGY_VERSION:
        return record.strategy_version
    if dimension is StrategyAggregationDimension.SCOPE:
        return record.scope
    return record.environment


def _group_sort_key(
    key: str | None, dimension: StrategyAggregationDimension
) -> tuple[int, int, str]:
    """Deterministic group ordering.

    The ``None`` (attribute unset) group sorts first when present; ``LEVEL``
    groups then follow canonical L0-L5 order and every other dimension sorts
    ascending lexicographically.
    """
    if key is None:
        return (0, 0, "")
    if dimension is StrategyAggregationDimension.LEVEL:
        canonical = tuple(ExecutionStrategyLevel)
        try:
            order = canonical.index(ExecutionStrategyLevel(key))
        except ValueError:
            order = len(canonical)
        return (1, order, key)
    return (1, 0, key)


def group_strategy_records(
    records: Iterable[StrategyPerformanceRecord],
    *,
    dimension: StrategyAggregationDimension,
) -> tuple[StrategyPerformanceGroup, ...]:
    """Split records into deterministic per-key aggregates along one dimension.

    Every group is keyed by an observed attribute value - nothing is
    invented. The ``None`` (attribute unset) group appears first when present,
    ``LEVEL`` groups follow in canonical L0-L5 order, and other dimensions
    sort ascending lexicographically. The same key always yields one merged
    group regardless of input order.
    """
    if not isinstance(dimension, StrategyAggregationDimension):
        raise TypeError(
            f"dimension must be a StrategyAggregationDimension, got {type(dimension).__name__}"
        )
    collected = _as_records(records)

    grouped: dict[str | None, list[StrategyPerformanceRecord]] = {}
    for record in collected:
        grouped.setdefault(_dimension_key(record, dimension), []).append(record)

    groups = tuple(
        StrategyPerformanceGroup(key=key, aggregate=aggregate_strategy_records(members))
        for key, members in grouped.items()
    )
    return tuple(sorted(groups, key=lambda group: _group_sort_key(group.key, dimension)))
