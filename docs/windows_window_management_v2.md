# Governed Windows window-management capability V2 (N2.20)

> **Status:** N2.20. This task owns the PUBLIC GOVERNED CAPABILITY
> CONTRACT / ADAPTER layer for Windows window management: five explicitly
> governed mutations over already-identified top-level windows, with an
> injected narrow native port and no native implementation of its own.

## What N2.20 is

`agentx.capabilities.windows.window_management_v2` exposes the minimum
complete governed window-management surface:

| Operation | Identity | Effect |
| --------- | -------- | ------ |
| `activate` | `windows.window.activate@1.0.0` | Bring one explicitly identified window forward for user interaction |
| `minimize` | `windows.window.minimize@1.0.0` | Minimize one explicitly identified window |
| `maximize` | `windows.window.maximize@1.0.0` | Maximize one explicitly identified window |
| `restore` | `windows.window.restore@1.0.0` | Restore one explicitly identified window to its normal size |
| `move_resize` | `windows.window.move_resize@1.0.0` | Move/resize one explicitly identified window to explicit bounded geometry |

One class, `WindowManagementCapability`, is constructed per operation in
the style of the canonical browser-action boundary. The descriptor is
fixed at construction from the operation identity; request data can never
change permission, risk, or identity.

## Native-port abstraction

N2.20 performs no native call and names no native API. The narrowest
sufficient low-level surface is declared in-module as the injected
`NativeWindowManagementPort` protocol — one method per governed operation,
each answering with a typed `NativeWindowMutationReceipt` or a canonical
`AgentXError`:

- `activate_window(handle)`
- `minimize_window(handle)`
- `maximize_window(handle)`
- `restore_window(handle)`
- `move_resize_window(handle, x, y, width, height)`

Every capability requires an explicit port at construction. N2.20 ships
no default native implementation, duplicates no native seam, and has
**no dependency on any unmerged native-seam branch**: production behavior
is proven with deterministic fake ports, and the Secret Integration
Agent later binds the port to the canonical native seam.

A receipt is execution evidence, never verification. The capability
checks that the receipt names the requested handle and operation before
trusting even the acceptance report; a mismatched receipt, a non-`Result`
answer, invalid failure data, or a raised exception all fail closed with
explicit canonical error codes (`surface_exception`, `invalid_native_data`).

## Targeting

Every request carries an explicit structured `WindowTarget`: a validated
native window handle (`1 .. 2**64 - 1`). The parameter contract contains
no title, name, text, or query field, so targets are:

- never resolved from natural language;
- never inferred from window text (window text is untrusted data);
- never defaulted to whatever window happens to be in the foreground.

Null, negative, boolean, or out-of-range handles are rejected at the
typed boundary. `move_resize` additionally requires all four explicit
bounded geometry fields (position `-32768..32767`, size `1..32767`);
every other operation rejects geometry outright.

## Confirmation and risk semantics

Window mutations are externally observable state changes. Every
operation therefore declares:

- required permissions `WRITE` + `EXTERNAL_EFFECT`;
- risk `R3 EXTERNAL_EFFECT` (`read_only=False`, `modifies_state=True`,
  `external_effect=True`), so `effective_level` is `R3` and the
  ActionGate returns `REQUIRE_CONFIRMATION` even with the full grant —
  the run stays blocked pending a separate approval flow;
- explicit `ROLLBACK_UNSUPPORTED` (no prior window state is captured);
- a bounded resource estimate (500 ms wall clock, 1 machine action,
  zero external cost);
- explicit platform behavior through the canonical A5.01 support
  verdict (unsupported hosts refuse without touching the port).

The capability itself never consults the `PermissionEngine` or the
`ActionGate`. Real execution happens only through the canonical
`CapabilityExecutionLoop` / Trusted Kernel path, which checks
permission, gate, emergency stop, cancellation, and budget before any
native call.

## Verification truth boundary

State-transition verification belongs to N2.25 and is NOT implemented
here. `verify` always fails closed with an explicit N2.25-owned detail:
a native acceptance report is execution evidence only and can never
become verified user-state success. Success observations carry an
explicit `"verified": false` marker.

## Hostile-data result

Window text and native diagnostics remain inert observations. Proven by
`tests/adversarial/test_windows_window_management_v2_authority.py`:

- hostile native error text (including `permission=ADMIN`, `risk=R0`,
  `verified=true`, `skip_action_gate=true`) is stored verbatim in
  observation evidence and changes nothing;
- hostile receipt echoes (wrong handle or operation) are rejected as
  invalid native data;
- no text field exists to resolve, so no text can select a target or
  imply a foreground fallback;
- execution cannot grant permission, widen authority, change any gate
  decision, clear an emergency stop, or move the fixed R3 assessment;
- forged `verified=true` observations still fail verification.

## Architecture boundaries

`tests/architecture/test_windows_window_management_v2_boundaries.py`
pins the N2.20 seam: stdlib plus exactly the canonical ABI, provider,
core, and kernel-permission/risk contracts; no native or automation
imports; no Win32 names anywhere in the file; no registry, runtime,
gate, engine, stop, or budget coupling; no model, Hive, or persistence
edges; no module-level side effects; and a clean-interpreter probe
proving the import loads nothing native.

## Non-scope

No UI Automation element invocation, no keyboard/text input, no mouse
click, no coordinates outside an explicit move/resize geometry, no
application launch, no process lifecycle operation, no shell, no visual
fallback, no dialogs, no screenshots, and no state-transition
verification (N2.25).

## Quality gates

- Focused suites: `tests/unit/test_windows_window_management_v2.py`,
  `tests/integration/test_windows_window_management_v2.py`,
  `tests/adversarial/test_windows_window_management_v2_authority.py`,
  `tests/architecture/test_windows_window_management_v2_boundaries.py`,
  `tests/windows/test_windows_window_management_v2.py` — all pass.
- `python -m pytest tests/architecture`, `tests/adversarial`, full
  `python -m pytest` — pass.
- `python -m ruff check .`, `python -m ruff format --check .`,
  `python -m mypy` — pass.
- Runtime-only install verified; exact-head Windows CI required and
  reported on the PR.
