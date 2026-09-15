"""Durable historical strategy-performance evidence foundation (AX-516).

Evidence is derived from canonical ExecutionMetricsRecord values and persisted
through the existing append-only EventJournal.  It is observation only: this
module never routes, selects a strategy, grants authority, changes risk, widens
a budget, executes a capability, or declares task success.

The journal is deliberately reused instead of creating a second persistence
schema. Strategy evidence is identified by an exact contract marker and decoded
fail-closed. Reads are bounded even when the shared journal contains unrelated
events.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final
from uuid import UUID, uuid4

from agentx.cognition.router import ExecutionLevel
from agentx.core.events import (
    CURRENT_EVENT_SCHEMA_VERSION,
    Event,
    EventType,
    ObservationPayload,
)
from agentx.core.ids import TaskId
from agentx.core.reuse_efficiency import ExecutionEvidenceOutcome
from agentx.execution_metrics import ExecutionMetricsRecord
from agentx.infrastructure.event_journal import EventJournal

__all__ = [
    "MAX_STRATEGY_EVIDENCE_READ",
    "MAX_STRATEGY_EVIDENCE_SCAN",
    "STRATEGY_PERFORMANCE_SCHEMA_VERSION",
    "StrategyPerformanceEntry",
    "StrategyPerformanceEvidence",
    "StrategyPerformanceEvidenceError",
    "StrategyPerformanceLedger",
]

STRATEGY_PERFORMANCE_SCHEMA_VERSION: Final[int] = 1
MAX_STRATEGY_EVIDENCE_READ: Final[int] = 256
MAX_STRATEGY_EVIDENCE_SCAN: Final[int] = 4096
_PAGE_SIZE: Final[int] = 128
_CONTRACT_MARKER: Final[str] = "agentx.strategy_performance.v1"
_SOURCE: Final[str] = "agentx.strategy_performance"


class StrategyPerformanceEvidenceError(ValueError):
    """Raised when typed strategy-performance evidence is malformed."""


def _time(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise StrategyPerformanceEvidenceError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID")
    if value.int == 0:
        raise StrategyPerformanceEvidenceError(f"{field_name} must not be nil")
    return value


def _counter(value: object, *, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise StrategyPerformanceEvidenceError(f"{field_name} must be a non-negative integer")
    return value


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise StrategyPerformanceEvidenceError("observed_at must be a string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise StrategyPerformanceEvidenceError("observed_at is invalid") from exc
    return _time(parsed, field_name="observed_at")


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyPerformanceEvidence:
    """One immutable measured outcome for one canonical execution level."""

    evidence_id: UUID
    task_id: TaskId
    correlation_id: UUID
    execution_level: ExecutionLevel
    outcome: ExecutionEvidenceOutcome
    observed_at: datetime
    elapsed: timedelta | None
    model_calls: int
    machine_actions: int
    external_cost: Decimal | None = None
    cost_unit: str | None = None
    verification_passed: bool | None = None
    schema_version: int = STRATEGY_PERFORMANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _uuid(self.evidence_id, field_name="evidence_id"))
        if not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be a TaskId")
        object.__setattr__(
            self,
            "correlation_id",
            _uuid(self.correlation_id, field_name="correlation_id"),
        )
        if not isinstance(self.execution_level, ExecutionLevel):
            raise TypeError("execution_level must be an ExecutionLevel")
        if not isinstance(self.outcome, ExecutionEvidenceOutcome):
            raise TypeError("outcome must be an ExecutionEvidenceOutcome")
        object.__setattr__(self, "observed_at", _time(self.observed_at, field_name="observed_at"))
        if self.elapsed is not None:
            if not isinstance(self.elapsed, timedelta):
                raise TypeError("elapsed must be a timedelta or None")
            if self.elapsed < timedelta(0):
                raise StrategyPerformanceEvidenceError("elapsed must not be negative")
        _counter(self.model_calls, field_name="model_calls")
        _counter(self.machine_actions, field_name="machine_actions")
        if self.external_cost is None:
            if self.cost_unit is not None:
                raise StrategyPerformanceEvidenceError("cost_unit requires external_cost")
        else:
            if not isinstance(self.external_cost, Decimal):
                raise TypeError("external_cost must be a Decimal or None")
            if not self.external_cost.is_finite() or self.external_cost < 0:
                raise StrategyPerformanceEvidenceError(
                    "external_cost must be finite and non-negative"
                )
            if not isinstance(self.cost_unit, str) or not self.cost_unit.strip():
                raise StrategyPerformanceEvidenceError(
                    "external_cost requires a non-empty cost_unit"
                )
        if self.verification_passed is not None and not isinstance(
            self.verification_passed, bool
        ):
            raise TypeError("verification_passed must be bool or None")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise StrategyPerformanceEvidenceError(
                "unsupported strategy-performance schema version"
            )

    @classmethod
    def from_metrics(
        cls,
        metrics: ExecutionMetricsRecord,
        *,
        observed_at: datetime,
        evidence_id: UUID | None = None,
    ) -> StrategyPerformanceEvidence:
        """Capture measured facts without deriving any routing recommendation."""
        if not isinstance(metrics, ExecutionMetricsRecord):
            raise TypeError("metrics must be an ExecutionMetricsRecord")
        verification_passed = (
            None if metrics.verification is None else metrics.verification.passed
        )
        return cls(
            evidence_id=uuid4() if evidence_id is None else evidence_id,
            task_id=metrics.task_id,
            correlation_id=metrics.correlation_id,
            execution_level=metrics.execution_level,
            outcome=metrics.outcome,
            observed_at=observed_at,
            elapsed=metrics.elapsed,
            model_calls=metrics.model_calls,
            machine_actions=metrics.machine_actions,
            external_cost=metrics.external_cost,
            cost_unit=metrics.cost_unit,
            verification_passed=verification_passed,
        )

    def to_dict(self) -> dict[str, object]:
        elapsed_us = (
            None
            if self.elapsed is None
            else self.elapsed // timedelta(microseconds=1)
        )
        return {
            "schema_version": self.schema_version,
            "evidence_id": str(self.evidence_id),
            "task_id": self.task_id.to_str(),
            "correlation_id": str(self.correlation_id),
            "execution_level": self.execution_level.value,
            "outcome": self.outcome.value,
            "observed_at": _format_time(self.observed_at),
            "elapsed_microseconds": elapsed_us,
            "model_calls": self.model_calls,
            "machine_actions": self.machine_actions,
            "external_cost": (
                None if self.external_cost is None else str(self.external_cost)
            ),
            "cost_unit": self.cost_unit,
            "verification_passed": self.verification_passed,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> StrategyPerformanceEvidence:
        expected = {
            "schema_version",
            "evidence_id",
            "task_id",
            "correlation_id",
            "execution_level",
            "outcome",
            "observed_at",
            "elapsed_microseconds",
            "model_calls",
            "machine_actions",
            "external_cost",
            "cost_unit",
            "verification_passed",
        }
        if set(raw) != expected:
            raise StrategyPerformanceEvidenceError(
                "strategy-performance fields are incomplete or unknown"
            )
        try:
            evidence_id = UUID(str(raw["evidence_id"]))
            correlation_id = UUID(str(raw["correlation_id"]))
            task_id = TaskId.parse(str(raw["task_id"]))
            level = ExecutionLevel(str(raw["execution_level"]))
            outcome = ExecutionEvidenceOutcome(str(raw["outcome"]))
        except (ValueError, TypeError) as exc:
            raise StrategyPerformanceEvidenceError(
                "strategy-performance identity or enum field is invalid"
            ) from exc

        elapsed_raw = raw["elapsed_microseconds"]
        if elapsed_raw is not None and (type(elapsed_raw) is not int or elapsed_raw < 0):
            raise StrategyPerformanceEvidenceError(
                "elapsed_microseconds must be a non-negative integer or null"
            )
        cost_raw = raw["external_cost"]
        cost: Decimal | None
        if cost_raw is None:
            cost = None
        elif isinstance(cost_raw, str):
            try:
                cost = Decimal(cost_raw)
            except InvalidOperation as exc:
                raise StrategyPerformanceEvidenceError("external_cost is invalid") from exc
        else:
            raise StrategyPerformanceEvidenceError("external_cost must be a string or null")
        verification = raw["verification_passed"]
        if verification is not None and not isinstance(verification, bool):
            raise StrategyPerformanceEvidenceError(
                "verification_passed must be boolean or null"
            )
        cost_unit = raw["cost_unit"]
        if cost_unit is not None and not isinstance(cost_unit, str):
            raise StrategyPerformanceEvidenceError("cost_unit must be a string or null")
        return cls(
            schema_version=raw["schema_version"],  # type: ignore[arg-type]
            evidence_id=evidence_id,
            task_id=task_id,
            correlation_id=correlation_id,
            execution_level=level,
            outcome=outcome,
            observed_at=_parse_time(raw["observed_at"]),
            elapsed=(
                None
                if elapsed_raw is None
                else timedelta(microseconds=elapsed_raw)
            ),
            model_calls=_counter(raw["model_calls"], field_name="model_calls"),
            machine_actions=_counter(
                raw["machine_actions"], field_name="machine_actions"
            ),
            external_cost=cost,
            cost_unit=cost_unit,
            verification_passed=verification,
        )


@dataclass(frozen=True, slots=True)
class StrategyPerformanceEntry:
    sequence: int
    evidence: StrategyPerformanceEvidence


@dataclass(frozen=True, slots=True)
class StrategyPerformanceLedger:
    """Append/read AX-516 evidence through the canonical durable EventJournal."""

    journal: EventJournal

    def __post_init__(self) -> None:
        if not isinstance(self.journal, EventJournal):
            raise TypeError("journal must be an EventJournal")

    def append(self, evidence: StrategyPerformanceEvidence) -> int:
        if not isinstance(evidence, StrategyPerformanceEvidence):
            raise TypeError("evidence must be StrategyPerformanceEvidence")
        event = Event(
            event_id=evidence.evidence_id,
            event_type=EventType.OBSERVATION_RECORDED,
            timestamp=evidence.observed_at,
            schema_version=CURRENT_EVENT_SCHEMA_VERSION,
            source=_SOURCE,
            correlation_id=evidence.correlation_id,
            causation_id=None,
            task_id=evidence.task_id.to_str(),
            payload=ObservationPayload(value=evidence.to_dict()),
            metadata={"contract": _CONTRACT_MARKER},
        )
        return self.journal.append(event)

    def read(
        self,
        *,
        after_sequence: int = 0,
        limit: int = MAX_STRATEGY_EVIDENCE_READ,
        max_scan: int = MAX_STRATEGY_EVIDENCE_SCAN,
        execution_level: ExecutionLevel | None = None,
    ) -> tuple[StrategyPerformanceEntry, ...]:
        if type(after_sequence) is not int or after_sequence < 0:
            raise StrategyPerformanceEvidenceError(
                "after_sequence must be a non-negative integer"
            )
        if type(limit) is not int or not 1 <= limit <= MAX_STRATEGY_EVIDENCE_READ:
            raise StrategyPerformanceEvidenceError("limit is outside the bounded range")
        if type(max_scan) is not int or not 1 <= max_scan <= MAX_STRATEGY_EVIDENCE_SCAN:
            raise StrategyPerformanceEvidenceError("max_scan is outside the bounded range")
        if execution_level is not None and not isinstance(execution_level, ExecutionLevel):
            raise TypeError("execution_level must be ExecutionLevel or None")

        cursor = after_sequence
        scanned = 0
        results: list[StrategyPerformanceEntry] = []
        while scanned < max_scan and len(results) < limit:
            batch_size = min(_PAGE_SIZE, max_scan - scanned)
            batch = self.journal.read(after_sequence=cursor, limit=batch_size)
            if not batch:
                break
            scanned += len(batch)
            cursor = batch[-1].sequence
            for entry in batch:
                event = entry.event
                if event.source != _SOURCE or event.metadata.get("contract") != _CONTRACT_MARKER:
                    continue
                if event.event_type is not EventType.OBSERVATION_RECORDED or not isinstance(
                    event.payload, ObservationPayload
                ):
                    raise StrategyPerformanceEvidenceError(
                        "strategy-performance journal event has the wrong event shape"
                    )
                value = event.payload.value
                if not isinstance(value, Mapping):
                    raise StrategyPerformanceEvidenceError(
                        "strategy-performance payload must be an object"
                    )
                evidence = StrategyPerformanceEvidence.from_dict(value)
                if evidence.evidence_id != event.event_id:
                    raise StrategyPerformanceEvidenceError(
                        "strategy-performance event/evidence identity mismatch"
                    )
                if evidence.correlation_id != event.correlation_id:
                    raise StrategyPerformanceEvidenceError(
                        "strategy-performance correlation mismatch"
                    )
                if event.task_id != evidence.task_id.to_str():
                    raise StrategyPerformanceEvidenceError(
                        "strategy-performance task identity mismatch"
                    )
                if execution_level is not None and evidence.execution_level is not execution_level:
                    continue
                results.append(
                    StrategyPerformanceEntry(sequence=entry.sequence, evidence=evidence)
                )
                if len(results) >= limit:
                    break
        return tuple(results)
