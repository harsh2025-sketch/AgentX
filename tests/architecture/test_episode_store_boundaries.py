"""Architecture guards for the C2.01 episode contract and persistence adapter."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE_EPISODES = _REPO_ROOT / "src" / "agentx" / "core" / "episodes.py"
_EPISODE_STORE = _REPO_ROOT / "src" / "agentx" / "infrastructure" / "episode_store.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(imported)


def test_episode_domain_contract_stays_inside_core_boundary() -> None:
    imports = _imports(_CORE_EPISODES)

    assert all(
        not module.startswith("agentx.") or module == "agentx.core.ids" for module in imports
    )


def test_episode_store_uses_only_core_and_canonical_persistence_foundation() -> None:
    imports = _imports(_EPISODE_STORE)

    assert all(
        not module.startswith("agentx.")
        or module
        in {
            "agentx.core.episodes",
            "agentx.core.ids",
            "agentx.infrastructure.persistence",
        }
        for module in imports
    )
    assert "agentx.infrastructure.event_journal" not in imports
    assert "agentx.infrastructure.event_bus" not in imports
    assert all(not module.startswith("agentx.hive") for module in imports)
    assert all(not module.startswith("agentx.kernel") for module in imports)


def test_episode_code_has_no_dynamic_deserialization_or_execution_hooks() -> None:
    forbidden_imports = {"pickle", "importlib"}
    forbidden_calls = {"eval", "exec", "__import__"}

    for path in (_CORE_EPISODES, _EPISODE_STORE):
        tree = _tree(path)
        imports = set(_imports(path))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        assert imports.isdisjoint(forbidden_imports)
        assert calls.isdisjoint(forbidden_calls)


def test_episode_store_does_not_define_forbidden_hive_stores() -> None:
    forbidden = {
        "KnowledgeStore",
        "ProcedureStore",
        "ArtifactStore",
        "AuditStore",
        "TaskStore",
    }
    defined = {
        node.name for node in ast.walk(_tree(_EPISODE_STORE)) if isinstance(node, ast.ClassDef)
    }

    assert defined.isdisjoint(forbidden)
