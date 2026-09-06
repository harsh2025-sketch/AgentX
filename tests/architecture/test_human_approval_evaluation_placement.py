"""Architecture constraints for A6.09 approval evidence evaluation."""

from __future__ import annotations

import ast
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "src" / "agentx" / "capabilities" / "human_approval_evaluation.py"
SOURCE = MODULE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _imported_modules() -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(TREE):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def _defined_classes() -> set[str]:
    return {node.name for node in TREE.body if isinstance(node, ast.ClassDef)}


def _defined_functions() -> set[str]:
    return {node.name for node in TREE.body if isinstance(node, ast.FunctionDef)}


def _loaded_names() -> set[str]:
    return {
        node.id
        for node in ast.walk(TREE)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _called_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(TREE):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _module_assigned_names() -> set[str]:
    names: set[str] = set()
    for node in TREE.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _function(name: str) -> ast.FunctionDef:
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function: {name}")


def _class(name: str) -> ast.ClassDef:
    for node in TREE.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"missing class: {name}")


def _argument_annotation(function: ast.FunctionDef, name: str) -> str:
    for argument in function.args.args:
        if argument.arg == name and argument.annotation is not None:
            return ast.unparse(argument.annotation)
    raise AssertionError(f"missing typed argument: {name}")


def test_module_is_in_capabilities_boundary() -> None:
    assert MODULE_PATH.as_posix().endswith("src/agentx/capabilities/human_approval_evaluation.py")


def test_production_surface_and_result_vocabulary_are_minimal() -> None:
    assert _defined_classes() == {"HumanApprovalEvidenceStatus"}
    assert _defined_functions() == {"evaluate_human_approval_evidence"}

    status_class = _class("HumanApprovalEvidenceStatus")
    members = {
        target.id
        for node in status_class.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert members == {
        "MATCHING_APPROVED",
        "MATCHING_DENIED",
        "MISSING",
        "MISMATCHED",
    }


def test_evaluator_reuses_canonical_a608_types_and_binding() -> None:
    imports = _imported_modules()
    assert "agentx.capabilities.human_approval" in imports

    function = _function("evaluate_human_approval_evidence")
    assert _argument_annotation(function, "request") == "HumanApprovalRequest"
    assert _argument_annotation(function, "decision") == "HumanApprovalDecision | None"
    assert "validate_binding" in {
        node.attr for node in ast.walk(function) if isinstance(node, ast.Attribute)
    }


def test_evaluator_does_not_import_human_mode_as_authority() -> None:
    imports = _imported_modules()
    assert "agentx.core.human_operating_modes" not in imports
    assert "HumanOperatingMode" not in _loaded_names()


def test_evaluator_has_no_action_gate_or_authority_creation_imports() -> None:
    imports = _imported_modules()
    forbidden = {
        "agentx.kernel.action_gate",
        "agentx.kernel.permissions",
        "agentx.kernel.risk",
        "agentx.kernel.resource_budget",
        "agentx.kernel.emergency_stop",
    }
    assert imports.isdisjoint(forbidden)
    assert _loaded_names().isdisjoint({"ActionGate", "AuthorityContext"})


def test_evaluator_has_no_execution_or_task_transition_imports() -> None:
    imports = _imported_modules()
    forbidden_prefixes = (
        "agentx.capabilities.runtime",
        "agentx.capabilities.executor",
        "agentx.capabilities.verifier",
        "agentx.cognition.task_manager",
        "agentx.core.task_state",
    )
    assert not any(module.startswith(forbidden_prefixes) for module in imports)


def test_evaluator_has_no_model_research_or_hive_imports() -> None:
    imports = _imported_modules()
    forbidden_prefixes = (
        "agentx.cognition",
        "agentx.research",
        "agentx.hive",
    )
    assert not any(module.startswith(forbidden_prefixes) for module in imports)


def test_evaluator_has_no_ui_or_persistence_imports() -> None:
    imports = _imported_modules()
    forbidden_fragments = (
        "sqlite",
        "sqlalchemy",
        "persistence",
        "storage",
        "database",
        "tkinter",
        "textual",
        "streamlit",
        "notifications",
    )
    assert not any(fragment in module for module in imports for fragment in forbidden_fragments)


def test_evaluator_has_no_state_service_constructs() -> None:
    assert _called_names().isdisjoint({"Lock", "Event", "Thread", "Timer"})
    assert "approved" not in _module_assigned_names()
    assert "cache" not in _module_assigned_names()
    assert not any(isinstance(node, ast.Global) for node in ast.walk(TREE))


def test_a607_a608_compatibility_keeps_mode_out_of_evaluation_authority() -> None:
    function = _function("evaluate_human_approval_evidence")
    assert _argument_annotation(function, "request") == "HumanApprovalRequest"
    assert _argument_annotation(function, "decision") == "HumanApprovalDecision | None"
    assert "HumanOperatingMode" not in SOURCE
