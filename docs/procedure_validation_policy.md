# Procedure candidate validation evidence policy (M4.02)

M4.02 owns the deterministic policy that decides whether an **already-existing
procedure candidate** has enough canonical validation evidence to be
**eligible for promotion**. It is a pure decision boundary:
`agentx.procedure_validation`.

**Eligible is not active.** The decision vocabulary (`ValidationDecision`)
contains exactly `INSUFFICIENT_EVIDENCE`, `ELIGIBLE_FOR_PROMOTION`,
`REJECTED`, and `DEGRADED`. `ELIGIBLE_FOR_PROMOTION` is **not**
`ProcedureStatus.ACTIVE`. The policy never builds, executes, persists, or
promotes a candidate; it returns an immutable `ValidationReport` and holds no
store reference, so it is structurally incapable of writing or mutating
anything.

## Position in the architecture

| Concern | Owner |
| --- | --- |
| Candidate lifecycle / status transitions (`ProcedureStatus`) | C3.09 via the canonical `ProcedureStore.update_status` |
| Procedure record contract (`ProcedureId`, `revision`, status vocabulary) | C2.03 `agentx.core.procedures` |
| Canonical execution/verification evidence (`ClosedLoopOutcome`) | A1.10 `agentx.capabilities.runtime` |
| **Promotion-eligibility evidence policy (this task)** | **M4.02 `ValidationPolicy`** |

Because the policy must read canonical procedure identity from `agentx.core`
and canonical execution/verification evidence from `agentx.capabilities`, it
lives at the `agentx` namespace root as a top-level composition module
(alongside `agent_loop`). The boundary manifest is not widened.

## Verification truth

A validation run counts as a canonical success only when three canonical
facts agree exactly:

1. `ClosedLoopOutcome.kind is LoopOutcome.VERIFIED`
2. `outcome.verification` is a canonical `VerificationResult` with
   `passed is True`
3. `outcome.task.status is TaskStatus.SUCCEEDED`

If any of the three disagree, the run is `INCONSISTENT` and the candidate is
rejected. No success is derived from "no exception", a procedure END node, a
provider return, `ExecutionResult.succeeded`, an action succeeding, a model
claim, or any historical text. Strings such as `"verified=true"` are inert.

## Decision rules (fixed order, fail-closed first)

1. **REJECTED** — a current-revision canonical failure (verification failure,
   execution failure, denial, timeout/cancel, environment mismatch) beyond
   `max_current_revision_failures` (default `0`), or inconsistent verification
   truth (always rejected; never tolerated).
2. **DEGRADED** — the evidence set is contaminated: forged/non-canonical
   records (e.g. a lookalike `VerificationResult`) or a conflicting duplicate
   run identity. Clean eligibility is withheld.
3. **ELIGIBLE_FOR_PROMOTION** — at least `min_verified_successes` distinct
   canonically verified successes **and** at least
   `min_distinct_parameter_bindings` distinct parameter bindings, with no
   blocking failure or contamination.
4. **INSUFFICIENT_EVIDENCE** — otherwise (including zero evidence).

Failures are never averaged away by successes: two successes plus one
current-revision failure is `REJECTED` under the default configuration.
Recovery requires an explicit, bounded `max_current_revision_failures` knob.

## Minimum promotion principle

The default requires **at least two distinct canonically verified successful
executions** before promotion eligibility. `min_verified_successes` is floored
at `2` and capped at `10_000`; no configuration, and no text, can lower it to
`0` or `1`. "Distinct" means distinct canonical validation-run identities
(`TaskId`): replaying the same run id twice counts once.

## Variation

Distinctness comes from the observed `parameter_binding`, never from
timestamps. The report records `distinct_parameter_bindings`,
`distinct_environments`, and `exact_repeat_only`, so it is always explicit
whether validation consisted only of exact repeats. When
`min_distinct_parameter_bindings > 1`, exact-repeat-only evidence remains
insufficient. Environment identity is optional and opaque: when
`allowed_environments` is configured, a run that *declares* an environment
outside the set is an environment mismatch; a run with no environment
identity is not presumed mismatched.

## Configuration

All knobs live on the frozen, validated `ValidationPolicy`:

| Field | Default | Bounds |
| --- | --- | --- |
| `min_verified_successes` | 2 | 2 .. 10 000 (hard floor) |
| `min_distinct_parameter_bindings` | 1 | 1 .. 10 000 |
| `max_validation_records_considered` | 10 000 | 1 .. 1 000 000 |
| `max_current_revision_failures` | 0 | 0 .. 10 000 |
| `allowed_environments` | `None` | `frozenset[str]`, ≤ 128 entries |

Validation fails closed: booleans, non-integers, non-finite floats, zero,
negative, and absurd values are rejected at construction.

## Decision report

`ValidationReport` is an immutable structured value — never a naked bool. It
carries:

- `candidate` identity/revision;
- `decision` (the enum, authoritative);
- `verified_successes` and the unique `counted_success_run_ids`;
- `distinct_parameter_bindings`, `distinct_environments`, `exact_repeat_only`;
- `failures` (canonical failures and inconsistent records);
- `invalid_evidence` (forged records and conflicting duplicate identities);
- `stale_revision_records`, `foreign_identity_records`, `replayed_duplicates`;
- `considered_record_count`, `truncated_record_count`;
- `requirements`, `unmet_requirements`, and `reasons`.

Reason codes never echo evidence content, so hostile text cannot leak through
the report.

## Explicit non-goals

- **No candidate building** — the candidate already exists; the policy only
  weighs its evidence.
- **No execution** — no capability is invoked; the policy reads
  `ClosedLoopOutcome` values only.
- **No persistence / mutation** — no `ProcedureStore`, `KnowledgeStore`,
  `EpisodeStore`, Hive, status change, or revision change.
- **No authority** — no `Permission`, `ActionGate` approval, risk downgrade,
  budget, `EmergencyStop` reset, or execution access. Promotion eligibility is
  learning evidence only; even a future ACTIVE procedure remains subject to
  the Trusted Kernel.
- **No model / research / Hive** — no cognition, learning, or hive imports.

## Guarantees tested

Zero/one/many successes, duplicate replay counting, conflicting duplicate
identity, every failure class, inconsistent verification truth, stale-revision
and foreign-identity scoping, exact-repeat versus variation, environment
mismatch, policy bounds, hostile text, forged lookalikes, replay flooding,
post-decision immutability, and no-dynamic-execution are covered in
`tests/unit/test_procedure_validation_policy.py`,
`tests/integration/test_procedure_validation_evidence.py`,
`tests/adversarial/test_procedure_validation_adversarial.py`, and
`tests/architecture/test_procedure_validation_boundaries.py`.
