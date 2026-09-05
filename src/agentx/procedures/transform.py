"""Typed TRANSFORM node-family contract (A3.03).

This module owns the DATA contract for the TRANSFORM node family of the
canonical A3.01 Procedure Graph IR (``agentx.procedures.graph``). A TRANSFORM
node records *deterministic transformation intent*: the name of a
deterministic operation plus inert data arguments.

The intent is inert data, never code:

    - ``operation`` is a name, not a callable. Callables, functions,
      byte code, and other live executable objects are rejected at the
      contract boundary; nothing in this module executes Python code, calls
      eval/exec, performs dynamic imports, runs shell commands, or loads
      plugins. Resolving an operation name to a deterministic
      implementation is owned by future runtime tasks, never here.
    - ``arguments`` is an immutable mapping of inert, JSON-compatible data
      only (null, booleans, integers, finite numbers, strings, arrays, and
      objects with string keys). Non-finite numbers, non-string keys, and
      unbounded nesting fail closed.
    - Constructing, validating, serializing, or deserializing a transform
      contract performs no side effects and reaches no authority or runtime
      subsystem: it cannot create a Permission/AuthorityContext, bypass the
      ActionGate, lower risk, enlarge a budget, clear an EmergencyStop,
      execute a Capability or Executor, invoke a Reasoner/model, transition a
      Task, fabricate verification, activate a Procedure, or mutate Hive.
    - Hostile strings in the operation name or arguments remain inert data.

The contract embeds in the A3.01 graph through ``ProcedureNode.params``:
:meth:`TransformContract.to_node` builds a ``ProcedureNode`` of kind TRANSFORM
whose opaque ``params`` are the canonical contract dict, and
:meth:`TransformContract.bind` / :meth:`TransformContract.from_dict` recover
a strictly typed, validated view of a node's opaque ``params``, failing
closed on anything that is not exactly this contract. The graph IR itself is
not redesigned: ``params`` stays opaque to the graph, and this module
depends only on the standard library plus the A3.01 graph module.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from agentx.procedures.graph import ProcedureNode, ProcedureNodeId, ProcedureNodeKind

__all__ = [
    "MAX_ARGUMENT_NESTING",
    "TransformContract",
    "TransformContractError",
]

# Maximum depth of nested inert argument data. Bounds fail-closed validation
# so pathological input cannot recurse into a stack overflow.
MAX_ARGUMENT_NESTING: Final[int] = 16

_NODE_KIND: Final[ProcedureNodeKind] = ProcedureNodeKind.TRANSFORM


class TransformContractError(ValueError):
    """Raised when data violates the TRANSFORM node-family contract."""


def _validate_operation(value: object, *, field_name: str) -> str:
    """Require a non-empty, trimmed operation name."""
    if not isinstance(value, str):
        raise TransformContractError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise TransformContractError(f"{field_name} must be non-empty and trimmed")
    return value


def _normalize_argument_value(value: object, *, path: str, depth: int) -> object:
    """Validate one argument value against the inert JSON data domain.

    Only inert data is admitted: null, booleans, integers, finite numbers,
    strings, arrays, and objects with string keys. Anything executable —
    callables, functions, classes, or other live objects — is rejected, as
    are non-finite numbers, non-string keys, and nesting beyond
    ``MAX_ARGUMENT_NESTING``. Containers are returned as canonical plain
    lists / immutable mappings so round-trips are exact.
    """
    if depth > MAX_ARGUMENT_NESTING:
        raise TransformContractError(
            f"{path} exceeds maximum argument nesting depth {MAX_ARGUMENT_NESTING}"
        )
    if callable(value):
        # Reject executables before any container inspection: a callable
        # (function, bound method, class, callable object) is never data.
        raise TransformContractError(
            f"{path} must be inert data; executables such as callables are never permitted"
        )
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TransformContractError(f"{path} must be a finite number")
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        items: list[object] = []
        for index, item in enumerate(value):
            items.append(_normalize_argument_value(item, path=f"{path}[{index}]", depth=depth + 1))
        return items
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TransformContractError(f"{path} keys must be strings")
            normalized[key] = _normalize_argument_value(item, path=f"{path}.{key}", depth=depth + 1)
        return MappingProxyType(normalized)
    raise TransformContractError(
        f"{path} must be inert JSON-compatible data, got {type(value).__name__}"
    )


def _render(value: object) -> object:
    """Render normalized contract data into plain JSON-compatible containers."""
    if isinstance(value, Mapping):
        return {key: _render(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class TransformContract:
    """Typed, inert contract for the TRANSFORM node family payload.

    ``operation`` names the intended deterministic transformation; it is an
    inert identifier, never a callable. ``arguments`` is an immutable
    mapping of inert, JSON-compatible data. This contract records intent
    only: no transformation is performed, imported, or loaded here.
    """

    operation: str
    arguments: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "operation", _validate_operation(self.operation, field_name="operation")
        )
        if not isinstance(self.arguments, Mapping):
            raise TransformContractError("arguments must be a mapping")
        normalized: dict[str, object] = {}
        for key, item in self.arguments.items():
            if not isinstance(key, str):
                raise TransformContractError("argument keys must be strings")
            normalized[key] = _normalize_argument_value(item, path=f"arguments[{key!r}]", depth=1)
        object.__setattr__(self, "arguments", MappingProxyType(normalized))

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible contract representation."""
        return {"operation": self.operation, "arguments": _render(self.arguments)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> TransformContract:
        """Validate and reconstruct a contract from a JSON-compatible mapping."""
        if not isinstance(raw, Mapping):
            raise TransformContractError("transform contract must be an object")
        actual = set(raw)
        expected = {"operation", "arguments"}
        if actual != expected:
            missing = expected - actual
            unknown = actual - expected
            if missing:
                raise TransformContractError(
                    f"transform contract missing required fields: {sorted(missing)}"
                )
            raise TransformContractError(
                f"transform contract contains unknown fields: {sorted(unknown)}"
            )
        arguments_raw = raw["arguments"]
        if not isinstance(arguments_raw, Mapping):
            raise TransformContractError("arguments must be an object")
        arguments: dict[str, object] = {}
        for key, item in arguments_raw.items():
            if not isinstance(key, str):
                raise TransformContractError("argument keys must be strings")
            arguments[key] = _normalize_argument_value(item, path=f"arguments[{key!r}]", depth=1)
        return cls(
            operation=_validate_operation(raw["operation"], field_name="operation"),
            arguments=arguments,
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
    def from_json(cls, raw: str) -> TransformContract:
        """Deserialize JSON text into a validated contract (no execution)."""
        if not isinstance(raw, str):
            raise TransformContractError("transform contract JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TransformContractError("transform contract JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise TransformContractError("transform contract JSON root must be an object")
        return cls.from_dict(decoded)

    def to_node(self, node_id: str, *, label: str | None = None) -> ProcedureNode:
        """Build an A3.01 TRANSFORM node whose opaque params are this contract."""
        return ProcedureNode(
            ProcedureNodeId(node_id), _NODE_KIND, label=label, params=self.to_dict()
        )

    @classmethod
    def bind(cls, node: ProcedureNode) -> TransformContract:
        """Strictly view a node's opaque params as this contract.

        Fails closed unless ``node`` is a TRANSFORM node whose ``params`` are
        exactly a valid transform contract; the graph stays opaque otherwise.
        """
        if not isinstance(node, ProcedureNode):
            raise TransformContractError("bind expects a ProcedureNode")
        if node.kind is not _NODE_KIND:
            raise TransformContractError(
                f"expected a transform node, got node kind {node.kind.value!r}"
            )
        return cls.from_dict(node.params)
