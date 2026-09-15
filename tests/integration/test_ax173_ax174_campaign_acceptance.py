"""Campaign acceptance proofs for the landed real L2/L3 procedure workflows."""

from __future__ import annotations

from agentx.agent_loop import OrchestrationStatus
from agentx.capabilities.verifier import VerificationRequirement
from agentx.cognition.router import ExecutionLevel, RoutingEvidence
from agentx.core.procedure_execution import ExecutedNodeKind, ProcedureRunDisposition
from agentx.core.tasks import TaskStatus
from agentx.guided_procedure_strategy import GuidedProcedureStrategy
from tests.integration.test_compiled_procedure_strategy import _adapter as compiled_adapter
from tests.integration.test_guided_procedure_strategy import _strategy as guided_strategy
from tests.support.orchestration_harness import OrchestrationHarness, default_limits
from tests.unit.test_guided_procedure_strategy import (
    FakeModelProvider,
    deterministic_binding,
)


def test_ax173_real_l2_chain_reaches_task_success_only_after_independent_verification() -> None:
    harness = OrchestrationHarness()
    adapter = compiled_adapter(harness)
    loop = harness.agent_loop({ExecutionLevel.L2_COMPILED: adapter})
    request = harness.make_request(
        routing_evidence=RoutingEvidence(verified_reasoning_free_procedure=True),
        requirement=VerificationRequirement({"stored": True}),
        limits=default_limits(max_total_attempts=1, escalation_permitted=False),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.initial_level is ExecutionLevel.L2_COMPILED
    assert outcome.status is OrchestrationStatus.SUCCEEDED
    assert outcome.verified is True
    assert harness.task_status(request.task) is TaskStatus.SUCCEEDED
    assert harness.capability.execute_calls == 1
    assert harness.capability.verify_calls == 1
    assert outcome.attempts[0].evaluation is not None
    assert outcome.attempts[0].evaluation.satisfied is True


def test_ax174_guided_chain_records_exact_reasoning_and_deterministic_regions() -> None:
    harness = OrchestrationHarness()
    provider = FakeModelProvider(outputs=("v1 is the right value",))
    strategy = guided_strategy(harness, provider=provider)
    task = harness.make_task("guided acceptance")
    context = harness.make_context(task)

    run = strategy.run_guided(task, context)

    assert run.disposition is ProcedureRunDisposition.REACHED_END
    assert run.reasoning_calls == 1
    assert run.governed_dispatches == 1
    assert len(provider.calls) == 1
    # REACHED_END is the terminal disposition; the canonical trace records
    # executed work nodes rather than manufacturing an executable END step.
    assert tuple(step.node_kind for step in run.steps) == (
        ExecutedNodeKind.REASON,
        ExecutedNodeKind.ACTION,
    )


def test_ax174_task_success_still_requires_independent_outer_verification() -> None:
    harness = OrchestrationHarness()
    provider = FakeModelProvider(outputs=("v1 is the right value",))
    strategy = guided_strategy(harness, provider=provider)
    loop = harness.agent_loop({ExecutionLevel.L3_GUIDED: strategy})
    request = harness.make_request(
        routing_evidence=RoutingEvidence(procedure_with_reasoning_gaps=True),
        requirement=VerificationRequirement({"stored": True, "key": "alpha"}),
        limits=default_limits(max_total_attempts=1, escalation_permitted=False),
    )

    outcome = loop.run(request).unwrap()

    assert outcome.status is OrchestrationStatus.SUCCEEDED
    assert outcome.verified is True
    assert harness.task_status(request.task) is TaskStatus.SUCCEEDED
    assert len(provider.calls) == 1
    assert harness.capability.execute_calls == 1
    assert harness.capability.verify_calls == 1


def test_ax174_deterministic_guided_regions_do_not_call_reasoner() -> None:
    harness = OrchestrationHarness()
    provider = FakeModelProvider()
    strategy = GuidedProcedureStrategy(
        executor=harness.executor,
        binding=deterministic_binding(),
        reasoner=None,
    )
    task = harness.make_task("deterministic guided region")

    run = strategy.run_guided(task, harness.make_context(task))

    assert run.reasoning_calls == 0
    assert run.governed_dispatches == 1
    assert provider.calls == []
