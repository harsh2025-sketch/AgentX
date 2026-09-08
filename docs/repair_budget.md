# M5.03 — Bounded Repair Attempt / Anti-Loop Policy (C4.09)

M5.03 defines the deterministic, bounded repair-attempt policy for `agentx.core.repair_budget`
— the "repair budgets / anti-loop (C4.09)" slot that the C4.01–C4.04 failure contracts defer to.

It answers exactly one question:

> Given explicit historical repair-attempt evidence for one repair target, may *another*
> repair attempt be **considered** under finite, explicit, repair-specific limits?

It does NOT answer: *should this repair run?* It does not execute the repair, does not consume
kernel budget, and does not override the kernel `ResourceBudget`. Both policies must eventually
pass for a repair to happen.

## Why this exists

Repair must be conservative. Failure must never create an autonomous mutation loop:

```
failure -> repair -> failed repair -> repair -> failed repair -> ... (forever)
```

The canonical Trusted Kernel `ResourceBudget` (C1.08) already bounds **global resource
accounting** — tokens, money, machine actions, wall-clock, and a single global
`repair_attempts` counter. The canonical cognition anti-loop (A2.09) already bounds **generic
agent-attempt repetition**. Neither owns the narrow repair-domain rule: *given this target's
own repair history — same proposal tried twice? three failures with no progress? — may another
attempt be considered?* M5.03 owns exactly that rule and nothing else.

## Canonical contracts

`agentx.core.repair_budget` exposes:

- `RepairTarget` / `ProcedureRepairTarget` / `RepairTargetFingerprint` — stable target identity
- `RepairProposalFingerprint` — stable identity of the exact repair proposal one attempt tried
- `RepairProgressMarker` — explicit, stable progress marker
- `RepairAttemptOutcome` + `CANONICAL_REPAIR_ATTEMPT_OUTCOMES` — the closed outcome vocabulary
- `RepairAttemptEvidence` — one immutable historical attempt record
- `RepairBudgetLimits` + `RepairBudgetScope` — explicit finite ceilings
- `RepairBudgetDecision` — the closed decision vocabulary
- `RepairBudgetAssessment` — the immutable decision-plus-counters result
- `assess_repair_attempt(...)` — the single pure evaluation entry point
- `RepairBudgetValidationError`

There is no store, no service, no executor, no scheduler, no clock, no thread, no model
client, and no persistence.

## `ALLOW_CONSIDERATION` is not authorization

The assessment is a loop-policy opinion, never a grant:

> allow != authorized · allow != safe · allow != selected · allow != executed ·
> allow != verified · stop != escalation

Composition with the canonical kernel budget is documented and enforced by separation:

| Repair policy | Kernel `ResourceBudget` | Result |
| --- | --- | --- |
| `ALLOW_CONSIDERATION` | `ALLOW` | repair may **still** be blocked by the Action Gate, risk checks, permissions, verification, … |
| `ALLOW_CONSIDERATION` | `DENY` | **NO REPAIR** |
| any `STOP_*` | `ALLOW` (budget remains) | **NO further repair** |
| any `STOP_*` | `DENY` | **NO REPAIR** |

A future repair executor must collect every gate independently; this module grants none of
them and its `ALLOW_CONSIDERATION` text says so.

## Limits model

`RepairBudgetLimits` is immutable, and every field is a **required positive integer** —
validated with `type(value) is not int`, so `bool` is rejected, and there is no `None`, no
`0`, no `-1`, no string sentinel, and no unlimited mode:

| Limit | Meaning | Resets? |
| --- | --- | --- |
| `max_total_attempts` | ceiling on recorded attempts, counted per `total_attempt_scope` | never — not by progress, not by validation |
| `max_attempts_per_proposal` | ceiling on exact occurrences of one proposal fingerprint for the target | never — a progress marker may justify a *different* proposal, but never revives an exhausted identical one |
| `max_consecutive_failures_without_progress` | ceiling on the trailing run of attempts that demonstrated neither a first-seen progress marker nor a `VALIDATED` outcome | only by genuinely new evidence |
| `total_attempt_scope` | `TARGET` (default semantics: only this target's evidence counts) or `SESSION` (explicit opt-in: every attempt in the supplied history counts toward the total) | n/a |

Reaching a ceiling exactly is terminal for the next attempt. Trigger priority when several
ceilings are reached by one history is deterministic: total, then repeat, then no-progress.

## Evidence model

`RepairAttemptEvidence` is an immutable record of one **completed** attempt:

- `target` — the stable `RepairTarget` the attempt repaired;
- `proposal` — the `RepairProposalFingerprint` of the exact repair it tried;
- `outcome` — the closed `RepairAttemptOutcome` vocabulary member;
- `progress` — an optional explicit `RepairProgressMarker`.

There is deliberately **no** metadata bag, correlation ID, free-text error, retry instruction,
timestamp, UUID, authority, status, or escalation field. Order is the caller-supplied tuple
position — the record structurally cannot represent "same content, newer time", so
timestamp-only and fresh-UUID games are impossible by construction. Fingerprints and markers
use a narrow opaque token grammar; this module never hashes, embeds, fuzzy-matches, or
otherwise interprets patch content — the caller computes any content digest before calling.

Historical outcome is DATA: a `FAILED_*` member records that a past attempt failed (it does
not trigger, schedule, or justify another attempt), and `VALIDATED` records that a past
attempt passed validation (it does not apply, approve, or verify anything now).

## Outcome vocabulary

The closed vocabulary names the observed endpoints of the repair lifecycle the C4.01–C4.04
contracts already anticipate (C4.05 patch generation, C4.06 validation, C4.07 shadow repair,
C4.08 replacement/rollback):

| Member | Meaning | Counts as progress? |
| --- | --- | --- |
| `VALIDATED` | the attempt's result passed its explicit validation | yes — it breaks the no-progress run |
| `FAILED_VALIDATION` | the proposed repair failed validation (C4.06) | no |
| `FAILED_SHADOW` | the proposed repair failed shadow execution (C4.07) | no |
| `FAILED_APPLICATION` | applying/replacing the target failed (C4.08) | no |
| `ABORTED` | the attempt ended before reaching any endpoint | no — demonstrates nothing (fail-closed) |
| `UNKNOWN` | the recorded outcome is not representable | no — demonstrates nothing (fail-closed) |

Unrecognized outcome strings are rejected, never coerced to `UNKNOWN`.

## Progress semantics

Progress is recognized only from explicit typed evidence, never inferred from wording,
patch length, model confidence, changed timestamps, new UUIDs, or exception-message
changes:

- only the **first occurrence** of a progress marker (for that target) demonstrates
  progress; replaying an old marker resets nothing;
- a `VALIDATED` outcome also demonstrates progress (it is the canonical completed-success
  signal), but it never resets the total or per-proposal ceilings;
- markers are scoped per target: target A's progress is never target B's progress.

## Target scoping

Evidence from target A never consumes target B's repetition or no-progress budget:

- procedure targets are identified by exactly the C2.03 record identity — canonical
  `ProcedureId` **plus exact revision** — so the same procedure at revision 2 and revision 3
  are always distinct targets;
- a procedure target never equals an opaque target, and opaque fingerprint equality is exact;
- callers needing finer-than-revision scoping (a single procedure node) represent it
  explicitly with an opaque target fingerprint — the contract never infers it;
- the total ceiling counts only the assessed target's evidence under `TARGET` scope; the
  `SESSION` scope (counting all supplied history) is an explicit opt-in.

## Determinism and fail-closed behavior

`assess_repair_attempt` is pure, stateless, and total over history validity: no clock,
randomness, database, filesystem, network, model, subprocess, thread, or sleep. The same
inputs always produce the same decision. Structurally invalid history — not a tuple, or any
item not a `RepairAttemptEvidence` — yields the typed `INVALID_HISTORY` decision with zero
counters, never an allowance. Malformed request objects (`target`, `proposal`, `limits` of
the wrong type) raise immediately, matching every other core construction boundary.

## Authority boundary

The module grants no `Permission`, constructs no `AuthorityContext`, invokes no `ActionGate`,
changes no `RiskLevel`, consumes no `ResourceBudget`, clears no `EmergencyStop`, transitions
no `Task`, activates no `Procedure`, publishes no events or audit records, mutates no Hive
state, and touches no store. Hostile strings in fingerprints, markers, or outcomes remain
inert data — they can only ever be exact-match identity tokens, and free text is rejected by
the token grammar. `STOP_*` decisions escalate nothing and suppress nothing: they are
terminal data for the caller.

## Non-goals

Not owned here: the repair patch contract, repair generation, repair validation, shadow
execution, rollback, procedure replacement, degradation detection, the generic cognition
anti-loop, kernel resource accounting, repair persistence, and any migration. The executor
that will one day consume this policy is a later, explicitly reviewed task — nothing in the
runtime imports this module yet.

## Testing

- `tests/unit/test_repair_budget.py` — limits, evidence, decisions, scoping, progress,
  determinism, immutability, order behavior, and fail-closed history handling.
- `tests/adversarial/test_repair_budget_authority.py` — hostile instruction tokens, duplicate
  flooding, UUID churn, cross-target pollution, progress replay, kernel-budget
  non-consumption, and authority-subsystem isolation.
- `tests/architecture/test_repair_budget_placement.py` — static proofs: stdlib + `agentx.core`
  imports only, no second resource-budget system, no anti-loop replacement, no I/O, no
  dynamic execution, no persistence, no global mutable state, no runtime wiring.
