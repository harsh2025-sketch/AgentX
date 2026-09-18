"""Focused M12 policy-boundary and quota concurrency tests."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from agentx.agent_loop import OrchestrationRequest
from agentx.cognition.router import ExecutionLevel
from agentx.core.execution import ExecutionContext
from agentx.core.scheduling import ScheduledTaskIntent
from agentx.core.tasks import Task
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.schedule_store import ScheduledTaskStore
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

_T0 = datetime(2026, 9, 18, 23, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _T0


class _Factory:
    def __init__(self, harness: OrchestrationHarness) -> None:
        self._harness = harness

    def build(
        self,
        *,
        task: Task,
        context: ExecutionContext,
        intent: ScheduledTaskIntent,
    ) -> OrchestrationRequest:
        del intent
        return self._harness.make_request(task=task, context=context)


class _Sink:
    def __init__(self) -> None:
        self.notifications: list[Notification] = []

    def notify(self, notification: Notification) -> None:
        self.notifications.append(notification)


def _runtime(path: Path) -> tuple[Scheduler, OrchestrationHarness, ScheduledTaskStore]:
    harness = OrchestrationHarness()
    strategy = harness.governed_strategy()
    loop = harness.agent_loop({level: strategy for level in ExecutionLevel})
    store = ScheduledTaskStore(SQLiteDatabase(path))
    scheduler = Scheduler(
        store=store,
        launcher=GovernedTaskLauncher(
            task_manager=harness.task_manager,
            agent_loop=loop,
            request_factory=_Factory(harness),
            run_store=store,
        ),
        clock=_Clock(),
    )
    return scheduler, harness, store


def _intent(
    *,
    resource_units: int = 0,
    model_calls: int = 0,
    machine_actions: int = 0,
) -> ScheduledTaskIntent:
    return ScheduledTaskIntent(
        objective="bounded policy test",
        route_key="demo.note",
        source="m12.policy.test",
        estimated_resource_units=resource_units,
        estimated_model_calls=model_calls,
        estimated_machine_actions=machine_actions,
    )


def _quota(
    store: ScheduledTaskStore,
    *,
    scope: str,
    maxima: tuple[int, int, int] = (1, 1, 1),
) -> BackgroundQuotaPolicy:
    return BackgroundQuotaPolicy(
        store=store,
        scope=scope,
        limits=BackgroundQuotaLimits(
            window=timedelta(hours=1),
            max_resource_units=maxima[0],
            max_model_calls=maxima[1],
            max_machine_actions=maxima[2],
        ),
    )


def test_confidence_quiet_hours_and_notification_are_deny_only(tmp_path: Path) -> None:
    scheduler, harness, store = _runtime(tmp_path / "m12.sqlite3")
    opt_in = OptInPolicy(store)
    opt_in.set_enabled(True)
    sink = _Sink()
    controller = ProactiveController(
        scheduler=scheduler,
        opt_in=opt_in,
        confidence=ConfidencePolicy(minimum=0.8),
        quota=_quota(store, scope="controller", maxima=(10, 10, 10)),
        stale_guard=StaleActionGuard(WorldModel()),
        quiet_hours=QuietHoursPolicy(
            start=time(22, 0),
            end=time(7, 0),
            timezone="UTC",
        ),
        notifications=sink,
    )
    low = ProactiveRecommendation.create(
        intent=_intent(machine_actions=1),
        confidence=0.79,
        observed_at=_T0,
        expires_at=_T0 + timedelta(minutes=5),
    )
    assert (
        controller.execute(low, now=_T0, attention=AttentionState.AVAILABLE).decision
        is ProactiveDecision.LOW_CONFIDENCE
    )

    confident = ProactiveRecommendation.create(
        intent=_intent(machine_actions=1),
        confidence=0.8,
        observed_at=_T0,
        expires_at=_T0 + timedelta(minutes=5),
    )
    assert (
        controller.execute(confident, now=_T0, attention=AttentionState.AVAILABLE).decision
        is ProactiveDecision.QUIET_HOURS
    )
    assert harness.capability.execute_calls == 0
    assert [item.action_reference for item in sink.notifications] == [
        str(low.recommendation_id),
        str(confident.recommendation_id),
    ]


def test_resource_model_and_machine_quotas_exhaust_independently(tmp_path: Path) -> None:
    store = ScheduledTaskStore(SQLiteDatabase(tmp_path / "m12.sqlite3"))
    cases = (
        ("resource", _intent(resource_units=1)),
        ("model", _intent(model_calls=1)),
        ("machine", _intent(machine_actions=1)),
    )
    for scope, intent in cases:
        policy = _quota(store, scope=scope)
        assert policy.consume(intent, now=_T0) is True
        assert policy.consume(intent, now=_T0) is False


def test_quota_consumption_is_transactional_under_concurrency(tmp_path: Path) -> None:
    store = ScheduledTaskStore(SQLiteDatabase(tmp_path / "m12.sqlite3"))
    # Initialize the canonical schema before racing quota transactions; schema
    # migration itself is startup work, not the concurrency behavior under test.
    with store.database.connection():
        pass
    policy = _quota(store, scope="concurrent", maxima=(1, 0, 0))
    intent = _intent(resource_units=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: policy.consume(intent, now=_T0), range(2)))

    assert sorted(results) == [False, True]


def test_corrupt_opt_in_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "m12.sqlite3"
    store = ScheduledTaskStore(SQLiteDatabase(path))
    opt_in = OptInPolicy(store)
    opt_in.set_enabled(True)
    assert opt_in.enabled() is True

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE agentx_m12_policy SET value_json=? WHERE policy_key=?",
            ("not-json", "proactivity.opt_in"),
        )
        connection.commit()

    assert opt_in.enabled() is False
