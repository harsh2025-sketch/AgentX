"""Architecture guardrails for the Windows provider boundary (A5.01+).

These are static, deterministic checks. They run identically on Windows,
Linux and macOS and they never touch a native API. They are architecture
guardrails, not security enforcement: authority remains owned by the Trusted
Kernel.

A5.02 introduced the isolated ``_native`` read-only Win32 discovery seam.
A5.03 adds the isolated ``_uia_native`` read-only UI Automation seam. M6
adds a one-shot read-only ``clipboard_native`` observation seam. M10 adds
the isolated ``_screen_native`` read-only desktop observation seam. N2.19 adds
the single controlled ``native_mutation`` Win32 mutation seam. Exactly
those native seam modules may import :mod:`ctypes` lazily; every Windows
module still inherits the same no-third-party and no-module-level-native-load
constraints. The A5.02 ``EnumWindows`` exception remains limited to
``_native``; N2.19 mutation exceptions remain limited to ``native_mutation``.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_WINDOWS_PKG = _AGENTX_SRC / "capabilities" / "windows"
_PROVIDER = _WINDOWS_PKG / "provider.py"
_NATIVE = _WINDOWS_PKG / "_native.py"
_UIA_NATIVE = _WINDOWS_PKG / "_uia_native.py"
_CLIPBOARD_NATIVE = _WINDOWS_PKG / "clipboard_native.py"
_NATIVE_MUTATION = _WINDOWS_PKG / "native_mutation.py"
_NATIVE_SEAMS = frozenset({_NATIVE, _UIA_NATIVE, _CLIPBOARD_NATIVE, _NATIVE_MUTATION})

# Native/automation imports banned in every Windows-package module except the
# explicit native seams below (which still permit only stdlib ``ctypes``).
_FORBIDDEN_IMPORTS = (
    "ctypes",
    "win32api",
    "win32gui",
    "win32con",
    "win32process",
    "win32clipboard",
    "pywintypes",
    "pythoncom",
    "comtypes",
    "win32com",
    "uiautomation",
    "pywinauto",
    "winreg",
    "msvcrt",
    "subprocess",
    "PIL",
    "mss",
    "pytesseract",
    "cv2",
    "numpy",
    "keyboard",
    "mouse",
    "pyautogui",
    "pyperclip",
    "selenium",
    "playwright",
)

# Native seams may use ctypes only as a lazy, call-time import. Module-level
# ctypes remains forbidden, so importing the package loads nothing native.
_NATIVE_SEAM_ALLOWED_IMPORTS = frozenset({"ctypes"})

# Exactly the A5.02 Win32 seam may expose the approved read-only window walk.
# Exactly the N2.19 mutation seam may expose the approved mutation primitives.
_NATIVE_SEAM_ALLOWED_SYMBOLS = {
    _NATIVE: frozenset({"EnumWindows", "GetForegroundWindow"}),
    _NATIVE_MUTATION: frozenset({"SendInput", "SetForegroundWindow", "SetWindowPos"}),
}

# Automation verbs no Windows-package module may implement unless listed in
# the path-specific native seam carve-outs above.
_FORBIDDEN_SYMBOLS = (
    "EnumWindows",
    "FindWindow",
    "GetForegroundWindow",
    "SetForegroundWindow",
    "SendInput",
    "keybd_event",
    "mouse_event",
    "CoInitialize",
    "CreateObject",
    "screenshot",
    "grab_screen",
    "ocr",
)

# Mutating Win32 APIs remain banned everywhere except the single N2.19
# native_mutation seam, where only the explicitly approved narrow primitives
# are recognized.
_FORBIDDEN_WIN32_MUTATIONS = (
    "TerminateProcess",
    "CreateProcess",
    "OpenClipboard",
    "EmptyClipboard",
    "SetClipboardData",
    "SendInput",
    "WriteProcessMemory",
    "ReadProcessMemory",
    "VirtualAllocEx",
    "SetWindowsHookEx",
    "SetForegroundWindow",
    "AllowSetForegroundWindow",
    "SetWindowText",
    "SetWindowPos",
    "ShowWindow",
    "DestroyWindow",
    "CloseWindow",
    "MoveWindow",
    "SetParent",
    "SetWindowLong",
    "PostMessage",
    "SendMessageTimeout",
    "SendMessage",
    "BroadcastSystemMessage",
    "ClipCursor",
    "SetCursorPos",
    "BlockInput",
    "AttachThreadInput",
    "BringWindowToTop",
    "SwitchToThisWindow",
    "ShellExecute",
    "WinExec",
    "SuspendThread",
    "ResumeThread",
    "DebugActiveProcess",
    "GenerateConsoleCtrlEvent",
    "SetThreadDesktop",
)

_CLIPBOARD_NATIVE_ALLOWED_WIN32_NAMES = frozenset({"OpenClipboard"})

_NATIVE_MUTATION_ALLOWED_WIN32_MUTATIONS = frozenset(
    {
        "CreateProcess",
        "OpenClipboard",
        "EmptyClipboard",
        "SetClipboardData",
        "SendInput",
        "SetForegroundWindow",
        "SetWindowPos",
        "ShowWindow",
    }
)

_FORBIDDEN_COUPLING = (
    "agentx.cognition",
    "agentx.hive",
    "agentx.learning",
    "agentx.procedures",
    "agentx.infrastructure",
)


def _windows_sources() -> tuple[Path, ...]:
    return tuple(sorted(_WINDOWS_PKG.rglob("*.py")))


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _top_level_imports(path: Path) -> tuple[str, ...]:
    """Return only the module-level imports of ``path`` (no nested imports)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in tree.body:
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


def test_windows_provider_lives_in_the_capabilities_subsystem() -> None:
    assert _PROVIDER.is_file()
    assert (_WINDOWS_PKG / "__init__.py").is_file()
    assert _WINDOWS_PKG.parent.name == "capabilities"


@pytest.mark.parametrize("path", _windows_sources(), ids=lambda p: p.name)
def test_windows_package_imports_no_native_or_automation_module(path: Path) -> None:
    native_seam = path in _NATIVE_SEAMS
    for module in _imported_modules(path):
        root = module.split(".")[0]
        if native_seam and root in _NATIVE_SEAM_ALLOWED_IMPORTS:
            continue
        assert root not in _FORBIDDEN_IMPORTS, f"{path.name} imports forbidden module {module}"


@pytest.mark.parametrize("path", _windows_sources(), ids=lambda p: p.name)
def test_ctypes_is_never_imported_at_module_level(path: Path) -> None:
    """Native seams may use ctypes, but never at import time."""
    for module in _top_level_imports(path):
        root = module.split(".")[0]
        assert root != "ctypes", f"{path.name} imports {module} at module level"


@pytest.mark.parametrize("path", _windows_sources(), ids=lambda p: p.name)
def test_windows_package_defines_no_automation_surface(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    allowed_symbols = _NATIVE_SEAM_ALLOWED_SYMBOLS.get(path, frozenset())
    for symbol in _FORBIDDEN_SYMBOLS:
        if symbol in allowed_symbols:
            continue
        assert symbol not in names
        assert symbol not in attributes


@pytest.mark.parametrize("path", _windows_sources(), ids=lambda p: p.name)
def test_windows_package_mentions_no_mutating_win32_api(path: Path) -> None:
    """Windows discovery/inspection remains read-only."""
    source = path.read_text(encoding="utf-8")
    for symbol in _FORBIDDEN_WIN32_MUTATIONS:
        if path == _CLIPBOARD_NATIVE and symbol in _CLIPBOARD_NATIVE_ALLOWED_WIN32_NAMES:
            continue
        if path == _NATIVE_MUTATION and symbol in _NATIVE_MUTATION_ALLOWED_WIN32_MUTATIONS:
            continue
        assert symbol not in source, f"{path.name} mentions forbidden Win32 API {symbol}"


@pytest.mark.parametrize("path", _windows_sources(), ids=lambda p: p.name)
def test_windows_package_has_no_model_hive_or_persistence_coupling(path: Path) -> None:
    for module in _imported_modules(path):
        for forbidden in _FORBIDDEN_COUPLING:
            assert module != forbidden and not module.startswith(f"{forbidden}."), (
                f"{path.name} couples to {module}"
            )


@pytest.mark.parametrize("path", _windows_sources(), ids=lambda p: p.name)
def test_windows_package_uses_only_stdlib_and_canonical_agentx(path: Path) -> None:
    stdlib = set(sys.stdlib_module_names)
    for module in _imported_modules(path):
        root = module.split(".")[0]
        assert root in stdlib or root == "agentx", f"{path.name} imports third-party {module}"


def test_provider_reuses_the_canonical_capability_contracts() -> None:
    imported = _imported_modules(_PROVIDER)
    assert "agentx.capabilities.abi" in imported
    assert "agentx.core.errors" in imported
    assert "agentx.core.result" in imported


def test_provider_does_not_take_over_registry_wiring() -> None:
    """A1.09 reserves registry imports for the canonical runtime module."""
    for path in _windows_sources():
        assert "agentx.capabilities.registry" not in _imported_modules(path)


def test_provider_does_not_duplicate_the_capability_abi_or_registry() -> None:
    """No shadow ABI: canonical contracts keep exactly one definition site."""
    for name in (
        "Capability",
        "CapabilityDescriptor",
        "CapabilityIdentity",
        "CapabilityName",
        "CapabilityVersion",
        "CapabilityScope",
        "CapabilityPlatform",
        "CapabilityParams",
        "CapabilityRequest",
        "ExecutionResult",
        "VerificationResult",
    ):
        assert _class_definitions(name) == [Path("agentx/capabilities/abi.py")], name
    assert _class_definitions("CapabilityRegistry") == [Path("agentx/capabilities/registry.py")]


def test_provider_defines_no_windows_capability_variant() -> None:
    tree = ast.parse(_PROVIDER.read_text(encoding="utf-8"))
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert defined == {
        "WindowsProviderIdentity",
        "PlatformFacts",
        "WindowsSupportStatus",
        "WindowsSupport",
        "WindowsProvider",
    }
    for banned in ("WindowsCapability", "WindowsCapabilityV2", "CapabilityV2"):
        assert banned not in defined


def test_windows_package_has_no_module_level_side_effects() -> None:
    """Top-level statements stay declarative: no detection, no registration."""
    for path in _windows_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            assert not isinstance(node, ast.Expr | ast.For | ast.While | ast.With | ast.Try) or (
                isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            ), f"{path.name} has an executable top-level statement"
            if isinstance(node, ast.If):  # pragma: no cover - defensive
                raise AssertionError(f"{path.name} has conditional module-level logic")


def test_windows_package_respects_the_canonical_subsystem_edges() -> None:
    allowed = {_architecture.CORE, _architecture.KERNEL, _architecture.CAPABILITIES}
    for path in _windows_sources():
        for module in _imported_modules(path):
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


def test_importing_the_provider_in_a_clean_interpreter_loads_nothing_native() -> None:
    """Import safety, provable on any host: no native/automation module loads.

    The check is a delta: only modules loaded by importing the Windows package
    itself count. Canonical dependencies are imported first, so stdlib modules
    that legitimately load ctypes on Windows (for example uuid) are never
    misattributed to A5.01.
    """
    probe = (
        "import sys\n"
        "import agentx.capabilities.abi  # noqa: F401\n"
        "import agentx.core.errors  # noqa: F401\n"
        "import agentx.core.result  # noqa: F401\n"
        "before = set(sys.modules)\n"
        "import agentx.capabilities.windows.provider as provider\n"
        "added = set(sys.modules) - before\n"
        "forbidden = {'ctypes', 'win32api', 'win32gui', 'win32con', 'comtypes',\n"
        "             'pywinauto', 'uiautomation', 'winreg', 'pythoncom',\n"
        "             'win32com', 'msvcrt', 'subprocess'}\n"
        "roots = {name.split('.')[0] for name in added}\n"
        "assert not (roots & forbidden), sorted(roots & forbidden)\n"
        "assert provider.WINDOWS_PROVIDER_NAME == 'windows'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_importing_the_provider_performs_no_platform_detection() -> None:
    """``platform``/``sys`` reads happen only inside explicit function calls."""
    probe = (
        "import agentx.capabilities.abi  # noqa: F401\n"
        "import agentx.core.errors  # noqa: F401\n"
        "import agentx.core.result  # noqa: F401\n"
        "import platform\n"
        "calls = []\n"
        "for name in ('system', 'release', 'version', 'machine'):\n"
        "    original = getattr(platform, name)\n"
        "    def spy(*args, _name=name, _original=original, **kwargs):\n"
        "        calls.append(_name)\n"
        "        return _original(*args, **kwargs)\n"
        "    setattr(platform, name, spy)\n"
        "import agentx.capabilities.windows.provider as provider\n"
        "assert calls == [], calls\n"
        "provider.detect_platform_facts()\n"
        "assert calls, 'explicit detection must read platform facts'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_importing_the_provider_registers_nothing() -> None:
    probe = (
        "import agentx.capabilities.windows.provider  # noqa: F401\n"
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


def test_runtime_dependency_set_is_unchanged() -> None:
    """Windows capability stages add no runtime dependency, especially pywin32."""
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject
    assert "pywin32" not in pyproject
