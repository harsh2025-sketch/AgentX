# Governed L4 plan execution

`GovernedPlanningStrategy` composes the existing inert `PlanningStrategy`
with `GovernedPlanExecutor`. Register the new adapter at `L4_PLANNED` using
the existing runtime strategy assembly. The old planner's `attempt()` remains
fail-closed; its data-only contract and architecture restrictions are unchanged.

The executor first applies canonical decomposition readiness. It expands group
dependencies to terminal descendants, inherits ancestor dependencies, rejects
semantic deadlocks, and orders ready leaves deterministically. All terminal bindings are resolved before the first action. Capability leaves
are resolved by a composition-owned `ApplicationActionBinder`, which maps an
exact canonical `CapabilityId` to a pre-bound request and verification
requirement. Procedure leaves are resolved separately by
`PreparedProcedureBinder` to `BoundPlanProcedure` values backed by the
canonical `GovernedCompiledProcedureStrategy`. Unknown capability/procedure ids
fail explicitly before mutation. Objectives and metadata never become executable
parameters, authority, or code.

Every request runs through the same canonical Executor, retaining kernel
permission, gate, budget, emergency-stop, audit, and independent capability
verification. A failed/denied step stops the plan and preserves its canonical
outcome. No retry, recursive planning, new budget, or compensating action is
implicit. Cancellation/deadline are checked before binding and around actions.
The configurable request ceiling includes the separate final goal check;
actual machine-action accounting remains the kernel's responsibility.

After the steps pass, a separately bound goal-check request executes through
the same governed path. Its evidence must meet the caller's explicit goal
requirement. The exact canonical result is returned to AgentLoop, which still
owns task-level success and rechecks its own independent requirement. Structural
group completion never creates a Task success or VerificationResult.

Bindings support explicitly resolved `higher_level` leaves, exact capability
UUID references, and exact procedure UUID references. Procedure execution does
not bypass its lifecycle: the compiled strategy binding requires an ACTIVE
record, parses the canonical Procedure Graph, checks applicability, validates
each pre-bound action identity, and executes every action through the canonical
Executor. RETIRED/CANDIDATE, malformed, mismatched, unknown, or unprepared
procedures therefore fail closed. Binders are trusted application composition,
not model-supplied plugins; they do not perform actions or parse
natural-language authority claims.

## Evidence and remaining acceptance

`tests/integration/test_plan_execution.py` uses real filesystem read/write
capabilities, the real kernel and AgentLoop, and a scripted model provider.
It can run with `PYTHONPATH=src python -m unittest
tests.integration.test_plan_execution` without development dependencies.

PR #163 extends this proof with
`tests/integration/test_plan_procedure_composition.py` for procedure-backed L4
leaves and `tests/integration/test_m1_closure.py` for typed application binding
and bounded L5 composition. The task-level M1 acceptance record is
`docs/m1_acceptance.md`. These deterministic tests are not represented as
live-model, live-web, interactive-desktop, or cold/learn/restart/warm
experiments; those remain separate later-milestone evidence requirements.
