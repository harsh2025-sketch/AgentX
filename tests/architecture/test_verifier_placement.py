"""Architecture guardrails for the A2.05 Verifier boundary.

The Verifier is a *boundary*, not an authority. These static checks prove the
properties that runtime tests cannot: that the boundary exists in exactly one
place, imports only canonical contracts, reaches no cognition/kernel
subsystem, invokes no capability verification or execution, duplicates no
governed mechanism, transitions no Task, publishes no evidence, and adds no
dependency.

Import-level rules are architecture guardrails, not security enforcement;
authority remains owned by the Trusted Kernel.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_VERIFIER_MODULE = _AGENTX_SRC / "capabilities" / "verifier.py"
_ABI_MODULE = _AGENTX_SRC / "capabilities" / "abi.py"
_RUNTIME_MODULE = _AGENTX_SRC / "capabilities" / "runtime.py"


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _defined_classes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def _defined_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _called_names(path: Path) -> set[str]:
    """Names of every called function/method in the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


# ---------------------------------------------------------------------------
# Canonical placement and contract inventory.
# ---------------------------------------------------------------------------


def test_verifier_is_a_single_canonical_module() -> None:
    """The Verifier exists in exactly one place with one entry type."""
    assert _VERIFIER_MODULE.is_file()

    definitions = [
        path for path in _AGENTX_SRC.rglob("*.py") if "Verifier" in _defined_classes(path)
    ]
    assert definitions == [_VERIFIER_MODULE]


def test_verifier_contract_types_are_canonical_and_complete() -> None:
    """The boundary defines exactly its four contract types plus helpers."""
    classes = _defined_classes(_VERIFIER_MODULE)
    assert {
        "Verifier",
        "VerifierRequest",
        "VerificationRequirement",
        "RequirementEvaluation",
        "VerifierRequestError",
    } <= classes


def test_verifier_stays_inside_the_capabilities_boundary() -> None:
    """A2.05 adds no top-level package and widens no manifest edge."""
    assert _VERIFIER_MODULE.is_relative_to(_AGENTX_SRC / "capabilities")
    assert _architecture.SUBSYSTEMS == (
        _architecture.CORE,
        _architecture.KERNEL,
        _architecture.CAPABILITIES,
        _architecture.HIVE,
        _architecture.PROCEDURES,
        _architecture.COGNITION,
        _architecture.LEARNING,
        _architecture.INFRASTRUCTURE,
    )
    for source, target in _architecture.ALLOWED_ARCHITECTURE_EDGES:
        assert source != _architecture.CAPABILITIES or target in (
            _architecture.CORE,
            _architecture.KERNEL,
        )


def test_verifier_imports_only_canonical_contracts() -> None:
    """Every agentx import of the Verifier is a canonical contract."""
    agentx_imports = {
        module for module in _imported_modules(_VERIFIER_MODULE) if module.startswith("agentx")
    }
    allowed_siblings = {"agentx.capabilities.abi", "agentx.capabilities.runtime"}
    for module in agentx_imports:
        assert module in allowed_siblings or module.startswith("agentx.core."), (
            f"verifier imports non-canonical module {module}"
        )

    for forbidden in (
        "agentx.infrastructure",
        "agentx.cognition",
        "agentx.hive",
        "agentx.procedures",
        "agentx.learning",
    ):
        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.") for module in agentx_imports
        ), f"verifier must not import {forbidden}"


def test_verifier_imports_the_canonical_a110_outcome_contracts() -> None:
    """Reuse is structural: the Verifier reads canonical A1.10/A1.08 types."""
    imported = _imported_modules(_VERIFIER_MODULE)
    assert "agentx.capabilities.runtime" in imported
    assert "agentx.capabilities.abi" in imported
    assert "ClosedLoopOutcome" in _defined_classes(_RUNTIME_MODULE)
    assert "VerificationResult" in _defined_classes(_ABI_MODULE)


def test_verifier_never_imports_the_kernel() -> None:
    """No kernel import means no path to authority, risk, budget, or stop."""
    imported = _imported_modules(_VERIFIER_MODULE)
    kernel_imports = [module for module in imported if module.startswith("agentx.kernel")]
    assert kernel_imports == []


# ---------------------------------------------------------------------------
# No second verification path: no capability invocation of any kind.
# ---------------------------------------------------------------------------


def test_verifier_never_invokes_capability_verify_or_execute() -> None:
    """No Capability.verify call, no execute call, anywhere in the module."""
    called = _called_names(_VERIFIER_MODULE)
    for forbidden in ("verify", "execute", "run", "require", "resolve", "check_and_consume"):
        assert forbidden not in called, f"verifier must not call {forbidden}()"

    source = _VERIFIER_MODULE.read_text(encoding="utf-8")
    assert "def verify" not in source
    assert "def execute" not in source
    assert "def run" not in source


def test_verifier_defines_no_execution_or_verification_mechanism() -> None:
    """A2.05 redefines nothing owned by A1.08/A1.10 or the kernel."""
    verifier_classes = _defined_classes(_VERIFIER_MODULE)
    for forbidden in (
        "Capability",
        "CapabilityExecutionLoop",
        "CapabilityRegistry",
        "VerificationResult",
        "ClosedLoopOutcome",
        "LoopOutcome",
        "ExecutionResult",
        "CapabilityObservation",
        "ActionGate",
        "PermissionEngine",
        "AuthorityContext",
        "ResourceBudget",
        "EmergencyStop",
        "Event",
        "SecurityAuditRecord",
        "Task",
    ):
        assert forbidden not in verifier_classes, f"{forbidden} must not be redefined by A2.05"


def test_verifier_manufactures_no_verdict_and_no_outcome() -> None:
    """It reads verdicts; it never constructs them."""
    called = _called_names(_VERIFIER_MODULE)
    for forbidden in ("VerificationResult", "ClosedLoopOutcome", "CapabilityObservation"):
        assert forbidden not in called, f"verifier must not construct {forbidden}"


def test_verifier_performs_no_task_transition_or_state_mutation() -> None:
    """No Task state machine, no transition, no Task field on the request."""
    imported = set(_imported_modules(_VERIFIER_MODULE))
    assert "agentx.core.task_state" not in imported

    called = _called_names(_VERIFIER_MODULE)
    for forbidden in ("try_transition_task", "transition_task", "validate_transition"):
        assert forbidden not in called, f"verifier must not call {forbidden}"


def test_verifier_touches_no_authority_or_evidence_module_directly() -> None:
    """Governed mechanisms are never reached directly by A2.05."""
    imported = set(_imported_modules(_VERIFIER_MODULE))
    forbidden = {
        "agentx.kernel.action_gate",
        "agentx.kernel.permissions",
        "agentx.kernel.resource_budget",
        "agentx.kernel.emergency_stop",
        "agentx.kernel.risk",
        "agentx.kernel.audit",
        "agentx.capabilities.registry",
        "agentx.capabilities.executor",
        "agentx.core.task_state",
        "agentx.core.events",
        "agentx.infrastructure.event_bus",
    }
    assert forbidden.isdisjoint(imported)


# ---------------------------------------------------------------------------
# No cognition, no retry/fallback/routing vocabulary.
# ---------------------------------------------------------------------------


def test_verifier_imports_no_reasoner_or_model_symbol() -> None:
    """A2.05 is deterministic evaluation: zero cognition dependency."""
    imported = _imported_modules(_VERIFIER_MODULE)
    assert not any(module.startswith("agentx.cognition") for module in imported)

    source = _VERIFIER_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    for forbidden in (
        "Reasoner",
        "ModelProvider",
        "ModelRole",
        "ModelRequest",
        "ModelResponse",
        "LLM",
        "Hive",
    ):
        assert forbidden not in identifiers, f"verifier must not reference {forbidden}"


def test_verifier_accepts_no_callables_or_text_conditions() -> None:
    """Predicates are typed data only: no Callable import, no prompt field."""
    imported = _imported_modules(_VERIFIER_MODULE)
    assert "collections.abc" in imported  # Mapping only
    assert not any("Callable" in module for module in imported)

    source = _VERIFIER_MODULE.read_text(encoding="utf-8")
    assert "Callable" not in source
    assert "prompt" not in source.lower()


def test_verifier_declares_no_retry_fallback_or_routing_vocabulary() -> None:
    """No retry, fallback, router, escalation, planning, or task manager."""
    names = _defined_classes(_VERIFIER_MODULE) | _defined_functions(_VERIFIER_MODULE)
    lowered = {name.lower() for name in names}
    for forbidden in (
        "retry",
        "fallback",
        "router",
        "route",
        "escalate",
        "escalation",
        "taskmanager",
        "executor",
        "plan",
        "planner",
        "strategy",
        "select",
        "repair",
        "research",
        "critic",
    ):
        assert not any(forbidden in name for name in lowered), (
            f"A2.05 must not define {forbidden!r} behaviour"
        )


def test_verifier_has_no_hidden_retry_control_flow() -> None:
    """No while loops and no try/except: nothing is swallowed or repeated."""
    source = _VERIFIER_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.While)], (
        "the verifier performs no unbounded iteration"
    )
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Try)], (
        "the verifier swallows nothing: evidence problems are unmet conditions"
    )


def test_verifier_is_deterministic_and_does_no_io() -> None:
    """No clock, randomness, subprocess, socket, dynamic loading, or print."""
    imported = _imported_modules(_VERIFIER_MODULE)
    assert {
        "importlib",
        "pkgutil",
        "subprocess",
        "socket",
        "urllib",
        "sqlite3",
        "time",
        "random",
        "asyncio",
        "threading",
    }.isdisjoint(imported)

    source = _VERIFIER_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"eval", "exec", "compile", "__import__", "open", "print"}.isdisjoint(called)


# ---------------------------------------------------------------------------
# Zero new dependencies.
# ---------------------------------------------------------------------------


def test_no_new_runtime_dependencies_for_a205() -> None:
    """The runtime package keeps its zero third-party dependency contract."""
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []
    assert pyproject["project"]["optional-dependencies"]["dev"] == [
        "pytest>=8.0",
        "ruff>=0.5",
        "mypy>=1.10",
    ]


def test_verifier_uses_only_the_standard_library_externally() -> None:
    """Non-agentx imports are standard library only."""
    stdlib_allowed = {"__future__", "math", "collections.abc", "dataclasses", "types", "typing"}
    for module in _imported_modules(_VERIFIER_MODULE):
        if module.startswith("agentx"):
            continue
        assert module in stdlib_allowed, f"unexpected external import {module}"
