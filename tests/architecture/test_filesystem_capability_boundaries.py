from __future__ import annotations

import ast
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[2] / "src" / "agentx" / "capabilities" / "filesystem.py"
_FORBIDDEN_AGENTX_PREFIXES = (
    "agentx.cognition",
    "agentx.hive",
    "agentx.infrastructure",
    "agentx.procedures",
    "agentx.agent_loop",
)
_FORBIDDEN_MODULES = frozenset({"subprocess", "glob", "shutil"})
_FORBIDDEN_CALL_NAMES = frozenset(
    {
        "eval",
        "exec",
        "system",
        "popen",
        "Popen",
        "run",
        "call",
        "check_call",
        "check_output",
        "mkdir",
        "unlink",
        "rmdir",
        "rename",
        "replace",
        "chmod",
        "lchmod",
        "glob",
        "rglob",
        "walk",
    }
)


def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"), filename=str(_SOURCE))


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def _called_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_filesystem_capability_respects_architecture_dependency_boundary() -> None:
    modules = _imported_modules(_tree())

    for module in modules:
        assert not module.startswith(_FORBIDDEN_AGENTX_PREFIXES), module


def test_filesystem_capability_does_not_import_shell_or_general_fs_toolkits() -> None:
    modules = _imported_modules(_tree())

    assert modules.isdisjoint(_FORBIDDEN_MODULES)
    assert "os" not in modules


def test_filesystem_capability_has_no_forbidden_dynamic_or_shell_calls() -> None:
    called = _called_names(_tree())

    assert called.isdisjoint(_FORBIDDEN_CALL_NAMES)


def test_filesystem_capability_does_not_define_general_filesystem_operations() -> None:
    tree = _tree()
    public_functions = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }

    assert public_functions == {"read_text_request", "write_text_request"}
