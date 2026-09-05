"""Canonical Procedure Graph intermediate representation (A3.01).

This module owns the graph-level IR and the canonical node TYPE vocabulary for
AgentX reusable procedures. It defines *structure only*:

    - :class:`ProcedureGraph` — a finite, directed, possibly cyclic graph of
      typed nodes plus typed edges and a single entry point.
    - :class:`ProcedureNode` / :class:`ProcedureNodeId` / :class:`ProcedureNodeKind`
      — the common node structure and the canonical node-kind vocabulary.
    - :class:`ProcedureEdge` / :class:`ProcedureEdgeKind` — explicit typed
      connectivity (normal progression and recovery/error paths).

This task deliberately does NOT implement node-family semantics. ACTION,
OBSERVE, VERIFY, BRANCH, TRANSFORM, REASON, RESEARCH, WAIT, ROLLBACK,
SUBPROCEDURE, and END are names in the canonical vocabulary only; their
detailed behavior is owned by later tasks (A3.02-A3.05). Node-specific payloads
are carried opaquely in ``ProcedureNode.params`` so those tasks can extend
cleanly without this contract becoming a universal dictionary.

The graph is DATA, never a program. Constructing, deserializing, validating, or
serializing a graph performs no side effects and never reaches any authority or
runtime subsystem:

    - It cannot create authority, lower a risk classification, clear an
      emergency stop, bypass the action gate, call a model provider, run a
      capability, mutate a task, publish an event, or mark anything verified.
    - It imports nothing from ``agentx.kernel``, ``agentx.capabilities``,
      ``agentx.cognition``, ``agentx.infrastructure``, or any other subsystem,
      and it exposes no execution, interpretation, compilation, or invocation
      surface. The module is pure standard library.

Serialization is deterministic, versioned, and strict:

    - A single explicit ``schema_version`` is always present and unsupported
      versions fail closed.
    - JSON is canonical (``sort_keys``, no whitespace, no NaN, UTF-8 safe) and
      nodes/edges are emitted in a stable order, so round-trips preserve graph
      identity exactly.
    - Deserialization is field-by-field and rejects unknown fields, malformed
      enums, and structural violations without dynamic imports or any executable
      reconstruction. Hostile strings inside ``params``/``label`` remain inert
      data.

The IR is designed to be embedded, opaquely, in the C2.03
:class:`agentx.core.procedures.ProcedurePayload` (``CANONICAL_JSON`` kind). The
durable store persists and returns that payload verbatim; it never interprets
graph semantics. This module does not depend on C2.03 storage, and C2.03 storage
does not depend on this module.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

__all__ = [
    "CURRENT_GRAPH_SCHEMA_VERSION",
    "ProcedureEdge",
    "ProcedureEdgeKind",
    "ProcedureGraph",
    "ProcedureGraphDeserializationError",
    "ProcedureGraphError",
    "ProcedureGraphValidationError",
    "ProcedureNode",
    "ProcedureNodeId",
    "ProcedureNodeKind",
    "UnsupportedGraphSchemaVersionError",
]

CURRENT_GRAPH_SCHEMA_VERSION: Final[int] = 1

# The smallest unquestionably-needed edge classifications. Normal progression is
# the happy path; recovery carries retry/repair/error branches. The vocabulary
# is extensible: later tasks add members rather than inventing a workflow DSL.
_NEXT_EDGE = "next"
_RECOVERY_EDGE = "recovery"

# Node kinds are the canonical vocabulary only; no semantics are attached here.
_ACTION_NODE = "action"
_OBSERVE_NODE = "observe"
_VERIFY_NODE = "verify"
_BRANCH_NODE = "branch"
_TRANSFORM_NODE = "transform"
_REASON_NODE = "reason"
_RESEARCH_NODE = "research"
_WAIT_NODE = "wait"
_ROLLBACK_NODE = "rollback"
_SUBPROCEDURE_NODE = "subprocedure"
_END_NODE = "end"


class ProcedureGraphError(ValueError):
    """Base error for Procedure Graph IR structural or serialization failures."""


class ProcedureGraphValidationError(ProcedureGraphError):
    """Raised when a graph violates a structural contract (not runtime checks)."""


class ProcedureGraphDeserializationError(ProcedureGraphError):
    """Raised when encoded graph data cannot be parsed into the canonical IR."""


class UnsupportedGraphSchemaVersionError(ProcedureGraphError):
    """Raised when encoded graph data uses a schema version this code rejects."""


class ProcedureNodeKind(StrEnum):
    """Canonical node-kind vocabulary for a procedure graph.

    These names record WHAT a node is. They carry no semantics and no authority:
    this contract never executes, interprets, or verifies a node. Detailed
    behavior for each kind is owned by later tasks (A3.02-A3.05). END is the
    terminal kind; its only structural rule here is that it may not have
    outgoing edges (it cannot lead anywhere).
    """

    ACTION = _ACTION_NODE
    OBSERVE = _OBSERVE_NODE
    VERIFY = _VERIFY_NODE
    BRANCH = _BRANCH_NODE
    TRANSFORM = _TRANSFORM_NODE
    REASON = _REASON_NODE
    RESEARCH = _RESEARCH_NODE
    WAIT = _WAIT_NODE
    ROLLBACK = _ROLLBACK_NODE
    SUBPROCEDURE = _SUBPROCEDURE_NODE
    END = _END_NODE


class ProcedureEdgeKind(StrEnum):
    """Typed edge classification for explicit graph connectivity.

    Only the two unquestionably-needed classifications are introduced now:
    ``NEXT`` is normal progression and ``RECOVERY`` carries retry/repair/error
    paths. Runtime handling of either edge is owned by later tasks (A3.07-A3.09);
    this contract only records the edge and validates its endpoints structurally.
    """

    NEXT = _NEXT_EDGE
    RECOVERY = _RECOVERY_EDGE


def _validate_nonempty_trimmed(value: object, *, field_name: str) -> str:
    """Require a non-empty, trimmed string identifier or label."""
    if not isinstance(value, str):
        raise ProcedureGraphValidationError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise ProcedureGraphValidationError(f"{field_name} must be non-empty and trimmed")
    return value


def _validate_node_id(value: object) -> ProcedureNodeId:
    if not isinstance(value, str):
        raise ProcedureGraphValidationError("node id must be a string")
    return ProcedureNodeId(_validate_nonempty_trimmed(value, field_name="node id"))


def _freeze_params(raw: Mapping[str, object]) -> Mapping[str, object]:
    """Freeze opaque node parameters into an immutable mapping.

    Keys must be strings; values are carried opaquely (they are interpreted by
    later node-family tasks, never here). No validation of value semantics occurs.
    """
    frozen: dict[str, object] = {}
    for key, item in raw.items():
        if not isinstance(key, str):
            raise ProcedureGraphValidationError("procedure node param keys must be strings")
        frozen[key] = item
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class ProcedureNodeId:
    """Typed, graph-local identifier for a single node.

    Node ids are local keys within one graph; they are not domain identifiers
    and never confer authority. Equality and ordering are by the underlying
    trimmed string value.
    """

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _validate_nonempty_trimmed(self.value, field_name="node id")
        )

    def to_str(self) -> str:
        """Return the canonical string form of this node id."""
        return self.value

    @classmethod
    def parse(cls, raw: object) -> ProcedureNodeId:
        """Parse a node id from a string, rejecting non-string input."""
        return _validate_node_id(raw)


@dataclass(frozen=True, slots=True)
class ProcedureNode:
    """One typed node in a procedure graph.

    ``kind`` selects a canonical vocabulary entry (no semantics here). ``label``
    is an optional human-readable description. ``params`` is an opaque,
    node-family-specific payload; later tasks (A3.02-A3.05) extend behavior by
    interpreting ``params`` for a given ``kind``. This contract never reads
    ``params`` for meaning, so hostile strings inside it remain inert data.
    """

    id: ProcedureNodeId
    kind: ProcedureNodeKind
    label: str | None = None
    params: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, ProcedureNodeId):
            raise ProcedureGraphValidationError("node id must be a ProcedureNodeId")
        if not isinstance(self.kind, ProcedureNodeKind):
            raise ProcedureGraphValidationError("node kind must be a ProcedureNodeKind")
        if self.label is not None:
            _validate_nonempty_trimmed(self.label, field_name="node label")
        if not isinstance(self.params, Mapping):
            raise ProcedureGraphValidationError("node params must be a mapping")
        object.__setattr__(self, "params", _freeze_params(self.params))

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible node representation."""
        return {
            "id": self.id.to_str(),
            "kind": self.kind.value,
            "label": self.label,
            "params": dict(self.params),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedureNode:
        """Validate and reconstruct a node from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise ProcedureGraphDeserializationError("procedure node must be an object")
        actual = set(raw)
        expected = {"id", "kind", "label", "params"}
        if actual != expected:
            missing = expected - actual
            unknown = actual - expected
            if missing:
                raise ProcedureGraphDeserializationError(
                    f"procedure node missing required fields: {sorted(missing)}"
                )
            raise ProcedureGraphDeserializationError(
                f"procedure node contains unknown fields: {sorted(unknown)}"
            )

        id_raw = raw["id"]
        if not isinstance(id_raw, str):
            raise ProcedureGraphDeserializationError("procedure node id must be a string")
        node_id = ProcedureNodeId.parse(id_raw)

        kind_raw = raw["kind"]
        if not isinstance(kind_raw, str):
            raise ProcedureGraphDeserializationError("procedure node kind must be a string")
        try:
            kind = ProcedureNodeKind(kind_raw)
        except ValueError as exc:
            raise ProcedureGraphDeserializationError(
                f"unknown procedure node kind: {kind_raw!r}"
            ) from exc

        label_raw = raw["label"]
        label = (
            None
            if label_raw is None
            else _validate_nonempty_trimmed(label_raw, field_name="node label")
        )

        params_raw = raw["params"]
        if not isinstance(params_raw, Mapping):
            raise ProcedureGraphDeserializationError("procedure node params must be an object")
        params: dict[str, object] = {}
        for key, item in params_raw.items():
            if not isinstance(key, str):
                raise ProcedureGraphDeserializationError(
                    "procedure node param keys must be strings"
                )
            params[key] = item

        return cls(id=node_id, kind=kind, label=label, params=params)


@dataclass(frozen=True, slots=True)
class ProcedureEdge:
    """A single typed, directed edge between two nodes.

    ``source`` and ``target`` name existing node ids. ``kind`` classifies the
    connection (normal progression or recovery). Runtime traversal of edges is
    owned by later tasks; this contract only records the edge and validates its
    endpoints structurally.
    """

    source: ProcedureNodeId
    target: ProcedureNodeId
    kind: ProcedureEdgeKind = ProcedureEdgeKind.NEXT

    def __post_init__(self) -> None:
        if not isinstance(self.source, ProcedureNodeId):
            raise ProcedureGraphValidationError("edge source must be a ProcedureNodeId")
        if not isinstance(self.target, ProcedureNodeId):
            raise ProcedureGraphValidationError("edge target must be a ProcedureNodeId")
        if not isinstance(self.kind, ProcedureEdgeKind):
            raise ProcedureGraphValidationError("edge kind must be a ProcedureEdgeKind")

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible edge representation."""
        return {
            "source": self.source.to_str(),
            "target": self.target.to_str(),
            "kind": self.kind.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedureEdge:
        """Validate and reconstruct an edge from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise ProcedureGraphDeserializationError("procedure edge must be an object")
        actual = set(raw)
        expected = {"source", "target", "kind"}
        if actual != expected:
            missing = expected - actual
            unknown = actual - expected
            if missing:
                raise ProcedureGraphDeserializationError(
                    f"procedure edge missing required fields: {sorted(missing)}"
                )
            raise ProcedureGraphDeserializationError(
                f"procedure edge contains unknown fields: {sorted(unknown)}"
            )

        source_raw = raw["source"]
        if not isinstance(source_raw, str):
            raise ProcedureGraphDeserializationError("edge source must be a string")
        source = ProcedureNodeId.parse(source_raw)

        target_raw = raw["target"]
        if not isinstance(target_raw, str):
            raise ProcedureGraphDeserializationError("edge target must be a string")
        target = ProcedureNodeId.parse(target_raw)

        kind_raw = raw["kind"]
        if not isinstance(kind_raw, str):
            raise ProcedureGraphDeserializationError("edge kind must be a string")
        try:
            kind = ProcedureEdgeKind(kind_raw)
        except ValueError as exc:
            raise ProcedureGraphDeserializationError(
                f"unknown procedure edge kind: {kind_raw!r}"
            ) from exc

        return cls(source=source, target=target, kind=kind)


@dataclass(frozen=True, slots=True)
class ProcedureGraph:
    """Canonical, finite, directed (possibly cyclic) procedure graph.

    Identity is structural: an entry node plus a set of uniquely identified
    typed nodes and typed edges. The graph is self-contained DATA. Cycles are
    permitted (recovery/retry/repair flows need them); this contract imposes no
    DAG-only restriction and performs no runtime loop handling.

    Construction normalizes node/edge ordering for deterministic, order-
    independent equality and serialization, then runs structural validation.
    """

    entry: ProcedureNodeId
    nodes: tuple[ProcedureNode, ...]
    edges: tuple[ProcedureEdge, ...]
    schema_version: int = CURRENT_GRAPH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.entry, ProcedureNodeId):
            raise ProcedureGraphValidationError("entry must be a ProcedureNodeId")
        if not isinstance(self.nodes, tuple):
            raise ProcedureGraphValidationError("nodes must be a tuple")
        if not isinstance(self.edges, tuple):
            raise ProcedureGraphValidationError("edges must be a tuple")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise ProcedureGraphValidationError("schema_version must be an integer")
        if self.schema_version != CURRENT_GRAPH_SCHEMA_VERSION:
            raise UnsupportedGraphSchemaVersionError(
                f"unsupported procedure graph schema version {self.schema_version}; "
                f"supported version is {CURRENT_GRAPH_SCHEMA_VERSION}"
            )

        # Normalize to a canonical, order-independent representation so that two
        # graphs differing only in author-supplied ordering are identical data.
        normalized_nodes = tuple(sorted(self.nodes, key=lambda node: node.id.to_str()))
        normalized_edges = tuple(
            sorted(
                self.edges,
                key=lambda edge: (
                    edge.source.to_str(),
                    edge.target.to_str(),
                    edge.kind.value,
                ),
            )
        )
        object.__setattr__(self, "nodes", normalized_nodes)
        object.__setattr__(self, "edges", normalized_edges)

        self.validate()

    def validate(self) -> None:
        """Structural validation only (no capability, model, or permission checks).

        Checks: at least one node; unique node ids; a valid entry node; edge
        endpoints that exist; no duplicate edges; and the sole END rule — END
        nodes are terminal and must not have outgoing edges. Cycles are allowed.
        """
        node_ids: list[str] = [node.id.to_str() for node in self.nodes]
        if not node_ids:
            raise ProcedureGraphValidationError("procedure graph must contain at least one node")

        seen_ids: set[str] = set()
        for node_id in node_ids:
            if node_id in seen_ids:
                raise ProcedureGraphValidationError(f"duplicate node id: {node_id!r}")
            seen_ids.add(node_id)

        entry_id = self.entry.to_str()
        if entry_id not in seen_ids:
            raise ProcedureGraphValidationError(
                f"entry node id is not present in the graph: {entry_id!r}"
            )

        seen_edges: set[tuple[str, str, str]] = set()
        for edge in self.edges:
            source_id = edge.source.to_str()
            target_id = edge.target.to_str()
            if source_id not in seen_ids:
                raise ProcedureGraphValidationError(
                    f"edge source node does not exist: {source_id!r}"
                )
            if target_id not in seen_ids:
                raise ProcedureGraphValidationError(
                    f"edge target node does not exist: {target_id!r}"
                )
            edge_key = (source_id, target_id, edge.kind.value)
            if edge_key in seen_edges:
                raise ProcedureGraphValidationError(f"duplicate edge: {edge_key!r}")
            seen_edges.add(edge_key)

        end_ids = {node.id.to_str() for node in self.nodes if node.kind is ProcedureNodeKind.END}
        if end_ids:
            for edge in self.edges:
                if edge.source.to_str() in end_ids:
                    raise ProcedureGraphValidationError(
                        f"END node must not have outgoing edges: {edge.source.to_str()!r}"
                    )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical, deterministically ordered JSON-compatible form."""
        return {
            "schema_version": self.schema_version,
            "entry": self.entry.to_str(),
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    def to_json(self) -> str:
        """Serialize to deterministic, canonical, UTF-8-safe JSON text."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedureGraph:
        """Validate and reconstruct a graph from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise ProcedureGraphDeserializationError("procedure graph must be an object")
        actual = set(raw)
        expected = {"schema_version", "entry", "nodes", "edges"}
        if actual != expected:
            missing = expected - actual
            unknown = actual - expected
            if missing:
                raise ProcedureGraphDeserializationError(
                    f"procedure graph missing required fields: {sorted(missing)}"
                )
            raise ProcedureGraphDeserializationError(
                f"procedure graph contains unknown fields: {sorted(unknown)}"
            )

        version_raw = raw["schema_version"]
        if not isinstance(version_raw, int) or isinstance(version_raw, bool):
            raise ProcedureGraphDeserializationError("schema_version must be an integer")

        entry_raw = raw["entry"]
        if not isinstance(entry_raw, str):
            raise ProcedureGraphDeserializationError("entry must be a string")
        entry = ProcedureNodeId.parse(entry_raw)

        nodes_raw = raw["nodes"]
        if not isinstance(nodes_raw, list):
            raise ProcedureGraphDeserializationError("nodes must be a list")
        node_list: list[ProcedureNode] = []
        for node in nodes_raw:
            if not isinstance(node, Mapping):
                raise ProcedureGraphDeserializationError("procedure node must be an object")
            node_list.append(ProcedureNode.from_dict(node))
        nodes = tuple(node_list)

        edges_raw = raw["edges"]
        if not isinstance(edges_raw, list):
            raise ProcedureGraphDeserializationError("edges must be a list")
        edge_list: list[ProcedureEdge] = []
        for edge in edges_raw:
            if not isinstance(edge, Mapping):
                raise ProcedureGraphDeserializationError("procedure edge must be an object")
            edge_list.append(ProcedureEdge.from_dict(edge))
        edges = tuple(edge_list)

        return cls(entry=entry, nodes=nodes, edges=edges, schema_version=version_raw)

    @classmethod
    def from_json(cls, raw: str) -> ProcedureGraph:
        """Deserialize JSON text without dynamic imports or executable reconstruction."""
        if not isinstance(raw, str):
            raise ProcedureGraphDeserializationError("procedure graph JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProcedureGraphDeserializationError("procedure graph JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise ProcedureGraphDeserializationError("procedure graph JSON root must be an object")
        copied: dict[str, object] = {}
        for key, item in decoded.items():
            if not isinstance(key, str):
                raise ProcedureGraphDeserializationError(
                    "procedure graph JSON contains a non-string object key"
                )
            copied[key] = item
        return cls.from_dict(copied)
