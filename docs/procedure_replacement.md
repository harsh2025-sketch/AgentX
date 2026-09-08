# M5.06 / C4.08 — Procedure Revision Replacement and Rollback Policy

`agentx.core.procedure_replacement` defines the deterministic **eligibility policy** that
decides whether one stored procedure revision may replace another as the live revision.

C4.08 answers exactly one question:

> *May the designated `ACTIVE` revision be replaced by this target revision, for this
> declared reason, on this declared evidence?*

It does **not** answer "should AgentX do it?", "is it authorized?", or "is it safe to
execute?". Those belong to the Trusted Kernel and to lifecycle owners.

## Why the policy is separate from storage

`agentx.core.procedures` (C2.03) models procedure revisions as immutable, **append-only**
history identified by `(procedure_id, revision)`. `agentx.infrastructure.procedure_store`
gives that history durable storage and enforces sequencing as a *storage integrity* rule:
revisions are contiguous, start at `1`, each insert must be exactly `max(stored) + 1`, and a
stored revision is never silently overwritten.

Those are mechanics. Storage deliberately decides nothing about **which revision should be
live**. Left ungoverned, the mechanics invite a silent-promotion bug: "the highest revision
number wins", or "the newest record wins", or "a `CANDIDATE` exists so it must be better".
This module closes that gap without gaining any ability to write.

## Canonical contracts

`agentx.core.procedure_replacement` exposes:

- `ProcedureReplacementKind` — `FORWARD_REPLACEMENT` | `ROLLBACK`
- `ProcedureReplacementReason` — closed motive vocabulary
- `ProcedureReplacementOutcome` — closed decision vocabulary
- `ProcedureReplacementFinding` — closed structured findings
- `EvidencePresence`, `TargetIntegrityState`, `RetiredTargetReactivation` — closed typed facts
- `ProcedureReplacementEvidence` — immutable typed evidence record
- `ProcedureReplacementRequest` — immutable request record
- `ProcedureReplacementDecision` — immutable, JSON-serializable decision record
- `assess_procedure_replacement(request) -> ProcedureReplacementDecision` — the only entry point
- `CURRENT_PROCEDURE_REPLACEMENT_SCHEMA_VERSION` (currently `1`)
- `ProcedureReplacementPolicyError`, `ProcedureReplacementRequestError`

There is no store, no service, no transaction, no planner, no model client, and no
executor.

## Replacement and rollback are different operations

| | `FORWARD_REPLACEMENT` | `ROLLBACK` |
|---|---|---|
| Direction | active `N` → `N+1` | active `N` → `M`, where `M < N` |
| Meaning | a repaired/improved revision takes over | the newer revision is abandoned |
| Target status | must be `CANDIDATE` | `CANDIDATE`, or `RETIRED` with attestation |
| Shadow evidence | required | not required |
| Permitted reasons | `VALIDATED_REPAIR`, `ENVIRONMENT_INCOMPATIBILITY` | `MANUAL_ROLLBACK`, `REGRESSION_ROLLBACK`, `SAFETY_ROLLBACK`, `ENVIRONMENT_INCOMPATIBILITY` |

The two are never conflated, and no third kind exists.

## Revision rules

### Forward replacement

All of the following must hold:

1. same `ProcedureId` on both records;
2. `target.revision > active.revision`;
3. `target.revision == active.revision + 1` — the canonical store keeps revisions
   **append-only** and contiguous, so an arbitrary jump is refused rather than silently
   accepted;
4. if the target revision is not part of the caller's `known_revisions`, it must be exactly
   `max(known_revisions) + 1`, the only number an append-only store can accept next;
5. the target's status is `CANDIDATE`.

### Rollback

All of the following must hold:

1. same `ProcedureId` on both records;
2. `target.revision < active.revision`;
3. `target.revision` is present in `known_revisions` — a rollback can only select a revision
   that actually exists;
4. an explicitly declared rollback reason;
5. an intact target.

A rollback never clones an old record into a new revision number. The target keeps its own
immutable `(procedure_id, revision)` identity.

## Status semantics

The record designated as active must actually carry `ProcedureStatus.ACTIVE`; otherwise there
is nothing to replace. A target that is already `ACTIVE` is refused, because canonical policy
must never leave two contradictory active revisions for one procedure.

`RETIRED` is treated asymmetrically on purpose:

- A **forward** target may not be `RETIRED`. A retired revision is withdrawn history; the
  architecturally correct path is to derive a *new* revision from that historical payload and
  have it assessed as its own forward replacement. History stays monotonic.
- A **rollback** to a `RETIRED` revision is permitted only as an explicitly attested
  controlled lifecycle act (`RetiredTargetReactivation.CONTROLLED_LIFECYCLE_ACT`) backed by
  the same evidence requirements as any other replacement. Without the attestation the
  outcome is `INSUFFICIENT_EVIDENCE`.

The policy never performs a transition. It assesses whether a *future* controlled transaction
could retire the current active revision and activate the target. No new status is invented,
no `DEGRADED` state is introduced, and no `RETIRED` record is resurrected by assessing. A
downstream lifecycle owner (C3.09) remains free to forbid `RETIRED → ACTIVE` outright and
require a derived new revision instead.

## Evidence requirements

Eligibility requires closed typed facts, never prose:

- at least one explicit `evidence_references` entry (an opaque identifier owned by the
  caller's audit trail — this module resolves nothing);
- `validation_evidence` is `PRESENT` (both kinds);
- `shadow_evidence` is `PRESENT` (forward replacement only);
- `target_integrity` is `INTACT` — `CORRUPT` and `UNKNOWN` both fail closed.

Every evidence field defaults to its fail-closed member. A reason explains *why* a
replacement is requested and grants no eligibility on its own; a free-text reference reading
`"validated"` is inert data, exactly like any other string.

## No silent replacement

The assessment reads **only** `procedure_id`, `revision`, `status`, and `scope` from the
caller-supplied records. It never reads `payload`, `created_at`, or `updated_at`. Therefore
none of the following can ever promote a revision:

- a higher revision number, or any "latest == best" reasoning;
- a newer timestamp;
- payload text such as `"fixed"`, `"verified=true"`, `"activate me"`, `"permission=ADMIN"`,
  `"risk=R0"`, or `"latest=true"`;
- the mere existence of a `CANDIDATE` record;
- a model's opinion that a revision is better.

Replacement is always an explicit, assessed act.

## History preservation

A rollback selects future live behaviour; it does not rewrite the past. The failed revision
stays stored, keeps its revision number, keeps its payload, and keeps its own status history.
This module has no delete, renumber, or rewrite concept at all — those verbs are absent from
its API and are asserted absent by the architecture guards.

## Decision vocabulary

| Outcome | Meaning |
|---|---|
| `ELIGIBLE` | structurally legal and sufficiently evidenced — **not** permission |
| `INELIGIBLE` | structurally illegal; more evidence cannot rescue it |
| `INSUFFICIENT_EVIDENCE` | structure is sound, required typed facts are missing |
| `INVALID_REVISION_RELATION` | the revision relation breaks canonical sequencing |
| `WRONG_PROCEDURE` | the two records are not the same procedure |

Every non-eligible decision carries a non-empty tuple of `ProcedureReplacementFinding`
members in a fixed canonical order, so two assessments of identical inputs are
byte-identical. There is no naked boolean anywhere in the API.

Precedence is fixed and documented: identity → revision relation → structure → evidence.

## Atomicity a future transaction must satisfy

This module implements no transaction. The decision is shaped so a future storage transaction
can honour it atomically. Such a transaction would need to perform, as one indivisible unit:

1. retire the current `ACTIVE` revision (an explicit status act);
2. register the target revision if it is new (append-only insert), and activate it.

It must never be observable half-applied: either the previous revision is still the single
`ACTIVE` one, or the target is. Because canonical policy forbids two contradictory `ACTIVE`
revisions for one procedure, a transaction that cannot complete both steps must apply
neither.

## Authority and execution boundary

**Eligibility is not execution permission.** An `ELIGIBLE` decision grants nothing: it cannot
grant a `Permission`, create an `AuthorityContext`, bypass an `ActionGate`, lower a
`RiskLevel`, widen a `ResourceEnvelope`, clear an `EmergencyStop`, transition a `Task`,
publish an `Event`, or execute a `Capability`. Even a fully eligible, repaired, active
procedure must still reach execution through the Trusted Kernel and its own approval
machinery. Authority belongs exclusively to `agentx.kernel`.

## Purity and determinism

The assessment touches no database, filesystem, clock, model, network, research service,
subprocess, or source of randomness — `datetime`, `time`, `os`, `pathlib`, `sqlite3`, and
`uuid` are not imported at all. The caller supplies the records and every fact. Identical
inputs always produce a byte-identical decision, and every returned decision is an immutable
snapshot that no later assessment can alter.

## Persistence decision

None. This task adds no table, no migration, no store, and no schema. The persistence
migration ladder is untouched and its highest landed migration remains `8`.

## Dependencies

`agentx.core.procedure_replacement` imports the standard library (`json`, `enum`,
`dataclasses`, `collections.abc`, `types`, `typing`) plus `agentx.core.ids` and
`agentx.core.procedures`. It imports no infrastructure, no kernel, no capabilities, no
cognition, no learning, no Hive, and no procedures interpreter — and it does not depend on
any unmerged work.

## Known limitations

- The policy trusts the caller's `known_revisions` as the authoritative history view; it
  cannot detect a caller that misreports which revisions are stored.
- When the forward target's revision is already in `known_revisions`, the policy cannot tell
  whether the supplied record *is* the stored one. Re-storing a duplicate pair stays the
  storage layer's job (`DuplicateProcedureRevisionError`).
- Eligibility says nothing about whether the repair is correct, safe, or desirable. Selection
  and execution remain owned by kernel policy and later repair tasks.
