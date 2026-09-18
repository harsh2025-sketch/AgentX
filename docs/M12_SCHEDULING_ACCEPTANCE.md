# M12 Scheduling, Event Watchers & Proactive Execution — Acceptance

Repository: `harsh2025-sketch/AgentX`  
Task range: `AX-461..AX-485`  
Starting main: `446771fab52278b4f12fa752fb000bd4668a5241`  
Implementation acceptance head: `497811f85abd0d981f5e6ad22c3c3d8f3f43d310`  
Pull request: #177  
Implementation-head quality run: `35367913564`

## Result

M12 is accepted as **25/25 VERIFIED** on the implementation candidate.

The implementation-head quality run passed:

- Ruff lint
- Ruff format check
- mypy over 706 source files
- 600-task ledger validation
- hosted Windows real-host regression
- real headless Chrome browser regression
- full pytest: **11295 passed, 9 skipped**

This report does not claim the PR is merged. A final exact-head CI run is required after the audit/ledger documentation commits, and post-merge main CI remains a separate integration responsibility.

## Production architecture

M12 adds a typed durable scheduled-work contract, a bounded explicitly-ticked scheduler, canonical SQLite persistence, restart reconciliation, concrete event sources, a pre-registered event-to-task boundary, notifications, proactive recommendations, deny-only policy gates, durable background quotas, exact short-lived proactive approval evidence, and M10 World Model stale-state revalidation.

Scheduled work flows through the existing governed runtime rather than a second execution path:

`ScheduledTask -> Scheduler -> TaskManager -> AgentLoop -> Executor -> CapabilityExecutionLoop -> ActionGate -> ResourceBudget -> capability -> independent verification`

Events and proactive metadata are data only. They cannot grant Permission, lower risk, widen budgets, clear EmergencyStop, self-confirm, or fabricate verification.

## Task-by-task audit

| Task | Requirement | Acceptance | Evidence |
| --- | --- | --- | --- |
| AX-461 | scheduler core | VERIFIED / E4 | `src/agentx/scheduling.py`, durable claims, cancellation/shutdown, governed dispatch, integration acceptance |
| AX-462 | scheduled-task contract | VERIFIED / E3 | `src/agentx/core/scheduling.py`, typed JSON-only intent/schedule records, hostile-context rejection |
| AX-463 | one-shot scheduled tasks | VERIFIED / E4 | due -> claim -> governed launch -> verification -> terminal; duplicate tick suppressed |
| AX-464 | recurring scheduled tasks | VERIFIED / E4 | interval scheduling, missed-run coalescing, max-runs, cancellation, no backlog storm |
| AX-465 | event watcher framework | VERIFIED / E2 | existing canonical watcher framework retained and revalidated |
| AX-466 | event-triggered task launch | VERIFIED / E4 | pre-registered source/event rule -> deterministic schedule -> governed task; replay deduplicated |
| AX-467 | file-change trigger | VERIFIED / E4 | real controlled create/modify/rename/delete plus cancellation; real file->watcher->task E2E |
| AX-468 | process-state trigger | VERIFIED / E5 | appeared/exited/changed unit behavior plus real controlled Windows child process lifecycle |
| AX-469 | browser-state trigger | VERIFIED / E4 | existing M7/M10 browser state boundary; hostile page authority remains denied |
| AX-470 | device-state trigger | VERIFIED / E3 | provider-neutral M10 device World Model state change boundary; no fabricated Android provider |
| AX-471 | notification boundary | VERIFIED / E3 | typed notification sink, denial notification, no execution authority or sensitive raw payload |
| AX-472 | proactive recommendation contract | VERIFIED / E3 | recommendation remains distinct from scheduling/execution |
| AX-473 | proactive confidence policy | VERIFIED / E3 | threshold boundary tested; low confidence suppresses action |
| AX-474 | user opt-in policy | VERIFIED / E3 | missing/corrupt/revoked state fails closed; durable opt-in |
| AX-475 | quiet-hours policy | VERIFIED / E3 | ordinary/overnight boundaries, UTC portability, suppression without weakening safety |
| AX-476 | attention-awareness policy | VERIFIED / E3 | AVAILABLE/BUSY/UNKNOWN; BUSY and UNKNOWN suppress execution |
| AX-477 | background resource quota | VERIFIED / E3 | durable windowed resource ceiling, exhaustion/reset/concurrency |
| AX-478 | background model-call quota | VERIFIED / E3 | durable independent model-call ceiling, exhaustion/reset |
| AX-479 | background machine-action quota | VERIFIED / E3 | durable independent machine-action ceiling, exhaustion/reset |
| AX-480 | long-running Task persistence | VERIFIED / E4 | background run/task data persists across SQLite reopen; live authority is not serialized |
| AX-481 | restart recovery for scheduled work | VERIFIED / E4 | DISPATCHING -> RECONCILIATION_REQUIRED; RESERVED/RUNNING -> UNCERTAIN; no blind replay |
| AX-482 | approval expiration semantics | VERIFIED / E3 | exact recommendation/intent/state binding, TTL, expired/mismatched rejection |
| AX-483 | stale proactive-action rejection | VERIFIED / E4 | real World Model capture/invalidation/revalidation rejects changed state before execution |
| AX-484 | proactive adversarial tests | VERIFIED / E3 | hostile schedule/event metadata, authority smuggling, replay, stale approvals and stale world-state tests |
| AX-485 | M12 milestone acceptance | VERIFIED / E4 | one-shot, recurring, event, restart, proactive, stale/security and real-host paths integrated |

## Failure and restart semantics

Invalid or corrupt durable records fail closed. Unknown/unsupported schedule data is not dynamically loaded. There is no `eval`, `exec`, stored Python callback, pickle callable, shell payload, or untrusted dynamic import path.

An interrupted dispatch is deliberately **not** advertised as distributed exactly-once. AgentX uses durable claiming/idempotency and fails ambiguous crash-after-side-effect state into reconciliation-required/uncertain status instead of blindly replaying it.

Recurring missed work is coalesced to a bounded future due point rather than unbounded catch-up replay.

## Security acceptance

The M9 invariant is preserved:

**UNTRUSTED CONTENT = DATA, NEVER AUTHORITY.**

Tests cover hostile content attempting to smuggle permission, approval, command text and ActionGate bypass directives. Event payloads never choose arbitrary capabilities; only trusted pre-registered rules describe intended work.

Proactive approval is evidence only and does not replace canonical ActionGate confirmation.

## External/dependency boundaries

- **M11:** no dependency.
- **M14:** no dependency.
- **M13:** no unmerged dependency is required for M12 acceptance. AX-470 uses the provider-neutral device state contract already available through the M10 World Model. Android transport/provider, real phone hardware, ADB and Android execution remain M13-owned and are not claimed here.
- Hosted real Windows evidence is Windows Server 2025, not a Windows 10/11 release matrix.
- M7 browser regression uses real headless Chrome on controlled local pages.

## Final acceptance state

`TASKS_TOTAL=25`  
`VERIFIED=25`  
`BLOCKED=0`  
`IN_PROGRESS=0`  
`NOT_IMPLEMENTED=0`

Implementation acceptance is complete. The PR remains intentionally unmerged pending final exact-head CI and the separate merge/post-merge-main validation phase.
