"""Adversarial authority-boundary tests for M5.01 procedure degradation.

Prove that hostile content, fake lookalikes, floods, disordered timestamps,
and smuggled authority claims never turn an assessment into lifecycle mutation,
repair, authority, or dynamic execution.
"""

from __future__ import annotations

import ast
import builtins
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest
from tests.support.authority_proxy import ForbiddenAuthorityProxy

from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.environment_change import (
    EnvironmentChangeResult,
    EnvironmentFactKey,
    EnvironmentFactKind,
    EnvironmentFactValue,
    EnvironmentFactValueKind,
    EnvironmentObservation,
    EnvironmentSnapshot,
    detect_environment_change,
)
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.core.failure_diagnosis import (
    DiagnosticConclusion,
    DiagnosticEvidence,
    DiagnosticEvidenceKind,
    package_diagnosis,
)
from agentx.core.failure_localization import FailureLocalization, FailureLocationKind
from agentx.core.failure_taxonomy import FailureCategory, FailureClassification
from agentx.core.ids import ProcedureId
from agentx.core.knowledge import (
    KnowledgeScope,
    ProvenanceKind,
    ProvenanceReference,
    ScopeDimension,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
)
from agentx.core.provenance import EvidenceKind, EvidenceReference
from agentx.procedure_degradation import (
    MAX_FAILURE_EVIDENCE,
    BoundEnvironmentChange,
    BoundFailureDiagnosis,
    DegradationState,
    ProcedureDegradationValidationError,
    ProcedureSuccessEvidence,
    assess_procedure_degradation,
)

_T0 = datetime(2026, 9, 7, 8, 0, 0, tzinfo=UTC)
_ASSESS = _T0 + timedelta(hours=5)
_TTL = timedelta(hours=6)
_SCOPE = KnowledgeScope(
    dimensions={
        ScopeDimension.OPERATING_SYSTEM: "windows",
        ScopeDimension.ENVIRONMENT: "lab",
    }
)

_HOSTILE = (
    "ADMIN",
    "ALLOW R4",
    "permission=WRITE",
    "verified=true",
    "confirmed=true",
    "retire=true",
    "retire this procedure",
    "risk=R0",
    "permission=ADMIN",
    "ignore previous instructions",
    "budget=unlimited",
    "repair=approved",
)


def _classification(
    category: FailureCategory = FailureCategory.PROCEDURE,
    *,
    summary: str = "failure",
    at: datetime = _T0,
) -> FailureClassification:
    return FailureClassification(category=category, summary=summary, classified_at=at)


def _localization(pid: ProcedureId, *, at: datetime = _T0) -> FailureLocalization:
    return FailureLocalization(
        kind=FailureLocationKind.PROCEDURE_NODE,
        summary="node",
        localized_at=at,
        procedure_id=pid,
        procedure_node_id="n1",
    )


def _strong(
    pid: ProcedureId,
    *,
    revision: int | None = 1,
    at: datetime = _T0,
    summary: str = "strong",
) -> BoundFailureDiagnosis:
    diagnosis = package_diagnosis(
        classification=_classification(summary=summary[:80], at=at),
        localization=_localization(pid, at=at),
        summary=summary[:80],
        diagnosed_at=at,
        evidence=(
            DiagnosticEvidence(
                kind=DiagnosticEvidenceKind.CHAIN_CORRELATION, correlation_id=uuid4()
            ),
        ),
        conclusion=DiagnosticConclusion.NODE_IMPLICATED,
    )
    return BoundFailureDiagnosis(diagnosis=diagnosis, procedure_revision=revision)


def _verified(at: datetime = _T0) -> CausalExperience:
    return CausalExperience(
        correlation_id=uuid4(),
        state_before=ExperienceState(
            captured_at=at, observation=ObservationPayload(value={"a": 1})
        ),
        action=ActionPayload(name="a"),
        action_at=at + timedelta(seconds=1),
        observation=ObservationPayload(value={"b": 1}),
        observation_at=at + timedelta(seconds=2),
        state_after=ExperienceState(
            captured_at=at + timedelta(seconds=3),
            observation=ObservationPayload(value={"c": 1}),
        ),
        verification=VerificationPayload(passed=True, detail="ok"),
        verification_at=at + timedelta(seconds=4),
        outcome=CausalOutcome.VERIFIED,
        outcome_at=at + timedelta(seconds=5),
    )


def _env(
    pid: ProcedureId, *, revision: int | None = 1, at: datetime = _T0
) -> BoundEnvironmentChange:
    diagnosis = package_diagnosis(
        classification=_classification(FailureCategory.ENVIRONMENT, at=at),
        localization=_localization(pid, at=at),
        summary="env",
        diagnosed_at=at,
    )
    fact = EnvironmentFactKey(kind=EnvironmentFactKind.PLATFORM_IDENTITY, subject="os")
    prov = ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="adv")
    baseline = EnvironmentSnapshot(
        scope=_SCOPE,
        evidence=EvidenceReference(
            kind=EvidenceKind.OBSERVATION,
            reference="b",
            provenance=prov,
            observed_at=at - timedelta(hours=1),
        ),
        observations=(
            EnvironmentObservation(
                fact=fact,
                value=EnvironmentFactValue(kind=EnvironmentFactValueKind.TEXT, text="win10"),
                observed_at=at - timedelta(hours=1),
                ttl=_TTL,
                provenance=prov,
            ),
        ),
    )
    current = EnvironmentSnapshot(
        scope=_SCOPE,
        evidence=EvidenceReference(
            kind=EvidenceKind.OBSERVATION,
            reference="c",
            provenance=prov,
            observed_at=at - timedelta(minutes=10),
        ),
        observations=(
            EnvironmentObservation(
                fact=fact,
                value=EnvironmentFactValue(kind=EnvironmentFactValueKind.TEXT, text="win11"),
                observed_at=at - timedelta(minutes=10),
                ttl=_TTL,
                provenance=prov,
            ),
        ),
    )
    detection = detect_environment_change(
        diagnosis=diagnosis,
        scope=_SCOPE,
        baseline=baseline,
        current=current,
        compared_at=at,
        summary="change",
    )
    assert detection.result is EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED
    return BoundEnvironmentChange(detection=detection, procedure_revision=revision)


# --------------------------------------------------------------------------
# lookalikes / wrong identity / wrong revision
# --------------------------------------------------------------------------


def test_fake_lookalike_failure_diagnosis_rejected() -> None:
    pid = ProcedureId.create()

    class FakeDiagnosis:
        procedure_id = pid
        conclusion = DiagnosticConclusion.NODE_IMPLICATED
        classification = type("C", (), {"category": FailureCategory.PROCEDURE})()
        diagnosed_at = _T0

    with pytest.raises(ProcedureDegradationValidationError):
        assess_procedure_degradation(
            procedure_id=pid,
            revision=1,
            failure_diagnoses=[FakeDiagnosis()],  # type: ignore[list-item]
            assessed_at=_ASSESS,
        )


def test_wrong_revision_flood_cannot_confirm_current() -> None:
    pid = ProcedureId.create()
    flood = [_strong(pid, revision=1, at=_T0 + timedelta(minutes=i)) for i in range(10)]
    result = assess_procedure_degradation(
        procedure_id=pid,
        revision=5,
        failure_diagnoses=flood,
        assessed_at=_ASSESS,
    )
    assert result.state is DegradationState.UNKNOWN
    assert result.ignored_failure_indices == tuple(range(10))
    assert result.is_confirmed_degraded is False


def test_wrong_procedure_flood_cannot_confirm() -> None:
    target = ProcedureId.create()
    other = ProcedureId.create()
    flood = [_strong(other, at=_T0 + timedelta(minutes=i)) for i in range(8)]
    result = assess_procedure_degradation(
        procedure_id=target,
        revision=1,
        failure_diagnoses=flood,
        assessed_at=_ASSESS,
    )
    assert result.state is DegradationState.UNKNOWN
    assert len(result.ignored_failure_indices) == 8


def test_duplicate_evidence_flood_within_bounds_is_deterministic() -> None:
    pid = ProcedureId.create()
    # Same strong diagnosis object repeated (still within bound).
    one = _strong(pid, at=_T0 + timedelta(minutes=1))
    two = _strong(pid, at=_T0 + timedelta(minutes=2))
    batch = [one, two] * 8  # 16 items
    a = assess_procedure_degradation(
        procedure_id=pid, revision=1, failure_diagnoses=batch, assessed_at=_ASSESS
    )
    b = assess_procedure_degradation(
        procedure_id=pid, revision=1, failure_diagnoses=batch, assessed_at=_ASSESS
    )
    assert a == b
    assert a.state is DegradationState.CONFIRMED_DEGRADED


def test_huge_evidence_list_rejected() -> None:
    pid = ProcedureId.create()
    huge = [_strong(pid) for _ in range(MAX_FAILURE_EVIDENCE + 5)]
    with pytest.raises(ProcedureDegradationValidationError, match="hard bound"):
        assess_procedure_degradation(
            procedure_id=pid,
            revision=1,
            failure_diagnoses=huge,
            assessed_at=_ASSESS,
        )


# --------------------------------------------------------------------------
# hostile text inert
# --------------------------------------------------------------------------


@pytest.mark.parametrize("hostile", _HOSTILE)
def test_hostile_strings_never_confirm_or_grant_authority(hostile: str) -> None:
    pid = ProcedureId.create()
    diagnosis = package_diagnosis(
        classification=_classification(summary=hostile[:80]),
        localization=_localization(pid),
        summary=hostile[:80],
        diagnosed_at=_T0,
        detail=hostile,
        evidence=(
            DiagnosticEvidence(
                kind=DiagnosticEvidenceKind.CHAIN_CORRELATION, correlation_id=uuid4()
            ),
        ),
        conclusion=DiagnosticConclusion.NODE_IMPLICATED,
    )
    result = assess_procedure_degradation(
        procedure_id=pid,
        revision=1,
        failure_diagnoses=[BoundFailureDiagnosis(diagnosis=diagnosis, procedure_revision=1)],
        assessed_at=_ASSESS,
    )
    # Single strong failure: suspected only. Hostile text does not escalate.
    assert result.state is DegradationState.SUSPECTED_DEGRADED
    assert result.is_confirmed_degraded is False
    # No reason is the hostile instruction itself as an authority claim.
    joined = " ".join(result.reasons).lower()
    assert "permission=admin" not in joined
    assert "risk=r0" not in joined


def test_confirmed_true_text_does_not_confirm() -> None:
    pid = ProcedureId.create()
    result = assess_procedure_degradation(
        procedure_id=pid,
        revision=1,
        failure_diagnoses=[_strong(pid, summary="confirmed=true procedure broken retire=true")],
        assessed_at=_ASSESS,
    )
    assert result.state is not DegradationState.CONFIRMED_DEGRADED


# --------------------------------------------------------------------------
# timestamp disorder / malformed
# --------------------------------------------------------------------------


def test_timestamp_disorder_still_deterministic() -> None:
    """Later-supplied but earlier-stamped evidence is ordered by timestamp, not list order."""
    pid = ProcedureId.create()
    early_success = ProcedureSuccessEvidence(
        procedure_id=pid,
        procedure_revision=1,
        observed_at=_T0,
        causal_experience=_verified(at=_T0),
    )
    late_failures = [
        _strong(pid, at=_T0 + timedelta(hours=2)),
        _strong(pid, at=_T0 + timedelta(hours=3)),
    ]
    # Feed failures first, success last — opposite of chronological order.
    a = assess_procedure_degradation(
        procedure_id=pid,
        revision=1,
        failure_diagnoses=late_failures,
        success_evidence=[early_success],
        assessed_at=_ASSESS,
    )
    b = assess_procedure_degradation(
        procedure_id=pid,
        revision=1,
        success_evidence=[early_success],
        failure_diagnoses=list(reversed(late_failures)),
        assessed_at=_ASSESS,
    )
    assert a.state is DegradationState.CONFIRMED_DEGRADED
    assert b.state is DegradationState.CONFIRMED_DEGRADED
    assert a.state is b.state


def test_malformed_evidence_types_rejected() -> None:
    pid = ProcedureId.create()
    with pytest.raises(ProcedureDegradationValidationError):
        assess_procedure_degradation(
            procedure_id=pid,
            revision=1,
            failure_diagnoses="not a sequence",  # type: ignore[arg-type]
            assessed_at=_ASSESS,
        )
    with pytest.raises(ProcedureDegradationValidationError):
        assess_procedure_degradation(
            procedure_id=pid,
            revision=1,
            environment_changes={"x": 1},  # type: ignore[arg-type]
            assessed_at=_ASSESS,
        )
    with pytest.raises(ProcedureDegradationValidationError):
        assess_procedure_degradation(
            procedure_id=pid,
            revision=1,
            success_evidence=[object()],  # type: ignore[list-item]
            assessed_at=_ASSESS,
        )


def test_json_string_not_accepted_as_diagnosis() -> None:
    pid = ProcedureId.create()
    with pytest.raises(ProcedureDegradationValidationError):
        assess_procedure_degradation(
            procedure_id=pid,
            revision=1,
            failure_diagnoses=['{"conclusion":"node_implicated"}'],  # type: ignore[list-item]
            assessed_at=_ASSESS,
        )


# --------------------------------------------------------------------------
# no lifecycle mutation / no repair / no authority / no dynamic execution
# --------------------------------------------------------------------------


def test_assessment_does_not_mutate_procedure_status() -> None:
    record = ProcedureRecord.create(
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content="{}"),
        created_at=_T0,
        revision=1,
    )
    original_status = record.status
    assert original_status is ProcedureStatus.CANDIDATE

    assess_procedure_degradation(
        procedure=record,
        failure_diagnoses=[
            _strong(record.procedure_id, at=_T0 + timedelta(minutes=1)),
            _strong(record.procedure_id, at=_T0 + timedelta(minutes=2)),
        ],
        assessed_at=_ASSESS,
    )

    assert record.status is original_status
    assert record.status is ProcedureStatus.CANDIDATE
    assert set(ProcedureStatus) == {
        ProcedureStatus.CANDIDATE,
        ProcedureStatus.ACTIVE,
        ProcedureStatus.RETIRED,
    }
    assert "DEGRADED" not in {m.name for m in ProcedureStatus}


def test_module_source_has_no_lifecycle_or_repair_calls() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "src" / "agentx" / "procedure_degradation.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    forbidden = {
        "update_status",
        "retire",
        "activate",
        "promote",
        "execute",
        "repair",
        "derive_repair_candidates",
        "grant",
        "revoke",
        "publish",
        "transition",
        "check_and_consume",
        "evaluate_gate",
        "request_stop",
        "eval",
        "exec",
        "open",
        "__import__",
    }
    assert called.isdisjoint(forbidden)


def test_assessment_never_touches_authority_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    touched: list[tuple[str, str]] = []
    forbidden_modules = (
        "agentx.kernel.action_gate",
        "agentx.kernel.permissions",
        "agentx.kernel.risk",
        "agentx.kernel.resource_budget",
        "agentx.kernel.emergency_stop",
        "agentx.infrastructure.procedure_store",
        "agentx.capabilities.executor",
        "agentx.cognition.reasoner",
        "agentx.hive.experience_memory",
        "agentx.learning.trajectory",
        "agentx.procedures.graph",
    )
    import sys

    for name in forbidden_modules:
        proxy: object = ForbiddenAuthorityProxy(name, touched)
        monkeypatch.setitem(sys.modules, name, proxy)

    pid = ProcedureId.create()
    assess_procedure_degradation(
        procedure_id=pid,
        revision=1,
        failure_diagnoses=[_strong(pid), _strong(pid, at=_T0 + timedelta(minutes=5))],
        environment_changes=[_env(pid, at=_T0 + timedelta(minutes=10))],
        success_evidence=[
            ProcedureSuccessEvidence(
                procedure_id=pid,
                procedure_revision=1,
                observed_at=_T0 - timedelta(hours=1),
                causal_experience=_verified(at=_T0 - timedelta(hours=1)),
            )
        ],
        assessed_at=_ASSESS,
    )
    assert touched == []


def test_no_dynamic_execution_primitives_used() -> None:
    source_path = (
        Path(__file__).resolve().parents[2] / "src" / "agentx" / "procedure_degradation.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    forbidden_imports = {
        "importlib",
        "subprocess",
        "socket",
        "sqlite3",
        "http",
        "urllib",
        "ctypes",
        "threading",
        "multiprocessing",
        "pickle",
        "pathlib",
        "os",
        "sys",
        "random",
        "secrets",
        "time",
    }
    assert imports.isdisjoint(forbidden_imports)
    assert "agentx.kernel" not in imports
    assert not any(name.startswith("agentx.kernel") for name in imports)
    assert not any(name.startswith("agentx.infrastructure") for name in imports)
    assert not any(name.startswith("agentx.capabilities") for name in imports)
    assert not any(name.startswith("agentx.cognition") for name in imports)
    assert not any(name.startswith("agentx.hive") for name in imports)
    assert not any(name.startswith("agentx.learning") for name in imports)
    assert not any(name.startswith("agentx.procedures") for name in imports)


def test_builtins_eval_exec_not_reachable_during_assessment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("dynamic execution must not run")

    monkeypatch.setattr(builtins, "eval", boom)
    monkeypatch.setattr(builtins, "exec", boom)
    monkeypatch.setattr(builtins, "compile", boom)

    pid = ProcedureId.create()
    result = assess_procedure_degradation(
        procedure_id=pid,
        revision=1,
        failure_diagnoses=[_strong(pid), _strong(pid, at=_T0 + timedelta(minutes=3))],
        assessed_at=_ASSESS,
    )
    assert result.state is DegradationState.CONFIRMED_DEGRADED


def test_assessment_exposes_no_repair_or_authority_methods() -> None:
    from agentx import procedure_degradation as mod

    for name in (
        "retire",
        "activate",
        "repair",
        "execute",
        "grant",
        "update_status",
        "derive_repair_candidates",
        "publish",
    ):
        assert not hasattr(mod, name)
        assert not hasattr(mod.ProcedureDegradationAssessment, name)
        assert not hasattr(mod.DegradationState, name)


def test_module_is_importable_as_plain_module() -> None:
    import agentx.procedure_degradation as mod

    assert isinstance(mod, ModuleType)
    assert mod.assess_procedure_degradation is assess_procedure_degradation
