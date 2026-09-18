"""Architecture guard for the M3 compiler/runtime integration seam."""

from __future__ import annotations

import ast
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "agentx"
_BINDING = _SRC_ROOT / "compiled_skill_binding.py"
_VALIDATION = _SRC_ROOT / "compiled_skill_validation.py"

_FORBIDDEN_DYNAMIC_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_compiled_skill_binding_has_no_dynamic_execution_or_authority_imports() -> None:
    tree = _tree(_BINDING)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not (_FORBIDDEN_DYNAMIC_CALLS & called)

    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(
        module.startswith(
            (
                "agentx.kernel",
                "agentx.infrastructure",
                "agentx.procedure_promotion",
            )
        )
        for module in imported
    )
    assert not any(
        module in {"pickle", "importlib", "subprocess"}
        for module in imported
    )


def test_candidate_validation_composes_executor_but_cannot_promote_or_persist() -> None:
    tree = _tree(_VALIDATION)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "agentx.capabilities.executor" in imported
    assert "agentx.procedures.interpreter" in imported
    assert not any(
        module.startswith(
            (
                "agentx.kernel",
                "agentx.infrastructure",
                "agentx.procedure_promotion",
            )
        )
        for module in imported
    )

    source = _VALIDATION.read_text(encoding="utf-8")
    for forbidden in (
        "update_status(",
        "promote_procedure_candidate(",
        "ProcedureStore(",
        "eval(",
        "exec(",
        "__import__(",
        "subprocess.",
    ):
        assert forbidden not in source
