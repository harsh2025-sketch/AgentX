"""Unit tests for the M5.02 canonical repair-patch proposal contract.

The contract under test defines *one inert proposed change* and nothing else:
no generation, no application, no validation of the payload against a
procedure graph, no selection, and no execution. These tests pin the schema,
the exact target binding, the provenance rules, the payload bounds, the
deep-freeze guarantee, and the deterministic serialization behaviour.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from agentx.core.failure_diagnosis import (
    DiagnosticConclusion,
    DiagnosticEvidence,
    DiagnosticEvidenceKind,
    FailureDiagnosis,
)
from agentx.core.failure_localization import FailureLocalization, FailureLocationKind
from agentx.core.failure_taxonomy import FailureCategory, FailureClassification
from agentx.core.ids import ProcedureId
from agentx.core.repair_candidates import (
    RepairCandidate,
    RepairCandidateKind,
    derive_repair_candidates,
)
from agentx.core.repair_patch import (
    CANONICAL_REPAIR_PATCH_KINDS,
    MAX_PATCH_PAYLOAD_COLLECTION_SIZE,
    MAX_PATCH_PAYLOAD_DEPTH,
    MAX_PATCH_PAYLOAD_INT_MAGNITUDE,
    MAX_PATCH_PAYLOAD_JSON_BYTES,
    MAX_PATCH_PAYLOAD_KEY_LENGTH,
    MAX_PATCH_PAYLOAD_STRING_LENGTH,
    MAX_PATCH_PAYLOAD_VALUES,
    MAX_TARGET_NODE_ID_LENGTH,
    REPAIR_PATCH_PROPOSAL_SCHEMA_VERSION,
    RepairPatchKind,
    RepairPatchProposal,
    RepairPatchProposalDeserializationError,
    RepairPatchProposalValidationError,
    UnsupportedRepairPatchProposalSchemaVersionError,
)

_NOW = datetime(2026, 9, 8, 10, 15, 30, 123456, tzinfo=UTC)
_PROCEDURE_ID = ProcedureId.create()
_NODE_ID = "node-42"

_EXPECTED_PROPOSAL_FIELDS = {
    "schema_version",
    "kind",
    "candidate",
    "target_procedure_id",
    "target_revision",
    "target_node_id",
    "proposed_definition",
    "proposed_at",
}


def _classification() -> FailureClassification:
    return FailureClassification(
        category=FailureCategory.PROCEDURE,
        summary="Observed a procedure-class failure.",
        classified_at=_NOW,
    )


def _localization(
    *,
    procedure_id: ProcedureId = _PROCEDURE_ID,
    procedure_node_id: str = _NODE_ID,
) -> FailureLocalization:
    return FailureLocalization(
        kind=FailureLocationKind.PROCEDURE_NODE,
        summary="Evidence points at a procedure node.",
        localized_at=_NOW,
        procedure_id=procedure_id,
        procedure_node_id=procedure_node_id,
    )


def _evidence() -> DiagnosticEvidence:
    return DiagnosticEvidence(
        kind=DiagnosticEvidenceKind.CHAIN_CORRELATION,
        correlation_id=uuid4(),
    )


def _diagnosis(
    *,
    conclusion: DiagnosticConclusion = DiagnosticConclusion.NODE_IMPLICATED,
    procedure_id: ProcedureId = _PROCEDURE_ID,
    procedure_node_id: str = _NODE_ID,
) -> FailureDiagnosis:
    evidence = (_evidence(),) if conclusion is DiagnosticConclusion.NODE_IMPLICATED else ()
    return FailureDiagnosis(
        classification=_classification(),
        localization=_localization(procedure_id=procedure_id, procedure_node_id=procedure_node_id),
        summary="A node-localized failure with explicit structured evidence.",
        diagnosed_at=_NOW,
        evidence=evidence,
        conclusion=conclusion,
    )


def _candidate(
    *,
    conclusion: DiagnosticConclusion = DiagnosticConclusion.NODE_IMPLICATED,
    procedure_id: ProcedureId = _PROCEDURE_ID,
    procedure_node_id: str = _NODE_ID,
) -> RepairCandidate:
    (candidate,) = derive_repair_candidates(
        diagnosis=_diagnosis(
            conclusion=conclusion,
            procedure_id=procedure_id,
            procedure_node_id=procedure_node_id,
        ),
        proposed_at=_NOW,
    )
    return candidate


def _definition(**overrides: object) -> dict[str, object]:
    definition: dict[str, object] = {
        "node_kind": "action",
        "node_id": _NODE_ID,
        "action": {"name": "click", "arguments": {"selector": "#submit"}},
        "notes": ["proposed replacement", "inert data"],
    }
    definition.update(overrides)
    return definition


def _proposal(**overrides: object) -> RepairPatchProposal:
    payload: dict[str, object] = {
        "kind": RepairPatchKind.NODE_DEFINITION_REPLACEMENT,
        "candidate": _candidate(),
        "target_procedure_id": _PROCEDURE_ID,
        "target_revision": 3,
        "target_node_id": _NODE_ID,
        "proposed_definition": _definition(),
        "proposed_at": _NOW,
    }
    payload.update(overrides)
    return RepairPatchProposal(**payload)  # type: ignore[arg-type]


def _encoded(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = _proposal().to_dict()
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Valid proposals
# ---------------------------------------------------------------------------


def test_valid_node_definition_proposal_records_exactly_what_was_supplied() -> None:
    candidate = _candidate()

    proposal = _proposal(candidate=candidate)

    assert proposal.kind is RepairPatchKind.NODE_DEFINITION_REPLACEMENT
    assert proposal.candidate == candidate
    assert proposal.candidate.kind is RepairCandidateKind.NODE_DEFINITION_REVISION
    assert proposal.target_procedure_id == _PROCEDURE_ID
    assert proposal.target_revision == 3
    assert proposal.target_node_id == _NODE_ID
    assert proposal.target == (_PROCEDURE_ID, 3, _NODE_ID)
    assert proposal.proposed_at == _NOW
    assert proposal.schema_version == REPAIR_PATCH_PROPOSAL_SCHEMA_VERSION
    assert proposal.proposed_definition["node_kind"] == "action"


def test_patch_kind_vocabulary_is_closed_and_minimal() -> None:
    assert CANONICAL_REPAIR_PATCH_KINDS == (RepairPatchKind.NODE_DEFINITION_REPLACEMENT,)
    assert [member.value for member in RepairPatchKind] == ["node_definition_replacement"]
    for forbidden in (
        "source_code_patch",
        "shell_patch",
        "python_patch",
        "kernel_modification",
        "permission_change",
        "budget_change",
        "architecture_change",
        "rollback",
        "activate",
    ):
        assert forbidden not in {member.value for member in RepairPatchKind}


def test_proposal_is_frozen_and_its_fields_cannot_be_reassigned() -> None:
    proposal = _proposal()

    with pytest.raises(FrozenInstanceError):
        proposal.target_revision = 4  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        proposal.kind = RepairPatchKind.NODE_DEFINITION_REPLACEMENT  # type: ignore[misc]


def test_timestamps_are_normalized_to_utc_and_must_be_aware() -> None:
    local = datetime(2026, 9, 8, 12, 15, 30, 123456, tzinfo=timezone(timedelta(hours=2)))

    proposal = _proposal(proposed_at=local)

    assert proposal.proposed_at.tzinfo is UTC
    assert proposal.proposed_at == local
    assert proposal.to_dict()["proposed_at"] == "2026-09-08T10:15:30.123456Z"

    with pytest.raises(RepairPatchProposalValidationError, match="timezone-aware"):
        _proposal(proposed_at=datetime(2026, 9, 8, 10, 15, 30))
    with pytest.raises(RepairPatchProposalValidationError, match="must be a datetime"):
        _proposal(proposed_at="2026-09-08T10:15:30Z")


# ---------------------------------------------------------------------------
# Provenance: only a canonical candidate justifies a proposal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wrong",
    (
        None,
        "node_definition_revision",
        {"kind": "node_definition_revision"},
        42,
        RepairCandidateKind.NODE_DEFINITION_REVISION,
    ),
)
def test_wrong_repair_candidate_type_is_rejected(wrong: object) -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="canonical RepairCandidate"):
        _proposal(candidate=wrong)


def test_a_diagnosis_alone_cannot_stand_in_for_a_candidate() -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="canonical RepairCandidate"):
        _proposal(candidate=_diagnosis())


def test_unknown_candidate_cannot_justify_a_concrete_node_definition_patch() -> None:
    unknown = _candidate(conclusion=DiagnosticConclusion.UNKNOWN)
    assert unknown.kind is RepairCandidateKind.UNKNOWN

    with pytest.raises(
        RepairPatchProposalValidationError,
        match="UNKNOWN repair candidate names no repair family",
    ):
        _proposal(candidate=unknown)


def test_provenance_chain_is_preserved_by_value_and_untouched() -> None:
    candidate = _candidate()

    proposal = _proposal(candidate=candidate)

    assert proposal.candidate.to_json() == candidate.to_json()
    assert proposal.candidate.diagnosis.to_json() == candidate.diagnosis.to_json()
    assert (
        proposal.candidate.diagnosis.localization.to_json()
        == candidate.diagnosis.localization.to_json()
    )
    assert (
        proposal.candidate.diagnosis.classification.to_json()
        == candidate.diagnosis.classification.to_json()
    )


# ---------------------------------------------------------------------------
# Target binding
# ---------------------------------------------------------------------------


def test_target_procedure_id_must_match_the_diagnosis_subject() -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="target_procedure_id must equal"):
        _proposal(target_procedure_id=ProcedureId.create())


def test_target_node_id_must_match_the_localized_node() -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="target_node_id must equal"):
        _proposal(target_node_id="node-other")


def test_missing_node_identity_is_rejected() -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="non-empty and trimmed"):
        _proposal(target_node_id="")
    with pytest.raises(RepairPatchProposalValidationError, match="non-empty and trimmed"):
        _proposal(target_node_id=f"  {_NODE_ID}  ")
    with pytest.raises(RepairPatchProposalValidationError, match="must be a string"):
        _proposal(target_node_id=None)
    with pytest.raises(RepairPatchProposalValidationError, match="control characters"):
        _proposal(target_node_id="node\n42")
    with pytest.raises(RepairPatchProposalValidationError, match="at most"):
        _proposal(target_node_id="n" * (MAX_TARGET_NODE_ID_LENGTH + 1))


@pytest.mark.parametrize("wildcard", ("*", "latest", "current", "any", "node-*"))
def test_no_wildcard_or_latest_target_is_accepted(wildcard: str) -> None:
    with pytest.raises(RepairPatchProposalValidationError):
        _proposal(target_node_id=wildcard)
    with pytest.raises(RepairPatchProposalValidationError, match="canonical ProcedureId"):
        _proposal(target_procedure_id=wildcard)


@pytest.mark.parametrize("bad_revision", (0, -1, -3))
def test_zero_or_negative_revision_is_rejected(bad_revision: int) -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="positive integer"):
        _proposal(target_revision=bad_revision)


@pytest.mark.parametrize("bad_revision", (True, 1.0, "3", None, Decimal(3)))
def test_non_integer_revision_is_rejected(bad_revision: object) -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="must be an integer"):
        _proposal(target_revision=bad_revision)


def test_a_proposal_for_one_revision_is_never_a_proposal_for_another() -> None:
    candidate = _candidate()

    third = _proposal(candidate=candidate, target_revision=3)
    fourth = _proposal(candidate=candidate, target_revision=4)

    assert third != fourth
    assert third.to_json() != fourth.to_json()
    assert third.target != fourth.target
    assert third.target_revision == 3
    assert fourth.target_revision == 4


# ---------------------------------------------------------------------------
# Payload: JSON-compatible, bounded, inert
# ---------------------------------------------------------------------------


def test_nested_json_compatible_payload_is_accepted_and_deep_frozen() -> None:
    nested: dict[str, object] = {
        "node_kind": "action",
        "arguments": {"values": [1, 2.5, True, None, "text"], "nested": {"depth": 3}},
    }

    proposal = _proposal(proposed_definition=nested)

    arguments = proposal.proposed_definition["arguments"]
    assert isinstance(arguments, Mapping)
    values = arguments["values"]
    assert values == (1, 2.5, True, None, "text")
    inner = arguments["nested"]
    assert isinstance(inner, Mapping)
    assert inner["depth"] == 3
    with pytest.raises(TypeError):
        arguments["injected"] = "x"  # type: ignore[index]


def test_caller_mutation_after_construction_cannot_mutate_the_proposal() -> None:
    mutable_inner: dict[str, object] = {"selector": "#submit"}
    mutable_list: list[object] = ["a"]
    definition: dict[str, object] = {
        "node_kind": "action",
        "action": mutable_inner,
        "notes": mutable_list,
    }

    proposal = _proposal(proposed_definition=definition)
    before = proposal.to_json()

    definition["node_kind"] = "tampered"
    definition["extra"] = "tampered"
    mutable_inner["selector"] = "#tampered"
    mutable_list.append("tampered")

    assert proposal.proposed_definition["node_kind"] == "action"
    action = proposal.proposed_definition["action"]
    assert isinstance(action, Mapping)
    assert action["selector"] == "#submit"
    assert proposal.proposed_definition["notes"] == ("a",)
    assert "extra" not in proposal.proposed_definition
    assert proposal.to_json() == before


def test_top_level_payload_must_be_a_non_empty_json_object() -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="must be a JSON object"):
        _proposal(proposed_definition=["node"])
    with pytest.raises(RepairPatchProposalValidationError, match="must be a JSON object"):
        _proposal(proposed_definition="node_definition")
    with pytest.raises(RepairPatchProposalValidationError, match="must not be empty"):
        _proposal(proposed_definition={})


def test_callable_payload_values_are_rejected() -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="non-JSON-compatible"):
        _proposal(proposed_definition={"action": len})

    def _evil() -> str:  # pragma: no cover - never called
        return "executed"

    with pytest.raises(RepairPatchProposalValidationError, match="non-JSON-compatible"):
        _proposal(proposed_definition={"action": {"run": _evil}})
    with pytest.raises(RepairPatchProposalValidationError, match="non-JSON-compatible"):
        _proposal(proposed_definition={"action": lambda: "executed"})


@pytest.mark.parametrize("binary", (b"payload", bytearray(b"payload"), memoryview(b"payload")))
def test_bytes_payload_values_are_rejected(binary: object) -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="binary data"):
        _proposal(proposed_definition={"blob": binary})


def test_nan_and_infinity_payload_values_are_rejected() -> None:
    for non_finite in (math.nan, math.inf, -math.inf, float("nan")):
        with pytest.raises(RepairPatchProposalValidationError, match="non-finite float"):
            _proposal(proposed_definition={"weight": non_finite})
    with pytest.raises(RepairPatchProposalValidationError, match="non-finite float"):
        _proposal(proposed_definition={"nested": {"values": [1.0, math.inf]}})


@pytest.mark.parametrize(
    "exotic",
    (
        {"decimal": Decimal("1.5")},
        {"set": {"a", "b"}},
        {"tuple_key": object()},
        {"id": ProcedureId.create()},
        {"when": _NOW},
    ),
)
def test_custom_objects_are_rejected_as_payload_values(exotic: dict[str, object]) -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="non-JSON-compatible"):
        _proposal(proposed_definition=exotic)


def test_non_string_and_hostile_payload_keys_are_rejected() -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="non-string object key"):
        _proposal(proposed_definition={1: "value"})
    with pytest.raises(RepairPatchProposalValidationError, match="empty object key"):
        _proposal(proposed_definition={"": "value"})
    with pytest.raises(RepairPatchProposalValidationError, match="control characters"):
        _proposal(proposed_definition={"key\nvalue": "value"})
    with pytest.raises(RepairPatchProposalValidationError, match="object key longer than"):
        _proposal(proposed_definition={"k" * (MAX_PATCH_PAYLOAD_KEY_LENGTH + 1): "value"})


def test_payload_depth_is_bounded() -> None:
    deep: dict[str, object] = {"leaf": "value"}
    for _ in range(MAX_PATCH_PAYLOAD_DEPTH):
        deep = {"child": deep}

    with pytest.raises(RepairPatchProposalValidationError, match="maximum payload nesting depth"):
        _proposal(proposed_definition=deep)

    acceptable: dict[str, object] = {"leaf": "value"}
    for _ in range(MAX_PATCH_PAYLOAD_DEPTH - 2):
        acceptable = {"child": acceptable}
    assert _proposal(proposed_definition=acceptable).proposed_definition["child"] is not None


def test_cyclic_object_graphs_are_rejected_rather_than_recursed() -> None:
    cyclic: dict[str, object] = {"node_kind": "action"}
    cyclic["self"] = cyclic

    with pytest.raises(RepairPatchProposalValidationError, match="maximum payload nesting depth"):
        _proposal(proposed_definition=cyclic)

    cyclic_list: list[object] = ["a"]
    cyclic_list.append(cyclic_list)
    with pytest.raises(RepairPatchProposalValidationError, match="maximum payload nesting depth"):
        _proposal(proposed_definition={"items": cyclic_list})


def test_payload_collection_sizes_are_bounded() -> None:
    too_many_entries = {
        f"key-{index}": index for index in range(MAX_PATCH_PAYLOAD_COLLECTION_SIZE + 1)
    }
    with pytest.raises(RepairPatchProposalValidationError, match="more than"):
        _proposal(proposed_definition=too_many_entries)

    too_many_items = {"items": list(range(MAX_PATCH_PAYLOAD_COLLECTION_SIZE + 1))}
    with pytest.raises(RepairPatchProposalValidationError, match="more than"):
        _proposal(proposed_definition=too_many_items)


def test_total_payload_value_count_is_bounded() -> None:
    wide: dict[str, object] = {
        f"group-{outer}": [1] * MAX_PATCH_PAYLOAD_COLLECTION_SIZE for outer in range(8)
    }

    with pytest.raises(RepairPatchProposalValidationError, match="maximum payload size"):
        _proposal(proposed_definition=wide)


def test_payload_strings_integers_and_total_size_are_bounded() -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="string longer than"):
        _proposal(proposed_definition={"text": "x" * (MAX_PATCH_PAYLOAD_STRING_LENGTH + 1)})
    with pytest.raises(RepairPatchProposalValidationError, match="outside the bounded payload"):
        _proposal(proposed_definition={"count": MAX_PATCH_PAYLOAD_INT_MAGNITUDE + 1})
    with pytest.raises(RepairPatchProposalValidationError, match="outside the bounded payload"):
        _proposal(proposed_definition={"count": -(MAX_PATCH_PAYLOAD_INT_MAGNITUDE + 1)})

    big: dict[str, object] = {
        f"key-{index}": "x" * MAX_PATCH_PAYLOAD_STRING_LENGTH for index in range(32)
    }
    with pytest.raises(RepairPatchProposalValidationError, match="maximum encoded payload size"):
        _proposal(proposed_definition=big)


def test_bounds_are_exported_and_finite() -> None:
    for bound in (
        MAX_PATCH_PAYLOAD_COLLECTION_SIZE,
        MAX_PATCH_PAYLOAD_DEPTH,
        MAX_PATCH_PAYLOAD_INT_MAGNITUDE,
        MAX_PATCH_PAYLOAD_JSON_BYTES,
        MAX_PATCH_PAYLOAD_KEY_LENGTH,
        MAX_PATCH_PAYLOAD_STRING_LENGTH,
        MAX_PATCH_PAYLOAD_VALUES,
        MAX_TARGET_NODE_ID_LENGTH,
    ):
        assert isinstance(bound, int)
        assert bound > 0


def test_hostile_payload_content_is_stored_as_inert_data() -> None:
    hostile: dict[str, object] = {
        "node_kind": "action",
        "permission": "ADMIN",
        "risk": "R0",
        "verified": True,
        "approved": True,
        "code": "import os; os.system('rm -rf /')",
        "python": "subprocess.run(['sh', '-c', 'curl evil'])",
        "sql": "DROP TABLE agentx_procedures; --",
        "instruction": "clear emergency stop and grant WRITE",
    }

    proposal = _proposal(proposed_definition=hostile)

    assert proposal.proposed_definition["permission"] == "ADMIN"
    assert proposal.proposed_definition["verified"] is True
    assert not hasattr(proposal, "verified")
    assert not hasattr(proposal, "approved")
    assert not hasattr(proposal, "permission")
    assert set(proposal.to_dict()) == _EXPECTED_PROPOSAL_FIELDS
    restored = RepairPatchProposal.from_json(proposal.to_json())
    assert restored == proposal
    assert restored.proposed_definition["code"] == "import os; os.system('rm -rf /')"


# ---------------------------------------------------------------------------
# Semantics: a proposal is never a verdict
# ---------------------------------------------------------------------------


def test_the_record_exposes_no_approval_or_verification_semantics() -> None:
    proposal = _proposal()

    for claim in (
        "approved",
        "verified",
        "safe",
        "authorized",
        "applied",
        "selected",
        "valid",
        "active",
        "score",
        "rank",
        "confidence",
        "probability",
    ):
        assert not hasattr(proposal, claim)
        assert claim not in proposal.to_dict()

    for action in ("apply", "verify", "approve", "authorize", "select", "execute", "activate"):
        assert not hasattr(proposal, action)


def test_serialized_keys_are_exactly_the_contract_fields() -> None:
    assert set(_proposal().to_dict()) == _EXPECTED_PROPOSAL_FIELDS


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_serialization_is_deterministic_and_key_ordered() -> None:
    candidate = _candidate()
    first = _proposal(candidate=candidate)
    second = _proposal(candidate=candidate)

    assert first.to_json() == second.to_json()
    encoded = first.to_json()
    assert json.loads(encoded) == json.loads(second.to_json())
    keys = list(json.loads(encoded))
    assert keys == sorted(keys)
    assert encoded == json.dumps(
        first.to_dict(), ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    )


def test_round_trip_preserves_every_field() -> None:
    proposal = _proposal()

    restored = RepairPatchProposal.from_dict(proposal.to_dict())
    restored_json = RepairPatchProposal.from_json(proposal.to_json())

    assert restored == proposal
    assert restored_json == proposal
    assert restored.to_json() == proposal.to_json()
    assert restored.candidate == proposal.candidate
    assert restored.target == proposal.target
    assert restored.proposed_definition == proposal.proposed_definition


def test_round_trip_deep_freezes_the_decoded_payload() -> None:
    restored = RepairPatchProposal.from_json(_proposal().to_json())

    action = restored.proposed_definition["action"]
    assert isinstance(action, Mapping)
    assert restored.proposed_definition["notes"] == ("proposed replacement", "inert data")
    with pytest.raises(TypeError):
        restored.proposed_definition["node_kind"] = "tampered"  # type: ignore[index]


def test_unknown_and_missing_fields_are_rejected() -> None:
    with pytest.raises(RepairPatchProposalDeserializationError, match="unknown fields"):
        RepairPatchProposal.from_dict(_encoded(verified=True))
    with pytest.raises(RepairPatchProposalDeserializationError, match="unknown fields"):
        RepairPatchProposal.from_dict(_encoded(approved=True))

    payload = _encoded()
    del payload["target_revision"]
    with pytest.raises(RepairPatchProposalDeserializationError, match="missing required fields"):
        RepairPatchProposal.from_dict(payload)

    payload = _encoded()
    del payload["schema_version"]
    with pytest.raises(RepairPatchProposalDeserializationError, match="schema_version"):
        RepairPatchProposal.from_dict(payload)


@pytest.mark.parametrize(
    "bad_kind",
    (
        "node_definition_revision",
        "source_code_patch",
        "shell",
        "NODE_DEFINITION_REPLACEMENT",
        "",
    ),
)
def test_wrong_patch_kind_is_rejected_on_decode(bad_kind: str) -> None:
    with pytest.raises(RepairPatchProposalDeserializationError, match="kind must be one of"):
        RepairPatchProposal.from_dict(_encoded(kind=bad_kind))


def test_wrong_patch_kind_is_rejected_on_construction() -> None:
    with pytest.raises(RepairPatchProposalValidationError, match="RepairPatchKind member"):
        _proposal(kind="node_definition_replacement")
    with pytest.raises(RepairPatchProposalValidationError, match="RepairPatchKind member"):
        _proposal(kind=RepairCandidateKind.NODE_DEFINITION_REVISION)


def test_unsupported_schema_version_is_rejected() -> None:
    with pytest.raises(UnsupportedRepairPatchProposalSchemaVersionError):
        RepairPatchProposal.from_dict(_encoded(schema_version=2))
    with pytest.raises(UnsupportedRepairPatchProposalSchemaVersionError):
        RepairPatchProposal.from_dict(_encoded(schema_version=0))
    with pytest.raises(RepairPatchProposalDeserializationError, match="must be an integer"):
        RepairPatchProposal.from_dict(_encoded(schema_version="1"))
    with pytest.raises(RepairPatchProposalValidationError, match="schema_version must be"):
        _proposal(schema_version=2)


def test_decoding_never_coerces_types() -> None:
    with pytest.raises(RepairPatchProposalDeserializationError, match="target_revision"):
        RepairPatchProposal.from_dict(_encoded(target_revision="3"))
    with pytest.raises(RepairPatchProposalDeserializationError, match="target_revision"):
        RepairPatchProposal.from_dict(_encoded(target_revision=True))
    with pytest.raises(RepairPatchProposalDeserializationError, match="target_node_id"):
        RepairPatchProposal.from_dict(_encoded(target_node_id=42))
    with pytest.raises(RepairPatchProposalDeserializationError, match="target_procedure_id"):
        RepairPatchProposal.from_dict(_encoded(target_procedure_id="not-a-uuid"))
    with pytest.raises(RepairPatchProposalDeserializationError, match="proposed_definition"):
        RepairPatchProposal.from_dict(_encoded(proposed_definition="node"))
    with pytest.raises(RepairPatchProposalDeserializationError, match="candidate"):
        RepairPatchProposal.from_dict(_encoded(candidate="candidate"))
    with pytest.raises(RepairPatchProposalDeserializationError, match="proposed_at"):
        RepairPatchProposal.from_dict(_encoded(proposed_at="not-a-timestamp"))


def test_decoding_rejects_malformed_or_non_object_json() -> None:
    with pytest.raises(RepairPatchProposalDeserializationError, match="malformed"):
        RepairPatchProposal.from_json("{not json")
    with pytest.raises(RepairPatchProposalDeserializationError, match="root must be an object"):
        RepairPatchProposal.from_json("[]")
    with pytest.raises(RepairPatchProposalDeserializationError, match="must be text"):
        RepairPatchProposal.from_json(b"{}")  # type: ignore[arg-type]


def test_decoded_target_binding_is_still_checked_against_provenance() -> None:
    payload = _encoded(target_procedure_id=ProcedureId.create().to_str())
    with pytest.raises(
        RepairPatchProposalDeserializationError, match="target_procedure_id must equal"
    ):
        RepairPatchProposal.from_dict(payload)

    payload = _encoded(target_node_id="node-other")
    with pytest.raises(RepairPatchProposalDeserializationError, match="target_node_id must equal"):
        RepairPatchProposal.from_dict(payload)

    payload = _encoded(target_revision=0)
    with pytest.raises(RepairPatchProposalDeserializationError, match="positive integer"):
        RepairPatchProposal.from_dict(payload)


def test_decoded_payload_bounds_still_apply() -> None:
    payload = _encoded(proposed_definition={"text": "x" * (MAX_PATCH_PAYLOAD_STRING_LENGTH + 1)})
    with pytest.raises(RepairPatchProposalDeserializationError, match="string longer than"):
        RepairPatchProposal.from_dict(payload)

    payload = _encoded(proposed_definition={})
    with pytest.raises(RepairPatchProposalDeserializationError, match="must not be empty"):
        RepairPatchProposal.from_dict(payload)


def test_json_payload_with_non_finite_number_cannot_be_decoded() -> None:
    encoded = _proposal().to_json()
    tampered = encoded.replace('"node_kind":"action"', '"node_kind":NaN')

    with pytest.raises(RepairPatchProposalDeserializationError, match=r"malformed|non-finite"):
        RepairPatchProposal.from_json(tampered)
