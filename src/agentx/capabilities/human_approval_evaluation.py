"""Deterministic evaluation of canonical A6.08 human approval evidence.

This module answers one narrow question: does the supplied canonical human
approval decision match the exact governed approval request currently under
consideration, and if so was that explicit decision APPROVED or DENIED?

Evaluation is inert evidence classification only.  It does not ask a human,
parse text, create authority, grant permission, call ActionGate, alter risk or
budgets, clear emergency stops, execute capabilities, transition tasks, verify
success, activate procedures, persist approval state, or invoke models.
Human operating mode is intentionally absent because it is contextual data,
not approval authority.
"""

from __future__ import annotations

from enum import StrEnum

from agentx.capabilities.human_approval import (
    HumanApprovalDecision,
    HumanApprovalMismatchError,
    HumanApprovalOutcome,
    HumanApprovalRequest,
    HumanApprovalValidationError,
)

__all__ = [
    "HumanApprovalEvidenceStatus",
    "evaluate_human_approval_evidence",
]


class HumanApprovalEvidenceStatus(StrEnum):
    """Closed result vocabulary for one deterministic evidence evaluation."""

    MATCHING_APPROVED = "MATCHING_APPROVED"
    MATCHING_DENIED = "MATCHING_DENIED"
    MISSING = "MISSING"
    MISMATCHED = "MISMATCHED"


def evaluate_human_approval_evidence(
    request: HumanApprovalRequest,
    decision: HumanApprovalDecision | None,
) -> HumanApprovalEvidenceStatus:
    """Classify canonical approval evidence against the exact supplied request.

    ``MATCHING_APPROVED`` means only that the supplied explicit human approval
    evidence is bound to this exact A6.08 request.  It is not authorization.
    ``MATCHING_DENIED`` records an exact matching denial.  ``MISSING`` means no
    decision evidence was supplied.  ``MISMATCHED`` means canonical decision
    evidence was supplied for a different request, including replay against an
    otherwise-identical new request instance.

    Non-canonical objects such as strings, model output, webpage text, or mode
    values are rejected rather than interpreted as approval evidence.
    """
    if not isinstance(request, HumanApprovalRequest):
        raise TypeError("request must be a HumanApprovalRequest")
    if decision is None:
        return HumanApprovalEvidenceStatus.MISSING
    if not isinstance(decision, HumanApprovalDecision):
        raise TypeError("decision must be a HumanApprovalDecision or None")

    try:
        decision.validate_binding(request)
    except HumanApprovalMismatchError:
        return HumanApprovalEvidenceStatus.MISMATCHED

    if decision.outcome is HumanApprovalOutcome.APPROVED:
        return HumanApprovalEvidenceStatus.MATCHING_APPROVED
    if decision.outcome is HumanApprovalOutcome.DENIED:
        return HumanApprovalEvidenceStatus.MATCHING_DENIED

    # Canonical HumanApprovalDecision construction makes this unreachable, but
    # fail closed rather than silently treating malformed evidence as approval.
    raise HumanApprovalValidationError("human approval decision has unsupported outcome")
