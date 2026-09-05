"""Architecture-boundary tests for the A2.07 execution-level Router."""

from __future__ import annotations

import ast
from pathlib import Path

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "src" / "agentx" / "cognition" / "router.py"


def _tree() -> ast.Module:
    return ast.parse(_ROUTER_PATH.read_text(encoding="utf-8"))


def _imports() -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


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


def test_router_is_placed_in_cognition_boundary() -> None:
    assert _ROUTER_PATH.is_file()
    assert _ROUTER_PATH.parts[-3:] == ("agentx", "cognition", "router.py")


def test_router_has_no_agentx_subsystem_dependencies() -> None:
    assert not {name for name in _imports() if name.startswith("agentx.")}


def test_router_has_no_model_hive_store_or_execution_imports() -> None:
    forbidden_prefixes = (
        "agentx.capabilities",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.kernel",
        "agentx.learning",
        "agentx.procedures",
        "agentx.cognition.model_provider",
        "agentx.cognition.model_roles",
        "agentx.cognition.reasoner",
        "agentx.cognition.task_manager",
    )
    assert not any(name.startswith(prefix) for name in _imports() for prefix in forbidden_prefixes)


def test_router_has_no_network_filesystem_process_or_async_runtime_dependency() -> None:
    forbidden_roots = {
        "aiohttp",
        "asyncio",
        "http",
        "httpx",
        "multiprocessing",
        "os",
        "pathlib",
        "requests",
        "socket",
        "sqlite3",
        "subprocess",
        "urllib",
    }
    roots = {name.split(".", maxsplit=1)[0] for name in _imports()}
    assert not roots & forbidden_roots


def test_router_calls_no_execution_or_external_io_surface() -> None:
    forbidden_calls = {
        "activate",
        "execute",
        "get",
        "invoke",
        "open",
        "publish",
        "put",
        "read",
        "request",
        "transition",
        "verify",
        "write",
    }
    assert not _called_names() & forbidden_calls


def test_router_exposes_no_retry_fallback_escalation_or_loop_methods() -> None:
    forbidden = {
        "escalate",
        "fallback",
        "plan",
        "reason",
        "research",
        "retry",
        "run",
        "run_loop",
    }
    methods = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert not methods & forbidden


def test_router_starts_no_background_worker() -> None:
    calls = _called_names()
    assert "Thread" not in calls
    assert "create_task" not in calls
    assert "start" not in calls


def test_router_has_no_module_level_router_singleton() -> None:
    for node in _tree().body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            assert value.func.id != "ExecutionLevelRouter"


def test_router_declares_no_runtime_dependency_installation() -> None:
    source = _ROUTER_PATH.read_text(encoding="utf-8")
    assert "pip install" not in source
    assert "importlib" not in source
    assert "entry_points" not in source


def test_router_production_surface_is_contract_only() -> None:
    classes = {node.name for node in _tree().body if isinstance(node, ast.ClassDef)}
    assert classes == {
        "ExecutionLevel",
        "ExecutionLevelRouter",
        "RoutingDecision",
        "RoutingEvidence",
    }
