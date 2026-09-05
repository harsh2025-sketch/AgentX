# Windows process/application discovery (A5.02)

> **Status:** A5.02. This task implements the smallest production-quality
> **read-only** Windows discovery foundation: identifying running processes
> and their top-level application windows. It builds on the canonical A5.01
> provider boundary and implements no automation beyond reading what is
> already there.

## What A5.02 is

Future AgentX desktop automation needs to answer one question before it can
do anything: *what is running, and which processes present themselves as
applications?* `agentx.capabilities.windows.process_discovery` answers
exactly that, read-only, through three pieces:

| Piece | Contract |
| ----- | -------- |
| Typed discovered identity | `WindowsProcessIdentity`, `WindowsWindowIdentity`, `WindowsProcessSnapshot`, `MetadataStatus` |
| Deterministic discovery operation | `WindowsProcessDiscovery.discover() -> Result[WindowsProcessSnapshot, AgentXError]` |
| Capability Fabric compatibility | `WindowsProcessDiscoveryCapability` (`windows.processes.discover@1.0.0`) |

The only Win32 knowledge in the codebase lives in the isolated
`agentx.capabilities.windows._native` module. Everything above it is pure
Python and testable on any host.

## Native mechanism

The smallest reliable stdlib-only read-only Windows mechanism, per surface:

| Data | Win32 mechanism (all read-only) |
| ---- | ------------------------------- |
| Process list | `CreateToolhelp32Snapshot`(`TH32CS_SNAPPROCESS`) + `Process32FirstW`/`Process32NextW` (Toolhelp32) |
| Full image path | `OpenProcess`(`PROCESS_QUERY_LIMITED_INFORMATION`) + `QueryFullProcessImageNameW` |
| Top-level windows | `EnumWindows` + `GetWindowThreadProcessId` + `IsWindowVisible` + `GetWindowTextLengthW`/`GetWindowTextW` + `GetClassNameW` |

Justification for window association: these are currently available, stable
Win32 desktop **read** APIs, and "which windows does this process own" is the
only read-only signal the OS gives for *application*-ness
(`WindowsProcessIdentity.is_application`). UI Automation traversal is
deliberately absent — it belongs to a later task with its own boundary.

`ctypes` is imported **lazily inside each native function**, never at module
level, so importing the package loads nothing native on Windows, Linux or
macOS. No `pywin32`, no `pywinauto`, no `psutil`, no new dependency of any
kind: the runtime dependency set stays empty. Architecture tests enforce that
the `ctypes`/Win32 markers appear only in `_native.py`, that `ctypes` is never
a module-level import, and that mutating Win32 APIs (`TerminateProcess`,
`CreateProcess*`, `SendInput`, `SetForegroundWindow`, `SetWindowText`,
`PostMessage`/`SendMessage`, `ShowWindow`, ...) appear **nowhere** in the
Windows package — not even as a string.

## Result model

- `WindowsProcessIdentity`: `process_id`, `parent_process_id | None`,
  `executable_name` + status, `executable_path` + status (best-effort),
  `window_handles` (sorted), `visible_window_count`, derived
  `is_application`.
- `WindowsWindowIdentity`: `handle`, owning `process_id`, `title` + status,
  `class_name` + status, `is_visible`. Handles are snapshot-scoped: the OS
  recycles them.
- `WindowsProcessSnapshot`: both tuples plus `dropped_invalid_entries` and
  `merged_duplicate_entries` counters, with construction-time enforcement of
  every ordering/consistency invariant. `to_dict()` yields JSON-compatible
  evidence.
- `MetadataStatus` explains every absent optional field explicitly:
  `available`, `unsupported`, `access_denied`, `vanished`, `empty`,
  `invalid`. A field is non-`None` exactly when it is `available`.

Validation treats the two string kinds differently. **OS identifiers**
(executable names, paths, window class names) must be non-empty,
control-character-free and within defensive bounds; failures become `EMPTY`
or `INVALID` metadata — the record is kept, the value is not trusted.
**Free text** (window titles) may contain anything, including hostile
injection payloads; it is stored verbatim and stays inert.

## Ordering and determinism

The OS may enumerate in any order and may return duplicates or garbage.
`discover()` therefore normalizes every result:

- processes are deduplicated by PID; the deterministic survivor is chosen by
  the smallest `(executable_name, parent_process_id)` key — never by arrival
  order;
- windows are deduplicated by handle;
- invalid entries (bad PID/handle types, negative values) are dropped and
  counted, never propagated and never fatal;
- the snapshot is ordered by `(process_id, executable_name, executable_path)`
  for processes and by handle for windows, so the same OS multiset always
  yields the identical snapshot regardless of enumeration order (unit-tested
  across shuffled seeds).

There is **no hidden global cache, no background watcher and no polling
loop**: each `discover()` call performs a fresh read of the seam, and nothing
persists between calls. There is **no persistence**: no schema, no disk
writes, no state outside the returned value.

## Race and error semantics

- **Unsupported platform** (the A5.01 verdict says not-Windows):
  `discover()` returns the canonical
  `capabilities.windows.unsupported_platform` error and never touches the
  native seam.
- **Native surface unavailable** (a Windows verdict on a non-Windows host,
  e.g. a forged verdict): `capabilities.windows.process_discovery.native_unavailable`
  (`PRECONDITION` / `NON_RETRYABLE`), returned, never raised.
- **OS enumeration failure** (snapshot creation/walk, window walk):
  `...process_enumeration_failed` / `...window_enumeration_failed`
  (`EXECUTION` / `UNKNOWN` retryability) with the Win32 error code in
  `details`.
- **Per-record metadata failures are data, not errors**:
  - image path denied for an elevated process → `ACCESS_DENIED`;
  - the process exited between snapshot and path query → `VANISHED`, and the
    process record **stays** in the snapshot (the race is recorded, not
    hidden);
  - window of an already-exited process → kept, simply unjoined from any
    process record;
  - hostile/oversized strings → `INVALID`/`EMPTY` with the value dropped.
- **Operation-level path-query failure** (the seam itself fails):
  `...path_query_failed` wrapping the cause code.

Importing any module performs **no machine action**: no native load, no
platform detection, no registration, no read. Architecture tests prove this
statically and with clean-subprocess probes that patch the native functions
to raise if touched at import time.

## Security boundary

> **AVAILABILITY IS NOT AUTHORITY. DISCOVERED METADATA IS UNTRUSTED DATA.**

Executable names, paths, window titles and class names cannot grant a
`Permission`, become capability names, bypass the `ActionGate`, reduce a
`RiskAssessment`, widen a `ResourceEnvelope`, clear an `EmergencyStop`, or
fabricate verification. The capability descriptor is fixed at construction —
`READ` permission only, R0 read-only risk — and no discovery result can
change it. The Trusted Kernel (`agentx.kernel`) remains the only authority
boundary. Adversarial tests pin all of this, including that discovery invokes
only the three read seam methods, starts no threads, and exposes no
lifecycle/UI surface.

## Capability Fabric compatibility

`WindowsProcessDiscoveryCapability` is an ordinary canonical `Capability`:
canonical `CapabilityDescriptor` keyed by `CapabilityIdentity`
(`windows.processes.discover@1.0.0`), `windows` scope, `Permission.READ`
required, R0 risk, one declarative precondition (supported Windows host),
rollback `NOT_APPLICABLE`, zero-external-cost estimate. `execute` honours
cooperative cancellation, performs exactly one discovery, and returns the
snapshot as `CapabilityObservation` evidence (an unsupported host yields
`succeeded=False` with the canonical error in the evidence). `verify` checks
that evidence for structural consistency — counters agree, ordering is
canonical — without re-reading the OS, and never manufactures success.

The provider/registry wiring stays canonical A5.01/A1.09: the composition
root contributes the instance to `WindowsProvider` and registers it in the
`CapabilityRegistry` it owns. Discovery performs no registration itself and
imports no registry.

## Tests

- `tests/unit/test_windows_process_discovery.py` — model validation and
  invariants, unsupported-platform behaviour, non-Windows native semantics
  (explicit failure, never a crash), deterministic ordering across shuffled
  OS orders, duplicate merging, invalid-entry dropping, access-denied and
  vanished races, hostile executable/window strings, no-cache/fresh-read
  semantics, capability execute/verify, and provider+registry compatibility.
  One Windows-only test validates the real Toolhelp surface against the host
  (system processes always exist; no CI dependency on a particular
  application).
- `tests/adversarial/test_windows_process_discovery_authority.py` — metadata
  inertness, kernel-state integrity (permissions, ActionGate, EmergencyStop,
  risk), read-seam-only discipline, no threads/watchers/polling, no
  lifecycle/UI surface.
- `tests/architecture/test_windows_process_discovery_boundaries.py` —
  native-seam isolation, canonical contract reuse, no registry takeover, and
  clean-interpreter import probes (no native load, no read, no platform
  detection, no registration).
- `tests/architecture/test_windows_provider_boundaries.py` — amended so the
  original A5.01 guardrails still hold, with the single documented carve-out:
  lazy `ctypes` plus the approved read-only Win32 APIs inside `_native.py`
  only; mutating Win32 APIs banned everywhere, including inside the seam.

All destructive/unreliable OS surfaces are faked (`tests/support/
fake_windows_native.py`); CI never depends on a desktop application being
open.

## Deliberate non-scope

No UI Automation traversal, no control discovery, no clicking or text entry,
no keyboard/mouse input, no window manipulation or focus change, no
capture/OCR/vision, no shell execution, no process termination or creation,
no privilege mutation of any kind. Those belong to later tasks; A5.02 only
looks, never touches.
