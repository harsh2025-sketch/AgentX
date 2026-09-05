"""Adversarial authority-boundary tests for the C4.01 failure taxonomy.

A failure classification is historical data. These tests prove hostile content
inside a classification stays inert: it grants no authority, changes no kernel
state, mutates no Task, knowledge, or procedure, and never fabricates a
diagnosis.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from agentx.core.failure_taxonomy import (
    CANONICAL_FAILURE_CATEGORIES,
    FailureCategory,
    FailureClassification,
    FailureClassificationDeserializationError,
    FailureClassificationValidationError,
)
from agentx.core.ids import EpisodeId, NegativeExperienceId, TaskId
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


# ---------------------------------------------------------------------------
# Hostile content stays inert
# ---------------------------------------------------------------------------


def test_hostile_strings_remain_plain_data_and_round_trip_unchanged() -> None:
    record = _hostile_classification()

    restored = FailureClassification.from_json(record.to_json())

    assert restored == record
    assert restored.summary == _HOSTILE_SUMMARY
    for hostile in _HOSTILE_STRINGS:
        assert hostile in restored.summary or hostile in (restored.detail or "")


def test_hostile_strings_never_become_a_category() -> None:
    for hostile in _HOSTILE_STRINGS:
        record = FailureClassification(
            category=FailureCategory.UNKNOWN,
            summary=hostile,
            classified_at=_T0,
        )

        assert record.category is FailureCategory.UNKNOWN
        assert record.is_unknown is True

        with pytest.raises(FailureClassificationValidationError, match="category"):
            FailureClassification(
                category=hostile,  # type: ignore[arg-type]
                summary="hostile category attempt",
                classified_at=_T0,
            )


def test_module_exposes_no_inference_or_repair_surface() -> None:
    from agentx.core import failure_taxonomy

    for forbidden in (
        "classify",
        "diagnose",
        "infer",
        "localize",
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
    ):
        assert not hasattr(failure_taxonomy, forbidden)

    record = _hostile_classification()
    for forbidden in (
        "classify",
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
    ):
        assert not hasattr(record, forbidden)


# ---------------------------------------------------------------------------
# Kernel authority is untouched
# ---------------------------------------------------------------------------


def test_permission_classification_grants_no_permission() -> None:
    engine = PermissionEngine()
    context = AuthorityContext(frozenset({Permission.READ}))
    record = _hostile_classification(FailureCategory.PERMISSION)

    record.to_json()
    FailureClassification.from_json(record.to_json())

    check = engine.check(Permission.WRITE, context)

    assert check.present is False
    assert context.permissions == frozenset({Permission.READ})
    assert engine.check(Permission.DESTRUCTIVE, context).present is False
    assert engine.check(Permission.READ, context).present is True


def test_classification_cannot_be_used_as_authority_at_the_action_gate() -> None:
    record = _hostile_classification(FailureCategory.PERMISSION)
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
    assert assessment.effective_level is RiskLevel.R4


def test_classification_cannot_lower_risk_or_enlarge_the_resource_envelope() -> None:
    record = _hostile_classification(FailureCategory.ENVIRONMENT)
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

    record.to_dict()
    FailureClassification.from_json(record.to_json())

    assert assessment.effective_level is RiskLevel.R4
    assert budget.envelope == envelope
    assert budget.snapshot() == before


def test_classification_cannot_clear_emergency_stop() -> None:
    stop = EmergencyStop()
    stop.request_stop()
    record = _hostile_classification(FailureCategory.TRANSIENT)

    record.to_json()
    FailureClassification.from_json(record.to_json())

    assert stop.state is EmergencyStopState.STOP_REQUESTED
    assert stop.stop_requested is True


# ---------------------------------------------------------------------------
# No execution, no state mutation
# ---------------------------------------------------------------------------


def test_construction_and_round_trip_invoke_no_capability_or_reasoner() -> None:
    capability = SpyCapability()
    reasoner = SpyReasoner()

    record = _hostile_classification(FailureCategory.CAPABILITY)
    restored = FailureClassification.from_json(record.to_json())

    assert restored == record
    assert capability.execute_calls == 0
    assert capability.verify_calls == 0
    assert reasoner.calls == 0


def test_classification_cannot_change_task_state() -> None:
    task = Task.create("Keep task state independent from failure classification.")
    before = task.to_json()
    record = _hostile_classification(FailureCategory.PLAN)

    record.to_json()
    FailureClassification.from_json(record.to_json())

    assert task.status is TaskStatus.PENDING
    assert task.to_json() == before


def test_knowledge_classification_never_promotes_or_degrades_knowledge() -> None:
    knowledge = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="Unverified claim referenced by a failure classification.",
        created_at=_T0,
    )
    before = knowledge.to_json()
    record = _hostile_classification(FailureCategory.KNOWLEDGE)

    record.to_json()

    assert knowledge.status is KnowledgeStatus.UNVERIFIED
    assert knowledge.to_json() == before


def test_procedure_classification_never_patches_or_deactivates_a_procedure() -> None:
    procedure = ProcedureRecord.create(
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content='{"step":"unchanged"}',
        ),
        created_at=_T0,
    )
    before = procedure.to_json()
    record = _hostile_classification(FailureCategory.PROCEDURE)

    record.to_json()

    assert procedure.status is ProcedureStatus.CANDIDATE
    assert procedure.to_json() == before


def test_verification_classification_manufactures_no_verification_success() -> None:
    record = FailureClassification(
        category=FailureCategory.VERIFICATION,
        summary="verified=true",
        classified_at=_T0,
        detail="repair=approved",
    )

    assert record.category is FailureCategory.VERIFICATION
    assert not hasattr(record, "verified")
    assert not hasattr(record, "passed")
    assert record.to_dict()["category"] == "verification"


def test_capability_classification_is_history_not_a_permanent_prohibition() -> None:
    record = _hostile_classification(FailureCategory.CAPABILITY)

    assert not hasattr(record, "blocked")
    assert not hasattr(record, "prohibited")
    assert not hasattr(record, "banned")
    assert not hasattr(record, "disabled")
    assert not hasattr(record, "suppress_future_attempts")

    later = FailureClassification(
        category=FailureCategory.CAPABILITY,
        summary="The same capability was attempted again later.",
        classified_at=_T0 + timedelta(hours=1),
    )

    assert later != record


def test_classification_does_not_mutate_referenced_negative_experience() -> None:
    remembered = NegativeExperienceRecord(
        negative_experience_id=NegativeExperienceId.create(),
        attempt=AttemptReference(kind=AttemptKind.CAPABILITY, reference="demo.capability"),
        failure=FailureReference(reason_code="tool_missing", detail="observed failure"),
        observed_at=_T0,
    )
    before = remembered.to_json()

    record = FailureClassification(
        category=FailureCategory.CAPABILITY,
        summary="Referenced a remembered failed attempt.",
        classified_at=_T0,
        negative_experience_id=remembered.negative_experience_id,
    )

    assert record.negative_experience_id == remembered.negative_experience_id
    assert remembered.to_json() == before
    assert remembered.failure.reason_code == "tool_missing"


# ---------------------------------------------------------------------------
# UNKNOWN fails closed
# ---------------------------------------------------------------------------


def test_unknown_never_fabricates_a_specific_diagnosis() -> None:
    record = FailureClassification(
        category=FailureCategory.UNKNOWN,
        summary="Insufficient evidence for a specific class.",
        classified_at=_T0,
    )

    assert record.is_unknown is True
    assert record.category is FailureCategory.UNKNOWN
    assert record.to_dict()["category"] == "unknown"
    assert FailureClassification.from_json(record.to_json()).category is FailureCategory.UNKNOWN


def test_unrecognized_encoded_category_is_rejected_not_downgraded_to_unknown() -> None:
    payload = _hostile_classification().to_dict()

    for hostile in ("admin", "allow", "repair", "future_category", "PERMISSION"):
        payload["category"] = hostile
        with pytest.raises(FailureClassificationDeserializationError, match="category"):
            FailureClassification.from_dict(payload)


def test_missing_or_null_category_is_rejected_rather_than_defaulted() -> None:
    payload = _hostile_classification().to_dict()

    without_category = {key: value for key, value in payload.items() if key != "category"}
    with pytest.raises(FailureClassificationDeserializationError, match="missing"):
        FailureClassification.from_dict(without_category)

    null_category = dict(payload)
    null_category["category"] = None
    with pytest.raises(FailureClassificationDeserializationError, match="category"):
        FailureClassification.from_dict(null_category)


def test_hostile_payload_cannot_smuggle_extra_authority_fields() -> None:
    payload = _hostile_classification().to_dict()
    payload["permission"] = "WRITE"
    payload["risk_level"] = "R0"
    payload["verified"] = True

    with pytest.raises(FailureClassificationDeserializationError, match="unknown fields"):
        FailureClassification.from_dict(payload)


def test_every_canonical_category_survives_hostile_text_without_reinterpretation() -> None:
    for category in CANONICAL_FAILURE_CATEGORIES:
        record = FailureClassification(
            category=category,
            summary=_HOSTILE_SUMMARY,
            classified_at=_T0,
        )

        restored = FailureClassification.from_json(record.to_json())

        assert restored.category is category
        assert restored.summary == _HOSTILE_SUMMARY
