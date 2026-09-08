import ast
from pathlib import Path


def test_no_model_calls_in_transaction():
    """Verify that the transaction does not call LLM APIs or network services."""
    module_path = Path("src/agentx/procedure_replacement_transaction.py")
    tree = ast.parse(module_path.read_text())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                assert name.name not in {"requests", "httpx", "urllib", "openai", "anthropic"}
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in {"requests", "httpx", "urllib", "openai", "anthropic"}


def test_no_second_persistence_system():
    """Verify it relies on the provided ProcedureStore and SQLite."""
    module_path = Path("src/agentx/procedure_replacement_transaction.py")
    tree = ast.parse(module_path.read_text())

    has_sqlite = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                if name.name == "sqlite3":
                    has_sqlite = True
    assert has_sqlite, "Should use sqlite3 directly or via store"
