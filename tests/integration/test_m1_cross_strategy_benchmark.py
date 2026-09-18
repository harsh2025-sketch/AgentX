"""AX-084 cross-strategy production-path benchmark.

This is one benchmark, not a list of isolated strategy unit tests. It executes
representative L0-L5 tasks through the canonical AgentLoop and production
strategy adapters. Controlled local providers are used only where the task does
not require real external-provider acceptance; AX-083/085 remain separately
bound to their real-world/live dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentx.agent_loop import AgentLoop, OrchestrationStatus, StrategyRegistry
from agentx.capabilities.filesystem import (
    FilesystemReadTextCapability,
    FilesystemWriteTextCapability,
    read_text_request,
)
from agentx.capabilities.verifier import VerificationRequirement
from agentx.capability_strategy import CapabilityStrategyBinding, GovernedCapabilityStrategy
from agentx.cognition.gap_detector import KnowledgeGapRequirement
from agentx.cognition.model_provider import (
    ModelCapability,
    ModelDescriptor,
    ModelId,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderDescriptor,
    ProviderId,
    TextContent,
)
from agentx.cognition.model_roles import ModelRole, ModelRoleBinding, ModelRoleBindings
from agentx.cognition.reasoner import Reasoner
from agentx.cognition.research_objective import ResearchObjective
from agentx.cognition.research_provider import (
    ResearchProviderAvailability,
    ResearchProviderIdentity,
    ResearchRequest,
    ResearchResponse,
)
from agentx.cognition.router import ExecutionLevel, RoutingEvidence
from agentx.cognition.task_manager import TaskManager
from agentx.compiled_procedure_strategy import GovernedCompiledProcedureStrategy
from agentx.core.errors import AgentXError
from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import KnowledgeScope, KnowledgeStatus, ScopeDimension
from agentx.core.result import Result
from agentx.exploratory_strategy import ExploratoryStrategyBinding, GovernedExploratoryStrategy
from agentx.governed_research import (
    GovernedResearchAcquisitionCapability,
    ResearchAcquisitionMode,
    governed_research_request,
)
from agentx.hive_first_research import HiveFirstResearchLookup
from agentx.infrastructure.knowledge_retrieval import KnowledgeRetrieval, KnowledgeRetrievalQuery
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.kernel.permissions import Permission
from agentx.plan_execution import BoundPlanAction, GovernedPlanExecutor, GovernedPlanningStrategy
from agentx.planning_strategy import PlanningStrategy
from tests.integration.test_cache_strategy import _request as cache_request
from tests.integration.test_guided_procedure_strategy import (
    _strategy as guided_strategy,
)
from tests.integration.test_plan_execution import FileBinder, plan_for
from tests.integration.test_plan_procedure_composition import _compiled_strategy
from tests.support.demo_capability import NoteWriteParams, write_request
from tests.support.orchestration_harness import (
    FixedClock,
    OrchestrationHarness,
    default_limits,
    make_envelope,
)

_SCOPE = KnowledgeScope({ScopeDimension.PROJECT: "AgentX"})


class _PlanProvider:
    def __init__(self, task, plan) -> None:
        provider_id = ProviderId("m1-cross-strategy")
        capabilities = frozenset(
            {ModelCapability("content.text.input"), ModelCapability("content.text.output")}
        )
        model = ModelDescriptor(
            model_id=ModelId(provider_id, "controlled-local"),
            capabilities=capabilities,
        )
        self.descriptor = ProviderDescriptor(
            provider_id=provider_id,
            capabilities=capabilities,
            models=(model,),
        )
        self.calls = 0
        self.output = json.dumps(
            {
                "schema_version": 1,
                "root_task_id": task.task_id.to_str(),
                "nodes": [node.to_dict() for node in plan.nodes],
            }
        )

    def invoke(self, request: ModelRequest) -> Result[ModelResponse, AgentXError]:
        self.calls += 1
        return Result.success(
            ModelResponse(
                model_id=request.model_id,
                content=(TextContent(self.output),),
                usage=ModelUsage(),
            )
        )


class _ResearchPort:
    def __init__(self) -> None:
        self.calls = 0

    def acquire(self, request: ResearchRequest) -> ResearchResponse:
        self.calls += 1
        return ResearchResponse(
            request_id=request.request_id,
            research_provider_id=ResearchProviderIdentity(
                research_provider_id="m1-cross-strategy-local",
                kind="fixture",
                name="M1 cross-strategy benchmark",
            ),
            availability=ResearchProviderAvailability.AVAILABLE,
            evidence=(),
        )


def _assert_level(outcome, level: ExecutionLevel) -> None:
    assert outcome.status is OrchestrationStatus.SUCCEEDED
    assert outcome.verified is True
    assert outcome.final_level is level
    assert outcome.attempts[-1].verified is True


def test_ax084_cross_strategy_production_benchmark(tmp_path: Path) -> None:
    observed: set[ExecutionLevel] = set()

    # L0 — verified cache reuse through AgentLoop.
    cache_manager = TaskManager()
    cache_run, cache_strategy, _lookup = cache_request(
        cache_manager,
        expected_fingerprint="sha256:abc123",
    )
    cache_outcome = AgentLoop(
        task_manager=cache_manager,
        strategies=StrategyRegistry({ExecutionLevel.L0_CACHE: cache_strategy}),
    ).run(cache_run).unwrap()
    _assert_level(cache_outcome, ExecutionLevel.L0_CACHE)
    observed.add(cache_outcome.final_level)

    # L1 — deterministic typed capability through Executor/ActionGate.
    direct = OrchestrationHarness()
    direct_strategy = GovernedCapabilityStrategy(
        executor=direct.executor,
        binding=CapabilityStrategyBinding(
            request=write_request(NoteWriteParams(key="alpha", value="v1"))
        ),
    )
    direct_outcome = direct.agent_loop({ExecutionLevel.L1_DIRECT: direct_strategy}).run(
        direct.make_request(
            routing_evidence=RoutingEvidence(deterministic_direct_path=True),
            requirement=VerificationRequirement({"stored": True, "key": "alpha"}),
            limits=default_limits(max_total_attempts=1, escalation_permitted=False),
        )
    ).unwrap()
    _assert_level(direct_outcome, ExecutionLevel.L1_DIRECT)
    observed.add(direct_outcome.final_level)

    # L2 — ACTIVE compiled procedure through the canonical procedure runtime.
    compiled = OrchestrationHarness()
    compiled_outcome = compiled.agent_loop(
        {ExecutionLevel.L2_COMPILED: _compiled_strategy(compiled)}
    ).run(
        compiled.make_request(
            routing_evidence=RoutingEvidence(verified_reasoning_free_procedure=True),
            requirement=VerificationRequirement({"stored": True, "key": "alpha"}),
            limits=default_limits(max_total_attempts=1, escalation_permitted=False),
        )
    ).unwrap()
    _assert_level(compiled_outcome, ExecutionLevel.L2_COMPILED)
    observed.add(compiled_outcome.final_level)

    # L3 — guided procedure with one bounded controlled reasoning gap.
    guided = OrchestrationHarness()
    guided_adapter = guided_strategy(guided, provider=None, guided=False)
    guided_outcome = guided.agent_loop({ExecutionLevel.L3_GUIDED: guided_adapter}).run(
        guided.make_request(
            routing_evidence=RoutingEvidence(procedure_with_reasoning_gaps=True),
            requirement=VerificationRequirement({"stored": True, "key": "alpha"}),
            limits=default_limits(max_total_attempts=1, escalation_permitted=False),
        )
    ).unwrap()
    _assert_level(guided_outcome, ExecutionLevel.L3_GUIDED)
    observed.add(guided_outcome.final_level)

    # L4 — controlled model decomposition, typed binding, real filesystem,
    # shared governed budget, and independent final readback.
    planned_path = tmp_path / "planned.txt"
    planned = OrchestrationHarness(
        authority=frozenset({Permission.READ, Permission.WRITE}),
        envelope=make_envelope(max_machine_actions=12),
        register_capability=False,
    )
    planned.registry.register(FilesystemReadTextCapability())
    planned.registry.register(FilesystemWriteTextCapability())
    planned_task = planned.make_task("prepare a final document")
    plan = plan_for(planned_task)
    binder = FileBinder(planned_path)
    plan_executor = GovernedPlanExecutor(
        executor=planned.executor,
        binder=binder,
        goal_check=BoundPlanAction(
            request=read_text_request(str(planned_path)),
            requirement=VerificationRequirement({"text": "finished"}),
        ),
        max_actions=3,
    )
    plan_provider = _PlanProvider(planned_task, plan)
    clock = FixedClock()
    planning = GovernedPlanningStrategy(
        planner=PlanningStrategy(
            reasoner=Reasoner(
                bindings=ModelRoleBindings(
                    bindings=(
                        ModelRoleBinding(
                            ModelRole.REASONING,
                            plan_provider.descriptor.models[0],
                        ),
                    )
                ),
                provider=plan_provider,
                clock=clock,
            ),
            clock=clock,
        ),
        executor=plan_executor,
    )
    planned_outcome = planned.agent_loop({ExecutionLevel.L4_PLANNED: planning}).run(
        planned.make_request(
            task=planned_task,
            context=planned.make_context(planned_task),
            routing_evidence=RoutingEvidence(known_composition_required=True),
            requirement=VerificationRequirement({"text": "finished"}),
            limits=default_limits(max_total_attempts=1, escalation_permitted=False),
        )
    ).unwrap()
    _assert_level(planned_outcome, ExecutionLevel.L4_PLANNED)
    assert plan_provider.calls == 1
    assert planned_path.read_text(encoding="utf-8") == "finished"
    observed.add(planned_outcome.final_level)

    # L5 — Hive-first miss then one governed local research acquisition. The
    # result remains explicitly unverified research data.
    research_port = _ResearchPort()
    research_capability = GovernedResearchAcquisitionCapability(
        port=research_port,
        mode=ResearchAcquisitionMode.LOCAL,
    )
    exploratory = OrchestrationHarness(
        authority=frozenset({Permission.READ}),
        envelope=make_envelope(max_research_queries=1, max_machine_actions=4),
        register_capability=False,
    )
    exploratory.registry.register(research_capability)
    store = KnowledgeStore(SQLiteDatabase(tmp_path / "m1-cross-strategy.sqlite3"))
    requirement = KnowledgeGapRequirement(
        requirement_id="missing-fact",
        acceptable_knowledge_ids=frozenset({KnowledgeId.create()}),
        acceptable_statuses=frozenset({KnowledgeStatus.VERIFIED}),
        scope=_SCOPE,
    )
    research_request = ResearchRequest(
        request_id="m1-cross-strategy-research",
        objective=ResearchObjective(
            objective_id="m1-cross-strategy-objective",
            question="Find evidence for the explicit missing requirement.",
            unmet_requirement_ids=("missing-fact",),
            scope=_SCOPE,
            acceptable_statuses=frozenset({KnowledgeStatus.VERIFIED}),
        ),
    )
    exploratory_strategy = GovernedExploratoryStrategy(
        lookup=HiveFirstResearchLookup(KnowledgeRetrieval(store)),
        executor=exploratory.executor,
        budget=exploratory.budget,
        binding=ExploratoryStrategyBinding(
            requirements=(requirement,),
            query=KnowledgeRetrievalQuery(scope=_SCOPE),
            acquisition_request=governed_research_request(
                research_request,
                mode=ResearchAcquisitionMode.LOCAL,
            ),
        ),
    )
    exploratory_task = exploratory.make_task("research one explicit knowledge gap")
    exploratory_outcome = exploratory.agent_loop(
        {ExecutionLevel.L5_EXPLORATORY: exploratory_strategy}
    ).run(
        exploratory.make_request(
            task=exploratory_task,
            context=exploratory.make_context(exploratory_task),
            routing_evidence=RoutingEvidence(),
            requirement=VerificationRequirement(
                {"availability": "available", "research_verified": False}
            ),
            limits=default_limits(max_total_attempts=1, escalation_permitted=False),
        )
    ).unwrap()
    _assert_level(exploratory_outcome, ExecutionLevel.L5_EXPLORATORY)
    assert research_port.calls == 1
    assert exploratory.budget.snapshot().research_queries == 1
    observed.add(exploratory_outcome.final_level)

    assert observed == set(ExecutionLevel)
