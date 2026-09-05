"""Typed END node-family contract (A3.05).

This module owns the DATA contract for the END node family of the canonical
A3.01 Procedure Graph IR (``agentx.procedures.graph``). END represents
*explicit procedure termination intent* and nothing more. This is
deliberately the smallest useful contract.

END is graph control-flow data, never an outcome:

    - The canonical A3.01 graph already owns the only structural END rule
      (END is terminal: it must not have outgoing edges) and does NOT
      distinguish terminal outcome intents — there is one END kind, so this
      contract introduces no outcome, verdict, status, reason, or
      success vocabulary of its own. The payload is a version marker only;
      the contract's job is strict, deterministic, inert *containment*: an
      END node's params are valid if and only if they are exactly this
      contract, and every other shape fails closed.
    - It is structurally impossible for an END node to mark a Task
      successful, to create ``VERIFIED`` success, to call a Verifier, to
      transition a Task, or to bypass verification — no field that could
      carry such a claim exists, and hostile fields such as
      ``"verified": true`` or ``"outcome": "success"`` are rejected at the
      contract boundary rather than interpreted.
    - The governing invariant, unchanged from A1.10/canonical verification::

          reaching END != verified task success
          No action == success without verification.

      Reaching a validly constructed END node remains mere graph data for a
      later interpreter to route; whether any task ever counts as verified
      successful is decided exclusively by the canonical verifier against
      real evidence, elsewhere.
    - Constructing, validating, serializing, or deserializing an END
      contract performs no side effects and reaches no authority or runtime
      subsystem: it cannot create a Permission/AuthorityContext, alter a
      RiskLevel, bypass the ActionGate, modify a ResourceEnvelope, clear an
      EmergencyStop, execute capabilities, transition tasks, or produce a
      VerificationResult.

The contract embeds in the A3.01 graph through ``ProcedureNode.params``:
:meth:`EndNodeSpec.to_node` builds a ``ProcedureNode`` of kind END whose
opaque ``params`` are the canonical contract dict, and
:meth:`EndNodeSpec.from_node` / :meth:`EndNodeSpec.from_dict` recover a
strictly typed, validated view of a node's opaque ``params``, failing closed
on anything that is not exactly this contract. The graph IR itself is not
redesigned: ``params`` stay opaque to the graph, plain graph-level END nodes
without any payload remain legal A3.01 structure (the contract validates a
payload when asked to view one; it does not impose new graph rules), and this
module depends only on the standard library plus the A3.01 graph module.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from agentx.procedures.graph import (
    ProcedureGraphError,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)

__all__ = [
    "CURRENT_END_CONTRACT_VERSION",
    "EndContractError",
    "EndNodeSpec",
]

CURRENT_END_CONTRACT_VERSION: Final[int] = 1

_NODE_KIND: Final[ProcedureNodeKind] = ProcedureNodeKind.END


class EndContractError(ProcedureGraphError):
    """Raised when data violates the END node-family contract.

    Subclasses :class:`~agentx.procedures.graph.ProcedureGraphError` so
    callers can handle every Procedure Graph data failure uniformly; this
    module adds no parallel error hierarchy.
    """


def _validate_contract_version(value: object) -> int:
    """Require the one supported contract version; anything else fails closed."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise EndContractError("contract_version must be an integer")
    if value != CURRENT_END_CONTRACT_VERSION:
        raise EndContractError(
            f"unsupported END contract version {value}; "
            f"supported version is {CURRENT_END_CONTRACT_VERSION}"
        )
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class EndNodeSpec:
    """Typed, inert contract for the END node family payload.

    The spec carries only its contract version — the smallest representation
    that can pin a canonical END node to a strict, serializable payload.
    Because there is no other field, an END node can never encode success,
    failure, verification, approval, or any other outcome intent, and an
    author who tries to smuggle one in (``verified=true`` as an extra
    payload field, a future version with extra semantics) fails closed at
    construction or binding time. Terminal control-flow meaning stays where
    A3.01 put it: END is terminal, END has no outgoing edges, and nothing
    more may be read into reaching one.
    """

    contract_version: int = CURRENT_END_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _validate_contract_version(self.contract_version)

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible contract representation."""
        return {"contract_version": self.contract_version}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EndNodeSpec:
        """Validate and reconstruct a contract from a JSON-compatible mapping.

        Payloads claiming more than termination (any extra field such as
        ``"outcome"``, ``"success"``, ``"verified"``, or ``"status"``) are
        rejected, not interpreted.
        """
        if not isinstance(raw, Mapping):
            raise EndContractError("end contract must be an object")
        actual = set(raw)
        expected = {"contract_version"}
        if actual != expected:
            missing = expected - actual
            unknown = actual - expected
            if missing:
                raise EndContractError(f"end contract missing required fields: {sorted(missing)}")
            raise EndContractError(f"end contract contains unknown fields: {sorted(unknown)}")
        return cls(contract_version=_validate_contract_version(raw["contract_version"]))

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
    def from_json(cls, raw: str) -> EndNodeSpec:
        """Deserialize JSON text into a validated contract (no outcome)."""
        if not isinstance(raw, str):
            raise EndContractError("end contract JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EndContractError("end contract JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise EndContractError("end contract JSON root must be an object")
        return cls.from_dict(decoded)

    def to_node(self, node_id: ProcedureNodeId | str, *, label: str | None = None) -> ProcedureNode:
        """Build an A3.01 END node whose opaque params are this contract."""
        if isinstance(node_id, ProcedureNodeId):
            identifier = node_id
        else:
            identifier = ProcedureNodeId.parse(node_id)
        return ProcedureNode(id=identifier, kind=_NODE_KIND, label=label, params=self.to_dict())

    @classmethod
    def from_node(cls, node: ProcedureNode) -> EndNodeSpec:
        """Strictly view a node's opaque params as this contract.

        Fails closed unless ``node`` is an END node whose ``params`` are
        exactly a valid end contract; the graph stays opaque otherwise. The
        structural terminality of END (no outgoing edges) is enforced by the
        graph itself, independent of what any payload claims.
        """
        if not isinstance(node, ProcedureNode):
            raise EndContractError("from_node expects a ProcedureNode")
        if node.kind is not _NODE_KIND:
            raise EndContractError(f"expected an end node, got node kind {node.kind.value!r}")
        return cls.from_dict(node.params)
