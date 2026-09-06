"""C6.06 knowledge revalidation lifecycle boundary.

Contracts and orchestration only: this module never acquires research or
interprets research text. Provider responses are inert evidence; promotion to
VERIFIED requires an explicit canonical verification verdict from trusted code.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Iterable
from agentx.core.knowledge import KnowledgeRecord, KnowledgeScope, KnowledgeStatus
from agentx.core.provenance import KnowledgeEvidence

__all__ = ["RevalidationReason", "RevalidationDecision", "RevalidationRequest", "RevalidationOutcome", "KnowledgeRevalidator", "revalidation_needed", "apply_revalidation_evidence"]

class RevalidationReason(StrEnum):
    FRESH = "fresh"
    EXPIRED = "expired"
    STALE = "stale"
    ENVIRONMENT_CHANGED = "environment_changed"
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    CONFLICTED = "conflicted"
    DEGRADED = "degraded"
    FAILED_RESEARCH = "failed_research"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"

@dataclass(frozen=True, slots=True)
class RevalidationDecision:
    needed: bool
    reasons: tuple[RevalidationReason, ...]

@dataclass(frozen=True, slots=True)
class RevalidationRequest:
    request_id: str
    knowledge_id: object
    objective: object
    reason: RevalidationReason
    requested_at: datetime
    scope: KnowledgeScope

@dataclass(frozen=True, slots=True)
class RevalidationOutcome:
    request_id: str
    knowledge_id: object
    status: KnowledgeStatus
    evidence: tuple[KnowledgeEvidence, ...]
    sufficient: bool
    failed: bool = False
    conflict: bool = False
    provenance_preserved: bool = True

class KnowledgeRevalidator:
    """Evaluate and apply revalidation; research providers remain separate."""
    def evaluate(self, record: KnowledgeRecord, *, now: datetime | None = None, environment: KnowledgeScope | None = None, max_age_seconds: float | None = None) -> RevalidationDecision:
        if not isinstance(record, KnowledgeRecord): raise TypeError("record must be a KnowledgeRecord")
        at = datetime.now(UTC) if now is None else now.astimezone(UTC)
        reasons: list[RevalidationReason] = []
        if record.status is KnowledgeStatus.UNVERIFIED: reasons.append(RevalidationReason.UNVERIFIED)
        elif record.status is KnowledgeStatus.SUPPORTED: reasons.append(RevalidationReason.SUPPORTED)
        elif record.status is KnowledgeStatus.CONFLICTED: reasons.append(RevalidationReason.CONFLICTED)
        elif record.status is KnowledgeStatus.DEGRADED: reasons.append(RevalidationReason.DEGRADED)
        if environment is not None and environment != record.scope: reasons.append(RevalidationReason.ENVIRONMENT_CHANGED)
        if max_age_seconds is not None and (at - (record.verified_at or record.created_at)).total_seconds() > max_age_seconds:
            reasons.append(RevalidationReason.EXPIRED)
        if not reasons and record.status is KnowledgeStatus.VERIFIED: reasons.append(RevalidationReason.FRESH)
        return RevalidationDecision(bool(reasons and reasons != [RevalidationReason.FRESH]), tuple(reasons))

    def apply(self, record: KnowledgeRecord, *, request_id: str, evidence: Iterable[KnowledgeEvidence] = (), canonical_verified: bool = False, sufficient: bool = False, failed: bool = False, conflict: bool = False, verified_at: datetime | None = None) -> RevalidationOutcome:
        if not isinstance(record, KnowledgeRecord): raise TypeError("record must be a KnowledgeRecord")
        ev = tuple(evidence)
        if any(not isinstance(item, KnowledgeEvidence) for item in ev): raise TypeError("evidence must contain KnowledgeEvidence values")
        if any(item.knowledge_id != record.knowledge_id for item in ev): raise ValueError("evidence belongs to a different knowledge record")
        if conflict: target = KnowledgeStatus.CONFLICTED
        elif failed or not sufficient: target = KnowledgeStatus.DEGRADED if record.status is KnowledgeStatus.VERIFIED else record.status
        elif canonical_verified: target = KnowledgeStatus.VERIFIED
        else: target = KnowledgeStatus.SUPPORTED if record.status in (KnowledgeStatus.UNVERIFIED, KnowledgeStatus.PROVISIONAL) else record.status
        return RevalidationOutcome(request_id=request_id, knowledge_id=record.knowledge_id, status=target, evidence=ev, sufficient=sufficient, failed=failed, conflict=conflict)

def revalidation_needed(record: KnowledgeRecord, **kwargs: object) -> RevalidationDecision:
    return KnowledgeRevalidator().evaluate(record, **kwargs)  # type: ignore[arg-type]

def apply_revalidation_evidence(record: KnowledgeRecord, **kwargs: object) -> RevalidationOutcome:
    return KnowledgeRevalidator().apply(record, **kwargs)  # type: ignore[arg-type]

    def build_request(self, record: KnowledgeRecord, *, request_id: str, objective: object,
                      reason: RevalidationReason | None = None,
                      now: datetime | None = None,
                      environment: KnowledgeScope | None = None,
                      max_age_seconds: float | None = None) -> RevalidationRequest:
        decision = self.evaluate(record, now=now, environment=environment,
                                 max_age_seconds=max_age_seconds)
        selected = reason or next((item for item in decision.reasons if item is not RevalidationReason.FRESH), RevalidationReason.STALE)
        if selected is RevalidationReason.FRESH:
            raise ValueError("fresh knowledge does not require a revalidation request")
        return RevalidationRequest(request_id=request_id, knowledge_id=record.knowledge_id,
                                   objective=objective, reason=selected,
                                   requested_at=datetime.now(UTC) if now is None else now.astimezone(UTC),
                                   scope=record.scope if environment is None else environment)
