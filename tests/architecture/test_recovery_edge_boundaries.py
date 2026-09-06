"""Architecture guardrails for the canonical A3.07 error/recovery edge contract.

These tests keep the A3.07 recovery contract where and what it must be:

    - One inert DATA module in ``agentx.procedures`` (``recovery.py``)
      importing nothing but the standard library, the A3.01 graph IR module,
      and the canonical core failure vocabulary it composes.
    - No interpreter, evaluator, verifier, store, model, Hive, kernel,
      capability, task, or telemetry dependency, and no execution, rollback,
      retry, or authority surface of any kind.
    - No second edge schema and no duplicated canonical type: recovery
      connectivity is rendered as canonical A3.01 ``RECOVERY`` edges and the
      failure vocabulary is composed from ``agentx.core.failure_taxonomy``.
    - A3.02-A3.06 isolation: no other owned contract is defined or imported,
      and no foreign ``ProcedureNodeKind`` member is read.
    - A3.01 stays intact: the graph IR's public API is unchanged, the graph
      stays free of contract-class knowledge, the architecture manifest is
      unwidened, and C2.03 storage stays opaque. No new top-level subsystem.
    - Zero new runtime dependencies.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_RECOVERY_MODULE = _AGENTX_SRC / "procedures" / "recovery.py"

# Exact allowed import set: the standard library subset actually used plus the
# A3.01 graph IR module and the canonical core failure vocabulary. Anything
# else is a boundary violation.
_ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "json",
        "collections.abc",
        "dataclasses",
        "typing",
        "agentx.core.failure_taxonomy",
        "agentx.procedures.graph",
    }
)

_EXPECTED_CLASSES = frozenset({"ProcedureRecoveryEdges", "RecoveryContractError", "RecoveryEdge"})

_EXPECTED_EXPORTS = frozenset(
    {
        "CURRENT_RECOVERY_CONTRACT_VERSION",
        "ProcedureRecoveryEdges",
        "RecoveryContractError",
        "RecoveryEdge",
    }
)

# Public method names a contract class may expose: strictly data operations.
_ALLOWED_METHODS = frozenset(
    {
        "to_dict",
        "from_dict",
        "to_json",
        "from_json",
        "to_graph_edges",
        "declared_target",
        "bind_to_graph",
    }
)

_FORBIDDEN_MODULES = frozenset(
    {
        "importlib",
        "subprocess",
        "os",
        "socket",
        "threading",
        "time",
        "asyncio",
        "signal",
        "multiprocessing",
        "shutil",
        "ctypes",
        "pickle",
        "marshal",
        "concurrent",
        "select",
        "sqlite3",
        "builtins",
        "re",
        "random",
        "pathlib",
    }
)

_FORBIDDEN_CALL_NAMES = frozenset(
    {"eval", "exec", "compile", "__import__", "input", "breakpoint", "getattr", "setattr"}
)

_FORBIDDEN_CALL_ATTRIBUTES = frozenset(
    {
        "system",
        "popen",
        "Popen",
        "spawn",
        "sleep",
        "exec",
        "execfile",
        "create_task",
        "connect",
        "run",
        "execute",
        "invoke",
        "rollback",
        "restore",
        "recover",
        "retry",
        "undo",
        "revert",
        "resolve",
        "fetch",
        "lookup",
        "call",
        "open",
        "request",
        "send",
        "verify",
        "validate_evidence",
        "transition",
        "grant",
        "publish",
        "emit",
        "generate",
        "reason",
        "research",
        "search",
        "evaluate",
        "satisfy",
        "succeeded",
        "activate",
        "promote",
    }
)

# Authority/runtime symbols that must never be referenced by name in source.
_FORBIDDEN_SYMBOLS = frozenset(
    {
        "AuthorityContext",
        "Permission",
        "PermissionEngine",
        "ActionGate",
        "GateRequest",
        "RiskAssessment",
        "RiskLevel",
        "ResourceEnvelope",
        "ResourceBudget",
        "EmergencyStop",
        "VerificationResult",
        "Verifier",
        "TaskStatus",
        "Task",
        "Capability",
        "CapabilityRegistry",
        "CapabilityRequest",
        "CapabilityABI",
        "ProcedureStore",
        "ProcedureRecord",
        "ProcedurePayload",
        "ProcedureStatus",
        "ModelProvider",
        "Reasoner",
        "Interpreter",
        "sqlite3",
    }
)

# Procedure storage/ABI/node-family classes A3.07 must not redefine or steal.
_COMPETING_CLASSES = frozenset(
    {
        "ProcedureGraph",
        "ProcedureNode",
        "ProcedureNodeKind",
        "ProcedureEdge",
        "ProcedureEdgeKind",
        "ProcedureRecord",
        "ProcedurePayload",
        "ProcedureStore",
        "Capability",
        "CapabilityIdentity",
        "CapabilityRequest",
        "CapabilityDescriptor",
        "VerificationResult",
        "RollbackDeclaration",
        "RollbackSupport",
        "FailureCategory",
        "FailureClassification",
        "ActionNodeSpec",
        "ObserveNodeSpec",
        "VerifyNodeSpec",
        "BranchContract",
        "BranchOutcome",
        "TransformContract",
        "WaitContract",
        "ReasonNodeSpec",
        "ResearchNodeSpec",
        "RollbackNodeSpec",
        "RollbackScope",
        "RollbackScopeKind",
        "SubprocedureNodeSpec",
        "EndNodeSpec",
    }
)

_A3_01_GRAPH_API = frozenset(
    {
        "CURRENT_GRAPH_SCHEMA_VERSION",
        "ProcedureEdge",
        "ProcedureEdgeKind",
        "ProcedureGraph",
        "ProcedureGraphDeserializationError",
        "ProcedureGraphError",
        "ProcedureGraphValidationError",
        "ProcedureNode",
        "ProcedureNodeId",
        "ProcedureNodeKind",
        "UnsupportedGraphSchemaVersionError",
    }
)

# The canonical architecture manifest, asserted unchanged by this task.
_CANONICAL_EDGES = frozenset(
    {
        ("agentx.kernel", "agentx.core"),
        ("agentx.capabilities", "agentx.core"),
        ("agentx.hive", "agentx.core"),
        ("agentx.procedures", "agentx.core"),
        ("agentx.cognition", "agentx.core"),
        ("agentx.learning", "agentx.core"),
        ("agentx.infrastructure", "agentx.core"),
        ("agentx.capabilities", "agentx.kernel"),
        ("agentx.procedures", "agentx.kernel"),
        ("agentx.cognition", "agentx.kernel"),
    }
)


def _tree() -> ast.Module:
    return ast.parse(_RECOVERY_MODULE.read_text(encoding="utf-8"))


def _source() -> str:
    return _RECOVERY_MODULE.read_text(encoding="utf-8")


def _imported_modules() -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def _top_level_class_names() -> set[str]:
    return {node.name for node in _tree().body if isinstance(node, ast.ClassDef)}


def _referenced_names() -> set[str]:
    return {
        node.id
        for node in ast.walk(_tree())
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _module_all() -> frozenset[str]:
    for node in _tree().body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
            )
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            return frozenset(
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    raise AssertionError("recovery: no string-literal __all__")


# ---------------------------------------------------------------------------
# Placement and ownership
# ---------------------------------------------------------------------------


def test_recovery_module_exists_and_defines_only_its_own_contracts() -> None:
    assert _RECOVERY_MODULE.is_file()
    assert _top_level_class_names() == _EXPECTED_CLASSES


def test_public_exports_are_exactly_the_own_contract_surface() -> None:
    assert _module_all() == _EXPECTED_EXPORTS


def test_a3_07_defines_no_competing_graph_storage_capability_or_taxonomy_type() -> None:
    offenders = _top_level_class_names() & _COMPETING_CLASSES
    assert offenders == set(), offenders


def test_failure_vocabulary_is_composed_not_duplicated() -> None:
    """The closed failure vocabulary has exactly one canonical definition and
    this module imports it: no local enum re-states the categories."""
    from agentx.core.failure_taxonomy import FailureCategory

    imported = _imported_modules()
    assert "agentx.core.failure_taxonomy" in imported
    assert "FailureCategory" in _referenced_names()

    # The module declares no enum of its own.
    for node in _tree().body:
        if isinstance(node, ast.ClassDef):
            bases = {base.id for base in node.bases if isinstance(base, ast.Name)}
            assert "StrEnum" not in bases and "Enum" not in bases, node.name

    definition_files: list[Path] = []
    for path in sorted(_AGENTX_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            isinstance(item, ast.ClassDef) and item.name == "FailureCategory" for item in tree.body
        ):
            definition_files.append(path.relative_to(_SRC_ROOT))
    assert definition_files == [Path("agentx/core/failure_taxonomy.py")], definition_files
    assert len(list(FailureCategory)) == 13


def test_a3_07_does_not_claim_other_owned_contracts() -> None:
    """No other owned procedure contract is defined, imported, or referenced."""
    for token in (
        "ActionNodeSpec",
        "ObserveNodeSpec",
        "VerifyNodeSpec",
        "BranchContract",
        "TransformContract",
        "WaitContract",
        "ReasonNodeSpec",
        "ResearchNodeSpec",
        "RollbackNodeSpec",
        "SubprocedureNodeSpec",
        "EndNodeSpec",
        "ProcedureCondition",
        "ConditionId",
        "EvidenceFacts",
        "evaluate_condition",
        "agentx.procedures.nodes",
        "agentx.procedures.branch",
        "agentx.procedures.transform",
        "agentx.procedures.wait",
        "agentx.procedures.reason_research",
        "agentx.procedures.rollback",
        "agentx.procedures.subprocedure",
        "agentx.procedures.end",
        "agentx.procedures.conditions",
        "agentx.procedures.condition_evaluation",
    ):
        assert token not in _source(), token


def test_only_the_end_node_kind_is_read() -> None:
    """The sole node-kind rule this contract owns is END terminality; every
    other family is treated identically and is never inspected."""
    kind_refs = {
        node.attr
        for node in ast.walk(_tree())
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ProcedureNodeKind"
    }
    assert kind_refs <= {"END"}, kind_refs


# ---------------------------------------------------------------------------
# Dependency model: stdlib + A3.01 graph IR + canonical failure vocabulary
# ---------------------------------------------------------------------------


def test_module_imports_exactly_its_allowed_modules() -> None:
    imported = _imported_modules()
    assert imported == _ALLOWED_IMPORTS, sorted(imported)


def test_no_store_kernel_capability_cognition_hive_or_task_dependency() -> None:
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.infrastructure",
        "agentx.hive",
        "agentx.learning",
        "agentx.core.procedures",  # C2.03 storage contract stays independent
        "agentx.core.tasks",
        "agentx.core.task_state",
        "agentx.core.execution",
    )
    imported = _imported_modules()
    violations = [module for module in imported if module.startswith(forbidden_prefixes)]
    assert violations == [], violations
    agentx_imports = {module for module in imported if module.startswith("agentx.")}
    assert agentx_imports <= {
        "agentx.procedures.graph",
        "agentx.core.failure_taxonomy",
    }, agentx_imports


def test_no_interpreter_or_runtime_dependency_symbols() -> None:
    referenced = _referenced_names()
    assert referenced.isdisjoint(_FORBIDDEN_SYMBOLS), referenced & _FORBIDDEN_SYMBOLS


def test_pyproject_declares_zero_runtime_dependencies() -> None:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []


# ---------------------------------------------------------------------------
# No execution, no recovery action, no dynamic behavior
# ---------------------------------------------------------------------------


def test_module_has_no_execution_shell_io_or_loading_surface() -> None:
    imported = _imported_modules()
    assert imported.isdisjoint(_FORBIDDEN_MODULES), imported & _FORBIDDEN_MODULES

    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in _FORBIDDEN_CALL_NAMES, func.id
            elif isinstance(func, ast.Attribute):
                assert func.attr not in _FORBIDDEN_CALL_ATTRIBUTES, func.attr
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # No dynamic or relative escapes: every import is absolute.
            assert getattr(node, "level", 0) == 0
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                assert handler.type is not None  # no bare except


def test_contract_public_methods_are_data_only() -> None:
    """Contract classes expose only canonical data operations — nothing that
    executes, rolls back, retries, verifies, interprets, or grants."""
    from agentx.procedures.recovery import ProcedureRecoveryEdges, RecoveryEdge

    edge_methods = {
        name
        for name in dir(RecoveryEdge)
        if not name.startswith("_") and callable(getattr(RecoveryEdge, name))
    }
    document_methods = {
        name
        for name in dir(ProcedureRecoveryEdges)
        if not name.startswith("_") and callable(getattr(ProcedureRecoveryEdges, name))
    }

    assert edge_methods <= _ALLOWED_METHODS, sorted(edge_methods)
    assert document_methods <= _ALLOWED_METHODS, sorted(document_methods)
    assert edge_methods == {"to_dict", "from_dict"}, sorted(edge_methods)

    for contract in (RecoveryEdge, ProcedureRecoveryEdges):
        assert "__call__" not in vars(contract)
        for name, member in vars(contract).items():
            assert not isinstance(member, property), (contract.__name__, name)
        annotations = getattr(contract, "__annotations__", {})
        for field_name, annotation in annotations.items():
            # No field is annotated as a callable of any kind.
            assert "Callable" not in str(annotation), (contract.__name__, field_name)


def test_module_public_names_carry_no_authority_or_action_verbs() -> None:
    import agentx.procedures.recovery as recovery_module

    verbs = (
        "execute",
        "run",
        "interpret",
        "compile",
        "invoke",
        "grant",
        "apply",
        "perform",
        "mark",
        "publish",
        "lower",
        "bypass",
        "clear",
        "restore",
        "reset",
        "resolve",
        "lookup",
        "load",
        "evaluate",
        "satisfy",
        "verify",
        "retry",
        "rollback",
        "recover",
        "activate",
        "transition",
    )
    public_names = {name for name in dir(recovery_module) if not name.startswith("_")} | set(
        recovery_module.__all__
    )
    for name in public_names:
        lowered = name.lower()
        assert lowered not in verbs, name
        assert not any(lowered.startswith(f"{verb}_") for verb in verbs), name


def test_recovery_contract_declares_no_retry_authority_or_outcome_vocabulary() -> None:
    """No identifier in the module's source can express a retry count, an
    authorization, or an outcome: representation cannot become execution or
    verdict."""
    for token in (
        "retry",
        "retries",
        "attempts",
        "backoff",
        "delay",
        "timeout",
        "budget",
        "permission",
        "risk",
        "verified",
        "satisfied",
        "succeeded",
        "outcome",
        "status",
        "verdict",
    ):
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Name) and node.id == token:
                raise AssertionError(f"recovery source references forbidden token {token!r}")
            if isinstance(node, ast.Attribute) and node.attr == token:
                raise AssertionError(f"recovery source references forbidden token {token!r}")
    # The contract's fields are control flow only.
    source = _source()
    for token in ("source", "target", "on_failure", "label"):
        assert token in source


def test_to_graph_edges_renders_canonical_recovery_edges_only() -> None:
    """The bridge to graph connectivity produces ordinary A3.01 edges carrying
    the canonical RECOVERY kind — never NEXT edges, never a new edge type."""
    from agentx.core.failure_taxonomy import FailureCategory
    from agentx.procedures.graph import ProcedureEdge, ProcedureEdgeKind, ProcedureNodeId
    from agentx.procedures.recovery import ProcedureRecoveryEdges, RecoveryEdge

    document = ProcedureRecoveryEdges(
        recovery_edges=(
            RecoveryEdge(
                source=ProcedureNodeId("a"),
                target=ProcedureNodeId("b"),
                on_failure=FailureCategory.TRANSIENT,
            ),
        )
    )
    rendered = document.to_graph_edges()

    assert rendered == (
        ProcedureEdge(
            source=ProcedureNodeId("a"),
            target=ProcedureNodeId("b"),
            kind=ProcedureEdgeKind.RECOVERY,
        ),
    )
    assert all(type(edge) is ProcedureEdge for edge in rendered)
    assert len({ProcedureEdgeKind.RECOVERY, ProcedureEdgeKind.NEXT}) == 2


# ---------------------------------------------------------------------------
# A3.01 stays intact; manifest unwidened; C2.03 storage stays opaque
# ---------------------------------------------------------------------------


def test_a3_01_graph_ir_public_api_is_unchanged() -> None:
    import agentx.procedures.graph as graph_module

    assert set(graph_module.__all__) == _A3_01_GRAPH_API


def test_graph_module_does_not_know_the_recovery_contract() -> None:
    graph_source = (_AGENTX_SRC / "procedures" / "graph.py").read_text(encoding="utf-8")
    for token in (
        "ProcedureRecoveryEdges",
        "RecoveryEdge",
        "RecoveryContractError",
        "FailureCategory",
        "agentx.procedures.recovery",
    ):
        assert token not in graph_source, token


def test_architecture_manifest_is_not_widened_and_gains_no_subsystem() -> None:
    from agentx import _architecture

    assert _architecture.ALLOWED_ARCHITECTURE_EDGES == _CANONICAL_EDGES
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
    assert _architecture.PROCEDURES == "agentx.procedures"


def test_procedure_storage_stays_opaque_to_the_recovery_contract() -> None:
    """C2.03 storage persists graphs and payloads opaquely: no storage module
    knows the A3.07 contract, and the contract knows no storage."""
    opaque_files = (
        _AGENTX_SRC / "infrastructure" / "procedure_store.py",
        _AGENTX_SRC / "infrastructure" / "persistence.py",
        _AGENTX_SRC / "core" / "procedures.py",
    )
    for path in opaque_files:
        source = path.read_text(encoding="utf-8")
        for token in (
            "agentx.procedures.recovery",
            "ProcedureRecoveryEdges",
            "RecoveryEdge",
            "RecoveryContractError",
        ):
            assert token not in source, (path.name, token)


def test_a3_07_module_is_referenced_nowhere_but_by_tests_and_package_docs() -> None:
    """Nothing in ``src`` silently grows a dependency on the A3.07 recovery
    module: the only src-file mentions are the module itself and the package
    initializer docstring."""
    allowed = {
        _RECOVERY_MODULE,
        _AGENTX_SRC / "procedures" / "__init__.py",
    }
    needles = (
        "procedures.recovery",
        "ProcedureRecoveryEdges",
        "RecoveryEdge",
        "RecoveryContractError",
    )
    offenders: list[str] = []
    for path in sorted(_AGENTX_SRC.rglob("*.py")):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                offenders.append(f"{path.relative_to(_SRC_ROOT)}: {needle}")
    assert offenders == [], offenders
