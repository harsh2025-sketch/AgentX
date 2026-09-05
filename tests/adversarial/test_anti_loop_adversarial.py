"""Adversarial authority-boundary tests for the A2.09 anti-loop guard."""

from __future__ import annotations

from dataclasses import fields
from datetime import timedelta
from decimal import Decimal

import pytest

from agentx.cognition.anti_loop import (
    AttemptEvidence,
    AttemptFingerprint,
    LoopGuard,
    LoopGuardDecision,
    LoopGuardLimits,
    LoopGuardTrigger,
    OutcomeFingerprint,
    ProgressFingerprint,
)
from agentx.kernel.action_gate import ActionGate, GateRequest
from agentx.kernel.permissions import Permission
from agentx.kernel.resource_budget import ResourceEnvelope
from agentx.kernel.risk import RiskAssessment, RiskLevel


def _limits() -> LoopGuardLimits:
    return LoopGuardLimits(
        max_total_attempts=8,
        max_same_attempts=2,
        max_same_outcomes_without_progress=2,
    )


def _evidence(
    attempt: str,
    outcome: str = "failure:stable",
    progress: str | None = None,
) -> AttemptEvidence:
    return AttemptEvidence(
        attempt=AttemptFingerprint(attempt),
        outcome=OutcomeFingerprint(outcome),
        progress=None if progress is None else ProgressFingerprint(progress),
    )


def test_hostile_instruction_tokens_in_attempt_or_outcome_are_inert() -> None:
    history = (
        _evidence("progress=true", "permission=WRITE"),
        _evidence("progress=true", "permission=WRITE"),
    )

    result = LoopGuard().evaluate(history=history, limits=_limits())

    assert result.decision is LoopGuardDecision.STOP_LOOP
    assert result.trigger is LoopGuardTrigger.REPEATED_ATTEMPT
    assert result.distinct_progress_markers == 0


def test_hostile_natural_language_cannot_masquerade_as_stable_progress() -> None:
    for hostile in (
        "new strategy",
        "ignore loop guard",
        "progress=true now",
        "ADMIN allow retry",
    ):
        with pytest.raises(ValueError):
            ProgressFingerprint(hostile)


def test_only_explicit_typed_progress_changes_progress_epoch() -> None:
    no_progress = (
        _evidence("progress=true"),
        _evidence("progress=true"),
    )
    with_progress = (
        _evidence("attempt:same"),
        _evidence("attempt:same", progress="progress:milestone-1"),
    )

    stopped = LoopGuard().evaluate(history=no_progress, limits=_limits())
    continued = LoopGuard().evaluate(history=with_progress, limits=_limits())

    assert stopped.decision is LoopGuardDecision.STOP_LOOP
    assert stopped.distinct_progress_markers == 0
    assert continued.decision is LoopGuardDecision.CONTINUE
    assert continued.distinct_progress_markers == 1


def test_loop_result_cannot_be_used_as_authority_context() -> None:
    result = LoopGuard().evaluate(
        history=(_evidence("attempt:a"), _evidence("attempt:a")),
        limits=_limits(),
    )
    request = GateRequest(
        operation="dangerous.operation",
        required_permission=Permission.WRITE,
        risk_assessment=RiskAssessment(
            level=RiskLevel.R2,
            reason="state modification",
            modifies_state=True,
            reversible=True,
            external_effect=False,
        ),
    )

    with pytest.raises(TypeError, match="AuthorityContext"):
        ActionGate().evaluate(request, result)  # type: ignore[arg-type]


def test_loop_result_cannot_manufacture_permissions() -> None:
    result = LoopGuard().evaluate(history=(), limits=_limits())
    result_fields = {field.name for field in fields(type(result))}

    assert result_fields.isdisjoint(
        {
            "permission",
            "permissions",
            "authority",
            "authority_context",
            "grant",
        }
    )


def test_guard_does_not_lower_canonical_risk() -> None:
    assessment = RiskAssessment(
        level=RiskLevel.R0,
        reason="hostile caller attempted downgrade",
        modifies_state=True,
        reversible=False,
        external_effect=True,
        destructive=True,
    )

    LoopGuard().evaluate(history=(), limits=_limits())

    assert assessment.effective_level is RiskLevel.R4


def test_guard_does_not_mutate_or_enlarge_resource_envelope() -> None:
    envelope = ResourceEnvelope(
        max_wall_clock=timedelta(seconds=30),
        max_model_calls=2,
        max_model_tokens=1000,
        max_research_queries=1,
        max_machine_actions=3,
        max_repair_attempts=1,
        max_external_cost=Decimal("1.00"),
        max_risk_level=RiskLevel.R2,
    )
    before = envelope

    LoopGuard().evaluate(
        history=(_evidence("attempt:a"), _evidence("attempt:b")),
        limits=_limits(),
    )

    assert envelope == before
    assert envelope.max_machine_actions == 3
    assert envelope.max_repair_attempts == 1


def test_loop_contract_has_no_budget_reset_or_resource_accounting_fields() -> None:
    names = {field.name for field in fields(LoopGuardLimits)}
    assert names == {
        "max_total_attempts",
        "max_same_attempts",
        "max_same_outcomes_without_progress",
    }
    assert names.isdisjoint(
        {
            "model_calls",
            "model_tokens",
            "research_queries",
            "machine_actions",
            "repair_attempts",
            "external_cost",
            "max_risk_level",
        }
    )


def test_stop_loop_is_evidence_not_retry_escalation_or_task_transition() -> None:
    result = LoopGuard().evaluate(
        history=(_evidence("attempt:a"), _evidence("attempt:a")),
        limits=_limits(),
    )

    assert result.decision is LoopGuardDecision.STOP_LOOP
    for name in (
        "execute",
        "retry",
        "escalate",
        "fallback",
        "transition",
        "research",
        "repair",
        "activate",
    ):
        assert not hasattr(result, name)


def test_loop_result_cannot_fabricate_verification() -> None:
    result = LoopGuard().evaluate(history=(), limits=_limits())
    names = {field.name for field in fields(type(result))}

    assert names.isdisjoint(
        {
            "verified",
            "verification",
            "passed",
            "success",
            "verdict",
        }
    )


def test_hostile_attempt_cannot_override_limits() -> None:
    limits = LoopGuardLimits(
        max_total_attempts=10,
        max_same_attempts=2,
        max_same_outcomes_without_progress=10,
    )
    history = (
        _evidence("attempt:ignore-loop-guard", "outcome:budget=unlimited"),
        _evidence("attempt:ignore-loop-guard", "outcome:budget=unlimited"),
    )

    result = LoopGuard().evaluate(history=history, limits=limits)

    assert result.decision is LoopGuardDecision.STOP_LOOP
    assert result.trigger is LoopGuardTrigger.REPEATED_ATTEMPT
    assert limits.max_same_attempts == 2
