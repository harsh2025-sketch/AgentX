"""Typed WAIT node-family contract (A3.03).

This module owns the DATA contract for the WAIT node family of the
canonical A3.01 Procedure Graph IR (``agentx.procedures.graph``). A WAIT
node records an *explicit procedural waiting requirement*: what is being
awaited, plus an optional upper bound on how long the wait may last.

WAIT is data, and this module never waits:

    - It never sleeps, blocks, polls, creates timers, threads, or tasks,
      and performs no I/O of any kind. Constructing, validating,
      serializing, or deserializing a wait contract is a pure data
      operation. Actually waiting (and observing whether the requirement is
      satisfied) is owned by future runtime tasks, never here.
    - ``requirement`` is an inert descriptor naming what is awaited; it is
      stored, compared, and serialized as plain data and is never evaluated
      or matched here.
    - ``timeout_seconds`` reuses the canonical duration semantics of
      ``agentx.core.execution``: finite, non-negative seconds — the same
      domain as ``Deadline.after``'s ``timeout_seconds``. The absolute
      monotonic ``Deadline`` value itself is execution-scoped (its reading
      is only meaningful within one run), so a durable, reusable procedure
      records the wait requirement and its duration bound, not a live clock
      reading. ``None`` means no explicit bound: the requirement stands on
      its own and the wait lasts until it is satisfied.
    - Constructing, validating, serializing, or deserializing a wait
      contract reaches no authority or runtime subsystem: it cannot create
      a Permission/AuthorityContext, bypass the ActionGate, lower risk,
      enlarge a budget, clear an EmergencyStop, execute a Capability or
      Executor, invoke a Reasoner/model, transition a Task, fabricate
      verification, activate a Procedure, or mutate Hive.
    - Hostile strings in the requirement remain inert data.

The contract embeds in the A3.01 graph through ``ProcedureNode.params``:
:meth:`WaitContract.to_node` builds a ``ProcedureNode`` of kind WAIT whose
opaque ``params`` are the canonical contract dict, and
:meth:`WaitContract.bind` / :meth:`WaitContract.from_dict` recover a
strictly typed, validated view of a node's opaque ``params``, failing closed
on anything that is not exactly this contract. The graph IR itself is not
redesigned: ``params`` stays opaque to the graph, and this module depends
only on the standard library plus the A3.01 graph module.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from agentx.procedures.graph import ProcedureNode, ProcedureNodeId, ProcedureNodeKind

__all__ = [
    "WaitContract",
    "WaitContractError",
]

_NODE_KIND: Final[ProcedureNodeKind] = ProcedureNodeKind.WAIT


class WaitContractError(ValueError):
    """Raised when data violates the WAIT node-family contract."""


def _validate_requirement(value: object, *, field_name: str) -> str:
    """Require a non-empty, trimmed requirement descriptor."""
    if not isinstance(value, str):
        raise WaitContractError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise WaitContractError(f"{field_name} must be non-empty and trimmed")
    return value


def _normalize_timeout(value: object, *, field_name: str) -> float | None:
    """Require ``None`` or a finite, non-negative duration in seconds.

    Mirrors the canonical duration domain of ``agentx.core.execution``
    (``Deadline.after``): zero is a valid bound, negative or non-finite
    durations fail closed, and integers are normalized to the canonical
    float form so round-trips are exact.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise WaitContractError(
            f"{field_name} must be a finite number of seconds or null, got {type(value).__name__}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise WaitContractError(f"{field_name} must be finite")
    if number < 0:
        raise WaitContractError(f"{field_name} must be greater than or equal to zero")
    return number


@dataclass(frozen=True, slots=True)
class WaitContract:
    """Typed, inert contract for the WAIT node family payload.

    ``requirement`` names what is awaited (inert data). ``timeout_seconds``
    is the optional upper bound on the wait in seconds (canonical duration
    semantics: finite, non-negative; ``None`` = no explicit bound). This
    contract records the requirement only; it never waits, blocks, or
    performs I/O.
    """

    requirement: str
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requirement",
            _validate_requirement(self.requirement, field_name="requirement"),
        )
        object.__setattr__(
            self,
            "timeout_seconds",
            _normalize_timeout(self.timeout_seconds, field_name="timeout_seconds"),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible contract representation."""
        return {"requirement": self.requirement, "timeout_seconds": self.timeout_seconds}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> WaitContract:
        """Validate and reconstruct a contract from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise WaitContractError("wait contract must be an object")
        actual = set(raw)
        expected = {"requirement", "timeout_seconds"}
        if actual != expected:
            missing = expected - actual
            unknown = actual - expected
            if missing:
                raise WaitContractError(f"wait contract missing required fields: {sorted(missing)}")
            raise WaitContractError(f"wait contract contains unknown fields: {sorted(unknown)}")
        return cls(
            requirement=_validate_requirement(raw["requirement"], field_name="requirement"),
            timeout_seconds=_normalize_timeout(
                raw["timeout_seconds"], field_name="timeout_seconds"
            ),
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
    def from_json(cls, raw: str) -> WaitContract:
        """Deserialize JSON text into a validated contract (no waiting)."""
        if not isinstance(raw, str):
            raise WaitContractError("wait contract JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WaitContractError("wait contract JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise WaitContractError("wait contract JSON root must be an object")
        return cls.from_dict(decoded)

    def to_node(self, node_id: str, *, label: str | None = None) -> ProcedureNode:
        """Build an A3.01 WAIT node whose opaque params are this contract."""
        return ProcedureNode(
            ProcedureNodeId(node_id), _NODE_KIND, label=label, params=self.to_dict()
        )

    @classmethod
    def bind(cls, node: ProcedureNode) -> WaitContract:
        """Strictly view a node's opaque params as this contract.

        Fails closed unless ``node`` is a WAIT node whose ``params`` are
        exactly a valid wait contract; the graph stays opaque otherwise.
        """
        if not isinstance(node, ProcedureNode):
            raise WaitContractError("bind expects a ProcedureNode")
        if node.kind is not _NODE_KIND:
            raise WaitContractError(f"expected a wait node, got node kind {node.kind.value!r}")
        return cls.from_dict(node.params)
