"""Typed BRANCH node-family contract (A3.03).

This module owns the DATA contract for the BRANCH node family of the
canonical A3.01 Procedure Graph IR (``agentx.procedures.graph``). A BRANCH
node records an *explicit future branching decision*: an ordered, non-empty
set of named outcomes, each with an optional inert condition descriptor.

The decision is inert data, never a program:

    - Conditions are plain strings, not expressions. There is no expression
      DSL, no eval/exec, no model-based or capability-based branch selection,
      and no L0-L5 or task routing. Nothing in this module interprets,
      evaluates, or matches a condition; how an outcome is ever chosen is
      owned by future interpreter/runtime tasks.
    - Connectivity is not restated here: the graph's typed outgoing edges
      from the BRANCH node carry where the graph can lead. This contract only
      names the outcomes and preserves their author order (priority).
    - Constructing, validating, serializing, or deserializing a branch
      contract performs no side effects and reaches no authority or runtime
      subsystem: it cannot create a Permission/AuthorityContext, bypass the
      ActionGate, lower risk, enlarge a budget, clear an EmergencyStop,
      execute a Capability or Executor, invoke a Reasoner/model, transition a
      Task, fabricate verification, activate a Procedure, or mutate Hive.
    - Hostile strings in names or conditions remain inert data.

The contract embeds in the A3.01 graph through ``ProcedureNode.params``:
:meth:`BranchContract.to_node` builds a ``ProcedureNode`` of kind BRANCH whose
opaque ``params`` are the canonical contract dict, and
:meth:`BranchContract.bind` / :meth:`BranchContract.from_dict` recover a
strictly typed, validated view of a node's opaque ``params``, failing closed
on anything that is not exactly this contract. The graph IR itself is not
redesigned: ``params`` stays opaque to the graph, and this module depends
only on the standard library plus the A3.01 graph module.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from agentx.procedures.graph import ProcedureNode, ProcedureNodeId, ProcedureNodeKind

__all__ = [
    "BranchContract",
    "BranchContractError",
    "BranchOutcome",
]

_NODE_KIND: Final[ProcedureNodeKind] = ProcedureNodeKind.BRANCH


class BranchContractError(ValueError):
    """Raised when data violates the BRANCH node-family contract."""


def _validate_descriptor(value: object, *, field_name: str) -> str:
    """Require a non-empty, trimmed string descriptor."""
    if not isinstance(value, str):
        raise BranchContractError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise BranchContractError(f"{field_name} must be non-empty and trimmed")
    return value


@dataclass(frozen=True, slots=True)
class BranchOutcome:
    """One named outcome of an explicit branching decision.

    ``name`` identifies the outcome within its branch (unique among the
    branch's outcomes). ``condition`` is an optional inert descriptor of
    what the outcome means; it is stored, compared, and serialized as plain
    data and is never evaluated, interpreted, or matched here.
    """

    name: str
    condition: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "name", _validate_descriptor(self.name, field_name="branch outcome name")
        )
        if self.condition is not None:
            object.__setattr__(
                self,
                "condition",
                _validate_descriptor(self.condition, field_name="branch outcome condition"),
            )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible outcome representation."""
        return {"name": self.name, "condition": self.condition}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> BranchOutcome:
        """Validate and reconstruct an outcome from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise BranchContractError("branch outcome must be an object")
        actual = set(raw)
        expected = {"name", "condition"}
        if actual != expected:
            missing = expected - actual
            unknown = actual - expected
            if missing:
                raise BranchContractError(
                    f"branch outcome missing required fields: {sorted(missing)}"
                )
            raise BranchContractError(f"branch outcome contains unknown fields: {sorted(unknown)}")
        condition_raw = raw["condition"]
        condition = (
            None
            if condition_raw is None
            else _validate_descriptor(condition_raw, field_name="branch outcome condition")
        )
        return cls(
            name=_validate_descriptor(raw["name"], field_name="branch outcome name"),
            condition=condition,
        )


@dataclass(frozen=True, slots=True)
class BranchContract:
    """Typed, inert contract for the BRANCH node family payload.

    ``outcomes`` is the ordered, non-empty set of named outcomes of the
    explicit branching decision. Outcome order is author-meaningful
    (priority) and is preserved exactly through serialization. The contract
    is a strictly validated view of the node's opaque ``params``; it never
    evaluates a condition and never chooses an outcome.
    """

    outcomes: tuple[BranchOutcome, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.outcomes, tuple):
            raise BranchContractError("outcomes must be a tuple")
        if not self.outcomes:
            raise BranchContractError("a branch must declare at least one outcome")
        seen: set[str] = set()
        for outcome in self.outcomes:
            if not isinstance(outcome, BranchOutcome):
                raise BranchContractError("each outcome must be a BranchOutcome")
            if outcome.name in seen:
                raise BranchContractError(f"duplicate branch outcome name: {outcome.name!r}")
            seen.add(outcome.name)
        object.__setattr__(self, "outcomes", tuple(self.outcomes))

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible contract representation."""
        return {"outcomes": [outcome.to_dict() for outcome in self.outcomes]}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> BranchContract:
        """Validate and reconstruct a contract from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise BranchContractError("branch contract must be an object")
        actual = set(raw)
        expected = {"outcomes"}
        if actual != expected:
            missing = expected - actual
            unknown = actual - expected
            if missing:
                raise BranchContractError(
                    f"branch contract missing required field: {sorted(missing)}"
                )
            raise BranchContractError(f"branch contract contains unknown fields: {sorted(unknown)}")
        outcomes_raw = raw["outcomes"]
        if not isinstance(outcomes_raw, list):
            raise BranchContractError("outcomes must be a list")
        outcomes = tuple(BranchOutcome.from_dict(item) for item in outcomes_raw)
        return cls(outcomes=outcomes)

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
    def from_json(cls, raw: str) -> BranchContract:
        """Deserialize JSON text into a validated contract (no execution)."""
        if not isinstance(raw, str):
            raise BranchContractError("branch contract JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BranchContractError("branch contract JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise BranchContractError("branch contract JSON root must be an object")
        return cls.from_dict(decoded)

    def to_node(self, node_id: str, *, label: str | None = None) -> ProcedureNode:
        """Build an A3.01 BRANCH node whose opaque params are this contract."""
        return ProcedureNode(
            ProcedureNodeId(node_id), _NODE_KIND, label=label, params=self.to_dict()
        )

    @classmethod
    def bind(cls, node: ProcedureNode) -> BranchContract:
        """Strictly view a node's opaque params as this contract.

        Fails closed unless ``node`` is a BRANCH node whose ``params`` are
        exactly a valid branch contract; the graph stays opaque otherwise.
        """
        if not isinstance(node, ProcedureNode):
            raise BranchContractError("bind expects a ProcedureNode")
        if node.kind is not _NODE_KIND:
            raise BranchContractError(f"expected a branch node, got node kind {node.kind.value!r}")
        return cls.from_dict(node.params)
