"""Architecture tests for the canonical Task state machine placement (A1.06).

The Task lifecycle contract must live in the inward ``agentx.core`` boundary,
must reuse the A1.05 ``TaskStatus`` vocabulary instead of restating it, and must
not reach outward for authority (``agentx.kernel``) or plumbing
(``agentx.infrastructure``) merely to express lifecycle rules.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx.core.task_state import can_transition, transition_task
from agentx.core.tasks import TaskStatus

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTX_SRC = _REPO_ROOT / "src" / "agentx"


def _module_paths() -> list[Path]:
    return sorted(path for path in _AGENTX_SRC.rglob("*.py") if path.is_file())


def _defined_classes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}


def _defined_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def test_task_status_vocabulary_is_defined_once() -> None:
    """A1.06 reuses the A1.05 vocabulary; it never re-declares it."""
    definitions = [
        path.relative_to(_REPO_ROOT / "src")
        for path in _module_paths()
        if "TaskStatus" in _defined_classes(path)
    ]

    assert definitions == [Path("agentx/core/tasks.py")]
    assert TaskStatus.__module__ == "agentx.core.tasks"


def test_transition_contract_is_defined_once() -> None:
    """The transition matrix and its helpers exist only in ``agentx.core.task_state``."""
    owners = [
        path.relative_to(_REPO_ROOT / "src")
        for path in _module_paths()
        if {"can_transition", "transition_task"} <= _defined_functions(path)
    ]

    assert owners == [Path("agentx/core/task_state.py")]
    assert can_transition.__module__ == "agentx.core.task_state"
    assert transition_task.__module__ == "agentx.core.task_state"


def test_no_parallel_task_manager_or_executor_surface_in_core() -> None:
    """A1.06 must not smuggle Day 2 / A1.07 subsystems into the core contract.

    Scoped to ``agentx.core``: ``agentx.infrastructure.event_bus`` legitimately
    owns the canonical EventBus (C1.03), so a repository-wide scan would be the
    wrong guard here. What must hold is that the inward domain layer gains no
    manager, store, executor, scheduler, or cancellation surface.
    """
    forbidden = {
        "TaskManager",
        "TaskStore",
        "EventBus",
        "EventJournal",
        "Executor",
        "Planner",
        "Scheduler",
        "RetryEngine",
        "CancellationToken",
        "PermissionEngine",
        "ActionGate",
        "TaskStateMachine",
    }
    for path in sorted((_AGENTX_SRC / "core").rglob("*.py")):
        assert forbidden.isdisjoint(_defined_classes(path)), (
            f"{path} defines forbidden runtime type"
        )


def test_state_machine_module_imports_no_outer_subsystem() -> None:
    """Lifecycle vocabulary needs neither authority nor infrastructure."""
    module_path = _AGENTX_SRC / "core" / "task_state.py"
    imported = _imported_modules(module_path)

    assert imported
    for module in imported:
        assert (
            module == "__future__"
            or module.startswith("agentx.core.")
            or not module.startswith("agentx.")
        ), f"agentx.core.task_state must not import {module}"


def test_core_remains_a_dependency_leaf() -> None:
    """No module under ``agentx.core`` imports another canonical subsystem."""
    for path in sorted((_AGENTX_SRC / "core").rglob("*.py")):
        for module in _imported_modules(path):
            if not module.startswith("agentx."):
                continue
            assert module == "agentx.core" or module.startswith("agentx.core."), (
                f"{path.name} imports {module}"
            )
