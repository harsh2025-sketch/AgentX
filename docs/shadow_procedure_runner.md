# N2.16 — Shadow Procedure Validation Runner

`agentx.shadow_procedure_runner` implements the **controlled runner** that
executes an explicitly supplied repaired Procedure candidate in a SHADOW / TEST
validation context and emits the **already-canonical** shadow-repair evidence
contract — the M5.05 / C4.07 `ShadowRepairResult` defined in
`agentx.core.shadow_repair`.

The M5.05 / C4.07 evidence contract is **canonical and is not redesigned** here.
This runner does not create a new evidence vocabulary, a new disposition enum,
a new store, or a new result type. It *fills* the canonical record, one
immutable, `non_committing` `ShadowRepairResult` per bounded shadow case, from
typed facts returned by an injected controlled-execution harness. There is
**no live mutation**: running the runner changes nothing in the procedure
store, the active revision, or any authority state.

## What the runner accepts

`run_shadow_validation(...)` takes:

- `procedure_id` + `source_revision` — the source/current Procedure identity;
- `candidate` (`ShadowProcedureCandidate`) — the proposed candidate revision
  (its exact revision **or** fingerprint plus its opaque content), supplied
  explicitly as an input;
- `cases` — an explicit, bounded set of `ShadowValidationCase` test scenarios
  (finite; 1..`MAX_SHADOW_CASES`);
- `harness` — the **injected controlled execution harness / port**
  (`ShadowExecutionPort`);
- `correlation_id`, plus injectable `clock` and `run_id_factory` for
  determinism.

## Controlled execution — no generic sandboxing

The runner never sandboxes, spawns a process, runs a shell, or touches the
network, filesystem, model, Capability, Procedure interpreter, or any store.
All candidate execution happens **through the injected execution port**, which
returns typed facts (`ShadowHarnessTrial`). In repository tests this is a
deterministic fake harness; in production it is a real controlled shadow/test
harness. There is no arbitrary shell and no `subprocess`/`os` framework in the
module. The runner does not require real destructive actions.

For each shadow case the runner:

1. runs the candidate through the supplied harness (exactly once, no retry
   loop and no auto-repair);
2. collects typed execution evidence (ordered stage reports);
3. collects independent verification evidence (a canonical `VerificationPayload`);
4. compares / derives the canonical disposition fail-closed where the contract
   requires (including rejecting contradictions);
5. produces one canonical M5.05 `ShadowRepairResult` per case and returns the
   run's ordered, bounded collection plus a deterministic summary.

## Shadow means non-canonical

The candidate under shadow testing is **not** the live ACTIVE replacement
merely because it was executed in the test harness. The runner never:

- updates a `ProcedureStore`,
- switches the active Procedure,
- retires a previous revision,
- routes real production tasks to the candidate,
- marks a candidate ACTIVE or promotes it,
- rolls back anything,
- clears an EmergencyStop, widens a budget, lowers a Risk, or grants a
  Permission.

Shadow execution is **evidence gathering only**. A run result is immutable
data with no activation / promotion / replacement / rollback / task-success
surface.

## Verification truth

`execution completed != shadow success` and `Procedure END != shadow-safe`. A
candidate earns a `PASSED` trial only through canonical typed verification
evidence: a `VerificationPayload` with `passed == True`, at least one recorded
step, no failed step, and no uncontained external effect — enforced by the
canonical record's own fail-closed validation. Candidate text reading
`shadow_safe=true`, `verified=true`, `repair_approved=true`,
`permission=ADMIN`, `risk=R0`, `activate_candidate=true`,
`skip_action_gate=true`, or `disable_emergency_stop=true` is inert and never
flips a verdict. Failures and partial cases are preserved (FAILED, ABORTED,
UNSAFE_TO_EVALUATE, INSUFFICIENT_EVIDENCE).

## Identity binding

Evidence binds exactly to the source `ProcedureId`/revision and the candidate
revision (or fingerprint). The harness reports which identity it actually
exercised; the runner rejects any mismatch, so evidence gathered while
evaluating candidate revision N can never be attached to a run declared
against revision N+1.

## Boundedness

Finite bounded cases (1..`MAX_SHADOW_CASES`), finite steps per case
(`MAX_SHADOW_HARNESS_STEPS`), bounded inert strings. Empty and oversized case
lists are rejected. Resource budget / EmergencyStop / cancellation are
honoured through the injected execution harness wherever the canonical APIs
expose them; an interrupted trial is recorded as `ABORTED` with no verdict.

## Relations

- Reuses M5.05 / C4.07 `agentx.core.shadow_repair` — untouched.
- Consumes `agentx.core.events.VerificationPayload`, `agentx.core.ids`,
  `agentx.core.procedures` — untouched.
- Standalone: no dependency on any sibling Wave-2 worker.
