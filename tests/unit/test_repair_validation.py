"""Unit tests for the C4.06 canonical repair-validation evidence contract."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest

from agentx.core.ids import ProcedureId, TaskId
from agentx.core.repair_validation import (
    CANONICAL_REPAIR_VALIDATION_CRITERIA,
    CANONICAL_REPAIR_VALIDATION_DISPOSITIONS,
    CANONICAL_REPAIR_VALIDATION_OUTCOMES,
    DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA,
    MAX_REPAIR_VALIDATION_DETAIL_LENGTH,
    MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS,
    MAX_REPAIR_VALIDATION_REFERENCE_LENGTH,
    MAX_REPAIR_VALIDATION_REQUIRED_CRITERIA,
    REPAIR_VALIDATION_SCHEMA_VERSION,
    RepairValidationCriterion,
    RepairValidationDeserializationError,
    RepairValidationDisposition,
    RepairValidationEvidence,
    RepairValidationOutcome,
    RepairValidationReport,
    RepairValidationTarget,
    RepairValidationValidationError,
    UnsupportedRepairValidationSchemaVersionError,
    evaluate_repair_validation,
)

_NOW = datetime(2026, 9, 5, 12, 30, 15, 123456, tzinfo=UTC)
_PROCEDURE = ProcedureId.create()
_NODE = "node-1"
_REPAIR_REF = "repair-ref-001"

_EXPECTED_CRITERION_VALUES = (
    "structural_validity",
    "target_binding",
    "precondition_preservation",
    "postcondition_compatibility",
    "regression_free",
    "verification_evidence",
    "scope_compatibility",
)

_EXPECTED_OUTCOME_VALUES = (
    "passed",
    "failed",
    "insufficient_evidence",
    "not_applicable",
)

_EXPECTED_DISPOSITION_VALUES = (
    "validated",
    "failed",
    "insufficient",
    "not_applicable",
)

_EXPECTED_REPORT_FIELDS = {
    "schema_version",
    "repair_reference",
    "target",
    "disposition",
    "required_criteria",
    "evidence",
    "evaluated_at",
    "detail",
}


def _target(**overrides: object) -> RepairValidationTarget:
    payload: dict[str, object] = {
        "procedure_id": _PROCEDURE,
        "procedure_revision": 1,
        "procedure_node_id": _NODE,
    }
    payload.update(overrides)
    return RepairValidationTarget(**payload)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> RepairValidationEvidence:
    payload: dict[str, object] = {
        "repair_reference": _REPAIR_REF,
        "procedure_id": _PROCEDURE,
        "procedure_revision": 1,
        "procedure_node_id": _NODE,
        "criterion": RepairValidationCriterion.STRUCTURAL_VALIDITY,
        "outcome": RepairValidationOutcome.PASSED,
        "evidence_reference": "ev-1",
        "evaluated_at": _NOW,
    }
    payload.update(overrides)
    return RepairValidationEvidence(**payload)  # type: ignore[arg-type]


def _passing_required(
    *,
    repair_reference: str = _REPAIR_REF,
    procedure_id: ProcedureId | None = None,
    procedure_revision: int = 1,
    procedure_node_id: str | None = _NODE,
    criteria: tuple[RepairValidationCriterion, ...] | None = None,
) -> tuple[RepairValidationEvidence, ...]:
    pid = procedure_id if procedure_id is not None else _PROCEDURE
    required = criteria if criteria is not None else DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA
    items: list[RepairValidationEvidence] = []
    for index, criterion in enumerate(required):
        items.append(
            _evidence(
                repair_reference=repair_reference,
                procedure_id=pid,
                procedure_revision=procedure_revision,
                procedure_node_id=procedure_node_id,
                criterion=criterion,
                outcome=RepairValidationOutcome.PASSED,
                evidence_reference=f"ev-pass-{index}",
            )
        )
    return tuple(items)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_canonical_criteria_are_exactly_the_closed_vocabulary() -> None:
    assert tuple(member.value for member in CANONICAL_REPAIR_VALIDATION_CRITERIA) == (
        _EXPECTED_CRITERION_VALUES
    )
    assert len(RepairValidationCriterion) == 7


def test_canonical_outcomes_and_dispositions() -> None:
    assert tuple(member.value for member in CANONICAL_REPAIR_VALIDATION_OUTCOMES) == (
        _EXPECTED_OUTCOME_VALUES
    )
    assert tuple(member.value for member in CANONICAL_REPAIR_VALIDATION_DISPOSITIONS) == (
        _EXPECTED_DISPOSITION_VALUES
    )


def test_default_required_criteria_are_non_empty_safe_minimum() -> None:
    assert DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA
    assert set(DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA) <= set(
        CANONICAL_REPAIR_VALIDATION_CRITERIA
    )
    assert RepairValidationCriterion.STRUCTURAL_VALIDITY in (
        DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA
    )
    assert RepairValidationCriterion.TARGET_BINDING in (DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA)


# ---------------------------------------------------------------------------
# Target binding
# ---------------------------------------------------------------------------


def test_target_requires_exact_procedure_and_positive_revision() -> None:
    target = _target()
    assert target.procedure_id is _PROCEDURE
    assert target.procedure_revision == 1
    assert target.procedure_node_id == _NODE


def test_target_rejects_non_positive_revision_and_bool() -> None:
    with pytest.raises(RepairValidationValidationError):
        _target(procedure_revision=0)
    with pytest.raises(RepairValidationValidationError):
        _target(procedure_revision=-1)
    with pytest.raises(RepairValidationValidationError):
        _target(procedure_revision=True)


def test_target_rejects_wildcard_node_ids() -> None:
    for bad in ("*", "latest", "any", "LATEST"):
        with pytest.raises(RepairValidationValidationError):
            _target(procedure_node_id=bad)


def test_target_binds_evidence_exactly() -> None:
    target = _target()
    assert target.binds_evidence(
        procedure_id=_PROCEDURE,
        procedure_revision=1,
        procedure_node_id=_NODE,
    )
    assert not target.binds_evidence(
        procedure_id=ProcedureId.create(),
        procedure_revision=1,
        procedure_node_id=_NODE,
    )
    assert not target.binds_evidence(
        procedure_id=_PROCEDURE,
        procedure_revision=2,
        procedure_node_id=_NODE,
    )
    assert not target.binds_evidence(
        procedure_id=_PROCEDURE,
        procedure_revision=1,
        procedure_node_id="node-2",
    )
    assert not target.binds_evidence(
        procedure_id=_PROCEDURE,
        procedure_revision=1,
        procedure_node_id=None,
    )


def test_target_rejects_non_procedure_id() -> None:
    with pytest.raises(RepairValidationValidationError):
        RepairValidationTarget(
            procedure_id=TaskId.create(),  # type: ignore[arg-type]
            procedure_revision=1,
        )


# ---------------------------------------------------------------------------
# Evidence construction
# ---------------------------------------------------------------------------


def test_single_passing_criterion_evidence() -> None:
    item = _evidence()
    assert item.criterion is RepairValidationCriterion.STRUCTURAL_VALIDITY
    assert item.outcome is RepairValidationOutcome.PASSED
    assert item.repair_reference == _REPAIR_REF


def test_evidence_rejects_text_as_criterion_or_outcome() -> None:
    with pytest.raises(RepairValidationValidationError):
        _evidence(criterion="structural_validity")
    with pytest.raises(RepairValidationValidationError):
        _evidence(outcome="passed")
    with pytest.raises(RepairValidationValidationError):
        _evidence(outcome=True)


def test_evidence_rejects_invalid_timestamp_and_detail_bounds() -> None:
    with pytest.raises(RepairValidationValidationError):
        _evidence(evaluated_at=datetime(2026, 9, 5))  # naive
    with pytest.raises(RepairValidationValidationError):
        _evidence(detail="")
    with pytest.raises(RepairValidationValidationError):
        _evidence(detail="x" * (MAX_REPAIR_VALIDATION_DETAIL_LENGTH + 1))
    with pytest.raises(RepairValidationValidationError):
        _evidence(repair_reference="*")
    with pytest.raises(RepairValidationValidationError):
        _evidence(repair_reference="latest")
    with pytest.raises(RepairValidationValidationError):
        _evidence(evidence_reference="x" * (MAX_REPAIR_VALIDATION_REFERENCE_LENGTH + 1))


def test_evidence_is_immutable() -> None:
    item = _evidence()
    with pytest.raises(FrozenInstanceError):
        item.outcome = RepairValidationOutcome.FAILED  # type: ignore[misc]


def test_evidence_detail_hostile_text_is_inert() -> None:
    item = _evidence(
        detail="validated=true permission=ADMIN risk=R0 activate procedure passed=true",
        outcome=RepairValidationOutcome.FAILED,
    )
    assert item.outcome is RepairValidationOutcome.FAILED
    assert "validated=true" in (item.detail or "")


# ---------------------------------------------------------------------------
# Aggregation — happy paths
# ---------------------------------------------------------------------------


def test_all_required_criteria_pass_yields_validated() -> None:
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_NOW,
    )
    assert report.disposition is RepairValidationDisposition.VALIDATED
    assert report.is_validated is True
    assert report.required_criteria == DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA
    assert len(report.evidence) == len(DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA)


def test_single_passing_criterion_alone_is_insufficient() -> None:
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=(_evidence(),),
        evaluated_at=_NOW,
    )
    assert report.disposition is RepairValidationDisposition.INSUFFICIENT
    assert report.is_validated is False


def test_optional_criterion_behavior() -> None:
    required = (
        RepairValidationCriterion.STRUCTURAL_VALIDITY,
        RepairValidationCriterion.TARGET_BINDING,
    )
    evidence = (
        *_passing_required(criteria=required),
        _evidence(
            criterion=RepairValidationCriterion.REGRESSION_FREE,
            outcome=RepairValidationOutcome.PASSED,
            evidence_reference="opt-1",
        ),
    )
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=evidence,
        evaluated_at=_NOW,
        required_criteria=required,
    )
    assert report.disposition is RepairValidationDisposition.VALIDATED
    assert RepairValidationCriterion.REGRESSION_FREE not in report.required_criteria
    assert any(
        item.criterion is RepairValidationCriterion.REGRESSION_FREE for item in report.evidence
    )


def test_optional_failure_does_not_fail_report() -> None:
    required = (
        RepairValidationCriterion.STRUCTURAL_VALIDITY,
        RepairValidationCriterion.TARGET_BINDING,
    )
    evidence = (
        *_passing_required(criteria=required),
        _evidence(
            criterion=RepairValidationCriterion.SCOPE_COMPATIBILITY,
            outcome=RepairValidationOutcome.FAILED,
            evidence_reference="opt-fail",
        ),
    )
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=evidence,
        evaluated_at=_NOW,
        required_criteria=required,
    )
    assert report.disposition is RepairValidationDisposition.VALIDATED


# ---------------------------------------------------------------------------
# Aggregation — fail-closed paths
# ---------------------------------------------------------------------------


def test_missing_required_criterion_is_insufficient() -> None:
    required = DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA
    partial = _passing_required(criteria=required[:-1])
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=partial,
        evaluated_at=_NOW,
        required_criteria=required,
    )
    assert report.disposition is RepairValidationDisposition.INSUFFICIENT


def test_one_required_failure_yields_failed() -> None:
    items = list(_passing_required())
    items[0] = _evidence(
        criterion=items[0].criterion,
        outcome=RepairValidationOutcome.FAILED,
        evidence_reference="fail-1",
    )
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=tuple(items),
        evaluated_at=_NOW,
    )
    assert report.disposition is RepairValidationDisposition.FAILED


def test_conflicting_evidence_for_same_criterion_fails_closed() -> None:
    criterion = RepairValidationCriterion.STRUCTURAL_VALIDITY
    evidence = (
        _evidence(
            criterion=criterion,
            outcome=RepairValidationOutcome.PASSED,
            evidence_reference="a",
        ),
        _evidence(
            criterion=criterion,
            outcome=RepairValidationOutcome.FAILED,
            evidence_reference="b",
        ),
        *_passing_required(
            criteria=tuple(
                c for c in DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA if c is not criterion
            )
        ),
    )
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=evidence,
        evaluated_at=_NOW,
    )
    assert report.disposition is RepairValidationDisposition.FAILED


def test_duplicate_evidence_does_not_count_as_independent_proof() -> None:
    item = _evidence()
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=(item, item, item),
        evaluated_at=_NOW,
        required_criteria=(RepairValidationCriterion.STRUCTURAL_VALIDITY,),
    )
    assert report.disposition is RepairValidationDisposition.VALIDATED
    assert len(report.evidence) == 1


def test_wrong_procedure_id_evidence_ignored() -> None:
    foreign = ProcedureId.create()
    evidence = _passing_required(procedure_id=foreign)
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=evidence,
        evaluated_at=_NOW,
    )
    assert report.disposition is RepairValidationDisposition.INSUFFICIENT
    assert report.evidence == ()


def test_wrong_revision_evidence_ignored() -> None:
    evidence = _passing_required(procedure_revision=2)
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(procedure_revision=1),
        evidence=evidence,
        evaluated_at=_NOW,
    )
    assert report.disposition is RepairValidationDisposition.INSUFFICIENT
    assert report.evidence == ()


def test_wrong_node_evidence_ignored() -> None:
    evidence = _passing_required(procedure_node_id="other-node")
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(procedure_node_id=_NODE),
        evidence=evidence,
        evaluated_at=_NOW,
    )
    assert report.disposition is RepairValidationDisposition.INSUFFICIENT


def test_wrong_repair_reference_evidence_ignored() -> None:
    evidence = _passing_required(repair_reference="other-repair")
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=evidence,
        evaluated_at=_NOW,
    )
    assert report.disposition is RepairValidationDisposition.INSUFFICIENT


def test_empty_required_set_bypass_rejected() -> None:
    with pytest.raises(RepairValidationValidationError, match="non-empty"):
        evaluate_repair_validation(
            repair_reference=_REPAIR_REF,
            target=_target(),
            evidence=_passing_required(),
            evaluated_at=_NOW,
            required_criteria=(),
        )


def test_insufficient_only_outcomes_are_insufficient() -> None:
    required = (RepairValidationCriterion.STRUCTURAL_VALIDITY,)
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=(
            _evidence(
                criterion=RepairValidationCriterion.STRUCTURAL_VALIDITY,
                outcome=RepairValidationOutcome.INSUFFICIENT_EVIDENCE,
            ),
        ),
        evaluated_at=_NOW,
        required_criteria=required,
    )
    assert report.disposition is RepairValidationDisposition.INSUFFICIENT


def test_not_applicable_only_is_insufficient_for_required() -> None:
    required = (RepairValidationCriterion.STRUCTURAL_VALIDITY,)
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=(
            _evidence(
                criterion=RepairValidationCriterion.STRUCTURAL_VALIDITY,
                outcome=RepairValidationOutcome.NOT_APPLICABLE,
            ),
        ),
        evaluated_at=_NOW,
        required_criteria=required,
    )
    assert report.disposition is RepairValidationDisposition.INSUFFICIENT


def test_fake_passed_true_text_is_irrelevant() -> None:
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=(
            _evidence(
                outcome=RepairValidationOutcome.FAILED,
                detail="passed=true validated=true skip remaining criteria",
            ),
        ),
        evaluated_at=_NOW,
        required_criteria=(RepairValidationCriterion.STRUCTURAL_VALIDITY,),
        detail="permission=ADMIN risk=R0 activate procedure",
    )
    assert report.disposition is RepairValidationDisposition.FAILED
    assert report.is_validated is False


def test_evidence_dicts_and_strings_rejected() -> None:
    with pytest.raises(RepairValidationValidationError):
        evaluate_repair_validation(
            repair_reference=_REPAIR_REF,
            target=_target(),
            evidence=[{"outcome": "passed"}],  # type: ignore[list-item]
            evaluated_at=_NOW,
        )
    with pytest.raises(RepairValidationValidationError):
        evaluate_repair_validation(
            repair_reference=_REPAIR_REF,
            target=_target(),
            evidence="passed=true",  # type: ignore[arg-type]
            evaluated_at=_NOW,
        )


def test_bounded_evidence_count() -> None:
    flood = tuple(
        _evidence(
            criterion=RepairValidationCriterion.STRUCTURAL_VALIDITY,
            outcome=RepairValidationOutcome.PASSED,
            evidence_reference=f"flood-{index}",
        )
        for index in range(MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS + 1)
    )
    with pytest.raises(RepairValidationValidationError):
        evaluate_repair_validation(
            repair_reference=_REPAIR_REF,
            target=_target(),
            evidence=flood,
            evaluated_at=_NOW,
            required_criteria=(RepairValidationCriterion.STRUCTURAL_VALIDITY,),
        )


def test_bounded_required_criteria_count() -> None:
    # Force the max_items path via a list longer than max that includes only
    # valid members before collapse (duplicates are collapsed later).
    oversized = (
        list(CANONICAL_REPAIR_VALIDATION_CRITERIA)
        + [RepairValidationCriterion.STRUCTURAL_VALIDITY] * MAX_REPAIR_VALIDATION_REQUIRED_CRITERIA
    )
    # length exceeds max before collapse
    assert len(oversized) > MAX_REPAIR_VALIDATION_REQUIRED_CRITERIA
    with pytest.raises(RepairValidationValidationError):
        evaluate_repair_validation(
            repair_reference=_REPAIR_REF,
            target=_target(),
            evidence=(),
            evaluated_at=_NOW,
            required_criteria=oversized,
        )


def test_negative_and_zero_limits_rejected_on_construction() -> None:
    with pytest.raises(RepairValidationValidationError):
        _target(procedure_revision=0)
    with pytest.raises(RepairValidationValidationError):
        _evidence(procedure_revision=-3)


# ---------------------------------------------------------------------------
# Determinism / immutability
# ---------------------------------------------------------------------------


def test_deterministic_report_for_same_inputs() -> None:
    evidence = _passing_required()
    target = _target()
    first = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=target,
        evidence=evidence,
        evaluated_at=_NOW,
    )
    second = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=target,
        evidence=evidence,
        evaluated_at=_NOW,
    )
    assert first == second
    assert first.to_json() == second.to_json()


def test_report_is_immutable() -> None:
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_NOW,
    )
    with pytest.raises(FrozenInstanceError):
        report.disposition = RepairValidationDisposition.FAILED  # type: ignore[misc]


def test_timestamp_normalized_to_utc() -> None:
    offset = timezone(timedelta(hours=-5))
    stamp = datetime(2026, 9, 5, 7, 30, 15, 123456, tzinfo=offset)
    item = _evidence(evaluated_at=stamp)
    assert item.evaluated_at.tzinfo == UTC
    assert item.evaluated_at == _NOW


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_serialization_round_trip() -> None:
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_NOW,
        detail="inert note",
    )
    restored = RepairValidationReport.from_json(report.to_json())
    assert restored == report
    assert json.loads(report.to_json()) == report.to_dict()
    assert set(report.to_dict()) == _EXPECTED_REPORT_FIELDS


def test_evidence_serialization_round_trip() -> None:
    item = _evidence(detail="note")
    restored = RepairValidationEvidence.from_json(item.to_json())
    assert restored == item


def test_unknown_fields_rejected() -> None:
    payload = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_NOW,
    ).to_dict()
    payload["validated"] = True
    with pytest.raises(RepairValidationDeserializationError, match="unknown"):
        RepairValidationReport.from_dict(payload)

    evidence_payload = _evidence().to_dict()
    evidence_payload["passed"] = True
    with pytest.raises(RepairValidationDeserializationError, match="unknown"):
        RepairValidationEvidence.from_dict(evidence_payload)


def test_malformed_enum_rejected() -> None:
    payload = _evidence().to_dict()
    payload["criterion"] = "not_a_criterion"
    with pytest.raises(RepairValidationDeserializationError):
        RepairValidationEvidence.from_dict(payload)
    payload = _evidence().to_dict()
    payload["outcome"] = "PASSED"
    with pytest.raises(RepairValidationDeserializationError):
        RepairValidationEvidence.from_dict(payload)
    report_payload = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_NOW,
    ).to_dict()
    report_payload["disposition"] = "ok"
    with pytest.raises(RepairValidationDeserializationError):
        RepairValidationReport.from_dict(report_payload)


def test_invalid_timestamp_rejected_on_decode() -> None:
    payload = _evidence().to_dict()
    payload["evaluated_at"] = "not-a-timestamp"
    with pytest.raises(RepairValidationDeserializationError):
        RepairValidationEvidence.from_dict(payload)
    payload["evaluated_at"] = "2026-09-05T12:00:00"
    with pytest.raises(RepairValidationDeserializationError):
        RepairValidationEvidence.from_dict(payload)


def test_unsupported_schema_version() -> None:
    payload = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_NOW,
    ).to_dict()
    payload["schema_version"] = 99
    with pytest.raises(UnsupportedRepairValidationSchemaVersionError):
        RepairValidationReport.from_dict(payload)


def test_schema_version_constant() -> None:
    assert REPAIR_VALIDATION_SCHEMA_VERSION == 1


def test_report_rejects_mismatched_embedded_evidence_target() -> None:
    good = _passing_required()
    with pytest.raises(RepairValidationValidationError):
        RepairValidationReport(
            repair_reference=_REPAIR_REF,
            target=_target(),
            disposition=RepairValidationDisposition.VALIDATED,
            required_criteria=DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA,
            evidence=(
                *good,
                _evidence(
                    procedure_revision=9,
                    criterion=RepairValidationCriterion.SCOPE_COMPATIBILITY,
                    evidence_reference="bad-rev",
                ),
            ),
            evaluated_at=_NOW,
        )


def test_exact_target_binding_on_report() -> None:
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(procedure_revision=3, procedure_node_id="n-9"),
        evidence=_passing_required(procedure_revision=3, procedure_node_id="n-9"),
        evaluated_at=_NOW,
    )
    assert report.target.procedure_revision == 3
    assert report.target.procedure_node_id == "n-9"
    assert all(item.procedure_revision == 3 for item in report.evidence)


def test_no_execution_side_effects_from_evaluate() -> None:
    # evaluate is pure: repeated calls neither mutate inputs nor allocate
    # authority-bearing fields.
    evidence = _passing_required()
    target = _target()
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=target,
        evidence=evidence,
        evaluated_at=_NOW,
    )
    assert "permission" not in report.to_dict()
    assert "authority" not in report.to_dict()
    assert "authorized" not in report.to_dict()
    assert "executed" not in report.to_dict()
    assert report.to_dict()["disposition"] == "validated"


def test_lookalike_procedure_id_does_not_bind() -> None:
    # Different UUID bytes even if string-similar never bind.
    other = ProcedureId(uuid4())
    assert other != _PROCEDURE
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(procedure_id=_PROCEDURE),
        evidence=_passing_required(procedure_id=other),
        evaluated_at=_NOW,
    )
    assert report.disposition is RepairValidationDisposition.INSUFFICIENT


def test_required_criteria_canonical_order() -> None:
    shuffled = (
        RepairValidationCriterion.POSTCONDITION_COMPATIBILITY,
        RepairValidationCriterion.STRUCTURAL_VALIDITY,
        RepairValidationCriterion.TARGET_BINDING,
    )
    report = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=_passing_required(criteria=shuffled),
        evaluated_at=_NOW,
        required_criteria=shuffled,
    )
    assert report.required_criteria == (
        RepairValidationCriterion.STRUCTURAL_VALIDITY,
        RepairValidationCriterion.TARGET_BINDING,
        RepairValidationCriterion.POSTCONDITION_COMPATIBILITY,
    )


def test_from_json_rejects_non_object_root() -> None:
    with pytest.raises(RepairValidationDeserializationError):
        RepairValidationReport.from_json("[]")
    with pytest.raises(RepairValidationDeserializationError):
        RepairValidationReport.from_json("not-json")
    with pytest.raises(RepairValidationDeserializationError):
        RepairValidationReport.from_json(123)  # type: ignore[arg-type]


def test_bool_as_int_schema_version_rejected() -> None:
    payload: dict[str, Any] = evaluate_repair_validation(
        repair_reference=_REPAIR_REF,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_NOW,
    ).to_dict()
    payload["schema_version"] = True
    with pytest.raises(RepairValidationDeserializationError):
        RepairValidationReport.from_dict(payload)
