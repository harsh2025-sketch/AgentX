# Windows keyboard, text and clipboard capabilities (A5.06)

A5.06 adds the governed Windows surface for bounded text entry, one validated
key/chord operation, and Unicode-text clipboard read/write/clear. These are
ordinary Capability ABI implementations. Availability comes from the A5.01
Windows provider; authority, risk gating, budget enforcement, execution,
verification orchestration, events and audit remain owned by the canonical
`CapabilityExecutionLoop` / Trusted Kernel.

## Canonical operations

| Identity | Permission(s) | Effective risk | A5.06 verification |
| --- | --- | --- | --- |
| `windows.text.enter@1.0.0` | `WRITE`, `EXTERNAL_EFFECT` | R3 | fails closed; A5.10 owns state verification |
| `windows.keyboard.press@1.0.0` | `EXECUTE`, `EXTERNAL_EFFECT` | R3 | fails closed; A5.10 owns state verification |
| `windows.clipboard.read_text@1.0.0` | `READ` | R0 | validates the bounded read evidence only |
| `windows.clipboard.write_text@1.0.0` | `WRITE` | R2 | fails closed; API acceptance is not state verification |
| `windows.clipboard.clear@1.0.0` | `WRITE` | R2 | fails closed; API acceptance is not state verification |

R3 text/key input therefore receives `REQUIRE_CONFIRMATION` from the current
Action Gate even when its explicit permissions are present. A5.06 does not
create an approval bypass or a second execution path.

## Native boundary

All Win32 knowledge stays in the pre-existing
`agentx.capabilities.windows._native` seam. It loads `ctypes` lazily only when
an explicit native function is called.

- Text entry uses `SendInput` with `KEYEVENTF_UNICODE` and bounded UTF-16 input.
- Key input uses `SendInput` with a closed, layout-stable key vocabulary and
  explicit modifier tuple; raw virtual-key integers are not caller input.
- Clipboard access uses `CF_UNICODETEXT` with bounded reads/writes and explicit
  Win32 failures.

No global hotkey is registered, no keyboard hook is installed, no keystrokes
are captured, and no foreground target is discovered or changed. Text/key
operations require the user-intended target to already own foreground focus.

## Trust and evidence

Text and clipboard content is untrusted data. It cannot alter descriptors,
permissions, risk, budgets, or verification. Text-entry and clipboard-write
observations deliberately retain only counts and native acceptance evidence;
they do not echo the content. Clipboard read necessarily returns the bounded
text because that is the operation's purpose, but the content remains inert.

A successful Windows API return means only that the native invocation was
accepted. It does not prove that an application reached the user's intended
state. A5.06 therefore never returns a passing mutation verification verdict.

## Deliberate non-scope

A5.06 does not implement UI Automation resolution or invocation, focus
selection, dialogs, visual fallback, screenshots, A5.10 state-transition
verification, global hotkeys, background key logging, arbitrary keystroke
capture, retries, or clipboard history.
