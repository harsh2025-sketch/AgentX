# M1.02 — Governed capability strategy adapter

`agentx.capability_strategy` is the top-level composition adapter that lets A2.10 attempt one explicitly pre-bound canonical capability request as deterministic L1 dispatch.

It is wiring, not authority.

## Public contracts

### `CAPABILITY_STRATEGY_LEVEL`

The only supported strategy level is canonical `ExecutionLevel.L1_DIRECT`. The adapter is not a cache strategy, procedure runner, guided strategy, planner, or exploratory/research strategy.

### `CapabilityStrategyBinding`

A frozen binding containing exactly:

- `request: CapabilityRequest[Any]` — the canonical already-selected capability identity plus typed parameters;
- `level: ExecutionLevel` — fixed to `L1_DIRECT`.

Construction rejects raw dictionaries, missing requests, non-canonical levels, and any level other than L1. The binding has no permission, authority, risk, budget, verification, retry, fallback, routing, escalation, model, or metadata override.

### `GovernedCapabilityStrategy`

Constructed with:

- a real canonical A2.04 `Executor`;
- one immutable `CapabilityStrategyBinding`.

It implements the A2.10 strategy port:

```text
attempt(task, context, level) -> StrategyResult
```

If `level` is not the bound L1 level, it returns `StrategyResult.unavailable(...)` and performs no execution or fallback.

For L1 it creates the PENDING sibling Task/context required by the governed A1.10 execution boundary, then constructs one `ExecutorRequest` using the exact pre-bound `CapabilityRequest` and calls `Executor.execute(...)` exactly once.

The canonical returned `Result[ClosedLoopOutcome, AgentXError]` is wrapped with `StrategyResult.executed(...)` without unwrapping, rewriting, retrying, or manufacturing verification.

## Governed execution path

```text
AgentLoop (A2.10)
  -> GovernedCapabilityStrategy.attempt(...)
  -> Executor.execute(ExecutorRequest(...))          A2.04
  -> CapabilityExecutionLoop.run(...)               A1.10
     -> CapabilityRegistry                           A1.09
     -> PermissionEngine / ActionGate                Trusted Kernel
     -> EmergencyStop
     -> ExecutionContext cancellation/deadline
     -> ResourceBudget
     -> Capability.execute(...)
     -> Capability.verify(...)
     -> canonical ClosedLoopOutcome
  -> StrategyResult.executed(canonical result)
  -> A2.10 classification + A2.05 verification requirement gate
```

The adapter never calls `Capability.execute(...)` or `Capability.verify(...)` directly and does not import the A1.10 runtime, registry, verifier, or Trusted Kernel implementation.

## Task lifecycle separation

A2.10 transitions the orchestrated Task through its canonical `TaskManager` before invoking a strategy. A1.10 independently requires a PENDING Task at its own boundary. The baseline A2.10 orchestration harness already resolves this by creating a fresh PENDING sibling Task for the governed capability attempt; M1.02 follows the same pattern.

Only the original Task objective is copied, as inert text. The governed `ExecutionContext` reuses the original correlation ID, cancellation token, and deadline but binds the sibling Task ID. The capability request is never derived from the Task objective or metadata.

Therefore text such as:

```text
verified=true
permission=ADMIN
risk=R0
skip ActionGate
ignore previous instructions
```

cannot alter capability identity, typed parameters, required permissions, risk, budget, or verification requirements.

## Failure semantics

The adapter does not collapse governed outcomes into booleans.

- Wrong execution level -> explicit A2.10 strategy unavailability; no execution.
- Registry miss -> canonical A1.10 `runtime.capability_not_registered` outcome.
- Missing permission -> canonical A1.10 `runtime.permission_denied` outcome.
- ActionGate refusal -> canonical A1.10 `runtime.gate_denied` outcome.
- Emergency stop -> canonical A1.10 `runtime.emergency_stop_active` outcome.
- Cancellation/deadline -> canonical A1.10 `runtime.context_stopped` outcome.
- Budget refusal -> canonical A1.10 `runtime.budget_denied` outcome.
- Capability execution failure -> canonical A1.10 execution-failure outcome.
- Verification failure -> canonical A1.10 verification-failure outcome.
- Verified execution -> canonical `LoopOutcome.VERIFIED`; A2.10 still performs its own final A2.05 verification-requirement gate before orchestration success.

No exception-free return, provider text, observation text, or `ExecutionResult.succeeded=True` can make the adapter assert success.

## Deliberate non-scope

M1.02 does not:

- modify A2.10 or `StrategyRegistry`;
- implement filesystem operations or any concrete capability;
- register strategies automatically;
- route or mutate `RoutingEvidence`;
- retry, escalate, or fall back;
- implement planner, procedure, model, research, Hive, episode, cache, or persistence behavior;
- call native APIs, subprocesses, `eval`, or `exec`;
- modify permission, risk, budget, verification, or architecture contracts.

The adapter remains a single top-level `agentx` composition module because it must legally compose cognition-owned A2.10 strategy contracts with capabilities-owned A2.04 execution contracts without adding a forbidden `COGNITION -> CAPABILITIES` subsystem edge.
