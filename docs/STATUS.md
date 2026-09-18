# AgentX status and completion ledger

Evidence snapshot: 2026-09-18. This ledger replaces the README's obsolete claim
that all subsystems are empty. It does **not** turn task counts into a completion
percentage or equate a worker report with a released product.

## Current continuation

- PR #159 integrated the worker campaign into `main` at
  `72bf7059d64a38ed9512aedf420ec7b1d2552837`. References to unmerged workers in
  the historical review sections below describe the earlier review point.
- Reviewed PR #160 and its successful Windows quality gate; merged at
  `a2952ccf53fa18e3ee7d27c7b03434907b72f7fa`. Browser fill text is redacted from
  evidence and verification requires independent field-value readback.
- PR #161 adds explicitly bound governed L4 plan execution, concrete-provider
  metrics instrumentation, and the complete normalized AX task definitions.
  The inert planner remains separate from execution. See
  [plan execution](plan_execution.md) and [model metrics](instrumented_model_provider.md).
- PR #163 is the focused M1 closure candidate from canonical main
  `294cf9029e63d91b476971db79133e09264f2087`. It adds typed application and
  procedure-leaf composition, bounded Hive-first L5 research through the canonical
  Executor, and task-level AX-041–AX-085 acceptance evidence. See
  [M1 acceptance](m1_acceptance.md). It remains unmerged pending review.
- [TASKS.json](TASKS.json) is the machine-readable AX-001 through AX-600 source;
  [TASKS.md](TASKS.md) is its generated view. Imported reported completion is
  distinct from task-level acceptance evidence. The dependency graph is
  explicitly partial; it is not a completed audit of all task relationships.
- Live-model cold/learn/restart/warm acceptance remains outstanding. The local
  continuation environment has no configured model service credentials or
  interactive Windows/Android environment. Loopback HTTP and scripted-provider
  tests do not substitute for those experiments.

## Canonical baseline and work in this review

- Starting `main`: `760fe33e4e00e3f98b60d92ca657bec7b2cd583e`.
- Reviewed and merged AX-040 / PR #145. Resulting `main`:
  `cb504ed46681b08ad9b9f8248cce3ca7758363ea`.
- AX-040 source head `2ffdf1748df24aea219a7ddea56c9e7f2352e0ab` passed
  [Windows C1.01 run 34969197873](https://github.com/harsh2025-sketch/AgentX/actions/runs/34969197873).
- PR #157 passed Windows C1.01 run 35086633212 and merged into the #156 worker
  branch. It repairs formatting, strict typing and a brittle architecture check.
- PR #158 passed Windows C1.01 run 35086498890 and merged to main at
  `4af91722ea33031fefb3003ca86fa91d647c7997`. The concrete HTTP model adapter
  is canonical; loopback protocol tests are not a live-model benchmark.
- The worker integration candidate preserves individual source histories for
  #146 through #156 and retains the already-canonical native receipt typing fix.
  Candidate code and individual green source runs are not canonical acceptance.

The 643 tracked baseline files were retrieved at pinned Git object identities;
their content hashes were checked. Targeted code/contract review covered the
runtime, model, secret, strategy, capability and persistence boundaries. This is
not a claim that every one of the historical 600 AX checkboxes was fully audited.

## Milestone status

The [roadmap](ROADMAP.md) remains the milestone/exit-criteria authority. These
states distinguish production code, candidate changes and missing acceptance.

| Milestone | Evidence in the repository | Work still required |
| --- | --- | --- |
| M0 Kernel | Permission/risk/gate/budget/stop/audit/secret contracts; governed capability loop; AX-040 merged | Continue whole-system security review as new surfaces land |
| M1 Single-task runtime | PR #163 candidate: governed L0-L5 composition, typed L4 capability/procedure binding, independent root verification and 45-task acceptance audit | Review/merge #163; real external-model/provider and interactive-host experiments remain later-milestone work |
| M2 Persistent memory | Event journal, episodes, semantic knowledge, typed scopes and integrated #147 | Prove combined restart and corruption behavior |
| M3 Compilation and reuse | Procedure IR/interpreter, compiler, validation, promotion, reuse and integrated #148 | Full cold-to-restart-to-warm acceptance |
| M4 Learning efficiency | Metrics, reuse evidence and experiment contracts | Real model-backed cold/warm comparison with independently verified outcomes |
| M5 Repair | Diagnosis, degradation, shadow validation, replacement and rollback machinery | Deliberate break/repair/reuse experiment and failed-repair rollback |
| M6 Windows | Structured filesystem, app/window/input/UIA foundations; #156/#157 integrated | Representative Windows 10/11 workflows |
| M7 Browser | State, DOM, target selection, navigation, click and selected-node fill | N2.27 privacy/field-type acceptance; concrete browser driver and verified multi-page workflow |
| M8 Models/research | Canonical model/Reasoner; integrated #149; HTTP adapter and invocation metrics | N2.07 L5; configured real service/credentials; live acceptance and model-budget composition |
| M9 Adversarial hardening | Existing suites, merged AX-040 and integrated #150 | Modern cross-surface attacks on the integrated runtime |
| M10 World model | Canonical snapshots; integrated #146/#151 observation/cache/invalidation | Real environment-change recovery benchmark |
| M11 Voice/product | Audio abstractions; #152 event protocol candidate | Real STT/TTS, interruption, user controls, usable application/CLI |
| M12 Persistent operation | #153 watcher candidate | Scheduler, durable task recovery, explicit proactivity policy and resource bounds |
| M13 Multi-device | Device protocol foundation | Actual Android provider and verified cross-device handoff |
| M14 Optimization | #155 descriptive strategy-evidence candidate | Reproducible optimization experiments; no learned authority changes |
| M15 Self-extension | #154 missing-capability analysis candidate | Isolated generation/validation, approval-bound installation and rollback; research remains |
| M16 Release | Windows CI and extensive regression infrastructure | Integrated acceptance, Windows 10/11 host matrix, packaging/upgrades/privacy and release signoff |

## A critical runtime distinction

`PlanningStrategy.plan()` makes an inert validated `TaskDecomposition`; that
class's own `attempt()` still fails closed. `GovernedPlanningStrategy` is the
separate L4 execution adapter. It validates readiness, expands dependencies,
preflights explicit application bindings, executes via the canonical Executor,
and requires a separate governed goal check. It returns actual verification
evidence to AgentLoop instead of fabricating success from a plan.
`ApplicationActionBinder` resolves exact typed capability ids to composition-owned
requests. `PreparedProcedureBinder` resolves exact procedure ids to the canonical
ACTIVE/applicable compiled-procedure runtime; unknown or unavailable procedures
fail closed. The L5 candidate performs Hive-first gap assessment and delegates
one pre-bound acquisition through a governed capability, so permissions, risk,
budget, stop and verification remain kernel-owned. Research observations remain
unverified data. None of these binders parse natural-language authority claims.
Live-model/external-provider acceptance remains a later environment-dependent proof.

The current CLI exposes help/version only. Installing the package does not start
a usable natural-language agent. This is a product/composition gap as well as a
documentation issue.

## Original open-worker evidence

These are source-head runs observed before integration; they do not certify a
combined candidate. Source SHAs remain recorded in each linked PR.

| PR | Scope | Observed Windows C1.01 run | Source status |
| --- | --- | --- | --- |
| [146](https://github.com/harsh2025-sketch/AgentX/pull/146) | World model | 34977312365 | passed; unmerged at review |
| [147](https://github.com/harsh2025-sketch/AgentX/pull/147) | Hive assurance/scope | 35008179993 | passed; unmerged at review |
| [148](https://github.com/harsh2025-sketch/AgentX/pull/148) | Restart-safe L2/L3 reuse | 34998598769 | passed; unmerged at review |
| [149](https://github.com/harsh2025-sketch/AgentX/pull/149) | Hive-first research ingestion | 35006454004 | passed; unmerged at review |
| [150](https://github.com/harsh2025-sketch/AgentX/pull/150) | Threat catalogue | 35003744973 | passed; unmerged at review |
| [151](https://github.com/harsh2025-sketch/AgentX/pull/151) | World freshness/DOM invalidation | 34999938745 | passed; unmerged at review |
| [152](https://github.com/harsh2025-sketch/AgentX/pull/152) | Audio/UI event foundations | 35003601828 | passed; unmerged at review |
| [153](https://github.com/harsh2025-sketch/AgentX/pull/153) | Event watcher | 35007348652 | passed; unmerged at review |
| [154](https://github.com/harsh2025-sketch/AgentX/pull/154) | Missing-capability detector | 35004663688 | passed; unmerged at review |
| [155](https://github.com/harsh2025-sketch/AgentX/pull/155) | Strategy-performance evidence | 35006512711 | passed; unmerged at review |
| [156](https://github.com/harsh2025-sketch/AgentX/pull/156) | Filesystem/input verification | 35008219896 | 9,761 tests and lint passed; formatting failed; mypy skipped |

The previous handoff's GitHub runner restriction is not the observed blocker:
these runs executed. The previous claim that only one or two PRs remained open
was also stale; twelve were open at the start of this review, including #145.

## Completion sequence and gates

1. Verify and review the combined worker candidate, resolving shared surfaces
   centrally. Preserve canonical migrations and history; none of these imported
   diffs changes the migration ladder or architecture manifest.
2. Configure a real pinned model through
   the secret boundary. Keep operational failures and missing usage explicit.
3. Review and merge the PR #163 M1 closure candidate; its L4 typed binding and
   bounded Hive-first L5 composition are complete on the candidate branch.
4. Audit and harden the existing selected-node fill against N2.27, including
   sensitive-text redaction and independent field-value readback. N2.27 explicitly
   forbids implicit submission. Complete a concrete browser driver and separately
   governed multi-page workflows; authentication/upload/submission remain explicit.
5. Demonstrate cold goal -> governed action -> independent verification -> causal
   experience -> compile -> varied validation -> promote -> restart -> verified
   warm reuse with measured reasoning reduction.
6. Break the procedure; detect degradation; repair and shadow-validate; replace
   atomically; verify reuse; demonstrate rollback on a failed replacement.
7. Finish the remaining product/device/operations milestones against their own
   exit criteria. Late research milestones are not implied by a working prototype.

Canonical acceptance requires the candidate's current base/head, full Windows
pytest, Ruff lint/format, strict mypy, runtime-only install and resulting-main
verification. Existing platform-specific skips must be reported rather than
represented as executed Windows 10/11 desktop acceptance. Hosted Windows CI is
not a substitute for the target interactive desktop/device/model environment.

The connected review workspace does not provide an authenticated local checkout,
so no local full-suite result is fabricated. Exact repository gates run through
the canonical Windows C1.01 workflow. PR #163 has already demonstrated a green
implementation-head run; its final documentation/ledger head must also pass before
the closure conclusion is recorded. No skips, exclusions, xfails or workflow
weakening were added to evade this.

## Integration review repairs (PR #159)

- World cache invalidation bookkeeping is bounded as well as cache entries;
  generation checks prevent stale refresh acceptance after tombstone rotation.
- Scoped Hive retrieval reads at most the configured scan bound plus one row,
  and fails explicitly on overflow instead of answering from a partial scan.
- Missing-capability detection defaults to incomplete inventory visibility.
  Unknown and restricted identities never become missing-capability gaps;
  only an explicit complete snapshot can establish absence.
- Distinct execution/verification port checks exercise all five adapters rather
  than asserting one spelling of a source expression.

These additional repairs have focused regression coverage. Acceptance still
requires the final combined Windows gate; intermediate green runs do not
certify later commits.
