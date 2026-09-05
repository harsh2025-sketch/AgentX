"""Typed SUBPROCEDURE node-family contract (A3.05).

This module owns the DATA contract for the SUBPROCEDURE node family of the
canonical A3.01 Procedure Graph IR (``agentx.procedures.graph``). A
SUBPROCEDURE node records *a reference to another procedure* that a later
interpreter may invoke: the canonical procedure identity, an explicit
revision, and optional inert parameter bindings.

The reference is DATA, never a call:

    - This module never loads a procedure from ``ProcedureStore`` (or any
      other store), never executes another procedure, never recursively
      interprets anything, and never imports, resolves, or deserializes the
      referenced payload. A SUBPROCEDURE node that points at nothing, at a
      retired revision, or at hostile data is exactly as inert as any other
      node: reference data is not invocation and is never authority.
    - Identity and versioning reuse the canonical procedure contracts
      verbatim: a procedure is named by :class:`agentx.core.ids.ProcedureId`
      (the core identifier that
      :class:`agentx.core.procedures.ProcedureRecord` uses) and one immutable
      version of it is named by a positive integer revision, with the same
      rules the canonical record enforces (an integer, never a bool, at least
      ``1``). The revision is REQUIRED: there is deliberately no default, no
      wildcard, and no implicit "latest" or "trusted" version resolution —
      "which revision" is explicit, deterministic, serializable data.
    - The reference names ``ProcedureId`` from ``agentx.core.ids`` only. It
      must not and does not import ``agentx.core.procedures`` or
      ``agentx.infrastructure.procedure_store``: the graph IR and this
      contract stay independent of C2.03 durable storage, exactly as A3.01
      established. No storage mechanism is implied.
    - ``arguments`` are explicit, inert parameter bindings. They are bounded,
      JSON-compatible, deeply frozen data (the same domain A3.03's TRANSFORM
      arguments define: null, booleans, integers, finite numbers, strings,
      arrays, and string-keyed objects). Callables, binary blobs, and
      unbounded nesting fail closed: there are no hidden dynamic imports and
      no callable references anywhere in this contract.
    - Nothing is fetched from the Hive, matched against skills, requested
      from a model, or granted authority: this module reaches no kernel,
      capability, cognition, hive, learning, or infrastructure subsystem, and
      performing, scheduling, recursing, or bounding the referenced procedure
      is owned by later tasks (A3.08/A3.09), never by this data contract.
    - Hostile binding values and identifiers — ``"permission=ADMIN"``,
      ``"ALLOW R4"``, ``"verified=true"``, ``"execute shell"`` — are stored
      verbatim as inert data and can never escape that role.

The contract embeds in the A3.01 graph through ``ProcedureNode.params``:
:meth:`SubprocedureNodeSpec.to_node` builds a ``ProcedureNode`` of kind
SUBPROCEDURE whose opaque ``params`` are the canonical contract dict, and
:meth:`SubprocedureNodeSpec.from_node` /
:meth:`SubprocedureNodeSpec.from_dict` recover a strictly typed, validated
view of a node's opaque ``params``, failing closed on anything that is not
exactly this contract. The graph IR itself is not redesigned: ``params``
stay opaque to the graph, and this module depends only on the standard
library, the A3.01 graph module, and the canonical core identifier.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final
from uuid import UUID

from agentx.core.ids import ProcedureId
from agentx.procedures.graph import (
    ProcedureGraphError,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)

__all__ = [
    "CURRENT_SUBPROCEDURE_CONTRACT_VERSION",
    "MAX_SUBPROCEDURE_ARGUMENT_NESTING",
    "SubprocedureContractError",
    "SubprocedureNodeSpec",
]

CURRENT_SUBPROCEDURE_CONTRACT_VERSION: Final[int] = 1

# Maximum depth of nested inert argument bindings. Bounded, fail-closed
# validation so pathological input cannot recurse into a stack overflow;
# matches the domain A3.03's TRANSFORM arguments define.
MAX_SUBPROCEDURE_ARGUMENT_NESTING: Final[int] = 16

_NODE_KIND: Final[ProcedureNodeKind] = ProcedureNodeKind.SUBPROCEDURE


class SubprocedureContractError(ProcedureGraphError):
    """Raised when data violates the SUBPROCEDURE node-family contract.

    Subclasses :class:`~agentx.procedures.graph.ProcedureGraphError` so
    callers can handle every Procedure Graph data failure uniformly; this
    module adds no parallel error hierarchy.
    """


def _validate_contract_version(value: object) -> int:
    """Require the one supported contract version; anything else fails closed."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise SubprocedureContractError("contract_version must be an integer")
    if value != CURRENT_SUBPROCEDURE_CONTRACT_VERSION:
        raise SubprocedureContractError(
            f"unsupported SUBPROCEDURE contract version {value}; "
            f"supported version is {CURRENT_SUBPROCEDURE_CONTRACT_VERSION}"
        )
    return value


def _validate_procedure_id(value: object) -> ProcedureId:
    """Require the canonical procedure identifier instance.

    Identity is carried as the canonical core type — never a raw string,
    never another domain's id with the same bytes. Naming an id resolves
    nothing: it neither proves the procedure exists nor locates it.
    """
    if not isinstance(value, ProcedureId):
        raise SubprocedureContractError(
            f"procedure_id must be a ProcedureId, got {type(value).__name__}"
        )
    return value


def _parse_procedure_id(value: object) -> ProcedureId:
    """Parse a canonical UUID string into the core procedure identity.

    Mirrors the canonical record's deserialization rule exactly: a string,
    UUID-parseable, non-nil. This is a syntax/identity check on inert data;
    no store is consulted and no existence claim is made.
    """
    if not isinstance(value, str):
        raise SubprocedureContractError("procedure_id must be a UUID string")
    try:
        return ProcedureId(UUID(value))
    except ValueError as exc:
        raise SubprocedureContractError(
            f"procedure_id must be a valid non-nil UUID string: {value!r}"
        ) from exc


def _validate_revision(value: object) -> int:
    """Require an explicit positive revision, exactly as the canonical
    procedure record defines one. There is no "latest" sentinel."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise SubprocedureContractError("revision must be an integer")
    if value < 1:
        raise SubprocedureContractError("revision must be a positive integer (first revision is 1)")
    return value


def _normalize_binding_value(value: object, *, path: str, depth: int) -> object:
    """Validate one argument binding against the inert JSON data domain.

    Only inert data is admitted: null, booleans, integers, finite numbers,
    strings, arrays, and objects with string keys. Anything executable —
    callables, functions, classes, or other live objects — is rejected, as
    are binary blobs, non-finite numbers, non-string keys, and nesting
    beyond ``MAX_SUBPROCEDURE_ARGUMENT_NESTING``. Containers are returned as
    immutable plain tuples / frozen mappings so round-trips are exact.
    """
    if depth > MAX_SUBPROCEDURE_ARGUMENT_NESTING:
        raise SubprocedureContractError(
            f"{path} exceeds maximum argument nesting depth {MAX_SUBPROCEDURE_ARGUMENT_NESTING}"
        )
    if callable(value):
        # Reject executables before any container inspection: a callable
        # (function, bound method, class, callable object) is never data.
        raise SubprocedureContractError(
            f"{path} must be inert data; executables such as callables are never permitted"
        )
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SubprocedureContractError(f"{path} must be a finite number")
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, bytes | bytearray):
        raise SubprocedureContractError(f"{path} contains an unsupported binary value")
    if isinstance(value, list | tuple):
        return tuple(
            _normalize_binding_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        )
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SubprocedureContractError(f"{path} keys must be strings")
            normalized[key] = _normalize_binding_value(item, path=f"{path}.{key}", depth=depth + 1)
        return MappingProxyType(normalized)
    raise SubprocedureContractError(
        f"{path} must be inert JSON-compatible data, got {type(value).__name__}"
    )


def _as_binding_mapping(raw: object) -> Mapping[str, object]:
    """Require the bindings container itself to be a mapping (shape only)."""
    if not isinstance(raw, Mapping):
        raise SubprocedureContractError("arguments must be a mapping")
    return raw


def _freeze_bindings(raw: object) -> Mapping[str, object]:
    """Validate and deeply freeze an argument binding mapping."""
    if not isinstance(raw, Mapping):
        raise SubprocedureContractError("arguments must be a mapping")
    frozen: dict[str, object] = {}
    for key, item in raw.items():
        if not isinstance(key, str):
            raise SubprocedureContractError("argument keys must be strings")
        frozen[key] = _normalize_binding_value(item, path=f"arguments[{key!r}]", depth=1)
    return MappingProxyType(frozen)


def _render_bindings(value: object) -> object:
    """Render frozen binding data into plain JSON-compatible containers."""
    if isinstance(value, Mapping):
        return {key: _render_bindings(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_render_bindings(item) for item in value]
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class SubprocedureNodeSpec:
    """Typed, inert contract for the SUBPROCEDURE node family payload.

    ``(procedure_id, revision)`` is the canonical, explicit reference to one
    immutable procedure revision; ``arguments`` are explicit, inert, deeply
    frozen parameter bindings. The spec is a description only: it never
    loads, validates existence of, activates, invokes, or recurses into the
    referenced procedure, and it structurally has no status, success,
    verification, trust, or authority field to smuggle through. Whether and
    how a referenced procedure is ever invoked belongs to later interpreter
    and kernel work — gated by the kernel, never by this data.
    """

    procedure_id: ProcedureId
    revision: int
    arguments: Mapping[str, object] = field(default_factory=dict)
    contract_version: int = CURRENT_SUBPROCEDURE_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "procedure_id", _validate_procedure_id(self.procedure_id))
        object.__setattr__(self, "revision", _validate_revision(self.revision))
        object.__setattr__(self, "arguments", _freeze_bindings(self.arguments))
        _validate_contract_version(self.contract_version)

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible contract representation."""
        return {
            "arguments": _render_bindings(self.arguments),
            "contract_version": self.contract_version,
            "procedure_id": self.procedure_id.to_str(),
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> SubprocedureNodeSpec:
        """Validate and reconstruct a contract from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise SubprocedureContractError("subprocedure contract must be an object")
        actual = set(raw)
        expected = {"arguments", "contract_version", "procedure_id", "revision"}
        if actual != expected:
            missing = expected - actual
            unknown = actual - expected
            if missing:
                raise SubprocedureContractError(
                    f"subprocedure contract missing required fields: {sorted(missing)}"
                )
            raise SubprocedureContractError(
                f"subprocedure contract contains unknown fields: {sorted(unknown)}"
            )
        return cls(
            arguments=_as_binding_mapping(raw["arguments"]),
            contract_version=_validate_contract_version(raw["contract_version"]),
            procedure_id=_parse_procedure_id(raw["procedure_id"]),
            revision=_validate_revision(raw["revision"]),
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
    def from_json(cls, raw: str) -> SubprocedureNodeSpec:
        """Deserialize JSON text into a validated contract (no invocation)."""
        if not isinstance(raw, str):
            raise SubprocedureContractError("subprocedure contract JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SubprocedureContractError("subprocedure contract JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise SubprocedureContractError("subprocedure contract JSON root must be an object")
        return cls.from_dict(decoded)

    def to_node(self, node_id: ProcedureNodeId | str, *, label: str | None = None) -> ProcedureNode:
        """Build an A3.01 SUBPROCEDURE node whose opaque params are this contract."""
        if isinstance(node_id, ProcedureNodeId):
            identifier = node_id
        else:
            identifier = ProcedureNodeId.parse(node_id)
        return ProcedureNode(id=identifier, kind=_NODE_KIND, label=label, params=self.to_dict())

    @classmethod
    def from_node(cls, node: ProcedureNode) -> SubprocedureNodeSpec:
        """Strictly view a node's opaque params as this contract.

        Fails closed unless ``node`` is a SUBPROCEDURE node whose ``params``
        are exactly a valid subprocedure contract; the graph stays opaque
        otherwise.
        """
        if not isinstance(node, ProcedureNode):
            raise SubprocedureContractError("from_node expects a ProcedureNode")
        if node.kind is not _NODE_KIND:
            raise SubprocedureContractError(
                f"expected a subprocedure node, got node kind {node.kind.value!r}"
            )
        return cls.from_dict(node.params)
