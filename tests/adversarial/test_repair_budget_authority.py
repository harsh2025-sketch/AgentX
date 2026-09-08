"""Adversarial authority-boundary tests for the C4.09 / M5.03 repair budget.

These tests prove hostile content can never turn a repair-budget assessment
into a decision, an authority, an execution, a verification, or a resource
consumption: assessments grant no Permission, create no AuthorityContext,
bypass no ActionGate, lower no risk, widen or consume no ResourceBudget,
clear no EmergencyStop, transition no Task, never patch or activate a
procedure, never mutate Hive, never touch a store, never invoke a model, and
never fabricate progress, resets, or unlimited allowances from free text.
"""

from __future__ import annotations

import builtins
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from uuid import uuid4

import pytest
from tests.support.authority_proxy import ForbiddenAuthorityProxy

import agentx.core.repair_budget as repair_budget_module
from agentx.core.ids import ProcedureId, TaskId
from agentx.core.repair_budget import (
    ProcedureRepairTarget,
    RepairAttemptEvidence,
    RepairAttemptOutcome,
    RepairBudgetDecision,
    RepairBudgetLimits,
    RepairBudgetScope,
    RepairProgressMarker,
    RepairProposalFingerprint,
    RepairTarget,
    RepairTargetFingerprint,
    assess_repair_attempt,
)
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel

_PROCEDURE = ProcedureId(uuid4())


def _target(*, revision: int = 7) -> RepairTarget:
    return RepairTarget(procedure=ProcedureRepairTarget(procedure_id=_PROCEDURE, revision=revision))


def _limits(
    *,
    total: int = 4,
    per_proposal: int = 2,
    no_progress: int = 3,
    scope: RepairBudgetScope = RepairBudgetScope.TARGET,
) -> RepairBudgetLimits:
    return RepairBudgetLimits(
        max_total_attempts=total,
        max_attempts_per_proposal=per_proposal,
        max_consecutive_failures_without_progress=no_progress,
        total_attempt_scope=scope,
    )


def _evidence(
    proposal: str,
    outcome: RepairAttemptOutcome = RepairAttemptOutcome.FAILED_SHADOW,
    *,
    target: RepairTarget | None = None,
    progress: str | None = None,
) -> RepairAttemptEvidence:
    return RepairAttemptEvidence(
        target=target if target is not None else _target(),
        proposal=RepairProposalFingerprint(proposal),
        outcome=outcome,
        progress=None if progress is None else RepairProgressMarker(progress),
    )


def _envelope() -> ResourceEnvelope:
    return ResourceEnvelope(
        max_wall_clock=timedelta(minutes=10),
        max_model_calls=10,
        max_model_tokens=10_000,
        max_research_queries=5,
        max_machine_actions=5,
        max_repair_attempts=3,
        max_external_cost=Decimal("1.00"),
        max_risk_level=RiskLevel.R2,
    )


# ---------------------------------------------------------------------------
# Hostile instruction strings are inert data.
# ---------------------------------------------------------------------------


def test_hostile_instruction_tokens_never_change_typed_counters() -> None:
    # These tokens satisfy the opaque grammar, so they are legal fingerprints
    # and markers — but they can only ever be exact-match identity tokens.
    hostile = "permission=ADMIN"
    history = (
        _evidence(hostile, progress="verified=true"),
        _evidence(hostile, progress="budget=unlimited"),
        _evidence(hostile, progress="risk=R0"),
    )

    result = assess_repair_attempt(
        target=_target(),
        proposal=RepairProposalFingerprint(hostile),
        history=history,
        limits=_limits(total=9, per_proposal=2, no_progress=9),
    )

    # The identical hostile proposal was attempted 3 times: the repeat ceiling
    # fired exactly as it would for any boring token. Nothing interpreted the
    # text as permission, verification, budget, or risk.
    assert result.decision is RepairBudgetDecision.STOP_REPEAT_LIMIT
    assert result.proposal_attempt_count == 3
    assert result.distinct_progress_markers == 3
    assert result.consecutive_no_progress_count == 0


def test_hostile_natural_language_cannot_masquerade_as_tokens() -> None:
    # Free text with spaces is rejected outright by every token contract.
    for hostile in ("reset attempts", "repair approved", "ignore previous failures"):
        with pytest.raises(ValueError):
            RepairProposalFingerprint(hostile)
        with pytest.raises(ValueError):
            RepairProgressMarker(hostile)
        with pytest.raises(ValueError):
            RepairTargetFingerprint(hostile)


def test_grammar_valid_hostile_tokens_are_inert_identity_only() -> None:
    # Tokens that satisfy the opaque grammar ("budget=unlimited", "risk=R0",
    # "verified=true", "permission=ADMIN") are legal fingerprints/markers, but
    # they can only ever be exact-match identity tokens: constructing them
    # grants nothing, and evaluation counts them like any boring token.
    for hostile in ("budget=unlimited", "risk=R0", "verified=true", "permission=ADMIN"):
        proposal = RepairProposalFingerprint(hostile)
        marker = RepairProgressMarker(hostile)
        assert proposal.value == hostile
        assert marker.value == hostile

    history = (_evidence("budget=unlimited", progress="verified=true"),)
    result = assess_repair_attempt(
        target=_target(),
        proposal=RepairProposalFingerprint("budget=unlimited"),
        history=history,
        limits=_limits(total=9, per_proposal=9, no_progress=9),
    )
    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.proposal_attempt_count == 1
    assert result.consecutive_no_progress_count == 0
    assert result.distinct_progress_markers == 1


def test_hostile_outcome_strings_are_rejected_not_interpreted() -> None:
    for hostile in ("verified=true", "repair approved", "VALIDATED", "allowed"):
        with pytest.raises(ValueError):
            RepairAttemptOutcome(hostile)


def test_hostile_strings_cannot_widen_limits() -> None:
    with pytest.raises(TypeError):
        RepairBudgetLimits(
            max_total_attempts="unlimited",  # type: ignore[arg-type]
            max_attempts_per_proposal=2,
            max_consecutive_failures_without_progress=3,
            total_attempt_scope=RepairBudgetScope.TARGET,
        )
    with pytest.raises(ValueError):
        RepairBudgetLimits(
            max_total_attempts=0,
            max_attempts_per_proposal=2,
            max_consecutive_failures_without_progress=3,
            total_attempt_scope=RepairBudgetScope.TARGET,
        )


# ---------------------------------------------------------------------------
# Loop-flooding attacks.
# ---------------------------------------------------------------------------


def test_duplicate_flooding_is_bounded_by_every_ceiling() -> None:
    flood = tuple(_evidence("patch:identical") for _ in range(500))

    by_total = assess_repair_attempt(
        target=_target(),
        proposal=RepairProposalFingerprint("patch:identical"),
        history=flood,
        limits=_limits(total=4, per_proposal=9_000, no_progress=9_000),
    )
    by_repeat = assess_repair_attempt(
        target=_target(),
        proposal=RepairProposalFingerprint("patch:identical"),
        history=flood,
        limits=_limits(total=9_000, per_proposal=2, no_progress=9_000),
    )
    by_progress = assess_repair_attempt(
        target=_target(),
        proposal=RepairProposalFingerprint("patch:other"),
        history=flood,
        limits=_limits(total=9_000, per_proposal=9_000, no_progress=3),
    )

    assert by_total.decision is RepairBudgetDecision.STOP_TOTAL_LIMIT
    assert by_repeat.decision is RepairBudgetDecision.STOP_REPEAT_LIMIT
    assert by_progress.decision is RepairBudgetDecision.STOP_NO_PROGRESS
    for result in (by_total, by_repeat, by_progress):
        assert result.target_attempt_count == 500


def test_new_uuid_every_time_cannot_extend_the_assessed_targets_budget() -> None:
    # Every evidence record uses a brand-new ProcedureId (and so a brand-new
    # target). None of that churn may extend the assessed target's own
    # allowance: its own three attempts still fill its own total ceiling.
    churn = tuple(
        RepairAttemptEvidence(
            target=RepairTarget(
                procedure=ProcedureRepairTarget(
                    procedure_id=ProcedureId(uuid4()),
                    revision=7,
                )
            ),
            proposal=RepairProposalFingerprint("patch:same"),
            outcome=RepairAttemptOutcome.FAILED_VALIDATION,
        )
        for _ in range(97)
    )
    own = tuple(_evidence("patch:same") for _ in range(3))

    target_scope = assess_repair_attempt(
        target=_target(),
        proposal=RepairProposalFingerprint("patch:same"),
        history=(*churn, *own),
        limits=_limits(total=3, per_proposal=9, no_progress=9),
    )

    assert target_scope.decision is RepairBudgetDecision.STOP_TOTAL_LIMIT
    assert target_scope.target_attempt_count == 3
    assert target_scope.history_attempt_count == 100

    # With an explicit SESSION scope, UUID churn cannot escape the shared
    # session ceiling either.
    session_scope = assess_repair_attempt(
        target=_target(),
        proposal=RepairProposalFingerprint("patch:same"),
        history=(*churn, *own),
        limits=_limits(total=100, per_proposal=9, no_progress=9, scope=RepairBudgetScope.SESSION),
    )

    assert session_scope.decision is RepairBudgetDecision.STOP_TOTAL_LIMIT
    assert session_scope.history_attempt_count == 100


def test_uuid_churn_across_targets_still_cannot_shrink_or_reset_counters() -> None:
    churn = tuple(
        RepairAttemptEvidence(
            target=RepairTarget(
                procedure=ProcedureRepairTarget(
                    procedure_id=ProcedureId(uuid4()),
                    revision=1,
                )
            ),
            proposal=RepairProposalFingerprint("patch:same"),
            outcome=RepairAttemptOutcome.FAILED_APPLICATION,
        )
        for _ in range(50)
    )
    own = (_evidence("patch:same"),)

    result = assess_repair_attempt(
        target=_target(),
        proposal=RepairProposalFingerprint("patch:same"),
        history=(*churn, *own),
        limits=_limits(total=9, per_proposal=2, no_progress=9),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.target_attempt_count == 1
    assert result.proposal_attempt_count == 1


def test_timestamp_only_changes_are_structurally_impossible() -> None:
    # The evidence record has no timestamp, UUID, or ordering field at all:
    # two records with identical typed fields ARE identical evidence, and
    # uninvited extra fields are rejected instead of stored.
    first = _evidence("patch:1")
    second = _evidence("patch:1")
    assert first == second
    assert hash(first) == hash(second)

    with pytest.raises(TypeError):
        RepairAttemptEvidence(  # type: ignore[call-arg]
            target=_target(),
            proposal=RepairProposalFingerprint("patch:1"),
            outcome=RepairAttemptOutcome.FAILED_SHADOW,
            recorded_at="2026-01-01T00:00:00Z",
        )
    with pytest.raises(TypeError):
        RepairAttemptEvidence(  # type: ignore[call-arg]
            target=_target(),
            proposal=RepairProposalFingerprint("patch:1"),
            outcome=RepairAttemptOutcome.FAILED_SHADOW,
            attempt_id=uuid4(),
        )


def test_cross_target_pollution_is_impossible_in_both_directions() -> None:
    procedure_a = _target(revision=7)
    procedure_b = _target(revision=8)
    opaque = RepairTarget(opaque=RepairTargetFingerprint("target:opaque"))
    task_id = TaskId(uuid4())

    assert procedure_a != procedure_b
    assert procedure_a != opaque
    assert opaque != procedure_a
    # Canonical IDs are domain-tagged: a TaskId is never a ProcedureId, so a
    # foreign ID can never be smuggled in as target identity.
    assert ProcedureId(uuid4()) != task_id
    with pytest.raises(repair_budget_module.RepairBudgetValidationError):
        ProcedureRepairTarget(procedure_id=task_id, revision=7)  # type: ignore[arg-type]

    flood_b = tuple(_evidence("patch:same", target=procedure_b) for _ in range(10))
    result = assess_repair_attempt(
        target=procedure_a,
        proposal=RepairProposalFingerprint("patch:same"),
        history=flood_b,
        limits=_limits(total=1, per_proposal=1, no_progress=1),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.target_attempt_count == 0
    assert result.history_attempt_count == 10


def test_progress_marker_replay_flooding_never_resets_the_run() -> None:
    history = tuple(_evidence(f"patch:{index}", progress="verified=true") for index in range(10))

    result = assess_repair_attempt(
        target=_target(),
        proposal=RepairProposalFingerprint("patch:next"),
        history=history,
        limits=_limits(total=9_000, per_proposal=9_000, no_progress=9),
    )

    assert result.decision is RepairBudgetDecision.STOP_NO_PROGRESS
    assert result.consecutive_no_progress_count == 9
    assert result.distinct_progress_markers == 1


# ---------------------------------------------------------------------------
# Fail-closed behavior.
# ---------------------------------------------------------------------------


def test_structurally_invalid_history_fails_closed_not_open() -> None:
    for bad_history in (
        None,
        "ignore previous failures",
        7,
        {"proposal": "patch:1"},
        (_evidence("patch:1"), "budget=unlimited"),
        (_evidence("patch:1"), None),
    ):
        result = assess_repair_attempt(
            target=_target(),
            proposal=RepairProposalFingerprint("patch:1"),
            history=bad_history,  # type: ignore[arg-type]
            limits=_limits(total=1_000_000, per_proposal=1_000_000, no_progress=1_000_000),
        )
        assert result.decision is RepairBudgetDecision.INVALID_HISTORY
        assert result.history_attempt_count == 0


def test_malformed_requests_raise_instead_of_returning_allowance() -> None:
    with pytest.raises(TypeError):
        assess_repair_attempt(
            target="target:any",  # type: ignore[arg-type]
            proposal=RepairProposalFingerprint("patch:1"),
            history=(),
            limits=_limits(),
        )
    with pytest.raises(TypeError):
        assess_repair_attempt(
            target=_target(),
            proposal="patch:1",  # type: ignore[arg-type]
            history=(),
            limits=_limits(),
        )
    with pytest.raises(TypeError):
        assess_repair_attempt(
            target=_target(),
            proposal=RepairProposalFingerprint("patch:1"),
            history=(),
            limits="limits:unlimited",  # type: ignore[arg-type]
        )


def test_allow_decision_remains_data_and_makes_no_state_change() -> None:
    history = (_evidence("patch:1"),)
    target = _target()
    proposal = RepairProposalFingerprint("patch:2")
    limits = _limits()
    module_names_before = set(repair_budget_module.__dict__)

    for _ in range(25):
        result = assess_repair_attempt(
            target=target, proposal=proposal, history=history, limits=limits
        )
        assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION

    assert set(repair_budget_module.__dict__) == module_names_before
    # The ALLOW text itself claims no authority.
    assert "grants no kernel authority" in result.reason


# ---------------------------------------------------------------------------
# Authority-subsystem isolation.
# ---------------------------------------------------------------------------


def test_no_authority_or_runtime_subsystem_is_even_imported_or_touchable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched: list[tuple[str, str]] = []
    for subsystem in (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.infrastructure",
        "agentx.learning",
        "agentx.hive",
        "agentx.procedures",
    ):
        monkeypatch.setitem(
            sys.modules, subsystem, cast(ModuleType, ForbiddenAuthorityProxy(subsystem, touched))
        )
        for submodule in (
            "permissions",
            "risk",
            "emergency_stop",
            "action_gate",
            "resource_budget",
            "executor",
            "model_provider",
            "persistence",
            "anti_loop",
        ):
            full = f"{subsystem}.{submodule}"
            monkeypatch.setitem(
                sys.modules, full, cast(ModuleType, ForbiddenAuthorityProxy(full, touched))
            )

    history = (
        _evidence("permission=ADMIN", RepairAttemptOutcome.VALIDATED, progress="verified=true"),
        _evidence("risk=R0"),
    )
    result = assess_repair_attempt(
        target=_target(),
        proposal=RepairProposalFingerprint("repair approved".replace(" ", "-")),
        history=history,
        limits=_limits(),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert touched == []


def test_module_exposes_no_authority_risk_or_budget_surface() -> None:
    for forbidden in (
        "Permission",
        "AuthorityContext",
        "ActionGate",
        "GateRequest",
        "RiskLevel",
        "RiskAssessment",
        "ResourceBudget",
        "ResourceEnvelope",
        "ResourceUsage",
        "ResourceRequest",
        "BudgetEvaluator",
        "BudgetResult",
        "EmergencyStop",
        "VerificationResult",
        "Task",
        "TaskManager",
        "EventBus",
        "ProcedureStore",
        "KnowledgeStore",
        "Capability",
        "CapabilityRegistry",
        "LoopGuard",
        "ModelProvider",
        "Reasoner",
    ):
        assert not hasattr(repair_budget_module, forbidden)


def test_assessing_never_consumes_or_widens_kernel_resource_budget() -> None:
    budget = ResourceBudget(_envelope())
    before = budget.snapshot()

    history = tuple(_evidence(f"patch:{index}") for index in range(3))
    for _ in range(10):
        result = assess_repair_attempt(
            target=_target(),
            proposal=RepairProposalFingerprint("patch:next"),
            history=history,
            limits=_limits(total=3),
        )
        assert result.decision is RepairBudgetDecision.STOP_TOTAL_LIMIT

    after = budget.snapshot()
    assert after == before
    assert after.repair_attempts == 0
    assert budget.envelope == _envelope()


def test_allow_opinion_plus_kernel_deny_is_no_repair() -> None:
    # The documented composition: the repair policy may allow consideration
    # while the canonical kernel budget independently denies the very same
    # repair attempt — and the kernel decision stands. The repair policy
    # cannot consume, widen, or override anything.
    budget = ResourceBudget(_envelope())
    allow = assess_repair_attempt(
        target=_target(),
        proposal=RepairProposalFingerprint("patch:fresh"),
        history=(),
        limits=_limits(),
    )

    from agentx.kernel.resource_budget import (
        BudgetDecision,
        ResourceDelta,
        ResourceRequest,
    )

    kernel_result = budget.evaluate(
        ResourceRequest(delta=ResourceDelta.zero(), risk_level=RiskLevel.R4)
    )

    assert allow.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert kernel_result.decision is BudgetDecision.DENY
    assert budget.snapshot().repair_attempts == 0


def test_open_and_import_of_execution_primitives_are_never_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbidden_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("the repair-budget policy must not touch the filesystem")

    monkeypatch.setattr(builtins, "open", _forbidden_open)

    history = (_evidence("patch:1"),)
    result = assess_repair_attempt(
        target=_target(),
        proposal=RepairProposalFingerprint("patch:2"),
        history=history,
        limits=_limits(),
    )
    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION


def test_assessment_lifecycle_creates_no_files_anywhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    history = tuple(_evidence(f"patch:{index}") for index in range(3))
    for proposal_name in ("patch:1", "patch:2", "patch:3"):
        assess_repair_attempt(
            target=_target(),
            proposal=RepairProposalFingerprint(proposal_name),
            history=history,
            limits=_limits(),
        )

    assert list(tmp_path.iterdir()) == []


def test_hostile_objects_as_evidence_fields_are_rejected_by_type() -> None:
    class HostileString(str):
        """A string subclass that pretends to carry instructions."""

        def __new__(cls, value: str = "budget=unlimited") -> HostileString:
            return super().__new__(cls, value)

    with pytest.raises(TypeError):
        assess_repair_attempt(
            target=_target(),
            proposal=cast(Any, HostileString()),
            history=(),
            limits=_limits(),
        )
    # Hostile objects in target identification fields are rejected by type
    # before their content is ever inspected.
    with pytest.raises(repair_budget_module.RepairBudgetValidationError):
        RepairTarget(opaque=cast(Any, HostileString()))
    with pytest.raises(repair_budget_module.RepairBudgetValidationError):
        RepairTarget(procedure=cast(Any, HostileString()))
    with pytest.raises(TypeError):
        RepairBudgetLimits(
            max_total_attempts=cast(Any, HostileString()),
            max_attempts_per_proposal=2,
            max_consecutive_failures_without_progress=3,
            total_attempt_scope=RepairBudgetScope.TARGET,
        )
