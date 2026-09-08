"""Architecture proofs for the M1.03 top-level episode capture boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import agentx.execution_episode as execution_episode_module

_PACKAGE_ROOT = Path(execution_episode_module.__file__).resolve().parent
_SOURCE_PATH = _PACKAGE_ROOT / "execution_episode.py"
_SOURCE = _SOURCE_PATH.read_text(encoding="utf-8")


def _class_definitions(name: str) -> list[Path]:
    matches: list[Path] = []
    for path in _PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree)):
            matches.append(path.relative_to(_PACKAGE_ROOT))
    return sorted(matches)


def _imports() -> set[str]:
    tree = ast.parse(_SOURCE)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def test_episode_record_remains_the_unique_canonical_episode_schema() -> None:
    assert _class_definitions("EpisodeRecord") == [Path("core/episodes.py")]


def test_episode_store_remains_the_unique_episode_persistence_owner() -> None:
    assert _class_definitions("EpisodeStore") == [Path("infrastructure/episode_store.py")]
    assert "EpisodeStore" not in _SOURCE
    assert "record_episode" in _SOURCE
    assert "ExperienceMemory" in _SOURCE


def test_capture_boundary_adds_no_database_or_migration_implementation() -> None:
    forbidden = (
        "sqlite3",
        "SQLiteDatabase",
        "_Migration",
        "CREATE TABLE",
        "INSERT INTO",
        "agentx_schema_migrations",
    )
    for fragment in forbidden:
        assert fragment not in _SOURCE

    persistence = (_PACKAGE_ROOT / "infrastructure" / "persistence.py").read_text(encoding="utf-8")
    assert "execution_episode" not in persistence


def test_agent_loop_is_not_hooked_to_episode_persistence() -> None:
    agent_loop = (_PACKAGE_ROOT / "agent_loop.py").read_text(encoding="utf-8")
    assert "execution_episode" not in agent_loop
    assert "agentx.agent_loop" not in _imports()


def test_boundary_cannot_execute_capabilities_or_mutate_kernel_authority() -> None:
    forbidden = (
        "CapabilityExecutionLoop",
        "CapabilityRegistry",
        "ActionGate",
        "PermissionEngine",
        "EmergencyStop",
        "ResourceBudget",
        "RiskLevel",
        ".execute(",
    )
    for fragment in forbidden:
        assert fragment not in _SOURCE
    assert all(not module.startswith("agentx.kernel") for module in _imports())


def test_boundary_imports_no_cognition_subsystem() -> None:
    assert all(not module.startswith("agentx.cognition") for module in _imports())


def test_capture_file_is_a_top_level_composition_adapter() -> None:
    assert _SOURCE_PATH.parent == _PACKAGE_ROOT
    assert _SOURCE_PATH.name == "execution_episode.py"
