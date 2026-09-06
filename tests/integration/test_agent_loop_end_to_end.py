"""A2.10 integration tests: the bounded loop over the real canonical path.

Every test here wires the *real* canonical collaborators — the A1.09 registry,
C1.07 PermissionEngine/ActionGate, C1.09 EmergencyStop, C1.08 ResourceBudget,
the C1.03 EventBus, canonical C1.09 audit records — behind the canonical A1.10
:class:`~agentx.capabilities.runtime.CapabilityExecutionLoop`, reaches it only
through the A2.04 Executor, and drives the whole thing with the A2.10
:class:`~agentx.cognition.agent_loop.AgentLoop`.

The purpose is to prove composition: canonical semantics still hold, and A2.10
changed none of them. There is no model, no network, and no sleeping anywhere
in this module.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from agentx.capabilities.abi import CapabilityObservation, VerificationResult
from agentx.capabilities.runtime import LoopOutcome
from agentx.capabilities.verifier import VerificationRequirement
from agentx.cognition.agent_loop import (
    AttemptDisposition,
    OrchestrationStatus,
    OrchestrationStopReason,
)
from agentx.cognition.router import CANONICAL_EXECUTION_LEVELS, ExecutionLevel, RoutingEvidence
from agentx.core.events import EventType
from agentx.core.execution import CancellationSource, Deadline
from agentx.core.task_state import is_terminal
from agentx.core.tasks import TaskStatus
from agentx.kernel.audit import AuditOutcome
from agentx.kernel.permissions import Permission
from tests.support.demo_capability import DemoNoteCapability
from tests.support.orchestration_harness import (
    FixedClock,
    OrchestrationHarness,
    UnavailableStrategy,
    default_limits,
    make_envelope,
)


def _all_levels(strategy: object) -> dict[ExecutionLevel, object]:
    return {level: strategy for level in ExecutionLevel}


# ---------------------------------------------------------------------------
# Verified success through the real governed path.
# ---------------------------------------------------------------------------


def test_verified_first_attempt_success_through_the_canonical_path() -> None:
    """One governed, gated, budgeted, verified capability run => SUCCEEDED."""
    harness = OrchestrationHarness()
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))
    request = harness.make_request()

    outcome = loop.run(request).unwrap()

    assert outcome.status is OrchestrationStatus.SUCCEEDED
    assert outcome.stop_reason is OrchestrationStopReason.VERIFIED
    assert outcome.attempt_count == 1
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert harness.task_status(request.task) is TaskStatus.SUCCEEDED

    # Canonical A1.10 evidence really was produced by the governed loop.
    record = outcome.attempts[0]
    assert record.outcome is not None
    assert record.outcome.kind is LoopOutcome.VERIFIED
    assert record.outcome.verification is not None
    assert record.outcome.verification.passed is True
    assert record.evaluation is not None
    assert record.evaluation.satisfied is True

    # The capability really executed and really verified, exactly once.
    assert harness.capability.execute_calls == 1
    assert harness.capability.verify_calls == 1
    assert harness.capability.state == {"alpha": "v1"}

    # Canonical events and audit records came from the A1.10 path, not A2.10.
    published = [event.event_type for event in harness.events]
    assert EventType.TASK_STARTED in published
    assert EventType.VERIFICATION_COMPLETED in published
    assert EventType.TASK_COMPLETED in published
    assert all(event.source == "agentx.capabilities.runtime" for event in harness.events)
    assert any(record.outcome is AuditOutcome.SUCCEEDED for record in harness.audit_records)


def test_canonical_budget_is_consumed_exactly_once_per_governed_attempt() -> None:
    """C1.08 accounting stays the sole authority and is not double counted."""
    harness = OrchestrationHarness()
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))

    before = harness.budget.snapshot()
    outcome = loop.run(harness.make_request()).unwrap()
    after = harness.budget.snapshot()

    assert outcome.status is OrchestrationStatus.SUCCEEDED
    assert after.machine_actions == before.machine_actions + 1
    assert after.model_calls == 0
    assert after.model_tokens == 0
    assert after.research_queries == 0
    assert after.repair_attempts == 0


# ---------------------------------------------------------------------------
# I1 over the real path: verification failure is never success.
# ---------------------------------------------------------------------------


def test_action_success_but_canonical_verification_failure_is_not_success() -> None:
    """The capability executes successfully but verify() fails => NOT success."""
    harness = OrchestrationHarness(capability=DemoNoteCapability(verification_mode="fail"))
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))
    request = harness.make_request(limits=default_limits(max_total_attempts=2))

    outcome = loop.run(request).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.task.status is TaskStatus.FAILED
    assert harness.capability.execute_calls >= 1
    assert harness.capability.verify_calls >= 1
    for record in outcome.attempts:
        assert record.disposition is AttemptDisposition.UNVERIFIED
        assert record.outcome is not None
        assert record.outcome.kind is LoopOutcome.VERIFICATION_FAILED
        assert record.outcome.verification is not None
        assert record.outcome.verification.passed is False


def test_observation_exists_without_verification_and_is_not_success() -> None:
    """A failed execution still produces an observation; that is not success."""
    harness = OrchestrationHarness(capability=DemoNoteCapability(execution_mode="fail"))
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))

    outcome = loop.run(
        harness.make_request(limits=default_limits(max_total_attempts=1))
    ).unwrap()

    record = outcome.attempts[0]
    assert record.outcome is not None
    assert isinstance(record.outcome.observation, CapabilityObservation)
    assert record.outcome.verification is None
    assert record.disposition is AttemptDisposition.UNVERIFIED
    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert harness.capability.verify_calls == 0


def test_capability_exception_is_not_success() -> None:
    """A raised exception inside the governed path never becomes success."""
    harness = OrchestrationHarness(capability=DemoNoteCapability(execution_mode="raise"))
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))

    outcome = loop.run(
        harness.make_request(limits=default_limits(max_total_attempts=1))
    ).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.attempts[0].outcome is not None
    assert outcome.attempts[0].outcome.kind is LoopOutcome.EXECUTION_FAILED


def test_verification_exception_is_not_success() -> None:
    """A verify() exception is a verification failure, never success."""
    harness = OrchestrationHarness(capability=DemoNoteCapability(verification_mode="raise"))
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))

    outcome = loop.run(
        harness.make_request(limits=default_limits(max_total_attempts=1))
    ).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.attempts[0].outcome is not None
    assert outcome.attempts[0].outcome.kind is LoopOutcome.VERIFICATION_FAILED


def test_explicit_requirement_mismatch_blocks_success_despite_canonical_verification() -> None:
    """Both halves of the I1 gate must agree: A1.10 verdict AND A2.05 evaluation."""
    harness = OrchestrationHarness()
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))
    request = harness.make_request(
        requirement=VerificationRequirement({"stored": True, "value": "different"}),
        limits=default_limits(max_total_attempts=1),
    )

    outcome = loop.run(request).unwrap()

    record = outcome.attempts[0]
    assert record.outcome is not None
    assert record.outcome.kind is LoopOutcome.VERIFIED
    assert record.outcome.verification is not None
    assert record.outcome.verification.passed is True
    assert record.evaluation is not None
    assert record.evaluation.satisfied is False
    assert record.disposition is AttemptDisposition.UNVERIFIED
    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.task.status is TaskStatus.FAILED


# ---------------------------------------------------------------------------
# Authority: denial, emergency stop, resource exhaustion over the real kernel.
# ---------------------------------------------------------------------------


def test_denied_action_gate_path_never_executes_and_never_succeeds() -> None:
    """Missing WRITE authority denies the run before any capability effect."""
    harness = OrchestrationHarness(authority=frozenset())
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))

    outcome = loop.run(
        harness.make_request(limits=default_limits(max_total_attempts=2))
    ).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert harness.capability.execute_calls == 0
    assert harness.capability.verify_calls == 0
    assert harness.capability.state == {}
    for record in outcome.attempts:
        assert record.outcome is not None
        assert record.outcome.kind is LoopOutcome.DENIED
    assert any(item.outcome is AuditOutcome.DENY for item in harness.audit_records)
    assert outcome.task.status is TaskStatus.FAILED


def test_gate_denial_for_destructive_risk_is_not_success() -> None:
    """A canonical gate denial at higher effective risk stays a denial."""
    harness = OrchestrationHarness(
        capability=DemoNoteCapability(destructive=True),
        authority=frozenset({Permission.WRITE}),
    )
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))

    outcome = loop.run(
        harness.make_request(limits=default_limits(max_total_attempts=1))
    ).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert harness.capability.execute_calls == 0
    assert outcome.attempts[0].outcome is not None
    assert outcome.attempts[0].outcome.kind is LoopOutcome.DENIED


def test_emergency_stop_active_halts_orchestration_and_never_executes() -> None:
    """C1.09 EmergencyStop stops the run; A2.10 never clears or bypasses it."""
    harness = OrchestrationHarness()
    harness.emergency_stop.request_stop()
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))

    outcome = loop.run(
        harness.make_request(limits=default_limits(max_total_attempts=4))
    ).unwrap()

    assert harness.capability.execute_calls == 0
    assert harness.emergency_stop.stop_requested is True
    assert outcome.status is OrchestrationStatus.CANCELLED
    assert outcome.stop_reason is OrchestrationStopReason.SAFETY_STOP
    assert outcome.attempt_count == 1
    assert outcome.attempts[0].disposition is AttemptDisposition.SAFETY_STOPPED
    assert outcome.task.status is TaskStatus.CANCELLED
    assert any(
        item.outcome is AuditOutcome.STOP_REQUESTED for item in harness.audit_records
    )


def test_resource_exhaustion_fails_closed_and_never_widens_the_envelope() -> None:
    """C1.08 exhaustion is terminal: no retry, no escalation, no budget reset."""
    harness = OrchestrationHarness(envelope=make_envelope(max_machine_actions=1))
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))

    first = loop.run(harness.make_request(limits=default_limits(max_total_attempts=1))).unwrap()
    assert first.status is OrchestrationStatus.SUCCEEDED
    assert harness.budget.snapshot().machine_actions == 1

    # The single machine action is spent; the next run must fail closed.
    second = loop.run(
        harness.make_request(limits=default_limits(max_total_attempts=4))
    ).unwrap()

    assert second.status is OrchestrationStatus.FAILED
    assert second.stop_reason is OrchestrationStopReason.RESOURCE_EXHAUSTED
    assert second.attempt_count == 1
    assert second.attempts[0].disposition is AttemptDisposition.RESOURCE_EXHAUSTED
    assert second.attempts[0].escalation is None
    assert harness.budget.envelope.max_machine_actions == 1
    assert harness.budget.snapshot().machine_actions == 1
    assert harness.capability.execute_calls == 1


def test_resource_exhaustion_is_not_relaxed_by_escalation() -> None:
    """Escalation never enlarges the envelope; exhaustion still fails closed."""
    harness = OrchestrationHarness(envelope=make_envelope(max_machine_actions=0))
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))

    outcome = loop.run(
        harness.make_request(
            routing_evidence=RoutingEvidence(verified_reusable_result=True),
            limits=default_limits(max_total_attempts=6),
        )
    ).unwrap()

    assert outcome.stop_reason is OrchestrationStopReason.RESOURCE_EXHAUSTED
    assert outcome.attempt_count == 1
    assert outcome.final_level is ExecutionLevel.L0_CACHE
    assert harness.budget.envelope.max_machine_actions == 0
    assert harness.capability.execute_calls == 0


def test_capability_not_registered_is_denied_and_not_success() -> None:
    """An unregistered capability is a canonical denial, never a success."""
    harness = OrchestrationHarness(register_capability=False)
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))

    outcome = loop.run(
        harness.make_request(limits=default_limits(max_total_attempts=1))
    ).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.attempts[0].outcome is not None
    assert outcome.attempts[0].outcome.kind is LoopOutcome.DENIED


# ---------------------------------------------------------------------------
# Escalation and L5 over the real path.
# ---------------------------------------------------------------------------


def test_escalation_walks_the_hierarchy_and_then_verifies() -> None:
    """Unavailable cheap levels escalate one step at a time to a governed level."""
    harness = OrchestrationHarness()
    governed = harness.governed_strategy()
    unavailable = UnavailableStrategy()
    loop = harness.agent_loop(
        {
            ExecutionLevel.L0_CACHE: unavailable,
            ExecutionLevel.L1_DIRECT: unavailable,
            ExecutionLevel.L2_COMPILED: governed,
        }
    )
    request = harness.make_request(
        routing_evidence=RoutingEvidence(verified_reusable_result=True),
        limits=default_limits(max_total_attempts=4),
    )

    outcome = loop.run(request).unwrap()

    assert [record.level for record in outcome.attempts] == [
        ExecutionLevel.L0_CACHE,
        ExecutionLevel.L1_DIRECT,
        ExecutionLevel.L2_COMPILED,
    ]
    assert outcome.status is OrchestrationStatus.SUCCEEDED
    assert outcome.initial_level is ExecutionLevel.L0_CACHE
    assert outcome.final_level is ExecutionLevel.L2_COMPILED
    assert harness.capability.execute_calls == 1


def test_reaching_l5_without_an_authorized_strategy_never_researches() -> None:
    """L5 is a classification, not authority: no adapter => fail closed, no effect."""
    harness = OrchestrationHarness()
    unavailable = UnavailableStrategy()
    loop = harness.agent_loop(
        {level: unavailable for level in ExecutionLevel if level is not ExecutionLevel.L5_EXPLORATORY}
    )
    request = harness.make_request(
        routing_evidence=RoutingEvidence(verified_reusable_result=True),
        limits=default_limits(max_total_attempts=8),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.final_level is ExecutionLevel.L5_EXPLORATORY
    assert outcome.stop_reason is OrchestrationStopReason.STRATEGY_UNAVAILABLE
    assert outcome.status is OrchestrationStatus.FAILED
    assert [record.level for record in outcome.attempts] == list(CANONICAL_EXECUTION_LEVELS)
    # Nothing executed, nothing was researched, no budget moved.
    assert harness.capability.execute_calls == 0
    assert harness.budget.snapshot().research_queries == 0
    assert harness.budget.snapshot().model_calls == 0
    assert harness.budget.snapshot().machine_actions == 0


def test_l5_exhaustion_after_a_real_governed_failure() -> None:
    """An L5-routed governed failure exhausts because L5 has no successor."""
    harness = OrchestrationHarness(capability=DemoNoteCapability(verification_mode="fail"))
    strategy = harness.governed_strategy()
    loop = harness.agent_loop({ExecutionLevel.L5_EXPLORATORY: strategy})
    request = harness.make_request(
        routing_evidence=RoutingEvidence(), limits=default_limits(max_total_attempts=4)
    )

    outcome = loop.run(request).unwrap()

    assert outcome.initial_level is ExecutionLevel.L5_EXPLORATORY
    assert outcome.status is OrchestrationStatus.EXHAUSTED
    assert outcome.stop_reason is OrchestrationStopReason.ESCALATION_EXHAUSTED
    assert outcome.attempt_count == 1
    assert outcome.task.status is TaskStatus.FAILED


# ---------------------------------------------------------------------------
# Cancellation and deadline over the real path.
# ---------------------------------------------------------------------------


def test_cancellation_before_the_first_attempt_touches_nothing() -> None:
    """A cancelled request never executes, never budgets, never publishes."""
    harness = OrchestrationHarness()
    source = CancellationSource()
    source.request_cancellation("operator cancelled")
    task = harness.make_task()
    request = harness.make_request(
        task=task, context=harness.make_context(task, cancellation=source)
    )
    loop = harness.agent_loop(_all_levels(harness.governed_strategy()))

    outcome = loop.run(request).unwrap()

    assert outcome.status is OrchestrationStatus.CANCELLED
    assert outcome.attempt_count == 0
    assert harness.capability.execute_calls == 0
    assert harness.events == []
    assert harness.audit_records == []
    assert harness.budget.snapshot().machine_actions == 0
    assert harness.task_status(task) is TaskStatus.CANCELLED


def test_cancellation_between_attempts_stops_the_governed_path() -> None:
    """Cancelling after attempt 1 prevents any further governed execution."""
    harness = OrchestrationHarness(capability=DemoNoteCapability(verification_mode="fail"))
    source = CancellationSource()
    task = harness.make_task()
    request = harness.make_request(
        task=task,
        context=harness.make_context(task, cancellation=source),
        limits=default_limits(max_total_attempts=4),
    )

    class CancelAfterFirst:
        def __init__(self, inner: object) -> None:
            self._inner = inner
            self.calls = 0

        def attempt(self, task, context, level):  # noqa: ANN001, ANN202 - test adapter
            self.calls += 1
            result = self._inner.attempt(task, context, level)  # type: ignore[attr-defined]
            source.request_cancellation("cancelled between attempts")
            return result

    adapter = CancelAfterFirst(harness.governed_strategy())
    loop = harness.agent_loop(_all_levels(adapter))

    outcome = loop.run(request).unwrap()

    assert adapter.calls == 1
    assert harness.capability.execute_calls == 1
    assert outcome.status is OrchestrationStatus.CANCELLED
    assert outcome.stop_reason is OrchestrationStopReason.CANCELLED
    assert outcome.attempt_count == 1
    assert harness.task_status(task) is TaskStatus.CANCELLED


def test_deadline_expiry_stops_orchestration_deterministically() -> None:
    """An expired A1.07 deadline stops the run without any wall-clock sleeping."""
    harness = OrchestrationHarness()
    clock = FixedClock(now=1000.0)
    task = harness.make_task()
    request = harness.make_request(
        task=task,
        context=harness.make_context(task, deadline=Deadline(clock.monotonic() - 0.5)),
    )
    loop = harness.agent_loop(_all_levels(harness.governed_strategy()))

    outcome = loop.run(request, clock=clock).unwrap()

    assert outcome.status is OrchestrationStatus.TIMED_OUT
    assert outcome.stop_reason is OrchestrationStopReason.DEADLINE_EXPIRED
    assert harness.capability.execute_calls == 0
    assert harness.task_status(task) is TaskStatus.CANCELLED


# ---------------------------------------------------------------------------
# Anti-loop over the real path, and overall determinism.
# ---------------------------------------------------------------------------


def test_anti_loop_bounds_repeated_governed_failures() -> None:
    """A2.09 stops repeated identical governed failures at the configured bound."""
    from agentx.cognition.agent_loop import OrchestrationLimits
    from agentx.cognition.anti_loop import LoopGuardDecision, LoopGuardLimits

    harness = OrchestrationHarness(capability=DemoNoteCapability(verification_mode="fail"))
    strategy = harness.governed_strategy()
    loop = harness.agent_loop(_all_levels(strategy))
    request = harness.make_request(
        routing_evidence=RoutingEvidence(verified_reusable_result=True),
        limits=OrchestrationLimits(
            max_total_attempts=6,
            loop_guard_limits=LoopGuardLimits(
                max_total_attempts=3,
                max_same_attempts=8,
                max_same_outcomes_without_progress=8,
            ),
        ),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.attempt_count == 3
    assert outcome.stop_reason is OrchestrationStopReason.ANTI_LOOP
    assert outcome.status is OrchestrationStatus.EXHAUSTED
    assert outcome.attempts[-1].loop_guard.decision is LoopGuardDecision.STOP_LOOP
    assert outcome.attempts[-1].escalation is None
    assert harness.capability.execute_calls == 3
    assert outcome.task.status is TaskStatus.FAILED


def test_repeated_identical_runs_are_deterministic_end_to_end() -> None:
    """Two independently composed runs make identical orchestration decisions."""
    snapshots = []
    for _ in range(2):
        harness = OrchestrationHarness(
            capability=DemoNoteCapability(verification_mode="fail")
        )
        loop = harness.agent_loop(_all_levels(harness.governed_strategy()))
        outcome = loop.run(
            harness.make_request(
                routing_evidence=RoutingEvidence(verified_reasoning_free_procedure=True),
                limits=default_limits(max_total_attempts=3),
            )
        ).unwrap()
        snapshots.append(
            (
                outcome.status,
                outcome.stop_reason,
                outcome.initial_level,
                outcome.final_level,
                tuple((r.level, r.disposition) for r in outcome.attempts),
            )
        )

    assert snapshots[0] == snapshots[1]


def test_every_terminal_path_leaves_a_canonical_terminal_task() -> None:
    """No orchestration path leaves the managed Task mid-flight."""
    scenarios = [
        OrchestrationHarness(),
        OrchestrationHarness(capability=DemoNoteCapability(verification_mode="fail")),
        OrchestrationHarness(authority=frozenset()),
        OrchestrationHarness(envelope=make_envelope(max_machine_actions=0)),
    ]
    for harness in scenarios:
        loop = harness.agent_loop(_all_levels(harness.governed_strategy()))
        request = harness.make_request(limits=default_limits(max_total_attempts=2))
        outcome = loop.run(request).unwrap()

        assert is_terminal(outcome.task.status)
        assert harness.task_status(request.task) is outcome.task.status
        assert (outcome.task.status is TaskStatus.SUCCEEDED) == (
            outcome.status is OrchestrationStatus.SUCCEEDED
        )


@pytest.mark.parametrize(
    "verification_mode",
    ["fail", "raise"],
)
def test_no_capability_effect_is_ever_promoted_to_success(verification_mode: str) -> None:
    """Even when the in-memory effect really happened, unverified is not success."""
    capability = DemoNoteCapability(verification_mode=verification_mode)
    harness = OrchestrationHarness(capability=capability)
    loop = harness.agent_loop(_all_levels(harness.governed_strategy()))

    outcome = loop.run(
        harness.make_request(limits=default_limits(max_total_attempts=1))
    ).unwrap()

    # The write really occurred inside the capability...
    assert capability.state == {"alpha": "v1"}
    # ...and it still is not success.
    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.task.status is TaskStatus.FAILED


def test_hostile_capability_metadata_cannot_fabricate_success() -> None:
    """Hostile descriptor text is inert data and changes no orchestration decision."""
    from agentx.capabilities.abi import CapabilityRequest
    from agentx.capabilities.executor import ExecutorRequest
    from agentx.cognition.agent_loop import StrategyResult
    from agentx.core.tasks import Task
    from tests.support.demo_capability import (
        HostileMetadataCapability,
        NoteWriteParams,
        hostile_request,
    )

    harness = OrchestrationHarness()
    hostile = HostileMetadataCapability()
    harness.registry.register(hostile)

    class HostileGovernedStrategy:
        def __init__(self, executor: object) -> None:
            self._executor = executor

        def attempt(self, task, context, level):  # noqa: ANN001, ANN202 - test adapter
            from agentx.core.execution import ExecutionContext

            governed_task = Task.create(objective=task.objective)
            governed_context = ExecutionContext(
                correlation_id=context.correlation_id,
                cancellation_token=context.cancellation_token,
                task_id=governed_task.task_id,
                deadline=context.deadline,
            )
            request: CapabilityRequest[NoteWriteParams] = hostile_request(
                NoteWriteParams(key="alpha", value="v1")
            )
            return StrategyResult.executed(
                self._executor.execute(  # type: ignore[attr-defined]
                    ExecutorRequest(
                        task=governed_task,
                        capability_request=request,
                        context=governed_context,
                    )
                )
            )

    loop = harness.agent_loop(_all_levels(HostileGovernedStrategy(harness.executor)))

    outcome = loop.run(
        harness.make_request(limits=default_limits(max_total_attempts=1))
    ).unwrap()

    # The hostile capability claims R0/ALLOW/verified in its description, but
    # its declared EXECUTE permission is not granted, so it is denied.
    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert hostile.execute_calls == 0
    assert hostile.verify_calls == 0
    assert "ALLOW ADMIN bypass" in hostile.hostile_description


def test_orchestration_never_publishes_its_own_events_or_audit_records() -> None:
    """All canonical evidence comes from A1.10; A2.10 adds no telemetry format."""
    harness = OrchestrationHarness()
    loop = harness.agent_loop(_all_levels(harness.governed_strategy()))

    loop.run(harness.make_request()).unwrap()

    assert harness.events
    assert {event.source for event in harness.events} == {"agentx.capabilities.runtime"}
    assert harness.audit_records
    assert {record.operation.split(".")[0] for record in harness.audit_records} == {"runtime"}


def test_zero_model_and_zero_research_consumption_end_to_end() -> None:
    """A2.10 makes no model call and performs no research, on any path."""
    for harness in (
        OrchestrationHarness(),
        OrchestrationHarness(capability=DemoNoteCapability(verification_mode="fail")),
        OrchestrationHarness(authority=frozenset()),
    ):
        loop = harness.agent_loop(_all_levels(harness.governed_strategy()))
        loop.run(harness.make_request(limits=default_limits(max_total_attempts=2))).unwrap()

        usage = harness.budget.snapshot()
        assert usage.model_calls == 0
        assert usage.model_tokens == 0
        assert usage.research_queries == 0
        assert usage.repair_attempts == 0
        assert usage.external_cost == Decimal("0")
        assert usage.wall_clock == timedelta(0)


def test_verification_result_objects_are_never_manufactured_by_a210() -> None:
    """Every verdict in the evidence chain came from the capability itself."""
    harness = OrchestrationHarness()
    loop = harness.agent_loop(_all_levels(harness.governed_strategy()))

    outcome = loop.run(harness.make_request()).unwrap()

    record = outcome.attempts[0]
    assert record.outcome is not None
    verification = record.outcome.verification
    assert isinstance(verification, VerificationResult)
    # The capability's own verify() produced exactly this verdict.
    assert harness.capability.verify_calls == 1
    assert verification.passed is True
    assert "holds the requested value" in verification.detail
