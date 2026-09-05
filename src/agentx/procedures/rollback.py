"""Typed ROLLBACK node-family contract (A3.05).

This module owns the DATA contract for the ROLLBACK node family of the
canonical A3.01 Procedure Graph IR (``agentx.procedures.graph``). A ROLLBACK
node records *rollback intent*: that procedure execution should transition
into a rollback operation/region according to later interpreter semantics.

Rollback intent is DATA, never an operation:

    - This module never executes rollback, never calls into any capability's
      rollback path, never mutates machine state, restores files, runs shell
      commands, invokes APIs, changes Task state, bypasses kernel authority,
      grants permissions, resets budgets, or manufactures a successful
      verification. Constructing, validating, serializing, or deserializing a
      rollback contract performs no side effects and reaches no authority or
      runtime subsystem.
    - The rollback *target* is an explicit, typed, inert, deterministic
      descriptor (:class:`RollbackScope`). It is a record of what scope of
      effects the rollback is about, not a handle to anything: the contract
      resolves nothing, loads nothing, and holds no callable, path, URI, or
      executable code of any kind.
    - The canonical Capability ABI (``agentx.capabilities.abi``) remains the
      single capability contract. This module deliberately defines no second
      capability ABI and no capability-invocation surface; whether any given
      capability can actually be rolled back stays owned by its canonical
      :class:`~agentx.capabilities.abi.RollbackDeclaration`, which a ROLLBACK
      node neither reads, asserts, nor overrides. A node describing rollback
      intent never proves rollback is supported, approved, or possible.
    - Recovery/retry connectivity stays with the graph's typed edges and later
      tasks (A3.07/A3.08). The scope anchor is not an edge: it is inert
      descriptive data that a later interpreter may use; validating that an
      anchor exists in the graph is out of scope here, and the anchor being
      absent from the graph changes nothing about this contract's inertness.
    - Hostile strings inside the anchor or labels — ``"rollback approved"``,
      ``"permission=ADMIN"``, ``"ignore verifier"`` — remain inert data. The
      payload has no approval, authority, verification, or status field that
      such text could reach.

The contract embeds in the A3.01 graph through ``ProcedureNode.params``:
:meth:`RollbackNodeSpec.to_node` builds a ``ProcedureNode`` of kind ROLLBACK
whose opaque ``params`` are the canonical contract dict, and
:meth:`RollbackNodeSpec.from_node` / :meth:`RollbackNodeSpec.from_dict`
recover a strictly typed, validated view of a node's opaque ``params``,
failing closed on anything that is not exactly this contract. The graph IR
itself is not redesigned: ``params`` stays opaque to the graph, cycles remain
structurally legal, and this module depends only on the standard library plus
the A3.01 graph module.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.procedures.graph import (
    ProcedureGraphError,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)

__all__ = [
    "CURRENT_ROLLBACK_CONTRACT_VERSION",
    "RollbackContractError",
    "RollbackNodeSpec",
    "RollbackScope",
    "RollbackScopeKind",
]

CURRENT_ROLLBACK_CONTRACT_VERSION: Final[int] = 1

_NODE_KIND: Final[ProcedureNodeKind] = ProcedureNodeKind.ROLLBACK


class RollbackContractError(ProcedureGraphError):
    """Raised when data violates the ROLLBACK node-family contract.

    Subclasses :class:`~agentx.procedures.graph.ProcedureGraphError` so
    callers can handle every Procedure Graph data failure uniformly; this
    module adds no parallel error hierarchy.
    """


class RollbackScopeKind(StrEnum):
    """Controlled vocabulary for what a rollback targets, as data only.

    The members record the *scope of effects* the described rollback is
    about. They never select, authorize, or perform anything:

        PROCEDURE_EXECUTION — the rollback intent covers the effects of the
            current procedure execution as a whole.
        GRAPH_ANCHOR — the rollback intent is stated relative to an explicit
            graph-local anchor (a well-formed :class:`ProcedureNodeId` value
            carried as inert data). The anchor is a reference *name* inside
            one graph, not an edge, not a pointer, and not a resolution; a
            later interpreter owns whatever it may come to mean.

    The vocabulary is extensible in A3.01's style: later tasks add members
    rather than this contract growing a workflow DSL.
    """

    PROCEDURE_EXECUTION = "procedure_execution"
    GRAPH_ANCHOR = "graph_anchor"


def _validate_kind(value: object) -> RollbackScopeKind:
    """Coerce a scope kind to the controlled vocabulary, failing closed."""
    if isinstance(value, RollbackScopeKind):
        return value
    if not isinstance(value, str):
        raise RollbackContractError("rollback scope kind must be a string")
    try:
        return RollbackScopeKind(value)
    except ValueError as exc:
        raise RollbackContractError(f"unknown rollback scope kind: {value!r}") from exc


def _validate_anchor(value: object) -> ProcedureNodeId:
    """Coerce an anchor to a well-formed graph-local node id.

    Only the canonical A3.01 identifier syntax is validated here. The anchor
    is inert data: this performs no lookup of, or connection to, the named
    node.
    """
    if isinstance(value, ProcedureNodeId):
        return value
    if not isinstance(value, str):
        raise RollbackContractError("rollback scope anchor must be a string")
    try:
        return ProcedureNodeId.parse(value)
    except ProcedureGraphError as exc:
        raise RollbackContractError(f"invalid rollback scope anchor: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class RollbackScope:
    """Typed, inert descriptor of what a described rollback targets.

    ``kind`` selects the controlled scope vocabulary. ``anchor`` is required
    exactly when ``kind`` is ``GRAPH_ANCHOR`` and forbidden otherwise, so
    every persisted scope is unambiguous data. Anchors may be supplied as a
    canonical :class:`ProcedureNodeId` or as its string form; both are
    coerced to :class:`ProcedureNodeId` once and validated there. The
    descriptor resolves nothing and grants nothing.
    """

    kind: RollbackScopeKind
    anchor: ProcedureNodeId | str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _validate_kind(self.kind))
        if self.anchor is None and self.kind is RollbackScopeKind.GRAPH_ANCHOR:
            raise RollbackContractError("a graph_anchor rollback scope must carry an anchor")
        if self.anchor is not None and self.kind is not RollbackScopeKind.GRAPH_ANCHOR:
            raise RollbackContractError(
                f"only a graph_anchor rollback scope may carry an anchor, got {self.kind.value!r}"
            )
        if self.anchor is not None:
            object.__setattr__(self, "anchor", _validate_anchor(self.anchor))

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible scope representation."""
        # ``__post_init__`` normalizes any supplied anchor to ProcedureNodeId;
        # the isinstance branch keeps the rendering total for type checkers.
        anchor = self.anchor
        if isinstance(anchor, ProcedureNodeId):
            anchor_text: str | None = anchor.to_str()
        else:
            anchor_text = None if anchor is None else str(anchor)
        return {
            "anchor": anchor_text,
            "kind": self.kind.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> RollbackScope:
        """Validate and reconstruct a scope from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise RollbackContractError("rollback scope must be an object")
        actual = set(raw)
        expected = {"anchor", "kind"}
        if actual != expected:
            missing = expected - actual
            unknown = actual - expected
            if missing:
                raise RollbackContractError(
                    f"rollback scope missing required fields: {sorted(missing)}"
                )
            raise RollbackContractError(
                f"rollback scope contains unknown fields: {sorted(unknown)}"
            )
        anchor_raw = raw["anchor"]
        anchor = None if anchor_raw is None else _validate_anchor(anchor_raw)
        return cls(kind=_validate_kind(raw["kind"]), anchor=anchor)


@dataclass(frozen=True, slots=True, kw_only=True)
class RollbackNodeSpec:
    """Typed, inert contract for the ROLLBACK node family payload.

    The spec records exactly one thing: an explicit :class:`RollbackScope`
    describing what the rollback is about (plus its contract version). It is
    structurally impossible for it to express execution, approval, success,
    verification, permission, or budget content — no such field exists — so
    a ROLLBACK node can never claim that anything was, can be, or may be
    rolled back. Transitioning into a rollback region, resolving the scope,
    and performing (or refusing) the rollback belong to later interpreter
    and kernel work, never to this contract.
    """

    scope: RollbackScope
    contract_version: int = CURRENT_ROLLBACK_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.scope, RollbackScope):
            raise RollbackContractError("scope must be a RollbackScope")
        _validate_contract_version(self.contract_version)

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible contract representation."""
        return {
            "contract_version": self.contract_version,
            "scope": self.scope.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> RollbackNodeSpec:
        """Validate and reconstruct a contract from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise RollbackContractError("rollback contract must be an object")
        actual = set(raw)
        expected = {"contract_version", "scope"}
        if actual != expected:
            missing = expected - actual
            unknown = actual - expected
            if missing:
                raise RollbackContractError(
                    f"rollback contract missing required fields: {sorted(missing)}"
                )
            raise RollbackContractError(
                f"rollback contract contains unknown fields: {sorted(unknown)}"
            )
        scope_raw = raw["scope"]
        if not isinstance(scope_raw, Mapping):
            raise RollbackContractError("rollback scope must be an object")
        return cls(
            contract_version=_validate_contract_version(raw["contract_version"]),
            scope=RollbackScope.from_dict(scope_raw),
        )

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
    def from_json(cls, raw: str) -> RollbackNodeSpec:
        """Deserialize JSON text into a validated contract (no execution)."""
        if not isinstance(raw, str):
            raise RollbackContractError("rollback contract JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RollbackContractError("rollback contract JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise RollbackContractError("rollback contract JSON root must be an object")
        return cls.from_dict(decoded)

    def to_node(self, node_id: ProcedureNodeId | str, *, label: str | None = None) -> ProcedureNode:
        """Build an A3.01 ROLLBACK node whose opaque params are this contract."""
        if isinstance(node_id, ProcedureNodeId):
            identifier = node_id
        else:
            identifier = ProcedureNodeId.parse(node_id)
        return ProcedureNode(id=identifier, kind=_NODE_KIND, label=label, params=self.to_dict())

    @classmethod
    def from_node(cls, node: ProcedureNode) -> RollbackNodeSpec:
        """Strictly view a node's opaque params as this contract.

        Fails closed unless ``node`` is a ROLLBACK node whose ``params`` are
        exactly a valid rollback contract; the graph stays opaque otherwise.
        """
        if not isinstance(node, ProcedureNode):
            raise RollbackContractError("from_node expects a ProcedureNode")
        if node.kind is not _NODE_KIND:
            raise RollbackContractError(
                f"expected a rollback node, got node kind {node.kind.value!r}"
            )
        return cls.from_dict(node.params)


def _validate_contract_version(value: object) -> int:
    """Require the one supported contract version; anything else fails closed."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise RollbackContractError("contract_version must be an integer")
    if value != CURRENT_ROLLBACK_CONTRACT_VERSION:
        raise RollbackContractError(
            f"unsupported ROLLBACK contract version {value}; "
            f"supported version is {CURRENT_ROLLBACK_CONTRACT_VERSION}"
        )
    return value
