"""Unit tests for N2.02 canonical runtime strategy assembly."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agentx.agent_loop import StrategyRegistry, StrategyResult
from agentx.cognition.router import CANONICAL_EXECUTION_LEVELS, ExecutionLevel
from agentx.core.execution import ExecutionContext
from agentx.core.tasks import Task
from agentx.strategy_assembly import (
    RuntimeStrategyAssembly,
    StrategyAssemblyError,
    StrategyBinding,
)


class NeverCalledStrategy:
    def __init__(self, name: str = "strategy") -> None:
        self.name = name
        self.calls = 0

    def attempt(
        self,
        task: Task,
        context: ExecutionContext,
        level: ExecutionLevel,
    ) -> StrategyResult:
        del task, context, level
        self.calls += 1
        return StrategyResult.unavailable("unit-test strategy was invoked")


class MalformedStrategy:
    attempt = "not callable"


def _binding(level: ExecutionLevel, name: str | None = None) -> StrategyBinding:
    return StrategyBinding(
        level=level,
        strategy=NeverCalledStrategy(name or level.value),
    )


def test_empty_explicit_assembly_is_legal_and_contains_no_defaults() -> None:
    assembly = RuntimeStrategyAssembly(())

    assert assembly.bindings == ()
    assert assembly.levels == ()
    assert len(assembly.registry) == 0
    for level in CANONICAL_EXECUTION_LEVELS:
        assert assembly.strategy_for(level) is None
        assert assembly.registry.get(level) is None


def test_one_level_assembly_exposes_exact_strategy() -> None:
    strategy = NeverCalledStrategy()
    assembly = RuntimeStrategyAssembly(
        (StrategyBinding(level=ExecutionLevel.L1_DIRECT, strategy=strategy),)
    )

    assert assembly.levels == (ExecutionLevel.L1_DIRECT,)
    assert assembly.strategy_for(ExecutionLevel.L1_DIRECT) is strategy
    assert assembly.registry.get(ExecutionLevel.L1_DIRECT) is strategy
    assert strategy.calls == 0


def test_all_six_levels_reuse_exact_canonical_execution_levels() -> None:
    bindings = tuple(_binding(level) for level in CANONICAL_EXECUTION_LEVELS)

    assembly = RuntimeStrategyAssembly(bindings)

    assert assembly.levels == CANONICAL_EXECUTION_LEVELS
    assert tuple(binding.level for binding in assembly.bindings) == CANONICAL_EXECUTION_LEVELS
    assert all(type(binding.level) is ExecutionLevel for binding in assembly.bindings)
    assert len(assembly.registry) == 6


def test_duplicate_level_binding_is_rejected() -> None:
    with pytest.raises(StrategyAssemblyError, match="duplicate strategy binding"):
        RuntimeStrategyAssembly(
            (
                _binding(ExecutionLevel.L2_COMPILED, "first"),
                _binding(ExecutionLevel.L2_COMPILED, "second"),
            )
        )


def test_same_strategy_object_cannot_be_mapped_to_multiple_levels() -> None:
    strategy = NeverCalledStrategy()

    with pytest.raises(StrategyAssemblyError, match="multiple execution levels"):
        RuntimeStrategyAssembly(
            (
                StrategyBinding(ExecutionLevel.L1_DIRECT, strategy),
                StrategyBinding(ExecutionLevel.L2_COMPILED, strategy),
            )
        )

    assert strategy.calls == 0


def test_registry_composition_is_stable_in_canonical_order() -> None:
    reverse_bindings = tuple(_binding(level) for level in reversed(CANONICAL_EXECUTION_LEVELS))

    first = RuntimeStrategyAssembly(reverse_bindings)
    second = RuntimeStrategyAssembly(reversed(reverse_bindings))

    assert first.levels == CANONICAL_EXECUTION_LEVELS
    assert second.levels == CANONICAL_EXECUTION_LEVELS
    assert tuple(binding.level for binding in first.bindings) == CANONICAL_EXECUTION_LEVELS
    assert tuple(binding.level for binding in second.bindings) == CANONICAL_EXECUTION_LEVELS


def test_missing_levels_stay_missing_without_fabricated_availability() -> None:
    l0 = NeverCalledStrategy("cache")
    l4 = NeverCalledStrategy("planned")
    assembly = RuntimeStrategyAssembly(
        (
            StrategyBinding(ExecutionLevel.L4_PLANNED, l4),
            StrategyBinding(ExecutionLevel.L0_CACHE, l0),
        )
    )

    assert assembly.levels == (ExecutionLevel.L0_CACHE, ExecutionLevel.L4_PLANNED)
    assert assembly.strategy_for(ExecutionLevel.L0_CACHE) is l0
    assert assembly.strategy_for(ExecutionLevel.L4_PLANNED) is l4
    for level in (
        ExecutionLevel.L1_DIRECT,
        ExecutionLevel.L2_COMPILED,
        ExecutionLevel.L3_GUIDED,
        ExecutionLevel.L5_EXPLORATORY,
    ):
        assert assembly.strategy_for(level) is None


def test_malformed_level_is_rejected_without_coercion() -> None:
    strategy = NeverCalledStrategy()

    with pytest.raises(TypeError, match="ExecutionLevel"):
        StrategyBinding("L1_DIRECT", strategy)  # type: ignore[arg-type]

    assert strategy.calls == 0


def test_malformed_strategy_is_rejected() -> None:
    with pytest.raises(TypeError, match="callable attempt"):
        StrategyBinding(
            ExecutionLevel.L1_DIRECT,
            MalformedStrategy(),  # type: ignore[arg-type]
        )

    with pytest.raises(TypeError, match="callable attempt"):
        StrategyBinding(ExecutionLevel.L1_DIRECT, object())  # type: ignore[arg-type]


def test_malformed_binding_collection_is_rejected() -> None:
    with pytest.raises(TypeError, match="StrategyBinding"):
        RuntimeStrategyAssembly((object(),))  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="iterable"):
        RuntimeStrategyAssembly(None)  # type: ignore[arg-type]


def test_assembly_never_invokes_any_strategy() -> None:
    strategies = [NeverCalledStrategy(level.value) for level in CANONICAL_EXECUTION_LEVELS]
    bindings = tuple(
        StrategyBinding(level, strategy)
        for level, strategy in zip(CANONICAL_EXECUTION_LEVELS, strategies, strict=True)
    )

    assembly = RuntimeStrategyAssembly(bindings)

    assert assembly.levels == CANONICAL_EXECUTION_LEVELS
    assert [strategy.calls for strategy in strategies] == [0] * 6


def test_assembly_and_bindings_are_immutable_after_construction() -> None:
    strategy = NeverCalledStrategy()
    binding = StrategyBinding(ExecutionLevel.L1_DIRECT, strategy)
    assembly = RuntimeStrategyAssembly((binding,))

    with pytest.raises(FrozenInstanceError):
        binding.level = ExecutionLevel.L2_COMPILED  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        assembly._bindings = ()  # type: ignore[misc]

    assert assembly.bindings == (binding,)
    assert assembly.strategy_for(ExecutionLevel.L1_DIRECT) is strategy


def test_real_canonical_strategy_registry_is_the_composition_product() -> None:
    strategy = NeverCalledStrategy()
    assembly = RuntimeStrategyAssembly((StrategyBinding(ExecutionLevel.L3_GUIDED, strategy),))

    registry = assembly.registry

    assert isinstance(registry, StrategyRegistry)
    assert registry.levels() == (ExecutionLevel.L3_GUIDED,)
    assert registry.get(ExecutionLevel.L3_GUIDED) is strategy
