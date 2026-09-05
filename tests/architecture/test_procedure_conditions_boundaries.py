"""Architecture guardrails for the A3.06 preconditions/postconditions contract.

These tests keep the A3.06 conditions contract where and what it must be:

    - One inert DATA module in ``agentx.procedures`` (``conditions.py``)
      importing nothing but the standard library, the A3.01 graph IR module,
      and the canonical core evidence vocabulary.
    - No interpreter, evaluator, verifier, store, model, Hive, kernel,
      capability, task, or telemetry dependency, and no execution,
      evaluation, verification, or authority surface of any kind.
    - A3.02-A3.05 isolation: no other owned node-family contract is defined,
      claimed, or imported here, and no ``ProcedureNodeKind`` member is read
      (conditions are attachable to every family alike).
    - A3.01 stays intact: the graph IR's public API is unchanged, the graph
      itself stays free of contract-class knowledge, and C2.03 storage stays
      opaque to this contract. No second graph schema exists.
    - Zero new runtime dependencies.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_CONDITIONS_MODULE = _AGENTX_SRC / "procedures" / "conditions.py"

# Exact allowed import set: the standard library subset actually used plus the
# A3.01 graph IR module and the canonical core evidence vocabulary. Anything
# else is a boundary violation.
_ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "json",
        "collections.abc",
        "dataclasses",
        "typing",
        "agentx.core.provenance",
        "agentx.procedures.graph",
    }
)

_EXPECTED_CLASSES = frozenset(
    {
        "ConditionId",
        "NodeConditions",
        "ProcedureCondition",
        "ProcedureConditions",
        "ProcedureConditionsError",
    }
)

_EXPECTED_EXPORTS = frozenset(
    {
        "CURRENT_CONDITIONS_CONTRACT_VERSION",
        "ConditionId",
        "NodeConditions",
        "ProcedureCondition",
        "ProcedureConditions",
        "ProcedureConditionsError",
    }
)

# Public method names a contract class may expose: strictly data operations.
_ALLOWED_METHODS = frozenset(
    {"to_dict", "from_dict", "to_json", "from_json", "to_str", "parse", "bind_to_graph"}
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
        "undo",
        "revert",
        "load",
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
        "search",
        "evaluate",
        "satisfy",
        "succeeded",
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

# Procedure storage/ABI/classes A3.06 must not redefine or steal.
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


def _tree() -> ast.Module:
    return ast.parse(_CONDITIONS_MODULE.read_text(encoding="utf-8"))


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
    raise AssertionError("conditions: no string-literal __all__")


# ---------------------------------------------------------------------------
# Placement and ownership
# ---------------------------------------------------------------------------


def test_conditions_module_exists_and_defines_only_its_own_contracts() -> None:
    assert _CONDITIONS_MODULE.is_file()
    assert _top_level_class_names() == _EXPECTED_CLASSES


def test_public_exports_are_exactly_the_own_contract_surface() -> None:
    assert _module_all() == _EXPECTED_EXPORTS


def test_a3_06_defines_no_competing_graph_storage_capability_or_family_type() -> None:
    offenders = _top_level_class_names() & _COMPETING_CLASSES
    assert offenders == set(), offenders


def test_a3_06_does_not_claim_other_owned_node_families() -> None:
    """No A3.02-A3.05 spec class is defined, imported, or referenced, and no
    ``ProcedureNodeKind`` member is read: conditions are family-agnostic."""
    source = _CONDITIONS_MODULE.read_text(encoding="utf-8")
    tree = _tree()
    for token in (
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
        "SubprocedureNodeSpec",
        "EndNodeSpec",
        "ProcedureNodeKind",
        "agentx.procedures.nodes",
        "agentx.procedures.branch",
        "agentx.procedures.transform",
        "agentx.procedures.wait",
        "agentx.procedures.reason_research",
        "agentx.procedures.rollback",
        "agentx.procedures.subprocedure",
        "agentx.procedures.end",
    ):
        assert token not in source, token
    kind_refs = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ProcedureNodeKind"
    }
    assert kind_refs == set(), kind_refs


# ---------------------------------------------------------------------------
# Dependency model: stdlib + A3.01 graph IR + core evidence vocabulary
# ---------------------------------------------------------------------------


def test_module_imports_exactly_its_allowed_modules() -> None:
    imported = _imported_modules()
    assert imported == _ALLOWED_IMPORTS, sorted(imported)


def test_no_store_kernel_capability_cognition_hive_or_task_dependency() -> None:
    """A3.06 imports no outer subsystem and no storage contract: conditions
    reference nodes only via canonical graph-local ids, and procedures only
    via the existing opaque payload channel."""
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
    assert agentx_imports <= {"agentx.procedures.graph", "agentx.core.provenance"}, agentx_imports


def test_no_interpreter_or_runtime_dependency_symbols() -> None:
    referenced = _referenced_names()
    assert referenced.isdisjoint(_FORBIDDEN_SYMBOLS), referenced & _FORBIDDEN_SYMBOLS


def test_pyproject_declares_zero_runtime_dependencies() -> None:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []


# ---------------------------------------------------------------------------
# No execution, no evaluation, no dynamic behavior
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
    evaluates, verifies, executes, interprets, or grants."""
    from agentx.procedures.conditions import (
        ConditionId,
        NodeConditions,
        ProcedureCondition,
        ProcedureConditions,
    )

    for contract in (ConditionId, ProcedureCondition, NodeConditions, ProcedureConditions):
        methods = {
            name
            for name in dir(contract)
            if not name.startswith("_") and callable(getattr(contract, name))
        }
        assert methods <= _ALLOWED_METHODS, (contract.__name__, sorted(methods))
        assert "__call__" not in vars(contract)
        for name, member in vars(contract).items():
            assert not isinstance(member, property), (contract.__name__, name)
        annotations = getattr(contract, "__annotations__", {})
        for field_name, annotation in annotations.items():
            # No field is annotated as a callable of any kind.
            assert "Callable" not in str(annotation), (contract.__name__, field_name)


def test_module_public_names_carry_no_authority_verbs() -> None:
    import agentx.procedures.conditions as conditions_module

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
    )
    public_names = {name for name in dir(conditions_module) if not name.startswith("_")} | set(
        conditions_module.__all__
    )
    for name in public_names:
        lowered = name.lower()
        assert lowered not in verbs, name
        assert not any(lowered.startswith(f"{verb}_") for verb in verbs), name


def test_conditions_declare_no_satisfaction_or_verdict_vocabulary() -> None:
    """No identifier in the module's source can express that a condition
    holds, passed, or was verified: representation cannot become outcome."""
    for token in (
        "satisfied",
        "is_met",
        "passed",
        "verdict",
        "succeeded",
        "outcome",
        "result",
        "status",
    ):
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Name) and node.id == token:
                raise AssertionError(f"conditions source references outcome token {token!r}")
            if isinstance(node, ast.Attribute) and node.attr == token:
                raise AssertionError(f"conditions source references outcome token {token!r}")
    # Fields are declarations only: identity, statement, and evidence data.
    source = _CONDITIONS_MODULE.read_text(encoding="utf-8")
    assert "statement" in source
    assert "evidence_kind" in source
    assert "evidence_reference" in source


# ---------------------------------------------------------------------------
# A3.01 stays intact; C2.03 storage stays opaque
# ---------------------------------------------------------------------------


def test_a3_01_graph_ir_public_api_is_unchanged() -> None:
    import agentx.procedures.graph as graph_module

    assert set(graph_module.__all__) == _A3_01_GRAPH_API


def test_graph_module_does_not_know_the_conditions_contract() -> None:
    graph_source = (_AGENTX_SRC / "procedures" / "graph.py").read_text(encoding="utf-8")
    for token in (
        "ProcedureConditions",
        "ProcedureCondition",
        "NodeConditions",
        "ConditionId",
        "agentx.procedures.conditions",
    ):
        assert token not in graph_source, token


def test_procedure_storage_stays_opaque_to_the_conditions_contract() -> None:
    """C2.03 storage persists the graph and any payload opaquely: no storage
    module knows the A3.06 contract, and the contract knows no storage."""
    opaque_files = (
        _AGENTX_SRC / "infrastructure" / "procedure_store.py",
        _AGENTX_SRC / "infrastructure" / "persistence.py",
        _AGENTX_SRC / "core" / "procedures.py",
    )
    for path in opaque_files:
        source = path.read_text(encoding="utf-8")
        for token in (
            "agentx.procedures.conditions",
            "ProcedureConditions",
            "NodeConditions",
            "ConditionId",
        ):
            assert token not in source, (path.name, token)


def test_a3_06_module_is_referenced_nowhere_but_by_tests_and_package_docs() -> None:
    """Nothing in ``src`` silently grows a dependency on the A3.06 module:
    the only src-file mentions are the module itself and the package
    initializer docstring."""
    allowed = {
        _CONDITIONS_MODULE,
        _AGENTX_SRC / "procedures" / "__init__.py",
    }
    needles = (
        "procedures.conditions",
        "ProcedureConditions",
        "ProcedureCondition",
        "ConditionId",
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
