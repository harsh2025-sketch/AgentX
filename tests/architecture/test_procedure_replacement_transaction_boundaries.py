from __future__ import annotations

import ast
from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_n2_17_delegates_raw_persistence_to_single_infrastructure_seam() -> None:
    source = _source("src/agentx/procedure_replacement_transaction.py")
    assert "agentx.infrastructure.procedure_activation" in source
    assert "BEGIN IMMEDIATE" not in source
    assert ".database" not in source
    assert "sqlite3" not in source
    assert "DELETE FROM" not in source


def test_shared_activation_seam_is_the_only_b_transaction_raw_sql_owner() -> None:
    source = _source("src/agentx/infrastructure/procedure_activation.py")
    assert "_write_transaction" in source
    assert "DELETE FROM" not in source
    assert "ProcedureStatus.RETIRED" in source
    assert "ProcedureStatus.ACTIVE" in source
    tree = ast.parse(source)
    imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(module.startswith("agentx.kernel") for module in imports)
