"""Architecture guards for the C4.03 canonical procedure-node diagnosis contract.

The failure-diagnosis module is a pure inward ``agentx.core`` data contract.
These static guards prove it stays that way: no outward subsystem imports, no
persistence or migration surface, no repair/execution/inference machinery, no
keyword/model/stack-trace interpretation, and no competing identifier or error
types. C4.01 (failure taxonomy) and C4.02 (failure localization) remain
untouched and are consumed by value only.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "core" / "failure_diagnosis.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TAXONOMY = (_REPO_ROOT / "src" / "agentx" / "core" / "failure_taxonomy.py").read_text(
    encoding="utf-8"
)
_LOCALIZATION = (_REPO_ROOT / "src" / "agentx" / "core" / "failure_localization.py").read_text(
    encoding="utf-8"
)


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

    assert agentx_imports == {
        "agentx.core.errors",
        "agentx.core.failure_localization",
        "agentx.core.failure_taxonomy",
        "agentx.core.ids",
    }
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


def test_c403_defines_data_contracts_not_execution_diagnosis_or_policy_subsystems() -> None:
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
        "FailureDiagnosisStore",
        "CausalExperience",
        "NormalizedTrajectory",
        "ProcedureGraph",
        "ProcedureNode",
        "ProcedureNodeId",
        "FailureClassification",
        "FailureLocalization",
    }

    assert classes.isdisjoint(forbidden)
    assert {
        "DiagnosticEvidenceKind",
        "DiagnosticConclusion",
        "DiagnosticEvidence",
        "FailureDiagnosis",
    } <= classes


def test_c403_creates_no_competing_error_or_id_hierarchy() -> None:
    exception_bases: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name):
                exception_bases.add(base.id)

    assert "AgentXError" not in _classes()
    assert "AgentXException" not in _classes()
    assert "TaskId" not in _classes()
    assert "EpisodeId" not in _classes()
    assert "ProcedureId" not in _classes()
    assert "ProcedureNodeId" not in _classes()
    assert "AgentXError" not in exception_bases
    assert "Exception" not in exception_bases
    assert "BaseException" not in exception_bases
    assert {"ValueError"} <= exception_bases


def test_c403_exposes_only_the_pure_packaging_entry_point() -> None:
    module_level = {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }

    assert module_level == {"package_diagnosis"}
    forbidden_functions = {
        "classify",
        "classify_failure",
        "diagnose",
        "diagnose_failure",
        "infer",
        "infer_category",
        "infer_conclusion",
        "infer_location",
        "localize",
        "localize_failure",
        "repair",
        "retry",
        "fallback",
        "escalate",
        "suppress",
        "patch",
        "rollback",
        "execute",
        "verify",
        "analyze_trajectory",
        "query_store",
        "query_graph",
        "embed",
        "score",
        "root_cause",
    }

    assert module_level.isdisjoint(forbidden_functions)
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
        "re",
        "hashlib",
        "threading",
        "ctypes",
    }
    forbidden_calls = {
        "eval",
        "exec",
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
        "patch",
        "rollback",
        "escalate",
        "suppress",
        "classify",
        "infer",
        "findall",
        "search",
        "match",
        "fullmatch",
        "sub",
    }

    assert _imports().isdisjoint(forbidden_imports)
    assert "__import__" not in _called_names()
    assert _called_names().isdisjoint(forbidden_calls)


def test_c403_adds_no_persistence_or_migration_surface() -> None:
    assert "SQLiteDatabase" not in _SOURCE
    assert "_Migration" not in _SOURCE
    assert "agentx_schema_migrations" not in _SOURCE
    assert "CREATE TABLE" not in _SOURCE
    assert "FailureDiagnosisStore" not in _SOURCE

    persistence_source = (
        _REPO_ROOT / "src" / "agentx" / "infrastructure" / "persistence.py"
    ).read_text(encoding="utf-8")
    migration_versions = [
        int(match) for match in re.findall(r"^\s*version=(\d+),", persistence_source, re.MULTILINE)
    ]

    # C4.03 is a pure core contract: the migration ladder is untouched and the
    # highest landed migration is the canonical M12 scheduling store (v9).
    assert migration_versions == sorted(migration_versions)
    assert max(migration_versions) == 9
    assert "failure_diagnosis" not in persistence_source
    assert "agentx_failure_diagnosis" not in persistence_source


def test_c403_carries_no_probabilistic_confidence_or_model_machinery() -> None:
    lowered = _SOURCE.lower()

    for token in (
        "confidence=",
        "probability=",
        "score=",
        "weight=",
        "likelihood",
        "openai",
        "anthropic",
        "model_provider",
        "tokenizer",
        "embeddings",
        "vector_store",
    ):
        assert token not in lowered

    assert "embedding=" not in lowered


def test_c403_does_not_implement_keyword_inference() -> None:
    # The module must not scan free text for diagnostic keywords. Mentions of
    # forbidden words may appear in documentation strings explaining the ban,
    # but there must be no membership/contains tests against arbitrary text.
    tree = _tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    lowered = comparator.value.lower()
                    assert lowered not in {
                        "permission",
                        "button",
                        "api",
                        "verify",
                        "traceback",
                        "root cause",
                        "patch",
                    }


def test_c403_never_mutates_c401_taxonomy_or_c402_localization() -> None:
    # The C4.01 module still owns the category vocabulary untouched.
    assert "class FailureCategory" in _TAXONOMY
    assert "class FailureClassification" in _TAXONOMY
    assert "class DiagnosticEvidenceKind" not in _TAXONOMY
    assert "class FailureDiagnosis" not in _TAXONOMY
    assert "package_diagnosis" not in _TAXONOMY
    # C4.01 still documents C4.03 as a later non-goal, not as owned surface.
    assert "procedure-node diagnosis (C4.03)" in _TAXONOMY

    # The C4.02 module still owns the localization vocabulary untouched.
    assert "class FailureLocalization" in _LOCALIZATION
    assert "class FailureLocationKind" in _LOCALIZATION
    assert "class DiagnosticConclusion" not in _LOCALIZATION
    assert "package_diagnosis" not in _LOCALIZATION
    # C4.02 still documents that C4.03 owns node-logical-wrongness, not C4.02.
    assert "C4.03" in _LOCALIZATION


def test_c403_does_not_query_or_analyze_other_records_or_stores() -> None:
    assert "causal_experience" not in _SOURCE
    assert "NormalizedTrajectory" not in _SOURCE
    assert "normalize_trajectory" not in _SOURCE
    assert "EpisodeStore" not in _SOURCE
    assert "NegativeExperienceStore" not in _SOURCE
    assert "ExperienceMemory" not in _SOURCE
    assert "ProcedureStore" not in _SOURCE


def test_c403_records_expose_no_repair_or_authority_methods() -> None:
    # Record methods are pure data access/serialization only; no method may name
    # a repair, retry, authority, inference, or execution action.
    forbidden_methods = {
        "repair",
        "retry",
        "rollback",
        "patch",
        "grant",
        "revoke",
        "execute",
        "escalate",
        "suppress",
        "fallback",
        "verify",
        "classify",
        "infer",
        "diagnose",
        "apply",
        "promote",
        "activate",
        "publish",
        "root_cause",
    }
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                assert statement.name not in forbidden_methods


def test_docs_page_exists_for_c403() -> None:
    docs = _REPO_ROOT / "docs" / "failure_diagnosis.md"
    assert docs.is_file()
    text = docs.read_text(encoding="utf-8")
    assert "C4.03" in text
    assert "UNKNOWN" in text
    assert "NODE_IMPLICATED" in text
    assert "evidence" in text
    assert "Persistence decision" in text
    assert "orthogonal" in text.lower() or "orthogonality" in text.lower()
