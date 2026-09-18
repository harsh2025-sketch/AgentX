# M12 Scheduling, Event Watchers, and Proactive Execution

M12 extends AgentX with bounded, durable background work without creating a second authority path.

## Security boundary

Scheduled records, event payloads, browser/device observations, World Model values, notification text, and recommendation metadata are inert data. They cannot grant a Permission, lower risk, widen resource budgets, clear EmergencyStop, fabricate verification, or bypass ActionGate.

Durable schedules never serialize Python callbacks, closures, process handles, browser objects, live approvals, authority contexts, pickle payloads, shell commands, or dynamic imports.

A due schedule is converted by trusted composition into a canonical Task and then routed through:

TaskManager -> AgentLoop -> Executor -> CapabilityExecutionLoop -> ActionGate -> ResourceBudget -> capability -> independent verification

## Scheduled-task contract

agentx.core.scheduling.ScheduledTaskIntent is the durable work-intent contract. It contains a bounded objective, a trusted composition route key, source, Task priority, inert JSON context, and conservative background resource estimates.

ScheduledTask owns schedule identity, one-shot/interval timing, lifecycle, next due time, expiry, bounded run count, and the current durable dispatch identity.

One-shot schedules terminate after one attempt. Recurring schedules calculate the first interval boundary strictly after the current clock, so downtime coalesces missed occurrences instead of replaying an unbounded backlog.

## Scheduler

agentx.scheduling.Scheduler is explicitly ticked. It creates no daemon thread, busy-wait loop, or unbounded poller. Tests can inject a wall clock.

Due records are transactionally claimed by ScheduledTaskStore. SQLite BEGIN IMMEDIATE serialization plus conditional update state prevents two workers from owning the same due record.

Cancellation persists CANCELLED and propagates cooperative cancellation to an active dispatch. Shutdown also requests cancellation for active dispatches.

## Persistence and restart

Canonical persistence migration 9 adds agentx_scheduled_tasks, agentx_background_runs, agentx_m12_policy, and agentx_m12_quota.

A schedule survives SQLite reopen. A process restart that discovers a schedule in DISPATCHING does not blindly replay it. The record becomes RECONCILIATION_REQUIRED; nonterminal background-run records become UNCERTAIN. This is deliberate fail-closed handling for the crash-after-side-effect/before-commit ambiguity.

Cancelled, completed, failed, expired, and reconciliation-required schedules are not redispatched automatically.

## Event watchers

M12 preserves agentx.infrastructure.event_watcher.EventWatcherFramework (AX-465) and adds bounded concrete sources in agentx.event_triggers:

- filesystem metadata snapshots and create/modify/rename/delete changes;
- canonical Windows process-discovery state differences;
- browser-page state differences through the existing World Model;
- device-state differences through the existing provider-neutral World Model/device contracts.

Watcher payloads never select arbitrary capabilities. EventTriggeredTaskLauncher accepts only pre-registered (source_id, event_key) -> ScheduledTaskIntent rules. Event-derived schedule IDs are deterministic, making replay idempotent.

## Proactivity

ProactiveRecommendation represents a recommendation separately from execution.

ProactiveController requires durable explicit opt-in, a non-expired recommendation, confidence threshold, quiet-hours policy, explicit AVAILABLE attention state, fresh/current M10 World Model references, exact short-lived approval when required, and durable background quota capacity.

Only after those checks is a deterministic one-shot schedule submitted to the governed scheduler.

## Quiet hours and attention

Quiet hours use an explicit IANA timezone and support ordinary and overnight ranges. They suppress proactive execution; they do not disable kernel safety controls.

Attention is a closed AVAILABLE, BUSY, UNKNOWN vocabulary. UNKNOWN is not treated as availability. M12 performs no invasive surveillance to infer it.

## Background quotas

BackgroundQuotaPolicy is an additional ceiling for resource units, model calls, and machine actions in a bounded time window. It can only reject work and does not replace or widen canonical C1.08 ResourceBudget limits.

Quota consumption is transactional and durable so concurrent callers and restart cannot silently reset background usage.

## Approval expiry

ProactiveApproval is evidence only. It is bound to the exact recommendation ID, canonical intent digest, captured world-state digests, issue time, and expiry. It is never a Permission or ActionGate result.

Expired, replayed, or mismatched evidence is rejected before scheduling. Canonical A1.10 human approval remains authoritative for capabilities whose ActionGate decision requires confirmation.

## Stale-action rejection

StaleActionGuard captures references from actual WorldModel.cache lookups only when they are fresh. Immediately before proactive execution every reference is refreshed/revalidated. Stale, invalidated, unavailable, or changed state rejects execution.

## Failure behavior

Expected fail-closed outcomes include invalid/corrupt schedule records, unsupported schema, quota exhaustion, opt-out, quiet hours, unknown attention, approval expiry, stale world state, duplicate event replay, scheduler shutdown, cancellation, and interrupted dispatch reconciliation.

One watcher-source failure remains isolated by the existing AX-465 framework.

## Acceptance coverage

M12 tests cover one-shot exactly-once behavior; recurring execution, missed-run coalescing and cancellation; competing durable claims; SQLite reopen/restart reconciliation; real controlled filesystem change to watcher to pre-registered event rule to governed Task to canonical verification; opt-in/confidence/quiet-hours/attention/quotas; approval expiry/mismatch; World Model invalidation; hostile event/schedule content; and durable event replay suppression.

No M11 or M14 implementation is required. Device watchers use the device/world-model contracts already on main; M12 does not fabricate an unmerged M13 Android provider or physical-device evidence.
