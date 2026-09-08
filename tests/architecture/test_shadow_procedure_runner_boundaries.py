"""Architecture boundary guards for the N2.16 shadow-procedure validation runner.

The runner is a top-level ``agentx`` composition module. These static guards
prove it stays a pure controlled orchestrator: it imports only the inward
``agentx.core`` evidence contracts plus safe standard-library modules; it has
no store, authority, capability, interpreter, model, or sandbox dependency; it
spawns no shell/subprocess and touches no filesystem/network; and it emits the
already-canonical M5.05 / C4.07 ``ShadowRepairResult`` contract without
redefining or editing any canonical worker file.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "shadow_procedure_runner.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_CORE = _REPO_ROOT / "src" / "agentx" / "core"
_SHADOW_REPAIR = (_CORE / "shadow_repair.py").read_text(encoding="utf-8")
_PERSISTENCE = (_REPO_ROOT / "src" / "agentx" / "infrastructure" / "persistence.py").read_text(
    encoding="utf-8"
)


def _imports() -> set[str]:
    tree = ast.parse(_SOURCE)
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def _call_names() -> set[str]:
    tree = ast.parse(_SOURCE)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node, ast.Name):
            names.add(node.func.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.func.attr)
    return names


def _required_files() -> tuple[Path, ...]:
    return (
        _REPO_ROOT / "src" / "agentx" / "shadow_procedure_runner.py",
        _REPO_ROOT / "tests" / "unit" / "test_shadow_procedure_runner.py",
        _REPO_ROOT / "tests" / "integration" / "test_shadow_procedure_runner.py",
        _REPO_ROOT / "tests" / "adversarial" / "test_shadow_procedure_runner_authority.py",
        _REPO_ROOT / "tests" / "architecture" / "test_shadow_procedure_runner_boundaries.py",
        _REPO_ROOT / "docs" / "shadow_procedure_runner.md",
    )


def test_hard_file_ownership_is_satisfied() -> None:
    assert all(path.is_file() for path in _required_files())
    # The runner lives at the agentx namespace root, not inside a subsystem.
    assert _MODULE.parent.name == "agentx"
    assert _MODULE.name == "shadow_procedure_runner.py"


def test_module_imports_only_inward_core_evidence_contracts() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx")}
    assert agentx_imports == {
        "agentx.core.events",
        "agentx.core.ids",
        "agentx.core.procedures",
        "agentx.core.shadow_repair",
    }
    for foreign in (
        "agentx.kernel",
        "agentx.infrastructure",
        "agentx.capabilities",
        "agentx.hive",
        "agentx.cognition",
        "agentx.procedures",
        "agentx.learning",
    ):
        assert not any(name.startswith(foreign) for name in agentx_imports)


def test_module_uses_only_safe_standard_library_modules() -> None:
    non_agentx = {name.split(".")[0] for name in _imports() if not name.startswith("agentx")}
    assert non_agentx <= {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "typing",
        "uuid",
    }
    assert non_agentx.isdisjoint(
        {
            "os",
            "subprocess",
            "sys",
            "shutil",
            "tempfile",
            "pathlib",
            "socket",
            "sqlite3",
            "importlib",
            "ctypes",
            "pickle",
            "multiprocessing",
            "threading",
            "asyncio",
            "requests",
            "urllib",
            "http",
        }
    )


def test_module_has_no_shell_execution_or_filesystem_primitives() -> None:
    forbidden_calls = {
        "eval",
        "exec",
        "compile",
        "open",
        "system",
        "popen",
        "urlopen",
        "read",
        "write",
        "mkdir",
        "unlink",
        "remove",
        "fork",
        "spawn",
    }
    assert _call_names().isdisjoint(forbidden_calls)
    assert "__import__" not in _call_names()


def test_module_defines_no_authority_store_or_sandbox_surface() -> None:
    # The runner is pure orchestration: it introduces no competing store,
    # sandbox, executor, or authority type and no lifecycle mutation calls.
    for token in (
        "ProcedureStore",
        "ShadowRepairStore",
        "ShadowSandbox",
        "Sandbox",
        "ActionGate",
        "EmergencyStop",
        "AuthorityContext",
        "PermissionEngine",
        "class Sandbox",
        "update_status",
        ".insert(",
        "request_stop",
        "clear_stop",
        "grant(",
        "mark_active",
        "succeed_task",
    ):
        assert token not in _SOURCE


def test_module_reuses_canonical_evidence_types() -> None:
    # The runner fills the canonical M5.05 / C4.07 contract; it does not
    # redefine a competing evidence vocabulary or a competing disposition enum.
    for token in (
        "ShadowRepairResult",
        "ShadowRepairDisposition",
        "ShadowRepairMode.NON_COMMITTING",
        "ShadowRepairStepEvidence",
        "VerificationPayload",
    ):
        assert token in _SOURCE
    # No competing disposition enum with PASSED/FAILED member names is defined.
    assert "class ShadowRepairDisposition" not in _SOURCE


def test_module_does_not_depend_on_later_wave_work() -> None:
    # N2.16 is standalone: it must not require any sibling worker module.
    for token in (
        "repair_validation",
        "procedure_replacement",
        "repair_workflow",
        "repair_patch_materializer",
        "procedure_store",
    ):
        assert token not in _SOURCE.lower()


def test_canonical_core_contracts_remain_untouched() -> None:
    # The canonical shadow-repair evidence contract still owns its classes and
    # never learns about the runner.
    assert "class ShadowRepairResult" in _SHADOW_REPAIR
    assert "shadow_procedure_runner" not in _SHADOW_REPAIR
    assert "run_shadow_validation" not in _SHADOW_REPAIR
    # No persistence or migration surface was added for shadow runs.
    migration_versions = [
        int(match) for match in re.findall(r"^\s*version=(\d+),", _PERSISTENCE, re.MULTILINE)
    ]
    assert migration_versions == sorted(migration_versions)
    assert "shadow_procedure" not in _PERSISTENCE
    assert "shadow_repair_runs" not in _PERSISTENCE


def test_docs_page_exists_and_describes_the_controlled_boundary() -> None:
    docs = _REPO_ROOT / "docs" / "shadow_procedure_runner.md"
    assert docs.is_file()
    text = docs.read_text(encoding="utf-8")
    for token in (
        "N2.16",
        "shadow",
        "non_committing",
        "ShadowRepairResult",
        "controlled",
        "harness",
        "no live mutation",
        "bounded",
        "verification",
    ):
        assert token.lower() in text.lower()
