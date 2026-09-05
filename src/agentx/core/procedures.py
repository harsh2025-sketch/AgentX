"""Canonical AgentX procedure-record contract (C2.03).

This module defines *storage records* only. A procedure record is DATA, never
authority and never a program: storing, retrieving, or inspecting a procedure
revision must not grant Permission, change RiskLevel, increase a
ResourceEnvelope, bypass the Action Gate, clear an EmergencyStop, execute a
Capability, mutate a Task, publish Events, or execute the procedure itself.

Boundary ownership (deliberate):

    - The full Procedure Graph canonical IR — ACTION/VERIFY/BRANCH/REASON node
      semantics, graph structure, interpretation — is owned by Day-3 A3.01 and
      is NOT defined here. This contract carries only an opaque
      :class:`ProcedurePayload` hook that a future Procedure Graph compiler can
      serialize into without this schema ever interpreting it.
    - Candidate-skill lifecycle and trust are owned by C3.09. This contract
      carries a minimal storage-level :class:`ProcedureStatus` vocabulary so
      persisted records can be distinguished; it implements no transition
      rules and confers no trust.
    - Persistence lives outward in ``agentx.infrastructure.procedure_store``.
      This module is storage-independent.

Revision identity:

    - A record is identified by the pair ``(procedure_id, revision)``:
      the canonical :class:`agentx.core.ids.ProcedureId` names the procedure,
      and an explicit positive integer ``revision`` names one immutable
      version of it. Revisions are append-only history: a stored revision is
      never silently overwritten, and a new revision is always distinguishable
      from an old one.
    - Sequencing of revisions (append-only, contiguous, first revision is 1)
      is enforced by the store, not by the record: the record only requires a
      positive integer.

Status policy:

    - Records enter life ``CANDIDATE`` via :meth:`ProcedureRecord.create`.
    - Status changes are explicit acts performed by callers (through
      ``ProcedureStore.update_status``). Persistence alone never promotes,
      retires, or otherwise mutates a record's status, and a stored record
      never becomes ``ACTIVE`` merely because it exists in storage.
    - Even an ``ACTIVE`` record is inert data and grants zero execution
      authority. ``updated_at`` records WHEN the status was last explicitly
      changed; it is historical data, not a grant of authority.

This module belongs to ``agentx.core`` and imports nothing from other AgentX
subsystems except the canonical identifiers in ``agentx.core.ids``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from agentx.core.ids import ProcedureId

__all__ = [
    "CURRENT_PROCEDURE_SCHEMA_VERSION",
    "ProcedurePayload",
    "ProcedurePayloadKind",
    "ProcedureRecord",
    "ProcedureScope",
    "ProcedureScopeDimension",
    "ProcedureStatus",
    "ProcedureValidationError",
    "UnsupportedProcedureSchemaVersionError",
]

CURRENT_PROCEDURE_SCHEMA_VERSION: Final[int] = 1


class ProcedureValidationError(ValueError):
    """Raised when a procedure record violates the canonical contract."""


class UnsupportedProcedureSchemaVersionError(ProcedureValidationError):
    """Raised when encoded record data uses a schema version this code cannot read."""


class ProcedureStatus(StrEnum):
    """Storage-level status vocabulary for persisted procedure revisions.

    The vocabulary exists so storage can DISTINGUISH records. It carries no
    trust and no lifecycle policy:

        - A status value is data. Storing, reading, or transitioning a status
          never grants Permission, changes RiskLevel, enlarges a
          ResourceEnvelope, bypasses the Action Gate, clears an EmergencyStop,
          executes anything, or mutates a Task.
        - Storage applies no transition rules and never promotes a revision.
          A revision becomes ACTIVE only through an explicit caller act, never
          as a side effect of being stored or read.
        - Candidate-skill lifecycle and trust policy are owned by C3.09; the
          Procedure Graph IR is owned by A3.01. Neither is implemented here.

    Members:

        CANDIDATE — birth state of every new revision; untrusted by
            construction.
        ACTIVE — explicitly marked live by a caller; still inert data with
            zero execution authority.
        RETIRED — explicitly withdrawn historical state.
    """

    CANDIDATE = "candidate"
    ACTIVE = "active"
    RETIRED = "retired"


class ProcedureScopeDimension(StrEnum):
    """Controlled dimension vocabulary for scoping procedure applicability.

    Intentionally the smallest extensible typed boundary: future tasks add
    members rather than a universal scope ontology. An empty scope means the
    record is global (unscoped). Applicability recorded here is descriptive
    data only; it is never an authorization check.
    """

    APPLICATION = "application"
    APPLICATION_VERSION = "application_version"
    OPERATING_SYSTEM = "os"
    ENVIRONMENT = "environment"
    PROJECT = "project"


class ProcedurePayloadKind(StrEnum):
    """Channel vocabulary for the opaque procedure payload hook.

    These kinds record WHAT FORM the payload takes. They carry no trust and no
    semantics: this contract never parses, validates, interprets, imports, or
    executes payload content. Payload interpretation belongs exclusively to
    the future Procedure Graph IR (A3.01) and its compilers.
    """

    CANONICAL_JSON = "canonical_json"
    ARTIFACT_REFERENCE = "artifact_reference"


def _validate_nonempty_trimmed(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ProcedureValidationError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise ProcedureValidationError(f"{field_name} must be non-empty and trimmed")
    return value


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ProcedureValidationError(f"{field_name} must be an ISO-8601 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ProcedureValidationError(f"{field_name} must be a valid ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProcedureValidationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    utc_value = value.astimezone(UTC)
    return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ProcedureValidationError(f"{field_name} must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProcedureValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_revision(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProcedureValidationError(f"{field_name} must be an integer")
    if value < 1:
        raise ProcedureValidationError(
            f"{field_name} must be a positive integer (first revision is 1)"
        )
    return value


def _parse_procedure_id(value: object) -> ProcedureId:
    if not isinstance(value, str):
        raise ProcedureValidationError("procedure_id must be a UUID string")
    try:
        return ProcedureId(UUID(value))
    except ValueError as exc:
        raise ProcedureValidationError(
            f"procedure_id must be a valid non-nil UUID string: {value!r}"
        ) from exc


def _freeze_scope_dimensions(
    value: Mapping[ProcedureScopeDimension, str],
) -> Mapping[ProcedureScopeDimension, str]:
    if not isinstance(value, Mapping):
        raise ProcedureValidationError("scope dimensions must be a mapping")
    frozen: dict[ProcedureScopeDimension, str] = {}
    for key, item in value.items():
        if not isinstance(key, ProcedureScopeDimension):
            raise ProcedureValidationError(
                f"scope dimension key must be a ProcedureScopeDimension, got {key!r}"
            )
        frozen[key] = _validate_nonempty_trimmed(item, field_name=f"scope.{key.value}")
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class ProcedureScope:
    """Typed, extensible applicability boundary for a procedure record.

    Maps controlled :class:`ProcedureScopeDimension` names to non-empty string
    values (for example ``OPERATING_SYSTEM -> "windows"``). An empty scope is
    the global scope. Equality is by dimension/value content. Scope describes
    where a procedure claims to apply; it is never an authorization check.
    """

    dimensions: Mapping[ProcedureScopeDimension, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dimensions", _freeze_scope_dimensions(self.dimensions))

    def value_for(self, dimension: ProcedureScopeDimension) -> str | None:
        """Return the scoped value for ``dimension`` or ``None`` if unscoped."""
        if not isinstance(dimension, ProcedureScopeDimension):
            raise ProcedureValidationError("dimension must be a ProcedureScopeDimension")
        return self.dimensions.get(dimension)

    def to_dict(self) -> dict[str, str]:
        return {dimension.value: value for dimension, value in self.dimensions.items()}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedureScope:
        if not isinstance(raw, Mapping):
            raise ProcedureValidationError("scope must be a JSON object")
        dimensions: dict[ProcedureScopeDimension, str] = {}
        for key, item in raw.items():
            if not isinstance(key, str):
                raise ProcedureValidationError("scope dimension key must be a string")
            try:
                dimension = ProcedureScopeDimension(key)
            except ValueError as exc:
                raise ProcedureValidationError(f"unknown scope dimension: {key!r}") from exc
            dimensions[dimension] = _validate_nonempty_trimmed(item, field_name=f"scope.{key}")
        return cls(dimensions=dimensions)


@dataclass(frozen=True, slots=True)
class ProcedurePayload:
    """Opaque payload hook: a controlled kind plus opaque non-empty content.

    ``content`` is deliberately opaque. For ``CANONICAL_JSON`` it is a
    producer-canonical JSON document (the future Procedure Graph IR, A3.01,
    serializes itself into it); for ``ARTIFACT_REFERENCE`` it is an opaque
    reference to an artifact stored elsewhere (for example an
    :class:`agentx.core.ids.ArtifactId` string; no artifact store exists yet
    and none is implied by this contract).

    This contract never parses, validates against a schema, interprets,
    imports, or executes payload content. A payload claiming "verified",
    "trusted", "active", "permission=DESTRUCTIVE", "risk=R0", or embedding
    executable-looking text is exactly as inert as any other string.
    """

    kind: ProcedurePayloadKind
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProcedurePayloadKind):
            raise ProcedureValidationError("payload kind must be a ProcedurePayloadKind")
        _validate_nonempty_trimmed(self.content, field_name="payload.content")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "content": self.content}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedurePayload:
        actual = set(raw)
        expected = {"kind", "content"}
        if actual != expected:
            raise ProcedureValidationError(
                f"payload must contain exactly {sorted(expected)}; got {sorted(actual)}"
            )
        kind_raw = raw["kind"]
        if not isinstance(kind_raw, str):
            raise ProcedureValidationError("payload kind must be a string")
        try:
            kind = ProcedurePayloadKind(kind_raw)
        except ValueError as exc:
            raise ProcedureValidationError(f"unknown payload kind: {kind_raw!r}") from exc
        content = _validate_nonempty_trimmed(raw["content"], field_name="payload.content")
        return cls(kind=kind, content=content)


_PROCEDURE_RECORD_FIELDS: Final = frozenset(
    {
        "schema_version",
        "procedure_id",
        "revision",
        "payload",
        "status",
        "scope",
        "created_at",
        "updated_at",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureRecord:
    """Immutable canonical AgentX procedure-record revision.

    Identity is the pair ``(procedure_id, revision)``. A procedure revision is
    DATA, not authority and not a program: whatever its ``status``,
    ``scope``, or ``payload`` content, it is inert. Strings such as
    ``"grant admin"``, ``"risk=R0"``, ``"active=true"``, or
    ``"execute shell: rm -rf /"`` inside ``payload`` have ZERO authority.
    Authority semantics belong exclusively to ``agentx.kernel``; payload
    interpretation belongs exclusively to the future Procedure Graph IR
    (A3.01), which is deliberately not defined here.

    Records are immutable snapshots. The only storage-level mutation is an
    explicit status transition (see ``ProcedureStore.update_status``), which
    produces a new immutable snapshot and never rewrites history that has
    already been observed.
    """

    procedure_id: ProcedureId
    revision: int
    payload: ProcedurePayload
    created_at: datetime
    status: ProcedureStatus = ProcedureStatus.CANDIDATE
    scope: ProcedureScope = field(default_factory=ProcedureScope)
    updated_at: datetime | None = None
    schema_version: int = CURRENT_PROCEDURE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, ProcedureId):
            raise ProcedureValidationError("procedure_id must be a ProcedureId")
        object.__setattr__(
            self, "revision", _validate_revision(self.revision, field_name="revision")
        )
        if not isinstance(self.payload, ProcedurePayload):
            raise ProcedureValidationError("payload must be a ProcedurePayload")
        if not isinstance(self.status, ProcedureStatus):
            raise ProcedureValidationError("status must be a ProcedureStatus")
        if not isinstance(self.scope, ProcedureScope):
            raise ProcedureValidationError("scope must be a ProcedureScope")
        object.__setattr__(
            self,
            "created_at",
            _validate_timestamp(self.created_at, field_name="created_at"),
        )
        if self.updated_at is not None:
            object.__setattr__(
                self,
                "updated_at",
                _validate_timestamp(self.updated_at, field_name="updated_at"),
            )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise ProcedureValidationError("schema_version must be an integer")
        if self.schema_version != CURRENT_PROCEDURE_SCHEMA_VERSION:
            raise UnsupportedProcedureSchemaVersionError(
                f"unsupported procedure schema version {self.schema_version}; "
                f"supported version is {CURRENT_PROCEDURE_SCHEMA_VERSION}"
            )

    @classmethod
    def create(
        cls,
        *,
        payload: ProcedurePayload,
        procedure_id: ProcedureId | None = None,
        revision: int = 1,
        scope: ProcedureScope | None = None,
        created_at: datetime | None = None,
    ) -> ProcedureRecord:
        """Create a new revision that enters life CANDIDATE.

        The factory deliberately exposes no ``status``/``updated_at``
        parameters: new revisions are born ``CANDIDATE`` with no update
        timestamp. Any other status is an explicit act taken later through an
        explicit status update — never a side effect of creation or storage.

        ``procedure_id`` defaults to a freshly generated identity (a brand-new
        procedure) and ``revision`` defaults to ``1`` (the first revision).
        Callers advancing an existing procedure pass its identity and the next
        revision number; the store enforces append-only contiguous revisions.
        """
        return cls(
            procedure_id=ProcedureId.create() if procedure_id is None else procedure_id,
            revision=revision,
            payload=payload,
            created_at=datetime.now(UTC) if created_at is None else created_at,
            status=ProcedureStatus.CANDIDATE,
            scope=ProcedureScope() if scope is None else scope,
            updated_at=None,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible schema-v1 representation."""
        return {
            "schema_version": self.schema_version,
            "procedure_id": self.procedure_id.to_str(),
            "revision": self.revision,
            "payload": self.payload.to_dict(),
            "status": self.status.value,
            "scope": self.scope.to_dict(),
            "created_at": _format_timestamp(self.created_at),
            "updated_at": (None if self.updated_at is None else _format_timestamp(self.updated_at)),
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
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedureRecord:
        """Validate and deserialize a canonical JSON-compatible record object.

        Deserialization restores persisted data verbatim — including a
        historical ``ACTIVE`` status or a hostile payload — without adding,
        promoting, demoting, or executing anything. A restored record is still
        inert data.
        """
        if "schema_version" not in raw:
            raise ProcedureValidationError("record missing required field: schema_version")
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise ProcedureValidationError("schema_version must be an integer")
        if version != CURRENT_PROCEDURE_SCHEMA_VERSION:
            raise UnsupportedProcedureSchemaVersionError(
                f"unsupported procedure schema version {version}; "
                f"supported version is {CURRENT_PROCEDURE_SCHEMA_VERSION}"
            )

        actual = set(raw)
        if actual != _PROCEDURE_RECORD_FIELDS:
            missing = _PROCEDURE_RECORD_FIELDS - actual
            unknown = actual - _PROCEDURE_RECORD_FIELDS
            if missing:
                raise ProcedureValidationError(f"record missing required fields: {sorted(missing)}")
            raise ProcedureValidationError(f"record contains unknown fields: {sorted(unknown)}")

        status_raw = raw["status"]
        if not isinstance(status_raw, str):
            raise ProcedureValidationError("status must be a string")
        try:
            status = ProcedureStatus(status_raw)
        except ValueError as exc:
            raise ProcedureValidationError(f"unknown status: {status_raw!r}") from exc

        scope_raw = raw["scope"]
        if not isinstance(scope_raw, Mapping):
            raise ProcedureValidationError("scope must be a JSON object")

        payload_raw = raw["payload"]
        if not isinstance(payload_raw, Mapping):
            raise ProcedureValidationError("payload must be a JSON object")
        payload_object: dict[str, object] = {}
        for key, item in payload_raw.items():
            if not isinstance(key, str):
                raise ProcedureValidationError("payload contains a non-string object key")
            payload_object[key] = item

        updated_raw = raw["updated_at"]
        updated_at = (
            None if updated_raw is None else _parse_timestamp(updated_raw, field_name="updated_at")
        )

        return cls(
            procedure_id=_parse_procedure_id(raw["procedure_id"]),
            revision=_validate_revision(raw["revision"], field_name="revision"),
            payload=ProcedurePayload.from_dict(payload_object),
            created_at=_parse_timestamp(raw["created_at"], field_name="created_at"),
            status=status,
            scope=ProcedureScope.from_dict(scope_raw),
            updated_at=updated_at,
            schema_version=version,
        )

    @classmethod
    def from_json(cls, raw: str) -> ProcedureRecord:
        """Deserialize JSON text without dynamic imports or arbitrary object construction."""
        if not isinstance(raw, str):
            raise ProcedureValidationError("record JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProcedureValidationError("record JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise ProcedureValidationError("record JSON root must be an object")
        copied: dict[str, object] = {}
        for key, item in decoded.items():
            if not isinstance(key, str):
                raise ProcedureValidationError("record JSON contains a non-string object key")
            copied[key] = item
        return cls.from_dict(copied)
