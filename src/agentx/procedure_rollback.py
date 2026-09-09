"""Explicit rollback composition for a caller-selected Procedure revision (N2.18).

The canonical replacement policy owns rollback eligibility. This module binds
one exact ELIGIBLE ROLLBACK decision to exact persisted Procedure history and
delegates storage mechanics to ``agentx.infrastructure.procedure_activation``.
It performs no target selection, execution, verification, promotion policy or
authority decision.

RETIRED is terminal. If the exact caller-selected historical target is RETIRED,
this module materializes a NEW contiguous CANDIDATE with the same payload and
scope; only that new revision may be activated. The original RETIRED record is
left untouched as historical evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
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
from agentx.core.procedures import ProcedureRecord, ProcedureStatus
from agentx.infrastructure.procedure_activation import (
    ProcedureActivationConflict,
    activate_procedure_revision_atomically,
)
from agentx.infrastructure.procedure_store import (
    CorruptProcedureRecordError,
    ProcedureStore,
    ProcedureStoreError,
)

CURRENT_ROLLBACK_SCHEMA_VERSION: Final[int] = 1


class ProcedureRollbackError(ValueError):
    """Base error for rollback request construction."""


class ProcedureRollbackRequestError(ProcedureRollbackError):
    """Raised when a rollback request is structurally malformed."""


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
    if type(value) is not int:
        raise ProcedureRollbackRequestError(f"{field_name} must be an integer")
    if value < 1:
        raise ProcedureRollbackRequestError(f"{field_name} must be a positive integer")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ProcedureRollbackRequestError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _freeze_known_revisions(value: object | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ProcedureRollbackRequestError(
            "expected_known_revisions must be a sequence of positive integers or None"
        )
    frozen = tuple(
        _validate_revision(item, field_name="expected_known_revisions item") for item in value
    )
    if not frozen:
        raise ProcedureRollbackRequestError("expected_known_revisions must not be empty")
    if len(set(frozen)) != len(frozen):
        raise ProcedureRollbackRequestError("expected_known_revisions must not contain duplicates")
    if tuple(sorted(frozen)) != frozen:
        raise ProcedureRollbackRequestError("expected_known_revisions must be sorted")
    return frozen


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureRollbackRequest:
    """Exact caller-selected rollback request; target selection is impossible here."""

    procedure_id: ProcedureId
    current_revision: int
    target_revision: int
    eligibility: ProcedureReplacementDecision
    requested_at: datetime
    expected_known_revisions: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, ProcedureId):
            raise ProcedureRollbackRequestError("procedure_id must be a ProcedureId")
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
            self,
            "requested_at",
            _validate_timestamp(self.requested_at, field_name="requested_at"),
        )
        known = _freeze_known_revisions(self.expected_known_revisions)
        object.__setattr__(self, "expected_known_revisions", known)
        if known is not None and self.current_revision not in known:
            raise ProcedureRollbackRequestError(
                "expected_known_revisions must contain current_revision"
            )
        if self.target_revision >= self.current_revision:
            raise ProcedureRollbackRequestError(
                "target_revision must be strictly earlier than current_revision for rollback"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureRollbackResult:
    """Bounded lifecycle result; never verified Task success or execution authority."""

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
        if not isinstance(self.procedure_id, ProcedureId):
            raise ProcedureRollbackRequestError("procedure_id must be a ProcedureId")
        _validate_revision(self.previous_current_revision, field_name="previous_current_revision")
        _validate_revision(self.rollback_target, field_name="rollback_target")
        if self.resulting_revision is not None:
            _validate_revision(self.resulting_revision, field_name="resulting_revision")
        if self.resulting_status is not None and not isinstance(
            self.resulting_status, ProcedureStatus
        ):
            raise ProcedureRollbackRequestError("resulting_status must be ProcedureStatus or None")
        if self.failure_reason is not None and not isinstance(
            self.failure_reason, RollbackFailureReason
        ):
            raise ProcedureRollbackRequestError(
                "failure_reason must be RollbackFailureReason or None"
            )
        if not isinstance(self.explanation, str) or not self.explanation.strip():
            raise ProcedureRollbackRequestError("explanation must be non-empty")
        if (
            type(self.schema_version) is not int
            or self.schema_version != CURRENT_ROLLBACK_SCHEMA_VERSION
        ):
            raise ProcedureRollbackRequestError("unsupported rollback schema version")
        if self.outcome is RollbackOutcome.APPLIED:
            if self.failure_reason is not None:
                raise ProcedureRollbackRequestError("applied result cannot carry failure_reason")
            if (
                self.resulting_revision is None
                or self.resulting_status is not ProcedureStatus.ACTIVE
            ):
                raise ProcedureRollbackRequestError(
                    "applied result requires resulting ACTIVE revision"
                )
        elif self.failure_reason is None:
            raise ProcedureRollbackRequestError("rejected result requires failure_reason")

    @property
    def applied(self) -> bool:
        return self.outcome is RollbackOutcome.APPLIED

    @property
    def rejected(self) -> bool:
        return self.outcome is RollbackOutcome.REJECTED


def _reject(
    request: ProcedureRollbackRequest,
    reason: RollbackFailureReason,
    explanation: str,
) -> ProcedureRollbackResult:
    return ProcedureRollbackResult(
        outcome=RollbackOutcome.REJECTED,
        procedure_id=request.procedure_id,
        previous_current_revision=request.current_revision,
        rollback_target=request.target_revision,
        resulting_revision=None,
        resulting_status=None,
        failure_reason=reason,
        explanation=explanation,
    )


def _decision_failure(request: ProcedureRollbackRequest) -> ProcedureRollbackResult | None:
    decision = request.eligibility
    if decision.outcome is not ProcedureReplacementOutcome.ELIGIBLE:
        return _reject(
            request,
            RollbackFailureReason.INELIGIBLE_TARGET,
            "canonical rollback decision is not ELIGIBLE",
        )
    if decision.kind is not ProcedureReplacementKind.ROLLBACK:
        return _reject(
            request,
            RollbackFailureReason.MALFORMED_ELIGIBILITY,
            "canonical decision kind is not ROLLBACK",
        )
    if decision.procedure_id != request.procedure_id:
        return _reject(
            request,
            RollbackFailureReason.PROCEDURE_ID_MISMATCH,
            "eligibility ProcedureId does not match rollback request",
        )
    if decision.active_revision != request.current_revision:
        return _reject(
            request,
            RollbackFailureReason.CURRENT_REVISION_MISMATCH,
            "eligibility active revision does not match rollback request",
        )
    if decision.target_revision != request.target_revision:
        return _reject(
            request,
            RollbackFailureReason.MALFORMED_ELIGIBILITY,
            "eligibility target revision does not match rollback request",
        )
    return None


def execute_procedure_rollback(
    store: ProcedureStore,
    request: ProcedureRollbackRequest,
) -> ProcedureRollbackResult:
    """Apply one exact rollback without automatic selection or RETIRED resurrection."""
    if not isinstance(store, ProcedureStore):
        raise TypeError("store must be a ProcedureStore")
    if not isinstance(request, ProcedureRollbackRequest):
        raise TypeError("request must be a ProcedureRollbackRequest")

    decision_failure = _decision_failure(request)
    if decision_failure is not None:
        return decision_failure

    try:
        history = store.history(request.procedure_id)
    except CorruptProcedureRecordError as exc:
        reason = (
            RollbackFailureReason.TARGET_INVALID_CORRUPT
            if exc.revision == request.target_revision
            else RollbackFailureReason.PERSISTENCE_CONFLICT
        )
        return _reject(request, reason, "persisted Procedure history is corrupt")
    except ProcedureStoreError as exc:
        return _reject(
            request,
            RollbackFailureReason.PERSISTENCE_CONFLICT,
            f"ProcedureStore read failed: {type(exc).__name__}",
        )
    if not history:
        return _reject(
            request,
            RollbackFailureReason.CURRENT_REVISION_MISMATCH,
            "current Procedure history is absent",
        )

    revisions = tuple(record.revision for record in history)
    if revisions != tuple(range(1, len(revisions) + 1)):
        return _reject(
            request,
            RollbackFailureReason.PERSISTENCE_CONFLICT,
            "stored Procedure revisions are not contiguous",
        )
    if (
        request.expected_known_revisions is not None
        and revisions != request.expected_known_revisions
    ):
        return _reject(
            request,
            RollbackFailureReason.CONCURRENT_STORE_CHANGED,
            "stored revisions differ from caller's exact expected history",
        )
    if revisions[-1] != request.current_revision:
        return _reject(
            request,
            RollbackFailureReason.STALE_REQUEST,
            "max_revision differs from requested current revision",
        )

    current = next(
        (record for record in history if record.revision == request.current_revision),
        None,
    )
    if current is None:
        return _reject(
            request,
            RollbackFailureReason.CURRENT_REVISION_MISMATCH,
            "requested current revision is absent",
        )
    if current.status is not ProcedureStatus.ACTIVE:
        return _reject(
            request,
            RollbackFailureReason.CURRENT_NOT_ACTIVE,
            "requested current revision is not ACTIVE",
        )
    active_records = tuple(record for record in history if record.status is ProcedureStatus.ACTIVE)
    if active_records != (current,):
        return _reject(
            request,
            RollbackFailureReason.CONCURRENT_STORE_CHANGED,
            "current revision is not the sole ACTIVE revision",
        )
    if current.updated_at is not None and current.updated_at > request.requested_at:
        return _reject(
            request,
            RollbackFailureReason.STALE_REQUEST,
            "current revision changed after rollback was requested",
        )

    target = next(
        (record for record in history if record.revision == request.target_revision),
        None,
    )
    if target is None:
        return _reject(
            request,
            RollbackFailureReason.TARGET_REVISION_ABSENT,
            "caller-selected rollback target is absent",
        )
    if target.procedure_id != request.procedure_id:
        return _reject(
            request,
            RollbackFailureReason.CROSS_PROCEDURE_TARGET,
            "rollback target belongs to a different ProcedureId",
        )
    if target.status is ProcedureStatus.ACTIVE:
        return _reject(
            request,
            RollbackFailureReason.TARGET_ALREADY_ACTIVE,
            "rollback target is already ACTIVE",
        )

    if target.status is ProcedureStatus.RETIRED:
        target_candidate = ProcedureRecord(
            procedure_id=request.procedure_id,
            revision=request.current_revision + 1,
            payload=target.payload,
            created_at=request.requested_at,
            status=ProcedureStatus.CANDIDATE,
            scope=target.scope,
        )
        target_must_exist = False
    else:
        target_candidate = target
        target_must_exist = True

    try:
        transition = activate_procedure_revision_atomically(
            store,
            expected_history=history,
            expected_active=current,
            target_candidate=target_candidate,
            target_must_exist=target_must_exist,
            require_target_latest=False,
            transitioned_at=request.requested_at,
        )
    except ProcedureActivationConflict as exc:
        return _reject(
            request,
            RollbackFailureReason.CONCURRENT_STORE_CHANGED,
            f"concurrent ProcedureStore state rejected: {exc}",
        )
    except ProcedureStoreError as exc:
        return _reject(
            request,
            RollbackFailureReason.PERSISTENCE_CONFLICT,
            f"ProcedureStore write failed: {type(exc).__name__}",
        )

    return ProcedureRollbackResult(
        outcome=RollbackOutcome.APPLIED,
        procedure_id=request.procedure_id,
        previous_current_revision=request.current_revision,
        rollback_target=request.target_revision,
        resulting_revision=transition.active_record.revision,
        resulting_status=ProcedureStatus.ACTIVE,
        failure_reason=None,
        explanation=(
            "rollback materialized a new revision from terminal RETIRED history"
            if target.status is ProcedureStatus.RETIRED
            else "rollback activated the exact caller-selected CANDIDATE target"
        ),
    )


__all__ = [
    "CURRENT_ROLLBACK_SCHEMA_VERSION",
    "ProcedureRollbackError",
    "ProcedureRollbackRequest",
    "ProcedureRollbackRequestError",
    "ProcedureRollbackResult",
    "RollbackFailureReason",
    "RollbackOutcome",
    "execute_procedure_rollback",
]
