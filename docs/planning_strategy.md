# N2.06 — L4 planning strategy boundary

`agentx.planning_strategy` is the concrete `L4_PLANNED` strategy for novel
tasks that require bounded planning. It produces **plan data only** — an
inert, strictly validated canonical `TaskDecomposition` candidate — through
the canonical A2.03 `Reasoner` and the canonical A6.01
`accept_model_proposal` acceptance boundary.

It is planning, not authority.

## Public contracts

### `PLANNING_STRATEGY_LEVEL`

The only supported strategy level is canonical `ExecutionLevel.L4_PLANNED`.
The strategy is not a cache strategy, direct executor, procedure runner,
guided strategy, or exploratory/research strategy.

### `PlanningStrategyLimits`

A frozen limits record containing exactly:

- `max_output_tokens: int` (default `PLANNING_DEFAULT_MAX_OUTPUT_TOKENS`) —
  the finite model-output bound forwarded on every `ReasonerRequest`;
- `max_instruction_chars: int` (default
  `PLANNING_DEFAULT_MAX_INSTRUCTION_CHARS`) — the finite bound on the
  assembled planning instruction.

The derived `max_response_chars` property gates a fail-closed ceiling on
total model response text so a hostile provider that ignores
`max_output_tokens` still cannot return unbounded output.

The limits carry no permission, authority, risk, budget, retry, routing,
escalation, or fallback configuration.

### `PlanningStrategy`

Constructed with:

- a real canonical A2.03 `Reasoner` (bound to the `REASONING` model role);
- one immutable `PlanningStrategyLimits` (optional; defaults apply);
- an optional A1.07 `MonotonicClock` for deterministic stop observation.

Two ports exist:

```text
plan(task, context) -> Result[TaskDecomposition, AgentXError]
attempt(task, context, level) -> StrategyResult        # A2.10 port, fails closed
```

`plan(...)` performs the bounded planning flow. `attempt(...)` satisfies the
A2.10 `ExecutionStrategy` port so the strategy can be registered in a
`StrategyRegistry`, and it always fails closed: planning produces no
execution outcome, the port vocabulary has no plan channel, and no L4
attempt can ever claim `executed`.

## Planning bounds

| Dimension | Bound | Enforced by |
| --- | --- | --- |
| Model output tokens | `PlanningStrategyLimits.max_output_tokens` | forwarded `ReasonerRequest.max_output_tokens` |
| Model response text | `limits.max_response_chars` | `planning_strategy.response_too_large` |
| Assembled instruction | `limits.max_instruction_chars` | `planning_strategy.input_too_large` |
| Decomposition nodes | canonical `MAX_DECOMPOSITION_NODES` (`PLANNING_MAX_NODES`) | A6.01 acceptance boundary |
| Decomposition depth | canonical `MAX_DECOMPOSITION_DEPTH` (`PLANNING_MAX_DEPTH`) | A6.01 acceptance boundary |
| Success criteria per node | canonical `MAX_SUCCESS_CRITERIA` | A6.01 acceptance boundary |
| Model calls | exactly one per `plan(...)` | no retry/fallback path exists |

No second decomposition schema is invented: the strategy consumes the landed
A6.01 `TaskDecomposition`/`DecompositionNode` contract exclusively.

## Typed output rule

Free-form model text is never executable plan authority:

1. the instruction requests exactly the canonical proposal shape
   (`schema_version` / `root_task_id` / `nodes`) and nothing else;
2. the response is parsed as one JSON document — no fence stripping, no
   substring extraction; malformed text fails closed with
   `planning_strategy.invalid_json`;
3. the parsed data is accepted exclusively through
   `accept_model_proposal(parsed, root_task_id=task.task_id, version=1)`.

Unknown fields (`task_decomposition.unknown_fields`), malformed hierarchies,
unbounded node counts/depths, duplicate identities, dangling dependency
references, cycles, and non-canonical ids
(`task_decomposition.invalid_structure` / `invalid_field` /
`root_mismatch` / ...) all fail closed with structured
`task_decomposition.*` codes. Record identity (`decomposition_id`,
`version`, `created_at`, record `metadata`) is owned by the accepting
boundary, never by model text.

## Planning is not execution

A successful `plan(...)` result means exactly one thing:

> a bounded candidate plan was produced.

It does **not** mean the Task was accepted, permission was granted, a
capability is executable, a Procedure became active, the Task succeeded, or
verification passed.

The strategy:

- performs no capability execution and no Procedure execution;
- performs no direct Task success transition (it imports no task-state or
  task-manager surface);
- performs no research/network I/O itself (the one model call is the
  REASONING role through the canonical Reasoner);
- performs no persistence and publishes no events;
- never converts a stop into success: the caller's `ExecutionContext` is
  observed before the model call and again after it, and a cancelled or
  expired planning run discards the plan and fails closed
  (`planning_strategy.cancelled` / `planning_strategy.timeout`);
- propagates provider/reasoner failures unchanged instead of fabricating
  plan output.

There is no direct `L4 -> CAPABILITIES` edge and no direct
`L4 -> ProcedureStore` activation.

## Hostile model output

Payloads such as `permission=ADMIN`, `risk=R0`, `execute_now=true`,
`task_success=true`, `verified=true`, `skip_action_gate=true`, or
`disable_stop=true` either:

- fail closed as `task_decomposition.unknown_fields` when smuggled in as
  fields, or
- remain inert verbatim strings inside canonical node objectives, success
  criteria, or metadata when smuggled inside valid-shaped text.

Either way they are inert data and cannot influence Trusted Kernel state.
Metadata keys containing authority/secret markers are rejected by the
canonical A6.01 boundary.

## Bounded planning flow

```text
PlanningStrategy.plan(task, context)
  -> observe A1.07 stop state                       (fail closed before work)
  -> assemble bounded deterministic instruction
     (task identity/objective/parent/priority/metadata + canonical bounds)
  -> Reasoner.reason(ReasonerRequest(...))          A2.03, exactly once
  -> observe A1.07 stop state again                 (discard plan after stop)
  -> bounded response-text check                    (planning_strategy.*)
  -> json.loads                                     (strict, one document)
  -> accept_model_proposal(root_task_id=task.task_id)   A6.01 validation
  -> Result.success(TaskDecomposition)              (inert plan candidate)
```

The A2.10 `attempt(...)` port never enters this flow: it reports
`StrategyResult.unavailable(...)` for every level, so a loop routed to L4
fails closed without consuming a model call, and canonical escalation then
fails closed at L5 where no execution strategy exists.

## Placement

`agentx.planning_strategy` lives at the `agentx` namespace root, next to the
A2.10 loop and the M1.02 capability strategy adapter: it consumes cognition
contracts (A2.03 Reasoner, A2.07 levels) and core data contracts (A1.07
context, A6.01 decomposition) without importing both sides of any forbidden
architecture edge, and it adds no subsystem edge. It imports no kernel,
capability, procedure, store, persistence, hive, learning, or research
module, and it adds zero third-party runtime dependencies.

## Coverage

- `tests/unit/test_planning_strategy.py` — bounded planning, malformed
  output, unknown fields, node/depth limits, hierarchy/dependency errors,
  duplicate identity, deterministic parsing, exactly-once Reasoner
  invocation, cancellation/deadline, provider failure propagation,
  no-transition/no-success-fabrication, fail-closed A2.10 port.
- `tests/integration/test_planning_strategy.py` — the strategy inside the
  real A2.10 loop: registered L4 planning can never produce loop success,
  executes nothing, and consumes no model call in the loop.
- `tests/adversarial/test_planning_strategy_authority.py` — hostile payloads
  cannot change Permission, ActionGate, Risk, budget, or EmergencyStop state,
  cannot transition the Task, and persist nothing.
- `tests/architecture/test_planning_strategy_boundaries.py` — exact import
  set, no shadow contracts, one canonical Reasoner call, no
  transition/persistence/execution surface, zero runtime dependencies.
