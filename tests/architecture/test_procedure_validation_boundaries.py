"""Architecture guardrails for the M4.02 procedure validation evidence policy.

These are import/structure guardrails, not security enforcement. Authority is
owned exclusively by the Trusted Kernel; procedure lifecycle transitions are
owned by C3.09 through the canonical ``ProcedureStore``.

The policy is a top-level composition module (alongside ``agent_loop``): it
must read canonical procedure identity from ``agentx.core`` and canonical
execution/verification evidence from ``agentx.capabilities``, without widening
the boundary manifest, executing any capability, persisting anything, mutating
any lifecycle, or reaching any authority.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture
from agentx.core.procedures import ProcedureStatus
from agentx.procedure_validation import (
    MIN_VERIFIED_SUCCESSES_FLOOR,
    ValidationDecision,
    ValidationPolicy,
    ValidationReport,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_MODULE_PATH = _AGENTX_SRC / "procedure_validation.py"


def _tree() -> ast.Module:
    return ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))


def _imports() -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(imported)


def _defined_classes() -> dict[str, ast.ClassDef]:
    return {node.name: node for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)}


# ---------------------------------------------------------------------------
# Placement: top-level composition, no subsystem, no manifest widening.
# ---------------------------------------------------------------------------


def test_policy_is_a_single_top_level_composition_module() -> None:
    assert _MODULE_PATH.is_file()
    assert _MODULE_PATH.parent == _AGENTX_SRC


def test_policy_is_not_inside_any_canonical_subsystem() -> None:
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        assert not _MODULE_PATH.is_relative_to(package)


def test_policy_did_not_create_a_new_subsystem() -> None:
    assert "agentx.procedure_validation" not in _architecture.SUBSYSTEMS
    assert not (_AGENTX_SRC / "procedure_validation").exists()
    assert _architecture.SUBSYSTEMS == (
        "agentx.core",
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.hive",
        "agentx.procedures",
        "agentx.cognition",
        "agentx.learning",
        "agentx.infrastructure",
    )


def test_boundary_manifest_was_not_widened() -> None:
    assert (
        frozenset(
            {
                (_architecture.KERNEL, _architecture.CORE),
                (_architecture.CAPABILITIES, _architecture.CORE),
                (_architecture.HIVE, _architecture.CORE),
                (_architecture.PROCEDURES, _architecture.CORE),
                (_architecture.COGNITION, _architecture.CORE),
                (_architecture.LEARNING, _architecture.CORE),
                (_architecture.INFRASTRUCTURE, _architecture.CORE),
                (_architecture.CAPABILITIES, _architecture.KERNEL),
                (_architecture.PROCEDURES, _architecture.KERNEL),
                (_architecture.COGNITION, _architecture.KERNEL),
            }
        )
        == _architecture.ALLOWED_ARCHITECTURE_EDGES
    )


# ---------------------------------------------------------------------------
# Import boundary: core + capabilities only, no authority or persistence.
# ---------------------------------------------------------------------------


def test_policy_imports_only_core_and_capabilities_contracts() -> None:
    imports = _imports()
    agentx_imports = [module for module in imports if module.startswith("agentx.")]
    assert agentx_imports, "policy must consume canonical contracts"
    for module in agentx_imports:
        assert module.startswith("agentx.core.") or module.startswith("agentx.capabilities."), (
            f"policy must import only agentx.core / agentx.capabilities; got {module}"
        )


def test_policy_imports_no_authority_or_governed_mechanism() -> None:
    imports = set(_imports())
    for forbidden in (
        "agentx.kernel",
        "agentx.kernel.permissions",
        "agentx.kernel.action_gate",
        "agentx.kernel.risk",
        "agentx.kernel.resource_budget",
        "agentx.kernel.emergency_stop",
        "agentx.kernel.audit",
    ):
        assert not any(module == forbidden for module in imports), forbidden


def test_policy_imports_no_persistence_or_lifecycle_store() -> None:
    imports = set(_imports())
    for forbidden in (
        "agentx.infrastructure",
        "agentx.infrastructure.persistence",
        "agentx.infrastructure.procedure_store",
        "agentx.infrastructure.knowledge_store",
        "agentx.infrastructure.episode_store",
        "sqlite3",
    ):
        assert not any(module == forbidden for module in imports), forbidden


def test_policy_imports_no_model_research_hive_or_learning() -> None:
    imports = set(_imports())
    for subsystem in ("agentx.cognition", "agentx.hive", "agentx.learning", "agentx.procedures"):
        assert not any(module.startswith(f"{subsystem}.") for module in imports), subsystem


# ---------------------------------------------------------------------------
# No execution, no mutation, no persistence surface.
# ---------------------------------------------------------------------------


def test_policy_never_imports_or_calls_capability_execution() -> None:
    imports = set(_imports())
    for forbidden in (
        "agentx.capabilities.registry",
        "agentx.capabilities.executor",
    ):
        assert not any(module == forbidden for module in imports), forbidden
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else (target.attr if isinstance(target, ast.Attribute) else "")
            )
            assert name not in {"execute", "verify"}, (
                f"policy must not invoke capability execute/verify ({name!r})"
            )


def test_policy_never_transitions_a_task() -> None:
    imports = set(_imports())
    assert "agentx.core.task_state" not in imports
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else (target.attr if isinstance(target, ast.Attribute) else "")
            )
            assert name not in {"transition_task", "try_transition_task", "update_status", "insert"}


def test_policy_exposes_no_mutating_or_persisting_api() -> None:
    classes = _defined_classes()
    forbidden_methods = {"persist", "promote", "activate", "execute", "write", "store", "save"}
    for class_name in ("ValidationPolicy", "ValidationReport", "ValidationRunEvidence"):
        assert class_name in classes
        methods = {
            node.name for node in ast.walk(classes[class_name]) if isinstance(node, ast.FunctionDef)
        }
        assert not (methods & forbidden_methods), (
            f"{class_name} exposes {methods & forbidden_methods}"
        )


def test_policy_has_no_module_level_side_effects() -> None:
    # The module defines only imports, classes, and pure functions; it calls
    # nothing at import time that could reach the outside world.
    forbidden_calls = {"open", "eval", "exec", "compile", "__import__", "system", "popen"}
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else (target.attr if isinstance(target, ast.Attribute) else "")
            )
            assert name not in forbidden_calls, name


# ---------------------------------------------------------------------------
# Decision vocabulary and the minimum-promotion floor.
# ---------------------------------------------------------------------------


def test_eligible_for_promotion_is_not_active() -> None:
    # The two vocabularies are disjoint: promotion eligibility never aliases a
    # persisted procedure status.
    decision_values = {member.value for member in ValidationDecision}
    status_values = {member.value for member in ProcedureStatus}
    assert decision_values.isdisjoint(status_values)


def test_decision_vocabulary_is_exactly_the_four_conceptual_outcomes() -> None:
    members = {member.name for member in ValidationDecision}
    assert members == {
        "INSUFFICIENT_EVIDENCE",
        "ELIGIBLE_FOR_PROMOTION",
        "REJECTED",
        "DEGRADED",
    }


def test_minimum_verified_successes_floor_is_two() -> None:
    assert MIN_VERIFIED_SUCCESSES_FLOOR == 2
    policy = ValidationPolicy()
    assert policy.min_verified_successes >= 2


def test_report_is_a_structured_value_not_a_bool() -> None:
    report_fields = {field.name for field in ValidationReport.__dataclass_fields__.values()}
    assert "decision" in report_fields
    assert "verified_successes" in report_fields
    assert "reasons" in report_fields
    assert "unmet_requirements" in report_fields
    # The authoritative field is the enum; the bool is only a convenience.
    assert isinstance(ValidationReport.eligible, property)
