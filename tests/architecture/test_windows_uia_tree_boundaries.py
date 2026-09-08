"""Architecture guardrails for the A5.03 read-only UIA inspection boundary."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from agentx.capabilities.windows.uia_tree import (
    UIAElementSnapshot,
    UIATreeSnapshot,
    WindowsUIATreeInspection,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WINDOWS = _REPO_ROOT / "src" / "agentx" / "capabilities" / "windows"
_MODULE = _WINDOWS / "uia_tree.py"
_NATIVE = _WINDOWS / "_uia_native.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def _defined_methods(path: Path) -> set[str]:
    return {
        node.name
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_a5_03_lives_in_windows_capabilities() -> None:
    assert WindowsUIATreeInspection.__module__ == "agentx.capabilities.windows.uia_tree"
    assert UIAElementSnapshot.__module__ == "agentx.capabilities.windows.uia_tree"
    assert UIATreeSnapshot.__module__ == "agentx.capabilities.windows.uia_tree"


def test_public_boundary_uses_only_canonical_windows_provider_core_and_native_seam() -> None:
    agentx_imports = {module for module in _imports(_MODULE) if module.startswith("agentx.")}
    assert agentx_imports == {
        "agentx.capabilities.windows",
        "agentx.capabilities.windows.provider",
        "agentx.core.errors",
        "agentx.core.result",
        "agentx.core.tasks",
    }
    assert not any(
        module.startswith(prefix)
        for module in agentx_imports
        for prefix in (
            "agentx.cognition",
            "agentx.hive",
            "agentx.learning",
            "agentx.procedures",
            "agentx.infrastructure",
        )
    )


def test_uia_native_seam_has_zero_third_party_dependency() -> None:
    stdlib = set(sys.stdlib_module_names)
    for module in _imports(_NATIVE):
        root = module.split(".", 1)[0]
        assert root in stdlib or root == "agentx", module


def test_ctypes_is_not_imported_at_module_level() -> None:
    for path in (_MODULE, _NATIVE):
        for node in _tree(path).body:
            if isinstance(node, ast.Import):
                assert all(alias.name != "ctypes" for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                assert node.module != "ctypes"


def test_public_boundary_defines_only_inspection_not_actions() -> None:
    forbidden = {
        "click",
        "invoke",
        "type",
        "type_text",
        "set_value",
        "set_focus",
        "send_keys",
        "press_key",
        "move_mouse",
        "execute",
        "verify",
        "succeed",
        "activate",
    }
    assert _defined_methods(_MODULE).isdisjoint(forbidden)
    assert forbidden.isdisjoint(dir(WindowsUIATreeInspection))


def test_native_seam_uses_no_action_or_input_api() -> None:
    source = _NATIVE.read_text(encoding="utf-8")
    forbidden = (
        "SetFocus",
        "SendInput",
        "keybd_event",
        "mouse_event",
        "SetWindowText",
        "SetForegroundWindow",
        "PostMessage",
        "SendMessage",
        "SetCursorPos",
        "click",
        "type_text",
        "screenshot",
        "ocr",
    )
    assert not any(token in source for token in forbidden)


def test_native_seam_never_obtains_a_pattern_object() -> None:
    source = _NATIVE.read_text(encoding="utf-8")
    # A5.03 reads only Is*PatternAvailable property IDs. Pattern interfaces
    # would create an action-capable surface and are therefore out of scope.
    assert "GetCurrentPattern" not in source
    assert "GetCurrentPatternAs" not in source
    assert "GetCachedPattern" not in source


def test_traversal_is_iterative_and_has_two_explicit_bounds() -> None:
    source = _NATIVE.read_text(encoding="utf-8")
    assert "max_depth" in source
    assert "max_nodes" in source
    assert "stack" in source
    functions = {
        node.name: node for node in ast.walk(_tree(_NATIVE)) if isinstance(node, ast.FunctionDef)
    }
    for name, function in functions.items():
        recursive_calls = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
        ]
        assert recursive_calls == [], name


def test_no_vision_semantic_resolution_models_or_research() -> None:
    imports = _imports(_MODULE) | _imports(_NATIVE)
    forbidden_roots = {
        "cv2",
        "PIL",
        "numpy",
        "mss",
        "pytesseract",
        "openai",
        "anthropic",
        "transformers",
        "sentence_transformers",
    }
    assert {module.split(".", 1)[0] for module in imports}.isdisjoint(forbidden_roots)
    source = (_MODULE.read_text(encoding="utf-8") + _NATIVE.read_text(encoding="utf-8")).lower()
    assert "semantic_resolver" not in source
    assert "vision_fallback" not in source


def test_no_environment_filesystem_network_or_persistence_surface() -> None:
    imports = _imports(_MODULE) | _imports(_NATIVE)
    forbidden_roots = {
        "os",
        "pathlib",
        "subprocess",
        "socket",
        "urllib",
        "http",
        "sqlite3",
        "threading",
        "asyncio",
    }
    assert {module.split(".", 1)[0] for module in imports}.isdisjoint(forbidden_roots)
    source = (_MODULE.read_text(encoding="utf-8") + _NATIVE.read_text(encoding="utf-8")).lower()
    for token in ("knowledge_store", "episode_store", "procedure_store", "hive", "migration"):
        assert token not in source


def test_no_authority_kernel_dependency() -> None:
    imports = _imports(_MODULE) | _imports(_NATIVE)
    assert not any(module.startswith("agentx.kernel") for module in imports)


def test_runtime_dependency_set_remains_empty() -> None:
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject
    for dependency in ("pywin32", "comtypes", "pywinauto", "uiautomation"):
        assert dependency not in pyproject
