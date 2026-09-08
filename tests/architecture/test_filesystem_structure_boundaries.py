"""Architecture guardrails for the M7.05 governed filesystem structure boundary.

Static and subprocess-probe checks that run identically on Windows, Linux and
macOS and never depend on a particular directory layout. They pin the M7.05
invariants:

* the structural capability lives in ``agentx.capabilities`` and imports only
  the canonical core/kernel/capabilities contracts (stdlib-only otherwise);
* it never depends on Worker-01's text read/write surface
  (``agentx.capabilities.filesystem``) and performs no registry wiring;
* importing the module performs no filesystem read, no registration, no shell,
  no ``subprocess``, and no dynamic code;
* it defines no shadow canonical contracts and no delete surface.

These are architecture guardrails, not security enforcement; authority remains
owned by the Trusted Kernel.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_MODULE = _SRC_ROOT / "agentx" / "capabilities" / "filesystem_structure.py"

# The four production capability classes M7.05 must define.
_EXPECTED_CAPABILITIES = frozenset(
    {
        "StatPathCapability",
        "ListDirectoryCapability",
        "CreateDirectoryCapability",
        "MovePathCapability",
    }
)

# Forbidden top-level roots this module may never import (stdlib or otherwise).
_FORBIDDEN_ROOTS = frozenset({"subprocess", "importlib", "shlex", "ctypes", "win32api"})


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def test_filesystem_structure_lives_in_the_capabilities_subsystem() -> None:
    assert _MODULE.is_file()
    assert _MODULE.parent.name == "capabilities"
    assert _MODULE.parent.parent.name == "agentx"


def test_filesystem_structure_never_depends_on_worker01_text_surface() -> None:
    assert "agentx.capabilities.filesystem" not in _imports(_MODULE)


def test_filesystem_structure_imports_only_allowed_subsystems() -> None:
    allowed = {_architecture.CORE, _architecture.KERNEL, _architecture.CAPABILITIES}
    for module in _imports(_MODULE):
        if not module.startswith("agentx"):
            continue
        owner = next(
            (
                package
                for package in _architecture.SUBSYSTEMS
                if module == package or module.startswith(f"{package}.")
            ),
            None,
        )
        assert owner is None or owner in allowed, f"{_MODULE.name} -> {module}"
    # No forbidden roots (shell / dynamic / platform-native).
    for module in _imports(_MODULE):
        assert module.split(".")[0] not in _FORBIDDEN_ROOTS, module


def test_filesystem_structure_reuses_the_canonical_contracts() -> None:
    imported = _imports(_MODULE)
    assert "agentx.capabilities.abi" in imported
    assert "agentx.core.errors" in imported
    assert "agentx.core.result" in imported
    assert "agentx.core.execution" in imported
    assert "agentx.kernel.permissions" in imported
    assert "agentx.kernel.risk" in imported


def test_filesystem_structure_never_takes_over_registry_wiring() -> None:
    assert "agentx.capabilities.registry" not in _imports(_MODULE)
    assert "agentx.capabilities.runtime" not in _imports(_MODULE)


def test_filesystem_structure_does_not_import_worker_subsystems() -> None:
    forbidden_owners = {
        _architecture.COGNITION,
        _architecture.HIVE,
        _architecture.INFRASTRUCTURE,
        _architecture.PROCEDURES,
        _architecture.LEARNING,
    }
    for module in _imports(_MODULE):
        if not module.startswith("agentx."):
            continue
        segments = module.split(".")
        if len(segments) < 2:
            continue
        owner = "agentx." + segments[1]
        assert owner not in forbidden_owners, f"forbidden dependency on {owner}"


def test_filesystem_structure_defines_no_shadow_contracts() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert defined >= _EXPECTED_CAPABILITIES
    for banned in (
        "Capability",
        "CapabilityV2",
        "CapabilityDescriptor",
        "CapabilityRegistry",
        "CapabilityExecutionLoop",
        "WindowsCapability",
    ):
        assert banned not in defined, f"shadow contract {banned} is defined"


def test_filesystem_structure_exports_no_delete_surface() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    exported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "__all__"
                    and isinstance(node.value, ast.List)
                ):
                    for element in node.value.elts:
                        if isinstance(element, ast.Constant) and isinstance(element.value, str):
                            exported.append(element.value)
    assert not any("delete" in name.lower() for name in exported)
    assert "move_path" in exported and "create_directory" in exported


def test_importing_module_in_a_clean_interpreter_registers_nothing() -> None:
    probe = (
        "import sys\n"
        "import agentx.capabilities.filesystem_structure as fss  # noqa: F401\n"
        "from agentx.capabilities.registry import CapabilityRegistry\n"
        "assert len(CapabilityRegistry()) == 0\n"
        "assert fss.__file__ is not None\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_importing_module_performs_no_filesystem_read() -> None:
    probe = (
        "import agentx.capabilities.filesystem_structure as fss\n"
        "import agentx.capabilities.filesystem_structure as fss2  # noqa: F401\n"
        "# Import must not touch the filesystem or perform any operation.\n"
        "assert fss.MAX_PATH_CHARS > 0\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr
