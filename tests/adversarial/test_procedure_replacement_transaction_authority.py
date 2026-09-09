from __future__ import annotations

import ast
from pathlib import Path

from agentx.procedure_replacement_transaction import ReplacementTransactionResult


def test_forward_transaction_module_has_no_kernel_or_execution_authority() -> None:
    source = Path("src/agentx/procedure_replacement_transaction.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any(module.startswith("agentx.kernel") for module in imports)
    assert "Capability" not in source
    assert "ActionGate" not in source
    assert "EmergencyStop" not in source
    assert "TaskStatus" not in source
    assert "execute(" not in source


def test_result_is_lifecycle_data_not_success_authority() -> None:
    assert set(ReplacementTransactionResult.__dataclass_fields__) == {
        "status",
        "procedure_id",
        "previous_revision",
        "replacement_revision",
        "reason",
    }
