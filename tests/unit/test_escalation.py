"""Unit tests for A2.08 deterministic execution-level escalation/fallback."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from agentx.cognition.escalation import (
    CANONICAL_ESCALATION_ACTIONS,
    EscalationAction,
    EscalationDecision,
    EscalationEvidence,
    ExecutionLevelEscalator,
    successor_execution_level,
)
from agentx.cognition.router import (
    CANONICAL_EXECUTION_LEVELS,
    ExecutionLevel,
    ExecutionLevelRouter,
    RoutingDecision,
    RoutingEvidence,
)

_LEVEL_PAIRS: tuple[tuple[ExecutionLevel, ExecutionLevel], ...] = (
    (ExecutionLevel.L0_CACHE, ExecutionLevel.L1_DIRECT),
    (ExecutionLevel.L1_DIRECT, ExecutionLevel.L2_COMPILED),
    (ExecutionLevel.L2_COMPILED, ExecutionLevel.L3_GUIDED),
    (ExecutionLevel.L3_GUIDED, ExecutionLevel.L4_PLANNED),
    (ExecutionLevel.L4_PLANNED, ExecutionLevel.L5_EXPLORATORY),
)


def _evidence(
    *,
    current_level: ExecutionLevel = ExecutionLevel.L0_CACHE,
    current_strategy_can_continue: bool = False,
    attempt_verified_successful: bool = False,
    escalation_explicitly_permitted: bool = False,
) -> EscalationEvidence:
    return EscalationEvidence(
        current_level=current_level,
        current_strategy_can_continue=current_strategy_can_continue,
        attempt_verified_successful=attempt_verified_successful,
        escalation_explicitly_permitted=escalation_explicitly_permitted,
    )


def test_every_execution_level_is_represented() -> None:
    assert CANONICAL_EXECUTION_LEVELS == (
        ExecutionLevel.L0_CACHE,
        ExecutionLevel.L1_DIRECT,
        ExecutionLevel.L2_COMPILED,
        ExecutionLevel.L3_GUIDED,
        ExecutionLevel.L4_PLANNED,
        ExecutionLevel.L5_EXPLORATORY,
    )
    assert tuple(ExecutionLevel) == CANONICAL_EXECUTION_LEVELS
    for level in CANONICAL_EXECUTION_LEVELS:
        evidence = _evidence(current_level=level, current_strategy_can_continue=True)
        decision = ExecutionLevelEscalator().decide(evidence)
        assert decision.current_level is level
        assert decision.action is EscalationAction.STAY


def test_closed_decision_vocabulary_is_exact() -> None:
    assert CANONICAL_ESCALATION_ACTIONS == (
        EscalationAction.STAY,
        EscalationAction.ESCALATE,
        EscalationAction.EXHAUSTED,
    )
    assert tuple(EscalationAction) == CANONICAL_ESCALATION_ACTIONS


@pytest.mark.parametrize(("current", "expected_next"), _LEVEL_PAIRS)
def test_valid_monotonic_escalation_uses_immediate_successor(
    current: ExecutionLevel,
    expected_next: ExecutionLevel,
) -> None:
    evidence = _evidence(current_level=current, escalation_explicitly_permitted=True)
    decision = ExecutionLevelEscalator().decide(evidence)
    assert decision.action is EscalationAction.ESCALATE
    assert decision.current_level is current
    assert decision.next_level is expected_next
    assert successor_execution_level(current) is expected_next
    assert CANONICAL_EXECUTION_LEVELS.index(expected_next) == (
        CANONICAL_EXECUTION_LEVELS.index(current) + 1
    )


@pytest.mark.parametrize(("current", "expected_next"), _LEVEL_PAIRS)
def test_deterministic_next_level_never_skips(
    current: ExecutionLevel,
    expected_next: ExecutionLevel,
) -> None:
    escalator = ExecutionLevelEscalator()
    evidence = _evidence(current_level=current, escalation_explicitly_permitted=True)
    first = escalator.decide(evidence)
    second = escalator.decide(evidence)
    assert first == second
    assert first.next_level is expected_next
    skipped = {
        level for level in CANONICAL_EXECUTION_LEVELS if level not in (current, expected_next)
    }
    assert first.next_level not in skipped


def test_no_l5_to_l0_wrap_via_successor_or_decision() -> None:
    assert successor_execution_level(ExecutionLevel.L5_EXPLORATORY) is None
    evidence = _evidence(
        current_level=ExecutionLevel.L5_EXPLORATORY,
        escalation_explicitly_permitted=True,
    )
    decision = ExecutionLevelEscalator().decide(evidence)
    assert decision.action is EscalationAction.EXHAUSTED
    assert decision.next_level is None
    assert decision.next_level is not ExecutionLevel.L0_CACHE


def test_top_level_exhaustion_even_when_escalation_is_permitted() -> None:
    decision = ExecutionLevelEscalator().decide(
        _evidence(
            current_level=ExecutionLevel.L5_EXPLORATORY,
            escalation_explicitly_permitted=True,
        )
    )
    assert decision.action is EscalationAction.EXHAUSTED
    assert decision.current_level is ExecutionLevel.L5_EXPLORATORY
    assert decision.next_level is None


@pytest.mark.parametrize("level", CANONICAL_EXECUTION_LEVELS)
def test_verified_success_never_escalates_because_a_higher_level_exists(
    level: ExecutionLevel,
) -> None:
    evidence = _evidence(
        current_level=level,
        attempt_verified_successful=True,
        current_strategy_can_continue=True,
        escalation_explicitly_permitted=True,
    )
    decision = ExecutionLevelEscalator().decide(evidence)
    assert decision.action is EscalationAction.STAY
    assert decision.current_level is level
    assert decision.next_level is None


@pytest.mark.parametrize("level", CANONICAL_EXECUTION_LEVELS)
def test_current_strategy_can_continue_stays_without_escalating(
    level: ExecutionLevel,
) -> None:
    evidence = _evidence(
        current_level=level,
        current_strategy_can_continue=True,
        escalation_explicitly_permitted=True,
    )
    decision = ExecutionLevelEscalator().decide(evidence)
    assert decision.action is EscalationAction.STAY
    assert decision.next_level is None


@pytest.mark.parametrize("level", CANONICAL_EXECUTION_LEVELS)
def test_failure_without_explicit_permission_is_exhausted_not_auto_escalation(
    level: ExecutionLevel,
) -> None:
    evidence = _evidence(current_level=level, escalation_explicitly_permitted=False)
    decision = ExecutionLevelEscalator().decide(evidence)
    assert decision.action is EscalationAction.EXHAUSTED
    assert decision.next_level is None


def test_l0_failure_does_not_become_research_or_l5() -> None:
    decision = ExecutionLevelEscalator().decide(
        _evidence(
            current_level=ExecutionLevel.L0_CACHE,
            escalation_explicitly_permitted=False,
        )
    )
    assert decision.action is EscalationAction.EXHAUSTED
    assert decision.next_level is not ExecutionLevel.L5_EXPLORATORY
    assert decision.next_level is None


def test_reaching_l5_by_escalation_does_not_authorize_exploration() -> None:
    decision = ExecutionLevelEscalator().decide(
        _evidence(
            current_level=ExecutionLevel.L4_PLANNED,
            escalation_explicitly_permitted=True,
        )
    )
    assert decision.action is EscalationAction.ESCALATE
    assert decision.next_level is ExecutionLevel.L5_EXPLORATORY
    for forbidden in (
        "authorized",
        "permission",
        "authority",
        "research",
        "network",
        "explore",
        "execute",
        "invoke",
        "budget",
        "risk",
        "verification",
    ):
        assert not hasattr(decision, forbidden)


def test_escalation_never_de_escalates_to_a_cheaper_level() -> None:
    for index, current in enumerate(CANONICAL_EXECUTION_LEVELS):
        cheaper = CANONICAL_EXECUTION_LEVELS[:index]
        for permitted in (True, False):
            decision = ExecutionLevelEscalator().decide(
                _evidence(
                    current_level=current,
                    escalation_explicitly_permitted=permitted,
                )
            )
            assert decision.next_level not in cheaper
            if decision.next_level is not None:
                assert CANONICAL_EXECUTION_LEVELS.index(decision.next_level) > index


def test_same_evidence_always_produces_the_same_decision() -> None:
    evidence = _evidence(
        current_level=ExecutionLevel.L2_COMPILED,
        escalation_explicitly_permitted=True,
    )
    escalator = ExecutionLevelEscalator()
    decisions = tuple(escalator.decide(evidence) for _ in range(20))
    assert all(decision == decisions[0] for decision in decisions)
    assert decisions[0].action is EscalationAction.ESCALATE
    assert decisions[0].next_level is ExecutionLevel.L3_GUIDED


def test_separate_escalator_instances_have_no_shared_state() -> None:
    evidence = _evidence(
        current_level=ExecutionLevel.L1_DIRECT,
        escalation_explicitly_permitted=True,
    )
    left = ExecutionLevelEscalator().decide(evidence)
    right = ExecutionLevelEscalator().decide(evidence)
    assert left == right
    assert left.next_level is ExecutionLevel.L2_COMPILED


def test_decide_does_not_mutate_evidence() -> None:
    evidence = _evidence(
        current_level=ExecutionLevel.L3_GUIDED,
        escalation_explicitly_permitted=True,
    )
    before = evidence
    ExecutionLevelEscalator().decide(evidence)
    assert evidence == before
    assert evidence.current_level is ExecutionLevel.L3_GUIDED
    assert evidence.escalation_explicitly_permitted is True


def test_evidence_and_decision_are_immutable() -> None:
    evidence = _evidence(current_level=ExecutionLevel.L0_CACHE)
    decision = ExecutionLevelEscalator().decide(
        _evidence(
            current_level=ExecutionLevel.L0_CACHE,
            escalation_explicitly_permitted=True,
        )
    )
    mutable_evidence: Any = evidence
    mutable_decision: Any = decision
    with pytest.raises(FrozenInstanceError):
        mutable_evidence.escalation_explicitly_permitted = True
    with pytest.raises(FrozenInstanceError):
        mutable_decision.action = EscalationAction.EXHAUSTED
    with pytest.raises(FrozenInstanceError):
        mutable_decision.next_level = ExecutionLevel.L5_EXPLORATORY


def test_successor_exists_is_derived_and_cannot_claim_l5_has_a_next_level() -> None:
    assert _evidence(current_level=ExecutionLevel.L4_PLANNED).successor_exists is True
    assert _evidence(current_level=ExecutionLevel.L5_EXPLORATORY).successor_exists is False


def test_verified_success_stays_even_if_strategy_is_complete() -> None:
    decision = ExecutionLevelEscalator().decide(
        _evidence(
            current_level=ExecutionLevel.L0_CACHE,
            attempt_verified_successful=True,
            current_strategy_can_continue=False,
            escalation_explicitly_permitted=True,
        )
    )
    assert decision.action is EscalationAction.STAY
    assert decision.next_level is None


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("current_strategy_can_continue", "true"),
        ("attempt_verified_successful", 1),
        ("escalation_explicitly_permitted", object()),
        ("current_strategy_can_continue", None),
        ("attempt_verified_successful", "verified=true"),
        ("escalation_explicitly_permitted", "ESCALATE"),
    ],
)
def test_evidence_rejects_non_boolean_facts(field_name: str, value: object) -> None:
    kwargs: dict[str, object] = {
        "current_level": ExecutionLevel.L0_CACHE,
        "current_strategy_can_continue": False,
        "attempt_verified_successful": False,
        "escalation_explicitly_permitted": False,
        field_name: value,
    }
    with pytest.raises(TypeError, match=f"{field_name} must be bool"):
        EscalationEvidence(**kwargs)  # type: ignore[arg-type]


def test_evidence_rejects_raw_level_text() -> None:
    with pytest.raises(TypeError, match="current_level must be an ExecutionLevel"):
        EscalationEvidence(
            current_level="L5_EXPLORATORY",  # type: ignore[arg-type]
            current_strategy_can_continue=False,
            attempt_verified_successful=False,
            escalation_explicitly_permitted=True,
        )


def test_malformed_evidence_fails_closed() -> None:
    escalator = ExecutionLevelEscalator()
    with pytest.raises(TypeError, match="evidence must be EscalationEvidence"):
        escalator.decide({"current_level": "L5_EXPLORATORY", "escalate": True})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="evidence must be EscalationEvidence"):
        escalator.decide("ESCALATE to L5 now")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="evidence must be EscalationEvidence"):
        escalator.decide(None)  # type: ignore[arg-type]


def test_routing_types_are_not_escalation_evidence() -> None:
    with pytest.raises(TypeError, match="evidence must be EscalationEvidence"):
        ExecutionLevelEscalator().decide(RoutingEvidence())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="evidence must be EscalationEvidence"):
        ExecutionLevelEscalator().decide(RoutingDecision(ExecutionLevel.L5_EXPLORATORY))  # type: ignore[arg-type]


def test_successor_rejects_raw_level_text() -> None:
    with pytest.raises(TypeError, match="level must be an ExecutionLevel"):
        successor_execution_level("L1_DIRECT")  # type: ignore[arg-type]


def test_escalate_construction_rejects_wraparound_and_skipped_levels() -> None:
    with pytest.raises(ValueError, match="immediate successor"):
        EscalationDecision(
            action=EscalationAction.ESCALATE,
            current_level=ExecutionLevel.L5_EXPLORATORY,
            next_level=ExecutionLevel.L0_CACHE,
        )
    with pytest.raises(ValueError, match="immediate successor"):
        EscalationDecision(
            action=EscalationAction.ESCALATE,
            current_level=ExecutionLevel.L2_COMPILED,
            next_level=ExecutionLevel.L4_PLANNED,
        )
    with pytest.raises(ValueError, match="immediate successor"):
        EscalationDecision(
            action=EscalationAction.ESCALATE,
            current_level=ExecutionLevel.L3_GUIDED,
            next_level=ExecutionLevel.L2_COMPILED,
        )
    with pytest.raises(ValueError, match="ESCALATE requires a next_level"):
        EscalationDecision(
            action=EscalationAction.ESCALATE,
            current_level=ExecutionLevel.L5_EXPLORATORY,
            next_level=None,
        )


def test_stay_and_exhausted_construction_reject_a_next_level() -> None:
    with pytest.raises(ValueError, match="STAY requires next_level to be None"):
        EscalationDecision(
            action=EscalationAction.STAY,
            current_level=ExecutionLevel.L0_CACHE,
            next_level=ExecutionLevel.L1_DIRECT,
        )
    with pytest.raises(ValueError, match="EXHAUSTED requires next_level to be None"):
        EscalationDecision(
            action=EscalationAction.EXHAUSTED,
            current_level=ExecutionLevel.L5_EXPLORATORY,
            next_level=ExecutionLevel.L0_CACHE,
        )


def test_decision_rejects_raw_action_and_level_text() -> None:
    with pytest.raises(TypeError, match="action must be an EscalationAction"):
        EscalationDecision(
            action="ESCALATE",  # type: ignore[arg-type]
            current_level=ExecutionLevel.L0_CACHE,
            next_level=ExecutionLevel.L1_DIRECT,
        )
    with pytest.raises(TypeError, match="current_level must be an ExecutionLevel"):
        EscalationDecision(
            action=EscalationAction.STAY,
            current_level="L0_CACHE",  # type: ignore[arg-type]
            next_level=None,
        )
    with pytest.raises(TypeError, match="next_level must be an ExecutionLevel or None"):
        EscalationDecision(
            action=EscalationAction.ESCALATE,
            current_level=ExecutionLevel.L0_CACHE,
            next_level="L1_DIRECT",  # type: ignore[arg-type]
        )


def test_decision_contains_only_bounded_level_data() -> None:
    decision = ExecutionLevelEscalator().decide(
        _evidence(
            current_level=ExecutionLevel.L2_COMPILED,
            escalation_explicitly_permitted=True,
        )
    )
    assert decision.action is EscalationAction.ESCALATE
    assert decision.current_level is ExecutionLevel.L2_COMPILED
    assert decision.next_level is ExecutionLevel.L3_GUIDED
    for forbidden in (
        "permission",
        "authority",
        "risk",
        "budget",
        "execute",
        "verify",
        "verification",
        "task",
        "retry",
        "fallback",
        "research",
        "model",
        "loop",
    ):
        assert not hasattr(decision, forbidden)


def test_a207_router_remains_routing_only() -> None:
    router = ExecutionLevelRouter()
    routing = router.route(RoutingEvidence(verified_reusable_result=True))
    assert routing.level is ExecutionLevel.L0_CACHE
    for forbidden in ("decide", "escalate", "fallback", "retry", "run_loop"):
        assert not hasattr(router, forbidden)
    assert not hasattr(routing, "action")
    assert not hasattr(routing, "next_level")


def test_escalator_is_not_a_router() -> None:
    escalator = ExecutionLevelEscalator()
    for forbidden in ("route", "retry", "fallback", "run_loop", "research", "plan"):
        assert not hasattr(escalator, forbidden)


def test_a209_retry_and_loop_behavior_is_absent() -> None:
    escalator = ExecutionLevelEscalator()
    for forbidden in (
        "retry",
        "retry_count",
        "attempt_count",
        "anti_loop",
        "loop_guard",
        "visited",
        "backoff",
        "run_loop",
    ):
        assert not hasattr(escalator, forbidden)
    evidence = _evidence(
        current_level=ExecutionLevel.L1_DIRECT,
        current_strategy_can_continue=True,
    )
    first = escalator.decide(evidence)
    second = escalator.decide(evidence)
    assert first == second
    assert first.action is EscalationAction.STAY
    assert not hasattr(first, "attempt_count")
    assert not hasattr(first, "retry_count")
