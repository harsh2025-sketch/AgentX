# Verified reuse-efficiency evidence (M8.01)

`agentx.core.reuse_efficiency` is the canonical **evidence/comparison contract** for the
AgentX reuse-efficiency experiment. It records measured facts for one run and compares a
baseline run with a later run. It does not execute either run.

## Research claim this contract can support

The intended experiment is:

1. a first execution is novel/reasoning-heavy and relatively expensive;
2. a later same-task replay or explicitly related task uses verified reusable work;
3. the later execution consumes less measured resources;
4. canonical verified success is preserved.

The comparison reports only what the supplied typed evidence supports. A cheap failed or
unverified run is never an efficiency win.

## One-run evidence schema

`ExecutionEfficiencyEvidence` is frozen and versioned. It requires canonical `TaskId`,
`EpisodeId`, and correlation UUID identity plus an explicit evidence source/reference.
Unknown measurements are `None`, not zero.

Measured fields are:

- caller-supplied `started_at` + `ended_at`, or explicit `elapsed`; timestamps are normalized
  to UTC and no clock is read by this module;
- model call count;
- model input/output token counts and aggregate model-token count (when both components are
  present the aggregate is derived exactly, and a contradictory supplied total is rejected);
- research-query count;
- machine/capability-action count;
- repair-attempt count;
- exact finite non-negative `Decimal` external cost plus its caller-chosen accounting unit;
- exact `ProcedureId` + positive integer revision when a procedure mode is claimed;
- closed reuse mode;
- typed outcome status and explicit canonical verification evidence.

Counters reject booleans, negatives, and values above the signed 64-bit accounting range.
Costs reject negative, NaN, and Infinity. Timestamp reversal and elapsed/timestamp
contradictions fail closed.

## Reuse-mode vocabulary

The closed descriptive `ReuseMode` vocabulary mirrors the existing AgentX execution
hierarchy without importing or invoking the cognition router:

| Evidence mode | Existing hierarchy meaning |
| --- | --- |
| `CACHE_REUSE` | L0 cached verified result |
| `DETERMINISTIC_CAPABILITY` | L1 direct deterministic path |
| `PROCEDURE_REUSE` | L2 compiled/reasoning-free procedure |
| `GUIDED_PROCEDURE` | L3 procedure with reasoning gaps |
| `NOVEL_PLAN` | L4 planned execution |
| `EXPLORATORY` | L5 exploratory execution |

These values are data only. They do not route a task or activate a procedure.

## Verified-success truth boundary

`ExecutionEvidenceOutcome.VERIFIED_SUCCESS` is accepted only when a canonical
`agentx.core.events.VerificationPayload` is present with `passed=True` and an explicit
verification source/reference. Text such as `verified=true`, a successful API return, a
zero model-call count, or a procedure description cannot satisfy this rule.

A passing `VerificationPayload` attached to `FAILED` or `UNVERIFIED` is contradictory and
rejected. An unverified run cannot carry completed verification evidence.

## Procedure revision binding

Procedure modes require `ProcedureRevisionRef(ProcedureId, revision)`. Revision is an exact
positive integer. There is no `latest`, wildcard, or implicit upgrade. Text/detail fields
cannot replace the typed pair.

This contract preserves the claimed exact revision but intentionally does not load a
procedure store or prove that the revision exists; authenticity of instrumentation is an
upstream responsibility.

## Task comparability

The deterministic rules are:

- identical typed `TaskId` values establish same-task replay;
- different `TaskId` values require explicit `TaskRelationshipEvidence`;
- `RELATED_TASK_REUSE` permits comparison;
- `UNRELATED`, absent cross-task relation evidence, or contradictory relationship evidence
  yields `NOT_COMPARABLE`;
- natural-language similarity is never inferred.

Relationship evidence itself carries a typed relationship plus explicit source/reference.

## Metrics and exact arithmetic

`compare_execution_efficiency()` computes `reuse - baseline` for every primary dimension
measured in both runs:

- model calls;
- aggregate model tokens;
- elapsed microseconds;
- external cost when accounting units match;
- research queries;
- machine actions;
- repair attempts.

Each `MetricComparison` contains exact baseline/reuse values, exact delta, direction, and an
exact rational `reuse / baseline` ratio. When the baseline denominator is zero, ratio is
`None`: Infinity and NaN are never emitted. Money remains `Decimal`; no provider price is
estimated here.

Input/output token components are retained as evidence and can establish the exact aggregate
token count. The disposition uses aggregate tokens so components are not double-counted as
independent optimization dimensions.

## Disposition policy

There is no weighted or opaque score and no research-significance threshold.

After task comparability is established:

1. loss of verified success => `REGRESSED`, regardless of cheaper metrics;
2. unverified baseline => `INSUFFICIENT_EVIDENCE`;
3. no common measured efficiency dimension => `INSUFFICIENT_EVIDENCE`;
4. any measured dimension worse => `REGRESSED`;
5. at least one measured dimension better and none worse => `IMPROVED`;
6. all common measured dimensions equal => `NO_MATERIAL_IMPROVEMENT`.

Reasons are generated deterministically from typed evidence and identify each measured
dimension's change. User-controlled text does not participate in classification.

## Serialization

Evidence and comparisons use schema version 1, deterministic sorted JSON, UTC timestamp
strings, exact decimal strings, closed enum values, immutable nested records, and exact-field
validation. Unknown fields are rejected. Comparison deserialization recomputes metrics,
ratios, reasons, and disposition and rejects tampered derived values.

No pickle, object hooks, dynamic imports, or executable payloads are used.

## Authority and architecture

`agentx.core.reuse_efficiency` imports only the standard library and `agentx.core.events` /
`agentx.core.ids`. It does not import kernel, capabilities, cognition, Hive, infrastructure,
learning, or procedure execution.

Metrics are inert evidence. They cannot grant permission, lower risk, widen a resource
budget, transition a Task, activate a Procedure, promote knowledge, or bypass the Trusted
Kernel / Action Gate.

## Explicit non-goals

M8.01 does **not**:

- run `AgentLoop` or a benchmark scenario;
- invoke a model or perform research/network calls;
- execute a capability;
- retrieve, load, compile, or activate a procedure;
- query or write Hive;
- read wall-clock or monotonic time;
- persist metrics or add a migration;
- estimate model/provider pricing;
- perform statistical-significance testing;
- implement an RL reward or dashboard;
- modify routing, execution, verification, or authority policy.

The module is intentionally the pure measurement truth boundary needed before a later
instrumented experiment can claim that verified reuse made execution cheaper or faster.
