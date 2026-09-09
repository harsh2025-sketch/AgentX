"""Architecture guards for the C4.09 / M5.03 bounded repair-attempt policy.

The repair-budget module is a pure inward ``agentx.core`` loop-policy
contract. These static guards prove it stays that way: no outward subsystem
imports, no second resource-accounting system, no generic anti-loop
replacement, no I/O, no dynamic execution, no persistence, no authority
surface, no global mutable state, and no wiring into any runtime component.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture
from agentx.cognition import anti_loop
from agentx.core import repair_budget
from agentx.kernel import resource_budget

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "core" / "repair_budget.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_SRC_ROOT = _REPO_ROOT / "src"


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


def _functions() -> set[str]:
    return {
        node.name
        for node in _tree().body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
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


def _referenced_names() -> set[str]:
    return {node.id for node in ast.walk(_tree()) if isinstance(node, ast.Name)}


def test_contract_lives_in_core_and_is_an_inward_leaf() -> None:
    assert _MODULE.is_file()
    assert repair_budget.__name__ == "agentx.core.repair_budget"

    agentx_imports = {name for name in _imports() if name.startswith("agentx")}

    # The only agentx dependency is the canonical core identifier contract.
    assert agentx_imports == {"agentx.core.ids"}
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


def test_no_new_architecture_edge_is_required_or_declared() -> None:
    # The module needs no outward edge, and the canonical manifest still
    # declares none for core: core stays an inward leaf for every subsystem.
    assert all(
        source != _architecture.CORE for source, _target in _architecture.ALLOWED_ARCHITECTURE_EDGES
    )
    assert _architecture.CORE in _architecture.SUBSYSTEMS


def test_contract_uses_only_inert_standard_library_modules() -> None:
    non_agentx = {name.split(".")[0] for name in _imports() if not name.startswith("agentx")}

    assert non_agentx <= {
        "__future__",
        "re",
        "dataclasses",
        "enum",
        "typing",
    }


def test_no_network_filesystem_thread_scheduler_or_persistence_surface() -> None:
    imports = _imports()
    forbidden_prefixes = (
        "requests",
        "urllib",
        "http",
        "socket",
        "webbrowser",
        "pathlib",
        "subprocess",
        "threading",
        "asyncio",
        "sqlite3",
        "json",
        "pickle",
        "shelve",
        "dbm",
    )
    assert not any(module.startswith(prefix) for module in imports for prefix in forbidden_prefixes)

    # Docstrings may name forbidden systems while documenting the boundary;
    # executable AST must never reference them.
    referenced = _referenced_names()
    assert referenced.isdisjoint(
        {
            "EventBus",
            "ProcedureStore",
            "KnowledgeStore",
            "ModelProvider",
            "Reasoner",
            "CapabilityRegistry",
            "TaskManager",
            "Lock",
            "Thread",
            "sleep",
        }
    )


def test_no_dynamic_execution_or_io_primitives_are_referenced() -> None:
    assert _referenced_names().isdisjoint(
        {
            "eval",
            "exec",
            "compile",
            "__import__",
            "open",
            "input",
            "print",
            "globals",
            "locals",
            "setattr",
            "delattr",
        }
    )


def test_module_defines_exactly_the_bounded_policy_surface() -> None:
    assert _classes() == {
        "RepairBudgetValidationError",
        "RepairProposalFingerprint",
        "RepairProgressMarker",
        "RepairTargetFingerprint",
        "ProcedureRepairTarget",
        "RepairTarget",
        "RepairAttemptOutcome",
        "RepairAttemptEvidence",
        "RepairBudgetScope",
        "RepairBudgetLimits",
        "RepairBudgetDecision",
        "RepairBudgetAssessment",
    }
    assert _functions() == {
        "_validate_token",
        "_validate_limit",
        "_validate_count",
        "_validate_revision",
        "_invalid_history",
        "_history_violation",
        "_demonstrated_progress",
        "assess_repair_attempt",
    }
    assert set(repair_budget.__all__) == {
        "CANONICAL_REPAIR_ATTEMPT_OUTCOMES",
        "RepairAttemptEvidence",
        "RepairAttemptOutcome",
        "RepairBudgetAssessment",
        "RepairBudgetDecision",
        "RepairBudgetLimits",
        "RepairBudgetScope",
        "RepairBudgetValidationError",
        "RepairProgressMarker",
        "RepairProposalFingerprint",
        "RepairTarget",
        "RepairTargetFingerprint",
        "ProcedureRepairTarget",
        "assess_repair_attempt",
    }


def test_no_resource_budget_or_envelope_redefinition() -> None:
    # The canonical C1.08 kernel resource-accounting vocabulary stays unique:
    # this module defines none of it and no competing budget system.
    kernel_surface = set(resource_budget.__all__)
    assert _classes().isdisjoint(kernel_surface)
    assert "resource" not in {name.lower() for name in _classes()}
    referenced = _referenced_names()
    assert referenced.isdisjoint(
        {
            "ResourceBudget",
            "ResourceEnvelope",
            "ResourceUsage",
            "ResourceRequest",
            "ResourceDelta",
            "BudgetEvaluator",
            "BudgetResult",
            "BudgetDecision",
        }
    )


def test_no_generic_anti_loop_replacement() -> None:
    # The canonical A2.09 cognition anti-loop stays the generic guard; this
    # module defines none of its vocabulary and replaces nothing.
    cognition_surface = set(anti_loop.__all__)
    assert _classes().isdisjoint(cognition_surface)
    assert "LoopGuard" not in _referenced_names()


def test_no_execution_retry_repair_or_authority_calls() -> None:
    calls = {name.lower() for name in _called_names()}
    forbidden = {
        "execute",
        "retry",
        "escalate",
        "fallback",
        "generate",
        "complete",
        "invoke",
        "reason",
        "research",
        "browse",
        "fetch",
        "repair",
        "transition",
        "verify",
        "apply",
        "patch",
        "sleep",
        "poll",
        "publish",
        "emit",
        "persist",
        "save",
        "load",
        "connect",
        "send",
        "grant",
        "approve",
        "authorize",
        "consume",
    }
    assert calls.isdisjoint(forbidden)

    referenced = _referenced_names()
    assert referenced.isdisjoint(
        {
            "Permission",
            "AuthorityContext",
            "ActionGate",
            "GateRequest",
            "GateDecision",
            "RiskLevel",
            "RiskAssessment",
            "EmergencyStop",
            "VerificationResult",
            "TaskStatus",
            "Capability",
            "Procedure",
            "Hive",
        }
    )


def test_no_global_mutable_policy_state() -> None:
    tree = _tree()
    module_assignments = [
        node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    # Only module constants/pattern configuration exist. No history, counter,
    # cache, singleton guard, lock, or runtime state is retained globally.
    rendered = "\n".join(ast.unparse(node) for node in module_assignments)
    for token in ("history", "counter", "cache", "singleton", "lock", "state"):
        assert token not in rendered.lower()
    assert len(module_assignments) == 5  # __all__ + three constants + vocabulary tuple


def test_only_the_canonical_repair_orchestrator_consumes_the_policy() -> None:
    # The only permitted consumer is the N2.14 top-level repair workflow
    # orchestrator, which composes this policy as its bounded anti-loop stage
    # without re-implementing, resetting, or widening it. No executor, kernel,
    # capability, or runtime component is wired to it.
    allowed = {"src/agentx/repair_workflow.py"}
    importers = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        if path == _MODULE:
            continue
        source = path.read_text(encoding="utf-8")
        if "repair_budget" in source:
            importers.append(path.relative_to(_REPO_ROOT).as_posix())
    assert set(importers) <= allowed
