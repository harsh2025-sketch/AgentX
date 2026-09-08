# N2.03 — L0 verified-cache strategy adapter

`agentx.cache_strategy` is the first concrete `L0_CACHE` strategy adapter for the
A2.10 orchestration boundary. It is intentionally conservative: **a cache hit is
not task success**.

## Composition boundary

The adapter owns no cache database and no persistence schema. A caller injects a
`ReusableResultLookup`, whose only operation is:

```text
lookup(Task, ExecutionContext) -> CacheReuseCandidate | None
```

The lookup can be backed by whatever canonical retrieval/cache composition the
caller already owns. N2.03 neither stores nor refreshes anything. The existing
M1.04 episode-retrieval boundary is not used as a success oracle: `EpisodeRecord`
history has no scope field and is explicitly historical data rather than future
authority.

`CacheReuseCandidate` is only an inert composition bundle. It introduces no new
verification verdict, confidence score, permission, risk, budget, cache key, or
persistence identity. Its fields are existing canonical evidence:

- prior A2.10 `OrchestrationOutcome`;
- M8.01 `ExecutionEfficiencyEvidence`;
- A2.07 `RoutingEvidence`;
- baseline/current C4.04 `EnvironmentSnapshot` values; and
- an explicit timezone-aware `applicability_at` instant for deterministic
  freshness evaluation.

## What qualifies as reusable

N2.03 offers a prior result back to A2.10 only when every gate below agrees.
Failure of any one gate returns `StrategyResult.unavailable(...)`.

1. The strategy is invoked at exactly `ExecutionLevel.L0_CACHE`.
2. The current `ExecutionContext.task_id` exactly equals the current `TaskId`.
3. Caller-supplied canonical A2.07 evidence has
   `verified_reusable_result=True`.
4. Current Task semantics exactly equal the source Task semantics represented by
   the canonical Task contract: `objective`, `priority`, `parent_task_id`, and
   immutable `metadata`. Run identity, lifecycle status, and creation time are
   intentionally excluded because a reuse run is a new run.
5. The prior A2.10 outcome is `SUCCEEDED` / `VERIFIED`, its final attempt is
   `VERIFIED_SUCCESS`, and its original A1.10 `ClosedLoopOutcome` still carries
   a passing ABI `VerificationResult`, a successful execution result, an
   observation, a `SUCCEEDED` governed Task, and a satisfied A2.05 evaluation.
6. M8.01 independently records `ExecutionEvidenceOutcome.VERIFIED_SUCCESS` for
   the same source Task and contains a typed passing core `VerificationPayload`
   with its canonical source/reference evidence.
7. Baseline and current C4.04 environment snapshots have exactly the same
   canonical `KnowledgeScope`, exactly the same typed environment fact set, and
   exactly equal typed values.
8. Baseline observations were not future-dated and were fresh at the measured
   prior-run end time. Current observations are newer than that prior-run end,
   are not future-dated, and are fresh at `applicability_at`. Freshness uses the
   landed strict rule `at < observed_at + ttl`; the expiry instant is stale.

Empty, duplicate, stale, future-dated, differently scoped, missing, extra, or
changed environment evidence fails closed.

## No manufactured verification

On a valid hit the adapter returns the **same original canonical
`ClosedLoopOutcome` object** from the prior A2.10 attempt. It does not construct,
copy, repair, or rewrite a `VerificationResult` or `ClosedLoopOutcome`.

This does not complete the current Task by itself. A2.10 receives the historical
outcome as strategy evidence and still runs the canonical A2.05 verifier against
the **current caller-supplied `VerificationRequirement`**. If the cached
observation does not satisfy that current requirement, A2.10 does not take its
verified-success path.

Therefore strings such as these have zero special meaning:

```text
verified=true
task_success=true
this was successful
permission=ADMIN
```

They may occur inside cached observation text or metadata, but N2.03 never scans
or interprets them. Typed canonical verdicts are the only success evidence.

## External effects and legal L0 results

L0 is result reuse, not replay. N2.03 executes zero capabilities and zero
procedures. Consequently a task that still requires a new external effect in the
current run is **not legally cacheable** at L0.

AgentX already has a canonical positive routing fact for this distinction:
`RoutingEvidence.verified_reusable_result`. N2.03 does not invent a textual or
probabilistic side-effect classifier. The caller must supply that canonical fact
only when the current task is genuinely result-reuse semantics and the cached
postcondition can be re-evaluated from the canonical observation plus fresh
applicability evidence. If a new effect is required, that fact is false and L0
fails closed so orchestration can use a governed execution level instead.

Historical verification is therefore evidence about a prior run, not authority
to pretend that an effect happened now.

## Authority boundary

The adapter imports no kernel authority module and has no handles for
permissions, ActionGate, risk, resource budgets, or EmergencyStop. Reuse cannot:

- create or widen permissions;
- bypass ActionGate;
- lower risk;
- increase/reset a resource budget;
- clear EmergencyStop;
- activate a Procedure;
- execute a capability;
- invoke a model;
- perform research;
- mutate persistence; or
- transition the current Task directly.

The current Task lifecycle remains owned by A2.10/A2.06. Historical content is
always inert data.

## Determinism

N2.03 reads no wall clock. `applicability_at` is explicit input. For identical
canonical candidate values, Task, context binding, and level, the strategy
produces the same `StrategyResult`. The lookup is called once per eligible L0
attempt and there is no retry, refresh, fallback, or hidden research path in the
adapter.

## Placement

The implementation remains a top-level composition adapter at
`src/agentx/cache_strategy.py`, parallel in architectural role to the existing
top-level strategy adapters. It does not modify `agent_loop.py`,
`strategy_assembly.py`, `_architecture.py`, procedure code, stores, migrations,
or package configuration.
