# AgentX status and completion ledger

Evidence snapshot: 2026-09-18.

This status is synchronized to the independent AX-001–AX-600 audit plus the
M1/M4/M7/M8 second-pass closure campaign on clean PR #173, based on canonical
main `5eb3c35598bddf6142523a8a0e34c23abcbb7f67`. Historical
`reported_status` remains immutable and separate from audited acceptance.

## Canonical repository state

- Original strict-audit campaign baseline after PR #167: `509f13d44a42f1803e22c85b0602083bfa1e1183`.
- Clean core-intelligence PR base after the proprietary-license merge:
  `5eb3c35598bddf6142523a8a0e34c23abcbb7f67`.
- Clean campaign PR: #173, `codex/m1-m4-m7-m8-core-intelligence-final`.
- PR #173 is intentionally not self-merged. Its exact final head must pass the
  required C1.01 workflow before Product Owner review.
- Separate open M9/M10 work is not counted in these totals until merged into
  canonical main.

## Independent 600-task audit

| Acceptance state | Count |
| --- | ---: |
| VERIFIED | 411 |
| NOT_AUDITED | 0 |
| IN_PROGRESS | 1 |
| BLOCKED | 7 |
| NOT_IMPLEMENTED | 181 |
| **TOTAL** | **600** |

The machine-readable record is [AUDIT_600.json](AUDIT_600.json); the complete
human-readable audit, evidence policy, per-task decisions and readiness
dashboard are in [AUDIT_600.md](AUDIT_600.md). [TASKS.json](TASKS.json)
remains the canonical task ledger and [TASKS.md](TASKS.md) is generated from it.

## Milestone audit status

| Milestone | VERIFIED | Remaining audit state |
| --- | ---: | --- |
| M0 Trusted Kernel | 40/40 | none |
| M1 Agent Runtime | 43/45 | AX-083 and AX-085 BLOCKED on strict live/full-agent acceptance |
| M2 Hive and Memory | 40/40 | none |
| M3 Procedure Compiler | 50/50 | none |
| M4 Learning Efficiency | 27/30 | AX-198/204/205 BLOCKED on genuine live-model measurements |
| M5 Self-Repair | 40/40 | none |
| M6 Windows Capabilities | 55/55 | milestone scope accepted; release host matrix remains M16 |
| M7 Browser Agent | 35/35 | milestone scope accepted with actual headless Chrome evidence |
| M8 Models and Research | 33/35 | AX-369/370 BLOCKED on genuine external provider acceptance |
| M9 Security | 20/35 | 15 NOT_IMPLEMENTED on canonical main |
| M10 World Model | 16/30 | 14 NOT_IMPLEMENTED on canonical main |
| M11 Voice and HUD | 2/25 | 23 NOT_IMPLEMENTED |
| M12 Scheduling | 1/25 | 24 NOT_IMPLEMENTED |
| M13 Multi-device and Android | 2/30 | 28 NOT_IMPLEMENTED |
| M14 Optimization | 2/25 | 23 NOT_IMPLEMENTED |
| M15 Self-extension | 1/30 | 29 NOT_IMPLEMENTED |
| M16 Production and Release | 4/30 | AX-574 IN_PROGRESS; 25 NOT_IMPLEMENTED |

## Core-intelligence acceptance evidence

M1 now has one L0–L5 cross-strategy AgentLoop benchmark rather than only
separate strategy tests. A real-Chrome L1 AgentLoop vertical also proves
`AgentLoop -> Executor -> ActionGate -> browser -> independent verification`,
but it does not replace the stricter missing natural-goal/live model-research
vertical required by AX-083. AX-085 therefore remains blocked on AX-083.

M4 now has one executable cold L4 -> canonical metrics -> causal experience ->
compile -> varied validation -> ACTIVE promotion -> persistence -> fresh-process
restart -> related L2 warm-reuse chain. The remaining M4 tasks require genuine
provider-backed measurements; controlled-provider numbers are not represented
as real-model efficiency evidence.

M7 has a concrete W3C WebDriver provider with production process-launch and
JavaScript/CDP escape hatches excluded. Canonical Windows CI drives actual
headless Chrome through a deterministic localhost fixture and verifies forms,
explicit submission/redirects, upload/download, cookies/auth state, tabs,
fresh-DOM recovery, multi-page operation, sensitive-value redaction and hostile
page isolation. The observed browser evidence is Chrome 152.0.7977.83 on
Windows Server 2025 build 10.0.26100 in headless mode. This does not imply
arbitrary third-party account/site compatibility.

M8 has provider-independent bounded retry/fallback/health/capability selection,
canonical Reasoner/ResourceBudget integration, and a verified-research handoff
that carries only inert data. A fail-closed live-provider acceptance entry point
exists, but no genuine model/research credential was available during this
campaign, so AX-369/370 remain BLOCKED.

## Evidence boundaries that must not be blurred

M6 and M7 have real hosted Windows evidence, but the host is Windows Server
2025 and not an interactive Windows 10/11 release matrix. This does not prove
AX-576/577, multi-DPI/multi-monitor coverage, or installer/upgrade acceptance.

A localhost real-browser fixture proves browser mechanics in an actual Chrome
process. It does not automatically prove external authentication against
arbitrary third-party services.

No configured live model/research credential was available. Scripted,
controlled or loopback providers are therefore not used to satisfy tasks whose
exact wording requires a real service.

## Product and release posture

The core runtime, persistent memory, procedure compiler/reuse, self-repair,
Windows capability fabric and browser milestone are substantial and strongly
governed. However, the CLI still exposes package metadata/help rather than a
complete natural-language product session, and later product/device/release
milestones remain open.

Implementation coverage, strict acceptance coverage, external-environment
readiness, product/UI readiness and release readiness are reported separately
in [AUDIT_600.md](AUDIT_600.md). A green core-runtime milestone is not treated
as proof that AgentX v1 is release-ready.
