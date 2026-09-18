# M5 failure-driven self-repair acceptance

This document records the strict task-level audit for Milestone M5 on the focused
closure PR. The audit started from canonical main
`294cf9029e63d91b476971db79133e09264f2087`.

## Result

- Canonical M5 range: AX-206 through AX-245 (40 tasks).
- Historical reported baseline: 36 COMPLETE, 0 PARTIAL, 4 NOT_IMPLEMENTED.
- Historical fields are protected and intentionally remain unchanged.
- Acceptance audit after implementation: 40 VERIFIED.
- Acceptance implementation commit: `2f56a6bd5821c35ebf52c149e0ebddb8b3727f8d`.
- Windows C1.01 run: 35310763170 (#771), success.
- Full suite on that commit: 9969 passed, 6 platform-semantic skips.
- Ruff lint: passed.
- Ruff format check: passed.
- strict mypy: passed.

## Successful repair acceptance

`tests/integration/test_m5_failure_driven_repair_acceptance.py` proves a real lifecycle using production components:

1. persist a known-good ACTIVE filesystem Procedure v1;
2. execute it through L2 Procedure Runtime -> Executor -> ActionGate and independently verify it;
3. physically rename its parent directory so the stored path becomes stale;
4. execute v1 again and observe real governed filesystem failure evidence;
5. classify, node-localize and diagnose using typed evidence;
6. require repeated strong evidence before confirmed degradation;
7. derive a bounded repair candidate and replace only the implicated node definition;
8. materialize v2 as CANDIDATE without mutating v1;
9. execute three varied shadow cases through ProcedureInterpreter -> Executor -> ActionGate;
10. require canonical independent verification for every shadow case;
11. run the evidence-only repair workflow and replacement eligibility policy;
12. atomically retire v1 and activate v2;
13. reopen the SQLite-backed store and select exactly one ACTIVE v2;
14. reuse v2 through the governed L2 runtime and independently verify success;
15. preserve v1 as RETIRED history.

## Failed repair and rollback acceptance

The same acceptance module also proves both safety paths.

A deliberately incorrect candidate targets a parent directory that does not
exist. Real shadow execution fails on every varied case, replacement eligibility
is insufficient, v1 remains the sole ACTIVE revision, restart selection still
returns v1, and verified reuse succeeds after the known-good environment is
restored.

For post-promotion rollback, the successful v2 environment is later reverted.
Two real v2 failures confirm degradation; canonical rollback eligibility is
evaluated; the rollback API creates a new contiguous revision v3 carrying the
known-good v1 payload rather than reactivating a RETIRED row. History becomes
v1 RETIRED, v2 RETIRED, v3 ACTIVE, exactly one revision is ACTIVE after restart,
and v3 is reused and independently verified.

## Governance and security audit

Existing focused M5 suites remain part of the acceptance evidence:

- hostile strings cannot fabricate failure taxonomy, localization, diagnosis,
  validation or replacement authority;
- repair proposals cannot grant Permission, lower risk, widen budget, clear
  EmergencyStop, claim verification, mutate ACTIVE state, or invoke dynamic code;
- repair budgets terminate total-attempt, repeated-proposal and no-progress loops;
- shadow evidence is exact source/candidate bound and non-committing;
- replacement and rollback use one serialized activation seam with exactly-one-ACTIVE
  postconditions;
- stale/concurrent lifecycle requests fail closed;
- injected failure after retire-before-activate and postcondition failure roll back
  the entire transaction;
- RETIRED rows remain terminal; rollback copies known-good behavior into a new
  revision rather than resurrecting history.

No M4 learning-efficiency claim, M6 desktop-host claim, or M16 production crash
recovery claim is made here.

## All M5 tasks

| AX ID | Requirement | Historical before | Acceptance after | Implementation evidence | Test evidence | Acceptance evidence |
| --- | --- | --- | --- | --- | --- | --- |
| AX-206 | failure taxonomy | COMPLETE | VERIFIED | `src/agentx/core/failure_taxonomy.py` | `tests/unit/test_failure_taxonomy.py; tests/adversarial/test_failure_taxonomy_authority.py` | `tests/unit/test_failure_taxonomy.py` |
| AX-207 | transient-failure classification | COMPLETE | VERIFIED | `src/agentx/core/failure_taxonomy.py` | `tests/unit/test_failure_taxonomy.py; tests/adversarial/test_failure_taxonomy_authority.py` | `tests/unit/test_failure_taxonomy.py` |
| AX-208 | environment-unavailable classification | COMPLETE | VERIFIED | `src/agentx/core/failure_taxonomy.py` | `tests/unit/test_failure_taxonomy.py; tests/adversarial/test_failure_taxonomy_authority.py` | `tests/unit/test_failure_taxonomy.py` |
| AX-209 | precondition-failure classification | COMPLETE | VERIFIED | `src/agentx/core/failure_taxonomy.py` | `tests/unit/test_failure_taxonomy.py; tests/adversarial/test_failure_taxonomy_authority.py` | `tests/unit/test_failure_taxonomy.py` |
| AX-210 | capability-change classification | COMPLETE | VERIFIED | `src/agentx/core/failure_taxonomy.py` | `tests/unit/test_failure_taxonomy.py; tests/adversarial/test_failure_taxonomy_authority.py` | `tests/unit/test_failure_taxonomy.py` |
| AX-211 | UI/API-change classification | COMPLETE | VERIFIED | `src/agentx/core/failure_taxonomy.py` | `tests/unit/test_failure_taxonomy.py; tests/adversarial/test_failure_taxonomy_authority.py` | `tests/unit/test_failure_taxonomy.py` |
| AX-212 | verification-failure classification | COMPLETE | VERIFIED | `src/agentx/core/failure_taxonomy.py` | `tests/unit/test_failure_taxonomy.py; tests/adversarial/test_failure_taxonomy_authority.py` | `tests/unit/test_failure_taxonomy.py` |
| AX-213 | failure localization | COMPLETE | VERIFIED | `src/agentx/core/failure_localization.py` | `tests/unit/test_failure_localization.py; tests/adversarial/test_failure_localization_authority.py` | `tests/adversarial/test_failure_localization_authority.py` |
| AX-214 | failure diagnosis | COMPLETE | VERIFIED | `src/agentx/core/failure_diagnosis.py` | `tests/unit/test_failure_diagnosis.py; tests/adversarial/test_failure_diagnosis_authority.py` | `tests/adversarial/test_failure_diagnosis_authority.py` |
| AX-215 | repair-candidate contract | COMPLETE | VERIFIED | `src/agentx/core/repair_candidates.py` | `tests/unit/test_repair_candidates.py; tests/adversarial/test_repair_candidates_authority.py` | `tests/adversarial/test_repair_candidates_authority.py` |
| AX-216 | procedure degradation detector | COMPLETE | VERIFIED | `src/agentx/procedure_degradation.py` | `tests/unit/test_procedure_degradation.py; tests/integration/test_procedure_degradation_evidence.py; tests/adversarial/test_procedure_degradation_adversarial.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-217 | repair-budget policy | COMPLETE | VERIFIED | `src/agentx/core/repair_budget.py` | `tests/unit/test_repair_budget.py; tests/adversarial/test_repair_budget_authority.py` | `tests/adversarial/test_repair_budget_authority.py` |
| AX-218 | repair anti-loop | COMPLETE | VERIFIED | `src/agentx/core/repair_budget.py` | `tests/unit/test_repair_budget.py; tests/adversarial/test_repair_budget_authority.py` | `tests/adversarial/test_repair_budget_authority.py` |
| AX-219 | repair workflow orchestrator | COMPLETE | VERIFIED | `src/agentx/repair_workflow.py` | `tests/unit/test_repair_workflow.py; tests/integration/test_repair_workflow.py; tests/adversarial/test_repair_workflow_authority.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-220 | repair proposal generation boundary | COMPLETE | VERIFIED | `src/agentx/core/repair_candidates.py; src/agentx/core/repair_patch.py` | `tests/unit/test_repair_candidates.py; tests/unit/test_repair_patch.py` | `tests/adversarial/test_repair_patch_authority.py` |
| AX-221 | node-definition replacement patch | COMPLETE | VERIFIED | `src/agentx/core/repair_patch.py` | `tests/unit/test_repair_patch.py; tests/adversarial/test_repair_patch_authority.py` | `tests/adversarial/test_repair_patch_authority.py` |
| AX-222 | repair patch materializer | COMPLETE | VERIFIED | `src/agentx/repair_patch_materializer.py` | `tests/unit/test_repair_patch_materializer.py; tests/integration/test_repair_patch_materializer.py; tests/adversarial/test_repair_patch_materializer_authority.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-223 | immutable source preservation | COMPLETE | VERIFIED | `src/agentx/repair_patch_materializer.py` | `tests/unit/test_repair_patch_materializer.py; tests/integration/test_repair_patch_materializer.py; tests/adversarial/test_repair_patch_materializer_authority.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-224 | repaired candidate generation | COMPLETE | VERIFIED | `src/agentx/repair_patch_materializer.py` | `tests/unit/test_repair_patch_materializer.py; tests/integration/test_repair_patch_materializer.py; tests/adversarial/test_repair_patch_materializer_authority.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-225 | repair validation evidence | COMPLETE | VERIFIED | `src/agentx/core/repair_validation.py` | `tests/unit/test_repair_validation.py; tests/adversarial/test_repair_validation_authority.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-226 | shadow-repair evidence | COMPLETE | VERIFIED | `src/agentx/core/shadow_repair.py` | `tests/unit/test_shadow_repair.py; tests/adversarial/test_shadow_repair_authority.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-227 | shadow procedure runner | COMPLETE | VERIFIED | `src/agentx/shadow_procedure_runner.py` | `tests/integration/test_shadow_procedure_runner.py; tests/architecture/test_shadow_repair_placement.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-228 | source/candidate exact binding | COMPLETE | VERIFIED | `src/agentx/shadow_procedure_runner.py` | `tests/integration/test_shadow_procedure_runner.py; tests/architecture/test_shadow_repair_placement.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-229 | no-live-mutation shadow invariant | COMPLETE | VERIFIED | `src/agentx/shadow_procedure_runner.py` | `tests/integration/test_shadow_procedure_runner.py; tests/architecture/test_shadow_repair_placement.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-230 | procedure replacement eligibility | COMPLETE | VERIFIED | `src/agentx/core/procedure_replacement.py` | `tests/unit/test_procedure_replacement.py; tests/adversarial/test_procedure_replacement_authority.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-231 | replacement transaction | COMPLETE | VERIFIED | `src/agentx/procedure_replacement_transaction.py` | `tests/unit/test_procedure_replacement_transaction.py; tests/integration/test_procedure_replacement_transaction.py; tests/adversarial/test_procedure_replacement_transaction_authority.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-232 | serialized activation transaction | COMPLETE | VERIFIED | `src/agentx/infrastructure/procedure_activation.py; src/agentx/infrastructure/active_procedure_reader.py` | `tests/adversarial/test_procedure_activation_races.py; tests/unit/test_active_procedure_reuse.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-233 | stale-revision rejection | COMPLETE | VERIFIED | `src/agentx/procedure_replacement_transaction.py` | `tests/unit/test_procedure_replacement_transaction.py; tests/integration/test_procedure_replacement_transaction.py; tests/adversarial/test_procedure_replacement_transaction_authority.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-234 | exactly-one-ACTIVE invariant | COMPLETE | VERIFIED | `src/agentx/infrastructure/procedure_activation.py; src/agentx/infrastructure/active_procedure_reader.py` | `tests/adversarial/test_procedure_activation_races.py; tests/unit/test_active_procedure_reuse.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-235 | RETIRED terminal invariant | COMPLETE | VERIFIED | `src/agentx/infrastructure/procedure_activation.py; src/agentx/infrastructure/active_procedure_reader.py` | `tests/adversarial/test_procedure_activation_races.py; tests/unit/test_active_procedure_reuse.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-236 | rollback eligibility | COMPLETE | VERIFIED | `src/agentx/core/procedure_replacement.py` | `tests/unit/test_procedure_replacement.py; tests/adversarial/test_procedure_replacement_authority.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-237 | rollback transaction | COMPLETE | VERIFIED | `src/agentx/procedure_rollback.py` | `tests/unit/test_procedure_rollback.py; tests/integration/test_procedure_rollback.py; tests/adversarial/test_procedure_rollback_authority.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-238 | historical RETIRED-copy semantics | COMPLETE | VERIFIED | `src/agentx/procedure_rollback.py` | `tests/unit/test_procedure_rollback.py; tests/integration/test_procedure_rollback.py; tests/adversarial/test_procedure_rollback_authority.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-239 | rollback creates new revision | COMPLETE | VERIFIED | `src/agentx/procedure_rollback.py` | `tests/unit/test_procedure_rollback.py; tests/integration/test_procedure_rollback.py; tests/adversarial/test_procedure_rollback_authority.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-240 | replacement/rollback concurrency tests | COMPLETE | VERIFIED | `src/agentx/infrastructure/procedure_activation.py; src/agentx/procedure_replacement_transaction.py; src/agentx/procedure_rollback.py` | `tests/adversarial/test_procedure_activation_races.py` | `tests/adversarial/test_procedure_activation_races.py` |
| AX-241 | transaction fault-injection tests | COMPLETE | VERIFIED | `src/agentx/infrastructure/procedure_activation.py; src/agentx/procedure_replacement_transaction.py; src/agentx/procedure_rollback.py` | `tests/adversarial/test_procedure_activation_races.py` | `tests/adversarial/test_procedure_activation_races.py` |
| AX-242 | deliberately break real learned procedure | NOT_IMPLEMENTED | VERIFIED | `tests/integration/test_m5_failure_driven_repair_acceptance.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-243 | detect and localize real degradation | NOT_IMPLEMENTED | VERIFIED | `tests/integration/test_m5_failure_driven_repair_acceptance.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-244 | repair and revalidate real procedure | NOT_IMPLEMENTED | VERIFIED | `tests/integration/test_m5_failure_driven_repair_acceptance.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
| AX-245 | complete break -> repair -> rollback acceptance proof | NOT_IMPLEMENTED | VERIFIED | `tests/integration/test_m5_failure_driven_repair_acceptance.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` | `tests/integration/test_m5_failure_driven_repair_acceptance.py` |
