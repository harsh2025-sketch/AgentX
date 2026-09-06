# AgentX Strategy-Performance History (A8.01)

A8.01 persists and queries the **measured** history of execution-strategy
performance so later tasks (A8.02+) can compare and optimize strategies. It is
a data layer: nothing here selects strategies, changes the Router (A2.07),
implements bandits/RL, caches results, or optimizes costs automatically.

The canonical execution-level identity vocabulary is the A2.07 hierarchy
(L0 cached → L5 exploratory). The A8.01 record names the level that **was
actually executed** as an observed fact; it never routes or recommends one.

## Canonical record

`agentx.core.strategy_performance.StrategyPerformanceRecord` is the inert
shared-domain contract (schema v1). It records, as appropriate:

- `record_id` — canonical `StrategyPerformanceId` identity;
- `level` — executed strategy class, `ExecutionStrategyLevel` pinned to the
  canonical A2.07 execution levels (the L0-cached → L5-exploratory vocabulary
  is guarded against `agentx.cognition.router` by an architecture test);
- `strategy_name` / `strategy_version` — strategy identity and version labels;
- `procedure_id` + `procedure_revision`, `capability_id` — which
  procedure/capability revision executed;
- `task_id`, `episode_id`, `correlation_id` — task/scope and
  provenance/episode references;
- `scope`, `environment` — bounded labels of the task scope and environment;
- `outcome` — the canonical historical terminal outcome vocabulary reused
  from `CausalOutcome` (verified / verification_failed / execution_failed /
  denied / cancelled / timed_out);
- `verification` — embedded canonical `VerificationPayload` evidence;
- `failure_category` — canonical C4.01 `FailureCategory` when the outcome is a
  strategy failure;
- `cost` + `cost_unit`, `latency_seconds` — measured quantities (non-negative,
  finite, unit-bound cost);
- `started_at`, `ended_at`, `recorded_at` — UTC timestamps.

## No fake success

Success requires canonical verified evidence. Construction and deserialization
enforce the same rules C2.10 causal experience enforces:

- `outcome == verified` **requires** an embedded canonical `VerificationPayload`
  with `passed == True`; a missing or failing payload is a validation error.
- `outcome == verification_failed` requires an embedded payload with
  `passed == False`.
- every other outcome must **not** carry verification evidence (fabricated
  evidence is rejected).
- `verified` outcomes carry no failure category; `denied`/`cancelled`/
  `timed_out` carry neither verification nor a failure category; the two
  failure outcomes (`execution_failed`, `verification_failed`) require an
  explicit category (`FailureCategory.UNKNOWN` when the cause is not further
  classified).

Model text is inert: strings such as `"verified=true"` or `"success"` in any
label, scope, environment, or payload detail can never flip an outcome,
because classification reads only typed enum fields and typed boolean verdicts.
Deserialization rejects unknown enum strings, unknown fields, unsupported
schema versions, non-finite/negative metrics, and non-timezone-aware
timestamps; it never coerces hostile input into a record.

## Deterministic aggregation primitives

Pure, in-`core` primitives (usable without persistence):

- `aggregate_strategy_records(records)` → `StrategyPerformanceAggregate`:
  attempt count; per-outcome counts and per-failure-category counts over the
  canonical closed vocabularies (all members, canonical order, zero counts
  included); measured-latency summary; per-unit cost summaries (units are
  never mixed). Aggregation ignores input order.
- `group_strategy_records(records, dimension=...)` → ordered
  `StrategyPerformanceGroup` tuple, keyed by observed attribute values only
  (level / strategy_name / strategy_version / scope / environment), with the
  explicit `None` "unset" group first, LEVEL groups in canonical L0-L5 order,
  and other dimensions sorted lexicographically.

These primitives are descriptive statistics; comparison, ranking, selection,
and optimization are deliberately left to A8.02+.

## Persistence schema

A8.01 appends migration **9**, `create_strategy_performance_store`, to the
canonical C1.05 migration sequence. It creates `agentx_strategy_performance`
(append-only; rows are never updated or deleted) with:

- `sequence INTEGER PRIMARY KEY AUTOINCREMENT` — durable order;
- `record_id TEXT NOT NULL UNIQUE` — canonical identity + duplicate protection;
- denormalized, indexed filter columns (`level`, `outcome`, `strategy_name`,
  `procedure_id`, `capability_id`, `task_id`, `episode_id`, `correlation_id`);
- `record_json TEXT NOT NULL` — the canonical deterministic record JSON;
- `recorded_at_utc TEXT NOT NULL` — SQLite insertion timestamp.

Column text is intentionally not CHECK-bounded by SQL vocabularies: canonical
vocabularies live in `agentx.core` and must evolve without SQL surgery. Every
read cross-checks the denormalized columns against the canonical record; a
mismatch is corruption (`CorruptStrategyPerformanceError`), never alternate
truth.

## Store API

`agentx.infrastructure.strategy_performance_store.StrategyPerformanceStore`
receives an explicit canonical `SQLiteDatabase`:

```python
store = StrategyPerformanceStore(database)
sequence = store.append(record)                       # -> durable sequence
restored = store.get(record.record_id)                # -> record | None
entries = store.read(after_sequence=0, level=..., outcome=..., ...)  # ordered, exact filters
stats = store.aggregate(...)                          # filters + core aggregation
groups = store.aggregate_grouped(dimension=..., ...)  # filters + core grouping
```

`append` accepts only canonical `StrategyPerformanceRecord` values, commits
atomically, and rejects duplicate `record_id`s with
`DuplicateStrategyPerformanceError`. History survives close/reopen and
migration replay is idempotent.

## Out of scope

No strategy selection, no Router change, no bandits/RL, no caching, no
automatic cost optimization, no verification of outcomes (evidence is only
required and stored), no comparison/ranking (A8.02+), no authority grants.
