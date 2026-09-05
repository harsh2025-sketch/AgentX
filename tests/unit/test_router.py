"""Unit tests for A2.07 deterministic execution-level routing."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from agentx.cognition.router import (
    CANONICAL_EXECUTION_LEVELS,
    ExecutionLevel,
    ExecutionLevelRouter,
    RoutingDecision,
    RoutingEvidence,
)


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (
            RoutingEvidence(verified_reusable_result=True),
            ExecutionLevel.L0_CACHE,
        ),
        (
            RoutingEvidence(deterministic_direct_path=True),
            ExecutionLevel.L1_DIRECT,
        ),
        (
            RoutingEvidence(verified_reasoning_free_procedure=True),
            ExecutionLevel.L2_COMPILED,
        ),
        (
            RoutingEvidence(procedure_with_reasoning_gaps=True),
            ExecutionLevel.L3_GUIDED,
        ),
        (
            RoutingEvidence(known_composition_required=True),
            ExecutionLevel.L4_PLANNED,
        ),
        (
            RoutingEvidence(),
            ExecutionLevel.L5_EXPLORATORY,
        ),
    ],
)
def test_all_six_levels(evidence: RoutingEvidence, expected: ExecutionLevel) -> None:
    assert ExecutionLevelRouter().route(evidence).level is expected


def test_canonical_level_order_is_exact_and_deterministic() -> None:
    assert CANONICAL_EXECUTION_LEVELS == (
        ExecutionLevel.L0_CACHE,
        ExecutionLevel.L1_DIRECT,
        ExecutionLevel.L2_COMPILED,
        ExecutionLevel.L3_GUIDED,
        ExecutionLevel.L4_PLANNED,
        ExecutionLevel.L5_EXPLORATORY,
    )
    assert tuple(ExecutionLevel) == CANONICAL_EXECUTION_LEVELS


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (
            RoutingEvidence(
                verified_reusable_result=True,
                deterministic_direct_path=True,
                verified_reasoning_free_procedure=True,
                procedure_with_reasoning_gaps=True,
                known_composition_required=True,
            ),
            ExecutionLevel.L0_CACHE,
        ),
        (
            RoutingEvidence(
                deterministic_direct_path=True,
                verified_reasoning_free_procedure=True,
                procedure_with_reasoning_gaps=True,
                known_composition_required=True,
            ),
            ExecutionLevel.L1_DIRECT,
        ),
        (
            RoutingEvidence(
                verified_reasoning_free_procedure=True,
                procedure_with_reasoning_gaps=True,
                known_composition_required=True,
            ),
            ExecutionLevel.L2_COMPILED,
        ),
        (
            RoutingEvidence(
                procedure_with_reasoning_gaps=True,
                known_composition_required=True,
            ),
            ExecutionLevel.L3_GUIDED,
        ),
    ],
)
def test_lowest_justified_level_wins(
    evidence: RoutingEvidence,
    expected: ExecutionLevel,
) -> None:
    assert ExecutionLevelRouter().route(evidence).level is expected


def test_planned_beats_exploratory_fallback() -> None:
    evidence = RoutingEvidence(known_composition_required=True)
    assert ExecutionLevelRouter().route(evidence).level is ExecutionLevel.L4_PLANNED


def test_insufficient_positive_evidence_fails_closed_to_exploratory() -> None:
    assert ExecutionLevelRouter().route(RoutingEvidence()).level is ExecutionLevel.L5_EXPLORATORY


def test_same_evidence_always_produces_same_decision() -> None:
    evidence = RoutingEvidence(
        deterministic_direct_path=True,
        procedure_with_reasoning_gaps=True,
    )
    router = ExecutionLevelRouter()
    decisions = tuple(router.route(evidence) for _ in range(20))
    assert all(decision == decisions[0] for decision in decisions)
    assert decisions[0].level is ExecutionLevel.L1_DIRECT


def test_separate_router_instances_have_no_shared_state() -> None:
    evidence = RoutingEvidence(verified_reasoning_free_procedure=True)
    left = ExecutionLevelRouter().route(evidence)
    right = ExecutionLevelRouter().route(evidence)
    assert left == right
    assert left.level is ExecutionLevel.L2_COMPILED


def test_routing_does_not_mutate_evidence() -> None:
    evidence = RoutingEvidence(procedure_with_reasoning_gaps=True)
    before = evidence
    ExecutionLevelRouter().route(evidence)
    assert evidence == before


def test_evidence_is_immutable() -> None:
    evidence = RoutingEvidence()
    mutable: Any = evidence
    with pytest.raises(FrozenInstanceError):
        mutable.verified_reusable_result = True


def test_decision_is_immutable() -> None:
    decision = RoutingDecision(ExecutionLevel.L1_DIRECT)
    mutable: Any = decision
    with pytest.raises(FrozenInstanceError):
        mutable.level = ExecutionLevel.L5_EXPLORATORY


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("verified_reusable_result", "true"),
        ("deterministic_direct_path", 1),
        ("verified_reasoning_free_procedure", object()),
        ("procedure_with_reasoning_gaps", None),
        ("known_composition_required", "L0_CACHE"),
    ],
)
def test_evidence_rejects_non_boolean_facts(field_name: str, value: object) -> None:
    kwargs: dict[str, object] = {field_name: value}
    with pytest.raises(TypeError, match=f"{field_name} must be bool"):
        RoutingEvidence(**kwargs)  # type: ignore[arg-type]


def test_router_rejects_untyped_mapping() -> None:
    with pytest.raises(TypeError, match="evidence must be RoutingEvidence"):
        ExecutionLevelRouter().route({"verified_reusable_result": True})  # type: ignore[arg-type]


def test_router_rejects_free_text() -> None:
    with pytest.raises(TypeError, match="evidence must be RoutingEvidence"):
        ExecutionLevelRouter().route("L0 CACHE")  # type: ignore[arg-type]


def test_decision_rejects_raw_level_text() -> None:
    with pytest.raises(TypeError, match="level must be an ExecutionLevel"):
        RoutingDecision("L0_CACHE")  # type: ignore[arg-type]


def test_decision_contains_only_execution_level_data() -> None:
    decision = ExecutionLevelRouter().route(
        RoutingEvidence(verified_reusable_result=True),
    )
    assert decision.level is ExecutionLevel.L0_CACHE
    for forbidden in (
        "permission",
        "authority",
        "risk",
        "budget",
        "execute",
        "verify",
        "task",
        "retry",
        "fallback",
        "escalation",
    ):
        assert not hasattr(decision, forbidden)
