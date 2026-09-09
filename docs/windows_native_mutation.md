# N2.19 Windows Native Mutation Boundary

This document defines the canonical low-level Windows native mutation seam in
`agentx.capabilities.windows.native_mutation`.

## Existing seams inspected

The repository already had two Windows native seams:

- `agentx.capabilities.windows._native` — read-only Toolhelp/process/window
  discovery. It enumerates processes, queries executable paths and enumerates
  top-level windows. It intentionally performs no mutation.
- `agentx.capabilities.windows._uia_native` — read-only UI Automation tree
  inspection. It reads bounded UIA properties/pattern availability and exposes
  no UIA invoke/value/focus/input methods.

N2.19 is not a duplicate of either seam. It creates the first and only
controlled native boundary for OS mutations, while the existing seams remain
observation-only.

## Intended call direction

```text
governed Windows capability
    -> injected NativeMutationSurface / WindowsNativeMutationAdapter
    -> fixed Win32 calls
```

The seam is not a public AgentX capability and is not registered by the
provider or registry. It contains no policy engine and no user-confirmation
logic. It is invoked only after an upstream governed caller has already made a
legal decision to act.

## Exact native method surface

`WindowsNativeMutationAdapter` implements `NativeMutationSurface` with exactly
six typed methods:

1. `launch_process(NativeProcessLaunchRequest)`
   - Input: absolute Windows executable path plus `argv` tuple.
   - Native mechanism: `CreateProcessW` with `lpApplicationName` populated.
   - No command interpreter, no `shell=True`, no command-string parsing.
   - Output: PID/thread ID and handle-close diagnostics only.

2. `set_window_state(NativeWindowStateRequest)`
   - Input: HWND plus one of `SHOW`, `RESTORE`, `MINIMIZE`, `MAXIMIZE`.
   - Native mechanism: `ShowWindow`.
   - Output: the low-level previous-visibility bit reported by Win32.

3. `activate_window(NativeWindowActivationRequest)`
   - Input: HWND.
   - Native mechanism: `SetForegroundWindow`.
   - Output: low-level acceptance/error only.

4. `send_text(NativeTextInputRequest)`
   - Input: non-empty Unicode text capped at 1,024 UTF-16 code units.
   - Native mechanism: `SendInput` with Unicode keyboard input events.
   - Output: requested and accepted input-event counts.

5. `send_key_strokes(NativeKeyInputRequest)`
   - Input: bounded sequence of controlled virtual keys and controlled
     modifiers. There is no raw virtual-key escape hatch.
   - Native mechanism: `SendInput` keyboard events.
   - Output: requested and accepted input-event counts.

6. `set_clipboard_text(NativeClipboardTextRequest)`
   - Input: Unicode text capped at 65,536 UTF-16 code units.
   - Native mechanism: `OpenClipboard`, `EmptyClipboard`, and
     `SetClipboardData(CF_UNICODETEXT)`.
   - Output: low-level format and text-size data only.

The seam intentionally omits broad operations such as arbitrary DLL loading,
raw function invocation, command interpreters, generic process injection,
remote-thread creation, process termination, arbitrary message posting, window
reparenting, low-level hooks, cursor clipping and pointer movement.

## Platform behavior

Importing `agentx.capabilities.windows.native_mutation` is inert on every
platform. The module imports only standard library code and AgentX core
error/result contracts at module import time. `ctypes` and Windows libraries
are imported lazily inside Windows-only implementation functions.

On non-Windows platforms every adapter method returns an `AgentXError` with
code `capabilities.windows.native_mutation.native_unavailable`. Construction
of `WindowsNativeMutationAdapter` is always safe and performs no native call.

## Shell prohibition

Process launch is structured as:

```text
absolute executable path + argv tuple
```

The executable path is passed to `CreateProcessW` as `lpApplicationName`; argv
is quoted only according to Windows argv rules for the process command line.
No command interpreter is used, no generic command string is accepted and no
shell parsing is exposed.

Hostile strings in argv, text input or clipboard content remain literal data.
They do not become policy decisions or executable command lines.

## Authority separation

The native mutation seam knows nothing about permission grants, risk policy,
user approval, tasks, procedures, Hive state, model output, registry wiring or
capability execution. There is no `authorized` parameter and no second gate.

Availability is not authority. A caller may inject a fake `NativeMutationSurface`
for tests, or the real `WindowsNativeMutationAdapter` for Windows execution,
but governance lives outside this seam.

## Native result is not verification

A successful native result means only that the low-level Windows call reported
that outcome:

- `CreateProcessW` success means a process object was created; it does not
  prove the application is ready.
- `ShowWindow` reports previous visibility; it does not prove the final window
  state is what the user intended.
- `SetForegroundWindow` acceptance is not an end-to-end focus guarantee.
- `SendInput` accepted-event counts do not prove text appeared in a target.
- Clipboard API success does not prove a later consumer observed the text.

Verification of user-intended state belongs to future governed capabilities,
not to this native seam.

## Testing

N2.19 adds unit, adversarial, architecture and Windows-only smoke coverage for:

- import safety off Windows;
- inert adapter construction;
- explicit unsupported-platform results;
- injectable fake native surfaces;
- malformed executable paths and argv;
- shell and subprocess prohibition;
- absence of arbitrary DLL/function invocation;
- no authority, registry, task, procedure, Hive or model coupling;
- native outcome values that do not fabricate verification;
- no invented cancellation channel;
- hostile strings preserved as data;
- non-destructive Windows smoke construction.
