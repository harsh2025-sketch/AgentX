# Governed L4 plan execution

`GovernedPlanningStrategy` composes the existing inert `PlanningStrategy`
with `GovernedPlanExecutor`. Register the new adapter at `L4_PLANNED` using
the existing runtime strategy assembly. The old planner's `attempt()` remains
fail-closed; its data-only contract and architecture restrictions are unchanged.

The executor first applies canonical decomposition readiness. It expands group
dependencies to terminal descendants, inherits ancestor dependencies, rejects
semantic deadlocks, and orders ready leaves deterministically. All terminal
bindings are resolved before the first action. A composition-time binder must
return a typed capability request and a canonical verification requirement;
unknown objectives fail explicitly. Metadata never supplies authority or code.

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

Bindings support explicitly resolved `higher_level` leaves and capability UUID
references validated against the binder's trusted mapping. Procedure leaves
currently fail closed rather than bypassing procedure applicability/lifecycle
checks. Binders are trusted application composition, not model-supplied plugins;
they must not perform actions or parse natural-language authority claims.

## Evidence and remaining acceptance

`tests/integration/test_plan_execution.py` uses real filesystem read/write
capabilities, the real kernel and AgentLoop, and a scripted model provider.
It can run with `PYTHONPATH=src python -m unittest
tests.integration.test_plan_execution` without development dependencies.

This closes the missing plan-to-action composition path for explicitly bound
capabilities. It is not AX-083/084/085 acceptance, a general natural-language
binding implementation, L5, a live-model benchmark, or cold/learn/restart/warm
proof. Real-provider model resource accounting and application composition
remain required. No M1/M4 task is marked complete merely from these regressions.
