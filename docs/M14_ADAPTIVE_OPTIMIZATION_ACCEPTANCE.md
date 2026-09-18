# M14 — Adaptive Strategy Optimization and Specialized Models

Task range: **AX-516–AX-540**.

Implementation baseline: `446771fab52278b4f12fa752fb000bd4668a5241`.
Campaign PR: **#176**.

## Acceptance boundary

M14 optimizes **which already-permitted strategy is selected**. It does not alter
what AgentX is permitted to do. The canonical A2.07 router still establishes the
cheapest execution level justified by typed routing evidence; the optional M14
router may select only that level or a more open-ended level explicitly exposed
by the composition root. Every selected strategy still executes through the
unchanged AgentLoop, ActionGate, resource budget, emergency-stop, confirmation,
and independent verification paths.

Adaptive and specialist artifacts are inert data. They do not import the Trusted
Kernel, do not own permissions/risk/budgets, and do not use pickle, eval, exec,
dynamic imports, subprocess execution, or executable learned artifacts.

## Production components

- `src/agentx/adaptive_optimization.py`
  - deterministic strategy baseline;
  - durable strategy-outcome history over the canonical EventJournal;
  - per-environment and per-task-family statistics;
  - latency, cost, model-call, attempt, and independently verified success statistics;
  - confidence calibration using verified outcomes only;
  - seeded bounded contextual-bandit experiment;
  - chronological offline policy evaluation with explicit logged-policy support;
  - explicit user-correction dataset and transparent preference scoring/ranking;
  - AgentLoop-compatible adaptive router constrained by canonical A2.07 evidence;
  - append-only candidate/promotion/rollback policy lifecycle with compare-and-swap checks;
  - restart-safe runtime fallback to deterministic baseline on missing/corrupt policy state.

- `src/agentx/specialized_models.py`
  - provenance-aware grounding and routing datasets;
  - deterministic dataset identities;
  - lightweight transparent grounding/routing specialists with abstention;
  - safe bounded JSON model serialization/loading;
  - verifier-assistance experiment that records false positives and is never final authority;
  - provenance-aware distillation lineage;
  - exact-count specialist benchmark with truthful improved/same/regressed/insufficient outcomes.

## Evidence integrity

Strategy success is never inferred from API return or self-report. A successful
sample requires canonical `VERIFIED_SUCCESS` plus `verification_passed=True`.
A failed sample is counted as verified failure only when canonical failure
evidence carries `verification_passed=False`. Unverified evidence remains
unverified and is excluded from success-rate/calibration truth.

Missing latency and provider cost remain `None`. Mixed cost units are not
summed. Unknown environment/task family stays an explicit unknown grouping.
Stale samples can be excluded by a caller-provided age bound. Dataset builders
reject duplicate identities and unverified routing labels.

## Reproducible experiments

Controlled tests use fixed timestamps, deterministic UUIDs/configuration, and
seeded strategy selection. Offline policy evaluation is chronological: only
earlier samples are available as history when a later logged sample is
evaluated. Candidate and deterministic baseline are compared only where the
logged action supports an outcome for the selected strategy.

The controlled M14 acceptance corpus deliberately makes L1 fail and L4 succeed,
so the bandit experiment has logged support for both strategies and produces a
measured improvement over the deterministic fixed-priority baseline. This is a
**controlled acceptance result**, not a claim of production superiority or
statistical significance.

A second controlled degradation corpus is explicitly allowed to produce
REGRESSED, SAME, or INSUFFICIENT_EVIDENCE and never auto-promotes a candidate.
The active candidate is then rolled back to the persisted known-good baseline
and verified after reopening the SQLite-backed EventJournal.

## Preference evidence

Only canonical `UserPreference` records with
`PreferenceSource.USER_CORRECTION` and
`PreferenceKey.WORKFLOW_PREFERENCE` enter the correction dataset. Ordinary
explicit preferences, model output, documents, web text, or inferred behavior
are not relabeled as corrections. Preference affects ranking only; it never
authorizes an action.

Current acceptance uses deterministic controlled correction records. No claim is
made that a statistically meaningful real-user preference corpus is present.

## Specialized-model evidence

Grounding labels require explicit provenance supplied by the caller. Routing
training examples require canonical verified strategy outcomes. Specialists
return advisory predictions and abstain on unknown/low-evidence inputs.

The verifier-assistance experiment measures true/false positives and negatives
against independent truth and records `sole_authority=False`. Learned output
therefore cannot replace canonical postcondition/task verification.

Distillation records teacher identity, dataset identity, training configuration
identity, student model identity, and evaluation reference. It never promotes or
silently replaces a production model.

## Persistence, concurrency, and failure behavior

Acceptance covers:

- actual SQLite/EventJournal close-and-reopen semantics for outcome history and policies;
- concurrent independent strategy-outcome writers;
- stale compare-and-swap policy promotion rejection;
- missing/invalid active policy fallback;
- corrupt persisted policy event fallback to the deterministic baseline;
- candidate promotion, restart, explicit rollback, and restart after rollback.

Policy/runtime state contains only strategy identities and artifact references;
it never persists live authority, confirmation, executable objects, or kernel
policy.

## Security regression

`tests/adversarial/test_m14_optimization_authority.py` proves:

- M14 production modules contain no Trusted-Kernel imports or dynamic execution escape hatch;
- hostile optimizer output cannot select a cheaper strategy than canonical
  routing evidence permits;
- adaptive AgentLoop routing with insufficient WRITE authority remains denied by
  the existing governed execution path.

M9's data-not-authority rule therefore remains intact for optimization inputs.

## Tests

Dedicated M14 evidence:

- `tests/integration/test_m14_adaptive_optimization.py`
- `tests/unit/test_m14_specialized_models.py`
- `tests/adversarial/test_m14_optimization_authority.py`
- `tests/integration/test_m14_acceptance.py`

Canonical exact-head C1.01 additionally runs Ruff, Ruff format check, mypy,
task-ledger validation, Windows-host acceptance, real headless-Chrome M7
acceptance, and the full pytest suite.

## Real-data / real-provider boundary

M14 has no new external runtime dependency and no live model/provider is required
by the canonical M14 task titles or roadmap exit criteria. Nevertheless:

- **REAL_MODEL_EVIDENCE:** not claimed by this M14 campaign;
- **REAL_USER_DATA_EVIDENCE:** not claimed; controlled explicit corrections prove
  the pipeline and semantics only;
- **REAL_COST_EVIDENCE:** not claimed; real cost remains unknown unless canonical
  provider usage supplies it;
- **STATISTICAL_PRODUCTION_IMPROVEMENT:** not claimed.

These are limitations of the evidence population, not unimplemented M14
production paths.

## Task acceptance

| Task | Requirement | Evidence |
| --- | --- | --- |
| AX-516 | strategy-performance evidence foundation | existing canonical evidence foundation revalidated |
| AX-517 | execution-level metrics foundation | existing canonical metrics foundation revalidated |
| AX-518 | deterministic strategy baseline | fixed non-learning baseline + repeatability test |
| AX-519 | strategy outcome history | EventJournal persistence/restart/concurrency |
| AX-520 | per-environment strategy statistics | opaque environment/revision grouped statistics |
| AX-521 | per-task-family strategy statistics | bounded task-family grouping + unknown fallback |
| AX-522 | strategy latency statistics | exact elapsed aggregation; missing remains unknown |
| AX-523 | strategy cost statistics | exact Decimal/unit accounting; missing/mixed units unknown |
| AX-524 | strategy success statistics | canonical verification-derived success/failure only |
| AX-525 | confidence calibration | minimum-sample gate + Brier score over verified outcomes |
| AX-526 | contextual-bandit experiment | fixed-seed bounded controlled experiment |
| AX-527 | bandit safety constraints | permitted-strategy intersection + AgentLoop/kernel regression |
| AX-528 | offline policy evaluation | chronological train/evaluation separation + support counts |
| AX-529 | online preference-ranking experiment | prequential explicit-correction ranking experiment |
| AX-530 | explicit user-correction dataset | USER_CORRECTION-only versioned dataset |
| AX-531 | preference scoring model | transparent correction-count model + safe fallback |
| AX-532 | grounding-specialist dataset | versioned provenance-aware verified-label dataset |
| AX-533 | lightweight grounding model | deterministic token specialist + JSON artifact + abstention |
| AX-534 | routing-specialist dataset | verified execution evidence + provenance |
| AX-535 | lightweight routing model | bounded allowed-strategy prediction + abstention |
| AX-536 | verifier-assistance model experiment | confusion counts; advisory-only false-positive evidence |
| AX-537 | model-distillation pipeline | teacher/dataset/config/student/evaluation lineage |
| AX-538 | specialized-model benchmark | exact-count baseline comparison incl. regression case |
| AX-539 | rollbackable optimization policy | staged/promoted/rollback EventJournal lifecycle |
| AX-540 | optimization milestone acceptance | controlled end-to-end history→experiment→policy→rollback and specialist lifecycle |
