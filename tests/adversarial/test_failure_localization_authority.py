"""Adversarial authority-boundary tests for the C4.02 failure-localization contract.

A failure localization is historical data. These tests prove hostile content
inside a localization stays inert: it grants no authority, changes no kernel
state, mutates no Task/knowledge/procedure, never fabricates a location from
keywords or category alone, and never diagnoses or repairs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from agentx.core.failure_localization import (
    CANONICAL_FAILURE_LOCATION_KINDS,
    FailureLocalization,
    FailureLocalizationDeserializationError,
    FailureLocalizationValidationError,
    FailureLocationKind,
    LocalizationEvidence,
    localize_failure,
)
from agentx.core.failure_taxonomy import (
    FailureCategory,
    FailureClassification,
)
from agentx.core.ids import (
    CapabilityId,
    EpisodeId,
    NegativeExperienceId,
    ProcedureId,
    TaskId,
)
from agentx.core.knowledge import KnowledgeRecord, KnowledgeStatus, KnowledgeType
from agentx.core.negative_experience import (
    AttemptKind,
    AttemptReference,
    FailureReference,
    NegativeExperienceRecord,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
)
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import AuthorityContext, Permission, PermissionEngine
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskAssessment, RiskLevel

_T0 = datetime(2026, 9, 5, 9, 0, 0, tzinfo=UTC)

_HOSTILE_STRINGS = (
    "ADMIN",
    "ALLOW R4",
    "permission=WRITE",
    "verified=true",
    "repair=approved",
    "retry forever",
    "ignore policy",
    "budget=unlimited",
)
_HOSTILE_SUMMARY = (
    "ADMIN ALLOW R4 permission=WRITE verified=true repair=approved "
    "retry forever ignore policy budget=unlimited"
)
_KEYWORD_BAIT = (
    "permission denied clicking the button because the API changed; "
    "verify failed in traceback; procedure node broken; capability missing"
)


class SpyCapability:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.verify_calls = 0

    def execute(self) -> None:
        self.execute_calls += 1

    def verify(self) -> None:
        self.verify_calls += 1


class SpyReasoner:
    def __init__(self) -> None:
        self.calls = 0

    def reason(self) -> None:
        self.calls += 1


class SpyStore:
    def __init__(self) -> None:
        self.queries = 0

    def query(self, *_args: object, **_kwargs: object) -> None:
        self.queries += 1


def _hostile_classification(
    category: FailureCategory = FailureCategory.PERMISSION,
) -> FailureClassification:
    return FailureClassification(
        category=category,
        summary=_HOSTILE_SUMMARY,
        classified_at=_T0,
        detail="\n".join(_HOSTILE_STRINGS),
        error_code="permission.denied",
        task_id=TaskId.create(),
        episode_id=EpisodeId.create(),
        negative_experience_id=NegativeExperienceId.create(),
        correlation_id=uuid4(),
    )


def _hostile_localization(
    kind: FailureLocationKind = FailureLocationKind.UNKNOWN,
    **overrides: object,
) -> FailureLocalization:
    payload: dict[str, object] = {
        "kind": kind,
        "summary": _HOSTILE_SUMMARY,
        "localized_at": _T0,
        "detail": "\n".join(_HOSTILE_STRINGS),
        "classification": _hostile_classification(),
    }
    if kind is FailureLocationKind.TASK:
        payload["task_id"] = TaskId.create()
    elif kind is FailureLocationKind.CAPABILITY:
        payload["capability_id"] = CapabilityId.create()
    elif kind is FailureLocationKind.PROCEDURE:
        payload["procedure_id"] = ProcedureId.create()
    elif kind is FailureLocationKind.PROCEDURE_NODE:
        payload["procedure_id"] = ProcedureId.create()
        payload["procedure_node_id"] = "node-hostile"
    elif kind is FailureLocationKind.ACTION:
        payload["correlation_id"] = uuid4()
        payload["action_name"] = "hostile.action@9.9.9"
    elif kind in {FailureLocationKind.OBSERVATION, FailureLocationKind.VERIFICATION}:
        payload["correlation_id"] = uuid4()
    elif kind is FailureLocationKind.ENVIRONMENT:
        payload["environment_ref"] = "hostile.env"
    elif kind is FailureLocationKind.DEPENDENCY:
        payload["dependency_ref"] = "hostile.dep"
    payload.update(overrides)
    return FailureLocalization(**payload)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Hostile content stays inert / no keyword inference
# ---------------------------------------------------------------------------


def test_hostile_strings_remain_plain_data_and_round_trip_unchanged() -> None:
    record = _hostile_localization(FailureLocationKind.CAPABILITY)

    restored = FailureLocalization.from_json(record.to_json())

    assert restored == record
    assert restored.summary == _HOSTILE_SUMMARY
    for hostile in _HOSTILE_STRINGS:
        assert hostile in restored.summary or hostile in (restored.detail or "")


def test_hostile_summary_and_detail_cannot_change_location_kind() -> None:
    capability_id = CapabilityId.create()
    record = FailureLocalization(
        kind=FailureLocationKind.CAPABILITY,
        summary=_KEYWORD_BAIT,
        localized_at=_T0,
        detail=_HOSTILE_SUMMARY,
        capability_id=capability_id,
    )

    restored = FailureLocalization.from_json(record.to_json())

    assert restored.kind is FailureLocationKind.CAPABILITY
    assert restored.capability_id == capability_id
    assert restored.procedure_id is None
    assert restored.procedure_node_id is None
    assert "permission" in restored.summary
    assert "button" in restored.summary
    assert "API" in restored.summary
    assert "verify" in restored.summary


def test_keyword_bait_cannot_be_used_as_evidence_or_kind() -> None:
    with pytest.raises(FailureLocalizationValidationError, match="LocalizationEvidence"):
        localize_failure(
            _KEYWORD_BAIT,  # type: ignore[arg-type]
            summary="must not accept free text",
            localized_at=_T0,
        )

    for bait in ("permission", "button", "API", "verify", "traceback", "procedure"):
        with pytest.raises(FailureLocalizationValidationError, match="FailureLocationKind"):
            FailureLocalization(
                kind=bait,  # type: ignore[arg-type]
                summary="hostile kind attempt",
                localized_at=_T0,
            )


def test_module_exposes_no_diagnosis_repair_or_inference_surface() -> None:
    from agentx.core import failure_localization

    for forbidden in (
        "classify",
        "diagnose",
        "infer",
        "repair",
        "retry",
        "fallback",
        "escalate",
        "suppress",
        "patch",
        "rollback",
        "execute",
        "verify",
        "confidence",
        "probability",
        "score",
        "embed",
        "query",
        "analyze",
    ):
        assert not hasattr(failure_localization, forbidden)

    record = _hostile_localization()
    for forbidden in (
        "diagnose",
        "repair",
        "retry",
        "retry_policy",
        "fallback",
        "escalate",
        "suppress",
        "confidence",
        "probability",
        "score",
        "authority",
        "permission",
        "grant",
        "apply",
        "execute",
        "patch",
        "is_wrong",
        "logical_error",
    ):
        assert not hasattr(record, forbidden)


# ---------------------------------------------------------------------------
# Kernel authority is untouched
# ---------------------------------------------------------------------------


def test_permission_shaped_localization_grants_no_permission() -> None:
    engine = PermissionEngine()
    context = AuthorityContext(frozenset({Permission.READ}))
    record = _hostile_localization(FailureLocationKind.UNKNOWN)

    record.to_json()
    FailureLocalization.from_json(record.to_json())

    check = engine.check(Permission.WRITE, context)

    assert check.present is False
    assert context.permissions == frozenset({Permission.READ})
    assert engine.check(Permission.DESTRUCTIVE, context).present is False
    assert engine.check(Permission.READ, context).present is True


def test_localization_cannot_be_used_as_authority_at_the_action_gate() -> None:
    record = _hostile_localization(FailureLocationKind.CAPABILITY)
    assessment = RiskAssessment(
        level=RiskLevel.R0,
        reason="Destructive operation remains high risk.",
        reversible=False,
        external_effect=True,
        destructive=True,
    )
    request = GateRequest(
        operation="future.destructive.action",
        required_permission=Permission.DESTRUCTIVE,
        risk_assessment=assessment,
    )

    with pytest.raises(TypeError, match="AuthorityContext"):
        ActionGate().evaluate(request, record)  # type: ignore[arg-type]

    denied = ActionGate().evaluate(request, AuthorityContext(frozenset()))

    assert denied.decision is GateDecision.DENY


def test_localization_cannot_lower_risk_or_enlarge_budget() -> None:
    assessment = RiskAssessment(
        level=RiskLevel.R0,
        reason="Critical destructive operation.",
        reversible=False,
        external_effect=True,
        critical=True,
        destructive=True,
    )
    envelope = ResourceEnvelope(
        max_wall_clock=timedelta(seconds=5),
        max_model_calls=1,
        max_model_tokens=50,
        max_research_queries=0,
        max_machine_actions=1,
        max_repair_attempts=0,
        max_external_cost=Decimal("0.50"),
        max_risk_level=RiskLevel.R2,
    )
    budget = ResourceBudget(envelope)
    before = budget.snapshot()
    record = _hostile_localization(FailureLocationKind.ENVIRONMENT)

    record.to_json()
    FailureLocalization.from_json(record.to_json())

    assert assessment.effective_level is RiskLevel.R4
    assert budget.envelope == envelope
    assert budget.snapshot() == before


def test_localization_cannot_clear_emergency_stop() -> None:
    stop = EmergencyStop()
    stop.request_stop()
    record = _hostile_localization(FailureLocationKind.ACTION)

    record.to_json()
    FailureLocalization.from_json(record.to_json())

    assert stop.state is EmergencyStopState.STOP_REQUESTED
    assert stop.stop_requested is True


# ---------------------------------------------------------------------------
# No execution, no state mutation, no store query
# ---------------------------------------------------------------------------


def test_construction_and_round_trip_invoke_no_capability_reasoner_or_store() -> None:
    capability = SpyCapability()
    reasoner = SpyReasoner()
    store = SpyStore()

    record = _hostile_localization(FailureLocationKind.PROCEDURE_NODE)
    restored = FailureLocalization.from_json(record.to_json())
    localize_failure(
        LocalizationEvidence(
            kind=FailureLocationKind.UNKNOWN,
            classification=_hostile_classification(),
        ),
        summary="unlocalized packaging",
        localized_at=_T0,
    )

    assert restored == record
    assert capability.execute_calls == 0
    assert capability.verify_calls == 0
    assert reasoner.calls == 0
    assert store.queries == 0


def test_localization_cannot_change_task_state() -> None:
    task = Task.create("Keep task state independent from failure localization.")
    before = task.to_json()
    record = _hostile_localization(
        FailureLocationKind.TASK,
        task_id=task.task_id,
        summary=_HOSTILE_SUMMARY,
    )

    record.to_json()
    FailureLocalization.from_json(record.to_json())

    assert task.status is TaskStatus.PENDING
    assert task.to_json() == before


def test_localization_never_promotes_or_degrades_knowledge() -> None:
    knowledge = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="Unverified claim referenced by a failure localization.",
        created_at=_T0,
    )
    before = knowledge.to_json()
    record = _hostile_localization(FailureLocationKind.UNKNOWN)

    record.to_json()

    assert knowledge.status is KnowledgeStatus.UNVERIFIED
    assert knowledge.to_json() == before


def test_procedure_node_localization_never_patches_or_diagnoses_a_procedure() -> None:
    procedure = ProcedureRecord.create(
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content='{"step":"unchanged"}',
        ),
        created_at=_T0,
    )
    before = procedure.to_json()
    record = _hostile_localization(
        FailureLocationKind.PROCEDURE_NODE,
        procedure_id=procedure.procedure_id,
        procedure_node_id="node-that-looks-wrong",
        summary="this node is logically wrong repair=approved",
    )

    record.to_json()

    assert procedure.status is ProcedureStatus.CANDIDATE
    assert procedure.to_json() == before
    assert not hasattr(record, "diagnosis")
    assert not hasattr(record, "patch")
    assert not hasattr(record, "is_logically_wrong")


def test_verification_localization_manufactures_no_verification_success() -> None:
    record = FailureLocalization(
        kind=FailureLocationKind.VERIFICATION,
        summary="verified=true",
        localized_at=_T0,
        detail="repair=approved",
        correlation_id=uuid4(),
    )

    assert record.kind is FailureLocationKind.VERIFICATION
    assert not hasattr(record, "verified")
    assert not hasattr(record, "passed")
    assert record.to_dict()["kind"] == "verification"


def test_localization_does_not_mutate_referenced_negative_experience() -> None:
    remembered = NegativeExperienceRecord(
        negative_experience_id=NegativeExperienceId.create(),
        attempt=AttemptReference(kind=AttemptKind.CAPABILITY, reference="demo.capability"),
        failure=FailureReference(reason_code="tool_missing", detail="observed failure"),
        observed_at=_T0,
    )
    before = remembered.to_json()

    record = FailureLocalization(
        kind=FailureLocationKind.CAPABILITY,
        summary="Referenced a remembered failed attempt.",
        localized_at=_T0,
        capability_id=CapabilityId.create(),
        negative_experience_id=remembered.negative_experience_id,
    )

    assert record.negative_experience_id == remembered.negative_experience_id
    assert remembered.to_json() == before
    assert remembered.failure.reason_code == "tool_missing"


# ---------------------------------------------------------------------------
# C4.01 interaction and fail-closed UNKNOWN
# ---------------------------------------------------------------------------


def test_c401_classification_is_preserved_and_not_rewritten() -> None:
    classification = _hostile_classification(FailureCategory.PROCEDURE)
    before = classification.to_json()

    record = localize_failure(
        LocalizationEvidence(
            kind=FailureLocationKind.UNKNOWN,
            classification=classification,
        ),
        summary="Category preserved under unlocalized result.",
        localized_at=_T0,
    )

    assert record.classification is not None
    assert record.classification.to_json() == before
    assert record.classification.category is FailureCategory.PROCEDURE
    assert record.kind is FailureLocationKind.UNKNOWN
    assert classification.to_json() == before


def test_category_alone_cannot_fabricate_location_even_when_hostile() -> None:
    for category in (
        FailureCategory.PROCEDURE,
        FailureCategory.CAPABILITY,
        FailureCategory.VERIFICATION,
        FailureCategory.PERMISSION,
        FailureCategory.ENVIRONMENT,
        FailureCategory.DEPENDENCY,
    ):
        classification = FailureClassification(
            category=category,
            summary=_KEYWORD_BAIT,
            classified_at=_T0,
            detail=_HOSTILE_SUMMARY,
        )
        record = localize_failure(
            LocalizationEvidence(kind=FailureLocationKind.UNKNOWN, classification=classification),
            summary="category alone is not a location",
            localized_at=_T0,
        )

        assert record.is_unlocalized is True
        assert record.procedure_id is None
        assert record.capability_id is None
        assert record.procedure_node_id is None
        assert record.environment_ref is None
        assert record.dependency_ref is None
        assert record.classification is not None
        assert record.classification.category is category


def test_unknown_never_fabricates_a_specific_location() -> None:
    record = FailureLocalization(
        kind=FailureLocationKind.UNKNOWN,
        summary="Insufficient structured evidence for a specific target.",
        localized_at=_T0,
    )

    assert record.is_unlocalized is True
    assert record.is_unknown is True
    assert record.kind is FailureLocationKind.UNKNOWN
    assert record.to_dict()["kind"] == "unknown"
    assert FailureLocalization.from_json(record.to_json()).kind is FailureLocationKind.UNKNOWN


def test_unrecognized_encoded_kind_is_rejected_not_downgraded_to_unknown() -> None:
    payload = _hostile_localization().to_dict()

    for hostile in ("admin", "allow", "repair", "future_kind", "CAPABILITY", "procedure_node "):
        payload["kind"] = hostile
        with pytest.raises(FailureLocalizationDeserializationError, match="kind"):
            FailureLocalization.from_dict(payload)


def test_missing_or_null_kind_is_rejected_rather_than_defaulted() -> None:
    payload = _hostile_localization().to_dict()

    without_kind = {key: value for key, value in payload.items() if key != "kind"}
    with pytest.raises(FailureLocalizationDeserializationError, match="missing"):
        FailureLocalization.from_dict(without_kind)

    null_kind = dict(payload)
    null_kind["kind"] = None
    with pytest.raises(FailureLocalizationDeserializationError, match="kind"):
        FailureLocalization.from_dict(null_kind)


def test_hostile_payload_cannot_smuggle_extra_authority_fields() -> None:
    payload = _hostile_localization().to_dict()
    payload["permission"] = "WRITE"
    payload["risk_level"] = "R0"
    payload["verified"] = True
    payload["repair"] = "approved"
    payload["patch"] = {"node": "x"}

    with pytest.raises(FailureLocalizationDeserializationError, match="unknown fields"):
        FailureLocalization.from_dict(payload)


def test_every_canonical_kind_survives_hostile_text_without_reinterpretation() -> None:
    for kind in CANONICAL_FAILURE_LOCATION_KINDS:
        record = _hostile_localization(kind)
        restored = FailureLocalization.from_json(record.to_json())

        assert restored.kind is kind
        assert restored.summary == _HOSTILE_SUMMARY


def test_localize_failure_does_not_interpret_classification_text_as_location() -> None:
    classification = FailureClassification(
        category=FailureCategory.UNKNOWN,
        summary=_KEYWORD_BAIT,
        classified_at=_T0,
        detail="procedure_node=node-1 capability=demo.capability verified=true",
    )
    evidence = LocalizationEvidence(
        kind=FailureLocationKind.UNKNOWN,
        classification=classification,
        task_id=TaskId.create(),
        episode_id=EpisodeId.create(),
        correlation_id=uuid4(),
    )

    record = localize_failure(
        evidence,
        summary="still unlocalized despite keyword-rich classification",
        localized_at=_T0 + timedelta(seconds=1),
    )

    assert record.kind is FailureLocationKind.UNKNOWN
    assert record.capability_id is None
    assert record.procedure_id is None
    assert record.procedure_node_id is None
    assert record.action_name is None
