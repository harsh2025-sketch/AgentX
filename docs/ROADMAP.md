# AgentX Whole-Project Milestones

This roadmap tracks the full AgentX project, not only the current Wave-2 task set.

AgentX is a Windows-first, local-first Adaptive Personal Operating Intelligence / AgentOS. The defining project goal is a governed system that can reason about goals, act through trusted capability boundaries, verify outcomes, retain causal experience, compile and reuse procedures, detect degradation, repair procedures, and progressively reduce expensive reasoning for familiar work.

## Milestone 0 — Governed Architecture and Trusted Kernel

**Status:** Completed and acceptance-verified for the M0 task scope

Establish the architectural and security substrate that all later milestones depend on.

Exit criteria:
- canonical subsystem boundaries and dependency rules;
- Permission Engine, Action Gate, R0–R4 risk model, resource budgets, emergency stop, audit and secrets boundaries;
- canonical Task / ExecutionContext / result / verification contracts;
- capability ABI, registry and governed execution loop;
- event and persistence foundations;
- architecture/adversarial/unit/integration quality gates on Windows + Python 3.12.

## Milestone 1 — Governed Single-Task Agent Runtime

**Status:** Mostly acceptance-verified; strict real-world/benchmark closure remains

Turn the substrate into a real bounded agent loop for one task.

Exit criteria:
- deterministic L0–L5 execution-level vocabulary and selection;
- runtime strategy assembly;
- L0 cache, L1 deterministic capability, L2 compiled procedure, L3 guided procedure, L4 planning and bounded L5 exploratory paths;
- Hive-first L5 gap handling with research kept untrusted and governed through the Trusted Kernel;
- task decomposition/readiness validation;
- governed capability execution only through the Trusted Kernel;
- independent task-level verification; procedure END or API success never equals task success;
- bounded escalation, anti-loop and failure handling.

## Milestone 2 — Persistent Experience and Restart-Safe Memory

**Status:** Completed and acceptance-verified

Make verified experience survive process restarts and become queryable context.

Exit criteria:
- episodic capture of execution + verification outcomes;
- durable restart-safe episode storage/retrieval;
- semantic knowledge store with provenance and verification state;
- environmental state with freshness/TTL;
- relationship graph and world-state snapshot foundations;
- explicit user preferences;
- contradiction/supersession handling without silently rewriting history;
- restart tests demonstrating lossless retrieval of relevant experience.

Acceptance evidence: PR #162 adds `tests/integration/test_m2_restart_memory_acceptance.py`,
covering durable episode/semantic/relationship/assurance/world-link recovery across a
real child-process restart, exact-scope isolation, hostile persisted strings remaining
inert data, and fail-closed corruption handling. AX-086–AX-125 are recorded as
acceptance VERIFIED while the protected historical reported baseline remains unchanged.

## Milestone 3 — Procedure Runtime and Verified Skill Compilation

**Status:** Completed and acceptance-verified

Convert verified experience into reusable procedures without conflating one success with general validity.

Exit criteria:
- Procedure Graph IR and deterministic interpreter;
- procedure execution evidence distinct from task verification;
- skill compiler pipeline from verified causal experience;
- causal-action filtering, parameter extraction/generalization and determinism/reasoning-region classification;
- candidate-only procedure synthesis;
- varied-parameter validation runner;
- explicit validation evidence policy;
- explicit promotion transaction from CANDIDATE to ACTIVE;
- applicability matching and deterministic reuse selection;
- restart-safe ACTIVE-procedure reuse.

Acceptance evidence: merged PR #166 adds the production compiler/runtime binding and governed
candidate-validation seam plus `tests/integration/test_m3_skill_compilation_acceptance.py`,
which proves cold governed execution -> causal experience -> corroborated compilation ->
varied validation -> explicit promotion -> persisted ACTIVE retrieval after a fresh-process
restart -> verified L2 reuse. Windows C1.01 run #820 passed the canonical repository gate
on implementation head `e55d48b102de4b346faa54d1af0d3de7d35008a0`.

## Milestone 4 — Learning Efficiency Proof

**Status:** Partially implemented / not yet demonstrated end-to-end

Prove that AgentX converts expensive reasoning into cheaper verified execution over time.

Exit criteria:
- cold-vs-warm experiment harness;
- execution metrics for model calls, tokens, latency, machine actions and cost where available;
- unknown task solved with L4/L5 reasoning/research;
- verified experience compiled and promoted;
- restart AgentX;
- related task solved through L2/L3 procedure reuse;
- warm execution remains verified while materially reducing reasoning/model-call cost;
- evidence is non-authoritative and cannot alter permissions/risk/gates.

## Milestone 5 — Failure-Driven Self-Repair and Procedure Lifecycle

**Status:** Completed and acceptance-verified

Detect when learned procedures stop working and repair them conservatively.

Exit criteria:
- failure taxonomy, localization and diagnosis;
- degradation detection;
- bounded repair workflow and repair budget / anti-loop;
- repair patch materialization;
- shadow procedure validation;
- atomic forward replacement;
- rollback semantics with RETIRED history preserved;
- exactly one ACTIVE revision after successful lifecycle mutation;
- deliberate procedure break -> detection -> diagnosis -> repair -> shadow validation -> new revision -> verified reuse;
- rollback proof when repaired revision fails.

Acceptance evidence: PR #164 adds the deliberate break -> independently verified failure ->
typed localization/diagnosis -> bounded repair -> CANDIDATE -> varied shadow validation ->
atomic replacement -> restart -> verified reuse lifecycle, plus a bad-repair path that
fails validation and preserves/restores the known-good ACTIVE revision. AX-206–AX-245
are recorded as acceptance VERIFIED without rewriting the protected historical baseline.

## Milestone 6 — Windows-Native Capability Fabric

**Status:** Completed and acceptance-verified

Provide robust Windows-first control using structured APIs before visual fallback.

Exit criteria:
- application discovery/launch;
- window discovery, activation, minimize/maximize/restore and bounded move/resize;
- keyboard/text/clipboard capability surface;
- filesystem read/write and structural operations with request-sensitive risk;
- UI Automation tree observation and semantic target resolution;
- canonical state-transition verification;
- native mutation seams remain behind governed capability contracts;
- no raw keyboard logging/global hotkey capture/unsafe shell bypass;
- representative real-host Windows workflows verified end-to-end.

Acceptance evidence: PR #165 integrates governed human-approval/runtime binding and native
application-launch, keyboard and clipboard adapters, plus dedicated Windows host acceptance.
The canonical Windows quality gate runs the real-host mutation and representative multi-process
workflow tests before the full suite. Those tests require native mutation followed by independent
Win32/process readback and task verification; native API return success alone is insufficient.
The hosted evidence is Windows Server CI and must not be represented as a Windows 10 + Windows 11
release matrix. AX-246–AX-300 are recorded as acceptance VERIFIED while the protected historical
reported baseline remains unchanged.

## Milestone 7 — Browser Agent Capability Layer

**Status:** Partial

Build a governed browser surface for multi-step web workflows.

Exit criteria:
- browser page/tab state and DOM observation;
- governed navigation and click actions;
- semantic DOM target selection;
- governed text/form entry and submission;
- redirect/navigation verification;
- download/upload/auth/cookie behavior added only through explicit governed contracts where required;
- hostile webpage content treated as untrusted data, never authority;
- multi-step browser workflow benchmark with task-level verification.

## Milestone 8 — Real Model Gateway and Exploratory/Research Runtime

**Status:** Major remaining core milestone

Make L4/L5 practical with provider-neutral real model execution and governed research.

Exit criteria:
- concrete model-provider adapter against canonical provider-neutral gateway;
- secrets accessed only through canonical secret boundary;
- provider output treated as untrusted data;
- bounded L4 planning calls;
- clean L5 exploratory/research strategy boundary;
- Hive-first retrieval before external research;
- research findings enter memory as unverified knowledge until independently verified;
- model/provider failure and fallback behavior explicitly bounded;
- no provider-specific assumptions leak into core architecture.

## Milestone 9 — Security and Adversarial Trust-Boundary Hardening

**Task range:** AX-371–AX-405  
**Status:** Complete / task-level acceptance verified on PR #169, subject to exact-head C1.01.

Exit criteria achieved by the M9 campaign:
- external/untrusted content remains data rather than authority across canonical surfaces;
- modern C4.10 research, knowledge, learning, repair and kernel-authority replay;
- tool-directive, Unicode/homoglyph, encoded-payload and SQL-shaped adversarial corpus;
- secrets leakage and audit-log privacy audits;
- cross-subsystem privilege and dynamic-execution source audits;
- multi-hop whole-system and restart-safe hostile-content evidence;
- machine-readable matrix in `docs/M9_SECURITY_MATRIX.json`;
- second-pass acceptance report in `docs/M9_SECURITY_ACCEPTANCE.md`.


## Milestone 10 — Perception and World Model

**Status:** Foundations only

Give AgentX a bounded, freshness-aware model of relevant computer state without continuously mirroring the entire desktop.

Exit criteria:
- structured screen/perception representation;
- lazy world-state cache with freshness and invalidation;
- UIA/DOM/native observations preferred over screenshots when available;
- visual fallback only when structured state is unavailable;
- target grounding confidence/evidence kept separate from action authority;
- cross-application state relationships represented in the Hive/world model;
- benchmark for recovery from UI/layout/environment change.

## Milestone 11 — Voice, Realtime Interaction and Product Surface

**Status:** Later-stage

Add natural realtime interaction without coupling the intelligence architecture to one voice/model vendor.

Exit criteria:
- provider-neutral STT/TTS/realtime audio abstractions;
- interruption/barge-in and turn handling;
- runtime event protocol for UI/HUD;
- user-visible listening/reasoning/executing/verifying/recovering states;
- voice commands routed through the same governed runtime as typed goals;
- voice output never bypasses task verification or authority boundaries;
- HUD is telemetry/control surface, not the system architecture.

## Milestone 12 — Proactivity, Scheduling and Long-Running Operation

**Status:** Future

Move from request/response agent to a persistent personal operating intelligence.

Exit criteria:
- scheduled and event-driven tasks;
- event watcher framework;
- proactive recommendations with explicit user policy;
- quiet/attention-aware behavior;
- bounded background resource use;
- human approval for consequential external effects;
- durable restart/recovery of long-running task state;
- anti-loop, rate limiting and cost-control remain enforced.

## Milestone 13 — Multi-Device / Android / Cross-Device AgentOS

**Status:** Future

Generalize the capability fabric beyond the Windows host.

Exit criteria:
- device abstraction/protocol and registry;
- Android capability provider using structured APIs/ADB/accessibility before vision tapping;
- consistent task/verification/authority contracts across Windows/browser/Android;
- cross-device task decomposition and handoff;
- device-specific environment validity for learned procedures;
- end-to-end PC + browser + phone workflow with verification on every device boundary.

## Milestone 14 — Adaptive Strategy Optimization and Specialized Models

**Status:** Research-stage future

Improve strategy choice and reduce model cost using evidence rather than unrestricted self-modification.

Exit criteria:
- strategy performance history;
- deterministic heuristics as baseline;
- contextual-bandit or ranking experiments where justified;
- preference learning from explicit user corrections;
- small specialized models for narrow classification/grounding/action tasks where useful;
- reproducible evaluation showing improvement over fixed strategy selection;
- no learning mechanism can modify Trusted Kernel authority.

## Milestone 15 — Safe Self-Extension and Tool/Capability Creation

**Status:** Open-research / late-stage

Allow AgentX to propose and validate new capabilities without granting itself unrestricted code-install authority.

Exit criteria:
- missing-capability detection;
- research/design proposal generation;
- isolated code/tool generation environment;
- static analysis, tests, adversarial checks and sandbox execution;
- human approval gates for installation/promotion;
- versioned capability registry updates and rollback;
- generated code/content cannot modify Trusted Kernel directly;
- evidence-backed promotion only after repeated successful validation.

## Milestone 16 — Production Hardening, Benchmarks and Release

**Status:** Final program milestone

Turn the research prototype into a defensible, measurable AgentOS release.

Exit criteria:
- canonical whole-system ledger and documentation synchronized with live code;
- Windows 10/11 real-host test matrix;
- crash/restart/database recovery tests;
- performance, latency, cost and model-call benchmarks;
- security/adversarial regression suite;
- representative OS/browser/memory/repair benchmark corpus;
- full cold -> learn -> restart -> warm reuse -> break -> repair acceptance demonstration;
- installer/configuration/upgrade/rollback story;
- privacy, audit and data-retention controls;
- release candidate passes full exact-head quality gates.

---

## Current canonical position

The independent AX-001–AX-600 audit against canonical main
`bcc0692246a981be5ae15c993b0c3e6512871b92` records 380 VERIFIED,
9 IN_PROGRESS, 7 BLOCKED and 189 NOT_IMPLEMENTED tasks. M0, M2, M3, M5 and
M6 are fully accepted at their milestone task scope. M1 is 42/45 under the
stricter audit because the current deterministic integration evidence does not
by itself prove the "real-world single-task vertical slice" or one cross-strategy
benchmark; its release proof therefore remains dependent on those gaps.

The broader project is not complete: M4 live learning-efficiency proof, governed
browser completion, live model/research acceptance, modern cross-surface
adversarial replay, later platform/product milestones, and release-level Windows
10/11 matrix coverage remain separate work.

Milestone completion must be judged by exact requirements and canonical evidence,
not by raw PR count, historical task-number count, or a single readiness percentage.
