"""Architecture tests for the C7.07 event-watcher framework placement.

C7.07 is generic non-domain plumbing over canonical events, so it lives in
``agentx.infrastructure`` next to the EventBus and EventJournal it composes:

* the framework reads the journal (never around it) and never publishes or
  appends on its own;
* it imports nothing from the Trusted Kernel, capabilities, cognition, hive,
  procedures, or learning — a watcher match is data, never authority;
* filters are declarative data: no callables, no predicates, no code accepted
  from untrusted data;
* there is no scheduler: no threads, timers, asyncio, signals, or polling —
  every operation is a bounded synchronous call;
* exactly one new persistence migration (``create_event_watcher_state``) is
  appended, and migrations v1-v8 remain untouched.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from agentx.infrastructure.event_watcher import (
    EventFilter,
    EventWatcher,
    WatcherMatch,
)
from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_WATCHER_MODULE = _AGENTX_SRC / "infrastructure" / "event_watcher.py"
_INFRASTRUCTURE_PACKAGE = _AGENTX_SRC / "infrastructure"

_EXPECTED_CLASSES = {
    "CorruptWatcherStateError",
    "DuplicateWatcherIdError",
    "EventFilter",
    "EventWatcher",
    "EventWatcherConflictError",
    "EventWatcherError",
    "EventWatcherService",
    "EventWatcherStorageError",
    "EventWatcherStore",
    "EventWatcherStoreError",
    "EventWatcherValidationError",
    "MatchOrigin",
    "UnknownWatcherIdError",
    "WatcherId",
    "WatcherMatch",
}

_EXPECTED_SERVICE_METHODS = {
    "create_watcher",
    "get",
    "list_watchers",
    "enable",
    "disable",
    "cancel",
    "observe_live",
    "live_handler",
    "process_pending",
    "recent_matches",
}


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in _AGENTX_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree)):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


# ---------------------------------------------------------------------------
# Single canonical placement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "class_name",
    sorted(_EXPECTED_CLASSES),
)
def test_watcher_framework_has_exactly_one_canonical_definition(class_name: str) -> None:
    assert _class_definitions(class_name) == [Path("agentx/infrastructure/event_watcher.py")]


def test_watcher_module_lives_in_the_infrastructure_package() -> None:
    infrastructure_modules = sorted(path.name for path in _INFRASTRUCTURE_PACKAGE.glob("*.py"))
    assert "event_watcher.py" in infrastructure_modules
    assert "__init__.py" in infrastructure_modules


# ---------------------------------------------------------------------------
# Dependency boundaries
# ---------------------------------------------------------------------------


def test_watcher_module_depends_only_on_events_bus_journal_and_persistence() -> None:
    imported = _imported_modules(_WATCHER_MODULE)
    agentx_imports = {module for module in imported if module.startswith("agentx.")}
    assert agentx_imports == {
        "agentx.core.events",
        "agentx.infrastructure.event_bus",
        "agentx.infrastructure.event_journal",
        "agentx.infrastructure.persistence",
    }


def test_watcher_module_avoids_kernel_and_domain_subsystems() -> None:
    imported = _imported_modules(_WATCHER_MODULE)
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.hive",
        "agentx.procedures",
        "agentx.learning",
    )
    violations = [module for module in imported if module.startswith(forbidden_prefixes)]
    assert violations == []


def test_watcher_module_uses_no_third_party_runtime_dependencies() -> None:
    imported = _imported_modules(_WATCHER_MODULE)
    non_stdlib = [
        module
        for module in imported
        if module.split(".")[0] not in sys.stdlib_module_names and not module.startswith("agentx.")
    ]
    assert non_stdlib == []


# ---------------------------------------------------------------------------
# No scheduler, no threads, no dynamic execution
# ---------------------------------------------------------------------------


def test_watcher_module_spawns_no_threads_timers_or_loops() -> None:
    imported = _imported_modules(_WATCHER_MODULE)
    assert {"asyncio", "sched", "signal", "subprocess", "multiprocessing"}.isdisjoint(imported)

    names = _imported_names(_WATCHER_MODULE)
    assert {"Thread", "Timer", "ThreadPoolExecutor", "Process"}.isdisjoint(names)

    tree = ast.parse(_WATCHER_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "threading":
            imported_names = {alias.name for alias in node.names}
            # A plain lock for concurrent-publisher safety is the only
            # threading primitive the framework is allowed to use.
            assert imported_names <= {"Lock"}


def test_watcher_module_performs_no_dynamic_execution() -> None:
    tree = ast.parse(_WATCHER_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name) and function.id in {"eval", "exec", "compile"}:
                raise AssertionError(f"dynamic execution call: {function.id}")
            if isinstance(function, ast.Name) and function.id == "__import__":
                raise AssertionError("__import__ call inside the watcher framework")
            if isinstance(function, ast.Attribute) and function.attr == "system":
                raise AssertionError("os.system-style call inside the watcher framework")
        if isinstance(node, ast.Import) and any(alias.name == "importlib" for alias in node.names):
            raise AssertionError("importlib has no place in the watcher framework")
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and (node.module == "importlib" or node.module.startswith("importlib."))
        ):
            raise AssertionError("importlib has no place in the watcher framework")
    assert "pickle" not in _imported_modules(_WATCHER_MODULE)


def test_service_never_publishes_subscribes_or_appends_on_its_own() -> None:
    """The framework is a read-only observer: composition owns the wiring."""
    tree = ast.parse(_WATCHER_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"publish", "subscribe"}:
            raise AssertionError("the watcher service must not publish or subscribe")
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "append"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "_journal"
        ):
            raise AssertionError("the watcher service must not append to the journal")


# ---------------------------------------------------------------------------
# Declarative filters: no callables, no authority fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("record_type", [EventFilter, EventWatcher, WatcherMatch])
def test_filter_and_match_records_accept_no_callables(record_type: type[object]) -> None:
    for field_descriptor in fields(record_type):  # type: ignore[arg-type]
        annotation = str(field_descriptor.type)
        assert "Callable" not in annotation, field_descriptor.name
        assert "callable" not in annotation, field_descriptor.name


def test_filter_field_set_is_exactly_the_declarative_dimensions() -> None:
    assert {field_descriptor.name for field_descriptor in fields(EventFilter)} == {
        "event_types",
        "categories",
        "sources",
        "task_ids",
        "correlation_ids",
        "metadata",
    }


def test_watcher_state_field_set_has_no_authority_surface() -> None:
    assert {field_descriptor.name for field_descriptor in fields(EventWatcher)} == {
        "watcher_id",
        "name",
        "filter",
        "created_at",
        "enabled",
        "cancelled",
        "checkpoint",
        "last_processed_event_id",
    }


def test_match_record_field_set_has_no_authority_surface() -> None:
    assert {field_descriptor.name for field_descriptor in fields(WatcherMatch)} == {
        "watcher_id",
        "event_id",
        "event_type",
        "event_timestamp",
        "origin",
        "observed_at",
        "sequence",
    }


def test_watcher_module_declares_no_scheduler_or_objective_api() -> None:
    tree = ast.parse(_WATCHER_MODULE.read_text(encoding="utf-8"))
    public_classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }
    assert public_classes == _EXPECTED_CLASSES

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "EventWatcherService":
            methods = {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not item.name.startswith("_")
            }
            assert methods == _EXPECTED_SERVICE_METHODS
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef):
                    raise AssertionError("the watcher service must be fully synchronous")


# ---------------------------------------------------------------------------
# Persistence: exactly one appended migration, v1-v8 immutable
# ---------------------------------------------------------------------------


def test_c707_appends_exactly_one_watcher_state_migration() -> None:
    versions = tuple(migration.version for migration in _MIGRATIONS)
    names = tuple(migration.name for migration in _MIGRATIONS)

    assert versions == tuple(range(1, len(_MIGRATIONS) + 1))
    assert names.count("create_event_watcher_state") == 1

    # Identified by name, not by ladder position (later append-only tasks may
    # extend the ladder above this entry): A8.01 owns v9, C7.07 owns v10.
    watcher_migration = next(
        migration for migration in _MIGRATIONS if migration.name == "create_event_watcher_state"
    )
    assert watcher_migration.version == 10
    joined = "\n".join(watcher_migration.statements)
    assert "agentx_event_watchers" in joined


def test_migrations_v1_through_v9_remain_immutable() -> None:
    names = tuple(migration.name for migration in _MIGRATIONS)
    assert names[:9] == (
        "create_persistence_metadata",
        "create_event_journal",
        "create_knowledge_store",
        "create_episode_store",
        "create_procedure_store",
        "create_artifact_and_audit_stores",
        "create_knowledge_integrity",
        "create_negative_experience_store",
        "create_strategy_performance_store",
    )
