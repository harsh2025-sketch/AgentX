"""Architecture-boundary tests for C5.01 browser-provider contracts."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_BROWSER_PROVIDER_PATH = _SRC_ROOT / "agentx" / "capabilities" / "browser_provider.py"
_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"


def _tree() -> ast.Module:
    return ast.parse(_BROWSER_PROVIDER_PATH.read_text(encoding="utf-8"))


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


def test_browser_provider_is_owned_by_capabilities_subsystem() -> None:
    assert _BROWSER_PROVIDER_PATH.is_file()
    assert _BROWSER_PROVIDER_PATH.parts[-3:] == (
        "agentx",
        "capabilities",
        "browser_provider.py",
    )


def test_browser_provider_depends_only_on_canonical_capability_abi() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx.")}
    assert agentx_imports == {"agentx.capabilities.abi"}


def test_browser_provider_has_no_cognition_hive_procedure_or_infrastructure_dependency() -> None:
    forbidden_prefixes = (
        "agentx.cognition",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.learning",
        "agentx.procedures",
    )
    assert not any(name.startswith(prefix) for name in _imports() for prefix in forbidden_prefixes)


def test_browser_provider_does_not_duplicate_kernel_or_execution_contracts() -> None:
    class_names = {node.name for node in _tree().body if isinstance(node, ast.ClassDef)}
    forbidden = {
        "ActionGate",
        "AuthorityContext",
        "BrowserCapabilityV2",
        "BrowserExecutionContext",
        "BrowserPermission",
        "BrowserRiskLevel",
        "Capability",
        "CapabilityDescriptor",
        "CapabilityRegistry",
        "ExecutionContext",
        "Permission",
        "ResourceEnvelope",
        "RiskLevel",
        "VerificationResult",
    }
    assert not class_names & forbidden


def test_browser_provider_has_no_browser_automation_or_external_io_imports() -> None:
    forbidden_roots = {
        "aiohttp",
        "asyncio",
        "http",
        "httpx",
        "netrc",
        "os",
        "pathlib",
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


def test_browser_provider_calls_no_execution_connection_or_io_surface() -> None:
    forbidden_calls = {
        "connect",
        "create_connection",
        "disconnect",
        "execute",
        "getenv",
        "launch",
        "open",
        "Popen",
        "read",
        "register",
        "socket",
        "verify",
        "write",
    }
    assert not _called_names() & forbidden_calls


def test_browser_provider_protocol_exposes_only_inert_properties() -> None:
    methods = _class_methods("BrowserProvider")
    assert methods == {"capabilities", "descriptor", "status"}


def test_browser_provider_defines_no_c5_02_target_or_session_contract() -> None:
    class_names = {node.name for node in _tree().body if isinstance(node, ast.ClassDef)}
    assert "BrowserTarget" not in class_names
    assert "BrowserTargetRef" not in class_names
    assert "BrowserSession" not in class_names
    assert "BrowserSessionRef" not in class_names


def test_browser_provider_has_no_retry_fallback_or_verification_surface() -> None:
    provider_methods = _class_methods("BrowserProvider")
    forbidden = {
        "escalate",
        "fallback",
        "reason",
        "repair",
        "retry",
        "verify",
    }
    assert not provider_methods & forbidden


def test_browser_provider_declares_no_persistence_or_background_worker() -> None:
    calls = _called_names()
    assert "Thread" not in calls
    assert "create_task" not in calls
    assert "start" not in calls
    assert "sqlite3" not in _imports()


def test_browser_provider_adds_no_runtime_dependency() -> None:
    config = tomllib.loads(_PYPROJECT_PATH.read_text(encoding="utf-8"))
    assert config["project"]["dependencies"] == []


def test_browser_provider_production_surface_is_minimal() -> None:
    classes = {node.name for node in _tree().body if isinstance(node, ast.ClassDef)}
    assert classes == {
        "BrowserProvider",
        "BrowserProviderAvailability",
        "BrowserProviderDescriptor",
        "BrowserProviderId",
        "BrowserProviderStatus",
        "BrowserProviderValidationError",
    }
