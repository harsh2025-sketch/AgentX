"""Unit tests for the C4.04 canonical repair-candidate contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest

from agentx.core.failure_diagnosis import (
    DiagnosticConclusion,
    DiagnosticEvidence,
    DiagnosticEvidenceKind,
    FailureDiagnosis,
    FailureDiagnosisValidationError,
    package_diagnosis,
)
from agentx.core.failure_localization import (
    FailureLocalization,
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
from agentx.core.repair_candidates import (
    CANONICAL_REPAIR_CANDIDATE_KINDS,
    REPAIR_CANDIDATE_SCHEMA_VERSION,
    RepairCandidate,
    RepairCandidateDeserializationError,
    RepairCandidateKind,
    RepairCandidateValidationError,
    UnsupportedRepairCandidateSchemaVersionError,
    derive_repair_candidates,
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

_EXPECTED_KIND_VALUES = ("unknown", "node_definition_revision")

_EXPECTED_CANDIDATE_FIELDS = {
    "schema_version",
    "kind",
    "diagnosis",
    "supporting_evidence_indices",
    "proposed_at",
}


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


def _implicated_diagnosis(**overrides: object) -> FailureDiagnosis:
    payload: dict[str, object] = {
        "evidence": (_evidence(), _evidence()),
        "conclusion": DiagnosticConclusion.NODE_IMPLICATED,
    }
    payload.update(overrides)
    return _diagnosis(**payload)


def _candidate(**overrides: object) -> RepairCandidate:
    diagnosis = _implicated_diagnosis()
    payload: dict[str, object] = {
        "kind": RepairCandidateKind.NODE_DEFINITION_REVISION,
        "diagnosis": diagnosis,
        "supporting_evidence_indices": tuple(range(len(diagnosis.evidence))),
        "proposed_at": _NOW,
    }
    payload.update(overrides)
    return RepairCandidate(**payload)  # type: ignore[arg-type]


def _candidate_payload(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = _candidate().to_dict()
    payload.update(overrides)
    return payload


def _nested_diagnosis(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = payload["diagnosis"]
    if not isinstance(raw, dict):
        raise AssertionError("diagnosis must be a nested object payload")
    return raw


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_canonical_kinds_are_exactly_the_smallest_justified_vocabulary() -> None:
    assert tuple(RepairCandidateKind) == CANONICAL_REPAIR_CANDIDATE_KINDS
    assert tuple(member.value for member in CANONICAL_REPAIR_CANDIDATE_KINDS) == (
        _EXPECTED_KIND_VALUES
    )


def test_kind_names_match_the_canonical_architecture_names() -> None:
    assert {member.name for member in CANONICAL_REPAIR_CANDIDATE_KINDS} == {
        "UNKNOWN",
        "NODE_DEFINITION_REVISION",
    }


def test_vocabulary_values_are_unique_and_lowercase() -> None:
    values = [member.value for member in CANONICAL_REPAIR_CANDIDATE_KINDS]
    assert len(values) == len(set(values))
    assert all(value == value.lower() and value.strip() == value for value in values)


def test_no_speculative_repair_members_exist() -> None:
    # A candidate category must be justified by a canonical structured fact.
    # Anything that the C4.01-C4.03 vocabulary cannot evidence stays absent.
    forbidden = {
        "grant_permission",
        "retry",
        "rerun",
        "rollback",
        "reboot",
        "rewrite_code",
        "activate_procedure",
        "promote_knowledge",
        "suppress",
        "do_nothing",
    }
    assert forbidden.isdisjoint(member.value for member in CANONICAL_REPAIR_CANDIDATE_KINDS)


def test_members_carry_no_policy_severity_or_confidence_attributes() -> None:
    for member in CANONICAL_REPAIR_CANDIDATE_KINDS:
        assert not hasattr(member, "severity")
        assert not hasattr(member, "confidence")
        assert not hasattr(member, "score")
        assert not hasattr(member, "rank")
        assert not hasattr(member, "authorized")
        assert not hasattr(member, "selected")
        assert not hasattr(member, "verified")


def test_schema_version_is_one() -> None:
    assert REPAIR_CANDIDATE_SCHEMA_VERSION == 1


# ---------------------------------------------------------------------------
# Construction from supported explicit diagnoses
# ---------------------------------------------------------------------------


def test_derive_from_supported_explicit_diagnosis_yields_one_node_revision_candidate() -> None:
    diagnosis = _implicated_diagnosis()

    candidates = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    assert isinstance(candidates, tuple)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.kind is RepairCandidateKind.NODE_DEFINITION_REVISION
    assert candidate.supporting_evidence_indices == (0, 1)
    assert candidate.diagnosis is diagnosis
    assert candidate.proposed_at == _NOW
    assert candidate.is_unknown is False


def test_derived_candidate_cites_every_supporting_evidence_position_in_order() -> None:
    diagnosis = _implicated_diagnosis(
        evidence=tuple(_evidence() for _ in range(len(DiagnosticEvidenceKind))),
    )

    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    assert candidate.supporting_evidence_indices == tuple(range(len(diagnosis.evidence)))


def test_candidate_embeds_the_exact_diagnosis_by_value() -> None:
    diagnosis = _implicated_diagnosis()

    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    assert candidate.diagnosis == diagnosis
    assert candidate.diagnosis.classification == diagnosis.classification
    assert candidate.diagnosis.localization == diagnosis.localization
    assert candidate.diagnosis.evidence == diagnosis.evidence
    assert candidate.diagnosis.conclusion is diagnosis.conclusion


# ---------------------------------------------------------------------------
# Insufficient diagnosis / UNKNOWN semantics
# ---------------------------------------------------------------------------


def test_derive_from_unknown_conclusion_diagnosis_yields_one_unknown_candidate() -> None:
    diagnosis = _diagnosis()  # conclusion defaults to UNKNOWN

    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    assert candidate.kind is RepairCandidateKind.UNKNOWN
    assert candidate.is_unknown is True
    assert candidate.supporting_evidence_indices == ()


def test_evidence_rich_unknown_conclusion_never_promotes_a_specific_candidate() -> None:
    # C4.03 permits evidence with an UNKNOWN conclusion; evidence may not
    # promote itself into a candidate category here either.
    diagnosis = _diagnosis(evidence=(_evidence(), _evidence(), _evidence()))

    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    assert candidate.kind is RepairCandidateKind.UNKNOWN
    assert candidate.supporting_evidence_indices == ()
    assert len(candidate.diagnosis.evidence) == 3


def test_unknown_candidate_is_always_representable_and_cites_no_evidence() -> None:
    candidate = RepairCandidate(
        kind=RepairCandidateKind.UNKNOWN,
        diagnosis=_implicated_diagnosis(),
        proposed_at=_NOW,
    )
    assert candidate.is_unknown is True
    assert candidate.supporting_evidence_indices == ()

    with pytest.raises(RepairCandidateValidationError, match="must not carry"):
        RepairCandidate(
            kind=RepairCandidateKind.UNKNOWN,
            diagnosis=_implicated_diagnosis(),
            proposed_at=_NOW,
            supporting_evidence_indices=(0,),
        )


def test_unknown_candidate_is_not_a_verdict_that_no_repair_exists() -> None:
    # An UNKNOWN candidate must not claim any decisional property.
    diagnosis = _diagnosis()
    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    for forbidden in (
        "is_suppressed",
        "no_repair_possible",
        "unrecoverable",
        "rejected",
        "authorized",
        "selected",
        "executed",
        "verified",
    ):
        assert not hasattr(candidate, forbidden)


def test_specific_candidate_without_evidence_link_is_rejected() -> None:
    with pytest.raises(RepairCandidateValidationError, match="at least one"):
        RepairCandidate(
            kind=RepairCandidateKind.NODE_DEFINITION_REVISION,
            diagnosis=_implicated_diagnosis(evidence=(_evidence(),)),
            proposed_at=_NOW,
            supporting_evidence_indices=(),
        )


def test_specific_candidate_requires_explicit_node_implicated_conclusion() -> None:
    # No other structured fact (category, localization kind, correlation) may
    # fabricate the specific candidate kind: it fails closed without the
    # explicit typed conclusion in the embedded diagnosis.
    without_conclusion = _diagnosis(evidence=(_evidence(),))
    with pytest.raises(RepairCandidateValidationError, match="NODE_IMPLICATED"):
        RepairCandidate(
            kind=RepairCandidateKind.NODE_DEFINITION_REVISION,
            diagnosis=without_conclusion,
            proposed_at=_NOW,
            supporting_evidence_indices=(0,),
        )

    # A PROCEDURE-category classification with an UNKNOWN conclusion still
    # justifies nothing more specific.
    procedure_flavored = _diagnosis(
        classification=_classification(category=FailureCategory.PROCEDURE),
        evidence=(_evidence(),),
    )
    (candidate,) = derive_repair_candidates(diagnosis=procedure_flavored, proposed_at=_NOW)
    assert candidate.kind is RepairCandidateKind.UNKNOWN


# ---------------------------------------------------------------------------
# Provenance preservation
# ---------------------------------------------------------------------------


def test_provenance_survives_serialization_byte_identically() -> None:
    diagnosis = _implicated_diagnosis(
        classification=_classification(
            error_code="procedure.stale",
            task_id=TaskId.create(),
            episode_id=EpisodeId.create(),
            correlation_id=uuid4(),
        )
    )
    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    restored = RepairCandidate.from_json(candidate.to_json())

    assert restored == candidate
    assert restored.to_json() == candidate.to_json()
    assert restored.diagnosis.to_json() == diagnosis.to_json()
    assert restored.supporting_evidence_indices == candidate.supporting_evidence_indices


def test_linkage_to_localization_subject_is_exact_not_copied() -> None:
    diagnosis = _implicated_diagnosis(
        localization=_localization(procedure_node_id="check"),
    )
    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    restored = RepairCandidate.from_json(candidate.to_json())
    assert restored.diagnosis.procedure_id == diagnosis.procedure_id
    assert restored.diagnosis.procedure_node_id == "check"
    # The candidate carries no duplicate subject fields of its own.
    assert "procedure_id" not in restored.to_dict()
    assert "procedure_node_id" not in restored.to_dict()


def test_real_procedure_graph_node_id_flows_through_and_back_out() -> None:
    nodes = (
        ProcedureNode(ProcedureNodeId("collect"), ProcedureNodeKind.ACTION, label="Collect"),
        ProcedureNode(ProcedureNodeId("check"), ProcedureNodeKind.VERIFY, label="Check"),
    )
    edges = (
        ProcedureEdge(ProcedureNodeId("collect"), ProcedureNodeId("check"), ProcedureEdgeKind.NEXT),
    )
    graph = ProcedureGraph(ProcedureNodeId("collect"), nodes, edges)
    graph_node = next(node for node in graph.nodes if node.id.to_str() == "check")

    diagnosis = _implicated_diagnosis(
        localization=_localization(procedure_node_id=graph_node.id.to_str()),
    )
    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    restored = RepairCandidate.from_json(candidate.to_json())
    assert ProcedureNodeId.parse(restored.diagnosis.procedure_node_id) == graph_node.id
    assert graph_node.id in {node.id for node in graph.nodes}


def test_duplicate_evidence_positions_are_preserved_not_collapsed() -> None:
    # Two byte-identical evidence items at distinct positions are materially
    # distinct diagnostic evidence (each is one fact the diagnosis carries).
    shared = _evidence()
    diagnosis = _implicated_diagnosis(evidence=(shared, shared))
    assert diagnosis.evidence[0] == diagnosis.evidence[1]

    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    assert candidate.supporting_evidence_indices == (0, 1)
    restored = RepairCandidate.from_json(candidate.to_json())
    assert restored.supporting_evidence_indices == (0, 1)
    assert len(restored.diagnosis.evidence) == 2


def test_candidates_from_distinct_diagnoses_are_not_collapsed_together() -> None:
    left = _implicated_diagnosis()
    right = _implicated_diagnosis()  # fresh ids -> materially distinct provenance
    (left_candidate,) = derive_repair_candidates(diagnosis=left, proposed_at=_NOW)
    (right_candidate,) = derive_repair_candidates(diagnosis=right, proposed_at=_NOW)

    assert left_candidate != right_candidate
    assert left_candidate.to_json() != right_candidate.to_json()

    same_left = derive_repair_candidates(diagnosis=left, proposed_at=_NOW)[0]
    assert same_left == left_candidate
    assert same_left.to_json() == left_candidate.to_json()


# ---------------------------------------------------------------------------
# Deterministic representation
# ---------------------------------------------------------------------------


def test_derivation_is_deterministic_across_repeated_calls() -> None:
    diagnosis = _implicated_diagnosis()

    first = [c.to_json() for c in derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)]
    second = [c.to_json() for c in derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)]

    assert first == second


def test_to_dict_is_the_exact_canonical_shape() -> None:
    payload = _candidate().to_dict()

    assert set(payload) == _EXPECTED_CANDIDATE_FIELDS
    assert payload["schema_version"] == REPAIR_CANDIDATE_SCHEMA_VERSION
    assert payload["kind"] == RepairCandidateKind.NODE_DEFINITION_REVISION.value
    assert payload["supporting_evidence_indices"] == [0, 1]
    assert payload["proposed_at"] == "2026-09-05T12:30:15.123456Z"
    assert isinstance(payload["diagnosis"], dict)


def test_to_json_is_deterministic_sorted_and_compact() -> None:
    candidate = _candidate()

    text = candidate.to_json()
    decoded = json.loads(text)

    assert text == json.dumps(decoded, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    # Re-constructing from the decoded payload re-serializes byte-identically.
    assert RepairCandidate.from_dict(decoded).to_json() == text


def test_json_round_trip_preserves_every_canonical_kind() -> None:
    for kind in CANONICAL_REPAIR_CANDIDATE_KINDS:
        diagnosis = (
            _implicated_diagnosis()
            if kind is RepairCandidateKind.NODE_DEFINITION_REVISION
            else _diagnosis()
        )
        indices = (
            tuple(range(len(diagnosis.evidence))) if kind is not RepairCandidateKind.UNKNOWN else ()
        )
        record = RepairCandidate(
            kind=kind,
            diagnosis=diagnosis,
            supporting_evidence_indices=indices,
            proposed_at=_NOW,
        )
        restored = RepairCandidate.from_json(record.to_json())
        assert restored == record
        assert restored.kind is kind


def test_timestamps_round_trip_with_offset_normalization() -> None:
    tz = timezone(timedelta(hours=5, minutes=45))
    shifted = datetime(2026, 9, 5, 18, 15, 15, 123456, tzinfo=tz)
    diagnosis = _implicated_diagnosis()
    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=shifted)

    assert candidate.proposed_at == _NOW
    assert candidate.proposed_at.tzinfo is UTC
    restored = RepairCandidate.from_json(candidate.to_json())
    assert restored.proposed_at == candidate.proposed_at


# ---------------------------------------------------------------------------
# Immutability and structural identity
# ---------------------------------------------------------------------------


def test_records_are_immutable() -> None:
    candidate = _candidate()

    with pytest.raises(FrozenInstanceError):
        candidate.kind = RepairCandidateKind.UNKNOWN  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        candidate.supporting_evidence_indices = (9,)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        candidate.proposed_at = datetime(2020, 1, 1, tzinfo=UTC)  # type: ignore[misc]

    # slots: there is no instance dictionary to smuggle fields through, and
    # the frozen setter refuses unknown attributes as well (the refusal type
    # differs between Python 3.11 and 3.12; any refusal proves the point).
    assert not hasattr(candidate, "__dict__")
    with pytest.raises((AttributeError, TypeError)):
        candidate.injected = "field"  # type: ignore[attr-defined]
    assert not hasattr(candidate, "injected")


def test_equality_and_hashing_are_structural() -> None:
    diagnosis = _implicated_diagnosis()
    first = RepairCandidate(
        kind=RepairCandidateKind.NODE_DEFINITION_REVISION,
        diagnosis=diagnosis,
        supporting_evidence_indices=(0, 1),
        proposed_at=_NOW,
    )
    same = RepairCandidate.from_dict(first.to_dict())
    other = RepairCandidate(
        kind=RepairCandidateKind.NODE_DEFINITION_REVISION,
        diagnosis=diagnosis,
        supporting_evidence_indices=(0,),
        proposed_at=_NOW,
    )

    assert first == same
    assert hash(first) == hash(same)
    assert first != other
    assert len({first, same, other}) == 2


def test_kinds_distinguish_records() -> None:
    diagnosis = _implicated_diagnosis()
    specific = RepairCandidate(
        kind=RepairCandidateKind.NODE_DEFINITION_REVISION,
        diagnosis=diagnosis,
        supporting_evidence_indices=(0, 1),
        proposed_at=_NOW,
    )
    unknown = RepairCandidate(
        kind=RepairCandidateKind.UNKNOWN,
        diagnosis=diagnosis,
        proposed_at=_NOW,
    )
    assert specific != unknown


# ---------------------------------------------------------------------------
# Input discipline: typed records only
# ---------------------------------------------------------------------------


def test_derive_rejects_free_form_diagnosis_payloads() -> None:
    with pytest.raises(RepairCandidateValidationError, match="FailureDiagnosis"):
        derive_repair_candidates(
            diagnosis={"category": "procedure", "summary": "permission denied"},  # type: ignore[arg-type]
            proposed_at=_NOW,
        )
    with pytest.raises(RepairCandidateValidationError, match="FailureDiagnosis"):
        derive_repair_candidates(diagnosis="permission denied", proposed_at=_NOW)  # type: ignore[arg-type]


def test_derive_rejects_exception_objects_and_traceback_strings() -> None:
    with pytest.raises(RepairCandidateValidationError, match="FailureDiagnosis"):
        derive_repair_candidates(diagnosis=ValueError("boom"), proposed_at=_NOW)  # type: ignore[arg-type]
    with pytest.raises(RepairCandidateValidationError, match="FailureDiagnosis"):
        derive_repair_candidates(
            diagnosis="Traceback (most recent call last):\n  File 'app.py', line 1",  # type: ignore[arg-type]
            proposed_at=_NOW,
        )


def test_kind_must_be_typed_member_not_text() -> None:
    with pytest.raises(RepairCandidateValidationError, match="RepairCandidateKind"):
        RepairCandidate(
            kind="node_definition_revision",  # type: ignore[arg-type]
            diagnosis=_implicated_diagnosis(),
            proposed_at=_NOW,
            supporting_evidence_indices=(0, 1),
        )


def test_diagnosis_must_be_canonical_record_not_dict() -> None:
    with pytest.raises(RepairCandidateValidationError, match="FailureDiagnosis"):
        RepairCandidate(
            kind=RepairCandidateKind.UNKNOWN,
            diagnosis=_diagnosis().to_dict(),  # type: ignore[arg-type]
            proposed_at=_NOW,
        )


def test_proposed_at_must_be_timezone_aware_datetime() -> None:
    with pytest.raises(RepairCandidateValidationError, match="proposed_at"):
        RepairCandidate(
            kind=RepairCandidateKind.UNKNOWN,
            diagnosis=_diagnosis(),
            proposed_at=datetime(2026, 9, 5, 12, 0),  # naive
        )
    with pytest.raises(RepairCandidateValidationError, match="proposed_at"):
        RepairCandidate(
            kind=RepairCandidateKind.UNKNOWN,
            diagnosis=_diagnosis(),
            proposed_at=_NOW.isoformat(),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Evidence link rules
# ---------------------------------------------------------------------------


def test_links_must_be_in_range_distinct_and_increasing() -> None:
    diagnosis = _implicated_diagnosis(evidence=(_evidence(), _evidence()))

    with pytest.raises(RepairCandidateValidationError, match="existing evidence items"):
        RepairCandidate(
            kind=RepairCandidateKind.NODE_DEFINITION_REVISION,
            diagnosis=diagnosis,
            supporting_evidence_indices=(2,),
            proposed_at=_NOW,
        )
    with pytest.raises(RepairCandidateValidationError, match="strictly increasing"):
        RepairCandidate(
            kind=RepairCandidateKind.NODE_DEFINITION_REVISION,
            diagnosis=diagnosis,
            supporting_evidence_indices=(1, 0),
            proposed_at=_NOW,
        )
    with pytest.raises(RepairCandidateValidationError, match="strictly increasing"):
        RepairCandidate(
            kind=RepairCandidateKind.NODE_DEFINITION_REVISION,
            diagnosis=diagnosis,
            supporting_evidence_indices=(0, 0),
            proposed_at=_NOW,
        )


def test_links_must_be_a_tuple_of_plain_integers() -> None:
    diagnosis = _implicated_diagnosis(evidence=(_evidence(),))

    with pytest.raises(RepairCandidateValidationError, match="tuple"):
        RepairCandidate(
            kind=RepairCandidateKind.NODE_DEFINITION_REVISION,
            diagnosis=diagnosis,
            supporting_evidence_indices=[0],  # type: ignore[arg-type]
            proposed_at=_NOW,
        )
    for bad in (True, "0", 0.0, None):
        with pytest.raises(RepairCandidateValidationError, match="integers"):
            RepairCandidate(
                kind=RepairCandidateKind.NODE_DEFINITION_REVISION,
                diagnosis=diagnosis,
                supporting_evidence_indices=(bad,),  # type: ignore[arg-type]
                proposed_at=_NOW,
            )


def test_negative_link_is_rejected() -> None:
    diagnosis = _implicated_diagnosis(evidence=(_evidence(),))
    with pytest.raises(RepairCandidateValidationError, match="existing evidence items"):
        RepairCandidate(
            kind=RepairCandidateKind.NODE_DEFINITION_REVISION,
            diagnosis=diagnosis,
            supporting_evidence_indices=(-1,),
            proposed_at=_NOW,
        )


# ---------------------------------------------------------------------------
# Malformed serialization input fails closed
# ---------------------------------------------------------------------------


def test_from_dict_rejects_unknown_kind_instead_of_downgrading() -> None:
    with pytest.raises(RepairCandidateDeserializationError, match="kind must be one of"):
        RepairCandidate.from_dict(_candidate_payload(kind="patch_it"))

    with pytest.raises(RepairCandidateDeserializationError, match="kind must be a string"):
        RepairCandidate.from_dict(_candidate_payload(kind={"grant": True}))


def test_from_dict_rejects_malformed_schema_versions() -> None:
    for bad in ("1", 1.0, None, True, 2, 0, -1):
        with pytest.raises(
            (
                UnsupportedRepairCandidateSchemaVersionError,
                RepairCandidateDeserializationError,
            )
        ):
            RepairCandidate.from_dict(_candidate_payload(schema_version=bad))


def test_from_dict_requires_the_exact_field_set() -> None:
    with pytest.raises(RepairCandidateDeserializationError, match="missing required fields"):
        RepairCandidate.from_dict({"schema_version": 1})

    smuggled = _candidate_payload()
    smuggled["authority"] = "kernel"
    with pytest.raises(RepairCandidateDeserializationError, match="unknown fields"):
        RepairCandidate.from_dict(smuggled)

    selected = _candidate_payload()
    selected["selected"] = True
    with pytest.raises(RepairCandidateDeserializationError, match="unknown fields"):
        RepairCandidate.from_dict(selected)


def test_from_dict_rejects_malformed_link_arrays() -> None:
    bad_arrays: tuple[object, ...] = ({}, "01", 0, None, True)
    for bad in bad_arrays:
        with pytest.raises(RepairCandidateDeserializationError, match="JSON array"):
            RepairCandidate.from_dict(_candidate_payload(supporting_evidence_indices=bad))

    bad_entries: tuple[object, ...] = (True, "0", 1.5, None, [0])
    for bad_entry in bad_entries:
        with pytest.raises(RepairCandidateDeserializationError, match="integers"):
            RepairCandidate.from_dict(
                _candidate_payload(supporting_evidence_indices=[0, bad_entry])
            )


def test_from_dict_rejects_malformed_timestamps() -> None:
    for bad in ("not-a-time", None, 12345, "2026-09-05T12:30:15"):
        with pytest.raises(RepairCandidateDeserializationError, match="proposed_at"):
            RepairCandidate.from_dict(_candidate_payload(proposed_at=bad))


def test_from_dict_wraps_invalid_nested_diagnosis() -> None:
    bad_diagnoses: tuple[object, ...] = (None, "diagnosis", ["x"], {})
    for bad in bad_diagnoses:
        with pytest.raises(RepairCandidateDeserializationError, match="diagnosis"):
            RepairCandidate.from_dict(_candidate_payload(diagnosis=bad))

    payload = _candidate_payload()
    broken = _nested_diagnosis(payload)
    broken["conclusion"] = "node_implicated_but_claimed"
    payload["diagnosis"] = broken
    with pytest.raises(RepairCandidateDeserializationError, match="conclusion"):
        RepairCandidate.from_dict(payload)


def test_from_json_fails_closed_on_hostile_or_malformed_root_documents() -> None:
    for bad in (
        "not json",
        "[]",
        '"node_definition_revision"',
        "null",
        "[1, 2, 3]",
    ):
        with pytest.raises(RepairCandidateDeserializationError):
            RepairCandidate.from_json(bad)
    with pytest.raises(RepairCandidateDeserializationError, match="must be text"):
        RepairCandidate.from_json({"kind": "unknown"})  # type: ignore[arg-type]


def test_valid_payload_with_unknown_conclusion_and_specific_kind_fails_closed() -> None:
    # A crafted payload pairing a specific kind with a diagnosis whose
    # conclusion is UNKNOWN is rejected, never coerced and never downgraded.
    diagnosis = _diagnosis(evidence=(_evidence(),))
    payload = {
        "schema_version": REPAIR_CANDIDATE_SCHEMA_VERSION,
        "kind": RepairCandidateKind.NODE_DEFINITION_REVISION.value,
        "diagnosis": diagnosis.to_dict(),
        "supporting_evidence_indices": [0],
        "proposed_at": "2026-09-05T12:30:15.123456Z",
    }

    with pytest.raises(RepairCandidateDeserializationError, match="NODE_IMPLICATED"):
        RepairCandidate.from_dict(payload)


# ---------------------------------------------------------------------------
# Category orthogonality and C4.01-C4.03 compatibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", CANONICAL_FAILURE_CATEGORIES)
def test_every_canonical_category_is_orthogonal_to_the_candidate_kind(
    category: FailureCategory,
) -> None:
    diagnosis = _implicated_diagnosis(classification=_classification(category=category))

    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    # The candidate kind depends only on the typed conclusion, never on the
    # category; even the UNKNOWN category with a justified conclusion yields
    # the same candidate treatment as any other.
    assert candidate.kind is RepairCandidateKind.NODE_DEFINITION_REVISION
    assert candidate.diagnosis.classification.category is category


def test_records_built_through_c401_c402_c403_apis_round_trip() -> None:
    classification = FailureClassification(
        category=FailureCategory.UI_CHANGE,
        summary="The observed UI surface no longer matched.",
        classified_at=_NOW,
    )
    localization = FailureLocalization(
        kind=FailureLocationKind.PROCEDURE_NODE,
        summary="Evidence points at a procedure node.",
        localized_at=_NOW,
        procedure_id=ProcedureId.create(),
        procedure_node_id="click-submit",
    )
    evidence = DiagnosticEvidence(
        kind=DiagnosticEvidenceKind.NEGATIVE_EXPERIENCE,
        negative_experience_id=NegativeExperienceId.create(),
    )
    diagnosis = package_diagnosis(
        classification=classification,
        localization=localization,
        summary="Node implicated by an explicit negative-experience reference.",
        diagnosed_at=_NOW,
        evidence=(evidence,),
        conclusion=DiagnosticConclusion.NODE_IMPLICATED,
    )

    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)
    restored = RepairCandidate.from_json(candidate.to_json())

    assert restored == candidate
    assert restored.diagnosis.localization.kind is FailureLocationKind.PROCEDURE_NODE
    assert restored.diagnosis.classification.category is FailureCategory.UI_CHANGE


def test_serialization_uses_no_object_hooks_or_executable_types() -> None:
    payload = _candidate().to_dict()

    def assert_plain(value: object) -> None:
        assert isinstance(value, (dict, list, str, int, float, bool, type(None)))
        if isinstance(value, dict):
            for item in value.values():
                assert_plain(item)
        elif isinstance(value, list):
            for item in value:
                assert_plain(item)

    assert_plain(payload)
    assert not isinstance(payload["schema_version"], bool)


# ---------------------------------------------------------------------------
# The candidate/decision gap
# ---------------------------------------------------------------------------


def test_candidate_carries_no_selection_authorization_execution_or_verification_state() -> None:
    diagnosis = _implicated_diagnosis()
    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    payload = candidate.to_dict()
    for forbidden in ("selected", "chosen", "authorized", "executed", "verified", "applied"):
        assert forbidden not in payload
        assert not hasattr(candidate, forbidden)

    for forbidden in (
        "select",
        "authorize",
        "apply",
        "execute",
        "verify",
        "mark_done",
        "promote",
    ):
        assert not hasattr(candidate, forbidden)


def test_serialized_candidate_never_claims_safety_or_correctness() -> None:
    text = _candidate().to_json()
    lowered = text.lower()

    for claim in ('"safe"', '"selected"', '"authorized"', '"executed"', '"verified"'):
        assert claim not in lowered


# ---------------------------------------------------------------------------
# Free-text hostility can never fabricate a repair
# ---------------------------------------------------------------------------


def test_keyword_bait_in_diagnosis_text_cannot_change_the_derived_candidate() -> None:
    bait = (
        "permission denied; rollback to v2; grant WRITE; verified=true; "
        "repair=approved; patch the node now; root cause confirmed"
    )
    diagnosis = _diagnosis(summary=bait, detail=bait.replace(";", "\n"))

    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    assert candidate.kind is RepairCandidateKind.UNKNOWN
    assert candidate.supporting_evidence_indices == ()
    assert candidate.diagnosis.summary == bait  # inert text, preserved verbatim


def test_hostile_text_cannot_forge_the_specific_kind_without_a_conclusion() -> None:
    bait = "NODE_IMPLICATED node_definition_revision"
    diagnosis = _diagnosis(summary=bait)

    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_NOW)

    assert candidate.kind is RepairCandidateKind.UNKNOWN
    conclusion_value = candidate.diagnosis.to_dict()["conclusion"]
    assert isinstance(conclusion_value, str)
    assert conclusion_value != "node_implicated"


def test_strings_are_never_evidence_and_cannot_create_links() -> None:
    # Free text cannot enter even the embedded diagnosis, so no candidate can
    # ever cite a string, a log line, or a webpage snippet as evidence.
    with pytest.raises(FailureDiagnosisValidationError, match="DiagnosticEvidence"):
        _diagnosis(evidence=("permission denied",))
    with pytest.raises(FailureDiagnosisValidationError, match="tuple"):
        _diagnosis(evidence=[_evidence()])
