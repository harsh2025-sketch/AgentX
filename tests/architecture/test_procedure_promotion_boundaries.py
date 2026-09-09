"""Architecture guardrails for the N2.10 procedure-promotion transaction.

These are import/structure guardrails, not security enforcement. Authority is
owned exclusively by the Trusted Kernel; the promotion transaction is a pure
composition boundary that applies already-canonical decisions to persistence.

The transaction is a top-level composition module (alongside
``agentx.procedure_validation``): it must read lifecycle/record contracts from
``agentx.core``, the eligibility report from ``agentx.procedure_validation``,
and commit through ``agentx.infrastructure.procedure_store`` — without
widening the boundary manifest, reaching any authority, executing anything,
or hand-rolling its own SQL.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from agentx import _architecture
from agentx.core.procedures import ProcedureStatus
from agentx.infrastructure.procedure_store import (
    ProcedureStaleRecordError,
    ProcedureStore,
    ProcedureStoreError,
)
from agentx.procedure_promotion import (
    ProcedurePromotionOutcome,
    ProcedurePromotionRequest,
    ProcedurePromotionResult,
)
from agentx.procedure_validation import ValidationDecision

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_MODULE_PATH = _AGENTX_SRC / "procedure_promotion.py"


def _tree() -> ast.Module:
    return ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))


def _imports() -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(imported)


def _call_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            target = node.func
            names.add(
                target.id
                if isinstance(target, ast.Name)
                else (target.attr if isinstance(target, ast.Attribute) else "")
            )
    return names


# ---------------------------------------------------------------------------
# Placement: top-level composition, no subsystem, no manifest widening.
# ---------------------------------------------------------------------------


def test_transaction_is_a_single_top_level_composition_module() -> None:
    assert _MODULE_PATH.is_file()
    assert _MODULE_PATH.parent == _AGENTX_SRC


def test_transaction_is_not_inside_any_canonical_subsystem() -> None:
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        assert not _MODULE_PATH.is_relative_to(package)


def test_transaction_did_not_create_a_new_subsystem() -> None:
    assert "agentx.procedure_promotion" not in _architecture.SUBSYSTEMS
    assert not (_AGENTX_SRC / "procedure_promotion").exists()
    assert _architecture.SUBSYSTEMS == (
        "agentx.core",
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.hive",
        "agentx.procedures",
        "agentx.cognition",
        "agentx.learning",
        "agentx.infrastructure",
    )


def test_boundary_manifest_was_not_widened() -> None:
    assert (
        frozenset(
            {
                (_architecture.KERNEL, _architecture.CORE),
                (_architecture.CAPABILITIES, _architecture.CORE),
                (_architecture.HIVE, _architecture.CORE),
                (_architecture.PROCEDURES, _architecture.CORE),
                (_architecture.COGNITION, _architecture.CORE),
                (_architecture.LEARNING, _architecture.CORE),
                (_architecture.INFRASTRUCTURE, _architecture.CORE),
                (_architecture.CAPABILITIES, _architecture.KERNEL),
                (_architecture.PROCEDURES, _architecture.KERNEL),
                (_architecture.COGNITION, _architecture.KERNEL),
            }
        )
        == _architecture.ALLOWED_ARCHITECTURE_EDGES
    )


# ---------------------------------------------------------------------------
# Import boundary: core + the canonical store + the canonical report only.
# ---------------------------------------------------------------------------


def test_transaction_imports_only_canonical_contracts() -> None:
    imports = _imports()
    agentx_imports = [module for module in imports if module.startswith("agentx.")]
    assert agentx_imports, "transaction must consume canonical contracts"
    for module in agentx_imports:
        assert (
            module.startswith("agentx.core.")
            or module == "agentx.infrastructure.procedure_store"
            or module == "agentx.procedure_validation"
        ), f"unexpected canonical import: {module}"


def test_transaction_imports_no_authority_or_governed_mechanism() -> None:
    imports = set(_imports())
    for forbidden in (
        "agentx.kernel",
        "agentx.kernel.permissions",
        "agentx.kernel.action_gate",
        "agentx.kernel.risk",
        "agentx.kernel.resource_budget",
        "agentx.kernel.emergency_stop",
        "agentx.kernel.audit",
    ):
        assert not any(module == forbidden for module in imports), forbidden


def test_transaction_imports_no_model_research_or_execution_machinery() -> None:
    imports = set(_imports())
    for forbidden in (
        "agentx.cognition",
        "agentx.learning",
        "agentx.hive",
        "agentx.procedures",
        "agentx.capabilities",
        "agentx.capabilities.executor",
        "agentx.capabilities.registry",
    ):
        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.") for module in imports
        ), forbidden


def test_transaction_never_touches_sql_or_concurrency_primitives() -> None:
    imports = set(_imports())
    for forbidden in (
        "sqlite3",
        "agentx.infrastructure.persistence",
        "threading",
        "multiprocessing",
        "subprocess",
        "socket",
        "asyncio",
        "ctypes",
    ):
        assert not any(module == forbidden for module in imports), forbidden


# ---------------------------------------------------------------------------
# The transaction must reuse the canonical policies, never re-decide them.
# ---------------------------------------------------------------------------


def test_transaction_reuses_the_canonical_lifecycle_policy() -> None:
    assert "assess_procedure_transition" in _call_names(), (
        "the transaction must derive its lifecycle verdict from the canonical M4.03 policy"
    )


def test_transaction_commits_only_through_the_compare_and_set_seam() -> None:
    call_names = _call_names()
    assert "update_status_if_current" in call_names, (
        "the only write path is the store's compare-and-set seam"
    )
    forbidden_writes = {"update_status", "insert", "execute", "delete", "drop"}
    assert not (call_names & forbidden_writes), call_names & forbidden_writes


def test_transaction_checks_both_canonical_positive_gates() -> None:
    source = _MODULE_PATH.read_text(encoding="utf-8")
    tree = _tree()
    attribute_names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "ELIGIBLE_FOR_PROMOTION" in attribute_names, (
        "the validation gate must be checked against the canonical enum member"
    )
    assert "ALLOWED" in attribute_names, (
        "the lifecycle gate must be checked against the canonical enum member"
    )
    assert "CANDIDATE" in attribute_names and "ACTIVE" in attribute_names
    assert "approved" not in source or "approved=True" not in source


def test_transaction_has_no_module_level_side_effects() -> None:
    forbidden_calls = {"open", "eval", "exec", "compile", "__import__", "system", "popen"}
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else (target.attr if isinstance(target, ast.Attribute) else "")
            )
            assert name not in forbidden_calls, name


def test_transaction_declares_its_public_surface() -> None:
    tree = _tree()
    exported = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            exported = node
    assert exported is not None, "the transaction must declare __all__"


# ---------------------------------------------------------------------------
# The vocabularies stay disjoint: ELIGIBLE != ACTIVE, PROMOTED != a status.
# ---------------------------------------------------------------------------


def test_eligible_and_promoted_are_never_lifecycle_statuses() -> None:
    status_values = {member.value for member in ProcedureStatus}
    decision_values = {member.value for member in ValidationDecision}
    outcome_values = {member.value for member in ProcedurePromotionOutcome}
    assert decision_values.isdisjoint(status_values)
    assert outcome_values.isdisjoint(status_values)


def test_outcome_vocabulary_is_bounded_and_explicit() -> None:
    members = {member.name for member in ProcedurePromotionOutcome}
    assert members == {
        "PROMOTED",
        "REJECTED_PROCEDURE_ABSENT",
        "REJECTED_IDENTITY_MISMATCH",
        "REJECTED_REVISION_MISMATCH",
        "REJECTED_VALIDATION_EVIDENCE_FOREIGN",
        "REJECTED_VALIDATION_NOT_ELIGIBLE",
        "REJECTED_LIFECYCLE_DECISION_INVALID",
        "REJECTED_REPEATED_PROMOTION",
        "REJECTED_RETIRED_TERMINAL",
        "REJECTED_LIFECYCLE_REJECTED",
        "REJECTED_STALE_CURRENT_RECORD",
    }


def test_request_requires_canonical_typed_evidence_not_booleans() -> None:
    request_fields = set(ProcedurePromotionRequest.__dataclass_fields__)
    assert {
        "procedure_id",
        "revision",
        "expected_current",
        "validation_report",
        "lifecycle_assessment",
        "requested_at",
    } == request_fields
    result_fields = set(ProcedurePromotionResult.__dataclass_fields__)
    assert "outcome" in result_fields  # a typed enum, never a naked bool
    assert isinstance(ProcedurePromotionResult.promoted, property)


# ---------------------------------------------------------------------------
# The compare-and-set store seam stays narrow and fail-closed.
# ---------------------------------------------------------------------------


def test_store_exposes_the_compare_and_set_seam() -> None:
    signature = inspect.signature(ProcedureStore.update_status_if_current)
    parameters = signature.parameters
    assert "expected_current" in parameters
    assert parameters["expected_current"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["expected_current"].default is inspect.Parameter.empty


def test_stale_record_error_is_a_typed_store_error() -> None:
    assert issubclass(ProcedureStaleRecordError, ProcedureStoreError)
