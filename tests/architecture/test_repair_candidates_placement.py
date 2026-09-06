"""Architecture guards for the C4.04 canonical repair-candidate contract.

The repair-candidate module is a pure inward ``agentx.core`` data contract.
These static guards prove it stays that way: no outward subsystem imports, no
persistence or migration surface, no repair/execution/selection machinery, no
keyword/model/stack-trace interpretation, no ranking or scoring, and no
competing identifier or error types. C4.01 (taxonomy), C4.02 (localization),
and C4.03 (diagnosis) remain untouched and are consumed by value only.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "core" / "repair_candidates.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TAXONOMY = (_REPO_ROOT / "src" / "agentx" / "core" / "failure_taxonomy.py").read_text(
    encoding="utf-8"
)
_LOCALIZATION = (_REPO_ROOT / "src" / "agentx" / "core" / "failure_localization.py").read_text(
    encoding="utf-8"
)
_DIAGNOSIS = (_REPO_ROOT / "src" / "agentx" / "core" / "failure_diagnosis.py").read_text(
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

    assert agentx_imports == {"agentx.core.failure_diagnosis"}
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
    }


def test_c404_defines_data_contracts_not_repair_selection_or_policy_subsystems() -> None:
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
        "ProcedureGraph",
        "ProcedureNode",
        "ProcedureNodeId",
        "RepairPlanner",
        "RepairEngine",
        "RepairSelector",
        "PatchGenerator",
        "RetryPolicy",
        "FallbackPolicy",
        "EscalationPolicy",
        "RepairCandidateStore",
        "RepairCandidateRanker",
        "RepairBudget",
        "FailureClassifier",
        "FailureLocalizer",
        "FailureDiagnoser",
        "FailureClassification",
        "FailureLocalization",
        "FailureDiagnosis",
    }

    assert classes.isdisjoint(forbidden)
    assert {"RepairCandidateKind", "RepairCandidate"} <= classes
    # No competing record types were invented for provenance: only the two
    # contract classes above plus the three error classes exist here.
    assert classes == {
        "RepairCandidateKind",
        "RepairCandidate",
        "RepairCandidateValidationError",
        "RepairCandidateDeserializationError",
        "UnsupportedRepairCandidateSchemaVersionError",
    }


def test_c404_creates_no_competing_error_or_id_hierarchy() -> None:
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
    assert "ProcedureId" not in _classes()
    assert "AgentXError" not in exception_bases
    assert "Exception" not in exception_bases
    assert "BaseException" not in exception_bases
    assert {"ValueError"} <= exception_bases


def test_c404_exposes_only_the_pure_derivation_entry_point() -> None:
    module_level = {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }

    assert module_level == {"derive_repair_candidates"}
    forbidden_functions = {
        "select_candidate",
        "choose_repair",
        "authorize",
        "approve",
        "apply_repair",
        "patch",
        "generate_patch",
        "repair",
        "execute_repair",
        "rollback",
        "retry",
        "fallback",
        "escalate",
        "suppress",
        "rank",
        "rank_candidates",
        "score",
        "score_candidate",
        "predict_success",
        "verify",
        "infer",
        "infer_repair",
        "classify",
        "diagnose",
        "localize",
        "analyze_traceback",
        "analyze_trajectory",
        "query_store",
        "query_graph",
        "embed",
        "mutate_procedure",
        "activate_procedure",
        "execute",
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
        "http",
        "os",
        "pathlib",
        "random",
        "secrets",
        "re",
        "hashlib",
        "threading",
        "ctypes",
        "shutil",
        "tempfile",
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
        "select",
        "rank",
        "score",
        "findall",
        "search",
        "match",
        "fullmatch",
        "sub",
        "shuffle",
        "choice",
        "sample",
        "randint",
        "random",
        "seed",
        "now",
        "today",
        "utcnow",
    }

    assert _imports().isdisjoint(forbidden_imports)
    assert "__import__" not in _called_names()
    assert _called_names().isdisjoint(forbidden_calls)


def test_c404_adds_no_persistence_or_migration_surface() -> None:
    assert "SQLiteDatabase" not in _SOURCE
    assert "_Migration" not in _SOURCE
    assert "agentx_schema_migrations" not in _SOURCE
    assert "CREATE TABLE" not in _SOURCE
    assert "RepairCandidateStore" not in _SOURCE

    persistence_source = (
        _REPO_ROOT / "src" / "agentx" / "infrastructure" / "persistence.py"
    ).read_text(encoding="utf-8")
    migration_versions = [
        int(match) for match in re.findall(r"^\s*version=(\d+),", persistence_source, re.MULTILINE)
    ]

    # C4.04 is a pure core contract: the migration ladder is untouched and the
    # highest landed migration remains the C2.06 negative-experience store (v8).
    assert migration_versions == sorted(migration_versions)
    assert max(migration_versions) == 8
    assert "repair_candidate" not in persistence_source
    assert "agentx_repair_candidates" not in persistence_source


def test_c404_carries_no_ranking_scoring_confidence_or_model_machinery() -> None:
    # Mirrors the C4.03 guard: executable/probabilistic machinery is banned;
    # prose that *explains* the ban is allowed.
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
    assert "ranking=" not in lowered
    assert "sort_candidates" not in lowered


def test_c404_does_not_implement_keyword_inference() -> None:
    # The module must not scan free text for repair keywords. Mentions of
    # forbidden words may appear in documentation strings explaining the ban,
    # but there must be no membership/contains tests against arbitrary text.
    for node in ast.walk(_tree()):
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
                        "retry",
                        "rollback",
                        "permission denied",
                    }


def test_c404_does_not_mutate_or_reexport_upstream_contracts() -> None:
    # C4.01 still owns its vocabulary untouched and does not know C4.04 exists.
    assert "class FailureCategory" in _TAXONOMY
    assert "class FailureClassification" in _TAXONOMY
    assert "RepairCandidate" not in _TAXONOMY
    assert "repair_candidates" not in _TAXONOMY

    # C4.02 is untouched.
    assert "class FailureLocalization" in _LOCALIZATION
    assert "class FailureLocationKind" in _LOCALIZATION
    assert "RepairCandidate" not in _LOCALIZATION
    assert "repair_candidates" not in _LOCALIZATION

    # C4.03 is untouched: candidates consume it; it never consumes or re-exports them.
    assert "class FailureDiagnosis" in _DIAGNOSIS
    assert "class DiagnosticConclusion" in _DIAGNOSIS
    assert "RepairCandidate" not in _DIAGNOSIS
    assert "repair_candidates" not in _DIAGNOSIS
    assert "derive_repair_candidates" not in _DIAGNOSIS


def test_c404_does_not_query_or_analyze_other_records_or_stores() -> None:
    assert "causal_experience" not in _SOURCE
    assert "NormalizedTrajectory" not in _SOURCE
    assert "EpisodeStore" not in _SOURCE
    assert "NegativeExperienceStore" not in _SOURCE
    assert "ExperienceMemory" not in _SOURCE
    assert "ProcedureStore" not in _SOURCE
    assert "KnowledgeStore" not in _SOURCE


def test_c404_record_exposes_no_repair_selection_or_authority_methods() -> None:
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
        "select",
        "choose",
        "approve",
        "authorize",
        "rank",
        "score",
        "mark_selected",
        "mark_executed",
        "mark_verified",
    }
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                assert statement.name not in forbidden_methods


def test_c404_declares_the_candidate_fields_and_no_decision_fields() -> None:
    dataclass_fields: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ClassDef) and node.name == "RepairCandidate":
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    dataclass_fields.add(statement.target.id)

    assert dataclass_fields == {
        "kind",
        "diagnosis",
        "proposed_at",
        "supporting_evidence_indices",
        "schema_version",
    }
    for decision_field in (
        "selected",
        "authorized",
        "executed",
        "verified",
        "applied",
        "safe",
        "correct",
        "score",
        "rank",
        "confidence",
        "probability",
    ):
        assert decision_field not in dataclass_fields


def test_core_package_status_documents_the_new_leaf() -> None:
    core_init = (_REPO_ROOT / "src" / "agentx" / "core" / "__init__.py").read_text(encoding="utf-8")
    assert "``repair_candidates``" in core_init
    assert "C4.04" in core_init


def test_docs_page_exists_for_c404() -> None:
    docs = _REPO_ROOT / "docs" / "repair_candidates.md"
    assert docs.is_file()
    text = docs.read_text(encoding="utf-8")
    for token in (
        "C4.04",
        "UNKNOWN",
        "NODE_DEFINITION_REVISION",
        "Persistence decision",
        "provenance",
        "hypothesis",
        "Zero new runtime dependencies",
        "candidate != selected",
    ):
        assert token in text
