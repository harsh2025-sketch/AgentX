"""Architecture guards for the N2.26 governed browser navigation capability."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "capabilities" / "browser_navigation.py"
_ARCHITECTURE = _REPO_ROOT / "src" / "agentx" / "_architecture.py"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE, filename=str(_MODULE))


def _imports() -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def _called_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _class_methods(class_name: str) -> set[str]:
    for node in _TREE.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                member.name
                for member in node.body
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
            }
    raise AssertionError(f"class {class_name} not found")


def test_browser_navigation_lives_in_capabilities() -> None:
    assert _MODULE.is_file()
    assert _MODULE.parts[-3:] == ("agentx", "capabilities", "browser_navigation.py")


def test_browser_navigation_does_not_import_outer_adaptive_subsystems() -> None:
    forbidden_prefixes = (
        "agentx.cognition",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.procedures",
        "agentx.learning",
    )
    imported = _imports()
    assert not any(
        name == prefix or name.startswith(f"{prefix}.")
        for name in imported
        for prefix in forbidden_prefixes
    )


def test_browser_navigation_does_not_alter_architecture_manifest() -> None:
    architecture_source = _ARCHITECTURE.read_text(encoding="utf-8")
    assert "browser_navigation" not in architecture_source
    assert "NAVIGATE_TO_URL" not in architecture_source


def test_browser_navigation_does_not_redefine_canonical_browser_contracts() -> None:
    class_names = {node.name for node in _TREE.body if isinstance(node, ast.ClassDef)}
    forbidden = {
        "BrowserConnectionRef",
        "BrowserTargetRef",
        "BrowserSessionId",
        "BrowserTargetId",
        "BrowserDomNodeRef",
        "BrowserDomNodeSnapshot",
        "BrowserDomObservation",
        "BrowserDomReadRequest",
        "BrowserDomSelector",
        "BrowserDomSelectionResult",
        "BrowserProvider",
        "BrowserProviderId",
        "BrowserProviderStatus",
        "BrowserProviderDescriptor",
        "CapabilityRequest",
        "CapabilityDescriptor",
        "CapabilityRegistry",
        "ActionGate",
    }
    assert class_names.isdisjoint(forbidden)
    imported = _imports()
    assert "agentx.capabilities.browser_connection" in imported
    assert "agentx.capabilities.browser_dom" in imported
    assert "agentx.capabilities.browser_provider" in imported


def test_browser_navigation_is_standalone_and_does_not_duplicate_actions() -> None:
    # The navigation capability shares no code, no types, and no imports with
    # the M7.01 action module or the C5.04 selection module. It reuses only the
    # canonical C5.01-C5.03 contracts beneath both.
    imported = _imports()
    assert "agentx.capabilities.browser_actions" not in imported
    assert "agentx.capabilities.browser_selection" not in imported
    source = _SOURCE
    assert "from agentx.capabilities.browser_actions" not in source
    assert "import browser_actions" not in source
    assert "click_selected" not in source
    assert "fill_selected" not in source
    assert "submit_selected" not in source
    assert "BrowserActionDriver" not in source
    assert "BrowserActionParams" not in source
    assert "BrowserActionsCapability" not in source


def test_browser_navigation_exposes_one_closed_operation_only() -> None:
    imported = _imports()
    assert "agentx.capabilities.abi" in imported
    source = _SOURCE
    assert "NAVIGATE_TO_URL" in source
    assert "BrowserNavigationOperation" in source
    # The driver surface is exactly navigation plus the independent read.
    driver_methods = _class_methods("BrowserNavigationDriver")
    assert driver_methods == {"navigate", "observe_dom"}
    defined = {
        node.name
        for node in ast.walk(_TREE)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    forbidden = {
        "execute_script",
        "evaluate",
        "evaluate_javascript",
        "execute_javascript",
        "javascript",
        "eval_js",
        "runtime_evaluate",
        "send_cdp",
        "cdp",
        "click",
        "click_selected",
        "fill_selected",
        "submit_selected",
        "submit",
        "accept_dialog",
        "launch_browser",
    }
    assert defined.isdisjoint(forbidden)
    assert "execute_script" not in source
    assert "evaluate_javascript" not in source


def test_browser_navigation_does_not_use_process_or_dynamic_import_primitives() -> None:
    imported = _imports()
    roots = {name.split(".", maxsplit=1)[0] for name in imported}
    assert "subprocess" not in roots
    assert "os" not in roots
    assert "importlib" not in roots
    assert "playwright" not in roots
    assert "selenium" not in roots
    assert "socket" not in roots
    called = _called_names()
    assert called.isdisjoint({"eval", "exec", "compile", "__import__", "system", "Popen", "run"})
    assert "os.system" not in _SOURCE
    assert "subprocess" not in _SOURCE


def test_browser_navigation_does_not_import_runtime_executor_or_registry() -> None:
    imported = _imports()
    assert "agentx.capabilities.runtime" not in imported
    assert "agentx.capabilities.executor" not in imported
    assert "agentx.capabilities.registry" not in imported
    assert "agentx.capabilities.verifier" not in imported
    assert "agentx.agent_loop" not in imported
    assert "agentx.core.task_state" not in imported


def test_browser_navigation_reuses_canonical_abi_and_kernel_contracts() -> None:
    imported = _imports()
    assert "agentx.capabilities.abi" in imported
    assert "agentx.kernel.permissions" in imported
    assert "agentx.kernel.risk" in imported
    assert "agentx.core.errors" in imported
    assert "agentx.core.result" in imported
    assert "agentx.core.execution" in imported


def test_browser_navigation_does_not_call_action_gate_or_kernel_authority() -> None:
    imported = _imports()
    assert "agentx.kernel.action_gate" not in imported
    assert "ActionGate" not in _SOURCE
    assert "PermissionEngine" not in _SOURCE
    assert "check_and_consume" not in _SOURCE
    assert "EmergencyStop" not in _SOURCE


def test_browser_navigation_adds_no_runtime_dependency() -> None:
    config = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    assert config["project"]["dependencies"] == []


def test_url_parsing_is_stdlib_only() -> None:
    imported = _imports()
    assert "urllib.parse" in imported
    assert "urllib.request" not in imported
    assert "webbrowser" not in imported
    assert "requests" not in imported
    assert "httpx" not in imported
