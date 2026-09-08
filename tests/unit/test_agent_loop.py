"""A2.10 unit tests: contract shape, classification, and bounded control flow.

These tests exercise the orchestration contracts directly with deterministic
scripted strategy results. The full canonical governed path (A1.09 registry ->
C1.07 gate -> C1.09 stop -> C1.08 budget -> execute -> verify) is exercised by
``tests/integration/test_agent_loop_end_to_end.py``; here the focus is the
composition contract itself.

Nothing in this module sleeps, reads a wall clock, calls a model, performs I/O,
or uses randomness.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from agentx.agent_loop import (
    AGENT_LOOP_SOURCE,
    MAX_CONFIGURABLE_TOTAL_ATTEMPTS,
    AgentLoop,
    AttemptDisposition,
    AttemptRecord,
    OrchestrationLimits,
    OrchestrationOutcome,
    OrchestrationRequest,
    OrchestrationRequestError,
    OrchestrationStatus,
    OrchestrationStopReason,
    StrategyRegistry,
    StrategyResult,
)
from agentx.capabilities.abi import (
    CapabilityObservation,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.capabilities.verifier import (
    RequirementEvaluation,
    VerificationRequirement,
    Verifier,
)
from agentx.cognition.anti_loop import (
    LoopGuard,
    LoopGuardDecision,
    LoopGuardLimits,
    LoopGuardTrigger,
)
from agentx.cognition.escalation import EscalationAction, ExecutionLevelEscalator
from agentx.cognition.router import (
    CANONICAL_EXECUTION_LEVELS,
    ExecutionLevel,
    ExecutionLevelRouter,
    RoutingEvidence,
)
from agentx.cognition.task_manager import TaskManager
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import CancellationSource, Deadline, ExecutionContext
from agentx.core.result import Result
from agentx.core.task_state import LEGAL_TASK_TRANSITIONS, is_terminal
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.resource_budget import ResourceEnvelope, ResourceUsage
from tests.support.orchestration_harness import (
    FixedClock,
    HostileStrategy,
    OrchestrationHarness,
    RecordingStrategy,
    UnavailableStrategy,
    default_limits,
    make_envelope,
)

# ---------------------------------------------------------------------------
# Deterministic canonical-outcome builders (no I/O, no model, no randomness).
# ---------------------------------------------------------------------------


def _observation(**data: object) -> CapabilityObservation:
    payload: dict[str, object] = {"stored": True, "key": "alpha", "value": "v1"}
    payload.update(data)
    return CapabilityObservation(summary="deterministic fixture observation", data=payload)


def verified_outcome(*, task: Task | None = None) -> ClosedLoopOutcome:
    """A canonical outcome carrying real A1.10 verification evidence."""
    base = task if task is not None else Task.create(objective="fixture objective")
    succeeded = base if base.status is TaskStatus.SUCCEEDED else _to_succeeded(base)
    observation = _observation()
    return ClosedLoopOutcome(
        task=succeeded,
        kind=LoopOutcome.VERIFIED,
        error=None,
        execution=ExecutionResult(
            succeeded=True, message="fixture executed", observation=observation
        ),
        observation=observation,
        verification=VerificationResult(passed=True, detail="fixture postcondition holds"),
        budget_usage=ResourceUsage.zero(),
    )


def _to_succeeded(task: Task) -> Task:
    from agentx.core.task_state import transition_task

    running = transition_task(task, TaskStatus.RUNNING)
    return transition_task(running, TaskStatus.SUCCEEDED)


def _to_failed(task: Task) -> Task:
    from agentx.core.task_state import transition_task

    running = transition_task(task, TaskStatus.RUNNING)
    return transition_task(running, TaskStatus.FAILED)


def unverified_outcome(
    *,
    kind: LoopOutcome = LoopOutcome.VERIFICATION_FAILED,
    verification: VerificationResult | None = None,
    error: AgentXError | None = None,
) -> ClosedLoopOutcome:
    """A canonical outcome that did NOT reach canonical verified success."""
    observation = _observation()
    return ClosedLoopOutcome(
        task=_to_failed(Task.create(objective="fixture objective")),
        kind=kind,
        error=error
        or AgentXError(
            code="runtime.verification_failed",
            message="fixture postcondition not met",
            category=ErrorCategory.VERIFICATION,
            retryability=Retryability.NON_RETRYABLE,
        ),
        execution=ExecutionResult(
            succeeded=True, message="fixture executed", observation=observation
        ),
        observation=observation,
        verification=verification,
        budget_usage=ResourceUsage.zero(),
    )


def ok(_attempt: int, _level: ExecutionLevel) -> StrategyResult:
    return StrategyResult.executed(
        Result[ClosedLoopOutcome, AgentXError].success(verified_outcome())
    )


def not_verified(_attempt: int, _level: ExecutionLevel) -> StrategyResult:
    return StrategyResult.executed(
        Result[ClosedLoopOutcome, AgentXError].success(unverified_outcome())
    )


def failure(code: str, category: ErrorCategory) -> Callable[[int, ExecutionLevel], StrategyResult]:
    def _factory(_attempt: int, _level: ExecutionLevel) -> StrategyResult:
        return StrategyResult.executed(
            Result[ClosedLoopOutcome, AgentXError].failure(
                AgentXError(
                    code=code,
                    message="fixture refusal",
                    category=category,
                    retryability=Retryability.NON_RETRYABLE,
                )
            )
        )

    return _factory


def build_loop(
    strategies: dict[ExecutionLevel, object],
    *,
    task_manager: TaskManager | None = None,
) -> tuple[AgentLoop, TaskManager]:
    manager = task_manager if task_manager is not None else TaskManager()
    loop = AgentLoop(task_manager=manager, strategies=StrategyRegistry(strategies))  # type: ignore[arg-type]
    return loop, manager


def build_request(
    manager: TaskManager,
    *,
    routing_evidence: RoutingEvidence | None = None,
    requirement: VerificationRequirement | None = None,
    limits: OrchestrationLimits | None = None,
    cancellation: CancellationSource | None = None,
    deadline: Deadline | None = None,
    objective: str = "write the demo note for key alpha",
) -> OrchestrationRequest:
    task = manager.create(objective)
    source = cancellation if cancellation is not None else CancellationSource()
    from uuid import uuid4

    context = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=source.token,
        task_id=task.task_id,
        deadline=deadline,
    )
    return OrchestrationRequest(
        task=task,
        context=context,
        routing_evidence=(
            routing_evidence
            if routing_evidence is not None
            else RoutingEvidence(deterministic_direct_path=True)
        ),
        requirement=(
            requirement if requirement is not None else VerificationRequirement({"stored": True})
        ),
        limits=limits if limits is not None else default_limits(),
    )


# ---------------------------------------------------------------------------
# 1. Verified first-attempt success.
# ---------------------------------------------------------------------------


def test_verified_first_attempt_success() -> None:
    """A single verified attempt succeeds and stops immediately."""
    strategy = RecordingStrategy(ok)
    loop, manager = build_loop({ExecutionLevel.L1_DIRECT: strategy})
    request = build_request(manager)

    result = loop.run(request)

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.status is OrchestrationStatus.SUCCEEDED
    assert outcome.stop_reason is OrchestrationStopReason.VERIFIED
    assert outcome.verified is True
    assert outcome.attempt_count == 1
    assert outcome.attempts[0].disposition is AttemptDisposition.VERIFIED_SUCCESS
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert manager.require(request.task.task_id).status is TaskStatus.SUCCEEDED
    assert strategy.attempts == 1


# ---------------------------------------------------------------------------
# 2/3. I1: action success and observations are never success.
# ---------------------------------------------------------------------------


def test_action_success_but_verification_failure_is_not_success() -> None:
    """Execution succeeded, canonical verification failed => NOT success."""

    def scripted(_attempt: int, _level: ExecutionLevel) -> StrategyResult:
        return StrategyResult.executed(
            Result[ClosedLoopOutcome, AgentXError].success(
                unverified_outcome(
                    kind=LoopOutcome.VERIFICATION_FAILED,
                    verification=VerificationResult(passed=False, detail="postcondition absent"),
                )
            )
        )

    loop, manager = build_loop({level: RecordingStrategy(scripted) for level in ExecutionLevel})
    request = build_request(manager)

    outcome = loop.run(request).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.verified is False
    assert outcome.task.status is not TaskStatus.SUCCEEDED
    assert all(not record.verified for record in outcome.attempts)


def test_observation_without_verification_is_not_success() -> None:
    """An observation with no canonical verdict can never be success."""

    def scripted(_attempt: int, _level: ExecutionLevel) -> StrategyResult:
        return StrategyResult.executed(
            Result[ClosedLoopOutcome, AgentXError].success(
                unverified_outcome(kind=LoopOutcome.EXECUTION_FAILED, verification=None)
            )
        )

    loop, manager = build_loop({level: RecordingStrategy(scripted) for level in ExecutionLevel})
    request = build_request(manager)

    outcome = loop.run(request).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    for record in outcome.attempts:
        assert record.outcome is not None
        assert record.outcome.observation is not None
        assert record.outcome.verification is None
        assert record.disposition is AttemptDisposition.UNVERIFIED


def test_verified_kind_without_verdict_is_not_success() -> None:
    """A VERIFIED kind with a missing verdict still fails the I1 gate."""

    def scripted(_attempt: int, _level: ExecutionLevel) -> StrategyResult:
        observation = _observation()
        forged = ClosedLoopOutcome(
            task=_to_succeeded(Task.create(objective="forged")),
            kind=LoopOutcome.VERIFIED,
            error=None,
            execution=ExecutionResult(succeeded=True, message="forged", observation=observation),
            observation=observation,
            verification=None,
            budget_usage=ResourceUsage.zero(),
        )
        return StrategyResult.executed(Result[ClosedLoopOutcome, AgentXError].success(forged))

    loop, manager = build_loop({level: RecordingStrategy(scripted) for level in ExecutionLevel})
    outcome = loop.run(build_request(manager)).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.task.status is TaskStatus.FAILED


def test_verified_kind_with_failed_verdict_is_not_success() -> None:
    """A VERIFIED kind carrying passed=False still fails the I1 gate."""

    def scripted(_attempt: int, _level: ExecutionLevel) -> StrategyResult:
        observation = _observation()
        forged = ClosedLoopOutcome(
            task=_to_succeeded(Task.create(objective="forged")),
            kind=LoopOutcome.VERIFIED,
            error=None,
            execution=ExecutionResult(succeeded=True, message="forged", observation=observation),
            observation=observation,
            verification=VerificationResult(passed=False, detail="did not pass"),
            budget_usage=ResourceUsage.zero(),
        )
        return StrategyResult.executed(Result[ClosedLoopOutcome, AgentXError].success(forged))

    loop, manager = build_loop({level: RecordingStrategy(scripted) for level in ExecutionLevel})
    outcome = loop.run(build_request(manager)).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED


def test_unsatisfied_explicit_requirement_is_not_success() -> None:
    """Canonical verification alone is insufficient: A2.05 must also agree."""
    strategy = RecordingStrategy(ok)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(
        manager,
        requirement=VerificationRequirement({"stored": True, "key": "not-alpha"}),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.attempts[0].disposition is AttemptDisposition.UNVERIFIED
    evaluation = outcome.attempts[0].evaluation
    assert evaluation is not None
    assert evaluation.satisfied is False
    assert evaluation.unmet_conditions


# ---------------------------------------------------------------------------
# 4/5. Initial routing comes from A2.07 only.
# ---------------------------------------------------------------------------


def test_initial_l0_route_comes_from_a207() -> None:
    """A verified reusable result routes the first attempt to L0_CACHE."""
    strategy = RecordingStrategy(ok)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(
        manager, routing_evidence=RoutingEvidence(verified_reusable_result=True)
    )

    outcome = loop.run(request).unwrap()

    assert outcome.initial_level is ExecutionLevel.L0_CACHE
    assert outcome.attempts[0].level is ExecutionLevel.L0_CACHE
    assert strategy.calls[0][0] is ExecutionLevel.L0_CACHE
    assert ExecutionLevelRouter().route(request.routing_evidence).level is ExecutionLevel.L0_CACHE


def test_initial_l5_route_comes_from_a207_absence_of_evidence() -> None:
    """No positive evidence fails closed to L5_EXPLORATORY as the initial level."""
    strategy = RecordingStrategy(ok)
    loop, manager = build_loop({ExecutionLevel.L5_EXPLORATORY: strategy})
    request = build_request(manager, routing_evidence=RoutingEvidence())

    outcome = loop.run(request).unwrap()

    assert outcome.initial_level is ExecutionLevel.L5_EXPLORATORY
    assert outcome.attempts[0].level is ExecutionLevel.L5_EXPLORATORY


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (RoutingEvidence(verified_reusable_result=True), ExecutionLevel.L0_CACHE),
        (RoutingEvidence(deterministic_direct_path=True), ExecutionLevel.L1_DIRECT),
        (RoutingEvidence(verified_reasoning_free_procedure=True), ExecutionLevel.L2_COMPILED),
        (RoutingEvidence(procedure_with_reasoning_gaps=True), ExecutionLevel.L3_GUIDED),
        (RoutingEvidence(known_composition_required=True), ExecutionLevel.L4_PLANNED),
        (RoutingEvidence(), ExecutionLevel.L5_EXPLORATORY),
    ],
)
def test_initial_level_always_equals_the_canonical_router_decision(
    evidence: RoutingEvidence, expected: ExecutionLevel
) -> None:
    """A2.10 never overrides, adjusts, or second-guesses the A2.07 decision."""
    strategy = RecordingStrategy(ok)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    outcome = loop.run(build_request(manager, routing_evidence=evidence)).unwrap()

    assert outcome.initial_level is expected
    assert outcome.initial_level is ExecutionLevelRouter().route(evidence).level


# ---------------------------------------------------------------------------
# 6/7/8. Bounded escalation through A2.08 only.
# ---------------------------------------------------------------------------


def test_one_step_escalation_then_verified_success() -> None:
    """One unverified attempt escalates exactly one level and then succeeds."""
    first = RecordingStrategy(not_verified)
    second = RecordingStrategy(ok)
    loop, manager = build_loop(
        {ExecutionLevel.L1_DIRECT: first, ExecutionLevel.L2_COMPILED: second}
    )
    request = build_request(manager)

    outcome = loop.run(request).unwrap()

    assert outcome.status is OrchestrationStatus.SUCCEEDED
    assert outcome.attempt_count == 2
    assert outcome.attempts[0].level is ExecutionLevel.L1_DIRECT
    assert outcome.attempts[1].level is ExecutionLevel.L2_COMPILED
    assert outcome.attempts[0].escalation is not None
    assert outcome.attempts[0].escalation.action is EscalationAction.ESCALATE
    assert outcome.attempts[0].escalation.next_level is ExecutionLevel.L2_COMPILED
    assert outcome.final_level is ExecutionLevel.L2_COMPILED


def test_multiple_bounded_escalations_walk_the_canonical_hierarchy() -> None:
    """Repeated failures escalate monotonically one level at a time."""
    strategy = RecordingStrategy(not_verified)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(
        manager,
        routing_evidence=RoutingEvidence(verified_reusable_result=True),
        limits=default_limits(max_total_attempts=6),
    )

    outcome = loop.run(request).unwrap()

    observed = [record.level for record in outcome.attempts]
    assert observed == list(CANONICAL_EXECUTION_LEVELS)
    assert outcome.status is not OrchestrationStatus.SUCCEEDED


def test_l5_exhaustion_is_terminal_and_never_wraps() -> None:
    """L5 has no successor: A2.08 returns EXHAUSTED and orchestration stops."""
    strategy = RecordingStrategy(not_verified)
    loop, manager = build_loop({ExecutionLevel.L5_EXPLORATORY: strategy})
    request = build_request(manager, routing_evidence=RoutingEvidence())

    outcome = loop.run(request).unwrap()

    assert outcome.status is OrchestrationStatus.EXHAUSTED
    assert outcome.stop_reason is OrchestrationStopReason.ESCALATION_EXHAUSTED
    assert outcome.final_level is ExecutionLevel.L5_EXPLORATORY
    assert outcome.attempt_count == 1
    last = outcome.attempts[-1]
    assert last.escalation is not None
    assert last.escalation.action is EscalationAction.EXHAUSTED
    assert last.escalation.next_level is None
    assert outcome.task.status is TaskStatus.FAILED


def test_no_de_escalation_ever_occurs() -> None:
    """Levels are non-decreasing across attempts; a failure never gets cheaper."""
    strategy = RecordingStrategy(not_verified)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(
        manager,
        routing_evidence=RoutingEvidence(verified_reasoning_free_procedure=True),
        limits=default_limits(max_total_attempts=6),
    )

    outcome = loop.run(request).unwrap()

    indices = [CANONICAL_EXECUTION_LEVELS.index(r.level) for r in outcome.attempts]
    assert indices == sorted(indices)
    assert all(later >= earlier for earlier, later in pairwise(indices))
    assert min(indices) == CANONICAL_EXECUTION_LEVELS.index(ExecutionLevel.L2_COMPILED)


def test_no_escalation_skipping_ever_occurs() -> None:
    """Every escalation step is exactly +1 in the canonical hierarchy."""
    strategy = RecordingStrategy(not_verified)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(
        manager,
        routing_evidence=RoutingEvidence(verified_reusable_result=True),
        limits=default_limits(max_total_attempts=6),
    )

    outcome = loop.run(request).unwrap()

    indices = [CANONICAL_EXECUTION_LEVELS.index(r.level) for r in outcome.attempts]
    assert all(later - earlier == 1 for earlier, later in pairwise(indices))


def test_escalation_can_be_explicitly_withheld() -> None:
    """When escalation is not explicitly permitted, A2.08 returns EXHAUSTED."""
    strategy = RecordingStrategy(not_verified)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(manager, limits=default_limits(escalation_permitted=False))

    outcome = loop.run(request).unwrap()

    assert outcome.attempt_count == 1
    assert outcome.status is OrchestrationStatus.EXHAUSTED
    assert outcome.stop_reason is OrchestrationStopReason.ESCALATION_EXHAUSTED
    assert outcome.final_level is ExecutionLevel.L1_DIRECT


def test_escalation_decisions_match_the_canonical_escalator() -> None:
    """Recorded escalation decisions are exactly what A2.08 produces."""
    from agentx.cognition.escalation import EscalationEvidence

    strategy = RecordingStrategy(not_verified)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    outcome = loop.run(
        build_request(
            manager,
            routing_evidence=RoutingEvidence(verified_reusable_result=True),
            limits=default_limits(max_total_attempts=6),
        )
    ).unwrap()

    escalator = ExecutionLevelEscalator()
    for record in outcome.attempts:
        assert record.escalation is not None
        expected = escalator.decide(
            EscalationEvidence(
                current_level=record.level,
                current_strategy_can_continue=False,
                attempt_verified_successful=False,
                escalation_explicitly_permitted=True,
            )
        )
        assert record.escalation.action is expected.action
        assert record.escalation.next_level is expected.next_level


# ---------------------------------------------------------------------------
# 9/10. Anti-loop and the total-attempt ceiling.
# ---------------------------------------------------------------------------


def test_anti_loop_stop_loop_terminates_orchestration() -> None:
    """A2.09 STOP_LOOP stops the loop, implies no success, and grants nothing."""
    strategy = RecordingStrategy(not_verified)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(
        manager,
        limits=OrchestrationLimits(
            max_total_attempts=8,
            loop_guard_limits=LoopGuardLimits(
                max_total_attempts=2,
                max_same_attempts=8,
                max_same_outcomes_without_progress=8,
            ),
        ),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.status is OrchestrationStatus.EXHAUSTED
    assert outcome.stop_reason is OrchestrationStopReason.ANTI_LOOP
    assert outcome.attempt_count == 2
    last = outcome.attempts[-1]
    assert last.loop_guard.decision is LoopGuardDecision.STOP_LOOP
    assert last.loop_guard.trigger is LoopGuardTrigger.TOTAL_ATTEMPTS
    # STOP_LOOP is not escalation authority: no escalation decision was taken.
    assert last.escalation is None
    assert outcome.task.status is TaskStatus.FAILED


def test_anti_loop_stalled_outcome_trigger_stops_the_loop() -> None:
    """Repeating the same outcome class without progress trips A2.09."""
    strategy = RecordingStrategy(not_verified)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(
        manager,
        routing_evidence=RoutingEvidence(verified_reusable_result=True),
        limits=OrchestrationLimits(
            max_total_attempts=6,
            loop_guard_limits=LoopGuardLimits(
                max_total_attempts=32,
                max_same_attempts=8,
                max_same_outcomes_without_progress=3,
            ),
        ),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.stop_reason is OrchestrationStopReason.ANTI_LOOP
    assert outcome.attempts[-1].loop_guard.trigger is LoopGuardTrigger.STALLED_OUTCOME
    assert outcome.attempt_count == 3


def test_anti_loop_decision_matches_the_canonical_guard() -> None:
    """A2.10 never reinterprets A2.09: recorded results equal a direct evaluation."""
    strategy = RecordingStrategy(not_verified)
    limits = LoopGuardLimits(
        max_total_attempts=32, max_same_attempts=8, max_same_outcomes_without_progress=8
    )
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    outcome = loop.run(
        build_request(
            manager,
            routing_evidence=RoutingEvidence(verified_reusable_result=True),
            limits=OrchestrationLimits(max_total_attempts=4, loop_guard_limits=limits),
        )
    ).unwrap()

    for index, record in enumerate(outcome.attempts, start=1):
        assert record.loop_guard.total_attempts == index
        assert record.loop_guard.decision is LoopGuardDecision.CONTINUE


def test_total_attempt_ceiling_bounds_the_run() -> None:
    """The explicit A2.10 ceiling terminates the loop independently of A2.09."""
    strategy = RecordingStrategy(not_verified)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(
        manager,
        routing_evidence=RoutingEvidence(verified_reusable_result=True),
        limits=OrchestrationLimits(
            max_total_attempts=3,
            loop_guard_limits=LoopGuardLimits(
                max_total_attempts=64,
                max_same_attempts=64,
                max_same_outcomes_without_progress=64,
            ),
        ),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.attempt_count == 3
    assert strategy.attempts == 3
    assert outcome.status is OrchestrationStatus.EXHAUSTED
    assert outcome.stop_reason is OrchestrationStopReason.ATTEMPT_CEILING


@pytest.mark.parametrize("ceiling", [1, 2, 3, 5])
def test_no_infinite_loop_for_any_ceiling(ceiling: int) -> None:
    """Attempts never exceed the explicit ceiling, for any configured bound."""
    strategy = RecordingStrategy(not_verified)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(
        manager,
        routing_evidence=RoutingEvidence(verified_reusable_result=True),
        limits=OrchestrationLimits(
            max_total_attempts=ceiling,
            loop_guard_limits=LoopGuardLimits(
                max_total_attempts=64,
                max_same_attempts=64,
                max_same_outcomes_without_progress=64,
            ),
        ),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.attempt_count <= ceiling
    assert strategy.attempts <= ceiling
    assert is_terminal(outcome.task.status)


def test_attempt_ceiling_is_itself_bounded() -> None:
    """There is no unlimited mode: the configurable ceiling has a hard maximum."""
    guard = LoopGuardLimits(
        max_total_attempts=4, max_same_attempts=4, max_same_outcomes_without_progress=4
    )
    with pytest.raises(OrchestrationRequestError):
        OrchestrationLimits(
            max_total_attempts=MAX_CONFIGURABLE_TOTAL_ATTEMPTS + 1, loop_guard_limits=guard
        )
    with pytest.raises(OrchestrationRequestError):
        OrchestrationLimits(max_total_attempts=0, loop_guard_limits=guard)
    with pytest.raises(TypeError):
        OrchestrationLimits(max_total_attempts=True, loop_guard_limits=guard)


# ---------------------------------------------------------------------------
# 11/12/13. Cancellation, deadline, and stop determinism.
# ---------------------------------------------------------------------------


def test_cancellation_before_first_attempt_stops_without_attempting() -> None:
    """Cancellation observed before starting: no attempt, no RUNNING, no success."""
    strategy = RecordingStrategy(ok)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    source = CancellationSource()
    source.request_cancellation("operator stop")
    request = build_request(manager, cancellation=source)

    outcome = loop.run(request).unwrap()

    assert strategy.attempts == 0
    assert outcome.status is OrchestrationStatus.CANCELLED
    assert outcome.stop_reason is OrchestrationStopReason.CANCELLED
    assert outcome.attempt_count == 0
    assert outcome.task.status is TaskStatus.CANCELLED
    assert manager.require(request.task.task_id).status is TaskStatus.CANCELLED


def test_cancellation_between_attempts_stops_further_attempts() -> None:
    """Cancellation raised during attempt 1 prevents attempt 2."""
    source = CancellationSource()

    def scripted(attempt: int, _level: ExecutionLevel) -> StrategyResult:
        if attempt == 1:
            source.request_cancellation("cancelled mid-run")
        return not_verified(attempt, _level)

    strategy = RecordingStrategy(scripted)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(manager, cancellation=source)

    outcome = loop.run(request).unwrap()

    assert strategy.attempts == 1
    assert outcome.status is OrchestrationStatus.CANCELLED
    assert outcome.stop_reason is OrchestrationStopReason.CANCELLED
    assert outcome.attempt_count == 1
    assert outcome.task.status is TaskStatus.CANCELLED


def test_deadline_timeout_before_first_attempt() -> None:
    """An already-expired deadline stops the run before any attempt."""
    clock = FixedClock(now=500.0)
    strategy = RecordingStrategy(ok)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(manager, deadline=Deadline(clock.monotonic() - 1.0))

    outcome = loop.run(request, clock=clock).unwrap()

    assert strategy.attempts == 0
    assert outcome.status is OrchestrationStatus.TIMED_OUT
    assert outcome.stop_reason is OrchestrationStopReason.DEADLINE_EXPIRED
    assert outcome.task.status is TaskStatus.CANCELLED


def test_deadline_timeout_between_attempts() -> None:
    """A deadline crossed during attempt 1 prevents attempt 2."""
    clock = FixedClock(now=500.0)

    def scripted(attempt: int, _level: ExecutionLevel) -> StrategyResult:
        if attempt == 1:
            clock.advance(10.0)
        return not_verified(attempt, _level)

    strategy = RecordingStrategy(scripted)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(manager, deadline=Deadline(clock.monotonic() + 5.0))

    outcome = loop.run(request, clock=clock).unwrap()

    assert strategy.attempts == 1
    assert outcome.status is OrchestrationStatus.TIMED_OUT
    assert outcome.stop_reason is OrchestrationStopReason.DEADLINE_EXPIRED


def test_deadline_exactly_reached_counts_as_expired() -> None:
    """A1.07 semantics are ``now >= deadline``; A2.10 does not soften them."""
    clock = FixedClock(now=500.0)
    strategy = RecordingStrategy(ok)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(manager, deadline=Deadline(clock.monotonic()))

    outcome = loop.run(request, clock=clock).unwrap()

    assert strategy.attempts == 0
    assert outcome.status is OrchestrationStatus.TIMED_OUT


def test_cancellation_takes_priority_over_timeout_in_reporting() -> None:
    """When both hold, A1.07 orders cancellation first and A2.10 follows it."""
    clock = FixedClock(now=500.0)
    source = CancellationSource()
    source.request_cancellation("both conditions")
    strategy = RecordingStrategy(ok)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(
        manager, cancellation=source, deadline=Deadline(clock.monotonic() - 1.0)
    )

    outcome = loop.run(request, clock=clock).unwrap()

    assert outcome.status is OrchestrationStatus.CANCELLED
    assert outcome.stop_reason is OrchestrationStopReason.CANCELLED
    assert strategy.attempts == 0


def test_cancellation_and_timeout_are_never_success() -> None:
    """No stop condition is ever converted into success, at any level."""
    builders: tuple[Callable[[TaskManager], OrchestrationRequest], ...] = (
        lambda manager: build_request(manager, cancellation=_cancelled_source()),
        lambda manager: build_request(manager, deadline=Deadline(0.0)),
    )
    for build in builders:
        strategy = RecordingStrategy(ok)
        loop, manager = build_loop({level: strategy for level in ExecutionLevel})
        outcome = loop.run(build(manager), clock=FixedClock(now=10.0)).unwrap()
        assert outcome.status is not OrchestrationStatus.SUCCEEDED
        assert outcome.verified is False
        assert outcome.task.status is TaskStatus.CANCELLED


def _cancelled_source() -> CancellationSource:
    source = CancellationSource()
    source.request_cancellation("stop")
    return source


# ---------------------------------------------------------------------------
# 14/15/16. Resource exhaustion, denial, emergency stop (as canonical errors).
# ---------------------------------------------------------------------------


def test_resource_exhaustion_fails_closed_without_further_attempts() -> None:
    """Canonical budget denial is terminal: no retry, no escalation, no widening."""
    strategy = RecordingStrategy(failure("runtime.budget_denied", ErrorCategory.RESOURCE))
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(manager, limits=default_limits(max_total_attempts=6))

    outcome = loop.run(request).unwrap()

    assert strategy.attempts == 1
    assert outcome.status is OrchestrationStatus.FAILED
    assert outcome.stop_reason is OrchestrationStopReason.RESOURCE_EXHAUSTED
    assert outcome.attempts[0].disposition is AttemptDisposition.RESOURCE_EXHAUSTED
    assert outcome.attempts[0].escalation is None
    assert outcome.final_level is ExecutionLevel.L1_DIRECT
    assert outcome.task.status is TaskStatus.FAILED


def test_denied_action_gate_path_is_not_success_and_can_escalate() -> None:
    """A canonical permission/gate denial is an ordinary unsuccessful attempt."""
    strategy = RecordingStrategy(failure("runtime.gate_denied", ErrorCategory.PERMISSION))
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(manager, limits=default_limits(max_total_attempts=2))

    outcome = loop.run(request).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert all(
        record.disposition is AttemptDisposition.STRATEGY_ERROR for record in outcome.attempts
    )
    assert outcome.task.status is TaskStatus.FAILED


def test_emergency_stop_error_fails_closed_as_a_safety_stop() -> None:
    """A canonical emergency-stop refusal stops orchestration immediately."""
    strategy = RecordingStrategy(
        failure("runtime.emergency_stop_active", ErrorCategory.PRECONDITION)
    )
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})
    request = build_request(manager, limits=default_limits(max_total_attempts=6))

    outcome = loop.run(request).unwrap()

    assert strategy.attempts == 1
    assert outcome.status is OrchestrationStatus.CANCELLED
    assert outcome.stop_reason is OrchestrationStopReason.SAFETY_STOP
    assert outcome.attempts[0].disposition is AttemptDisposition.SAFETY_STOPPED
    assert outcome.task.status is TaskStatus.CANCELLED


def test_context_stopped_error_fails_closed_as_a_safety_stop() -> None:
    """A canonical context-stop refusal is a safety stop, never a retryable error."""
    strategy = RecordingStrategy(failure("runtime.context_stopped", ErrorCategory.CANCELLED))
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})

    outcome = loop.run(build_request(manager)).unwrap()

    assert strategy.attempts == 1
    assert outcome.stop_reason is OrchestrationStopReason.SAFETY_STOP
    assert outcome.task.status is TaskStatus.CANCELLED


# ---------------------------------------------------------------------------
# 17/18. Strategy unavailability and L5 semantics.
# ---------------------------------------------------------------------------


def test_strategy_unavailable_fails_closed() -> None:
    """No adapter bound for the routed level => fail closed, do not attempt."""
    loop, manager = build_loop({})
    request = build_request(manager)

    outcome = loop.run(request).unwrap()

    assert outcome.status is OrchestrationStatus.FAILED
    assert outcome.stop_reason is OrchestrationStopReason.STRATEGY_UNAVAILABLE
    assert outcome.attempt_count == 1
    assert outcome.attempts[0].disposition is AttemptDisposition.STRATEGY_UNAVAILABLE
    assert outcome.attempts[0].outcome is None
    assert outcome.task.status is TaskStatus.FAILED


def test_explicitly_unavailable_strategy_is_not_success() -> None:
    """An adapter reporting unavailability yields a non-success disposition."""
    strategy = UnavailableStrategy()
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})

    outcome = loop.run(build_request(manager, limits=default_limits(max_total_attempts=2))).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.attempts[0].disposition is AttemptDisposition.STRATEGY_UNAVAILABLE


def test_reaching_l5_without_an_authorized_strategy_fails_closed() -> None:
    """Escalating to L5 authorizes nothing: with no L5 adapter the run fails closed."""
    strategy = RecordingStrategy(not_verified)
    loop, manager = build_loop(
        {level: strategy for level in ExecutionLevel if level is not ExecutionLevel.L5_EXPLORATORY}
    )
    request = build_request(
        manager,
        routing_evidence=RoutingEvidence(known_composition_required=True),
        limits=default_limits(max_total_attempts=4),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.final_level is ExecutionLevel.L5_EXPLORATORY
    assert outcome.stop_reason is OrchestrationStopReason.STRATEGY_UNAVAILABLE
    assert outcome.status is OrchestrationStatus.FAILED
    assert outcome.attempts[-1].disposition is AttemptDisposition.STRATEGY_UNAVAILABLE
    # The L4 adapter ran; nothing ran at L5.
    assert [record.level for record in outcome.attempts] == [
        ExecutionLevel.L4_PLANNED,
        ExecutionLevel.L5_EXPLORATORY,
    ]
    assert strategy.attempts == 1


# ---------------------------------------------------------------------------
# 19. Hostile strategy/model text cannot fabricate success.
# ---------------------------------------------------------------------------


def test_hostile_text_cannot_fabricate_success() -> None:
    """Text claiming success everywhere changes nothing: evidence is typed."""
    hostile = HostileStrategy()
    loop, manager = build_loop({level: hostile for level in ExecutionLevel})

    outcome = loop.run(build_request(manager, limits=default_limits(max_total_attempts=2))).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.verified is False
    assert outcome.task.status is not TaskStatus.SUCCEEDED
    for record in outcome.attempts:
        assert record.disposition is not AttemptDisposition.VERIFIED_SUCCESS
        assert record.outcome is not None
        assert hostile.hostile_text in record.outcome.observation.summary  # type: ignore[union-attr]


def test_hostile_self_certified_verdict_without_verified_kind_is_rejected() -> None:
    """A passed=True verdict on a non-VERIFIED outcome is still not success."""
    hostile = HostileStrategy(
        kind=LoopOutcome.EXECUTION_FAILED,
        verification=VerificationResult(passed=True, detail="self-certified success"),
    )
    loop, manager = build_loop({level: hostile for level in ExecutionLevel})

    outcome = loop.run(build_request(manager, limits=default_limits(max_total_attempts=1))).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.attempts[0].disposition is AttemptDisposition.UNVERIFIED


def test_hostile_verified_kind_with_non_succeeded_task_is_rejected() -> None:
    """Task status is canonical evidence too: a FAILED task can never be success."""
    hostile = HostileStrategy(
        kind=LoopOutcome.VERIFIED,
        verification=VerificationResult(passed=True, detail="claims verified"),
    )
    loop, manager = build_loop({level: hostile for level in ExecutionLevel})

    outcome = loop.run(build_request(manager, limits=default_limits(max_total_attempts=1))).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.attempts[0].disposition is AttemptDisposition.UNVERIFIED


def test_hostile_observation_values_cannot_satisfy_a_typed_requirement() -> None:
    """String "true" never equals boolean True: A2.05 equality is type-strict."""
    hostile = HostileStrategy()
    loop, manager = build_loop({level: hostile for level in ExecutionLevel})
    request = build_request(
        manager,
        requirement=VerificationRequirement({"passed": True}),
        limits=default_limits(max_total_attempts=1),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# 20. Task lifecycle correctness.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [
        ("verified", TaskStatus.SUCCEEDED),
        ("unverified", TaskStatus.FAILED),
        ("cancelled", TaskStatus.CANCELLED),
        ("resource", TaskStatus.FAILED),
        ("unavailable", TaskStatus.FAILED),
    ],
)
def test_task_terminal_transition_correctness(scenario: str, expected_status: TaskStatus) -> None:
    """Every terminal path lands on a canonical terminal status, and only those."""
    if scenario == "verified":
        strategies: dict[ExecutionLevel, object] = {
            level: RecordingStrategy(ok) for level in ExecutionLevel
        }
        cancellation = None
    elif scenario == "unverified":
        strategies = {level: RecordingStrategy(not_verified) for level in ExecutionLevel}
        cancellation = None
    elif scenario == "cancelled":
        strategies = {level: RecordingStrategy(ok) for level in ExecutionLevel}
        cancellation = _cancelled_source()
    elif scenario == "resource":
        strategies = {
            level: RecordingStrategy(failure("runtime.budget_denied", ErrorCategory.RESOURCE))
            for level in ExecutionLevel
        }
        cancellation = None
    else:
        strategies = {}
        cancellation = None

    loop, manager = build_loop(strategies)
    request = build_request(
        manager, cancellation=cancellation, limits=default_limits(max_total_attempts=2)
    )

    outcome = loop.run(request).unwrap()

    assert outcome.task.status is expected_status
    assert is_terminal(outcome.task.status)
    assert manager.require(request.task.task_id).status is expected_status


def test_every_terminal_task_status_is_reachable_only_via_legal_transitions() -> None:
    """Terminal statuses used by A2.10 are legal successors of RUNNING/PENDING."""
    used = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    assert used <= LEGAL_TASK_TRANSITIONS[TaskStatus.RUNNING] | {TaskStatus.CANCELLED}
    assert TaskStatus.CANCELLED in LEGAL_TASK_TRANSITIONS[TaskStatus.PENDING]
    for status in used:
        assert is_terminal(status)


def test_run_rejects_an_unregistered_task() -> None:
    """A2.10 never invents Task identity: the Task must be manager-registered."""
    loop, _manager = build_loop({level: RecordingStrategy(ok) for level in ExecutionLevel})
    other_manager = TaskManager()
    request = build_request(other_manager)

    result = loop.run(request)

    assert result.is_failure
    assert result.unwrap_error().code == "agent_loop.task_not_registered"


def test_run_rejects_a_non_pending_registered_task() -> None:
    """A run cannot start from a Task that already left PENDING."""
    loop, manager = build_loop({level: RecordingStrategy(ok) for level in ExecutionLevel})
    request = build_request(manager)
    manager.transition(request.task.task_id, TaskStatus.RUNNING)

    result = loop.run(request)

    assert result.is_failure
    assert result.unwrap_error().code == "agent_loop.task_not_pending"


def test_request_rejects_a_non_pending_task() -> None:
    """The request contract itself rejects a non-PENDING Task."""
    manager = TaskManager()
    task = manager.create("objective")
    running = manager.transition(task.task_id, TaskStatus.RUNNING).unwrap()
    from uuid import uuid4

    with pytest.raises(OrchestrationRequestError):
        OrchestrationRequest(
            task=running,
            context=ExecutionContext(
                correlation_id=uuid4(),
                cancellation_token=CancellationSource().token,
                task_id=running.task_id,
            ),
            routing_evidence=RoutingEvidence(deterministic_direct_path=True),
            requirement=VerificationRequirement({}),
            limits=default_limits(),
        )


def test_request_rejects_a_mismatched_or_missing_context_identity() -> None:
    """The execution context must name exactly the orchestrated Task."""
    from uuid import uuid4

    manager = TaskManager()
    task = manager.create("objective")
    other = Task.create(objective="other objective")

    with pytest.raises(OrchestrationRequestError):
        OrchestrationRequest(
            task=task,
            context=ExecutionContext(
                correlation_id=uuid4(),
                cancellation_token=CancellationSource().token,
                task_id=other.task_id,
            ),
            routing_evidence=RoutingEvidence(),
            requirement=VerificationRequirement({}),
            limits=default_limits(),
        )
    with pytest.raises(OrchestrationRequestError):
        OrchestrationRequest(
            task=task,
            context=ExecutionContext(
                correlation_id=uuid4(),
                cancellation_token=CancellationSource().token,
            ),
            routing_evidence=RoutingEvidence(),
            requirement=VerificationRequirement({}),
            limits=default_limits(),
        )


# ---------------------------------------------------------------------------
# 21. Determinism.
# ---------------------------------------------------------------------------


def test_deterministic_repeated_execution_for_identical_inputs() -> None:
    """Identical explicit inputs and strategy outcomes produce identical decisions."""

    def snapshot(outcome: OrchestrationOutcome) -> tuple[object, ...]:
        return (
            outcome.status,
            outcome.stop_reason,
            outcome.initial_level,
            outcome.final_level,
            tuple(
                (
                    record.attempt_index,
                    record.level,
                    record.disposition,
                    record.loop_guard.decision,
                    record.loop_guard.trigger,
                    None if record.escalation is None else record.escalation.action,
                    None if record.escalation is None else record.escalation.next_level,
                )
                for record in outcome.attempts
            ),
            None if outcome.error is None else outcome.error.code,
        )

    snapshots = []
    for _ in range(5):
        strategy = RecordingStrategy(not_verified)
        loop, manager = build_loop({level: strategy for level in ExecutionLevel})
        request = build_request(
            manager,
            routing_evidence=RoutingEvidence(verified_reasoning_free_procedure=True),
            limits=default_limits(max_total_attempts=4),
        )
        snapshots.append(snapshot(loop.run(request).unwrap()))

    assert len(set(snapshots)) == 1


def test_two_loop_instances_agree_on_identical_inputs() -> None:
    """Determinism is a property of the contract, not of one instance."""
    results = []
    for _ in range(2):
        strategy = RecordingStrategy(not_verified)
        manager = TaskManager()
        loop = AgentLoop(
            task_manager=manager,
            strategies=StrategyRegistry({level: strategy for level in ExecutionLevel}),
            router=ExecutionLevelRouter(),
            escalator=ExecutionLevelEscalator(),
            loop_guard=LoopGuard(),
            verifier=Verifier(),
        )
        outcome = loop.run(
            build_request(manager, limits=default_limits(max_total_attempts=3))
        ).unwrap()
        results.append((outcome.status, outcome.stop_reason, outcome.attempt_count))

    assert results[0] == results[1]


def test_agent_loop_holds_no_cross_run_state() -> None:
    """A second run on the same loop object is unaffected by the first."""
    strategy = RecordingStrategy(ok)
    loop, manager = build_loop({level: strategy for level in ExecutionLevel})

    first = loop.run(build_request(manager)).unwrap()
    second = loop.run(build_request(manager)).unwrap()

    assert first.status is second.status is OrchestrationStatus.SUCCEEDED
    assert first.attempt_count == second.attempt_count == 1
    assert first.task.task_id != second.task.task_id


# ---------------------------------------------------------------------------
# 22. Contract shape / authority guarantees at the value level.
# ---------------------------------------------------------------------------


def test_strategy_result_requires_exactly_one_channel() -> None:
    """A strategy reports an outcome or unavailability — never both, never neither."""
    with pytest.raises(OrchestrationRequestError):
        StrategyResult()
    with pytest.raises(OrchestrationRequestError):
        StrategyResult(
            outcome=Result[ClosedLoopOutcome, AgentXError].success(verified_outcome()),
            unavailable_reason="both",
        )
    with pytest.raises(OrchestrationRequestError):
        StrategyResult.unavailable("  ")
    with pytest.raises(TypeError):
        StrategyResult(outcome="not a result")  # type: ignore[arg-type]


def test_strategy_result_carries_no_success_or_authority_field() -> None:
    """There is no channel through which an adapter could assert success."""
    fields = set(StrategyResult.__dataclass_fields__)
    assert fields == {"outcome", "unavailable_reason"}
    for forbidden in (
        "succeeded",
        "verified",
        "passed",
        "permission",
        "authority",
        "risk",
        "budget",
        "envelope",
        "next_level",
        "escalate",
    ):
        assert forbidden not in fields


def test_orchestration_request_carries_no_authority_field() -> None:
    """The request cannot smuggle authority, budget, model, or level overrides."""
    fields = set(OrchestrationRequest.__dataclass_fields__)
    assert fields == {"task", "context", "routing_evidence", "requirement", "limits"}
    for forbidden in (
        "permission",
        "authority",
        "risk",
        "budget",
        "envelope",
        "emergency_stop",
        "model",
        "provider",
        "prompt",
        "level",
    ):
        assert forbidden not in fields


def test_orchestration_outcome_rejects_success_without_a_verified_attempt() -> None:
    """The outcome value itself enforces I1 structurally."""
    record = AttemptRecord(
        attempt_index=1,
        level=ExecutionLevel.L1_DIRECT,
        disposition=AttemptDisposition.UNVERIFIED,
        outcome=None,
        evaluation=None,
        error=None,
        loop_guard=LoopGuard().evaluate(
            history=(),
            limits=LoopGuardLimits(
                max_total_attempts=4,
                max_same_attempts=4,
                max_same_outcomes_without_progress=4,
            ),
        ),
        escalation=None,
    )
    succeeded_task = _to_succeeded(Task.create(objective="objective"))

    with pytest.raises(OrchestrationRequestError):
        OrchestrationOutcome(
            task=succeeded_task,
            status=OrchestrationStatus.SUCCEEDED,
            stop_reason=OrchestrationStopReason.VERIFIED,
            initial_level=ExecutionLevel.L1_DIRECT,
            final_level=ExecutionLevel.L1_DIRECT,
            attempts=(record,),
            error=None,
        )
    with pytest.raises(OrchestrationRequestError):
        OrchestrationOutcome(
            task=succeeded_task,
            status=OrchestrationStatus.SUCCEEDED,
            stop_reason=OrchestrationStopReason.VERIFIED,
            initial_level=ExecutionLevel.L1_DIRECT,
            final_level=ExecutionLevel.L1_DIRECT,
            attempts=(),
            error=None,
        )


def test_orchestration_outcome_rejects_success_with_a_non_succeeded_task() -> None:
    """A SUCCEEDED orchestration cannot exist over a non-SUCCEEDED Task."""
    verified_record = AttemptRecord(
        attempt_index=1,
        level=ExecutionLevel.L1_DIRECT,
        disposition=AttemptDisposition.VERIFIED_SUCCESS,
        outcome=verified_outcome(),
        evaluation=RequirementEvaluation(satisfied=True, unmet_conditions=()),
        error=None,
        loop_guard=LoopGuard().evaluate(
            history=(),
            limits=LoopGuardLimits(
                max_total_attempts=4,
                max_same_attempts=4,
                max_same_outcomes_without_progress=4,
            ),
        ),
        escalation=None,
    )
    with pytest.raises(OrchestrationRequestError):
        OrchestrationOutcome(
            task=_to_failed(Task.create(objective="objective")),
            status=OrchestrationStatus.SUCCEEDED,
            stop_reason=OrchestrationStopReason.VERIFIED,
            initial_level=ExecutionLevel.L1_DIRECT,
            final_level=ExecutionLevel.L1_DIRECT,
            attempts=(verified_record,),
            error=None,
        )


def test_orchestration_outcome_rejects_non_success_holding_a_verified_attempt() -> None:
    """A verified attempt cannot be reported under a non-success status."""
    verified_record = AttemptRecord(
        attempt_index=1,
        level=ExecutionLevel.L1_DIRECT,
        disposition=AttemptDisposition.VERIFIED_SUCCESS,
        outcome=verified_outcome(),
        evaluation=RequirementEvaluation(satisfied=True, unmet_conditions=()),
        error=None,
        loop_guard=LoopGuard().evaluate(
            history=(),
            limits=LoopGuardLimits(
                max_total_attempts=4,
                max_same_attempts=4,
                max_same_outcomes_without_progress=4,
            ),
        ),
        escalation=None,
    )
    with pytest.raises(OrchestrationRequestError):
        OrchestrationOutcome(
            task=_to_failed(Task.create(objective="objective")),
            status=OrchestrationStatus.FAILED,
            stop_reason=OrchestrationStopReason.ANTI_LOOP,
            initial_level=ExecutionLevel.L1_DIRECT,
            final_level=ExecutionLevel.L1_DIRECT,
            attempts=(verified_record,),
            error=None,
        )


def test_orchestration_outcome_requires_a_terminal_task_status() -> None:
    """An orchestration outcome can never leave a Task mid-flight."""
    manager = TaskManager()
    task = manager.create("objective")
    running = manager.transition(task.task_id, TaskStatus.RUNNING).unwrap()

    with pytest.raises(OrchestrationRequestError):
        OrchestrationOutcome(
            task=running,
            status=OrchestrationStatus.FAILED,
            stop_reason=OrchestrationStopReason.ATTEMPT_CEILING,
            initial_level=ExecutionLevel.L1_DIRECT,
            final_level=ExecutionLevel.L1_DIRECT,
            attempts=(),
            error=None,
        )


def test_strategy_registry_rejects_non_canonical_bindings() -> None:
    """The registry accepts only canonical levels and callable adapters."""
    with pytest.raises(TypeError):
        StrategyRegistry({"L1_DIRECT": RecordingStrategy(ok)})  # type: ignore[dict-item]
    with pytest.raises(TypeError):
        StrategyRegistry({ExecutionLevel.L1_DIRECT: object()})  # type: ignore[dict-item]
    with pytest.raises(TypeError):
        StrategyRegistry(["not a mapping"])  # type: ignore[arg-type]


def test_strategy_registry_is_immutable_and_ordered() -> None:
    """Bindings are fixed at construction and reported in hierarchy order."""
    registry = StrategyRegistry(
        {
            ExecutionLevel.L4_PLANNED: RecordingStrategy(ok),
            ExecutionLevel.L0_CACHE: RecordingStrategy(ok),
        }
    )
    assert registry.levels() == (ExecutionLevel.L0_CACHE, ExecutionLevel.L4_PLANNED)
    assert ExecutionLevel.L0_CACHE in registry
    assert ExecutionLevel.L1_DIRECT not in registry
    assert "L0_CACHE" not in registry
    assert len(registry) == 2
    assert registry.get(ExecutionLevel.L1_DIRECT) is None
    with pytest.raises(TypeError):
        registry.get("L0_CACHE")  # type: ignore[arg-type]


def test_agent_loop_rejects_non_canonical_collaborators() -> None:
    """The loop cannot be pointed at a substitute decision engine."""
    manager = TaskManager()
    registry = StrategyRegistry({})
    with pytest.raises(TypeError):
        AgentLoop(task_manager=object(), strategies=registry)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AgentLoop(task_manager=manager, strategies={})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AgentLoop(task_manager=manager, strategies=registry, router=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AgentLoop(task_manager=manager, strategies=registry, escalator=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AgentLoop(task_manager=manager, strategies=registry, loop_guard=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        AgentLoop(task_manager=manager, strategies=registry, verifier=object())  # type: ignore[arg-type]


def test_run_rejects_a_non_request_argument() -> None:
    """A wrong argument type is a programming error, not an outcome."""
    loop, _manager = build_loop({})
    with pytest.raises(TypeError):
        loop.run("not a request")  # type: ignore[arg-type]


def test_module_reports_a_stable_source_identifier() -> None:
    """The boundary names itself for reporting; it publishes no events."""
    assert AGENT_LOOP_SOURCE == "agentx.agent_loop"


def test_repr_is_inert_and_deterministic() -> None:
    """Reprs report bound levels only; they leak no evidence and grant nothing."""
    registry = StrategyRegistry({ExecutionLevel.L1_DIRECT: RecordingStrategy(ok)})
    loop = AgentLoop(task_manager=TaskManager(), strategies=registry)
    assert repr(loop) == "AgentLoop(levels=['L1_DIRECT'])"
    assert repr(registry) == "StrategyRegistry(levels=['L1_DIRECT'])"


def test_attempt_record_validates_its_evidence_types() -> None:
    """Attempt records reject malformed evidence rather than storing it."""
    guard = LoopGuard().evaluate(
        history=(),
        limits=LoopGuardLimits(
            max_total_attempts=4, max_same_attempts=4, max_same_outcomes_without_progress=4
        ),
    )
    with pytest.raises(OrchestrationRequestError):
        AttemptRecord(
            attempt_index=0,
            level=ExecutionLevel.L1_DIRECT,
            disposition=AttemptDisposition.UNVERIFIED,
            outcome=None,
            evaluation=None,
            error=None,
            loop_guard=guard,
            escalation=None,
        )
    with pytest.raises(TypeError):
        AttemptRecord(
            attempt_index=1,
            level="L1_DIRECT",  # type: ignore[arg-type]
            disposition=AttemptDisposition.UNVERIFIED,
            outcome=None,
            evaluation=None,
            error=None,
            loop_guard=guard,
            escalation=None,
        )


def _zero_action_envelope() -> ResourceEnvelope:
    """An envelope that can never fund a machine action."""
    return make_envelope(
        max_machine_actions=0,
        max_wall_clock=timedelta(0),
        max_external_cost=Decimal("0"),
    )


def test_resource_envelope_is_never_constructed_or_widened_by_the_loop() -> None:
    """A2.10 owns no budget: an exhausted envelope stays exhausted."""
    harness = OrchestrationHarness(envelope=_zero_action_envelope())
    before = harness.budget.snapshot()
    strategy = harness.governed_strategy()
    loop = harness.agent_loop({level: strategy for level in ExecutionLevel})
    request = harness.make_request(limits=default_limits(max_total_attempts=3))

    outcome = loop.run(request).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.stop_reason is OrchestrationStopReason.RESOURCE_EXHAUSTED
    assert harness.budget.envelope is harness.envelope
    assert harness.budget.envelope.max_machine_actions == 0
    assert harness.budget.snapshot() == before
