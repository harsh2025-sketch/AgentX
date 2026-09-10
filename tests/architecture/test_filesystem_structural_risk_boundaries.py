"""Architecture boundary tests for the filesystem structural risk policy (N2.22).

The policy is a pure classifier: it may import only the standard library and
the canonical ``agentx.kernel.risk`` contract. It must not import any
filesystem toolkit, any other subsystem, any authority component, or any
persistence/model machinery, and it must not contain calls that could perform
filesystem I/O or dynamic execution.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "agentx"
    / "capabilities"
    / "filesystem_structural_risk.py"
)

#: The only canonical import this module is allowed to establish.
_ALLOWED_AGENTX_IMPORT: str = "agentx.kernel.risk"

_FORBIDDEN_AGENTX_PREFIXES = (
    "agentx.cognition",
    "agentx.hive",
    "agentx.infrastructure",
    "agentx.procedures",
    "agentx.learning",
    "agentx.agent_loop",
    "agentx.kernel.action_gate",
    "agentx.kernel.permissions",
    "agentx.kernel.resource_budget",
    "agentx.kernel.emergency_stop",
    "agentx.kernel.audit",
    "agentx.core.tasks",
    "agentx.core.execution",
    "agentx.core.events",
    "agentx.core.artifacts",
    "agentx.capabilities.abi",
    "agentx.capabilities.filesystem",
    "agentx.capabilities.runtime",
    "agentx.capabilities.executor",
    "agentx.capabilities.verifier",
    "agentx.capabilities.registry",
)

_FORBIDDEN_MODULES = frozenset(
    {
        "os",
        "os.path",
        "pathlib",
        "shutil",
        "subprocess",
        "glob",
        "socket",
        "ftplib",
        "smtplib",
        "http",
        "urllib",
        "requests",
        "importlib",
    }
)

# Bare (global-name) calls that would enable I/O, dynamic execution, or
# subversion of this test. Import checks above already rule out module-qualified
# forms such as ``os.remove`` or ``shutil.move``.
_FORBIDDEN_BARE_CALLS = frozenset(
    {
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
        "input",
        "breakpoint",
    }
)

# Method calls that would perform filesystem probing/mutation, network I/O, or
# persistence. These names are only dangerous when invoked on filesystem,
# socket, or storage objects; the import checks make such objects unreachable,
# so this list is a second, independent guardrail.
_FORBIDDEN_METHOD_CALLS = frozenset(
    {
        "stat",
        "lstat",
        "exists",
        "lexists",
        "is_file",
        "is_dir",
        "is_link",
        "mkdir",
        "makedirs",
        "rmdir",
        "unlink",
        "rename",
        "replace",
        "remove",
        "rmtree",
        "listdir",
        "scandir",
        "walk",
        "glob",
        "rglob",
        "chmod",
        "chown",
        "chflags",
        "access",
        "readlink",
        "symlink",
        "link",
        "copy",
        "copyfile",
        "copytree",
        "expanduser",
        "abspath",
        "realpath",
        "normpath",
        "normcase",
        "getsize",
        "getmtime",
        "open",
        "read",
        "read_bytes",
        "read_text",
        "write",
        "write_bytes",
        "write_text",
        "touch",
        "send",
        "connect",
        "request",
        "persist",
        "publish",
        "save",
        "commit",
    }
)

_EXPECTED_PUBLIC_API = frozenset(
    {
        "MAX_PATH_LENGTH",
        "DeletionScope",
        "FilesystemStructuralOperationKind",
        "FilesystemStructuralRiskPolicy",
        "FilesystemStructuralRiskRequest",
        "FilesystemStructuralRiskResult",
        "TargetState",
        "classify_filesystem_structural_risk",
    }
)


def _tree() -> ast.Module:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"), filename=str(_SOURCE))


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def _called_bare_names(tree: ast.AST) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _called_method_names(tree: ast.AST) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def test_risk_policy_establishes_only_the_canonical_risk_edge() -> None:
    modules = _imported_modules(_tree())
    agentx_modules = {module for module in modules if module.startswith("agentx")}

    assert agentx_modules == {_ALLOWED_AGENTX_IMPORT}
    for module in modules:
        assert not module.startswith(_FORBIDDEN_AGENTX_PREFIXES), module


def test_risk_policy_imports_no_filesystem_or_io_toolkits() -> None:
    modules = _imported_modules(_tree())

    assert modules.isdisjoint(_FORBIDDEN_MODULES)


def test_risk_policy_has_no_forbidden_dynamic_or_io_bare_calls() -> None:
    called = _called_bare_names(_tree())

    assert called.isdisjoint(_FORBIDDEN_BARE_CALLS)


def test_risk_policy_has_no_forbidden_io_or_shell_method_calls() -> None:
    called = _called_method_names(_tree())

    assert called.isdisjoint(_FORBIDDEN_METHOD_CALLS)


def test_risk_policy_exposes_the_expected_public_api() -> None:
    tree = _tree()
    all_names: list[str] = []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
            )
            and isinstance(node.value, ast.List)
        ):
            all_names = [
                item.value
                for item in node.value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]

    assert frozenset(all_names) == _EXPECTED_PUBLIC_API


def test_risk_policy_public_surface_is_pure() -> None:
    """Every public function takes an explicit request and returns a result."""
    tree = _tree()
    public_functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }

    assert public_functions == {"classify_filesystem_structural_risk"}
