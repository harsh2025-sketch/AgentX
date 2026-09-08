# L2 Compiled-Procedure Strategy Adapter

`agentx.compiled_procedure_strategy` is the top-level composition adapter for
`ExecutionLevel.L2_COMPILED`. It lets A2.10 attempt one explicitly selected,
reasoning-free compiled procedure while preserving the architecture rule:

```text
NO PROCEDURES -> CAPABILITIES
```

The adapter does not make the procedure subsystem capable of execution. It
composes the already-canonical interpreter and governed execution machinery at
the top-level `agentx` boundary.

## Legal execution direction

The runtime direction is:

```text
A2.10 / top-level strategy
    -> canonical ProcedureInterpreter
    -> inert ACTION_REQUIRED instruction
    -> explicitly pre-bound canonical CapabilityRequest
    -> canonical Executor
    -> canonical CapabilityExecutionLoop
       -> permission evaluation / ActionGate / EmergencyStop / budget
       -> capability execute
       -> capability verify
    -> canonical ClosedLoopOutcome
    -> StepCompleted only after LoopOutcome.VERIFIED
    -> interpreter continuation
```

The procedure interpreter never imports or invokes a capability. The adapter
never creates a second interpreter, Executor, Verifier, ActionGate, capability
registry, permission engine, risk engine, or execution loop.

## Explicit selection and eligibility

`CompiledProcedureStrategyBinding` represents one explicit caller selection.
Normal L2 execution is accepted only when all of the following are true:

1. The selected value is a canonical `ProcedureCandidate` containing a
   `ProcedureRecord` whose lifecycle status is exactly `ProcedureStatus.ACTIVE`.
2. The payload is inline `ProcedurePayloadKind.CANONICAL_JSON` and decodes as a
   canonical `ProcedureGraph`.
3. The canonical M4.04 `ProcedureApplicabilityMatcher` reports the selected
   candidate structurally applicable to the caller-supplied
   `ProcedureRequirement` (`EXACT_MATCH` or `COMPATIBLE`).
4. The graph is reasoning-free for this adapter: its nodes are canonical
   ACTION/END nodes with valid A3 node-family contracts, and canonical
   interpreter traversal of the successful path reaches END.
5. Every ACTION node has exactly one caller-supplied canonical
   `CapabilityRequest`, and the request's capability name/version exactly match
   the inert ACTION description.

CANDIDATE and RETIRED revisions are rejected. A matching result does not grant
execution authority: M4.04 remains a structural applicability assessment only.
An artifact-reference payload is not dereferenced by this strategy and fails
closed; resolving an artifact is separate integration work, not an excuse to
add persistence or dynamic loading here.

## Why ACTION params are not executable

`ActionNodeSpec.params` is hostile/inert procedure data. N2.04 deliberately does
not convert arbitrary node dictionaries into typed capability parameters. The
caller binds a real canonical `CapabilityRequest` at the top-level composition
boundary before execution. The adapter checks only the canonical action
identity/version relationship and passes that pre-bound request to the
Executor.

This keeps strings such as these inert:

```text
permission=ADMIN
risk=R0
skip_action_gate=true
verified=true
task_success=true
clear_emergency_stop=true
```

They cannot create an `AuthorityContext`, lower risk, widen the resource
budget, bypass the ActionGate, clear the monotonic EmergencyStop, or mark the
original task successful. Actual authority is whatever the composition root
already supplied to the canonical `CapabilityExecutionLoop` used by the
injected Executor.

## ACTION_REQUIRED handling

For an ACTION instruction, the adapter creates the same kind of fresh governed
sibling Task/context used by the existing top-level capability strategy, then
calls `Executor.execute(...)` exactly once with the explicitly bound request.
The original A2.10 Task remains under A2.10/TaskManager lifecycle ownership.

Interpreter control advances only when the canonical capability outcome is
`LoopOutcome.VERIFIED`. A denial, execution failure, or verification failure is
recorded and terminates the procedure attempt without advancing to END. There
is no retry, model call, planner call, research call, or hidden L3/L4/L5
fallback in this adapter.

## Evidence and the END invariant

The adapter records canonical M3.02 `ProcedureRunRecord`/
`ProcedureStepRecord` evidence using the caller's correlation identity. A
verified ACTION step has explicit verification evidence. Reaching the canonical
END node produces:

```text
ProcedureRunDisposition.REACHED_END
```

and nothing more.

The procedure run leaves:

```text
ProcedureTaskVerification.NOT_ASSESSED
```

unless some separate canonical task-verification authority records otherwise.
N2.04 never sets `TASK_VERIFIED`.

Therefore:

```text
END != original Task success
capability execution return != verification
capability verification != original Task verification
```

When used by A2.10, the returned canonical `ClosedLoopOutcome` is still passed
through A2.10's independent A2.05 `VerificationRequirement` evaluation. A
capability can be canonically verified and the ProcedureGraph can reach END
while the original Task still fails its own requirement. Conversely, A2.10 may
only report success after that separate task-level verification gate is
satisfied.

## Tests

The N2.04 owned tests pin:

- ACTIVE/applicable selection and lifecycle rejection;
- wrong-level and malformed-procedure fail-closed behavior;
- canonical M4.04 applicability mismatch;
- top-level ACTION_REQUIRED dispatch through the real Executor/runtime;
- ActionGate denial and capability verification failure;
- explicit END without original-task success;
- successful capability execution with failed original-task verification;
- successful independent A2.10 task verification;
- hostile procedure strings remaining inert against authority, stop and budget;
- absence of direct Procedure-to-Capability imports, duplicate execution or
  verification engines, model calls, escalation, and architecture-manifest
  shortcuts.
