"""Conservative top-level L0 verified-result cache strategy adapter (N2.03).

A cache hit is not Task success. This adapter only offers a prior canonical
``ClosedLoopOutcome`` back to A2.10 when explicit canonical reuse, verification,
Task-applicability, scope, and freshness evidence all agree. The original
outcome is returned unchanged; A2.10 still applies its current A2.05
``VerificationRequirement`` before the current Task can become ``SUCCEEDED``.

The adapter owns no cache store, persistence, executor, procedure, model,
research client, Task manager, or kernel authority object.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol, cast

from agentx.agent_loop import (
    AttemptDisposition,
    OrchestrationOutcome,
    OrchestrationStatus,
    OrchestrationStopReason,
    StrategyResult,
)
from agentx.capabilities.abi import (
    CapabilityObservation,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.capabilities.verifier import RequirementEvaluation
from agentx.cognition.router import ExecutionLevel, RoutingEvidence
from agentx.core.environment_change import (
    EnvironmentFactKey,
    EnvironmentObservation,
    EnvironmentSnapshot,
)
from agentx.core.errors import AgentXError
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.reuse_efficiency import (
    ExecutionEfficiencyEvidence,
    ExecutionEvidenceOutcome,
)
from agentx.core.tasks import Task, TaskStatus

__all__ = [
    "CACHE_STRATEGY_LEVEL",
    "CacheReuseCandidate",
    "ReusableResultLookup",
    "VerifiedCacheStrategy",
]

CACHE_STRATEGY_LEVEL: Final[ExecutionLevel] = ExecutionLevel.L0_CACHE


class ReusableResultLookup(Protocol):
    """Read-only lookup seam for one explicit reusable-result candidate."""

    def lookup(
        self,
        task: Task,
        context: ExecutionContext,
    ) -> CacheReuseCandidate | None:
        """Return one candidate for this request, or ``None`` on a miss."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheReuseCandidate:
    """Inert bundle of existing canonical evidence needed for L0 reuse.

    ``applicability_at`` is supplied explicitly so freshness checks are
    deterministic and this module never reads a wall clock.
    """

    prior: OrchestrationOutcome
    efficiency: ExecutionEfficiencyEvidence
    routing_evidence: RoutingEvidence
    baseline_environment: EnvironmentSnapshot
    current_environment: EnvironmentSnapshot
    applicability_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.prior, OrchestrationOutcome):
            raise TypeError(
                f"prior must be an OrchestrationOutcome, got {type(self.prior).__name__}"
            )
        if not isinstance(self.efficiency, ExecutionEfficiencyEvidence):
            raise TypeError(
                "efficiency must be an ExecutionEfficiencyEvidence, got "
                f"{type(self.efficiency).__name__}"
            )
        if not isinstance(self.routing_evidence, RoutingEvidence):
            raise TypeError(
                "routing_evidence must be a RoutingEvidence, got "
                f"{type(self.routing_evidence).__name__}"
            )
        if not isinstance(self.baseline_environment, EnvironmentSnapshot):
            raise TypeError(
                "baseline_environment must be an EnvironmentSnapshot, got "
                f"{type(self.baseline_environment).__name__}"
            )
        if not isinstance(self.current_environment, EnvironmentSnapshot):
            raise TypeError(
                "current_environment must be an EnvironmentSnapshot, got "
                f"{type(self.current_environment).__name__}"
            )
        if not isinstance(self.applicability_at, datetime):
            raise TypeError(
                "applicability_at must be a timezone-aware datetime, got "
                f"{type(self.applicability_at).__name__}"
            )
        if self.applicability_at.tzinfo is None or self.applicability_at.utcoffset() is None:
            raise ValueError("applicability_at must be timezone-aware")
        object.__setattr__(self, "applicability_at", self.applicability_at.astimezone(UTC))


def _task_semantics_match(current: Task, source: Task) -> bool:
    """Compare all reusable request semantics exposed by canonical ``Task``."""

    return (
        current.objective == source.objective
        and current.priority is source.priority
        and current.parent_task_id == source.parent_task_id
        and current.metadata == source.metadata
    )


def _verified_prior_outcome(candidate: CacheReuseCandidate) -> ClosedLoopOutcome | None:
    """Return the original final A1.10 outcome only for typed prior success."""

    prior = candidate.prior
    if prior.status is not OrchestrationStatus.SUCCEEDED:
        return None
    if prior.stop_reason is not OrchestrationStopReason.VERIFIED or not prior.verified:
        return None
    if prior.task.status is not TaskStatus.SUCCEEDED or prior.error is not None:
        return None
    if not prior.attempts:
        return None

    final = prior.attempts[-1]
    if final.disposition is not AttemptDisposition.VERIFIED_SUCCESS or not final.verified:
        return None
    if final.level is not prior.final_level:
        return None
    if not isinstance(final.evaluation, RequirementEvaluation) or not final.evaluation.satisfied:
        return None

    outcome = final.outcome
    if not isinstance(outcome, ClosedLoopOutcome):
        return None
    if outcome.kind is not LoopOutcome.VERIFIED:
        return None
    if outcome.task.status is not TaskStatus.SUCCEEDED or outcome.error is not None:
        return None
    if outcome.task.objective != prior.task.objective:
        return None

    verification = outcome.verification
    if not isinstance(verification, VerificationResult) or verification.passed is not True:
        return None
    observation = outcome.observation
    execution = outcome.execution
    if not isinstance(observation, CapabilityObservation):
        return None
    if not isinstance(execution, ExecutionResult) or execution.succeeded is not True:
        return None
    if execution.observation != observation:
        return None
    return outcome


def _efficiency_supports_prior(candidate: CacheReuseCandidate) -> bool:
    """Require independent M8.01 typed verified-success evidence."""

    evidence = candidate.efficiency
    if evidence.task_id != candidate.prior.task.task_id:
        return False
    if evidence.outcome is not ExecutionEvidenceOutcome.VERIFIED_SUCCESS:
        return False
    if not evidence.verified_success:
        return False
    if evidence.verification is None or evidence.verification.passed is not True:
        return False

    started_at = evidence.started_at
    ended_at = evidence.ended_at
    if started_at is None or ended_at is None:
        return False
    if started_at < candidate.prior.task.created_at:
        return False
    return ended_at <= candidate.applicability_at


def _observation_map(
    snapshot: EnvironmentSnapshot,
) -> dict[EnvironmentFactKey, EnvironmentObservation] | None:
    """Index typed facts and reject duplicate fact identities."""

    indexed: dict[EnvironmentFactKey, EnvironmentObservation] = {}
    for observation in snapshot.observations:
        if observation.fact in indexed:
            return None
        indexed[observation.fact] = observation
    return indexed


def _environment_still_applies(candidate: CacheReuseCandidate) -> bool:
    """Require exact typed scope/facts/values plus canonical freshness."""

    baseline = candidate.baseline_environment
    current = candidate.current_environment
    if baseline.scope != current.scope:
        return False
    if not baseline.observations or not current.observations:
        return False

    baseline_by_fact = _observation_map(baseline)
    current_by_fact = _observation_map(current)
    if baseline_by_fact is None or current_by_fact is None:
        return False
    if baseline_by_fact.keys() != current_by_fact.keys():
        return False

    prior_at = candidate.efficiency.ended_at
    if prior_at is None or prior_at > candidate.applicability_at:
        return False

    for fact, prior_observation in baseline_by_fact.items():
        current_observation = current_by_fact[fact]
        if prior_observation.observed_at > prior_at:
            return False
        if current_observation.observed_at > candidate.applicability_at:
            return False
        if current_observation.observed_at < prior_at:
            return False
        if not prior_observation.is_fresh(prior_at):
            return False
        if not current_observation.is_fresh(candidate.applicability_at):
            return False
        if prior_observation.value != current_observation.value:
            return False
    return True


def _candidate_applies(
    candidate: CacheReuseCandidate,
    task: Task,
) -> ClosedLoopOutcome | None:
    """Fail closed unless every canonical reuse precondition agrees."""

    if candidate.routing_evidence.verified_reusable_result is not True:
        return None
    if not _task_semantics_match(task, candidate.prior.task):
        return None

    outcome = _verified_prior_outcome(candidate)
    if outcome is None:
        return None
    if not _efficiency_supports_prior(candidate):
        return None
    if not _environment_still_applies(candidate):
        return None
    return outcome


class VerifiedCacheStrategy:
    """A2.10-compatible adapter serving exactly conservative ``L0_CACHE``."""

    __slots__ = ("_lookup",)

    def __init__(self, *, lookup: object) -> None:
        if not callable(getattr(lookup, "lookup", None)):
            raise TypeError("lookup must provide callable lookup(task, context)")
        self._lookup = cast(ReusableResultLookup, lookup)

    @property
    def level(self) -> ExecutionLevel:
        """Return the one canonical execution level this adapter serves."""

        return CACHE_STRATEGY_LEVEL

    @property
    def lookup(self) -> ReusableResultLookup:
        """Return the injected read-only lookup seam."""

        return self._lookup

    def attempt(
        self,
        task: Task,
        context: ExecutionContext,
        level: ExecutionLevel,
    ) -> StrategyResult:
        """Offer an applicable prior outcome to A2.10 without executing."""

        if not isinstance(task, Task):
            raise TypeError(f"task must be a Task, got {type(task).__name__}")
        if not isinstance(context, ExecutionContext):
            raise TypeError(
                f"context must be an ExecutionContext, got {type(context).__name__}"
            )
        if not isinstance(level, ExecutionLevel):
            raise TypeError(f"level must be an ExecutionLevel, got {type(level).__name__}")

        if level is not CACHE_STRATEGY_LEVEL:
            return StrategyResult.unavailable(
                "verified-cache strategy is available only for L0_CACHE"
            )
        if context.task_id is None or context.task_id != task.task_id:
            return StrategyResult.unavailable(
                "current execution context is not bound to the current Task"
            )

        candidate: object = self._lookup.lookup(task, context)
        if candidate is None:
            return StrategyResult.unavailable("no reusable cache candidate is available")
        if not isinstance(candidate, CacheReuseCandidate):
            return StrategyResult.unavailable(
                "cache lookup returned a non-canonical reuse candidate"
            )

        outcome = _candidate_applies(candidate, task)
        if outcome is None:
            return StrategyResult.unavailable(
                "cache candidate does not prove current verified reuse applicability"
            )
        return StrategyResult.executed(
            Result[ClosedLoopOutcome, AgentXError].success(outcome)
        )
