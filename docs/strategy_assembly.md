# N2.02 — Canonical Runtime Strategy Assembly

`agentx.strategy_assembly` is the top-level composition boundary that turns an
explicit set of A2.10 execution strategies into the canonical
`agentx.agent_loop.StrategyRegistry`.

It is configuration only. It is not a Router, strategy implementation,
fallback engine, authority boundary, executor, verifier, or persistence layer.

## Public contract

### `StrategyBinding`

An immutable pair of:

- one canonical `ExecutionLevel`; and
- one object satisfying the existing A2.10 `ExecutionStrategy` shape by
  providing a callable `attempt(...)` method.

The existing generic `ExecutionStrategy` protocol does not declare a level
attribute. Therefore the binding's canonical `ExecutionLevel` is the explicit
composition identity. The assembly does not infer a level from a class name,
strategy name, prompt, metadata, or other free-form field.

### `RuntimeStrategyAssembly`

Construction accepts explicit `StrategyBinding` values and validates them
before creating one canonical `StrategyRegistry`.

The assembly:

- permits an empty strategy set;
- permits any subset of the six canonical levels;
- rejects duplicate level bindings;
- rejects malformed bindings and strategies;
- rejects binding the same strategy object to multiple levels because the
  generic strategy protocol contains no contract declaring multi-level reuse
  safe;
- normalizes exposed bindings to canonical L0-L5 order for deterministic
  inspection;
- keeps absent levels absent;
- exposes the exact immutable bindings, present levels, canonical registry and
  per-level lookup; and
- invokes no strategy during assembly.

The canonical levels are reused directly from `agentx.cognition.router`:

1. `L0_CACHE`
2. `L1_DIRECT`
3. `L2_COMPILED`
4. `L3_GUIDED`
5. `L4_PLANNED`
6. `L5_EXPLORATORY`

No second enum or alternate registry is introduced.

## Duplicate inspection

At baseline `f6f7b8750b4a3ded126e23456bc92e11064a5752`:

- A2.10 already owns the immutable `StrategyRegistry` and consumes caller-bound
  `ExecutionStrategy` values;
- callers and tests construct `StrategyRegistry` directly;
- `GovernedCapabilityStrategy` is an L1 strategy implementation, not a runtime
  strategy-set assembly;
- the procedure interpreter and applicability matcher are separate procedure
  concerns and are not A2.10 strategy assembly; and
- no canonical `strategy_assembly.py`, `RuntimeStrategyAssembly`, or equivalent
  top-level strategy-set composition object exists.

N2.02 therefore composes the existing registry rather than replacing it.

## Absence and fallback

Missing levels are valid configuration. If only L1 and L4 are bound, L0, L2,
L3 and L5 remain absent from both `levels` and registry lookup.

N2.02 never creates a default strategy and never treats a missing level as a
request to route, retry, escalate, fall back, plan or explore. Those decisions
remain with their existing canonical owners.

## Authority boundary

Strategy assembly is inert configuration data. Constructing or inspecting it
does not:

- create or widen an `AuthorityContext`;
- grant a `Permission`;
- invoke or bypass `ActionGate`;
- lower a `RiskLevel`;
- consume or enlarge a `ResourceBudget`;
- clear `EmergencyStop`;
- execute a capability;
- execute or activate a Procedure;
- call a model;
- transition a `Task`;
- manufacture or alter verification success; or
- read from or write to persistence.

Free-form values on a strategy such as `permission=ADMIN`, `risk=R0`,
`verified=true`, or `skip_gate=true` have no authority meaning at this boundary.
The assembly does not inspect those values.

## Immutability and dependencies

`StrategyBinding` and `RuntimeStrategyAssembly` are immutable after
construction. The assembly stores a tuple of validated bindings and one
canonical `StrategyRegistry`, whose own storage is already immutable.

The production module adds no runtime dependency. It imports only standard
library composition helpers plus the existing A2.10 registry/protocol and
canonical execution-level contracts.
