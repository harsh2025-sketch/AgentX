"""Deterministic resource envelopes and accounting for the AgentX Trusted Kernel.

C1.08 bounds autonomous work. A budget allowance is a resource decision only:
it does not grant permission, authorize an action, execute a capability, publish
an event, or otherwise bypass the Action Gate.

Every v1 resource dimension is explicitly bounded. There is deliberately no
implicit or sentinel-based unlimited mode. Wall-clock use is supplied as elapsed
``timedelta`` consumption; this module does not read a clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from threading import Lock
from typing import Final

from agentx.kernel.risk import RiskLevel

_MAX_COUNTER: Final[int] = (1 << 63) - 1


def _validate_duration(name: str, value: timedelta) -> None:
    if not isinstance(value, timedelta):
        raise TypeError(f"{name} must be a timedelta")
    if value < timedelta(0):
        raise ValueError(f"{name} must not be negative")


def _validate_counter(name: str, value: int) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int")
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    if value > _MAX_COUNTER:
        raise OverflowError(f"{name} exceeds the supported counter range")


def _validate_cost(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if value < 0:
        raise ValueError(f"{name} must not be negative")


def _validate_consumption_values(
    *,
    wall_clock: timedelta,
    model_calls: int,
    model_tokens: int,
    research_queries: int,
    machine_actions: int,
    repair_attempts: int,
    external_cost: Decimal,
) -> None:
    _validate_duration("wall_clock", wall_clock)
    _validate_counter("model_calls", model_calls)
    _validate_counter("model_tokens", model_tokens)
    _validate_counter("research_queries", research_queries)
    _validate_counter("machine_actions", machine_actions)
    _validate_counter("repair_attempts", repair_attempts)
    _validate_cost("external_cost", external_cost)


@dataclass(frozen=True, slots=True)
class ResourceEnvelope:
    """Immutable explicit upper bounds for one autonomous-work budget.

    ``max_model_tokens`` is the aggregate input-plus-output model token budget.
    ``max_external_cost`` is a finite non-negative Decimal in one caller-chosen
    accounting unit; this layer performs no currency conversion or billing.
    """

    max_wall_clock: timedelta
    max_model_calls: int
    max_model_tokens: int
    max_research_queries: int
    max_machine_actions: int
    max_repair_attempts: int
    max_external_cost: Decimal
    max_risk_level: RiskLevel

    def __post_init__(self) -> None:
        _validate_consumption_values(
            wall_clock=self.max_wall_clock,
            model_calls=self.max_model_calls,
            model_tokens=self.max_model_tokens,
            research_queries=self.max_research_queries,
            machine_actions=self.max_machine_actions,
            repair_attempts=self.max_repair_attempts,
            external_cost=self.max_external_cost,
        )
        if not isinstance(self.max_risk_level, RiskLevel):
            raise TypeError("max_risk_level must be a RiskLevel")


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    """Immutable snapshot of cumulative consumption."""

    wall_clock: timedelta
    model_calls: int
    model_tokens: int
    research_queries: int
    machine_actions: int
    repair_attempts: int
    external_cost: Decimal

    def __post_init__(self) -> None:
        _validate_consumption_values(
            wall_clock=self.wall_clock,
            model_calls=self.model_calls,
            model_tokens=self.model_tokens,
            research_queries=self.research_queries,
            machine_actions=self.machine_actions,
            repair_attempts=self.repair_attempts,
            external_cost=self.external_cost,
        )

    @classmethod
    def zero(cls) -> ResourceUsage:
        """Return an explicit zero-consumption snapshot."""
        return cls(
            wall_clock=timedelta(0),
            model_calls=0,
            model_tokens=0,
            research_queries=0,
            machine_actions=0,
            repair_attempts=0,
            external_cost=Decimal("0"),
        )


@dataclass(frozen=True, slots=True)
class ResourceDelta:
    """Immutable non-negative resource consumption requested by one operation.

    ``model_tokens`` uses the same aggregate input-plus-output token accounting
    as the envelope. ``external_cost`` must use the envelope's accounting unit.
    """

    wall_clock: timedelta
    model_calls: int
    model_tokens: int
    research_queries: int
    machine_actions: int
    repair_attempts: int
    external_cost: Decimal

    def __post_init__(self) -> None:
        _validate_consumption_values(
            wall_clock=self.wall_clock,
            model_calls=self.model_calls,
            model_tokens=self.model_tokens,
            research_queries=self.research_queries,
            machine_actions=self.machine_actions,
            repair_attempts=self.repair_attempts,
            external_cost=self.external_cost,
        )

    @classmethod
    def zero(cls) -> ResourceDelta:
        """Return an explicit zero-consumption delta."""
        return cls(
            wall_clock=timedelta(0),
            model_calls=0,
            model_tokens=0,
            research_queries=0,
            machine_actions=0,
            repair_attempts=0,
            external_cost=Decimal("0"),
        )


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    """Inert request combining consumption with the operation's risk level."""

    delta: ResourceDelta
    risk_level: RiskLevel

    def __post_init__(self) -> None:
        if not isinstance(self.delta, ResourceDelta):
            raise TypeError("delta must be a ResourceDelta")
        if not isinstance(self.risk_level, RiskLevel):
            raise TypeError("risk_level must be a RiskLevel")


class BudgetDecision(Enum):
    """Canonical resource-budget evaluation outcome."""

    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True, slots=True)
class BudgetResult:
    """Immutable budget decision and usage state resulting from that decision."""

    decision: BudgetDecision
    reason: str
    usage_before: ResourceUsage
    usage_after: ResourceUsage

    def __post_init__(self) -> None:
        if not isinstance(self.decision, BudgetDecision):
            raise TypeError("decision must be a BudgetDecision")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")
        if not self.reason or self.reason != self.reason.strip():
            raise ValueError("reason must be non-empty and trimmed")
        if not isinstance(self.usage_before, ResourceUsage):
            raise TypeError("usage_before must be a ResourceUsage")
        if not isinstance(self.usage_after, ResourceUsage):
            raise TypeError("usage_after must be a ResourceUsage")


class BudgetEvaluator:
    """Pure deterministic evaluation of usage plus request against an envelope."""

    __slots__ = ()

    def evaluate(
        self,
        envelope: ResourceEnvelope,
        usage: ResourceUsage,
        request: ResourceRequest,
    ) -> BudgetResult:
        if not isinstance(envelope, ResourceEnvelope):
            raise TypeError("envelope must be a ResourceEnvelope")
        if not isinstance(usage, ResourceUsage):
            raise TypeError("usage must be a ResourceUsage")
        if not isinstance(request, ResourceRequest):
            raise TypeError("request must be a ResourceRequest")

        self._validate_usage_within_envelope(envelope, usage)

        if request.risk_level > envelope.max_risk_level:
            return self._deny(
                usage,
                (
                    f"DENY: requested risk {request.risk_level.value} exceeds "
                    f"resource ceiling {envelope.max_risk_level.value}."
                ),
            )

        delta = request.delta
        if delta.wall_clock > envelope.max_wall_clock - usage.wall_clock:
            return self._deny(
                usage,
                "DENY: wall_clock request would exceed its explicit resource limit.",
            )
        if delta.model_calls > envelope.max_model_calls - usage.model_calls:
            return self._deny(
                usage,
                "DENY: model_calls request would exceed its explicit resource limit.",
            )
        if delta.model_tokens > envelope.max_model_tokens - usage.model_tokens:
            return self._deny(
                usage,
                "DENY: model_tokens request would exceed its explicit resource limit.",
            )
        if delta.research_queries > envelope.max_research_queries - usage.research_queries:
            return self._deny(
                usage,
                "DENY: research_queries request would exceed its explicit resource limit.",
            )
        if delta.machine_actions > envelope.max_machine_actions - usage.machine_actions:
            return self._deny(
                usage,
                "DENY: machine_actions request would exceed its explicit resource limit.",
            )
        if delta.repair_attempts > envelope.max_repair_attempts - usage.repair_attempts:
            return self._deny(
                usage,
                "DENY: repair_attempts request would exceed its explicit resource limit.",
            )
        if delta.external_cost > envelope.max_external_cost - usage.external_cost:
            return self._deny(
                usage,
                "DENY: external_cost request would exceed its explicit resource limit.",
            )

        next_usage = ResourceUsage(
            wall_clock=usage.wall_clock + delta.wall_clock,
            model_calls=usage.model_calls + delta.model_calls,
            model_tokens=usage.model_tokens + delta.model_tokens,
            research_queries=usage.research_queries + delta.research_queries,
            machine_actions=usage.machine_actions + delta.machine_actions,
            repair_attempts=usage.repair_attempts + delta.repair_attempts,
            external_cost=usage.external_cost + delta.external_cost,
        )
        return BudgetResult(
            decision=BudgetDecision.ALLOW,
            reason=(
                "ALLOW: requested consumption fits every explicit resource limit and "
                "risk ceiling; this budget result does not grant action permission."
            ),
            usage_before=usage,
            usage_after=next_usage,
        )

    @staticmethod
    def _deny(usage: ResourceUsage, reason: str) -> BudgetResult:
        return BudgetResult(
            decision=BudgetDecision.DENY,
            reason=reason,
            usage_before=usage,
            usage_after=usage,
        )

    @staticmethod
    def _validate_usage_within_envelope(
        envelope: ResourceEnvelope,
        usage: ResourceUsage,
    ) -> None:
        if usage.wall_clock > envelope.max_wall_clock:
            raise ValueError("usage.wall_clock exceeds envelope")
        if usage.model_calls > envelope.max_model_calls:
            raise ValueError("usage.model_calls exceeds envelope")
        if usage.model_tokens > envelope.max_model_tokens:
            raise ValueError("usage.model_tokens exceeds envelope")
        if usage.research_queries > envelope.max_research_queries:
            raise ValueError("usage.research_queries exceeds envelope")
        if usage.machine_actions > envelope.max_machine_actions:
            raise ValueError("usage.machine_actions exceeds envelope")
        if usage.repair_attempts > envelope.max_repair_attempts:
            raise ValueError("usage.repair_attempts exceeds envelope")
        if usage.external_cost > envelope.max_external_cost:
            raise ValueError("usage.external_cost exceeds envelope")


class ResourceBudget:
    """Process-local cumulative accounting with atomic check-and-consume."""

    __slots__ = ("_envelope", "_lock", "_usage")

    def __init__(self, envelope: ResourceEnvelope) -> None:
        if not isinstance(envelope, ResourceEnvelope):
            raise TypeError("envelope must be a ResourceEnvelope")
        self._envelope = envelope
        self._usage = ResourceUsage.zero()
        self._lock = Lock()

    @property
    def envelope(self) -> ResourceEnvelope:
        """Return the immutable envelope."""
        return self._envelope

    def snapshot(self) -> ResourceUsage:
        """Return the current immutable usage snapshot."""
        with self._lock:
            return self._usage

    def evaluate(self, request: ResourceRequest) -> BudgetResult:
        """Evaluate a consistent current snapshot without consuming budget."""
        if not isinstance(request, ResourceRequest):
            raise TypeError("request must be a ResourceRequest")
        with self._lock:
            return BudgetEvaluator().evaluate(self._envelope, self._usage, request)

    def check_and_consume(self, request: ResourceRequest) -> BudgetResult:
        """Atomically evaluate and consume only when the budget result is ALLOW."""
        if not isinstance(request, ResourceRequest):
            raise TypeError("request must be a ResourceRequest")
        with self._lock:
            result = BudgetEvaluator().evaluate(self._envelope, self._usage, request)
            if result.decision is BudgetDecision.ALLOW:
                self._usage = result.usage_after
            return result


__all__ = [
    "BudgetDecision",
    "BudgetEvaluator",
    "BudgetResult",
    "ResourceBudget",
    "ResourceDelta",
    "ResourceEnvelope",
    "ResourceRequest",
    "ResourceUsage",
]
