"""Architecture/security placement tests for A2.09 anti-loop."""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "cognition" / "anti_loop.py"


def _tree() -> ast.Module:
    return ast.parse(_MODULE.read_text(encoding="utf-8"))


def _imports() -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def _defined_classes() -> set[str]:
    return {node.name for node in _tree().body if isinstance(node, ast.ClassDef)}


def _defined_functions() -> set[str]:
    return {node.name for node in _tree().body if isinstance(node, ast.FunctionDef)}


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


def test_anti_loop_is_in_cognition_and_preserves_canonical_boundary() -> None:
    assert _MODULE.exists()
    assert (_architecture.COGNITION, _architecture.CORE) in _architecture.ALLOWED_ARCHITECTURE_EDGES
    assert (_architecture.COGNITION, _architecture.KERNEL) in _architecture.ALLOWED_ARCHITECTURE_EDGES

    # A2.09 currently needs neither edge: its decision engine is a pure
    # cognition-local/stdlib contract and therefore introduces no new edge.
    assert not any(module.startswith("agentx.") for module in _imports())


def test_anti_loop_has_zero_runtime_dependencies() -> None:
    assert _imports() <= {
        "__future__",
        "re",
        "dataclasses",
        "enum",
        "typing",
    }


def test_anti_loop_does_not_depend_on_concurrent_a2_08() -> None:
    imports = _imports()
    assert not any("escalat" in module.lower() for module in imports)
    assert not any("fallback" in module.lower() for module in imports)

    class_names = {name.lower() for name in _defined_classes()}
    assert not any("escalat" in name for name in class_names)
    assert not any("fallback" in name for name in class_names)


def test_anti_loop_defines_only_bounded_data_and_evaluation_surface() -> None:
    assert _defined_classes() == {
        "AttemptFingerprint",
        "OutcomeFingerprint",
        "ProgressFingerprint",
        "AttemptEvidence",
        "LoopGuardLimits",
        "LoopGuardDecision",
        "LoopGuardTrigger",
        "LoopGuardResult",
        "LoopGuard",
    }
    assert _defined_functions() == {
        "_validate_fingerprint",
        "_validate_limit",
        "_validate_count",
    }


def test_no_execution_retry_escalation_model_research_or_repair_calls() -> None:
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
        "sleep",
        "poll",
        "publish",
    }
    assert calls.isdisjoint(forbidden)


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
    )
    assert not any(module.startswith(forbidden_prefixes) for module in imports)

    source = _MODULE.read_text(encoding="utf-8")
    for token in (
        "EventBus",
        "ProcedureStore",
        "KnowledgeStore",
        "ModelProvider",
        "Reasoner",
        "CapabilityRegistry",
        "TaskManager",
    ):
        assert token not in source


def test_no_authority_risk_budget_or_verification_contract_is_imported() -> None:
    tree = _tree()
    referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    forbidden = {
        "Permission",
        "AuthorityContext",
        "ActionGate",
        "GateDecision",
        "RiskLevel",
        "RiskAssessment",
        "ResourceEnvelope",
        "ResourceBudget",
        "EmergencyStop",
        "VerificationResult",
        "KnowledgeStatus",
        "TaskStatus",
    }
    assert referenced.isdisjoint(forbidden)


def test_no_global_mutable_guard_state() -> None:
    tree = _tree()
    module_assignments = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    # Only module constants/pattern configuration exist.  No history, counter,
    # cache, singleton guard, lock, or runtime state is retained globally.
    rendered = "\n".join(ast.unparse(node) for node in module_assignments)
    for token in ("history", "counter", "cache", "singleton", "lock", "state"):
        assert token not in rendered.lower()
