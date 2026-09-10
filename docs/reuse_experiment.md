# Cold-vs-warm verified reuse experiment harness (N2.12)

`agentx.reuse_experiment` is the deterministic **measurement/orchestration harness** for the
canonical AgentX reuse experiment. It runs or consumes two bounded execution phases through
injected ports and produces canonical comparative evidence using the landed M8.01 contracts
(`agentx.core.reuse_efficiency`). It is measurement only: it implements no caching, no
Procedure selection, no skill compilation, and no model providers.

## Experiment model

One experiment has exactly two phases, each executed by exactly one call to an injected
execution port (`PhaseRunner.run_phase`):

* **Phase A — COLD**: the measured task is completed by an execution path that does *not*
  claim reusable prior execution.
* **Phase B — WARM**: the measured task (or an explicitly related task) is completed by an
  execution path that claims explicit reuse evidence according to the canonical M8.01
  reuse-mode vocabulary.

A `ReuseExperimentSpec` declares what the experiment intends to measure: the experiment UUID,
the task each phase measures, optional canonical `TaskRelationshipEvidence` for cross-task
experiments, and an optional expected exact `ProcedureId` + revision binding for the warm
phase. The harness never decides which Procedure, route, or capability a phase should use —
it measures what the caller actually executed and verifies the evidence matches the
declaration.

`ReuseExperimentHarness(cold_runner=..., warm_runner=...).run(spec)` invokes each port exactly
once (no retries, no loops), requires each call to return a canonical
`ExecutionEfficiencyEvidence`, and freezes everything into an inert
`ReuseExperimentResult`. A port returning untyped data (dicts, strings, `verified=true`
text) fails closed with `PhaseEvidenceError`; the harness never parses untyped data into
measurements. Runner exceptions propagate unchanged — the harness fabricates nothing.

## Cold and warm definitions

Cold/warm is classified **only** from the typed `ReuseMode` of the returned evidence. The
closed, total mapping partitions the canonical vocabulary:

| Phase | Reuse modes | Meaning |
| --- | --- | --- |
| COLD | `DETERMINISTIC_CAPABILITY`, `NOVEL_PLAN`, `EXPLORATORY` | no claim of reusable prior execution (L1 direct deterministic path, L4 planned, L5 exploratory) |
| WARM | `CACHE_REUSE`, `PROCEDURE_REUSE`, `GUIDED_PROCEDURE` | explicit reuse evidence: cached verified result (L0), compiled procedure (L2), guided procedure (L3) |

Cold/warm is never inferred from execution time, cost, model-call counts, Procedure name
text, or any other measured value. If the evidence modes do not match the phase semantics —
a cold phase claiming `PROCEDURE_REUSE`, or a warm phase executing `NOVEL_PLAN` — the
experiment is `INVALID_EXPERIMENT` and keeps both evidence records but produces **no**
comparison.

## Metrics reused from M8.01

Every measured fact, and all metric arithmetic, comes verbatim from
`ExecutionEfficiencyEvidence` and `compare_execution_efficiency`:

- model call count; aggregate model tokens (input/output components stay evidence);
- elapsed duration in exact microseconds;
- external cost as an exact `Decimal` when accounting units match;
- research-query, machine-action, and repair-attempt counts;
- exact rational `reuse / baseline` ratios, with `None` (never Infinity/NaN) when the
  baseline denominator is zero;
- exact `ProcedureId` + revision binding for procedure reuse modes;
- typed task relationship evidence.

The harness defines **no metric of its own** and re-implements none of M8.01's delta,
ratio, zero-denominator, or disposition rules: `ReuseExperimentResult.metric_comparison()`
simply delegates to the canonical comparison, and unknown measurements stay `None` rather
than being fabricated as zeros.

## Verified-success requirement

Efficiency comparison is meaningful only if success truth remains intact, and M8.01 already
enforces it:

- a faster **failed** warm run is not an improvement — it is `WARM_REGRESSED`;
- a cheaper **unverified** warm run is not an improvement — it is `WARM_REGRESSED`;
- a warm result whose text says `verified=true` is not verified — verified success requires
  a canonical typed `VerificationPayload` with `passed=True` plus an explicit verification
  source/reference, and the canonical contract rejects contradictory combinations at
  construction time.

The experiment verdict is a strict one-to-one translation of the canonical disposition:

| Canonical M8.01 disposition | Experiment verdict |
| --- | --- |
| `IMPROVED` | `WARM_IMPROVED` |
| `NO_MATERIAL_IMPROVEMENT` | `WARM_NO_MATERIAL_IMPROVEMENT` |
| `REGRESSED` | `WARM_REGRESSED` |
| `NOT_COMPARABLE` | `NOT_COMPARABLE` |
| `INSUFFICIENT_EVIDENCE` | `INSUFFICIENT_EVIDENCE` |

Task comparability is delegated entirely to M8.01: identical `TaskId` values establish
same-task replay; different tasks require explicit typed relationship evidence
(`RELATED_TASK_REUSE` permits comparison; `UNRELATED`, missing, or contradictory evidence
yields `NOT_COMPARABLE`); natural-language similarity is never inferred.

## Invalid experiments

The harness adds exactly three harness-level validity checks, all fail-closed:

1. phase classification: the cold evidence mode must be cold and the warm evidence mode
   must be warm;
2. declared task match: each phase must have measured the task the spec declared;
3. procedure binding: when the spec declares an expected warm procedure, the warm evidence
   must carry exactly that `ProcedureId` and revision (there is no `latest`/wildcard).

Any violation, or a canonical rejection of the phase pair (for example two records that
dishonestly share one evidence reference), yields `INVALID_EXPERIMENT` with deterministic
reasons, preserves both evidence records, and produces no comparison — nothing can be
smuggled through an invalid experiment.

## Deterministic measurement design

The harness reads no clock, generates no randomness, performs no network or model call, and
performs no persistence. All measurements arrive as explicit canonical evidence from the
injected runners, so tests use deterministic fakes: no sleeping, no wall-clock
nondeterminism, no paid model call, no benchmark requiring credentials. Repeated runs over
the same injected evidence produce identical results, and results serialize to deterministic
sorted JSON (schema version 1) with exact-field validation that recomputes every derived
value — tampered verdicts, metrics, classifications, or reasons are rejected on
deserialization. A runner may persist its own results externally; that is the runner's
business and invisible to the harness.

## Experiment results are evidence only — no authority

A `ReuseExperimentResult` — including one whose verdict says the warm phase improved — is
inert data. It cannot and must not:

- route future tasks or mutate routing policy;
- select, activate, or promote any Procedure or candidate;
- grant Permission or change any capability authority;
- lower Risk or bypass the Action Gate;
- widen any `ResourceEnvelope` budget;
- clear or influence an `EmergencyStop`;
- transition or mark any Task successful.

No policy is auto-updated from a result. The module imports only
`agentx.core.reuse_efficiency` and `agentx.core.ids`; it has no handle on the kernel,
stores, capabilities, cognition, or infrastructure, and no subsystem imports it.
`agentx.core.reuse_efficiency` remains the canonical measurement truth boundary; this
harness is only the experiment orchestration around it.
