# Varied-parameter procedure validation runner (N2.09)

N2.09 owns the canonical execution-side **runner** that evaluates one explicit
procedure candidate revision against an explicit, bounded, caller-supplied
collection of validation cases and produces canonical validation **evidence**:
`agentx.procedure_validation_runner`.

It is the controlled runner **around** the canonical M4.02 validation policy,
which already landed. This task redesigns nothing that M4.02 owns:

| Concern | Owner |
| --- | --- |
| Promotion-eligibility evidence policy (`ValidationPolicy`) | M4.02 `agentx.procedure_validation` |
| Procedure record contract (`ProcedureId`, `revision`, `ProcedureStatus`) | C2.03 `agentx.core.procedures` |
| Lifecycle / status transitions | C3.09 via the canonical `ProcedureStore.update_status` |
| Canonical execution + verification evidence (`ClosedLoopOutcome`) | A1.10 `agentx.capabilities.runtime` |
| Canonical verification requirements (`VerificationRequirement`) | A2.05 `agentx.capabilities.verifier` |
| **Varied-parameter validation RUNNER (this task)** | **N2.09 `ProcedureValidationRunner`** |

## Why this task exists

The failure mode it exists to prevent is:

```
"the procedure worked once"  =>  "the procedure is a validated reusable skill"
```

A runner over one case cannot tell a genuinely reusable procedure from a
procedure that happens to match one trajectory. The runner therefore treats the
**case set** as the unit of validation:

- every case is explicit, bounded, and caller-supplied — no fuzzing, no
  model-generated cases, no random parameter mutation;
- every case carries its own parameter binding, so cases are expected to
  differ, and that difference is preserved case by case;
- a procedure that passes case A and fails case B is reported as a **partial**
  result, never as universal validation.

## Composition

```
ProcedureCandidateIdentity | ProcedureRecord     (canonical, C2.03)
  + tuple[ProcedureValidationCase]               (bounded, explicit, varied)
  + ProcedureValidationHarness                   (injected execution port)
  + optional VerificationRequirement per case    (A2.05)
      -> per-case ProcedureValidationCaseResult  (caller order)
      -> ValidationRunEvidence -> ValidationPolicy (M4.02) -> ValidationReport
      -> ProcedureValidationRunResult            (immutable, deterministic)
```

The runner computes nothing about eligibility that M4.02 does not own. It builds
the canonical `ValidationRunEvidence` records, hands them to the canonical
`ValidationPolicy`, and composes around the report that comes back.

## Validation case model

`ProcedureValidationCase` is the minimum data needed to run and verify the
candidate under one controlled variation:

| Field | Meaning |
| --- | --- |
| `case_id` | Opaque, bounded case identity. Duplicates are rejected; ordering and reporting key on it. Inert. |
| `run_id` | Canonical `TaskId` identifying the governed unit of work the case produces. Supplied explicitly (never generated) and unique per run, so replay cannot inflate evidence. |
| `parameter_binding` | The varied parameters, in the canonical JSON-compatible value space. This is the controlled variation. |
| `environment` | Optional opaque environment label. Never interpreted. |
| `verification` | Optional canonical A2.05 `VerificationRequirement` for this case. `None` relies on the closed-loop verification evidence alone. |

A case carries no procedure graph, no status, no permission, no risk, and no
expected-outcome flag: **a case cannot declare itself passed**.

## Variation semantics

- Cases are executed exactly once each, in caller-supplied order, and results
  are returned in that same order.
- The runner's default policy requires **two distinct parameter bindings**
  (`DEFAULT_MIN_DISTINCT_PARAMETER_BINDINGS = 2`) in addition to M4.02's own
  "at least two verified successes" floor. An exact repeat is reported as
  `INSUFFICIENT_EVIDENCE` with `EXACT_REPEAT_ONLY`. Callers may pass their own
  `ValidationPolicy`; nobody can lower M4.02's floors.
- Bounds are enforced before anything executes: at least one case, at most
  `max_cases` (default 32, hard ceiling 1 000), no duplicate case identity, and
  no duplicate run identity. An empty case collection is **rejected**, never
  treated as evidence.

## Verification truth boundary

Execution return is not validation success. Procedure END is not validation
success. Capability execution success is not validation success.

- the injected harness must return a canonical `ClosedLoopOutcome`;
- when a case declares a `VerificationRequirement`, the runner evaluates it with
  the canonical A2.05 `Verifier` **over the produced outcome**. The Verifier
  reads evidence; it manufactures nothing. An unsatisfied requirement is an
  explicit verification failure even when the outcome's own text claims success;
- the per-case success classification itself is M4.02's, never this module's.

Data inside a case is inert. `verified=true`, `task_success=true`,
`validation_passed=true`, `permission=ADMIN`, and `risk=R0` inside a parameter
binding, an observation, a case id, or an environment label change nothing.

## Failure and partial-result semantics

Per-case results preserve every outcome:

| Situation | Per-case kind |
| --- | --- |
| canonically verified success | `VERIFIED_SUCCESS` (the only success) |
| verification did not pass | `VERIFICATION_FAILURE` |
| execution failed / harness returned `Result.failure` / harness raised | `EXECUTION_FAILURE` |
| governed path denied the run | `DENIED` |
| timeout or cancellation | `TIMEOUT_OR_CANCEL` |
| verification truth disagrees with itself | `INCONSISTENT` |
| non-canonical or lookalike evidence | `FORGED` |

Failing cases are never erased, never averaged into success, and never
reordered into invisibility. A run in which any case failed to produce canonical
evidence is reported with `complete=False` and an explicit unmet requirement.

## Aggregate decision (never upgrades M4.02)

`ProcedureValidationRunResult.decision` is composed fail-closed:

1. forged (non-canonical) case evidence -> `DEGRADED`;
2. any blocking per-case failure -> `REJECTED`;
3. a complete run whose M4.02 decision is eligible -> `ELIGIBLE_FOR_PROMOTION`;
4. otherwise the M4.02 decision, unchanged.

The runner may only ever **downgrade** the policy's decision.

## NO PROMOTION AUTHORITY

The runner produces evidence only. It does not and cannot:

- activate a Procedure or change a `ProcedureStatus`;
- mutate a `ProcedureStore` lifecycle state;
- grant a `Permission`, widen authority, or approve an `ActionGate` decision;
- downgrade a `RiskLevel`, enlarge a `ResourceBudget`, or clear an
  `EmergencyStop`;
- transition a `Task`, execute a capability itself, call a model, perform
  research, or touch persistence.

Even a perfect result — every case a canonically verified success and M4.02
returning `ELIGIBLE_FOR_PROMOTION` — is evidence, not activation. The module
imports no kernel, infrastructure, hive, cognition, learning, or procedures
module and holds no store reference, so it is structurally incapable of
persisting, mutating, promoting, or executing anything.

The only external effect of a run is whatever the **injected harness** does.
When the harness is the canonical A1.10 `CapabilityExecutionLoop`, execution
evidence is recorded by that canonical machinery, through the canonical kernel,
exactly as it would be without this runner.

## Determinism

- no clock reads and no generated identities: run identities are supplied, and
  evidence records carry no timestamp;
- no randomness, no environment inspection, no dynamic loading, no threads, no
  module-level side effects;
- identical inputs (candidate, cases, harness results, policy) produce equal
  results.

## Using it

```python
runner = ProcedureValidationRunner(harness=my_governed_harness)
result = runner.run(candidate_record, cases)

for case in result.cases:  # caller-supplied order
    print(case.case_id, case.kind, case.passed)

result.decision  # the authoritative aggregate decision
result.report  # the canonical M4.02 report over the fed evidence
result.complete  # every case produced canonical evidence
```

`my_governed_harness` implements `ProcedureValidationHarness.run(request) ->
Result[ClosedLoopOutcome, AgentXError]` and is expected to delegate to the
canonical A1.10 `CapabilityExecutionLoop`.

## Guarantees tested

- one valid case (insufficient), multiple varied valid cases (eligible), exact
  repeat (insufficient);
- one of several cases fails verification; execution failure; denial;
  timeout/cancellation; forged evidence; hostile parameter and observation text;
- malformed case, duplicate case identity, duplicate run identity, empty
  validation set, finite maximum-case bound;
- exact procedure revision binding and deterministic result ordering;
- execution return without verification cannot pass; procedure END alone cannot
  pass;
- no Procedure activation, no `ProcedureStatus` mutation, no permission grant,
  no `ActionGate` bypass, no risk reduction, no budget widening, no
  `EmergencyStop` clearing, no model call, no research, no persistence mutation.
