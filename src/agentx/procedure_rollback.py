"""Explicit rollback transaction for restoring a known-good Procedure revision (N2.18).

This module owns the mutation-level rollback transaction that restores a
previously known Procedure revision after canonical rollback eligibility has
already been established by ``agentx.core.procedure_replacement``.

Rollback is NOT history rewriting, NOT deletion, NOT automatic repair.

What rollback does
------------------
- Consumes explicit caller-supplied evidence:
  - current live ProcedureId / revision (expected ACTIVE)
  - exact known-good historical target revision (caller-chosen, not inferred)
  - canonical eligibility decision (ProcedureReplacementDecision ELIGIBLE, kind ROLLBACK)
  - exact current stored state (expected known revisions for concurrency guard)
- Applies only the legal lifecycle/persistence transition atomically.
- Preserves ALL history: bad/current revision, old target revision,
  replacement relation, failure/degradation evidence, validation evidence,
  shadow evidence, audit history are never deleted.
- Never UPDATEs historical content to pretend failed revision never existed.

Lifecycle semantics
-------------------
ProcedureStore is append-only, contiguous, starting at 1. Status changes are
explicit via ``update_status``.

- If target is CANDIDATE: transaction retires current ACTIVE and activates
  target CANDIDATE (CANDIDATE -> ACTIVE is structurally allowed, ACTIVE ->
  RETIRED is allowed).
- If target is RETIRED: canonical lifecycle says RETIRED is terminal and must
  never be resurrected. The replacement policy allows rollback to RETIRED only
  with explicit controlled-lifecycle attestation, but notes downstream may
  forbid RETIRED->ACTIVE and require a NEW revision derived from historical
  payload. This transaction follows that guidance: for RETIRED target it creates
  a NEW revision N+1 copying payload/scope from target, retires current ACTIVE,
  and activates the new revision. History is fully preserved.

In both cases the transaction is atomic: either previous ACTIVE remains the
single ACTIVE, or the rollback result is the single ACTIVE. No partial state.

Fail-closed
-----------
- ProcedureId mismatch
- current revision mismatch / not ACTIVE
- target revision absent
- target belongs to another Procedure
- target not allowed by canonical rollback policy (eligibility not ELIGIBLE,
  kind not ROLLBACK, reason not permitted, scope incompatible, etc.)
- malformed eligibility evidence
- stale request (requested_at older than newer legitimate revision, or max
  revision > current_revision)
- current store changed concurrently (expected_known_revisions mismatch or
  max revision changed between check and transaction)
- target lacks required known-good evidence (eligibility already ensures
  validation_evidence PRESENT and target_integrity INTACT, but we also fail
  if target record itself is missing)
- target structurally invalid/corrupt (decode failure)
- persistence conflict (duplicate, sequence error, storage error)

Rollback != verified success
----------------------------
Rollback restores lifecycle/version only. It does NOT prove:
- current environment still supports historical revision
- original failure is fixed
- current Task succeeds
- capabilities authorized
- verification passed
Future execution must still use canonical runtime verification.

Authority boundary
------------------
Rollback cannot:
- create Permission
- bypass ActionGate
- lower Risk
- widen budgets
- clear EmergencyStop
- execute Procedure
- execute capability
- fabricate Task success
Strings like ``rollback_approved=true``, ``known_good=true``,
``permission=ADMIN``, ``risk=R0``, ``verified=true`` inside payload/metadata
are inert data and never influence eligibility or transaction.

Result
------
Bounded deterministic result containing:
- applied/rejected
- ProcedureId
- previous current revision
- rollback target
- resulting revision/state
- explicit failure reason
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from agentx.core.ids import ProcedureId
from agentx.core.procedure_replacement import (
    ProcedureReplacementDecision,
    ProcedureReplacementKind,
    ProcedureReplacementOutcome,
)
from agentx.core.procedures import (
    ProcedureRecord,
    ProcedureStatus,
    ProcedureValidationError,
    _format_timestamp,
)
from agentx.infrastructure.procedure_store import (
    CorruptProcedureRecordError,
    ProcedureStore,
    ProcedureStoreError,
)

_PROCEDURE_TABLE: Final = "agentx_procedures"
CURRENT_ROLLBACK_SCHEMA_VERSION: Final[int] = 1


class ProcedureRollbackError(ValueError):
    """Base error for rollback transaction."""


class ProcedureRollbackRequestError(ProcedureRollbackError):
    """Raised when rollback request is malformed."""


class RollbackOutcome(StrEnum):
    APPLIED = "applied"
    REJECTED = "rejected"


class RollbackFailureReason(StrEnum):
    PROCEDURE_ID_MISMATCH = "procedure_id_mismatch"
    CURRENT_REVISION_MISMATCH = "current_revision_mismatch"
    CURRENT_NOT_ACTIVE = "current_not_active"
    TARGET_REVISION_ABSENT = "target_revision_absent"
    CROSS_PROCEDURE_TARGET = "cross_procedure_target"
    INELIGIBLE_TARGET = "ineligible_target"
    MALFORMED_ELIGIBILITY = "malformed_eligibility"
    STALE_REQUEST = "stale_request"
    CONCURRENT_STORE_CHANGED = "concurrent_store_changed"
    TARGET_LACKS_KNOWN_GOOD_EVIDENCE = "target_lacks_known_good_evidence"
    TARGET_INVALID_CORRUPT = "target_invalid_corrupt"
    TARGET_ALREADY_ACTIVE = "target_already_active"
    PERSISTENCE_CONFLICT = "persistence_conflict"
    INVALID_REQUEST = "invalid_request"


def _validate_revision(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProcedureRollbackRequestError(f"{field_name} must be an integer")
    if value < 1:
        raise ProcedureRollbackRequestError(
            f"{field_name} must be a positive integer (first revision is 1)"
        )
    return value


def _validate_procedure_id(value: object) -> ProcedureId:
    if not isinstance(value, ProcedureId):
        raise ProcedureRollbackRequestError("procedure_id must be a ProcedureId")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ProcedureRollbackRequestError(f"{field_name} must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProcedureRollbackRequestError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _freeze_known_revisions(value: object | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ProcedureRollbackRequestError(
            "expected_known_revisions must be a sequence of integers or None"
        )
    frozen = tuple(
        _validate_revision(item, field_name="expected_known_revisions item") for item in value
    )
    if not frozen:
        raise ProcedureRollbackRequestError(
            "expected_known_revisions must not be empty when provided"
        )
    if len(set(frozen)) != len(frozen):
        raise ProcedureRollbackRequestError("expected_known_revisions must not contain duplicates")
    return frozen


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureRollbackRequest:
    """Explicit, immutable request to rollback to a known-good revision.

    Fields:
        procedure_id: canonical ProcedureId
        current_revision: expected live ACTIVE revision number
        target_revision: exact historical revision to restore (caller-supplied,
            never inferred)
        eligibility: canonical ProcedureReplacementDecision that must be
            ELIGIBLE, kind ROLLBACK, matching procedure_id, active_revision,
            target_revision
        requested_at: timezone-aware instant when rollback was requested, used
            for staleness detection (caller-owned clock, never read from system)
        expected_known_revisions: optional caller view of stored revision numbers
            at request time, used to detect concurrent store changes. If provided,
            must contain current_revision.
    """

    procedure_id: ProcedureId
    current_revision: int
    target_revision: int
    eligibility: ProcedureReplacementDecision
    requested_at: datetime
    expected_known_revisions: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "procedure_id", _validate_procedure_id(self.procedure_id))
        object.__setattr__(
            self,
            "current_revision",
            _validate_revision(self.current_revision, field_name="current_revision"),
        )
        object.__setattr__(
            self,
            "target_revision",
            _validate_revision(self.target_revision, field_name="target_revision"),
        )
        if not isinstance(self.eligibility, ProcedureReplacementDecision):
            raise ProcedureRollbackRequestError(
                "eligibility must be a ProcedureReplacementDecision"
            )
        object.__setattr__(
            self, "requested_at", _validate_timestamp(self.requested_at, field_name="requested_at")
        )
        object.__setattr__(
            self, "expected_known_revisions", _freeze_known_revisions(self.expected_known_revisions)
        )
        if (
            self.expected_known_revisions is not None
            and self.current_revision not in self.expected_known_revisions
        ):
            raise ProcedureRollbackRequestError(
                "expected_known_revisions must contain current_revision"
            )
        # target must be strictly earlier than current (rollback direction)
        if self.target_revision >= self.current_revision:
            raise ProcedureRollbackRequestError(
                "target_revision must be strictly earlier than current_revision for rollback"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureRollbackResult:
    """Bounded deterministic result of one rollback attempt.

    No authority, no execution, no Task success fabrication.
    """

    outcome: RollbackOutcome
    procedure_id: ProcedureId
    previous_current_revision: int
    rollback_target: int
    resulting_revision: int | None
    resulting_status: ProcedureStatus | None
    failure_reason: RollbackFailureReason | None
    explanation: str
    schema_version: int = CURRENT_ROLLBACK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, RollbackOutcome):
            raise ProcedureRollbackRequestError("outcome must be a RollbackOutcome")
        object.__setattr__(self, "procedure_id", _validate_procedure_id(self.procedure_id))
        object.__setattr__(
            self,
            "previous_current_revision",
            _validate_revision(
                self.previous_current_revision, field_name="previous_current_revision"
            ),
        )
        object.__setattr__(
            self,
            "rollback_target",
            _validate_revision(self.rollback_target, field_name="rollback_target"),
        )
        if self.resulting_revision is not None:
            object.__setattr__(
                self,
                "resulting_revision",
                _validate_revision(self.resulting_revision, field_name="resulting_revision"),
            )
        if self.resulting_status is not None and not isinstance(
            self.resulting_status, ProcedureStatus
        ):
            raise ProcedureRollbackRequestError(
                "resulting_status must be a ProcedureStatus or None"
            )
        if self.failure_reason is not None and not isinstance(
            self.failure_reason, RollbackFailureReason
        ):
            raise ProcedureRollbackRequestError(
                "failure_reason must be a RollbackFailureReason or None"
            )
        if not isinstance(self.explanation, str) or not self.explanation.strip():
            raise ProcedureRollbackRequestError("explanation must be a non-empty string")
        if (
            not isinstance(self.schema_version, int)
            or self.schema_version != CURRENT_ROLLBACK_SCHEMA_VERSION
        ):
            raise ProcedureRollbackRequestError(
                f"unsupported rollback schema version {self.schema_version}"
            )
        # Consistency: applied => no failure, resulting present; rejected => failure present
        if self.outcome is RollbackOutcome.APPLIED:
            if self.failure_reason is not None:
                raise ProcedureRollbackRequestError("applied result must not carry failure_reason")
            if self.resulting_revision is None or self.resulting_status is None:
                raise ProcedureRollbackRequestError(
                    "applied result must carry resulting_revision and status"
                )
        else:
            if self.failure_reason is None:
                raise ProcedureRollbackRequestError("rejected result must carry failure_reason")

    @property
    def applied(self) -> bool:
        return self.outcome is RollbackOutcome.APPLIED

    @property
    def rejected(self) -> bool:
        return self.outcome is RollbackOutcome.REJECTED


# ---------------------------------------------------------------------------
# Internal helpers: transaction and row decoding
# ---------------------------------------------------------------------------


@contextmanager
def _write_transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    if connection.in_transaction:
        raise ProcedureRollbackError("Nested transactions not supported")
    try:
        connection.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as exc:
        raise ProcedureRollbackError(f"Unable to begin rollback transaction: {exc}") from exc
    try:
        yield connection
    except BaseException:
        try:
            connection.rollback()
        except sqlite3.Error as rollback_error:
            raise ProcedureRollbackError(
                f"Rollback transaction failed and rollback also failed: {rollback_error}"
            ) from rollback_error
        raise
    else:
        try:
            connection.commit()
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except sqlite3.Error as rollback_error:
                raise ProcedureRollbackError(
                    f"Rollback commit failed and rollback also failed: {rollback_error}"
                ) from rollback_error
            raise ProcedureRollbackError(f"Unable to commit rollback transaction: {exc}") from exc


def _select_revision_row(
    connection: sqlite3.Connection, procedure_id: ProcedureId, revision: int
) -> sqlite3.Row | None:
    return connection.execute(  # type: ignore[no-any-return]
        f"SELECT procedure_id, revision, created_at_utc, status, record_json "
        f"FROM {_PROCEDURE_TABLE} WHERE procedure_id = ? AND revision = ?",
        (procedure_id.to_str(), revision),
    ).fetchone()


def _decode_row(row: sqlite3.Row) -> ProcedureRecord:
    procedure_id = str(row["procedure_id"])
    raw_revision = row["revision"]
    try:
        revision = int(raw_revision)
    except (TypeError, ValueError) as exc:
        raise CorruptProcedureRecordError(procedure_id=procedure_id, revision=-1) from exc
    record_json = str(row["record_json"])
    try:
        record = ProcedureRecord.from_json(record_json)
    except ProcedureValidationError as exc:
        raise CorruptProcedureRecordError(procedure_id=procedure_id, revision=revision) from exc
    if record.procedure_id.to_str() != procedure_id:
        raise CorruptProcedureRecordError(procedure_id=procedure_id, revision=revision)
    if record.revision != revision:
        raise CorruptProcedureRecordError(procedure_id=procedure_id, revision=revision)
    if _format_timestamp(record.created_at) != str(row["created_at_utc"]):
        raise CorruptProcedureRecordError(procedure_id=procedure_id, revision=revision)
    if record.status.value != str(row["status"]):
        raise CorruptProcedureRecordError(procedure_id=procedure_id, revision=revision)
    return record


def _max_revision_for_procedure(connection: sqlite3.Connection, procedure_id: ProcedureId) -> int:
    row = connection.execute(
        f"SELECT MAX(revision) AS max_revision FROM {_PROCEDURE_TABLE} WHERE procedure_id = ?",
        (procedure_id.to_str(),),
    ).fetchone()
    if row is None or row["max_revision"] is None:
        return 0
    return int(row["max_revision"])


def _list_revisions(connection: sqlite3.Connection, procedure_id: ProcedureId) -> tuple[int, ...]:
    rows = connection.execute(
        f"SELECT revision FROM {_PROCEDURE_TABLE} WHERE procedure_id = ? ORDER BY revision ASC",
        (procedure_id.to_str(),),
    ).fetchall()
    return tuple(int(r["revision"]) for r in rows)


def _build_result(
    *,
    outcome: RollbackOutcome,
    procedure_id: ProcedureId,
    previous_current_revision: int,
    rollback_target: int,
    resulting_revision: int | None,
    resulting_status: ProcedureStatus | None,
    failure_reason: RollbackFailureReason | None,
    explanation: str,
) -> ProcedureRollbackResult:
    return ProcedureRollbackResult(
        outcome=outcome,
        procedure_id=procedure_id,
        previous_current_revision=previous_current_revision,
        rollback_target=rollback_target,
        resulting_revision=resulting_revision,
        resulting_status=resulting_status,
        failure_reason=failure_reason,
        explanation=explanation,
    )


def _explain_applied(
    *, current_rev: int, target_rev: int, resulting_rev: int, target_was_retired: bool
) -> str:
    if target_was_retired:
        return (
            f"rollback applied: retired current {current_rev}, "
            f"created new revision {resulting_rev} from retired target {target_rev} payload, "
            f"activated {resulting_rev}; history preserved"
        )
    else:
        return (
            f"rollback applied: retired current {current_rev}, "
            f"activated target {target_rev}; history preserved, no content rewritten"
        )


def _explain_rejected(reason: RollbackFailureReason, detail: str) -> str:
    return f"rollback rejected: {reason.value}: {detail}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def execute_procedure_rollback(
    store: ProcedureStore, request: ProcedureRollbackRequest
) -> ProcedureRollbackResult:
    """Execute one explicit rollback transaction atomically.

    The function is data-only aside from the controlled SQLite transaction:
    it never grants Permission, changes RiskLevel, enlarges ResourceEnvelope,
    bypasses ActionGate, clears EmergencyStop, executes Capability or Procedure,
    mutates Task, publishes Events, or calls a model.

    It preserves ALL history and never rewrites payload content.

    Args:
        store: canonical ProcedureStore
        request: immutable rollback request with explicit eligibility evidence

    Returns:
        ProcedureRollbackResult with applied/rejected and canonical facts.
    """
    if not isinstance(store, ProcedureStore):
        raise ProcedureRollbackRequestError("store must be a ProcedureStore")
    if not isinstance(request, ProcedureRollbackRequest):
        raise ProcedureRollbackRequestError("request must be a ProcedureRollbackRequest")

    proc_id = request.procedure_id
    current_rev = request.current_revision
    target_rev = request.target_revision
    eligibility = request.eligibility

    # -------------------------------------------------------------------
    # Validate eligibility evidence shape (fail-closed)
    # -------------------------------------------------------------------
    # Must be ELIGIBLE, kind ROLLBACK, matching ids/revisions
    if not isinstance(eligibility, ProcedureReplacementDecision):
        return _build_result(  # type: ignore[unreachable]
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.MALFORMED_ELIGIBILITY,
            explanation=_explain_rejected(
                RollbackFailureReason.MALFORMED_ELIGIBILITY,
                "eligibility must be a ProcedureReplacementDecision",
            ),
        )

    # Check eligibility outcome and kind
    if eligibility.outcome is not ProcedureReplacementOutcome.ELIGIBLE:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.INELIGIBLE_TARGET,
            explanation=_explain_rejected(
                RollbackFailureReason.INELIGIBLE_TARGET,
                f"eligibility outcome is {eligibility.outcome.value}, not eligible",
            ),
        )

    if eligibility.kind is not ProcedureReplacementKind.ROLLBACK:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.MALFORMED_ELIGIBILITY,
            explanation=_explain_rejected(
                RollbackFailureReason.MALFORMED_ELIGIBILITY,
                f"eligibility kind is {eligibility.kind.value}, expected rollback",
            ),
        )

    # ProcedureId must match
    if eligibility.procedure_id != proc_id:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.PROCEDURE_ID_MISMATCH,
            explanation=_explain_rejected(
                RollbackFailureReason.PROCEDURE_ID_MISMATCH,
                f"eligibility procedure_id {eligibility.procedure_id} != request {proc_id}",
            ),
        )

    if eligibility.active_revision != current_rev:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.CURRENT_REVISION_MISMATCH,
            explanation=_explain_rejected(
                RollbackFailureReason.CURRENT_REVISION_MISMATCH,
                f"eligibility active {eligibility.active_revision} != request {current_rev}",
            ),
        )

    if eligibility.target_revision != target_rev:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.MALFORMED_ELIGIBILITY,
            explanation=_explain_rejected(
                RollbackFailureReason.MALFORMED_ELIGIBILITY,
                f"eligibility target {eligibility.target_revision} != request {target_rev}",
            ),
        )

    # -------------------------------------------------------------------
    # Read current store state (non-transactional pre-checks)
    # -------------------------------------------------------------------
    try:
        with store.database.connection() as conn:
            current_row = _select_revision_row(conn, proc_id, current_rev)
            target_row = _select_revision_row(conn, proc_id, target_rev)
            max_rev = _max_revision_for_procedure(conn, proc_id)
            known_revs = _list_revisions(conn, proc_id)
    except CorruptProcedureRecordError:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.TARGET_INVALID_CORRUPT,
            explanation=_explain_rejected(
                RollbackFailureReason.TARGET_INVALID_CORRUPT, "stored procedure row is corrupt"
            ),
        )
    except (sqlite3.Error, ProcedureStoreError) as exc:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.PERSISTENCE_CONFLICT,
            explanation=_explain_rejected(
                RollbackFailureReason.PERSISTENCE_CONFLICT, f"storage error during pre-check: {exc}"
            ),
        )

    # Decode rows with corruption handling
    try:
        current_record = _decode_row(current_row) if current_row is not None else None
    except CorruptProcedureRecordError:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.TARGET_INVALID_CORRUPT,
            explanation=_explain_rejected(
                RollbackFailureReason.TARGET_INVALID_CORRUPT, "current revision row is corrupt"
            ),
        )

    try:
        target_record = _decode_row(target_row) if target_row is not None else None
    except CorruptProcedureRecordError:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.TARGET_INVALID_CORRUPT,
            explanation=_explain_rejected(
                RollbackFailureReason.TARGET_INVALID_CORRUPT, "target revision row is corrupt"
            ),
        )

    # Fail-closed checks on existence
    if current_record is None:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.CURRENT_REVISION_MISMATCH,
            explanation=_explain_rejected(
                RollbackFailureReason.CURRENT_REVISION_MISMATCH,
                f"current revision {current_rev} absent in store",
            ),
        )

    if target_record is None:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.TARGET_REVISION_ABSENT,
            explanation=_explain_rejected(
                RollbackFailureReason.TARGET_REVISION_ABSENT,
                f"target revision {target_rev} absent in store",
            ),
        )

    # ProcedureId mismatch / cross-procedure
    if current_record.procedure_id != proc_id:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.PROCEDURE_ID_MISMATCH,
            explanation=_explain_rejected(
                RollbackFailureReason.PROCEDURE_ID_MISMATCH,
                "current record procedure_id mismatch",
            ),
        )

    if target_record.procedure_id != proc_id:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.CROSS_PROCEDURE_TARGET,
            explanation=_explain_rejected(
                RollbackFailureReason.CROSS_PROCEDURE_TARGET,
                f"target record belongs to {target_record.procedure_id}, not {proc_id}",
            ),
        )

    # Current must be ACTIVE
    if current_record.status is not ProcedureStatus.ACTIVE:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.CURRENT_NOT_ACTIVE,
            explanation=_explain_rejected(
                RollbackFailureReason.CURRENT_NOT_ACTIVE,
                f"current {current_rev} status {current_record.status.value} not active",
            ),
        )

    # Target must not already be ACTIVE (would be two actives)
    if target_record.status is ProcedureStatus.ACTIVE:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.TARGET_ALREADY_ACTIVE,
            explanation=_explain_rejected(
                RollbackFailureReason.TARGET_ALREADY_ACTIVE,
                f"target revision {target_rev} is already active",
            ),
        )

    # Target status must be CANDIDATE or RETIRED (RETIRED requires new revision path)
    if target_record.status not in (ProcedureStatus.CANDIDATE, ProcedureStatus.RETIRED):
        # Should not happen as only 3 statuses exist, but fail-closed
        return _build_result(  # type: ignore[unreachable]
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.INELIGIBLE_TARGET,
            explanation=_explain_rejected(
                RollbackFailureReason.INELIGIBLE_TARGET,
                f"target status {target_record.status.value} not eligible for rollback",
            ),
        )

    # Stale / concurrent checks
    # If max_rev > current_rev, store has newer legitimate revision -> stale
    if max_rev > current_rev:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.STALE_REQUEST,
            explanation=_explain_rejected(
                RollbackFailureReason.STALE_REQUEST,
                f"store max revision {max_rev} > current {current_rev}, newer revision exists",
            ),
        )

    # If expected_known_revisions provided, must match actual known_revs
    if request.expected_known_revisions is not None and tuple(sorted(known_revs)) != tuple(
        sorted(request.expected_known_revisions)
    ):
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.CONCURRENT_STORE_CHANGED,
            explanation=_explain_rejected(
                RollbackFailureReason.CONCURRENT_STORE_CHANGED,
                f"expected known {request.expected_known_revisions} != actual {known_revs}",
            ),
        )

    # Also check requested_at vs newest record created_at for staleness
    # If any revision has created_at > requested_at and revision > current_rev,
    # it's already covered by max_rev check, but also if created_at > requested_at
    # for current revision's updated_at, we treat as stale? Simpler: if current
    # record's updated_at is not None and updated_at > requested_at, then
    # request is stale because status changed after request.
    if current_record.updated_at is not None and current_record.updated_at > request.requested_at:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.STALE_REQUEST,
            explanation=_explain_rejected(
                RollbackFailureReason.STALE_REQUEST,
                f"updated_at {current_record.updated_at} > requested_at {request.requested_at}",
            ),
        )

    # -------------------------------------------------------------------
    # Atomic transaction
    # -------------------------------------------------------------------
    try:
        with (
            store.database.connection() as conn,
            _write_transaction(conn),
        ):
            # Re-check inside transaction to prevent TOCTOU
            cur_row_tx = _select_revision_row(conn, proc_id, current_rev)
            tgt_row_tx = _select_revision_row(conn, proc_id, target_rev)
            max_rev_tx = _max_revision_for_procedure(conn, proc_id)
            known_revs_tx = _list_revisions(conn, proc_id)

            if cur_row_tx is None or tgt_row_tx is None:
                raise ProcedureRollbackError("revision disappeared during transaction")

            try:
                cur_rec_tx = _decode_row(cur_row_tx)
                tgt_rec_tx = _decode_row(tgt_row_tx)
            except CorruptProcedureRecordError as exc:
                raise ProcedureRollbackError(f"corrupt row during transaction: {exc}") from exc

            # Re-validate active status and max revision
            if cur_rec_tx.status is not ProcedureStatus.ACTIVE:
                raise ProcedureRollbackError(
                    f"current status changed to {cur_rec_tx.status.value} during tx"
                )
            if max_rev_tx != max_rev:
                raise ProcedureRollbackError(
                    f"max revision changed from {max_rev} to {max_rev_tx} during transaction"
                )
            if request.expected_known_revisions is not None and tuple(
                sorted(known_revs_tx)
            ) != tuple(sorted(request.expected_known_revisions)):
                raise ProcedureRollbackError("known revisions changed during transaction")

            # Perform rollback
            now = datetime.now(UTC)

            if tgt_rec_tx.status is ProcedureStatus.CANDIDATE:
                # Path A: retire current, activate target
                # Update current to RETIRED
                retired_current = ProcedureRecord(
                    procedure_id=cur_rec_tx.procedure_id,
                    revision=cur_rec_tx.revision,
                    payload=cur_rec_tx.payload,
                    created_at=cur_rec_tx.created_at,
                    status=ProcedureStatus.RETIRED,
                    scope=cur_rec_tx.scope,
                    updated_at=now,
                    schema_version=cur_rec_tx.schema_version,
                )
                conn.execute(
                    f"UPDATE {_PROCEDURE_TABLE} SET status = ?, record_json = ? "
                    f"WHERE procedure_id = ? AND revision = ?",
                    (
                        retired_current.status.value,
                        retired_current.to_json(),
                        proc_id.to_str(),
                        current_rev,
                    ),
                )

                # Update target to ACTIVE
                activated_target = ProcedureRecord(
                    procedure_id=tgt_rec_tx.procedure_id,
                    revision=tgt_rec_tx.revision,
                    payload=tgt_rec_tx.payload,
                    created_at=tgt_rec_tx.created_at,
                    status=ProcedureStatus.ACTIVE,
                    scope=tgt_rec_tx.scope,
                    updated_at=now,
                    schema_version=tgt_rec_tx.schema_version,
                )
                conn.execute(
                    f"UPDATE {_PROCEDURE_TABLE} SET status = ?, record_json = ? "
                    f"WHERE procedure_id = ? AND revision = ?",
                    (
                        activated_target.status.value,
                        activated_target.to_json(),
                        proc_id.to_str(),
                        target_rev,
                    ),
                )

                resulting_revision = target_rev
                resulting_status = ProcedureStatus.ACTIVE
                target_was_retired = False

            else:  # RETIRED target -> create new revision
                new_rev = max_rev_tx + 1
                # New record copies payload/scope from target, new revision, now
                new_record_candidate = ProcedureRecord(
                    procedure_id=proc_id,
                    revision=new_rev,
                    payload=tgt_rec_tx.payload,
                    created_at=now,
                    status=ProcedureStatus.CANDIDATE,
                    scope=tgt_rec_tx.scope,
                    updated_at=None,
                )
                # Insert candidate
                conn.execute(
                    f"INSERT INTO {_PROCEDURE_TABLE} "
                    "(procedure_id, revision, created_at_utc, status, record_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        new_record_candidate.procedure_id.to_str(),
                        new_record_candidate.revision,
                        _format_timestamp(new_record_candidate.created_at),
                        new_record_candidate.status.value,
                        new_record_candidate.to_json(),
                    ),
                )

                # Retire current
                retired_current = ProcedureRecord(
                    procedure_id=cur_rec_tx.procedure_id,
                    revision=cur_rec_tx.revision,
                    payload=cur_rec_tx.payload,
                    created_at=cur_rec_tx.created_at,
                    status=ProcedureStatus.RETIRED,
                    scope=cur_rec_tx.scope,
                    updated_at=now,
                    schema_version=cur_rec_tx.schema_version,
                )
                conn.execute(
                    f"UPDATE {_PROCEDURE_TABLE} SET status = ?, record_json = ? "
                    f"WHERE procedure_id = ? AND revision = ?",
                    (
                        retired_current.status.value,
                        retired_current.to_json(),
                        proc_id.to_str(),
                        current_rev,
                    ),
                )

                # Activate new
                activated_new = ProcedureRecord(
                    procedure_id=new_record_candidate.procedure_id,
                    revision=new_record_candidate.revision,
                    payload=new_record_candidate.payload,
                    created_at=new_record_candidate.created_at,
                    status=ProcedureStatus.ACTIVE,
                    scope=new_record_candidate.scope,
                    updated_at=now,
                    schema_version=new_record_candidate.schema_version,
                )
                conn.execute(
                    f"UPDATE {_PROCEDURE_TABLE} SET status = ?, record_json = ? "
                    f"WHERE procedure_id = ? AND revision = ?",
                    (
                        activated_new.status.value,
                        activated_new.to_json(),
                        proc_id.to_str(),
                        new_rev,
                    ),
                )

                resulting_revision = new_rev
                resulting_status = ProcedureStatus.ACTIVE
                target_was_retired = True

            # Transaction will commit on exit

    except sqlite3.Error as exc:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.PERSISTENCE_CONFLICT,
            explanation=_explain_rejected(
                RollbackFailureReason.PERSISTENCE_CONFLICT, f"sqlite error during rollback: {exc}"
            ),
        )
    except ProcedureRollbackError as exc:
        # Distinguish stale/concurrent vs persistence
        msg = str(exc).lower()
        if "changed" in msg or "disappeared" in msg:
            reason = RollbackFailureReason.CONCURRENT_STORE_CHANGED
        elif "corrupt" in msg:
            reason = RollbackFailureReason.TARGET_INVALID_CORRUPT
        else:
            reason = RollbackFailureReason.PERSISTENCE_CONFLICT
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=reason,
            explanation=_explain_rejected(reason, f"transaction failed: {exc}"),
        )
    except (ProcedureStoreError, ProcedureValidationError) as exc:
        return _build_result(
            outcome=RollbackOutcome.REJECTED,
            procedure_id=proc_id,
            previous_current_revision=current_rev,
            rollback_target=target_rev,
            resulting_revision=None,
            resulting_status=None,
            failure_reason=RollbackFailureReason.PERSISTENCE_CONFLICT,
            explanation=_explain_rejected(
                RollbackFailureReason.PERSISTENCE_CONFLICT, f"store error during rollback: {exc}"
            ),
        )

    # Success
    return _build_result(
        outcome=RollbackOutcome.APPLIED,
        procedure_id=proc_id,
        previous_current_revision=current_rev,
        rollback_target=target_rev,
        resulting_revision=resulting_revision,
        resulting_status=resulting_status,
        failure_reason=None,
        explanation=_explain_applied(
            current_rev=current_rev,
            target_rev=target_rev,
            resulting_rev=resulting_revision,
            target_was_retired=target_was_retired,
        ),
    )
