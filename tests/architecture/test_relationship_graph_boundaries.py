"""Architecture guardrails for the M6.02 Hive relationship graph.

Import/structure guardrails, not security enforcement. Authority is owned
exclusively by the Trusted Kernel; lifecycle transitions by C2.08 through the
canonical store; persistence by infrastructure. The graph is pure in-memory
data over canonical ``KnowledgeId`` identities.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTX_SRC = _REPO_ROOT / "src" / "agentx"
_GRAPH = _AGENTX_SRC / "hive" / "relationship_graph.py"


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


def _enum_members(tree: ast.Module, class_name: str) -> set[str]:
    members: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    members.update(
                        target.id for target in statement.targets if isinstance(target, ast.Name)
                    )
    return members


def test_relationship_graph_lives_inside_the_hive_boundary() -> None:
    assert _GRAPH.is_file()
    assert _GRAPH.parent.name == "hive"
    assert _GRAPH.parent.parent.name == "agentx"


def test_relationship_graph_uses_only_the_canonical_hive_core_edge() -> None:
    imports = _imports(_GRAPH)
    agentx_imports = [module for module in imports if module.startswith("agentx.")]
    assert agentx_imports, "graph must consume canonical core identity/provenance contracts"
    assert all(module.startswith("agentx.core.") for module in agentx_imports), agentx_imports
    for module in imports:
        for subsystem in _architecture.SUBSYSTEMS:
            if subsystem == _architecture.CORE:
                continue
            assert not module.startswith(f"{subsystem}."), (
                f"relationship graph must not import {subsystem} (found {module})"
            )
    assert (_architecture.HIVE, _architecture.CORE) in _architecture.ALLOWED_ARCHITECTURE_EDGES


def test_relationship_graph_never_depends_on_models_persistence_or_io() -> None:
    imports = _imports(_GRAPH)
    for forbidden in (
        "agentx.cognition.model_provider",
        "agentx.cognition.model_roles",
        "agentx.cognition.reasoner",
        "agentx.infrastructure.persistence",
        "agentx.infrastructure.knowledge_store",
        "agentx.kernel",
        "sqlite3",
        "socket",
        "urllib.request",
        "http.client",
        "subprocess",
        "os",
        "sys",
        "pathlib",
        "pickle",
        "shelve",
        "json",
    ):
        assert forbidden not in imports, forbidden


def test_relationship_graph_is_free_of_io_and_dynamic_execution() -> None:
    forbidden_calls = {
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
        "system",
        "popen",
        "remove",
        "unlink",
        "rmtree",
        "print",
        "input",
        "connect",
        "execute",
        "commit",
    }
    for node in ast.walk(_tree(_GRAPH)):
        if isinstance(node, ast.Call):
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else (target.attr if isinstance(target, ast.Attribute) else "")
            )
            assert name not in forbidden_calls, f"relationship graph must not call {name!r}"


def test_relationship_vocabulary_is_closed_and_exact() -> None:
    members = _enum_members(_tree(_GRAPH), "RelationshipKind")
    assert members == {"SUPPORTS", "CONTRADICTS", "SUPERSEDES", "DERIVED_FROM", "RELATED_TO"}


def test_graph_defines_no_node_id_type() -> None:
    """Nodes are canonical KnowledgeId values; no duplicate identity type exists."""
    tree = _tree(_GRAPH)
    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert not any("NodeId" in name or name == "Node" for name in class_names), class_names
    assert "agentx.core.ids" in _imports(_GRAPH)


def test_graph_has_no_remove_delete_or_status_methods() -> None:
    tree = _tree(_GRAPH)
    method_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "RelationshipGraph":
            method_names = {
                statement.name for statement in node.body if isinstance(statement, ast.FunctionDef)
            }
    assert method_names
    for forbidden in ("remove", "delete", "clear", "purge", "update_status", "resolve", "merge"):
        assert forbidden not in method_names, forbidden
    assert {"add", "edges_from", "edges_to", "relationships_between", "neighbors"} <= method_names


def test_public_surface_is_small() -> None:
    tree = _tree(_GRAPH)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            assert isinstance(node.value, ast.List)
            names = {ast.literal_eval(element) for element in node.value.elts}
            break
    else:  # pragma: no cover - guard
        raise AssertionError("__all__ missing")
    assert names == {
        "DEFAULT_RELATIONSHIP_GRAPH_LIMITS",
        "RelationshipDirection",
        "RelationshipEdge",
        "RelationshipGraph",
        "RelationshipGraphLimitError",
        "RelationshipGraphLimits",
        "RelationshipKind",
        "RelationshipQuery",
        "RelationshipResult",
        "RelationshipValidationError",
    }


def test_architecture_manifest_and_hive_init_are_untouched_by_this_task() -> None:
    manifest = (_AGENTX_SRC / "_architecture.py").read_text(encoding="utf-8")
    hive_init = (_AGENTX_SRC / "hive" / "__init__.py").read_text(encoding="utf-8")
    assert "relationship_graph" not in manifest
    assert "relationship_graph" not in hive_init
