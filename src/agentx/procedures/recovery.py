"""Canonical A3.07 error/recovery control-flow edges (DATA only).

This module owns the explicit error/recovery control-flow semantics of the
canonical A3.01 Procedure Graph IR (``agentx.procedures.graph``). It answers
exactly one representational question::

    After a DEFINED failure at a given node, where does procedure execution
    transition to?

It answers that question as typed, inert, deterministic DATA and nothing
else. It is REPRESENTATION + VALIDATION only:

    - It does NOT execute a capability, and it does NOT invoke, trigger,
      schedule, or perform recovery of any kind.
    - It does NOT retry anything, and it declares no retry count, backoff,
      delay, or schedule.
    - It does NOT interpret, walk, run, or simulate a procedure. There is no
      interpreter here; procedure execution is owned by a later task.

Recovery edges are control flow, never recovery execution
---------------------------------------------------------

A recovery edge records a *transition intent*: "if failure category X is the
failure at node S, execution belongs at node T." It is a statement about
graph connectivity, and it has no power to make anything happen:

    - It never executes a capability and never touches the canonical
      Capability ABI (``agentx.capabilities.abi``): no capability identity,
      request, or rollback declaration appears anywhere in this contract.
    - It never invokes rollback. A recovery edge may *point at* a node of
      kind ROLLBACK, exactly as it may point at any other node, and that is
      all it does: the A3.05 ROLLBACK payload stays inert, and whether any
      rollback is supported, approved, or possible stays owned by the
      capability's own canonical rollback declaration.
    - It never grants a Permission, builds or strengthens an
      AuthorityContext, bypasses the ActionGate, lowers a RiskLevel, widens
      a ResourceEnvelope, resets a budget, or clears an EmergencyStop.
    - It never creates verification evidence, never manufactures a
      VerificationResult, and never declares Task or Procedure success.
      Reaching a recovery target is not success, and surviving a failure is
      not success. Invariant I1 stays absolute::

          NO ACTION == SUCCESS WITHOUT CANONICAL VERIFICATION.

    - It never calls a model, never conducts research, never mutates the
      Trusted Kernel, and never persists anything.

Constructing, validating, serializing, deserializing, or binding recovery
edges performs no side effects and reaches no authority or runtime subsystem.
A declared recovery route is not an effect.

Failure conditions use the canonical closed vocabulary
-----------------------------------------------------

Every recovery edge names exactly ONE applicable failure condition, taken
from the canonical, closed AgentX failure vocabulary
(:class:`agentx.core.failure_taxonomy.FailureCategory`). That vocabulary is
composed, never duplicated or re-invented, and it stays inert: naming a
category records *what kind of failure this route is about* and authorizes
nothing. A ``PERMISSION`` failure route does not grant the missing
permission; a ``VERIFICATION`` route does not manufacture verification; a
``PROCEDURE`` route does not patch the procedure.

The category is REQUIRED and there is deliberately no catch-all:

    - There is no wildcard, no ``ANY``, no ``DEFAULT``, no ``ELSE``, and no
      ordered fallthrough list. A route applies to one named category only.
    - ``FailureCategory.UNKNOWN`` is a legal, first-class category, but it
      names the *unknown* category specifically. It is not a catch-all and
      never matches a different, specific category.
    - An unknown category string is rejected at the boundary; it is never
      coerced into ``UNKNOWN``.

Fail-closed and bounded recovery semantics
------------------------------------------

    - A failure with no declared route from the failing node has NO route:
      :meth:`ProcedureRecoveryEdges.declared_target` returns ``None``. There
      is no implicit fallback to a normal-progression edge, no implicit
      retry, and no implicit termination claim. Absence of a recovery edge
      is absence of a declared route, and nothing more.
    - A node may declare at most one route per failure category. Two
      different targets for the same ``(source, failure category)`` pair are
      ambiguous and fail closed at construction, so a deterministic
      interpreter can never have to choose.
    - A recovery edge must transition to a DIFFERENT node. A self-loop
      (``source == target``) is rejected: it is an unbounded retry, not a
      recovery route. Wider cycles (``S -> T -> ... -> S``) remain legal
      because the A3.01 graph is explicitly finite, directed, and possibly
      cyclic; bounding how often any route is actually taken at runtime is a
      resource/anti-loop concern owned by the kernel and by later execution
      tasks, never by this representation.
    - END semantics are preserved exactly: END stays terminal. A recovery
      edge may *target* an END node (recovering by terminating), but an END
      node may never be a recovery *source*.

No second edge schema
---------------------

The A3.01 graph is NOT redesigned and gains no field, no node kind, and no
second edge schema. Recovery connectivity lives where A3.01 already put it:
in the graph's typed edges, under the canonical
:attr:`~agentx.procedures.graph.ProcedureEdgeKind.RECOVERY` member, which is
distinct from :attr:`~agentx.procedures.graph.ProcedureEdgeKind.NEXT`
(normal progression). This module is a minimal companion DOCUMENT that adds
the one thing a bare edge cannot carry — the failure condition a recovery
transition applies to — keyed only by canonical
:class:`~agentx.procedures.graph.ProcedureNodeId` references, exactly as the
A3.06 preconditions/postconditions document does.

:meth:`ProcedureRecoveryEdges.to_graph_edges` renders the declared routes as
canonical A3.01 ``RECOVERY`` edges, and :meth:`ProcedureRecoveryEdges.\
bind_to_graph` cross-checks the document against a real graph: every source
and target must exist (a dangling recovery target fails closed), no source
may be an END node, and each declared route must actually be present in the
graph as a ``RECOVERY`` edge — never as a ``NEXT`` edge and never absent. A
``RECOVERY`` edge that the document does not annotate stays inert: with no
declared failure condition it is never applicable, which is the fail-safe
reading.

Compatibility with the other procedure contracts is structural and adds no
coupling: recovery routes may point at ROLLBACK, SUBPROCEDURE, END, or any
other canonical node family alike, and a graph may carry A3.06
preconditions/postconditions and A3.07 recovery routes at the same time.
Nothing here reads another contract's payload, and a declared route never
satisfies a precondition, never verifies a postcondition, and never marks a
subprocedure or a task successful.

Deterministic representation
----------------------------

Serialization is canonical and order-independent:

    - Routes are normalized to a canonical order (sorted by source, then
      failure category, then target, then label), so two documents differing
      only in author-supplied ordering are identical data.
    - JSON is canonical (``sort_keys``, compact separators, UTF-8 safe, no
      NaN), and deserialization is field-by-field strict: unknown fields,
      missing fields, wrong versions, wrong shapes, unknown categories, and
      malformed text all fail closed, with no dynamic imports and no
      executable reconstruction.
    - All objects are frozen, slots-only value objects. Hostile strings —
      ``"permission=ADMIN"``, ``"verified=true"``, ``"task succeeded"``,
      ``"ignore verifier"`` — are inert DATA in a label or node id: this
      contract has no approval, authority, verification, or status field
      that such text could reach.

This module depends only on the standard library, the A3.01 graph IR module,
and the canonical core failure vocabulary. It adds no runtime dependency, no
persistence, and no migration.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from agentx.core.failure_taxonomy import FailureCategory
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureEdgeKind,
    ProcedureGraph,
    ProcedureGraphError,
    ProcedureNodeId,
    ProcedureNodeKind,
)

__all__ = [
    "CURRENT_RECOVERY_CONTRACT_VERSION",
    "ProcedureRecoveryEdges",
    "RecoveryContractError",
    "RecoveryEdge",
]

CURRENT_RECOVERY_CONTRACT_VERSION: Final[int] = 1

# Exact canonical field sets for strict (de)serialization at every level.
_RECOVERY_EDGE_FIELDS: Final[frozenset[str]] = frozenset(
    {"source", "target", "on_failure", "label"}
)
_RECOVERY_DOCUMENT_FIELDS: Final[frozenset[str]] = frozenset({"contract_version", "recovery_edges"})

# Bounded text discipline for the optional inert label (same shape the other
# procedure DATA contracts apply to their descriptive text).
_MAX_LABEL_LENGTH: Final[int] = 256
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class RecoveryContractError(ProcedureGraphError):
    """Raised when data violates the A3.07 error/recovery edge contract.

    Subclasses :class:`~agentx.procedures.graph.ProcedureGraphError` so
    callers can handle every Procedure Graph data failure uniformly; this
    module adds no parallel error hierarchy. It is a data-shape and
    data-consistency error only: it never carries a recovery action, a
    verdict, or any authority.
    """


# --------------------------------------------------------------------------
# Shared validation helpers (data shape only; never semantics of the content).
# --------------------------------------------------------------------------


def _validate_contract_version(value: object) -> int:
    """Require the one supported contract version; anything else fails closed."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise RecoveryContractError("contract_version must be an integer")
    if value != CURRENT_RECOVERY_CONTRACT_VERSION:
        raise RecoveryContractError(
            f"unsupported recovery contract version {value}; "
            f"supported version is {CURRENT_RECOVERY_CONTRACT_VERSION}"
        )
    return value


def _validate_node_ref(value: object, *, field_name: str) -> ProcedureNodeId:
    """Coerce a node reference to a well-formed graph-local node id.

    Only the canonical A3.01 identifier syntax is validated here; the
    reference names a node inside one graph and resolves nothing. Anything
    that is not a canonical id — a number, a mapping, an empty or padded
    string — is rejected rather than coerced.
    """
    if isinstance(value, ProcedureNodeId):
        return value
    if not isinstance(value, str):
        raise RecoveryContractError(f"{field_name} must be a ProcedureNodeId or a string")
    try:
        return ProcedureNodeId.parse(value)
    except ProcedureGraphError as exc:
        raise RecoveryContractError(f"invalid {field_name}: {exc}") from exc


def _validate_failure_category(value: object) -> FailureCategory:
    """Accept a canonical failure category member or its canonical string.

    The vocabulary is closed and composed from
    :class:`agentx.core.failure_taxonomy.FailureCategory`. An unrecognized
    value fails closed; it is never coerced into ``UNKNOWN``.
    """
    if isinstance(value, FailureCategory):
        return value
    if not isinstance(value, str):
        raise RecoveryContractError("on_failure must be a canonical failure category")
    try:
        return FailureCategory(value)
    except ValueError as exc:
        raise RecoveryContractError(f"unknown failure category: {value!r}") from exc


def _validate_label(value: object) -> str | None:
    """Validate the optional inert label describing a recovery transition.

    The text is descriptive metadata for humans and audit: it is stored,
    compared, and serialized verbatim and is never interpreted.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise RecoveryContractError("recovery edge label must be a string")
    if not value or value != value.strip():
        raise RecoveryContractError("recovery edge label must be non-empty and trimmed")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise RecoveryContractError("recovery edge label must not contain control characters")
    if len(value) > _MAX_LABEL_LENGTH:
        raise RecoveryContractError(
            f"recovery edge label must not exceed {_MAX_LABEL_LENGTH} characters"
        )
    return value


def _require_exact_fields(
    raw: Mapping[str, object], *, expected: frozenset[str], field_name: str
) -> None:
    actual = set(raw)
    if actual == expected:
        return
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise RecoveryContractError(f"{field_name} missing required fields: {sorted(missing)}")
    raise RecoveryContractError(f"{field_name} contains unknown fields: {sorted(unknown)}")


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _parse_json_object(raw: str, *, document_name: str) -> Mapping[str, object]:
    """Decode JSON text to a mapping, rejecting anything else, strictly."""
    if not isinstance(raw, str):
        raise RecoveryContractError(f"{document_name} JSON must be a string")
    try:
        decoded: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RecoveryContractError(f"{document_name} JSON is malformed") from exc
    if not isinstance(decoded, Mapping):
        raise RecoveryContractError(f"{document_name} JSON root must be an object")
    copied: dict[str, object] = {}
    for key, item in decoded.items():
        if not isinstance(key, str):
            raise RecoveryContractError(f"{document_name} JSON contains a non-string object key")
        copied[key] = item
    return copied


def _edge_sort_key(edge: RecoveryEdge) -> tuple[str, str, str, str]:
    """Total deterministic order for one recovery route."""
    return (
        edge.source.to_str(),
        edge.on_failure.value,
        edge.target.to_str(),
        "" if edge.label is None else edge.label,
    )


# --------------------------------------------------------------------------
# One typed recovery/error control-flow edge.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryEdge:
    """One typed, inert error/recovery control-flow transition.

    ``source`` is the node the declared failure happens at, ``target`` is the
    node execution belongs at afterwards, and ``on_failure`` is the single
    canonical :class:`~agentx.core.failure_taxonomy.FailureCategory` this
    transition applies to. ``label`` is optional inert descriptive text.

    The edge is pure control-flow data. It holds no capability, no callable,
    no path, no URI, no retry count, no timeout, no risk, no permission, no
    verification, and no outcome — no such field exists, so a recovery edge
    is structurally incapable of executing recovery, authorizing anything, or
    claiming success.
    """

    source: ProcedureNodeId
    target: ProcedureNodeId
    on_failure: FailureCategory
    label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source", _validate_node_ref(self.source, field_name="recovery edge source")
        )
        object.__setattr__(
            self, "target", _validate_node_ref(self.target, field_name="recovery edge target")
        )
        object.__setattr__(self, "on_failure", _validate_failure_category(self.on_failure))
        object.__setattr__(self, "label", _validate_label(self.label))
        if self.source == self.target:
            raise RecoveryContractError(
                "a recovery edge must transition to a different node: "
                f"{self.source.to_str()!r} is an unbounded retry, not a recovery route"
            )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible recovery-edge representation."""
        return {
            "source": self.source.to_str(),
            "target": self.target.to_str(),
            "on_failure": self.on_failure.value,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> RecoveryEdge:
        """Validate and reconstruct a recovery edge from a JSON mapping.

        Payloads claiming more than a transition (any extra field such as
        ``"retry"``, ``"verified"``, ``"permission"``, or ``"risk"``) are
        rejected, not interpreted.
        """
        if not isinstance(raw, Mapping):
            raise RecoveryContractError("recovery edge must be an object")
        _require_exact_fields(raw, expected=_RECOVERY_EDGE_FIELDS, field_name="recovery edge")
        return cls(
            source=_validate_node_ref(raw["source"], field_name="recovery edge source"),
            target=_validate_node_ref(raw["target"], field_name="recovery edge target"),
            on_failure=_validate_failure_category(raw["on_failure"]),
            label=_validate_label(raw["label"]),
        )


# --------------------------------------------------------------------------
# The graph-level recovery document.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureRecoveryEdges:
    """Explicit error/recovery control flow for one procedure graph.

    This is the smallest graph-level wrapper that can carry typed recovery
    routes without redesigning the canonical A3.01 graph: a companion DATA
    document holding :class:`RecoveryEdge` routes keyed by canonical
    :class:`~agentx.procedures.graph.ProcedureNodeId` references. It adds no
    nodes, no second edge schema, and no new node kind.

    Construction normalizes ordering so that two documents differing only in
    author-supplied order are identical data, rejects exact duplicate routes,
    and rejects ambiguous routes — one target per ``(source, failure
    category)`` pair, so no deterministic consumer ever has to choose. An
    empty document is valid and declares exactly that: no recovery routes.
    """

    contract_version: int = CURRENT_RECOVERY_CONTRACT_VERSION
    recovery_edges: tuple[RecoveryEdge, ...] = ()

    def __post_init__(self) -> None:
        _validate_contract_version(self.contract_version)
        if not isinstance(self.recovery_edges, tuple):
            raise RecoveryContractError("recovery_edges must be a tuple")
        for edge in self.recovery_edges:
            if not isinstance(edge, RecoveryEdge):
                raise RecoveryContractError("each recovery_edges item must be a RecoveryEdge")

        normalized = tuple(sorted(self.recovery_edges, key=_edge_sort_key))

        seen_routes: set[tuple[str, str, str]] = set()
        declared_targets: dict[tuple[str, str], str] = {}
        for edge in normalized:
            source_id = edge.source.to_str()
            target_id = edge.target.to_str()
            category = edge.on_failure.value
            route_key = (source_id, category, target_id)
            if route_key in seen_routes:
                raise RecoveryContractError(f"duplicate recovery edge: {route_key!r}")
            seen_routes.add(route_key)

            selection_key = (source_id, category)
            declared = declared_targets.get(selection_key)
            if declared is not None and declared != target_id:
                raise RecoveryContractError(
                    f"ambiguous recovery edges from {source_id!r} on failure {category!r}: "
                    f"targets {declared!r} and {target_id!r}"
                )
            declared_targets[selection_key] = target_id

        object.__setattr__(self, "recovery_edges", normalized)

    def to_dict(self) -> dict[str, object]:
        """Return the canonical, deterministically ordered JSON-compatible form."""
        return {
            "contract_version": self.contract_version,
            "recovery_edges": [edge.to_dict() for edge in self.recovery_edges],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedureRecoveryEdges:
        """Validate and reconstruct the document from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise RecoveryContractError("recovery document must be an object")
        _require_exact_fields(
            raw, expected=_RECOVERY_DOCUMENT_FIELDS, field_name="recovery document"
        )

        edges_raw = raw["recovery_edges"]
        if not isinstance(edges_raw, list):
            raise RecoveryContractError("recovery_edges must be a JSON array")
        edges: list[RecoveryEdge] = []
        for index, item in enumerate(edges_raw):
            if not isinstance(item, Mapping):
                raise RecoveryContractError(f"recovery_edges[{index}] must be an object")
            edges.append(RecoveryEdge.from_dict(item))

        return cls(
            contract_version=_validate_contract_version(raw["contract_version"]),
            recovery_edges=tuple(edges),
        )

    def to_json(self) -> str:
        """Serialize to deterministic, canonical, UTF-8-safe JSON text."""
        return _canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, raw: str) -> ProcedureRecoveryEdges:
        """Deserialize canonical JSON text without executable reconstruction."""
        return cls.from_dict(_parse_json_object(raw, document_name="recovery document"))

    def to_graph_edges(self) -> tuple[ProcedureEdge, ...]:
        """Render the declared routes as canonical A3.01 ``RECOVERY`` edges.

        This is the only way this contract touches graph connectivity, and it
        creates nothing new: the result is a deterministic tuple of ordinary
        :class:`~agentx.procedures.graph.ProcedureEdge` values carrying the
        canonical ``RECOVERY`` kind, which A3.01 already distinguishes from
        normal ``NEXT`` progression. Building a graph from these edges
        performs no side effects and starts no recovery.
        """
        return tuple(
            ProcedureEdge(source=edge.source, target=edge.target, kind=ProcedureEdgeKind.RECOVERY)
            for edge in self.recovery_edges
        )

    def declared_target(
        self,
        source: ProcedureNodeId | str,
        on_failure: FailureCategory | str,
    ) -> ProcedureNodeId | None:
        """Return the declared recovery target for one exact failure, or None.

        This is a read of declared data, not a resolution or a decision: the
        match is exact typed equality on the canonical node reference and the
        canonical failure category. ``None`` means the document declares no
        route for that failure at that node — there is no implicit fallback
        to a normal-progression edge, no implicit retry, and no catch-all.
        ``FailureCategory.UNKNOWN`` matches only itself.

        Malformed arguments fail closed with :class:`RecoveryContractError`;
        they are never answered with ``None``.
        """
        source_id = _validate_node_ref(source, field_name="recovery lookup source")
        category = _validate_failure_category(on_failure)
        for edge in self.recovery_edges:
            if edge.source == source_id and edge.on_failure is category:
                return edge.target
        return None

    def bind_to_graph(self, graph: ProcedureGraph) -> ProcedureRecoveryEdges:
        """Structurally cross-check the declared routes against an A3.01 graph.

        Fails closed when a route's source or target does not exist in
        ``graph`` (a dangling recovery target), when a route starts at an END
        node (END stays terminal), or when a route is not present in the
        graph as a canonical ``RECOVERY`` edge. On success returns ``self``
        unchanged.

        This is a membership and kind check of canonical references only: it
        reads no node payload, interprets no node family, and never modifies
        the graph. A ``RECOVERY`` edge the document does not annotate is left
        alone and stays inert, because an undeclared route has no failure
        condition and is therefore never applicable.
        """
        if not isinstance(graph, ProcedureGraph):
            raise RecoveryContractError("bind_to_graph expects a ProcedureGraph")

        node_kinds: dict[str, ProcedureNodeKind] = {
            node.id.to_str(): node.kind for node in graph.nodes
        }
        recovery_pairs = {
            (edge.source.to_str(), edge.target.to_str())
            for edge in graph.edges
            if edge.kind is ProcedureEdgeKind.RECOVERY
        }

        for edge in self.recovery_edges:
            source_id = edge.source.to_str()
            target_id = edge.target.to_str()
            if source_id not in node_kinds:
                raise RecoveryContractError(
                    f"recovery edge source node does not exist in the graph: {source_id!r}"
                )
            if target_id not in node_kinds:
                raise RecoveryContractError(
                    f"dangling recovery target, node does not exist in the graph: {target_id!r}"
                )
            if node_kinds[source_id] is ProcedureNodeKind.END:
                raise RecoveryContractError(
                    f"END node is terminal and must not be a recovery source: {source_id!r}"
                )
            if (source_id, target_id) not in recovery_pairs:
                raise RecoveryContractError(
                    "recovery edge is not present in the graph as a RECOVERY edge: "
                    f"{(source_id, target_id)!r}"
                )
        return self
