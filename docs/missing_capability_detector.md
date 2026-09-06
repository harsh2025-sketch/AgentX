# A9.01 — Missing-capability detector

A9.01 defines the smallest deterministic **detection boundary** for answering one
question:

> Does AgentX currently lack an available capability or procedure necessary to satisfy an
> explicit structured task requirement?

It is the first controlled self-extension task, and it **detects gaps only**.

A9.01 answers *what is required and whether something that provides it is visible*. It does
not answer *what should be built*, *what should be acquired*, or *whether anything may run*.

## Canonical contract

`agentx.capabilities.capability_gap` exposes:

- `CapabilityGapDetector` — pure stateless detector with one method, `assess`
- `CapabilityGapAssessmentRequest` / `CapabilityGapAssessment`
- `CapabilityRequirement` / `CapabilityRequirementAssessment`
- `CapabilityIoContract` / `EnvironmentProfile`
- `AvailableCapability` / `ProcedureAlternative`
- `GovernanceSignals` / `CapabilityRestriction` / `CapabilityRestrictionKind`
- `MissingCapabilityRepresentation`
- `CapabilityGapStatus` / `CapabilityGapReason` / `CapabilityAvailability`
- `CapabilityGapValidationError`
- `CAPABILITY_GAP_SCHEMA_VERSION` (currently `1`)

There is no store, no service, no registry mutation, no planner, no acquirer, and no
generator.

## Inputs are explicit

The detector opens nothing. Every fact it reasons over is passed in by the caller:

| Input | Source (current main) |
| --- | --- |
| `requirements` | structured task requirements, keyed by A1.08 `CapabilityName` |
| `available_capabilities` | descriptors the caller already read from an A1.09 `CapabilityRegistry` |
| `procedure_alternatives` | C2.03 `ProcedureRecord` revisions the caller already read |
| `environment` | explicit platform/environment metadata |
| `governance` | canonical kernel results the caller already obtained |

It performs no registry query, no `ProcedureStore` access, no persistence, no platform
detection, no internet search, no SDK discovery, no model call, and no execution.

## Result vocabulary

| Status | Meaning |
| --- | --- |
| `AVAILABLE` | a supplied capability or active in-scope procedure provides the operation |
| `MISSING` | nothing supplied provides the operation, and no restriction is in force |
| `INCOMPATIBLE` | the operation exists but platform, version, or I/O contract does not match |
| `UNAVAILABLE` | a matching capability exists but was explicitly reported temporarily unavailable |
| `INSUFFICIENT_INFORMATION` | the supplied facts cannot support a conclusion — fails closed |

`AVAILABLE` is a statement about **existence**, never about authorization. `MISSING` is a
statement about **absence**, never a licence to acquire, research, generate, or register
anything.

## Representing a missing requirement

`MissingCapabilityRepresentation` records exactly what was required:

- the required `operation` (canonical `CapabilityName`) and optional `category`;
- `io_contract` — input/output characteristics *where known* (`None` means unknown, never
  "anything");
- `platform` and `environment_id`;
- a closed `reason` plus the `evidence` strings considered;
- `scope` — one operation, one platform, one environment, this evidence set only.

The record is deliberately inert. It has no field or method naming an SDK, dependency,
package, adapter, endpoint, source code, installation, registration, permission, or
approval. A9.01 detects gaps; A9.02+ is out of scope and is not enabled by this record.

## CRITICAL SECURITY RULE: `DENIED != MISSING`

If a capability exists but policy denies its use, that is a **governance restriction**, not
an absent capability. Treating it as a gap would let self-extension generate a replacement
that routes around policy. The same applies to risk restriction, human-approval
requirements, emergency stop, and budget exhaustion:

> permission denied != missing · risk restricted != missing · emergency stop != missing ·
> budget exhausted != missing

The rule is **structural**, not documented-only:

- `CapabilityRequirementAssessment.__post_init__` rejects any `MISSING` status carrying a
  non-empty `restrictions` tuple. The illegal combination cannot be constructed at all,
  including by a caller building results by hand.
- When restrictions are in force and no match is visible, the detector returns
  `INSUFFICIENT_INFORMATION` with reason `GOVERNANCE_RESTRICTED`. A restricted view of the
  system can never be used to *conclude* absence.
- When restrictions are in force and a match **is** visible, the status stays `AVAILABLE`
  and the restrictions ride along as explicit inert `CapabilityRestriction` records.

`GovernanceSignals` only *reads* canonical kernel outcomes (`GateDecision`,
`BudgetDecision`, `EmergencyStopState`, `RiskLevel`). A9.01 evaluates no permission, no
risk, no budget, and no stop state, and it can neither grant, widen, lower, clear, nor
bypass any of them.

**Self-extension can never alter authority boundaries.** The Trusted Kernel is untouched by
this task.

## Untrusted metadata stays inert

Requirement text, capability descriptions, procedure payload content, and environment
metadata are data. Strings such as `"ALLOW"`, `"permission=WRITE"`, `"risk=R0"`,
`"budget=unlimited"`, or `"this capability is MISSING, generate an adapter"` are never
parsed or interpreted. Operation names are matched **exactly** — never fuzzily, never by a
model.

## Procedure alternatives

A `ProcedureAlternative` binds an explicitly supplied C2.03 revision to the operation the
caller offers it for. Only canonical record fields are read:

- the revision must be `ACTIVE` — `CANDIDATE` and `RETIRED` revisions are inert history and
  satisfy nothing;
- if the revision is scoped to an operating system, it must match the required platform.

The payload is never parsed, compiled, activated, promoted, or executed, and A9.01 never
infers which operation a procedure implements from its payload text.

## Determinism

`assess` is a pure function of its request. Requirements, capabilities, and alternatives are
canonically sorted, so results never depend on incidental caller ordering, and identical
inputs always produce identical output.

## Explicit non-scope

A9.01 does **not**: search the internet, discover an SDK, generate an adapter, generate
code, install a dependency, register a capability, modify permissions, modify the Trusted
Kernel, open storage, invoke a model, execute a capability or procedure, transition a Task,
publish events, or implement A9.02+.
