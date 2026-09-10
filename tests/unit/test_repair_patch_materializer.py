"""Unit tests for deterministic repair-patch candidate materialization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID, uuid4

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
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.core.repair_candidates import RepairCandidate, derive_repair_candidates
from agentx.core.repair_patch import RepairPatchKind, RepairPatchProposal
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureGraph,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.repair_patch_materializer import (
    MaterializedProcedureCandidate,
    RepairPatchMaterializationError,
    materialize_repair_patch,
)

_T0 = datetime(2026, 9, 8, 12, 30, 0, 123456, tzinfo=UTC)
_PROCEDURE_ID = ProcedureId(UUID("00000000-0000-4000-8000-000000000015"))
_OTHER_PROCEDURE_ID = ProcedureId(UUID("00000000-0000-4000-8000-000000000016"))
_NODE_ID = "repair-node"


def _source(
    *,
    procedure_id: ProcedureId = _PROCEDURE_ID,
    revision: int = 7,
    scope: ProcedureScope | None = None,
) -> ProcedureRecord:
    graph = ProcedureGraph(
        entry=ProcedureNodeId(_NODE_ID),
        nodes=(
            ProcedureNode(
                id=ProcedureNodeId(_NODE_ID),
                kind=ProcedureNodeKind.ACTION,
                label="original action",
                params={"instruction": "old value"},
            ),
            ProcedureNode(
                id=ProcedureNodeId("end"),
                kind=ProcedureNodeKind.END,
                label="end",
                params={},
            ),
        ),
        edges=(ProcedureEdge(ProcedureNodeId(_NODE_ID), ProcedureNodeId("end")),),
    )
    return ProcedureRecord(
        procedure_id=procedure_id,
        revision=revision,
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=graph.to_json()),
        status=ProcedureStatus.ACTIVE,
        scope=ProcedureScope() if scope is None else scope,
        created_at=_T0,
    )


def _candidate(
    *,
    procedure_id: ProcedureId = _PROCEDURE_ID,
    node_id: str = _NODE_ID,
) -> RepairCandidate:
    diagnosis = FailureDiagnosis(
        classification=FailureClassification(
            category=FailureCategory.PROCEDURE,
            summary="A procedure node was implicated.",
            classified_at=_T0,
        ),
        localization=FailureLocalization(
            kind=FailureLocationKind.PROCEDURE_NODE,
            summary="The repair target is exact.",
            localized_at=_T0,
            procedure_id=procedure_id,
            procedure_node_id=node_id,
        ),
        summary="A node-definition repair may be considered.",
        diagnosed_at=_T0,
        evidence=(
            DiagnosticEvidence(
                kind=DiagnosticEvidenceKind.CHAIN_CORRELATION,
                correlation_id=uuid4(),
            ),
        ),
        conclusion=DiagnosticConclusion.NODE_IMPLICATED,
    )
    (candidate,) = derive_repair_candidates(diagnosis=diagnosis, proposed_at=_T0)
    return candidate


def _replacement(
    *,
    node_id: str = _NODE_ID,
    kind: ProcedureNodeKind = ProcedureNodeKind.ACTION,
    label: str = "repaired action",
    params: dict[str, object] | None = None,
) -> dict[str, object]:
    return ProcedureNode(
        id=ProcedureNodeId(node_id),
        kind=kind,
        label=label,
        params={"instruction": "new value"} if params is None else params,
    ).to_dict()


def _patch(
    *,
    procedure_id: ProcedureId = _PROCEDURE_ID,
    revision: int = 7,
    node_id: str = _NODE_ID,
    definition: Mapping[str, object] | None = None,
) -> RepairPatchProposal:
    return RepairPatchProposal(
        kind=RepairPatchKind.NODE_DEFINITION_REPLACEMENT,
        candidate=_candidate(procedure_id=procedure_id, node_id=node_id),
        target_procedure_id=procedure_id,
        target_revision=revision,
        target_node_id=node_id,
        proposed_definition=_replacement(node_id=node_id) if definition is None else definition,
        proposed_at=_T0,
    )


def _node_by_id(record: ProcedureRecord, node_id: str) -> ProcedureNode:
    graph = ProcedureGraph.from_json(record.payload.content)
    return next(node for node in graph.nodes if node.id.to_str() == node_id)


def test_materializes_complete_node_definition_as_a_new_candidate_revision() -> None:
    source = _source()
    patch = _patch()

    materialized = materialize_repair_patch(source=source, patch=patch)

    assert isinstance(materialized, MaterializedProcedureCandidate)
    assert materialized.candidate.procedure_id == source.procedure_id
    assert materialized.candidate.revision == source.revision + 1
    assert materialized.candidate.status is ProcedureStatus.CANDIDATE
    assert materialized.candidate.updated_at is None
    assert materialized.candidate.created_at == patch.proposed_at
    assert materialized.candidate.scope == source.scope
    assert _node_by_id(materialized.candidate, _NODE_ID).to_dict() == _replacement()
    assert _node_by_id(materialized.candidate, "end").kind is ProcedureNodeKind.END


def test_source_record_and_definition_remain_byte_for_byte_unchanged() -> None:
    source = _source()
    source_json = source.to_json()
    source_payload = source.payload.content
    source_node = _node_by_id(source, _NODE_ID).to_dict()

    materialize_repair_patch(source=source, patch=_patch())

    assert source.to_json() == source_json
    assert source.payload.content == source_payload
    assert _node_by_id(source, _NODE_ID).to_dict() == source_node


def test_materialized_candidate_preserves_exact_source_and_patch_provenance() -> None:
    source = _source()
    patch = _patch()

    materialized = materialize_repair_patch(source=source, patch=patch)

    assert materialized.source_procedure_id == source.procedure_id
    assert materialized.source_revision == source.revision
    assert materialized.repair_patch == patch
    assert materialized.repair_patch.candidate == patch.candidate
    serialized = materialized.to_dict()
    assert serialized["source_procedure_id"] == source.procedure_id.to_str()
    assert serialized["source_revision"] == source.revision
    assert serialized["repair_patch"] == patch.to_dict()
    assert serialized["candidate"] == materialized.candidate.to_dict()


def test_repeated_materialization_is_deterministic() -> None:
    source = _source(
        scope=ProcedureScope(dimensions={ProcedureScopeDimension.PROJECT: "repair-fixture"})
    )
    patch = _patch()

    first = materialize_repair_patch(source=source, patch=patch)
    second = materialize_repair_patch(source=source, patch=patch)

    assert first == second
    assert first.to_json() == second.to_json()
    assert first.candidate.to_json() == second.candidate.to_json()


def test_result_is_frozen_and_cannot_be_reclassified_as_active() -> None:
    materialized = materialize_repair_patch(source=_source(), patch=_patch())

    with pytest.raises(FrozenInstanceError):
        materialized.source_revision = 999  # type: ignore[misc]
    assert materialized.candidate.status is ProcedureStatus.CANDIDATE
    for forbidden in ("active", "validated", "shadow_safe", "accepted", "authorized"):
        assert not hasattr(materialized, forbidden)


def test_rejects_patch_for_a_different_procedure_id() -> None:
    with pytest.raises(RepairPatchMaterializationError, match="procedure id"):
        materialize_repair_patch(source=_source(), patch=_patch(procedure_id=_OTHER_PROCEDURE_ID))


def test_rejects_stale_or_wrong_source_revision() -> None:
    with pytest.raises(RepairPatchMaterializationError, match="target revision"):
        materialize_repair_patch(source=_source(revision=7), patch=_patch(revision=6))


def test_rejects_patch_targeting_a_node_not_in_the_source_graph() -> None:
    with pytest.raises(RepairPatchMaterializationError, match="does not exist"):
        materialize_repair_patch(source=_source(), patch=_patch(node_id="missing-node"))


def test_rejects_unsupported_patch_kind_before_any_graph_work() -> None:
    unsupported = object.__new__(RepairPatchProposal)
    object.__setattr__(unsupported, "kind", "foreign_patch_kind")

    with pytest.raises(RepairPatchMaterializationError, match="unsupported repair patch kind"):
        materialize_repair_patch(source=_source(), patch=unsupported)


def test_rejects_malformed_replacement_node_payload() -> None:
    malformed = {"id": _NODE_ID, "kind": "action", "label": "missing params"}

    with pytest.raises(RepairPatchMaterializationError, match="complete canonical ProcedureNode"):
        materialize_repair_patch(source=_source(), patch=_patch(definition=malformed))


def test_rejects_replacement_for_a_different_node_even_when_that_node_is_valid() -> None:
    with pytest.raises(RepairPatchMaterializationError, match="node id"):
        materialize_repair_patch(
            source=_source(),
            patch=_patch(definition=_replacement(node_id="different-node")),
        )


def test_rejects_replacement_that_makes_the_graph_structurally_invalid() -> None:
    end_replacement = _replacement(node_id=_NODE_ID, kind=ProcedureNodeKind.END, label="not legal")

    with pytest.raises(RepairPatchMaterializationError, match="invalid ProcedureGraph"):
        materialize_repair_patch(source=_source(), patch=_patch(definition=end_replacement))


def test_rejects_source_payload_that_is_not_a_canonical_procedure_graph() -> None:
    source = ProcedureRecord(
        procedure_id=_PROCEDURE_ID,
        revision=7,
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON, content='{"not":"a graph"}'
        ),
        status=ProcedureStatus.ACTIVE,
        created_at=_T0,
    )

    with pytest.raises(RepairPatchMaterializationError, match="valid canonical ProcedureGraph"):
        materialize_repair_patch(source=source, patch=_patch())


def test_rejects_non_graph_artifact_source_without_resolving_it() -> None:
    source = ProcedureRecord(
        procedure_id=_PROCEDURE_ID,
        revision=7,
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.ARTIFACT_REFERENCE, content="artifact-123"
        ),
        status=ProcedureStatus.ACTIVE,
        created_at=_T0,
    )

    with pytest.raises(RepairPatchMaterializationError, match="CANONICAL_JSON"):
        materialize_repair_patch(source=source, patch=_patch())
