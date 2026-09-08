"""Architecture guardrails for the M3.01 procedure graph interpreter.

These tests statically prove that the interpreter is exactly one deterministic
control-flow module in ``agentx.procedures``:

    - it imports only the standard library subset it actually uses plus
      already-canonical procedure DATA contracts and the canonical core
      failure vocabulary — imports permitted for ``agentx.procedures`` by the
      canonical architecture manifest;
    - it never imports capabilities, cognition, learning, infrastructure,
      hive, or kernel runtime modules;
    - it defines no duplicate ProcedureGraph/ProcedureNode (or any other
      canonical procedure/capability type);
    - it contains no dynamic execution and no I/O primitives;
    - the canonical architecture manifest itself is unchanged.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx._architecture import ALLOWED_ARCHITECTURE_EDGES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTX_SRC = _REPO_ROOT / "src" / "agentx"
_INTERPRETER_MODULE = _AGENTX_SRC / "procedures" / "interpreter.py"

# Exact allowed import set: the standard library subset actually used plus the
# canonical procedure DATA contracts the interpreter composes and the core
# failure vocabulary. Anything else is a boundary violation.
_ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections.abc",
        "dataclasses",
        "enum",
        "types",
        "typing",
        "agentx.core.failure_taxonomy",
        "agentx.procedures.graph",
        "agentx.procedures.branch",
        "agentx.procedures.recovery",
    }
)

_FORBIDDEN_IMPORT_PREFIXES = (
    "agentx.capabilities",
    "agentx.cognition",
    "agentx.learning",
    "agentx.infrastructure",
    "agentx.hive",
    "agentx.kernel",
    "agentx.agent_loop",
)

_FORBIDDEN_MODULES = frozenset(
    {
        "importlib",
        "subprocess",
        "os",
        "sys",
        "io",
        "socket",
        "threading",
        "time",
        "datetime",
        "random",
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
        "pathlib",
        "tempfile",
        "urllib",
        "http",
    }
)

_FORBIDDEN_CALL_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "input",
        "breakpoint",
        "open",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
    }
)

# Canonical types the interpreter must compose, never redefine.
_COMPETING_CLASSES = frozenset(
    {
        "ProcedureGraph",
        "ProcedureNode",
        "ProcedureNodeId",
        "ProcedureNodeKind",
        "ProcedureEdge",
        "ProcedureEdgeKind",
        "ProcedureRecord",
        "ProcedurePayload",
        "ProcedureStore",
        "BranchContract",
        "BranchOutcome",
        "RecoveryEdge",
        "ProcedureRecoveryEdges",
        "ProcedureCondition",
        "ProcedureConditions",
        "ConditionEvaluation",
        "EvidenceFact",
        "EvidenceFacts",
        "FailureCategory",
        "Capability",
        "CapabilityRequest",
        "VerificationResult",
        "Task",
        "TaskStatus",
        "LoopGuard",
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
    return ast.parse(_INTERPRETER_MODULE.read_text(encoding="utf-8"))


def _imported_modules() -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def test_interpreter_module_exists_inside_procedures() -> None:
    assert _INTERPRETER_MODULE.is_file()


def test_interpreter_imports_are_exactly_the_allowed_set() -> None:
    imported = _imported_modules()
    unexpected = imported - _ALLOWED_IMPORTS
    assert not unexpected, f"interpreter imports outside the allowed set: {sorted(unexpected)}"


def test_interpreter_imports_no_forbidden_subsystem() -> None:
    for module in _imported_modules():
        for prefix in _FORBIDDEN_IMPORT_PREFIXES:
            assert not module.startswith(prefix), (
                f"interpreter must not import {prefix} (found {module!r})"
            )


def test_interpreter_imports_no_forbidden_stdlib_module() -> None:
    imported = _imported_modules()
    hit = imported & _FORBIDDEN_MODULES
    assert not hit, f"interpreter imports forbidden runtime modules: {sorted(hit)}"


def test_interpreter_respects_the_canonical_manifest_edges() -> None:
    # Every agentx import from procedures must be within agentx.procedures
    # itself or along a manifest-allowed edge for agentx.procedures.
    allowed_targets = {
        target for source, target in ALLOWED_ARCHITECTURE_EDGES if source == "agentx.procedures"
    }
    for module in _imported_modules():
        if not module.startswith("agentx."):
            continue
        if module.startswith("agentx.procedures"):
            continue
        assert any(
            module == target or module.startswith(target + ".") for target in allowed_targets
        ), f"import {module!r} violates the canonical manifest for agentx.procedures"


def test_architecture_manifest_is_unchanged() -> None:
    assert ALLOWED_ARCHITECTURE_EDGES == _CANONICAL_EDGES


def test_interpreter_defines_no_competing_canonical_types() -> None:
    defined = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }
    hit = defined & _COMPETING_CLASSES
    assert not hit, f"interpreter redefines canonical types: {sorted(hit)}"


def test_interpreter_uses_no_dynamic_execution_calls() -> None:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in _FORBIDDEN_CALL_NAMES, (
                f"forbidden dynamic-execution/builtin call: {node.func.id}"
            )


def test_interpreter_has_no_async_threads_or_loop_constructs_without_bounds() -> None:
    # No async machinery and no `while True:` anywhere: every loop the module
    # contains iterates over finite canonical data.
    for node in ast.walk(_tree()):
        assert not isinstance(node, ast.AsyncFunctionDef | ast.AsyncFor | ast.AsyncWith | ast.Await)
        if isinstance(node, ast.While):
            raise AssertionError("the interpreter must not contain while-loops")


def test_interpreter_defines_no_second_condition_evaluator() -> None:
    # The canonical single-condition evaluator stays the only evaluator: the
    # interpreter neither imports it (the caller drives it) nor defines any
    # function that evaluates a condition.
    defined = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    for name in defined:
        assert "evaluate" not in name, f"interpreter must not define an evaluator: {name}"
    assert "agentx.procedures.condition_evaluation" not in _imported_modules()


def test_interpreter_public_api_is_data_and_control_only() -> None:
    import agentx.procedures.interpreter as interpreter_module

    public = set(interpreter_module.__all__)
    expected = {
        "DEFAULT_MAX_STEPS",
        "MAX_STEP_LIMIT",
        "BranchSelected",
        "InterpreterFailureReason",
        "InterpreterStatus",
        "ProcedureInstruction",
        "ProcedureInstructionKind",
        "ProcedureInterpreter",
        "ProcedureInterpreterError",
        "ProcedureInterpreterState",
        "StepCompleted",
        "StepFailed",
    }
    assert public == expected
    forbidden_verbs = ("execute", "dispatch", "invoke", "grant", "approve", "authorize")
    for name in public:
        lowered = name.lower()
        for verb in forbidden_verbs:
            assert verb not in lowered


def test_interpreter_does_not_modify_other_procedure_modules() -> None:
    # The canonical inert contracts this task builds on are untouched: they
    # still never import the interpreter (no cycle, no seam widening).
    for module_name in (
        "graph",
        "nodes",
        "branch",
        "conditions",
        "condition_evaluation",
        "end",
        "recovery",
        "reason_research",
        "rollback",
        "subprocedure",
        "transform",
        "wait",
    ):
        source = (_AGENTX_SRC / "procedures" / f"{module_name}.py").read_text(encoding="utf-8")
        assert "agentx.procedures.interpreter" not in source


def test_procedures_package_init_is_unchanged_by_this_task() -> None:
    # M3.01 owns only interpreter.py; the package __init__ must not have been
    # widened to import or re-export interpreter names.
    init_tree = ast.parse((_AGENTX_SRC / "procedures" / "__init__.py").read_text(encoding="utf-8"))
    for node in ast.walk(init_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "interpreter" not in alias.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            assert "interpreter" not in node.module
