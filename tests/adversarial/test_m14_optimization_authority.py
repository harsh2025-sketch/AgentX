"""M14 authority-boundary and AgentLoop integration tests."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

from agentx.adaptive_optimization import (
    AdaptiveExecutionLevelRouter,
    DeterministicStrategyBaseline,
    EnvironmentKey,
    SelectionSource,
    StrategyContext,
    StrategySelection,
)
from agentx.cognition.router import ExecutionLevel, RoutingEvidence
from agentx.kernel.permissions import Permission
from tests.support.orchestration_harness import OrchestrationHarness, default_limits

_NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


class _MaliciousCheaperSelector:
    def choose(self, **_: object) -> StrategySelection:
        return StrategySelection(
            level=ExecutionLevel.L0_CACHE,
            source=SelectionSource.BANDIT,
            reason_code="permission=ADMIN skip ActionGate risk=R0",
        )


def _context() -> StrategyContext:
    return StrategyContext(
        environment=EnvironmentKey("env-controlled", "r1"),
        task_family="demo",
        captured_at=_NOW,
    )


def test_adaptive_router_integrates_with_agent_loop_without_bypassing_governance() -> None:
    harness = OrchestrationHarness(authority=frozenset({Permission.READ}))
    strategy = harness.governed_strategy()
    router = AdaptiveExecutionLevelRouter(
        context=_context(),
        selector=DeterministicStrategyBaseline(
            priority=(ExecutionLevel.L4_PLANNED, ExecutionLevel.L1_DIRECT)
        ),
        available=(ExecutionLevel.L1_DIRECT, ExecutionLevel.L4_PLANNED),
    )
    task = harness.make_task("adaptive strategy still requires write authority")
    request = harness.make_request(
        task=task,
        context=harness.make_context(task),
        routing_evidence=RoutingEvidence(deterministic_direct_path=True),
        limits=default_limits(max_total_attempts=1, escalation_permitted=False),
    )

    from agentx.agent_loop import AgentLoop, StrategyRegistry

    outcome = AgentLoop(
        task_manager=harness.task_manager,
        strategies=StrategyRegistry(
            {
                ExecutionLevel.L1_DIRECT: strategy,
                ExecutionLevel.L4_PLANNED: strategy,
            }
        ),
        router=router,
    ).run(request).unwrap()

    assert outcome.initial_level is ExecutionLevel.L4_PLANNED
    assert outcome.verified is False
    assert harness.authority is not None
    assert harness.authority.permissions == frozenset({Permission.READ})
    assert harness.capability.read("alpha") is None


def test_adaptive_router_cannot_select_cheaper_level_than_canonical_evidence() -> None:
    router = AdaptiveExecutionLevelRouter(
        context=_context(),
        selector=_MaliciousCheaperSelector(),
        available=(
            ExecutionLevel.L0_CACHE,
            ExecutionLevel.L1_DIRECT,
            ExecutionLevel.L4_PLANNED,
        ),
    )

    decision = router.route(RoutingEvidence(deterministic_direct_path=True))

    # A2.07 establishes L1 as the cheapest justified strategy. Hostile adaptive
    # output requesting L0 is rejected and canonical L1 remains authoritative.
    assert decision.level is ExecutionLevel.L1_DIRECT


def test_m14_sources_have_no_kernel_import_or_dynamic_execution_escape_hatch() -> None:
    root = Path(__file__).resolve().parents[2]
    source_paths = (
        root / "src" / "agentx" / "adaptive_optimization.py",
        root / "src" / "agentx" / "specialized_models.py",
    )
    forbidden_calls = {"eval", "exec", "__import__"}
    forbidden_import_prefixes = (
        "agentx.kernel",
        "pickle",
        "cloudpickle",
        "dill",
        "marshal",
        "subprocess",
    )

    for path in source_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)

        assert not any(
            imported.startswith(prefix)
            for imported in imports
            for prefix in forbidden_import_prefixes
        )
        assert calls.isdisjoint(forbidden_calls)
