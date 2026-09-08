"""Integration tests for N2.03 against the canonical A2.10 strategy boundary."""

from __future__ import annotations

from datetime import timedelta

from tests.support.orchestration_harness import default_limits
from tests.unit.test_cache_strategy import (
    _T0,
    _candidate,
    _context,
    _prior,
    _StaticLookup,
)

from agentx.agent_loop import (
    AgentLoop,
    OrchestrationRequest,
    OrchestrationStatus,
    StrategyRegistry,
)
from agentx.cache_strategy import VerifiedCacheStrategy
from agentx.capabilities.verifier import VerificationRequirement
from agentx.cognition.router import ExecutionLevel, RoutingEvidence
from agentx.cognition.task_manager import TaskManager
from agentx.core.tasks import TaskStatus


def _request(
    manager: TaskManager,
    *,
    expected_fingerprint: str,
) -> tuple[OrchestrationRequest, VerifiedCacheStrategy, _StaticLookup]:
    prior = _prior()
    candidate = _candidate(prior=prior)
    task = manager.create(
        prior.task.objective,
        priority=prior.task.priority,
        parent_task_id=prior.task.parent_task_id,
        created_at=_T0 + timedelta(seconds=3),
        metadata=dict(prior.task.metadata),
    )
    lookup = _StaticLookup(candidate)
    strategy = VerifiedCacheStrategy(lookup=lookup)
    request = OrchestrationRequest(
        task=task,
        context=_context(task),
        routing_evidence=RoutingEvidence(verified_reusable_result=True),
        requirement=VerificationRequirement(
            expected_observation={"fingerprint": expected_fingerprint}
        ),
        limits=default_limits(max_total_attempts=2),
    )
    return request, strategy, lookup


def test_strategy_registers_at_l0_and_a210_accepts_verified_reuse() -> None:
    manager = TaskManager()
    request, strategy, lookup = _request(
        manager,
        expected_fingerprint="sha256:abc123",
    )
    registry = StrategyRegistry({ExecutionLevel.L0_CACHE: strategy})
    loop = AgentLoop(task_manager=manager, strategies=registry)

    assert registry.get(ExecutionLevel.L0_CACHE) is strategy
    assert registry.levels() == (ExecutionLevel.L0_CACHE,)

    result = loop.run(request)
    outcome = result.unwrap()

    assert outcome.status is OrchestrationStatus.SUCCEEDED
    assert outcome.final_level is ExecutionLevel.L0_CACHE
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert manager.require(request.task.task_id).status is TaskStatus.SUCCEEDED
    assert lookup.calls == 1


def test_cache_hit_does_not_bypass_current_a205_verification_requirement() -> None:
    manager = TaskManager()
    request, strategy, lookup = _request(
        manager,
        expected_fingerprint="sha256:different",
    )
    loop = AgentLoop(
        task_manager=manager,
        strategies=StrategyRegistry({ExecutionLevel.L0_CACHE: strategy}),
    )

    result = loop.run(request)
    outcome = result.unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.task.status is not TaskStatus.SUCCEEDED
    assert manager.require(request.task.task_id).status is not TaskStatus.SUCCEEDED
    assert lookup.calls == 1
