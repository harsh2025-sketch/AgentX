"""N2.13 integration tests: instrumentation around canonical execution paths.

These tests instrument *external to* the canonical execution machinery: the
recorder is driven by test code at the observed boundaries, and the governed
path (A1.10 loop / A2.04 Executor / M8.01 comparison) is exercised unchanged.
No production file is modified for instrumentation; all timing is
deterministic (fake clock or supplied durations).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from agentx.capabilities.executor import ExecutorRequest
from agentx.capabilities.runtime import LoopOutcome
from agentx.cognition.model_provider import (
    ModelId,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderDescriptor,
    ProviderFailureKind,
    ProviderId,
    TextContent,
    provider_failure,
)
from agentx.cognition.router import ExecutionLevel
from agentx.core.errors import AgentXError
from agentx.core.events import VerificationPayload
from agentx.core.ids import EpisodeId, ProcedureId, TaskId
from agentx.core.result import Result
from agentx.core.reuse_efficiency import (
    EfficiencyMetric,
    ExecutionEvidenceOutcome,
    ProcedureRevisionRef,
    ReuseEfficiencyDisposition,
    ReuseMode,
    compare_execution_efficiency,
)
from agentx.core.tasks import Task
from agentx.execution_metrics import (
    ExecutionMetricsRecord,
    ExecutionMetricsRecorder,
    ModelCallEvent,
)
from tests.support.demo_capability import NoteWriteParams, write_request
from tests.support.orchestration_harness import OrchestrationHarness

_T0 = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


class _FakeClock:
    """Deterministic injected clock for the measurement session."""

    __slots__ = ("_now",)

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


def _cold_run(task_id: TaskId) -> ExecutionMetricsRecord:
    """Instrument one expensive cold (reasoning-heavy) run."""

    clock = _FakeClock(_T0)
    recorder = ExecutionMetricsRecorder(
        task_id=task_id,
        correlation_id=uuid4(),
        execution_level=ExecutionLevel.L4_PLANNED,
        clock=clock,
        cost_unit="USD",
    )
    recorder.start()
    for model_name in ("planner", "planner", "critic"):
        model_id = ModelId(ProviderId("test-provider"), model_name)
        recorder.record_model_call(
            ModelCallEvent(
                model_id=model_id,
                usage=ModelUsage(
                    input_tokens=100,
                    output_tokens=50,
                    external_cost=Decimal("0.01"),
                ),
            )
        )
    recorder.record_machine_action()
    recorder.record_machine_action()
    clock.advance(timedelta(milliseconds=3200))
    recorder.set_outcome(
        ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
        verification=VerificationPayload(passed=True, detail="cold run verified"),
        verification_source="tests.integration.verifier",
        verification_reference=uuid4(),
    )
    return recorder.finish()


def _warm_run(task_id: TaskId) -> ExecutionMetricsRecord:
    """Instrument one cheap warm (procedure-reuse) run."""

    clock = _FakeClock(_T0)
    recorder = ExecutionMetricsRecorder(
        task_id=task_id,
        correlation_id=uuid4(),
        execution_level=ExecutionLevel.L2_COMPILED,
        procedure=ProcedureRevisionRef(ProcedureId.create(), revision=3),
        clock=clock,
    )
    recorder.start()
    recorder.record_machine_action()
    clock.advance(timedelta(milliseconds=150))
    recorder.set_outcome(
        ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
        verification=VerificationPayload(passed=True, detail="warm run verified"),
        verification_source="tests.integration.verifier",
        verification_reference=uuid4(),
    )
    return recorder.finish()


def test_cold_vs_warm_reuse_measurement_comparisons_via_m801() -> None:
    task_id = TaskId.create()
    cold = _cold_run(task_id)
    warm = _warm_run(task_id)

    cold_evidence = cold.to_reuse_efficiency_evidence(
        episode_id=EpisodeId.create(),
        mode=ReuseMode.NOVEL_PLAN,
        evidence_source="tests.integration",
        evidence_reference=uuid4(),
    )
    warm_evidence = warm.to_reuse_efficiency_evidence(
        episode_id=EpisodeId.create(),
        mode=ReuseMode.PROCEDURE_REUSE,
        evidence_source="tests.integration",
        evidence_reference=uuid4(),
    )

    comparison = compare_execution_efficiency(cold_evidence, warm_evidence)

    assert comparison.disposition is ReuseEfficiencyDisposition.IMPROVED
    model_calls = comparison.metric(EfficiencyMetric.MODEL_CALLS)
    assert model_calls is not None
    assert model_calls.baseline == Decimal(3)
    assert model_calls.reuse == Decimal(0)
    # The warm run reported no cost and no token usage: absence stays absence
    # (never invented as zero), so those dimensions stay unavailable.
    assert comparison.metric(EfficiencyMetric.EXTERNAL_COST) is None
    assert comparison.metric(EfficiencyMetric.MODEL_TOKENS) is None
    duration = comparison.metric(EfficiencyMetric.DURATION_MICROSECONDS)
    assert duration is not None
    assert duration.baseline == Decimal(3_200_000)
    assert duration.reuse == Decimal(150_000)


def test_instruments_real_governed_closed_loop_run() -> None:
    harness = OrchestrationHarness()
    task = Task.create(objective="write the demo note for key alpha")
    context = harness.make_context(task)
    clock = _FakeClock(_T0)

    recorder = ExecutionMetricsRecorder(
        task_id=task.task_id,
        correlation_id=context.correlation_id,
        execution_level=ExecutionLevel.L1_DIRECT,
        clock=clock,
    )
    recorder.start()

    result = harness.executor.execute(
        ExecutorRequest(
            task=task,
            capability_request=write_request(NoteWriteParams(key="alpha", value="v1")),
            context=context,
        )
    )
    assert result.is_success, "governed run unexpectedly failed"
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFIED

    # The canonical loop performs exactly one machine action and zero model
    # calls; the recorder must observe exactly those measured facts.
    recorder.record_machine_action(count=harness.budget.snapshot().machine_actions)
    clock.advance(timedelta(seconds=1))
    assert outcome.verification is not None
    recorder.set_outcome(
        ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
        verification=VerificationPayload(
            passed=outcome.verification.passed, detail=outcome.verification.detail
        ),
        verification_source="tests.integration.canonical_loop",
        verification_reference=uuid4(),
    )
    record = recorder.finish()

    assert record.model_calls == 0
    assert record.model_tokens is None
    assert record.machine_actions == 1
    assert record.elapsed == timedelta(seconds=1)
    assert record.verified_success is True
    # The record is inert evidence: the canonical budget state is untouched
    # by the measurement itself.
    assert harness.budget.snapshot().model_calls == 0


class _FakeReasoningProvider:
    """Deterministic in-test provider behind the model-invocation boundary."""

    __slots__ = ("_calls", "_model_id", "_provider_id")

    def __init__(self) -> None:
        self._calls = 0
        self._provider_id = ProviderId("test-provider")
        self._model_id = ModelId(self._provider_id, "reasoner")

    @property
    def descriptor(self) -> ProviderDescriptor:
        raise AssertionError("descriptor is not needed for the hook boundary")

    def invoke(self, request: ModelRequest) -> Result[ModelResponse, AgentXError]:
        if request.model_id != self._model_id:
            return Result.failure(
                provider_failure(
                    ProviderFailureKind.INVALID_REQUEST,
                    provider_id=self._provider_id,
                    message="unknown model",
                )
            )
        self._calls += 1
        return Result.success(
            ModelResponse(
                model_id=self._model_id,
                content=(TextContent(text=f"answer {self._calls}"),),
                usage=ModelUsage(input_tokens=3, output_tokens=2),
            )
        )


def test_model_call_hook_counts_exactly_one_per_provider_invocation() -> None:
    provider = _FakeReasoningProvider()
    assert isinstance(provider, ModelProvider)

    request = ModelRequest(
        model_id=ModelId(ProviderId("test-provider"), "reasoner"),
        content=(TextContent(text="plan the run"),),
    )
    clock = _FakeClock(_T0)
    recorder = ExecutionMetricsRecorder(
        task_id=TaskId.create(),
        correlation_id=uuid4(),
        execution_level=ExecutionLevel.L4_PLANNED,
        clock=clock,
        cost_unit="USD",
    )
    recorder.start()

    # Instrumentation hook at the provider boundary: one explicit event per
    # actual canonical invocation, usage from the canonical response.
    for _ in range(3):
        result = provider.invoke(request)
        assert result.is_success
        recorder.record_model_call(ModelCallEvent.from_response(result.unwrap()))
    # One failed invocation: still a real invocation, no usage reported.
    bad_request = ModelRequest(
        model_id=ModelId(ProviderId("test-provider"), "other-model"),
        content=(TextContent(text="never served"),),
    )
    failed = provider.invoke(bad_request)
    assert failed.is_failure
    recorder.record_model_call(ModelCallEvent(model_id=bad_request.model_id, usage=None))

    clock.advance(timedelta(milliseconds=900))
    record = recorder.finish()

    assert provider._calls == 3
    assert record.model_calls == 4
    assert record.model_input_tokens == 9
    assert record.model_output_tokens == 6
    assert record.model_tokens == 15
