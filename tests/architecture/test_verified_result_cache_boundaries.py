"""Architecture guardrails for the A8.07 verified-result cache.

The cache is a *boundary around evidence*, not an authority and not a second
execution path. These static checks prove the properties runtime tests cannot:
that the cache exists in exactly one place, imports only canonical contracts,
reaches no kernel/cognition/hive/infrastructure subsystem, executes and verifies
nothing, wires nothing into the Router or the closed loop, manufactures no
verdict, persists nothing, spawns no background maintenance, implements no
A8.08 decay, and adds no dependency.

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

_MODULE = _AGENTX_SRC / "capabilities" / "verified_result_cache.py"
_ABI_MODULE = _AGENTX_SRC / "capabilities" / "abi.py"
_RUNTIME_MODULE = _AGENTX_SRC / "capabilities" / "runtime.py"
_ROUTER_MODULE = _AGENTX_SRC / "cognition" / "router.py"
_EXECUTOR_MODULE = _AGENTX_SRC / "capabilities" / "executor.py"
_VERIFIER_MODULE = _AGENTX_SRC / "capabilities" / "verifier.py"


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


def _called_functions(path: Path) -> set[str]:
    """Names of every directly called function (never a method)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _called_chains(path: Path) -> set[str]:
    """Dotted call targets such as ``hashlib.sha256`` or ``json.dumps``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    chains: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            value = node.func.value
            if isinstance(value, ast.Name):
                chains.add(f"{value.id}.{node.func.attr}")
    return chains


def _source() -> str:
    return _MODULE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Canonical placement and contract inventory.
# ---------------------------------------------------------------------------


def test_the_cache_is_a_single_canonical_module() -> None:
    """Verified-result caching exists in exactly one place."""
    assert _MODULE.is_file()

    definitions = [
        path
        for path in sorted(_AGENTX_SRC.rglob("*.py"))
        if "VerifiedResultCache" in _defined_classes(path)
    ]
    assert definitions == [_MODULE]

    entries = [
        path
        for path in sorted(_AGENTX_SRC.rglob("*.py"))
        if "VerifiedResultEntry" in _defined_classes(path)
    ]
    assert entries == [_MODULE]


def test_the_cache_contract_types_are_canonical_and_complete() -> None:
    """The boundary defines exactly its contract types plus private helpers."""
    classes = _defined_classes(_MODULE)
    assert {
        "VerifiedResultCache",
        "VerifiedResultKey",
        "VerifiedResultEntry",
        "VerifiedResultLookup",
        "VerifiedResultSubmission",
        "VerifiedResultProvenance",
        "EnvironmentIdentity",
        "ProcedureRevision",
        "InvalidationRecord",
        "InvalidationCause",
        "EntryState",
        "LookupStatus",
        "VerifiedResultCacheError",
        "VerifiedResultCacheValidationError",
        "VerifiedResultCacheRejection",
        "VerifiedResultCacheCapacityError",
        "VerifiedResultCacheClockError",
    } <= classes


def test_the_lookup_vocabulary_is_exactly_the_five_required_statuses() -> None:
    """MISS / VERIFIED_HIT / STALE / INVALIDATED / INCOMPATIBLE and nothing else."""
    source = _source()
    start = source.index("class LookupStatus(StrEnum):")
    end = source.index("@dataclass", start)
    body = source[start:end]

    for required in ("MISS", "VERIFIED_HIT", "STALE", "INVALIDATED", "INCOMPATIBLE"):
        assert f"{required} = " in body, required
    assert body.count(' = "') == 5


def test_the_cache_stays_inside_the_capabilities_boundary() -> None:
    """A8.07 adds no top-level package and widens no manifest edge."""
    assert _MODULE.is_relative_to(_AGENTX_SRC / "capabilities")
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


def test_the_cache_imports_only_canonical_contracts() -> None:
    """Every agentx import is a canonical A1.08/A1.10 sibling or a core contract."""
    agentx_imports = {
        module for module in _imported_modules(_MODULE) if module.startswith("agentx")
    }
    allowed_siblings = {"agentx.capabilities.abi", "agentx.capabilities.runtime"}
    for module in agentx_imports:
        assert module in allowed_siblings or module.startswith("agentx.core."), (
            f"verified_result_cache imports non-canonical module {module}"
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
        ), f"verified_result_cache must not import {forbidden}"


def test_the_cache_consumes_the_canonical_typed_evidence_contracts() -> None:
    """Reuse is structural: the cache stores canonical A1.08/A1.10 values."""
    imported = _imported_modules(_MODULE)
    assert "agentx.capabilities.runtime" in imported
    assert "agentx.capabilities.abi" in imported
    assert "agentx.core.execution" in imported
    assert "agentx.core.ids" in imported
    assert "agentx.core.tasks" in imported

    source = _source()
    for name in (
        "ClosedLoopOutcome",
        "LoopOutcome",
        "VerificationResult",
        "CapabilityObservation",
        "ExecutionResult",
        "CapabilityIdentity",
        "CapabilityScope",
        "ExecutionContext",
        "TaskStatus",
    ):
        assert name in source, f"A8.07 must consume the canonical {name} contract"

    assert "ClosedLoopOutcome" in _defined_classes(_RUNTIME_MODULE)
    assert "LoopOutcome" in _defined_classes(_RUNTIME_MODULE)
    assert "VerificationResult" in _defined_classes(_ABI_MODULE)


def test_the_cache_never_imports_the_kernel() -> None:
    """No kernel import means no path to authority, risk, budget, or stop."""
    imported = _imported_modules(_MODULE)
    assert [module for module in imported if module.startswith("agentx.kernel")] == []


# ---------------------------------------------------------------------------
# No second execution or verification path.
# ---------------------------------------------------------------------------


def test_the_cache_never_executes_or_verifies_a_capability() -> None:
    called = _called_names(_MODULE)
    for forbidden in ("execute", "verify", "run", "require", "resolve", "check_and_consume"):
        assert forbidden not in called, f"verified_result_cache must not call {forbidden}()"

    source = _source()
    assert "def execute" not in source
    assert "def verify" not in source
    assert "def run" not in source


def test_the_cache_never_manufactures_a_verdict_or_an_outcome() -> None:
    """It stores verdicts produced by A1.10; it constructs none of them."""
    called = _called_names(_MODULE)
    for forbidden in (
        "VerificationResult",
        "ClosedLoopOutcome",
        "ExecutionResult",
        "CapabilityObservation",
        "LoopOutcome",
        "Task",
    ):
        assert forbidden not in called, f"verified_result_cache must not construct {forbidden}"


def test_the_cache_redefines_no_canonical_contract() -> None:
    classes = _defined_classes(_MODULE)
    for forbidden in (
        "Capability",
        "CapabilityExecutionLoop",
        "CapabilityRegistry",
        "VerificationResult",
        "ClosedLoopOutcome",
        "LoopOutcome",
        "ExecutionResult",
        "CapabilityObservation",
        "CapabilityIdentity",
        "CapabilityVersion",
        "CapabilityScope",
        "Executor",
        "Verifier",
        "ActionGate",
        "PermissionEngine",
        "AuthorityContext",
        "ResourceBudget",
        "EmergencyStop",
        "Event",
        "SecurityAuditRecord",
        "Task",
        "EnvironmentalCache",
    ):
        assert forbidden not in classes, f"{forbidden} must not be redefined by A8.07"


def test_the_cache_performs_no_task_transition_or_state_mutation() -> None:
    imported = set(_imported_modules(_MODULE))
    assert "agentx.core.task_state" not in imported

    called = _called_names(_MODULE)
    for forbidden in ("try_transition_task", "transition_task", "validate_transition"):
        assert forbidden not in called, f"verified_result_cache must not call {forbidden}()"


def test_the_cache_touches_no_authority_or_evidence_module_directly() -> None:
    imported = set(_imported_modules(_MODULE))
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
# No routing, no cognition, no A8.08 decay.
# ---------------------------------------------------------------------------


def test_the_cache_imports_no_router_or_model_symbol() -> None:
    imported = _imported_modules(_MODULE)
    assert not any(module.startswith("agentx.cognition") for module in imported)

    tree = ast.parse(_source())
    identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    for forbidden in (
        "ExecutionLevel",
        "ExecutionLevelRouter",
        "RoutingEvidence",
        "RoutingDecision",
        "Reasoner",
        "ModelProvider",
        "ModelRole",
        "ModelRequest",
        "ModelResponse",
        "LLM",
    ):
        assert forbidden not in identifiers, f"verified_result_cache must not reference {forbidden}"


def test_the_router_and_the_governed_paths_are_untouched_by_a807() -> None:
    """A8.07 wires itself into nothing: the Router, Executor, and loop are leaves."""
    for path in (_ROUTER_MODULE, _EXECUTOR_MODULE, _VERIFIER_MODULE, _RUNTIME_MODULE):
        imported = set(_imported_modules(path))
        assert "agentx.capabilities.verified_result_cache" not in imported, (
            f"{path.name} must not import A8.07"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert not any(name.startswith("VerifiedResult") for name in identifiers), (
            f"{path.name} must not reference A8.07 contracts"
        )

    router_classes = _defined_classes(_ROUTER_MODULE)
    assert {
        "ExecutionLevel",
        "ExecutionLevelRouter",
        "RoutingDecision",
        "RoutingEvidence",
    } <= router_classes


def test_no_production_module_wires_the_cache_yet() -> None:
    """Composition happens in a future task: A8.07 ships as an unused boundary."""
    importers = [
        path
        for path in sorted(_AGENTX_SRC.rglob("*.py"))
        if path != _MODULE
        and "agentx.capabilities.verified_result_cache" in set(_imported_modules(path))
    ]
    assert importers == []


def test_a807_implements_no_a808_decay_or_retention_model() -> None:
    """No scoring, ranking, weighting, usage counting, or eviction policy."""
    names = _defined_classes(_MODULE) | _defined_functions(_MODULE)
    lowered = {name.lower() for name in names}
    for forbidden in (
        "decay",
        "score",
        "rank",
        "weight",
        "confidence",
        "strength",
        "halflife",
        "reinforce",
        "promote",
        "demote",
        "trust",
        "usage",
        "hits",
        "lru",
        "lfu",
        "evict",
    ):
        assert not any(forbidden in name for name in lowered), (
            f"A8.07 must not define {forbidden!r} behaviour (A8.08 owns decay)"
        )

    source = _source().lower()
    for forbidden in ("def decay", "decay_factor", "half_life", "hit_count", "lru_cache"):
        assert forbidden not in source


def test_the_cache_declares_no_retry_fallback_or_planning_vocabulary() -> None:
    names = _defined_classes(_MODULE) | _defined_functions(_MODULE)
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
        "repair",
        "research",
        "critic",
        "similarity",
        "fuzzy",
        "embedding",
    ):
        assert not any(forbidden in name for name in lowered), (
            f"A8.07 must not define {forbidden!r} behaviour"
        )


# ---------------------------------------------------------------------------
# Determinism, no persistence, no background work.
# ---------------------------------------------------------------------------


def test_the_cache_is_deterministic_and_does_no_io() -> None:
    imported = set(_imported_modules(_MODULE))
    assert {
        "importlib",
        "pkgutil",
        "subprocess",
        "socket",
        "urllib",
        "sqlite3",
        "shelve",
        "pickle",
        "tempfile",
        "pathlib",
        "os",
        "time",
        "random",
        "asyncio",
    }.isdisjoint(imported)

    called = _called_names(_MODULE)
    assert {"eval", "exec", "compile", "__import__", "open", "print"}.isdisjoint(called)
    assert {"uuid4", "urandom", "random", "monotonic", "time"}.isdisjoint(called)


def test_the_cache_never_reconstructs_evidence_from_serialized_text() -> None:
    """Canonical JSON is written for keying only; nothing is ever read back."""
    called = _called_names(_MODULE)
    for forbidden in ("loads", "load", "from_dict", "from_json", "from_str", "parse", "read_text"):
        assert forbidden not in called, f"verified_result_cache must not call {forbidden}()"

    source = _source()
    assert "json.dumps" in source
    assert "json.loads" not in source


def test_the_cache_spawns_no_background_maintenance() -> None:
    called = _called_names(_MODULE)
    assert {"Thread", "Timer", "Process", "atexit", "sleep", "start", "submit"}.isdisjoint(called)

    imported = set(_imported_modules(_MODULE))
    assert "threading" in imported  # one lock, nothing else
    source = _source()
    assert source.count("Lock()") == 1
    assert "Thread(" not in source


def test_keying_uses_a_stable_digest_and_never_python_hashing() -> None:
    """Fingerprints come from SHA-256, never from PYTHONHASHSEED-randomized ``hash()``."""
    chains = _called_chains(_MODULE)
    functions = _called_functions(_MODULE)

    assert "hashlib.sha256" in chains
    assert "json.dumps" in chains
    assert {"hash", "id", "hashlib.md5"}.isdisjoint(functions)
    assert "hashlib.md5" not in chains
    assert "hashlib.sha1" not in chains
    assert {"random", "uuid4", "urandom", "monotonic"}.isdisjoint(functions | chains)


def test_the_module_has_no_unbounded_iteration_or_swallowed_errors() -> None:
    tree = ast.parse(_source())
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.While)], (
        "the cache performs no unbounded iteration"
    )
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Try)], (
        "the cache swallows nothing: evidence problems are rejections"
    )


# ---------------------------------------------------------------------------
# Zero new dependencies.
# ---------------------------------------------------------------------------


def test_no_new_runtime_dependencies_for_a807() -> None:
    """The runtime package keeps its zero third-party dependency contract."""
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []
    assert pyproject["project"]["optional-dependencies"]["dev"] == [
        "pytest>=8.0",
        "ruff>=0.5",
        "mypy>=1.10",
    ]


def test_the_cache_uses_only_the_standard_library_externally() -> None:
    stdlib_allowed = {
        "__future__",
        "collections.abc",
        "dataclasses",
        "datetime",
        "enum",
        "hashlib",
        "json",
        "math",
        "threading",
        "types",
        "typing",
        "uuid",
    }
    for module in _imported_modules(_MODULE):
        if module.startswith("agentx"):
            continue
        assert module in stdlib_allowed, f"unexpected external import {module}"
