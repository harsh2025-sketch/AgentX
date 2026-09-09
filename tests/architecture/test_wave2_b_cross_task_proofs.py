"""Final cross-task authority proofs for the Wave-2 Authority-B integration set.

These tests exercise boundaries between independently owned tasks. They add no
policy; they pin composition facts already established by the task-local suites.
"""

from __future__ import annotations

import ast
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
from agentx.procedure_reuse_selector import ProcedureReuseSelectionResult

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "agentx"


def _source(relative: str) -> str:
    return (_SRC / relative).read_text(encoding="utf-8")


def _imports(relative: str) -> frozenset[str]:
    tree = ast.parse(_source(relative))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return frozenset(imported)


def test_n2_03_n2_04_n2_06_l0_l2_l4_have_distinct_semantics_and_authority() -> None:
    """L0 evidence reuse, L2 governed execution and L4 planning are not aliases."""
    levels = (
        CACHE_STRATEGY_LEVEL,
        COMPILED_PROCEDURE_STRATEGY_LEVEL,
        PLANNING_STRATEGY_LEVEL,
    )
    assert levels == (
        ExecutionLevel.L0_CACHE,
        ExecutionLevel.L2_COMPILED,
        ExecutionLevel.L4_PLANNED,
    )
    assert len(set(levels)) == 3

    cache = _source("cache_strategy.py")
    compiled = _source("compiled_procedure_strategy.py")
    planning = _source("planning_strategy.py")
    cache_imports = _imports("cache_strategy.py")
    compiled_imports = _imports("compiled_procedure_strategy.py")
    planning_imports = _imports("planning_strategy.py")

    assert "A cache hit is not Task success" in cache
    assert "agentx.capabilities.executor" not in cache_imports
    assert "agentx.procedures.interpreter" not in cache_imports

    assert "agentx.capabilities.executor" in compiled_imports
    assert "agentx.procedures.interpreter" in compiled_imports
    assert "normal L2 execution requires ProcedureStatus.ACTIVE" in compiled

    assert "Planning is not execution" in planning
    assert "agentx.cognition.reasoner" in planning_imports
    assert not any(
        name.startswith("agentx.capabilities") for name in planning_imports
    )
    assert not any(
        name.startswith("agentx.infrastructure") for name in planning_imports
    )
    assert not any(name.startswith("agentx.kernel") for name in planning_imports)


def test_n2_08_n2_09_n2_10_chain_requires_candidate_then_validation_then_explicit_promotion(
) -> None:
    """Compilation and varied validation cannot silently collapse into ACTIVE."""
    compiler = _source("skill_compiler.py")
    validation_runner = _source("procedure_validation_runner.py")
    compiler_imports = _imports("skill_compiler.py")
    validation_imports = _imports("procedure_validation_runner.py")

    assert "ProcedureStatus.CANDIDATE" in compiler
    assert not any(
        name.startswith("agentx.infrastructure") for name in compiler_imports
    )
    assert "agentx.infrastructure.procedure_store" not in compiler_imports

    assert "ValidationPolicy" in validation_runner
    assert "ValidationRunEvidence" in validation_runner
    assert not any(
        name.startswith("agentx.infrastructure") for name in validation_imports
    )

    assert PERMITTED_TRANSITION_REASONS[
        (ProcedureStatus.CANDIDATE, ProcedureStatus.ACTIVE)
    ] == frozenset({ProcedureLifecycleReason.VALIDATION_PROMOTION})
    assert ProcedureStatus.RETIRED in TERMINAL_PROCEDURE_STATUSES
    assert PERMITTED_TRANSITION_REASONS.get(
        (ProcedureStatus.RETIRED, ProcedureStatus.ACTIVE), frozenset()
    ) == frozenset()


def test_n2_11_n2_12_reuse_selection_and_measurement_are_evidence_not_authority() -> None:
    """Selection/measurement cannot route, grant authority or invent metrics."""
    experiment = _source("reuse_experiment.py")
    efficiency = _source("core/reuse_efficiency.py")
    selector_imports = _imports("procedure_reuse_selector.py")
    experiment_imports = _imports("reuse_experiment.py")

    assert ProcedureReuseSelectionResult.grants_execution_authority is False
    assert not any(
        name.startswith("agentx.infrastructure") for name in selector_imports
    )
    assert not any(name.startswith("agentx.kernel") for name in selector_imports)
    assert not any(
        name.startswith("agentx.cognition") for name in selector_imports
    )

    assert "compare_execution_efficiency" in experiment
    assert "ExecutionEfficiencyEvidence" in experiment
    assert "WARM_IMPROVED" in experiment
    assert not any(name.startswith("agentx.kernel") for name in experiment_imports)
    assert not any(
        name.startswith("agentx.infrastructure") for name in experiment_imports
    )
    assert not any(
        name.startswith("agentx.cognition") for name in experiment_imports
    )

    assert "INSUFFICIENT_EVIDENCE" in efficiency
    assert "None" in efficiency
    assert "verified" in efficiency.lower()


def test_n2_14_n2_15_n2_17_n2_18_share_one_atomic_activation_seam_without_resurrection(
) -> None:
    """Repair evidence/materialization stays inert until one shared transaction seam."""
    materializer = _source("repair_patch_materializer.py")
    replacement = _source("procedure_replacement_transaction.py")
    rollback = _source("procedure_rollback.py")
    activation = _source("infrastructure/procedure_activation.py")
    workflow_imports = _imports("repair_workflow.py")
    materializer_imports = _imports("repair_patch_materializer.py")

    assert not any(
        name.startswith("agentx.infrastructure") for name in workflow_imports
    )
    assert "ProcedureStatus.CANDIDATE" in materializer
    assert not any(
        name.startswith("agentx.infrastructure") for name in materializer_imports
    )

    shared_import = "from agentx.infrastructure.procedure_activation import"
    assert shared_import in replacement
    assert shared_import in rollback
    assert "activate_procedure_revision_atomically(" in replacement
    assert "activate_procedure_revision_atomically(" in rollback

    assert "with _write_transaction(connection):" in activation
    assert "history != expected_history" in activation
    assert "target_candidate.status is not ProcedureStatus.CANDIDATE" in activation
    assert "len(active_records) > 1" in activation
    assert "active_records != (expected_active,)" in activation
    assert "atomic transition did not produce exactly one ACTIVE revision" in activation

    assert ProcedureStatus.RETIRED in TERMINAL_PROCEDURE_STATUSES
    assert "if target.status is ProcedureStatus.RETIRED:" in rollback
    assert "revision=request.current_revision + 1" in rollback
    assert "status=ProcedureStatus.CANDIDATE" in rollback


def test_n2_21_to_n2_25_keep_risk_action_resolution_observation_and_verification_separate(
) -> None:
    """Action evidence cannot collapse policy, resolution or verification."""
    launch = _source("capabilities/windows/application_launch_v2.py")
    risk = _source("capabilities/filesystem_structural_risk.py")
    keyboard = _source("capabilities/windows/keyboard_text_clipboard.py")
    resolver = _source("capabilities/windows/uia_target_resolution.py")
    verification = _source("windows_transition_verification.py")
    risk_imports = _imports("capabilities/filesystem_structural_risk.py")
    resolver_imports = _imports("capabilities/windows/uia_target_resolution.py")

    assert "RiskAssessment" in risk
    assert "The policy never performs filesystem I/O" in risk
    assert "never executes a filesystem operation" in risk
    assert not any(
        name.startswith("agentx.infrastructure") for name in risk_imports
    )

    assert "CapabilityObservation" in launch
    assert "readiness is unverified" in launch
    assert "CapabilityObservation" in keyboard
    assert "untrusted data" in keyboard.lower()

    assert "pure data-processing boundary" in resolver
    assert "does not call UI Automation" in resolver
    assert "does not click, invoke, type" in resolver
    assert not any(name.startswith("agentx.kernel") for name in resolver_imports)

    assert "NATIVE API SUCCESS != INTENDED USER-VISIBLE STATE SUCCESS" in verification
    assert "post-state observation" in verification
    assert "native_reported_success" in verification
    assert "never from an execution return value" in verification
