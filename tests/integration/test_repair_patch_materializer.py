"""Integration proof that repair-patch materialization does not touch ProcedureStore."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

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
    ProcedureStatus,
)
from agentx.core.repair_candidates import derive_repair_candidates
from agentx.core.repair_patch import RepairPatchKind, RepairPatchProposal
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureGraph,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.repair_patch_materializer import materialize_repair_patch

_T0 = datetime(2026, 9, 8, 13, 0, 0, tzinfo=UTC)
_PROCEDURE_ID = ProcedureId(UUID("00000000-0000-4000-8000-000000000115"))
_NODE_ID = "repair-node"


def _source() -> ProcedureRecord:
    graph = ProcedureGraph(
        entry=ProcedureNodeId(_NODE_ID),
        nodes=(
            ProcedureNode(
                id=ProcedureNodeId(_NODE_ID),
                kind=ProcedureNodeKind.ACTION,
                label="source",
                params={"instruction": "before"},
            ),
            ProcedureNode(ProcedureNodeId("end"), ProcedureNodeKind.END, label="end", params={}),
        ),
        edges=(ProcedureEdge(ProcedureNodeId(_NODE_ID), ProcedureNodeId("end")),),
    )
    return ProcedureRecord(
        procedure_id=_PROCEDURE_ID,
        revision=1,
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=graph.to_json()),
        status=ProcedureStatus.ACTIVE,
        created_at=_T0,
    )


def _patch() -> RepairPatchProposal:
    diagnosis = FailureDiagnosis(
        classification=FailureClassification(
            category=FailureCategory.PROCEDURE,
            summary="The source node failed.",
            classified_at=_T0,
        ),
        localization=FailureLocalization(
            kind=FailureLocationKind.PROCEDURE_NODE,
            summary="The target node is exact.",
            localized_at=_T0,
            procedure_id=_PROCEDURE_ID,
            procedure_node_id=_NODE_ID,
        ),
        summary="A node repair can be considered.",
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
    replacement = ProcedureNode(
        id=ProcedureNodeId(_NODE_ID),
        kind=ProcedureNodeKind.ACTION,
        label="candidate",
        params={"instruction": "after"},
    )
    return RepairPatchProposal(
        kind=RepairPatchKind.NODE_DEFINITION_REPLACEMENT,
        candidate=candidate,
        target_procedure_id=_PROCEDURE_ID,
        target_revision=1,
        target_node_id=_NODE_ID,
        proposed_definition=replacement.to_dict(),
        proposed_at=_T0,
    )


def test_materialization_returns_an_unstored_candidate_without_mutating_procedure_store(
    tmp_path: Path,
) -> None:
    source = _source()
    store = ProcedureStore(SQLiteDatabase(tmp_path / "procedure-store.sqlite3"))
    store.insert(source)
    before = store.list_records()

    materialized = materialize_repair_patch(source=source, patch=_patch())

    assert materialized.candidate.status is ProcedureStatus.CANDIDATE
    assert materialized.candidate.revision == 2
    assert store.list_records() == before
    assert store.get(_PROCEDURE_ID, 1) == source
    assert store.get(_PROCEDURE_ID, 2) is None
