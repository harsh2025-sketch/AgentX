"""Architecture guardrails for the A5.02 Windows process discovery boundary.

Static and subprocess-probe checks that run identically on Windows, Linux and
macOS and never depend on a real desktop. They pin the A5.02 invariants:

* Win32/``ctypes`` knowledge lives only in isolated Windows native seam modules;
* discovery reuses the A5.01 provider verdicts and the canonical capability
  ABI, duplicates none of them, and performs no registry wiring;
* importing the modules performs no native load, no platform detection, no
  registration, and no native read;
* the runtime dependency set stays empty (stdlib-only, no pywin32, no
  pywinauto).

These are architecture guardrails, not security enforcement; authority
remains owned by the Trusted Kernel.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_WINDOWS_PKG = _SRC_ROOT / "agentx" / "capabilities" / "windows"
_DISCOVERY = _WINDOWS_PKG / "process_discovery.py"
_NATIVE = _WINDOWS_PKG / "_native.py"
_UIA_NATIVE = _WINDOWS_PKG / "_uia_native.py"
_NATIVE_MUTATION = _WINDOWS_PKG / "native_mutation.py"
_NATIVE_SEAMS = frozenset({_NATIVE, _UIA_NATIVE, _NATIVE_MUTATION})

# Native markers that may appear ONLY inside isolated seam modules.
_NATIVE_ONLY_MARKERS = ("windll", "WinDLL", "WINFUNCTYPE", "kernel32", "user32")

# Classes A5.02 is allowed to define in the discovery module.
_EXPECTED_DISCOVERY_CLASSES = frozenset(
    {
        "MetadataStatus",
        "WindowsWindowIdentity",
        "WindowsProcessIdentity",
        "WindowsProcessSnapshot",
        "NativeWindowsSurface",
        "ToolhelpNativeSurface",
        "WindowsProcessDiscovery",
        "WindowsProcessDiscoveryParams",
        "WindowsProcessDiscoveryCapability",
        "_NormalizedProcess",
        "_NormalizedWindow",
    }
)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _windows_sources() -> tuple[Path, ...]:
    return tuple(sorted(_WINDOWS_PKG.rglob("*.py")))


def test_process_discovery_lives_in_the_capabilities_subsystem() -> None:
    assert _DISCOVERY.is_file()
    assert _NATIVE.is_file()
    assert _UIA_NATIVE.is_file()
    assert _DISCOVERY.parent.parent.name == "capabilities"


def test_native_seams_are_the_only_win32_knowledge_sites() -> None:
    for path in _windows_sources():
        if path in _NATIVE_SEAMS:
            continue
        source = path.read_text(encoding="utf-8")
        for marker in _NATIVE_ONLY_MARKERS:
            assert marker not in source, f"{path.name} mentions native marker {marker}"
        for module in _imports(path):
            assert module.split(".")[0] != "ctypes", f"{path.name} imports ctypes"


def test_discovery_reuses_the_canonical_contracts() -> None:
    imported = _imports(_DISCOVERY)
    assert "agentx.capabilities.abi" in imported
    assert "agentx.capabilities.windows.provider" in imported
    assert "agentx.core.errors" in imported
    assert "agentx.core.result" in imported
    assert "agentx.core.execution" in imported
    assert "agentx.kernel.permissions" in imported
    assert "agentx.kernel.risk" in imported


def test_discovery_never_takes_over_registry_wiring() -> None:
    for path in _windows_sources():
        assert "agentx.capabilities.registry" not in _imports(path)


def test_discovery_defines_no_shadow_contracts() -> None:
    tree = ast.parse(_DISCOVERY.read_text(encoding="utf-8"))
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert defined == _EXPECTED_DISCOVERY_CLASSES
    for banned in (
        "WindowsCapability",
        "WindowsCapabilityV2",
        "CapabilityV2",
        "Capability",
        "CapabilityDescriptor",
        "CapabilityRegistry",
    ):
        assert banned not in defined


def test_discovery_respects_the_canonical_subsystem_edges() -> None:
    allowed = {_architecture.CORE, _architecture.KERNEL, _architecture.CAPABILITIES}
    for path in _windows_sources():
        for module in _imports(path):
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
            assert owner is None or owner in allowed, f"{path.name} -> {module}"


def test_importing_discovery_in_a_clean_interpreter_loads_nothing_native() -> None:
    """Import safety, provable on any host: no native/automation module loads."""
    probe = (
        "import sys\n"
        "import agentx.capabilities.abi  # noqa: F401\n"
        "import agentx.core.errors  # noqa: F401\n"
        "import agentx.core.result  # noqa: F401\n"
        "import agentx.capabilities.windows.provider  # noqa: F401\n"
        "before = set(sys.modules)\n"
        "import agentx.capabilities.windows._native  # noqa: F401\n"
        "import agentx.capabilities.windows.process_discovery  # noqa: F401\n"
        "added = set(sys.modules) - before\n"
        "forbidden = {'ctypes', 'win32api', 'win32gui', 'win32con', 'comtypes',\n"
        "             'pywinauto', 'uiautomation', 'winreg', 'pythoncom',\n"
        "             'win32com', 'msvcrt', 'subprocess'}\n"
        "roots = {name.split('.')[0] for name in added}\n"
        "assert not (roots & forbidden), sorted(roots & forbidden)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_importing_discovery_performs_no_native_read() -> None:
    """Every native read happens only inside an explicit discovery call."""
    probe = (
        "import agentx.capabilities.windows._native as native\n"
        "def _boom(*args, **kwargs):\n"
        "    raise AssertionError('native read performed at import time')\n"
        "native.enumerate_processes_raw = _boom\n"
        "native.enumerate_windows_raw = _boom\n"
        "native.query_executable_path_raw = _boom\n"
        "import agentx.capabilities.windows.process_discovery as discovery\n"
        "assert discovery.WINDOWS_PROCESS_DISCOVERY_IDENTITY is not None\n"
        "surface = discovery.ToolhelpNativeSurface()\n"
        "assert surface is not None\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_importing_discovery_performs_no_platform_detection() -> None:
    """``platform``/``sys`` reads happen only inside explicit function calls."""
    probe = (
        "import agentx.capabilities.windows.provider  # noqa: F401\n"
        "import platform\n"
        "calls = []\n"
        "for name in ('system', 'release', 'version', 'machine'):\n"
        "    original = getattr(platform, name)\n"
        "    def spy(*args, _name=name, _original=original, **kwargs):\n"
        "        calls.append(_name)\n"
        "        return _original(*args, **kwargs)\n"
        "    setattr(platform, name, spy)\n"
        "import agentx.capabilities.windows._native  # noqa: F401\n"
        "import agentx.capabilities.windows.process_discovery  # noqa: F401\n"
        "assert calls == [], calls\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_importing_discovery_registers_nothing() -> None:
    probe = (
        "import agentx.capabilities.windows.process_discovery  # noqa: F401\n"
        "from agentx.capabilities.registry import CapabilityRegistry\n"
        "assert len(CapabilityRegistry()) == 0\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_windows_discovery_keeps_the_runtime_dependency_set_empty() -> None:
    """A5.02 adds no dependency: native seams remain stdlib-only."""
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject
    for banned in ("pywin32", "pywinauto", "comtypes", "uiautomation", "psutil"):
        assert banned not in pyproject
