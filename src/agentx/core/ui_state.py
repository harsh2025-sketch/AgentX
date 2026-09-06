"""Canonical AgentX runtime-to-UI state protocol (C7.01).

This module defines the read-oriented, provider- and framework-neutral
contract through which a future HUD/debug UI can *observe* AgentX runtime
state. It is a data protocol only.

What the protocol exposes
-------------------------

    - :class:`UiStateSnapshot` — a structured, versioned, deterministic
      observation of one task's runtime state: task identity/state, current
      execution level, current operation, plan/procedure reference where
      available, verification status, pending approval/risk state, the
      structured error where present, progress where known, and the
      observation timestamp.
    - :func:`project_state_event` — a pure, deterministic conversion of the
      canonical event stream (``agentx.core.events.Event``) onto snapshots,
      so a UI consumer can reconstruct state by replaying canonical events.

Canonical reuse
---------------

Every concept that already has a canonical home in ``agentx.core`` is
reused, never restated: task identity and schema (``agentx.core.tasks`` /
``agentx.core.ids``), the canonical Task transition matrix
(``agentx.core.task_state``), the structured error model
(``agentx.core.errors``), and the canonical event envelope
(``agentx.core.events``).

Execution-level, risk-level, and permission values are carried as closed
string vocabularies whose values exactly mirror their canonical owners
(``agentx.cognition.router.ExecutionLevel``,
``agentx.kernel.risk.RiskLevel``, and
``agentx.kernel.permissions.Permission``). ``agentx.core`` is the
dependency leaf and must not import outer subsystems, so the protocol
mirrors the *values* instead of importing the enum objects; the canonical
modules remain the authority for those concepts.

The UI is not a trusted kernel
------------------------------

Every field of the protocol is an *observation*. The wire schema is a closed
set of exact keys at every level, and deserialization rejects unknown fields
fail-closed. Authority-shaped fields (``approved``, ``granted``, ``bypass``,
``execute``, ...) are not in the schema and can therefore never be admitted:
a state object sent back by a UI consumer is inert data, and nothing in this
module interprets any text it carries. The protocol contains no method that
mutates a runtime: every value type is frozen, and the only transformation
this module performs, :func:`project_state_event`, returns a new snapshot
without touching its inputs. Structurally, the module imports no authority
boundary (no ``agentx.kernel``, no ``agentx.capabilities``), so it holds no
path to permissions, gates, budgets, or execution.

Versioning and determinism
--------------------------

``schema_version`` is explicit and currently 1; any other version fails
closed with :class:`UnsupportedUiStateSchemaVersionError`. Serialization is
canonical JSON (sorted keys, compact separators, no NaN) with UTC ISO-8601
microsecond timestamps, so identical state always serializes to identical
bytes. Projection is a pure function: identical inputs always yield equal
outputs.

Deliberate non-scope
--------------------

Not a HUD, web frontend, desktop frontend, or visual styling. Not a
transport or provider adapter (later C7 tasks own how snapshots and events
reach a UI process). Not a permission or approval decision, not an approval
flow, not task execution, not an event publisher, and not persistence. This
module emits nothing and mutates nothing.

Owner: C7.01. Belongs to ``agentx.core``; imports only the standard library
and sibling ``agentx.core`` contracts.
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast
from uuid import UUID

from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.events import Event, EventType, VerificationPayload
from agentx.core.ids import ProcedureId, TaskId
from agentx.core.task_state import can_transition, is_terminal
from agentx.core.tasks import JsonValue, Task, TaskPriority, TaskStatus

__all__ = [
    "CURRENT_UI_STATE_SCHEMA_VERSION",
    "UiExecutionLevel",
    "UiPermission",
    "UiRiskLevel",
    "UiStateApproval",
    "UiStateDeserializationError",
    "UiStateError",
    "UiStateExecution",
    "UiStatePlan",
    "UiStateProjectionError",
    "UiStateProtocolError",
    "UiStateSnapshot",
    "UiStateTask",
    "UiStateValidationError",
    "UiStateVerification",
    "UiVerificationStatus",
    "UnsupportedUiStateSchemaVersionError",
    "project_state_event",
]

#: Schema version of the serialized UI state protocol. Bumped only when the
#: serialized form changes incompatibly.
CURRENT_UI_STATE_SCHEMA_VERSION: Final[int] = 1

_MAX_OPERATION_LENGTH: Final[int] = 256
_MAX_DETAIL_LENGTH: Final[int] = 512
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")

_SNAPSHOT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "task",
        "execution",
        "plan",
        "verification",
        "approval",
        "error",
        "progress",
        "timestamp",
    }
)


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class UiStateProtocolError(ValueError):
    """Base error for violations of the C7.01 runtime-to-UI state protocol."""


class UnsupportedUiStateSchemaVersionError(UiStateProtocolError):
    """Raised when encoded UI state uses a schema version this code cannot read."""


class UiStateValidationError(UiStateProtocolError):
    """Raised when a UI state value violates the protocol at construction."""


class UiStateDeserializationError(UiStateProtocolError):
    """Raised when encoded UI state data cannot be decoded into a snapshot."""


class UiStateProjectionError(UiStateProtocolError):
    """Raised when a canonical event cannot be projected onto a snapshot."""


# --------------------------------------------------------------------------
# Closed protocol vocabularies
# --------------------------------------------------------------------------


class UiExecutionLevel(StrEnum):
    """Controlled UI-side mirror of the canonical execution-level vocabulary.

    Values exactly mirror ``agentx.cognition.router.ExecutionLevel`` (A2.07),
    ordered cheapest to most open-ended. ``agentx.core`` is the dependency
    leaf and cannot import ``agentx.cognition``, so the protocol carries the
    level as this closed string vocabulary rather than the enum object. The
    canonical A2.07 module remains the authority for execution-level
    semantics; this mirror exists only so the read-oriented protocol stays
    importable by every subsystem, including a future UI layer.
    """

    L0_CACHE = "L0_CACHE"
    L1_DIRECT = "L1_DIRECT"
    L2_COMPILED = "L2_COMPILED"
    L3_GUIDED = "L3_GUIDED"
    L4_PLANNED = "L4_PLANNED"
    L5_EXPLORATORY = "L5_EXPLORATORY"


class UiRiskLevel(StrEnum):
    """Controlled UI-side mirror of the canonical R0-R4 risk vocabulary.

    Values exactly mirror ``agentx.kernel.risk.RiskLevel``. The risk level in
    a snapshot is an *observation* of the risk classification attached to a
    pending request; it never classifies anything itself and never feeds a
    gate decision. ``agentx.core`` cannot import ``agentx.kernel``, so the
    values are mirrored here; the canonical kernel module remains the
    authority for risk semantics.
    """

    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


class UiPermission(StrEnum):
    """Controlled UI-side mirror of the canonical permission vocabulary.

    Values exactly mirror ``agentx.kernel.permissions.Permission``. In a
    snapshot the permission names the authority a pending request requires;
    it is never a grant and is never consulted as one. ``agentx.core`` cannot
    import ``agentx.kernel``, so the values are mirrored here; the canonical
    kernel module remains the authority for permission semantics.
    """

    READ = "READ"
    WRITE = "WRITE"
    EXECUTE = "EXECUTE"
    EXTERNAL_EFFECT = "EXTERNAL_EFFECT"
    DESTRUCTIVE = "DESTRUCTIVE"


class UiVerificationStatus(StrEnum):
    """Closed verification-observation vocabulary for the UI protocol.

    ``NOT_STARTED`` — no verification verdict has been produced for the
    current execution yet. ``PASSED`` / ``FAILED`` — a canonical
    verification verdict has been observed. The protocol *reports* verdicts;
    it never produces them (verdicts are manufactured only by the canonical
    verification boundary, A1.10/A2.05).
    """

    NOT_STARTED = "not_started"
    PASSED = "passed"
    FAILED = "failed"


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------


def _validate_operation(value: object, *, field_name: str) -> str | None:
    """Validate a bounded operation descriptor; hostile text stays inert data."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise UiStateValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > _MAX_OPERATION_LENGTH:
        raise UiStateValidationError(
            f"{field_name} must not exceed {_MAX_OPERATION_LENGTH} characters"
        )
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise UiStateValidationError(f"{field_name} must not contain control characters")
    return value


def _validate_detail(value: object, *, field_name: str) -> str | None:
    """Validate an optional bounded detail string."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise UiStateValidationError(f"{field_name} must be non-empty and trimmed when provided")
    if len(value) > _MAX_DETAIL_LENGTH:
        raise UiStateValidationError(
            f"{field_name} must not exceed {_MAX_DETAIL_LENGTH} characters"
        )
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise UiStateValidationError(f"{field_name} must not contain control characters")
    return value


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID, got {type(value).__name__}")
    if value.int == 0:
        raise UiStateValidationError(f"{field_name} must not be the nil UUID")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise UiStateValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_progress(value: object, *, field_name: str) -> float | None:
    """Validate a bounded deterministic progress fraction in [0.0, 1.0]."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} must be a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise UiStateValidationError(f"{field_name} must be finite")
    if not 0.0 <= number <= 1.0:
        raise UiStateValidationError(f"{field_name} must be within [0.0, 1.0]")
    return number


def _freeze_json(value: object, *, path: str, error_cls: type[UiStateProtocolError]) -> object:
    """Validate JSON compatibility and return an immutable defensive copy."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise error_cls(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise error_cls(f"{path} contains a non-string object key")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}", error_cls=error_cls)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]", error_cls=error_cls)
            for index, item in enumerate(value)
        )
    raise error_cls(f"{path} contains non-JSON-compatible value of type {type(value).__name__}")


def _to_json_value(value: object, *, path: str) -> JsonValue:
    """Convert an internally frozen JSON value back to JSON-compatible primitives."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):  # defensive; construction already rejects this
            raise UiStateValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):  # defensive; construction already rejects this
                raise UiStateValidationError(f"{path} contains a non-string object key")
            result[key] = _to_json_value(item, path=f"{path}.{key}")
        return result
    if isinstance(value, tuple | list):
        return [_to_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise UiStateValidationError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _to_json_object(value: Mapping[str, object], *, path: str) -> dict[str, JsonValue]:
    converted = _to_json_value(value, path=path)
    if not isinstance(converted, dict):  # pragma: no cover - mapping always converts to dict
        raise AssertionError("JSON object conversion produced a non-dict")
    return converted


def _require_exact_keys(
    raw: Mapping[str, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    context: str,
) -> None:
    """Fail closed on missing or unknown fields — the anti-smuggling rule.

    Unknown fields are rejected (never ignored) so that authority-shaped
    fields smuggled in by a UI consumer cannot ride inside a returned state
    object.
    """
    actual = set(raw)
    missing = required - actual
    unknown = actual - required - optional
    if missing:
        raise UiStateDeserializationError(f"{context} missing required fields: {sorted(missing)}")
    if unknown:
        raise UiStateDeserializationError(f"{context} contains unknown fields: {sorted(unknown)}")


def _as_string_key_mapping(value: object, *, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise UiStateDeserializationError(f"{path} must be a JSON object")
    copied: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise UiStateDeserializationError(f"{path} contains a non-string object key")
        copied[key] = item
    return copied


def _parse_task_id(value: object, *, field_name: str) -> TaskId:
    if not isinstance(value, str):
        raise UiStateDeserializationError(f"{field_name} must be a TaskId string")
    try:
        return TaskId.parse(value)
    except ValueError as exc:
        raise UiStateDeserializationError(f"{field_name} is not a valid TaskId: {value!r}") from exc


def _parse_procedure_id(value: object, *, field_name: str) -> ProcedureId:
    if not isinstance(value, str):
        raise UiStateDeserializationError(f"{field_name} must be a ProcedureId string")
    try:
        return ProcedureId.parse(value)
    except ValueError as exc:
        raise UiStateDeserializationError(
            f"{field_name} is not a valid ProcedureId: {value!r}"
        ) from exc


def _parse_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, str):
        raise UiStateDeserializationError(f"{field_name} must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise UiStateDeserializationError(
            f"{field_name} must be a valid UUID string: {value!r}"
        ) from exc
    if parsed.int == 0:
        raise UiStateDeserializationError(f"{field_name} must not be the nil UUID")
    return parsed


def _parse_revision(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise UiStateDeserializationError(f"{field_name} must be an integer or null")
    if value < 1:
        raise UiStateDeserializationError(f"{field_name} must be a positive integer")
    return value


def _parse_enum[T: StrEnum](enum_type: type[T], value: object, *, field_name: str) -> T:
    if not isinstance(value, str):
        raise UiStateDeserializationError(f"{field_name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise UiStateDeserializationError(
            f"{field_name} must be one of {[member.value for member in enum_type]}; got {value!r}"
        ) from exc


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise UiStateDeserializationError(f"{field_name} must be an ISO-8601 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise UiStateDeserializationError(
            f"{field_name} must be a valid ISO-8601 datetime: {value!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UiStateDeserializationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _parse_progress(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise UiStateDeserializationError("progress must be a number or null")
    number = float(value)
    if not math.isfinite(number):
        raise UiStateDeserializationError("progress must be finite")
    if not 0.0 <= number <= 1.0:
        raise UiStateDeserializationError("progress must be within [0.0, 1.0]")
    return number


# --------------------------------------------------------------------------
# Snapshot sections
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class UiStateTask:
    """Task identity and lifecycle state, reusing the canonical Task schema."""

    task_id: TaskId
    status: TaskStatus
    priority: TaskPriority

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, TaskId):
            raise TypeError(f"task_id must be a TaskId, got {type(self.task_id).__name__}")
        if not isinstance(self.status, TaskStatus):
            raise TypeError(f"status must be a TaskStatus, got {type(self.status).__name__}")
        if not isinstance(self.priority, TaskPriority):
            raise TypeError(f"priority must be a TaskPriority, got {type(self.priority).__name__}")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "task_id": self.task_id.to_str(),
            "status": self.status.value,
            "priority": self.priority.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> UiStateTask:
        _require_exact_keys(
            raw,
            required=frozenset({"task_id", "status", "priority"}),
            context="task",
        )
        return cls(
            task_id=_parse_task_id(raw["task_id"], field_name="task.task_id"),
            status=_parse_enum(TaskStatus, raw["status"], field_name="task.status"),
            priority=_parse_enum(TaskPriority, raw["priority"], field_name="task.priority"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class UiStateExecution:
    """Current execution observation: strategy class and operation in flight.

    ``level`` mirrors the canonical A2.07 execution hierarchy; ``operation``
    is the bounded descriptor of the operation currently in flight (the same
    descriptive concept the kernel gate uses). Both are observations only:
    neither selects a strategy nor authorizes an operation.
    """

    level: UiExecutionLevel | None
    operation: str | None

    def __post_init__(self) -> None:
        if self.level is not None and not isinstance(self.level, UiExecutionLevel):
            raise TypeError(f"level must be a UiExecutionLevel, got {type(self.level).__name__}")
        object.__setattr__(
            self, "operation", _validate_operation(self.operation, field_name="operation")
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "level": None if self.level is None else self.level.value,
            "operation": self.operation,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> UiStateExecution:
        _require_exact_keys(
            raw,
            required=frozenset({"level", "operation"}),
            context="execution",
        )
        level_raw = raw["level"]
        level = (
            None
            if level_raw is None
            else _parse_enum(UiExecutionLevel, level_raw, field_name="execution.level")
        )
        operation_raw = raw["operation"]
        if operation_raw is not None and not isinstance(operation_raw, str):
            raise UiStateDeserializationError("execution.operation must be a string or null")
        return cls(level=level, operation=operation_raw)


@dataclass(frozen=True, slots=True, kw_only=True)
class UiStatePlan:
    """Plan/procedure reference where available.

    Reuses the canonical :class:`~agentx.core.ids.ProcedureId` and the
    canonical (procedure_id, revision) record identity from C2.03. A present
    reference is data only: it does not activate, authorize, or execute the
    referenced procedure.
    """

    procedure_id: ProcedureId
    revision: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, ProcedureId):
            raise TypeError(
                f"procedure_id must be a ProcedureId, got {type(self.procedure_id).__name__}"
            )
        if self.revision is not None:
            if isinstance(self.revision, bool) or not isinstance(self.revision, int):
                raise TypeError(f"revision must be an integer, got {type(self.revision).__name__}")
            if self.revision < 1:
                raise UiStateValidationError("revision must be a positive integer")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "procedure_id": self.procedure_id.to_str(),
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> UiStatePlan:
        _require_exact_keys(
            raw,
            required=frozenset({"procedure_id", "revision"}),
            context="plan",
        )
        return cls(
            procedure_id=_parse_procedure_id(raw["procedure_id"], field_name="plan.procedure_id"),
            revision=_parse_revision(raw["revision"], field_name="plan.revision"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class UiStateVerification:
    """Verification status observation (reports verdicts, never produces them)."""

    status: UiVerificationStatus
    detail: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, UiVerificationStatus):
            raise TypeError(
                f"status must be a UiVerificationStatus, got {type(self.status).__name__}"
            )
        object.__setattr__(self, "detail", _validate_detail(self.detail, field_name="detail"))

    def to_dict(self) -> dict[str, JsonValue]:
        return {"status": self.status.value, "detail": self.detail}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> UiStateVerification:
        _require_exact_keys(
            raw,
            required=frozenset({"status", "detail"}),
            context="verification",
        )
        detail_raw = raw["detail"]
        if detail_raw is not None and not isinstance(detail_raw, str):
            raise UiStateDeserializationError("verification.detail must be a string or null")
        return cls(
            status=_parse_enum(
                UiVerificationStatus, raw["status"], field_name="verification.status"
            ),
            detail=detail_raw,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class UiStateApproval:
    """Pending approval/risk state, where an explicit human decision is owed.

    Observes one pending request: its opaque request identity (the same
    identity A6.08 approval evidence uses), the bounded operation descriptor,
    the required permission (mirrored canonical vocabulary), and the
    effective risk level (mirrored canonical vocabulary). This section is a
    *view* of the pending request, not the request itself and not a decision:
    it cannot approve, deny, or bind an approval.
    """

    request_id: UUID
    operation: str
    permission: UiPermission
    risk_level: UiRiskLevel

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "request_id", _validate_uuid(self.request_id, field_name="request_id")
        )
        if self.operation is None:
            raise TypeError("operation must be a string, got NoneType")
        object.__setattr__(
            self, "operation", _validate_operation(self.operation, field_name="operation")
        )
        if not isinstance(self.permission, UiPermission):
            raise TypeError(
                f"permission must be a UiPermission, got {type(self.permission).__name__}"
            )
        if not isinstance(self.risk_level, UiRiskLevel):
            raise TypeError(
                f"risk_level must be a UiRiskLevel, got {type(self.risk_level).__name__}"
            )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "request_id": str(self.request_id),
            "operation": self.operation,
            "permission": self.permission.value,
            "risk_level": self.risk_level.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> UiStateApproval:
        _require_exact_keys(
            raw,
            required=frozenset({"request_id", "operation", "permission", "risk_level"}),
            context="approval",
        )
        operation_raw = raw["operation"]
        if not isinstance(operation_raw, str):
            raise UiStateDeserializationError("approval.operation must be a string")
        return cls(
            request_id=_parse_uuid(raw["request_id"], field_name="approval.request_id"),
            operation=operation_raw,
            permission=_parse_enum(
                UiPermission, raw["permission"], field_name="approval.permission"
            ),
            risk_level=_parse_enum(
                UiRiskLevel, raw["risk_level"], field_name="approval.risk_level"
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class UiStateError:
    """Structured error observation, reusing the canonical error model.

    Carries exactly the canonical :class:`~agentx.core.errors.AgentXError`
    fields (code, message, category, retryability) plus an optional frozen
    JSON-compatible detail mapping. The wrapped ``cause`` exception is never
    carried, mirroring the canonical serialization policy.
    """

    code: str
    message: str
    category: ErrorCategory
    retryability: Retryability
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.code, str):
            raise TypeError(f"code must be a string, got {type(self.code).__name__}")
        if not isinstance(self.message, str):
            raise TypeError(f"message must be a string, got {type(self.message).__name__}")
        if not isinstance(self.category, ErrorCategory):
            raise TypeError(
                f"category must be an ErrorCategory, got {type(self.category).__name__}"
            )
        if not isinstance(self.retryability, Retryability):
            raise TypeError(
                f"retryability must be a Retryability, got {type(self.retryability).__name__}"
            )
        if not isinstance(self.details, Mapping):
            raise TypeError(f"details must be a mapping, got {type(self.details).__name__}")
        object.__setattr__(
            self,
            "details",
            cast(
                "Mapping[str, object]",
                _freeze_json(self.details, path="details", error_cls=UiStateValidationError),
            ),
        )

    @classmethod
    def from_agentx_error(cls, error: AgentXError) -> UiStateError:
        """Convert a canonical structured error into its protocol observation.

        The conversion is a read: the input error is never modified, and a
        canonical error whose details are not JSON-compatible is rejected
        rather than silently sanitized.
        """
        if not isinstance(error, AgentXError):
            raise TypeError(f"error must be an AgentXError, got {type(error).__name__}")
        return cls(
            code=error.code,
            message=error.message,
            category=error.category,
            retryability=error.retryability,
            details=dict(error.details),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "code": self.code,
            "message": self.message,
            "category": self.category.value,
            "retryability": self.retryability.value,
        }
        if self.details:
            result["details"] = _to_json_object(self.details, path="details")
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> UiStateError:
        _require_exact_keys(
            raw,
            required=frozenset({"code", "message", "category", "retryability"}),
            optional=frozenset({"details"}),
            context="error",
        )
        code_raw = raw["code"]
        message_raw = raw["message"]
        if not isinstance(code_raw, str):
            raise UiStateDeserializationError("error.code must be a string")
        if not isinstance(message_raw, str):
            raise UiStateDeserializationError("error.message must be a string")
        details_raw = raw.get("details", {})
        details = _as_string_key_mapping(details_raw, path="details")
        # Pre-freeze on the wire path so malformed detail values (e.g. NaN
        # literals) report as deserialization errors, not construction errors.
        frozen_details = cast(
            "Mapping[str, object]",
            _freeze_json(details, path="details", error_cls=UiStateDeserializationError),
        )
        return cls(
            code=code_raw,
            message=message_raw,
            category=_parse_enum(ErrorCategory, raw["category"], field_name="error.category"),
            retryability=_parse_enum(
                Retryability, raw["retryability"], field_name="error.retryability"
            ),
            details=frozen_details,
        )


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class UiStateSnapshot:
    """Immutable, versioned observation of one task's runtime state.

    The snapshot is a *read* of runtime state: constructing, serializing, or
    deserializing it grants nothing, mutates nothing, and transitions no
    Task. Optional sections (``plan``, ``approval``, ``error``) and
    ``progress`` are explicit ``null`` on the wire when absent.
    """

    schema_version: int
    task: UiStateTask
    execution: UiStateExecution
    verification: UiStateVerification
    timestamp: datetime
    plan: UiStatePlan | None = None
    approval: UiStateApproval | None = None
    error: UiStateError | None = None
    progress: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int):
            raise TypeError(
                f"schema_version must be an integer, got {type(self.schema_version).__name__}"
            )
        if self.schema_version != CURRENT_UI_STATE_SCHEMA_VERSION:
            raise UnsupportedUiStateSchemaVersionError(
                f"unsupported ui state schema version {self.schema_version}; "
                f"supported version is {CURRENT_UI_STATE_SCHEMA_VERSION}"
            )
        if not isinstance(self.task, UiStateTask):
            raise TypeError(f"task must be a UiStateTask, got {type(self.task).__name__}")
        if not isinstance(self.execution, UiStateExecution):
            raise TypeError(
                f"execution must be a UiStateExecution, got {type(self.execution).__name__}"
            )
        if self.plan is not None and not isinstance(self.plan, UiStatePlan):
            raise TypeError(f"plan must be a UiStatePlan, got {type(self.plan).__name__}")
        if not isinstance(self.verification, UiStateVerification):
            raise TypeError(
                f"verification must be a UiStateVerification, got "
                f"{type(self.verification).__name__}"
            )
        if self.approval is not None and not isinstance(self.approval, UiStateApproval):
            raise TypeError(
                f"approval must be a UiStateApproval, got {type(self.approval).__name__}"
            )
        if self.error is not None and not isinstance(self.error, UiStateError):
            raise TypeError(f"error must be a UiStateError, got {type(self.error).__name__}")
        object.__setattr__(
            self, "timestamp", _validate_timestamp(self.timestamp, field_name="timestamp")
        )
        object.__setattr__(
            self, "progress", _validate_progress(self.progress, field_name="progress")
        )

    @classmethod
    def from_task(cls, task: Task, *, timestamp: datetime) -> UiStateSnapshot:
        """Build the initial observation of a canonical Task.

        This is the runtime-side starting point: the task section reuses the
        canonical Task's identity, status, and priority, and every other
        section starts empty. The caller supplies the observation timestamp
        explicitly so the result is deterministic.
        """
        if not isinstance(task, Task):
            raise TypeError(f"task must be a Task, got {type(task).__name__}")
        return cls(
            schema_version=CURRENT_UI_STATE_SCHEMA_VERSION,
            task=UiStateTask(task_id=task.task_id, status=task.status, priority=task.priority),
            execution=UiStateExecution(level=None, operation=None),
            verification=UiStateVerification(status=UiVerificationStatus.NOT_STARTED, detail=None),
            timestamp=timestamp,
        )

    @property
    def is_terminal(self) -> bool:
        """Whether the observed task status is terminal (reuses A1.06)."""
        return is_terminal(self.task.status)

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the canonical JSON-compatible schema-v1 representation."""
        return {
            "schema_version": self.schema_version,
            "task": self.task.to_dict(),
            "execution": self.execution.to_dict(),
            "plan": None if self.plan is None else self.plan.to_dict(),
            "verification": self.verification.to_dict(),
            "approval": None if self.approval is None else self.approval.to_dict(),
            "error": None if self.error is None else self.error.to_dict(),
            "progress": self.progress,
            "timestamp": _format_timestamp(self.timestamp),
        }

    def to_json(self) -> str:
        """Serialize to deterministic UTF-8-safe JSON text."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> UiStateSnapshot:
        """Validate and deserialize a canonical JSON-compatible snapshot object.

        Unknown fields at any level are rejected fail-closed (never ignored),
        so a state object returned by a UI consumer can never smuggle
        authority-shaped fields into the protocol.
        """
        if "schema_version" not in raw:
            raise UiStateDeserializationError(
                "ui state snapshot missing required field: schema_version"
            )
        version = raw["schema_version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise UiStateDeserializationError("schema_version must be an integer")
        if version != CURRENT_UI_STATE_SCHEMA_VERSION:
            raise UnsupportedUiStateSchemaVersionError(
                f"unsupported ui state schema version {version}; "
                f"supported version is {CURRENT_UI_STATE_SCHEMA_VERSION}"
            )

        _require_exact_keys(raw, required=_SNAPSHOT_FIELDS, context="ui state snapshot")

        plan_raw = raw["plan"]
        if plan_raw is not None and not isinstance(plan_raw, Mapping):
            raise UiStateDeserializationError("plan must be a JSON object or null")
        approval_raw = raw["approval"]
        if approval_raw is not None and not isinstance(approval_raw, Mapping):
            raise UiStateDeserializationError("approval must be a JSON object or null")
        error_raw = raw["error"]
        if error_raw is not None and not isinstance(error_raw, Mapping):
            raise UiStateDeserializationError("error must be a JSON object or null")

        return cls(
            schema_version=version,
            task=UiStateTask.from_dict(_as_string_key_mapping(raw["task"], path="task")),
            execution=UiStateExecution.from_dict(
                _as_string_key_mapping(raw["execution"], path="execution")
            ),
            plan=None
            if plan_raw is None
            else UiStatePlan.from_dict(_as_string_key_mapping(plan_raw, path="plan")),
            verification=UiStateVerification.from_dict(
                _as_string_key_mapping(raw["verification"], path="verification")
            ),
            approval=None
            if approval_raw is None
            else UiStateApproval.from_dict(_as_string_key_mapping(approval_raw, path="approval")),
            error=None
            if error_raw is None
            else UiStateError.from_dict(_as_string_key_mapping(error_raw, path="error")),
            progress=_parse_progress(raw["progress"]),
            timestamp=_parse_timestamp(raw["timestamp"], field_name="timestamp"),
        )

    @classmethod
    def from_json(cls, text: str) -> UiStateSnapshot:
        """Deserialize JSON text without dynamic imports or arbitrary object construction."""
        if not isinstance(text, str):
            raise TypeError(f"ui state JSON must be a string, got {type(text).__name__}")
        try:
            decoded: object = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UiStateDeserializationError("ui state JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise UiStateDeserializationError("ui state JSON root must be an object")
        return cls.from_dict(_as_string_key_mapping(decoded, path="ui state snapshot"))


# --------------------------------------------------------------------------
# Event conversion (projection)
# --------------------------------------------------------------------------

#: Closed map from canonical task-lifecycle event kinds to the status each one
#: establishes. Any canonical event kind not listed here is an explicit no-op
#: for projection (it carries no field this protocol observes).
_PROJECTABLE_TASK_EVENTS: Final[Mapping[EventType, TaskStatus]] = MappingProxyType(
    {
        EventType.TASK_STARTED: TaskStatus.RUNNING,
        EventType.TASK_COMPLETED: TaskStatus.SUCCEEDED,
        EventType.TASK_FAILED: TaskStatus.FAILED,
    }
)


def _bind_event_task(snapshot: UiStateSnapshot, event: Event) -> TaskId:
    """Fail closed unless the event is bound to the snapshot's exact task."""
    raw = event.task_id
    if raw is None:
        raise UiStateProjectionError(
            "event carries no task_id and cannot be bound to a task snapshot"
        )
    try:
        event_task_id = TaskId.parse(raw)
    except ValueError as exc:
        raise UiStateProjectionError(f"event task_id is not a valid TaskId: {raw!r}") from exc
    if event_task_id != snapshot.task.task_id:
        raise UiStateProjectionError("event is bound to a different task than the snapshot")
    return event_task_id


def project_state_event(snapshot: UiStateSnapshot, event: Event) -> UiStateSnapshot:
    """Project one canonical Event onto a snapshot, returning a new snapshot.

    This is the protocol's event conversion: a pure, deterministic function
    from ``(snapshot, canonical Event)`` to a new
    :class:`UiStateSnapshot`. It never mutates its inputs, never executes
    anything, and never grants anything; it only reports what the event says
    about the observed task.

    Conversion rules (closed and deterministic):

    - The event must be bound to the snapshot's exact task
      (``event.task_id`` parses to the snapshot's
      :class:`~agentx.core.ids.TaskId`), otherwise
      :class:`UiStateProjectionError`.
    - ``task.started`` / ``task.completed`` / ``task.failed`` move the task
      status to running / succeeded / failed, and only when the canonical
      A1.06 transition matrix allows the move; an illegal move raises
      :class:`UiStateProjectionError` instead of being coerced.
    - ``verification.completed`` records the observed verdict
      (``PASSED`` / ``FAILED`` with the payload detail) and is applicable
      only while the observed task is running.
    - ``task.created`` cannot be projected onto an existing snapshot and
      raises :class:`UiStateProjectionError`: the initial snapshot is
      constructed from the Task itself (see :meth:`UiStateSnapshot.from_task`).
    - Every other canonical event kind is an explicit no-op and returns the
      snapshot unchanged.

    The resulting snapshot's ``timestamp`` is the event's canonical
    timestamp; every other field is carried through exactly.
    """
    if not isinstance(snapshot, UiStateSnapshot):
        raise TypeError(f"snapshot must be a UiStateSnapshot, got {type(snapshot).__name__}")
    if not isinstance(event, Event):
        raise TypeError(f"event must be an Event, got {type(event).__name__}")

    _bind_event_task(snapshot, event)

    if event.event_type is EventType.TASK_CREATED:
        raise UiStateProjectionError(
            "task.created cannot be projected onto an existing snapshot; construct "
            "the initial snapshot from the Task instead"
        )

    target_status: TaskStatus | None = _PROJECTABLE_TASK_EVENTS.get(event.event_type)
    if target_status is not None:
        current = snapshot.task.status
        if not can_transition(current, target_status):
            raise UiStateProjectionError(
                f"event {event.event_type.value} would move task status "
                f"{current.value} -> {target_status.value}, which is not a legal transition"
            )
        return dataclasses.replace(
            snapshot,
            task=UiStateTask(
                task_id=snapshot.task.task_id, status=target_status, priority=snapshot.task.priority
            ),
            timestamp=event.timestamp,
        )

    if event.event_type is EventType.VERIFICATION_COMPLETED:
        if snapshot.task.status is not TaskStatus.RUNNING:
            raise UiStateProjectionError(
                "verification.completed can only be projected while the task is running"
            )
        payload = event.payload
        if not isinstance(payload, VerificationPayload):  # defensive; Event enforces this
            raise UiStateProjectionError("verification.completed event has no verification payload")
        return dataclasses.replace(
            snapshot,
            verification=UiStateVerification(
                status=UiVerificationStatus.PASSED
                if payload.passed
                else UiVerificationStatus.FAILED,
                detail=payload.detail,
            ),
            timestamp=event.timestamp,
        )

    return snapshot
