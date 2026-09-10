"""Architecture guards for the N2.14 repair workflow orchestrator.

The orchestrator is a pure top-level composition module at the ``agentx``
namespace root. These static guards prove it stays that way: core-only
imports (plus the sibling degradation policy), no subsystem residence, no
architecture-manifest widening, no store/lifecycle/authority surface, no
duplicated stage logic, and no I/O, clock, model, or dynamic execution.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_MODULE = _AGENTX_SRC / "repair_workflow.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_ARCHITECTURE_SOURCE = (_AGENTX_SRC / "_architecture.py").read_text(encoding="utf-8")
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

_STAGE_MODULES = (
    _AGENTX_SRC / "procedure_degradation.py",
    _AGENTX_SRC / "core" / "repair_candidates.py",
    _AGENTX_SRC / "core" / "repair_patch.py",
    _AGENTX_SRC / "core" / "repair_budget.py",
    _AGENTX_SRC / "core" / "repair_validation.py",
    _AGENTX_SRC / "core" / "shadow_repair.py",
    _AGENTX_SRC / "core" / "procedure_replacement.py",
)


def _tree() -> ast.Module:
    return ast.parse(_SOURCE)


def _imports() -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def _classes() -> set[str]:
    return {node.name for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)}


def _public_functions() -> set[str]:
    return {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }


def _called_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _referenced_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


# --------------------------------------------------------------------------
# placement
# --------------------------------------------------------------------------


def test_module_is_a_single_top_level_composition_file() -> None:
    assert _MODULE.is_file()
    assert _MODULE.parent == _AGENTX_SRC
    assert not (_AGENTX_SRC / "repair_workflow").exists()


def test_module_is_not_inside_any_canonical_subsystem() -> None:
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        assert not _MODULE.is_relative_to(package)


def test_module_did_not_create_a_new_top_level_subsystem() -> None:
    assert "agentx.repair_workflow" not in _architecture.SUBSYSTEMS
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


def test_architecture_manifest_unchanged() -> None:
    assert "repair_workflow" not in _ARCHITECTURE_SOURCE
    assert "REPAIR" not in _ARCHITECTURE_SOURCE
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


def test_no_subsystem_imports_the_orchestrator() -> None:
    offenders: list[str] = []
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        for path in sorted(package.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "repair_workflow" not in text:
                continue
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.Import):
                    offenders.extend(
                        str(path.relative_to(_SRC_ROOT))
                        for alias in node.names
                        if "repair_workflow" in alias.name
                    )
                elif (
                    isinstance(node, ast.ImportFrom)
                    and node.module is not None
                    and "repair_workflow" in node.module
                ):
                    offenders.append(str(path.relative_to(_SRC_ROOT)))
    assert offenders == []


# --------------------------------------------------------------------------
# imports / surface
# --------------------------------------------------------------------------


def test_imports_only_core_contracts_and_the_sibling_degradation_policy() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx")}
    assert agentx_imports == {
        "agentx.core.ids",
        "agentx.core.procedure_replacement",
        "agentx.core.repair_budget",
        "agentx.core.repair_candidates",
        "agentx.core.repair_patch",
        "agentx.core.repair_validation",
        "agentx.core.shadow_repair",
        "agentx.procedure_degradation",
    }
    for foreign in (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.hive",
        "agentx.learning",
        "agentx.infrastructure",
        "agentx.procedures",
    ):
        assert not any(name == foreign or name.startswith(foreign + ".") for name in agentx_imports)

    non_agentx = {name.split(".")[0] for name in _imports() if not name.startswith("agentx")}
    assert non_agentx <= {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "typing",
    }


def test_public_entry_point_is_the_single_orchestration_function() -> None:
    assert _public_functions() == {"run_repair_workflow"}
    assert _public_functions().isdisjoint(
        {
            "apply_repair",
            "activate",
            "replace",
            "rollback",
            "execute",
            "persist",
            "grant",
            "authorize",
            "update_status",
            "score",
            "rank",
        }
    )


def test_defined_classes_are_composition_data_only() -> None:
    classes = _classes()
    assert {
        "RepairWorkflowStage",
        "RepairWorkflowStopReason",
        "RepairWorkflowOutcome",
        "RepairWorkflowTarget",
        "RepairWorkflowRequest",
        "RepairWorkflowEvidence",
        "RepairWorkflowResult",
        "RepairWorkflowValidationError",
    } <= classes
    assert classes.isdisjoint(
        {
            "ActionGate",
            "AuthorityContext",
            "Permission",
            "ResourceBudget",
            "ResourceEnvelope",
            "EmergencyStop",
            "ProcedureStore",
            "ProcedureGraph",
            "Executor",
            "Verifier",
            "Reasoner",
            "TaskManager",
            "ModelProvider",
        }
    )


def test_no_stage_contract_is_redefined_here() -> None:
    """The orchestrator composes canonical stage types; it never redeclares them."""
    forbidden = {
        "DegradationState",
        "ProcedureDegradationAssessment",
        "RepairCandidate",
        "RepairCandidateKind",
        "RepairPatchProposal",
        "RepairPatchKind",
        "RepairBudgetAssessment",
        "RepairBudgetLimits",
        "RepairBudgetDecision",
        "RepairValidationReport",
        "RepairValidationDisposition",
        "ShadowRepairResult",
        "ShadowRepairDisposition",
        "ProcedureReplacementDecision",
        "ProcedureReplacementRequest",
    }
    assert _classes().isdisjoint(forbidden)


def test_canonical_stage_functions_are_called_not_reimplemented() -> None:
    called = _called_names()
    assert {
        "assess_procedure_degradation",
        "derive_repair_candidates",
        "assess_repair_attempt",
        "evaluate_repair_validation",
        "assess_procedure_replacement",
    } <= called


def test_no_io_clock_network_model_or_dynamic_execution() -> None:
    assert _imports().isdisjoint(
        {
            "importlib",
            "pickle",
            "subprocess",
            "socket",
            "sqlite3",
            "urllib",
            "http",
            "os",
            "pathlib",
            "random",
            "secrets",
            "re",
            "threading",
            "ctypes",
            "time",
            "sys",
            "asyncio",
            "logging",
        }
    )
    assert _called_names().isdisjoint(
        {
            "eval",
            "exec",
            "open",
            "now",
            "utcnow",
            "today",
            "sleep",
            "urlopen",
            "__import__",
            "system",
            "Popen",
            "complete",
            "generate",
            "invoke_model",
        }
    )


def test_no_store_lifecycle_or_authority_symbol_is_referenced() -> None:
    referenced = _referenced_names()
    for forbidden in (
        "ProcedureStore",
        "ProcedureStatus",
        "update_status",
        "insert",
        "delete",
        "commit",
        "ActionGate",
        "EmergencyStop",
        "ResourceBudget",
        "Permission",
        "RiskLevel",
    ):
        assert forbidden not in referenced


def test_adds_no_runtime_dependency() -> None:
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []


# --------------------------------------------------------------------------
# ownership: stage contracts are untouched
# --------------------------------------------------------------------------


def test_owned_stage_contract_files_are_not_extended_with_workflow_symbols() -> None:
    for path in _STAGE_MODULES:
        text = path.read_text(encoding="utf-8")
        assert "repair_workflow" not in text
        assert "RepairWorkflowStage" not in text
        assert "run_repair_workflow" not in text


def test_stage_contracts_still_expose_their_canonical_entry_points() -> None:
    expectations = {
        _AGENTX_SRC / "procedure_degradation.py": "def assess_procedure_degradation(",
        _AGENTX_SRC / "core" / "repair_candidates.py": "def derive_repair_candidates(",
        _AGENTX_SRC / "core" / "repair_budget.py": "def assess_repair_attempt(",
        _AGENTX_SRC / "core" / "repair_validation.py": "def evaluate_repair_validation(",
        _AGENTX_SRC / "core" / "procedure_replacement.py": "def assess_procedure_replacement(",
    }
    for path, marker in expectations.items():
        assert marker in path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# semantics guards
# --------------------------------------------------------------------------


def test_no_keyword_inference_from_free_text() -> None:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Compare):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    assert comparator.value.lower() not in {
                        "repair_approved=true",
                        "shadow_safe=true",
                        "activate_candidate=true",
                        "permission=admin",
                        "risk=r0",
                        "approved",
                        "safe",
                        "true",
                    }


def test_no_confidence_scoring_or_model_vocabulary() -> None:
    lowered = _SOURCE.lower()
    for token in (
        "confidence=",
        "probability=",
        "score=",
        "weight=",
        "embedding=",
        "openai",
        "anthropic",
        "vector_store",
        "prompt=",
    ):
        assert token not in lowered


def test_docs_page_exists_and_states_the_boundaries() -> None:
    docs = _REPO_ROOT / "docs" / "repair_workflow.md"
    assert docs.is_file()
    text = docs.read_text(encoding="utf-8")
    assert "N2.14" in text
    assert "ProcedureStore" in text
    assert "stage" in text.lower()
    assert "never" in text.lower()
    for stage in (
        "degradation",
        "candidate",
        "patch_proposal",
        "budget",
        "validation",
        "shadow",
        "replacement",
    ):
        assert stage in text
