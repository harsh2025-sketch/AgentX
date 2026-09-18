"""AX-175: cold verified experience -> compiled ACTIVE skill -> process-restart L2 reuse."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from agentx.agent_loop import (
    AgentLoop,
    OrchestrationLimits,
    OrchestrationRequest,
    OrchestrationStatus,
    StrategyRegistry,
)
from agentx.active_procedure_reuse import ActiveProcedureReuse
from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.executor import Executor
from agentx.capabilities.filesystem import (
    FILESYSTEM_READ_TEXT_IDENTITY,
    FilesystemReadTextCapability,
    ReadTextParams,
    read_text_request,
)
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, LoopOutcome
from agentx.capabilities.verifier import VerificationRequirement
from agentx.capability_strategy import CapabilityStrategyBinding, GovernedCapabilityStrategy
from agentx.cognition.anti_loop import LoopGuardLimits
from agentx.cognition.router import ExecutionLevel, RoutingEvidence
from agentx.cognition.task_manager import TaskManager
from agentx.compiled_skill_binding import materialize_compiled_procedure_graph
from agentx.compiled_skill_validation import GovernedCompiledSkillValidationHarness
from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.ids import EpisodeId, ProcedureId, TaskId
from agentx.core.procedure_lifecycle import (
    ProcedureLifecycleReason,
    assess_procedure_transition,
)
from agentx.core.procedure_matching import ProcedureRequirement
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.core.tasks import Task
from agentx.execution_episode import ExecutionEpisodeCapture, ExecutionEpisodeRequest
from agentx.hive.experience_memory import ExperienceMemory
from agentx.infrastructure.active_procedure_reader import ActiveProcedureReader
from agentx.infrastructure.episode_store import EpisodeStore
from agentx.infrastructure.negative_experience_store import NegativeExperienceStore
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel
from agentx.learning.region_classification import RegionClassification
from agentx.procedure_promotion import (
    ProcedurePromotionOutcome,
    ProcedurePromotionRequest,
    promote_procedure_candidate,
)
from agentx.procedure_reuse_selector import ProcedureReuseSelectionOutcome
from agentx.procedure_validation import ValidationDecision, ValidationPolicy
from agentx.procedure_validation_runner import ProcedureValidationCase, ProcedureValidationRunner
from agentx.procedures.graph import ProcedureGraph, ProcedureNodeKind
from agentx.skill_compiler import SkillCompilationOutcome, compile_skill_candidate

pytestmark = pytest.mark.integration

_T0 = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)
_SCOPE = ProcedureScope({ProcedureScopeDimension.ENVIRONMENT: "m3-filesystem"})
_PROCEDURE_ID = ProcedureId(UUID("30000000-0000-4000-8000-000000000175"))


def _envelope(max_actions: int = 20) -> ResourceEnvelope:
    return ResourceEnvelope(
        max_wall_clock=timedelta(seconds=30),
        max_model_calls=0,
        max_model_tokens=0,
        max_research_queries=0,
        max_machine_actions=max_actions,
        max_repair_attempts=0,
        max_external_cost=Decimal("0"),
        max_risk_level=RiskLevel.R2,
    )


def _limits() -> OrchestrationLimits:
    return OrchestrationLimits(
        max_total_attempts=1,
        loop_guard_limits=LoopGuardLimits(
            max_total_attempts=2,
            max_same_attempts=2,
            max_same_outcomes_without_progress=2,
        ),
        escalation_permitted=False,
    )


def _executor(*, authority: AuthorityContext | None, max_actions: int = 20) -> Executor:
    registry = CapabilityRegistry()
    registry.register(FilesystemReadTextCapability())
    return Executor(
        execution_loop=CapabilityExecutionLoop(
            registry=registry,
            action_gate=ActionGate(),
            authority=authority,
            emergency_stop=EmergencyStop(),
            budget=ResourceBudget(_envelope(max_actions)),
            publish_event=lambda _event: None,
            publish_audit=lambda _record: None,
        )
    )


def _read_factory(data: Mapping[str, object]) -> CapabilityRequest[ReadTextParams]:
    path_value = data["path"]
    max_bytes_value = data["max_bytes"]
    assert isinstance(path_value, str)
    assert type(max_bytes_value) is int
    return read_text_request(path_value, max_bytes=max_bytes_value)


def _cold_read(
    target: Path,
    *,
    expected: str,
    correlation_id: UUID,
    episode_id: EpisodeId,
    offset: int,
) -> tuple[Task, ExecutionContext, CausalExperience]:
    executor = _executor(
        authority=AuthorityContext(permissions=frozenset({Permission.READ})),
        max_actions=4,
    )
    strategy = GovernedCapabilityStrategy(
        executor=executor,
        binding=CapabilityStrategyBinding(
            request=read_text_request(str(target), max_bytes=256)
        ),
    )
    manager = TaskManager()
    task = manager.create(f"cold read {target.name}")
    context = ExecutionContext(
        correlation_id=correlation_id,
        cancellation_token=CancellationSource().token,
        task_id=task.task_id,
    )
    result = AgentLoop(
        task_manager=manager,
        strategies=StrategyRegistry({ExecutionLevel.L1_DIRECT: strategy}),
    ).run(
        OrchestrationRequest(
            task=task,
            context=context,
            routing_evidence=RoutingEvidence(deterministic_direct_path=True),
            requirement=VerificationRequirement(
                {"text": expected, "byte_count": len(expected.encode("utf-8"))}
            ),
            limits=_limits(),
        )
    )
    assert result.is_success, result.unwrap_error()
    orchestration = result.unwrap()
    assert orchestration.status is OrchestrationStatus.SUCCEEDED
    assert orchestration.verified is True
    attempt = orchestration.attempts[0]
    assert attempt.outcome is not None
    outcome = attempt.outcome
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.observation is not None
    assert outcome.verification is not None and outcome.verification.passed

    base = _T0 + timedelta(minutes=offset)
    experience = CausalExperience(
        task_id=orchestration.task.task_id,
        correlation_id=context.correlation_id,
        episode_id=episode_id,
        state_before=ExperienceState(
            captured_at=base,
            observation=ObservationPayload(value={"target": str(target)}),
        ),
        action=ActionPayload(
            name="filesystem.read_text@1.0.0",
            data={"path": str(target), "max_bytes": 256},
        ),
        action_at=base + timedelta(seconds=1),
        observation=ObservationPayload(value=outcome.observation.to_dict()),
        observation_at=base + timedelta(seconds=2),
        state_after=ExperienceState(
            captured_at=base + timedelta(seconds=3),
            observation=ObservationPayload(value=outcome.observation.to_dict()),
        ),
        verification=VerificationPayload(
            passed=outcome.verification.passed,
            detail=outcome.verification.detail,
        ),
        verification_at=base + timedelta(seconds=4),
        outcome=CausalOutcome.VERIFIED,
        outcome_at=base + timedelta(seconds=5),
    )
    return orchestration.task, context, experience


def _parameter_name(graph: ProcedureGraph) -> str:
    action = next(node for node in graph.nodes if node.kind is ProcedureNodeKind.ACTION)
    parameters = action.params["parameters"]
    assert isinstance(parameters, Mapping)
    path_parameter = parameters["path"]
    assert isinstance(path_parameter, Mapping)
    name = path_parameter["parameter_name"]
    assert isinstance(name, str)
    return name


_CHILD_REUSE = """
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from agentx.active_procedure_reuse import ActiveProcedureReuse
from agentx.agent_loop import AgentLoop, OrchestrationLimits, OrchestrationRequest, OrchestrationStatus, StrategyRegistry
from agentx.capabilities.executor import Executor
from agentx.capabilities.filesystem import FILESYSTEM_READ_TEXT_IDENTITY, FilesystemReadTextCapability, read_text_request
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop
from agentx.capabilities.verifier import VerificationRequirement
from agentx.cognition.anti_loop import LoopGuardLimits
from agentx.cognition.router import ExecutionLevel, RoutingEvidence
from agentx.cognition.task_manager import TaskManager
from agentx.compiled_procedure_strategy import CompiledProcedureStrategyBinding, GovernedCompiledProcedureStrategy
from agentx.compiled_skill_binding import build_compiled_action_requests
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.procedure_matching import ProcedureRequirement
from agentx.core.procedures import ProcedureScope, ProcedureScopeDimension, ProcedureStatus
from agentx.infrastructure.active_procedure_reader import ActiveProcedureReader
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel
from agentx.procedure_reuse_selector import ProcedureReuseSelectionOutcome
from agentx.procedures.graph import ProcedureGraph


def main() -> int:
    db_path = Path(sys.argv[1])
    target = Path(sys.argv[2])
    parameter_name = sys.argv[3]
    expected = sys.argv[4]
    scope = ProcedureScope({ProcedureScopeDimension.ENVIRONMENT: "m3-filesystem"})
    requirement = ProcedureRequirement(scope=scope)

    store = ProcedureStore(SQLiteDatabase(db_path))
    selected = ActiveProcedureReuse(ActiveProcedureReader(store), {}).select(requirement)
    assert selected.outcome is ProcedureReuseSelectionOutcome.SELECTED
    assert selected.selected is not None
    assert selected.selected.record.status is ProcedureStatus.ACTIVE

    graph = ProcedureGraph.from_json(selected.selected.record.payload.content)

    def factory(data):
        return read_text_request(str(data["path"]), max_bytes=int(data["max_bytes"]))

    action_requests = build_compiled_action_requests(
        graph,
        {parameter_name: str(target)},
        {FILESYSTEM_READ_TEXT_IDENTITY: factory},
    )

    registry = CapabilityRegistry()
    registry.register(FilesystemReadTextCapability())
    executor = Executor(
        execution_loop=CapabilityExecutionLoop(
            registry=registry,
            action_gate=ActionGate(),
            authority=AuthorityContext(permissions=frozenset({Permission.READ})),
            emergency_stop=EmergencyStop(),
            budget=ResourceBudget(
                ResourceEnvelope(
                    max_wall_clock=timedelta(seconds=30),
                    max_model_calls=0,
                    max_model_tokens=0,
                    max_research_queries=0,
                    max_machine_actions=5,
                    max_repair_attempts=0,
                    max_external_cost=Decimal("0"),
                    max_risk_level=RiskLevel.R2,
                )
            ),
            publish_event=lambda _event: None,
            publish_audit=lambda _record: None,
        )
    )
    strategy = GovernedCompiledProcedureStrategy(
        executor=executor,
        binding=CompiledProcedureStrategyBinding(
            candidate=selected.selected,
            requirement=requirement,
            action_requests=action_requests,
            run_id=UUID("30000000-0000-4000-8000-000000000999"),
            recorded_at=datetime(2026, 9, 18, 5, 0, tzinfo=UTC),
        ),
    )
    manager = TaskManager()
    task = manager.create("warm related read after restart")
    context = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=task.task_id,
    )
    result = AgentLoop(
        task_manager=manager,
        strategies=StrategyRegistry({ExecutionLevel.L2_COMPILED: strategy}),
    ).run(
        OrchestrationRequest(
            task=task,
            context=context,
            routing_evidence=RoutingEvidence(verified_reasoning_free_procedure=True),
            requirement=VerificationRequirement(
                {"text": expected, "byte_count": len(expected.encode("utf-8"))}
            ),
            limits=OrchestrationLimits(
                max_total_attempts=1,
                loop_guard_limits=LoopGuardLimits(
                    max_total_attempts=2,
                    max_same_attempts=2,
                    max_same_outcomes_without_progress=2,
                ),
                escalation_permitted=False,
            ),
        )
    )
    assert result.is_success, result.unwrap_error()
    outcome = result.unwrap()
    assert outcome.status is OrchestrationStatus.SUCCEEDED
    assert outcome.verified is True
    assert outcome.attempts[0].level is ExecutionLevel.L2_COMPILED
    print(
        "M3_REUSE_VERIFIED|"
        + selected.selected.record.procedure_id.to_str()
        + "|"
        + str(selected.selected.record.revision)
        + "|"
        + target.read_text(encoding="utf-8")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


def test_ax175_cold_compile_validate_promote_restart_and_l2_reuse(tmp_path: Path) -> None:
    train_a = tmp_path / "train-a.txt"
    train_b = tmp_path / "train-b.txt"
    validate_c = tmp_path / "validate-c.txt"
    validate_d = tmp_path / "validate-d.txt"
    warm_e = tmp_path / "warm-e.txt"
    train_a.write_text("alpha", encoding="utf-8")
    train_b.write_text("beta", encoding="utf-8")
    validate_c.write_text("gamma", encoding="utf-8")
    validate_d.write_text("delta", encoding="utf-8")
    warm_e.write_text("epsilon", encoding="utf-8")

    task_a, context_a, experience_a = _cold_read(
        train_a,
        expected="alpha",
        correlation_id=UUID("31000000-0000-4000-8000-000000000001"),
        episode_id=EpisodeId(UUID("32000000-0000-4000-8000-000000000001")),
        offset=0,
    )
    _task_b, _context_b, experience_b = _cold_read(
        train_b,
        expected="beta",
        correlation_id=UUID("31000000-0000-4000-8000-000000000002"),
        episode_id=EpisodeId(UUID("32000000-0000-4000-8000-000000000002")),
        offset=1,
    )

    database_path = tmp_path / "m3-acceptance.sqlite3"
    database = SQLiteDatabase(database_path)
    episode_store = EpisodeStore(database)
    memory = ExperienceMemory(
        episode_store=episode_store,
        negative_experience_store=NegativeExperienceStore(database),
    )
    recorded = ExecutionEpisodeCapture(memory=memory).record(
        ExecutionEpisodeRequest(
            task=task_a,
            context=context_a,
            experience=experience_a,
            episode_id=experience_a.episode_id,
            recorded_at=experience_a.outcome_at + timedelta(seconds=1),
        )
    )
    assert recorded.episode.outcome.value == "succeeded"

    compilation = compile_skill_candidate(
        (experience_a,),
        corroborating=((experience_b,),),
        procedure_id=_PROCEDURE_ID,
        revision=1,
    )
    assert compilation.outcome is SkillCompilationOutcome.CANDIDATE
    assert compilation.status is ProcedureStatus.CANDIDATE
    assert compilation.graph is not None
    assert {item.classification for item in compilation.regions.actions} == {
        RegionClassification.DETERMINISTIC
    }
    compiler_graph = compilation.graph
    parameter_name = _parameter_name(compiler_graph)

    runtime_graph = materialize_compiled_procedure_graph(
        compiler_graph,
        {"filesystem.read_text@1.0.0": FILESYSTEM_READ_TEXT_IDENTITY},
    )
    candidate = ProcedureRecord.create(
        procedure_id=_PROCEDURE_ID,
        revision=1,
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content=runtime_graph.to_json(),
        ),
        scope=_SCOPE,
        created_at=_T0 + timedelta(minutes=5),
    )
    procedure_store = ProcedureStore(database)
    procedure_store.insert(candidate)
    assert procedure_store.get(_PROCEDURE_ID, 1) == candidate
    assert candidate.status is ProcedureStatus.CANDIDATE

    validation_harness = GovernedCompiledSkillValidationHarness(
        candidate=candidate,
        executor=_executor(
            authority=AuthorityContext(permissions=frozenset({Permission.READ})),
            max_actions=8,
        ),
        request_factories={FILESYSTEM_READ_TEXT_IDENTITY: _read_factory},
    )
    validation = ProcedureValidationRunner(
        harness=validation_harness,
        policy=ValidationPolicy(
            min_verified_successes=2,
            min_distinct_parameter_bindings=2,
            allowed_environments=frozenset({"m3-filesystem"}),
        ),
    ).run(
        candidate,
        (
            ProcedureValidationCase(
                case_id="variant-gamma",
                run_id=TaskId(UUID("33000000-0000-4000-8000-000000000001")),
                parameter_binding={parameter_name: str(validate_c)},
                environment="m3-filesystem",
                verification=VerificationRequirement({"text": "gamma", "byte_count": 5}),
            ),
            ProcedureValidationCase(
                case_id="variant-delta",
                run_id=TaskId(UUID("33000000-0000-4000-8000-000000000002")),
                parameter_binding={parameter_name: str(validate_d)},
                environment="m3-filesystem",
                verification=VerificationRequirement({"text": "delta", "byte_count": 5}),
            ),
        ),
    )
    assert validation.complete is True
    assert validation.all_cases_passed is True
    assert validation.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION
    assert procedure_store.get(_PROCEDURE_ID, 1).status is ProcedureStatus.CANDIDATE

    promoted_at = _T0 + timedelta(minutes=10)
    lifecycle = assess_procedure_transition(
        procedure_id=_PROCEDURE_ID,
        revision=1,
        current_status=ProcedureStatus.CANDIDATE,
        target_status=ProcedureStatus.ACTIVE,
        reason=ProcedureLifecycleReason.VALIDATION_PROMOTION,
        requested_at=promoted_at,
    )
    promotion = promote_procedure_candidate(
        procedure_store,
        ProcedurePromotionRequest(
            procedure_id=_PROCEDURE_ID,
            revision=1,
            expected_current=candidate,
            validation_report=validation.report,
            lifecycle_assessment=lifecycle,
            requested_at=promoted_at,
        ),
    )
    assert promotion.outcome is ProcedurePromotionOutcome.PROMOTED
    assert procedure_store.get(_PROCEDURE_ID, 1).status is ProcedureStatus.ACTIVE

    selection = ActiveProcedureReuse(ActiveProcedureReader(procedure_store), {}).select(
        ProcedureRequirement(scope=_SCOPE)
    )
    assert selection.outcome is ProcedureReuseSelectionOutcome.SELECTED

    del memory, episode_store, procedure_store, database

    workdir = tmp_path / "fresh-runtime"
    workdir.mkdir()
    child = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _CHILD_REUSE,
            str(database_path),
            str(warm_e),
            parameter_name,
            "epsilon",
        ],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == f"M3_REUSE_VERIFIED|{_PROCEDURE_ID}|1|epsilon"
