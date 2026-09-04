"""Canonical AgentX Task schema (A1.05).

A Task is the *governed unit of work* in the AgentX pipeline::

    Goal -> Task -> Policy -> Capability -> Action -> Observation
         -> Verification -> Outcome

This module defines the **Task data model only**. It is a shared domain
contract, deliberately inert:

    - It is not an executor, planner, scheduler, or Task Manager.
    - It does not own the Task state machine (that is A1.06). The controlled
      :class:`TaskStatus` vocabulary lives here because the schema needs it,
      but there is no transition logic, no ``can_transition()``, no mutation
      helper, and no automatic status change.
    - It does not emit events and knows nothing about the EventBus (C1.02 /
      C1.03). It exposes a stable ``TaskId`` string that future integration can
      place in ``Event.task_id``.
    - It does not persist anything and knows nothing about SQLite (C1.05).
    - It grants no authority. :class:`TaskPriority` is a scheduling hint; a
      CRITICAL task must still pass through Policy/Kernel exactly like any
      other task.

Design rules enforced here
--------------------------

Immutability:
    ``Task`` is a frozen, slotted dataclass. Its ``metadata`` mapping is
    defensively copied and exposed as an immutable mapping with tuple-valued
    arrays, so no caller — and no task store — can mutate a Task in place.

Identity:
    Identity is the canonical :class:`agentx.core.ids.TaskId`. Raw UUIDs and
    strings are rejected at the constructor boundary so that the rest of the
    system has exactly one task identity type.

Time:
    ``created_at`` is always timezone-aware and always normalized to UTC.
    Naive datetimes are rejected, never guessed.

Hierarchy:
    ``parent_task_id`` is optional and makes parent/child decomposition
    *representable* (parent -> child -> child). This module does **not**
    implement decomposition, DAG traversal, ancestry validation against a
    store, or any structural query. Direct self-parenting is the one
    detectable cycle, and it is rejected.

Metadata:
    Metadata is non-authoritative context only:
      * defensive copy, immutable externally;
      * JSON-compatible values only (no callables, modules, or executables);
      * reserved key markers for secrets and authority are rejected, so
        metadata can never be used as a secret store or as a policy bypass.

Owner: A1.05. Belongs to ``agentx.core``; imports only the standard library
and :mod:`agentx.core.ids`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast

from agentx.core.ids import TaskId

__all__ = [
    "TASK_SCHEMA_VERSION",
    "JsonValue",
    "Task",
    "TaskDeserializationError",
    "TaskPriority",
    "TaskStatus",
    "TaskValidationError",
    "UnsupportedTaskSchemaVersionError",
]

#: Schema version of the serialized Task representation. Bumped only when the
#: serialized form changes incompatibly.
TASK_SCHEMA_VERSION: Final[int] = 1

#: JSON-compatible value used by Task serialization. Structured values are
#: frozen as tuples internally and emitted as lists by :meth:`Task.to_dict`.
#: This mirrors the shape of ``agentx.infrastructure.events.JsonValue``; it is
#: restated here because ``agentx.core`` is the dependency leaf and must not
#: import non-domain plumbing.
JsonValue = bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None

_TASK_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "task_id",
        "objective",
        "status",
        "priority",
        "parent_task_id",
        "created_at",
        "metadata",
    }
)

_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")

# Metadata is context, never a credential store. Keys containing these markers
# are rejected so that secrets cannot be smuggled through a Task.
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
# rejected so that a Task cannot carry a policy/permission bypass.
_AUTHORITY_KEY_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "permission",
        "authority",
        "privilege",
        "bypass",
        "escalat",
    }
)


class TaskValidationError(ValueError):
    """Raised when a Task or its serialized form violates the canonical schema."""


class TaskDeserializationError(TaskValidationError):
    """Raised when encoded Task data cannot be decoded into a Task."""


class UnsupportedTaskSchemaVersionError(TaskDeserializationError):
    """Raised when encoded Task data uses a schema version this code cannot read."""


class TaskStatus(StrEnum):
    """Controlled lifecycle vocabulary for a Task.

    A1.05 owns this **vocabulary only**. The transition rules, guards, and any
    derived helpers belong to A1.06 (Task state machine) and must not be added
    here. Nothing in this module mutates status.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    """Controlled scheduling-hint vocabulary for a Task.

    Priority is data about urgency, **not** authority. A CRITICAL task does not
    bypass permissions, risk limits, budgets, or kernel policy; those decisions
    belong to Policy and the Trusted Kernel. Nothing in this module reads
    priority to grant or widen anything.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


def _validate_objective(value: object) -> str:
    """Validate that *value* is an explicit, non-empty, trimmed objective."""
    if not isinstance(value, str):
        raise TypeError(f"objective must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise TaskValidationError("objective must be non-empty and trimmed")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise TaskValidationError("objective must not contain control characters")
    return value


def _validate_task_id(value: object, *, field_name: str) -> TaskId:
    """Validate that *value* is a canonical :class:`TaskId`."""
    if not isinstance(value, TaskId):
        raise TypeError(f"{field_name} must be a TaskId, got {type(value).__name__}")
    return value


def _validate_status(value: object) -> TaskStatus:
    """Validate that *value* is a member of the controlled status vocabulary."""
    if not isinstance(value, TaskStatus):
        raise TypeError(f"status must be a TaskStatus, got {type(value).__name__}")
    return value


def _validate_priority(value: object) -> TaskPriority:
    """Validate that *value* is a member of the controlled priority vocabulary."""
    if not isinstance(value, TaskPriority):
        raise TypeError(f"priority must be a TaskPriority, got {type(value).__name__}")
    return value


def _validate_parent_task_id(value: object, *, task_id: TaskId) -> TaskId | None:
    """Validate an optional parent :class:`TaskId`, rejecting direct self-parenting."""
    if value is None:
        return None
    if not isinstance(value, TaskId):
        raise TypeError(f"parent_task_id must be a TaskId or None, got {type(value).__name__}")
    if value == task_id:
        raise TaskValidationError(f"task {task_id.to_str()} must not be its own parent")
    return value


def _validate_created_at(value: object) -> datetime:
    """Validate that *value* is a timezone-aware datetime and normalize it to UTC."""
    if not isinstance(value, datetime):
        raise TypeError(f"created_at must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise TaskValidationError("created_at must be timezone-aware")
    return value.astimezone(UTC)


def _reject_reserved_metadata_key(key: str, *, path: str) -> str:
    """Reject metadata keys that would smuggle secrets or authority through a Task."""
    normalized = key.strip().lower()
    if not key or key != key.strip():
        raise TaskValidationError(f"{path} contains an empty or untrimmed metadata key")
    for marker in _SECRET_KEY_MARKERS:
        if marker in normalized:
            raise TaskValidationError(
                f"{path} metadata key {key!r} is reserved: task metadata must not be "
                f"used as a secret store"
            )
    for marker in _AUTHORITY_KEY_MARKERS:
        if marker in normalized:
            raise TaskValidationError(
                f"{path} metadata key {key!r} is reserved: task metadata must not carry "
                f"authority or policy bypass"
            )
    return key


def _freeze_json(value: object, *, path: str) -> object:
    """Validate JSON compatibility and return an immutable defensive copy."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TaskValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TaskValidationError(f"{path} contains a non-string metadata key")
            _reject_reserved_metadata_key(key, path=path)
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        )
    raise TaskValidationError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _freeze_metadata(value: object, *, path: str = "metadata") -> Mapping[str, object]:
    """Validate and freeze a Task metadata mapping."""
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
            raise TaskValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):  # defensive; construction already rejects this
                raise TaskValidationError(f"{path} contains a non-string metadata key")
            result[key] = _to_json_value(item, path=f"{path}.{key}")
        return result
    if isinstance(value, tuple | list):
        return [_to_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise TaskValidationError(
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


@dataclass(frozen=True, slots=True)
class Task:
    """Immutable canonical Task record: the governed unit of work in AgentX.

    A Task is a *data* contract. It carries identity, purpose, lifecycle
    vocabulary, scheduling hint, optional parent link, creation time, and
    non-authoritative context. It carries no execution behaviour: no run
    method, no planning, no transitions, no persistence, no event publication,
    and no authority. Its only methods are identity/serialization accessors.

    Attributes:
        task_id: Canonical :class:`~agentx.core.ids.TaskId` identity.
        objective: Explicit, non-empty, trimmed statement of the work.
        status: Controlled :class:`TaskStatus` value. Set at construction;
            never mutated here — transitions belong to A1.06.
        priority: Controlled :class:`TaskPriority` scheduling hint. It grants
            no authority of any kind.
        parent_task_id: Optional parent :class:`TaskId`, making decomposition
            representable. Never equal to ``task_id``.
        created_at: Timezone-aware creation timestamp, normalized to UTC.
        metadata: Immutable, JSON-compatible, non-authoritative context. Never
            a secret store and never an authority channel.

    Tasks are compared by value. They are not hashable because ``metadata`` is
    a mapping; use ``task_id`` as the identity key in sets and dicts.
    """

    task_id: TaskId
    objective: str
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    parent_task_id: TaskId | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        task_id = _validate_task_id(self.task_id, field_name="task_id")
        objective = _validate_objective(self.objective)
        status = _validate_status(self.status)
        priority = _validate_priority(self.priority)
        parent_task_id = _validate_parent_task_id(self.parent_task_id, task_id=task_id)
        created_at = _validate_created_at(self.created_at)

        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "priority", priority)
        object.__setattr__(self, "parent_task_id", parent_task_id)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    # -- Construction -------------------------------------------------------

    @classmethod
    def create(
        cls,
        objective: str,
        *,
        task_id: TaskId | None = None,
        status: TaskStatus = TaskStatus.PENDING,
        priority: TaskPriority = TaskPriority.NORMAL,
        parent_task_id: TaskId | None = None,
        created_at: datetime | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Task:
        """Create a Task, generating a canonical id and a UTC creation timestamp.

        This is a creation convenience only. It performs no registration,
        scheduling, validation against a store, or event publication.
        """
        return cls(
            task_id=_new_task_id() if task_id is None else task_id,
            objective=objective,
            status=status,
            priority=priority,
            parent_task_id=parent_task_id,
            created_at=datetime.now(UTC) if created_at is None else created_at,
            metadata={} if metadata is None else metadata,
        )

    # -- Identity -----------------------------------------------------------

    @property
    def task_id_str(self) -> str:
        """Stable canonical string form of ``task_id``.

        This is the exact value future integration places in ``Event.task_id``
        (C1.02 carries task identity as an opaque string). It is stable for the
        lifetime of the Task and round-trips through :meth:`TaskId.parse`.
        """
        return self.task_id.to_str()

    @property
    def is_root(self) -> bool:
        """True when this Task has no parent link.

        This is a local predicate on one field only; it performs no store
        lookup, no ancestry walk, and no DAG traversal.
        """
        return self.parent_task_id is None

    def is_child_of(self, parent_task_id: TaskId | None) -> bool:
        """True when this Task's parent link equals ``parent_task_id``.

        This compares the direct parent reference only. It does not resolve
        grandparents, ancestry, or any stored Task.
        """
        if parent_task_id is None:
            return False
        return self.parent_task_id == parent_task_id

    # -- Serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the canonical JSON-compatible schema-v1 representation."""
        return {
            "schema_version": TASK_SCHEMA_VERSION,
            "task_id": self.task_id.to_str(),
            "objective": self.objective,
            "status": self.status.value,
            "priority": self.priority.value,
            "parent_task_id": (
                None if self.parent_task_id is None else self.parent_task_id.to_str()
            ),
            "created_at": _format_timestamp(self.created_at),
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
    def from_dict(cls, raw: Mapping[str, object]) -> Task:
        """Validate and deserialize a canonical JSON-compatible Task object."""
        if "schema_version" not in raw:
            raise TaskDeserializationError("task missing required field: schema_version")
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise TaskDeserializationError("schema_version must be an integer")
        if version != TASK_SCHEMA_VERSION:
            raise UnsupportedTaskSchemaVersionError(
                f"unsupported task schema version {version}; "
                f"supported version is {TASK_SCHEMA_VERSION}"
            )

        actual = set(raw)
        missing = _TASK_FIELDS - actual
        unknown = actual - _TASK_FIELDS
        if missing:
            raise TaskDeserializationError(f"task missing required fields: {sorted(missing)}")
        if unknown:
            raise TaskDeserializationError(f"task contains unknown fields: {sorted(unknown)}")

        metadata = raw["metadata"]
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TypeError(f"metadata must be a mapping, got {type(metadata).__name__}")

        return cls(
            task_id=_parse_task_id(raw["task_id"], field_name="task_id"),
            objective=_validate_objective(raw["objective"]),
            status=_parse_status(raw["status"]),
            priority=_parse_priority(raw["priority"]),
            parent_task_id=_parse_optional_task_id(raw["parent_task_id"]),
            created_at=_parse_timestamp(raw["created_at"]),
            metadata={} if metadata is None else metadata,
        )

    @classmethod
    def from_json(cls, text: str) -> Task:
        """Validate and deserialize canonical Task JSON text."""
        if not isinstance(text, str):
            raise TypeError(f"task JSON must be a string, got {type(text).__name__}")
        try:
            decoded = json.loads(text)
        except ValueError as exc:
            raise TaskDeserializationError(f"task JSON is malformed: {exc}") from exc
        if not isinstance(decoded, Mapping):
            raise TaskDeserializationError("task JSON root must be an object")
        return cls.from_dict(decoded)


def _parse_task_id(value: object, *, field_name: str) -> TaskId:
    """Parse a serialized canonical TaskId string."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    try:
        parsed = TaskId.parse(value)
    except ValueError as exc:
        raise TaskDeserializationError(f"{field_name} is not a valid TaskId: {value!r}") from exc
    # ``DomainId.parse`` is declared as returning ``DomainId``; restore the
    # concrete type at the single point of use.
    return cast(TaskId, parsed)


def _new_task_id() -> TaskId:
    """Generate a new canonical TaskId.

    ``DomainId.create()`` is declared as returning ``DomainId`` (A1.04 predates
    ``Self`` typing on the factory), so the concrete type is restored here, at
    the single point of use.
    """
    return cast(TaskId, TaskId.create())


def _parse_optional_task_id(value: object) -> TaskId | None:
    """Parse a serialized nullable TaskId string."""
    if value is None:
        return None
    return _parse_task_id(value, field_name="parent_task_id")


def _parse_status(value: object) -> TaskStatus:
    """Parse a serialized status from the controlled vocabulary."""
    if not isinstance(value, str):
        raise TypeError(f"status must be a string, got {type(value).__name__}")
    try:
        return TaskStatus(value)
    except ValueError as exc:
        raise TaskDeserializationError(
            f"status must be one of {[member.value for member in TaskStatus]}; got {value!r}"
        ) from exc


def _parse_priority(value: object) -> TaskPriority:
    """Parse a serialized priority from the controlled vocabulary."""
    if not isinstance(value, str):
        raise TypeError(f"priority must be a string, got {type(value).__name__}")
    try:
        return TaskPriority(value)
    except ValueError as exc:
        raise TaskDeserializationError(
            f"priority must be one of {[member.value for member in TaskPriority]}; got {value!r}"
        ) from exc


def _parse_timestamp(value: object) -> datetime:
    """Parse a canonical UTC timestamp string into an aware datetime."""
    if not isinstance(value, str):
        raise TypeError(f"created_at must be a string, got {type(value).__name__}")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise TaskDeserializationError(
            f"created_at is not a valid ISO-8601 timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TaskDeserializationError("created_at must be timezone-aware")
    return parsed.astimezone(UTC)
