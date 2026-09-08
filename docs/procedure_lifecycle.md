# Procedure lifecycle transition policy (M4.03)

M4.03 owns the single deterministic domain policy that decides **which
procedure-revision status transitions are structurally legal**. It lives at
`agentx.core.procedure_lifecycle` and is a pure decision boundary over the
canonical C2.03 `ProcedureStatus` vocabulary.

**`STRUCTURALLY_ALLOWED != EVIDENCE_VALIDATED`.** The policy answers *"is this
status transition structurally permitted for this reason?"*. It does **not**
answer *"has this candidate actually earned promotion?"*. Evidence sufficiency
is owned by the independent validation policy and by the future integration
layer; nothing here inspects, scores, or trusts evidence, and nothing here
promotes, retires, stores, or executes anything.

## Position in the architecture

| Concern | Owner |
| --- | --- |
| Procedure record schema, `ProcedureStatus` vocabulary, revision identity | C2.03 `agentx.core.procedures` |
| Durable storage and the explicit `update_status` write | C2.03 `agentx.infrastructure.procedure_store` |
| **Legal lifecycle transitions (this task)** | **M4.03 `agentx.core.procedure_lifecycle`** |
| Validation evidence sufficiency | Independent validation policy (not consumed here) |
| Procedure Graph IR / payload interpretation | A3.01 `agentx.procedures` |

Storage deliberately owns no trust or lifecycle policy: `ProcedureStore`
records a caller-chosen status verbatim. M4.03 adds the missing *domain* rule
without touching the record contract, the store, or persistence — the policy
holds no store reference at all, so it is structurally incapable of writing
anything.

## Legal transition matrix

| From \ To | CANDIDATE | ACTIVE | RETIRED |
| --- | --- | --- | --- |
| **CANDIDATE** | `NO_OP` | ✅ `VALIDATION_PROMOTION` only | ✅ any retirement reason |
| **ACTIVE** | ❌ `REJECTED_ILLEGAL_TRANSITION` | `NO_OP` | ✅ any retirement reason |
| **RETIRED** | ❌ `REJECTED_RETIREMENT_IS_TERMINAL` | ❌ `REJECTED_RETIREMENT_IS_TERMINAL` | `NO_OP` |

`LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS` is the single source of truth and is
exhaustive over the closed status vocabulary: a target that is not listed is
rejected — never inferred and never defaulted to "allowed".
`TERMINAL_PROCEDURE_STATUSES` (`{RETIRED}`) is derived from that same table so
terminality can never drift.

## Typed reason vocabulary

`ProcedureLifecycleReason` is closed. A reason is **data describing intent**:
it records why a caller says it wants a move. It never proves validation
happened, never creates authority, and never unlocks a forbidden transition.
Arbitrary strings are not accepted — `"validation_promotion"` (a plain `str`)
is rejected even though the enum is a `StrEnum`.

| Reason | Permitted for |
| --- | --- |
| `VALIDATION_PROMOTION` | `CANDIDATE -> ACTIVE` only |
| `MANUAL_RETIREMENT` | `CANDIDATE -> RETIRED`, `ACTIVE -> RETIRED` |
| `SUPERSEDED_BY_NEW_REVISION` | `CANDIDATE -> RETIRED`, `ACTIVE -> RETIRED` |
| `SAFETY_RETIREMENT` | `CANDIDATE -> RETIRED`, `ACTIVE -> RETIRED` |

A legal transition requested with the wrong reason (for example retiring with
`VALIDATION_PROMOTION`, or promoting with `MANUAL_RETIREMENT`) is rejected as
`REJECTED_REASON_MISMATCH`.

## Promotion is always explicit

A `CANDIDATE` never becomes `ACTIVE` because it was stored, loaded, has a
payload that says `verified=true`, succeeded once in a trajectory, was praised
by a model, parses as a syntactically valid graph, or ran without an
exception. The *only* structural path is an explicit
`VALIDATION_PROMOTION` request — and an `ALLOWED` verdict still leaves the
integrator responsible for proving the validation evidence before performing
the explicit `ProcedureStore.update_status` write.

## Retirement semantics

Retirement is **monotonic and terminal** for a revision identity
`(procedure_id, revision)`:

* no resurrection, no "unretire", no reset, no silent status rewind;
* no reason, revision number, identity, or timestamp unlocks a retired
  revision — every attempt returns `REJECTED_RETIREMENT_IS_TERMINAL`;
* a retired revision stays immutable historical data.

When a repaired or improved procedure is needed, the architecture creates a
**new revision** elsewhere. M4.03 never increments a revision, never creates
one, never copies a payload or scope, and never rewrites a persisted
timestamp.

## Same-status behaviour

`CANDIDATE -> CANDIDATE`, `ACTIVE -> ACTIVE`, and `RETIRED -> RETIRED` return
the explicit typed outcome `NO_OP`. The policy never pretends a transition
occurred: `permits_status_write` is `False` for a no-op, exactly as it is for
a rejection. Because nothing is being permitted, the requested reason is
recorded on the assessment but not matched against the reason table.

## Decision model

`assess_procedure_transition(...)` consumes only typed canonical facts —
`procedure_id`, `revision`, `current_status`, `target_status`, `reason`, and a
caller-supplied timezone-aware `requested_at` — and returns an immutable
`ProcedureLifecycleAssessment` carrying:

* `decision` — one `ProcedureLifecycleDecision` (`ALLOWED`, `NO_OP`,
  `REJECTED_ILLEGAL_TRANSITION`, `REJECTED_RETIREMENT_IS_TERMINAL`,
  `REJECTED_REASON_MISMATCH`);
* `permits_status_write` / `is_no_op` / `is_rejected` — derived, never stored;
* `permitted_reasons` — the deterministically ordered, duplicate-free reasons
  for that status pair (empty when the pair is illegal);
* `explanation` — a deterministic sentence composed **only** from controlled
  vocabulary, so hostile text can never leak into a decision;
* the echoed revision identity and the UTC-normalized `requested_at`.

Decision order is fixed: type validation → same-status `NO_OP` → transition
legality (terminal retirement distinguished) → reason compatibility →
`ALLOWED`.

Malformed input is a violated contract, not a lifecycle outcome: it raises
`ProcedureLifecycleError` (a `ProcedureValidationError`, hence a `ValueError`).
Wrong types, raw strings, foreign vocabularies (`KnowledgeStatus`,
`TaskStatus`), non-positive or boolean revisions, non-`ProcedureId`
identities, and naive datetimes all fail closed.

Supporting pure helpers: `legal_target_statuses`, `is_terminal_status`,
`permitted_reasons_for`, `is_transition_permitted`.

## Purity and inertness

No database, filesystem, network, model, research, clock read, UUID
generation, thread, subprocess, or randomness. The same inputs always produce
an equal assessment. Timestamps come from the caller.

An assessment is DATA. It grants no `Permission`, creates no
`AuthorityContext`, bypasses no `ActionGate`, changes no `RiskLevel`, enlarges
no `ResourceEnvelope`, clears no `EmergencyStop`, executes no `Capability`,
mutates no `Task`, and writes to no store. `ACTIVE` is a lifecycle state, not
authority: an active procedure remains subject to governed execution.

Decisions are transient in-memory values, so — mirroring the sibling pure
transition contract `agentx.core.task_state` — no serialization surface is
added. Callers that need an audit trail compose one from the typed fields.

## Non-goals

No validation/evidence policy, no candidate execution or verification, no
`ProcedureStore` promotion or mutation, no database update, no revision
creation, no procedure execution, no skill compilation, no degradation
detection (`DEGRADED` is deliberately **not** a `ProcedureStatus`), no repair,
no migration, and no architecture-manifest change.
