"""M12 scheduler persistence, timing, cancellation, and quota tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from agentx.agent_loop import OrchestrationRequest
from agentx.cognition.router import ExecutionLevel
from agentx.core.execution import ExecutionContext
from agentx.core.scheduling import (
    BackgroundRunStatus,
    ScheduledTask,
    ScheduledTaskIntent,
    ScheduleStatus,
)
from agentx.core.tasks import Task
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.schedule_store import CorruptScheduleError, ScheduledTaskStore
from agentx.proactivity import (
    AttentionState,
    BackgroundQuotaLimits,
    BackgroundQuotaPolicy,
    ConfidencePolicy,
    Notification,
    OptInPolicy,
    ProactiveController,
    ProactiveDecision,
    ProactiveRecommendation,
    QuietHoursPolicy,
    StaleActionGuard,
)
from agentx.scheduling import GovernedTaskLauncher, Scheduler
from agentx.world_model import WorldModel
from tests.support.orchestration_harness import OrchestrationHarness

_T0 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, now: datetime = _T0) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


class _Factory:
    def __init__(self, harness: OrchestrationHarness) -> None:
        self.harness = harness

    def build(
        self,
        *,
        task: Task,
        context: ExecutionContext,
        intent: ScheduledTaskIntent,
    ) -> OrchestrationRequest:
        del intent
        return self.harness.make_request(task=task, context=context)


def _scheduler(
    path: Path,
    clock: _Clock,
) -> tuple[Scheduler, OrchestrationHarness, ScheduledTaskStore]:
    harness = OrchestrationHarness()
    strategy = harness.governed_strategy()
    loop = harness.agent_loop({level: strategy for level in ExecutionLevel})
    store = ScheduledTaskStore(SQLiteDatabase(path))
    launcher = GovernedTaskLauncher(
        task_manager=harness.task_manager,
        agent_loop=loop,
        request_factory=_Factory(harness),
        run_store=store,
    )
    return Scheduler(store=store, launcher=launcher, clock=clock), harness, store


def _intent(
    *,
    model_calls: int = 0,
    machine_actions: int = 1,
    resource_units: int = 1,
) -> ScheduledTaskIntent:
    return ScheduledTaskIntent(
        objective="write the scheduled demo note",
        route_key="demo.note",
        source="m12.test",
        estimated_resource_units=resource_units,
        estimated_model_calls=model_calls,
        estimated_machine_actions=machine_actions,
    )


def test_one_shot_executes_once_through_governed_runtime(tmp_path: Path) -> None:
    clock = _Clock()
    scheduler, harness, store = _scheduler(tmp_path / "m12.sqlite3", clock)
    scheduled = scheduler.register_one_shot(intent=_intent(), due_at=_T0)

    first = scheduler.tick()
    second = scheduler.tick()

    assert first.attempted == 1
    assert first.verified == 1
    assert second.attempted == 0
    assert harness.capability.execute_calls == 1
    assert harness.capability.verify_calls == 1
    persisted = store.get(scheduled.schedule_id)
    assert persisted is not None
    assert persisted.status is ScheduleStatus.COMPLETED
    assert persisted.run_count == 1


def test_recurring_schedule_coalesces_missed_runs_and_cancels(tmp_path: Path) -> None:
    clock = _Clock()
    scheduler, harness, store = _scheduler(tmp_path / "m12.sqlite3", clock)
    scheduled = scheduler.register_recurring(
        intent=_intent(),
        first_due_at=_T0,
        interval=timedelta(minutes=1),
    )

    assert scheduler.tick().verified == 1
    clock.current = _T0 + timedelta(minutes=10)
    assert scheduler.tick().verified == 1
    persisted = store.get(scheduled.schedule_id)
    assert persisted is not None
    assert persisted.run_count == 2
    assert persisted.next_due_at == _T0 + timedelta(minutes=11)

    scheduler.cancel(scheduled.schedule_id)
    clock.current = _T0 + timedelta(hours=1)
    assert scheduler.tick().attempted == 0
    assert harness.capability.execute_calls == 2
    cancelled = store.get(scheduled.schedule_id)
    assert cancelled is not None
    assert cancelled.status is ScheduleStatus.CANCELLED


def test_restart_marks_interrupted_claim_for_reconciliation(tmp_path: Path) -> None:
    path = tmp_path / "m12.sqlite3"
    clock = _Clock()
    scheduler, _harness, store = _scheduler(path, clock)
    scheduled = scheduler.register_one_shot(intent=_intent(), due_at=_T0)
    claims = store.claim_due(now=_T0)

    assert len(claims) == 1
    dispatching = store.get(scheduled.schedule_id)
    assert dispatching is not None
    assert dispatching.status is ScheduleStatus.DISPATCHING

    restarted, _harness2, restarted_store = _scheduler(path, clock)
    recovered = restarted.recover_after_restart()

    assert len(recovered) == 1
    current = restarted_store.get(scheduled.schedule_id)
    assert current is not None
    assert current.status is ScheduleStatus.RECONCILIATION_REQUIRED
    assert restarted.tick().attempted == 0


def test_two_store_instances_cannot_claim_same_due_schedule(tmp_path: Path) -> None:
    path = tmp_path / "m12.sqlite3"
    first = ScheduledTaskStore(SQLiteDatabase(path))
    second = ScheduledTaskStore(SQLiteDatabase(path))

    schedule = ScheduledTask.one_shot(
        intent=_intent(),
        due_at=_T0,
        created_at=_T0,
    )
    first.create(schedule)

    claims_a = first.claim_due(now=_T0)
    claims_b = second.claim_due(now=_T0)

    assert len(claims_a) == 1
    assert claims_b == ()


def test_opt_in_quiet_hours_attention_and_quota_fail_closed(tmp_path: Path) -> None:
    clock = _Clock()
    scheduler, _harness, store = _scheduler(tmp_path / "m12.sqlite3", clock)
    opt_in = OptInPolicy(store)
    quota = BackgroundQuotaPolicy(
        store=store,
        limits=BackgroundQuotaLimits(
            window=timedelta(hours=1),
            max_resource_units=1,
            max_model_calls=0,
            max_machine_actions=1,
        ),
    )
    controller = ProactiveController(
        scheduler=scheduler,
        opt_in=opt_in,
        confidence=ConfidencePolicy(minimum=0.8),
        quota=quota,
        stale_guard=StaleActionGuard(WorldModel()),
    )
    recommendation = ProactiveRecommendation.create(
        intent=_intent(),
        confidence=0.9,
        observed_at=_T0,
        expires_at=_T0 + timedelta(minutes=5),
    )

    assert (
        controller.execute(
            recommendation,
            now=_T0,
            attention=AttentionState.AVAILABLE,
        ).decision
        is ProactiveDecision.OPTED_OUT
    )

    opt_in.set_enabled(True)
    assert (
        controller.execute(
            recommendation,
            now=_T0,
            attention=AttentionState.UNKNOWN,
        ).decision
        is ProactiveDecision.ATTENTION_UNAVAILABLE
    )

    first = controller.execute(
        recommendation,
        now=_T0,
        attention=AttentionState.AVAILABLE,
    )
    assert first.decision is ProactiveDecision.SCHEDULED

    another = ProactiveRecommendation.create(
        intent=_intent(),
        confidence=0.9,
        observed_at=_T0,
        expires_at=_T0 + timedelta(minutes=5),
    )
    assert (
        controller.execute(
            another,
            now=_T0,
            attention=AttentionState.AVAILABLE,
        ).decision
        is ProactiveDecision.QUOTA_EXHAUSTED
    )


def test_overnight_quiet_hours_boundaries() -> None:
    policy = QuietHoursPolicy(
        start=time(22, 0),
        end=time(7, 0),
        timezone="UTC",
    )

    assert policy.is_quiet(datetime(2026, 9, 18, 22, 0, tzinfo=UTC))
    assert policy.is_quiet(datetime(2026, 9, 19, 6, 59, tzinfo=UTC))
    assert not policy.is_quiet(datetime(2026, 9, 19, 7, 0, tzinfo=UTC))


class _Notifications:
    def __init__(self) -> None:
        self.items: list[Notification] = []

    def notify(self, notification: Notification) -> None:
        self.items.append(notification)


def _quota(
    store: ScheduledTaskStore,
    *,
    scope: str,
    resources: int = 10,
    model_calls: int = 10,
    machine_actions: int = 10,
) -> BackgroundQuotaPolicy:
    return BackgroundQuotaPolicy(
        store=store,
        scope=scope,
        limits=BackgroundQuotaLimits(
            window=timedelta(hours=1),
            max_resource_units=resources,
            max_model_calls=model_calls,
            max_machine_actions=machine_actions,
        ),
    )


def test_confidence_quiet_hours_notifications_and_opt_in_revocation(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    scheduler, harness, store = _scheduler(tmp_path / "m12.sqlite3", clock)
    opt_in = OptInPolicy(store)
    opt_in.set_enabled(True)
    notifications = _Notifications()
    controller = ProactiveController(
        scheduler=scheduler,
        opt_in=opt_in,
        confidence=ConfidencePolicy(minimum=0.8),
        quota=_quota(store, scope="policy-edge"),
        stale_guard=StaleActionGuard(WorldModel()),
        notifications=notifications,
    )
    low_confidence = ProactiveRecommendation.create(
        intent=_intent(),
        confidence=0.79,
        observed_at=_T0,
        expires_at=_T0 + timedelta(minutes=5),
    )

    low = controller.execute(
        low_confidence,
        now=_T0,
        attention=AttentionState.AVAILABLE,
    )

    assert low.decision is ProactiveDecision.LOW_CONFIDENCE
    assert harness.capability.execute_calls == 0
    assert len(notifications.items) == 1
    assert low_confidence.intent.objective not in notifications.items[0].message
    assert notifications.items[0].action_reference == str(low_confidence.recommendation_id)

    quiet_controller = ProactiveController(
        scheduler=scheduler,
        opt_in=opt_in,
        confidence=ConfidencePolicy(minimum=0.8),
        quota=_quota(store, scope="quiet-edge"),
        stale_guard=StaleActionGuard(WorldModel()),
        quiet_hours=QuietHoursPolicy(
            start=time(11, 0),
            end=time(13, 0),
            timezone="UTC",
        ),
        notifications=notifications,
    )
    quiet_recommendation = ProactiveRecommendation.create(
        intent=_intent(),
        confidence=0.9,
        observed_at=_T0,
        expires_at=_T0 + timedelta(minutes=5),
    )
    quiet = quiet_controller.execute(
        quiet_recommendation,
        now=_T0,
        attention=AttentionState.AVAILABLE,
    )

    assert quiet.decision is ProactiveDecision.QUIET_HOURS
    assert harness.capability.execute_calls == 0

    opt_in.revoke()
    opted_out = quiet_controller.execute(
        quiet_recommendation,
        now=_T0,
        attention=AttentionState.AVAILABLE,
    )
    assert opted_out.decision is ProactiveDecision.OPTED_OUT

    with store.database.connection() as connection:
        connection.execute(
            "UPDATE agentx_m12_policy SET value_json = ? WHERE policy_key = ?",
            ("{", "proactivity.opt_in"),
        )
        connection.commit()
    assert opt_in.enabled() is False


def test_background_quota_categories_reset_and_concurrent_consumption(
    tmp_path: Path,
) -> None:
    path = tmp_path / "m12.sqlite3"
    store = ScheduledTaskStore(SQLiteDatabase(path))
    store.set_policy("m12.test.initialized", True)

    cases = (
        ("resource", _intent(resource_units=1, model_calls=0, machine_actions=0)),
        ("model", _intent(resource_units=0, model_calls=1, machine_actions=0)),
        ("machine", _intent(resource_units=0, model_calls=0, machine_actions=1)),
    )
    for scope, intent in cases:
        quota = _quota(
            store,
            scope=f"quota.{scope}",
            resources=1,
            model_calls=1,
            machine_actions=1,
        )
        assert quota.consume(intent, now=_T0)
        assert not quota.consume(intent, now=_T0)
        assert quota.consume(intent, now=_T0 + timedelta(hours=1))

    def consume_once(_: int) -> bool:
        worker_store = ScheduledTaskStore(SQLiteDatabase(path))
        return _quota(
            worker_store,
            scope="quota.concurrent",
            resources=1,
            model_calls=0,
            machine_actions=0,
        ).consume(
            _intent(resource_units=1, model_calls=0, machine_actions=0),
            now=_T0,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = tuple(pool.map(consume_once, range(4)))

    assert sum(outcomes) == 1


def test_corrupt_schedule_fails_closed_and_interrupted_run_recovers_uncertain(
    tmp_path: Path,
) -> None:
    path = tmp_path / "corrupt.sqlite3"
    store = ScheduledTaskStore(SQLiteDatabase(path))
    schedule = ScheduledTask.one_shot(
        intent=_intent(),
        due_at=_T0,
        created_at=_T0,
    )
    store.create(schedule)
    with store.database.connection() as connection:
        connection.execute(
            "UPDATE agentx_scheduled_tasks SET record_json = ? WHERE schedule_id = ?",
            ("{", str(schedule.schedule_id)),
        )
        connection.commit()

    with pytest.raises(CorruptScheduleError):
        store.get(schedule.schedule_id)

    run_path = tmp_path / "run.sqlite3"
    run_store = ScheduledTaskStore(SQLiteDatabase(run_path))
    dispatch_id = uuid4()
    assert run_store.reserve_run(
        dispatch_id=dispatch_id,
        schedule_id=None,
        intent=_intent(),
        created_at=_T0,
    )

    reopened = ScheduledTaskStore(SQLiteDatabase(run_path))
    before = reopened.get_run(dispatch_id)
    assert before is not None
    assert before.status is BackgroundRunStatus.RESERVED

    recovered = reopened.recover_interrupted_runs(recovered_at=_T0 + timedelta(seconds=1))
    assert recovered == (dispatch_id,)
    after = reopened.get_run(dispatch_id)
    assert after is not None
    assert after.status is BackgroundRunStatus.UNCERTAIN


def test_scheduler_shutdown_rejects_new_background_work(tmp_path: Path) -> None:
    scheduler, _harness, _store = _scheduler(tmp_path / "m12.sqlite3", _Clock())

    scheduler.shutdown(reason="emergency stop")

    with pytest.raises(RuntimeError, match="shut down"):
        scheduler.register_one_shot(intent=_intent(), due_at=_T0)


def test_verified_background_task_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "m12.sqlite3"
    scheduler, _harness, _store = _scheduler(path, _Clock())
    scheduled = scheduler.register_one_shot(intent=_intent(), due_at=_T0)

    report = scheduler.tick()

    assert report.verified == 1
    assert len(report.reports) == 1
    dispatch_id = report.reports[0].dispatch_id

    reopened = ScheduledTaskStore(SQLiteDatabase(path))
    run = reopened.get_run(dispatch_id)
    assert run is not None
    assert run.schedule_id == scheduled.schedule_id
    assert run.status is BackgroundRunStatus.VERIFIED
    assert run.task is not None
    assert run.task.objective == _intent().objective


def test_busy_attention_state_suppresses_proactive_execution(tmp_path: Path) -> None:
    scheduler, harness, store = _scheduler(tmp_path / "m12.sqlite3", _Clock())
    opt_in = OptInPolicy(store)
    opt_in.set_enabled(True)
    controller = ProactiveController(
        scheduler=scheduler,
        opt_in=opt_in,
        confidence=ConfidencePolicy(minimum=0.5),
        quota=_quota(store, scope="attention-busy"),
        stale_guard=StaleActionGuard(WorldModel()),
    )
    recommendation = ProactiveRecommendation.create(
        intent=_intent(),
        confidence=0.9,
        observed_at=_T0,
        expires_at=_T0 + timedelta(minutes=5),
    )

    report = controller.execute(
        recommendation,
        now=_T0,
        attention=AttentionState.BUSY,
    )

    assert report.decision is ProactiveDecision.ATTENTION_UNAVAILABLE
    assert harness.capability.execute_calls == 0
