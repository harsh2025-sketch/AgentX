"""Architecture guards for the C4.07 canonical shadow-repair evidence contract.

The shadow-repair module is a pure inward ``agentx.core`` data contract.
These static guards prove it stays that way: no outward subsystem imports,
no persistence or migration surface, no execution/sandbox/patch machinery,
no keyword/text interpretation, no ranking or scoring, and no competing
identifier or error types. C2.03 (procedures), C2.02 ids, and C1.02
events remain untouched and are consumed by value only.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "core" / "shadow_repair.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_PROCEDURES = (_REPO_ROOT / "src" / "agentx" / "core" / "procedures.py").read_text(encoding="utf-8")
_IDS = (_REPO_ROOT / "src" / "agentx" / "core" / "ids.py").read_text(encoding="utf-8")
_EVENTS = (_REPO_ROOT / "src" / "agentx" / "core" / "events.py").read_text(encoding="utf-8")
_REPAIR_CANDIDATES = (_REPO_ROOT / "src" / "agentx" / "core" / "repair_candidates.py").read_text(
    encoding="utf-8"
)
_CAUSAL = (_REPO_ROOT / "src" / "agentx" / "core" / "causal_experience.py").read_text(
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
    # Module-level public callables only; class methods (to_dict, from_json,
    # is_passed, ...) are part of the data-contract surface, not free
    # functions.
    return {
        node.name
        for node in _tree().body
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

    assert agentx_imports == {"agentx.core.events", "agentx.core.ids", "agentx.core.procedures"}
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


def test_c407_defines_data_contracts_not_repair_or_sandbox_subsystems() -> None:
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
        "RepairPlanner",
        "RepairEngine",
        "RepairSelector",
        "PatchGenerator",
        "RetryPolicy",
        "FallbackPolicy",
        "EscalationPolicy",
        "ShadowRepairStore",
        "ShadowRepairRunner",
        "ShadowSandbox",
        "Sandbox",
        "IsolationBoundary",
        "RepairBudget",
        "ShadowExecutor",
    }

    assert classes.isdisjoint(forbidden)
    # The contract is exactly the five vocabulary/record types plus the
    # three error classes; no store, runner, sandbox, or policy engine exists.
    assert classes == {
        "ShadowRepairMode",
        "ShadowRepairDisposition",
        "ShadowStepOutcome",
        "ShadowRevisionOutcome",
        "ShadowRepairStepEvidence",
        "ShadowRepairResult",
        "ShadowRepairValidationError",
        "ShadowRepairDeserializationError",
        "UnsupportedShadowRepairSchemaVersionError",
    }


def test_c407_creates_no_competing_error_or_id_hierarchy() -> None:
    exception_bases: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name):
                exception_bases.add(base.id)

    # The run identity is a plain non-nil UUID alias, not a new DomainId
    # subclass, and no other competing identifier type is invented here.
    assert "ShadowRepairRunId" not in _classes()
    assert "TaskId" not in _classes()
    assert "ProcedureId" not in _classes()
    assert "DomainId" not in _classes()
    assert "AgentXError" not in _classes()
    assert "AgentXException" not in _classes()
    assert "AgentXError" not in exception_bases
    assert "Exception" not in exception_bases
    assert "BaseException" not in exception_bases
    assert {"ValueError"} <= exception_bases


def test_c407_exposes_no_public_functions() -> None:
    # A pure data contract: every public name is a class, a constant, or the
    # run-identity type alias. There is no derivation, execution, or policy
    # entry point — a future composition task owns execution.
    module_level = {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }

    assert module_level == set()
    assert _public_functions() == set()

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
        "execute_shadow",
        "run_shadow",
        "create_sandbox",
        "sandbox",
        "rollback",
        "retry",
        "fallback",
        "escalate",
        "suppress",
        "rank",
        "score",
        "predict_success",
        "verify",
        "infer",
        "query_store",
        "embed",
        "mutate_procedure",
        "activate_procedure",
        "replace_revision",
        "execute",
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
        "sys",
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


def test_c407_adds_no_persistence_or_migration_surface() -> None:
    assert "SQLiteDatabase" not in _SOURCE
    assert "_Migration" not in _SOURCE
    assert "agentx_schema_migrations" not in _SOURCE
    assert "CREATE TABLE" not in _SOURCE
    assert "ShadowRepairStore" not in _SOURCE

    persistence_source = (
        _REPO_ROOT / "src" / "agentx" / "infrastructure" / "persistence.py"
    ).read_text(encoding="utf-8")
    migration_versions = [
        int(match) for match in re.findall(r"^\s*version=(\d+),", persistence_source, re.MULTILINE)
    ]

    # C4.07 is a pure core contract: the migration ladder is untouched and
    # the highest landed migration is the canonical M12 scheduling store
    # (v9).
    assert migration_versions == sorted(migration_versions)
    assert max(migration_versions) == 9
    assert "shadow_repair" not in persistence_source
    assert "shadow_repair_runs" not in persistence_source


def test_c407_carries_no_ranking_scoring_confidence_or_model_machinery() -> None:
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
        "candidate_superior",
    ):
        assert token not in lowered


def test_c407_does_not_implement_keyword_inference() -> None:
    # The module must not scan free text for shadow/safety keywords.
    # Mentions of forbidden words may appear in docstrings explaining the
    # ban, but there must be no membership/contains tests against text.
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Compare):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    lowered = comparator.value.lower()
                    assert lowered not in {
                        "permission",
                        "verify",
                        "traceback",
                        "root cause",
                        "patch",
                        "retry",
                        "rollback",
                        "permission denied",
                        "safe",
                        "safe=true",
                        "verified=true",
                        "passed=true",
                        "shadow=true",
                        "sandbox=true",
                        "risk=r0",
                        "apply candidate",
                        "call shell",
                    }


def test_upstream_contracts_remain_untouched() -> None:
    # C2.03 still owns the procedure record and does not know C4.07 exists.
    assert "class ProcedureRecord" in _PROCEDURES
    assert "shadow_repair" not in _PROCEDURES
    assert "ShadowRepairResult" not in _PROCEDURES

    # A1.04 ids are untouched: no shadow-run domain ID was carved out.
    assert "class ProcedureId" in _IDS
    assert "shadow_repair" not in _IDS
    assert "ShadowRepairRunId" not in _IDS

    # C1.02 events are untouched; the verification payload is consumed, not
    # redefined.
    assert "class VerificationPayload" in _EVENTS
    assert "shadow_repair" not in _EVENTS

    # C4.04 is untouched: shadow repair consumes nothing from it and it
    # never re-exports C4.07.
    assert "class RepairCandidate" in _REPAIR_CANDIDATES
    assert "shadow_repair" not in _REPAIR_CANDIDATES

    # C2.10 is untouched.
    assert "class CausalExperience" in _CAUSAL
    assert "shadow_repair" not in _CAUSAL


def test_core_package_reexports_are_untouched() -> None:
    # This task owns no __init__.py, so the core package surface must be
    # exactly as it was at baseline: no new re-export of the leaf.
    core_init = (_REPO_ROOT / "src" / "agentx" / "core" / "__init__.py").read_text(encoding="utf-8")
    assert "shadow_repair" not in core_init


def test_c407_record_exposes_no_repair_sandbox_or_authority_methods() -> None:
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
        "run_trial",
        "create_sandbox",
        "replace_revision",
        "succeed_task",
    }
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                assert statement.name not in forbidden_methods


def test_c407_declares_the_exact_record_fields_and_no_decision_fields() -> None:
    dataclass_fields: dict[str, set[str]] = {}
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ClassDef) and node.name in {
            "ShadowRepairResult",
            "ShadowRepairStepEvidence",
        }:
            fields: set[str] = set()
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    fields.add(statement.target.id)
            dataclass_fields[node.name] = fields

    assert dataclass_fields["ShadowRepairResult"] == {
        "run_id",
        "procedure_id",
        "source_revision",
        "candidate_revision",
        "candidate_fingerprint",
        "target_node_id",
        "task_id",
        "correlation_id",
        "scope",
        "started_at",
        "ended_at",
        "mode",
        "disposition",
        "verification",
        "steps",
        "original_outcome",
        "regression_node_ids",
        "detail",
        "schema_version",
    }
    assert dataclass_fields["ShadowRepairStepEvidence"] == {
        "order",
        "node_id",
        "outcome",
        "external_effect_observed",
        "effect_contained",
    }
    for decision_field in (
        "selected",
        "authorized",
        "executed",
        "applied",
        "safe",
        "sandboxed",
        "correct",
        "score",
        "rank",
        "confidence",
        "probability",
        "task_succeeded",
        "candidate_superior",
    ):
        assert decision_field not in dataclass_fields["ShadowRepairResult"]
        assert decision_field not in dataclass_fields["ShadowRepairStepEvidence"]


def test_docs_page_exists_for_c407() -> None:
    docs = _REPO_ROOT / "docs" / "shadow_repair.md"
    assert docs.is_file()
    text = docs.read_text(encoding="utf-8")
    for token in (
        "C4.07",
        "DATA ONLY",
        "non_committing",
        "PASSED",
        "FAILED",
        "ABORTED",
        "UNSAFE_TO_EVALUATE",
        "INSUFFICIENT_EVIDENCE",
        "no OS-level sandboxing",
        "candidate identity",
        "zero new runtime dependencies",
    ):
        assert token in text
