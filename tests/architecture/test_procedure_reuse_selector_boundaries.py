from __future__ import annotations

import ast
from pathlib import Path

_SOURCE = Path("src/agentx/procedure_reuse_selector.py").read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)


def test_selector_is_in_its_owned_module_and_has_no_effectful_imports() -> None:
    imports = {node.module or "" for node in ast.walk(_TREE) if isinstance(node, ast.ImportFrom)}
    assert imports <= {
        "__future__",
        "collections.abc",
        "dataclasses",
        "enum",
        "typing",
        "agentx.core.ids",
        "agentx.core.procedure_matching",
        "agentx.core.procedures",
    }
    assert "ProcedureStore" not in _SOURCE
    assert "agentx.infrastructure" not in _SOURCE
    assert "agentx.kernel" not in _SOURCE


def test_selector_has_no_execution_or_mutation_surface() -> None:
    called = {
        node.func.id
        for node in ast.walk(_TREE)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"open", "exec", "eval", "random", "execute", "update_status"}
    assert "ProcedureApplicabilityMatcher" in _SOURCE
    assert "ProcedureMatchOutcome" in _SOURCE
