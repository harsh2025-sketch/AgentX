"""Architecture guardrails for the A3.05 node-family contracts (ROLLBACK,
SUBPROCEDURE, END).

These tests keep the A3.05 contracts where and what they must be:

    - One inert DATA module per node family in ``agentx.procedures``
      (``rollback.py``, ``subprocedure.py``, ``end.py``) importing nothing but
      the standard library, the A3.01 graph IR module, and — for
      SUBPROCEDURE — the canonical core identifier module.
    - No interpreter, runtime, store, model, Hive, kernel, capability, task,
      or telemetry dependency, and no execution, loading, waiting, or
      authority surface of any kind.
    - A3.02/A3.03/A3.04 isolation: no other owned node family is claimed
      here, and A3.05 never references their node-kind vocabulary.
    - A3.01 stays intact: the graph IR's public API is unchanged, the graph
      itself stays free of contract-class knowledge, and C2.03 storage stays
      opaque to these contracts.
    - Zero new runtime dependencies.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_FAMILY_MODULES: dict[str, Path] = {
    "rollback": _AGENTX_SRC / "procedures" / "rollback.py",
    "subprocedure": _AGENTX_SRC / "procedures" / "subprocedure.py",
    "end": _AGENTX_SRC / "procedures" / "end.py",
}

# Exact allowed import sets per module: the standard library subset actually
# used plus the A3.01 graph IR module (and, for SUBPROCEDURE, the canonical
# core procedure identifier). Anything else is a boundary violation.
_ALLOWED_IMPORTS: dict[str, frozenset[str]] = {
    "rollback": frozenset(
        {
            "__future__",
            "json",
            "collections.abc",
            "dataclasses",
            "enum",
            "typing",
            "agentx.procedures.graph",
        }
    ),
    "subprocedure": frozenset(
        {
            "__future__",
            "json",
            "math",
            "uuid",
            "collections.abc",
            "dataclasses",
            "types",
            "typing",
            "agentx.core.ids",
            "agentx.procedures.graph",
        }
    ),
    "end": frozenset(
        {
            "__future__",
            "json",
            "collections.abc",
            "dataclasses",
            "typing",
            "agentx.procedures.graph",
        }
    ),
}

_EXPECTED_CLASSES: dict[str, frozenset[str]] = {
    "rollback": frozenset(
        {
            "RollbackContractError",
            "RollbackNodeSpec",
            "RollbackScope",
            "RollbackScopeKind",
        }
    ),
    "subprocedure": frozenset({"SubprocedureContractError", "SubprocedureNodeSpec"}),
    "end": frozenset({"EndContractError", "EndNodeSpec"}),
}

_EXPECTED_EXPORTS: dict[str, frozenset[str]] = {
    "rollback": frozenset(
        {
            "CURRENT_ROLLBACK_CONTRACT_VERSION",
            "RollbackContractError",
            "RollbackNodeSpec",
            "RollbackScope",
            "RollbackScopeKind",
        }
    ),
    "subprocedure": frozenset(
        {
            "CURRENT_SUBPROCEDURE_CONTRACT_VERSION",
            "MAX_SUBPROCEDURE_ARGUMENT_NESTING",
            "SubprocedureContractError",
            "SubprocedureNodeSpec",
        }
    ),
    "end": frozenset({"CURRENT_END_CONTRACT_VERSION", "EndContractError", "EndNodeSpec"}),
}

# Public method names a contract class may expose: strictly data operations.
_ALLOWED_METHODS: frozenset[str] = frozenset(
    {"to_dict", "from_dict", "to_json", "from_json", "to_node", "from_node"}
)

_FORBIDDEN_MODULES: frozenset[str] = frozenset(
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
    }
)

_FORBIDDEN_CALL_NAMES: frozenset[str] = frozenset(
    {"eval", "exec", "compile", "__import__", "input", "breakpoint", "getattr", "setattr"}
)

_FORBIDDEN_CALL_ATTRIBUTES: frozenset[str] = frozenset(
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
    }
)

# Authority/runtime symbols that must never be referenced by name in source.
_FORBIDDEN_SYMBOLS: frozenset[str] = frozenset(
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

# Node kinds owned by other tasks: A3.05 modules must not read or extend them.
_FOREIGN_KIND_ATTRIBUTES: frozenset[str] = frozenset(
    {"ACTION", "OBSERVE", "VERIFY", "BRANCH", "TRANSFORM", "REASON", "RESEARCH", "WAIT"}
)

_OWN_KIND_ATTRIBUTES: frozenset[str] = frozenset({"ROLLBACK", "SUBPROCEDURE", "END"})

_A3_01_GRAPH_API: frozenset[str] = frozenset(
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

# Procedure storage/ABI classes A3.05 must not redefine or steal.
_COMPETING_CLASSES: frozenset[str] = frozenset(
    {
        "ProcedureGraph",
        "ProcedureNode",
        "ProcedureNodeKind",
        "ProcedureEdge",
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
    }
)


def _tree(family: str) -> ast.Module:
    return ast.parse(_FAMILY_MODULES[family].read_text(encoding="utf-8"))


def _imported_modules(family: str) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree(family)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def _top_level_class_names(family: str) -> set[str]:
    return {node.name for node in _tree(family).body if isinstance(node, ast.ClassDef)}


def _referenced_names(family: str) -> set[str]:
    return {
        node.id
        for node in ast.walk(_tree(family))
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def _attribute_names(family: str) -> set[str]:
    return {node.attr for node in ast.walk(_tree(family)) if isinstance(node, ast.Attribute)}


def _module_all(family: str) -> frozenset[str]:
    for node in _tree(family).body:
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
    raise AssertionError(f"{family}: no string-literal __all__")


# ---------------------------------------------------------------------------
# Placement and ownership
# ---------------------------------------------------------------------------


def test_node_family_modules_exist_and_define_only_their_own_contracts() -> None:
    for family in _FAMILY_MODULES:
        assert _FAMILY_MODULES[family].is_file()
        assert _top_level_class_names(family) == _EXPECTED_CLASSES[family], family


def test_public_exports_are_exactly_the_own_contract_surface() -> None:
    for family in _FAMILY_MODULES:
        assert _module_all(family) == _EXPECTED_EXPORTS[family], family


def test_a3_05_defines_no_competing_graph_storage_or_capability_abi_type() -> None:
    for family in _FAMILY_MODULES:
        offenders = _top_level_class_names(family) & _COMPETING_CLASSES
        assert offenders == set(), (family, offenders)


def test_a3_05_does_not_claim_other_owned_node_families() -> None:
    """No A3.02/A3.03/A3.04 spec class is defined, and no other family's
    node-kind vocabulary is referenced — A3.05 sees only its own kinds."""
    other_owned = {
        "ActionNodeSpec",
        "ObserveNodeSpec",
        "VerifyNodeSpec",
        "BranchContract",
        "BranchOutcome",
        "TransformContract",
        "WaitContract",
        "ReasonNodeSpec",
        "ResearchNodeSpec",
    }
    for family in _FAMILY_MODULES:
        assert _top_level_class_names(family).isdisjoint(other_owned), family
        kind_refs = {
            node.attr
            for node in ast.walk(_tree(family))
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ProcedureNodeKind"
        }
        assert kind_refs <= _OWN_KIND_ATTRIBUTES, (family, kind_refs)
        assert kind_refs.isdisjoint(_FOREIGN_KIND_ATTRIBUTES), (family, kind_refs)


# ---------------------------------------------------------------------------
# Dependency model: stdlib + A3.01 graph IR (+ core identity for SUBPROCEDURE)
# ---------------------------------------------------------------------------


def test_modules_import_exactly_their_allowed_modules() -> None:
    for family in _FAMILY_MODULES:
        imported = _imported_modules(family)
        assert imported == _ALLOWED_IMPORTS[family], (family, sorted(imported))


def test_no_store_kernel_capability_cognition_hive_or_task_dependency() -> None:
    """A3.05 modules import no outer subsystem at all: SUBPROCEDURE may name
    a procedure only via the canonical core identifier, never via storage."""
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
    )
    for family in _FAMILY_MODULES:
        imported = _imported_modules(family)
        violations = [module for module in imported if module.startswith(forbidden_prefixes)]
        assert violations == [], (family, violations)
        agentx_imports = {module for module in imported if module.startswith("agentx.")}
        assert agentx_imports <= {"agentx.procedures.graph", "agentx.core.ids"}, (
            family,
            agentx_imports,
        )


def test_no_interpreter_or_runtime_dependency_symbols() -> None:
    for family in _FAMILY_MODULES:
        referenced = _referenced_names(family)
        assert referenced.isdisjoint(_FORBIDDEN_SYMBOLS), (family, referenced & _FORBIDDEN_SYMBOLS)


def test_pyproject_declares_zero_runtime_dependencies() -> None:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []


# ---------------------------------------------------------------------------
# No execution, no loading, no dynamic behavior
# ---------------------------------------------------------------------------


def test_modules_have_no_execution_shell_io_or_loading_surface() -> None:
    for family in _FAMILY_MODULES:
        imported = _imported_modules(family)
        assert imported.isdisjoint(_FORBIDDEN_MODULES), (family, imported & _FORBIDDEN_MODULES)

        for node in ast.walk(_tree(family)):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    assert func.id not in _FORBIDDEN_CALL_NAMES, (family, func.id)
                elif isinstance(func, ast.Attribute):
                    assert func.attr not in _FORBIDDEN_CALL_ATTRIBUTES, (family, func.attr)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # No dynamic or relative escapes: every import is absolute.
                assert getattr(node, "level", 0) == 0, family
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    assert handler.type is not None  # no bare except


def test_contract_public_methods_are_data_only() -> None:
    """Contract classes expose only canonical data operations — nothing that
    runs, rolls back, loads, invokes, interprets, or grants."""
    from agentx.procedures.end import EndNodeSpec
    from agentx.procedures.rollback import RollbackNodeSpec, RollbackScope
    from agentx.procedures.subprocedure import SubprocedureNodeSpec

    for contract in (RollbackNodeSpec, RollbackScope, SubprocedureNodeSpec, EndNodeSpec):
        methods = {
            name
            for name in dir(contract)
            if not name.startswith("_") and callable(getattr(contract, name))
        }
        assert methods <= _ALLOWED_METHODS, (contract.__name__, sorted(methods))
        if contract is RollbackScope:
            assert methods == {"to_dict", "from_dict"}, methods


def test_module_public_names_carry_no_authority_verbs() -> None:
    import agentx.procedures.end as end_module
    import agentx.procedures.rollback as rollback_module
    import agentx.procedures.subprocedure as subprocedure_module

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
    )
    for module in (rollback_module, subprocedure_module, end_module):
        public_names = {name for name in dir(module) if not name.startswith("_")} | set(
            module.__all__
        )
        for name in public_names:
            lowered = name.lower()
            assert lowered not in verbs, (module.__name__, name)
            assert not any(lowered.startswith(f"{verb}_") for verb in verbs), (
                module.__name__,
                name,
            )


def test_no_callable_or_property_binding_surface_in_public_contracts() -> None:
    """Public contract classes own no ``__call__`` and no properties: every
    member is either a plain data field or one of the canonical data
    operations. There is no attribute that could be invoked to do something."""
    from agentx.procedures.end import EndNodeSpec
    from agentx.procedures.rollback import RollbackNodeSpec, RollbackScope
    from agentx.procedures.subprocedure import SubprocedureNodeSpec

    for cls in (RollbackNodeSpec, RollbackScope, SubprocedureNodeSpec, EndNodeSpec):
        assert "__call__" not in vars(cls)
        for name, member in vars(cls).items():
            assert not isinstance(member, property), (cls.__name__, name)
        annotations = getattr(cls, "__annotations__", {})
        for field_name, annotation in annotations.items():
            # No field is annotated as a callable of any kind.
            assert "Callable" not in str(annotation), (cls.__name__, field_name)


# ---------------------------------------------------------------------------
# A3.01 stays intact; C2.03 storage stays opaque
# ---------------------------------------------------------------------------


def test_a3_01_graph_ir_public_api_is_unchanged() -> None:
    import agentx.procedures.graph as graph_module

    assert set(graph_module.__all__) == _A3_01_GRAPH_API


def test_graph_module_does_not_know_the_a3_05_contracts() -> None:
    graph_source = (_AGENTX_SRC / "procedures" / "graph.py").read_text(encoding="utf-8")
    for token in (
        "RollbackNodeSpec",
        "RollbackScope",
        "SubprocedureNodeSpec",
        "EndNodeSpec",
        "agentx.procedures.rollback",
        "agentx.procedures.subprocedure",
        "agentx.procedures.end",
    ):
        assert token not in graph_source, token


def test_procedure_store_stays_opaque_to_a3_05_contracts() -> None:
    store_source = (_AGENTX_SRC / "infrastructure" / "procedure_store.py").read_text(
        encoding="utf-8"
    )
    for token in (
        "agentx.procedures.rollback",
        "agentx.procedures.subprocedure",
        "agentx.procedures.end",
        "RollbackNodeSpec",
        "RollbackScope",
        "SubprocedureNodeSpec",
        "EndNodeSpec",
    ):
        assert token not in store_source, token


def test_a3_05_modules_are_referenced_nowhere_but_by_tests_and_package_docs() -> None:
    """Nothing in ``src`` silently grows a dependency on the A3.05 modules:
    the only src-file mentions are the modules themselves and the package
    initializer docstring."""
    allowed = {
        _AGENTX_SRC / "procedures" / "rollback.py",
        _AGENTX_SRC / "procedures" / "subprocedure.py",
        _AGENTX_SRC / "procedures" / "end.py",
        _AGENTX_SRC / "procedures" / "__init__.py",
    }
    needles = (
        "procedures.rollback",
        "procedures.subprocedure",
        "procedures.end",
        "RollbackNodeSpec",
        "SubprocedureNodeSpec",
        "EndNodeSpec",
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
