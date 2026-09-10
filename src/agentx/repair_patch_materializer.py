"""Pure materialization of one revision-bound canonical repair-patch proposal.

This top-level composition module joins the inward
:mod:`agentx.core.repair_patch` proposal contract to the canonical
:mod:`agentx.procedures.graph` representation.  It has exactly one job:
replace one existing graph node definition with the complete replacement node
carried by a correctly bound ``RepairPatchProposal``, and return a **new**
``ProcedureRecord`` candidate revision together with exact source and proposal
provenance.

It deliberately does not decide whether a proposal is accepted.  The core
proposal contract carries no acceptance state, so acceptance is an external
precondition of this pure transform.  Nor does this module persist, activate,
replace, validate, shadow-run, execute, or otherwise use the returned
candidate.  It does not query durable storage or any current environment.

The only patch operation the landed closed proposal contract represents is
``RepairPatchKind.NODE_DEFINITION_REPLACEMENT``.  It is applied as a whole
canonical ``ProcedureNode`` replacement: the replacement's ``id`` must equal
the exact target node id, existing graph edges stay unchanged, and the
canonical ``ProcedureGraph`` constructor validates the resulting structure.
There is no generic patch language, partial update, diff, node insertion,
node deletion, edge mutation, procedure activation, or source-code operation.

The transform is deterministic.  The new revision keeps the source
``ProcedureId``, uses exactly ``source.revision + 1``, preserves the source
scope, is born ``ProcedureStatus.CANDIDATE`` with no update timestamp, and
uses the proposal's caller-supplied ``proposed_at`` as its deterministic
creation instant.  The output includes the complete proposal by value, whose
embedded RepairCandidate preserves the available diagnostic/evidence chain.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from agentx.core.ids import ProcedureId
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
    ProcedureValidationError,
)
from agentx.core.repair_patch import RepairPatchKind, RepairPatchProposal
from agentx.procedures.graph import (
    ProcedureGraph,
    ProcedureGraphError,
    ProcedureNode,
)

__all__ = [
    "MATERIALIZED_PROCEDURE_CANDIDATE_SCHEMA_VERSION",
    "MaterializedProcedureCandidate",
    "RepairPatchMaterializationError",
    "materialize_repair_patch",
]

MATERIALIZED_PROCEDURE_CANDIDATE_SCHEMA_VERSION: Final[int] = 1


class RepairPatchMaterializationError(ValueError):
    """Raised when a source record and proposal cannot form a new candidate.

    This is a fail-closed data error.  It never indicates that a repair was
    executed, validated, persisted, activated, or approved.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class MaterializedProcedureCandidate:
    """An immutable new candidate revision plus exact materialization provenance.

    ``candidate`` is a newly constructed :class:`ProcedureRecord`, not a
    mutation of a source record.  It has the source ``ProcedureId``, exactly
    the following revision number, and ``ProcedureStatus.CANDIDATE``.  Its
    payload is a canonical JSON ``ProcedureGraph`` with exactly one complete
    node definition replaced.

    ``source_procedure_id`` and ``source_revision`` are explicit provenance
    links.  ``repair_patch`` preserves the entire canonical proposal by value;
    the proposal has no separate identity in the landed contract, and its
    embedded ``RepairCandidate`` carries any canonical diagnosis/evidence
    available for the proposal.  None of these data fields grants authority or
    asserts that the candidate is accepted, validated, shadow-safe, active, or
    executable.
    """

    source_procedure_id: ProcedureId
    source_revision: int
    repair_patch: RepairPatchProposal
    candidate: ProcedureRecord
    schema_version: int = MATERIALIZED_PROCEDURE_CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.source_procedure_id, ProcedureId):
            raise RepairPatchMaterializationError("source_procedure_id must be a ProcedureId")
        if isinstance(self.source_revision, bool) or not isinstance(self.source_revision, int):
            raise RepairPatchMaterializationError("source_revision must be an integer")
        if self.source_revision < 1:
            raise RepairPatchMaterializationError("source_revision must be a positive integer")
        if not isinstance(self.repair_patch, RepairPatchProposal):
            raise RepairPatchMaterializationError("repair_patch must be a RepairPatchProposal")
        if not isinstance(self.candidate, ProcedureRecord):
            raise RepairPatchMaterializationError("candidate must be a ProcedureRecord")
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int):
            raise RepairPatchMaterializationError("schema_version must be an integer")
        if self.schema_version != MATERIALIZED_PROCEDURE_CANDIDATE_SCHEMA_VERSION:
            raise RepairPatchMaterializationError(
                f"unsupported materialized procedure candidate schema version {self.schema_version}"
            )
        if self.repair_patch.kind is not RepairPatchKind.NODE_DEFINITION_REPLACEMENT:
            raise RepairPatchMaterializationError(
                "materialized candidate requires a node_definition_replacement patch"
            )
        if self.repair_patch.target_procedure_id != self.source_procedure_id:
            raise RepairPatchMaterializationError(
                "repair patch procedure id must equal source_procedure_id"
            )
        if self.repair_patch.target_revision != self.source_revision:
            raise RepairPatchMaterializationError(
                "repair patch target revision must equal source_revision"
            )
        if self.candidate.procedure_id != self.source_procedure_id:
            raise RepairPatchMaterializationError(
                "candidate procedure id must equal source_procedure_id"
            )
        if self.candidate.revision != self.source_revision + 1:
            raise RepairPatchMaterializationError(
                "candidate revision must be exactly source_revision + 1"
            )
        if self.candidate.status is not ProcedureStatus.CANDIDATE:
            raise RepairPatchMaterializationError("candidate status must be CANDIDATE")
        if self.candidate.updated_at is not None:
            raise RepairPatchMaterializationError(
                "new candidate must not have an updated_at timestamp"
            )
        if self.candidate.created_at != self.repair_patch.proposed_at:
            raise RepairPatchMaterializationError(
                "candidate creation timestamp must equal repair patch proposed_at"
            )

    @property
    def procedure_id(self) -> ProcedureId:
        """Return the candidate procedure identity without resolving any store."""
        return self.candidate.procedure_id

    @property
    def revision(self) -> int:
        """Return the new candidate revision number."""
        return self.candidate.revision

    def to_dict(self) -> dict[str, object]:
        """Return deterministic data preserving candidate and proposal provenance."""
        return {
            "schema_version": self.schema_version,
            "source_procedure_id": self.source_procedure_id.to_str(),
            "source_revision": self.source_revision,
            "repair_patch": self.repair_patch.to_dict(),
            "candidate": self.candidate.to_dict(),
        }

    def to_json(self) -> str:
        """Serialize deterministically; serialization is data-only and non-persistent."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _require_source_record(source: object) -> ProcedureRecord:
    if not isinstance(source, ProcedureRecord):
        raise RepairPatchMaterializationError("source must be a canonical ProcedureRecord")
    if source.payload.kind is not ProcedurePayloadKind.CANONICAL_JSON:
        raise RepairPatchMaterializationError(
            "source payload must be CANONICAL_JSON containing a ProcedureGraph"
        )
    return source


def _require_patch(patch: object) -> RepairPatchProposal:
    if not isinstance(patch, RepairPatchProposal):
        raise RepairPatchMaterializationError("patch must be a canonical RepairPatchProposal")
    if patch.kind is not RepairPatchKind.NODE_DEFINITION_REPLACEMENT:
        raise RepairPatchMaterializationError(f"unsupported repair patch kind: {patch.kind!r}")
    return patch


def _parse_source_graph(source: ProcedureRecord) -> ProcedureGraph:
    """Return the source graph, rejecting opaque or structurally invalid content."""
    try:
        return ProcedureGraph.from_json(source.payload.content)
    except (ProcedureGraphError, TypeError, ValueError) as exc:
        raise RepairPatchMaterializationError(
            "source payload must contain a structurally valid canonical ProcedureGraph"
        ) from exc


def _replacement_node(patch: RepairPatchProposal) -> ProcedureNode:
    """Read one complete canonical node definition from untrusted proposal data."""
    try:
        definition = patch.to_dict()["proposed_definition"]
        if not isinstance(definition, Mapping):  # pragma: no cover - patch contract guarantees this
            raise RepairPatchMaterializationError(
                "repair patch proposed_definition must be a canonical node object"
            )
        replacement = ProcedureNode.from_dict(definition)
    except RepairPatchMaterializationError:
        raise
    except (ProcedureGraphError, TypeError, ValueError) as exc:
        raise RepairPatchMaterializationError(
            "repair patch proposed_definition is not a complete canonical ProcedureNode"
        ) from exc
    if replacement.id.to_str() != patch.target_node_id:
        raise RepairPatchMaterializationError(
            "repair patch replacement node id must exactly equal target_node_id"
        )
    return replacement


def _replace_node(
    *,
    source_graph: ProcedureGraph,
    target_node_id: str,
    replacement: ProcedureNode,
) -> ProcedureGraph:
    """Replace exactly one graph node while retaining canonical graph connectivity."""
    found = False
    nodes: list[ProcedureNode] = []
    for node in source_graph.nodes:
        if node.id.to_str() == target_node_id:
            nodes.append(replacement)
            found = True
        else:
            nodes.append(node)
    if not found:
        raise RepairPatchMaterializationError(
            f"repair patch target node does not exist in source graph: {target_node_id!r}"
        )
    try:
        return ProcedureGraph(
            entry=source_graph.entry,
            nodes=tuple(nodes),
            edges=source_graph.edges,
            schema_version=source_graph.schema_version,
        )
    except (ProcedureGraphError, TypeError, ValueError) as exc:
        raise RepairPatchMaterializationError(
            "repair patch would produce an invalid ProcedureGraph"
        ) from exc


def materialize_repair_patch(
    *,
    source: ProcedureRecord,
    patch: RepairPatchProposal,
) -> MaterializedProcedureCandidate:
    """Materialize one correctly bound node-replacement proposal into a new candidate.

    The operation is a pure deterministic transformation.  It accepts only the
    one closed ``NODE_DEFINITION_REPLACEMENT`` patch kind represented by the
    landed canonical proposal contract.  It rejects a wrong procedure identity,
    stale/wrong revision, missing target node, malformed node payload, or an
    invalid result.  It never reads or writes a store, clock, environment, task,
    kernel, model, validator, shadow runner, or capability.

    The returned ``candidate`` is not stored or activated.  It retains the
    source ProcedureId, takes ``source.revision + 1``, preserves source scope,
    and is created through ``ProcedureRecord.create`` so its status is exactly
    ``CANDIDATE`` and its update timestamp is absent.
    """
    source_record = _require_source_record(source)
    proposal = _require_patch(patch)

    if proposal.target_procedure_id != source_record.procedure_id:
        raise RepairPatchMaterializationError(
            "repair patch target procedure id does not match source procedure id"
        )
    if proposal.target_revision != source_record.revision:
        raise RepairPatchMaterializationError(
            "repair patch target revision does not match source revision"
        )

    source_graph = _parse_source_graph(source_record)
    replacement = _replacement_node(proposal)
    candidate_graph = _replace_node(
        source_graph=source_graph,
        target_node_id=proposal.target_node_id,
        replacement=replacement,
    )

    try:
        candidate = ProcedureRecord.create(
            procedure_id=source_record.procedure_id,
            revision=source_record.revision + 1,
            payload=ProcedurePayload(
                kind=ProcedurePayloadKind.CANONICAL_JSON,
                content=candidate_graph.to_json(),
            ),
            scope=source_record.scope,
            created_at=proposal.proposed_at,
        )
    except (ProcedureGraphError, ProcedureValidationError, TypeError, ValueError) as exc:
        raise RepairPatchMaterializationError(
            "repair patch could not produce a canonical candidate ProcedureRecord"
        ) from exc

    return MaterializedProcedureCandidate(
        source_procedure_id=source_record.procedure_id,
        source_revision=source_record.revision,
        repair_patch=proposal,
        candidate=candidate,
    )
