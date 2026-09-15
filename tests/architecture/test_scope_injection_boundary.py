"""AX-124 source-level guard: protected retrieval never inspects content."""

from __future__ import annotations

import ast
from pathlib import Path


def test_protected_scope_matching_never_reads_record_content() -> None:
    path = Path(__file__).resolve().parents[2] / "src" / "agentx" / "hive" / "scope_retrieval.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    assert "content" not in attributes
