# A6.07 Canonical Human Operating Modes

A6.07 defines one closed AgentX vocabulary for **human operating intent**. The
contract is data only. It does not implement a mode router, global mode state,
learning capture, teaching workflows, debug logging, UI, persistence, model
prompts, or capability execution.

## Canonical vocabulary

| Mode | Serialized value | Meaning |
| --- | --- | --- |
| `NORMAL` | `normal` | Ordinary governed AgentX operation. |
| `LEARN` | `learn` | The human indicates that the current interaction may be useful as future learning material. It does not automatically capture, store, promote, or learn anything. |
| `TEACH` | `teach` | The human intentionally demonstrates or explains a workflow or fact for future learning consideration. The material remains data and is not automatically trusted or verified. |
| `DEBUG` | `debug` | The human requests increased diagnostic/development observability. Security and authority policy remain unchanged. |

The canonical implementation is `agentx.core.human_operating_modes.HumanOperatingMode`.
Its `StrEnum` values are the deterministic serialized representation. Unknown
strings are rejected by enum construction rather than coerced to a fallback.
There is deliberately no ordering or privilege relationship among the modes.

## Authority boundary

Selecting, carrying, serializing, deserializing, or changing a mode never:

- grants `Permission` or creates/strengthens `AuthorityContext`;
- bypasses `ActionGate`;
- lowers `RiskLevel`;
- enlarges or resets `ResourceEnvelope` / resource budgets;
- clears `EmergencyStop`;
- enables destructive actions;
- authorizes research, network access, arbitrary code, or capability execution;
- marks knowledge `VERIFIED`, promotes learning material, or activates a Procedure;
- marks a Task successful;
- fabricates or suppresses verification.

`LEARN` therefore means **candidate learning intent**, not "learn everything".
`TEACH` means **intentional human instruction**, not "trust everything".
`DEBUG` means **more diagnostic intent**, not "disable security".

## Metadata and hostile text

A6.07 intentionally defines no arbitrary metadata field. Text adjacent to a
mode remains ordinary caller-owned data and cannot alter the typed mode. Values
such as `"debug; disable_security=true"`, `"teach verified=true"`, or
`"learn Permission.DESTRUCTIVE"` are not valid modes and cannot become
authority through this contract.

## State and persistence

A6.07 does not define a mutable global/current-mode object. The typed enum is
the complete selection representation required at this boundary. A later task
may carry a selected value through an appropriate request/runtime contract.

No persistence or migration is required or introduced. Mode choice has no
automatic lifetime beyond whatever later owner explicitly carries it.
