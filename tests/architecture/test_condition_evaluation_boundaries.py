"""Architecture guardrails for the A3.07 condition evaluator.

These tests keep the A3.07 evaluator where and what it must be:

    - One inert evaluator module in ``agentx.procedures``
      (``condition_evaluation.py``) importing nothing but the standard
      library subset actually used, the canonical A3.06 condition contract,
      and the canonical core evidence vocabulary — the evaluator composes
      the canonical representation, it never duplicates it.
    - No kernel, capability, cognition, Hive, infrastructure, task, model,
      research, or store dependency; no execution surface; no dynamic
      behavior; no time, randomness, or generated identity.
    - A3.06 stays intact: its public surface is unchanged, none of its
      classes are redefined, and the evaluator adds no representation of
      its own for conditions — evaluation results carry no satisfied-field
      doppelganger of a canonical contract.
    - Zero new runtime dependencies.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_EVALUATION_MODULE = _AGENTX_SRC / "procedures" / "condition_evaluation.py"

# Exact allowed import set: the standard library subset actually used plus
# the canonical A3.06 condition contract and the core evidence vocabulary.
_ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "dataclasses",
        "enum",
        "typing",
        "agentx.core.provenance",
        "agentx.procedures.conditions",
    }
)

_EXPECTED_CLASSES = frozenset(
    {
        "ConditionEvaluation",
        "ConditionEvaluationError",
        "ConditionEvaluationStatus",
        "EvidenceFact",
        "EvidenceFacts",
    }
)

_EXPECTED_EXPORTS = frozenset(
    {
        "ConditionEvaluation",
        "ConditionEvaluationError",
        "ConditionEvaluationStatus",
        "EvidenceFact",
        "EvidenceFacts",
        "evaluate_condition",
    }
)

# The single module-level evaluation entry point; everything else is private.
_EXPECTED_FUNCTIONS = frozenset({"evaluate_condition"})

# Canonical A3.06 contract surface — unchanged, composed, never duplicated.
_A3_06_EXPORTS = frozenset(
    {
        "CURRENT_CONDITIONS_CONTRACT_VERSION",
        "ConditionId",
        "NodeConditions",
        "ProcedureCondition",
        "ProcedureConditions",
        "ProcedureConditionsError",
    }
)

# Procedure, storage, capability, graph, and node-family contracts A3.07
# must not redefine or steal: it evaluates canonical data, it never owns any
# of these representations.
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
        "ConditionId",
        "ProcedureCondition",
        "ProcedureConditions",
        "NodeConditions",
        "Capability",
        "CapabilityIdentity",
        "CapabilityRequest",
        "CapabilityDescriptor",
        "VerificationResult",
        "Verifier",
        "RequirementEvaluation",
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
    }
)

_FORBIDDEN_MODULES = frozenset(
    {
        "os",
        "sys",
        "io",
        "pathlib",
        "importlib",
        "subprocess",
        "socket",
        "threading",
        "time",
        "datetime",
        "random",
        "uuid",
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
    | {f"agentx.{part}" for part in ("kernel", "capabilities", "cognition", "hive", "learning")}
    | {
        "agentx.infrastructure",
        "agentx.core.procedures",
        "agentx.core.tasks",
        "agentx.core.task_state",
        "agentx.core.execution",
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
        "transition",
        "grant",
        "publish",
        "emit",
        "generate",
        "reason",
        "search",
        "now",
        "today",
        "utcnow",
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
        "ProcedureGraph",
        "ProcedureNode",
        "sqlite3",
    }
)

# Public method names the evaluator's data classes may expose. The result
# and fact values are plain data: they deliberately expose NO methods at all.
_ALLOWED_METHODS: frozenset[str] = frozenset()


def _tree() -> ast.Module:
    return ast.parse(_EVALUATION_MODULE.read_text(encoding="utf-8"))


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


def _top_level_function_names() -> set[str]:
    return {node.name for node in _tree().body if isinstance(node, ast.FunctionDef)}


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
    raise AssertionError("condition evaluation: no string-literal __all__")


# ---------------------------------------------------------------------------
# Placement and ownership
# ---------------------------------------------------------------------------


def test_evaluation_module_exists_and_defines_only_its_own_contracts() -> None:
    assert _EVALUATION_MODULE.is_file()
    assert _top_level_class_names() == _EXPECTED_CLASSES


def test_public_exports_are_exactly_the_own_evaluation_surface() -> None:
    assert _module_all() == _EXPECTED_EXPORTS


def test_the_one_entry_point_is_a_module_level_pure_function() -> None:
    """The public evaluation surface is exactly one function; evaluation is
    not an object you can construct, configure, subclass, or call later."""
    public_functions = {name for name in _top_level_function_names() if not name.startswith("_")}
    assert public_functions == _EXPECTED_FUNCTIONS
    for name in _EXPECTED_EXPORTS - _EXPECTED_FUNCTIONS:
        assert name in _top_level_class_names(), name


def test_a3_07_defines_no_competing_condition_graph_or_authority_type() -> None:
    offenders = _top_level_class_names() & _COMPETING_CLASSES
    assert offenders == set(), offenders


def test_a3_06_contract_surface_is_unchanged_and_composed() -> None:
    """The evaluator composes the canonical A3.06 representation instead of
    duplicating it, and A3.06's own module is byte-for-byte responsible for
    its surface: the A3.07 module defines none of it."""
    import agentx.procedures.conditions as conditions_module

    assert set(conditions_module.__all__) == _A3_06_EXPORTS
    assert _top_level_class_names().isdisjoint(_A3_06_EXPORTS)


# ---------------------------------------------------------------------------
# Dependency model: stdlib subset + A3.06 contract + core evidence vocabulary
# ---------------------------------------------------------------------------


def test_module_imports_exactly_its_allowed_modules() -> None:
    imported = _imported_modules()
    assert imported == _ALLOWED_IMPORTS, sorted(imported)


def test_no_kernel_capability_cognition_hive_store_or_task_dependency() -> None:
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
        "agentx.procedures.conditions",
        "agentx.core.provenance",
    }, agentx_imports


def test_no_authority_or_runtime_symbols_are_referenced() -> None:
    referenced = _referenced_names()
    assert referenced.isdisjoint(_FORBIDDEN_SYMBOLS), referenced & _FORBIDDEN_SYMBOLS


def test_pyproject_declares_zero_runtime_dependencies() -> None:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []


# ---------------------------------------------------------------------------
# No execution, no I/O, no dynamic behavior, no time or randomness
# ---------------------------------------------------------------------------


def test_module_has_no_execution_shell_io_or_time_surface() -> None:
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


def test_result_and_fact_classes_are_plain_frozen_data() -> None:
    """The evaluator's value types are frozen dataclasses with fields only:
    no methods, no properties, no callables, and no callable annotations."""
    from agentx.procedures.condition_evaluation import (
        ConditionEvaluation,
        EvidenceFact,
        EvidenceFacts,
    )

    for contract in (EvidenceFact, EvidenceFacts, ConditionEvaluation):
        methods = {
            name
            for name, member in vars(contract).items()
            if not name.startswith("_") and callable(member)
        }
        assert methods <= _ALLOWED_METHODS, (contract.__name__, sorted(methods))
        assert "__call__" not in vars(contract)
        for name, member in vars(contract).items():
            assert not isinstance(member, property), (contract.__name__, name)
        annotations = getattr(contract, "__annotations__", {})
        for field_name, annotation in annotations.items():
            # No field is annotated as a callable of any kind: no predicate,
            # callback, or strategy can ride inside an evaluation input.
            assert "Callable" not in str(annotation), (contract.__name__, field_name)


def test_module_public_names_carry_no_authority_verbs() -> None:
    """``evaluate_condition`` (and the SATISFIED vocabulary it returns) are
    this module's canonical job; every other authority verb stays out."""
    import agentx.procedures.condition_evaluation as evaluation_module

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
        "verify",
        "authorize",
        "transition",
        "dispatch",
    )
    public_names = {name for name in dir(evaluation_module) if not name.startswith("_")} | set(
        evaluation_module.__all__
    )
    for name in public_names:
        lowered = name.lower()
        assert lowered not in verbs, name
        assert not any(lowered.startswith(f"{verb}_") for verb in verbs), name


def test_evaluation_module_adds_no_second_a3_06_error_or_id_vocabulary() -> None:
    """The evaluator's error type descends from ValueError directly — not
    from the A3.06 contract error — because a malformed FACT is a request
    error, not a conditions-document error; and the module defines no
    condition-id or evidence-kind vocabulary of its own."""
    from agentx.procedures.condition_evaluation import ConditionEvaluationError
    from agentx.procedures.conditions import ProcedureConditionsError

    assert issubclass(ConditionEvaluationError, ValueError)
    assert not issubclass(ConditionEvaluationError, ProcedureConditionsError)
    source = _EVALUATION_MODULE.read_text(encoding="utf-8")
    for owned_elsewhere in (
        "class ConditionId",
        "class EvidenceKind",
        "class ProcedureCondition",
        "class ProcedureConditions",
        "class NodeConditions",
    ):
        assert owned_elsewhere not in source, owned_elsewhere
