"""Atomic forward Procedure replacement composition (N2.17, B-reverified).

Canonical replacement eligibility is decided upstream by
``agentx.core.procedure_replacement``. This module binds one exact ELIGIBLE
FORWARD_REPLACEMENT decision to exact immutable store evidence and delegates
the storage mutation to ``agentx.infrastructure.procedure_activation``.

Rollback is deliberately out of scope: N2.18 owns rollback composition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

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
from agentx.infrastructure.procedure_store import ProcedureStore, ProcedureStoreError


class ReplacementTransactionError(Exception):
    """Base error for replacement-transaction composition failures."""


class ReplacementConcurrentStateError(ReplacementTransactionError):
    """Compatibility error name for stale/concurrent-state failures."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplacementTransactionResult:
    """Bounded lifecycle result; it is not verification or execution authority."""

    status: str
    procedure_id: ProcedureId
    previous_revision: int
    replacement_revision: int
    reason: str | None = None


def _rejected(
    decision: ProcedureReplacementDecision,
    reason: str,
) -> ReplacementTransactionResult:
    return ReplacementTransactionResult(
        status="REJECTED",
        procedure_id=decision.procedure_id,
        previous_revision=decision.active_revision,
        replacement_revision=decision.target_revision,
        reason=reason,
    )


def execute_replacement_transaction(
    store: ProcedureStore,
    decision: ProcedureReplacementDecision,
    active_record: ProcedureRecord,
    target_record: ProcedureRecord,
) -> ReplacementTransactionResult:
    """Apply one exact, already-eligible forward replacement atomically."""
    if not isinstance(store, ProcedureStore):
        raise TypeError("store must be a ProcedureStore")
    if not isinstance(decision, ProcedureReplacementDecision):
        raise TypeError("decision must be a ProcedureReplacementDecision")
    if not isinstance(active_record, ProcedureRecord):
        raise TypeError("active_record must be a ProcedureRecord")
    if not isinstance(target_record, ProcedureRecord):
        raise TypeError("target_record must be a ProcedureRecord")

    if decision.outcome is not ProcedureReplacementOutcome.ELIGIBLE:
        return _rejected(decision, f"Decision outcome is {decision.outcome.value}")
    if decision.kind is not ProcedureReplacementKind.FORWARD_REPLACEMENT:
        return _rejected(decision, "N2.17 accepts FORWARD_REPLACEMENT decisions only")
    if decision.procedure_id != active_record.procedure_id:
        return _rejected(decision, "Active record ProcedureId mismatch")
    if decision.procedure_id != target_record.procedure_id:
        return _rejected(decision, "Target record ProcedureId mismatch")
    if decision.active_revision != active_record.revision:
        return _rejected(decision, "Active record revision mismatch")
    if decision.target_revision != target_record.revision:
        return _rejected(decision, "Target record revision mismatch")
    if active_record.status is not ProcedureStatus.ACTIVE:
        return _rejected(decision, "Supplied active record is not ACTIVE")
    if target_record.status is not ProcedureStatus.CANDIDATE:
        return _rejected(decision, "Forward replacement target must be CANDIDATE")
    if target_record.revision != active_record.revision + 1:
        return _rejected(decision, "Forward replacement target must be the next revision")

    try:
        history = store.history(decision.procedure_id)
    except ProcedureStoreError as exc:
        return _rejected(decision, f"ProcedureStore read failed: {type(exc).__name__}")
    if not history:
        return _rejected(decision, "Active Procedure history is absent")

    revisions = tuple(record.revision for record in history)
    if revisions != tuple(range(1, len(revisions) + 1)):
        return _rejected(decision, "Stored Procedure history is not contiguous")
    stored_active = next(
        (record for record in history if record.revision == active_record.revision),
        None,
    )
    if stored_active != active_record:
        return _rejected(decision, "Active record state changed after eligibility assessment")
    active_records = tuple(record for record in history if record.status is ProcedureStatus.ACTIVE)
    if active_records != (active_record,):
        return _rejected(decision, "Expected active record is not the sole ACTIVE revision")

    stored_target = next(
        (record for record in history if record.revision == target_record.revision),
        None,
    )
    target_must_exist = stored_target is not None
    if target_must_exist:
        if stored_target != target_record:
            return _rejected(decision, "Target record differs from exact decision evidence")
        if target_record.revision != revisions[-1]:
            return _rejected(decision, "Forward target is stale; a newer revision exists")
    elif target_record.revision != revisions[-1] + 1:
        return _rejected(
            decision,
            "Forward target is not the next append-only revision",
        )

    try:
        transition = activate_procedure_revision_atomically(
            store,
            expected_history=history,
            expected_active=active_record,
            target_candidate=target_record,
            target_must_exist=target_must_exist,
            require_target_latest=True,
            transitioned_at=datetime.now(UTC),
        )
    except ProcedureActivationConflict as exc:
        return _rejected(decision, f"Concurrent store state rejected: {exc}")
    except ProcedureStoreError as exc:
        return _rejected(decision, f"ProcedureStore write failed: {type(exc).__name__}")

    return ReplacementTransactionResult(
        status="APPLIED",
        procedure_id=decision.procedure_id,
        previous_revision=transition.retired_record.revision,
        replacement_revision=transition.active_record.revision,
        reason=None,
    )


__all__ = [
    "ReplacementConcurrentStateError",
    "ReplacementTransactionError",
    "ReplacementTransactionResult",
    "execute_replacement_transaction",
]
