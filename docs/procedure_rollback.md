# N2.18 — Procedure Rollback Transaction

`agentx.procedure_rollback` implements the explicit rollback transaction that
restores a previously known Procedure revision after canonical rollback
eligibility has already been established by `agentx.core.procedure_replacement`.

## Purpose

Rollback is the controlled act of abandoning a live (bad) revision and
restoring a known-good historical revision. It is:

- NOT history rewriting
- NOT deletion
- NOT automatic repair
- NOT verified success

## Rollback Model

Rollback consumes explicit canonical evidence:

- **current live ProcedureId / revision** — expected ACTIVE revision
- **exact known-good historical revision** — caller-supplied, never inferred from age or revision number
- **canonical rollback/replacement-policy eligibility** — a `ProcedureReplacementDecision` with outcome `ELIGIBLE`, kind `ROLLBACK`, matching procedure_id, active_revision, target_revision
- **exact current stored state** — optional `expected_known_revisions` tuple for concurrency guard, plus `requested_at` timestamp for staleness detection

Then applies only the legal lifecycle/persistence transition atomically.

Caller must supply exact historical target revision. The transaction never selects "nearest good revision" automatically.

## Known-Good Evidence Semantics

Known-good is not inferred from age, revision number, or payload text. It is proven by closed typed evidence:

- `validation_evidence` PRESENT (required for both forward and rollback)
- `target_integrity` INTACT (CORRUPT and UNKNOWN both fail closed)
- At least one explicit `evidence_references` entry (opaque identifier owned by caller's audit trail)
- `shadow_evidence` is NOT required for rollback (only forward replacement needs it)

The eligibility decision (`ProcedureReplacementDecision`) is the canonical authority for whether target is allowed by rollback policy. If decision is not ELIGIBLE or kind is not ROLLBACK, rollback is rejected as `INELIGIBLE_TARGET` or `MALFORMED_ELIGIBILITY`.

Target must also exist in store, belong to same ProcedureId, and not already be ACTIVE. Structurally invalid or corrupt rows are rejected as `TARGET_INVALID_CORRUPT`.

## Rollback Target Policy

- Same `ProcedureId` on both records
- `target.revision < active.revision` (strictly earlier)
- `target.revision` present in caller's known history (must exist in store)
- Explicitly declared rollback reason (`MANUAL_ROLLBACK`, `REGRESSION_ROLLBACK`, `SAFETY_ROLLBACK`, `ENVIRONMENT_INCOMPATIBILITY`)
- Target status:
  - `CANDIDATE` — normal case, transaction retires current ACTIVE and activates target CANDIDATE
  - `RETIRED` — allowed only with explicit attestation `CONTROLLED_LIFECYCLE_ACT`. Because canonical lifecycle says RETIRED is terminal (no resurrection), this transaction creates a NEW revision N+1 copying payload/scope from retired target, retires current ACTIVE, and activates new revision. History is fully preserved; retired target stays RETIRED.
  - `ACTIVE` — rejected (`TARGET_ALREADY_ACTIVE`), canonical policy forbids two ACTIVE revisions

Rollback never clones arbitrarily, never renumbers existing history, never deletes. A rollback that creates new revision preserves both bad revision and old target revision.

## History Preservation

Never deletes:

- bad/current revision
- old target revision
- replacement relation
- failure/degradation evidence
- validation evidence
- shadow evidence
- audit history

No `UPDATE` of historical content to pretend failed revision never existed. Payload content is never rewritten; only status and `updated_at` change via explicit `ProcedureRecord` reconstruction. For RETIRED target path, new revision's payload is copy of target's payload, but old target's payload remains untouched.

## Atomicity

Uses `BEGIN IMMEDIATE` transaction on `ProcedureStore.database`. Inside transaction:

1. Re-check current revision still ACTIVE and max revision unchanged (prevents TOCTOU and concurrent-store changes)
2. If expected_known_revisions provided, verify it still matches actual store
3. Retire current ACTIVE (`ACTIVE -> RETIRED`)
4. Either activate target CANDIDATE (`CANDIDATE -> ACTIVE`) or insert new revision from RETIRED target and activate it

Either previous ACTIVE remains single ACTIVE, or rollback result is single ACTIVE. No partial lifecycle state observable. On any failure, transaction rolls back and returns REJECTED with explicit failure reason.

Prevents stale rollback requests from reverting newer legitimate revision: if store max revision > current_revision, or current record's `updated_at` > `requested_at`, or expected_known_revisions mismatch, request is rejected as `STALE_REQUEST` or `CONCURRENT_STORE_CHANGED`.

No broad persistence redesign, no migration. Highest migration remains 8.

## Stale Request Behavior

Stale if:

- Store has newer revision than `current_revision` (`max > current`)
- Current record's `updated_at` is newer than `requested_at`
- `expected_known_revisions` provided and does not match actual known revisions

Stale requests are rejected fail-closed, with no mutation. This prevents an old rollback request from undoing a newer legitimate forward replacement.

## Fail-Closed Conditions

- `PROCEDURE_ID_MISMATCH` — request procedure_id != eligibility procedure_id
- `CURRENT_REVISION_MISMATCH` — current revision absent or eligibility active_revision != request current
- `CURRENT_NOT_ACTIVE` — current revision status != ACTIVE
- `TARGET_REVISION_ABSENT` — target not in store
- `CROSS_PROCEDURE_TARGET` — target belongs to another Procedure
- `INELIGIBLE_TARGET` — eligibility outcome != ELIGIBLE
- `MALFORMED_ELIGIBILITY` — eligibility kind != ROLLBACK or fields malformed
- `STALE_REQUEST` — newer revision exists or updated_at newer than requested_at
- `CONCURRENT_STORE_CHANGED` — expected_known_revisions mismatch or max revision changed during transaction
- `TARGET_LACKS_KNOWN_GOOD_EVIDENCE` — reserved, covered by INELIGIBLE
- `TARGET_INVALID_CORRUPT` — row decode fails
- `TARGET_ALREADY_ACTIVE` — target already ACTIVE
- `PERSISTENCE_CONFLICT` — SQLite error, duplicate, sequence error
- `INVALID_REQUEST` — malformed request fields

No "nearest good revision" fallback. All rejections leave store unchanged.

## No Authority / No Verification Claim

Rollback transaction:

- Cannot create Permission
- Cannot bypass ActionGate
- Cannot lower Risk
- Cannot widen budgets
- Cannot clear EmergencyStop
- Cannot execute Procedure
- Cannot execute capability
- Cannot fabricate Task success

Strings like `rollback_approved=true`, `known_good=true`, `permission=ADMIN`, `risk=R0`, `verified=true` inside payload/metadata are inert.

Rollback means only lifecycle/version restoration. It does NOT prove:

- current environment still supports historical revision
- original failure is fixed
- current Task succeeds
- capabilities authorized
- verification passed

Future execution must still use canonical runtime verification.

## Result

`ProcedureRollbackResult` is bounded deterministic record:

- `outcome`: APPLIED | REJECTED
- `procedure_id`: ProcedureId
- `previous_current_revision`: int (expected live before rollback)
- `rollback_target`: int (requested target)
- `resulting_revision`: int | None (now ACTIVE, either target or new)
- `resulting_status`: ProcedureStatus | None (ACTIVE when applied)
- `failure_reason`: RollbackFailureReason | None (when rejected)
- `explanation`: str (deterministic, from controlled vocabulary)
- `schema_version`: 1

No generic workflow engine, no bool-only API.

## Dependencies

- `agentx.core.ids`
- `agentx.core.procedures`
- `agentx.core.procedure_replacement`
- `agentx.infrastructure.procedure_store`
- stdlib: `sqlite3`, `datetime`, `enum`, `dataclasses`, `contextlib`, `typing`

No kernel, no capabilities, no cognition, no Hive, no learning, no procedures interpreter, no model, no network.

## Testing

- Unit: valid rollback, exact target, history preserved, wrong ProcedureId, wrong current, missing target, cross-Procedure, ineligible, stale, concurrent mismatch, no partial mutation, repeated rollback, hostile metadata inert, no execution, no verification claim, result facts
- Integration: persistence across restart, retired target new revision path
- Adversarial: authority boundary (no kernel/capability touch), hostile metadata inert, no Procedure/capability execution, no Task success fabrication, Permission/ActionGate/Risk/budget/EmergencyStop unaffected, no model call, deterministic
- Architecture: placement in `agentx` top-level, limited imports, no migration, no workflow engine, no authority verbs, frozen dataclasses, no deletion/renumber verbs
