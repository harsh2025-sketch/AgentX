"""Durable append-only storage for canonical C1.09 security-audit records (C2.04).

The :class:`AuditStore` persists the security-audit facts defined by the
canonical C1.09 contract, :class:`agentx.kernel.audit.SecurityAuditRecord`,
over the canonical :class:`agentx.infrastructure.persistence.SQLiteDatabase`
foundation. It owns durability only: it never replaces, reinterprets, or
extends the kernel contract, and it defines no audit vocabulary of its own.

Boundary note: the canonical architecture manifest forbids
``agentx.infrastructure`` from importing ``agentx.kernel``. To persist the
C1.09 record directly anyway, this module consumes it through the smallest
possible read-only structural view protocol (:class:`AuditRecordView`) — a
persistence seam, not a competing contract. The one canonical definition of
``SecurityAuditRecord``, ``AuditContext``, and ``AuditOutcome`` remains
``agentx.kernel.audit``; kernel records satisfy this view structurally.
Because the store never imports the kernel, it cannot and does not re-import
stored text back into kernel enum types: reads return inert
:class:`PersistedAuditRecord` values whose controlled fields are plain
strings. Re-materialization into canonical types is kernel-side composition
work, outside storage.

Semantics (C2.04 contract):

    - Append-only durable log. Every append allocates the next SQLite-assigned
      sequence; stored rows are never updated, replaced, merged, or deleted by
      this API.
    - Duplicate identity fails explicitly: re-appending an existing
      ``audit_id`` raises :class:`DuplicateAuditRecordError` and writes
      nothing — even when the incoming record is byte-identical. Duplicate
      *content* under distinct identities is preserved as history; storage
      never dedupes evidence.
    - Ordering is the durable sequence, total and restart-stable. It is
      independent of the record's own timestamp.
    - Corruption fails closed: a row whose persisted document cannot be
      decoded to the exact canonical representation, or whose promoted
      columns disagree with it, raises :class:`CorruptAuditRecordError`. No
      silent skipping, repairing, or partial returns.

History, not authority: reading an audit entry can never grant a Permission,
create an ``AuthorityContext``, bypass the Action Gate, lower effective risk,
enlarge a resource budget, clear an EmergencyStop, execute a Capability,
mutate a Task, or mark anything verified. Every historical field — outcome,
permission, risk, actor, target, operation, reason — is inert stored text.
A stored ``ALLOW`` is not a decision, a stored ``DESTRUCTIVE`` is not a
grant, and a stored ``R0`` is not a classification.

This store is deliberately NOT the EventJournal: it stores canonical
security-audit history, not canonical Events, never replays or publishes,
and shares no table or wiring with the journal beyond the shared database
file. There is no automatic EventBus integration of any kind.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

from agentx.core.ids import TaskId
from agentx.infrastructure.persistence import (
    PersistenceError,
    SQLiteDatabase,
    TransactionError,
    TransactionStateError,
)

_AUDIT_TABLE: Final = "agentx_audit_log"
_AUDIT_PERSISTENCE_SCHEMA_VERSION: Final[int] = 1
_MAX_AUDIT_TEXT_LENGTH: Final[int] = 4_096

_AUDIT_DOCUMENT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "audit_id",
        "timestamp",
        "operation",
        "outcome",
        "reason",
        "task_id",
        "correlation_id",
        "risk_level",
        "context",
    }
)
_AUDIT_CONTEXT_FIELDS: Final[frozenset[str]] = frozenset(
    {"actor", "target", "permission", "secret_ref"}
)


class AuditStoreError(PersistenceError):
    """Base error for durable audit-store operations."""


class DuplicateAuditRecordError(AuditStoreError):
    """Raised when an ``audit_id`` already exists in the append-only log."""


class AuditRecordValidationError(AuditStoreError, ValueError):
    """Raised when an audit document or supplied record view is not valid."""


class AuditStoreStorageError(AuditStoreError):
    """Raised when SQLite cannot complete an audit-store statement."""


class CorruptAuditRecordError(AuditStoreError):
    """Raised when a persisted audit row fails closed against its own storage."""

    def __init__(self, *, sequence: int, audit_id: str) -> None:
        self.sequence = sequence
        self.audit_id = audit_id
        super().__init__(f"Audit row sequence {sequence} for audit_id {audit_id!r} is corrupt")


# ---------------------------------------------------------------------------
# Structural view of the canonical C1.09 record (persistence seam only).
#
# These protocols mirror the PUBLIC attributes of the kernel-owned
# SecurityAuditRecord/AuditContext/SecretRef contracts so this module can
# persist them without importing agentx.kernel. They add nothing and define no
# audit semantics; kernel records satisfy them structurally.
# ---------------------------------------------------------------------------


@runtime_checkable
class AuditEnumValueView(Protocol):
    """An enum member exposing its canonical string value."""

    @property
    def value(self) -> str: ...


@runtime_checkable
class AuditSecretRefView(Protocol):
    """An opaque secret reference exposing only its identifier."""

    @property
    def identifier(self) -> str: ...


@runtime_checkable
class AuditContextView(Protocol):
    """Structural view of ``agentx.kernel.audit.AuditContext``."""

    @property
    def actor(self) -> str | None: ...

    @property
    def target(self) -> str | None: ...

    @property
    def permission(self) -> AuditEnumValueView | None: ...

    @property
    def secret_ref(self) -> AuditSecretRefView | None: ...


@runtime_checkable
class AuditRecordView(Protocol):
    """Structural view of ``agentx.kernel.audit.SecurityAuditRecord``."""

    @property
    def audit_id(self) -> UUID: ...

    @property
    def timestamp(self) -> datetime: ...

    @property
    def operation(self) -> str: ...

    @property
    def outcome(self) -> AuditEnumValueView: ...

    @property
    def reason(self) -> str: ...

    @property
    def task_id(self) -> TaskId | None: ...

    @property
    def correlation_id(self) -> UUID | None: ...

    @property
    def risk_level(self) -> AuditEnumValueView | None: ...

    @property
    def context(self) -> AuditContextView | None: ...


# ---------------------------------------------------------------------------
# Smallest persistence representation (store-owned canonical JSON, schema v1).
# ---------------------------------------------------------------------------


def _require_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise AuditRecordValidationError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise AuditRecordValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > _MAX_AUDIT_TEXT_LENGTH:
        raise AuditRecordValidationError(
            f"{field_name} must be at most {_MAX_AUDIT_TEXT_LENGTH} characters"
        )
    return value


def _require_optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name=field_name)


def _require_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise AuditRecordValidationError(f"{field_name} must be a UUID")
    if value.int == 0:
        raise AuditRecordValidationError(f"{field_name} must not be the nil UUID")
    return value


def _require_optional_uuid(value: object, *, field_name: str) -> UUID | None:
    if value is None:
        return None
    return _require_uuid(value, field_name=field_name)


def _require_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise AuditRecordValidationError(f"{field_name} must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise AuditRecordValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise AuditRecordValidationError(f"{field_name} must be an ISO-8601 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AuditRecordValidationError(f"{field_name} must be a valid ISO-8601 datetime") from exc
    return _require_timestamp(parsed, field_name=field_name)


def _require_enum_text(value: object, *, field_name: str) -> str:
    """Extract a canonical controlled-vocabulary string from an Enum member.

    Requiring an actual Enum instance keeps the vocabulary single-sourced in
    the kernel: the store stores ``member.value`` verbatim and never
    interprets it.
    """
    if not isinstance(value, Enum):
        raise AuditRecordValidationError(
            f"{field_name} must be a canonical kernel Enum member; the audit "
            "store does not accept free-form authority claims"
        )
    return _require_text(value.value, field_name=f"{field_name}.value")


def _parse_task_id(value: object, *, field_name: str) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AuditRecordValidationError(f"{field_name} must be a string or null")
    try:
        return TaskId.parse(value)
    except ValueError as exc:
        raise AuditRecordValidationError(
            f"{field_name} must be a valid non-nil UUID string: {value!r}"
        ) from exc


def _parse_uuid_text(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, str):
        raise AuditRecordValidationError(f"{field_name} must be a string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise AuditRecordValidationError(f"{field_name} is not a valid UUID: {value!r}") from exc
    return _require_uuid(parsed, field_name=field_name)


@dataclass(frozen=True, slots=True, kw_only=True)
class PersistedAuditContext:
    """Inert persisted view of one canonical AuditContext (all fields are strings)."""

    actor: str | None = None
    target: str | None = None
    permission: str | None = None
    secret_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor", _require_optional_text(self.actor, field_name="actor"))
        object.__setattr__(self, "target", _require_optional_text(self.target, field_name="target"))
        object.__setattr__(
            self, "permission", _require_optional_text(self.permission, field_name="permission")
        )
        object.__setattr__(
            self, "secret_ref", _require_optional_text(self.secret_ref, field_name="secret_ref")
        )
        if (
            self.actor is None
            and self.target is None
            and self.permission is None
            and self.secret_ref is None
        ):
            raise AuditRecordValidationError(
                "audit context must carry at least one explicit context field"
            )

    def to_document(self) -> dict[str, str | None]:
        return {
            "actor": self.actor,
            "target": self.target,
            "permission": self.permission,
            "secret_ref": self.secret_ref,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class PersistedAuditRecord:
    """Smallest canonical persistence representation of one C1.09 audit record.

    Every controlled field (``outcome``, ``permission``, ``risk_level``,
    ``secret_ref``) is stored and returned as inert descriptive text. This is
    history and evidence, never an authority object: no field here can grant,
    authorize, classify, verify, or execute anything.
    """

    audit_id: UUID
    timestamp: datetime
    operation: str
    outcome: str
    reason: str
    task_id: TaskId | None = None
    correlation_id: UUID | None = None
    risk_level: str | None = None
    context: PersistedAuditContext | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "audit_id", _require_uuid(self.audit_id, field_name="audit_id"))
        object.__setattr__(
            self, "timestamp", _require_timestamp(self.timestamp, field_name="timestamp")
        )
        object.__setattr__(self, "operation", _require_text(self.operation, field_name="operation"))
        object.__setattr__(self, "outcome", _require_text(self.outcome, field_name="outcome"))
        object.__setattr__(self, "reason", _require_text(self.reason, field_name="reason"))
        if self.task_id is not None and not isinstance(self.task_id, TaskId):
            raise AuditRecordValidationError("task_id must be a TaskId or None")
        object.__setattr__(
            self,
            "correlation_id",
            _require_optional_uuid(self.correlation_id, field_name="correlation_id"),
        )
        object.__setattr__(
            self, "risk_level", _require_optional_text(self.risk_level, field_name="risk_level")
        )
        if self.context is not None and not isinstance(self.context, PersistedAuditContext):
            raise AuditRecordValidationError("context must be a PersistedAuditContext or None")

    def to_document(self) -> dict[str, object]:
        """Return the canonical JSON-compatible persistence document."""
        return {
            "schema_version": _AUDIT_PERSISTENCE_SCHEMA_VERSION,
            "audit_id": str(self.audit_id),
            "timestamp": _format_timestamp(self.timestamp),
            "operation": self.operation,
            "outcome": self.outcome,
            "reason": self.reason,
            "task_id": None if self.task_id is None else self.task_id.to_str(),
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
            "risk_level": self.risk_level,
            "context": None if self.context is None else self.context.to_document(),
        }

    def to_json(self) -> str:
        """Serialize to deterministic canonical JSON text."""
        return json.dumps(
            self.to_document(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _persist_from_view(record: AuditRecordView) -> PersistedAuditRecord:
    """Convert one canonical C1.09 record into the persistence representation.

    Field extraction is strictly validated; controlled vocabulary comes from
    the kernel's own Enum ``.value`` strings, and secret material is
    excluded by design because only ``SecretRef.identifier`` is ever read.
    """
    context_raw: object = record.context
    context: PersistedAuditContext | None
    if context_raw is None:
        context = None
    else:
        actor_raw: object = getattr(context_raw, "actor", None)
        target_raw: object = getattr(context_raw, "target", None)
        permission_raw: object = getattr(context_raw, "permission", None)
        secret_ref_raw: object = getattr(context_raw, "secret_ref", None)
        permission: str | None
        if permission_raw is None:
            permission = None
        else:
            permission = _require_enum_text(permission_raw, field_name="context.permission")
        secret_ref: str | None
        if secret_ref_raw is None:
            secret_ref = None
        else:
            identifier_raw: object = getattr(secret_ref_raw, "identifier", None)
            if identifier_raw is None:
                raise AuditRecordValidationError(
                    "context.secret_ref must expose an identifier string"
                )
            secret_ref = _require_text(identifier_raw, field_name="context.secret_ref")
        context = PersistedAuditContext(
            actor=_require_optional_text(actor_raw, field_name="context.actor"),
            target=_require_optional_text(target_raw, field_name="context.target"),
            permission=permission,
            secret_ref=secret_ref,
        )

    task_id_raw: object = record.task_id
    if task_id_raw is not None and not isinstance(task_id_raw, TaskId):
        raise AuditRecordValidationError("task_id must be a TaskId or None")
    risk_level_raw: object = record.risk_level
    risk_level: str | None = (
        None
        if risk_level_raw is None
        else _require_enum_text(risk_level_raw, field_name="risk_level")
    )

    return PersistedAuditRecord(
        audit_id=_require_uuid(record.audit_id, field_name="audit_id"),
        timestamp=_require_timestamp(record.timestamp, field_name="timestamp"),
        operation=_require_text(record.operation, field_name="operation"),
        outcome=_require_enum_text(record.outcome, field_name="outcome"),
        reason=_require_text(record.reason, field_name="reason"),
        task_id=task_id_raw,
        correlation_id=_require_optional_uuid(record.correlation_id, field_name="correlation_id"),
        risk_level=risk_level,
        context=context,
    )


def _decode_document(text: str) -> PersistedAuditRecord:
    """Decode and fully re-validate one persisted canonical audit document.

    Decoding is closed-world: the exact schema-v1 field set is required,
    identifiers must be canonical non-nil UUIDs, timestamps must round-trip,
    and the final canonical byte-identity check rejects any byte that does
    not reproduce from the decoded structure.
    """
    if not isinstance(text, str):
        raise AuditRecordValidationError("audit document must be a string")
    try:
        decoded: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AuditRecordValidationError("audit document JSON is malformed") from exc
    if not isinstance(decoded, Mapping):
        raise AuditRecordValidationError("audit document JSON root must be an object")
    raw: dict[str, object] = {}
    for key, item in decoded.items():
        if not isinstance(key, str):
            raise AuditRecordValidationError("audit document contains a non-string object key")
        raw[key] = item

    version = raw.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise AuditRecordValidationError("schema_version must be an integer")
    if version != _AUDIT_PERSISTENCE_SCHEMA_VERSION:
        raise AuditRecordValidationError(
            f"unsupported audit persistence schema version {version}; "
            f"supported version is {_AUDIT_PERSISTENCE_SCHEMA_VERSION}"
        )
    if set(raw) != _AUDIT_DOCUMENT_FIELDS:
        expected = sorted(_AUDIT_DOCUMENT_FIELDS)
        raise AuditRecordValidationError(
            f"audit document must contain exactly the canonical fields {expected}"
        )

    context_raw = raw["context"]
    context: PersistedAuditContext | None
    if context_raw is None:
        context = None
    elif isinstance(context_raw, Mapping):
        context_items: dict[str, object] = {}
        for key, item in context_raw.items():
            if not isinstance(key, str):
                raise AuditRecordValidationError("audit context contains a non-string object key")
            context_items[key] = item
        if set(context_items) != _AUDIT_CONTEXT_FIELDS:
            raise AuditRecordValidationError(
                "audit context must contain exactly the canonical fields "
                f"{sorted(_AUDIT_CONTEXT_FIELDS)}"
            )
        context = PersistedAuditContext(
            actor=_require_optional_text(context_items["actor"], field_name="context.actor"),
            target=_require_optional_text(context_items["target"], field_name="context.target"),
            permission=_require_optional_text(
                context_items["permission"], field_name="context.permission"
            ),
            secret_ref=_require_optional_text(
                context_items["secret_ref"], field_name="context.secret_ref"
            ),
        )
    else:
        raise AuditRecordValidationError("audit context must be a JSON object or null")

    record = PersistedAuditRecord(
        audit_id=_parse_uuid_text(raw["audit_id"], field_name="audit_id"),
        timestamp=_parse_timestamp(raw["timestamp"], field_name="timestamp"),
        operation=_require_text(raw["operation"], field_name="operation"),
        outcome=_require_text(raw["outcome"], field_name="outcome"),
        reason=_require_text(raw["reason"], field_name="reason"),
        task_id=_parse_task_id(raw["task_id"], field_name="task_id"),
        correlation_id=(
            None
            if raw["correlation_id"] is None
            else _parse_uuid_text(raw["correlation_id"], field_name="correlation_id")
        ),
        risk_level=_require_optional_text(raw["risk_level"], field_name="risk_level"),
        context=context,
    )
    if record.to_json() != text:
        raise AuditRecordValidationError(
            "audit document is not canonical (stored bytes do not reproduce from their contents)"
        )
    return record


@contextmanager
def _write_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run one serialized write transaction with commit-or-rollback semantics.

    Same rationale as the durable sibling stores: a deferred read-then-write
    upgrade can fail with a stale-snapshot ``SQLITE_BUSY`` under the WAL
    journal instead of waiting on the busy timeout, so the write lock is
    taken up front with ``BEGIN IMMEDIATE``. Migration metadata application
    in ``agentx.infrastructure.persistence`` uses the same pattern.
    """
    if connection.in_transaction:
        raise TransactionStateError("Nested SQLite transactions are not supported")

    try:
        connection.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as exc:
        raise TransactionError(f"Unable to begin SQLite write transaction: {exc}") from exc

    try:
        yield connection
    except BaseException:
        try:
            connection.rollback()
        except sqlite3.Error as rollback_error:
            raise TransactionError(
                f"SQLite transaction failed and rollback also failed: {rollback_error}"
            ) from rollback_error
        raise
    else:
        try:
            connection.commit()
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except sqlite3.Error as rollback_error:
                raise TransactionError(
                    f"SQLite commit failed and rollback also failed: {rollback_error}"
                ) from rollback_error
            raise TransactionError(f"Unable to commit SQLite transaction: {exc}") from exc


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One persisted audit record paired with its durable append sequence."""

    sequence: int
    record: PersistedAuditRecord


@dataclass(frozen=True, slots=True)
class AuditStore:
    """Append and deterministically read durable security-audit history from SQLite."""

    database: SQLiteDatabase

    def append(self, record: AuditRecordView) -> int:
        """Atomically append one canonical audit record; return its durable sequence.

        Accepts any object structurally matching the C1.09
        ``SecurityAuditRecord`` contract (kernel records do, by construction).
        The call persists the record verbatim as inert text and grants
        nothing: a repeated ``audit_id`` fails explicitly with
        :class:`DuplicateAuditRecordError` and the stored row is never
        overwritten or merged. The returned sequence is assigned by SQLite
        after a successful commit; there is no process-local counter.
        """
        if not isinstance(record, AuditRecordView):
            raise TypeError(
                "record must expose the canonical agentx.kernel.audit SecurityAuditRecord interface"
            )

        persisted = _persist_from_view(record)
        document = persisted.to_json()
        task_id = None if persisted.task_id is None else persisted.task_id.to_str()

        with self.database.connection() as connection:
            try:
                with _write_transaction(connection):
                    cursor = connection.execute(
                        f"""
                        INSERT INTO {_AUDIT_TABLE} (audit_id, task_id, record_json)
                        VALUES (?, ?, ?)
                        """,
                        (str(persisted.audit_id), task_id, document),
                    )
                    sequence = cursor.lastrowid
                    if sequence is None:
                        raise AuditStoreStorageError(
                            "SQLite did not return a sequence for the appended audit record"
                        )
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                    raise DuplicateAuditRecordError(
                        f"Audit record {persisted.audit_id} already exists in the AuditStore"
                    ) from exc
                raise AuditStoreStorageError(
                    f"Unable to append audit record {persisted.audit_id}"
                ) from exc
            except sqlite3.Error as exc:
                raise AuditStoreStorageError(
                    f"Unable to append audit record {persisted.audit_id}"
                ) from exc

        return sequence

    def get(self, audit_id: UUID) -> AuditEntry | None:
        """Return one persisted entry by canonical audit identity, or None when absent.

        The returned entry is inert history. Materializing it into anything
        kernel-shaped is the caller's responsibility and grants nothing.
        """
        _require_uuid(audit_id, field_name="audit_id")

        with self.database.connection() as connection:
            try:
                row = connection.execute(
                    f"""
                    SELECT sequence, audit_id, task_id, record_json
                    FROM {_AUDIT_TABLE}
                    WHERE audit_id = ?
                    """,
                    (str(audit_id),),
                ).fetchone()
            except sqlite3.Error as exc:
                raise AuditStoreStorageError(f"Unable to get audit record {audit_id}") from exc

        if row is None:
            return None
        return _decode_row(row)

    def read(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        task_id: TaskId | None = None,
    ) -> tuple[AuditEntry, ...]:
        """Read entries in ascending durable sequence order.

        ``after_sequence`` is exclusive. The optional ``task_id`` filter is a
        correlation over inert stored identifiers only — never a permission
        check, capability lookup, or task mutation. Historical data returned
        here is evidence: it cannot grant, authorize, de-risk, un-budget,
        un-stop, execute, or verify anything, and the enumeration order is
        the durable append order, stable across restarts and processes.
        """
        _validate_read_boundary(after_sequence=after_sequence, limit=limit)
        if task_id is not None and not isinstance(task_id, TaskId):
            raise TypeError("task_id must be a TaskId or None")

        clauses = ["sequence > ?"]
        parameters: list[int | str] = [after_sequence]
        if task_id is not None:
            clauses.append("task_id = ?")
            parameters.append(task_id.to_str())

        sql = (
            "SELECT sequence, audit_id, task_id, record_json "
            f"FROM {_AUDIT_TABLE} WHERE {' AND '.join(clauses)} "
            "ORDER BY sequence ASC"
        )
        if limit is not None:
            sql = f"{sql} LIMIT ?"
            parameters.append(limit)

        with self.database.connection() as connection:
            try:
                rows = connection.execute(sql, tuple(parameters)).fetchall()
            except sqlite3.Error as exc:
                raise AuditStoreStorageError("Unable to read AuditStore history") from exc

        return tuple(_decode_row(row) for row in rows)


def _validate_read_boundary(*, after_sequence: int, limit: int | None) -> None:
    if (
        not isinstance(after_sequence, int)
        or isinstance(after_sequence, bool)
        or after_sequence < 0
    ):
        raise ValueError("after_sequence must be a non-negative integer")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
        raise ValueError("limit must be a positive integer or None")


def _decode_row(row: sqlite3.Row) -> AuditEntry:
    sequence_raw = row["sequence"]
    audit_id_raw = row["audit_id"]
    task_id_raw = row["task_id"]
    record_json_raw = row["record_json"]

    if not isinstance(sequence_raw, int) or isinstance(sequence_raw, bool) or sequence_raw <= 0:
        raise CorruptAuditRecordError(sequence=0, audit_id="<invalid>")
    if not isinstance(audit_id_raw, str) or not audit_id_raw:
        raise CorruptAuditRecordError(sequence=sequence_raw, audit_id="<invalid>")
    if task_id_raw is not None and (not isinstance(task_id_raw, str) or not task_id_raw):
        raise CorruptAuditRecordError(sequence=sequence_raw, audit_id=audit_id_raw)
    if not isinstance(record_json_raw, str) or not record_json_raw:
        raise CorruptAuditRecordError(sequence=sequence_raw, audit_id=audit_id_raw)

    try:
        record = _decode_document(record_json_raw)
    except AuditRecordValidationError as exc:
        raise CorruptAuditRecordError(sequence=sequence_raw, audit_id=audit_id_raw) from exc

    if str(record.audit_id) != audit_id_raw:
        raise CorruptAuditRecordError(sequence=sequence_raw, audit_id=audit_id_raw)
    expected_task_id = None if record.task_id is None else record.task_id.to_str()
    if expected_task_id != task_id_raw:
        raise CorruptAuditRecordError(sequence=sequence_raw, audit_id=audit_id_raw)

    return AuditEntry(sequence=sequence_raw, record=record)


__all__ = [
    "AuditContextView",
    "AuditEntry",
    "AuditEnumValueView",
    "AuditRecordValidationError",
    "AuditRecordView",
    "AuditSecretRefView",
    "AuditStore",
    "AuditStoreError",
    "AuditStoreStorageError",
    "CorruptAuditRecordError",
    "DuplicateAuditRecordError",
    "PersistedAuditContext",
    "PersistedAuditRecord",
]
