"""Architecture guards for the C4.08 procedure replacement / rollback policy.

``agentx.core.procedure_replacement`` is a pure inward ``agentx.core`` policy
leaf. These static guards prove it stays that way: no outward subsystem
imports, no store or migration surface, no mutation or execution machinery, no
authority types, no clock or I/O, and — critically — no read of the record
fields whose content must never be able to promote a revision.

The guards also pin the task's hard file-ownership boundary: the canonical
procedure record, the procedure store, the persistence migration ladder, and
every ``__init__.py`` are untouched by this task.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path

import pytest

from agentx.core.procedure_replacement import (
    ProcedureReplacementDecision,
    ProcedureReplacementEvidence,
    ProcedureReplacementFinding,
    ProcedureReplacementKind,
    ProcedureReplacementOutcome,
    ProcedureReplacementReason,
    ProcedureReplacementRequest,
    RetiredTargetReactivation,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "agentx"
_MODULE = _SRC / "core" / "procedure_replacement.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_PROCEDURES = (_SRC / "core" / "procedures.py").read_text(encoding="utf-8")
_PROCEDURE_STORE = (_SRC / "infrastructure" / "procedure_store.py").read_text(encoding="utf-8")
_PERSISTENCE = (_SRC / "infrastructure" / "persistence.py").read_text(encoding="utf-8")


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


def _accessed_attributes() -> set[str]:
    return {node.attr for node in ast.walk(_tree()) if isinstance(node, ast.Attribute)}


# ---------------------------------------------------------------------------
# Placement: an inward core leaf
# ---------------------------------------------------------------------------


def test_contract_lives_in_core_and_is_an_inward_leaf() -> None:
    assert _MODULE.is_file()
    assert _MODULE.parent == _SRC / "core"

    agentx_imports = {name for name in _imports() if name.startswith("agentx")}

    assert agentx_imports == {"agentx.core.ids", "agentx.core.procedures"}
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
        "enum",
        "json",
        "types",
        "typing",
    }


def test_no_persistence_migration_or_procedure_store_surface() -> None:
    assert "ProcedureStore" not in _SOURCE
    assert "SQLiteDatabase" not in _SOURCE
    assert "sqlite3" not in _SOURCE
    assert "CREATE TABLE" not in _SOURCE
    assert "agentx_schema_migrations" not in _SOURCE
    assert "_Migration" not in _SOURCE
    assert "update_status" not in _SOURCE
    assert "insert(" not in _SOURCE
    assert "connection" not in _SOURCE


def test_the_persistence_migration_ladder_is_untouched() -> None:
    migration_versions = [
        int(match) for match in re.findall(r"^\s*version=(\d+),", _PERSISTENCE, re.MULTILINE)
    ]

    assert migration_versions == sorted(migration_versions)
    assert max(migration_versions) == 9
    assert "procedure_replacement" not in _PERSISTENCE
    assert "agentx_procedure_replacements" not in _PERSISTENCE


# ---------------------------------------------------------------------------
# Surface: policy contracts only
# ---------------------------------------------------------------------------


def test_only_the_policy_contracts_are_defined_here() -> None:
    assert _classes() == {
        "ProcedureReplacementPolicyError",
        "ProcedureReplacementRequestError",
        "ProcedureReplacementKind",
        "ProcedureReplacementReason",
        "ProcedureReplacementOutcome",
        "ProcedureReplacementFinding",
        "EvidencePresence",
        "TargetIntegrityState",
        "RetiredTargetReactivation",
        "ProcedureReplacementEvidence",
        "ProcedureReplacementRequest",
        "ProcedureReplacementDecision",
    }

    forbidden = {
        # authority
        "Permission",
        "AuthorityContext",
        "ActionGate",
        "RiskAssessment",
        "RiskLevel",
        "ResourceEnvelope",
        "ResourceBudget",
        "EmergencyStop",
        # storage / execution
        "ProcedureStore",
        "SQLiteDatabase",
        "Executor",
        "Capability",
        "Verifier",
        "Router",
        "Reasoner",
        "TaskManager",
        # competing or re-invented contracts
        "ProcedureRecord",
        "ProcedureId",
        "ProcedureStatus",
        "ProcedurePayload",
        "ProcedureScope",
        "ProcedureGraph",
        "ProcedureNode",
        "AgentXError",
        "AgentXException",
        "TaskId",
        # lifecycle engines this task must not implement
        "ProcedureLifecycle",
        "ProcedureLifecycleEngine",
        "ReplacementTransaction",
        "ReplacementExecutor",
        "ReplacementPlanner",
        "ProcedureActivator",
    }
    assert _classes().isdisjoint(forbidden)


def test_no_competing_error_or_identifier_hierarchy() -> None:
    exception_bases: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name):
                exception_bases.add(base.id)

    assert "AgentXError" not in _classes()
    assert "AgentXException" not in _classes()
    assert {"ValueError"} <= exception_bases
    assert {"Exception", "BaseException", "AgentXError"}.isdisjoint(exception_bases)


def test_exactly_one_public_entry_point_and_no_mutation_verbs() -> None:
    module_level = {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }

    assert module_level == {"assess_procedure_replacement"}

    forbidden_functions = {
        "replace",
        "replace_procedure",
        "apply_replacement",
        "commit_replacement",
        "activate",
        "activate_procedure",
        "retire",
        "retire_procedure",
        "promote",
        "demote",
        "rollback",
        "rollback_procedure",
        "delete_revision",
        "remove_revision",
        "renumber",
        "rewrite",
        "mutate_procedure",
        "insert",
        "store",
        "save",
        "persist",
        "execute",
        "run",
        "shadow_run",
        "validate_repair",
        "grant",
        "revoke",
        "authorize",
        "approve",
        "verify",
        "select",
        "choose",
        "rank",
        "score",
        "predict",
        "infer",
        "classify",
        "diagnose",
        "query_store",
        "now",
    }
    assert module_level.isdisjoint(forbidden_functions)
    assert {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }.isdisjoint(forbidden_functions)


def test_records_expose_no_mutation_or_authority_methods() -> None:
    forbidden_methods = {
        "activate",
        "retire",
        "promote",
        "demote",
        "apply",
        "commit",
        "execute",
        "run",
        "delete",
        "remove",
        "renumber",
        "rewrite",
        "mutate",
        "save",
        "persist",
        "store",
        "grant",
        "revoke",
        "authorize",
        "approve",
        "verify",
        "select",
        "choose",
        "rank",
        "score",
        "replace",
        "rollback",
        "transition",
        "set_status",
        "update_status",
    }
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                assert statement.name not in forbidden_methods


@pytest.mark.parametrize(
    "record",
    [
        ProcedureReplacementDecision,
        ProcedureReplacementRequest,
        ProcedureReplacementEvidence,
    ],
)
def test_the_policy_records_are_frozen_immutable_snapshots(record: type[object]) -> None:
    assert dataclasses.is_dataclass(record)
    # ``vars`` avoids both an untyped attribute access and bugbear's B009.
    params = vars(record)["__dataclass_params__"]
    assert params.frozen is True
    assert params.slots is True


# ---------------------------------------------------------------------------
# Purity: no clock, I/O, network, randomness, or dynamic import
# ---------------------------------------------------------------------------


def test_no_side_effect_imports_or_calls() -> None:
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
        "datetime",
        "time",
        "uuid",
        "requests",
    }
    forbidden_calls = {
        "eval",
        "exec",
        "open",
        "urlopen",
        "execute",
        "commit",
        "rollback",
        "insert",
        "delete",
        "remove",
        "activate",
        "retire",
        "promote",
        "grant",
        "revoke",
        "patch",
        "escalate",
        "verify",
        "reason",
        "publish",
        "now",
        "today",
        "utcnow",
        "timestamp",
        "shuffle",
        "choice",
        "sample",
        "randint",
        "random",
        "seed",
        "findall",
        "search",
        "match",
        "fullmatch",
        "sub",
        "compile",
    }

    assert _imports().isdisjoint(forbidden_imports)
    assert "__import__" not in _called_names()
    assert _called_names().isdisjoint(forbidden_calls)


def test_the_module_never_reads_payload_or_timestamp_fields() -> None:
    """Content and clock data must be structurally unable to promote a revision."""
    accessed = _accessed_attributes()

    # These belong to ProcedureRecord; reading any of them would let content or
    # clock data influence a decision. (``kind``/``schema_version`` are this
    # module's own record fields and are deliberately not in scope here.)
    for forbidden in ("payload", "created_at", "updated_at", "content"):
        assert forbidden not in accessed

    for token in (".payload", ".created_at", ".updated_at"):
        assert token not in _SOURCE


def test_the_module_only_reads_structural_record_fields() -> None:
    accessed = _accessed_attributes()
    record_fields = {"procedure_id", "revision", "status", "scope", "dimensions"}

    assert record_fields <= accessed
    assert "active_revision" in accessed
    assert "target_revision" in accessed


def test_status_is_only_ever_compared_never_assigned() -> None:
    """The policy assesses lifecycle acts; it never performs one."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    assert target.attr != "status"
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__setattr__"
            and len(node.args) >= 2
        ):
            second = node.args[1]
            assert not (isinstance(second, ast.Constant) and second.value == "status")

    assert "ProcedureStatus.ACTIVE =" not in _SOURCE
    assert "ProcedureStatus.RETIRED =" not in _SOURCE
    assert "ProcedureStatus.CANDIDATE =" not in _SOURCE


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------


def test_the_replacement_kind_vocabulary_is_exactly_two_operations() -> None:
    assert [member.value for member in ProcedureReplacementKind] == [
        "forward_replacement",
        "rollback",
    ]


def test_the_decision_vocabulary_is_the_closed_five() -> None:
    assert {member.value for member in ProcedureReplacementOutcome} == {
        "eligible",
        "ineligible",
        "insufficient_evidence",
        "invalid_revision_relation",
        "wrong_procedure",
    }


def test_the_reason_vocabulary_is_closed_and_architecturally_justified() -> None:
    assert {member.value for member in ProcedureReplacementReason} == {
        "validated_repair",
        "environment_incompatibility",
        "manual_rollback",
        "regression_rollback",
        "safety_rollback",
    }


def test_no_invented_status_or_degraded_vocabulary() -> None:
    lowered = _SOURCE.lower()

    assert "degraded" not in lowered
    assert "class ProcedureStatus" not in _SOURCE
    assert "DEGRADED" not in _SOURCE
    assert "SUPERSEDED" not in _SOURCE


def test_findings_are_a_closed_structured_vocabulary_not_booleans() -> None:
    assert len(ProcedureReplacementFinding) == 16
    assert all(isinstance(member.value, str) for member in ProcedureReplacementFinding)


def test_evidence_and_attestation_vocabularies_fail_closed() -> None:
    assert ProcedureReplacementEvidence().validation_evidence.value == "absent"
    assert ProcedureReplacementEvidence().shadow_evidence.value == "absent"
    assert ProcedureReplacementEvidence().target_integrity.value == "unknown"
    assert ProcedureReplacementEvidence().evidence_references == ()
    assert RetiredTargetReactivation.NOT_ATTESTED.value == "not_attested"


# ---------------------------------------------------------------------------
# Hard file ownership: nothing else was touched
# ---------------------------------------------------------------------------


def test_the_canonical_procedure_record_contract_is_untouched() -> None:
    assert "class ProcedureRecord" in _PROCEDURES
    assert "class ProcedureStatus" in _PROCEDURES
    assert "procedure_replacement" not in _PROCEDURES
    assert "Replacement" not in _PROCEDURES
    assert "assess_procedure_replacement" not in _PROCEDURES


def test_the_procedure_store_is_untouched_and_owns_no_replacement_policy() -> None:
    assert "class ProcedureStore" in _PROCEDURE_STORE
    assert "procedure_replacement" not in _PROCEDURE_STORE
    assert "Replacement" not in _PROCEDURE_STORE
    assert "assess_procedure_replacement" not in _PROCEDURE_STORE
    assert "Rollback" not in _PROCEDURE_STORE


def test_no_package_init_was_modified_for_this_task() -> None:
    for init in _SRC.rglob("__init__.py"):
        text = init.read_text(encoding="utf-8")
        assert "procedure_replacement" not in text, init


def test_no_unmerged_worker_module_is_referenced() -> None:
    """The policy depends on nothing outside the canonical baseline core."""
    for token in (
        "repair_validation",
        "shadow_repair",
        "repair_budget",
        "patch_generation",
        "worker_17",
        "worker_18",
        "worker-17",
        "worker-18",
    ):
        assert token not in _SOURCE


def test_the_module_defines_no_model_research_or_network_surface() -> None:
    # Prose that *explains* the ban is allowed; the AST guard in
    # ``test_no_side_effect_imports_or_calls`` is what proves the imports and
    # calls themselves are absent.
    lowered = _SOURCE.lower()

    for token in (
        "openai",
        "anthropic",
        "model_provider",
        "tokenizer",
        "embeddings",
        "vector_store",
        "confidence=",
        "probability=",
        "score=",
        "weight=",
        "likelihood",
    ):
        assert token not in lowered


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------


def test_docs_page_exists_and_states_the_policy_contract() -> None:
    docs = _REPO_ROOT / "docs" / "procedure_replacement.md"
    assert docs.is_file()

    text = docs.read_text(encoding="utf-8")
    for token in (
        "M5.06",
        "C4.08",
        "FORWARD_REPLACEMENT",
        "ROLLBACK",
        "ELIGIBLE",
        "INSUFFICIENT_EVIDENCE",
        "INVALID_REVISION_RELATION",
        "WRONG_PROCEDURE",
        "append-only",
        "Eligibility is not execution permission",
        "Atomicity a future transaction must satisfy",
        "No silent replacement",
        "History preservation",
    ):
        assert token in text
