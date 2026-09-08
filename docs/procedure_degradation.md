# M5.01 — Procedure Degradation Evidence Detector

M5.01 defines a pure, deterministic **assessment policy** that answers:

> Given bounded typed evidence about one procedure revision, should that
> revision be considered `UNKNOWN`, `HEALTHY`, `SUSPECTED_DEGRADED`, or
> `CONFIRMED_DEGRADED`?

It does **not** mutate procedure lifecycle. `ProcedureStatus` remains
`CANDIDATE` / `ACTIVE` / `RETIRED` only — there is no `DEGRADED` status, and
this module never adds one. Assessment is data, never authority and never a
repair instruction.

## Placement

`agentx.procedure_degradation` is a **top-level composition module** at the
`agentx` namespace root (sibling of `agent_loop`). It composes inward
`agentx.core` contracts only:

| Contract | Module |
| --- | --- |
| Procedure identity / revision | `agentx.core.procedures.ProcedureRecord`, `agentx.core.ids.ProcedureId` |
| Failure classification | `agentx.core.failure_taxonomy` (C4.01) |
| Failure localization | `agentx.core.failure_localization` (C4.02) |
| Failure diagnosis | `agentx.core.failure_diagnosis` (C4.03) |
| Environment-change detection | `agentx.core.environment_change` (C4.04) |
| Verified success | `agentx.core.causal_experience.CausalExperience` (C2.10) |

It imports nothing from `kernel`, `capabilities`, `cognition`, `learning`,
`hive`, `infrastructure`, or `procedures` execution. `_architecture.py` is
unchanged.

## Public surface

- `DegradationState` / `CANONICAL_DEGRADATION_STATES`
- `BoundFailureDiagnosis`
- `BoundEnvironmentChange`
- `ProcedureSuccessEvidence`
- `ProcedureDegradationAssessment`
- `assess_procedure_degradation`
- `MAX_FAILURE_EVIDENCE` (32)
- `MAX_ENVIRONMENT_EVIDENCE` (16)
- `MAX_SUCCESS_EVIDENCE` (32)
- `ProcedureDegradationValidationError`

There is no store, no service, no scanner, no model client, no repair planner,
and no policy object that grants authority.

## Assessment states

| State | Meaning |
| --- | --- |
| `UNKNOWN` | No matching evidence justifies any other state (fail closed). |
| `HEALTHY` | Matching verified success supports past reliability; no later matching implicating failure overturns it. |
| `SUSPECTED_DEGRADED` | Matching structured evidence raises suspicion below the confirmation threshold. |
| `CONFIRMED_DEGRADED` | Strong structured evidence meets the confirmation threshold for this revision. |

Free text such as `"procedure broken"` or `"confirmed=true"` never produces
any state. Confirmation requires typed structured facts only.

## Evidence matching rules

1. **Procedure identity.** A failure diagnosis matches only when its embedded
   C4.02 localization `procedure_id` equals the target. Environment-change
   evidence matches via the same identity on its embedded C4.03 diagnosis.
   Success evidence matches via its explicit `procedure_id`. Unrelated IDs
   are ignored (recorded in `ignored_*_indices`).
2. **Revision.** Evidence bound to a *different* revision never transfers to
   the target revision. Old-revision failures remain historical evidence for
   that old revision only. Success evidence must carry the exact target
   revision.
3. **Revision binding for failures/env.** C4.03/C4.04 records do not natively
   carry procedure revision. Callers bind revision through
   `BoundFailureDiagnosis.procedure_revision` /
   `BoundEnvironmentChange.procedure_revision`. Unbound matching evidence may
   raise suspicion by identity but **never alone confirms** degradation of a
   specific revision (recorded as an uncertainty).
4. **No text inference.** Summaries, details, hostile strings, and log
   messages are never parsed for application names, keywords, or authority
   claims.

## Failure handling

| Category (C4.01) | Effect |
| --- | --- |
| `PERMISSION` | Never proves procedure degradation. |
| `TRANSIENT` | Never proves procedure degradation. |
| `CAPABILITY` | Never proves procedure degradation. |
| `DEPENDENCY` | Never proves procedure degradation. |
| `PRECONDITION` | Never proves procedure degradation. |
| `ENVIRONMENT` | Category alone does not prove degradation (use C4.04 change evidence). |
| `KNOWLEDGE` / `PLAN` / `UNKNOWN` | Non-implicating. |
| `PROCEDURE` without `NODE_IMPLICATED` | Weak suspicion only. |
| `VERIFICATION` / `UI_CHANGE` / `API_CHANGE` without node implication | Weak suspicion. |
| `NODE_IMPLICATED` + procedure-side category | **Strong** evidence. |

Resource exhaustion is not a C4.01 category; global resource pressure is
outside this policy and never invents degradation. Permission denial never
proves the procedure definition is bad.

## Environment-change handling

Only a C4.04 result of `RELEVANT_CHANGE_DETECTED` whose embedded diagnosis
matches the target procedure may raise suspicion. Linkage is by typed
`procedure_id` on the embedded diagnosis — never by parsing observation text.
Environment change alone yields at most `SUSPECTED_DEGRADED`. Confirmation
requires the change **plus** at least one revision-bound strong
(`NODE_IMPLICATED`) failure.

## Success evidence

`ProcedureSuccessEvidence` requires a canonical C2.10 `CausalExperience` with
`outcome == VERIFIED` (passing verification). It is evidence of **past**
success for exactly one `(procedure_id, revision)` pair:

- It is not future authority.
- It does not erase later matching failures.
- A later matching verified success can supersede older matching failures for
  the `HEALTHY` path (timestamp order, deterministic).
- Evidence is never averaged into a confidence score.

## Confirmation threshold

`CONFIRMED_DEGRADED` requires **revision-bound** strong evidence:

- at least **two** revision-bound `NODE_IMPLICATED` matching failures, **or**
- at least **one** revision-bound strong failure **and** one revision-bound
  matching `RELEVANT_CHANGE_DETECTED` environment change.

Anything less that still matches yields `SUSPECTED_DEGRADED` or `UNKNOWN` /
`HEALTHY` per the rules above.

## Hard bounds

Caller-supplied sequences are hard-capped:

| Input | Max |
| --- | --- |
| Failure diagnoses | 32 |
| Environment-change detections | 16 |
| Success evidence records | 32 |

Exceeding a bound raises `ProcedureDegradationValidationError`. The module
never scans stores or accepts unbounded streams.

## No side effects / zero authority

`assess_procedure_degradation` never:

- retires, activates, or versions a procedure
- changes `ProcedureStatus`
- creates a repair candidate or executes repair
- queries a database, filesystem, network, or model
- reads a clock (`assessed_at` is caller-supplied)
- grants Permission, lowers risk, widens a budget, or clears EmergencyStop
- writes Events, audit records, or Hive state

Hostile strings remain inert data in reasons/uncertainties.

## Determinism

Same typed inputs → same assessment. No randomness, no wall clock, no I/O.

## Non-goals

- Modifying `ProcedureStatus` or adding `DEGRADED`
- Repair selection, patch generation, shadow repair, repair budgets (C4.09)
- Persistence / migration of health state
- Environment sensing or store queries
- Confidence floats or ranking
