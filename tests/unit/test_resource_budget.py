"""Unit and adversarial tests for C1.08 resource envelopes and budgets."""

from __future__ import annotations

from dataclasses import MISSING, FrozenInstanceError, fields
from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Lock, Thread
from typing import cast, get_type_hints

import pytest

from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import (
    BudgetDecision,
    BudgetEvaluator,
    BudgetResult,
    ResourceBudget,
    ResourceDelta,
    ResourceEnvelope,
    ResourceRequest,
    ResourceUsage,
)
from agentx.kernel.risk import RiskAssessment, RiskLevel


def _envelope(
    *,
    max_wall_clock: timedelta = timedelta(seconds=10),
    max_model_calls: int = 10,
    max_model_tokens: int = 10_000,
    max_research_queries: int = 10,
    max_machine_actions: int = 10,
    max_repair_attempts: int = 10,
    max_external_cost: Decimal = Decimal("10.00"),
    max_risk_level: RiskLevel = RiskLevel.R3,
) -> ResourceEnvelope:
    return ResourceEnvelope(
        max_wall_clock=max_wall_clock,
        max_model_calls=max_model_calls,
        max_model_tokens=max_model_tokens,
        max_research_queries=max_research_queries,
        max_machine_actions=max_machine_actions,
        max_repair_attempts=max_repair_attempts,
        max_external_cost=max_external_cost,
        max_risk_level=max_risk_level,
    )


def _delta(
    *,
    wall_clock: timedelta = timedelta(0),
    model_calls: int = 0,
    model_tokens: int = 0,
    research_queries: int = 0,
    machine_actions: int = 0,
    repair_attempts: int = 0,
    external_cost: Decimal = Decimal("0"),
) -> ResourceDelta:
    return ResourceDelta(
        wall_clock=wall_clock,
        model_calls=model_calls,
        model_tokens=model_tokens,
        research_queries=research_queries,
        machine_actions=machine_actions,
        repair_attempts=repair_attempts,
        external_cost=external_cost,
    )


def _request(
    *,
    delta: ResourceDelta | None = None,
    risk_level: RiskLevel = RiskLevel.R0,
) -> ResourceRequest:
    return ResourceRequest(
        delta=ResourceDelta.zero() if delta is None else delta,
        risk_level=risk_level,
    )


def _risk(level: RiskLevel) -> RiskAssessment:
    return RiskAssessment(
        level=level,
        reason=f"{level.value} budget-composition test.",
        reversible=level is RiskLevel.R1,
        external_effect=level is RiskLevel.R3,
    )


def test_resource_envelope_is_explicit_typed_and_immutable() -> None:
    envelope = _envelope()

    assert tuple(field.name for field in fields(ResourceEnvelope)) == (
        "max_wall_clock",
        "max_model_calls",
        "max_model_tokens",
        "max_research_queries",
        "max_machine_actions",
        "max_repair_attempts",
        "max_external_cost",
        "max_risk_level",
    )
    for field in fields(ResourceEnvelope):
        assert field.default is MISSING
        assert field.default_factory is MISSING

    with pytest.raises(FrozenInstanceError):
        envelope.__setattr__("max_model_calls", 1)


def test_usage_delta_request_and_result_are_immutable_values() -> None:
    usage = ResourceUsage.zero()
    delta = ResourceDelta.zero()
    request = _request(delta=delta)
    result = BudgetEvaluator().evaluate(_envelope(), usage, request)

    with pytest.raises(FrozenInstanceError):
        usage.__setattr__("model_calls", 1)
    with pytest.raises(FrozenInstanceError):
        delta.__setattr__("model_calls", 1)
    with pytest.raises(FrozenInstanceError):
        request.__setattr__("risk_level", RiskLevel.R4)
    with pytest.raises(FrozenInstanceError):
        result.__setattr__("decision", BudgetDecision.DENY)


def test_negative_resource_deltas_are_rejected_and_cannot_refund_budget() -> None:
    with pytest.raises(ValueError, match="wall_clock"):
        _delta(wall_clock=timedelta(microseconds=-1))
    with pytest.raises(ValueError, match="model_calls"):
        _delta(model_calls=-1)
    with pytest.raises(ValueError, match="model_tokens"):
        _delta(model_tokens=-1)
    with pytest.raises(ValueError, match="research_queries"):
        _delta(research_queries=-1)
    with pytest.raises(ValueError, match="machine_actions"):
        _delta(machine_actions=-1)
    with pytest.raises(ValueError, match="repair_attempts"):
        _delta(repair_attempts=-1)
    with pytest.raises(ValueError, match="external_cost"):
        _delta(external_cost=Decimal("-0.01"))


def test_invalid_numeric_values_fail_validation() -> None:
    with pytest.raises(TypeError, match="model_calls"):
        _delta(model_calls=cast(int, True))
    with pytest.raises(OverflowError, match="model_calls"):
        _delta(model_calls=1 << 63)
    with pytest.raises(TypeError, match="external_cost"):
        _delta(external_cost=cast(Decimal, 0.1))
    with pytest.raises(ValueError, match="finite"):
        _delta(external_cost=Decimal("NaN"))
    with pytest.raises(ValueError, match="finite"):
        _delta(external_cost=Decimal("Infinity"))


def test_no_implicit_unlimited_mode_from_none_or_missing_values() -> None:
    with pytest.raises(TypeError, match=r"max_model_calls|model_calls"):
        _envelope(max_model_calls=cast(int, None))
    with pytest.raises(TypeError, match=r"max_wall_clock|wall_clock"):
        _envelope(max_wall_clock=cast(timedelta, None))
    with pytest.raises(TypeError, match=r"max_external_cost|external_cost"):
        _envelope(max_external_cost=cast(Decimal, None))


def test_exact_boundary_consumption_covers_every_resource_dimension() -> None:
    envelope = _envelope(
        max_wall_clock=timedelta(seconds=3),
        max_model_calls=2,
        max_model_tokens=1234,
        max_research_queries=4,
        max_machine_actions=5,
        max_repair_attempts=6,
        max_external_cost=Decimal("7.89"),
        max_risk_level=RiskLevel.R2,
    )
    delta = _delta(
        wall_clock=timedelta(seconds=3),
        model_calls=2,
        model_tokens=1234,
        research_queries=4,
        machine_actions=5,
        repair_attempts=6,
        external_cost=Decimal("7.89"),
    )
    budget = ResourceBudget(envelope)

    result = budget.check_and_consume(_request(delta=delta, risk_level=RiskLevel.R2))

    assert result.decision is BudgetDecision.ALLOW
    assert result.usage_after == ResourceUsage(
        wall_clock=timedelta(seconds=3),
        model_calls=2,
        model_tokens=1234,
        research_queries=4,
        machine_actions=5,
        repair_attempts=6,
        external_cost=Decimal("7.89"),
    )
    assert budget.snapshot() == result.usage_after


@pytest.mark.parametrize(
    ("delta", "dimension"),
    [
        (_delta(wall_clock=timedelta(seconds=11)), "wall_clock"),
        (_delta(model_calls=11), "model_calls"),
        (_delta(model_tokens=10_001), "model_tokens"),
        (_delta(research_queries=11), "research_queries"),
        (_delta(machine_actions=11), "machine_actions"),
        (_delta(repair_attempts=11), "repair_attempts"),
        (_delta(external_cost=Decimal("10.01")), "external_cost"),
    ],
)
def test_each_resource_dimension_rejects_over_budget(
    delta: ResourceDelta,
    dimension: str,
) -> None:
    budget = ResourceBudget(_envelope())
    before = budget.snapshot()

    result = budget.check_and_consume(_request(delta=delta))

    assert result.decision is BudgetDecision.DENY
    assert dimension in result.reason
    assert result.usage_before is before
    assert result.usage_after is before
    assert budget.snapshot() is before


def test_cumulative_consumption_allows_exact_limit_then_denies_more() -> None:
    budget = ResourceBudget(
        _envelope(
            max_model_calls=3,
            max_model_tokens=30,
            max_external_cost=Decimal("0.30"),
        )
    )

    first = budget.check_and_consume(
        _request(
            delta=_delta(
                model_calls=1,
                model_tokens=10,
                external_cost=Decimal("0.10"),
            )
        )
    )
    second = budget.check_and_consume(
        _request(
            delta=_delta(
                model_calls=2,
                model_tokens=20,
                external_cost=Decimal("0.20"),
            )
        )
    )
    at_limit = budget.snapshot()
    denied = budget.check_and_consume(_request(delta=_delta(model_calls=1)))

    assert first.decision is BudgetDecision.ALLOW
    assert second.decision is BudgetDecision.ALLOW
    assert at_limit.model_calls == 3
    assert at_limit.model_tokens == 30
    assert at_limit.external_cost == Decimal("0.30")
    assert denied.decision is BudgetDecision.DENY
    assert denied.usage_after is at_limit
    assert budget.snapshot() is at_limit


def test_evaluation_without_consumption_does_not_mutate_usage() -> None:
    budget = ResourceBudget(_envelope(max_model_calls=1))
    request = _request(delta=_delta(model_calls=1))
    before = budget.snapshot()

    first = budget.evaluate(request)
    second = budget.evaluate(request)

    assert first == second
    assert first.decision is BudgetDecision.ALLOW
    assert budget.snapshot() is before


def test_wall_clock_accounting_is_explicit_elapsed_duration_without_sleep() -> None:
    budget = ResourceBudget(_envelope(max_wall_clock=timedelta(seconds=1)))

    first = budget.check_and_consume(
        _request(delta=_delta(wall_clock=timedelta(milliseconds=400))),
    )
    second = budget.check_and_consume(
        _request(delta=_delta(wall_clock=timedelta(milliseconds=600))),
    )
    denied = budget.check_and_consume(
        _request(delta=_delta(wall_clock=timedelta(microseconds=1))),
    )

    assert first.decision is BudgetDecision.ALLOW
    assert second.decision is BudgetDecision.ALLOW
    assert budget.snapshot().wall_clock == timedelta(seconds=1)
    assert denied.decision is BudgetDecision.DENY


def test_external_cost_uses_exact_decimal_accounting() -> None:
    budget = ResourceBudget(_envelope(max_external_cost=Decimal("0.30")))

    assert budget.check_and_consume(
        _request(delta=_delta(external_cost=Decimal("0.10")))
    ).decision is BudgetDecision.ALLOW
    assert budget.check_and_consume(
        _request(delta=_delta(external_cost=Decimal("0.20")))
    ).decision is BudgetDecision.ALLOW

    at_limit = budget.snapshot()
    assert at_limit.external_cost == Decimal("0.30")

    denied = budget.check_and_consume(
        _request(delta=_delta(external_cost=Decimal("0.01"))),
    )
    assert denied.decision is BudgetDecision.DENY
    assert budget.snapshot() is at_limit


def test_risk_ceiling_reuses_canonical_risk_ordering() -> None:
    budget = ResourceBudget(_envelope(max_risk_level=RiskLevel.R2))
    before = budget.snapshot()

    allowed = budget.check_and_consume(_request(risk_level=RiskLevel.R2))
    denied = budget.check_and_consume(_request(risk_level=RiskLevel.R3))

    assert allowed.decision is BudgetDecision.ALLOW
    assert denied.decision is BudgetDecision.DENY
    assert "R3" in denied.reason
    assert "R2" in denied.reason
    assert denied.usage_after == budget.snapshot()
    assert before == ResourceUsage.zero()
    assert get_type_hints(ResourceEnvelope)["max_risk_level"] is RiskLevel
    assert get_type_hints(ResourceRequest)["risk_level"] is RiskLevel


def test_zero_limits_allow_zero_consumption_and_deny_positive_consumption() -> None:
    budget = ResourceBudget(
        _envelope(
            max_wall_clock=timedelta(0),
            max_model_calls=0,
            max_model_tokens=0,
            max_research_queries=0,
            max_machine_actions=0,
            max_repair_attempts=0,
            max_external_cost=Decimal("0"),
            max_risk_level=RiskLevel.R0,
        )
    )

    zero = budget.check_and_consume(_request(delta=ResourceDelta.zero()))
    denied = budget.check_and_consume(_request(delta=_delta(model_calls=1)))

    assert zero.decision is BudgetDecision.ALLOW
    assert denied.decision is BudgetDecision.DENY
    assert budget.snapshot() == ResourceUsage.zero()


def test_malformed_requests_and_inconsistent_usage_fail_closed() -> None:
    budget = ResourceBudget(_envelope())

    with pytest.raises(TypeError, match="request must be a ResourceRequest"):
        budget.check_and_consume(cast(ResourceRequest, object()))
    with pytest.raises(TypeError, match="delta must be a ResourceDelta"):
        ResourceRequest(
            delta=cast(ResourceDelta, object()),
            risk_level=RiskLevel.R0,
        )
    with pytest.raises(TypeError, match="risk_level must be a RiskLevel"):
        ResourceRequest(
            delta=ResourceDelta.zero(),
            risk_level=cast(RiskLevel, "R0"),
        )

    with pytest.raises(ValueError, match=r"usage\.model_calls exceeds envelope"):
        BudgetEvaluator().evaluate(
            _envelope(max_model_calls=0),
            ResourceUsage(
                wall_clock=timedelta(0),
                model_calls=1,
                model_tokens=0,
                research_queries=0,
                machine_actions=0,
                repair_attempts=0,
                external_cost=Decimal("0"),
            ),
            _request(),
        )


def test_budget_decision_is_explicit_and_reason_is_inspectable() -> None:
    assert tuple(decision.value for decision in BudgetDecision) == ("ALLOW", "DENY")

    denied = ResourceBudget(_envelope(max_model_calls=0)).check_and_consume(
        _request(delta=_delta(model_calls=1)),
    )

    assert isinstance(denied, BudgetResult)
    assert denied.decision is BudgetDecision.DENY
    assert denied.reason.startswith("DENY:")
    assert denied.usage_before == denied.usage_after


def test_action_gate_allow_and_destructive_permission_do_not_increase_budget() -> None:
    gate_result = ActionGate().evaluate(
        GateRequest(
            operation="read.operation",
            required_permission=Permission.READ,
            risk_assessment=_risk(RiskLevel.R0),
        ),
        AuthorityContext(
            permissions=frozenset({Permission.READ, Permission.DESTRUCTIVE}),
        ),
    )
    budget = ResourceBudget(_envelope(max_model_calls=0))

    assert gate_result.decision is GateDecision.ALLOW

    budget_result = budget.check_and_consume(_request(delta=_delta(model_calls=1)))

    assert budget_result.decision is BudgetDecision.DENY
    assert budget.envelope.max_model_calls == 0
    assert budget.snapshot().model_calls == 0


def test_untrusted_priority_text_metadata_and_capability_identifiers_cannot_change_limits() -> None:
    envelope_fields = {field.name for field in fields(ResourceEnvelope)}
    request_fields = {field.name for field in fields(ResourceRequest)}
    forbidden = {
        "priority",
        "metadata",
        "model_text",
        "reasoning",
        "capability",
        "capability_id",
        "permission",
        "authority",
        "admin",
        "superuser",
        "wildcard",
        "bypass",
    }

    assert forbidden.isdisjoint(envelope_fields)
    assert forbidden.isdisjoint(request_fields)


def test_successful_consumption_never_expands_the_immutable_envelope() -> None:
    envelope = _envelope(max_machine_actions=1)
    budget = ResourceBudget(envelope)

    first = budget.check_and_consume(_request(delta=_delta(machine_actions=1)))
    denied = budget.check_and_consume(_request(delta=_delta(machine_actions=1)))

    assert first.decision is BudgetDecision.ALLOW
    assert denied.decision is BudgetDecision.DENY
    assert budget.envelope is envelope
    assert budget.envelope.max_machine_actions == 1
    assert budget.snapshot().machine_actions == 1


def test_atomic_check_and_consume_prevents_double_spending_final_unit() -> None:
    budget = ResourceBudget(_envelope(max_model_calls=1))
    request = _request(delta=_delta(model_calls=1))
    start = Barrier(3)
    results: list[BudgetResult] = []
    results_lock = Lock()

    def worker() -> None:
        start.wait()
        result = budget.check_and_consume(request)
        with results_lock:
            results.append(result)

    threads = [Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()

    start.wait()

    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert sum(result.decision is BudgetDecision.ALLOW for result in results) == 1
    assert sum(result.decision is BudgetDecision.DENY for result in results) == 1
    assert budget.snapshot().model_calls == 1


def test_budget_contract_exposes_no_execution_authorization_or_bypass_behavior() -> None:
    forbidden = {
        "execute",
        "invoke",
        "run",
        "publish",
        "authorize",
        "approve",
        "grant",
        "bypass",
        "admin",
        "superuser",
    }

    assert forbidden.isdisjoint(vars(ResourceBudget))
    assert forbidden.isdisjoint(vars(BudgetEvaluator))
    assert forbidden.isdisjoint(vars(ResourceRequest))
    assert forbidden.isdisjoint(vars(ResourceEnvelope))
