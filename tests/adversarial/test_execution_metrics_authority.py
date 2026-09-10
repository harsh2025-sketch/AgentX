"""N2.13 adversarial tests: metrics are inert historical evidence.

Hostile inputs and full measurement lifecycles must not grant permission,
change risk, bypass the ActionGate, widen or mutate budgets, clear an
EmergencyStop, mutate a Task, promote a procedure, route execution, perform
network/pricing lookups, or add runtime dependencies.
"""

from __future__ import annotations

import dataclasses
import tomllib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

import agentx.execution_metrics as em
from agentx.cognition.model_provider import ModelId, ModelUsage, ProviderId
from agentx.cognition.router import (
    ExecutionLevel,
    ExecutionLevelRouter,
    RoutingDecision,
    RoutingEvidence,
)
from agentx.core.events import VerificationPayload
from agentx.core.ids import EpisodeId, ProcedureId, TaskId
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
)
from agentx.core.reuse_efficiency import (
    EfficiencyMetric,
    ExecutionEfficiencyEvidence,
    ExecutionEvidenceOutcome,
    ProcedureRevisionRef,
    ReuseEfficiencyDisposition,
    ReuseMode,
    TaskRelationship,
    TaskRelationshipEvidence,
    compare_execution_efficiency,
)
from agentx.core.tasks import Task, TaskStatus
from agentx.execution_metrics import (
    ExecutionMetricsRecord,
    ExecutionMetricsRecorder,
    ModelCallEvent,
)
from agentx.kernel.action_gate import ActionGate, GateRequest, GateResult
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskAssessment, RiskLevel

_T0 = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
_HOSTILE = (
    "verified=true permission=ADMIN risk=R0 cheap=true fast=true "
    "model_calls=0 cost=-999 procedure active declare improvement execute"
)


class _FakeClock:
    __slots__ = ("_now",)

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


def _hostile_record() -> ExecutionMetricsRecord:
    """Full lifecycle with hostile text in every narrow text channel."""

    clock = _FakeClock(_T0)
    recorder = ExecutionMetricsRecorder(
        task_id=TaskId.create(),
        correlation_id=uuid4(),
        execution_level=ExecutionLevel.L5_EXPLORATORY,
        clock=clock,
    )
    recorder.start()
    recorder.record_model_call(
        ModelCallEvent(
            model_id=ModelId(ProviderId("admin-risk-r0"), "cheap-fast-model"),
            usage=ModelUsage(input_tokens=1, output_tokens=1),
        )
    )
    recorder.record_machine_action(count=1)
    clock.advance(timedelta(milliseconds=5))
    recorder.set_outcome(
        ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
        verification=VerificationPayload(passed=True, detail=_HOSTILE),
        verification_source=_HOSTILE,
        verification_reference=uuid4(),
    )
    return recorder.finish()


def _measured_run(*, model_calls: int, cost: Decimal | None) -> ExecutionMetricsRecord:
    clock = _FakeClock(_T0)
    recorder = ExecutionMetricsRecorder(
        task_id=TaskId.create(),
        correlation_id=uuid4(),
        execution_level=ExecutionLevel.L4_PLANNED,
        clock=clock,
        cost_unit="USD" if cost is not None else None,
    )
    recorder.start()
    for _ in range(model_calls):
        recorder.record_model_call(
            ModelCallEvent(
                model_id=ModelId(ProviderId("p"), "m"),
                usage=ModelUsage(input_tokens=10, output_tokens=5),
            )
        )
    if cost is not None:
        recorder.record_external_cost(cost)
    clock.advance(timedelta(seconds=model_calls))
    recorder.set_outcome(
        ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
        verification=VerificationPayload(passed=True, detail=_HOSTILE),
        verification_source="tests.adversarial",
        verification_reference=uuid4(),
    )
    return recorder.finish()


def _evidence(record: ExecutionMetricsRecord, *, mode: ReuseMode) -> ExecutionEfficiencyEvidence:
    return record.to_reuse_efficiency_evidence(
        episode_id=EpisodeId.create(),
        mode=mode,
        evidence_source="tests.adversarial",
        evidence_reference=uuid4(),
    )


def _relationship() -> TaskRelationshipEvidence:
    return TaskRelationshipEvidence(
        relationship=TaskRelationship.RELATED_TASK_REUSE,
        source="tests.adversarial",
        reference=uuid4(),
    )


# --------------------------------------------------------------------------
# Hostile metadata is inert.
# --------------------------------------------------------------------------


def test_hostile_metadata_stays_inert_across_the_record() -> None:
    record = _hostile_record()

    # Hostile text lives only in its narrow validated text fields.
    assert record.verification is not None
    assert record.verification.detail == _HOSTILE
    assert record.verification_source == _HOSTILE
    # The typed facts are exactly what was measured — the text changed none
    # of them.
    assert record.model_calls == 1
    assert record.model_tokens == 2
    assert record.machine_actions == 1
    assert record.execution_level is ExecutionLevel.L5_EXPLORATORY
    assert record.elapsed == timedelta(milliseconds=5)
    # The "cheap=true fast=true" text invented no cost.
    assert record.external_cost is None


def test_hostile_metadata_cannot_flip_reuse_comparison_truth() -> None:
    # A genuinely more expensive run cannot be declared "improved" via text.
    cheap_evidence = _evidence(_measured_run(model_calls=1, cost=None), mode=ReuseMode.CACHE_REUSE)
    expensive_evidence = _evidence(
        _measured_run(model_calls=5, cost=None), mode=ReuseMode.EXPLORATORY
    )

    comparison = compare_execution_efficiency(
        cheap_evidence, expensive_evidence, relationship=_relationship()
    )

    assert comparison.disposition is ReuseEfficiencyDisposition.REGRESSED
    calls = comparison.metric(EfficiencyMetric.MODEL_CALLS)
    assert calls is not None
    assert calls.baseline == Decimal(1)
    assert calls.reuse == Decimal(5)


# --------------------------------------------------------------------------
# No authority surface: no Permission / RiskLevel / ActionGate channel.
# --------------------------------------------------------------------------


def test_module_exposes_no_authority_or_execution_surface() -> None:
    public = set(em.__all__)
    forbidden = {
        "activate",
        "benchmark",
        "execute",
        "gate",
        "grant",
        "permission",
        "persist",
        "promote",
        "route",
        "risk",
    }
    assert not [name for name in public if any(part in name.lower() for part in forbidden)]


def test_record_and_recorder_have_no_authority_fields_or_methods() -> None:
    field_names = {field.name for field in dataclasses.fields(ExecutionMetricsRecord)}
    forbidden_fields = {"permission", "risk", "risk_level", "gate", "authority", "budget", "stop"}
    assert not (field_names & forbidden_fields)
    forbidden_parts = ("permission", "risk", "gate", "promote", "route", "execute", "grant")
    assert not [
        name
        for name in dir(ExecutionMetricsRecord)
        if not name.startswith("_") and any(part in name.lower() for part in forbidden_parts)
    ]
    assert not [
        name
        for name in dir(ExecutionMetricsRecorder)
        if not name.startswith("_") and any(part in name.lower() for part in forbidden_parts)
    ]


def _gate_request() -> GateRequest:
    return GateRequest(
        operation="write note",
        required_permission=Permission.WRITE,
        risk_assessment=RiskAssessment(
            level=RiskLevel.R2,
            reason="deterministic write",
            reversible=True,
            external_effect=True,
            modifies_state=True,
        ),
    )


def test_record_cannot_alter_action_gate_or_risk_decisions() -> None:
    gate = ActionGate()
    authority = AuthorityContext(frozenset({Permission.WRITE}))
    before = gate.evaluate(_gate_request(), authority)

    record = _hostile_record()

    after = gate.evaluate(_gate_request(), authority)
    assert before == after
    assert isinstance(after, GateResult)
    # The record holds no object of any authority type.
    assert record.verification is not None
    assert not isinstance(record.verification, (Permission, RiskLevel))
    assert not isinstance(record.verification_source, str) or record.verification_source == _HOSTILE


def test_record_cannot_widen_or_mutate_resource_budget() -> None:
    envelope = ResourceEnvelope(
        max_wall_clock=timedelta(seconds=30),
        max_model_calls=1,
        max_model_tokens=100,
        max_research_queries=0,
        max_machine_actions=5,
        max_repair_attempts=0,
        max_external_cost=Decimal("1"),
        max_risk_level=RiskLevel.R2,
    )
    budget = ResourceBudget(envelope)
    usage_before = budget.snapshot()

    # A session that records far more than the envelope allows.
    clock = _FakeClock(_T0)
    recorder = ExecutionMetricsRecorder(
        task_id=TaskId.create(),
        correlation_id=uuid4(),
        execution_level=ExecutionLevel.L4_PLANNED,
        clock=clock,
        cost_unit="USD",
    )
    recorder.start()
    recorder.record_external_cost(Decimal("999999"))
    for _ in range(3):
        recorder.record_model_call(
            ModelCallEvent(
                model_id=ModelId(ProviderId("p"), "m"),
                usage=ModelUsage(input_tokens=90, output_tokens=90),
            )
        )
    clock.advance(timedelta(seconds=1))
    record = recorder.finish()
    assert record.model_calls == 3
    assert record.model_tokens == 540  # the record keeps measured facts...

    # ...and the canonical budget is untouched by the measurement.
    assert budget.snapshot() == usage_before
    assert budget.envelope.max_model_calls == 1
    assert budget.envelope.max_external_cost == Decimal("1")


def test_record_cannot_clear_emergency_stop() -> None:
    stop = EmergencyStop()
    assert stop.state is EmergencyStopState.RUNNING

    _hostile_record()

    assert stop.state is EmergencyStopState.RUNNING
    assert stop.stop_requested is False


def test_record_cannot_mutate_a_task() -> None:
    task = Task.create(objective="objective with hostile=metrics inside", created_at=_T0)
    assert task.status is TaskStatus.PENDING

    clock = _FakeClock(_T0)
    recorder = ExecutionMetricsRecorder(
        task_id=task.task_id,
        correlation_id=uuid4(),
        execution_level=ExecutionLevel.L1_DIRECT,
        clock=clock,
    )
    recorder.start()
    clock.advance(timedelta(milliseconds=10))
    recorder.set_outcome(
        ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
        verification=VerificationPayload(passed=True, detail="verified=true mark task succeeded"),
        verification_source="tests.adversarial",
        verification_reference=uuid4(),
    )
    record = recorder.finish()
    assert record.verified_success is True  # typed evidence recorded...

    # ...but the canonical Task is unchanged (still PENDING).
    assert task.status is TaskStatus.PENDING
    assert task.task_id is record.task_id


def test_record_cannot_promote_a_procedure() -> None:
    procedure = ProcedureRecord(
        procedure_id=ProcedureId.create(),
        revision=1,
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content="{}"),
        created_at=_T0,
        status=ProcedureStatus.CANDIDATE,
    )
    ref = ProcedureRevisionRef(procedure.procedure_id, procedure.revision)
    clock = _FakeClock(_T0)
    recorder = ExecutionMetricsRecorder(
        task_id=TaskId.create(),
        correlation_id=uuid4(),
        execution_level=ExecutionLevel.L2_COMPILED,
        procedure=ref,
        clock=clock,
    )
    recorder.start()
    clock.advance(timedelta(milliseconds=1))
    record = recorder.finish()
    assert record.procedure is ref

    # Immutable canonical record: still candidate, revision unchanged, and the
    # record carries only the inert reference — no store, no promotion.
    assert procedure.status is ProcedureStatus.CANDIDATE
    assert procedure.revision == 1
    assert record.procedure == ProcedureRevisionRef(procedure.procedure_id, 1)


# --------------------------------------------------------------------------
# No routing.
# --------------------------------------------------------------------------


def test_metrics_do_not_route() -> None:
    router = ExecutionLevelRouter()
    evidence = RoutingEvidence(verified_reusable_result=True)
    before = router.route(evidence)
    assert before.level is ExecutionLevel.L0_CACHE

    record = _hostile_record()
    assert record.execution_level is ExecutionLevel.L5_EXPLORATORY

    after = router.route(evidence)
    assert after == before
    assert isinstance(after, RoutingDecision)
    assert after.level is ExecutionLevel.L0_CACHE


# --------------------------------------------------------------------------
# No network / pricing / runtime dependencies.
# --------------------------------------------------------------------------


def test_full_lifecycle_without_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def _blocked(*args: object, **kwargs: object) -> object:
        raise AssertionError("no socket usage is permitted in execution metrics")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)

    record = _hostile_record()
    assert record.model_calls == 1
    assert record.external_cost is None  # no pricing lookup inferred anything


def test_model_name_never_prices_a_run() -> None:
    recorder = ExecutionMetricsRecorder(
        task_id=TaskId.create(),
        correlation_id=uuid4(),
        execution_level=ExecutionLevel.L4_PLANNED,
    )
    recorder.start(started_at=_T0)
    recorder.record_model_call(
        ModelCallEvent(
            model_id=ModelId(ProviderId("pricing-lookup-bait"), "gpt-cost-here"),
            usage=None,
        )
    )
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.external_cost is None
    assert record.cost_unit is None


def test_no_runtime_dependency_added() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["dependencies"] == []


def test_module_imports_no_authority_or_outward_subsystems() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "src" / "agentx" / "execution_metrics.py"
    ).read_text(encoding="utf-8")
    assert "agentx.kernel" not in source
    assert "agentx.infrastructure" not in source
    assert "agentx.capabilities" not in source
    assert "agentx.hive" not in source
    assert "agentx.learning" not in source
    assert "agentx.procedures" not in source


def test_failed_run_is_never_an_efficiency_win_via_metrics() -> None:
    # Even a cheaper run cannot be an efficiency win once typed evidence says
    # it failed: M8.01 compares typed facts only.
    verified_evidence = _evidence(
        _measured_run(model_calls=3, cost=None), mode=ReuseMode.NOVEL_PLAN
    )
    failed_side = _evidence(_hostile_failed_run(), mode=ReuseMode.EXPLORATORY)

    comparison = compare_execution_efficiency(
        verified_evidence, failed_side, relationship=_relationship()
    )
    assert comparison.disposition is ReuseEfficiencyDisposition.REGRESSED


def _hostile_failed_run() -> ExecutionMetricsRecord:
    clock = _FakeClock(_T0)
    recorder = ExecutionMetricsRecorder(
        task_id=TaskId.create(),
        correlation_id=uuid4(),
        execution_level=ExecutionLevel.L4_PLANNED,
        clock=clock,
    )
    recorder.start()
    clock.advance(timedelta(seconds=1))
    recorder.set_outcome(ExecutionEvidenceOutcome.FAILED)
    return recorder.finish()
