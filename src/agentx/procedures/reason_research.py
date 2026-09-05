"""Canonical A3.04 REASON and RESEARCH node data contracts.

A3.01 already owns the canonical :class:`ProcedureNode` and node-kind
vocabulary.  This module does not create a second node hierarchy.  Instead it
provides strict, immutable node-family payload contracts that round-trip through
``ProcedureNode.params`` for the existing ``REASON`` and ``RESEARCH`` kinds.

Both contracts are DATA only.  Constructing, parsing, serializing, or attaching
a payload never invokes a Reasoner or ModelProvider, performs research, touches
the network or filesystem, grants authority, mutates a Task, verifies an
action, changes knowledge state, activates a procedure, or executes a
Capability.

The REASON contract stores only the canonical serialized token for the logical
A2.02 ``ModelRole.REASONING`` role rather than importing the cognition layer.
This preserves the established procedures -> core-only dependency direction.
Tests pin the token to ``ModelRole.REASONING.value`` so the contracts cannot
drift into a competing role vocabulary.  No provider, vendor, or physical model
identifier is representable.

Objectives, references, constraints, and bindings are inert strings.  Hostile
text such as ``permission=WRITE``, ``ALLOW R4``, or ``ignore policy`` is stored
verbatim and has no control meaning.  There is deliberately no chain-of-thought,
reasoning-trace, verification, trust, authority, status, or execution field.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from agentx.procedures.graph import (
    ProcedureGraphDeserializationError,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)

__all__ = [
    "CURRENT_REASON_RESEARCH_CONTRACT_VERSION",
    "ReasonNodeSpec",
    "ReasonResearchContractError",
    "ResearchNodeSpec",
]

CURRENT_REASON_RESEARCH_CONTRACT_VERSION: Final[int] = 1
_REASONING_ROLE_TOKEN: Final[str] = "REASONING"


class ReasonResearchContractError(ValueError):
    """Raised when a REASON/RESEARCH payload violates its data contract."""


def _require_trimmed_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ReasonResearchContractError(f"{field_name} must be a string")
    normalized = str(value)
    if normalized == "" or normalized != normalized.strip():
        raise ReasonResearchContractError(f"{field_name} must be non-empty and trimmed")
    return normalized


def _require_version(value: object) -> int:
    if type(value) is not int:
        raise ReasonResearchContractError("contract_version must be an integer")
    if value != CURRENT_REASON_RESEARCH_CONTRACT_VERSION:
        raise ReasonResearchContractError(
            "unsupported REASON/RESEARCH contract version "
            f"{value}; supported version is "
            f"{CURRENT_REASON_RESEARCH_CONTRACT_VERSION}"
        )
    return value


def _require_text_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ReasonResearchContractError(f"{field_name} must be a tuple")
    normalized = tuple(_require_trimmed_text(item, field_name=field_name) for item in value)
    if len(set(normalized)) != len(normalized):
        raise ReasonResearchContractError(f"{field_name} must not contain duplicate references")
    return normalized


def _read_json_text_list(value: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ReasonResearchContractError(f"{field_name} must be a JSON array")
    return _require_text_tuple(tuple(value), field_name=field_name)


def _require_exact_fields(
    raw: Mapping[str, object], *, expected: frozenset[str], payload_name: str
) -> None:
    actual = set(raw)
    if actual == expected:
        return
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise ReasonResearchContractError(
            f"{payload_name} missing required fields: {sorted(missing)}"
        )
    raise ReasonResearchContractError(f"{payload_name} contains unknown fields: {sorted(unknown)}")


def _canonical_json(payload: Mapping[str, object]) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReasonResearchContractError(
            "REASON/RESEARCH payload is not canonical JSON data"
        ) from exc


def _parse_json_object(raw: str, *, payload_name: str) -> Mapping[str, object]:
    if not isinstance(raw, str):
        raise TypeError("raw JSON must be a string")
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ReasonResearchContractError(f"{payload_name} JSON is malformed") from exc
    if not isinstance(decoded, Mapping):
        raise ReasonResearchContractError(f"{payload_name} JSON must encode an object")
    return decoded


def _require_node(node: ProcedureNode, *, expected_kind: ProcedureNodeKind) -> ProcedureNode:
    if not isinstance(node, ProcedureNode):
        raise TypeError("node must be a ProcedureNode")
    if node.kind is not expected_kind:
        raise ReasonResearchContractError(
            f"expected {expected_kind.value} node, got {node.kind.value}"
        )
    return node


@dataclass(frozen=True, slots=True, kw_only=True)
class ReasonNodeSpec:
    """Strict inert payload for one canonical ``REASON`` ProcedureNode.

    ``logical_model_role`` is the serialized A2.02 ``ModelRole.REASONING``
    token.  The contract intentionally carries no model/provider/vendor
    identity and no prompt, chain-of-thought, verification, trust, or authority
    field.
    """

    objective: str
    output_binding: str
    input_references: tuple[str, ...] = ()
    logical_model_role: str = _REASONING_ROLE_TOKEN
    contract_version: int = CURRENT_REASON_RESEARCH_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "objective",
            _require_trimmed_text(self.objective, field_name="objective"),
        )
        object.__setattr__(
            self,
            "output_binding",
            _require_trimmed_text(self.output_binding, field_name="output_binding"),
        )
        object.__setattr__(
            self,
            "input_references",
            _require_text_tuple(self.input_references, field_name="input_references"),
        )
        role = _require_trimmed_text(self.logical_model_role, field_name="logical_model_role")
        if role != _REASONING_ROLE_TOKEN:
            raise ReasonResearchContractError(
                "REASON logical_model_role must be canonical REASONING"
            )
        object.__setattr__(self, "logical_model_role", role)
        _require_version(self.contract_version)

    def to_dict(self) -> dict[str, object]:
        """Return the strict JSON-compatible payload representation."""
        return {
            "contract_version": self.contract_version,
            "input_references": list(self.input_references),
            "logical_model_role": self.logical_model_role,
            "objective": self.objective,
            "output_binding": self.output_binding,
        }

    def to_json(self) -> str:
        """Return deterministic canonical JSON for this payload."""
        return _canonical_json(self.to_dict())

    def to_node(self, *, node_id: ProcedureNodeId, label: str | None = None) -> ProcedureNode:
        """Attach this inert payload to the canonical A3.01 REASON node kind."""
        if not isinstance(node_id, ProcedureNodeId):
            raise TypeError("node_id must be a ProcedureNodeId")
        return ProcedureNode(
            id=node_id,
            kind=ProcedureNodeKind.REASON,
            label=label,
            params=self.to_dict(),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ReasonNodeSpec:
        if not isinstance(raw, Mapping):
            raise ReasonResearchContractError("REASON payload must be an object")
        _require_exact_fields(
            raw,
            expected=frozenset(
                {
                    "contract_version",
                    "input_references",
                    "logical_model_role",
                    "objective",
                    "output_binding",
                }
            ),
            payload_name="REASON payload",
        )
        return cls(
            contract_version=_require_version(raw["contract_version"]),
            input_references=_read_json_text_list(
                raw["input_references"], field_name="input_references"
            ),
            logical_model_role=_require_trimmed_text(
                raw["logical_model_role"], field_name="logical_model_role"
            ),
            objective=_require_trimmed_text(raw["objective"], field_name="objective"),
            output_binding=_require_trimmed_text(
                raw["output_binding"], field_name="output_binding"
            ),
        )

    @classmethod
    def from_json(cls, raw: str) -> ReasonNodeSpec:
        return cls.from_dict(_parse_json_object(raw, payload_name="REASON payload"))

    @classmethod
    def from_node(cls, node: ProcedureNode) -> ReasonNodeSpec:
        canonical = _require_node(node, expected_kind=ProcedureNodeKind.REASON)
        try:
            return cls.from_dict(canonical.params)
        except ProcedureGraphDeserializationError as exc:
            raise ReasonResearchContractError("invalid REASON node payload") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class ResearchNodeSpec:
    """Strict inert payload for one canonical ``RESEARCH`` ProcedureNode.

    The contract describes a knowledge gap and where future evidence/output
    data should be bound.  ``constraints`` are descriptive strings only; they
    do not fetch, browse, execute, verify, trust, or promote anything.
    """

    objective: str
    output_binding: str
    expected_evidence_bindings: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    contract_version: int = CURRENT_REASON_RESEARCH_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "objective",
            _require_trimmed_text(self.objective, field_name="objective"),
        )
        object.__setattr__(
            self,
            "output_binding",
            _require_trimmed_text(self.output_binding, field_name="output_binding"),
        )
        object.__setattr__(
            self,
            "expected_evidence_bindings",
            _require_text_tuple(
                self.expected_evidence_bindings,
                field_name="expected_evidence_bindings",
            ),
        )
        object.__setattr__(
            self,
            "constraints",
            _require_text_tuple(self.constraints, field_name="constraints"),
        )
        _require_version(self.contract_version)

    def to_dict(self) -> dict[str, object]:
        """Return the strict JSON-compatible payload representation."""
        return {
            "constraints": list(self.constraints),
            "contract_version": self.contract_version,
            "expected_evidence_bindings": list(self.expected_evidence_bindings),
            "objective": self.objective,
            "output_binding": self.output_binding,
        }

    def to_json(self) -> str:
        """Return deterministic canonical JSON for this payload."""
        return _canonical_json(self.to_dict())

    def to_node(self, *, node_id: ProcedureNodeId, label: str | None = None) -> ProcedureNode:
        """Attach this inert payload to the canonical A3.01 RESEARCH node kind."""
        if not isinstance(node_id, ProcedureNodeId):
            raise TypeError("node_id must be a ProcedureNodeId")
        return ProcedureNode(
            id=node_id,
            kind=ProcedureNodeKind.RESEARCH,
            label=label,
            params=self.to_dict(),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ResearchNodeSpec:
        if not isinstance(raw, Mapping):
            raise ReasonResearchContractError("RESEARCH payload must be an object")
        _require_exact_fields(
            raw,
            expected=frozenset(
                {
                    "constraints",
                    "contract_version",
                    "expected_evidence_bindings",
                    "objective",
                    "output_binding",
                }
            ),
            payload_name="RESEARCH payload",
        )
        return cls(
            constraints=_read_json_text_list(raw["constraints"], field_name="constraints"),
            contract_version=_require_version(raw["contract_version"]),
            expected_evidence_bindings=_read_json_text_list(
                raw["expected_evidence_bindings"],
                field_name="expected_evidence_bindings",
            ),
            objective=_require_trimmed_text(raw["objective"], field_name="objective"),
            output_binding=_require_trimmed_text(
                raw["output_binding"], field_name="output_binding"
            ),
        )

    @classmethod
    def from_json(cls, raw: str) -> ResearchNodeSpec:
        return cls.from_dict(_parse_json_object(raw, payload_name="RESEARCH payload"))

    @classmethod
    def from_node(cls, node: ProcedureNode) -> ResearchNodeSpec:
        canonical = _require_node(node, expected_kind=ProcedureNodeKind.RESEARCH)
        try:
            return cls.from_dict(canonical.params)
        except ProcedureGraphDeserializationError as exc:
            raise ReasonResearchContractError("invalid RESEARCH node payload") from exc
