"""Final cross-task authority proofs for the Wave-2 Authority-B integration set.

These tests intentionally exercise boundaries *between* independently owned tasks.
They do not create new policy; they pin the composition facts the individual task
suites already establish.
"""

from __future__ import annotations

from pathlib import Path

from agentx.cache_strategy import CACHE_STRATEGY_LEVEL
from agentx.cognition.router import ExecutionLevel
from agentx.compiled_procedure_strategy import COMPILED_PROCEDURE_STRATEGY_LEVEL
from agentx.core.procedure_lifecycle import (
    PERMITTED_TRANSITION_REASONS,
    TERMINAL_PROCEDURE_STATUSES,
    ProcedureLifecycleReason,
)
from agentx.core.procedures import ProcedureStatus
from agentx.planning_strategy import PLANNING_STRATEGY_LEVEL
from agentx.procedure_reuse_selector import ProcedureReuseSelection

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "agentx"


def _source(relative: str) -> str:
    return (_SRC / relative).read_text(encoding="utf-8")


def test_n2_03_n2_04_n2_06_l0_l2_l4_have_distinct_semantics_and_authority() -> None:
    """L0 evidence reuse, L2 governed execution and L4 planning are not aliases."""
    assert CACHE_STRATEGY_LEVEL is ExecutionLevel.L0_CACHE
    assert COMPILED_PROCEDURE_STRATEGY_LEVEL is ExecutionLevel.L2_COMPILED
    assert PLANNING_STRATEGY_LEVEL is ExecutionLevel.L4_PLANNED
    assert len({CACHE_STRATEGY_LEVEL, COMPILED_PROCEDURE_STRATEGY_LEVEL, PLANNING_STRATEGY_LEVEL}) == 3

    cache = _source("cache_strategy.py")
    compiled = _source("compiled_procedure_strategy.py")
    planning = _source("planning_strategy.py")

    # L0 reuses historical typed evidence but owns no executor/procedure engine.
    assert "A cache hit is not Task success" in cache
    assert "agentx.capabilities.executor" not in cache
    assert "agentx.procedures.interpreter" not in cache

    # L2 alone among these three is the compiled governed-action adapter.
    assert "from agentx.capabilities.executor import" in compiled
    assert "from agentx.procedures.interpreter import" in compiled
    assert "normal L2 execution requires ProcedureStatus.ACTIVE" in compiled

    # L4 produces bounded plan data; it has no direct capability/kernel/store edge.
    assert "Planning is not execution" in planning
    assert "from agentx.cognition.reasoner import Reasoner" in planning
    assert "agentx.capabilities.executor" not in planning
    assert "agentx.infrastructure" not in planning
    assert "agentx.kernel" not in planning


def test_n2_08_n2_09_n2_10_chain_requires_candidate_then_validation_then_explicit_promotion() -> None:
    """Compilation and varied validation cannot silently collapse into ACTIVE."""
    compiler = _source("skill_compiler.py")
    validation_runner = _source("procedure_validation_runner.py")

    # N2.08 is candidate construction only and has no persistence surface.
    assert "ProcedureStatus.CANDIDATE" in compiler
    assert "agentx.infrastructure" not in compiler
    assert "ProcedureStore" not in compiler
    assert "update_status" not in compiler

    # N2.09 delegates evidence sufficiency to canonical M4.02 and owns no store.
    assert "ValidationPolicy" in validation_runner
    assert "ValidationRunEvidence" in validation_runner
    assert "agentx.infrastructure" not in validation_runner
    assert "ProcedureStore" not in validation_runner

    # The canonical promotion contract is explicit: only the typed promotion
    # reason may structurally cross CANDIDATE -> ACTIVE. RETIRED cannot cross.
    assert PERMITTED_TRANSITION_REASONS[
        (ProcedureStatus.CANDIDATE, ProcedureStatus.ACTIVE)
    ] == frozenset({ProcedureLifecycleReason.VALIDATION_PROMOTION})
    assert ProcedureStatus.RETIRED in TERMINAL_PROCEDURE_STATUSES
    assert (
        ProcedureStatus.ACTIVE
        not in PERMITTED_TRANSITION_REASONS.get(
            (ProcedureStatus.RETIRED, ProcedureStatus.ACTIVE), frozenset()
        )
    )


def test_n2_11_n2_12_reuse_selection_and_measurement_are_evidence_not_authority() -> None:
    """Selection/measurement cannot route, grant authority or invent metrics."""
    selector = _source("procedure_reuse_selector.py")
    experiment = _source("reuse_experiment.py")
    efficiency = _source("core/reuse_efficiency.py")

    assert ProcedureReuseSelection.grants_execution_authority is False
    assert "agentx.infrastructure" not in selector
    assert "agentx.kernel" not in selector
    assert "ExecutionLevelRouter" not in selector

    # N2.12 delegates comparison arithmetic/truth to M8.01 instead of defining
    # a second metric system. WARM_IMPROVED is an inert result vocabulary only.
    assert "compare_execution_efficiency" in experiment
    assert "ExecutionEfficiencyEvidence" in experiment
    assert "WARM_IMPROVED" in experiment
    assert "ExecutionLevelRouter" not in experiment
    assert "agentx.kernel" not in experiment
    assert "ProcedureStore" not in experiment

    # Canonical efficiency keeps optional measurements optional; absence is
    # represented as None rather than a fabricated zero.
    assert "None" in efficiency
    assert "INSUFFICIENT_EVIDENCE" in efficiency
    assert "verified" in efficiency.lower()


def test_n2_14_n2_15_n2_17_n2_18_share_one_atomic_activation_seam_without_resurrection() -> None:
    """Repair evidence/materialization stays inert until one shared transaction seam."""
    workflow = _source("repair_workflow.py")
    materializer = _source("repair_patch_materializer.py")
    replacement = _source("procedure_replacement_transaction.py")
    rollback = _source("procedure_rollback.py")
    activation = _source("infrastructure/procedure_activation.py")

    # N2.14 and N2.15 cannot mutate persistence or activate their own output.
    assert "ProcedureStore" not in workflow
    assert "agentx.infrastructure" not in workflow
    assert "ProcedureStatus.CANDIDATE" in materializer
    assert "agentx.infrastructure" not in materializer
    assert "ProcedureStore" not in materializer

    # Both lifecycle compositions converge on the same storage-only seam.
    shared_import = "from agentx.infrastructure.procedure_activation import"
    assert shared_import in replacement
    assert shared_import in rollback
    assert "activate_procedure_revision_atomically(" in replacement
    assert "activate_procedure_revision_atomically(" in rollback

    # The seam itself rechecks state inside one write transaction, admits only
    # a CANDIDATE target, and verifies exactly-one-ACTIVE before commit.
    assert "with _write_transaction(connection):" in activation
    assert "history != expected_history" in activation
    assert "target_candidate.status is not ProcedureStatus.CANDIDATE" in activation
    assert "len(active_records) > 1" in activation
    assert "active_records != (expected_active,)" in activation
    assert "atomic transition did not produce exactly one ACTIVE revision" in activation

    # RETIRED is terminal; rollback materializes a new candidate instead of
    # editing/reactivating the historical RETIRED row.
    assert ProcedureStatus.RETIRED in TERMINAL_PROCEDURE_STATUSES
    assert "if target.status is ProcedureStatus.RETIRED:" in rollback
    assert "revision=request.current_revision + 1" in rollback
    assert "status=ProcedureStatus.CANDIDATE" in rollback


def test_n2_21_to_n2_25_keep_risk_action_resolution_observation_and_verification_separate() -> None:
    """Windows action evidence cannot collapse policy, resolution or verification."""
    launch = _source("capabilities/windows/application_launch_v2.py")
    risk = _source("capabilities/filesystem_structural_risk.py")
    keyboard = _source("capabilities/windows/keyboard_text_clipboard.py")
    resolver = _source("capabilities/windows/uia_target_resolution.py")
    verification = _source("windows_transition_verification.py")

    # N2.22 is pure request-sensitive risk classification, never execution.
    assert "RiskAssessment" in risk
    assert "The policy never performs filesystem I/O" in risk
    assert "never executes a filesystem operation" in risk
    assert "CapabilityObservation" not in risk

    # N2.21/N2.23 action surfaces emit observations; an API return is evidence,
    # not proof of the intended user-visible state.
    assert "CapabilityObservation" in launch
    assert "readiness is unverified" in launch
    assert "CapabilityObservation" in keyboard
    assert "untrusted data" in keyboard.lower()

    # N2.24 resolves only already-observed UIA data and performs no action.
    assert "pure data-processing boundary" in resolver
    assert "does not call UI Automation" in resolver
    assert "does not click, invoke, type" in resolver

    # N2.25 is the independent post-state verification boundary and explicitly
    # refuses to equate native return success with intended-state success.
    assert "NATIVE API SUCCESS != INTENDED USER-VISIBLE STATE SUCCESS" in verification
    assert "post-state observation" in verification
    assert "native_reported_success" in verification
    assert "never from an execution return value" in verification
