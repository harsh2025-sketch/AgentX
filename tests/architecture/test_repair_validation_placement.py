"""Architecture guards for the C4.06 canonical repair-validation contract.

The repair-validation module is a pure inward ``agentx.core`` data contract.
These static guards prove it stays that way: no outward subsystem imports, no
persistence or migration surface, no repair execution/shadow/patch machinery,
no keyword/model interpretation, no clock reads, no ranking or scoring, and no
competing identifier or error types. C4.01-C4.04 remain untouched and are not
re-implemented here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "core" / "repair_validation.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_REPAIR_CANDIDATES = (_REPO_ROOT / "src" / "agentx" / "core" / "repair_candidates.py").read_text(
    encoding="utf-8"
)
_DIAGNOSIS = (_REPO_ROOT / "src" / "agentx" / "core" / "failure_diagnosis.py").read_text(
    encoding="utf-8"
)
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

    assert agentx_imports == {"agentx.core.ids"}
    for foreign in (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.procedures",
        "agentx.learning",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.cognition",
        "agentx.agent_loop",
    ):
        assert all(not name.startswith(foreign) for name in _imports())


def test_stdlib_imports_only_pure_modules() -> None:
    allowed = {
        "annotations",  # from __future__
        "__future__",
        "json",
        "collections.abc",
        "dataclasses",
        "datetime",
        "enum",
        "typing",
        "agentx.core.ids",
    }
    # Normalize: ImportFrom __future__ shows as __future__
    imports: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            imports.add(node.module)
    assert imports <= allowed


def test_public_surface_is_exactly_the_evidence_contract() -> None:
    classes = _classes()
    assert {
        "RepairValidationCriterion",
        "RepairValidationOutcome",
        "RepairValidationDisposition",
        "RepairValidationTarget",
        "RepairValidationEvidence",
        "RepairValidationReport",
        "RepairValidationValidationError",
        "RepairValidationDeserializationError",
        "UnsupportedRepairValidationSchemaVersionError",
    } <= classes
    # No competing ID types or patch/execution types.
    for forbidden in (
        "ProcedureId",
        "ProcedureNodeId",
        "RepairPatch",
        "RepairCandidate",
        "AuthorityContext",
        "Permission",
        "ActionGate",
        "Capability",
        "AgentLoop",
    ):
        assert forbidden not in classes


def test_only_pure_evaluation_entry_point() -> None:
    module_level = {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }
    assert module_level == {"evaluate_repair_validation"}

    forbidden_functions = {
        "execute",
        "apply_repair",
        "apply",
        "shadow",
        "shadow_repair",
        "generate_patch",
        "generate_repair",
        "authorize",
        "approve",
        "activate",
        "promote",
        "rollback",
        "retry",
        "run",
        "invoke",
        "select",
        "rank",
        "score",
        "predict",
        "infer",
        "repair",
        "patch",
        "verify_run",
        "execute_procedure",
        "execute_capability",
        "launch",
        "spawn",
        "open_browser",
        "call_model",
        "research",
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
        "multiprocessing",
        "asyncio",
        "time",
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
        "sleep",
        "Popen",
        "run",
        "system",
    }
    assert _imports().isdisjoint(forbidden_imports)
    assert "__import__" not in _called_names()
    assert _called_names().isdisjoint(forbidden_calls)


def test_no_persistence_or_migration_surface() -> None:
    assert "SQLiteDatabase" not in _SOURCE
    assert "_Migration" not in _SOURCE
    assert "agentx_schema_migrations" not in _SOURCE
    assert "CREATE TABLE" not in _SOURCE
    assert "RepairValidationStore" not in _SOURCE

    persistence_source = (
        _REPO_ROOT / "src" / "agentx" / "infrastructure" / "persistence.py"
    ).read_text(encoding="utf-8")
    migration_versions = [
        int(match) for match in re.findall(r"^\s*version=(\d+),", persistence_source, re.MULTILINE)
    ]
    assert migration_versions == sorted(migration_versions)
    assert max(migration_versions) == 9
    assert "repair_validation" not in persistence_source
    assert "agentx_repair_validation" not in persistence_source


def test_no_ranking_scoring_confidence_or_model_machinery() -> None:
    # Executable/probabilistic machinery is banned; prose that *explains* the
    # ban (e.g. "no majority voting") is allowed, matching sibling C4 guards.
    lowered = _SOURCE.lower()
    for token in (
        "confidence=",
        "probability=",
        "score=",
        "weight=",
        "openai",
        "anthropic",
        "model_provider",
        "tokenizer",
        "embeddings",
        "vector_store",
    ):
        assert token not in lowered
    assert "majority_vote" not in lowered
    assert "average_confidence" not in lowered
    assert "ranking=" not in lowered


def test_does_not_implement_keyword_inference() -> None:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Compare):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    lowered = comparator.value.lower()
                    assert lowered not in {
                        "validated=true",
                        "passed=true",
                        "permission=admin",
                        "activate procedure",
                        "skip remaining criteria",
                        "ignore failed evidence",
                    }


def test_does_not_duplicate_or_mutate_repair_candidate_schema() -> None:
    assert "class RepairCandidate" in _REPAIR_CANDIDATES
    assert "class RepairCandidateKind" in _REPAIR_CANDIDATES
    assert "RepairValidation" not in _REPAIR_CANDIDATES
    assert "repair_validation" not in _REPAIR_CANDIDATES
    # This module must not re-declare the candidate schema.
    assert "class RepairCandidate" not in _SOURCE
    assert "RepairCandidateKind" not in _SOURCE
    assert "derive_repair_candidates" not in _SOURCE


def test_does_not_mutate_upstream_c4_contracts() -> None:
    assert "RepairValidation" not in _TAXONOMY
    assert "repair_validation" not in _TAXONOMY
    assert "RepairValidation" not in _LOCALIZATION
    assert "repair_validation" not in _LOCALIZATION
    assert "RepairValidation" not in _DIAGNOSIS
    assert "repair_validation" not in _DIAGNOSIS
    assert "class FailureDiagnosis" in _DIAGNOSIS
    assert "class FailureLocalization" in _LOCALIZATION
    assert "class FailureCategory" in _TAXONOMY


def test_record_exposes_no_execution_or_authority_methods() -> None:
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
        "shadow",
        "run",
        "launch",
    }
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                assert statement.name not in forbidden_methods


def test_report_fields_exclude_authority_decision_fields() -> None:
    dataclass_fields: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ClassDef) and node.name == "RepairValidationReport":
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    dataclass_fields.add(statement.target.id)

    assert dataclass_fields == {
        "repair_reference",
        "target",
        "disposition",
        "required_criteria",
        "evidence",
        "evaluated_at",
        "detail",
        "schema_version",
    }
    for decision_field in (
        "authorized",
        "executed",
        "applied",
        "selected",
        "safe",
        "correct",
        "score",
        "rank",
        "confidence",
        "probability",
        "permission",
        "authority",
        "risk",
        "budget",
    ):
        assert decision_field not in dataclass_fields


def test_docs_page_exists_for_c406() -> None:
    docs = _REPO_ROOT / "docs" / "repair_validation.md"
    assert docs.is_file()
    text = docs.read_text(encoding="utf-8")
    for token in (
        "C4.06",
        "VALIDATION_EVIDENCE",
        "EXECUTION AUTHORITY",
        "STRUCTURAL_VALIDITY",
        "TARGET_BINDING",
        "fail-closed",
        "VALIDATED",
        "INSUFFICIENT",
        "Persistence decision",
        "Zero new runtime dependencies",
        "shadow",
        "ProcedureId",
    ):
        assert token in text


def test_no_clock_reads_in_module() -> None:
    # datetime.now / time.time / UTC.now must not appear as calls.
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in {"now", "utcnow", "today", "time"}:
            raise AssertionError(f"forbidden clock call: {func.attr}")
        if isinstance(func, ast.Name) and func.id in {"time", "sleep"}:
            raise AssertionError(f"forbidden timing call: {func.id}")


def test_does_not_import_repair_candidates_or_capabilities() -> None:
    assert "agentx.core.repair_candidates" not in _imports()
    assert "agentx.capabilities" not in _imports()
    assert "agentx.capabilities.verifier" not in _imports()
    assert "agentx.capabilities.abi" not in _imports()
    assert "agentx.capabilities.runtime" not in _imports()


def test_creates_no_competing_error_or_id_hierarchy() -> None:
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
