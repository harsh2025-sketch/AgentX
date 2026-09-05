"""Architecture guards for the C5.02 browser connection/target boundary."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "capabilities" / "browser_connection.py"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _tree() -> ast.Module:
    return ast.parse(_MODULE.read_text(encoding="utf-8"), filename=str(_MODULE))


def _imports() -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


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


def _class_methods(class_name: str) -> set[str]:
    for node in _tree().body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                member.name
                for member in node.body
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
            }
    raise AssertionError(f"class {class_name} not found")


def test_c5_02_lives_in_capabilities_beside_c5_01() -> None:
    assert _MODULE.is_file()
    assert _MODULE.parts[-3:] == (
        "agentx",
        "capabilities",
        "browser_connection.py",
    )
    assert "agentx.capabilities.browser_provider" in _imports()


def test_c5_02_reuses_only_canonical_c5_01_provider_identity() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx.")}
    assert agentx_imports == {"agentx.capabilities.browser_provider"}


def test_c5_02_has_no_kernel_cognition_hive_learning_procedure_or_infrastructure_import() -> None:
    forbidden_prefixes = (
        "agentx.cognition",
        "agentx.core",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.kernel",
        "agentx.learning",
        "agentx.procedures",
    )
    assert not any(name.startswith(prefix) for name in _imports() for prefix in forbidden_prefixes)


def test_c5_02_has_no_browser_network_process_or_persistence_imports() -> None:
    forbidden_roots = {
        "aiohttp",
        "asyncio",
        "http",
        "httpx",
        "netrc",
        "os",
        "playwright",
        "requests",
        "selenium",
        "socket",
        "sqlite3",
        "subprocess",
        "urllib",
        "webbrowser",
        "websocket",
        "websockets",
    }
    roots = {name.split(".", maxsplit=1)[0] for name in _imports()}
    assert not roots & forbidden_roots


def test_c5_02_calls_no_browser_connection_execution_or_io_surface() -> None:
    forbidden_calls = {
        "connect",
        "create_connection",
        "disconnect",
        "launch",
        "Popen",
        "open",
        "read",
        "write",
        "socket",
        "navigate",
        "goto",
        "click",
        "type_text",
        "fill",
        "submit",
        "upload",
        "download",
        "evaluate",
        "execute",
        "verify",
        "screenshot",
        "register",
    }
    assert not _called_names() & forbidden_calls


def test_c5_02_exposes_snapshot_methods_only() -> None:
    assert _class_methods("BrowserConnectionRef") == {
        "__post_init__",
        "provider_id",
        "to_dict",
        "to_json",
    }
    assert _class_methods("BrowserTargetRef") == {
        "__post_init__",
        "provider_id",
        "session_id",
        "to_dict",
        "to_json",
    }


def test_c5_02_defines_no_c5_03_dom_navigation_or_interaction_behavior() -> None:
    defined = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    forbidden = {
        "BrowserPage",
        "BrowserDom",
        "DomNode",
        "navigate",
        "goto",
        "query_selector",
        "read_dom",
        "click",
        "type_text",
        "fill",
        "submit",
        "evaluate_javascript",
        "execute_javascript",
        "capture_screenshot",
        "verify_browser",
    }
    assert defined.isdisjoint(forbidden)


def test_c5_02_defines_no_authority_execution_or_verification_contracts() -> None:
    class_names = {node.name for node in _tree().body if isinstance(node, ast.ClassDef)}
    forbidden = {
        "ActionGate",
        "AuthorityContext",
        "Permission",
        "RiskLevel",
        "ResourceBudget",
        "ResourceEnvelope",
        "EmergencyStop",
        "Capability",
        "CapabilityRegistry",
        "ExecutionContext",
        "VerificationResult",
        "Task",
        "ProcedureGraph",
    }
    assert class_names.isdisjoint(forbidden)


def test_c5_02_production_surface_is_minimal_and_closed() -> None:
    class_names = {node.name for node in _tree().body if isinstance(node, ast.ClassDef)}
    assert class_names == {
        "BrowserConnectionValidationError",
        "BrowserSessionId",
        "BrowserTargetId",
        "BrowserConnectionState",
        "BrowserTargetKind",
        "BrowserTargetState",
        "BrowserConnectionRef",
        "BrowserTargetRef",
    }


def test_c5_02_adds_no_runtime_dependency() -> None:
    config = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    assert config["project"]["dependencies"] == []
