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

**Status:** 43/45 acceptance-verified; strict live natural-goal vertical and dependent release proof remain BLOCKED

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

**Status:** 27/30 acceptance-verified; deterministic cold-to-warm chain accepted, live-model efficiency proof remains BLOCKED

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

**Status:** Completed and acceptance-verified for the M7 task scope

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

Acceptance evidence: clean PR #173 adds a concrete W3C WebDriver provider, explicit
governed form/session/workflow contracts, deterministic DOM recovery and a canonical
Windows CI real-browser gate. Actual headless Chrome is driven against a controlled
localhost fixture through Executor/ActionGate/ResourceBudget with independent readback,
sensitive-value redaction, explicit submit/redirect, upload/download, cookies/auth,
tabs, recovery, multi-page flow and hostile-page authority-isolation checks. This
accepts AX-301–AX-335 at M7 scope without claiming arbitrary third-party-site compatibility.

## Milestone 8 — Real Model Gateway and Exploratory/Research Runtime

**Status:** 33/35 acceptance-verified; real external model/research vertical and milestone acceptance remain BLOCKED

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

Acceptance evidence: PR #173 adds a bounded provider-independent model gateway with
retry/fallback/health/capability registry and canonical Reasoner/ResourceBudget
composition, plus a verified-research-to-execution handoff that admits only canonical
VERIFIED knowledge as inert context. A fail-closed live-provider command is present,
but AX-369/370 remain BLOCKED because no genuine external provider credential/service
was available; loopback or scripted providers are not substituted.

## Milestone 9 — Security and Adversarial Trust-Boundary Hardening

**Status:** Ongoing; historical C4.10 requires modern replay/revalidation

Prove that external content and learned artifacts cannot become authority.

Exit criteria:
- adversarial prompt-injection/untrusted-content regression suite across research, memory, learning and repair;
- external content cannot grant permission, lower risk, bypass Action Gate, clear Emergency Stop, alter budgets or fabricate verification/task success;
- no dynamic-code/tool-directive smuggling through data fields;
- dependency-boundary and authority tests remain machine-enforced;
- replay/revalidate historical C4.10 against current canonical main;
- threat-model coverage for capability, memory, model, persistence and self-repair surfaces.

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

The independent AX-001–AX-600 audit plus the M1/M4/M7/M8 second-pass closure
campaign on clean PR #173 records 411 VERIFIED, 1 IN_PROGRESS, 7 BLOCKED and
181 NOT_IMPLEMENTED tasks. M0, M2, M3, M5, M6 and M7 are fully accepted at
their milestone task scope. M1 is 43/45: AX-084 now has one L0-L5 cross-strategy
benchmark, while AX-083/085 remain blocked because a real-Chrome pre-bound L1
slice is not a substitute for the strict full natural-goal/live model-research
vertical and dependent release proof. M4 is 27/30 with the deterministic
cold->compile->promote->restart->warm chain accepted; genuine provider-backed
efficiency measurements remain blocked. M8 is 33/35 with provider-independent
gateway/research integration accepted and real-service vertical acceptance blocked.

The broader project is not complete: live model/research acceptance, later
platform/product milestones, separate M9/M10 work not yet merged to canonical
main, and release-level Windows 10/11 matrix coverage remain separate work.

Milestone completion must be judged by exact requirements and canonical evidence,
not by raw PR count, historical task-number count, or a single readiness percentage.
