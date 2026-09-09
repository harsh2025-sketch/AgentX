from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

from agentx.procedure_rollback import ProcedureRollbackResult


def test_rollback_module_has_no_kernel_capability_or_model_authority() -> None:
    source = Path("src/agentx/procedure_rollback.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(module.startswith("agentx.kernel") for module in imports)
    assert not any(module.startswith("agentx.capabilities") for module in imports)
    assert not any(module.startswith("agentx.cognition") for module in imports)
    assert "ActionGate" not in source
    assert "EmergencyStop" not in source
    assert "Permission" not in source
    assert "TaskStatus" not in source
    assert "model" not in source.lower()


def test_rollback_result_cannot_encode_verified_task_success() -> None:
    names = {field.name for field in fields(ProcedureRollbackResult)}
    assert "verified" not in names
    assert "task_status" not in names
    assert "permission" not in names
    assert "risk" not in names


def test_hostile_metadata_is_never_parsed_as_authority() -> None:
    source = Path("src/agentx/procedure_rollback.py").read_text(encoding="utf-8")
    for token in ("permission=ADMIN", "risk=R0", "verified=true"):
        assert token not in source
