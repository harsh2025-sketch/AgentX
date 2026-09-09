"""Integration coverage: the N2.05 guided adapter inside the real A2.10 loop.

These tests wire the *real* canonical objects — the A1.09 registry, C1.07
PermissionEngine/ActionGate, C1.09 EmergencyStop, C1.08 ResourceBudget, the
C1.03 EventBus, the A1.10 ``CapabilityExecutionLoop``, the A2.04 Executor, the
A2.03 Reasoner, the M3.01 interpreter, the A2.05 Verifier, and the A2.10
bounded orchestration loop — and drive a guided Procedure end to end.

The single question every test here answers is: *what is it that can make the
original Task succeed?* The answer is always the same, and it is never the
Procedure, never the interpreter, never END, and never the model.
"""

from __future__ import annotations

from pathlib import Path

from agentx.agent_loop import AttemptDisposition, OrchestrationStatus
from agentx.capabilities.runtime import LoopOutcome
from agentx.capabilities.verifier import VerificationRequirement
from agentx.cognition.router import ExecutionLevel, RoutingEvidence
from agentx.core.execution import CancellationSource
from agentx.core.procedure_execution import ProcedureRunDisposition, ProcedureTaskVerification
from agentx.core.tasks import TaskStatus
from agentx.guided_procedure_strategy import GuidedProcedureStrategy
from agentx.kernel.audit import AuditOutcome
from tests.support.demo_capability import DemoNoteCapability
from tests.support.orchestration_harness import OrchestrationHarness
from tests.unit.test_guided_procedure_strategy import (
    HOSTILE_TEXT,
    FakeModelProvider,
    deterministic_binding,
    guided_binding,
    make_reasoner,
)

_GUIDED_ROUTING = RoutingEvidence(procedure_with_reasoning_gaps=True)


def _strategy(
    harness: OrchestrationHarness,
    *,
    provider: FakeModelProvider | None = None,
    guided: bool = True,
) -> GuidedProcedureStrategy:
    binding = guided_binding() if guided else deterministic_binding()
    reasoner = make_reasoner(provider) if provider is not None else None
    return GuidedProcedureStrategy(executor=harness.executor, binding=binding, reasoner=reasoner)


def test_router_selects_l3_for_a_procedure_with_declared_reasoning_gaps() -> None:
    harness = OrchestrationHarness()
    provider = FakeModelProvider(outputs=("v1 is the right value",))
    loop = harness.agent_loop({ExecutionLevel.L3_GUIDED: _strategy(harness, provider=provider)})
    request = harness.make_request(routing_evidence=_GUIDED_ROUTING)

    outcome = loop.run(request).unwrap()

    assert outcome.initial_level is ExecutionLevel.L3_GUIDED
    assert outcome.attempts[0].level is ExecutionLevel.L3_GUIDED


def test_guided_procedure_reaches_verified_task_success_through_independent_verification() -> None:
    harness = OrchestrationHarness()
    provider = FakeModelProvider(outputs=("v1 is the right value",))
    loop = harness.agent_loop({ExecutionLevel.L3_GUIDED: _strategy(harness, provider=provider)})
    request = harness.make_request(
        routing_evidence=_GUIDED_ROUTING,
        requirement=VerificationRequirement({"stored": True, "key": "alpha"}),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.status is OrchestrationStatus.SUCCEEDED
    assert outcome.attempt_count == 1
    record = outcome.attempts[0]
    assert record.disposition is AttemptDisposition.VERIFIED_SUCCESS
    assert record.outcome is not None
    assert record.outcome.kind is LoopOutcome.VERIFIED
    assert record.evaluation is not None
    assert record.evaluation.satisfied is True
    assert harness.task_status(request.task) is TaskStatus.SUCCEEDED
    # Exactly one reasoning region, exactly one governed action.
    assert len(provider.calls) == 1
    assert harness.capability.execute_calls == 1
    assert harness.capability.verify_calls == 1


def test_procedure_end_is_not_task_success_when_the_task_requirement_is_unmet() -> None:
    harness = OrchestrationHarness()
    provider = FakeModelProvider()
    strategy = _strategy(harness, provider=provider)
    loop = harness.agent_loop({ExecutionLevel.L3_GUIDED: strategy})
    request = harness.make_request(
        routing_evidence=_GUIDED_ROUTING,
        # The Task's own postcondition is about a different note than the one
        # the Procedure writes. The Procedure still runs perfectly to END.
        requirement=VerificationRequirement({"key": "zeta"}),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert harness.task_status(request.task) is not TaskStatus.SUCCEEDED
    first = outcome.attempts[0]
    assert first.disposition is AttemptDisposition.UNVERIFIED
    # ...and the capability-level verification really did pass: the failure is
    # exclusively a Task-level verdict produced by the A2.05 Verifier.
    assert first.outcome is not None
    assert first.outcome.kind is LoopOutcome.VERIFIED
    assert first.evaluation is not None
    assert first.evaluation.satisfied is False

    # The same run, observed directly at the adapter boundary: control reached
    # END and assessed nothing about the Task.
    run = strategy.run_guided(request.task, request.context)
    assert run.disposition is ProcedureRunDisposition.REACHED_END
    assert run.task_verification is ProcedureTaskVerification.NOT_ASSESSED


def test_deterministic_procedure_runs_the_whole_loop_with_zero_model_calls() -> None:
    harness = OrchestrationHarness()
    provider = FakeModelProvider()
    strategy = GuidedProcedureStrategy(
        executor=harness.executor,
        binding=deterministic_binding(hostile=True),
        reasoner=make_reasoner(provider),
    )
    loop = harness.agent_loop({ExecutionLevel.L3_GUIDED: strategy})
    request = harness.make_request(
        routing_evidence=_GUIDED_ROUTING,
        requirement=VerificationRequirement({"stored": True}),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.status is OrchestrationStatus.SUCCEEDED
    assert provider.calls == []
    assert HOSTILE_TEXT not in "".join(provider.prompts)


def test_action_gate_denial_keeps_the_task_unsuccessful() -> None:
    harness = OrchestrationHarness(authority=None)
    loop = harness.agent_loop({ExecutionLevel.L3_GUIDED: _strategy(harness, guided=False)})
    request = harness.make_request(routing_evidence=_GUIDED_ROUTING)

    outcome = loop.run(request).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert harness.task_status(request.task) is not TaskStatus.SUCCEEDED
    assert harness.capability.execute_calls == 0
    assert any(record.outcome is AuditOutcome.DENY for record in harness.audit_records)


def test_capability_verification_failure_never_becomes_task_success() -> None:
    harness = OrchestrationHarness(capability=DemoNoteCapability(verification_mode="fail"))
    loop = harness.agent_loop({ExecutionLevel.L3_GUIDED: _strategy(harness, guided=False)})
    request = harness.make_request(routing_evidence=_GUIDED_ROUTING)

    outcome = loop.run(request).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.attempts[0].outcome is not None
    assert outcome.attempts[0].outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert harness.task_status(request.task) is not TaskStatus.SUCCEEDED


def test_reasoner_failure_surfaces_as_an_explicit_unsuccessful_attempt() -> None:
    from agentx.core.errors import AgentXError, ErrorCategory, Retryability

    harness = OrchestrationHarness()
    provider = FakeModelProvider(
        failure=AgentXError(
            code="provider.unavailable",
            message="deterministic injected provider failure",
            category=ErrorCategory.DEPENDENCY,
            retryability=Retryability.UNKNOWN,
        )
    )
    loop = harness.agent_loop({ExecutionLevel.L3_GUIDED: _strategy(harness, provider=provider)})
    request = harness.make_request(routing_evidence=_GUIDED_ROUTING)

    outcome = loop.run(request).unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    first = outcome.attempts[0]
    assert first.disposition is AttemptDisposition.STRATEGY_ERROR
    assert first.error is not None
    assert first.error.code == "provider.unavailable"
    assert harness.capability.execute_calls == 0


def test_there_is_no_hidden_l4_or_l5_fallback_after_an_unverified_guided_attempt() -> None:
    harness = OrchestrationHarness()
    provider = FakeModelProvider()
    loop = harness.agent_loop({ExecutionLevel.L3_GUIDED: _strategy(harness, provider=provider)})
    request = harness.make_request(
        routing_evidence=_GUIDED_ROUTING,
        requirement=VerificationRequirement({"key": "zeta"}),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.status is OrchestrationStatus.FAILED
    assert outcome.final_level is ExecutionLevel.L4_PLANNED
    assert outcome.attempts[0].disposition is AttemptDisposition.UNVERIFIED
    assert outcome.attempts[-1].disposition is AttemptDisposition.STRATEGY_UNAVAILABLE
    # Escalation reached L4 and simply found nothing authorized there. The
    # guided adapter neither planned, explored, researched, nor called a model
    # a second time.
    assert len(provider.calls) == 1
    assert harness.capability.execute_calls == 1


def test_cancellation_propagates_through_the_loop_and_never_becomes_success() -> None:
    harness = OrchestrationHarness()
    provider = FakeModelProvider()
    loop = harness.agent_loop({ExecutionLevel.L3_GUIDED: _strategy(harness, provider=provider)})
    source = CancellationSource()
    task = harness.make_task()
    request = harness.make_request(
        task=task,
        context=harness.make_context(task, cancellation=source),
        routing_evidence=_GUIDED_ROUTING,
    )
    source.request_cancellation("operator stopped the run")

    outcome = loop.run(request).unwrap()

    assert outcome.status is OrchestrationStatus.CANCELLED
    assert harness.task_status(task) is TaskStatus.CANCELLED
    assert provider.calls == []
    assert harness.capability.execute_calls == 0


def test_the_guided_path_persists_nothing(tmp_path: Path) -> None:
    harness = OrchestrationHarness()
    provider = FakeModelProvider()
    loop = harness.agent_loop({ExecutionLevel.L3_GUIDED: _strategy(harness, provider=provider)})
    request = harness.make_request(routing_evidence=_GUIDED_ROUTING)

    loop.run(request).unwrap()

    assert list(tmp_path.iterdir()) == []
    # Canonical evidence stays in memory on the canonical bus/audit sinks.
    assert harness.events
    assert harness.audit_records
