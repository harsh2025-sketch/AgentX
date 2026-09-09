"""Architecture guardrails for N2.19 Windows native mutation boundary."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from agentx.capabilities.windows.native_mutation import WindowsNativeMutationAdapter

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_WINDOWS = _SRC_ROOT / "agentx" / "capabilities" / "windows"
_MODULE = _WINDOWS / "native_mutation.py"

_EXPECTED_ADAPTER_METHODS = {
    "activate_window",
    "launch_process",
    "move_resize_window",
    "send_key_strokes",
    "send_text",
    "set_clipboard_text",
    "set_window_state",
}

_EXPECTED_CLASSES = {
    "NativeWindowShowState",
    "WindowsVirtualKey",
    "WindowsKeyModifier",
    "NativeProcessLaunchRequest",
    "NativeWindowStateRequest",
    "NativeWindowActivationRequest",
    "NativeWindowMoveResizeRequest",
    "NativeTextInputRequest",
    "NativeKeyStroke",
    "NativeKeyInputRequest",
    "NativeClipboardTextRequest",
    "NativeProcessLaunchOutcome",
    "NativeWindowStateOutcome",
    "NativeWindowActivationOutcome",
    "NativeWindowMoveResizeOutcome",
    "NativeInputInjectionOutcome",
    "NativeClipboardMutationOutcome",
    "NativeMutationSurface",
    "WindowsNativeMutationAdapter",
    "STARTUPINFOW",
    "PROCESS_INFORMATION",
    "MOUSEINPUT",
    "KEYBDINPUT",
    "HARDWAREINPUT",
    "INPUT_UNION",
    "INPUT",
    "_KeyboardEventSpec",
}

_APPROVED_WIN32_MUTATION_NAMES = {
    "CloseClipboard",
    "CloseHandle",
    "CreateProcessW",
    "EmptyClipboard",
    "GlobalAlloc",
    "GlobalFree",
    "GlobalLock",
    "GlobalUnlock",
    "OpenClipboard",
    "SendInput",
    "SetClipboardData",
    "SetForegroundWindow",
    "SetWindowPos",
    "ShowWindow",
}

_FORBIDDEN_NATIVE_NAMES = {
    "AllowSetForegroundWindow",
    "AttachThreadInput",
    "BlockInput",
    "BroadcastSystemMessage",
    "ClipCursor",
    "CreateRemoteThread",
    "DebugActiveProcess",
    "DestroyWindow",
    "GenerateConsoleCtrlEvent",
    "GetProcAddress",
    "LoadLibrary",
    "MoveWindow",
    "PostMessage",
    "ReadProcessMemory",
    "ResumeThread",
    "SendMessage",
    "SetCursorPos",
    "SetParent",
    "SetThreadDesktop",
    "SetWindowLong",
    "SetWindowText",
    "ShellExecute",
    "SuspendThread",
    "SwitchToThisWindow",
    "TerminateProcess",
    "VirtualAllocEx",
    "WinExec",
    "WriteProcessMemory",
    "keybd_event",
    "mouse_event",
}


def _tree(path: Path = _MODULE) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path = _MODULE) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def _top_level_imports(path: Path = _MODULE) -> set[str]:
    result: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def test_native_mutation_lives_only_in_windows_capabilities() -> None:
    assert _MODULE.is_file()
    assert _MODULE.relative_to(_SRC_ROOT) == Path("agentx/capabilities/windows/native_mutation.py")
    assert WindowsNativeMutationAdapter.__module__ == (
        "agentx.capabilities.windows.native_mutation"
    )


def test_native_mutation_imports_only_stdlib_and_core_result_contracts() -> None:
    stdlib = set(sys.stdlib_module_names)
    imports = _imports()
    for imported in imports:
        root = imported.split(".", 1)[0]
        assert root in stdlib or root == "agentx", imported
    agentx_imports = {module for module in imports if module.startswith("agentx.")}
    assert agentx_imports == {"agentx.core.errors", "agentx.core.result"}


def test_ctypes_is_lazy_and_not_imported_at_module_level() -> None:
    assert "ctypes" not in _top_level_imports()


def test_import_and_construction_have_no_native_side_effects() -> None:
    probe = (
        "import sys\n"
        "import agentx.core.errors  # noqa: F401\n"
        "import agentx.core.result  # noqa: F401\n"
        "before = set(sys.modules)\n"
        "from agentx.capabilities.windows.native_mutation import "
        "WindowsNativeMutationAdapter\n"
        "adapter = WindowsNativeMutationAdapter()\n"
        "added = set(sys.modules) - before\n"
        "forbidden = {'ctypes', 'win32api', 'win32gui', 'win32con', 'win32clipboard', "
        "'pythoncom', 'comtypes', 'win32com', 'pywinauto', 'uiautomation', 'subprocess'}\n"
        "roots = {name.split('.')[0] for name in added}\n"
        "assert not (roots & forbidden), sorted(roots & forbidden)\n"
        "assert adapter is not None\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_adapter_method_surface_is_narrow_and_typed() -> None:
    public_methods = {
        name
        for name, value in WindowsNativeMutationAdapter.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert public_methods == _EXPECTED_ADAPTER_METHODS

    tree = _tree()
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert defined == _EXPECTED_CLASSES
    for banned in (
        "Capability",
        "CapabilityDescriptor",
        "CapabilityRegistry",
        "Permission",
        "ActionGate",
    ):
        assert banned not in defined


def test_no_public_generic_win32_or_shell_surface_exists() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    forbidden_text = (
        "shell=True",
        "subprocess",
        "cmd.exe /c",
        "powershell -Command",
        "os.system",
        "Popen",
        "run_command",
        "LoadLibrary",
        "GetProcAddress",
        "ctypes.CDLL",
        "ctypes.windll",
    )
    for token in forbidden_text:
        assert token not in source

    for forbidden in _FORBIDDEN_NATIVE_NAMES:
        assert forbidden not in source, forbidden


def test_only_approved_win32_mutation_api_names_are_referenced() -> None:
    tree = _tree()
    win32_calls = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"kernel32", "user32"}
    }
    assert win32_calls <= _APPROVED_WIN32_MUTATION_NAMES


def test_no_authority_task_model_or_persistence_coupling() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    imports = _imports()
    forbidden_import_prefixes = (
        "agentx.capabilities.abi",
        "agentx.capabilities.registry",
        "agentx.capabilities.windows.provider",
        "agentx.kernel",
        "agentx.cognition",
        "agentx.hive",
        "agentx.learning",
        "agentx.procedures",
        "agentx.infrastructure",
    )
    for imported in imports:
        assert not any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for prefix in forbidden_import_prefixes
        ), imported
    for token in (
        "authorized",
        "AuthorityContext",
        "PermissionEngine",
        "ActionGate",
        "RiskLevel",
        "TaskStatus",
        "Procedure",
        "Hive",
        "model_output",
        "verification verdict",
    ):
        assert token not in source


def test_native_outcomes_do_not_claim_verification_or_target_success() -> None:
    tree = _tree()
    outcome_classes = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name.endswith("Outcome")
    }
    assert outcome_classes
    for node in outcome_classes.values():
        field_names = {
            item.target.id
            for item in node.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        }
        joined = " ".join(field_names).casefold()
        for forbidden in (
            "verified",
            "verification",
            "succeeded",
            "success",
            "ready",
            "intended",
            "task",
            "cancel",
        ):
            assert forbidden not in joined, f"{node.name}: {field_names}"


def test_no_cancellation_or_authority_parameter_is_invented() -> None:
    tree = _tree()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        parameter_names = [arg.arg for arg in (*node.args.args, *node.args.kwonlyargs)]
        joined = " ".join(parameter_names).casefold()
        for forbidden in ("authorized", "permission", "authority", "risk", "gate", "cancel"):
            assert forbidden not in joined, f"{node.name}: {parameter_names}"
