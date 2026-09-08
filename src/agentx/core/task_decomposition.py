"""Canonical hierarchical task decomposition contract (A6.01).

A6.01 defines the canonical, inert **PLAN/DECOMPOSITION DATA** representation
for decomposing one high-level AgentX Task into smaller task units, and the
deterministic validation boundary through which such data — including model or
reasoner output — is accepted into canonical typed structures.

Shape
-----

::

    TaskDecomposition
    ├── decomposition_id   DecompositionId (record identity)
    ├── version            int >= 1 (revision counter)
    ├── root_task_id       TaskId (the high-level task being decomposed)
    ├── created_at         timezone-aware datetime, normalized to UTC
    ├── metadata           JSON-compatible, non-authoritative context
    └── nodes              tuple[DecompositionNode, ...]  (canonical pre-order)

Every node in the decomposition — including the root — is a
:class:`DecompositionNode` carrying:

    task_id            canonical TaskId (unique within the decomposition)
    objective          explicit, non-empty, trimmed statement of the work
    parent_task_id     TaskId | None (None only for the root)
    success_criteria   declarative completion criteria (inert text; empty OK)
    order_index        canonical 0-based position among siblings
    depends_on         TaskIds that must complete first (ordering constraint)
    metadata           JSON-compatible, non-authoritative context

The hierarchy is a **rooted tree**: every subtask has exactly one parent,
mirroring the single ``parent_task_id`` link of the A1.05 Task schema. A
subtask shared by multiple parents (a DAG-shaped hierarchy) is not
representable in v1 and therefore not canonical; ``depends_on`` adds
ordering constraints *across* the tree without ever changing parentage.
The ``nodes`` tuple is stored in canonical pre-order (root first; siblings
ascending by ``order_index``), which makes serialization a pure function of
the data.

Validation
----------

Construction and decoding enforce the canonical invariants:

    - no duplicate node task ids;
    - a valid root: ``root_task_id`` is present, exactly one node is rootless,
      and that node is the root;
    - no dangling parent reference (every parent is a present node);
    - no parent-link cycle (every node reaches the root);
    - bounded node count (``MAX_DECOMPOSITION_NODES``);
    - bounded depth (``MAX_DECOMPOSITION_DEPTH``, root = depth 1);
    - explicit sibling ordering (indices exactly ``0..k-1`` per sibling
      group) and canonical pre-order node sequence;
    - dependency integrity (targets present, no self-dependency, no
      duplicates, no dependency cycle);
    - valid task identity (canonical :class:`~agentx.core.ids.TaskId` /
      :class:`~agentx.core.ids.DecompositionId`, never raw strings/UUIDs);
    - deterministic serialization (sorted keys, canonical node order,
      canonical UTC timestamps, no NaN).

Model / reasoner acceptance
---------------------------

:func:`accept_model_proposal` is the inert acceptance boundary for external
(model or reasoner) output. It takes *data* — a JSON-compatible mapping, e.g.
the result of ``json.loads`` on model text — and validates it into a
canonical :class:`TaskDecomposition`. Model text is never executable
authority: it is stored verbatim as inert strings, it can set no identity,
grant no permission, carry no secret or authority marker, and its proposal
names no field beyond structure (``schema_version`` / ``root_task_id`` /
``nodes``). Record identity (``decomposition_id``, ``version``,
``created_at``, record ``metadata``) is owned by the accepting caller, never
by the model. Expected data failures cross the boundary as
``Result.failure(AgentXError)`` with a structured ``task_decomposition.*``
code; they never raise.

Not authority
-------------

This module is **plan/decomposition data only**. It:

    - does not execute or schedule anything (no executor, planner, or
      scheduler — A6.02 owns dependency planning, A6.04 full plan
      validation);
    - grants no permission and imports no kernel surface;
    - declares no success: there is no status field, no success-claim field,
      and no transition or completion API. The presence of children never
      implies the parent succeeded, and success criteria are declarations of
      what *would count* as success, not claims that it happened;
    - emits no events, persists nothing, and performs no I/O.

Owner: A6.01. Belongs to ``agentx.core``; imports only the standard library
and sibling ``agentx.core`` contracts, so ``agentx.core`` remains a
dependency leaf with respect to the other canonical subsystems.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final

from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.ids import DecompositionId, TaskId
from agentx.core.result import Result
from agentx.core.tasks import JsonValue

__all__ = [
    "DECOMPOSITION_SCHEMA_VERSION",
    "MAX_DECOMPOSITION_DEPTH",
    "MAX_DECOMPOSITION_NODES",
    "MAX_SUCCESS_CRITERIA",
    "DecompositionNode",
    "DecompositionNodeNotFoundError",
    "TaskDecomposition",
    "TaskDecompositionDeserializationError",
    "TaskDecompositionError",
    "TaskDecompositionValidationError",
    "UnsupportedDecompositionSchemaVersionError",
    "accept_model_proposal",
]

#: Schema version of the serialized decomposition representation. Bumped only
#: when the serialized form changes incompatibly.
DECOMPOSITION_SCHEMA_VERSION: Final[int] = 1

#: Maximum number of nodes (including the root) in one decomposition.
MAX_DECOMPOSITION_NODES: Final[int] = 256

#: Maximum root-to-leaf depth in one decomposition (the root is depth 1).
MAX_DECOMPOSITION_DEPTH: Final[int] = 8

#: Maximum number of declarative success criteria per node.
MAX_SUCCESS_CRITERIA: Final[int] = 64

# -- Structured error codes --------------------------------------------------

_CODE_INVALID_INPUT: Final[str] = "task_decomposition.invalid_input"
_CODE_INVALID_SCHEMA_VERSION: Final[str] = "task_decomposition.invalid_schema_version"
_CODE_MISSING_FIELDS: Final[str] = "task_decomposition.missing_fields"
_CODE_UNKNOWN_FIELDS: Final[str] = "task_decomposition.unknown_fields"
_CODE_ROOT_MISMATCH: Final[str] = "task_decomposition.root_mismatch"
_CODE_INVALID_FIELD: Final[str] = "task_decomposition.invalid_field"
_CODE_INVALID_STRUCTURE: Final[str] = "task_decomposition.invalid_structure"

_CANONICAL_FIELD_SET: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "decomposition_id",
        "version",
        "root_task_id",
        "created_at",
        "nodes",
        "metadata",
    }
)

_NODE_FIELD_SET: Final[frozenset[str]] = frozenset(
    {
        "task_id",
        "objective",
        "parent_task_id",
        "success_criteria",
        "order_index",
        "depends_on",
        "metadata",
    }
)

#: Fields of the untrusted model-proposal shape. Record identity and record
#: metadata are deliberately NOT model-controlled.
_MODEL_PROPOSAL_FIELD_SET: Final[frozenset[str]] = frozenset(
    {"schema_version", "root_task_id", "nodes"}
)

_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")

# Metadata is context, never a credential store. Keys containing these markers
# are rejected so that secrets cannot be smuggled through a decomposition.
_SECRET_KEY_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "credential",
        "private_key",
        "api_key",
        "access_token",
        "refresh_token",
        "auth_token",
        "session_token",
    }
)

# Metadata is context, never authority. Keys containing these markers are
# rejected so that a decomposition cannot carry a policy/permission bypass.
_AUTHORITY_KEY_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "permission",
        "authority",
        "privilege",
        "bypass",
        "escalat",
    }
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TaskDecompositionError(ValueError):
    """Base error for canonical decomposition contract violations.

    ``code`` carries the structured ``task_decomposition.*`` error code when
    the failure is one that can cross a subsystem boundary inside a
    :class:`~agentx.core.result.Result`; it is ``None`` for plain
    programming-contract violations.
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self._code = code

    @property
    def code(self) -> str | None:
        """The structured error code, when one is associated with this error."""
        return self._code


class TaskDecompositionValidationError(TaskDecompositionError):
    """Raised when decomposition data violates a canonical structural rule."""


class TaskDecompositionDeserializationError(TaskDecompositionError):
    """Raised when encoded decomposition data cannot be decoded."""


class UnsupportedDecompositionSchemaVersionError(TaskDecompositionDeserializationError):
    """Raised when encoded data uses a decomposition schema version that is unreadable."""


class DecompositionNodeNotFoundError(TaskDecompositionError):
    """Raised when a decomposition is queried for an absent node."""

    task_id: TaskId


def _node_not_found_error(task_id: TaskId) -> DecompositionNodeNotFoundError:
    """Build the structured error for a query against an absent node."""
    error = DecompositionNodeNotFoundError(
        f"node {task_id.to_str()} is not part of the decomposition"
    )
    error.task_id = task_id
    return error


# ---------------------------------------------------------------------------
# Field validation (shared by construction and decoding)
# ---------------------------------------------------------------------------


def _validate_task_id(value: object, *, field_name: str) -> TaskId:
    """Validate that *value* is a canonical :class:`TaskId`."""
    if not isinstance(value, TaskId):
        raise TypeError(f"{field_name} must be a TaskId, got {type(value).__name__}")
    return value


def _validate_decomposition_id(value: object) -> DecompositionId:
    """Validate that *value* is a canonical :class:`DecompositionId`."""
    if not isinstance(value, DecompositionId):
        raise TypeError(f"decomposition_id must be a DecompositionId, got {type(value).__name__}")
    return value


def _validate_version(value: object) -> int:
    """Validate a decomposition revision counter (an int >= 1, never bool)."""
    if type(value) is not int:
        raise TypeError(f"version must be an int, got {type(value).__name__}")
    if value < 1:
        raise TaskDecompositionValidationError("version must be >= 1")
    return value


def _validate_text(value: object, *, field_name: str) -> str:
    """Validate an explicit, non-empty, trimmed text field with no control characters."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise TaskDecompositionValidationError(f"{field_name} must be non-empty and trimmed")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise TaskDecompositionValidationError(f"{field_name} must not contain control characters")
    return value


def _validate_parent_task_id(value: object, *, task_id: TaskId) -> TaskId | None:
    """Validate an optional parent :class:`TaskId`, rejecting self-parenting."""
    if value is None:
        return None
    if not isinstance(value, TaskId):
        raise TypeError(f"parent_task_id must be a TaskId or None, got {type(value).__name__}")
    if value == task_id:
        raise TaskDecompositionValidationError(
            f"task {task_id.to_str()} must not be its own parent"
        )
    return value


def _validate_order_index(value: object) -> int:
    """Validate a canonical 0-based sibling position (an int, never bool)."""
    if type(value) is not int:
        raise TypeError(f"order_index must be an int, got {type(value).__name__}")
    if value < 0:
        raise TaskDecompositionValidationError("order_index must be >= 0")
    return value


def _validate_success_criteria(value: object) -> tuple[str, ...]:
    """Validate declarative completion criteria: unique, canonical strings, bounded."""
    if not isinstance(value, tuple):
        raise TypeError(f"success_criteria must be a tuple of str, got {type(value).__name__}")
    if len(value) > MAX_SUCCESS_CRITERIA:
        raise TaskDecompositionValidationError(
            f"success_criteria must not exceed {MAX_SUCCESS_CRITERIA} entries"
        )
    seen: set[str] = set()
    for index, criterion in enumerate(value):
        validated = _validate_text(criterion, field_name=f"success_criteria[{index}]")
        if validated in seen:
            raise TaskDecompositionValidationError(f"duplicate success criterion: {validated!r}")
        seen.add(validated)
    return value


def _validate_depends_on(value: object, *, task_id: TaskId) -> tuple[TaskId, ...]:
    """Validate decomposition-internal ordering dependencies.

    Node-level rules: entries are canonical TaskIds, the node must not
    depend on itself, and no dependency may be listed twice. Presence and
    acyclicity across the whole decomposition are checked at record level.
    """
    if not isinstance(value, tuple):
        raise TypeError(f"depends_on must be a tuple of TaskId, got {type(value).__name__}")
    seen: set[TaskId] = set()
    for index, dependency in enumerate(value):
        validated = _validate_task_id(dependency, field_name=f"depends_on[{index}]")
        if validated == task_id:
            raise TaskDecompositionValidationError(
                f"node {task_id.to_str()} must not depend on itself"
            )
        if validated in seen:
            raise TaskDecompositionValidationError(
                f"node {task_id.to_str()} lists duplicate dependency {validated.to_str()}"
            )
        seen.add(validated)
    return value


def _reject_reserved_metadata_key(key: str, *, path: str) -> None:
    """Reject metadata keys that would smuggle secrets or authority through a record."""
    normalized = key.strip().lower()
    for marker in _SECRET_KEY_MARKERS:
        if marker in normalized:
            raise TaskDecompositionValidationError(
                f"{path} metadata key {key!r} is reserved: decomposition metadata must not "
                f"be used as a secret store"
            )
    for marker in _AUTHORITY_KEY_MARKERS:
        if marker in normalized:
            raise TaskDecompositionValidationError(
                f"{path} metadata key {key!r} is reserved: decomposition metadata must not "
                f"carry authority or policy bypass"
            )


def _freeze_json(value: object, *, path: str) -> object:
    """Validate JSON compatibility and return an immutable defensive copy."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TaskDecompositionValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TaskDecompositionValidationError(f"{path} contains a non-string metadata key")
            if not key or key != key.strip():
                raise TaskDecompositionValidationError(
                    f"{path} contains an empty or untrimmed metadata key"
                )
            _reject_reserved_metadata_key(key, path=path)
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, tuple | list):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        )
    raise TaskDecompositionValidationError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _freeze_metadata(value: object, *, path: str) -> Mapping[str, object]:
    """Validate and freeze a metadata mapping (defensive, immutable)."""
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a mapping, got {type(value).__name__}")
    frozen = _freeze_json(value, path=path)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
        raise AssertionError("metadata freezing produced a non-mapping")
    return frozen


def _to_json_value(value: object, *, path: str) -> JsonValue:
    """Convert an internally frozen JSON value back to JSON-compatible primitives."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):  # defensive; construction already rejects this
            raise TaskDecompositionValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):  # defensive; construction already rejects this
                raise TaskDecompositionValidationError(f"{path} contains a non-string metadata key")
            result[key] = _to_json_value(item, path=f"{path}.{key}")
        return result
    if isinstance(value, tuple | list):
        return [_to_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise TaskDecompositionValidationError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _to_json_object(value: Mapping[str, object], *, path: str) -> dict[str, JsonValue]:
    """Convert a frozen metadata mapping to a plain JSON-compatible dict."""
    converted = _to_json_value(value, path=path)
    if not isinstance(converted, dict):  # pragma: no cover - mapping always converts to dict
        raise AssertionError("metadata conversion produced a non-dict")
    return converted


def _format_timestamp(value: datetime) -> str:
    """Format a UTC-normalized datetime in the canonical AgentX timestamp form."""
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    """Parse a canonical UTC timestamp string into an aware datetime (UTC-normalized)."""
    if not isinstance(value, str):
        raise TaskDecompositionDeserializationError(
            f"created_at must be a string, got {type(value).__name__}",
            code=_CODE_INVALID_FIELD,
        )
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise TaskDecompositionDeserializationError(
            f"created_at is not a valid ISO-8601 timestamp: {value!r}",
            code=_CODE_INVALID_FIELD,
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TaskDecompositionDeserializationError(
            "created_at must be timezone-aware", code=_CODE_INVALID_FIELD
        )
    return parsed.astimezone(UTC)


def _parse_task_id(value: object, *, field_name: str) -> TaskId:
    """Parse a serialized canonical TaskId string."""
    if not isinstance(value, str):
        raise TaskDecompositionDeserializationError(
            f"{field_name} must be a string, got {type(value).__name__}",
            code=_CODE_INVALID_FIELD,
        )
    try:
        return TaskId.parse(value)
    except ValueError as exc:
        raise TaskDecompositionDeserializationError(
            f"{field_name} is not a valid TaskId: {value!r}", code=_CODE_INVALID_FIELD
        ) from exc


def _parse_decomposition_id(value: object) -> DecompositionId:
    """Parse a serialized canonical DecompositionId string."""
    if not isinstance(value, str):
        raise TaskDecompositionDeserializationError(
            f"decomposition_id must be a string, got {type(value).__name__}",
            code=_CODE_INVALID_FIELD,
        )
    try:
        return DecompositionId.parse(value)
    except ValueError as exc:
        raise TaskDecompositionDeserializationError(
            f"decomposition_id is not a valid DecompositionId: {value!r}",
            code=_CODE_INVALID_FIELD,
        ) from exc


def _parse_optional_task_id(value: object) -> TaskId | None:
    """Parse a serialized nullable TaskId string."""
    if value is None:
        return None
    return _parse_task_id(value, field_name="parent_task_id")


# ---------------------------------------------------------------------------
# Canonical records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class DecompositionNode:
    """One node of a canonical task decomposition.

    A node is inert plan data: identity, purpose, one parent link (never for
    the root), declarative success criteria, an explicit sibling position,
    decomposition-internal ordering dependencies, and non-authoritative
    context. It carries no status, no execution behaviour, and no authority.
    """

    task_id: TaskId
    objective: str
    parent_task_id: TaskId | None = None
    success_criteria: tuple[str, ...] = ()
    order_index: int = 0
    depends_on: tuple[TaskId, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        task_id = _validate_task_id(self.task_id, field_name="task_id")
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(
            self, "objective", _validate_text(self.objective, field_name="objective")
        )
        object.__setattr__(
            self, "parent_task_id", _validate_parent_task_id(self.parent_task_id, task_id=task_id)
        )
        object.__setattr__(
            self, "success_criteria", _validate_success_criteria(self.success_criteria)
        )
        object.__setattr__(self, "order_index", _validate_order_index(self.order_index))
        object.__setattr__(
            self, "depends_on", _validate_depends_on(self.depends_on, task_id=task_id)
        )
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata, path="metadata"))

    # -- Serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the canonical JSON-compatible schema-v1 representation of this node."""
        return {
            "task_id": self.task_id.to_str(),
            "objective": self.objective,
            "parent_task_id": (
                None if self.parent_task_id is None else self.parent_task_id.to_str()
            ),
            "success_criteria": list(self.success_criteria),
            "order_index": self.order_index,
            "depends_on": [dependency.to_str() for dependency in self.depends_on],
            "metadata": _to_json_object(self.metadata, path="metadata"),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskDecomposition:
    """Immutable canonical decomposition of one high-level Task into task units.

    This is **plan/decomposition data only**: an inert, validated,
    deterministically serializable structure. It grants no authority,
    schedules nothing, executes nothing, and declares no success.

    Attributes:
        decomposition_id: Canonical :class:`~agentx.core.ids.DecompositionId`
            identity of this decomposition record.
        root_task_id: The high-level :class:`~agentx.core.ids.TaskId` being
            decomposed. Always present as a node (the unique root).
        nodes: Every node — root included — in canonical pre-order (root
            first; siblings ascending by ``order_index``).
        version: Revision counter for the decomposition (int >= 1).
        created_at: Timezone-aware creation timestamp, normalized to UTC.
        metadata: Immutable, JSON-compatible, non-authoritative record
            context. Never a secret store and never an authority channel.

    Equality is by value. Use ``decomposition_id`` as the identity key in
    sets and dicts.
    """

    decomposition_id: DecompositionId
    root_task_id: TaskId
    nodes: tuple[DecompositionNode, ...]
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        decomposition_id = _validate_decomposition_id(self.decomposition_id)
        root_task_id = _validate_task_id(self.root_task_id, field_name="root_task_id")
        version = _validate_version(self.version)
        if not isinstance(self.nodes, tuple):
            raise TypeError(f"nodes must be a tuple, got {type(self.nodes).__name__}")
        created_at = self.created_at
        if not isinstance(created_at, datetime):
            raise TypeError(f"created_at must be a datetime, got {type(created_at).__name__}")
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise TaskDecompositionValidationError("created_at must be timezone-aware")

        object.__setattr__(self, "decomposition_id", decomposition_id)
        object.__setattr__(self, "root_task_id", root_task_id)
        object.__setattr__(self, "nodes", self.nodes)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "created_at", created_at.astimezone(UTC))
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata, path="metadata"))

        _validate_structure(self)

    # -- Construction -------------------------------------------------------

    @classmethod
    def create(
        cls,
        root_task_id: TaskId,
        nodes: tuple[DecompositionNode, ...],
        *,
        decomposition_id: DecompositionId | None = None,
        version: int = 1,
        created_at: datetime | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> TaskDecomposition:
        """Create a decomposition, generating record identity and timestamp.

        A creation convenience only: it performs no planning, scheduling,
        registration, event publication, or state change of the tasks.
        """
        return cls(
            decomposition_id=(
                decomposition_id if decomposition_id is not None else DecompositionId.create()
            ),
            root_task_id=root_task_id,
            nodes=nodes,
            version=version,
            created_at=datetime.now(UTC) if created_at is None else created_at,
            metadata={} if metadata is None else metadata,
        )

    # -- Structure queries (read-only, derived) ------------------------------

    @property
    def node_count(self) -> int:
        """Total number of nodes, including the root."""
        return len(self.nodes)

    @property
    def subtask_count(self) -> int:
        """Number of nodes below the root."""
        return len(self.nodes) - 1

    @property
    def depth(self) -> int:
        """Maximum root-to-leaf depth of the decomposition (the root is depth 1)."""
        return max(_node_depths(self.nodes, self.root_task_id).values())

    @property
    def root(self) -> DecompositionNode:
        """The unique root node (canonical pre-order guarantees it is first)."""
        return self.nodes[0]

    @property
    def leaves(self) -> tuple[DecompositionNode, ...]:
        """Nodes without children, in canonical node order."""
        child_counts = _child_counts(self.nodes)
        return tuple(node for node in self.nodes if child_counts[node.task_id] == 0)

    def get_node(self, task_id: TaskId) -> DecompositionNode | None:
        """Return the node for ``task_id`` if present, else ``None``."""
        _validate_task_id(task_id, field_name="task_id")
        return _index_by_id(self.nodes).get(task_id)

    def require_node(self, task_id: TaskId) -> DecompositionNode:
        """Return the node for ``task_id`` or raise DecompositionNodeNotFoundError."""
        key = _validate_task_id(task_id, field_name="task_id")
        node = self.get_node(key)
        if node is None:
            raise _node_not_found_error(key)
        return node

    def children_of(self, task_id: TaskId) -> tuple[DecompositionNode, ...]:
        """Return the direct children of ``task_id`` in canonical sibling order."""
        key = _validate_task_id(task_id, field_name="task_id")
        if key not in _index_by_id(self.nodes):
            raise _node_not_found_error(key)
        return tuple(node for node in self.nodes if node.parent_task_id == key)

    # -- Serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the canonical JSON-compatible schema-v1 representation.

        Node order is the validated canonical pre-order, so the
        representation is a deterministic function of the record.
        """
        return {
            "schema_version": DECOMPOSITION_SCHEMA_VERSION,
            "decomposition_id": self.decomposition_id.to_str(),
            "version": self.version,
            "root_task_id": self.root_task_id.to_str(),
            "created_at": _format_timestamp(self.created_at),
            "nodes": [node.to_dict() for node in self.nodes],
            "metadata": _to_json_object(self.metadata, path="metadata"),
        }

    def to_json(self) -> str:
        """Serialize to deterministic JSON text (sorted keys, no NaN)."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> TaskDecomposition:
        """Validate and deserialize a canonical JSON-compatible decomposition object."""
        if "schema_version" not in raw:
            raise TaskDecompositionDeserializationError(
                "decomposition missing required field: schema_version",
                code=_CODE_MISSING_FIELDS,
            )
        version = raw["schema_version"]
        if type(version) is not int:
            raise TaskDecompositionDeserializationError(
                "schema_version must be an integer", code=_CODE_INVALID_FIELD
            )
        if version != DECOMPOSITION_SCHEMA_VERSION:
            raise UnsupportedDecompositionSchemaVersionError(
                f"unsupported decomposition schema version {version}; "
                f"supported version is {DECOMPOSITION_SCHEMA_VERSION}",
                code=_CODE_INVALID_SCHEMA_VERSION,
            )

        actual = set(raw)
        missing = _CANONICAL_FIELD_SET - actual
        unknown = actual - _CANONICAL_FIELD_SET
        if missing:
            raise TaskDecompositionDeserializationError(
                f"decomposition missing required fields: {sorted(missing)}",
                code=_CODE_MISSING_FIELDS,
            )
        if unknown:
            raise TaskDecompositionDeserializationError(
                f"decomposition contains unknown fields: {sorted(unknown)}",
                code=_CODE_UNKNOWN_FIELDS,
            )

        raw_metadata: object = raw["metadata"]
        metadata: Mapping[str, object]
        if raw_metadata is None:
            metadata = {}
        elif isinstance(raw_metadata, Mapping):
            metadata = raw_metadata
        else:
            raise TaskDecompositionDeserializationError(
                f"metadata must be a mapping, got {type(raw_metadata).__name__}",
                code=_CODE_INVALID_FIELD,
            )

        raw_nodes = raw["nodes"]
        if not isinstance(raw_nodes, list | tuple):
            raise TaskDecompositionDeserializationError(
                f"nodes must be a list, got {type(raw_nodes).__name__}",
                code=_CODE_INVALID_FIELD,
            )
        nodes = tuple(_parse_node(raw_node) for raw_node in raw_nodes)

        return cls(
            decomposition_id=_parse_decomposition_id(raw["decomposition_id"]),
            root_task_id=_parse_task_id(raw["root_task_id"], field_name="root_task_id"),
            nodes=nodes,
            version=_parse_version(raw["version"]),
            created_at=_parse_timestamp(raw["created_at"]),
            metadata=metadata,
        )

    @classmethod
    def from_json(cls, text: str) -> TaskDecomposition:
        """Validate and deserialize canonical decomposition JSON text."""
        if not isinstance(text, str):
            raise TypeError(f"decomposition JSON must be a string, got {type(text).__name__}")
        try:
            decoded: object = json.loads(text)
        except ValueError as exc:
            raise TaskDecompositionDeserializationError(
                f"decomposition JSON is malformed: {exc}", code=_CODE_INVALID_INPUT
            ) from exc
        if not isinstance(decoded, Mapping):
            raise TaskDecompositionDeserializationError(
                "decomposition JSON root must be an object", code=_CODE_INVALID_INPUT
            )
        return cls.from_dict(decoded)


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


def _index_by_id(nodes: tuple[DecompositionNode, ...]) -> dict[TaskId, DecompositionNode]:
    """Index nodes by task id, rejecting duplicate ids."""
    by_id: dict[TaskId, DecompositionNode] = {}
    for node in nodes:
        if node.task_id in by_id:
            raise TaskDecompositionValidationError(
                f"duplicate task id in decomposition: {node.task_id.to_str()}"
            )
        by_id[node.task_id] = node
    return by_id


def _child_counts(nodes: tuple[DecompositionNode, ...]) -> dict[TaskId, int]:
    """Count direct children per parent (root included)."""
    counts: dict[TaskId, int] = {node.task_id: 0 for node in nodes}
    for node in nodes:
        if node.parent_task_id is not None:
            counts[node.parent_task_id] += 1
    return counts


def _node_depths(nodes: tuple[DecompositionNode, ...], root_task_id: TaskId) -> dict[TaskId, int]:
    """Compute every node's depth (root = 1); reject parent-link cycles.

    With single-parent links, no dangling parents, and exactly one root, the
    only way a node can fail to reach the root is a closed parent-link cycle.
    """
    by_id = _index_by_id(nodes)
    depths: dict[TaskId, int] = {}
    for start in nodes:
        if start.task_id in depths:
            continue
        chain: list[DecompositionNode] = []
        on_path: set[TaskId] = set()
        cursor: DecompositionNode = start
        while cursor.task_id not in depths:
            if cursor.parent_task_id is None:
                if cursor.task_id != root_task_id:
                    raise TaskDecompositionValidationError(
                        "decomposition contains a node chain that does not reach the root"
                    )
                depths[cursor.task_id] = 1
                break
            if cursor.task_id in on_path:
                cycle = [node.task_id.to_str() for node in chain if node.task_id in on_path]
                raise TaskDecompositionValidationError(
                    "parent-link cycle detected involving: " + ", ".join(cycle)
                )
            on_path.add(cursor.task_id)
            chain.append(cursor)
            cursor = by_id[cursor.parent_task_id]
        base = depths[cursor.task_id]
        for ancestor in reversed(chain):
            base += 1
            depths[ancestor.task_id] = base
    return depths


def _dependency_cycle(nodes: tuple[DecompositionNode, ...]) -> tuple[TaskId, ...] | None:
    """Return one dependency cycle (as the involved ids) if the dependency graph has one."""
    by_id = _index_by_id(nodes)
    unvisited = 0
    in_stack = 1
    done = 2
    color: dict[TaskId, int] = {node.task_id: unvisited for node in nodes}
    parent: dict[TaskId, TaskId] = {}

    for start in by_id.values():
        if color[start.task_id] != unvisited:
            continue
        color[start.task_id] = in_stack
        stack: list[tuple[TaskId, Iterator[TaskId]]] = [
            (start.task_id, iter(by_id[start.task_id].depends_on))
        ]
        while stack:
            node_id, iterator = stack[-1]
            advanced = False
            for dependency in iterator:
                state = color[dependency]
                if state == in_stack:
                    # Re-entered a node on the active path: recover the cycle.
                    cycle = [dependency]
                    walker = node_id
                    while walker != dependency:
                        cycle.append(walker)
                        walker = parent[walker]
                    return tuple(reversed(cycle))
                if state == unvisited:
                    parent[dependency] = node_id
                    color[dependency] = in_stack
                    stack.append((dependency, iter(by_id[dependency].depends_on)))
                    advanced = True
                    break
            if not advanced:
                color[node_id] = done
                stack.pop()
    return None


def _canonical_preorder(
    nodes: tuple[DecompositionNode, ...], root_task_id: TaskId
) -> tuple[TaskId, ...]:
    """Compute the canonical pre-order: root first, siblings by order_index."""
    children: dict[TaskId, list[DecompositionNode]] = {node.task_id: [] for node in nodes}
    for node in nodes:
        if node.parent_task_id is not None:
            children[node.parent_task_id].append(node)
    for siblings in children.values():
        siblings.sort(key=lambda node: node.order_index)

    order: list[TaskId] = []
    stack: list[TaskId] = [root_task_id]
    while stack:
        task_id = stack.pop()
        order.append(task_id)
        for child in reversed(children[task_id]):
            stack.append(child.task_id)
    return tuple(order)


def _validate_structure(decomposition: TaskDecomposition) -> None:
    """Enforce every canonical structural invariant on a fully parsed decomposition."""
    nodes = decomposition.nodes
    if not nodes:
        raise TaskDecompositionValidationError(
            "decomposition must contain at least one node (the root)"
        )
    if len(nodes) > MAX_DECOMPOSITION_NODES:
        raise TaskDecompositionValidationError(
            f"decomposition contains {len(nodes)} nodes; maximum is {MAX_DECOMPOSITION_NODES}"
        )

    by_id = _index_by_id(nodes)

    # Valid root: present, unique rootless node, and that node is the root.
    if decomposition.root_task_id not in by_id:
        raise TaskDecompositionValidationError(
            f"root task {decomposition.root_task_id.to_str()} is not present "
            "among decomposition nodes"
        )
    roots = [node for node in nodes if node.parent_task_id is None]
    if len(roots) != 1:
        raise TaskDecompositionValidationError(
            f"exactly one node must be the root (parent_task_id is None); found {len(roots)}"
        )
    if roots[0].task_id != decomposition.root_task_id:
        raise TaskDecompositionValidationError(
            f"the rootless node {roots[0].task_id.to_str()} does not match "
            f"root_task_id {decomposition.root_task_id.to_str()}"
        )
    if roots[0].order_index != 0:
        raise TaskDecompositionValidationError("the root node's order_index must be 0")

    # No dangling parent.
    for node in nodes:
        parent = node.parent_task_id
        if parent is not None and parent not in by_id:
            raise TaskDecompositionValidationError(
                f"node {node.task_id.to_str()} has dangling parent reference: {parent.to_str()}"
            )

    # No parent-link cycle; bounded depth.
    depths = _node_depths(nodes, decomposition.root_task_id)
    max_depth = max(depths.values())
    if max_depth > MAX_DECOMPOSITION_DEPTH:
        raise TaskDecompositionValidationError(
            f"decomposition depth {max_depth} exceeds maximum {MAX_DECOMPOSITION_DEPTH}"
        )

    # Explicit sibling ordering: indices exactly 0..k-1 per sibling group.
    siblings: dict[TaskId | None, list[DecompositionNode]] = {}
    for node in nodes:
        siblings.setdefault(node.parent_task_id, []).append(node)
    for parent_id, group in siblings.items():
        expected = list(range(len(group)))
        found = sorted(node.order_index for node in group)
        if found != expected:
            label = "root" if parent_id is None else parent_id.to_str()
            raise TaskDecompositionValidationError(
                f"sibling order indices under {label} must be exactly {expected}; found {found}"
            )

    # Canonical node sequence: validated pre-order.
    actual_order = tuple(node.task_id for node in nodes)
    expected_order = _canonical_preorder(nodes, decomposition.root_task_id)
    if actual_order != expected_order:
        raise TaskDecompositionValidationError(
            "nodes are not in canonical pre-order traversal "
            "(root first; siblings ascending by order_index)"
        )

    # Dependency integrity: present targets, acyclic.
    for node in nodes:
        for dependency in node.depends_on:
            if dependency not in by_id:
                raise TaskDecompositionValidationError(
                    f"node {node.task_id.to_str()} depends on unknown task: {dependency.to_str()}"
                )
    cycle = _dependency_cycle(nodes)
    if cycle is not None:
        raise TaskDecompositionValidationError(
            "dependency cycle detected involving: "
            + ", ".join(task_id.to_str() for task_id in cycle)
        )


# ---------------------------------------------------------------------------
# Decoding (shared by canonical and model-proposal paths)
# ---------------------------------------------------------------------------


def _check_field_set(raw: Mapping[object, object], *, expected: frozenset[str]) -> None:
    """Reject missing or unknown keys with a structured error code."""
    keys = set(raw)
    string_keys = {key for key in keys if isinstance(key, str)}
    missing = expected - string_keys
    unknown = keys - expected
    if missing:
        raise TaskDecompositionDeserializationError(
            f"decomposition data missing required fields: {sorted(missing)}",
            code=_CODE_MISSING_FIELDS,
        )
    if unknown:
        raise TaskDecompositionDeserializationError(
            f"decomposition data contains unknown fields: {sorted(map(str, unknown))}",
            code=_CODE_UNKNOWN_FIELDS,
        )


def _parse_string_list(value: object, *, field_name: str) -> tuple[str, ...]:
    """Parse a JSON list of strings into a canonical tuple, rejecting any non-string item."""
    if not isinstance(value, list):
        raise TaskDecompositionDeserializationError(
            f"{field_name} must be a list of strings, got {type(value).__name__}",
            code=_CODE_INVALID_FIELD,
        )
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise TaskDecompositionDeserializationError(
                f"{field_name}[{index}] must be a string, got {type(item).__name__}",
                code=_CODE_INVALID_FIELD,
            )
        items.append(item)
    return tuple(items)


def _parse_version(value: object) -> int:
    """Parse a serialized decomposition revision counter (an int >= 1, never bool)."""
    if type(value) is not int:
        raise TaskDecompositionDeserializationError(
            f"version must be an integer, got {type(value).__name__}", code=_CODE_INVALID_FIELD
        )
    if value < 1:
        raise TaskDecompositionValidationError("version must be >= 1")
    return value


def _parse_order_index(value: object) -> int:
    """Parse a serialized canonical sibling position (an int >= 0, never bool)."""
    if type(value) is not int:
        raise TaskDecompositionDeserializationError(
            f"order_index must be an integer, got {type(value).__name__}", code=_CODE_INVALID_FIELD
        )
    if value < 0:
        raise TaskDecompositionValidationError("order_index must be >= 0")
    return value


def _parse_node(raw: object) -> DecompositionNode:
    """Validate and decode one node object from JSON-compatible data."""
    if not isinstance(raw, Mapping):
        raise TaskDecompositionDeserializationError(
            f"node must be an object, got {type(raw).__name__}", code=_CODE_INVALID_FIELD
        )
    _check_field_set(raw, expected=_NODE_FIELD_SET)

    criteria_raw = _parse_string_list(raw["success_criteria"], field_name="success_criteria")
    metadata = raw["metadata"]
    if metadata is not None and not isinstance(metadata, Mapping):
        raise TaskDecompositionDeserializationError(
            f"metadata must be a mapping, got {type(metadata).__name__}",
            code=_CODE_INVALID_FIELD,
        )
    depends_raw = _parse_string_list(raw["depends_on"], field_name="depends_on")

    return DecompositionNode(
        task_id=_parse_task_id(raw["task_id"], field_name="task_id"),
        objective=_validate_text(raw["objective"], field_name="objective"),
        parent_task_id=_parse_optional_task_id(raw["parent_task_id"]),
        success_criteria=criteria_raw,
        order_index=_parse_order_index(raw["order_index"]),
        depends_on=tuple(_parse_task_id(dep, field_name="depends_on") for dep in depends_raw),
        metadata={} if metadata is None else metadata,
    )


# ---------------------------------------------------------------------------
# Model / reasoner proposal acceptance boundary
# ---------------------------------------------------------------------------


def _accept_failure(
    code: str, message: str, *, details: dict[str, object] | None = None
) -> AgentXError:
    """Build the structured error carried by a rejected proposal.

    Rejection is deterministic: the same proposal against the same boundary
    always produces an equal error, and retrying it fails identically.
    """
    return AgentXError(
        code=code,
        message=message,
        category=ErrorCategory.VALIDATION,
        retryability=Retryability.NON_RETRYABLE,
        details=details,
    )


def _map_decomposition_error(exc: TaskDecompositionError) -> AgentXError:
    """Map an internal contract error raised while decoding a proposal."""
    code = exc.code or _CODE_INVALID_STRUCTURE
    if isinstance(exc, UnsupportedDecompositionSchemaVersionError):
        code = exc.code or _CODE_INVALID_SCHEMA_VERSION
    elif isinstance(exc, TaskDecompositionDeserializationError):
        code = exc.code or _CODE_INVALID_FIELD
    return _accept_failure(code, str(exc))


def accept_model_proposal(
    raw: object,
    *,
    root_task_id: TaskId | None = None,
    decomposition_id: DecompositionId | None = None,
    version: int = 1,
    created_at: datetime | None = None,
    metadata: Mapping[str, object] | None = None,
) -> Result[TaskDecomposition, AgentXError]:
    """Validate untrusted model/reasoner output into a canonical decomposition.

    ``raw`` is inert data — for example the result of ``json.loads`` on model
    text. The accepted proposal shape is exactly ``schema_version``,
    ``root_task_id``, and ``nodes``; any other field (instructions,
    commands, identity, authority markers, ...) is rejected. The model never
    sets record identity: ``decomposition_id``, ``version``, ``created_at``,
    and record ``metadata`` are supplied by the accepting caller (or derived
    here), so model text is never executable authority and never an identity
    or policy channel.

    Returns:
        ``Result.success(TaskDecomposition)`` when the proposal validates
        into canonical data; otherwise ``Result.failure(AgentXError)``
        carrying a structured ``task_decomposition.*`` code. Failures of the
        untrusted model data never raise.

    The caller-supplied typed arguments are a programming contract, validated
    (and raising) *before* the model-data boundary: ``root_task_id`` must be a
    :class:`~agentx.core.ids.TaskId`, ``decomposition_id`` a
    :class:`~agentx.core.ids.DecompositionId`, ``version`` an int >= 1,
    ``created_at`` an aware datetime, and ``metadata`` a mapping. Those
    errors propagate, because they are programming errors, not model data.
    """
    if root_task_id is not None:
        _validate_task_id(root_task_id, field_name="root_task_id")
    caller_version = _validate_version(version)
    caller_decomposition_id = (
        _validate_decomposition_id(decomposition_id) if decomposition_id is not None else None
    )
    caller_created_at: datetime
    if created_at is None:
        caller_created_at = datetime.now(UTC)
    elif isinstance(created_at, datetime):
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise TaskDecompositionValidationError("created_at must be timezone-aware")
        caller_created_at = created_at
    else:
        raise TypeError(f"created_at must be a datetime, got {type(created_at).__name__}")
    caller_metadata = _freeze_metadata(metadata, path="metadata") if metadata is not None else {}

    if not isinstance(raw, Mapping):
        return Result[TaskDecomposition, AgentXError].failure(
            _accept_failure(
                _CODE_INVALID_INPUT,
                f"model proposal must be a JSON object, got {type(raw).__name__}",
            )
        )

    try:
        _check_field_set(raw, expected=_MODEL_PROPOSAL_FIELD_SET)

        schema_version = raw["schema_version"]
        if type(schema_version) is not int:
            raise TaskDecompositionDeserializationError(
                "schema_version must be an integer", code=_CODE_INVALID_FIELD
            )
        if schema_version != DECOMPOSITION_SCHEMA_VERSION:
            raise UnsupportedDecompositionSchemaVersionError(
                f"unsupported decomposition schema version {schema_version}; "
                f"supported version is {DECOMPOSITION_SCHEMA_VERSION}",
                code=_CODE_INVALID_SCHEMA_VERSION,
            )

        proposed_root = _parse_task_id(raw["root_task_id"], field_name="root_task_id")
        if root_task_id is not None and proposed_root != root_task_id:
            return Result[TaskDecomposition, AgentXError].failure(
                _accept_failure(
                    _CODE_ROOT_MISMATCH,
                    "proposed root task does not match the task being decomposed",
                    details={
                        "expected_root_task_id": root_task_id.to_str(),
                        "proposed_root_task_id": proposed_root.to_str(),
                    },
                )
            )

        raw_nodes = raw["nodes"]
        if not isinstance(raw_nodes, list):
            raise TaskDecompositionDeserializationError(
                f"nodes must be a list, got {type(raw_nodes).__name__}",
                code=_CODE_INVALID_FIELD,
            )
        nodes = tuple(_parse_node(raw_node) for raw_node in raw_nodes)

        return Result[TaskDecomposition, AgentXError].success(
            TaskDecomposition(
                decomposition_id=(
                    caller_decomposition_id
                    if caller_decomposition_id is not None
                    else DecompositionId.create()
                ),
                root_task_id=proposed_root,
                nodes=nodes,
                version=caller_version,
                created_at=caller_created_at,
                metadata=caller_metadata,
            )
        )
    except TaskDecompositionError as exc:
        return Result[TaskDecomposition, AgentXError].failure(_map_decomposition_error(exc))
