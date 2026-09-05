"""Architecture guards for the C4.01 canonical failure taxonomy.

The failure taxonomy is a pure inward ``agentx.core`` data contract. These
static guards prove it stays that way: no outward subsystem imports, no
persistence or migration surface, no execution/diagnosis/repair machinery, and
no competing error hierarchy.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "core" / "failure_taxonomy.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")


def _tree() -> ast.Module:
    return ast.parse(_SOURCE)


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


def _public_functions() -> set[str]:
    return {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }


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


def test_contract_lives_in_core_and_is_an_inward_leaf() -> None:
    assert _MODULE.is_file()

    agentx_imports = {name for name in _imports() if name.startswith("agentx")}

    assert agentx_imports == {"agentx.core.errors", "agentx.core.ids"}
    for foreign in (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.hive",
        "agentx.procedures",
        "agentx.learning",
        "agentx.infrastructure",
    ):
        assert not any(name.startswith(foreign) for name in agentx_imports)


def test_contract_uses_only_inert_standard_library_modules() -> None:
    non_agentx = {name.split(".")[0] for name in _imports() if not name.startswith("agentx")}

    assert non_agentx <= {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "json",
        "typing",
        "uuid",
    }


def test_c401_defines_data_contracts_not_execution_diagnosis_or_policy_subsystems() -> None:
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
        "FailureClassifier",
        "FailureDiagnoser",
        "FailureLocalizer",
        "RepairPlanner",
        "RetryPolicy",
        "FallbackPolicy",
        "EscalationPolicy",
        "FailureClassificationStore",
    }

    assert classes.isdisjoint(forbidden)
    assert {"FailureCategory", "FailureClassification"} <= classes


def test_c401_creates_no_competing_error_hierarchy() -> None:
    exception_bases: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name):
                exception_bases.add(base.id)

    assert "AgentXError" not in _classes()
    assert "AgentXException" not in _classes()
    assert "AgentXError" not in exception_bases
    assert "Exception" not in exception_bases
    assert "BaseException" not in exception_bases
    # Validation failures reuse the canonical ValueError-based contract style.
    assert {"ValueError"} <= exception_bases


def test_c401_exposes_no_inference_or_repair_entry_points() -> None:
    forbidden_functions = {
        "classify",
        "classify_failure",
        "diagnose",
        "infer_category",
        "localize",
        "repair",
        "retry",
        "fallback",
        "escalate",
        "suppress",
    }

    assert _public_functions().isdisjoint(forbidden_functions)


def test_no_execution_dynamic_import_network_or_file_side_effect_primitives() -> None:
    forbidden_imports = {
        "importlib",
        "pickle",
        "subprocess",
        "socket",
        "sqlite3",
        "urllib",
        "os",
        "pathlib",
        "random",
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
        "retry",
        "repair",
        "grant",
        "revoke",
    }

    assert _imports().isdisjoint(forbidden_imports)
    assert _called_names().isdisjoint(forbidden_calls)


def test_c401_adds_no_persistence_or_migration_surface() -> None:
    assert "SQLiteDatabase" not in _SOURCE
    assert "_Migration" not in _SOURCE
    assert "agentx_schema_migrations" not in _SOURCE
    assert "CREATE TABLE" not in _SOURCE
    assert "FailureClassificationStore" not in _SOURCE

    persistence_source = (
        _REPO_ROOT / "src" / "agentx" / "infrastructure" / "persistence.py"
    ).read_text(encoding="utf-8")
    migration_versions = [
        int(match) for match in re.findall(r"^\s*version=(\d+),", persistence_source, re.MULTILINE)
    ]

    # C4.01 is a pure core contract: the migration ladder is untouched and the
    # highest landed migration remains the C2.06 negative-experience store (v8).
    assert migration_versions == sorted(migration_versions)
    assert max(migration_versions) == 8
    assert "failure_classification" not in persistence_source
    assert "agentx_failure" not in persistence_source


def test_c401_carries_no_probabilistic_confidence_machinery() -> None:
    lowered = _SOURCE.lower()

    for token in ("confidence=", "probability=", "score=", "weight=", "likelihood"):
        assert token not in lowered
