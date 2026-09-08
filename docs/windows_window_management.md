# Windows window-management capability (M7.02)

> **Status:** M7.02 — governed window-management capability.
> **Branch:** `worker-22/M7.02-windows-window-management`
> **Base:** `03a1221771ba4bcf038dca1d1432e9e8526b9550`

## What M7.02 is

AgentX already observes Windows processes and top-level windows (A5.02). The
next useful deterministic action surface is direct window management through
structured Win32 APIs, not mouse/keyboard automation.

This task implements the smallest governed capability set:

| Operation | Capability identity | Native mechanism (adapter delegate) | Verification |
| --------- | ------------------- | ----------------------------------- | ------------ |
| Focus / activate | `windows.window.focus@1.0.0` | `SetForegroundWindow` / `SetActiveWindow` | `GetForegroundWindow` matches handle |
| Minimize | `windows.window.minimize@1.0.0` | `ShowWindow` (`SW_MINIMIZE`) | `IsIconic` confirms minimized |
| Maximize | `windows.window.maximize@1.0.0` | `ShowWindow` (`SW_MAXIMIZE`) | `IsZoomed` confirms maximized |
| Restore | `windows.window.restore@1.0.0` | `ShowWindow` (`SW_RESTORE`) | `IsIconic`/`IsZoomed` both false |
| Move / resize | `windows.window.move_resize@1.0.0` | `SetWindowPos` / `MoveWindow` | `GetWindowRect` compared to requested geometry |
| Close (optional) | `windows.window.close@1.0.0` | `PostMessage` with `WM_CLOSE` | `IsWindow` independently absent |

## Window identity

Requests bind exclusively to explicit structured HWND handles (`int >= 1`).
No title fuzzy matching, no model matching, no OCR, no coordinate discovery.
Window titles and class names are untrusted OS data and are never parsed into
operation choice.

## Parameters

All operations take `Window*Params` with `handle: int`. Move/resize adds
`x: int`, `y: int`, `width: int`, `height: int` with strict validation:

- `type(value) is int` (bool rejected)
- `handle >= 1` and `<= 0x7FFFFFFF`
- geometry within `[-100_000, 100_000]`
- `width > 0`, `height > 0`
- integer overflow checked

## Governance

Each operation declares:

- **Permission**: `EXTERNAL_EFFECT` + `WRITE` (focus/minimize/maximize/restore/move_resize); same for close with stronger treatment.
- **Risk**: R2 for external-state changes (focus/minimize/maximize/restore/move_resize); R3 / `critical=True` for close.
- **ResourceEstimate**: ~50ms wall-clock, 1 machine action, `Decimal("0")` external cost.
- **Rollback**: supported for reversible operations; unsupported for close.
- **Precondition**: supported Windows host through A5.01; explicit handle only.

The `CapabilityDescriptor` binds identity, description, scope (`WINDOWS`),
permissions, risk assessment, preconditions, rollback declaration, and estimate.
No descriptor text authorizes execution; authority remains with the Trusted
Kernel / `CapabilityExecutionLoop`.

## Native adapter interface

`WindowManagementNativeSurface` (protocol) defines execution and observation
methods. The default adapter (`DefaultWindowManagementNativeSurface`) returns
explicit failure because the isolated native seam (`_native.py`) currently
provides read-only discovery only; management functions are expected as future
extensions of that isolated module.

Tests substitute `FakeWindowManagementNativeSurface`, which records every call
and returns configured observations.

## Verification semantics

- **Focus**: independent `observe_foreground()` must equal requested handle.
- **Minimize/Maximize/Restore**: independent `observe_window_state()` must match expected state.
- **Move/Resize**: independent `observe_window_rect()` must match exact expected geometry (documented limitation: Windows border metrics can cause minor variance; verification uses exact equality).
- **Close**: `WM_CLOSE` delivery is **not** proof. Independent `observe_window_exists()` must return `False`. If still present, verification fails explicitly.

## Race conditions

Windows state can change concurrently. A window may close or move between
execution and verification. The capability returns explicit failure for
stale targets (observation mismatch) and never claims atomic desktop control.

Documentation of residual races:

- Window closes between execution and verification -> verification fails (stale target).
- Window is moved by another process between execution and verification -> rect mismatch -> failure.
- Focus changes before verification -> foreground mismatch -> failure.

## Platform behavior

- **Windows**: adapter delegates to structured Win32 APIs through the isolated
  native seam.
- **Non-Windows**: `is_supported` false; `execute` returns canonical
  `capabilities.windows.unsupported_platform` (`PRECONDITION` / `NON_RETRYABLE`);
  no native surface is touched.

## Untrusted data

Window titles and class names from the OS are stored verbatim wherever
observed but never interpreted. Strings such as `permission=ADMIN`,
`verified=true`, or `focus me` grant no authority and are never parsed
into operation choice.

## Test coverage

- **Unit**: typed operations, invalid handle, bool-as-int, zero/negative dimensions,
  overflow, unsupported platform, native error, stale window, verification mismatch,
  hostile title, deterministic resource estimate.
- **Integration**: authorized operation reaches execute, gate denial prevents native
  call, budget / EmergencyStop paths, verification required, mismatch prevents success.
- **Adversarial**: fake HWND, overflow geometry, hostile strings, close smuggling
  attempt, no PowerShell / shell / subprocess, no keyboard/mouse injection,
  no authority mutation.
- **Architecture**: pure Python, no ctypes/Win32 markers in capability file,
  canonical ABI contracts, immutable objects, no new dependencies, no registry
  mutation, no second provider.

## Non-goals

- No application launch.
- No process termination (close uses `WM_CLOSE`, never `TerminateProcess`).
- No keyboard/clipboard automation (A5.06 is separate).
- No UIA semantic resolution.
- No screenshots, visual fallback, desktop planner, global hotkeys, background hooks.

## Confirmation of constraints

- **No keyboard/mouse injection**: module contains no `SendInput`, `keybd_event`, `mouse_event`, `pyautogui`, `pywinauto`, or coordinate-clicking logic.
- **No worker/dependency change**: only the 6 allowed files created/modified; `provider.py`, `_native.py`, `process_discovery.py`, `uia_tree.py`, `__init__.py`, `abi.py`, `architecture.py`, `loop.py`, and all `__init__.py` files untouched.
- **No PR merge**: PR opened from `worker-22/M7.02-windows-window-management`; not merged.

## References

- Canonical ABI: `src/agentx/capabilities/abi.py`
- Windows provider / platform facts: `src/agentx/capabilities/windows/provider.py`
- Isolated native seam: `src/agentx/capabilities/windows/_native.py`
- Process/window discovery: `src/agentx/capabilities/windows/process_discovery.py`
