"""A2.07 canonical deterministic L0-L5 execution-level routing contracts.

The Router consumes explicit typed facts and selects the cheapest justified
execution strategy class. It never executes the selected strategy, performs
I/O, invokes a model, grants authority, or mutates runtime state.

Absence of positive evidence for L0-L4 fails closed to L5_EXPLORATORY. That
fallback is a routing classification only; it does not authorize research,
experimentation, network access, or any other action.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "CANONICAL_EXECUTION_LEVELS",
    "ExecutionLevel",
    "ExecutionLevelRouter",
    "RoutingDecision",
    "RoutingEvidence",
]


class ExecutionLevel(StrEnum):
    """Canonical AgentX execution hierarchy, ordered cheapest to most open-ended."""

    L0_CACHE = "L0_CACHE"
    L1_DIRECT = "L1_DIRECT"
    L2_COMPILED = "L2_COMPILED"
    L3_GUIDED = "L3_GUIDED"
    L4_PLANNED = "L4_PLANNED"
    L5_EXPLORATORY = "L5_EXPLORATORY"


CANONICAL_EXECUTION_LEVELS: Final[tuple[ExecutionLevel, ...]] = (
    ExecutionLevel.L0_CACHE,
    ExecutionLevel.L1_DIRECT,
    ExecutionLevel.L2_COMPILED,
    ExecutionLevel.L3_GUIDED,
    ExecutionLevel.L4_PLANNED,
    ExecutionLevel.L5_EXPLORATORY,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class RoutingEvidence:
    """Explicit positive facts available to the deterministic Router.

    Each field states that a cheaper strategy is actually justified. False
    means only that this evidence object does not justify that level; it does
    not claim that the underlying resource does not exist.

    The Router deliberately accepts no free text, identifiers, stores,
    registries, providers, callbacks, or executable objects.
    """

    verified_reusable_result: bool = False
    deterministic_direct_path: bool = False
    verified_reasoning_free_procedure: bool = False
    procedure_with_reasoning_gaps: bool = False
    known_composition_required: bool = False

    def __post_init__(self) -> None:
        values = (
            ("verified_reusable_result", self.verified_reusable_result),
            ("deterministic_direct_path", self.deterministic_direct_path),
            ("verified_reasoning_free_procedure", self.verified_reasoning_free_procedure),
            ("procedure_with_reasoning_gaps", self.procedure_with_reasoning_gaps),
            ("known_composition_required", self.known_composition_required),
        )
        for field_name, value in values:
            if not isinstance(value, bool):
                raise TypeError(f"{field_name} must be bool")


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Inert routing result naming only the strategy class to attempt next."""

    level: ExecutionLevel

    def __post_init__(self) -> None:
        if not isinstance(self.level, ExecutionLevel):
            raise TypeError("level must be an ExecutionLevel")


class ExecutionLevelRouter:
    """Stateless deterministic selector for the canonical AgentX hierarchy."""

    __slots__ = ()

    def route(self, evidence: RoutingEvidence) -> RoutingDecision:
        """Select the lowest execution level justified by explicit evidence."""
        if not isinstance(evidence, RoutingEvidence):
            raise TypeError("evidence must be RoutingEvidence")

        if evidence.verified_reusable_result:
            level = ExecutionLevel.L0_CACHE
        elif evidence.deterministic_direct_path:
            level = ExecutionLevel.L1_DIRECT
        elif evidence.verified_reasoning_free_procedure:
            level = ExecutionLevel.L2_COMPILED
        elif evidence.procedure_with_reasoning_gaps:
            level = ExecutionLevel.L3_GUIDED
        elif evidence.known_composition_required:
            level = ExecutionLevel.L4_PLANNED
        else:
            level = ExecutionLevel.L5_EXPLORATORY

        return RoutingDecision(level)
