"""N2.02 explicit, non-authoritative runtime strategy composition.

This module is the top-level wiring boundary between caller-supplied A2.10
``ExecutionStrategy`` implementations and the canonical ``StrategyRegistry``.
It does not choose a level, execute a strategy, create a fallback, or grant any
authority. Missing execution levels remain explicitly missing.

The generic A2.10 ``ExecutionStrategy`` protocol deliberately has no level
attribute. Therefore ``StrategyBinding.level`` is the canonical composition
identity declared by the caller. N2.02 never infers identity or authority from
strategy names, free-form metadata, prompts, or other strategy attributes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from agentx.agent_loop import ExecutionStrategy, StrategyRegistry
from agentx.cognition.router import CANONICAL_EXECUTION_LEVELS, ExecutionLevel

__all__ = [
    "RuntimeStrategyAssembly",
    "StrategyAssemblyError",
    "StrategyBinding",
]


class StrategyAssemblyError(ValueError):
    """Raised when explicit strategy bindings are structurally incoherent."""


@dataclass(frozen=True, slots=True)
class StrategyBinding:
    """One explicit canonical execution-level-to-strategy declaration.

    ``level`` is configuration data only. It grants no permission, risk
    downgrade, budget, verification result, or execution authority.
    """

    level: ExecutionLevel
    strategy: ExecutionStrategy

    def __post_init__(self) -> None:
        if not isinstance(self.level, ExecutionLevel):
            raise TypeError("level must be an ExecutionLevel")
        attempt = getattr(self.strategy, "attempt", None)
        if attempt is None or not callable(attempt):
            raise TypeError("strategy must provide a callable attempt method")


@dataclass(frozen=True, slots=True, init=False)
class RuntimeStrategyAssembly:
    """Immutable explicit strategy set compiled into A2.10's canonical registry.

    Bindings are normalized to the canonical L0-L5 order solely for stable
    inspection. No missing level is filled, no strategy is invoked, and no
    strategy object is reused for multiple levels because the generic A2.10
    protocol exposes no contract declaring such multi-level use safe.
    """

    _bindings: tuple[StrategyBinding, ...]
    _registry: StrategyRegistry

    def __init__(self, bindings: Iterable[StrategyBinding] = ()) -> None:
        try:
            supplied = tuple(bindings)
        except TypeError as exc:
            raise TypeError("bindings must be an iterable of StrategyBinding values") from exc

        by_level: dict[ExecutionLevel, StrategyBinding] = {}
        strategy_levels: dict[int, ExecutionLevel] = {}
        for binding in supplied:
            if not isinstance(binding, StrategyBinding):
                raise TypeError("bindings must contain only StrategyBinding values")
            if binding.level in by_level:
                raise StrategyAssemblyError(
                    f"duplicate strategy binding for {binding.level.value}"
                )

            strategy_identity = id(binding.strategy)
            previous_level = strategy_levels.get(strategy_identity)
            if previous_level is not None and previous_level is not binding.level:
                raise StrategyAssemblyError(
                    "one strategy object cannot be bound to multiple execution levels"
                )

            by_level[binding.level] = binding
            strategy_levels[strategy_identity] = binding.level

        ordered = tuple(
            by_level[level]
            for level in CANONICAL_EXECUTION_LEVELS
            if level in by_level
        )
        registry = StrategyRegistry(
            {binding.level: binding.strategy for binding in ordered}
        )

        object.__setattr__(self, "_bindings", ordered)
        object.__setattr__(self, "_registry", registry)

    @property
    def bindings(self) -> tuple[StrategyBinding, ...]:
        """Return exactly the supplied bindings in canonical level order."""

        return self._bindings

    @property
    def levels(self) -> tuple[ExecutionLevel, ...]:
        """Return exactly the present canonical execution levels."""

        return self._registry.levels()

    @property
    def registry(self) -> StrategyRegistry:
        """Return the canonical immutable A2.10 registry for this assembly."""

        return self._registry

    def strategy_for(self, level: ExecutionLevel) -> ExecutionStrategy | None:
        """Inspect the explicitly configured strategy for ``level``, if present."""

        if not isinstance(level, ExecutionLevel):
            raise TypeError("level must be an ExecutionLevel")
        return self._registry.get(level)
