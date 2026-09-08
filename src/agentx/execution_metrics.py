"""N2.13 canonical execution-efficiency instrumentation boundary.

This module is the smallest explicit boundary through which measured
execution-efficiency facts can be recorded around one AgentX run. It is
**observation / accounting only**:

* it does not decide or change an execution level;
* it does not route, execute, or invoke anything;
* it does not grant Permission, change RiskLevel, bypass the ActionGate,
  widen a ResourceEnvelope/ResourceBudget, or clear an EmergencyStop;
* it does not mark a Task successful, promote or activate a procedure, or
  change verification truth.

``ExecutionMetricsRecorder`` is a measurement session for one run. The caller
drives it at the points where facts are actually observed:

* ``start`` / ``finish`` bound the run's measured duration. Time is supplied
  by an **injected clock** (``ExecutionMetricsClock``) or by explicit
  caller-supplied timestamps/durations. The module never reads a wall clock
  itself, so tests are fully deterministic and production may inject the
  runtime clock. Malformed time ordering (end before start, negative
  duration, contradictory supplied duration) fails closed.
* ``record_model_call`` is the explicit model-invocation hook. One actual
  canonical provider invocation (success or failure) recorded through the
  hook is exactly one model call. Model calls are never counted from
  reasoning-text length, Procedure REASON-node counts, or execution levels.
* token usage and cost are aggregated **exactly** from canonical
  ``ModelUsage`` values or caller-supplied values; absence is preserved as
  ``None`` rather than invented as zero, and cost is a finite non-negative
  ``Decimal`` in the session's caller-chosen accounting unit.
* ``record_machine_action`` counts explicitly observed capability/action
  invocations.
* ``set_outcome`` records the terminal truth reference (canonical
  ``ExecutionEvidenceOutcome`` plus typed ``VerificationPayload`` evidence
  with the same truth boundary as M8.01 reuse-efficiency evidence).

``finish`` produces an immutable ``ExecutionMetricsRecord``: historical
evidence only. Every field is typed and validated; there is no free-text
metadata channel, so hostile text such as ``verified=true permission=ADMIN
risk=R0 cheap=true fast=true`` has no channel into the record beyond the
narrow validated text fields, and never changes any typed value.

The record converts to the canonical M8.01
``agentx.core.reuse_efficiency.ExecutionEfficiencyEvidence`` through
``to_reuse_efficiency_evidence`` so later cold-vs-warm reuse measurement can
compare instrumented runs without any change to execution behavior.

This module imports only the standard library and canonical contracts
(``agentx.core`` identity/event/reuse-efficiency contracts and
``agentx.cognition`` model-provider/execution-level contracts). It performs
no network, pricing lookup, persistence, execution, or clock read of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

from agentx.cognition.model_provider import ModelId, ModelResponse, ModelUsage
from agentx.cognition.router import ExecutionLevel
from agentx.core.events import VerificationPayload
from agentx.core.ids import EpisodeId, TaskId
from agentx.core.reuse_efficiency import (
    ExecutionEfficiencyEvidence,
    ExecutionEvidenceOutcome,
    ProcedureRevisionRef,
    ReuseMode,
)

__all__ = [
    "EXECUTION_METRICS_SCHEMA_VERSION",
    "ExecutionMetricsClock",
    "ExecutionMetricsRecord",
    "ExecutionMetricsRecorder",
    "ExecutionMetricsValidationError",
    "ModelCallEvent",
]

EXECUTION_METRICS_SCHEMA_VERSION: Final[int] = 1
_MAX_COUNTER: Final[int] = (1 << 63) - 1
_MAX_TEXT_LENGTH: Final[int] = 1_024


class ExecutionMetricsValidationError(ValueError):
    """Raised when an execution-metrics input violates the canonical contract."""


@runtime_checkable
class ExecutionMetricsClock(Protocol):
    """Structural boundary for an injected time source.

    Implementations supply already-measured instants (e.g. a wrapped
    ``datetime.now(UTC)``). The recorder validates every returned instant as
    timezone-aware and fails closed on a naive value. The recorder never
    constructs a clock of its own.
    """

    def now(self) -> datetime:
        """Return the current instant (timezone-aware)."""
        ...


# --------------------------------------------------------------------------
# Validation helpers (fail closed; canonical C1.08 accounting semantics).
# --------------------------------------------------------------------------


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID")
    if value.int == 0:
        raise ExecutionMetricsValidationError(f"{field_name} must not be the nil UUID")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionMetricsValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_duration(value: object, *, field_name: str) -> timedelta | None:
    if value is None:
        return None
    if not isinstance(value, timedelta):
        raise TypeError(f"{field_name} must be a timedelta or None")
    if value < timedelta(0):
        raise ExecutionMetricsValidationError(f"{field_name} must not be negative")
    return value


def _validate_counter(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int or None")
    if value < 0:
        raise ExecutionMetricsValidationError(f"{field_name} must not be negative")
    if value > _MAX_COUNTER:
        raise OverflowError(f"{field_name} exceeds the supported counter range")
    return value


def _validate_positive_count(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int")
    if value < 1:
        raise ExecutionMetricsValidationError(f"{field_name} must be at least 1")
    if value > _MAX_COUNTER:
        raise OverflowError(f"{field_name} exceeds the supported counter range")
    return value


def _validate_decimal(value: object, *, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal or None")
    if not value.is_finite():
        raise ExecutionMetricsValidationError(f"{field_name} must be finite")
    if value < 0:
        raise ExecutionMetricsValidationError(f"{field_name} must not be negative")
    return value


def _validate_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ExecutionMetricsValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > _MAX_TEXT_LENGTH:
        raise ExecutionMetricsValidationError(
            f"{field_name} must not exceed {_MAX_TEXT_LENGTH} characters"
        )
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ExecutionMetricsValidationError(f"{field_name} must not contain control lines")
    return value


def _checked_add(a: int, b: int, *, field_name: str) -> int:
    total = a + b
    if total > _MAX_COUNTER:
        raise OverflowError(f"{field_name} exceeds the supported counter range")
    return total


def _validate_schema_version(value: object) -> int:
    if type(value) is not int:
        raise ExecutionMetricsValidationError("schema_version must be an integer")
    if value != EXECUTION_METRICS_SCHEMA_VERSION:
        raise ExecutionMetricsValidationError(
            f"unsupported execution-metrics schema version {value}; "
            f"supported version is {EXECUTION_METRICS_SCHEMA_VERSION}"
        )
    return value


def _verification_truth_boundary(
    outcome: ExecutionEvidenceOutcome,
    verification: VerificationPayload | None,
    verification_source: str | None,
    verification_reference: UUID | None,
) -> None:
    """Apply the canonical M8.01 verified-success truth boundary (inert data)."""

    if verification is not None and not isinstance(verification, VerificationPayload):
        raise TypeError("verification must be a VerificationPayload or None")
    source_present = verification_source is not None
    reference_present = verification_reference is not None
    if verification is None:
        if source_present or reference_present:
            raise ExecutionMetricsValidationError(
                "verification source/reference require typed verification evidence"
            )
    else:
        if not (source_present and reference_present):
            raise ExecutionMetricsValidationError(
                "typed verification evidence requires source and reference"
            )
    if outcome is ExecutionEvidenceOutcome.VERIFIED_SUCCESS:
        if verification is None or not verification.passed:
            raise ExecutionMetricsValidationError(
                "verified_success requires typed verification evidence with passed=True"
            )
    elif verification is not None and verification.passed:
        raise ExecutionMetricsValidationError(
            "passed verification is inconsistent with a non-verified-success outcome"
        )
    if outcome is ExecutionEvidenceOutcome.UNVERIFIED and verification is not None:
        raise ExecutionMetricsValidationError(
            "unverified outcome must not carry completed verification evidence"
        )


# --------------------------------------------------------------------------
# Explicit model-invocation event.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelCallEvent:
    """Explicit record of one observed canonical provider invocation.

    One actual canonical provider invocation equals exactly one model call.
    The event is supplied by the instrumentation hook at the point of
    invocation (typically from the canonical ``ModelResponse``). It is never
    derived from reasoning-text length, Procedure REASON-node counts, or
    execution levels.

    ``usage`` carries the canonical provider-reported ``ModelUsage`` when the
    invocation returned usage data; an invocation without reported usage is
    still a model call, and absence is preserved rather than invented.
    """

    model_id: ModelId
    usage: ModelUsage | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, ModelId):
            raise TypeError("model_id must be a ModelId")
        if self.usage is not None and not isinstance(self.usage, ModelUsage):
            raise TypeError("usage must be a ModelUsage or None")

    @classmethod
    def from_response(cls, response: ModelResponse) -> ModelCallEvent:
        """Build the explicit event from one successful canonical response."""

        if not isinstance(response, ModelResponse):
            raise TypeError("response must be a ModelResponse")
        return cls(model_id=response.model_id, usage=response.usage)


# --------------------------------------------------------------------------
# Immutable terminal record.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionMetricsRecord:
    """Immutable measured facts for one execution run (historical evidence).

    The record is inert data: it cannot grant permission, change risk, bypass
    the ActionGate, widen a budget, clear an EmergencyStop, mark a Task
    successful, activate or promote a procedure, or route future execution.
    There is no free-text metadata channel; every field is typed and
    validated, and hostile text in a narrow text field never changes any
    typed value.

    Absence semantics: provider-reported facts (tokens, cost) that were never
    observed stay ``None``. Explicit session event counters (``model_calls``,
    ``machine_actions``) are exact counts of recorded events, where ``0``
    means "zero events were observed in this session".
    """

    task_id: TaskId
    correlation_id: UUID
    execution_level: ExecutionLevel
    model_calls: int
    model_ids: tuple[ModelId, ...] = ()
    model_input_tokens: int | None = None
    model_output_tokens: int | None = None
    model_tokens: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    elapsed: timedelta | None = None
    external_cost: Decimal | None = None
    cost_unit: str | None = None
    machine_actions: int = 0
    procedure: ProcedureRevisionRef | None = None
    outcome: ExecutionEvidenceOutcome = ExecutionEvidenceOutcome.UNVERIFIED
    verification: VerificationPayload | None = None
    verification_source: str | None = None
    verification_reference: UUID | None = None
    schema_version: int = EXECUTION_METRICS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be a TaskId")
        object.__setattr__(
            self, "correlation_id", _validate_uuid(self.correlation_id, field_name="correlation_id")
        )
        if not isinstance(self.execution_level, ExecutionLevel):
            raise TypeError("execution_level must be an ExecutionLevel")
        object.__setattr__(self, "schema_version", _validate_schema_version(self.schema_version))

        model_calls = _validate_counter(self.model_calls, field_name="model_calls")
        if model_calls is None:  # pragma: no cover - guarded by non-optional field
            raise AssertionError("model_calls unexpectedly absent")
        if not isinstance(self.model_ids, tuple):
            raise TypeError("model_ids must be a tuple")
        for model_id in self.model_ids:
            if not isinstance(model_id, ModelId):
                raise TypeError("model_ids must contain only ModelId values")
        if len(self.model_ids) != model_calls:
            raise ExecutionMetricsValidationError(
                "model_ids length must equal model_calls (one identity per observed call)"
            )

        object.__setattr__(
            self,
            "model_input_tokens",
            _validate_counter(self.model_input_tokens, field_name="model_input_tokens"),
        )
        object.__setattr__(
            self,
            "model_output_tokens",
            _validate_counter(self.model_output_tokens, field_name="model_output_tokens"),
        )
        object.__setattr__(
            self,
            "model_tokens",
            _validate_counter(self.model_tokens, field_name="model_tokens"),
        )
        if (
            self.model_input_tokens is not None
            and self.model_output_tokens is not None
            and self.model_tokens is not None
        ):
            total = _checked_add(
                self.model_input_tokens,
                self.model_output_tokens,
                field_name="model_tokens",
            )
            if self.model_tokens != total:
                raise ExecutionMetricsValidationError(
                    "model_tokens must equal model_input_tokens + model_output_tokens"
                )

        started_at = _validate_timestamp(self.started_at, field_name="started_at")
        ended_at = _validate_timestamp(self.ended_at, field_name="ended_at")
        if started_at is None and ended_at is not None:
            raise ExecutionMetricsValidationError(
                "ended_at requires started_at (malformed time evidence)"
            )
        if started_at is not None and ended_at is not None:
            if ended_at < started_at:
                raise ExecutionMetricsValidationError(
                    "ended_at must not be earlier than started_at"
                )
            measured = ended_at - started_at
            supplied = _validate_duration(self.elapsed, field_name="elapsed")
            if supplied is not None and supplied != measured:
                raise ExecutionMetricsValidationError(
                    "elapsed must exactly match ended_at - started_at when both are supplied"
                )
            object.__setattr__(self, "elapsed", measured)
        else:
            object.__setattr__(
                self, "elapsed", _validate_duration(self.elapsed, field_name="elapsed")
            )
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "ended_at", ended_at)

        external_cost = _validate_decimal(self.external_cost, field_name="external_cost")
        object.__setattr__(self, "external_cost", external_cost)
        if external_cost is None:
            if self.cost_unit is not None:
                raise ExecutionMetricsValidationError("cost_unit requires external_cost")
        else:
            if self.cost_unit is None:
                raise ExecutionMetricsValidationError("external_cost requires cost_unit")
            object.__setattr__(
                self, "cost_unit", _validate_text(self.cost_unit, field_name="cost_unit")
            )

        object.__setattr__(
            self,
            "machine_actions",
            _validate_counter(self.machine_actions, field_name="machine_actions"),
        )
        if self.procedure is not None and not isinstance(self.procedure, ProcedureRevisionRef):
            raise TypeError("procedure must be a ProcedureRevisionRef or None")

        if not isinstance(self.outcome, ExecutionEvidenceOutcome):
            raise TypeError("outcome must be an ExecutionEvidenceOutcome")
        if self.verification_source is not None:
            object.__setattr__(
                self,
                "verification_source",
                _validate_text(self.verification_source, field_name="verification_source"),
            )
        if self.verification_reference is not None:
            object.__setattr__(
                self,
                "verification_reference",
                _validate_uuid(self.verification_reference, field_name="verification_reference"),
            )
        _verification_truth_boundary(
            self.outcome,
            self.verification,
            self.verification_source,
            self.verification_reference,
        )

    @property
    def verified_success(self) -> bool:
        """Whether typed evidence satisfies the verified-success truth boundary."""

        return (
            self.outcome is ExecutionEvidenceOutcome.VERIFIED_SUCCESS
            and self.verification is not None
            and self.verification.passed
        )

    def to_reuse_efficiency_evidence(
        self,
        *,
        episode_id: EpisodeId,
        mode: ReuseMode,
        evidence_source: str,
        evidence_reference: UUID,
    ) -> ExecutionEfficiencyEvidence:
        """Convert to the canonical M8.01 evidence record for comparison.

        Pure data conversion of typed facts; the caller supplies the run
        identity context this record does not own (episode identity, the
        descriptive reuse mode, and the evidence source/reference pair). No
        execution, routing, or timing happens here.
        """

        if not isinstance(episode_id, EpisodeId):
            raise TypeError("episode_id must be an EpisodeId")
        if not isinstance(mode, ReuseMode):
            raise TypeError("mode must be a ReuseMode")
        _validate_text(evidence_source, field_name="evidence_source")
        _validate_uuid(evidence_reference, field_name="evidence_reference")

        if self.started_at is not None and self.ended_at is not None:
            started_at = self.started_at
            ended_at = self.ended_at
        else:
            # M8.01 requires both timestamps or neither; keep only the exact
            # measured duration when the pair is incomplete.
            started_at = None
            ended_at = None

        return ExecutionEfficiencyEvidence(
            task_id=self.task_id,
            episode_id=episode_id,
            correlation_id=self.correlation_id,
            mode=mode,
            outcome=self.outcome,
            evidence_source=evidence_source,
            evidence_reference=evidence_reference,
            started_at=started_at,
            ended_at=ended_at,
            elapsed=self.elapsed,
            model_calls=self.model_calls,
            model_input_tokens=self.model_input_tokens,
            model_output_tokens=self.model_output_tokens,
            model_tokens=self.model_tokens,
            research_queries=None,
            machine_actions=self.machine_actions,
            repair_attempts=None,
            external_cost=self.external_cost,
            cost_unit=self.cost_unit,
            procedure=self.procedure,
            verification=self.verification,
            verification_source=self.verification_source,
            verification_reference=self.verification_reference,
        )


# --------------------------------------------------------------------------
# Measurement session.
# --------------------------------------------------------------------------


class ExecutionMetricsRecorder:
    """Explicit measurement session for one AgentX run.

    The recorder accumulates measured facts driven by the caller at the exact
    points where they are observed, then freezes them into one immutable
    :class:`ExecutionMetricsRecord` on ``finish``. It performs no execution,
    routing, persistence, pricing lookup, or clock read of its own: time comes
    from an injected :class:`ExecutionMetricsClock` or caller-supplied
    timestamps/durations, model calls come from explicit
    :class:`ModelCallEvent` hook calls, and cost comes from canonical
    ``ModelUsage.external_cost`` or caller-supplied exact ``Decimal`` values
    in the session's accounting unit.

    The session is single-use: after ``finish`` (or any validation failure of
    a terminal transition) it accepts no further events.
    """

    __slots__ = (
        "_clock",
        "_correlation_id",
        "_cost",
        "_cost_unit",
        "_execution_level",
        "_finished",
        "_input_tokens",
        "_machine_actions",
        "_model_calls",
        "_model_ids",
        "_outcome",
        "_outcome_set",
        "_output_tokens",
        "_procedure",
        "_started",
        "_started_at",
        "_task_id",
        "_total_tokens",
        "_verification",
        "_verification_reference",
        "_verification_source",
    )

    def __init__(
        self,
        *,
        task_id: TaskId,
        correlation_id: UUID,
        execution_level: ExecutionLevel,
        procedure: ProcedureRevisionRef | None = None,
        clock: ExecutionMetricsClock | None = None,
        cost_unit: str | None = None,
    ) -> None:
        if not isinstance(task_id, TaskId):
            raise TypeError("task_id must be a TaskId")
        _validate_uuid(correlation_id, field_name="correlation_id")
        if not isinstance(execution_level, ExecutionLevel):
            raise TypeError("execution_level must be an ExecutionLevel")
        if procedure is not None and not isinstance(procedure, ProcedureRevisionRef):
            raise TypeError("procedure must be a ProcedureRevisionRef or None")
        if clock is not None and not isinstance(clock, ExecutionMetricsClock):
            raise TypeError("clock must be an ExecutionMetricsClock or None")
        if cost_unit is not None:
            cost_unit = _validate_text(cost_unit, field_name="cost_unit")
        self._task_id = task_id
        self._correlation_id = correlation_id
        self._execution_level = execution_level
        self._procedure = procedure
        self._clock = clock
        self._cost_unit = cost_unit
        self._started = False
        self._started_at: datetime | None = None
        self._model_calls = 0
        self._model_ids: list[ModelId] = []
        self._input_tokens: int | None = None
        self._output_tokens: int | None = None
        self._total_tokens: int | None = None
        self._cost: Decimal | None = None
        self._machine_actions = 0
        self._outcome_set = False
        self._outcome: ExecutionEvidenceOutcome | None = None
        self._verification: VerificationPayload | None = None
        self._verification_source: str | None = None
        self._verification_reference: UUID | None = None
        self._finished = False

    # -- read-only observation --------------------------------------------

    @property
    def task_id(self) -> TaskId:
        """Return the bound canonical task identity."""

        return self._task_id

    @property
    def correlation_id(self) -> UUID:
        """Return the bound correlation identity."""

        return self._correlation_id

    @property
    def execution_level(self) -> ExecutionLevel:
        """Return the bound selected execution level (inert reference)."""

        return self._execution_level

    @property
    def procedure(self) -> ProcedureRevisionRef | None:
        """Return the bound exact procedure identity, when supplied."""

        return self._procedure

    @property
    def model_calls(self) -> int:
        """Return the exact count of recorded model-invocation events."""

        return self._model_calls

    @property
    def machine_actions(self) -> int:
        """Return the exact count of recorded capability/action invocations."""

        return self._machine_actions

    @property
    def started(self) -> bool:
        """Whether ``start`` has completed."""

        return self._started

    @property
    def finished(self) -> bool:
        """Whether the session has been finalized."""

        return self._finished

    # -- session events -----------------------------------------------------

    def start(self, *, started_at: datetime | None = None) -> None:
        """Record the run start.

        The instant comes from the explicit ``started_at`` argument when
        supplied, otherwise from the injected clock. A session with neither
        an injected clock nor a supplied start instant fails closed.
        """

        if self._finished:
            raise ExecutionMetricsValidationError("session already finished")
        if self._started:
            raise ExecutionMetricsValidationError("session already started")
        if started_at is not None:
            instant = _validate_timestamp(started_at, field_name="started_at")
        elif self._clock is not None:
            instant = _validate_timestamp(self._clock.now(), field_name="started_at")
        else:
            raise ExecutionMetricsValidationError("no supplied started_at and no injected clock")
        if instant is None:  # pragma: no cover - guarded by validation above
            raise AssertionError("start instant unexpectedly absent")
        self._started_at = instant
        self._started = True

    def record_model_call(self, event: ModelCallEvent) -> None:
        """Record one explicit observed model-invocation event.

        One recorded event is exactly one model call. The canonical hook is
        placed at the provider-invocation boundary: a successful invocation
        records ``ModelCallEvent.from_response(response)``; an invocation that
        failed without usage data records an event with ``usage=None``.
        Nothing in this module counts text, nodes, or levels as calls.
        """

        self._require_open()
        if not isinstance(event, ModelCallEvent):
            raise TypeError("event must be a ModelCallEvent")
        self._model_calls = _checked_add(self._model_calls, 1, field_name="model_calls")
        self._model_ids.append(event.model_id)
        usage = event.usage
        if usage is None:
            return
        if usage.input_tokens is not None:
            self._input_tokens = _checked_add(
                0 if self._input_tokens is None else self._input_tokens,
                usage.input_tokens,
                field_name="model input tokens",
            )
        if usage.output_tokens is not None:
            self._output_tokens = _checked_add(
                0 if self._output_tokens is None else self._output_tokens,
                usage.output_tokens,
                field_name="model output tokens",
            )
        accounted = usage.accounted_tokens
        if accounted is not None:
            self._total_tokens = _checked_add(
                0 if self._total_tokens is None else self._total_tokens,
                accounted,
                field_name="model tokens",
            )
        if usage.external_cost is not None:
            if self._cost_unit is None:
                raise ExecutionMetricsValidationError(
                    "model usage reports external_cost but the session has no cost_unit"
                )
            self._cost = (Decimal(0) if self._cost is None else self._cost) + usage.external_cost

    def record_machine_action(self, *, count: int = 1) -> None:
        """Record explicitly observed capability/action invocations."""

        self._require_open()
        value = _validate_positive_count(count, field_name="count")
        self._machine_actions = _checked_add(
            self._machine_actions, value, field_name="machine_actions"
        )

    def record_external_cost(self, cost: Decimal) -> None:
        """Record caller-supplied exact external cost in the session unit.

        The session must have been constructed with a ``cost_unit``; the
        module never prices a call itself and never infers cost from a model
        name.
        """

        self._require_open()
        value = _validate_decimal(cost, field_name="cost")
        if value is None:  # pragma: no cover - guarded by non-optional argument
            raise AssertionError("cost unexpectedly absent")
        if self._cost_unit is None:
            raise ExecutionMetricsValidationError(
                "cannot record external cost without a session cost_unit"
            )
        self._cost = (Decimal(0) if self._cost is None else self._cost) + value

    def set_outcome(
        self,
        outcome: ExecutionEvidenceOutcome,
        *,
        verification: VerificationPayload | None = None,
        verification_source: str | None = None,
        verification_reference: UUID | None = None,
    ) -> None:
        """Record the terminal truth reference (inert; sets nothing else).

        Applies the canonical M8.01 truth boundary: ``VERIFIED_SUCCESS``
        requires typed ``VerificationPayload(passed=True)`` evidence with an
        explicit source/reference; passed evidence is inconsistent with any
        other outcome; an unverified outcome cannot carry verification.
        """

        self._require_open()
        if self._outcome_set:
            raise ExecutionMetricsValidationError("outcome already recorded")
        if not isinstance(outcome, ExecutionEvidenceOutcome):
            raise TypeError("outcome must be an ExecutionEvidenceOutcome")
        _verification_truth_boundary(
            outcome, verification, verification_source, verification_reference
        )
        if verification_source is not None:
            verification_source = _validate_text(
                verification_source, field_name="verification_source"
            )
        if verification_reference is not None:
            verification_reference = _validate_uuid(
                verification_reference, field_name="verification_reference"
            )
        self._outcome = outcome
        self._verification = verification
        self._verification_source = verification_source
        self._verification_reference = verification_reference
        self._outcome_set = True

    def finish(
        self,
        *,
        ended_at: datetime | None = None,
        elapsed: timedelta | None = None,
    ) -> ExecutionMetricsRecord:
        """Freeze the session into one immutable record.

        The end instant comes from the explicit ``ended_at`` argument when
        supplied, otherwise from the injected clock. With no end instant, an
        explicitly supplied non-negative ``elapsed`` is used as the measured
        duration. When both an end instant and an ``elapsed`` are resolved
        they must agree exactly. Malformed time ordering (end before start,
        negative duration, contradiction) fails closed.
        """

        self._require_open()
        self._finished = True

        resolved_ended: datetime | None = None
        started_at: datetime | None
        final_ended: datetime | None
        final_elapsed: timedelta | None
        if ended_at is not None:
            resolved_ended = _validate_timestamp(ended_at, field_name="ended_at")
        elif self._clock is not None:
            resolved_ended = _validate_timestamp(self._clock.now(), field_name="ended_at")
        if resolved_ended is not None:
            if self._started_at is None:
                raise ExecutionMetricsValidationError(
                    "ended_at requires a recorded started_at (malformed time evidence)"
                )
            if resolved_ended < self._started_at:
                raise ExecutionMetricsValidationError(
                    "ended_at must not be earlier than started_at"
                )
            measured = resolved_ended - self._started_at
            supplied = _validate_duration(elapsed, field_name="elapsed")
            if supplied is not None and supplied != measured:
                raise ExecutionMetricsValidationError(
                    "supplied elapsed must exactly match ended_at - started_at"
                )
            started_at = self._started_at
            final_ended = resolved_ended
            final_elapsed = measured
        else:
            started_at = self._started_at
            final_ended = None
            final_elapsed = _validate_duration(elapsed, field_name="elapsed")

        model_calls = self._model_calls
        model_ids = tuple(self._model_ids)
        input_tokens = self._input_tokens
        output_tokens = self._output_tokens
        total_tokens = self._total_tokens
        if input_tokens is not None and output_tokens is not None:
            derived = _checked_add(input_tokens, output_tokens, field_name="model_tokens")
            if total_tokens is not None and total_tokens != derived:
                raise ExecutionMetricsValidationError(
                    "aggregate model tokens contradict aggregate input/output tokens"
                )
            total_tokens = derived

        outcome = (
            self._outcome if self._outcome is not None else ExecutionEvidenceOutcome.UNVERIFIED
        )
        verification = self._verification
        verification_source = self._verification_source
        verification_reference = self._verification_reference
        # The accounting unit travels with the cost fact: a declared unit with
        # no observed cost records absence, not an unattached unit.
        cost_unit = None if self._cost is None else self._cost_unit

        return ExecutionMetricsRecord(
            task_id=self._task_id,
            correlation_id=self._correlation_id,
            execution_level=self._execution_level,
            model_calls=model_calls,
            model_ids=model_ids,
            model_input_tokens=input_tokens,
            model_output_tokens=output_tokens,
            model_tokens=total_tokens,
            started_at=started_at,
            ended_at=final_ended,
            elapsed=final_elapsed,
            external_cost=self._cost,
            cost_unit=cost_unit,
            machine_actions=self._machine_actions,
            procedure=self._procedure,
            outcome=outcome,
            verification=verification,
            verification_source=verification_source,
            verification_reference=verification_reference,
        )

    def _require_open(self) -> None:
        if self._finished:
            raise ExecutionMetricsValidationError("session already finished")
