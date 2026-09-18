"""Adaptive strategy optimization for AgentX M14.

This module consumes canonical verified execution evidence and explicit user
corrections. It can recommend among already-permitted ExecutionLevel values,
but it owns no Permission, RiskLevel, ActionGate, budget, confirmation,
verification, emergency-stop, capability, procedure-promotion, or code-loading
authority.

All durable state is JSON-compatible EventJournal data. No pickle, eval, exec,
dynamic import, or executable learned artifact is used.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from agentx.cognition.router import CANONICAL_EXECUTION_LEVELS, ExecutionLevel
from agentx.core.events import (
    CURRENT_EVENT_SCHEMA_VERSION,
    Event,
    EventType,
    ObservationPayload,
)
from agentx.core.reuse_efficiency import ExecutionEvidenceOutcome
from agentx.core.user_preferences import (
    PreferenceKey,
    PreferenceSource,
    UserPreference,
)
from agentx.infrastructure.event_journal import EventJournal
from agentx.strategy_performance_evidence import StrategyPerformanceEvidence

__all__ = [
    "AdaptiveOptimizationError",
    "AdaptiveStrategySelector",
    "BanditConfig",
    "BanditDecision",
    "BanditExperimentReport",
    "ContextualBanditExperiment",
    "DeterministicStrategyBaseline",
    "EnvironmentKey",
    "ExplicitCorrectionDataset",
    "OfflinePolicyEvaluator",
    "OptimizationPolicy",
    "OptimizationPolicyRuntime",
    "OptimizationPolicyStore",
    "PolicyEvaluationDisposition",
    "PolicyEvaluationReport",
    "PolicyLifecycle",
    "PreferenceRankingExperiment",
    "PreferenceRankingReport",
    "PreferenceScoringModel",
    "StrategyContext",
    "StrategyOutcomeLedger",
    "StrategyOutcomeRecord",
    "StrategySelection",
    "StrategyStatistics",
    "StrategyStatisticsEngine",
    "UserCorrectionExample",
]

_MAX_TEXT: Final[int] = 256
_MAX_HISTORY_READ: Final[int] = 512
_MAX_HISTORY_SCAN: Final[int] = 8192
_HISTORY_PAGE: Final[int] = 128
_OUTCOME_SOURCE: Final[str] = "agentx.adaptive_optimization.outcome"
_OUTCOME_CONTRACT: Final[str] = "agentx.strategy_outcome.v1"
_POLICY_SOURCE: Final[str] = "agentx.adaptive_optimization.policy"
_POLICY_CONTRACT: Final[str] = "agentx.optimization_policy.v1"
_OUTCOME_SCHEMA: Final[int] = 1
_POLICY_SCHEMA: Final[int] = 1


class AdaptiveOptimizationError(ValueError):
    """Typed failure for invalid or unsafe M14 optimization data."""


def _text(value: object, *, name: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise AdaptiveOptimizationError(f"{name} must be a string")
    if not value or value != value.strip():
        raise AdaptiveOptimizationError(f"{name} must be non-empty and trimmed")
    if len(value) > _MAX_TEXT:
        raise AdaptiveOptimizationError(f"{name} exceeds {_MAX_TEXT} characters")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise AdaptiveOptimizationError(f"{name} contains forbidden control characters")
    return value


def _aware(value: object, *, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise AdaptiveOptimizationError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise AdaptiveOptimizationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: object, *, name: str) -> datetime:
    if not isinstance(value, str):
        raise AdaptiveOptimizationError(f"{name} must be an ISO-8601 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AdaptiveOptimizationError(f"{name} is not valid ISO-8601") from exc
    return _aware(parsed, name=name)


def _uuid(value: object, *, name: str) -> UUID:
    if not isinstance(value, UUID):
        raise AdaptiveOptimizationError(f"{name} must be a UUID")
    if value.int == 0:
        raise AdaptiveOptimizationError(f"{name} must not be nil")
    return value


def _parse_uuid(value: object, *, name: str) -> UUID:
    if not isinstance(value, str):
        raise AdaptiveOptimizationError(f"{name} must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise AdaptiveOptimizationError(f"{name} is not a valid UUID") from exc
    return _uuid(parsed, name=name)


def _counter(value: object, *, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise AdaptiveOptimizationError(f"{name} must be an integer >= {minimum}")
    return value


def _confidence(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal):
        raise AdaptiveOptimizationError("predicted_confidence must be Decimal or None")
    if not value.is_finite() or value < 0 or value > 1:
        raise AdaptiveOptimizationError("predicted_confidence must be finite in [0, 1]")
    return value


def _parse_decimal(value: object, *, name: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AdaptiveOptimizationError(f"{name} must be a decimal string or null")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise AdaptiveOptimizationError(f"{name} is invalid") from exc
    if not parsed.is_finite():
        raise AdaptiveOptimizationError(f"{name} must be finite")
    return parsed


def _level(value: object, *, name: str) -> ExecutionLevel:
    if isinstance(value, ExecutionLevel):
        return value
    if not isinstance(value, str):
        raise AdaptiveOptimizationError(f"{name} must be an ExecutionLevel")
    try:
        return ExecutionLevel(value)
    except ValueError as exc:
        raise AdaptiveOptimizationError(f"{name} is not a canonical ExecutionLevel") from exc


def _levels(values: Sequence[ExecutionLevel], *, name: str) -> tuple[ExecutionLevel, ...]:
    result = tuple(values)
    if not result:
        raise AdaptiveOptimizationError(f"{name} must not be empty")
    if any(not isinstance(item, ExecutionLevel) for item in result):
        raise AdaptiveOptimizationError(f"{name} must contain ExecutionLevel values")
    if len(result) != len(set(result)):
        raise AdaptiveOptimizationError(f"{name} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class EnvironmentKey:
    """Opaque M10 environment identity plus an optional trusted revision token."""

    environment_id: str | None
    revision: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "environment_id",
            _text(self.environment_id, name="environment_id", optional=True),
        )
        object.__setattr__(self, "revision", _text(self.revision, name="revision", optional=True))
        if self.environment_id is None and self.revision is not None:
            raise AdaptiveOptimizationError("environment revision requires environment identity")

    def to_dict(self) -> dict[str, object]:
        return {"environment_id": self.environment_id, "revision": self.revision}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EnvironmentKey:
        if set(raw) != {"environment_id", "revision"}:
            raise AdaptiveOptimizationError("environment key has missing or unknown fields")
        environment_id = raw["environment_id"]
        revision = raw["revision"]
        if environment_id is not None and not isinstance(environment_id, str):
            raise AdaptiveOptimizationError("environment_id must be a string or null")
        if revision is not None and not isinstance(revision, str):
            raise AdaptiveOptimizationError("revision must be a string or null")
        return cls(environment_id=environment_id, revision=revision)


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyContext:
    """Non-authoritative context used only to group/select permitted strategies."""

    environment: EnvironmentKey
    task_family: str | None
    captured_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.environment, EnvironmentKey):
            raise AdaptiveOptimizationError("environment must be EnvironmentKey")
        object.__setattr__(
            self,
            "task_family",
            _text(self.task_family, name="task_family", optional=True),
        )
        object.__setattr__(
            self,
            "captured_at",
            _aware(self.captured_at, name="captured_at"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "environment": self.environment.to_dict(),
            "task_family": self.task_family,
            "captured_at": _format_time(self.captured_at),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> StrategyContext:
        if set(raw) != {"environment", "task_family", "captured_at"}:
            raise AdaptiveOptimizationError("strategy context has missing or unknown fields")
        environment = raw["environment"]
        if not isinstance(environment, Mapping):
            raise AdaptiveOptimizationError("environment must be an object")
        task_family = raw["task_family"]
        if task_family is not None and not isinstance(task_family, str):
            raise AdaptiveOptimizationError("task_family must be a string or null")
        return cls(
            environment=EnvironmentKey.from_dict(environment),
            task_family=task_family,
            captured_at=_parse_time(raw["captured_at"], name="captured_at"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyOutcomeRecord:
    """Durable context attached to one canonical strategy-performance record."""

    performance: StrategyPerformanceEvidence
    context: StrategyContext
    attempt_count: int = 1
    predicted_confidence: Decimal | None = None
    schema_version: int = _OUTCOME_SCHEMA

    def __post_init__(self) -> None:
        if not isinstance(self.performance, StrategyPerformanceEvidence):
            raise AdaptiveOptimizationError(
                "performance must be StrategyPerformanceEvidence"
            )
        if not isinstance(self.context, StrategyContext):
            raise AdaptiveOptimizationError("context must be StrategyContext")
        _counter(self.attempt_count, name="attempt_count", minimum=1)
        object.__setattr__(
            self,
            "predicted_confidence",
            _confidence(self.predicted_confidence),
        )
        if type(self.schema_version) is not int or self.schema_version != _OUTCOME_SCHEMA:
            raise AdaptiveOptimizationError("unsupported strategy-outcome schema")
        if self.performance.observed_at < self.context.captured_at:
            raise AdaptiveOptimizationError(
                "context must not be captured after the execution outcome"
            )

    @property
    def strategy(self) -> ExecutionLevel:
        return self.performance.execution_level

    @property
    def verified_success(self) -> bool:
        return (
            self.performance.outcome is ExecutionEvidenceOutcome.VERIFIED_SUCCESS
            and self.performance.verification_passed is True
        )

    @property
    def verified_failure(self) -> bool:
        return self.performance.outcome is ExecutionEvidenceOutcome.FAILED

    @property
    def verification_known(self) -> bool:
        return self.verified_success or self.verified_failure

    @property
    def record_event_id(self) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            f"agentx.strategy-outcome:{self.performance.evidence_id}",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "performance": self.performance.to_dict(),
            "context": self.context.to_dict(),
            "attempt_count": self.attempt_count,
            "predicted_confidence": (
                None
                if self.predicted_confidence is None
                else str(self.predicted_confidence)
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> StrategyOutcomeRecord:
        expected = {
            "schema_version",
            "performance",
            "context",
            "attempt_count",
            "predicted_confidence",
        }
        if set(raw) != expected:
            raise AdaptiveOptimizationError(
                "strategy outcome has missing or unknown fields"
            )
        performance = raw["performance"]
        context = raw["context"]
        if not isinstance(performance, Mapping) or not isinstance(context, Mapping):
            raise AdaptiveOptimizationError(
                "performance and context must be objects"
            )
        confidence = _parse_decimal(
            raw["predicted_confidence"],
            name="predicted_confidence",
        )
        return cls(
            schema_version=_counter(raw["schema_version"], name="schema_version"),
            performance=StrategyPerformanceEvidence.from_dict(performance),
            context=StrategyContext.from_dict(context),
            attempt_count=_counter(
                raw["attempt_count"],
                name="attempt_count",
                minimum=1,
            ),
            predicted_confidence=confidence,
        )


@dataclass(frozen=True, slots=True)
class StrategyOutcomeLedger:
    """Append/replay strategy outcomes through the canonical EventJournal."""

    journal: EventJournal

    def __post_init__(self) -> None:
        if not isinstance(self.journal, EventJournal):
            raise AdaptiveOptimizationError("journal must be EventJournal")

    def append(self, record: StrategyOutcomeRecord) -> int:
        if not isinstance(record, StrategyOutcomeRecord):
            raise AdaptiveOptimizationError("record must be StrategyOutcomeRecord")
        event = Event(
            event_id=record.record_event_id,
            event_type=EventType.OBSERVATION_RECORDED,
            timestamp=record.performance.observed_at,
            schema_version=CURRENT_EVENT_SCHEMA_VERSION,
            source=_OUTCOME_SOURCE,
            correlation_id=record.performance.correlation_id,
            causation_id=record.performance.evidence_id,
            task_id=record.performance.task_id.to_str(),
            payload=ObservationPayload(value=record.to_dict()),
            metadata={"contract": _OUTCOME_CONTRACT},
        )
        return self.journal.append(event)

    def read(
        self,
        *,
        after_sequence: int = 0,
        limit: int = _MAX_HISTORY_READ,
        max_scan: int = _MAX_HISTORY_SCAN,
    ) -> tuple[StrategyOutcomeRecord, ...]:
        _counter(after_sequence, name="after_sequence")
        if not 1 <= limit <= _MAX_HISTORY_READ:
            raise AdaptiveOptimizationError("limit is outside the bounded range")
        if not 1 <= max_scan <= _MAX_HISTORY_SCAN:
            raise AdaptiveOptimizationError("max_scan is outside the bounded range")

        cursor = after_sequence
        scanned = 0
        results: list[StrategyOutcomeRecord] = []
        while scanned < max_scan and len(results) < limit:
            batch = self.journal.read(
                after_sequence=cursor,
                limit=min(_HISTORY_PAGE, max_scan - scanned),
            )
            if not batch:
                break
            scanned += len(batch)
            cursor = batch[-1].sequence
            for entry in batch:
                event = entry.event
                if (
                    event.source != _OUTCOME_SOURCE
                    or event.metadata.get("contract") != _OUTCOME_CONTRACT
                ):
                    continue
                if event.event_type is not EventType.OBSERVATION_RECORDED:
                    raise AdaptiveOptimizationError(
                        "strategy outcome event has the wrong event type"
                    )
                if not isinstance(event.payload, ObservationPayload):
                    raise AdaptiveOptimizationError(
                        "strategy outcome event has the wrong payload type"
                    )
                value = event.payload.value
                if not isinstance(value, Mapping):
                    raise AdaptiveOptimizationError(
                        "strategy outcome payload must be an object"
                    )
                record = StrategyOutcomeRecord.from_dict(value)
                if record.record_event_id != event.event_id:
                    raise AdaptiveOptimizationError(
                        "strategy outcome identity mismatch"
                    )
                if record.performance.correlation_id != event.correlation_id:
                    raise AdaptiveOptimizationError(
                        "strategy outcome correlation mismatch"
                    )
                if record.performance.task_id.to_str() != event.task_id:
                    raise AdaptiveOptimizationError("strategy outcome task mismatch")
                results.append(record)
                if len(results) >= limit:
                    break
        return tuple(results)


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyStatistics:
    """Truthful aggregate; missing latency/cost remains unknown, never zero."""

    strategy: ExecutionLevel
    samples: int
    verified_successes: int
    verified_failures: int
    unverified: int
    success_rate: Decimal | None
    latency_samples: int
    mean_latency_microseconds: Decimal | None
    cost_samples: int
    mean_external_cost: Decimal | None
    cost_unit: str | None
    model_calls_total: int
    attempts_total: int
    stale_excluded: int

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, ExecutionLevel):
            raise AdaptiveOptimizationError("strategy must be ExecutionLevel")
        for name in (
            "samples",
            "verified_successes",
            "verified_failures",
            "unverified",
            "latency_samples",
            "cost_samples",
            "model_calls_total",
            "attempts_total",
            "stale_excluded",
        ):
            _counter(getattr(self, name), name=name)
        if (
            self.verified_successes + self.verified_failures + self.unverified
            != self.samples
        ):
            raise AdaptiveOptimizationError("statistics sample partition is inconsistent")
        if self.success_rate is not None and not (
            Decimal(0) <= self.success_rate <= Decimal(1)
        ):
            raise AdaptiveOptimizationError("success_rate must be in [0, 1]")


@dataclass(frozen=True, slots=True, kw_only=True)
class ScopedStrategyStatistics:
    scope: str | None
    revision: str | None
    statistics: StrategyStatistics


class StrategyStatisticsEngine:
    """Deterministic aggregation over immutable outcome records."""

    __slots__ = ()

    @staticmethod
    def _fresh(
        records: Sequence[StrategyOutcomeRecord],
        *,
        now: datetime,
        max_age: timedelta | None,
    ) -> tuple[tuple[StrategyOutcomeRecord, ...], int]:
        current = _aware(now, name="now")
        if max_age is not None and (
            not isinstance(max_age, timedelta) or max_age < timedelta(0)
        ):
            raise AdaptiveOptimizationError("max_age must be non-negative or None")
        fresh: list[StrategyOutcomeRecord] = []
        stale = 0
        for record in records:
            if max_age is not None and current - record.performance.observed_at > max_age:
                stale += 1
            else:
                fresh.append(record)
        return tuple(fresh), stale

    def summarize(
        self,
        records: Sequence[StrategyOutcomeRecord],
        *,
        strategy: ExecutionLevel,
        now: datetime,
        max_age: timedelta | None = None,
    ) -> StrategyStatistics:
        if not isinstance(strategy, ExecutionLevel):
            raise AdaptiveOptimizationError("strategy must be ExecutionLevel")
        fresh, stale = self._fresh(records, now=now, max_age=max_age)
        selected = tuple(item for item in fresh if item.strategy is strategy)
        successes = sum(item.verified_success for item in selected)
        failures = sum(item.verified_failure for item in selected)
        unverified = len(selected) - successes - failures
        known = successes + failures
        success_rate = (
            None
            if known == 0
            else Decimal(successes) / Decimal(known)
        )

        latency_values = [
            Decimal(
                item.performance.elapsed // timedelta(microseconds=1)
            )
            for item in selected
            if item.performance.elapsed is not None
        ]
        mean_latency = (
            None
            if not latency_values
            else sum(latency_values, Decimal(0)) / Decimal(len(latency_values))
        )

        known_costs = [
            (item.performance.external_cost, item.performance.cost_unit)
            for item in selected
            if item.performance.external_cost is not None
        ]
        cost_units = {unit for _, unit in known_costs}
        if len(cost_units) == 1:
            cost_unit = next(iter(cost_units))
            cost_values = [
                value for value, _ in known_costs if value is not None
            ]
            mean_cost = (
                None
                if not cost_values
                else sum(cost_values, Decimal(0)) / Decimal(len(cost_values))
            )
        else:
            cost_unit = None
            mean_cost = None

        return StrategyStatistics(
            strategy=strategy,
            samples=len(selected),
            verified_successes=successes,
            verified_failures=failures,
            unverified=unverified,
            success_rate=success_rate,
            latency_samples=len(latency_values),
            mean_latency_microseconds=mean_latency,
            cost_samples=len(known_costs),
            mean_external_cost=mean_cost,
            cost_unit=cost_unit,
            model_calls_total=sum(item.performance.model_calls for item in selected),
            attempts_total=sum(item.attempt_count for item in selected),
            stale_excluded=stale,
        )

    def by_environment(
        self,
        records: Sequence[StrategyOutcomeRecord],
        *,
        strategy: ExecutionLevel,
        now: datetime,
        max_age: timedelta | None = None,
    ) -> tuple[ScopedStrategyStatistics, ...]:
        fresh, _ = self._fresh(records, now=now, max_age=max_age)
        groups: dict[EnvironmentKey, list[StrategyOutcomeRecord]] = defaultdict(list)
        for record in fresh:
            if record.strategy is strategy:
                groups[record.context.environment].append(record)
        ordered = sorted(
            groups.items(),
            key=lambda item: (
                item[0].environment_id or "",
                item[0].revision or "",
            ),
        )
        return tuple(
            ScopedStrategyStatistics(
                scope=key.environment_id,
                revision=key.revision,
                statistics=self.summarize(
                    values,
                    strategy=strategy,
                    now=now,
                    max_age=None,
                ),
            )
            for key, values in ordered
        )

    def by_task_family(
        self,
        records: Sequence[StrategyOutcomeRecord],
        *,
        strategy: ExecutionLevel,
        now: datetime,
        max_age: timedelta | None = None,
    ) -> tuple[ScopedStrategyStatistics, ...]:
        fresh, _ = self._fresh(records, now=now, max_age=max_age)
        groups: dict[str | None, list[StrategyOutcomeRecord]] = defaultdict(list)
        for record in fresh:
            if record.strategy is strategy:
                groups[record.context.task_family].append(record)
        ordered = sorted(groups.items(), key=lambda item: item[0] or "")
        return tuple(
            ScopedStrategyStatistics(
                scope=family,
                revision=None,
                statistics=self.summarize(
                    values,
                    strategy=strategy,
                    now=now,
                    max_age=None,
                ),
            )
            for family, values in ordered
        )

    def calibration(
        self,
        records: Sequence[StrategyOutcomeRecord],
        *,
        minimum_samples: int = 5,
    ) -> CalibrationReport:
        _counter(minimum_samples, name="minimum_samples", minimum=1)
        usable = tuple(
            item
            for item in records
            if item.predicted_confidence is not None and item.verification_known
        )
        if len(usable) < minimum_samples:
            return CalibrationReport(
                samples=len(usable),
                brier_score=None,
                mean_confidence=None,
                empirical_success_rate=None,
                sufficient=False,
            )
        confidences = [
            item.predicted_confidence
            for item in usable
            if item.predicted_confidence is not None
        ]
        outcomes = [
            Decimal(1) if item.verified_success else Decimal(0)
            for item in usable
        ]
        brier = sum(
            (confidence - outcome) ** 2
            for confidence, outcome in zip(confidences, outcomes, strict=True)
        ) / Decimal(len(usable))
        return CalibrationReport(
            samples=len(usable),
            brier_score=brier,
            mean_confidence=sum(confidences, Decimal(0)) / Decimal(len(usable)),
            empirical_success_rate=sum(outcomes, Decimal(0)) / Decimal(len(usable)),
            sufficient=True,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CalibrationReport:
    samples: int
    brier_score: Decimal | None
    mean_confidence: Decimal | None
    empirical_success_rate: Decimal | None
    sufficient: bool


class SelectionSource(StrEnum):
    BASELINE = "baseline"
    BANDIT = "bandit"
    PREFERENCE = "preference"
    POLICY_FALLBACK = "policy_fallback"


@dataclass(frozen=True, slots=True)
class StrategySelection:
    level: ExecutionLevel
    source: SelectionSource
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.level, ExecutionLevel):
            raise AdaptiveOptimizationError("selection level must be ExecutionLevel")
        if not isinstance(self.source, SelectionSource):
            raise AdaptiveOptimizationError("selection source must be SelectionSource")
        _text(self.reason_code, name="reason_code")


class AdaptiveStrategySelector(Protocol):
    def choose(
        self,
        *,
        context: StrategyContext,
        available: tuple[ExecutionLevel, ...],
        history: tuple[StrategyOutcomeRecord, ...],
    ) -> StrategySelection: ...


@dataclass(frozen=True, slots=True)
class DeterministicStrategyBaseline:
    """Fixed non-learning baseline using canonical execution-level ordering."""

    priority: tuple[ExecutionLevel, ...] = CANONICAL_EXECUTION_LEVELS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "priority",
            _levels(self.priority, name="priority"),
        )

    def choose(
        self,
        *,
        context: StrategyContext,
        available: tuple[ExecutionLevel, ...],
        history: tuple[StrategyOutcomeRecord, ...] = (),
    ) -> StrategySelection:
        if not isinstance(context, StrategyContext):
            raise AdaptiveOptimizationError("context must be StrategyContext")
        allowed = _levels(available, name="available")
        for level in self.priority:
            if level in allowed:
                return StrategySelection(
                    level=level,
                    source=SelectionSource.BASELINE,
                    reason_code="fixed-priority",
                )
        raise AdaptiveOptimizationError("no available strategy intersects baseline")


@dataclass(frozen=True, slots=True, kw_only=True)
class BanditConfig:
    permitted: tuple[ExecutionLevel, ...] = CANONICAL_EXECUTION_LEVELS
    seed: int = 0
    exploration_numerator: int = 1
    exploration_denominator: int = 10
    minimum_arm_samples: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "permitted",
            _levels(self.permitted, name="permitted"),
        )
        if type(self.seed) is not int:
            raise AdaptiveOptimizationError("seed must be an integer")
        _counter(
            self.exploration_numerator,
            name="exploration_numerator",
        )
        _counter(
            self.exploration_denominator,
            name="exploration_denominator",
            minimum=1,
        )
        if self.exploration_numerator > self.exploration_denominator:
            raise AdaptiveOptimizationError("exploration fraction must be <= 1")
        _counter(
            self.minimum_arm_samples,
            name="minimum_arm_samples",
            minimum=1,
        )


@dataclass(frozen=True, slots=True)
class ArmEstimate:
    level: ExecutionLevel
    verified_samples: int
    success_rate: Decimal | None


@dataclass(frozen=True, slots=True)
class BanditDecision:
    selection: StrategySelection
    explored: bool
    estimates: tuple[ArmEstimate, ...]


@dataclass(frozen=True, slots=True)
class SafeContextualBandit:
    """Seeded bounded selector over caller-supplied, already-permitted levels."""

    config: BanditConfig
    baseline: DeterministicStrategyBaseline = DeterministicStrategyBaseline()

    def __post_init__(self) -> None:
        if not isinstance(self.config, BanditConfig):
            raise AdaptiveOptimizationError("config must be BanditConfig")
        if not isinstance(self.baseline, DeterministicStrategyBaseline):
            raise AdaptiveOptimizationError(
                "baseline must be DeterministicStrategyBaseline"
            )

    @staticmethod
    def _matches(record: StrategyOutcomeRecord, context: StrategyContext) -> bool:
        return (
            record.context.environment == context.environment
            and record.context.task_family == context.task_family
        )

    def decide(
        self,
        *,
        context: StrategyContext,
        available: tuple[ExecutionLevel, ...],
        history: tuple[StrategyOutcomeRecord, ...],
    ) -> BanditDecision:
        allowed_input = _levels(available, name="available")
        allowed = tuple(
            level
            for level in allowed_input
            if level in self.config.permitted
        )
        if not allowed:
            raise AdaptiveOptimizationError(
                "no available strategy is permitted by bandit safety constraints"
            )

        matching = tuple(item for item in history if self._matches(item, context))
        estimates: list[ArmEstimate] = []
        for level in allowed:
            known = tuple(
                item
                for item in matching
                if item.strategy is level and item.verification_known
            )
            successes = sum(item.verified_success for item in known)
            estimates.append(
                ArmEstimate(
                    level=level,
                    verified_samples=len(known),
                    success_rate=(
                        None
                        if not known
                        else Decimal(successes) / Decimal(len(known))
                    ),
                )
            )

        payload = (
            f"{self.config.seed}|{context.environment.environment_id}|"
            f"{context.environment.revision}|{context.task_family}|{len(history)}"
        ).encode("utf-8")
        draw = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
        explore = (
            draw % self.config.exploration_denominator
            < self.config.exploration_numerator
        )
        under_sampled = tuple(
            item
            for item in estimates
            if item.verified_samples < self.config.minimum_arm_samples
        )
        if explore and under_sampled:
            selected = under_sampled[draw % len(under_sampled)].level
            reason = "bounded-exploration"
        else:
            scored = tuple(
                item for item in estimates if item.success_rate is not None
            )
            if not scored:
                fallback = self.baseline.choose(
                    context=context,
                    available=allowed,
                    history=history,
                )
                return BanditDecision(
                    selection=StrategySelection(
                        level=fallback.level,
                        source=SelectionSource.BANDIT,
                        reason_code="insufficient-evidence-baseline",
                    ),
                    explored=False,
                    estimates=tuple(estimates),
                )
            best_rate = max(
                item.success_rate for item in scored if item.success_rate is not None
            )
            candidates = tuple(
                item.level
                for item in scored
                if item.success_rate == best_rate
            )
            selected = next(level for level in allowed if level in candidates)
            reason = "highest-verified-success-rate"

        return BanditDecision(
            selection=StrategySelection(
                level=selected,
                source=SelectionSource.BANDIT,
                reason_code=reason,
            ),
            explored=explore and bool(under_sampled),
            estimates=tuple(estimates),
        )

    def choose(
        self,
        *,
        context: StrategyContext,
        available: tuple[ExecutionLevel, ...],
        history: tuple[StrategyOutcomeRecord, ...],
    ) -> StrategySelection:
        return self.decide(
            context=context,
            available=available,
            history=history,
        ).selection


@dataclass(frozen=True, slots=True)
class LoggedPolicyCase:
    record: StrategyOutcomeRecord
    available: tuple[ExecutionLevel, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.record, StrategyOutcomeRecord):
            raise AdaptiveOptimizationError("record must be StrategyOutcomeRecord")
        object.__setattr__(
            self,
            "available",
            _levels(self.available, name="available"),
        )
        if self.record.strategy not in self.available:
            raise AdaptiveOptimizationError(
                "logged strategy must be among available strategies"
            )


class PolicyEvaluationDisposition(StrEnum):
    IMPROVED = "improved"
    SAME = "same"
    REGRESSED = "regressed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyEvaluationReport:
    train_samples: int
    evaluation_samples: int
    baseline_supported: int
    candidate_supported: int
    baseline_success_rate: Decimal | None
    candidate_success_rate: Decimal | None
    disposition: PolicyEvaluationDisposition
    minimum_supported: int
    future_data_leakage: bool = False


class OfflinePolicyEvaluator:
    """Chronological logged-policy evaluation with explicit support counts."""

    __slots__ = ()

    def evaluate(
        self,
        cases: Sequence[LoggedPolicyCase],
        *,
        candidate: AdaptiveStrategySelector,
        baseline: AdaptiveStrategySelector | None = None,
        train_fraction: Decimal = Decimal("0.6"),
        minimum_supported: int = 3,
    ) -> PolicyEvaluationReport:
        if not isinstance(train_fraction, Decimal):
            raise AdaptiveOptimizationError("train_fraction must be Decimal")
        if not Decimal(0) < train_fraction < Decimal(1):
            raise AdaptiveOptimizationError("train_fraction must be in (0, 1)")
        _counter(minimum_supported, name="minimum_supported", minimum=1)
        if not cases:
            return PolicyEvaluationReport(
                train_samples=0,
                evaluation_samples=0,
                baseline_supported=0,
                candidate_supported=0,
                baseline_success_rate=None,
                candidate_success_rate=None,
                disposition=PolicyEvaluationDisposition.INSUFFICIENT_EVIDENCE,
                minimum_supported=minimum_supported,
            )

        ordered = sorted(
            cases,
            key=lambda case: (
                case.record.performance.observed_at,
                str(case.record.performance.evidence_id),
            ),
        )
        split = max(
            1,
            min(
                len(ordered) - 1,
                int(Decimal(len(ordered)) * train_fraction),
            ),
        )
        train = list(ordered[:split])
        evaluate = ordered[split:]
        if not evaluate:
            return PolicyEvaluationReport(
                train_samples=len(train),
                evaluation_samples=0,
                baseline_supported=0,
                candidate_supported=0,
                baseline_success_rate=None,
                candidate_success_rate=None,
                disposition=PolicyEvaluationDisposition.INSUFFICIENT_EVIDENCE,
                minimum_supported=minimum_supported,
            )
        baseline_selector = (
            DeterministicStrategyBaseline()
            if baseline is None
            else baseline
        )
        history = [case.record for case in train]
        baseline_outcomes: list[bool] = []
        candidate_outcomes: list[bool] = []
        for case in evaluate:
            baseline_choice = baseline_selector.choose(
                context=case.record.context,
                available=case.available,
                history=tuple(history),
            )
            candidate_choice = candidate.choose(
                context=case.record.context,
                available=case.available,
                history=tuple(history),
            )
            if (
                case.record.verification_known
                and baseline_choice.level is case.record.strategy
            ):
                baseline_outcomes.append(case.record.verified_success)
            if (
                case.record.verification_known
                and candidate_choice.level is case.record.strategy
            ):
                candidate_outcomes.append(case.record.verified_success)
            history.append(case.record)

        baseline_rate = self._rate(baseline_outcomes)
        candidate_rate = self._rate(candidate_outcomes)
        if (
            len(baseline_outcomes) < minimum_supported
            or len(candidate_outcomes) < minimum_supported
            or baseline_rate is None
            or candidate_rate is None
        ):
            disposition = PolicyEvaluationDisposition.INSUFFICIENT_EVIDENCE
        elif candidate_rate > baseline_rate:
            disposition = PolicyEvaluationDisposition.IMPROVED
        elif candidate_rate < baseline_rate:
            disposition = PolicyEvaluationDisposition.REGRESSED
        else:
            disposition = PolicyEvaluationDisposition.SAME

        return PolicyEvaluationReport(
            train_samples=len(train),
            evaluation_samples=len(evaluate),
            baseline_supported=len(baseline_outcomes),
            candidate_supported=len(candidate_outcomes),
            baseline_success_rate=baseline_rate,
            candidate_success_rate=candidate_rate,
            disposition=disposition,
            minimum_supported=minimum_supported,
        )

    @staticmethod
    def _rate(values: Sequence[bool]) -> Decimal | None:
        if not values:
            return None
        return Decimal(sum(values)) / Decimal(len(values))


@dataclass(frozen=True, slots=True, kw_only=True)
class BanditExperimentReport:
    seed: int
    cases: int
    evaluation: PolicyEvaluationReport


@dataclass(frozen=True, slots=True)
class ContextualBanditExperiment:
    """Reproducible offline experiment; it never executes a machine action."""

    config: BanditConfig

    def run(
        self,
        cases: Sequence[LoggedPolicyCase],
        *,
        minimum_supported: int = 3,
    ) -> BanditExperimentReport:
        bandit = SafeContextualBandit(self.config)
        report = OfflinePolicyEvaluator().evaluate(
            cases,
            candidate=bandit,
            minimum_supported=minimum_supported,
        )
        return BanditExperimentReport(
            seed=self.config.seed,
            cases=len(cases),
            evaluation=report,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class UserCorrectionExample:
    preference_id: UUID
    recorded_at: datetime
    task_family: str | None
    preferred: ExecutionLevel
    rejected: ExecutionLevel | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "preference_id",
            _uuid(self.preference_id, name="preference_id"),
        )
        object.__setattr__(
            self,
            "recorded_at",
            _aware(self.recorded_at, name="recorded_at"),
        )
        object.__setattr__(
            self,
            "task_family",
            _text(self.task_family, name="task_family", optional=True),
        )
        if not isinstance(self.preferred, ExecutionLevel):
            raise AdaptiveOptimizationError("preferred must be ExecutionLevel")
        if self.rejected is not None and not isinstance(
            self.rejected,
            ExecutionLevel,
        ):
            raise AdaptiveOptimizationError("rejected must be ExecutionLevel or None")
        if self.rejected is self.preferred:
            raise AdaptiveOptimizationError(
                "preferred and rejected strategies must differ"
            )


@dataclass(frozen=True, slots=True)
class ExplicitCorrectionDataset:
    """Dataset built only from explicit USER_CORRECTION preference records."""

    examples: tuple[UserCorrectionExample, ...]
    dataset_id: str

    @classmethod
    def from_preferences(
        cls,
        preferences: Sequence[UserPreference],
    ) -> ExplicitCorrectionDataset:
        examples: list[UserCorrectionExample] = []
        seen: set[UUID] = set()
        for preference in preferences:
            if not isinstance(preference, UserPreference):
                raise AdaptiveOptimizationError(
                    "preferences must contain UserPreference values"
                )
            if preference.preference_id in seen:
                raise AdaptiveOptimizationError("duplicate user-correction record")
            seen.add(preference.preference_id)
            if preference.source is not PreferenceSource.USER_CORRECTION:
                continue
            if preference.key is not PreferenceKey.WORKFLOW_PREFERENCE:
                continue
            value = preference.value
            if not isinstance(value, Mapping):
                raise AdaptiveOptimizationError(
                    "workflow correction value must be an object"
                )
            allowed_fields = {"preferred_strategy", "rejected_strategy", "task_family"}
            if not set(value) <= allowed_fields or "preferred_strategy" not in value:
                raise AdaptiveOptimizationError(
                    "workflow correction has unknown or missing fields"
                )
            preferred = _level(
                value["preferred_strategy"],
                name="preferred_strategy",
            )
            rejected_raw = value.get("rejected_strategy")
            rejected = (
                None
                if rejected_raw is None
                else _level(rejected_raw, name="rejected_strategy")
            )
            task_family_raw = value.get("task_family")
            if task_family_raw is not None and not isinstance(task_family_raw, str):
                raise AdaptiveOptimizationError(
                    "task_family correction field must be string or null"
                )
            examples.append(
                UserCorrectionExample(
                    preference_id=preference.preference_id,
                    recorded_at=preference.recorded_at,
                    task_family=task_family_raw,
                    preferred=preferred,
                    rejected=rejected,
                )
            )
        examples.sort(key=lambda item: (item.recorded_at, str(item.preference_id)))
        canonical = json.dumps(
            [
                {
                    "preference_id": str(item.preference_id),
                    "recorded_at": _format_time(item.recorded_at),
                    "task_family": item.task_family,
                    "preferred": item.preferred.value,
                    "rejected": (
                        None if item.rejected is None else item.rejected.value
                    ),
                }
                for item in examples
            ],
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(examples=tuple(examples), dataset_id=f"sha256:{digest}")


@dataclass(frozen=True, slots=True)
class PreferenceScoringModel:
    """Transparent frequency model over explicit correction evidence only."""

    dataset_id: str
    scores: tuple[tuple[str | None, ExecutionLevel, int], ...]

    @classmethod
    def train(cls, dataset: ExplicitCorrectionDataset) -> PreferenceScoringModel:
        if not isinstance(dataset, ExplicitCorrectionDataset):
            raise AdaptiveOptimizationError(
                "dataset must be ExplicitCorrectionDataset"
            )
        counts: dict[tuple[str | None, ExecutionLevel], int] = defaultdict(int)
        for example in dataset.examples:
            counts[(example.task_family, example.preferred)] += 1
            if example.rejected is not None:
                counts[(example.task_family, example.rejected)] -= 1
        scores = tuple(
            (family, level, score)
            for (family, level), score in sorted(
                counts.items(),
                key=lambda item: (
                    item[0][0] or "",
                    item[0][1].value,
                ),
            )
        )
        return cls(dataset_id=dataset.dataset_id, scores=scores)

    def choose(
        self,
        *,
        context: StrategyContext,
        available: tuple[ExecutionLevel, ...],
        history: tuple[StrategyOutcomeRecord, ...] = (),
    ) -> StrategySelection:
        allowed = _levels(available, name="available")
        score_map = {
            (family, level): score for family, level, score in self.scores
        }
        ranked = [
            (
                score_map.get((context.task_family, level), 0)
                + score_map.get((None, level), 0),
                -CANONICAL_EXECUTION_LEVELS.index(level),
                level,
            )
            for level in allowed
        ]
        best = max(ranked, key=lambda item: (item[0], item[1]))
        if best[0] <= 0:
            baseline = DeterministicStrategyBaseline().choose(
                context=context,
                available=allowed,
                history=history,
            )
            return StrategySelection(
                level=baseline.level,
                source=SelectionSource.PREFERENCE,
                reason_code="insufficient-explicit-corrections-baseline",
            )
        return StrategySelection(
            level=best[2],
            source=SelectionSource.PREFERENCE,
            reason_code="explicit-correction-score",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PreferenceRankingReport:
    samples: int
    correct: int
    accuracy: Decimal | None
    dataset_id: str


@dataclass(frozen=True, slots=True)
class PreferenceRankingExperiment:
    """Prequential ranking experiment with no future-correction leakage."""

    baseline: DeterministicStrategyBaseline = DeterministicStrategyBaseline()

    def run(
        self,
        dataset: ExplicitCorrectionDataset,
    ) -> PreferenceRankingReport:
        prior: list[UserCorrectionExample] = []
        correct = 0
        scored = 0
        for current in dataset.examples:
            if current.rejected is None:
                prior.append(current)
                continue
            prior_dataset = ExplicitCorrectionDataset(
                examples=tuple(prior),
                dataset_id="prequential-prior",
            )
            model = PreferenceScoringModel.train(prior_dataset)
            context = StrategyContext(
                environment=EnvironmentKey(None),
                task_family=current.task_family,
                captured_at=current.recorded_at,
            )
            choice = model.choose(
                context=context,
                available=(current.preferred, current.rejected),
            )
            scored += 1
            correct += choice.level is current.preferred
            prior.append(current)
        return PreferenceRankingReport(
            samples=scored,
            correct=correct,
            accuracy=(
                None if scored == 0 else Decimal(correct) / Decimal(scored)
            ),
            dataset_id=dataset.dataset_id,
        )


class PolicyLifecycle(StrEnum):
    BASELINE = "baseline"
    CANDIDATE = "candidate"
    ACTIVE = "active"
    RETIRED = "retired"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True, kw_only=True)
class OptimizationPolicy:
    """Versioned strategy-only policy; it cannot encode kernel authority."""

    policy_id: UUID
    revision: int
    strategy_priority: tuple[ExecutionLevel, ...]
    created_at: datetime
    dataset_id: str | None = None
    model_id: str | None = None
    schema_version: int = _POLICY_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_id",
            _uuid(self.policy_id, name="policy_id"),
        )
        _counter(self.revision, name="revision", minimum=1)
        object.__setattr__(
            self,
            "strategy_priority",
            _levels(self.strategy_priority, name="strategy_priority"),
        )
        object.__setattr__(
            self,
            "created_at",
            _aware(self.created_at, name="created_at"),
        )
        object.__setattr__(
            self,
            "dataset_id",
            _text(self.dataset_id, name="dataset_id", optional=True),
        )
        object.__setattr__(
            self,
            "model_id",
            _text(self.model_id, name="model_id", optional=True),
        )
        if type(self.schema_version) is not int or self.schema_version != _POLICY_SCHEMA:
            raise AdaptiveOptimizationError("unsupported optimization-policy schema")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_id": str(self.policy_id),
            "revision": self.revision,
            "strategy_priority": [
                level.value for level in self.strategy_priority
            ],
            "created_at": _format_time(self.created_at),
            "dataset_id": self.dataset_id,
            "model_id": self.model_id,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> OptimizationPolicy:
        expected = {
            "schema_version",
            "policy_id",
            "revision",
            "strategy_priority",
            "created_at",
            "dataset_id",
            "model_id",
        }
        if set(raw) != expected:
            raise AdaptiveOptimizationError("policy has missing or unknown fields")
        priority = raw["strategy_priority"]
        if not isinstance(priority, list | tuple):
            raise AdaptiveOptimizationError("strategy_priority must be an array")
        dataset_id = raw["dataset_id"]
        model_id = raw["model_id"]
        if dataset_id is not None and not isinstance(dataset_id, str):
            raise AdaptiveOptimizationError("dataset_id must be string or null")
        if model_id is not None and not isinstance(model_id, str):
            raise AdaptiveOptimizationError("model_id must be string or null")
        return cls(
            schema_version=_counter(raw["schema_version"], name="schema_version"),
            policy_id=_parse_uuid(raw["policy_id"], name="policy_id"),
            revision=_counter(raw["revision"], name="revision", minimum=1),
            strategy_priority=tuple(
                _level(item, name="strategy_priority item") for item in priority
            ),
            created_at=_parse_time(raw["created_at"], name="created_at"),
            dataset_id=dataset_id,
            model_id=model_id,
        )


class PolicyTransition(StrEnum):
    STAGED = "staged"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyEvent:
    event_id: UUID
    policy: OptimizationPolicy
    transition: PolicyTransition
    occurred_at: datetime
    previous_active: UUID | None = None
    rollback_target: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event_id",
            _uuid(self.event_id, name="event_id"),
        )
        if not isinstance(self.policy, OptimizationPolicy):
            raise AdaptiveOptimizationError("policy must be OptimizationPolicy")
        if not isinstance(self.transition, PolicyTransition):
            raise AdaptiveOptimizationError("transition must be PolicyTransition")
        object.__setattr__(
            self,
            "occurred_at",
            _aware(self.occurred_at, name="occurred_at"),
        )
        if self.previous_active is not None:
            _uuid(self.previous_active, name="previous_active")
        if self.rollback_target is not None:
            _uuid(self.rollback_target, name="rollback_target")

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": str(self.event_id),
            "policy": self.policy.to_dict(),
            "transition": self.transition.value,
            "occurred_at": _format_time(self.occurred_at),
            "previous_active": (
                None if self.previous_active is None else str(self.previous_active)
            ),
            "rollback_target": (
                None if self.rollback_target is None else str(self.rollback_target)
            ),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> PolicyEvent:
        expected = {
            "event_id",
            "policy",
            "transition",
            "occurred_at",
            "previous_active",
            "rollback_target",
        }
        if set(raw) != expected:
            raise AdaptiveOptimizationError(
                "policy event has missing or unknown fields"
            )
        policy = raw["policy"]
        if not isinstance(policy, Mapping):
            raise AdaptiveOptimizationError("policy event policy must be an object")
        transition_raw = raw["transition"]
        if not isinstance(transition_raw, str):
            raise AdaptiveOptimizationError("policy transition must be string")
        try:
            transition = PolicyTransition(transition_raw)
        except ValueError as exc:
            raise AdaptiveOptimizationError("unknown policy transition") from exc
        previous_raw = raw["previous_active"]
        target_raw = raw["rollback_target"]
        return cls(
            event_id=_parse_uuid(raw["event_id"], name="event_id"),
            policy=OptimizationPolicy.from_dict(policy),
            transition=transition,
            occurred_at=_parse_time(raw["occurred_at"], name="occurred_at"),
            previous_active=(
                None
                if previous_raw is None
                else _parse_uuid(previous_raw, name="previous_active")
            ),
            rollback_target=(
                None
                if target_raw is None
                else _parse_uuid(target_raw, name="rollback_target")
            ),
        )


@dataclass(frozen=True, slots=True)
class OptimizationPolicyStore:
    """Append-only policy lifecycle with explicit promotion and rollback."""

    journal: EventJournal

    def __post_init__(self) -> None:
        if not isinstance(self.journal, EventJournal):
            raise AdaptiveOptimizationError("journal must be EventJournal")

    def _append(self, item: PolicyEvent) -> int:
        event = Event(
            event_id=item.event_id,
            event_type=EventType.OBSERVATION_RECORDED,
            timestamp=item.occurred_at,
            schema_version=CURRENT_EVENT_SCHEMA_VERSION,
            source=_POLICY_SOURCE,
            correlation_id=item.policy.policy_id,
            causation_id=item.previous_active,
            task_id=None,
            payload=ObservationPayload(value=item.to_dict()),
            metadata={"contract": _POLICY_CONTRACT},
        )
        return self.journal.append(event)

    def events(self) -> tuple[PolicyEvent, ...]:
        results: list[PolicyEvent] = []
        cursor = 0
        scanned = 0
        while scanned < _MAX_HISTORY_SCAN:
            batch = self.journal.read(
                after_sequence=cursor,
                limit=min(_HISTORY_PAGE, _MAX_HISTORY_SCAN - scanned),
            )
            if not batch:
                break
            cursor = batch[-1].sequence
            scanned += len(batch)
            for entry in batch:
                event = entry.event
                if (
                    event.source != _POLICY_SOURCE
                    or event.metadata.get("contract") != _POLICY_CONTRACT
                ):
                    continue
                if not isinstance(event.payload, ObservationPayload):
                    raise AdaptiveOptimizationError(
                        "optimization policy event has wrong payload"
                    )
                value = event.payload.value
                if not isinstance(value, Mapping):
                    raise AdaptiveOptimizationError(
                        "optimization policy payload must be an object"
                    )
                item = PolicyEvent.from_dict(value)
                if item.event_id != event.event_id:
                    raise AdaptiveOptimizationError(
                        "optimization policy event identity mismatch"
                    )
                if item.policy.policy_id != event.correlation_id:
                    raise AdaptiveOptimizationError(
                        "optimization policy correlation mismatch"
                    )
                results.append(item)
        return tuple(results)

    def staged_policy(self, policy_id: UUID) -> OptimizationPolicy | None:
        policy_id = _uuid(policy_id, name="policy_id")
        found: OptimizationPolicy | None = None
        for item in self.events():
            if item.policy.policy_id == policy_id:
                found = item.policy
        return found

    def active_policy(self) -> OptimizationPolicy | None:
        active_id: UUID | None = None
        policies: dict[UUID, OptimizationPolicy] = {}
        for item in self.events():
            policies[item.policy.policy_id] = item.policy
            if item.transition is PolicyTransition.PROMOTED:
                active_id = item.policy.policy_id
            elif item.transition is PolicyTransition.ROLLED_BACK:
                active_id = item.rollback_target
            elif (
                item.transition is PolicyTransition.RETIRED
                and active_id == item.policy.policy_id
            ):
                active_id = None
        return None if active_id is None else policies.get(active_id)

    def stage(
        self,
        policy: OptimizationPolicy,
        *,
        occurred_at: datetime,
    ) -> int:
        if self.staged_policy(policy.policy_id) is not None:
            raise AdaptiveOptimizationError("policy identity already exists")
        return self._append(
            PolicyEvent(
                event_id=uuid4(),
                policy=policy,
                transition=PolicyTransition.STAGED,
                occurred_at=occurred_at,
            )
        )

    def promote(
        self,
        policy_id: UUID,
        *,
        occurred_at: datetime,
        expected_active: UUID | None,
    ) -> int:
        current = self.active_policy()
        current_id = None if current is None else current.policy_id
        if current_id != expected_active:
            raise AdaptiveOptimizationError(
                "active policy changed; promotion must be retried"
            )
        candidate = self.staged_policy(policy_id)
        if candidate is None:
            raise AdaptiveOptimizationError("candidate policy is not staged")
        return self._append(
            PolicyEvent(
                event_id=uuid4(),
                policy=candidate,
                transition=PolicyTransition.PROMOTED,
                occurred_at=occurred_at,
                previous_active=current_id,
            )
        )

    def rollback(
        self,
        *,
        occurred_at: datetime,
        expected_active: UUID,
        target_policy_id: UUID,
    ) -> int:
        current = self.active_policy()
        if current is None or current.policy_id != expected_active:
            raise AdaptiveOptimizationError(
                "active policy changed; rollback must be retried"
            )
        target = self.staged_policy(target_policy_id)
        if target is None:
            raise AdaptiveOptimizationError("rollback target does not exist")
        return self._append(
            PolicyEvent(
                event_id=uuid4(),
                policy=current,
                transition=PolicyTransition.ROLLED_BACK,
                occurred_at=occurred_at,
                previous_active=current.policy_id,
                rollback_target=target.policy_id,
            )
        )

    def retire(
        self,
        policy_id: UUID,
        *,
        occurred_at: datetime,
    ) -> int:
        policy = self.staged_policy(policy_id)
        if policy is None:
            raise AdaptiveOptimizationError("retired policy does not exist")
        return self._append(
            PolicyEvent(
                event_id=uuid4(),
                policy=policy,
                transition=PolicyTransition.RETIRED,
                occurred_at=occurred_at,
                previous_active=(
                    None
                    if self.active_policy() is None
                    else self.active_policy().policy_id
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class OptimizationPolicyRuntime:
    """Selects only from caller-permitted strategies and fails back to baseline."""

    store: OptimizationPolicyStore
    baseline: DeterministicStrategyBaseline = DeterministicStrategyBaseline()

    def choose(
        self,
        *,
        context: StrategyContext,
        available: tuple[ExecutionLevel, ...],
        history: tuple[StrategyOutcomeRecord, ...] = (),
    ) -> StrategySelection:
        allowed = _levels(available, name="available")
        try:
            active = self.store.active_policy()
        except AdaptiveOptimizationError:
            active = None
        if active is None:
            fallback = self.baseline.choose(
                context=context,
                available=allowed,
                history=history,
            )
            return StrategySelection(
                level=fallback.level,
                source=SelectionSource.POLICY_FALLBACK,
                reason_code="no-valid-active-policy",
            )
        for level in active.strategy_priority:
            if level in allowed:
                return StrategySelection(
                    level=level,
                    source=SelectionSource.POLICY_FALLBACK,
                    reason_code="active-versioned-policy",
                )
        fallback = self.baseline.choose(
            context=context,
            available=allowed,
            history=history,
        )
        return StrategySelection(
            level=fallback.level,
            source=SelectionSource.POLICY_FALLBACK,
            reason_code="active-policy-had-no-available-strategy",
        )
