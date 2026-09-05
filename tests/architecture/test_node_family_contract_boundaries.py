"""Architecture guardrails for the A3.03 node-family contracts (BRANCH,
TRANSFORM, WAIT).

These tests keep the A3.03 contracts where they belong:

    - One inert DATA module per node family in ``agentx.procedures``
      (``branch.py``, ``transform.py``, ``wait.py``), importing nothing but
      the standard library and the A3.01 graph IR module.
    - No authority, no execution surface (no eval/exec, no dynamic imports,
      no shell, no timers/threads/I/O), no model or Task touch.
    - A3.02 isolation: no ACTION/OBSERVE/VERIFY semantics are claimed here.
    - A3.01 stays intact: the graph IR module's public API is unchanged.
    - C2.03 storage stays opaque to the contracts, and the package keeps
      zero third-party runtime dependencies.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_FAMILY_MODULES: dict[str, Path] = {
    "branch": _AGENTX_SRC / "procedures" / "branch.py",
    "transform": _AGENTX_SRC / "procedures" / "transform.py",
    "wait": _AGENTX_SRC / "procedures" / "wait.py",
}

# Exact allowed import sets per module: the standard library subset actually
# used plus the A3.01 graph IR module. Anything else is a boundary violation.
_ALLOWED_IMPORTS: dict[str, frozenset[str]] = {
    "branch": frozenset(
        {
            "__future__",
            "json",
            "collections.abc",
            "dataclasses",
            "typing",
            "agentx.procedures.graph",
        }
    ),
    "transform": frozenset(
        {
            "__future__",
            "json",
            "math",
            "collections.abc",
            "dataclasses",
            "types",
            "typing",
            "agentx.procedures.graph",
        }
    ),
    "wait": frozenset(
        {
            "__future__",
            "json",
            "math",
            "collections.abc",
            "dataclasses",
            "typing",
            "agentx.procedures.graph",
        }
    ),
}

_EXPECTED_CLASSES: dict[str, frozenset[str]] = {
    "branch": frozenset({"BranchContract", "BranchContractError", "BranchOutcome"}),
    "transform": frozenset({"TransformContract", "TransformContractError"}),
    "wait": frozenset({"WaitContract", "WaitContractError"}),
}

# Public method names a contract class may expose: strictly data operations.
_ALLOWED_METHODS: frozenset[str] = frozenset(
    {"to_dict", "from_dict", "to_json", "from_json", "to_node", "bind"}
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
        "sys",
        "builtins",
    }
)

_FORBIDDEN_CALL_NAMES: frozenset[str] = frozenset(
    {"eval", "exec", "compile", "__import__", "input", "breakpoint"}
)

_FORBIDDEN_CALL_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "system",
        "popen",
        "Popen",
        "spawn",
        "sleep",
        "wait",
        "poll",
        "exec",
        "execfile",
        "create_task",
        "connect",
        "run",
    }
)

_A3_02_KIND_ATTRIBUTES: frozenset[str] = frozenset({"ACTION", "OBSERVE", "VERIFY"})

_A3_02_KIND_VALUES: frozenset[str] = frozenset({"action", "observe", "verify"})

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


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def _top_level_class_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}


# ---------------------------------------------------------------------------
# Placement and ownership
# ---------------------------------------------------------------------------


def test_node_family_modules_exist_and_define_only_their_own_contracts() -> None:
    for family, path in _FAMILY_MODULES.items():
        assert path.is_file(), path
        assert _top_level_class_names(path) == _EXPECTED_CLASSES[family], family


def test_no_a3_02_contract_classes_are_duplicated_or_claimed_by_a3_03() -> None:
    """A3.03 must not claim or duplicate the A3.02-owned node families: none of
    the three A3.03 family modules defines an Action*/Observe*/Verify* class,
    and the A3.02 spec classes keep their single canonical definition in
    ``agentx.procedures.nodes`` (A3.02's module).
    """
    a3_02_specs = frozenset({"ActionNodeSpec", "ObserveNodeSpec", "VerifyNodeSpec"})

    for family, path in _FAMILY_MODULES.items():
        offenders = {
            name
            for name in _top_level_class_names(path)
            if name.startswith(("Action", "Observe", "Verify"))
        }
        assert offenders == set(), (family, offenders)

    definition_files: list[Path] = []
    for path in (_AGENTX_SRC / "procedures").rglob("*.py"):
        defined = _top_level_class_names(path) & a3_02_specs
        if defined:
            definition_files.append(path.relative_to(_SRC_ROOT))
    assert definition_files == [Path("agentx/procedures/nodes.py")], definition_files


# ---------------------------------------------------------------------------
# Dependency model: stdlib + A3.01 graph IR only, zero new dependencies
# ---------------------------------------------------------------------------


def test_node_family_modules_import_exactly_their_allowed_modules() -> None:
    for family, path in _FAMILY_MODULES.items():
        imported = _imported_modules(path)
        assert imported == _ALLOWED_IMPORTS[family], (family, sorted(imported))


def test_node_family_modules_do_not_import_each_other_or_core() -> None:
    for family, path in _FAMILY_MODULES.items():
        imported = _imported_modules(path)
        agentx_imports = {module for module in imported if module.startswith("agentx.")}
        assert agentx_imports == {"agentx.procedures.graph"}, (family, agentx_imports)


def test_pyproject_declares_zero_runtime_dependencies() -> None:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []


# ---------------------------------------------------------------------------
# No execution, no shell, no waiting, no authority surface
# ---------------------------------------------------------------------------


def test_node_family_modules_have_no_execution_shell_or_io_surface() -> None:
    for family, path in _FAMILY_MODULES.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))

        imported = _imported_modules(path)
        assert imported.isdisjoint(_FORBIDDEN_MODULES), (family, imported & _FORBIDDEN_MODULES)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in _FORBIDDEN_CALL_NAMES, (family, func.id)
            elif isinstance(func, ast.Attribute):
                assert func.attr not in _FORBIDDEN_CALL_ATTRIBUTES, (family, func.attr)


def test_node_family_modules_reference_no_a3_02_vocabulary() -> None:
    """A3.03 must not interpret or name the A3.02-owned node kinds."""
    for family, path in _FAMILY_MODULES.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in _A3_02_KIND_ATTRIBUTES, (family, node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in _A3_02_KIND_VALUES, (family, node.value)


def test_contract_public_methods_are_data_only() -> None:
    """Contract classes expose only canonical data operations — nothing that
    runs, waits, interprets, or grants."""
    from agentx.procedures.branch import BranchContract
    from agentx.procedures.transform import TransformContract
    from agentx.procedures.wait import WaitContract

    for contract in (BranchContract, TransformContract, WaitContract):
        methods = {
            name
            for name, member in vars(contract).items()
            if not name.startswith("_") and callable(member)
        }
        assert methods <= _ALLOWED_METHODS, (contract.__name__, sorted(methods))


def test_node_family_module_public_names_carry_no_authority_verbs() -> None:
    import agentx.procedures.branch as branch_module
    import agentx.procedures.transform as transform_module
    import agentx.procedures.wait as wait_module

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
        "sleep",
        "block",
        "poll",
    )
    for module in (branch_module, transform_module, wait_module):
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


# ---------------------------------------------------------------------------
# A3.01 stays intact; C2.03 storage stays opaque
# ---------------------------------------------------------------------------


def test_a3_01_graph_ir_public_api_is_unchanged() -> None:
    import agentx.procedures.graph as graph_module

    assert set(graph_module.__all__) == _A3_01_GRAPH_API


def test_procedure_store_stays_opaque_to_node_family_contracts() -> None:
    store_source = (_AGENTX_SRC / "infrastructure" / "procedure_store.py").read_text(
        encoding="utf-8"
    )
    for token in (
        "agentx.procedures.branch",
        "agentx.procedures.transform",
        "agentx.procedures.wait",
        "BranchContract",
        "BranchOutcome",
        "TransformContract",
        "WaitContract",
    ):
        assert token not in store_source, token

    # And the graph IR module itself must not know about the contracts either:
    # the node-family payloads stay opaque to the A3.01 structure.
    graph_source = (_AGENTX_SRC / "procedures" / "graph.py").read_text(encoding="utf-8")
    for token in ("BranchContract", "TransformContract", "WaitContract"):
        assert token not in graph_source, token
