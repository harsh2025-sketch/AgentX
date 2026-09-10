"""Architecture guards for the N2.24 UIA semantic target resolution boundary."""

from __future__ import annotations

import ast
import inspect
import sys
from dataclasses import fields
from pathlib import Path

from agentx.capabilities.windows.uia_target_resolution import (
    UIATargetQuery,
    UIATargetResolutionResult,
    UIATargetResolutionStatus,
    resolve_uia_target,
)

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src" / "agentx" / "capabilities" / "windows" / "uia_target_resolution.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)


def _imports() -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _call_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _function_names() -> set[str]:
    return {
        node.name
        for node in ast.walk(_TREE)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _class_names() -> set[str]:
    return {node.name for node in _TREE.body if isinstance(node, ast.ClassDef)}


def _attribute_names() -> set[str]:
    return {node.attr for node in ast.walk(_TREE) if isinstance(node, ast.Attribute)}


def test_resolution_lives_in_windows_capabilities_boundary() -> None:
    assert _MODULE.exists()
    assert _MODULE.parent.name == "windows"
    assert _MODULE.parent.parent.name == "capabilities"
    assert resolve_uia_target.__module__ == "agentx.capabilities.windows.uia_target_resolution"
    assert UIATargetResolutionResult.__module__ == (
        "agentx.capabilities.windows.uia_target_resolution"
    )


def test_resolution_reuses_canonical_a5_03_observation_contracts() -> None:
    imported = _imports()
    assert "agentx.capabilities.windows.uia_tree" in imported
    assert "agentx.core.tasks" in imported
    assert "agentx.capabilities.windows.provider" not in imported
    assert "agentx.capabilities.windows._uia_native" not in imported
    assert "agentx.capabilities.windows._native" not in imported
    assert UIATargetResolutionResult.__annotations__["snapshot"] == "UIATreeSnapshot"
    assert UIATargetQuery.__annotations__["reference"] == "UIAElementReference | None"


def test_additional_criterion_set_is_exactly_canonical_structured_fields() -> None:
    assert [field.name for field in fields(UIATargetQuery)] == [
        "reference",
        "automation_id",
        "control_type",
        "name",
        "parent_path",
        "ancestor_path",
        "require_enabled",
        "require_visible",
        "require_keyboard_focusable",
    ]
    assert "text" not in [field.name for field in fields(UIATargetQuery)]
    assert "prompt" not in [field.name for field in fields(UIATargetQuery)]


def test_resolution_status_vocabulary_is_exactly_three_cardinality_outcomes() -> None:
    assert [status.value for status in UIATargetResolutionStatus] == [
        "resolved",
        "not_found",
        "ambiguous",
    ]
    assert "first" not in UIATargetResolutionStatus.__members__
    assert "last" not in UIATargetResolutionStatus.__members__
    assert "nearest" not in UIATargetResolutionStatus.__members__


def test_resolution_api_pure_function_takes_observation_and_query_and_returns_result() -> None:
    signature = inspect.signature(resolve_uia_target)
    assert list(signature.parameters) == ["snapshot", "query"]
    assert signature.return_annotation == "UIATargetResolutionResult"


def test_no_uia_invocation_focus_value_keyboard_or_mouse_surface() -> None:
    function_names = _function_names()
    attribute_names = _attribute_names()
    forbidden_methods = {
        "activate",
        "click",
        "execute",
        "invoke",
        "move_mouse",
        "press_key",
        "send_keys",
        "set_focus",
        "set_value",
        "type_text",
        "verify",
    }
    assert function_names.isdisjoint(forbidden_methods)
    assert attribute_names.isdisjoint(forbidden_methods)
    assert not hasattr(resolve_uia_target, "set_focus")
    assert not hasattr(UIATargetQuery, "invoke")
    assert not hasattr(UIATargetResolutionResult, "execute")
    for token in (
        "InvokePattern",
        "SelectionItemPattern",
        "SetFocus",
        "SendInput",
        "GetCurrentPattern",
        "ValuePattern",
    ):
        assert token not in _SOURCE


def test_no_model_embedding_or_fuzzy_semantic_machinery() -> None:
    imported = _imports()
    calls = _call_names()
    forbidden_imports = {
        "openai",
        "anthropic",
        "transformers",
        "sentence_transformers",
        "numpy",
        "cv2",
        "PIL",
        "pytesseract",
    }
    assert imported.isdisjoint(forbidden_imports)
    assert all(not name.startswith(prefix) for name in imported for prefix in forbidden_imports)
    assert {"re", "regex", "difflib", "rapidfuzz", "fuzzywuzzy"} & imported == set()
    assert {"search", "match", "fullmatch", "similarity", "score", "embed"} & calls == set()
    assert "agentx.cognition" not in imported
    assert "agentx.cognition.model_provider" not in imported


def test_no_live_desktop_process_network_or_persistence_surface() -> None:
    imported = _imports()
    calls = _call_names()
    forbidden_imports = {
        "ctypes",
        "subprocess",
        "os",
        "pathlib",
        "socket",
        "urllib",
        "http",
        "sqlite3",
        "threading",
        "asyncio",
        "msvcrt",
        "win32api",
        "pywinauto",
        "keyboard",
        "mouse",
        "pyautogui",
    }
    assert imported.isdisjoint(forbidden_imports)
    assert calls.isdisjoint({"open", "connect", "send", "recv", "Popen", "run", "eval", "exec"})
    assert {"agentx.hive", "agentx.infrastructure"} & imported == set()
    assert {
        "agentx.kernel",
        "agentx.cognition",
        "agentx.learning",
        "agentx.procedures",
    } & imported == set()


def test_no_authority_permission_risk_budget_or_task_symbols() -> None:
    tokens = (
        "ActionGate",
        "Permissions",
        "PermissionEngine",
        "AuthorityContext",
        "RiskAssessment",
        "ResourceBudget",
        "EmergencyStop",
        "TaskStatus",
        "Capability",
    )
    assert all(token not in _SOURCE for token in tokens)
    assert "PermissionGrant" not in _SOURCE
    assert "Risk" not in _SOURCE


def test_no_dynamic_or_provider_registry_surface() -> None:
    imported = _imports()
    calls = _call_names()
    assert "agentx.capabilities.registry" not in imported
    assert "agentx.capabilities.abi" not in imported
    assert calls.isdisjoint({"eval", "exec", "compile", "__import__"})
    assert "importlib" not in imported
    assert "load_module" not in _SOURCE


def test_production_module_has_no_module_level_side_effects() -> None:
    for node in _TREE.body:
        assert not isinstance(node, ast.Expr | ast.For | ast.While | ast.With | ast.Try) or (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        ), f"executable top-level statement: {node.lineno}"
        assert not isinstance(node, ast.If), "conditional module-level logic is forbidden"


def test_runtime_dependency_set_remains_empty() -> None:
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject
    for dependency in ("pywin32", "comtypes", "pywinauto", "uiautomation"):
        assert dependency not in pyproject


def test_windows_package_uses_only_stdlib_and_canonical_agentx() -> None:
    stdlib = set(sys.stdlib_module_names)
    for module in _imports():
        if module.startswith("agentx"):
            assert module in {
                "agentx.capabilities.windows.uia_tree",
                "agentx.core.tasks",
            }
        else:
            assert module.split(".", 1)[0] in stdlib
