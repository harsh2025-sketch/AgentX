# Windows provider boundary (A5.01)

> **Status:** A5.01. This task establishes the Windows provider/adapter
> boundary only. It implements **no Windows automation**. Automation is owned
> by A5.02-A5.10.

## What A5.01 is

AgentX is Windows-first, but the cognitive/runtime layer must never know that.
The runtime asks for *capabilities*; a provider implements Windows-specific
surfaces behind the canonical capability contracts.

`agentx.capabilities.windows.provider` therefore answers exactly three
questions:

| Question | Contract |
| -------- | -------- |
| Who is the Windows provider? | `WindowsProviderIdentity` / `WINDOWS_PROVIDER_IDENTITY` |
| Can it operate on this host? | `PlatformFacts` -> `evaluate_windows_support` -> `WindowsSupport` |
| How do Windows capabilities reach the execution path? | `WindowsProvider.contribute` / `WindowsProvider.register_into` |

Everything else that a Windows adapter will eventually need — process/window
discovery, the UIA tree, semantic controls, native invocation, keyboard/text,
dialogs, capture, visual fallback, verification — is deliberately absent.

## Relationship to the Capability ABI and Registry

There is **no** `WindowsCapabilityV2`. A5.01 composes the canonical contracts
and duplicates none of them:

- Windows capabilities are ordinary `Capability` implementations (A1.08).
- They describe themselves with the canonical `CapabilityDescriptor`, keyed by
  `CapabilityIdentity` (`CapabilityName` + explicit `CapabilityVersion`).
- Platform scope reuses the canonical `CapabilityScope` /
  `CapabilityPlatform.WINDOWS` vocabulary; the provider accepts only
  `windows`-scoped capabilities and rejects `any`.
- Registration targets a **caller-owned** A1.09 `CapabilityRegistry`. The
  provider owns no registry, is not a singleton, and holds no ambient state.
- Failures are canonical `AgentXError` values carried in `Result`. A5.01 adds
  **no** Windows-specific exception type: `PRECONDITION` / `NON_RETRYABLE`
  already expresses "this host is not Windows", under the stable code
  `capabilities.windows.unsupported_platform`.

The only genuinely new concepts are *provider identity* and *provider support*,
which the canonical ABI does not express. A provider identity is deliberately
not a `CapabilityIdentity`: a provider is not a capability and must never be
registered as one.

## Platform detection and support semantics

Detection is explicit, pure and testable:

- `detect_platform_facts()` is the only host-reading function. It reads
  `platform`/`sys` **strings** only — never a native API — so it is safe on
  Linux and macOS.
- `evaluate_windows_support(facts)` is a pure function of an explicit
  `PlatformFacts` value. Both branches (supported / unsupported) are unit
  testable on any host, deterministically.
- `WindowsProvider(support)` requires the verdict to be supplied by the caller.
  `WindowsProvider.for_current_host()` exists for application wiring and is
  never invoked at import time.
- An unsupported host fails **predictably**: `contribute` and `register_into`
  return an explicit canonical failure and change nothing. There is no
  `ImportError` from an eager native import and no silent no-op.

## Import-side-effect guarantees

Importing `agentx`, `agentx.capabilities.windows`, or
`agentx.capabilities.windows.provider`:

- reads no platform state,
- imports no native, COM, UIA or automation module,
- registers no capability anywhere,
- starts no thread, opens no handle, writes nothing,
- executes no top-level statement other than declarative constants.

Architecture tests assert all of this statically (AST) and dynamically (clean
subprocess probes), so they hold on Windows and non-Windows CI alike.

## Authority guarantees

> **AVAILABILITY IS NOT AUTHORITY.**

The provider never grants a `Permission`, never creates or widens an
`AuthorityContext`, never invokes the `ActionGate`, never alters a
`RiskAssessment`, never enlarges a `ResourceEnvelope`, never clears an
`EmergencyStop`, never calls `execute`/`verify`, never mutates Task state, and
never produces a verification verdict. Provider and descriptor text is inert:
hostile metadata is stored verbatim and authorizes exactly nothing. The
provider object itself is immutable after construction, so it cannot promote
its own support verdict.

The Trusted Kernel (`agentx.kernel`) remains the sole authority boundary.

## Dependencies and persistence

Zero new runtime dependencies. In particular **no `pywin32`**: a provider
boundary does not need one, and the native dependency belongs to the task that
first makes a real Windows call. No persistence: the provider stores nothing on
disk, defines no schema, and performs no migration. Its state is in-process and
caller-scoped.
