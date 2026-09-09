from __future__ import annotations

import ast
from pathlib import Path


def test_n2_18_uses_shared_transaction_seam_not_private_store_sql() -> None:
    source = Path("src/agentx/procedure_rollback.py").read_text(encoding="utf-8")
    assert "agentx.infrastructure.procedure_activation" in source
    assert "BEGIN IMMEDIATE" not in source
    assert ".database" not in source
    assert "sqlite3" not in source
    assert "DELETE FROM" not in source
    assert "_PROCEDURE_TABLE" not in source


def test_n2_18_import_surface_is_data_policy_and_store_only() -> None:
    tree = ast.parse(Path("src/agentx/procedure_rollback.py").read_text(encoding="utf-8"))
    modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    forbidden = (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.learning",
        "agentx.hive",
    )
    assert not any(module.startswith(forbidden) for module in modules)


def test_shared_seam_never_deletes_or_rewrites_revision_identity() -> None:
    source = Path("src/agentx/infrastructure/procedure_activation.py").read_text(encoding="utf-8")
    assert "DELETE FROM" not in source
    assert "SET revision" not in source
    assert "SET procedure_id" not in source
    assert "BEGIN IMMEDIATE" not in source
    assert "_write_transaction" in source
