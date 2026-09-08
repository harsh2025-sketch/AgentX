# Explicit procedure promotion transaction (N2.10)

N2.10 owns the single explicit, reviewed transaction boundary that may apply an
**already-canonical** promotion decision to canonical persistence: it is the
only step at which a procedure revision's persisted status may become
`ACTIVE`. It lives at `agentx.procedure_promotion` as a top-level composition
module (alongside `agentx.procedure_validation`).

**`CANDIDATE != ELIGIBLE != ACTIVE` — unchanged.** A validation report is
evidence, not state; eligibility evidence by itself never mutates
persistence. Promotion happens only through this explicit transaction, and
`ACTIVE` is a lifecycle status, never authority.

## Position in the architecture

| Concern | Owner |
| --- | --- |
| Record schema, `ProcedureStatus` vocabulary, revision identity | C2.03 `agentx.core.procedures` |
| Durable append-only storage, `update_status` write | C2.03 `agentx.infrastructure.procedure_store` |
| Validation evidence sufficiency (`ELIGIBLE_FOR_PROMOTION`) | M4.02 `agentx.procedure_validation` |
| Structural transition legality (`CANDIDATE -> ACTIVE`) | M4.03 `agentx.core.procedure_lifecycle` |
| **Applying both decisions atomically to persistence (this task)** | **N2.10 `agentx.procedure_promotion`** |

N2.10 re-decides nothing. It consumes the canonical typed artifacts, re-verifies
every one of them against the exact stored record, and performs one atomic
compare-and-set status write. The subsystem boundary manifest
(`agentx._architecture`) is deliberately unchanged.

## Required inputs (no booleans, no free text)

`ProcedurePromotionRequest` carries exactly the canonical typed facts:

| Field | Meaning |
| --- | --- |
| `procedure_id` | the exact canonical `ProcedureId` |
| `revision` | the exact candidate revision |
| `expected_current` | the exact stored `ProcedureRecord` the caller validated |
| `validation_report` | canonical M4.02 `ValidationReport` bound to that identity |
| `lifecycle_assessment` | canonical M4.03 `ProcedureLifecycleAssessment` for that identity |
| `requested_at` | explicit reviewed instant (UTC-normalized; persisted as `updated_at`) |

There is no `approved=`, `eligible=`, or `verified=` flag anywhere. Raw
booleans, raw strings, and lookalike objects fail closed with
`ProcedurePromotionError` (a `ProcedureValidationError`); only the canonical
frozen evidence types are accepted, and hostile text is inert data.

## Fixed decision order (fail closed)

`promote_procedure_candidate(store, request)` returns a typed
`ProcedurePromotionResult` in every decided case; the first failure wins and
**nothing is ever written on a rejection**:

1. Malformed store/request types raise `ProcedurePromotionError`.
2. Request-internal coherence: `procedure_id`/`revision` must match the
   expected-current record (`REJECTED_IDENTITY_MISMATCH` /
   `REJECTED_REVISION_MISMATCH`).
3. The validation report must be bound to exactly that identity
   (`REJECTED_VALIDATION_EVIDENCE_FOREIGN`).
4. Its decision must be `ELIGIBLE_FOR_PROMOTION`
   (`REJECTED_VALIDATION_NOT_ELIGIBLE` — covers negative, insufficient, and
   degraded evidence; the canonical decision is echoed on the result).
5. The lifecycle assessment must describe exactly `CANDIDATE -> ACTIVE` for
   reason `VALIDATION_PROMOTION` with decision `ALLOWED`, and the canonical
   policy must re-derive that same verdict
   (`REJECTED_LIFECYCLE_DECISION_INVALID`).
6. The stored record must exist and must equal the expected-current record
   exactly (`REJECTED_PROCEDURE_ABSENT` / `REJECTED_STALE_CURRENT_RECORD`).
   Stale requests are rejected, never refreshed.
7. The canonical policy must permit the *stored* status's move to `ACTIVE`:
   an already-`ACTIVE` revision is `REJECTED_REPEATED_PROMOTION` (the
   canonical `NO_OP` — a no-op never permits a status write); a `RETIRED`
   revision is `REJECTED_RETIRED_TERMINAL` (retirement is terminal and
   monotonic).
8. The write commits through
   `ProcedureStore.update_status_if_current(...)`: the compare-and-set
   re-reads and re-compares the stored record inside the same
   `BEGIN IMMEDIATE` transaction that writes, so a concurrent writer that
   changed the row after step 6 loses the race deterministically
   (`REJECTED_STALE_CURRENT_RECORD`) and nothing is persisted.

Infrastructure storage failures are raised, not swallowed (the store has
already rolled back, so persistence stays untouched); every deterministic
outcome is a typed result.

## Atomicity, concurrency, and history

* The write is **atomic**: check and write share one `BEGIN IMMEDIATE`
  transaction, so no interleaved writer (thread or process) can slip a
  retirement, a status flip, or a new revision between the verification and
  the commit.
* **Stale requests never activate stale data.** A promotion validated against
  revision *r* while it was `CANDIDATE` cannot land after another transaction
  retired or re-statused that revision: the compare-and-set refuses and the
  caller must re-read and re-validate.
* The write mutates **only** the status/`updated_at` of the one named
  revision. Revisions stay append-only; no row is created or deleted; the
  payload, scope, and timestamps of history are never rewritten; previously
  returned record objects remain immutable snapshots; validation evidence is
  never persisted into, or removed from, the procedure store.
* **Idempotency follows canonical policy, not convenience:** repeating a
  stale request is `REJECTED_STALE_CURRENT_RECORD`; a fresh request against
  an already-`ACTIVE` revision is `REJECTED_REPEATED_PROMOTION`. Neither
  writes anything, so repeated attempts cannot double-commit or corrupt
  history.

## Result

`ProcedurePromotionResult` is an immutable value that states explicitly:
whether promotion occurred (`outcome` / derived `promoted` property), the
exact `procedure_id`/`revision`, the prior lifecycle state, the resulting
lifecycle state (`ACTIVE` exactly when promoted), the canonical
validation/lifecycle decisions consulted, and a bounded `explanation`
composed only from canonical enum values and identities — hostile text can
never leak into it. The result is deterministic: the same request against the
same persisted state always yields an equal result.

## Authority boundary

`ACTIVE` means lifecycle status only. A promoted revision does **not** grant
Permission, authorize a specific run, bypass the ActionGate, reduce RiskLevel,
enlarge the ResourceBudget, clear an EmergencyStop, satisfy environment
applicability, mark a Task successful, or provide current verification. The
module imports no kernel contract and executes no capability, model call, or
research; execution still goes through the normal governed paths. Strings
such as `activate_candidate=true`, `permission=ADMIN`, `risk=R0`,
`skip_action_gate=true`, or `verified=true` in any payload cannot promote
anything and cannot leak into any result.

## Store seam

One tiny additive store call was required and added (no store redesign):
`ProcedureStore.update_status_if_current(...)` — the compare-and-set variant
of `update_status` that refuses (typed `ProcedureStaleRecordError`, nothing
written) unless the stored record still equals the caller's expected record.
`update_status` itself is unchanged, so all existing callers keep their exact
semantics.

## Non-goals

No validation/evidence policy, no lifecycle matrix changes, no retirement
path, no revision creation or increment, no payload/scope rewriting, no
procedure execution or verification, no Permission/ActionGate/Risk/budget/
EmergencyStop/Task effects, no second audit system, no migration, and no
architecture-manifest change.

## Test map

| Suite | Coverage |
| --- | --- |
| `tests/unit/test_procedure_promotion.py` | decision order, every rejection reason, compare-and-set seam, race injection, malformed requests, immutability, history/evidence preservation, determinism |
| `tests/integration/test_procedure_promotion.py` | end-to-end canonical pipeline on real SQLite, restart persistence, retire-vs-promote stale behavior, concurrent promotions (single winner), append-only history |
| `tests/adversarial/test_procedure_promotion_authority.py` | hostile payloads, forged lookalikes, raw booleans, SQL injection, resurrection attempts, zero authority/execution/model contact, static import/call scans |
| `tests/architecture/test_procedure_promotion_boundaries.py` | placement, manifest unchanged, import/call boundaries, disjoint vocabularies, canonical-policy reuse, keyword-only CAS seam |
