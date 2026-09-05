"""Unit tests for the C4.03 canonical procedure-node failure-diagnosis contract."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from agentx.core.failure_diagnosis import (
    CANONICAL_DIAGNOSTIC_CONCLUSIONS,
    CANONICAL_DIAGNOSTIC_EVIDENCE_KINDS,
    FAILURE_DIAGNOSIS_SCHEMA_VERSION,
    DiagnosticConclusion,
    DiagnosticEvidence,
    DiagnosticEvidenceKind,
    FailureDiagnosis,
    FailureDiagnosisDeserializationError,
    FailureDiagnosisValidationError,
    UnsupportedFailureDiagnosisSchemaVersionError,
    package_diagnosis,
)
from agentx.core.failure_localization import (
    FailureLocalization,
    FailureLocalizationValidationError,
    FailureLocationKind,
)
from agentx.core.failure_taxonomy import (
    CANONICAL_FAILURE_CATEGORIES,
    FailureCategory,
    FailureClassification,
)
from agentx.core.ids import (
    EpisodeId,
    NegativeExperienceId,
    ProcedureId,
    TaskId,
)
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureEdgeKind,
    ProcedureGraph,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)

_NOW = datetime(2026, 9, 5, 12, 30, 15, 123456, tzinfo=UTC)

_EXPECTED_CONCLUSION_VALUES = ("unknown", "node_implicated")
_EXPECTED_EVIDENCE_KIND_VALUES = (
    "canonical_error",
    "negative_experience",
    "chain_correlation",
    "episode",
    "task",
)


def _classification(**overrides: object) -> FailureClassification:
    payload: dict[str, object] = {
        "category": FailureCategory.PROCEDURE,
        "summary": "Observed a procedure-class failure.",
        "classified_at": _NOW,
    }
    payload.update(overrides)
    return FailureClassification(**payload)  # type: ignore[arg-type]


def _localization(**overrides: object) -> FailureLocalization:
    payload: dict[str, object] = {
        "kind": FailureLocationKind.PROCEDURE_NODE,
        "summary": "Evidence points at a procedure node.",
        "localized_at": _NOW,
        "procedure_id": ProcedureId.create(),
        "procedure_node_id": "node-1",
    }
    payload.update(overrides)
    return FailureLocalization(**payload)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> DiagnosticEvidence:
    payload: dict[str, object] = {
        "kind": DiagnosticEvidenceKind.CHAIN_CORRELATION,
        "correlation_id": uuid4(),
    }
    payload.update(overrides)
    return DiagnosticEvidence(**payload)  # type: ignore[arg-type]


def _diagnosis(**overrides: object) -> FailureDiagnosis:
    payload: dict[str, object] = {
        "classification": _classification(),
        "localization": _localization(),
        "summary": "Evidence-only diagnosis: no conclusion is asserted.",
        "diagnosed_at": _NOW,
    }
    payload.update(overrides)
    return FailureDiagnosis(**payload)  # type: ignore[arg-type]


def _nested_payload(payload: Mapping[str, object], key: str) -> dict[str, object]:
    """Return a fresh mutable copy of a nested JSON-object payload field."""
    raw = payload[key]
    if not isinstance(raw, Mapping):
        raise AssertionError(f"{key} must be a nested mapping")
    return {field: raw[field] for field in raw}


def _real_graph() -> tuple[ProcedureGraph, ProcedureNodeId]:
    """A small real A3.01 graph whose node ids C4.03 records must interoperate with."""
    nodes = (
        ProcedureNode(ProcedureNodeId("collect"), ProcedureNodeKind.ACTION, label="Collect"),
        ProcedureNode(ProcedureNodeId("check"), ProcedureNodeKind.VERIFY, label="Check"),
        ProcedureNode(ProcedureNodeId("finish"), ProcedureNodeKind.END, label="Finish"),
    )
    edges = (
        ProcedureEdge(ProcedureNodeId("collect"), ProcedureNodeId("check"), ProcedureEdgeKind.NEXT),
        ProcedureEdge(ProcedureNodeId("check"), ProcedureNodeId("finish"), ProcedureEdgeKind.NEXT),
    )
    return ProcedureGraph(ProcedureNodeId("collect"), nodes, edges), ProcedureNodeId("check")


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_canonical_conclusions_are_exactly_the_architecture_vocabulary() -> None:
    assert tuple(member.value for member in DiagnosticConclusion) == _EXPECTED_CONCLUSION_VALUES
    assert tuple(DiagnosticConclusion) == CANONICAL_DIAGNOSTIC_CONCLUSIONS
    assert len(CANONICAL_DIAGNOSTIC_CONCLUSIONS) == 2


def test_conclusion_names_match_the_canonical_architecture_names() -> None:
    assert {member.name for member in DiagnosticConclusion} == {"UNKNOWN", "NODE_IMPLICATED"}


def test_canonical_evidence_kinds_are_exactly_the_architecture_vocabulary() -> None:
    assert (
        tuple(member.value for member in DiagnosticEvidenceKind) == _EXPECTED_EVIDENCE_KIND_VALUES
    )
    assert tuple(DiagnosticEvidenceKind) == CANONICAL_DIAGNOSTIC_EVIDENCE_KINDS
    assert len(CANONICAL_DIAGNOSTIC_EVIDENCE_KINDS) == 5


def test_evidence_kind_names_match_the_canonical_architecture_names() -> None:
    assert {member.name for member in DiagnosticEvidenceKind} == {
        "CANONICAL_ERROR",
        "NEGATIVE_EXPERIENCE",
        "CHAIN_CORRELATION",
        "EPISODE",
        "TASK",
    }


def test_vocabulary_values_are_unique_and_lowercase() -> None:
    assert len({member.value for member in DiagnosticConclusion}) == 2
    assert len({member.value for member in DiagnosticEvidenceKind}) == 5
    assert all(
        member.value == member.value.lower()
        for member in (*DiagnosticConclusion, *DiagnosticEvidenceKind)
    )


def test_members_carry_no_severity_policy_or_confidence_attributes() -> None:
    for member in (*DiagnosticConclusion, *DiagnosticEvidenceKind):
        for attribute in (
            "severity",
            "confidence",
            "probability",
            "score",
            "weight",
            "retryable",
            "repair",
            "remedy",
            "rank",
            "patch",
            "root_cause",
            "authority",
        ):
            assert not hasattr(member, attribute)


def test_schema_version_is_one() -> None:
    assert FAILURE_DIAGNOSIS_SCHEMA_VERSION == 1


# ---------------------------------------------------------------------------
# Explicit node evidence: every canonical evidence kind
# ---------------------------------------------------------------------------


def test_canonical_error_evidence_requires_and_reuses_canonical_error_code() -> None:
    item = DiagnosticEvidence(
        kind=DiagnosticEvidenceKind.CANONICAL_ERROR, error_code="capability.timeout"
    )

    assert item.kind is DiagnosticEvidenceKind.CANONICAL_ERROR
    assert item.error_code == "capability.timeout"
    assert item.negative_experience_id is None
    assert item.correlation_id is None
    assert item.episode_id is None
    assert item.task_id is None


def test_negative_experience_evidence_requires_canonical_c2_06_reference() -> None:
    remembered = NegativeExperienceId.create()
    item = DiagnosticEvidence(
        kind=DiagnosticEvidenceKind.NEGATIVE_EXPERIENCE, negative_experience_id=remembered
    )

    assert item.negative_experience_id == remembered
    assert item.error_code is None
    assert item.correlation_id is None


def test_chain_correlation_evidence_requires_non_nil_uuid() -> None:
    correlation = uuid4()
    item = DiagnosticEvidence(
        kind=DiagnosticEvidenceKind.CHAIN_CORRELATION, correlation_id=correlation
    )

    assert item.correlation_id == correlation
    assert item.task_id is None
    assert item.episode_id is None


def test_episode_evidence_requires_canonical_episode_id() -> None:
    episode = EpisodeId.create()
    item = DiagnosticEvidence(kind=DiagnosticEvidenceKind.EPISODE, episode_id=episode)

    assert item.episode_id == episode
    assert item.correlation_id is None


def test_task_evidence_requires_canonical_task_id() -> None:
    task = TaskId.create()
    item = DiagnosticEvidence(kind=DiagnosticEvidenceKind.TASK, task_id=task)

    assert item.task_id == task
    assert item.correlation_id is None


def test_every_evidence_kind_round_trips_through_a_diagnosis() -> None:
    task = TaskId.create()
    episode = EpisodeId.create()
    remembered = NegativeExperienceId.create()
    correlation = uuid4()
    items = (
        DiagnosticEvidence(kind=DiagnosticEvidenceKind.CANONICAL_ERROR, error_code="task.timeout"),
        DiagnosticEvidence(
            kind=DiagnosticEvidenceKind.NEGATIVE_EXPERIENCE, negative_experience_id=remembered
        ),
        DiagnosticEvidence(
            kind=DiagnosticEvidenceKind.CHAIN_CORRELATION, correlation_id=correlation
        ),
        DiagnosticEvidence(kind=DiagnosticEvidenceKind.EPISODE, episode_id=episode),
        DiagnosticEvidence(kind=DiagnosticEvidenceKind.TASK, task_id=task),
    )

    record = FailureDiagnosis(
        classification=_classification(),
        localization=_localization(),
        summary="Full explicit evidence set.",
        diagnosed_at=_NOW,
        evidence=items,
    )
    restored = FailureDiagnosis.from_json(record.to_json())

    assert restored == record
    assert len(restored.evidence) == 5
    assert restored.evidence == items
    assert restored.evidence[0].error_code == "task.timeout"
    assert restored.evidence[1].negative_experience_id == remembered
    assert restored.evidence[2].correlation_id == correlation
    assert restored.evidence[3].episode_id == episode
    assert restored.evidence[4].task_id == task


def test_evidence_order_and_duplicates_are_preserved_deterministically() -> None:
    correlation = uuid4()
    items = (
        DiagnosticEvidence(
            kind=DiagnosticEvidenceKind.CHAIN_CORRELATION, correlation_id=correlation
        ),
        DiagnosticEvidence(kind=DiagnosticEvidenceKind.CHAIN_CORRELATION, correlation_id=uuid4()),
        DiagnosticEvidence(
            kind=DiagnosticEvidenceKind.CHAIN_CORRELATION, correlation_id=correlation
        ),
    )
    record = _diagnosis(evidence=items)

    restored = FailureDiagnosis.from_json(record.to_json())

    assert [item.correlation_id for item in restored.evidence] == [
        item.correlation_id for item in items
    ]


def test_diagnosis_exposes_the_embedded_node_subject_without_duplicate_ids() -> None:
    procedure_id = ProcedureId.create()
    record = _diagnosis(localization=_localization(procedure_id=procedure_id))

    assert record.procedure_id == procedure_id
    assert record.procedure_node_id == "node-1"
    # The subject is read from the embedded localization only; the record has no
    # separate subject fields that could disagree with C4.02.
    assert record.localization.procedure_id == record.procedure_id
    assert record.localization.procedure_node_id == record.procedure_node_id


# ---------------------------------------------------------------------------
# UNKNOWN / insufficient-evidence semantics (fail closed)
# ---------------------------------------------------------------------------


def test_unknown_is_the_default_fail_closed_conclusion() -> None:
    record = _diagnosis()

    assert record.conclusion is DiagnosticConclusion.UNKNOWN
    assert record.is_unknown is True


def test_evidence_only_record_with_empty_evidence_is_representable() -> None:
    record = _diagnosis(evidence=())

    assert record.evidence == ()
    assert record.conclusion is DiagnosticConclusion.UNKNOWN
    assert record.is_unknown is True
    restored = FailureDiagnosis.from_json(record.to_json())
    assert restored == record
    assert restored.is_unknown is True


def test_evidence_present_with_unknown_conclusion_stays_unknown() -> None:
    record = _diagnosis(
        evidence=(_evidence(),),
        summary="Facts are known but no conclusion is asserted.",
    )

    assert len(record.evidence) == 1
    assert record.conclusion is DiagnosticConclusion.UNKNOWN
    assert record.is_unknown is True
    restored = FailureDiagnosis.from_json(record.to_json())
    assert restored.conclusion is DiagnosticConclusion.UNKNOWN


def test_unknown_classification_is_representable_and_preserved() -> None:
    classification = _classification(category=FailureCategory.UNKNOWN)
    record = _diagnosis(classification=classification)

    restored = FailureDiagnosis.from_json(record.to_json())

    assert restored.classification.category is FailureCategory.UNKNOWN
    assert restored.classification.is_unknown is True
    assert restored.conclusion is DiagnosticConclusion.UNKNOWN
    assert restored.is_unknown is True


def test_insufficient_evidence_never_fabricates_implication() -> None:
    record = _diagnosis(
        evidence=(),
        summary="Pointer only: correlation is not causation.",
    )

    assert record.conclusion is DiagnosticConclusion.UNKNOWN
    assert record.is_unknown is True
    assert "node_implicated" not in record.to_json()


def test_node_implicated_without_evidence_is_rejected() -> None:
    builders: tuple[Callable[[], FailureDiagnosis], ...] = (
        lambda: FailureDiagnosis(
            classification=_classification(),
            localization=_localization(),
            summary="Implication without facts is not a diagnosis.",
            diagnosed_at=_NOW,
            conclusion=DiagnosticConclusion.NODE_IMPLICATED,
        ),
        lambda: package_diagnosis(
            classification=_classification(),
            localization=_localization(),
            summary="Implication without facts is not a diagnosis.",
            diagnosed_at=_NOW,
            conclusion=DiagnosticConclusion.NODE_IMPLICATED,
        ),
    )
    for builder in builders:
        with pytest.raises(FailureDiagnosisValidationError, match="evidence"):
            builder()


def test_category_alone_is_never_evidence_of_implication() -> None:
    # Even a PROCEDURE-classification with a node pointer and hostile detail does
    # not satisfy the explicit-evidence requirement for NODE_IMPLICATED.
    with pytest.raises(FailureDiagnosisValidationError, match="evidence"):
        FailureDiagnosis(
            classification=_classification(
                detail="this node is the root cause repair=approved verified=true"
            ),
            localization=_localization(),
            summary="Category text must not count as evidence.",
            diagnosed_at=_NOW,
            conclusion=DiagnosticConclusion.NODE_IMPLICATED,
        )


def test_node_implicated_with_explicit_evidence_is_representable() -> None:
    record = _diagnosis(
        evidence=(
            DiagnosticEvidence(
                kind=DiagnosticEvidenceKind.CANONICAL_ERROR, error_code="execution.failed"
            ),
        ),
        conclusion=DiagnosticConclusion.NODE_IMPLICATED,
    )

    assert record.conclusion is DiagnosticConclusion.NODE_IMPLICATED
    assert record.is_unknown is False
    restored = FailureDiagnosis.from_json(record.to_json())
    assert restored.conclusion is DiagnosticConclusion.NODE_IMPLICATED
    assert restored == record


# ---------------------------------------------------------------------------
# Subject gate: wrong or mismatched localization fails closed
# ---------------------------------------------------------------------------


def test_unknown_localization_is_not_a_procedure_node_subject() -> None:
    with pytest.raises(FailureDiagnosisValidationError, match="PROCEDURE_NODE"):
        FailureDiagnosis(
            classification=_classification(),
            localization=FailureLocalization(
                kind=FailureLocationKind.UNKNOWN,
                summary="Not localized to any target.",
                localized_at=_NOW,
            ),
            summary="Cannot diagnose a node that was not localized.",
            diagnosed_at=_NOW,
        )


def test_non_node_localization_kinds_are_rejected_as_subjects() -> None:
    procedure_id = ProcedureId.create()
    for localization in (
        FailureLocalization(
            kind=FailureLocationKind.PROCEDURE,
            summary="Procedure-level only; no node.",
            localized_at=_NOW,
            procedure_id=procedure_id,
        ),
        FailureLocalization(
            kind=FailureLocationKind.TASK,
            summary="Task-level localization.",
            localized_at=_NOW,
            task_id=TaskId.create(),
        ),
        FailureLocalization(
            kind=FailureLocationKind.ACTION,
            summary="Action-stage localization.",
            localized_at=_NOW,
            correlation_id=uuid4(),
        ),
    ):
        with pytest.raises(FailureDiagnosisValidationError, match="PROCEDURE_NODE"):
            FailureDiagnosis(
                classification=_classification(),
                localization=localization,
                summary="Wrong subject kind.",
                diagnosed_at=_NOW,
            )


def test_localization_must_be_a_canonical_record_not_a_dict() -> None:
    payload = _localization().to_dict()

    with pytest.raises(FailureDiagnosisValidationError, match="localization"):
        FailureDiagnosis(
            classification=_classification(),
            localization=payload,  # type: ignore[arg-type]
            summary="Dicts are not canonical localizations.",
            diagnosed_at=_NOW,
        )

    with pytest.raises(FailureDiagnosisValidationError, match="localization"):
        package_diagnosis(
            classification=_classification(),
            localization=payload,  # type: ignore[arg-type]
            summary="Dicts are not canonical localizations.",
            diagnosed_at=_NOW,
        )


def test_classification_must_be_a_canonical_record_not_a_dict() -> None:
    payload = _classification().to_dict()

    with pytest.raises(FailureDiagnosisValidationError, match="classification"):
        FailureDiagnosis(
            classification=payload,  # type: ignore[arg-type]
            localization=_localization(),
            summary="Dicts are not canonical classifications.",
            diagnosed_at=_NOW,
        )


def test_hostile_decoded_subject_mismatch_fails_closed() -> None:
    payload = _diagnosis().to_dict()
    localization_payload = _nested_payload(payload, "localization")
    localization_payload["kind"] = "unknown"
    localization_payload["procedure_id"] = None
    localization_payload["procedure_node_id"] = None
    payload["localization"] = localization_payload

    with pytest.raises(
        FailureDiagnosisDeserializationError,
        match="PROCEDURE_NODE",
    ):
        FailureDiagnosis.from_dict(payload)


def test_decoded_localization_with_missing_node_identity_is_rejected() -> None:
    payload = _diagnosis().to_dict()
    localization_payload = _nested_payload(payload, "localization")
    localization_payload["procedure_node_id"] = None
    payload["localization"] = localization_payload

    with pytest.raises(FailureDiagnosisDeserializationError, match="localization is invalid"):
        FailureDiagnosis.from_dict(payload)


# ---------------------------------------------------------------------------
# Evidence-item validation: mismatches fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "kwargs"),
    (
        (DiagnosticEvidenceKind.CANONICAL_ERROR, {}),
        (DiagnosticEvidenceKind.NEGATIVE_EXPERIENCE, {}),
        (DiagnosticEvidenceKind.CHAIN_CORRELATION, {}),
        (DiagnosticEvidenceKind.EPISODE, {}),
        (DiagnosticEvidenceKind.TASK, {}),
    ),
)
def test_every_evidence_kind_requires_its_own_reference(
    kind: DiagnosticEvidenceKind, kwargs: dict[str, object]
) -> None:
    with pytest.raises(FailureDiagnosisValidationError):
        DiagnosticEvidence(kind=kind, **kwargs)  # type: ignore[arg-type]


def test_evidence_rejects_foreign_reference_fields() -> None:
    cases: tuple[tuple[DiagnosticEvidenceKind, dict[str, object]], ...] = (
        (
            DiagnosticEvidenceKind.CANONICAL_ERROR,
            {"error_code": "capability.timeout", "task_id": TaskId.create()},
        ),
        (
            DiagnosticEvidenceKind.CANONICAL_ERROR,
            {"error_code": "capability.timeout", "correlation_id": uuid4()},
        ),
        (
            DiagnosticEvidenceKind.NEGATIVE_EXPERIENCE,
            {"negative_experience_id": NegativeExperienceId.create(), "error_code": "x.failed"},
        ),
        (
            DiagnosticEvidenceKind.CHAIN_CORRELATION,
            {"correlation_id": uuid4(), "episode_id": EpisodeId.create()},
        ),
        (
            DiagnosticEvidenceKind.EPISODE,
            {"episode_id": EpisodeId.create(), "task_id": TaskId.create()},
        ),
        (DiagnosticEvidenceKind.TASK, {"task_id": TaskId.create(), "correlation_id": uuid4()}),
    )
    for kind, kwargs in cases:
        with pytest.raises(FailureDiagnosisValidationError, match=r"carry foreign"):
            DiagnosticEvidence(kind=kind, **kwargs)  # type: ignore[arg-type]


def test_evidence_rejects_malformed_references() -> None:
    with pytest.raises(FailureDiagnosisValidationError, match="error_code"):
        DiagnosticEvidence(kind=DiagnosticEvidenceKind.CANONICAL_ERROR, error_code=" not a code")
    with pytest.raises(FailureDiagnosisValidationError, match="error_code"):
        DiagnosticEvidence(kind=DiagnosticEvidenceKind.CANONICAL_ERROR, error_code="bad\ncode")
    with pytest.raises(FailureDiagnosisValidationError, match="correlation_id"):
        DiagnosticEvidence(
            kind=DiagnosticEvidenceKind.CHAIN_CORRELATION, correlation_id=UUID(int=0)
        )
    with pytest.raises(FailureDiagnosisValidationError, match="task_id"):
        DiagnosticEvidence(kind=DiagnosticEvidenceKind.TASK, task_id="not-a-task-id")  # type: ignore[arg-type]


def test_evidence_kind_must_be_typed_member_not_text() -> None:
    for hostile in ("canonical_error", "unknown", "permission", "node_implicated"):
        with pytest.raises(FailureDiagnosisValidationError, match="kind"):
            DiagnosticEvidence(kind=hostile, correlation_id=uuid4())  # type: ignore[arg-type]


def test_evidence_items_must_be_evidence_records_not_free_text() -> None:
    for hostile_evidence in (
        "permission denied traceback ...",
        ["node-1", "root cause"],
        [None],
        7,
    ):
        with pytest.raises(FailureDiagnosisValidationError, match="DiagnosticEvidence"):
            FailureDiagnosis(
                classification=_classification(),
                localization=_localization(),
                summary="Free text is not diagnostic evidence.",
                diagnosed_at=_NOW,
                evidence=hostile_evidence,  # type: ignore[arg-type]
            )


def test_decoded_evidence_with_wrong_reference_shape_is_rejected() -> None:
    payload = _diagnosis().to_dict()
    evidence_payload = {
        "kind": "canonical_error",
        "error_code": None,
        "negative_experience_id": None,
        "correlation_id": None,
        "episode_id": None,
        "task_id": None,
    }
    payload["evidence"] = [evidence_payload]

    with pytest.raises(FailureDiagnosisDeserializationError, match="evidence"):
        FailureDiagnosis.from_dict(payload)


def test_decoded_evidence_with_foreign_combination_is_rejected() -> None:
    payload = _diagnosis().to_dict()
    evidence_payload = {
        "kind": "canonical_error",
        "error_code": "capability.timeout",
        "negative_experience_id": None,
        "correlation_id": str(uuid4()),
        "episode_id": None,
        "task_id": None,
    }
    payload["evidence"] = [evidence_payload]

    with pytest.raises(FailureDiagnosisDeserializationError, match="foreign"):
        FailureDiagnosis.from_dict(payload)


def test_decoded_evidence_unknown_kind_is_rejected_not_dropped() -> None:
    payload = _diagnosis().to_dict()
    evidence_payload = {
        "kind": "model_inference",
        "error_code": None,
        "negative_experience_id": None,
        "correlation_id": str(uuid4()),
        "episode_id": None,
        "task_id": None,
    }
    payload["evidence"] = [evidence_payload]

    with pytest.raises(FailureDiagnosisDeserializationError, match="evidence kind"):
        FailureDiagnosis.from_dict(payload)


# ---------------------------------------------------------------------------
# C4.01 / C4.02 interaction and orthogonality
# ---------------------------------------------------------------------------


def test_every_canonical_category_is_orthogonal_to_the_node_subject() -> None:
    for category in CANONICAL_FAILURE_CATEGORIES:
        classification = _classification(category=category)
        before = classification.to_json()

        record = _diagnosis(classification=classification)

        assert record.classification.category is category
        assert record.classification.to_json() == before
        assert record.localization.kind is FailureLocationKind.PROCEDURE_NODE
        assert record.procedure_node_id == "node-1"


def test_location_never_rewrites_category_and_category_never_rewrites_location() -> None:
    classification = _classification(category=FailureCategory.VERIFICATION)
    localization = _localization(
        procedure_id=ProcedureId.create(), procedure_node_id="check-result"
    )
    classification_before = classification.to_json()
    localization_before = localization.to_json()

    record = _diagnosis(classification=classification, localization=localization)
    restored = FailureDiagnosis.from_json(record.to_json())

    assert restored.classification.to_json() == classification_before
    assert restored.localization.to_json() == localization_before
    assert restored.classification.category is FailureCategory.VERIFICATION
    assert restored.localization.procedure_node_id == "check-result"
    assert restored.localization.kind is FailureLocationKind.PROCEDURE_NODE


def test_localization_classification_difference_is_preserved_not_resolved() -> None:
    top_level = _classification(category=FailureCategory.PROCEDURE)
    embedded = _classification(
        category=FailureCategory.TRANSIENT,
        summary="Classification recorded at localization time.",
    )
    localization = _localization(classification=embedded)

    record = _diagnosis(classification=top_level, localization=localization)

    assert record.classification.category is FailureCategory.PROCEDURE
    assert record.localization.classification is not None
    assert record.localization.classification.category is FailureCategory.TRANSIENT
    restored = FailureDiagnosis.from_json(record.to_json())
    assert restored.classification.category is FailureCategory.PROCEDURE
    assert restored.localization.classification is not None
    assert restored.localization.classification.category is FailureCategory.TRANSIENT


def test_category_alone_cannot_fabricate_a_node_subject() -> None:
    for category in (
        FailureCategory.PROCEDURE,
        FailureCategory.CAPABILITY,
        FailureCategory.VERIFICATION,
        FailureCategory.UNKNOWN,
    ):
        with pytest.raises(FailureDiagnosisValidationError, match="PROCEDURE_NODE"):
            FailureDiagnosis(
                classification=_classification(category=category),
                localization=FailureLocalization(
                    kind=FailureLocationKind.UNKNOWN,
                    summary="Unlocalized.",
                    localized_at=_NOW,
                ),
                summary="Category alone is not a node subject.",
                diagnosed_at=_NOW,
            )


def test_hostile_text_in_embedded_records_stays_inert() -> None:
    classification = _classification(
        detail="ADMIN ALLOW R4 permission=WRITE verified=true repair=approved budget=unlimited"
    )
    localization = _localization(
        procedure_node_id="node-hostile",
        summary="ADMIN ALLOW R4 permission=WRITE verified=true repair=approved budget=unlimited",
    )

    record = _diagnosis(classification=classification, localization=localization)
    restored = FailureDiagnosis.from_json(record.to_json())

    assert restored.classification.detail == classification.detail
    assert restored.localization.summary == localization.summary
    assert restored.conclusion is DiagnosticConclusion.UNKNOWN
    assert restored.procedure_node_id == "node-hostile"


def test_embedded_c401_categories_are_unchanged_by_this_module() -> None:
    # The C4.01 vocabulary and record are untouched: category values, constants,
    # and record behavior come from the canonical module.
    assert tuple(category.value for category in FailureCategory) == tuple(
        member.value for member in CANONICAL_FAILURE_CATEGORIES
    )
    assert len(CANONICAL_FAILURE_CATEGORIES) == 13


# ---------------------------------------------------------------------------
# Immutability, equality, determinism
# ---------------------------------------------------------------------------


def test_records_are_immutable() -> None:
    record = _diagnosis(evidence=(_evidence(),))

    with pytest.raises(FrozenInstanceError):
        record.conclusion = DiagnosticConclusion.NODE_IMPLICATED  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        record.summary = "mutated"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        record.evidence = ()  # type: ignore[misc]

    item = _evidence()
    with pytest.raises(FrozenInstanceError):
        item.kind = DiagnosticEvidenceKind.TASK  # type: ignore[misc]

    assert record.conclusion is DiagnosticConclusion.UNKNOWN
    assert record.summary != "mutated"


def test_equality_and_hashing_are_structural() -> None:
    classification = _classification()
    localization = _localization()
    left = _diagnosis(classification=classification, localization=localization)
    right = _diagnosis(classification=classification, localization=localization)
    other_conclusion = _diagnosis(
        classification=classification,
        localization=localization,
        conclusion=DiagnosticConclusion.UNKNOWN,
    )

    assert left == right
    assert hash(left) == hash(right)
    assert left == other_conclusion
    assert len({left, right, other_conclusion}) == 1

    evidenced = _diagnosis(evidence=(_evidence(),))
    concluded = _diagnosis(
        evidence=(_evidence(),),
        conclusion=DiagnosticConclusion.NODE_IMPLICATED,
    )

    assert evidenced != concluded
    assert hash(evidenced) != hash(concluded)
    assert len({evidenced, concluded}) == 2


def test_conclusions_and_kinds_distinguish_records() -> None:
    evidence = (_evidence(),)
    unknown = _diagnosis(evidence=evidence)
    implicated = _diagnosis(evidence=evidence, conclusion=DiagnosticConclusion.NODE_IMPLICATED)

    assert unknown != implicated
    assert unknown.is_unknown is True
    assert implicated.is_unknown is False


# ---------------------------------------------------------------------------
# Serialization shape and strictness
# ---------------------------------------------------------------------------


def test_to_dict_is_the_exact_canonical_shape() -> None:
    record = _diagnosis(
        evidence=(
            DiagnosticEvidence(
                kind=DiagnosticEvidenceKind.CANONICAL_ERROR, error_code="capability.timeout"
            ),
        ),
        detail="Optional inert detail.",
    )

    payload = record.to_dict()

    assert set(payload) == {
        "schema_version",
        "classification",
        "localization",
        "evidence",
        "conclusion",
        "summary",
        "diagnosed_at",
        "detail",
    }
    assert payload["schema_version"] == 1
    assert payload["conclusion"] == "unknown"
    assert isinstance(payload["classification"], dict)
    assert isinstance(payload["localization"], dict)
    assert isinstance(payload["evidence"], list)
    assert set(payload["evidence"][0]) == {
        "kind",
        "error_code",
        "negative_experience_id",
        "correlation_id",
        "episode_id",
        "task_id",
    }


def test_to_json_is_deterministic_and_sorted() -> None:
    record = _diagnosis(evidence=(_evidence(),), detail="Deterministic bytes.")

    first = record.to_json()
    second = record.to_json()
    decoded = json.loads(first)

    assert first == second
    assert list(decoded) == sorted(decoded)
    assert first.startswith('{"classification":')
    assert '": "' not in first
    assert ", " not in first
    assert '"conclusion":"unknown"' in first


def test_every_conclusion_and_evidence_kind_round_trips_deterministically() -> None:
    for conclusion in CANONICAL_DIAGNOSTIC_CONCLUSIONS:
        evidence: tuple[DiagnosticEvidence, ...] = ()
        if conclusion is DiagnosticConclusion.NODE_IMPLICATED:
            evidence = (
                DiagnosticEvidence(
                    kind=DiagnosticEvidenceKind.CANONICAL_ERROR, error_code="execution.failed"
                ),
            )
        record = _diagnosis(conclusion=conclusion, evidence=evidence)

        restored = FailureDiagnosis.from_json(record.to_json())

        assert restored == record
        assert restored.conclusion is conclusion


def test_from_dict_rejects_unknown_conclusion_values_instead_of_downgrading() -> None:
    payload = _diagnosis().to_dict()

    for hostile in ("admin", "allow", "repair", "future_conclusion", "NODE_IMPLICATED", "unknown "):
        payload["conclusion"] = hostile
        with pytest.raises(FailureDiagnosisDeserializationError, match="conclusion"):
            FailureDiagnosis.from_dict(payload)


def test_from_dict_rejects_missing_or_null_conclusion() -> None:
    payload = _diagnosis().to_dict()

    without_conclusion = {key: value for key, value in payload.items() if key != "conclusion"}
    with pytest.raises(FailureDiagnosisDeserializationError, match="missing"):
        FailureDiagnosis.from_dict(without_conclusion)

    null_conclusion = dict(payload)
    null_conclusion["conclusion"] = None
    with pytest.raises(FailureDiagnosisDeserializationError, match="conclusion"):
        FailureDiagnosis.from_dict(null_conclusion)


def test_from_dict_requires_the_exact_field_set() -> None:
    payload = _diagnosis().to_dict()

    for missing in ("classification", "localization", "evidence", "summary", "diagnosed_at"):
        partial = {key: value for key, value in payload.items() if key != missing}
        with pytest.raises(FailureDiagnosisDeserializationError, match="missing"):
            FailureDiagnosis.from_dict(partial)

    hostile = dict(payload)
    hostile["verified"] = True
    hostile["patch"] = {"node": "x"}
    hostile["root_cause"] = "node-1"
    hostile["retry"] = "forever"
    hostile["permission"] = "WRITE"
    with pytest.raises(FailureDiagnosisDeserializationError, match="unknown fields"):
        FailureDiagnosis.from_dict(hostile)


def test_from_dict_rejects_unsupported_or_malformed_schema_versions() -> None:
    for version in (0, 2, -1, True, "1", None):
        payload = _diagnosis().to_dict()
        payload["schema_version"] = version
        with pytest.raises(
            (FailureDiagnosisDeserializationError, UnsupportedFailureDiagnosisSchemaVersionError)
        ):
            FailureDiagnosis.from_dict(payload)

    payload = _diagnosis().to_dict()
    del payload["schema_version"]
    with pytest.raises(FailureDiagnosisDeserializationError, match="schema_version"):
        FailureDiagnosis.from_dict(payload)


def test_from_dict_rejects_malformed_field_types() -> None:
    cases: dict[str, object] = {
        "classification": "admin",
        "localization": ["not", "a", "mapping"],
        "evidence": {"kind": "task"},
        "conclusion": 7,
        "summary": None,
        "diagnosed_at": "yesterday",
        "detail": 5,
    }
    for field, hostile in cases.items():
        payload = _diagnosis().to_dict()
        payload[field] = hostile
        with pytest.raises(FailureDiagnosisDeserializationError):
            FailureDiagnosis.from_dict(payload)


def test_from_dict_rejects_malformed_nested_records() -> None:
    payload = _diagnosis().to_dict()

    classification_payload = _nested_payload(payload, "classification")
    classification_payload["category"] = "admin"
    payload["classification"] = classification_payload
    with pytest.raises(FailureDiagnosisDeserializationError, match="classification is invalid"):
        FailureDiagnosis.from_dict(payload)

    payload = _diagnosis().to_dict()
    localization_payload = _nested_payload(payload, "localization")
    localization_payload["kind"] = "capability"
    payload["localization"] = localization_payload
    with pytest.raises(FailureDiagnosisDeserializationError, match="localization is invalid"):
        FailureDiagnosis.from_dict(payload)


def test_nested_embedded_records_are_preserved_and_validated() -> None:
    classification = _classification(
        category=FailureCategory.UNKNOWN,
        detail="detail with newlines\nsecond line",
    )
    localization = _localization(
        procedure_node_id="node-with-unicode-λ",
        detail="Some detail.",
    )
    record = _diagnosis(classification=classification, localization=localization)

    restored = FailureDiagnosis.from_json(record.to_json())

    assert restored.classification == classification
    assert restored.localization == localization
    assert restored.to_json() == record.to_json()


def test_from_json_fails_closed_on_hostile_or_malformed_input() -> None:
    for hostile in ("", "not json", "[1,2,3]", "42", '"unknown"', '{"schema_version": 1}'):
        with pytest.raises(FailureDiagnosisDeserializationError):
            FailureDiagnosis.from_json(hostile)

    with pytest.raises(FailureDiagnosisDeserializationError):
        FailureDiagnosis.from_json(7)  # type: ignore[arg-type]


def test_timestamps_round_trip_with_offset_normalization() -> None:
    offset_now = datetime(2026, 9, 5, 14, 30, 15, 123456, tzinfo=timezone(timedelta(hours=2)))
    record = _diagnosis(diagnosed_at=offset_now)

    assert record.diagnosed_at == _NOW
    restored = FailureDiagnosis.from_json(record.to_json())

    assert restored.diagnosed_at == _NOW
    assert restored.diagnosed_at.tzinfo is UTC
    assert '"detail":null' in record.to_json()


def test_serialization_uses_no_object_hooks_or_executable_types() -> None:
    import agentx.core.failure_diagnosis as module

    source = module.__file__ or ""
    assert "object_hook" not in source
    assert "pickle" not in source


# ---------------------------------------------------------------------------
# package_diagnosis packaging function
# ---------------------------------------------------------------------------


def test_package_diagnosis_packages_explicit_data_without_inference() -> None:
    classification = _classification()
    localization = _localization()
    correlation = uuid4()

    record = package_diagnosis(
        classification=classification,
        localization=localization,
        summary="Packaged from explicit typed data.",
        diagnosed_at=_NOW,
        evidence=[
            DiagnosticEvidence(
                kind=DiagnosticEvidenceKind.CHAIN_CORRELATION, correlation_id=correlation
            )
        ],
        detail="Optional detail.",
    )

    assert record.classification == classification
    assert record.localization == localization
    assert len(record.evidence) == 1
    assert record.evidence[0].correlation_id == correlation
    assert record.conclusion is DiagnosticConclusion.UNKNOWN
    assert record.detail == "Optional detail."


def test_package_diagnosis_defaults_to_unknown_and_accepts_empty_evidence() -> None:
    record = package_diagnosis(
        classification=_classification(),
        localization=_localization(),
        summary="Pointer only.",
        diagnosed_at=_NOW,
    )

    assert record.evidence == ()
    assert record.conclusion is DiagnosticConclusion.UNKNOWN
    assert record.is_unknown is True


def test_package_diagnosis_rejects_conclusion_text_and_free_text_evidence() -> None:
    with pytest.raises(FailureDiagnosisValidationError, match="conclusion"):
        package_diagnosis(
            classification=_classification(),
            localization=_localization(),
            summary="Strings are not conclusions.",
            diagnosed_at=_NOW,
            conclusion="node_implicated",  # type: ignore[arg-type]
        )

    with pytest.raises(FailureDiagnosisValidationError, match="DiagnosticEvidence"):
        package_diagnosis(
            classification=_classification(),
            localization=_localization(),
            summary="Strings are not evidence.",
            diagnosed_at=_NOW,
            evidence="root cause is node-1",  # type: ignore[arg-type]
        )


def test_package_diagnosis_evidence_list_is_frozen_into_a_tuple() -> None:
    classification = _classification()
    localization = _localization()
    evidence = [_evidence(), _evidence()]
    record = package_diagnosis(
        classification=classification,
        localization=localization,
        summary="List input becomes an immutable tuple.",
        diagnosed_at=_NOW,
        evidence=evidence,
    )

    assert isinstance(record.evidence, tuple)
    assert record.evidence == tuple(evidence)

    evidence.append(_evidence())  # the caller's list is not referenced
    assert len(record.evidence) == 2


# ---------------------------------------------------------------------------
# Procedure Graph (A3.01) interaction
# ---------------------------------------------------------------------------


def test_real_graph_node_id_flows_through_diagnosis_unchanged() -> None:
    graph, target = _real_graph()
    node_id = target.to_str()

    localization = _localization(
        procedure_node_id=node_id,
        summary="Real graph node subject.",
    )
    record = _diagnosis(localization=localization)

    assert record.procedure_node_id == node_id
    restored = FailureDiagnosis.from_json(record.to_json())
    assert restored.procedure_node_id == node_id
    # The id round-trips into the identical typed graph identity.
    assert ProcedureNodeId.parse(restored.procedure_node_id) == target
    assert ProcedureNodeId.parse(restored.procedure_node_id) in {node.id for node in graph.nodes}


def test_every_real_graph_node_id_is_an_accepted_subject() -> None:
    graph, _ = _real_graph()

    for node in graph.nodes:
        record = _diagnosis(localization=_localization(procedure_node_id=node.id.to_str()))
        restored = FailureDiagnosis.from_json(record.to_json())

        assert restored.procedure_node_id == node.id.to_str()
        assert ProcedureNodeId.parse(restored.procedure_node_id) == node.id


def test_hostile_node_id_shapes_fail_closed_before_diagnosis() -> None:
    with pytest.raises(FailureLocalizationValidationError):
        _localization(procedure_node_id="")

    with pytest.raises(FailureLocalizationValidationError):
        _localization(procedure_node_id=" padded ")

    with pytest.raises(FailureLocalizationValidationError):
        _localization(procedure_node_id="node\x00id")

    with pytest.raises(FailureLocalizationValidationError):
        _localization(procedure_node_id=7)

    # C4.02's own deserializer also rejects node ids that are missing or hostile,
    # so hostile payloads cannot reach the C4.03 subject gate as a fake node.
    payload = _diagnosis().to_dict()
    localization_payload = _nested_payload(payload, "localization")
    localization_payload["procedure_node_id"] = "\x1bnode"
    payload["localization"] = localization_payload
    with pytest.raises(FailureDiagnosisDeserializationError, match="localization is invalid"):
        FailureDiagnosis.from_dict(payload)


def test_pure_data_boundary_never_checks_graph_membership() -> None:
    # A C4.03 record referencing an id that does not exist in any graph is still
    # constructible: existence checks require querying a store/runtime, which
    # this pure data boundary never does and never fabricates.
    graph, _ = _real_graph()
    known_ids = {node.id.to_str() for node in graph.nodes}

    record = _diagnosis(localization=_localization(procedure_node_id="not-in-this-graph"))

    assert record.procedure_node_id not in known_ids
    restored = FailureDiagnosis.from_json(record.to_json())
    assert restored.procedure_node_id == "not-in-this-graph"
