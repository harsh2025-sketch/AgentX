"""Architecture guardrails for the N2.23 keyboard/text/clipboard capability.

Static and subprocess-probe checks that run identically on Windows, Linux, and
macOS and never depend on a real desktop. They pin the N2.23 invariants:

* the public capability module carries no Win32/``ctypes`` knowledge and no
  Win32 API mentions of any kind — native input/clipboard mechanisms belong to
  a separately governed native seam (N2.19) reached only through the narrow
  injected ports;
* the module does not import the A5.02 read-only discovery seam or any
  unmerged native seam;
* the module makes no model calls, runs no shell, performs no persistence,
  installs no global hooks or keyloggers, and runs no clipboard monitoring or
  history — no background listeners of any kind;
* importing the module loads nothing native and registers nothing;
* the module reuses the canonical ABI/provider/kernel contracts, duplicates
  none of them, performs no registry wiring, and keeps the runtime dependency
  set empty.

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
_WINDOWS_PKG = _SRC_ROOT / "agentx" / "capabilities" / "windows"
_MODULE = _WINDOWS_PKG / "keyboard_text_clipboard.py"

# Native/automation imports banned in the capability module (mirrors the A5.01
# package-wide ban, plus the persistence/model/shell surfaces N2.23 must not
# touch).
_FORBIDDEN_IMPORTS = frozenset(
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
        "sqlite3",
        "shelve",
        "dbm",
        "pickle",
        "logging",
    }
)

# Win32 API mentions banned anywhere in the module source: mutating window
# APIs (A5.01 list), input-injection APIs, and hook/monitoring APIs.
# ``ctypes`` is checked separately as an import (AST), mirroring the A5.01/
# A5.02 guards: the docstring may state that the module never imports it.
_FORBIDDEN_SOURCE_MARKERS = (
    "windll",
    "WinDLL",
    "WINFUNCTYPE",
    "kernel32",
    "user32",
    "SendInput",
    "keybd_event",
    "mouse_event",
    "SetWindowsHookEx",
    "WH_KEYBOARD",
    "GetAsyncKeyState",
    "GetKeyState",
    "RegisterHotKey",
    "AddClipboardFormatListener",
    "AddClipboardView",
    "SetForegroundWindow",
    "AllowSetForegroundWindow",
    "SetWindowText",
    "PostMessage",
    "SendMessageTimeout",
    "SendMessage",
    "BroadcastSystemMessage",
    "SetCursorPos",
    "BlockInput",
    "AttachThreadInput",
    "ShellExecute",
    "WinExec",
    "TerminateProcess",
    "CreateProcess",
    "GetForegroundWindow",
    "FindWindow",
    "CoInitialize",
    "CreateObject",
)

# Unmerged/native seams the capability module must not import: the A5.02
# read-only discovery seam and the (unmerged) N2.19 native-mutation seam.
_FORBIDDEN_SEAM_MODULES = (
    "agentx.capabilities.windows._native",
    "agentx.capabilities.windows._uia_native",
    "agentx.capabilities.windows.uia_tree",
)

# The exact closed class inventory of the module (no shadow contracts).
_EXPECTED_CLASSES = frozenset(
    {
        "Key",
        "Modifier",
        "KeyChord",
        "SendTextParams",
        "SendKeysParams",
        "ClipboardReadTextParams",
        "ClipboardWriteTextParams",
        "ClipboardClearParams",
        "RawTextSend",
        "RawKeySend",
        "RawClipboardText",
        "RawClipboardWrite",
        "RawClipboardClear",
        "KeyboardNativePort",
        "ClipboardNativePort",
        "UnavailableKeyboardNativePort",
        "UnavailableClipboardNativePort",
        "WindowsSendTextCapability",
        "WindowsSendKeysCapability",
        "WindowsClipboardReadTextCapability",
        "WindowsClipboardWriteTextCapability",
        "WindowsClipboardClearCapability",
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


def test_module_lives_in_the_capabilities_subsystem() -> None:
    assert _MODULE.is_file()
    assert _MODULE.parent.parent.name == "capabilities"
    assert _MODULE.parent.name == "windows"


def test_module_has_no_win32_or_ctypes_knowledge() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    for marker in _FORBIDDEN_SOURCE_MARKERS:
        assert marker not in source, f"module mentions forbidden marker {marker}"
    for module in _imports(_MODULE):
        assert module.split(".")[0] != "ctypes", "module imports ctypes"


def test_module_imports_no_native_automation_or_forbidden_module() -> None:
    for module in _imports(_MODULE):
        root = module.split(".")[0]
        assert root not in _FORBIDDEN_IMPORTS, f"module imports forbidden module {module}"


def test_module_does_not_import_any_native_seam() -> None:
    """The capability layer reaches native code only through injected ports."""
    for module in _imports(_MODULE):
        assert module not in _FORBIDDEN_SEAM_MODULES, f"module imports a native seam {module}"
        assert "native" not in module.split(".")[-1].lower(), (
            f"module imports a native-seam-like module {module}"
        )


def test_module_makes_no_model_calls() -> None:
    for module in _imports(_MODULE):
        assert not module.startswith("agentx.cognition"), f"module makes model coupling {module}"


def test_module_performs_no_shell_or_code_execution() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec", "compile", "__import__", "print"}
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr != "system"  # os.system


def test_module_runs_no_background_listeners_or_history() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    assert "while True" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            assert not node.name.endswith(("History", "Monitor", "Watcher", "Listener", "Hook"))
        if isinstance(node, ast.While):
            # The capability is strictly request-scoped: no loops at all.
            raise AssertionError("the capability module must not contain any loop")


def test_module_keeps_no_module_level_mutable_state() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id != "__all__":
                    raise AssertionError(f"module keeps mutable global state {target.id}")


def test_module_reuses_the_canonical_contracts() -> None:
    imported = _imports(_MODULE)
    assert "agentx.capabilities.abi" in imported
    assert "agentx.capabilities.windows.provider" in imported
    assert "agentx.core.errors" in imported
    assert "agentx.core.result" in imported
    assert "agentx.core.execution" in imported
    assert "agentx.kernel.permissions" in imported
    assert "agentx.kernel.risk" in imported


def test_module_never_takes_over_registry_wiring() -> None:
    assert "agentx.capabilities.registry" not in _imports(_MODULE)


def test_module_defines_no_shadow_contracts() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert defined == _EXPECTED_CLASSES
    for banned in (
        "WindowsCapability",
        "WindowsCapabilityV2",
        "CapabilityV2",
        "Capability",
        "CapabilityDescriptor",
        "CapabilityRegistry",
    ):
        assert banned not in defined


def test_module_respects_the_canonical_subsystem_edges() -> None:
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
        assert owner is None or owner in allowed, f"module -> {module}"


def test_importing_module_in_a_clean_interpreter_loads_nothing_native() -> None:
    """Import safety, provable on any host: no native/automation module loads."""
    probe = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import agentx.capabilities.windows.keyboard_text_clipboard  # noqa: F401\n"
        "added = set(sys.modules) - before\n"
        "forbidden = {'ctypes', 'win32api', 'win32gui', 'win32con', 'win32clipboard',\n"
        "             'comtypes', 'pywinauto', 'uiautomation', 'winreg', 'pythoncom',\n"
        "             'win32com', 'msvcrt', 'subprocess', 'pyperclip', 'pyautogui',\n"
        "             'keyboard', 'mouse'}\n"
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


def test_importing_module_registers_nothing() -> None:
    probe = (
        "import agentx.capabilities.windows.keyboard_text_clipboard  # noqa: F401\n"
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


def test_importing_module_performs_no_platform_detection() -> None:
    """The module never reads platform facts itself; support is injected."""
    probe = (
        # Import the full dependency chain first (the stdlib uuid module calls
        # platform.system() once at import time); only the capability module's
        # own import must add no platform reads.
        "import agentx.capabilities.abi  # noqa: F401\n"
        "import agentx.core.execution  # noqa: F401\n"
        "import agentx.capabilities.windows.provider  # noqa: F401\n"
        "import platform\n"
        "calls = []\n"
        "for name in ('system', 'release', 'version', 'machine'):\n"
        "    original = getattr(platform, name)\n"
        "    def spy(*args, _name=name, _original=original, **kwargs):\n"
        "        calls.append(_name)\n"
        "        return _original(*args, **kwargs)\n"
        "    setattr(platform, name, spy)\n"
        "import agentx.capabilities.windows.keyboard_text_clipboard  # noqa: F401\n"
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


def test_n223_keeps_the_runtime_dependency_set_empty() -> None:
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject
    for banned in ("pywin32", "pywinauto", "comtypes", "uiautomation", "pyperclip", "pyautogui"):
        assert banned not in pyproject
