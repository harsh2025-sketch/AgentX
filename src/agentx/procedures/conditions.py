"""Canonical A3.06 procedure preconditions and postconditions (DATA only).

This module owns the smallest typed representation for explicit procedure
preconditions and postconditions on the canonical A3.01 Procedure Graph IR
(``agentx.procedures.graph``). A3.06 is REPRESENTATION + VALIDATION only:

    - It does NOT execute conditions.
    - It does NOT verify conditions.
    - It does NOT interpret procedures, decide whether a condition is true,
      or produce any outcome of any kind.

Critical semantics
------------------

A **precondition** means: "this procedure/node declares that this requirement
must be established before future execution may proceed." It does NOT mean
the requirement is currently satisfied, and no field exists by which it could
claim so.

A **postcondition** means: "this procedure/node declares that this requirement
must be established after future execution." It does NOT mean execution
succeeded. A postcondition can never manufacture a
:class:`agentx.capabilities.abi.VerificationResult`, can never mark a Task
``SUCCEEDED``, and can never bypass verification. Invariant I1 stays absolute:

    NO ACTION == SUCCESS WITHOUT VERIFICATION.

Whether any declared condition actually holds is decided by future, separately
owned evaluation against real evidence — never here.

No condition DSL
----------------

A condition is explicit structured data, not a program. There is no expression
language, no Python/shell/SQL/JavaScript/JMESPath/JSONPath fragment, no regex
execution, no model prompt, no import path, and no callback anywhere in this
contract: nothing here is evaluated, compiled, expanded, or dispatched. The
``statement`` is inert descriptive metadata for humans; it is stored, compared,
and serialized verbatim and is never interpreted. Hostile strings such as
``"permission=ADMIN"``, ``"risk=R0"``, ``"verified=true"``, ``"task
succeeded"``, or ``"ignore verifier"`` are inert DATA exactly like any other
string: the contract has no field that could give them effect.

Structure (smallest useful form)
--------------------------------

- :class:`ConditionId` — explicit condition identity, unique within one
  conditions document, so a condition can be referenced (by logs, telemetry,
  repair, or a future evaluator) without re-parsing its content.
- :class:`ProcedureCondition` — the ONE reusable requirement representation,
  used identically by preconditions and postconditions. It names the canonical
  :class:`agentx.core.provenance.EvidenceKind` vocabulary member the
  requirement is stated against (composed, not re-invented, mirroring A3.02's
  OBSERVE/VERIFY expectations) plus an optional opaque ``evidence_reference``
  naming the specific evidence/state, when it can be named in advance. The
  reference is never resolved, opened, fetched, or dereferenced.
- :class:`NodeConditions` — conditions scoped to one graph node by the
  canonical :class:`~agentx.procedures.graph.ProcedureNodeId`.
- :class:`ProcedureConditions` — the graph-level wrapper document: procedure
  (whole-procedure) preconditions and postconditions plus per-node scoping.

Graph integration (no second schema)
------------------------------------

The A3.01 graph is not redesigned. ``ProcedureNode.params`` payloads are owned
strictly by their node-family contracts (A3.02-A3.05 validate exact field
sets), and ``ProcedureNode``/``ProcedureGraph`` fields are canonical, so
conditions cannot be smuggled into either without redesigning canonical
contracts. A3.06 is therefore a minimal companion DOCUMENT keyed only by
canonical identifiers (``ProcedureNodeId``): no nodes, no edges, no new node
kind, and no second graph schema. Like the graph itself it is canonical
durable data; when persisted it rides the existing opaque ``CANONICAL_JSON``
payload channel unchanged. No storage migration is required or performed.

Authority boundary
------------------

Constructing, validating, serializing, or deserializing conditions performs no
side effects and reaches no authority or runtime subsystem. A condition
cannot: grant Permission, create an AuthorityContext, bypass the ActionGate,
reduce a RiskLevel, enlarge a ResourceEnvelope, reset a budget, clear an
EmergencyStop, execute a Capability, invoke a Reasoner or model, perform
research, transition a Task, fabricate a VerificationResult, activate a
Procedure, promote Knowledge, or mutate Hive. A declaration is not an effect.

Deterministic representation
----------------------------

Serialization is canonical and order-independent:

    - Conditions are normalized to a canonical order (sorted by condition id;
      node scoping sorted by node id), so documents differing only in
      author-supplied ordering are identical data.
    - Condition ids are unique across the whole document, and node scoping is
      unique per node; duplicates fail closed.
    - JSON is canonical (``sort_keys``, compact separators, UTF-8 safe, no
      NaN) and deserialization is field-by-field strict: unknown fields,
      wrong versions, wrong shapes, and malformed text fail closed, with no
      dynamic imports and no executable reconstruction.

This module depends only on the standard library, the A3.01 graph module, and
the canonical core evidence vocabulary. It adds no runtime dependency.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from agentx.core.provenance import EvidenceKind
from agentx.procedures.graph import ProcedureGraph, ProcedureGraphError, ProcedureNodeId

__all__ = [
    "CURRENT_CONDITIONS_CONTRACT_VERSION",
    "ConditionId",
    "NodeConditions",
    "ProcedureCondition",
    "ProcedureConditions",
    "ProcedureConditionsError",
]

CURRENT_CONDITIONS_CONTRACT_VERSION: Final[int] = 1

# Exact canonical field sets for strict (de)serialization at every level.
_CONDITION_FIELDS: Final[frozenset[str]] = frozenset(
    {"id", "statement", "evidence_kind", "evidence_reference"}
)
_NODE_CONDITIONS_FIELDS: Final[frozenset[str]] = frozenset(
    {"node_id", "preconditions", "postconditions"}
)
_CONDITIONS_FIELDS: Final[frozenset[str]] = frozenset(
    {"contract_version", "preconditions", "postconditions", "node_conditions"}
)

# Bounded text disciplines (same domains the A3.02 node specs use).
_MAX_ID_LENGTH: Final[int] = 128
_MAX_STATEMENT_LENGTH: Final[int] = 1024
_MAX_REFERENCE_LENGTH: Final[int] = 512
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class ProcedureConditionsError(ProcedureGraphError):
    """Raised when data violates the A3.06 pre/postconditions contract.

    Subclasses :class:`~agentx.procedures.graph.ProcedureGraphError` so
    callers can handle every Procedure Graph data failure uniformly; this
    module adds no parallel error hierarchy.
    """


# --------------------------------------------------------------------------
# Shared validation helpers (data shape only; never semantics of the content).
# --------------------------------------------------------------------------


def _validate_contract_version(value: object) -> int:
    """Require the one supported contract version; anything else fails closed."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProcedureConditionsError("contract_version must be an integer")
    if value != CURRENT_CONDITIONS_CONTRACT_VERSION:
        raise ProcedureConditionsError(
            f"unsupported conditions contract version {value}; "
            f"supported version is {CURRENT_CONDITIONS_CONTRACT_VERSION}"
        )
    return value


def _validate_text(value: object, *, field_name: str, max_length: int) -> str:
    """Validate explicit, non-empty, trimmed, control-character-free text.

    The content is otherwise carried verbatim as inert data: a statement that
    reads like an instruction, a permission claim, or a verdict is stored as a
    plain string and is never interpreted.
    """
    if not isinstance(value, str):
        raise ProcedureConditionsError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ProcedureConditionsError(f"{field_name} must be non-empty and trimmed")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise ProcedureConditionsError(f"{field_name} must not contain control characters")
    if len(value) > max_length:
        raise ProcedureConditionsError(f"{field_name} must not exceed {max_length} characters")
    return value


def _validate_optional_text(value: object, *, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    return _validate_text(value, field_name=field_name, max_length=max_length)


def _validate_evidence_kind(value: object) -> EvidenceKind:
    """Accept a canonical evidence-kind member (or its canonical string form)."""
    if isinstance(value, EvidenceKind):
        return value
    if not isinstance(value, str):
        raise ProcedureConditionsError("condition.evidence_kind must be an evidence kind string")
    try:
        return EvidenceKind(value)
    except ValueError as exc:
        raise ProcedureConditionsError(f"unknown evidence kind: {value!r}") from exc


def _validate_frozen_tuple[ItemT](
    value: object,
    *,
    field_name: str,
    item_type: type[ItemT],
) -> tuple[ItemT, ...]:
    """Require a tuple whose items are all the given canonical type."""
    if not isinstance(value, tuple):
        raise ProcedureConditionsError(f"{field_name} must be a tuple")
    for item in value:
        if not isinstance(item, item_type):
            raise ProcedureConditionsError(f"each {field_name} item must be a {item_type.__name__}")
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
        raise ProcedureConditionsError(f"{field_name} missing required fields: {sorted(missing)}")
    raise ProcedureConditionsError(f"{field_name} contains unknown fields: {sorted(unknown)}")


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
        raise ProcedureConditionsError(f"{document_name} JSON must be a string")
    try:
        decoded: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProcedureConditionsError(f"{document_name} JSON is malformed") from exc
    if not isinstance(decoded, Mapping):
        raise ProcedureConditionsError(f"{document_name} JSON root must be an object")
    copied: dict[str, object] = {}
    for key, item in decoded.items():
        if not isinstance(key, str):
            raise ProcedureConditionsError(f"{document_name} JSON contains a non-string object key")
        copied[key] = item
    return copied


def _normalize_conditions(
    value: object,
    *,
    field_name: str,
) -> tuple[ProcedureCondition, ...]:
    """Validate an in-memory condition tuple; normalize to identity order."""
    conditions = _validate_frozen_tuple(value, field_name=field_name, item_type=ProcedureCondition)
    return tuple(sorted(conditions, key=lambda condition: condition.id.value))


def _parse_condition_tuple(
    value: object,
    *,
    field_name: str,
) -> tuple[ProcedureCondition, ...]:
    """Parse a serialized JSON array of conditions, failing closed per item."""
    if not isinstance(value, list):
        raise ProcedureConditionsError(f"{field_name} must be a JSON array")
    items: list[ProcedureCondition] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ProcedureConditionsError(f"{field_name}[{index}] must be an object")
        items.append(ProcedureCondition.from_dict(item))
    return tuple(items)


# --------------------------------------------------------------------------
# Condition identity.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConditionId:
    """Typed, procedure-local identifier for one declared condition.

    A condition id gives the requirement a stable name so later components
    (telemetry, repair, a future evaluator) can reference it without
    re-reading its content. It is a local reference key inside one conditions
    document — unique there by construction, never domain-wide authority, and
    never resolved, looked up, or dereferenced by this contract. Any
    non-empty trimmed string is a valid id; hostile-looking ids remain inert
    data.
    """

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _validate_text(self.value, field_name="condition id", max_length=_MAX_ID_LENGTH),
        )

    def to_str(self) -> str:
        """Return the canonical string form of this condition id."""
        return self.value

    @classmethod
    def parse(cls, raw: object) -> ConditionId:
        """Parse a condition id from a string, rejecting non-string input."""
        return cls(_validate_text(raw, field_name="condition id", max_length=_MAX_ID_LENGTH))


# --------------------------------------------------------------------------
# The one reusable condition representation.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureCondition:
    """One explicit declared requirement — used by pre AND postconditions.

    ``statement`` is inert descriptive metadata: what the requirement means,
    for humans and for later components that own its evaluation. It is never
    itself executable logic and is never evaluated here.

    ``evidence_kind`` composes the canonical
    :class:`agentx.core.provenance.EvidenceKind` vocabulary, recording the
    kind of evidence/state the requirement is stated against (an observation,
    an artifact, or a knowledge record). ``evidence_reference`` optionally
    names the specific evidence/state when it can be named in advance; it is
    opaque reference data that is never resolved, opened, fetched, or
    dereferenced. Postconditions may legitimately leave it ``None``: the
    evidence may not exist until after execution.

    By construction this contract has NO satisfied/holds/outcome/verdict/
    passed/verified/status field: a declared condition can neither claim that
    it currently holds (preconditions are not satisfied) nor that execution
    succeeded (postconditions are not verified). Establishing whether a
    requirement holds belongs to future, separately owned evaluation.
    """

    id: ConditionId
    statement: str
    evidence_kind: EvidenceKind
    evidence_reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, ConditionId):
            raise ProcedureConditionsError("condition.id must be a ConditionId")
        object.__setattr__(
            self,
            "statement",
            _validate_text(
                self.statement,
                field_name="condition.statement",
                max_length=_MAX_STATEMENT_LENGTH,
            ),
        )
        object.__setattr__(self, "evidence_kind", _validate_evidence_kind(self.evidence_kind))
        if self.evidence_reference is not None:
            object.__setattr__(
                self,
                "evidence_reference",
                _validate_optional_text(
                    self.evidence_reference,
                    field_name="condition.evidence_reference",
                    max_length=_MAX_REFERENCE_LENGTH,
                ),
            )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible condition representation."""
        return {
            "id": self.id.to_str(),
            "statement": self.statement,
            "evidence_kind": self.evidence_kind.value,
            "evidence_reference": self.evidence_reference,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedureCondition:
        """Validate and reconstruct a condition from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise ProcedureConditionsError("procedure condition must be an object")
        _require_exact_fields(raw, expected=_CONDITION_FIELDS, field_name="procedure condition")

        id_raw = raw["id"]
        if not isinstance(id_raw, str):
            raise ProcedureConditionsError("condition.id must be a string")
        return cls(
            id=ConditionId.parse(id_raw),
            statement=_validate_text(
                raw["statement"],
                field_name="condition.statement",
                max_length=_MAX_STATEMENT_LENGTH,
            ),
            evidence_kind=_validate_evidence_kind(raw["evidence_kind"]),
            evidence_reference=_validate_optional_text(
                raw["evidence_reference"],
                field_name="condition.evidence_reference",
                max_length=_MAX_REFERENCE_LENGTH,
            ),
        )

    def to_json(self) -> str:
        """Serialize to deterministic, canonical, UTF-8-safe JSON text."""
        return _canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, raw: str) -> ProcedureCondition:
        """Deserialize canonical JSON text into a validated condition."""
        return cls.from_dict(_parse_json_object(raw, document_name="procedure condition"))


# --------------------------------------------------------------------------
# Per-node scoping.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeConditions:
    """Preconditions and postconditions declared for ONE graph node.

    ``node_id`` is the canonical
    :class:`~agentx.procedures.graph.ProcedureNodeId` of an A3.01 node — a
    reference key, never a pointer or a handle: naming a node resolves
    nothing and runs nothing. The node kind is deliberately irrelevant here:
    every A3.01-A3.05 node family may carry declared conditions, and this
    contract neither reads nor constrains the node's payload.
    """

    node_id: ProcedureNodeId
    preconditions: tuple[ProcedureCondition, ...] = ()
    postconditions: tuple[ProcedureCondition, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, ProcedureNodeId):
            raise ProcedureConditionsError("node conditions node_id must be a ProcedureNodeId")
        object.__setattr__(
            self,
            "preconditions",
            _normalize_conditions(self.preconditions, field_name="preconditions"),
        )
        object.__setattr__(
            self,
            "postconditions",
            _normalize_conditions(self.postconditions, field_name="postconditions"),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible node-conditions representation."""
        return {
            "node_id": self.node_id.to_str(),
            "preconditions": [condition.to_dict() for condition in self.preconditions],
            "postconditions": [condition.to_dict() for condition in self.postconditions],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> NodeConditions:
        """Validate and reconstruct node conditions from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise ProcedureConditionsError("node conditions must be an object")
        _require_exact_fields(raw, expected=_NODE_CONDITIONS_FIELDS, field_name="node conditions")

        node_id_raw = raw["node_id"]
        if not isinstance(node_id_raw, str):
            raise ProcedureConditionsError("node conditions node_id must be a string")
        return cls(
            node_id=ProcedureNodeId.parse(node_id_raw),
            preconditions=_parse_condition_tuple(
                raw["preconditions"], field_name="node conditions preconditions"
            ),
            postconditions=_parse_condition_tuple(
                raw["postconditions"], field_name="node conditions postconditions"
            ),
        )

    def to_json(self) -> str:
        """Serialize to deterministic, canonical, UTF-8-safe JSON text."""
        return _canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, raw: str) -> NodeConditions:
        """Deserialize canonical JSON text into validated node conditions."""
        return cls.from_dict(_parse_json_object(raw, document_name="node conditions"))


# --------------------------------------------------------------------------
# The graph-level wrapper document.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureConditions:
    """Explicit declared preconditions/postconditions for one procedure.

    This is the smallest graph-level wrapper that can carry conditions without
    redesigning the canonical A3.01 graph: it is a companion DATA document —
    no nodes, no edges, no new node kind, and no second graph schema. It
    holds:

        - ``preconditions`` — procedure-level requirements that must be
          established before future execution of the procedure may proceed.
        - ``postconditions`` — procedure-level requirements that must be
          established after future execution. Neither declares, implies, or
          records any outcome: postconditions are not verification and never
          mark a task succeeded.
        - ``node_conditions`` — per-node scoping, keyed by canonical
          :class:`~agentx.procedures.graph.ProcedureNodeId` references.

    All lists are optional; a document with nothing declared is valid and
    expresses exactly that. Construction normalizes ordering so that two
    documents differing only in author-supplied order are identical data, and
    rejects duplicate condition ids across the whole document (a condition id
    names exactly one requirement) and duplicate node scoping.
    """

    contract_version: int = CURRENT_CONDITIONS_CONTRACT_VERSION
    preconditions: tuple[ProcedureCondition, ...] = ()
    postconditions: tuple[ProcedureCondition, ...] = ()
    node_conditions: tuple[NodeConditions, ...] = ()

    def __post_init__(self) -> None:
        _validate_contract_version(self.contract_version)

        normalized_pre = _normalize_conditions(self.preconditions, field_name="preconditions")
        normalized_post = _normalize_conditions(self.postconditions, field_name="postconditions")

        scoped = _validate_frozen_tuple(
            self.node_conditions, field_name="node_conditions", item_type=NodeConditions
        )
        normalized_scoped = tuple(sorted(scoped, key=lambda item: item.node_id.to_str()))

        seen_nodes: set[str] = set()
        for item in normalized_scoped:
            node_id = item.node_id.to_str()
            if node_id in seen_nodes:
                raise ProcedureConditionsError(f"duplicate node conditions for node: {node_id!r}")
            seen_nodes.add(node_id)

        seen_conditions: set[str] = set()
        all_conditions: tuple[ProcedureCondition, ...] = (
            *normalized_pre,
            *normalized_post,
            *(item for scoped_item in normalized_scoped for item in scoped_item.preconditions),
            *(item for scoped_item in normalized_scoped for item in scoped_item.postconditions),
        )
        for condition in all_conditions:
            condition_id = condition.id.to_str()
            if condition_id in seen_conditions:
                raise ProcedureConditionsError(
                    f"duplicate condition id across the document: {condition_id!r}"
                )
            seen_conditions.add(condition_id)

        object.__setattr__(self, "preconditions", normalized_pre)
        object.__setattr__(self, "postconditions", normalized_post)
        object.__setattr__(self, "node_conditions", normalized_scoped)

    def to_dict(self) -> dict[str, object]:
        """Return the canonical, deterministically ordered JSON-compatible form."""
        return {
            "contract_version": self.contract_version,
            "preconditions": [condition.to_dict() for condition in self.preconditions],
            "postconditions": [condition.to_dict() for condition in self.postconditions],
            "node_conditions": [scoped.to_dict() for scoped in self.node_conditions],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedureConditions:
        """Validate and reconstruct the document from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise ProcedureConditionsError("procedure conditions must be an object")
        _require_exact_fields(raw, expected=_CONDITIONS_FIELDS, field_name="procedure conditions")

        node_conditions_raw = raw["node_conditions"]
        if not isinstance(node_conditions_raw, list):
            raise ProcedureConditionsError("node_conditions must be a JSON array")
        scoped_items: list[NodeConditions] = []
        for index, item in enumerate(node_conditions_raw):
            if not isinstance(item, Mapping):
                raise ProcedureConditionsError(f"node_conditions[{index}] must be an object")
            scoped_items.append(NodeConditions.from_dict(item))

        return cls(
            contract_version=_validate_contract_version(raw["contract_version"]),
            preconditions=_parse_condition_tuple(raw["preconditions"], field_name="preconditions"),
            postconditions=_parse_condition_tuple(
                raw["postconditions"], field_name="postconditions"
            ),
            node_conditions=tuple(scoped_items),
        )

    def to_json(self) -> str:
        """Serialize to deterministic, canonical, UTF-8-safe JSON text."""
        return _canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, raw: str) -> ProcedureConditions:
        """Deserialize canonical JSON text without executable reconstruction."""
        return cls.from_dict(_parse_json_object(raw, document_name="procedure conditions"))

    def bind_to_graph(self, graph: ProcedureGraph) -> ProcedureConditions:
        """Structurally cross-check node references against an A3.01 graph.

        Fails closed if any scoped node id does not exist in ``graph``; on
        success returns ``self`` unchanged. This is a membership check of
        canonical node-id REFERENCES only — it reads no node semantics,
        interprets nothing, and imposes no node-kind restriction: conditions
        are attachable to every A3.01-A3.05 node family alike. The graph
        itself is never modified; the conditions document remains separate,
        canonical data.
        """
        if not isinstance(graph, ProcedureGraph):
            raise ProcedureConditionsError("bind_to_graph expects a ProcedureGraph")
        known = {node.id.to_str() for node in graph.nodes}
        for scoped in self.node_conditions:
            node_id = scoped.node_id.to_str()
            if node_id not in known:
                raise ProcedureConditionsError(
                    f"conditions reference a node that does not exist in the graph: {node_id!r}"
                )
        return self
