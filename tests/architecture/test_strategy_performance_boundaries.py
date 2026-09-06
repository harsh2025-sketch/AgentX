"""Architecture guards for the A8.01 strategy-performance contract and store.

Mirrors the C2.01/C2.02 placement guards: the canonical measured-fact contract
lives inward in ``agentx.core``, the concrete append-only storage lives
outward in ``agentx.infrastructure``, the level vocabulary stays pinned to the
A2.07 Router's canonical execution levels, and nothing in the feature touches
authority, routing, caches, or adaptive/optimization subsystems.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_CORE_CONTRACT = _AGENTX_SRC / "core" / "strategy_performance.py"
_STORE = _AGENTX_SRC / "infrastructure" / "strategy_performance_store.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in _AGENTX_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree)):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


def test_strategy_performance_record_has_exactly_one_canonical_definition() -> None:
    assert _class_definitions("StrategyPerformanceRecord") == [
        Path("agentx/core/strategy_performance.py")
    ]


def test_strategy_performance_store_has_exactly_one_canonical_definition() -> None:
    assert _class_definitions("StrategyPerformanceStore") == [
        Path("agentx/infrastructure/strategy_performance_store.py")
    ]


def test_strategy_performance_id_has_exactly_one_definition() -> None:
    assert _class_definitions("StrategyPerformanceId") == [Path("agentx/core/ids.py")]


def test_core_contract_imports_only_core_and_stdlib() -> None:
    imports = _imports(_CORE_CONTRACT)

    assert all(
        not module.startswith("agentx.") or module.startswith("agentx.core") for module in imports
    )
    assert all(not module.startswith("agentx.cognition") for module in imports)
    assert all(not module.startswith("agentx.kernel") for module in imports)
    assert all(not module.startswith("agentx.infrastructure") for module in imports)
    assert all(not module.startswith("agentx.capabilities") for module in imports)


def test_store_uses_only_core_and_canonical_persistence_foundation() -> None:
    imports = _imports(_STORE)

    allowed = {
        "agentx.core.causal_experience",
        "agentx.core.ids",
        "agentx.core.strategy_performance",
        "agentx.infrastructure.persistence",
    }
    assert all(not module.startswith("agentx.") or module in allowed for module in imports)
    assert all(not module.startswith("agentx.hive") for module in imports)
    assert all(not module.startswith("agentx.kernel") for module in imports)
    assert all(not module.startswith("agentx.cognition") for module in imports)
    assert all(not module.startswith("agentx.learning") for module in imports)
    assert "agentx.infrastructure.event_journal" not in imports
    assert "agentx.infrastructure.event_bus" not in imports
    assert "agentx.infrastructure.artifact_store" not in imports
    assert "agentx.infrastructure.audit_store" not in imports


def test_strategy_performance_code_has_no_dynamic_or_executable_hooks() -> None:
    forbidden_imports = {"pickle", "importlib", "subprocess"}
    forbidden_calls = {"eval", "exec", "__import__"}

    for path in (_CORE_CONTRACT, _STORE):
        tree = _tree(path)
        imports = set(_imports(path))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert imports.isdisjoint(forbidden_imports)
        assert calls.isdisjoint(forbidden_calls)


def test_strategy_performance_code_has_no_authority_or_adaptive_surface() -> None:
    forbidden_definitions = {
        "Router",
        "ActionGate",
        "Bandit",
        "Cache",
        "TaskManager",
        "Verifier",
        "Capability",
        "AuthorityContext",
    }
    for path in (_CORE_CONTRACT, _STORE):
        defined = {node.name for node in ast.walk(_tree(path)) if isinstance(node, ast.ClassDef)}
        assert defined.isdisjoint(forbidden_definitions)


def _enum_members(path: Path, class_name: str) -> dict[str, str]:
    """Return ``{member_name: value}`` declared inside a StrEnum class body."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            members: dict[str, str] = {}
            for statement in node.body:
                if (
                    isinstance(statement, ast.Assign)
                    and isinstance(statement.targets[0], ast.Name)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                ):
                    members[statement.targets[0].id] = statement.value.value
            return members
    raise AssertionError(f"class {class_name} not found in {path}")


def test_level_vocabulary_is_pinned_to_the_a207_router() -> None:
    """The A8.01 identity vocabulary cannot drift from A2.07's canonical levels."""
    router = _AGENTX_SRC / "cognition" / "router.py"
    router_levels = _enum_members(router, "ExecutionLevel")
    contract_levels = _enum_members(_CORE_CONTRACT, "ExecutionStrategyLevel")

    assert tuple(contract_levels.items()) == tuple(router_levels.items())
    assert tuple(contract_levels) == (
        "L0_CACHE",
        "L1_DIRECT",
        "L2_COMPILED",
        "L3_GUIDED",
        "L4_PLANNED",
        "L5_EXPLORATORY",
    )
    # Canonical string values equal their member names (L0_CACHE == "L0_CACHE").
    assert all(name == value for name, value in contract_levels.items())


def test_schema_migration_is_registered_as_next_append_only_migration() -> None:
    # Identified by name, not by ladder position: later append-only tasks
    # (C7.07 owns v10, create_event_watcher_state) may extend the ladder above
    # this entry without changing its ownership.
    migration = next(
        entry for entry in _MIGRATIONS if entry.name == "create_strategy_performance_store"
    )
    assert migration.version == 9
    # The migration itself is strictly additive: it creates one new table and
    # its indexes and never rewrites historical schema or data.
    combined = " ".join(statement.upper() for statement in migration.statements)
    assert "CREATE TABLE" in combined
    for forbidden in ("ALTER TABLE", "DROP ", "DELETE FROM", "UPDATE "):
        assert forbidden not in combined


def test_migration_creates_only_the_strategy_performance_table() -> None:
    migration = next(
        entry for entry in _MIGRATIONS if entry.name == "create_strategy_performance_store"
    )
    statements = " ".join(migration.statements)
    assert "CREATE TABLE agentx_strategy_performance" in statements
    assert "CREATE INDEX agentx_strategy_performance_" in statements
    assert migration.statements[0].count("CREATE TABLE") == 1


def test_no_other_module_defines_the_store_table() -> None:
    for path in _AGENTX_SRC.rglob("*.py"):
        relative = path.relative_to(_SRC_ROOT)
        if relative == Path("agentx/infrastructure/persistence.py"):
            continue
        if relative == Path("agentx/infrastructure/strategy_performance_store.py"):
            continue
        if "agentx_strategy_performance" in path.read_text(encoding="utf-8"):
            raise AssertionError(f"unexpected strategy-performance table reference in {path}")
