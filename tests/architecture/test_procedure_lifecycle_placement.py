"""Architecture guardrails for the M4.03 procedure lifecycle transition policy.

These are import/structure guardrails, not security enforcement: authority is
owned exclusively by the Trusted Kernel. They prove the policy sits inward in
``agentx.core``, consumes only canonical core contracts plus the standard
library, duplicates no canonical vocabulary, adds no persistence, does not
reverse the record/storage dependency direction, and required no widening of
the canonical architecture manifest.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture
from agentx.core.procedures import ProcedureStatus

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_LIFECYCLE_POLICY = _AGENTX_SRC / "core" / "procedure_lifecycle.py"
_PROCEDURE_CONTRACT = _AGENTX_SRC / "core" / "procedures.py"
_PROCEDURE_STORE = _AGENTX_SRC / "infrastructure" / "procedure_store.py"
_ARCHITECTURE_MANIFEST = _AGENTX_SRC / "_architecture.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in _AGENTX_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree)):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


# ---------------------------------------------------------------------------
# Placement.
# ---------------------------------------------------------------------------


def test_lifecycle_policy_lives_inside_the_core_boundary() -> None:
    assert _LIFECYCLE_POLICY.is_file()
    assert _LIFECYCLE_POLICY.parent.name == "core"
    assert _LIFECYCLE_POLICY.parent.parent.name == "agentx"


def test_lifecycle_policy_imports_only_core_contracts_and_stdlib() -> None:
    imported = _imports(_LIFECYCLE_POLICY)
    agentx_imports = [module for module in imported if module.startswith("agentx.")]
    assert agentx_imports, "the policy must consume canonical core contracts"
    assert set(agentx_imports) == {"agentx.core.ids", "agentx.core.procedures"}


def test_lifecycle_policy_imports_no_other_subsystem() -> None:
    imported = _imports(_LIFECYCLE_POLICY)
    for module in imported:
        for subsystem in _architecture.SUBSYSTEMS:
            if subsystem == _architecture.CORE:
                continue
            assert module != subsystem and not module.startswith(f"{subsystem}."), (
                f"lifecycle policy must not import {subsystem} (found {module})"
            )


def test_lifecycle_policy_has_no_persistence_transport_or_runtime_imports() -> None:
    imported = _imports(_LIFECYCLE_POLICY)
    for forbidden in (
        "sqlite3",
        "os",
        "pathlib",
        "socket",
        "subprocess",
        "threading",
        "asyncio",
        "random",
        "time",
        "uuid",
        "logging",
        "urllib.request",
        "http.client",
        "agentx.infrastructure.persistence",
        "agentx.infrastructure.procedure_store",
        "agentx.kernel.permissions",
        "agentx.kernel.action_gate",
        "agentx.kernel.risk",
        "agentx.kernel.resource_budget",
        "agentx.kernel.emergency_stop",
        "agentx.core.tasks",
        "agentx.core.task_state",
    ):
        assert forbidden not in imported, forbidden


def test_lifecycle_policy_is_free_of_io_and_dynamic_execution() -> None:
    forbidden_calls = {
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
        "system",
        "popen",
        "remove",
        "unlink",
        "rmtree",
        "print",
        "input",
        "now",
        "utcnow",
        "uuid4",
        "sleep",
    }
    for node in ast.walk(_tree(_LIFECYCLE_POLICY)):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else "")
            )
            assert name not in forbidden_calls, (
                f"lifecycle policy must not call {name!r}; it is a pure decision boundary"
            )


# ---------------------------------------------------------------------------
# No duplicated canonical vocabulary; canonical contracts untouched.
# ---------------------------------------------------------------------------


def test_procedure_status_still_has_exactly_one_canonical_definition() -> None:
    assert _class_definitions("ProcedureStatus") == [Path("agentx/core/procedures.py")]
    assert _class_definitions("ProcedureRecord") == [Path("agentx/core/procedures.py")]
    assert _class_definitions("ProcedureStore") == [
        Path("agentx/infrastructure/procedure_store.py")
    ]


def test_lifecycle_policy_defines_no_competing_record_or_status_type() -> None:
    defined = {
        node.name for node in _tree(_LIFECYCLE_POLICY).body if isinstance(node, ast.ClassDef)
    }
    assert defined == {
        "ProcedureLifecycleError",
        "ProcedureLifecycleReason",
        "ProcedureLifecycleDecision",
        "ProcedureLifecycleAssessment",
    }


def test_canonical_procedure_status_vocabulary_is_unchanged() -> None:
    """The policy reuses the closed C2.03 vocabulary; it never widens it."""
    assert {status.name: status.value for status in ProcedureStatus} == {
        "CANDIDATE": "candidate",
        "ACTIVE": "active",
        "RETIRED": "retired",
    }
    assert not hasattr(ProcedureStatus, "DEGRADED")


def test_record_contract_and_store_do_not_depend_on_the_lifecycle_policy() -> None:
    """Storage and the record contract keep owning no lifecycle policy."""
    for path in (_PROCEDURE_CONTRACT, _PROCEDURE_STORE):
        assert "agentx.core.procedure_lifecycle" not in _imports(path), path


def test_architecture_manifest_needs_no_new_edge_for_this_policy() -> None:
    """``agentx.core`` remains a dependency leaf: no manifest widening needed."""
    assert all(
        source != _architecture.CORE for source, _ in _architecture.ALLOWED_ARCHITECTURE_EDGES
    )
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
    assert "agentx.core.procedure_lifecycle" not in _ARCHITECTURE_MANIFEST.read_text(
        encoding="utf-8"
    )


def test_core_package_initializer_stays_declarative() -> None:
    init_path = _AGENTX_SRC / "core" / "__init__.py"
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    assert len(tree.body) == 1
    statement = tree.body[0]
    assert isinstance(statement, ast.Expr)
    assert isinstance(statement.value, ast.Constant)


# ---------------------------------------------------------------------------
# Scope: exactly one new production module.
# ---------------------------------------------------------------------------


def test_lifecycle_policy_is_the_only_lifecycle_production_module() -> None:
    matches = sorted(
        path.relative_to(_SRC_ROOT) for path in _AGENTX_SRC.rglob("*procedure_lifecycle*.py")
    )
    assert matches == [Path("agentx/core/procedure_lifecycle.py")]


def test_lifecycle_policy_declares_an_explicit_public_surface() -> None:
    tree = _tree(_LIFECYCLE_POLICY)
    exported: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            assert isinstance(node.value, ast.List)
            exported = [
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
    assert exported, "the policy must declare __all__"
    assert "assess_procedure_transition" in exported
    assert all(not name.startswith("_") for name in exported)
