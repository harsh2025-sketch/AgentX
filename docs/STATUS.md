# AgentX status and completion ledger

Evidence snapshot: 2026-09-16. This ledger replaces the README's obsolete claim
that all subsystems are empty. It does **not** turn task counts into a completion
percentage or equate a worker report with a released product.

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
| M1 Single-task runtime | Agent loop, L0-L4 strategy boundaries, decomposition, independent verifier | Connect accepted L4 plans to governed execution; complete L5 path |
| M2 Persistent memory | Event journal, episodes, semantic knowledge and typed scopes | Integrate/review #147; prove combined restart and corruption behavior |
| M3 Compilation and reuse | Procedure IR/interpreter, compiler, validation, promotion and reuse | Integrate/review #148; full cold-to-restart-to-warm acceptance |
| M4 Learning efficiency | Metrics, reuse evidence and experiment contracts | Real model-backed cold/warm comparison with independently verified outcomes |
| M5 Repair | Diagnosis, degradation, shadow validation, replacement and rollback machinery | Deliberate break/repair/reuse experiment and failed-repair rollback |
| M6 Windows | Structured filesystem, app/window/input/UIA foundations | Integrate/review #156 and repair #157; representative Windows 10/11 workflows |
| M7 Browser | State, DOM, target selection, navigation and click | N2.27 form/text input and submission; verified multi-page workflow |
| M8 Models/research | Canonical model/Reasoner and research contracts; #149 research candidate; #158 canonical HTTP adapter | N2.07 L5; configured real service/credentials; plan-to-action wiring and live acceptance |
| M9 Adversarial hardening | Existing suites and merged AX-040 | Integrate/review #150; modern cross-surface attacks on the integrated runtime |
| M10 World model | Canonical snapshots; #146/#151 candidate observation/cache/invalidation work | Combined review and real environment-change recovery benchmark |
| M11 Voice/product | Audio abstractions; #152 event protocol candidate | Real STT/TTS, interruption, user controls, usable application/CLI |
| M12 Persistent operation | #153 watcher candidate | Scheduler, durable task recovery, explicit proactivity policy and resource bounds |
| M13 Multi-device | Device protocol foundation | Actual Android provider and verified cross-device handoff |
| M14 Optimization | #155 descriptive strategy-evidence candidate | Reproducible optimization experiments; no learned authority changes |
| M15 Self-extension | #154 missing-capability analysis candidate | Isolated generation/validation, approval-bound installation and rollback; research remains |
| M16 Release | Windows CI and extensive regression infrastructure | Integrated acceptance, Windows 10/11 host matrix, packaging/upgrades/privacy and release signoff |

## A critical runtime distinction

`PlanningStrategy.plan()` makes a validated `TaskDecomposition`. Its `attempt()`
method deliberately fails closed because the current strategy result port has no
plan-execution channel. Therefore, neither the existing L4 class nor a concrete
model connection demonstrates autonomous task completion. The next runtime work
must compose canonical decomposition/readiness, task management, governed action
binding, budgets/stop and independent verification. It must not interpret raw model
text as executable code or fabricate an `executed` result for a plan.

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
3. Complete L4 plan-to-action composition and N2.07 bounded Hive-first exploration.
4. Add N2.27 governed browser form/text entry and independent field/submission
   verification. Keep authentication, upload and consequential submission explicit.
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

Local review environment: Python 3.12.14, Linux. Runtime-only editable install and
the provider's stdlib HTTP tests work. pytest/Ruff/mypy are absent and downloading
their dependencies is blocked here; full gates therefore run in existing Windows
CI. No skips, exclusions, xfails or workflow weakening were added to evade this.

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
