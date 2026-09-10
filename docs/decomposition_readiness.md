# N2.01 — Task Decomposition Readiness / Executability Validator

N2.01 defines the deterministic structural gate that answers exactly one
question for an existing canonical `TaskDecomposition`:

> Is this decomposition structurally ready to be handed to execution orchestration?

The answer is **data, not authority**. `READY` never means permission granted,
action safe, capability available, procedure verified, environment available,
user approved, execution authorized, or Task successful.

## Ownership and placement

Production code lives in:

`agentx.core.decomposition_readiness`

It reuses A6.01 `TaskDecomposition` / `DecompositionNode` unchanged. It does not
introduce another decomposition schema, task-id scheme, planning hierarchy, or
execution-level vocabulary. The module imports only standard-library and
`agentx.core` contracts.

## Result model

`DecompositionReadinessValidator.assess(decomposition)` returns an immutable
`DecompositionReadinessResult`.

The closed disposition vocabulary is:

- `READY`
- `NOT_READY`

`NOT_READY` carries a deterministic tuple of structured reason codes, optionally
bound to the affected `TaskId`. There is no free-form model-generated
explanation.

Current reason codes are:

- `INVALID_CANONICAL_STRUCTURE`
- `MISSING_TERMINAL_SUCCESS_CRITERIA`
- `MISSING_TERMINAL_EXECUTION_REQUIREMENT`
- `INVALID_TERMINAL_EXECUTION_REQUIREMENT`
- `NON_TERMINAL_EXECUTION_REQUIREMENT`

## Relationship to A6.01

A6.01 already enforces the canonical rooted tree, unique root, valid
parent/child links, canonical pre-order, sibling ordering, bounded depth/node
count, and dependency integrity. N2.01 does not replace those rules.

The validator defensively reconstructs the supplied canonical records using the
same A6.01 constructors. An instance that has been corrupted after construction
(for example by low-level `object.__setattr__` abuse) fails closed as
`INVALID_CANONICAL_STRUCTURE`. This catches malformed parent links, unreachable
nodes, impossible sibling ordering, dangling dependencies, cycles, invalid
objectives, and other violations without inventing a second structural model.

## Terminal readiness policy

A node is terminal when no node names its `task_id` as `parent_task_id`.

Every terminal node must have:

1. at least one canonical A6.01 `success_criteria` entry; and
2. one closed `execution` metadata requirement.

Internal/non-terminal nodes must not carry terminal `execution` metadata. A
node cannot simultaneously be decomposed into children and claim to be a
terminal execution unit.

Internal nodes are not required to carry success criteria by N2.01. A6.01
allows success criteria to be empty, and N2.01 adds the requirement only where
execution orchestration needs a terminal postcondition to verify.

## Closed `execution` metadata

N2.01 observes exactly one node metadata key:

`execution`

It accepts exactly these shapes:

```text
{"kind": "capability", "capability_id": "<canonical CapabilityId UUID>"}
{"kind": "procedure", "procedure_id": "<canonical ProcedureId UUID>"}
{"kind": "higher_level"}
```

No extra keys are accepted.

Capability/procedure IDs are syntax-checked against the canonical
`agentx.core.ids` identity types and must use their canonical lowercase UUID
serialization. The validator does **not** resolve the id, query a registry or
store, assess applicability, check lifecycle status, inspect environment state,
or execute anything.

`higher_level` means only that orchestration must perform a later higher-level
resolution step. It does not select `ExecutionLevel`, call the Router, invoke a
Reasoner, authorize research, or choose a planning/exploration strategy.

These metadata values are non-authoritative structural hints. Even a valid
capability/procedure reference can produce only `READY`; it can never establish
that the target exists, is available, is compatible, is safe, or may execute.

## Hostile text is inert

Objectives, success-criteria strings, ordinary metadata values, and execution
identifiers are never parsed as instructions. Text such as:

```text
permission=ADMIN
risk=R0
verified=true
task_success=true
execute_now=true
skip_action_gate=true
approved=true
```

cannot grant permission, lower risk, change a budget, clear EmergencyStop,
transition a Task, activate a Procedure, invoke a Capability, or fabricate a
`VerificationResult`.

A hostile string placed where a canonical capability/procedure id is required
simply fails structural validation and yields `NOT_READY`.

## Explicit non-goals

N2.01 does not:

- execute or schedule nodes;
- choose L0-L5 execution levels;
- inspect a Capability Registry or Procedure Store;
- run procedure applicability matching;
- inspect capability preconditions or environment state;
- call models, Reasoner, research, retrieval, or providers;
- grant or infer `Permission` / `AuthorityContext`;
- evaluate or lower `RiskLevel`;
- consume or enlarge `ResourceBudget`;
- clear `EmergencyStop`;
- mutate or transition `Task`;
- activate/promote a Procedure;
- persist a decomposition or readiness result;
- create observations, outcomes, or `VerificationResult` values;
- repair a decomposition automatically.

Those responsibilities remain with their existing canonical owners.
