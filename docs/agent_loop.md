# A2.10 — Minimal End-to-End Agent Execution Loop

Status: implemented.
Owner: A2.10.
Canonical module: `agentx.agent_loop` (`src/agentx/agent_loop.py`).

A2.10 is the first end-to-end AgentX orchestration loop. It is a
**composition layer only**: it owns control flow and nothing else. Every
decision it makes is delegated to the canonical contract that already owns
that decision, and A2.10 only sequences those decisions and carries explicit
typed evidence between them.

It is not a planner, executor, verifier, router, escalation table, anti-loop
engine, budget system, task-state machine, model router, or research engine.

---

## 1. Architectural placement

### The problem

A2.10 must consume both sides of the runtime:

| Need | Canonical owner | Subsystem |
| --- | --- | --- |
| Task lifecycle (A2.06) | `TaskManager` | `agentx.cognition` |
| Route selection (A2.07) | `ExecutionLevelRouter` | `agentx.cognition` |
| Escalation policy (A2.08) | `ExecutionLevelEscalator` | `agentx.cognition` |
| Anti-loop bounds (A2.09) | `LoopGuard` | `agentx.cognition` |
| Governed execution (A1.10) | `CapabilityExecutionLoop` | `agentx.capabilities` |
| Outcome contracts (A1.10) | `ClosedLoopOutcome`, `LoopOutcome` | `agentx.capabilities` |
| Requirement evaluation (A2.05) | `Verifier` | `agentx.capabilities` |

`ALLOWED_ARCHITECTURE_EDGES` in `agentx._architecture` contains **no**
`(COGNITION, CAPABILITIES)` edge, and in fact contains **no inbound edge to
`COGNITION` at all**. Therefore *no existing canonical subsystem may legally
import both sides.*

### The ruling

The Technical Lead ruled that the edge must **not** be added, and that the
loop must be placed in the narrowest existing layer that may legally consume
both, introducing the smallest possible non-authority composition boundary if
none exists — without creating a new top-level subsystem.

### The resolution

The loop is placed at **`agentx.agent_loop`** — a single top-level module in
the `agentx` namespace root, alongside the existing top-level non-subsystem
modules `__main__.py`, `_architecture.py`, and `_version.py`.

This is correct rather than a loophole, for four reasons.

1. **`agentx` is explicitly not a subsystem.** The canonical boundary checker
   states it directly: *"`agentx.kernel` and `agentx.kernel.policy` are owned
   by `agentx.kernel`; `agentx` itself is not a subsystem."* `_owning_subsystem`
   returns `None` for the namespace root, and `_subsystem_files` only walks
   `src/agentx/<subsystem>/`. A top-level module is outside every subsystem,
   so it is bound by no subsystem edge.

2. **A composition root belongs outside the layers it composes.** The whole
   purpose of the manifest is to stop *subsystems* from entangling with one
   another. Wiring is the one job that cannot live inside either side without
   creating exactly the entanglement the manifest forbids. The application
   entry point (`__main__.py`) already occupies this level.

3. **It is the smallest possible boundary.** One module. No new package, no
   new directory, no new top-level subsystem, no manifest change, no
   `SUBSYSTEMS`/`BOUNDARY_PACKAGES` change.

4. **It preserves the invariant that actually matters.** `agentx.cognition`
   still does not import `agentx.capabilities`. That property is now pinned by
   a dedicated regression test rather than being incidentally true.

### What was explicitly rejected

* Adding `(COGNITION, CAPABILITIES)` to the manifest — forbidden by the ruling.
* Copying `ClosedLoopOutcome` / `LoopOutcome` into cognition — would duplicate
  canonical contracts.
* Locally-invented `Protocol` substitutes for canonical capability contracts —
  would weaken canonical type checks and create a second, softer definition of
  verification evidence.
* Duplicating `CapabilityExecutionLoop` or `Verifier` — would create a second
  verification mechanism.
* Moving authority or verification semantics into cognition.
* A new top-level subsystem/package for A2.10 alone.

---

## 2. Public API

```python
from agentx.agent_loop import (
    AGENT_LOOP_SOURCE,  # "agentx.agent_loop"
    AgentLoop,  # the composition loop
    AttemptDisposition,  # per-attempt classification
    AttemptRecord,  # immutable evidence for one attempt
    ExecutionStrategy,  # caller-supplied port (Protocol)
    OrchestrationLimits,  # explicit bounds
    OrchestrationOutcome,  # structured final result
    OrchestrationRequest,  # typed request
    OrchestrationRequestError,
    OrchestrationStatus,  # SUCCEEDED/FAILED/CANCELLED/TIMED_OUT/EXHAUSTED
    OrchestrationStopReason,
    StrategyRegistry,  # immutable level -> strategy map
    StrategyResult,  # executed(...) | unavailable(...)
)
```

Construction and invocation:

```python
loop = AgentLoop(
    task_manager=task_manager,  # A2.06, required
    strategies=StrategyRegistry({ExecutionLevel.L1_DIRECT: my_adapter}),
    router=None,  # defaults to canonical A2.07 router
    escalator=None,  # defaults to canonical A2.08 escalator
    loop_guard=None,  # defaults to canonical A2.09 guard
    verifier=None,  # defaults to canonical A2.05 verifier
)

result = loop.run(request, clock=None)  # Result[OrchestrationOutcome, AgentXError]
```

`AgentLoop` is stateless across runs. Collaborators are injected but are
**type-checked against the canonical classes**, so a permissive stand-in
cannot be substituted for the router, escalator, guard, or verifier.

---

## 3. Control flow

```
run(request)
  │
  ├─ validate request (task PENDING, context.task_id matches, task registered)
  ├─ observe A1.07 stop state ─── cancelled/expired ──► CANCELLED / TIMED_OUT
  │                                                     (task -> CANCELLED)
  ├─ route initial level via A2.07 ExecutionLevelRouter
  ├─ transition task PENDING -> RUNNING via A2.06 TaskManager
  │
  └─ for attempt_index in range(limits.max_total_attempts):        ← bounded
        │
        ├─ observe A1.07 stop state ── stop ──► CANCELLED / TIMED_OUT
        ├─ look up strategy for current level
        │     └─ absent ──► STRATEGY_UNAVAILABLE (fail closed; L5 included)
        ├─ strategy.attempt(task, context, level)   ← governed path only
        ├─ classify the canonical outcome:
        │     ├─ resource exhaustion   ──► terminal, fail closed
        │     ├─ safety stop           ──► terminal, task CANCELLED
        │     ├─ VERIFIED + verdict passed + task SUCCEEDED
        │     │     └─ A2.05 Verifier evaluates the explicit requirement
        │     │           └─ satisfied ──► VERIFIED_SUCCESS
        │     └─ anything else         ──► UNVERIFIED
        │
        ├─ VERIFIED_SUCCESS ──► task -> SUCCEEDED, return SUCCEEDED   ← only exit
        │
        ├─ append AttemptRecord to history
        ├─ A2.09 LoopGuard.evaluate(history, limits)
        │     └─ STOP_LOOP ──► EXHAUSTED / ANTI_LOOP (not success, no escalation)
        └─ A2.08 Escalator.decide(evidence)
              ├─ ESCALATE  ──► next canonical level, continue
              ├─ STAY      ──► same level, continue
              └─ EXHAUSTED ──► EXHAUSTED / ESCALATION_EXHAUSTED
        │
  └─ loop fell through ──► FAILED / ATTEMPT_CEILING
```

The attempt loop is a `for` over `range(...)`. There is no `while` loop
anywhere in the module, so unbounded iteration is impossible by construction —
even if every collaborator repeatedly requests escalation or retry.

---

## 4. Invariant I1 — no action == success without verification

> **NO ACTION == SUCCESS WITHOUT VERIFICATION.**

`OrchestrationStatus.SUCCEEDED` is produced in exactly one place, guarded by
the single predicate `_verified_success(outcome, evaluation)`, which requires
**all** of:

1. the strategy returned a canonical A1.10 `ClosedLoopOutcome`
   (an `isinstance` check against the real canonical class);
2. `outcome.kind is LoopOutcome.VERIFIED`;
3. `outcome.verification` is a canonical `VerificationResult` with
   `passed is True`;
4. `outcome.task.status is TaskStatus.SUCCEEDED`;
5. the canonical A2.05 `Verifier` evaluated that same outcome against the
   caller's explicit `VerificationRequirement` and returned `satisfied=True`.

None of the following can satisfy that guard, and each has a dedicated
adversarial test:

* a capability returning `succeeded=True`;
* an observation existing;
* no exception being raised;
* a strategy reporting completion;
* a procedure reaching END;
* model/observation/error text containing `success`, `verified=true`,
  `ignore verifier`, or `permission=ADMIN`;
* a forged `ClosedLoopOutcome` with a passing verdict but a non-`SUCCEEDED`
  task, or a `SUCCEEDED` task with no verdict, or a `DENIED` kind carrying a
  passing verdict.

The guard reads only typed canonical fields — enum identity and booleans —
never text. A2.05 equality is type-strict, so `"true"` is not `True` and `1`
is not `True`.

`OrchestrationOutcome.__post_init__` additionally enforces the invariant
structurally: a `SUCCEEDED` status is rejected at construction unless a
verified attempt record backs it and the task is terminal-`SUCCEEDED`.

---

## 5. Strategy ports (the L0–L5 limitation)

The repository has **no** concrete L0/L2/L3/L4/L5 strategy implementations, and
A2.10 must not fabricate them (no fake cache, no compiled-procedure runner, no
guided reasoning, no planner, no exploratory research).

Levels are therefore served by explicit caller-supplied `ExecutionStrategy`
adapters held in an immutable `StrategyRegistry`. An adapter is **subordinate,
never an authority**:

* it returns a canonical `Result[ClosedLoopOutcome, AgentXError]` produced by
  the governed path (A2.04 `Executor` over the A1.10 `CapabilityExecutionLoop`),
  or an explicit unavailability reason;
* `StrategyResult` has exactly two fields — `outcome` and `unavailable_reason`
  — so there is **no field through which an adapter can assert success**;
* whatever it returns is re-checked here against canonical evidence and the
  canonical A2.05 Verifier.

A level with no registered adapter fails closed as `STRATEGY_UNAVAILABLE`.
**That includes `L5_EXPLORATORY`: reaching L5 authorizes nothing.** The module
never browses, researches, plans, or invokes a model because escalation
arrived at L5.

---

## 6. Bounds and safety

| Concern | Owner | A2.10 behaviour |
| --- | --- | --- |
| Repeated attempts | A2.09 `LoopGuard` | Builds explicit history, honours `STOP_LOOP` verbatim. `STOP_LOOP` is a stop — never success, never escalation authorization. |
| Total attempts | A2.10 `OrchestrationLimits` | Hard `for`-range ceiling (max 64), independent of A2.09. |
| Escalation | A2.08 | One canonical step at a time. No de-escalation, no skipping, no second escalation table, no wrap past L5. |
| Resources | C1.08 | Sole authority. A2.10 never constructs, resets, widens, or consumes an envelope; it only observes exhaustion and fails closed. |
| Cancellation / deadline | A1.07 | Observed deterministically before the first attempt, before each later attempt, and after each attempt. Never converted to success. No sleeping, polling, threads, or background work. |
| Authority | C1.07 / C1.09 | A2.10 imports **no kernel module at all**. It cannot grant a permission, lower risk, bypass the ActionGate, manufacture budget, or clear an EmergencyStop. |
| Events / audit | A1.10 | All canonical events and audit records are emitted by the governed path. A2.10 publishes nothing and defines no parallel taxonomy. |

Determinism: no randomness, no wall-clock reads (the clock is injected), no
hidden global state, no ambient singleton, no module-level mutable state.
Identical deterministic inputs always produce identical decisions.

---

## 7. Structured results

`OrchestrationOutcome` carries the full evidence chain:

| Field | Meaning |
| --- | --- |
| `task` | final canonical Task (always terminal) |
| `status` | `SUCCEEDED` / `FAILED` / `CANCELLED` / `TIMED_OUT` / `EXHAUSTED` |
| `stop_reason` | `VERIFIED`, `CANCELLED`, `DEADLINE_EXPIRED`, `RESOURCE_EXHAUSTED`, `SAFETY_STOP`, `ANTI_LOOP`, `ESCALATION_EXHAUSTED`, `ATTEMPT_CEILING`, `STRATEGY_UNAVAILABLE` |
| `initial_level` / `final_level` | canonical A2.07 levels |
| `attempts` | immutable tuple of `AttemptRecord` |
| `error` | structured `AgentXError` on non-success paths |

Each `AttemptRecord` retains its level, disposition, the canonical
`ClosedLoopOutcome`, the A2.05 `RequirementEvaluation`, any error, and the
A2.09/A2.08 decisions that followed it. Failures are preserved as failures and
are never reinterpreted.

Error codes are namespaced `agent_loop.*`:
`task_not_registered`, `task_not_pending`, `cancelled`, `deadline_expired`,
`strategy_unavailable`, `strategy_outcome_malformed`, `resource_exhausted`,
`safety_stop`, `anti_loop_stop`, `escalation_exhausted`,
`attempt_ceiling_reached`.

---

## 8. Deliberate non-scope

No A3 interpreter, autonomous research, Hive learning, skill compilation,
repair engine, planning, model/provider selection, prompts, model-text
inspection, Windows or browser behaviour, voice, self-extension, EventBus
redesign, persistence schema, background daemon, scheduler, or UI. Those
belong to later ledger tasks.

A2.10 adds **zero** runtime dependencies.
