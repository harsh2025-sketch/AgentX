# AgentX Canonical Milestones

This document defines the current milestone roadmap for AgentX. It is intended to replace progress-by-PR-count and stale historical percentages with demonstrable stage gates.

Canonical baseline when this roadmap was created:

`5969340b2efac244bebf4a86ed85df1bb630a377`

The live repository and accepted integration history remain the source of truth. Historical task plans and handover documents are requirements inventory, not authority over current repository state.

## Milestone 0 — Governed Foundation

**Status: COMPLETE**

AgentX has a substantial Windows-first, local-first governed substrate including the Trusted Kernel boundary, capability ABI and registry, permissions, risk, Action Gate, budgets, audit, emergency stop, persistence, task/runtime contracts, verification contracts, model abstractions, Hive foundations, Procedure Graph foundations, learning/repair foundations, and initial Windows/browser capability surfaces.

Acceptance criteria:

- Trusted Kernel remains the sole authority boundary.
- External/model/retrieved/UI/clipboard content remains data, never authority.
- Capability execution remains governed through canonical execution paths.
- Verification remains distinct from execution success.
- Architecture boundary tests remain green.

## Milestone 1 — Wave-2 Core Runtime Completion

**Status: IN PROGRESS**

Current Wave-2 accounting:

- 24 accepted tasks are canonical on `main` through integration PRs #143 and #141.
- N2.26 was rejected as redundant with canonical browser-navigation functionality.
- Three real tasks remain unfinished.

Remaining tasks:

1. **N2.07 — L5 Exploratory/Research Strategy Boundary**
   - Recover/rebuild cleanly from current canonical main.
   - Must provide bounded exploration/research planning without gaining execution or governance authority.

2. **N2.27 — Governed Browser Text/Form Entry Capability**
   - Add canonical governed text/form entry on top of existing browser foundations.
   - Page/DOM content remains untrusted.
   - Submission success must not be treated as task verification.

3. **N2.28 — Concrete Model Provider Adapter**
   - Implement a real provider adapter behind the provider-neutral model gateway.
   - Provider output remains untrusted data.
   - Secrets must flow only through the canonical secret boundary.
   - Runtime architecture must not become provider-specific.

Exit criteria:

- N2.07, N2.27, and N2.28 are canonical on `main`.
- Full pytest, Ruff, Ruff format, mypy, runtime-only install, and Windows/Python 3.12 C1.01 are green on the resulting canonical main when runner infrastructure is available.
- No rejected N2.26 implementation is reintroduced.

## Milestone 2 — Adaptive Flywheel End-to-End Proof

**Status: NOT YET PROVEN**

This is the first milestone that proves AgentX's defining thesis rather than only its component architecture.

Required demonstration:

1. Start with an objective that has no reusable verified procedure.
2. Route to L4/L5 reasoning or research as required.
3. Execute through governed capabilities.
4. Observe and independently verify the original task goal.
5. Persist causal/episodic evidence.
6. Compile a Procedure candidate from verified experience.
7. Validate the candidate across explicit varied parameters.
8. Promote it through the canonical explicit lifecycle transaction.
9. Restart AgentX.
10. Solve a related objective using L2/L3 procedure reuse.
11. Demonstrate lower model-call/time/cost usage while preserving verified success.
12. Deliberately break the procedure or environment.
13. Detect degradation and localize failure.
14. Produce a bounded repair candidate.
15. Shadow-validate the repair.
16. Install a new procedure revision through the canonical replacement transaction.
17. Verify success again and prove rollback remains available when required.

Exit criteria:

- One reproducible acceptance harness executes the complete sequence.
- Cold and warm executions have canonical efficiency evidence.
- No stage conflates execution, verification, validation, lifecycle state, or authority.
- Restart/reuse is demonstrated from durable state, not process-local fixtures.
- Failure and repair are demonstrated deliberately, not only through unit mocks.

## Milestone 3 — Security, Capability Breadth, and Product-Grade Verification

**Status: PLANNED**

Scope includes:

- Clean replay/revalidation of C4.10 untrusted-content/prompt-injection regression coverage against modern main.
- Expand independent state-transition verification where canonical observation contracts exist.
- Harden browser workflows beyond navigation/click/form entry.
- Complete practical Windows application/window/text/clipboard workflows.
- Exercise permission, risk, confirmation, budget, emergency-stop, and audit behavior in real vertical scenarios.
- Add regression scenarios for hostile external content crossing research, Hive, learning, repair, browser, and UI boundaries.

Exit criteria:

- Security regression suite is canonical on modern main.
- At least several real multi-step Windows/browser workflows complete, verify, and recover without bypassing governance.
- No capability reports success solely from native/API return values.

## Milestone 4 — Persistent Personal Intelligence

**Status: PLANNED**

Scope includes:

- Improve Hive retrieval across semantic, episodic, procedural, environmental, and user-model stores.
- Context construction using provenance, freshness, confidence, contradiction and scope.
- Persistent preference learning from explicit corrections.
- Better procedure reuse selection using verified historical evidence.
- Strategy-performance statistics and bounded adaptive selection where justified.
- Lazy world-model integration for relevant application/window/device/task state.

Exit criteria:

- AgentX resumes useful context across restarts.
- Historical evidence can improve execution choice without becoming authority.
- Stale or contradictory memory fails closed or is explicitly surfaced.
- Known workflows increasingly move from L4/L5 toward L2/L3 reuse.

## Milestone 5 — Multimodal Interaction and UX

**Status: LATER**

Scope includes:

- Provider-neutral realtime voice I/O.
- Screen/perception integration beyond existing structured observation surfaces.
- HUD/event protocol driven by real runtime events rather than decorative state.
- User-facing approval, interruption, progress, verification and recovery UX.
- Optional visual fallback only where structured OS/browser control is insufficient.

Exit criteria:

- Voice/HUD surfaces consume canonical runtime events rather than owning agent logic.
- UI does not create a second authority or verification system.
- Structured APIs/UIA/DOM remain preferred over visual clicking.

## Milestone 6 — Multi-Device and Advanced Autonomy

**Status: FUTURE / RESEARCH-GRADE**

Scope may include:

- Android and cross-device capability fabric.
- Device coordination and shared context.
- Proactivity and scheduled/event-driven behavior with strict human-control gates.
- Safer autonomous research/experimentation.
- Tool/capability self-extension only behind explicit sandboxing, verification, provenance and approval boundaries.
- Contextual-bandit or other learning methods where sufficient evidence exists.
- RL only for domains with resettable simulators and meaningful reward signals.

Exit criteria:

- Cross-device actions use the same governed capability model.
- Self-extension cannot modify or bypass the Trusted Kernel.
- Autonomous experimentation is bounded, reversible where possible, auditable and approval-gated for destructive effects.

## Progress Rules

Progress is measured by milestone acceptance criteria, not raw PR count.

A task is counted as canonical only when its accepted implementation is present on `main`. An old open/closed worker PR is not evidence that work is missing or complete. Superseded/stacked/contaminated worker PRs are historical delivery records only.

A milestone is complete only when its end-to-end acceptance criteria are demonstrably satisfied. Component presence alone is not enough.

## Immediate Execution Order

The current critical path is:

`N2.28 -> N2.07 -> N2.27 -> C4.10 modern replay -> Milestone 2 adaptive-flywheel E2E proof`

N2.28 is prioritized because L4/L5 cannot become practical model-backed runtime paths without a concrete provider adapter. N2.07 closes the clean L5 boundary. N2.27 completes the currently missing browser form-entry capability surface. C4.10 is then replayed against the modern canonical architecture before the final adaptive-flywheel acceptance proof.
