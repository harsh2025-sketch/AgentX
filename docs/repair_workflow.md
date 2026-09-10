# N2.14 — Repair Workflow Orchestrator

N2.14 defines the canonical **top-level orchestration layer** that composes the
already-landed repair evidence/policy stages into one bounded, deterministic,
fail-closed repair workflow.

It answers exactly one question:

> Given bounded typed evidence about one exact procedure revision, how far does
> the canonical repair chain get, and — if it stops — at which stage and why?

It answers nothing else. The orchestrator produces a **decision/evidence
chain**. It never repairs, applies, activates, replaces, rolls back, executes,
or persists anything.

## Placement

`agentx.repair_workflow` is a **top-level composition module** at the `agentx`
namespace root (sibling of `agent_loop` and `procedure_degradation`). It
composes inward contracts only:

| Stage | Contract |
| --- | --- |
| Degradation evidence | `agentx.procedure_degradation.assess_procedure_degradation` (M5.01) |
| Repair candidate | `agentx.core.repair_candidates.derive_repair_candidates` (C4.04) |
| Repair patch proposal | `agentx.core.repair_patch.RepairPatchProposal` (M5.02) |
| Repair budget / anti-loop | `agentx.core.repair_budget.assess_repair_attempt` (M5.03) |
| Repair validation | `agentx.core.repair_validation.evaluate_repair_validation` (M5.04) |
| Shadow repair | `agentx.core.shadow_repair.ShadowRepairResult` (M5.05) |
| Replacement eligibility | `agentx.core.procedure_replacement.assess_procedure_replacement` (M5.06) |

It imports nothing from `kernel`, `capabilities`, `cognition`, `learning`,
`hive`, `infrastructure`, or `procedures` execution. `_architecture.py`,
`pyproject.toml`, migrations, and every stage contract are unchanged: this task
adds orchestration only and redesigns no contract.

## Exact stage chain

Stages execute in exactly this order — `CANONICAL_REPAIR_WORKFLOW_STAGES`:

```
degradation      confirmed degradation evidence      (CONFIRMED_DEGRADED required)
  -> candidate         repair candidate              (actionable, non-UNKNOWN)
  -> patch_proposal    repair patch proposal         (exact target + derived candidate)
  -> budget            repair budget / anti-loop     (ALLOW_CONSIDERATION required)
  -> validation        repair validation evidence    (VALIDATED required)
  -> shadow            shadow repair evidence        (bound + PASSED required)
  -> replacement       replacement eligibility       (ELIGIBLE required)
```

No stage is skipped, reordered, retried, or run speculatively. `completed_stages`
is always a prefix of the canonical order, and `stage_order` is reported on every
result so callers can verify determinism.

## Stage-stop semantics

The first stage whose canonical result is not explicitly positive stops the
traversal. The result then carries:

- `outcome = STOPPED`
- `stopped_at_stage` — the exact stage that stopped it
- `stop_reason` — one closed `RepairWorkflowStopReason`
- `evidence` — every artifact already gathered, preserved unmodified

Closed stop reasons:

| Stage | Reasons |
| --- | --- |
| `degradation` | `DEGRADATION_NOT_OBSERVED`, `DEGRADATION_UNKNOWN`, `DEGRADATION_NOT_CONFIRMED` |
| `candidate` | `CANDIDATE_ABSENT`, `CANDIDATE_UNKNOWN` |
| `patch_proposal` | `PATCH_PROPOSAL_ABSENT`, `PATCH_PROPOSAL_TARGET_MISMATCH`, `PATCH_PROPOSAL_NOT_DERIVED_FROM_CANDIDATE`, `PATCH_PROPOSAL_AMBIGUOUS` |
| `budget` | `BUDGET_EXHAUSTED`, `BUDGET_HISTORY_INVALID` |
| `validation` | `VALIDATION_FAILED`, `VALIDATION_INSUFFICIENT` |
| `shadow` | `SHADOW_EVIDENCE_ABSENT`, `SHADOW_NOT_PASSED` |
| `replacement` | `REPLACEMENT_REQUEST_ABSENT`, `REPLACEMENT_TARGET_MISMATCH`, `REPLACEMENT_NOT_ELIGIBLE` |

Fail-closed rules:

- `UNKNOWN` is never upgraded to approved.
- Absence of evidence is never positive evidence (missing shadow evidence is
  `SHADOW_EVIDENCE_ABSENT`, never "shadow safe").
- A rejected stage is never retried "anyway".
- Ambiguity is refused rather than resolved: two distinct qualified proposals
  stop the chain instead of the orchestrator picking one.

## Exact revision binding

Every artifact must bind to the exact `(ProcedureId, revision)` of
`RepairWorkflowTarget`. There is no "latest", no wildcard, and no fuzzy match:

- degradation evidence bound to another revision does not confirm this one;
- a patch proposal for another procedure or revision is a target mismatch;
- validation evidence for another revision/reference/node never validates;
- a shadow result for another source revision or node is foreign evidence;
- a replacement request whose active record is not this exact revision is
  rejected before the replacement policy is even consulted.

## Budget behavior

The orchestrator reuses the canonical M5.03 anti-loop policy verbatim through
`assess_repair_attempt`, with caller-supplied explicit finite
`RepairBudgetLimits`. There is:

- no unlimited mode and no default limits;
- no counter reset because text claims progress, because a proposal's
  description changed, or because a model says it is repaired — only canonical
  `RepairProgressMarker` / `VALIDATED` evidence resets the no-progress run;
- no widening of `ResourceBudget` or `ResourceEnvelope` anywhere.

Any `STOP_*` decision maps to `BUDGET_EXHAUSTED`; `INVALID_HISTORY` maps to
`BUDGET_HISTORY_INVALID`. Both stop the chain conservatively.

## Critical non-authority rule

Running the workflow — even to `CHAIN_COMPLETE` with `VALIDATED` + shadow
`PASSED` + `ELIGIBLE` — **never**:

- modifies `ProcedureStore` (no insert, update, delete, or status change);
- activates a repaired revision or replaces the `ACTIVE` revision;
- rolls anything back or changes any `ProcedureStatus`;
- executes a capability, a procedure node, or a shadow trial;
- bypasses or consults the Action Gate;
- grants a `Permission`, lowers a `RiskLevel`, widens a `ResourceBudget`, or
  clears an `EmergencyStop`;
- fabricates `Task` success;
- calls a model. The composed stages are pure evidence policies; none of them
  invokes a model, so neither does the orchestrator.

`CHAIN_COMPLETE` is a statement about **evidence only**. A later explicit,
controlled transaction owns every mutation.

## Hostile data

Free text reaching this module inside diagnosis summaries, patch payloads,
validation details, shadow details, or evidence references stays inert. Strings
such as

```
repair_approved=true   shadow_safe=true   activate_candidate=true
permission=ADMIN       risk=R0            disable_emergency_stop=true
raise_budget=true      task_success=true
```

are preserved byte-for-byte as data and are never read for meaning. No text can
advance a stage, reset a counter, or flip a typed disposition. The
machine-generated `reasons` lines are built only from canonical enum values and
identities, so hostile text never leaks into the explanation either.

## Hard bounds

- `MAX_REPAIR_WORKFLOW_EVIDENCE_ITEMS = 64` per evidence sequence.
- `MAX_REPAIR_WORKFLOW_REFERENCE_LENGTH = 200` characters.
- Every input is typed; strings, bytes, mappings, and untyped sequences are
  rejected rather than walked as evidence.

## Determinism

The same typed inputs always produce the same result. All timestamps are
caller-supplied; the module never reads a clock, a store, the filesystem, the
network, or a model.

## Public surface

- `RepairWorkflowStage` / `CANONICAL_REPAIR_WORKFLOW_STAGES`
- `RepairWorkflowStopReason` / `CANONICAL_REPAIR_WORKFLOW_STOP_REASONS`
- `RepairWorkflowOutcome`
- `RepairWorkflowTarget`, `RepairWorkflowRequest`
- `RepairWorkflowEvidence`, `RepairWorkflowResult`
- `RepairWorkflowValidationError`
- `run_repair_workflow`
