"""Architecture-boundary tests for the A2.06 Task Manager."""

from __future__ import annotations

import ast
from pathlib import Path

_TASK_MANAGER_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "agentx" / "cognition" / "task_manager.py"
)


def _tree() -> ast.Module:
    return ast.parse(_TASK_MANAGER_PATH.read_text(encoding="utf-8"))


def _imports() -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_task_manager_is_placed_in_cognition_boundary() -> None:
    assert _TASK_MANAGER_PATH.is_file()
    assert _TASK_MANAGER_PATH.parts[-3:] == ("agentx", "cognition", "task_manager.py")


def test_task_manager_depends_only_on_core_agentx_contracts() -> None:
    imports = _imports()
    agentx_imports = {name for name in imports if name.startswith("agentx.")}
    assert agentx_imports
    assert all(name.startswith("agentx.core.") for name in agentx_imports)


def test_task_manager_does_not_import_execution_subsystems() -> None:
    forbidden = (
        "agentx.capabilities",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.kernel",
        "agentx.learning",
        "agentx.procedures",
        "agentx.cognition.model_provider",
        "agentx.cognition.model_roles",
        "agentx.cognition.reasoner",
        "agentx.cognition.executor",
        "agentx.cognition.verifier",
        "agentx.cognition.router",
    )
    imports = _imports()
    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden)


def test_task_manager_has_no_network_or_process_runtime_dependency() -> None:
    forbidden_roots = {
        "aiohttp",
        "asyncio",
        "http",
        "httpx",
        "multiprocessing",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    roots = {name.split(".", maxsplit=1)[0] for name in _imports()}
    assert not roots & forbidden_roots


def test_task_manager_starts_no_background_thread() -> None:
    tree = _tree()
    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called_names.add(node.func.attr)
    assert "Thread" not in called_names
    assert "start" not in called_names
    assert "create_task" not in called_names


def test_task_state_updates_use_canonical_transition_api() -> None:
    source = _TASK_MANAGER_PATH.read_text(encoding="utf-8")
    tree = _tree()
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "try_transition_task" in calls
    assert "dataclasses.replace" not in source
    assert "object.__setattr__" not in source


def test_task_manager_exposes_no_executor_reasoner_verifier_router_methods() -> None:
    forbidden = {
        "execute",
        "verify",
        "reason",
        "route",
        "plan",
        "retry",
        "fallback",
        "dispatch",
        "invoke",
        "run_loop",
    }
    methods = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert not methods & forbidden


def test_task_manager_contains_no_persistence_or_event_publication_machinery() -> None:
    source = _TASK_MANAGER_PATH.read_text(encoding="utf-8").lower()
    forbidden_fragments = (
        "sqlite",
        "eventbus",
        "event_bus",
        "publish(",
        "emit(",
        "knowledge_store",
        "procedure_store",
        "artifact_store",
        "audit_store",
    )
    assert not any(fragment in source for fragment in forbidden_fragments)


def test_task_manager_declares_zero_runtime_dependency_installation() -> None:
    source = _TASK_MANAGER_PATH.read_text(encoding="utf-8")
    assert "pip install" not in source
    assert "importlib" not in source
    assert "entry_points" not in source


def test_task_manager_has_no_module_level_manager_instance() -> None:
    tree = _tree()
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            assert value.func.id != "TaskManager"
