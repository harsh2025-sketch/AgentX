"""Architecture guards for the C2.10 causal-experience model."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "core" / "causal_experience.py"


def _tree() -> ast.Module:
    return ast.parse(_MODULE.read_text(encoding="utf-8"))


def _imports() -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def _classes() -> set[str]:
    return {node.name for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)}


def _called_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_contract_is_an_inward_core_leaf() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx.")}

    assert agentx_imports == {"agentx.core.events", "agentx.core.ids"}
    assert not any(name.startswith("agentx.capabilities") for name in agentx_imports)
    assert not any(name.startswith("agentx.infrastructure") for name in agentx_imports)
    assert not any(name.startswith("agentx.kernel") for name in agentx_imports)
    assert not any(name.startswith("agentx.cognition") for name in agentx_imports)
    assert not any(name.startswith("agentx.learning") for name in agentx_imports)


def test_c210_defines_data_contracts_not_execution_or_policy_subsystems() -> None:
    classes = _classes()
    forbidden = {
        "ActionGate",
        "AuthorityContext",
        "Capability",
        "Executor",
        "Reasoner",
        "Router",
        "TaskManager",
        "Verifier",
        "Permission",
        "RiskAssessment",
        "ResourceEnvelope",
        "EmergencyStop",
        "KnowledgeRecord",
        "ProcedureRecord",
        "CausalExperienceStore",
    }

    assert classes.isdisjoint(forbidden)
    assert {"CausalExperience", "ExperienceState", "CausalOutcome"} <= classes


def test_no_execution_dynamic_import_network_or_file_side_effect_primitives() -> None:
    forbidden_imports = {
        "importlib",
        "pickle",
        "subprocess",
        "socket",
        "sqlite3",
        "urllib",
    }
    forbidden_calls = {
        "eval",
        "exec",
        "__import__",
        "open",
        "urlopen",
        "execute",
        "verify",
        "reason",
        "publish",
        "transition",
        "activate",
        "promote",
    }

    assert _imports().isdisjoint(forbidden_imports)
    assert _called_names().isdisjoint(forbidden_calls)


def test_c210_adds_no_persistence_or_migration_surface() -> None:
    source = _MODULE.read_text(encoding="utf-8")

    assert "SQLiteDatabase" not in source
    assert "_Migration" not in source
    assert "agentx_schema_migrations" not in source
    assert "CREATE TABLE" not in source
    assert "CausalExperienceStore" not in source
