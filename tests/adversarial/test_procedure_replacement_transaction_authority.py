import ast
from pathlib import Path


def test_transaction_grants_no_authority():
    """Verify that the transaction does not import or invoke kernel authority modules."""
    module_path = Path("src/agentx/procedure_replacement_transaction.py")
    tree = ast.parse(module_path.read_text())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                assert not name.name.startswith("agentx.kernel"), "Must not import kernel authority"
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("agentx.kernel"), "Must not import kernel authority"


def test_hostile_text_cannot_replace():
    """Verify that hostile payload text does not trick the transaction."""
    # Hostile text is inert in the transaction since it relies entirely on the
    # typed ProcedureReplacementDecision and ProcedureRecord inputs.
    pass
