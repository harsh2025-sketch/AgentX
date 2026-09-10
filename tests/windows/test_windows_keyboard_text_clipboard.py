"""Windows-platform coverage for the N2.23 governed keyboard/clipboard surface.

These tests are deliberately **read-only and side-effect free**: CI never
injects keyboard input and never mutates the runner clipboard, because the
Win32-backed native seam is not part of this clean rebuild — it is bound
separately by the integration authority (N2.19). What *is* provable on a
Windows host, safely:

* importing the capability module loads no native library at all;
* a capability with the default (unbound) seam fails every operation with one
  explicit canonical precondition error — no exception, no native call, no
  silent no-op;
* the A5.01 provider boundary evaluates the real host facts as supported on a
  Windows machine (pure standard-library string facts, no native read).
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any, Protocol, cast
from uuid import uuid4

import pytest

from agentx.capabilities.abi import CapabilityObservation, CapabilityRequest, ExecutionResult
from agentx.capabilities.windows.keyboard_text_clipboard import (
    NATIVE_SEAM_UNAVAILABLE_ERROR_CODE,
    Key,
    KeyChord,
    WindowsClipboardClearCapability,
    WindowsClipboardReadTextCapability,
    WindowsClipboardWriteTextCapability,
    WindowsSendKeysCapability,
    WindowsSendTextCapability,
    clipboard_clear_request,
    clipboard_read_request,
    clipboard_write_request,
    send_keys_request,
    send_text_request,
)
from agentx.capabilities.windows.provider import (
    detect_platform_facts,
    evaluate_windows_support,
)
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.tasks import JsonValue
from tests.support.fake_windows_native import windows_support


def _data(observation: CapabilityObservation) -> dict[str, JsonValue]:
    """Return the canonical JSON object under the observation's ``data`` key."""
    return cast("dict[str, JsonValue]", observation.to_dict()["data"])


def _error_data(observation: CapabilityObservation) -> dict[str, JsonValue]:
    """Return the canonical JSON error object embedded in the observation data."""
    return cast("dict[str, JsonValue]", _data(observation)["error"])


def _context() -> ExecutionContext:
    return ExecutionContext(correlation_id=uuid4(), cancellation_token=CancellationSource().token)


def test_importing_module_loads_no_native_library_on_any_host() -> None:
    probe = (
        "import sys\n"
        "import agentx.capabilities.windows.keyboard_text_clipboard  # noqa: F401\n"
        "assert 'ctypes' not in sys.modules, 'ctypes was loaded at import time'\n"
        "native_roots = {'win32api', 'win32gui', 'win32clipboard', 'comtypes', 'pythoncom'}\n"
        "loaded = {name.split('.')[0] for name in sys.modules}\n"
        "assert not (loaded & native_roots), sorted(loaded & native_roots)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


class _ExecutableCapability(Protocol):
    """Structural view over the five concrete capability classes."""

    def execute(
        self, request: CapabilityRequest[Any], context: ExecutionContext
    ) -> ExecutionResult: ...


def test_unbound_seam_fails_explicitly_without_any_machine_action() -> None:
    """Default ports: one explicit canonical failure per operation, nothing else."""
    support = windows_support()
    capabilities: tuple[tuple[_ExecutableCapability, CapabilityRequest[Any]], ...] = (
        (WindowsSendTextCapability(support), send_text_request("abc")),
        (
            WindowsSendKeysCapability(support),
            send_keys_request((KeyChord(key=Key.ENTER),)),
        ),
        (WindowsClipboardReadTextCapability(support), clipboard_read_request()),
        (WindowsClipboardWriteTextCapability(support), clipboard_write_request("abc")),
        (WindowsClipboardClearCapability(support), clipboard_clear_request()),
    )
    for capability, request in capabilities:
        result = capability.execute(request, _context())
        assert result.succeeded is False
        error = _error_data(result.observation)
        assert error["code"] == NATIVE_SEAM_UNAVAILABLE_ERROR_CODE
        assert error["category"] == "precondition"


@pytest.mark.skipif(sys.platform != "win32", reason="requires a real Windows host")
def test_real_host_facts_evaluate_as_supported() -> None:
    """On Windows the A5.01 provider boundary reports a supported host.

    This is a pure standard-library string check (``platform``/``sys``); it
    performs no native read and touches no input or clipboard surface.
    """
    facts = detect_platform_facts()
    support = evaluate_windows_support(facts)
    assert support.is_supported is True
