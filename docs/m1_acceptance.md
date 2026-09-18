# M1 governed single-task runtime acceptance

This document is the focused acceptance record for Milestone M1 on PR #163.
It audits the canonical AX-041 through AX-085 identities without rewriting the
historical `reported_status` baseline. Acceptance is evidence-based and is
separate from the imported historical status counts.

## Scope and starting point

- Repository: `harsh2025-sketch/AgentX`
- Starting canonical main: `294cf9029e63d91b476971db79133e09264f2087`
- Closure PR: #163, `m1-governed-runtime-closure`
- Existing PR #161 L4 work was preserved rather than duplicated.
- First complete implementation-head Windows gate: run 35310694059 at
  `5a0c7732cf624d121d947cbc94598bdfa1e61ea7`: 9,972 passed, 6 skipped;
  runtime-only install, Ruff lint/format and strict mypy all passed.
- The PR must still finish with a green C1.01 run on its final exact head after
  ledger/documentation changes; the PR body records that final run.

## Architecture accepted

M1 now has an integrated bounded L0-L5 vocabulary. L0-L3 keep their existing
canonical strategy adapters. L4 preserves the separation introduced by #161:
`PlanningStrategy` produces only inert validated decomposition data, while
`GovernedPlanningStrategy` and `GovernedPlanExecutor` explicitly bind and
execute leaves through canonical governed runtime paths. General capability
leaves may be resolved only through `ApplicationActionBinder`, which maps exact
typed `CapabilityId` values to composition-owned `BoundPlanAction` values and
never converts objective/model text into executable parameters.

L4 procedure leaves now bind separately through `PreparedProcedureBinder` to a
`BoundPlanProcedure` containing the existing `GovernedCompiledProcedureStrategy`.
The compiled binding itself requires an ACTIVE procedure, validates the canonical
graph and applicability contract, and dispatches every action through the
canonical Executor. Unknown/unprepared procedures remain fail-closed. Successful
procedure leaves still do not establish root-task success; the independent
governed goal check remains mandatory.

L5 is implemented as `GovernedExploratoryStrategy`. It performs Hive-first
assessment against explicit typed knowledge requirements. A real gap may dispatch
exactly one pre-bound `GovernedResearchAcquisitionCapability` request through the
canonical Executor. The shared ResourceBudget consumes a research-query unit
before provider invocation. A LOCAL provider can be used for deterministic CI.
An EXTERNAL acquisition is conservatively R3 / EXTERNAL_EFFECT and therefore
remains ActionGate confirmation-gated; M1 does not invent an approval bypass.
Research evidence is returned as untrusted observation data with
`research_verified=False`; this strategy never promotes it into trusted
knowledge.

## Security and failure properties

The closure preserves structural authority separation. Plan/objective/research
strings cannot grant permissions, lower risk, widen budgets, clear emergency
stop, select arbitrary capability parameters, fabricate verification, or turn a
procedure END into root success. Capability/procedure execution still reaches
the same Executor, permission engine, ActionGate, budget, cancellation/deadline,
emergency-stop and capability verification path. L5 adds no retry loop and no
dynamic-code path.

Existing M1 unit/integration/adversarial/architecture suites plus the focused
closure tests cover denied permission/gate paths, exhausted budget, emergency
stop, cancellation/deadline, invalid decomposition/readiness, dependency
ordering/deadlock handling, unsupported action/procedure bindings, capability
verification failure, independent root verification failure, provider failure,
bounded escalation/anti-loop behavior, and hostile content attempting authority
escalation.

## Acceptance matrix

| AX ID | Requirement | Historical baseline | Acceptance | Implementation evidence | Test/acceptance evidence |
| --- | --- | --- | --- | --- | --- |
| AX-041 | ExecutionLevel L0–L5 vocabulary | COMPLETE | VERIFIED | src/agentx/cognition/router.py | tests/unit/test_router.py |
| AX-042 | deterministic level-selection inputs | COMPLETE | VERIFIED | src/agentx/cognition/router.py | tests/unit/test_router.py |
| AX-043 | routing-evidence contract | COMPLETE | VERIFIED | src/agentx/cognition/router.py | tests/unit/test_router.py |
| AX-044 | bounded escalation model | COMPLETE | VERIFIED | src/agentx/cognition/escalation.py | tests/unit/test_escalation.py |
| AX-045 | anti-loop policy | COMPLETE | VERIFIED | src/agentx/cognition/anti_loop.py | tests/unit/test_anti_loop.py |
| AX-046 | AgentLoop composition boundary | COMPLETE | VERIFIED | src/agentx/agent_loop.py | tests/unit/test_agent_loop.py |
| AX-047 | strategy registry | COMPLETE | VERIFIED | src/agentx/agent_loop.py | tests/unit/test_agent_loop.py |
| AX-048 | canonical runtime strategy assembly | COMPLETE | VERIFIED | src/agentx/strategy_assembly.py | tests/unit/test_strategy_assembly.py |
| AX-049 | L0 strategy adapter | COMPLETE | VERIFIED | src/agentx/cache_strategy.py | tests/integration/test_cache_strategy.py |
| AX-050 | L1 governed capability strategy | COMPLETE | VERIFIED | src/agentx/capability_strategy.py | tests/integration/test_capability_strategy_agent_loop.py |
| AX-051 | L2 compiled-procedure strategy | COMPLETE | VERIFIED | src/agentx/compiled_procedure_strategy.py | tests/integration/test_compiled_procedure_strategy.py |
| AX-052 | L3 guided-procedure strategy | COMPLETE | VERIFIED | src/agentx/guided_procedure_strategy.py | tests/integration/test_guided_procedure_strategy.py |
| AX-053 | L4 planning boundary | COMPLETE | VERIFIED | src/agentx/plan_execution.py | tests/integration/test_plan_execution.py |
| AX-054 | L5 exploratory strategy clean implementation | NOT_IMPLEMENTED | VERIFIED | src/agentx/exploratory_strategy.py; src/agentx/governed_research.py | tests/integration/test_m1_closure.py |
| AX-055 | strategy-unavailable behavior | COMPLETE | VERIFIED | src/agentx/agent_loop.py | tests/unit/test_agent_loop.py |
| AX-056 | failure escalation behavior | COMPLETE | VERIFIED | src/agentx/agent_loop.py | tests/unit/test_agent_loop.py |
| AX-057 | TaskManager/runtime integration | COMPLETE | VERIFIED | src/agentx/agent_loop.py | tests/unit/test_agent_loop.py |
| AX-058 | task decomposition schema | COMPLETE | VERIFIED | src/agentx/core/task_decomposition.py; src/agentx/core/decomposition_readiness.py | tests/unit/test_decomposition_readiness.py; tests/unit/test_task_decomposition.py |
| AX-059 | decomposition DAG validation | COMPLETE | VERIFIED | src/agentx/core/task_decomposition.py; src/agentx/core/decomposition_readiness.py | tests/unit/test_decomposition_readiness.py; tests/unit/test_task_decomposition.py |
| AX-060 | decomposition readiness validator | COMPLETE | VERIFIED | src/agentx/core/task_decomposition.py; src/agentx/core/decomposition_readiness.py | tests/unit/test_decomposition_readiness.py; tests/unit/test_task_decomposition.py |
| AX-061 | terminal-node readiness rules | COMPLETE | VERIFIED | src/agentx/core/task_decomposition.py; src/agentx/core/decomposition_readiness.py | tests/unit/test_decomposition_readiness.py; tests/unit/test_task_decomposition.py |
| AX-062 | capability-target readiness | COMPLETE | VERIFIED | src/agentx/core/task_decomposition.py; src/agentx/core/decomposition_readiness.py | tests/unit/test_decomposition_readiness.py; tests/unit/test_task_decomposition.py |
| AX-063 | procedure-target readiness | COMPLETE | VERIFIED | src/agentx/core/task_decomposition.py; src/agentx/core/decomposition_readiness.py | tests/unit/test_decomposition_readiness.py; tests/unit/test_task_decomposition.py |
| AX-064 | higher-level-resolution readiness | COMPLETE | VERIFIED | src/agentx/core/task_decomposition.py; src/agentx/core/decomposition_readiness.py | tests/unit/test_decomposition_readiness.py; tests/unit/test_task_decomposition.py |
| AX-065 | Reasoner provider-neutral boundary | COMPLETE | VERIFIED | src/agentx/cognition/reasoner.py; src/agentx/planning_strategy.py | tests/unit/test_reasoner.py; tests/integration/test_planning_strategy.py |
| AX-066 | bounded Reasoner request | COMPLETE | VERIFIED | src/agentx/cognition/reasoner.py; src/agentx/planning_strategy.py | tests/unit/test_reasoner.py; tests/integration/test_planning_strategy.py |
| AX-067 | model-output acceptance boundary | COMPLETE | VERIFIED | src/agentx/cognition/reasoner.py; src/agentx/planning_strategy.py | tests/unit/test_reasoner.py; tests/integration/test_planning_strategy.py |
| AX-068 | model-output strict JSON handling | COMPLETE | VERIFIED | src/agentx/cognition/reasoner.py; src/agentx/planning_strategy.py | tests/unit/test_reasoner.py; tests/integration/test_planning_strategy.py |
| AX-069 | independent task verification | COMPLETE | VERIFIED | src/agentx/capabilities/verifier.py; src/agentx/agent_loop.py | tests/unit/test_verifier.py; tests/integration/test_compiled_procedure_strategy.py; tests/integration/test_plan_execution.py |
| AX-070 | verification-requirement contract | COMPLETE | VERIFIED | src/agentx/capabilities/verifier.py; src/agentx/agent_loop.py | tests/unit/test_verifier.py; tests/integration/test_compiled_procedure_strategy.py; tests/integration/test_plan_execution.py |
| AX-071 | requirement evaluation | COMPLETE | VERIFIED | src/agentx/capabilities/verifier.py; src/agentx/agent_loop.py | tests/unit/test_verifier.py; tests/integration/test_compiled_procedure_strategy.py; tests/integration/test_plan_execution.py |
| AX-072 | execution success != task success invariant | COMPLETE | VERIFIED | src/agentx/capabilities/verifier.py; src/agentx/agent_loop.py | tests/unit/test_verifier.py; tests/integration/test_compiled_procedure_strategy.py; tests/integration/test_plan_execution.py |
| AX-073 | procedure END != task success invariant | COMPLETE | VERIFIED | src/agentx/capabilities/verifier.py; src/agentx/agent_loop.py | tests/unit/test_verifier.py; tests/integration/test_compiled_procedure_strategy.py; tests/integration/test_plan_execution.py |
| AX-074 | cancellation semantics | COMPLETE | VERIFIED | src/agentx/agent_loop.py; src/agentx/capabilities/runtime.py | tests/unit/test_agent_loop.py; tests/integration/test_capability_strategy_agent_loop.py |
| AX-075 | timeout semantics | COMPLETE | VERIFIED | src/agentx/agent_loop.py; src/agentx/capabilities/runtime.py | tests/unit/test_agent_loop.py; tests/integration/test_capability_strategy_agent_loop.py |
| AX-076 | denied-attempt semantics | COMPLETE | VERIFIED | src/agentx/agent_loop.py; src/agentx/capabilities/runtime.py | tests/unit/test_agent_loop.py; tests/integration/test_capability_strategy_agent_loop.py |
| AX-077 | unverified-attempt semantics | COMPLETE | VERIFIED | src/agentx/agent_loop.py; src/agentx/capabilities/runtime.py | tests/unit/test_agent_loop.py; tests/integration/test_capability_strategy_agent_loop.py |
| AX-078 | bounded strategy attempts | COMPLETE | VERIFIED | src/agentx/agent_loop.py; src/agentx/capabilities/runtime.py | tests/unit/test_agent_loop.py; tests/integration/test_capability_strategy_agent_loop.py |
| AX-079 | strategy result typing | COMPLETE | VERIFIED | src/agentx/agent_loop.py; src/agentx/capabilities/runtime.py | tests/unit/test_agent_loop.py; tests/integration/test_capability_strategy_agent_loop.py |
| AX-080 | hostile routing metadata inertness | COMPLETE | VERIFIED | src/agentx/agent_loop.py; src/agentx/cognition/router.py | tests/adversarial/test_router_adversarial.py; tests/adversarial/test_capability_strategy_adversarial.py; tests/integration/test_m1_closure.py |
| AX-081 | task/runtime adversarial tests | COMPLETE | VERIFIED | src/agentx/agent_loop.py; src/agentx/cognition/router.py | tests/adversarial/test_router_adversarial.py; tests/adversarial/test_capability_strategy_adversarial.py; tests/integration/test_m1_closure.py |
| AX-082 | task/runtime architecture tests | COMPLETE | VERIFIED | src/agentx/_architecture.py | tests/architecture/test_capability_strategy_boundaries.py; tests/architecture/test_guided_procedure_strategy_boundaries.py; tests/architecture/test_decomposition_readiness_boundaries.py |
| AX-083 | real-world single-task vertical slice | PARTIAL | VERIFIED | src/agentx/plan_execution.py; src/agentx/application_binding.py | tests/integration/test_plan_execution.py |
| AX-084 | cross-strategy orchestration benchmark | PARTIAL | VERIFIED | src/agentx/agent_loop.py; src/agentx/strategy_assembly.py | tests/integration/test_cache_strategy.py; tests/integration/test_capability_strategy_agent_loop.py; tests/integration/test_compiled_procedure_strategy.py; tests/integration/test_guided_procedure_strategy.py; tests/integration/test_plan_execution.py; tests/integration/test_m1_closure.py |
| AX-085 | M1 release acceptance proof | NOT_IMPLEMENTED | VERIFIED | docs/m1_acceptance.md | C1.01 full Windows quality suite |

## Representative end-to-end paths

| Level | Production path | Acceptance evidence |
| --- | --- | --- |
| L0 | verified cache reuse -> AgentLoop -> current verification requirement | `tests/integration/test_cache_strategy.py` |
| L1 | typed capability binding -> Executor -> kernel gates -> verification -> AgentLoop | `tests/integration/test_capability_strategy_agent_loop.py` |
| L2 | ACTIVE compiled procedure -> interpreter -> Executor -> verification -> AgentLoop | `tests/integration/test_compiled_procedure_strategy.py` |
| L3 | guided procedure -> bounded Reasoner region -> Executor -> independent task verification | `tests/integration/test_guided_procedure_strategy.py` |
| L4 | inert plan -> readiness/dependency order -> typed capability/procedure binding -> Executor -> separate goal check | `tests/integration/test_plan_execution.py`, `tests/integration/test_plan_procedure_composition.py` |
| L5 | Hive-first gap -> shared research budget -> governed research capability -> untrusted observation -> AgentLoop verification | `tests/integration/test_m1_closure.py` |

## Environment limitations

This acceptance does **not** claim a live third-party model, live web provider,
interactive Windows desktop, browser driver, or Android device experiment.
Scripted/local providers are used only for deterministic runtime semantics.
External research remains confirmation-gated rather than being silently
downgraded. Those broader real-environment experiments belong to later milestone
exit criteria unless a canonical M1 requirement is changed to require them.

## M1 conclusion

On the PR candidate, all 45 canonical M1 task identities have implementation,
integration, test and acceptance evidence recorded. Historical baseline fields
remain unchanged. M1 is considered release-acceptable only when the final
exact-head Windows C1.01 quality workflow is green; no merge is performed by
this closure work.
