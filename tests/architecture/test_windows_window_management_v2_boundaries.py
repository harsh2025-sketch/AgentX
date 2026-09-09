"""Architecture guardrails for the N2.20 window-management boundary.

These are static, deterministic checks plus one subprocess import-safety
probe. They run identically on Windows, Linux, and macOS and they never
touch a native API. They are architecture guardrails, not security
enforcement: authority remains owned by the Trusted Kernel.

N2.20 owns the governed capability contract / adapter layer only. The
module must therefore stay pure Python with an injected native port: no
native imports, no Win32 names, no automation surface, no kernel-authority
or runtime coupling, and no model, Hive, or persistence edges.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from agentx import _architecture
from agentx.capabilities.windows.window_management_v2 import (
    NativeWindowManagementPort,
    WindowManagementCapability,
    WindowManagementOperation,
    WindowManagementParams,
    WindowTarget,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "capabilities" / "windows" / "window_management_v2.py"

_ALLOWED_AGENTX_IMPORTS = frozenset(
    {
        "agentx.capabilities.abi",
        "agentx.capabilities.windows.provider",
        "agentx.core.errors",
        "agentx.core.execution",
        "agentx.core.result",
        "agentx.core.tasks",
        "agentx.kernel.permissions",
        "agentx.kernel.risk",
    }
)

_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
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
    }
)

_FORBIDDEN_AGENTX_PREFIXES = (
    "agentx.cognition",
    "agentx.hive",
    "agentx.learning",
    "agentx.procedures",
    "agentx.infrastructure",
    "agentx.capabilities.registry",
    "agentx.capabilities.runtime",
    "agentx.capabilities.executor",
    "agentx.kernel.action_gate",
    "agentx.kernel.emergency_stop",
    "agentx.kernel.resource_budget",
    "agentx.kernel.audit",
)

_FORBIDDEN_NAMES = frozenset(
    {
        "PermissionEngine",
        "ActionGate",
        "GateRequest",
        "GateDecision",
        "CapabilityRegistry",
        "CapabilityExecutionLoop",
        "EmergencyStop",
        "ResourceBudget",
    }
)

_FORBIDDEN_WIN32_MUTATIONS = (
    "TerminateProcess",
    "CreateProcess",
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

_FORBIDDEN_AUTOMATION_SYMBOLS = (
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


def _tree() -> ast.Module:
    return ast.parse(_MODULE.read_text(encoding="utf-8"))


def _imported_modules() -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _referenced_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_n2_20_lives_in_the_windows_capabilities_package() -> None:
    expected = "agentx.capabilities.windows.window_management_v2"
    assert _MODULE.is_file()
    assert WindowManagementCapability.__module__ == expected
    assert WindowManagementOperation.__module__ == expected
    assert WindowManagementParams.__module__ == expected
    assert WindowTarget.__module__ == expected
    assert NativeWindowManagementPort.__module__ == expected


def test_n2_20_imports_only_stdlib_and_its_canonical_contracts() -> None:
    stdlib = set(sys.stdlib_module_names)
    agentx_imports: set[str] = set()
    for module in _imported_modules():
        root = module.split(".")[0]
        assert root not in _FORBIDDEN_IMPORT_ROOTS, f"forbidden import {module}"
        if module.startswith("agentx"):
            agentx_imports.add(module)
        else:
            assert root in stdlib, f"third-party import {module}"
    assert agentx_imports == set(_ALLOWED_AGENTX_IMPORTS)


def test_n2_20_has_no_model_hive_runtime_or_persistence_coupling() -> None:
    for module in _imported_modules():
        for prefix in _FORBIDDEN_AGENTX_PREFIXES:
            assert module != prefix and not module.startswith(f"{prefix}."), module


def test_n2_20_never_touches_kernel_authority_or_the_execution_loop() -> None:
    """No gate, engine, registry, loop, stop, or budget reference in code."""
    names = _referenced_names()
    for forbidden in _FORBIDDEN_NAMES:
        assert forbidden not in names, forbidden


def test_n2_20_names_no_native_api() -> None:
    """The contract is native-agnostic: no Win32 names anywhere in the file."""
    source = _MODULE.read_text(encoding="utf-8")
    assert "ctypes" not in source
    for symbol in _FORBIDDEN_WIN32_MUTATIONS:
        assert symbol not in source, symbol


def test_n2_20_defines_no_automation_surface() -> None:
    names = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    attributes = {node.attr for node in ast.walk(_tree()) if isinstance(node, ast.Attribute)}
    for symbol in _FORBIDDEN_AUTOMATION_SYMBOLS:
        assert symbol not in names, symbol
        assert symbol not in attributes, symbol


def test_n2_20_has_no_module_level_side_effects() -> None:
    for node in _tree().body:
        assert not isinstance(node, ast.Expr | ast.For | ast.While | ast.With | ast.Try) or (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        ), ast.dump(node)
        if isinstance(node, ast.If):  # pragma: no cover - defensive
            raise AssertionError("module-level conditional logic is forbidden")


def test_n2_20_uses_canonical_subsystem_edges_only() -> None:
    allowed = {_architecture.CORE, _architecture.KERNEL, _architecture.CAPABILITIES}
    for module in _imported_modules():
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
        assert owner is None or owner in allowed, module


def test_importing_n2_20_in_a_clean_interpreter_loads_nothing_native() -> None:
    """Import safety, provable on any host: no native module loads."""
    probe = (
        "import sys\n"
        "import agentx.capabilities.abi  # noqa: F401\n"
        "import agentx.core.errors  # noqa: F401\n"
        "import agentx.core.result  # noqa: F401\n"
        "before = set(sys.modules)\n"
        "import agentx.capabilities.windows.window_management_v2 as module\n"
        "added = set(sys.modules) - before\n"
        "forbidden = {'ctypes', 'win32api', 'win32gui', 'win32con', 'comtypes',\n"
        "             'pywinauto', 'uiautomation', 'winreg', 'pythoncom',\n"
        "             'win32com', 'msvcrt', 'subprocess'}\n"
        "roots = {name.split('.')[0] for name in added}\n"
        "assert not (roots & forbidden), sorted(roots & forbidden)\n"
        "assert module.WINDOW_ACTIVATE_IDENTITY.name.value == 'windows.window.activate'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr
