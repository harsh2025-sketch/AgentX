# Procedure Graph Interpreter (M3.01)

`agentx.procedures.interpreter` is the deterministic, bounded control-flow
machine that walks one canonical Procedure Graph
(`agentx.procedures.graph.ProcedureGraph`). It answers control questions —
*which node is current, what kind of node is it, what must happen externally
next, which transition follows an explicit result, has control terminated,
has the run failed structurally* — and it answers them as inert data.

It is **control-flow interpretation only**. It executes nothing.

## What it is not

- **Not an executor.** No capability is resolved, requested, or executed. An
  ACTION node produces an `ACTION_REQUIRED` instruction describing the
  canonical node and its opaque params; acting on that instruction is a
  future composition layer's job, and that layer must still route the action
  through the Trusted Kernel.
- **Not a reasoner or researcher.** REASON and RESEARCH nodes produce
  `REASON_REQUIRED` / `RESEARCH_REQUIRED` instructions. No model provider and
  no research provider is ever invoked.
- **Not a procedure loader.** SUBPROCEDURE nodes produce a
  `SUBPROCEDURE_REQUIRED` instruction carrying the node's inert reference
  payload. `ProcedureStore` is never touched.
- **Not a second condition evaluator.** BRANCH nodes produce a
  `BRANCH_DECISION_REQUIRED` instruction naming the outcomes declared by the
  node's canonical `BranchContract` and the candidate `NEXT` targets. How the
  caller decides is its own concern — the canonical single-condition
  evaluator (`agentx.procedures.condition_evaluation`) exists for exactly
  that, driven by explicit `EvidenceFacts`. The interpreter only validates
  and follows the explicit `BranchSelected` decision.
- **Not a verifier and not task success.** Reaching a canonical END node
  yields the `PROCEDURE_CONTROL_TERMINATED` terminal state and nothing more.
  The interpreter never transitions a Task, never creates a
  `VerificationResult`, never creates a `ClosedLoopOutcome`, and never claims
  the user's goal was satisfied or that external state is correct. Invariant
  I1 stays absolute: *no action == success without canonical verification*.

## State machine

State is explicit and immutable (`ProcedureInterpreterState`, frozen,
slots-only). `InterpreterStatus` is the finite vocabulary:

| Status | Meaning |
| --- | --- |
| `AWAITING_ACTION_RESULT` | current node is ACTION; an explicit external result is required |
| `AWAITING_OBSERVATION_RESULT` | current node is OBSERVE |
| `AWAITING_VERIFICATION_RESULT` | current node is VERIFY |
| `AWAITING_BRANCH_SELECTION` | current node is BRANCH; an explicit `BranchSelected` decision is required |
| `AWAITING_TRANSFORM_RESULT` | current node is TRANSFORM (the transform contract names an operation, not an implementation, so execution stays external) |
| `AWAITING_REASON_RESULT` | current node is REASON |
| `AWAITING_RESEARCH_RESULT` | current node is RESEARCH |
| `AWAITING_WAIT_RESULT` | current node is WAIT (the interpreter never sleeps) |
| `AWAITING_ROLLBACK_RESULT` | current node is ROLLBACK (rollback intent stays data) |
| `AWAITING_SUBPROCEDURE_RESULT` | current node is SUBPROCEDURE |
| `TERMINATED` (`procedure_control_terminated`) | control reached a canonical END node — control completion only, never task success |
| `FAILED` (`procedure_control_failed`) | the run failed structurally or exhausted its bound; `failure_reason` says exactly why |

`InterpreterFailureReason` members: `STEP_LIMIT_EXCEEDED`,
`NO_RECOVERY_ROUTE`, `MISSING_NEXT_EDGE`, `AMBIGUOUS_NEXT_EDGE`.

## Step API

```python
interpreter = ProcedureInterpreter(graph=graph, recovery=recovery, max_steps=64)

state = interpreter.start()  # entry node, steps_taken == 0
instruction = interpreter.instruction(state)  # inert *_REQUIRED description
state = interpreter.advance(state, result)  # exactly one transition
```

`advance` accepts exactly one explicit result value:

- `StepCompleted(node_kind=...)` — the caller states the required external
  step completed. The claimed kind must match the current node exactly.
  Progression follows the node's single outgoing `NEXT` edge; zero edges is
  the explicit `MISSING_NEXT_EDGE` failure, more than one is the explicit
  `AMBIGUOUS_NEXT_EDGE` failure (a deterministic interpreter never guesses).
- `StepFailed(node_kind=..., failure=FailureCategory...)` — explicit,
  canonical failure evidence. Routing reads only the canonical A3.07
  `ProcedureRecoveryEdges` document (cross-checked against the graph at
  construction): a declared `(node, category)` route is followed; no declared
  route is the explicit `NO_RECOVERY_ROUTE` failure. Failures are never
  guessed from text, never coerced (`UNKNOWN` matches only itself), and never
  rerouted onto a `NEXT` edge.
- `BranchSelected(outcome=..., target=...)` — the caller's explicit branch
  decision. The outcome must be declared by the node's canonical
  `BranchContract` and the target must be one of the node's `NEXT`
  successors; anything else fails closed.

Advancing a terminal state, supplying a wrong result type, or supplying a
forged/inconsistent state raises `ProcedureInterpreterError` (a
`ProcedureGraphError` subclass) — request-shape errors are exceptions,
run-level failures are explicit `FAILED` states, and no failure is ever
swallowed into a false success.

## Bounds

Interpretation is always finite:

- `max_steps` (default `DEFAULT_MAX_STEPS = 256`) is validated at
  construction: zero, negative, boolean, non-integer, or anything above the
  hard ceiling `MAX_STEP_LIMIT = 10_000` fails closed.
- Cycles are legal canonical graph structure, but crossing the ceiling
  produces the explicit `STEP_LIMIT_EXCEEDED` failure state — never an
  unbounded walk. There is no recursion and no unbounded loop in the module.
- The interpreter owns only its own local procedure-step ceiling; it does not
  reuse the cognition LoopGuard.

## Determinism

`advance` is a pure function of `(graph, recovery, max_steps, state,
result)`: identical inputs always produce an equal state. There is no
randomness, no wall-clock branching, no environment inspection, no model
call, and no global state. All values are frozen dataclasses; node params are
exposed through read-only mappings.

## Authority boundary

Procedure data is DATA. Hostile node content — `permission=ADMIN`,
`risk=R0`, `verified=true`, `task succeeded`, `skip ActionGate`, `clear
emergency stop` — is carried verbatim inside instructions as inert data and
can never change what the interpreter does: no state, instruction, or result
field carries permission, risk, budget, approval, or verification meaning. A
`ProcedureGraph` never authorizes its own actions; the interpreter creates no
authority token, lowers no risk, and approves nothing.

The module imports only the standard library subset it uses, the canonical
procedure DATA contracts (`graph`, `branch`, `recovery`), and the canonical
core failure vocabulary — consistent with the canonical architecture manifest
for `agentx.procedures`. It never imports `agentx.capabilities`,
`agentx.cognition`, `agentx.learning`, `agentx.hive`, or
`agentx.infrastructure`, and contains no subprocess, network, filesystem,
shell, database, timer, thread, or dynamic-execution surface. These
guarantees are pinned by `tests/architecture/
test_procedure_interpreter_boundaries.py`.

## Relationship to later tasks

The interpreter deliberately produces no trace/evidence artifact (M3.02), no
synthesized candidates (M4.01), and no candidate validation (M4.02). It
depends only on canonical baseline contracts.
