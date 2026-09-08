"""Architecture-boundary tests for M4.04 procedure applicability matching.

These tests are static: they parse the module with :mod:`ast` and prove it
stays inside ``agentx.core``, holds no I/O surface, reads no payload, reuses
canonical vocabulary instead of inventing any, and exposes no routing,
selection, ranking, or execution entry point.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture
from tests.unit.test_package import BOUNDARY_PACKAGES

_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "agentx" / "core" / "procedure_matching.py"
)

#: Modules a pure core policy must not reach for: no I/O, no clock, no
#: network, no process, no dynamic import, and no text-similarity machinery.
_FORBIDDEN_STDLIB_ROOTS = frozenset(
    {
        "asyncio",
        "concurrent",
        "ctypes",
        "datetime",
        "difflib",
        "fnmatch",
        "http",
        "importlib",
        "json",
        "logging",
        "multiprocessing",
        "os",
        "pathlib",
        "pickle",
        "re",
        "socket",
        "sqlite3",
        "ssl",
        "subprocess",
        "tempfile",
        "threading",
        "time",
        "unicodedata",
        "urllib",
    }
)

_FORBIDDEN_AGENTX_PREFIXES = (
    "agentx.capabilities",
    "agentx.cognition",
    "agentx.hive",
    "agentx.infrastructure",
    "agentx.kernel",
    "agentx.learning",
    "agentx.procedures",
)

_FORBIDDEN_CALLS = frozenset(
    {
        "__import__",
        "compile",
        "connect",
        "eval",
        "exec",
        "input",
        "load",
        "loads",
        "now",
        "open",
        "popen",
        "print",
        "read",
        "run",
        "sleep",
        "system",
        "urlopen",
        "utcnow",
        "write",
    }
)

_EXPECTED_PUBLIC_SURFACE = frozenset(
    {
        "CapabilityRequirement",
        "ProcedureApplicabilityMatcher",
        "ProcedureCandidate",
        "ProcedureLifecycleNote",
        "ProcedureMatchOutcome",
        "ProcedureMatchReason",
        "ProcedureMatchReasonCode",
        "ProcedureMatchResult",
        "ProcedureMatchValidationError",
        "ProcedureRequirement",
    }
)


def _tree() -> ast.Module:
    return ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))


def _imports() -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def _imported_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


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


def _accessed_attributes() -> set[str]:
    return {node.attr for node in ast.walk(_tree()) if isinstance(node, ast.Attribute)}


def _module_level_targets() -> set[str]:
    targets: set[str] = set()
    for node in _tree().body:
        if isinstance(node, ast.Assign):
            targets.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets.add(node.target.id)
    return targets


def test_module_is_placed_in_the_core_boundary() -> None:
    assert _MODULE_PATH.is_file()
    assert _MODULE_PATH.parts[-3:] == ("agentx", "core", "procedure_matching.py")


def test_module_imports_only_stdlib_and_core() -> None:
    for name in _imports():
        if name.startswith("agentx"):
            assert name.startswith("agentx.core"), f"outward import: {name}"
        else:
            assert name.split(".", maxsplit=1)[0] not in _FORBIDDEN_STDLIB_ROOTS, name


def test_module_imports_no_outward_subsystem() -> None:
    assert not any(
        name.startswith(prefix) for name in _imports() for prefix in _FORBIDDEN_AGENTX_PREFIXES
    )


def test_module_reuses_canonical_core_contracts() -> None:
    names = _imported_names()
    assert "CapabilityId" in names  # canonical identity, not a local invention
    assert "ProcedureId" in names
    assert {"ProcedureRecord", "ProcedureScope", "ProcedureScopeDimension"} <= names
    assert {"ProcedureStatus", "ProcedureValidationError"} <= names


def test_module_never_reaches_the_procedure_payload() -> None:
    # Payload inertness, statically: the module never names the payload or its
    # content, so it cannot parse, search, or compare it.
    assert "ProcedurePayload" not in _imported_names()
    assert not {"payload", "content"} & _accessed_attributes()


def test_module_defines_no_new_scope_vocabulary() -> None:
    classes = {node.name for node in _tree().body if isinstance(node, ast.ClassDef)}
    assert not any(name.endswith(("Scope", "Dimension", "Platform")) for name in classes)
    enums = {
        node.name
        for node in _tree().body
        if isinstance(node, ast.ClassDef) and "StrEnum" in {_base_name(base) for base in node.bases}
    }
    assert enums == {
        "ProcedureMatchOutcome",
        "ProcedureMatchReasonCode",
        "ProcedureLifecycleNote",
    }


def _base_name(node: ast.expr) -> str:
    return node.id if isinstance(node, ast.Name) else getattr(node, "attr", "")


def test_module_declares_no_module_level_mutable_state() -> None:
    targets = _module_level_targets()
    assert targets
    for name in targets:
        if name.startswith("__") and name.endswith("__"):
            continue  # dunder metadata (``__all__``), not policy state
        assert name.isupper(), f"module-level state must be a named constant, got {name!r}"


def test_declared_all_matches_the_public_contract() -> None:
    declared: list[str] = []
    for node in _tree().body:
        target_names: list[str] = []
        value: ast.expr | None
        if isinstance(node, ast.Assign):
            target_names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_names = [node.target.id]
            value = node.value
        else:
            continue
        if "__all__" in target_names and isinstance(value, ast.List | ast.Tuple):
            declared = [
                element.value
                for element in value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]

    assert set(declared) == _EXPECTED_PUBLIC_SURFACE | {"MAX_VERSION_TOKEN_LENGTH"}
    assert len(declared) == len(set(declared))


def test_module_calls_no_io_clock_or_dynamic_import_surface() -> None:
    assert not _called_names() & _FORBIDDEN_CALLS


def test_module_exposes_no_routing_selection_ranking_or_execution_entry_point() -> None:
    forbidden = {
        "activate",
        "best",
        "choose",
        "execute",
        "promote",
        "rank",
        "route",
        "search",
        "select",
    }
    defined = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    assert not defined & forbidden
    assert not _EXPECTED_PUBLIC_SURFACE & forbidden


def test_public_surface_is_exactly_the_documented_contract() -> None:
    defined = {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and not node.name.startswith("_")
    }
    assert defined == _EXPECTED_PUBLIC_SURFACE


def test_module_declares_no_async_or_background_entry_point() -> None:
    kinds = {type(node).__name__ for node in ast.walk(_tree())}
    assert "AsyncFunctionDef" not in kinds
    assert "Await" not in kinds
    assert "Global" not in kinds
    assert "Nonlocal" not in kinds


def test_canonical_boundary_manifest_is_unchanged_by_this_task() -> None:
    # Core remains the inward foundation: it may never gain an outward edge.
    assert all(
        source != _architecture.CORE for source, _target in _architecture.ALLOWED_ARCHITECTURE_EDGES
    )
    assert _architecture.SUBSYSTEMS == BOUNDARY_PACKAGES
    assert _architecture.CORE == "agentx.core"
