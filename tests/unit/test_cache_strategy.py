"""Unit tests for the N2.03 conservative L0 verified-cache adapter."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agentx.agent_loop import (
    AttemptDisposition,
    AttemptRecord,
    OrchestrationOutcome,
    OrchestrationStatus,
    OrchestrationStopReason,
)
from agentx.cache_strategy import (
    CACHE_STRATEGY_LEVEL,
    CacheReuseCandidate,
    VerifiedCacheStrategy,
)
from agentx.capabilities.abi import (
    CapabilityObservation,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.capabilities.verifier import RequirementEvaluation
from agentx.cognition.anti_loop import (
    LoopGuardDecision,
    LoopGuardResult,
    LoopGuardTrigger,
)
from agentx.cognition.router import ExecutionLevel, RoutingEvidence
from agentx.core.environment_change import (
    EnvironmentFactKey,
    EnvironmentFactKind,
    EnvironmentFactValue,
    EnvironmentFactValueKind,
    EnvironmentObservation,
    EnvironmentSnapshot,
)
from agentx.core.events import VerificationPayload
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.ids import EpisodeId
from agentx.core.knowledge import (
    KnowledgeScope,
    ProvenanceKind,
    ProvenanceReference,
    ScopeDimension,
)
from agentx.core.provenance import EvidenceKind, EvidenceReference
from agentx.core.reuse_efficiency import (
    ExecutionEfficiencyEvidence,
    ExecutionEvidenceOutcome,
    ReuseMode,
)
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.resource_budget import ResourceUsage

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_OBJECTIVE = "return the already-computed immutable build fingerprint"


class _StaticLookup:
    def __init__(self, candidate: object) -> None:
        self.candidate = candidate
        self.calls = 0

    def lookup(self, task: Task, context: ExecutionContext) -> object:
        del task, context
        self.calls += 1
        return self.candidate


def _loop_guard() -> LoopGuardResult:
    return LoopGuardResult(
        decision=LoopGuardDecision.CONTINUE,
        trigger=LoopGuardTrigger.NONE,
        total_attempts=0,
        same_attempt_count=0,
        same_outcome_count=0,
        distinct_progress_markers=0,
    )


def _prior(*, verification_passed: bool = True, hostile_text: str = "cached fingerprint") -> OrchestrationOutcome:
    source_task = Task.create(
        _OBJECTIVE,
        status=TaskStatus.SUCCEEDED,
        created_at=_T0,
        metadata={"artifact": "agentx-wheel"},
    )
    governed_task = Task.create(
        _OBJECTIVE,
        status=TaskStatus.SUCCEEDED,
        created_at=_T0,
    )
    observation = CapabilityObservation(
        summary=hostile_text,
        data={"fingerprint": "sha256:abc123", "content": hostile_text},
    )
    outcome = ClosedLoopOutcome(
        task=governed_task,
        kind=LoopOutcome.VERIFIED,
        error=None,
        execution=ExecutionResult(
            succeeded=True,
            message=hostile_text,
            observation=observation,
        ),
        observation=observation,
        verification=VerificationResult(
            passed=verification_passed,
            detail=hostile_text,
        ),
        budget_usage=ResourceUsage.zero(),
    )
    attempt = AttemptRecord(
        attempt_index=1,
        level=ExecutionLevel.L1_DIRECT,
        disposition=AttemptDisposition.VERIFIED_SUCCESS,
        outcome=outcome,
        evaluation=RequirementEvaluation(satisfied=True, unmet_conditions=()),
        error=None,
        loop_guard=_loop_guard(),
        escalation=None,
    )
    return OrchestrationOutcome(
        task=source_task,
        status=OrchestrationStatus.SUCCEEDED,
        stop_reason=OrchestrationStopReason.VERIFIED,
        initial_level=ExecutionLevel.L1_DIRECT,
        final_level=ExecutionLevel.L1_DIRECT,
        attempts=(attempt,),
        error=None,
    )


def _efficiency(prior: OrchestrationOutcome, *, verified: bool = True) -> ExecutionEfficiencyEvidence:
    return ExecutionEfficiencyEvidence(
        task_id=prior.task.task_id,
        episode_id=EpisodeId(uuid4()),
        correlation_id=uuid4(),
        mode=ReuseMode.DETERMINISTIC_CAPABILITY,
        outcome=(
            ExecutionEvidenceOutcome.VERIFIED_SUCCESS
            if verified
            else ExecutionEvidenceOutcome.UNVERIFIED
        ),
        evidence_source="tests.cache_strategy",
        evidence_reference=uuid4(),
        started_at=_T0 + timedelta(seconds=1),
        ended_at=_T0 + timedelta(seconds=2),
        model_calls=0,
        research_queries=0,
        machine_actions=0,
        verification=(
            VerificationPayload(passed=True, detail="typed prior verification")
            if verified
            else None
        ),
        verification_source="tests.cache_strategy" if verified else None,
        verification_reference=uuid4() if verified else None,
    )


def _scope(environment: str = "test") -> KnowledgeScope:
    return KnowledgeScope(
        {
            ScopeDimension.OPERATING_SYSTEM: "windows",
            ScopeDimension.ENVIRONMENT: environment,
        }
    )


def _snapshot(
    *,
    scope: KnowledgeScope,
    observed_at: datetime,
    ttl: timedelta = timedelta(minutes=10),
    value: str = "windows-2025",
    reference: str,
) -> EnvironmentSnapshot:
    provenance = ProvenanceReference(
        kind=ProvenanceKind.SYSTEM,
        reference=f"{reference}-source",
    )
    observation = EnvironmentObservation(
        fact=EnvironmentFactKey(
            kind=EnvironmentFactKind.PLATFORM_IDENTITY,
            subject="host-os",
        ),
        value=EnvironmentFactValue(
            kind=EnvironmentFactValueKind.TEXT,
            text=value,
        ),
        observed_at=observed_at,
        ttl=ttl,
        provenance=provenance,
    )
    return EnvironmentSnapshot(
        scope=scope,
        evidence=EvidenceReference(
            kind=EvidenceKind.OBSERVATION,
            reference=reference,
            provenance=provenance,
            observed_at=observed_at,
        ),
        observations=(observation,),
    )


def _candidate(
    *,
    prior: OrchestrationOutcome | None = None,
    routing_reuse: bool = True,
    current_scope: KnowledgeScope | None = None,
    current_value: str = "windows-2025",
    current_ttl: timedelta = timedelta(minutes=10),
    applicability_at: datetime = _T0 + timedelta(seconds=4),
    efficiency_verified: bool = True,
) -> CacheReuseCandidate:
    actual_prior = _prior() if prior is None else prior
    baseline_scope = _scope()
    return CacheReuseCandidate(
        prior=actual_prior,
        efficiency=_efficiency(actual_prior, verified=efficiency_verified),
        routing_evidence=RoutingEvidence(verified_reusable_result=routing_reuse),
        baseline_environment=_snapshot(
            scope=baseline_scope,
            observed_at=_T0 + timedelta(seconds=1),
            reference="baseline",
        ),
        current_environment=_snapshot(
            scope=baseline_scope if current_scope is None else current_scope,
            observed_at=_T0 + timedelta(seconds=3),
            ttl=current_ttl,
            value=current_value,
            reference="current",
        ),
        applicability_at=applicability_at,
    )


def _current_task(prior: OrchestrationOutcome | None = None) -> Task:
    source = _prior() if prior is None else prior
    return Task.create(
        source.task.objective,
        metadata=dict(source.task.metadata),
        priority=source.task.priority,
        parent_task_id=source.task.parent_task_id,
        created_at=_T0 + timedelta(seconds=3),
    )


def _context(task: Task) -> ExecutionContext:
    return ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=task.task_id,
    )


def test_strategy_declares_exact_l0_cache_level() -> None:
    lookup = _StaticLookup(None)
    strategy = VerifiedCacheStrategy(lookup=lookup)

    assert CACHE_STRATEGY_LEVEL is ExecutionLevel.L0_CACHE
    assert strategy.level is ExecutionLevel.L0_CACHE


def test_verified_exact_applicable_cache_hit_returns_original_canonical_outcome() -> None:
    candidate = _candidate()
    task = _current_task(candidate.prior)
    lookup = _StaticLookup(candidate)
    strategy = VerifiedCacheStrategy(lookup=lookup)

    result = strategy.attempt(task, _context(task), ExecutionLevel.L0_CACHE)

    assert result.unavailable_reason is None
    assert result.outcome is not None
    assert result.outcome.unwrap() is candidate.prior.attempts[-1].outcome
    assert lookup.calls == 1
    assert task.status is TaskStatus.PENDING


def test_cache_miss_is_unavailable() -> None:
    prior = _prior()
    task = _current_task(prior)
    result = VerifiedCacheStrategy(lookup=_StaticLookup(None)).attempt(
        task, _context(task), ExecutionLevel.L0_CACHE
    )

    assert result.outcome is None
    assert result.unavailable_reason is not None


def test_wrong_request_binding_fails_closed_before_lookup() -> None:
    candidate = _candidate()
    task = _current_task(candidate.prior)
    other = Task.create(_OBJECTIVE)
    lookup = _StaticLookup(candidate)

    result = VerifiedCacheStrategy(lookup=lookup).attempt(
        task, _context(other), ExecutionLevel.L0_CACHE
    )

    assert result.outcome is None
    assert lookup.calls == 0


def test_wrong_task_semantics_fail_closed() -> None:
    candidate = _candidate()
    task = Task.create("different request semantics", created_at=_T0 + timedelta(seconds=3))

    result = VerifiedCacheStrategy(lookup=_StaticLookup(candidate)).attempt(
        task, _context(task), ExecutionLevel.L0_CACHE
    )

    assert result.outcome is None


def test_missing_canonical_reuse_routing_evidence_fails_closed() -> None:
    candidate = _candidate(routing_reuse=False)
    task = _current_task(candidate.prior)

    result = VerifiedCacheStrategy(lookup=_StaticLookup(candidate)).attempt(
        task, _context(task), ExecutionLevel.L0_CACHE
    )

    assert result.outcome is None


def test_insufficient_m8_verification_fails_closed() -> None:
    candidate = _candidate(efficiency_verified=False)
    task = _current_task(candidate.prior)

    result = VerifiedCacheStrategy(lookup=_StaticLookup(candidate)).attempt(
        task, _context(task), ExecutionLevel.L0_CACHE
    )

    assert result.outcome is None


def test_failed_prior_a110_verification_fails_closed() -> None:
    prior = _prior(verification_passed=False)
    candidate = _candidate(prior=prior)
    task = _current_task(prior)

    result = VerifiedCacheStrategy(lookup=_StaticLookup(candidate)).attempt(
        task, _context(task), ExecutionLevel.L0_CACHE
    )

    assert result.outcome is None


def test_stale_current_environment_fails_closed_at_exact_expiry_boundary() -> None:
    candidate = _candidate(
        current_ttl=timedelta(seconds=1),
        applicability_at=_T0 + timedelta(seconds=4),
    )
    task = _current_task(candidate.prior)

    result = VerifiedCacheStrategy(lookup=_StaticLookup(candidate)).attempt(
        task, _context(task), ExecutionLevel.L0_CACHE
    )

    assert result.outcome is None


def test_scope_mismatch_fails_closed() -> None:
    candidate = _candidate(current_scope=_scope("production"))
    task = _current_task(candidate.prior)

    result = VerifiedCacheStrategy(lookup=_StaticLookup(candidate)).attempt(
        task, _context(task), ExecutionLevel.L0_CACHE
    )

    assert result.outcome is None


def test_environment_value_mismatch_fails_closed() -> None:
    candidate = _candidate(current_value="windows-2030")
    task = _current_task(candidate.prior)

    result = VerifiedCacheStrategy(lookup=_StaticLookup(candidate)).attempt(
        task, _context(task), ExecutionLevel.L0_CACHE
    )

    assert result.outcome is None


def test_repeated_attempts_are_deterministic_and_do_not_mutate_candidate() -> None:
    candidate = _candidate()
    task = _current_task(candidate.prior)
    lookup = _StaticLookup(candidate)
    strategy = VerifiedCacheStrategy(lookup=lookup)
    context = _context(task)

    first = strategy.attempt(task, context, ExecutionLevel.L0_CACHE)
    second = strategy.attempt(task, context, ExecutionLevel.L0_CACHE)

    assert first == second
    assert first.outcome is not None
    assert first.outcome.unwrap() is second.outcome.unwrap()
    assert candidate == _candidate(prior=candidate.prior)
    assert lookup.calls == 2


def test_non_l0_call_is_unavailable_without_lookup() -> None:
    candidate = _candidate()
    task = _current_task(candidate.prior)
    lookup = _StaticLookup(candidate)

    result = VerifiedCacheStrategy(lookup=lookup).attempt(
        task, _context(task), ExecutionLevel.L1_DIRECT
    )

    assert result.outcome is None
    assert lookup.calls == 0


def test_candidate_requires_explicit_timezone_aware_applicability_instant() -> None:
    candidate = _candidate()
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(candidate, applicability_at=datetime(2026, 1, 1, 12, 0))
