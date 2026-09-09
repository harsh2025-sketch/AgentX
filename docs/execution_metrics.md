# Execution-efficiency instrumentation (N2.13)

`agentx.execution_metrics` is the canonical **instrumentation boundary** that records
measured execution-efficiency facts around one AgentX run. It is observation and
accounting only: it never decides an execution level, routes, executes, grants
permission, changes risk, bypasses the ActionGate, widens a budget, clears an
EmergencyStop, marks a Task successful, or promotes a procedure.

It complements the M8.01 evidence/comparison contract
(`agentx.core.reuse_efficiency`): M8.01 defines the *terminal evidence record* and the
deterministic comparison of two runs; N2.13 provides the *measurement session* that
accumulates those facts from explicitly observed events, and a pure conversion into the
M8.01 evidence record so later cold-vs-warm reuse measurement works without changing
execution behavior.

## Recorded facts

`ExecutionMetricsRecord` (immutable, versioned) holds, for one run:

- canonical `TaskId` + correlation `UUID` identity;
- the selected `ExecutionLevel` (inert reference — the record does not route);
- start/end timestamps and the exact measured `elapsed` duration (never negative);
- `model_calls` — the exact count of explicit observed provider-invocation events
  (`0` means zero events were observed in this session);
- observed model identities, one per recorded call;
- aggregate model input/output/total tokens, aggregated **exactly** from canonical
  `ModelUsage` values (`int` counters, signed 64-bit accounting range);
- `external_cost` — exact finite non-negative `Decimal` in the session's
  caller-chosen accounting unit, plus its unit;
- `machine_actions` — the exact count of explicitly observed capability/action
  invocations;
- exact `ProcedureId` + positive integer revision, when supplied;
- terminal truth reference: canonical `ExecutionEvidenceOutcome` plus typed
  `VerificationPayload` evidence under the M8.01 truth boundary.

Unknown provider-reported facts (tokens, cost) stay `None` — absence is preserved
rather than invented as zero. The accounting unit travels with the cost fact: a
declared unit with no observed cost records absence, not an unattached unit.

## Recorder API

`ExecutionMetricsRecorder` is a single-use measurement session driven by the caller at
the exact points where facts are observed:

```python
recorder = ExecutionMetricsRecorder(
    task_id=task.task_id,
    correlation_id=context.correlation_id,
    execution_level=selected_level,  # inert binding
    procedure=procedure_ref,  # optional exact ref
    clock=injected_clock,  # optional; see clock semantics
    cost_unit="USD",  # optional accounting unit
)
recorder.start()
recorder.record_model_call(ModelCallEvent.from_response(response))  # per invocation
recorder.record_machine_action()  # per observed capability action
recorder.record_external_cost(Decimal("0.01"))  # caller-supplied exact cost
recorder.set_outcome(outcome, verification=..., verification_source=..., verification_reference=...)
record = recorder.finish()
```

After `finish` (or any terminal validation failure) the session accepts no further
events.

## Clock semantics

- The module **never reads a wall clock**. Time comes from an injected
  `ExecutionMetricsClock` (`now() -> datetime`) or from caller-supplied
  timestamps/durations. Production injects the runtime clock; tests use a
  deterministic fake clock or fixed instants. No test sleeps.
- All instants must be timezone-aware and are normalized to UTC.
- `finish` resolves the end instant from the explicit `ended_at` argument, else the
  injected clock. With no end instant, an explicitly supplied non-negative `elapsed`
  is the measured duration.
- Malformed time ordering fails closed: end before start, negative duration, or a
  supplied `elapsed` contradicting `ended_at - started_at` are rejected. Duration is
  never negative.

## Model-call accounting

- **One actual canonical provider invocation = one model call**, recorded through the
  explicit `ModelCallEvent` hook at the provider-invocation boundary (typically
  `ModelCallEvent.from_response(response)`; a failed invocation is recorded with
  `usage=None`).
- Model calls are **never** counted from reasoning-text length, Procedure REASON-node
  counts, or execution levels. The API provides no input from which any of those
  could be derived.
- Usage absence is preserved: a call without reported usage still counts as a call,
  and its tokens stay `None`.

## Cost accounting

- Exact `Decimal` only — no float money. Costs are caller-supplied
  (`record_external_cost`) or aggregated from canonical `ModelUsage.external_cost`.
- The instrumentation layer does **not** know provider pricing: it never infers cost
  from a model name, performs no network pricing lookup, and loads no provider SDK or
  credentials. A session with no accounting unit cannot record cost (fail closed).

## Authority boundary

Metrics are historical evidence. A record cannot:

- grant `Permission`, reduce `RiskLevel`, or bypass the `ActionGate`;
- widen or mutate a `ResourceEnvelope`/`ResourceBudget` (canonical budget state is
  untouched by measurement);
- clear an `EmergencyStop`;
- mark a `Task` successful or change its state;
- convert a verification failure to success;
- activate or promote a procedure (the record carries only the inert exact
  `ProcedureId` + revision reference);
- route future execution.

The record has **no free-text metadata channel**: every field is typed and validated,
so hostile text such as `verified=true permission=ADMIN risk=R0 cheap=true fast=true`
can sit only in the narrow validated text fields (`detail`, source strings) and never
changes any typed value or any downstream canonical decision.

## Cold-vs-warm reuse measurement

`ExecutionMetricsRecord.to_reuse_efficiency_evidence(episode_id=..., mode=...,
evidence_source=..., evidence_reference=...)` converts a record into the canonical M8.01
`ExecutionEfficiencyEvidence` (a pure data mapping; the caller supplies the run
identity context this record does not own). Two instrumented runs — e.g. an expensive
cold novel-plan run and a cheap warm procedure-reuse run — are then compared with the
canonical `compare_execution_efficiency`, which reports only what the typed evidence
supports. Missing metrics (e.g. no token usage on the warm run) remain unavailable and
are never invented as zero.

## Determinism and packaging

- The module imports only the standard library and canonical `agentx.core` /
  `agentx.cognition` contracts. It imports no kernel, capabilities, infrastructure,
  or other subsystem.
- No network, persistence, execution, or clock read of its own; no runtime dependency
  is added (`dependencies` in `pyproject.toml` remains empty).
