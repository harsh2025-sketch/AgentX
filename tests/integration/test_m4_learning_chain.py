"""M4 deterministic closure: cold L4 -> compile -> promote -> restart -> warm L2.

This closes the locally provable engineering chain AX-197 and AX-199..203.
It intentionally does not claim AX-198/204/205: those require a genuine
configured model-backed efficiency run rather than this controlled provider.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from agentx.agent_loop import OrchestrationStatus
from agentx.capabilities.filesystem import (
    FilesystemReadTextCapability,
    read_text_request,
)
from agentx.capabilities.verifier import VerificationRequirement
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
from agentx.cognition.router import ExecutionLevel, RoutingEvidence
from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.core.ids import EpisodeId
from agentx.core.procedure_lifecycle import (
    ProcedureLifecycleReason,
    assess_procedure_transition,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
)
from agentx.core.result import Result
from agentx.core.task_decomposition import DecompositionNode, TaskDecomposition
from agentx.execution_metrics import (
    ExecutionEvidenceOutcome,
    ExecutionMetricsRecorder,
)
from agentx.instrumented_model_provider import InstrumentedModelProvider
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.kernel.permissions import Permission
from agentx.plan_execution import BoundPlanAction, GovernedPlanExecutor, GovernedPlanningStrategy
from agentx.planning_strategy import PlanningStrategy
from agentx.procedure_promotion import (
    ProcedurePromotionOutcome,
    ProcedurePromotionRequest,
    promote_procedure_candidate,
)
from agentx.procedure_validation import ValidationDecision, ValidationPolicy
from agentx.procedure_validation_runner import ProcedureValidationCase, ProcedureValidationRunner
from agentx.compiled_skill_binding import materialize_compiled_procedure_graph
from agentx.compiled_skill_validation import GovernedCompiledSkillValidationHarness
from agentx.learning.region_classification import RegionClassification
from agentx.skill_compiler import SkillCompilationOutcome, compile_skill_candidate
from tests.integration.test_m3_skill_compilation_acceptance import (
    _CHILD_REUSE,
    _PROCEDURE_ID,
    _SCOPE,
    _executor,
    _parameter_name,
    _read_factory,
)
from tests.support.orchestration_harness import (
    FixedClock,
    OrchestrationHarness,
    default_limits,
    make_envelope,
)

_T0 = datetime(2026, 9, 18, 11, 0, tzinfo=UTC)


class _ReadBinder:
    def __init__(self, target: Path, expected: str) -> None:
        self.target = target
        self.expected = expected
        self.calls: list[str] = []

    def bind(self, node: DecompositionNode) -> Result[BoundPlanAction, object]:
        self.calls.append(node.objective)
        if node.objective != "read-input":
            from agentx.core.errors import AgentXError, ErrorCategory, Retryability

            return Result.failure(
                AgentXError(
                    code="m4.unsupported_plan_node",
                    message="controlled M4 binder rejects unknown plan nodes",
                    category=ErrorCategory.VALIDATION,
                    retryability=Retryability.NON_RETRYABLE,
                )
            )
        return Result.success(
            BoundPlanAction(
                request=read_text_request(str(self.target), max_bytes=256),
                requirement=VerificationRequirement(
                    {
                        "text": self.expected,
                        "byte_count": len(self.expected.encode("utf-8")),
                    }
                ),
            )
        )


class _ColdPlanProvider:
    def __init__(self, plan: TaskDecomposition) -> None:
        provider_id = ProviderId("m4-controlled-cold")
        capabilities = frozenset(
            {
                ModelCapability("content.text.input"),
                ModelCapability("content.text.output"),
            }
        )
        model = ModelDescriptor(
            model_id=ModelId(provider_id, "controlled-v1"),
            capabilities=capabilities,
        )
        self.descriptor = ProviderDescriptor(
            provider_id=provider_id,
            capabilities=capabilities,
            models=(model,),
        )
        self._output = json.dumps(
            {
                "schema_version": 1,
                "root_task_id": plan.root_task_id.to_str(),
                "nodes": [node.to_dict() for node in plan.nodes],
            }
        )
        self.calls = 0

    def invoke(self, request: ModelRequest):
        self.calls += 1
        return Result.success(
            ModelResponse(
                model_id=request.model_id,
                content=(TextContent(self._output),),
                usage=ModelUsage(
                    input_tokens=7,
                    output_tokens=11,
                    total_tokens=18,
                ),
            )
        )


def _plan(task_id, objective: str) -> TaskDecomposition:
    from agentx.core.ids import TaskId

    child = TaskId.create()
    return TaskDecomposition.create(
        root_task_id=task_id,
        nodes=(
            DecompositionNode(task_id=task_id, objective=objective),
            DecompositionNode(
                task_id=child,
                objective="read-input",
                parent_task_id=task_id,
                order_index=0,
                success_criteria=("file content independently verified",),
                metadata={"execution": {"kind": "higher_level"}},
            ),
        ),
    )


def _cold_l4_read(
    target: Path,
    *,
    expected: str,
    correlation_id: UUID,
    episode_id: EpisodeId,
    offset: int,
) -> tuple[CausalExperience, object]:
    harness = OrchestrationHarness(
        authority=frozenset({Permission.READ}),
        envelope=make_envelope(
            max_model_calls=4,
            max_model_tokens=1024,
            max_machine_actions=8,
        ),
        register_capability=False,
    )
    harness.registry.register(FilesystemReadTextCapability())
    task = harness.make_task(
        f"Unknown cold task: inspect {target.name} and establish its exact content."
    )
    context = harness.make_context(task)
    plan = _plan(task.task_id, task.objective)
    binder = _ReadBinder(target, expected)
    provider = _ColdPlanProvider(plan)
    metrics = ExecutionMetricsRecorder(
        task_id=task.task_id,
        correlation_id=correlation_id,
        execution_level=ExecutionLevel.L4_PLANNED,
    )
    metrics.start(started_at=_T0 + timedelta(minutes=offset))
    clock = FixedClock()
    planner = PlanningStrategy(
        reasoner=Reasoner(
            bindings=ModelRoleBindings(
                bindings=(
                    ModelRoleBinding(
                        ModelRole.REASONING,
                        provider.descriptor.models[0],
                    ),
                )
            ),
            provider=InstrumentedModelProvider(provider=provider, recorder=metrics),
            clock=clock,
        ),
        clock=clock,
    )
    strategy = GovernedPlanningStrategy(
        planner=planner,
        executor=GovernedPlanExecutor(
            executor=harness.executor,
            binder=binder,
            goal_check=BoundPlanAction(
                request=read_text_request(str(target), max_bytes=256),
                requirement=VerificationRequirement(
                    {
                        "text": expected,
                        "byte_count": len(expected.encode("utf-8")),
                    }
                ),
            ),
            max_actions=2,
        ),
    )
    result = harness.agent_loop({ExecutionLevel.L4_PLANNED: strategy}).run(
        harness.make_request(
            task=task,
            context=context,
            routing_evidence=RoutingEvidence(known_composition_required=True),
            requirement=VerificationRequirement(
                {
                    "text": expected,
                    "byte_count": len(expected.encode("utf-8")),
                }
            ),
            limits=default_limits(
                max_total_attempts=1,
                escalation_permitted=False,
            ),
        )
    ).unwrap()

    assert result.status is OrchestrationStatus.SUCCEEDED
    assert result.final_level is ExecutionLevel.L4_PLANNED
    assert result.verified is True
    assert provider.calls == 1
    assert binder.calls == ["read-input"]
    attempt = result.attempts[0]
    assert attempt.outcome is not None
    assert attempt.outcome.observation is not None
    assert attempt.outcome.verification is not None
    assert attempt.outcome.verification.passed

    metrics.record_machine_action(count=harness.budget.snapshot().machine_actions)
    metrics.set_outcome(
        ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
        verification=VerificationPayload(
            passed=True,
            detail=attempt.outcome.verification.detail,
        ),
        verification_source="m4.cold.l4.independent_readback",
        verification_reference=correlation_id,
    )
    record = metrics.finish(
        ended_at=_T0 + timedelta(minutes=offset, seconds=1)
    )
    assert record.model_calls == 1
    assert record.model_tokens == 18
    assert record.machine_actions == harness.budget.snapshot().machine_actions

    base = _T0 + timedelta(minutes=offset)
    observation = ObservationPayload(value=attempt.outcome.observation.to_dict())
    experience = CausalExperience(
        task_id=task.task_id,
        correlation_id=correlation_id,
        episode_id=episode_id,
        state_before=ExperienceState(
            captured_at=base,
            observation=ObservationPayload(
                value={"target": str(target), "cold_level": "L4_PLANNED"}
            ),
        ),
        action=ActionPayload(
            name="filesystem.read_text@1.0.0",
            data={"path": str(target), "max_bytes": 256},
        ),
        action_at=base + timedelta(milliseconds=100),
        observation=observation,
        observation_at=base + timedelta(milliseconds=200),
        state_after=ExperienceState(
            captured_at=base + timedelta(milliseconds=300),
            observation=observation,
        ),
        verification=VerificationPayload(
            passed=True,
            detail=attempt.outcome.verification.detail,
        ),
        verification_at=base + timedelta(milliseconds=400),
        outcome=CausalOutcome.VERIFIED,
        outcome_at=base + timedelta(milliseconds=500),
    )
    return experience, record


def test_m4_cold_l4_compile_validate_promote_restart_and_warm_l2(tmp_path: Path) -> None:
    train_a = tmp_path / "cold-a.txt"
    train_b = tmp_path / "cold-b.txt"
    validate_c = tmp_path / "variant-c.txt"
    validate_d = tmp_path / "variant-d.txt"
    warm_e = tmp_path / "warm-related-e.txt"
    train_a.write_text("alpha", encoding="utf-8")
    train_b.write_text("beta", encoding="utf-8")
    validate_c.write_text("gamma", encoding="utf-8")
    validate_d.write_text("delta", encoding="utf-8")
    warm_e.write_text("epsilon", encoding="utf-8")

    experience_a, metrics_a = _cold_l4_read(
        train_a,
        expected="alpha",
        correlation_id=UUID("41000000-0000-4000-8000-000000000001"),
        episode_id=EpisodeId(UUID("42000000-0000-4000-8000-000000000001")),
        offset=0,
    )
    experience_b, metrics_b = _cold_l4_read(
        train_b,
        expected="beta",
        correlation_id=UUID("41000000-0000-4000-8000-000000000002"),
        episode_id=EpisodeId(UUID("42000000-0000-4000-8000-000000000002")),
        offset=2,
    )
    assert metrics_a.model_calls == metrics_b.model_calls == 1

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
    parameter_name = _parameter_name(compilation.graph)
    runtime_graph = materialize_compiled_procedure_graph(
        compilation.graph,
        {
            "filesystem.read_text@1.0.0": (
                FilesystemReadTextCapability().descriptor.identity
            )
        },
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

    database_path = tmp_path / "m4-learning.sqlite3"
    store = ProcedureStore(SQLiteDatabase(database_path))
    store.insert(candidate)

    validation = ProcedureValidationRunner(
        harness=GovernedCompiledSkillValidationHarness(
            candidate=candidate,
            executor=_executor(
                authority=__import__(
                    "agentx.kernel.permissions",
                    fromlist=["AuthorityContext"],
                ).AuthorityContext(
                    permissions=frozenset({Permission.READ})
                ),
                max_actions=8,
            ),
            request_factories={
                FilesystemReadTextCapability().descriptor.identity: _read_factory
            },
        ),
        policy=ValidationPolicy(
            min_verified_successes=2,
            min_distinct_parameter_bindings=2,
            allowed_environments=frozenset({"m3-filesystem"}),
        ),
    ).run(
        candidate,
        (
            ProcedureValidationCase(
                case_id="m4-variant-gamma",
                run_id=__import__("agentx.core.ids", fromlist=["TaskId"]).TaskId(
                    UUID("43000000-0000-4000-8000-000000000001")
                ),
                parameter_binding={parameter_name: str(validate_c)},
                environment="m3-filesystem",
                verification=VerificationRequirement(
                    {"text": "gamma", "byte_count": 5}
                ),
            ),
            ProcedureValidationCase(
                case_id="m4-variant-delta",
                run_id=__import__("agentx.core.ids", fromlist=["TaskId"]).TaskId(
                    UUID("43000000-0000-4000-8000-000000000002")
                ),
                parameter_binding={parameter_name: str(validate_d)},
                environment="m3-filesystem",
                verification=VerificationRequirement(
                    {"text": "delta", "byte_count": 5}
                ),
            ),
        ),
    )
    assert validation.complete is True
    assert validation.all_cases_passed is True
    assert validation.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION

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
        store,
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
    assert store.get(_PROCEDURE_ID, 1).status is ProcedureStatus.ACTIVE

    # Fresh interpreter/process proves restart-safe durable ACTIVE reuse.
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
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == f"M3_REUSE_VERIFIED|{_PROCEDURE_ID}|1|epsilon"
