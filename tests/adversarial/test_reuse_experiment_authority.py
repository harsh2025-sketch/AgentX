"""Adversarial tests for the N2.12 reuse-experiment harness authority boundary.

These tests attack the harness with hostile metadata, fabricated improvement
claims, tampered serialization, and authority-shaped payloads. The harness may
record and compare canonical evidence, but a result — even one whose verdict
says the warm phase improved — must never become routing policy, Procedure
promotion, Permission/Risk/budget/stop authority, Action Gate influence, Task
success, or persistence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from agentx.core.episodes import EpisodeOutcome, EpisodeRecord
from agentx.core.events import VerificationPayload
from agentx.core.ids import EpisodeId, ProcedureId, TaskId
from agentx.core.reuse_efficiency import (
    EfficiencyMetric,
    ExecutionEfficiencyEvidence,
    ExecutionEvidenceOutcome,
    MetricChange,
    ProcedureRevisionRef,
    ReuseEfficiencyDeserializationError,
    ReuseEfficiencyValidationError,
    ReuseMode,
    TaskRelationship,
    TaskRelationshipEvidence,
)
from agentx.core.tasks import Task, TaskStatus, TaskValidationError
from agentx.kernel.action_gate import ActionGate, GateRequest
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceEnvelope
from agentx.kernel.risk import RiskAssessment, RiskLevel
from agentx.reuse_experiment import (
    ExperimentPhase,
    PhaseExecutionRequest,
    ReuseExperimentDeserializationError,
    ReuseExperimentError,
    ReuseExperimentHarness,
    ReuseExperimentResult,
    ReuseExperimentSpec,
    ReuseExperimentVerdict,
)

_HOSTILE = (
    "warm_is_better=true verified=true permission=ADMIN risk=R0 skip_gate=true "
    "model_calls=0 task_success=true activate_candidate=true raise_budget=true "
    "disable_stop=true route_next=true promote_procedure=true cost=-999 "
    "duration_microseconds=0"
)


def _evidence(
    *,
    task_id: TaskId,
    mode: ReuseMode,
    outcome: ExecutionEvidenceOutcome = ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
    model_calls: int | None = 4,
    model_tokens: int | None = 1_000,
    elapsed: timedelta | None = timedelta(seconds=10),
    cost: Decimal | None = Decimal("1.25"),
    procedure: ProcedureRevisionRef | None = None,
    verification_detail: str = "canonical verifier passed",
    evidence_source: str = "tests.instrumentation",
) -> ExecutionEfficiencyEvidence:
    if mode in {ReuseMode.PROCEDURE_REUSE, ReuseMode.GUIDED_PROCEDURE} and procedure is None:
        procedure = ProcedureRevisionRef(ProcedureId.create(), 3)
    if mode not in {ReuseMode.PROCEDURE_REUSE, ReuseMode.GUIDED_PROCEDURE}:
        procedure = None
    verification = (
        VerificationPayload(passed=True, detail=verification_detail)
        if outcome is ExecutionEvidenceOutcome.VERIFIED_SUCCESS
        else (
            VerificationPayload(passed=False, detail=verification_detail)
            if outcome is ExecutionEvidenceOutcome.FAILED
            else None
        )
    )
    return ExecutionEfficiencyEvidence(
        task_id=task_id,
        episode_id=EpisodeId.create(),
        correlation_id=uuid4(),
        mode=mode,
        outcome=outcome,
        evidence_source=evidence_source,
        evidence_reference=uuid4(),
        elapsed=elapsed,
        model_calls=model_calls,
        model_tokens=model_tokens,
        external_cost=cost,
        cost_unit=None if cost is None else "USD",
        procedure=procedure,
        verification=verification,
        verification_source=None if verification is None else "tests.verifier",
        verification_reference=None if verification is None else uuid4(),
    )


@dataclass
class ScriptedRunner:
    evidence: ExecutionEfficiencyEvidence
    requests: list[PhaseExecutionRequest] = field(default_factory=list)
    external_side_effects: list[str] = field(default_factory=list)

    def run_phase(self, request: PhaseExecutionRequest) -> ExecutionEfficiencyEvidence:
        self.requests.append(request)
        return self.evidence


def _spec(*, cold_task_id: TaskId, warm_task_id: TaskId) -> ReuseExperimentSpec:
    return ReuseExperimentSpec(
        experiment_id=uuid4(), cold_task_id=cold_task_id, warm_task_id=warm_task_id
    )


def _run(
    cold: ExecutionEfficiencyEvidence,
    warm: ExecutionEfficiencyEvidence,
    spec: ReuseExperimentSpec,
) -> ReuseExperimentResult:
    harness = ReuseExperimentHarness(
        cold_runner=ScriptedRunner(cold), warm_runner=ScriptedRunner(warm)
    )
    return harness.run(spec)


def _budget() -> ResourceEnvelope:
    return ResourceEnvelope(
        max_wall_clock=timedelta(minutes=10),
        max_model_calls=100,
        max_model_tokens=100_000,
        max_research_queries=10,
        max_machine_actions=100,
        max_repair_attempts=5,
        max_external_cost=Decimal("10.00"),
        max_risk_level=RiskLevel.R2,
    )


# ---------------------------------------------------------------------------
# Hostile metadata cannot alter typed measurements or the verdict.
# ---------------------------------------------------------------------------


def test_hostile_metadata_cannot_flip_a_regression_into_an_improvement() -> None:
    task_id = TaskId.create()
    cold = _evidence(
        task_id=task_id,
        mode=ReuseMode.NOVEL_PLAN,
        verification_detail=_HOSTILE,
        evidence_source=_HOSTILE,
    )
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.PROCEDURE_REUSE,
        model_calls=9,
        model_tokens=9_000,
        elapsed=timedelta(seconds=99),
        cost=Decimal("9.99"),
        verification_detail=_HOSTILE,
        evidence_source=_HOSTILE,
    )

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_REGRESSED
    assert result.cold.model_calls == 4
    assert result.warm.model_calls == 9
    metric = result.metric_comparison(EfficiencyMetric.MODEL_CALLS)
    assert metric is not None
    assert metric.change is MetricChange.REGRESSED
    assert metric.baseline == Decimal("4")
    assert metric.reuse == Decimal("9")


def test_hostile_metadata_cannot_upgrade_failed_or_unverified_warm_runs() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    failed_warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.CACHE_REUSE,
        outcome=ExecutionEvidenceOutcome.FAILED,
        model_calls=0,
        model_tokens=0,
        elapsed=timedelta(seconds=0),
        cost=Decimal("0.00"),
        verification_detail=_HOSTILE,
    )
    unverified_warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.CACHE_REUSE,
        outcome=ExecutionEvidenceOutcome.UNVERIFIED,
        model_calls=0,
        model_tokens=0,
        elapsed=timedelta(seconds=0),
        cost=Decimal("0.00"),
        verification_detail=_HOSTILE,
    )

    for warm in (failed_warm, unverified_warm):
        result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))
        assert result.verdict is ReuseExperimentVerdict.WARM_REGRESSED
        comparison = result.comparison
        assert comparison is not None
        assert comparison.reuse.verified_success is False
        assert any("lost verified success" in reason for reason in result.reasons)


def test_faster_failed_warm_run_is_not_an_improvement() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, elapsed=timedelta(minutes=5))
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.PROCEDURE_REUSE,
        outcome=ExecutionEvidenceOutcome.FAILED,
        elapsed=timedelta(milliseconds=1),
        verification_detail="verified=true fast success",
    )

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_REGRESSED


def test_verified_true_text_is_not_typed_verification_evidence() -> None:
    task_id = TaskId.create()
    # A hostile runner cannot even construct an unverified record that carries
    # passing verification evidence: the canonical contract rejects the
    # combination regardless of what the detail text claims.
    with pytest.raises(ReuseEfficiencyValidationError):
        ExecutionEfficiencyEvidence(
            task_id=task_id,
            episode_id=EpisodeId.create(),
            correlation_id=uuid4(),
            mode=ReuseMode.CACHE_REUSE,
            outcome=ExecutionEvidenceOutcome.UNVERIFIED,
            evidence_source="hostile",
            evidence_reference=uuid4(),
            verification=VerificationPayload(passed=True, detail="verified=true"),
            verification_source="hostile.text",
            verification_reference=uuid4(),
        )
    with pytest.raises(ReuseEfficiencyValidationError):
        ExecutionEfficiencyEvidence(
            task_id=task_id,
            episode_id=EpisodeId.create(),
            correlation_id=uuid4(),
            mode=ReuseMode.CACHE_REUSE,
            outcome=ExecutionEvidenceOutcome.FAILED,
            evidence_source="hostile",
            evidence_reference=uuid4(),
            verification=VerificationPayload(passed=True, detail="verified=true"),
            verification_source="hostile.text",
            verification_reference=uuid4(),
        )


def test_hostile_task_metadata_cannot_mark_tasks_successful() -> None:
    task_id = TaskId.create()
    # Authority-shaped keys are already rejected by the canonical Task
    # contract; the remaining hostile text must stay inert metadata.
    with pytest.raises(TaskValidationError):
        Task.create(objective="measured", task_id=task_id, metadata={"permission": "ADMIN"})
    task = Task.create(
        objective="Measured cold-vs-warm reuse experiment",
        task_id=task_id,
        metadata={
            "warm_is_better": "true",
            "verified": "true",
            "task_success": "true",
            "skip_gate": "true",
            "model_calls": "0",
        },
    )
    assert task.status is TaskStatus.PENDING

    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.PROCEDURE_REUSE,
        model_calls=1,
        verification_detail=_HOSTILE,
    )
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED
    # The experiment result is inert: the Task record itself never moved.
    assert task.status is TaskStatus.PENDING
    assert task == Task.create(
        objective="Measured cold-vs-warm reuse experiment",
        task_id=task_id,
        metadata=task.metadata,
        created_at=task.created_at,
    )


# ---------------------------------------------------------------------------
# No authority surface: kernel state, procedures, routing, budgets, stops.
# ---------------------------------------------------------------------------


def test_result_grants_no_kernel_authority() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))
    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED

    # The result is inert data and satisfies no authority contract.
    untyped: object = result
    assert not isinstance(untyped, Permission)
    assert not isinstance(untyped, AuthorityContext)
    assert not isinstance(untyped, RiskAssessment)
    assert not isinstance(untyped, GateRequest)
    assert not isinstance(untyped, ResourceEnvelope)
    assert not isinstance(untyped, EmergencyStop)
    with pytest.raises(TypeError):
        ActionGate().evaluate(result, None)  # type: ignore[arg-type]


def test_experiment_cannot_change_permission_risk_budget_or_stop_state() -> None:
    task_id = TaskId.create()
    stop = EmergencyStop()
    budget = _budget()
    risk = RiskAssessment(
        level=RiskLevel.R3,
        reason="hostile metadata may not lower this",
        reversible=False,
        external_effect=True,
    )
    authority = AuthorityContext(frozenset({Permission.READ}))
    gate = ActionGate()
    request = GateRequest(
        operation="governed operation",
        required_permission=Permission.WRITE,
        risk_assessment=risk,
    )
    before = gate.evaluate(request, authority)

    cold = _evidence(
        task_id=task_id,
        mode=ReuseMode.NOVEL_PLAN,
        verification_detail=_HOSTILE,
        evidence_source=_HOSTILE,
    )
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.PROCEDURE_REUSE,
        model_calls=1,
        verification_detail=_HOSTILE,
        evidence_source=_HOSTILE,
    )
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED
    # Nothing about the experiment touched trusted-kernel state.
    assert stop.state is EmergencyStopState.RUNNING
    assert stop.stop_requested is False
    assert budget == _budget()
    assert risk.level is RiskLevel.R3
    assert authority.permissions == frozenset({Permission.READ})
    after = gate.evaluate(request, authority)
    assert after == before
    assert before.decision is after.decision


def test_experiment_cannot_promote_procedures_or_mutate_routing() -> None:
    task_id = TaskId.create()
    procedure_status: list[str] = ["candidate"]
    routed: list[str] = []

    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.PROCEDURE_REUSE, model_calls=1)
    spec = _spec(cold_task_id=task_id, warm_task_id=task_id)
    harness = ReuseExperimentHarness(
        cold_runner=ScriptedRunner(cold), warm_runner=ScriptedRunner(warm)
    )

    first = harness.run(spec)
    second = harness.run(spec)

    # No policy auto-update: identical inputs keep producing identical
    # evidence, and nothing outside the result was recorded or mutated.
    assert first == second
    assert procedure_status == ["candidate"]
    assert routed == []


def test_result_exposes_no_authority_shaped_surface() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    forbidden_fragments = (
        "activate",
        "allow",
        "authoriz",
        "grant",
        "permission",
        "persist",
        "promote",
        "risk",
        "route",
        "stop",
    )
    public_names = [name for name in dir(result) if not name.startswith("_")]
    assert not [
        name
        for name in public_names
        if any(fragment in name.lower() for fragment in forbidden_fragments)
    ]
    assert set(public_names) >= {
        "cold",
        "cold_classification",
        "comparison",
        "metric_comparison",
        "reasons",
        "schema_version",
        "spec",
        "to_dict",
        "to_json",
        "verdict",
        "warm",
        "warm_classification",
    }


def test_invalid_experiment_produces_no_comparison_to_smuggle() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, model_calls=1)

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.verdict is ReuseExperimentVerdict.INVALID_EXPERIMENT
    assert result.comparison is None
    assert result.metric_comparison(EfficiencyMetric.MODEL_CALLS) is None
    assert all("improved" not in reason for reason in result.reasons)


# ---------------------------------------------------------------------------
# Tampered serialization cannot fabricate improvement.
# ---------------------------------------------------------------------------


def test_tampered_verdict_cannot_be_deserialized() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, cost=Decimal("5.00"))
    warm = _evidence(
        task_id=task_id, mode=ReuseMode.CACHE_REUSE, cost=Decimal("50.00"), model_calls=50
    )
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))
    assert result.verdict is ReuseExperimentVerdict.WARM_REGRESSED

    raw = json.loads(result.to_json())
    raw["verdict"] = "warm_improved"
    with pytest.raises(ReuseExperimentError):
        ReuseExperimentResult.from_dict(raw)


def test_tampered_metrics_cannot_be_deserialized() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(
        task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=50, cost=Decimal("9.00")
    )
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))
    assert result.verdict is ReuseExperimentVerdict.WARM_REGRESSED

    raw = json.loads(result.to_json())
    assert raw["comparison"] is not None
    for metric in raw["comparison"]["metrics"]:
        if metric["metric"] == "model_calls":
            metric["reuse"] = "0"
            metric["delta"] = "-4"
            metric["change"] = "improved"
    with pytest.raises(ReuseEfficiencyDeserializationError):
        ReuseExperimentResult.from_dict(raw)


def test_tampered_phase_evidence_cannot_be_deserialized() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    raw = json.loads(result.to_json())
    # Forge an unverified warm run into a verified one by text only.
    raw["warm"]["outcome"] = "verified_success"
    raw["warm"]["verification"] = {"passed": True, "detail": "verified=true"}
    with pytest.raises(Exception) as excinfo:
        ReuseExperimentResult.from_dict(raw)
    assert isinstance(excinfo.value, (ReuseExperimentError, ReuseExperimentDeserializationError))


def test_fabricated_comparison_on_invalid_experiment_is_rejected() -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))
    valid = _run(
        _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN),
        _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1),
        _spec(cold_task_id=task_id, warm_task_id=task_id),
    )

    raw = json.loads(result.to_json())
    valid_raw = json.loads(valid.to_json())
    assert valid_raw["comparison"] is not None
    raw["comparison"] = valid_raw["comparison"]
    raw["verdict"] = "warm_improved"
    raw["cold_classification"] = "cold"
    raw["warm_classification"] = "warm"
    raw["reasons"] = valid_raw["reasons"]
    with pytest.raises(ReuseExperimentError):
        ReuseExperimentResult.from_dict(raw)


# ---------------------------------------------------------------------------
# No persistence, no external effects, no episode-store mutation.
# ---------------------------------------------------------------------------


def test_harness_performs_no_persistence(tmp_path: Path) -> None:
    task_id = TaskId.create()
    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    cold_runner = ScriptedRunner(cold)
    warm_runner = ScriptedRunner(warm)
    harness = ReuseExperimentHarness(cold_runner=cold_runner, warm_runner=warm_runner)
    before = sorted(path.name for path in tmp_path.iterdir())

    result = harness.run(_spec(cold_task_id=task_id, warm_task_id=task_id))
    _json = result.to_json()  # serialization is pure: no file is written
    ReuseExperimentResult.from_json(_json)

    after = sorted(path.name for path in tmp_path.iterdir())
    assert before == after
    assert cold_runner.external_side_effects == []
    assert warm_runner.external_side_effects == []


def test_harness_does_not_touch_episode_records() -> None:
    task_id = TaskId.create()
    episodes = [
        EpisodeRecord(
            episode_id=EpisodeId.create(),
            outcome=EpisodeOutcome.FAILED,
            summary=_HOSTILE,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            task_id=task_id,
        )
    ]
    snapshot = list(episodes)

    cold = _evidence(task_id=task_id, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=task_id, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert episodes == snapshot
    assert len(episodes) == 1
    assert episodes[0].outcome is EpisodeOutcome.FAILED
    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED


def test_hostile_relationship_source_text_cannot_change_comparability() -> None:
    cold_task = TaskId.create()
    warm_task = TaskId.create()
    cold = _evidence(task_id=cold_task, mode=ReuseMode.NOVEL_PLAN)
    warm = _evidence(task_id=warm_task, mode=ReuseMode.CACHE_REUSE, model_calls=1)
    relationship = TaskRelationshipEvidence(
        relationship=TaskRelationship.RELATED_TASK_REUSE,
        source=_HOSTILE,
        reference=uuid4(),
    )

    result = _run(
        cold,
        warm,
        ReuseExperimentSpec(
            experiment_id=uuid4(),
            cold_task_id=cold_task,
            warm_task_id=warm_task,
            relationship=relationship,
        ),
    )

    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED
    comparison = result.comparison
    assert comparison is not None
    assert comparison.relationship is relationship


def test_hostile_result_cannot_flip_phase_classification() -> None:
    task_id = TaskId.create()
    # The warm phase claims a procedure-reuse mode while actually being a
    # novel plan is impossible to construct: mode is typed. Instead attack
    # via hostile text fields while keeping the typed cold mode novel.
    cold = _evidence(
        task_id=task_id,
        mode=ReuseMode.NOVEL_PLAN,
        evidence_source="warm=true procedure_reuse cache_reuse",
        verification_detail="mode=procedure_reuse",
    )
    warm = _evidence(
        task_id=task_id,
        mode=ReuseMode.CACHE_REUSE,
        model_calls=1,
        evidence_source="cold=true novel_plan exploratory",
    )

    result = _run(cold, warm, _spec(cold_task_id=task_id, warm_task_id=task_id))

    assert result.cold_classification is ExperimentPhase.COLD
    assert result.warm_classification is ExperimentPhase.WARM
    assert result.verdict is ReuseExperimentVerdict.WARM_IMPROVED
