"""N2.13 unit tests: execution-efficiency instrumentation boundary.

All timing is deterministic: an injected fake clock or caller-supplied
timestamps/durations. No wall-clock reads, no sleeps, no network.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from agentx.cognition.model_provider import (
    ModelId,
    ModelResponse,
    ModelUsage,
    ProviderId,
    TextContent,
)
from agentx.cognition.router import ExecutionLevel
from agentx.core.events import VerificationPayload
from agentx.core.ids import EpisodeId, ProcedureId, TaskId
from agentx.core.reuse_efficiency import (
    ExecutionEfficiencyEvidence,
    ExecutionEvidenceOutcome,
    ProcedureRevisionRef,
    ReuseEfficiencyValidationError,
    ReuseMode,
)
from agentx.execution_metrics import (
    ExecutionMetricsClock,
    ExecutionMetricsRecord,
    ExecutionMetricsRecorder,
    ExecutionMetricsValidationError,
    ModelCallEvent,
)

_T0 = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


class _FakeClock:
    """Deterministic injected clock; advances only when the test says so."""

    __slots__ = ("_now",)

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


def _model_id(name: str = "model-alpha") -> ModelId:
    return ModelId(ProviderId("test-provider"), name)


def _usage(
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    external_cost: Decimal | None = None,
) -> ModelUsage:
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        external_cost=external_cost,
    )


def _recorder(
    *,
    clock: ExecutionMetricsClock | None = None,
    execution_level: ExecutionLevel = ExecutionLevel.L4_PLANNED,
    **kwargs: object,
) -> ExecutionMetricsRecorder:
    return ExecutionMetricsRecorder(
        task_id=TaskId.create(),
        correlation_id=uuid4(),
        execution_level=execution_level,
        clock=clock,
        **kwargs,  # type: ignore[arg-type]
    )


def _verified_payload(detail: str = "postcondition held") -> VerificationPayload:
    return VerificationPayload(passed=True, detail=detail)


# --------------------------------------------------------------------------
# Model-call accounting.
# --------------------------------------------------------------------------


def test_zero_model_call_run_records_zero_and_absence() -> None:
    clock = _FakeClock(_T0)
    recorder = _recorder(clock=clock)
    recorder.start()
    clock.advance(timedelta(seconds=1))
    record = recorder.finish()

    assert record.model_calls == 0
    assert record.model_ids == ()
    assert record.model_input_tokens is None
    assert record.model_output_tokens is None
    assert record.model_tokens is None
    assert record.external_cost is None
    assert record.cost_unit is None


def test_single_model_call_run() -> None:
    clock = _FakeClock(_T0)
    recorder = _recorder(clock=clock)
    recorder.start()
    recorder.record_model_call(
        ModelCallEvent(model_id=_model_id(), usage=_usage(input_tokens=10, output_tokens=5))
    )
    clock.advance(timedelta(milliseconds=250))
    record = recorder.finish()

    assert record.model_calls == 1
    assert record.model_ids == (_model_id(),)
    assert record.model_input_tokens == 10
    assert record.model_output_tokens == 5
    assert record.model_tokens == 15


def test_multiple_model_calls_aggregate_tokens_exactly() -> None:
    clock = _FakeClock(_T0)
    recorder = _recorder(clock=clock)
    recorder.start()
    recorder.record_model_call(
        ModelCallEvent(model_id=_model_id("a"), usage=_usage(input_tokens=10, output_tokens=5))
    )
    recorder.record_model_call(
        ModelCallEvent(model_id=_model_id("b"), usage=_usage(input_tokens=7, output_tokens=3))
    )
    recorder.record_model_call(
        ModelCallEvent(model_id=_model_id("a"), usage=_usage(input_tokens=1, output_tokens=1))
    )
    record = recorder.finish()

    assert record.model_calls == 3
    assert record.model_ids == (_model_id("a"), _model_id("b"), _model_id("a"))
    assert record.model_input_tokens == 18
    assert record.model_output_tokens == 9
    assert record.model_tokens == 27


def test_model_call_event_from_response_uses_canonical_response_facts() -> None:
    response = ModelResponse(
        model_id=_model_id(),
        content=(TextContent(text="ok"),),
        usage=_usage(input_tokens=4, output_tokens=2),
    )
    event = ModelCallEvent.from_response(response)

    assert event.model_id is response.model_id
    assert event.usage is response.usage


def test_missing_usage_preserves_absence() -> None:
    clock = _FakeClock(_T0)
    recorder = _recorder(clock=clock)
    recorder.start()
    # Invocation failed/returned no usage data: still exactly one model call.
    recorder.record_model_call(ModelCallEvent(model_id=_model_id(), usage=None))
    # Invocation with a usage object that reports nothing: absence, not zero.
    recorder.record_model_call(ModelCallEvent(model_id=_model_id(), usage=ModelUsage()))
    record = recorder.finish()

    assert record.model_calls == 2
    assert record.model_input_tokens is None
    assert record.model_output_tokens is None
    assert record.model_tokens is None


def test_total_only_usage_preserves_reported_total() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    recorder.record_model_call(ModelCallEvent(model_id=_model_id(), usage=_usage(total_tokens=100)))
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.model_calls == 1
    assert record.model_input_tokens is None
    assert record.model_output_tokens is None
    assert record.model_tokens == 100


def test_partial_component_coverage_contradiction_fails_closed() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    recorder.record_model_call(
        ModelCallEvent(model_id=_model_id(), usage=_usage(input_tokens=10, output_tokens=5))
    )
    recorder.record_model_call(ModelCallEvent(model_id=_model_id(), usage=_usage(total_tokens=8)))
    with pytest.raises(ExecutionMetricsValidationError, match="contradict"):
        recorder.finish(ended_at=_T0 + timedelta(seconds=1))


def test_model_call_count_overflow_fails_closed() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    max_value = (1 << 63) - 1
    recorder.record_model_call(
        ModelCallEvent(model_id=_model_id(), usage=_usage(input_tokens=max_value))
    )
    with pytest.raises(OverflowError):
        recorder.record_model_call(
            ModelCallEvent(model_id=_model_id(), usage=_usage(input_tokens=max_value))
        )


def test_model_name_never_infers_cost() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    recorder.record_model_call(
        ModelCallEvent(
            model_id=ModelId(ProviderId("premium-pricing"), "very-expensive-model"),
            usage=_usage(input_tokens=3, output_tokens=3),
        )
    )
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.external_cost is None
    assert record.cost_unit is None


def test_model_response_hook_counts_one_call_per_invocation() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    for _ in range(3):
        response = ModelResponse(
            model_id=_model_id(),
            content=(TextContent(text="ok"),),
            usage=_usage(input_tokens=2, output_tokens=1),
        )
        recorder.record_model_call(ModelCallEvent.from_response(response))
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.model_calls == 3
    assert record.model_input_tokens == 6
    assert record.model_output_tokens == 3


# --------------------------------------------------------------------------
# Cost accounting.
# --------------------------------------------------------------------------


def test_explicit_decimal_cost_is_exact() -> None:
    recorder = _recorder(cost_unit="USD")
    recorder.start(started_at=_T0)
    recorder.record_external_cost(Decimal("0.00123456"))
    recorder.record_external_cost(Decimal("0.00000001"))
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.external_cost == Decimal("0.00123457")
    assert record.cost_unit == "USD"


def test_usage_external_cost_aggregates_exactly() -> None:
    recorder = _recorder(cost_unit="USD")
    recorder.start(started_at=_T0)
    recorder.record_model_call(
        ModelCallEvent(
            model_id=_model_id(),
            usage=_usage(input_tokens=1, output_tokens=1, external_cost=Decimal("0.00000003")),
        )
    )
    recorder.record_model_call(
        ModelCallEvent(
            model_id=_model_id(),
            usage=_usage(input_tokens=2, output_tokens=2, external_cost=Decimal("0.00000004")),
        )
    )
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.external_cost == Decimal("0.00000007")
    assert record.cost_unit == "USD"


def test_cost_unavailable_stays_absent() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.external_cost is None
    assert record.cost_unit is None


def test_declared_unit_without_observed_cost_records_absence() -> None:
    recorder = _recorder(cost_unit="USD")
    recorder.start(started_at=_T0)
    recorder.record_model_call(
        ModelCallEvent(model_id=_model_id(), usage=_usage(input_tokens=1, output_tokens=1))
    )
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.external_cost is None
    assert record.cost_unit is None


def test_usage_cost_without_session_unit_fails_closed() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    with pytest.raises(ExecutionMetricsValidationError, match="cost_unit"):
        recorder.record_model_call(
            ModelCallEvent(
                model_id=_model_id(),
                usage=_usage(input_tokens=1, output_tokens=1, external_cost=Decimal("0.01")),
            )
        )


def test_record_external_cost_without_unit_rejected() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    with pytest.raises(ExecutionMetricsValidationError, match="cost_unit"):
        recorder.record_external_cost(Decimal("1"))


def test_negative_or_non_decimal_cost_rejected() -> None:
    recorder = _recorder(cost_unit="USD")
    recorder.start(started_at=_T0)
    with pytest.raises(ExecutionMetricsValidationError):
        recorder.record_external_cost(Decimal("-0.01"))
    with pytest.raises(TypeError):
        recorder.record_external_cost(0.01)  # type: ignore[arg-type] # float money rejected


# --------------------------------------------------------------------------
# Clock and duration semantics.
# --------------------------------------------------------------------------


def test_deterministic_elapsed_duration_with_injected_clock() -> None:
    first = _deterministic_run()
    second = _deterministic_run()

    assert first.started_at == second.started_at == _T0
    assert first.ended_at == second.ended_at == _T0 + timedelta(milliseconds=250)
    assert first.elapsed == second.elapsed == timedelta(milliseconds=250)


def _deterministic_run() -> ExecutionMetricsRecord:
    clock = _FakeClock(_T0)
    recorder = _recorder(clock=clock)
    recorder.start()
    clock.advance(timedelta(milliseconds=250))
    return recorder.finish()


def test_supplied_timestamps_are_deterministic_and_normalized_to_utc() -> None:
    start = datetime(2026, 9, 8, 14, 30, 0, tzinfo=UTC)
    end = datetime(2026, 9, 8, 14, 30, 2, tzinfo=UTC)
    recorder = _recorder()
    recorder.start(started_at=start)
    record = recorder.finish(ended_at=end)

    assert record.started_at == start
    assert record.ended_at == end
    assert record.elapsed == timedelta(seconds=2)


def test_non_utc_offset_normalized_to_utc() -> None:
    start_local = datetime(2026, 9, 8, 14, 0, 0, tzinfo=_FixedOffset(hours=2))
    recorder = _recorder()
    recorder.start(started_at=start_local)
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.started_at == _T0
    assert record.elapsed == timedelta(seconds=1)


class _FixedOffset(tzinfo):
    """Deterministic fixed UTC offset for normalization checks."""

    def __init__(self, hours: int) -> None:
        self._hours = hours

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(hours=self._hours)

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "fixed"


def test_supplied_elapsed_consistent_with_clock() -> None:
    clock = _FakeClock(_T0)
    recorder = _recorder(clock=clock)
    recorder.start()
    clock.advance(timedelta(milliseconds=250))
    record = recorder.finish(elapsed=timedelta(milliseconds=250))

    assert record.elapsed == timedelta(milliseconds=250)


def test_supplied_elapsed_contradicting_clock_fails_closed() -> None:
    clock = _FakeClock(_T0)
    recorder = _recorder(clock=clock)
    recorder.start()
    clock.advance(timedelta(milliseconds=250))
    with pytest.raises(ExecutionMetricsValidationError, match="exactly match"):
        recorder.finish(elapsed=timedelta(milliseconds=249))


def test_negative_supplied_duration_rejected() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    with pytest.raises(ExecutionMetricsValidationError, match="negative"):
        recorder.finish(elapsed=timedelta(milliseconds=-1))


def test_reversed_clock_ordering_fails_closed() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0 + timedelta(seconds=5))
    with pytest.raises(ExecutionMetricsValidationError, match="earlier"):
        recorder.finish(ended_at=_T0)


def test_reversed_injected_clock_fails_closed() -> None:
    clock = _FakeClock(_T0 + timedelta(seconds=5))
    recorder = _recorder(clock=clock)
    recorder.start()
    clock._now = _T0  # hostile/host-adjacent clock rewinds
    with pytest.raises(ExecutionMetricsValidationError, match="earlier"):
        recorder.finish()


def test_ended_without_started_fails_closed() -> None:
    recorder = _recorder()
    with pytest.raises(ExecutionMetricsValidationError, match="started_at"):
        recorder.finish(ended_at=_T0 + timedelta(seconds=1))


def test_duration_only_run_without_timestamps() -> None:
    recorder = _recorder()
    record = recorder.finish(elapsed=timedelta(milliseconds=120))

    assert record.started_at is None
    assert record.ended_at is None
    assert record.elapsed == timedelta(milliseconds=120)


def test_no_clock_and_no_timing_fails_closed_on_start() -> None:
    recorder = _recorder()
    with pytest.raises(ExecutionMetricsValidationError, match="no supplied started_at"):
        recorder.start()


def test_clock_supplied_timestamp_wins_over_clock() -> None:
    clock = _FakeClock(_T0 + timedelta(seconds=99))
    recorder = _recorder(clock=clock)
    recorder.start(started_at=_T0)
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.started_at == _T0
    assert record.elapsed == timedelta(seconds=1)


def test_naive_timestamp_rejected() -> None:
    recorder = _recorder()
    with pytest.raises(ExecutionMetricsValidationError, match="timezone-aware"):
        recorder.start(started_at=datetime(2026, 9, 8, 12, 0, 0))


def test_naive_injected_clock_rejected() -> None:
    class _NaiveClock:
        def now(self) -> datetime:
            return datetime(2026, 9, 8, 12, 0, 0)

    recorder = _recorder(clock=_NaiveClock())
    with pytest.raises(ExecutionMetricsValidationError, match="timezone-aware"):
        recorder.start()


def test_clock_without_now_rejected() -> None:
    class _NotAClock:
        pass

    with pytest.raises(TypeError, match="ExecutionMetricsClock"):
        _recorder(clock=_NotAClock())  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Identity, level, procedure, and action binding.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level",
    [
        ExecutionLevel.L0_CACHE,
        ExecutionLevel.L1_DIRECT,
        ExecutionLevel.L2_COMPILED,
        ExecutionLevel.L3_GUIDED,
        ExecutionLevel.L4_PLANNED,
        ExecutionLevel.L5_EXPLORATORY,
    ],
)
def test_execution_level_binding(level: ExecutionLevel) -> None:
    recorder = _recorder(execution_level=level)
    recorder.start(started_at=_T0)
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.execution_level is level
    assert record.task_id is recorder.task_id
    assert record.correlation_id is recorder.correlation_id


def test_invalid_execution_level_rejected() -> None:
    with pytest.raises(TypeError, match="ExecutionLevel"):
        _recorder(execution_level="L4")  # type: ignore[arg-type]


def test_procedure_id_and_revision_binding() -> None:
    procedure = ProcedureRevisionRef(ProcedureId.create(), revision=7)
    recorder = _recorder(procedure=procedure)
    recorder.start(started_at=_T0)
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.procedure is procedure
    assert record.procedure.procedure_id is procedure.procedure_id
    assert record.procedure.revision == 7


def test_procedure_without_positive_revision_rejected() -> None:
    with pytest.raises(ReuseEfficiencyValidationError):
        ProcedureRevisionRef(ProcedureId.create(), revision=0)


def test_machine_action_count() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    recorder.record_machine_action()
    recorder.record_machine_action()
    recorder.record_machine_action(count=3)
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.machine_actions == 5


@pytest.mark.parametrize("bad", [0, -1, 1.0, True, "1"])
def test_invalid_machine_action_count_rejected(bad: object) -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    with pytest.raises((TypeError, ExecutionMetricsValidationError)):
        recorder.record_machine_action(count=bad)  # type: ignore[arg-type]


def test_nil_correlation_id_rejected() -> None:
    with pytest.raises(ExecutionMetricsValidationError, match="nil"):
        ExecutionMetricsRecorder(
            task_id=TaskId.create(),
            correlation_id=UUID(int=0),
            execution_level=ExecutionLevel.L1_DIRECT,
        )


# --------------------------------------------------------------------------
# Outcome truth boundary.
# --------------------------------------------------------------------------


def test_verified_run_represented_with_typed_evidence() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    recorder.set_outcome(
        ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
        verification=_verified_payload(),
        verification_source="tests.unit.canonical_verifier",
        verification_reference=uuid4(),
    )
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.outcome is ExecutionEvidenceOutcome.VERIFIED_SUCCESS
    assert record.verified_success is True


def test_failed_run_represented_without_changing_truth() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    recorder.set_outcome(ExecutionEvidenceOutcome.FAILED)
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.outcome is ExecutionEvidenceOutcome.FAILED
    assert record.verification is None
    assert record.verified_success is False


def test_unverified_run_is_the_default_and_cannot_carry_verification() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.outcome is ExecutionEvidenceOutcome.UNVERIFIED

    recorder2 = _recorder()
    recorder2.start(started_at=_T0)
    with pytest.raises(ExecutionMetricsValidationError, match="unverified"):
        recorder2.set_outcome(
            ExecutionEvidenceOutcome.UNVERIFIED,
            verification=VerificationPayload(passed=False, detail="nope"),
            verification_source="tests.unit",
            verification_reference=uuid4(),
        )


def test_verified_success_requires_passed_typed_evidence() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    with pytest.raises(ExecutionMetricsValidationError, match="passed=True"):
        recorder.set_outcome(
            ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
            verification=VerificationPayload(passed=False, detail="nope"),
            verification_source="tests.unit",
            verification_reference=uuid4(),
        )


def test_passed_verification_inconsistent_with_failed_outcome() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    with pytest.raises(ExecutionMetricsValidationError, match="inconsistent"):
        recorder.set_outcome(
            ExecutionEvidenceOutcome.FAILED,
            verification=_verified_payload(),
            verification_source="tests.unit",
            verification_reference=uuid4(),
        )


def test_outcome_recorded_only_once() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    recorder.set_outcome(ExecutionEvidenceOutcome.FAILED)
    with pytest.raises(ExecutionMetricsValidationError, match="already recorded"):
        recorder.set_outcome(ExecutionEvidenceOutcome.UNVERIFIED)


def test_verification_source_reference_pairing_enforced() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    with pytest.raises(ExecutionMetricsValidationError, match="source and reference"):
        recorder.set_outcome(
            ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
            verification=_verified_payload(),
            verification_source="tests.unit",
        )


# --------------------------------------------------------------------------
# Session lifecycle and record invariants.
# --------------------------------------------------------------------------


def test_start_twice_rejected() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    with pytest.raises(ExecutionMetricsValidationError, match="already started"):
        recorder.start(started_at=_T0 + timedelta(seconds=1))


def test_events_after_finish_rejected() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    recorder.finish(ended_at=_T0 + timedelta(seconds=1))
    with pytest.raises(ExecutionMetricsValidationError, match="already finished"):
        recorder.record_model_call(ModelCallEvent(model_id=_model_id()))
    with pytest.raises(ExecutionMetricsValidationError, match="already finished"):
        recorder.record_machine_action()
    with pytest.raises(ExecutionMetricsValidationError, match="already finished"):
        recorder.record_external_cost(Decimal("1"))
    with pytest.raises(ExecutionMetricsValidationError, match="already finished"):
        recorder.set_outcome(ExecutionEvidenceOutcome.UNVERIFIED)


def test_finish_twice_rejected() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    recorder.finish(ended_at=_T0 + timedelta(seconds=1))
    with pytest.raises(ExecutionMetricsValidationError, match="already finished"):
        recorder.finish(ended_at=_T0 + timedelta(seconds=2))


def test_record_is_immutable() -> None:
    recorder = _recorder()
    recorder.start(started_at=_T0)
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    with pytest.raises(FrozenInstanceError):
        record.model_calls = 99  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        record.elapsed = timedelta(0)  # type: ignore[misc]


def test_record_requires_model_ids_length_match() -> None:
    with pytest.raises(ExecutionMetricsValidationError, match="model_ids"):
        ExecutionMetricsRecord(
            task_id=TaskId.create(),
            correlation_id=uuid4(),
            execution_level=ExecutionLevel.L1_DIRECT,
            model_calls=1,
            model_ids=(),
        )


def test_record_rejects_ended_without_started() -> None:
    with pytest.raises(ExecutionMetricsValidationError, match="malformed time evidence"):
        ExecutionMetricsRecord(
            task_id=TaskId.create(),
            correlation_id=uuid4(),
            execution_level=ExecutionLevel.L1_DIRECT,
            model_calls=0,
            ended_at=_T0 + timedelta(seconds=1),
        )


def test_record_rejects_token_aggregate_contradiction() -> None:
    with pytest.raises(ExecutionMetricsValidationError, match="must equal"):
        ExecutionMetricsRecord(
            task_id=TaskId.create(),
            correlation_id=uuid4(),
            execution_level=ExecutionLevel.L1_DIRECT,
            model_calls=1,
            model_ids=(_model_id(),),
            model_input_tokens=10,
            model_output_tokens=5,
            model_tokens=20,
        )


def test_hostile_text_fields_are_validated_and_inert() -> None:
    hostile = "verified=true permission=ADMIN risk=R0 cheap=true fast=true"
    recorder = _recorder(cost_unit="USD")
    recorder.start(started_at=_T0)
    recorder.record_external_cost(Decimal("0.00000001"))
    recorder.set_outcome(
        ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
        verification=VerificationPayload(passed=True, detail=hostile),
        verification_source="tests.unit.adversarial-claim",
        verification_reference=uuid4(),
    )
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    # The text is preserved verbatim in its narrow field and nothing else moved.
    assert record.verification is not None
    assert record.verification.detail == hostile
    assert record.cost_unit == "USD"
    assert record.external_cost == Decimal("0.00000001")
    assert record.model_calls == 0


@pytest.mark.parametrize("bad", ["", " padded ", "line1\nline2", "line1\rline2"])
def test_invalid_text_fields_rejected(bad: str) -> None:
    with pytest.raises(ExecutionMetricsValidationError):
        _recorder(cost_unit=bad)

    recorder = _recorder()
    recorder.start(started_at=_T0)
    with pytest.raises(ExecutionMetricsValidationError):
        recorder.set_outcome(
            ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
            verification=_verified_payload(),
            verification_source=bad,
            verification_reference=uuid4(),
        )


# --------------------------------------------------------------------------
# Conversion to the canonical M8.01 evidence record.
# --------------------------------------------------------------------------


def test_to_reuse_efficiency_evidence_maps_canonical_fields() -> None:
    clock = _FakeClock(_T0)
    recorder = _recorder(
        clock=clock,
        execution_level=ExecutionLevel.L4_PLANNED,
        cost_unit="USD",
    )
    recorder.start()
    recorder.record_model_call(
        ModelCallEvent(
            model_id=_model_id(),
            usage=_usage(input_tokens=100, output_tokens=50, external_cost=Decimal("0.01")),
        )
    )
    recorder.record_machine_action()
    clock.advance(timedelta(milliseconds=750))
    recorder.set_outcome(
        ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
        verification=_verified_payload(),
        verification_source="tests.unit.verifier",
        verification_reference=uuid4(),
    )
    record = recorder.finish()

    episode_id = EpisodeId.create()
    evidence_reference = uuid4()
    evidence = record.to_reuse_efficiency_evidence(
        episode_id=episode_id,
        mode=ReuseMode.NOVEL_PLAN,
        evidence_source="tests.unit",
        evidence_reference=evidence_reference,
    )

    assert isinstance(evidence, ExecutionEfficiencyEvidence)
    assert evidence.task_id is record.task_id
    assert evidence.episode_id is episode_id
    assert evidence.correlation_id is record.correlation_id
    assert evidence.mode is ReuseMode.NOVEL_PLAN
    assert evidence.outcome is ExecutionEvidenceOutcome.VERIFIED_SUCCESS
    assert evidence.evidence_source == "tests.unit"
    assert evidence.evidence_reference is evidence_reference
    assert evidence.started_at == record.started_at
    assert evidence.ended_at == record.ended_at
    assert evidence.elapsed == record.elapsed == timedelta(milliseconds=750)
    assert evidence.model_calls == 1
    assert evidence.model_input_tokens == 100
    assert evidence.model_output_tokens == 50
    assert evidence.model_tokens == 150
    assert evidence.machine_actions == 1
    assert evidence.external_cost == Decimal("0.01")
    assert evidence.cost_unit == "USD"
    assert evidence.research_queries is None
    assert evidence.repair_attempts is None
    assert evidence.verification is record.verification


def test_duration_only_record_converts_with_elapsed_only() -> None:
    recorder = _recorder()
    record = recorder.finish(elapsed=timedelta(milliseconds=120))

    evidence = record.to_reuse_efficiency_evidence(
        episode_id=EpisodeId.create(),
        mode=ReuseMode.EXPLORATORY,
        evidence_source="tests.unit",
        evidence_reference=uuid4(),
    )

    assert evidence.started_at is None
    assert evidence.ended_at is None
    assert evidence.elapsed == timedelta(milliseconds=120)


def test_conversion_rejects_non_canonical_inputs() -> None:
    recorder = _recorder()
    record = recorder.finish(elapsed=timedelta(milliseconds=1))
    with pytest.raises(TypeError, match="EpisodeId"):
        record.to_reuse_efficiency_evidence(
            episode_id=TaskId.create(),  # type: ignore[arg-type]
            mode=ReuseMode.NOVEL_PLAN,
            evidence_source="tests.unit",
            evidence_reference=uuid4(),
        )
    with pytest.raises(TypeError, match="ReuseMode"):
        record.to_reuse_efficiency_evidence(
            episode_id=EpisodeId.create(),
            mode="NOVEL_PLAN",  # type: ignore[arg-type]
            evidence_source="tests.unit",
            evidence_reference=uuid4(),
        )


def test_conversion_keeps_m801_mode_procedure_pairing_rules() -> None:
    procedure = ProcedureRevisionRef(ProcedureId.create(), revision=2)
    recorder = _recorder(procedure=procedure)
    recorder.start(started_at=_T0)
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    with pytest.raises(ReuseEfficiencyValidationError, match="must not claim"):
        record.to_reuse_efficiency_evidence(
            episode_id=EpisodeId.create(),
            mode=ReuseMode.NOVEL_PLAN,
            evidence_source="tests.unit",
            evidence_reference=uuid4(),
        )
    paired = record.to_reuse_efficiency_evidence(
        episode_id=EpisodeId.create(),
        mode=ReuseMode.PROCEDURE_REUSE,
        evidence_source="tests.unit",
        evidence_reference=uuid4(),
    )
    assert paired.procedure is procedure
