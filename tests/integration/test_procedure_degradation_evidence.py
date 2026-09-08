"""Integration tests: real canonical evidence through the degradation detector.

These tests build genuine C2.03 ProcedureRecord, C4.01 FailureClassification,
C4.02 FailureLocalization, C4.03 FailureDiagnosis, C4.04 EnvironmentChangeDetection,
and C2.10 CausalExperience values — no fake string shortcuts — and prove the
detector matches by canonical IDs and revisions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.core.provenance import EvidenceKind, EvidenceReference
from agentx.procedure_degradation import (
    BoundEnvironmentChange,
    BoundFailureDiagnosis,
    DegradationState,
    ProcedureSuccessEvidence,
    assess_procedure_degradation,
)

_T0 = datetime(2026, 9, 6, 9, 0, 0, tzinfo=UTC)
_TTL = timedelta(hours=12)


def _build_procedure(revision: int = 1) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=ProcedureId.create(),
        revision=revision,
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content='{"nodes":[{"id":"open-report","kind":"action"}]}',
        ),
        created_at=_T0,
        status=ProcedureStatus.ACTIVE,
        scope=ProcedureScope(
            dimensions={
                ProcedureScopeDimension.APPLICATION: "contoso-inventory",
                ProcedureScopeDimension.OPERATING_SYSTEM: "windows",
            }
        ),
        updated_at=_T0 + timedelta(minutes=1),
    )


def _build_diagnosis(
    procedure: ProcedureRecord,
    *,
    category: FailureCategory,
    conclusion: DiagnosticConclusion,
    at: datetime,
    node_id: str = "open-report",
) -> BoundFailureDiagnosis:
    classification = FailureClassification(
        category=category,
        summary=f"Canonical {category.value} classification.",
        classified_at=at,
    )
    localization = FailureLocalization(
        kind=FailureLocationKind.PROCEDURE_NODE,
        summary="Canonical PROCEDURE_NODE localization.",
        localized_at=at,
        procedure_id=procedure.procedure_id,
        procedure_node_id=node_id,
        classification=classification,
    )
    evidence = ()
    if conclusion is DiagnosticConclusion.NODE_IMPLICATED:
        evidence = (
            DiagnosticEvidence(
                kind=DiagnosticEvidenceKind.CHAIN_CORRELATION, correlation_id=uuid4()
            ),
            DiagnosticEvidence(
                kind=DiagnosticEvidenceKind.CANONICAL_ERROR,
                error_code="execution.procedure_node_failed",
            ),
        )
    diagnosis = package_diagnosis(
        classification=classification,
        localization=localization,
        summary="Canonical packaged diagnosis.",
        diagnosed_at=at,
        evidence=evidence,
        conclusion=conclusion,
    )
    assert diagnosis.procedure_id == procedure.procedure_id
    assert diagnosis.procedure_node_id == node_id
    return BoundFailureDiagnosis(diagnosis=diagnosis, procedure_revision=procedure.revision)


def _build_verified_success(
    procedure: ProcedureRecord, *, at: datetime
) -> ProcedureSuccessEvidence:
    t1 = at + timedelta(seconds=1)
    t2 = at + timedelta(seconds=2)
    t3 = at + timedelta(seconds=3)
    t4 = at + timedelta(seconds=4)
    t5 = at + timedelta(seconds=5)
    experience = CausalExperience(
        correlation_id=uuid4(),
        state_before=ExperienceState(
            captured_at=at,
            observation=ObservationPayload(value={"report_open": False}),
        ),
        action=ActionPayload(name="procedure.open_report", data={"node": "open-report"}),
        action_at=t1,
        observation=ObservationPayload(value={"report_open": True}),
        observation_at=t2,
        state_after=ExperienceState(
            captured_at=t3,
            observation=ObservationPayload(value={"report_open": True}),
        ),
        verification=VerificationPayload(passed=True, detail="report visible"),
        verification_at=t4,
        outcome=CausalOutcome.VERIFIED,
        outcome_at=t5,
    )
    assert experience.verified is True
    return ProcedureSuccessEvidence(
        procedure_id=procedure.procedure_id,
        procedure_revision=procedure.revision,
        observed_at=at,
        causal_experience=experience,
    )


def _build_env_change(
    procedure: ProcedureRecord,
    *,
    at: datetime,
    from_version: str,
    to_version: str,
) -> BoundEnvironmentChange:
    diagnosis = _build_diagnosis(
        procedure,
        category=FailureCategory.ENVIRONMENT,
        conclusion=DiagnosticConclusion.UNKNOWN,
        at=at,
    ).diagnosis
    scope = KnowledgeScope(
        dimensions={
            ScopeDimension.OPERATING_SYSTEM: "windows",
            ScopeDimension.APPLICATION: "contoso-inventory",
            ScopeDimension.APPLICATION_VERSION: to_version,
        }
    )
    fact = EnvironmentFactKey(
        kind=EnvironmentFactKind.APPLICATION_VERSION, subject="contoso-inventory"
    )
    prov = ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="integration-env")
    baseline = EnvironmentSnapshot(
        scope=scope,
        evidence=EvidenceReference(
            kind=EvidenceKind.OBSERVATION,
            reference="baseline-app-version",
            provenance=prov,
            observed_at=at - timedelta(hours=1),
        ),
        observations=(
            EnvironmentObservation(
                fact=fact,
                value=EnvironmentFactValue(kind=EnvironmentFactValueKind.TEXT, text=from_version),
                observed_at=at - timedelta(hours=1),
                ttl=_TTL,
                provenance=prov,
            ),
        ),
    )
    current = EnvironmentSnapshot(
        scope=scope,
        evidence=EvidenceReference(
            kind=EvidenceKind.OBSERVATION,
            reference="current-app-version",
            provenance=prov,
            observed_at=at - timedelta(minutes=5),
        ),
        observations=(
            EnvironmentObservation(
                fact=fact,
                value=EnvironmentFactValue(kind=EnvironmentFactValueKind.TEXT, text=to_version),
                observed_at=at - timedelta(minutes=5),
                ttl=_TTL,
                provenance=prov,
            ),
        ),
    )
    detection = detect_environment_change(
        diagnosis=diagnosis,
        scope=scope,
        baseline=baseline,
        current=current,
        compared_at=at,
        summary="Application version changed under the procedure scope.",
    )
    assert detection.diagnosis.procedure_id == procedure.procedure_id
    return BoundEnvironmentChange(detection=detection, procedure_revision=procedure.revision)


def test_end_to_end_healthy_from_real_verified_success() -> None:
    procedure = _build_procedure(revision=1)
    success = _build_verified_success(procedure, at=_T0 + timedelta(minutes=5))

    assessment = assess_procedure_degradation(
        procedure=procedure,
        success_evidence=[success],
        assessed_at=_T0 + timedelta(hours=1),
    )

    assert assessment.procedure_id == procedure.procedure_id
    assert assessment.revision == 1
    assert assessment.state is DegradationState.HEALTHY
    assert assessment.supporting_success_indices == (0,)
    # Lifecycle untouched: status still ACTIVE, no DEGRADED invented.
    assert procedure.status is ProcedureStatus.ACTIVE
    assert not hasattr(ProcedureStatus, "DEGRADED")


def test_end_to_end_confirmed_from_repeated_node_implicated_diagnoses() -> None:
    procedure = _build_procedure(revision=2)
    f1 = _build_diagnosis(
        procedure,
        category=FailureCategory.PROCEDURE,
        conclusion=DiagnosticConclusion.NODE_IMPLICATED,
        at=_T0 + timedelta(minutes=10),
    )
    f2 = _build_diagnosis(
        procedure,
        category=FailureCategory.VERIFICATION,
        conclusion=DiagnosticConclusion.NODE_IMPLICATED,
        at=_T0 + timedelta(minutes=20),
    )

    assessment = assess_procedure_degradation(
        procedure=procedure,
        failure_diagnoses=[f1, f2],
        assessed_at=_T0 + timedelta(hours=1),
    )

    assert assessment.state is DegradationState.CONFIRMED_DEGRADED
    assert assessment.supporting_failure_indices == (0, 1)
    assert f1.diagnosis.procedure_id == procedure.procedure_id
    assert f2.diagnosis.localization.procedure_id == procedure.procedure_id
    assert procedure.status is ProcedureStatus.ACTIVE


def test_end_to_end_env_change_plus_node_implication_confirms() -> None:
    procedure = _build_procedure(revision=1)
    strong = _build_diagnosis(
        procedure,
        category=FailureCategory.PROCEDURE,
        conclusion=DiagnosticConclusion.NODE_IMPLICATED,
        at=_T0 + timedelta(minutes=15),
    )
    env = _build_env_change(
        procedure,
        at=_T0 + timedelta(minutes=30),
        from_version="3.1.0",
        to_version="4.0.0",
    )
    assert env.detection.result is EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED
    assert env.detection.diagnosis.procedure_id == procedure.procedure_id

    assessment = assess_procedure_degradation(
        procedure=procedure,
        failure_diagnoses=[strong],
        environment_changes=[env],
        assessed_at=_T0 + timedelta(hours=2),
    )

    assert assessment.state is DegradationState.CONFIRMED_DEGRADED
    assert assessment.supporting_failure_indices == (0,)
    assert assessment.supporting_environment_indices == (0,)


def test_end_to_end_revision_isolation_across_real_records() -> None:
    """Revision N failures never degrade revision N+1 without explicit linkage."""
    pid = ProcedureId.create()
    rev1 = ProcedureRecord(
        procedure_id=pid,
        revision=1,
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content='{"r":1}'),
        created_at=_T0,
        status=ProcedureStatus.RETIRED,
    )
    rev2 = ProcedureRecord(
        procedure_id=pid,
        revision=2,
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content='{"r":2}'),
        created_at=_T0 + timedelta(days=1),
        status=ProcedureStatus.ACTIVE,
    )

    old_failures = [
        _build_diagnosis(
            rev1,
            category=FailureCategory.PROCEDURE,
            conclusion=DiagnosticConclusion.NODE_IMPLICATED,
            at=_T0 + timedelta(minutes=10),
        ),
        _build_diagnosis(
            rev1,
            category=FailureCategory.PROCEDURE,
            conclusion=DiagnosticConclusion.NODE_IMPLICATED,
            at=_T0 + timedelta(minutes=20),
        ),
    ]
    # Bindings still carry revision 1 even if fed against rev2.
    assert all(f.procedure_revision == 1 for f in old_failures)

    assessment_rev2 = assess_procedure_degradation(
        procedure=rev2,
        failure_diagnoses=old_failures,
        assessed_at=_T0 + timedelta(days=2),
    )
    assert assessment_rev2.revision == 2
    assert assessment_rev2.state is DegradationState.UNKNOWN
    assert assessment_rev2.ignored_failure_indices == (0, 1)

    assessment_rev1 = assess_procedure_degradation(
        procedure=rev1,
        failure_diagnoses=old_failures,
        assessed_at=_T0 + timedelta(days=2),
    )
    assert assessment_rev1.revision == 1
    assert assessment_rev1.state is DegradationState.CONFIRMED_DEGRADED


def test_end_to_end_unrelated_procedure_evidence_never_matches() -> None:
    target = _build_procedure()
    other = _build_procedure()
    assert target.procedure_id != other.procedure_id

    foreign_failures = [
        _build_diagnosis(
            other,
            category=FailureCategory.PROCEDURE,
            conclusion=DiagnosticConclusion.NODE_IMPLICATED,
            at=_T0 + timedelta(minutes=10),
        ),
        _build_diagnosis(
            other,
            category=FailureCategory.PROCEDURE,
            conclusion=DiagnosticConclusion.NODE_IMPLICATED,
            at=_T0 + timedelta(minutes=20),
        ),
    ]
    foreign_success = _build_verified_success(other, at=_T0)
    foreign_env = _build_env_change(
        other, at=_T0 + timedelta(minutes=30), from_version="1.0", to_version="2.0"
    )

    assessment = assess_procedure_degradation(
        procedure=target,
        failure_diagnoses=foreign_failures,
        success_evidence=[foreign_success],
        environment_changes=[foreign_env],
        assessed_at=_T0 + timedelta(hours=3),
    )

    assert assessment.state is DegradationState.UNKNOWN
    assert assessment.ignored_failure_indices == (0, 1)
    assert assessment.ignored_success_indices == (0,)
    assert assessment.ignored_environment_indices == (0,)
    assert assessment.supporting_failure_indices == ()


def test_end_to_end_success_then_later_failures_on_same_revision() -> None:
    procedure = _build_procedure(revision=1)
    success = _build_verified_success(procedure, at=_T0)
    later_failures = [
        _build_diagnosis(
            procedure,
            category=FailureCategory.PROCEDURE,
            conclusion=DiagnosticConclusion.NODE_IMPLICATED,
            at=_T0 + timedelta(hours=2),
        ),
        _build_diagnosis(
            procedure,
            category=FailureCategory.UI_CHANGE,
            conclusion=DiagnosticConclusion.NODE_IMPLICATED,
            at=_T0 + timedelta(hours=3),
        ),
    ]

    assessment = assess_procedure_degradation(
        procedure=procedure,
        success_evidence=[success],
        failure_diagnoses=later_failures,
        assessed_at=_T0 + timedelta(hours=4),
    )

    assert assessment.state is DegradationState.CONFIRMED_DEGRADED
    assert assessment.supporting_success_indices == (0,)
    assert assessment.supporting_failure_indices == (0, 1)
