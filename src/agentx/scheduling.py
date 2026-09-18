"""Bounded durable M12 scheduler routed through canonical AgentX orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import Protocol
from uuid import UUID, uuid4

from agentx.agent_loop import AgentLoop, OrchestrationRequest
from agentx.cognition.task_manager import TaskManager
from agentx.core.execution import CancellationSource, CancellationToken, ExecutionContext
from agentx.core.scheduling import (
    BackgroundRunStatus,
    ScheduledTask,
    ScheduledTaskIntent,
    ScheduleStatus,
)
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.schedule_store import ScheduleClaim, ScheduledTaskStore


class WallClock(Protocol):
    def now(self) -> datetime: ...


class SystemWallClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ScheduledRequestFactory(Protocol):
    def build(
        self,
        *,
        task: Task,
        context: ExecutionContext,
        intent: ScheduledTaskIntent,
    ) -> OrchestrationRequest: ...


class LaunchStatus(StrEnum):
    VERIFIED = "verified"
    NON_VERIFIED = "non_verified"
    REFUSED = "refused"
    DUPLICATE_SUPPRESSED = "duplicate_suppressed"


@dataclass(frozen=True, slots=True, kw_only=True)
class LaunchReport:
    dispatch_id: UUID
    task: Task | None
    status: LaunchStatus
    detail: str

    @property
    def verified(self) -> bool:
        return self.status is LaunchStatus.VERIFIED


class GovernedTaskLauncher:
    """Only trusted composition may map inert schedule data to AgentLoop input."""

    def __init__(
        self,
        *,
        task_manager: TaskManager,
        agent_loop: AgentLoop,
        request_factory: ScheduledRequestFactory,
        run_store: ScheduledTaskStore,
    ) -> None:
        if not isinstance(task_manager, TaskManager):
            raise TypeError("task_manager must be TaskManager")
        if not isinstance(agent_loop, AgentLoop):
            raise TypeError("agent_loop must be AgentLoop")
        if not callable(getattr(request_factory, "build", None)):
            raise TypeError("request_factory must expose build")
        self._task_manager = task_manager
        self._loop = agent_loop
        self._request_factory = request_factory
        self._runs = run_store

    def launch(
        self,
        *,
        dispatch_id: UUID,
        schedule_id: UUID | None,
        intent: ScheduledTaskIntent,
        cancellation_token: CancellationToken,
        launched_at: datetime,
    ) -> LaunchReport:
        existing = self._runs.get_run(dispatch_id)
        if existing is not None and existing.status is not BackgroundRunStatus.RESERVED:
            return LaunchReport(
                dispatch_id=dispatch_id,
                task=existing.task,
                status=LaunchStatus.DUPLICATE_SUPPRESSED,
                detail="durable dispatch identity already executed",
            )
        task = self._task_manager.create(
            intent.objective,
            priority=intent.priority,
            created_at=launched_at,
            metadata={
                "m12_source": intent.source,
                "m12_route_key": intent.route_key,
                "m12_dispatch_id": str(dispatch_id),
                "m12_schedule_id": (None if schedule_id is None else str(schedule_id)),
            },
        )
        self._runs.update_run(
            dispatch_id=dispatch_id,
            status=BackgroundRunStatus.RUNNING,
            task=task,
            at=launched_at,
        )
        context = ExecutionContext(
            correlation_id=uuid4(),
            cancellation_token=cancellation_token,
            task_id=task.task_id,
        )
        request = self._request_factory.build(
            task=task,
            context=context,
            intent=intent,
        )
        if not isinstance(request, OrchestrationRequest):
            raise TypeError("request factory returned non-OrchestrationRequest")
        result = self._loop.run(request)
        if result.is_failure:
            current = self._task_manager.require(task.task_id)
            self._runs.update_run(
                dispatch_id=dispatch_id,
                status=BackgroundRunStatus.FAILED,
                task=current,
                at=launched_at,
                detail=result.unwrap_error().code,
            )
            return LaunchReport(
                dispatch_id=dispatch_id,
                task=current,
                status=LaunchStatus.REFUSED,
                detail=result.unwrap_error().code,
            )
        outcome = result.unwrap()
        status = (
            BackgroundRunStatus.VERIFIED
            if outcome.verified
            else (
                BackgroundRunStatus.CANCELLED
                if outcome.task.status is TaskStatus.CANCELLED
                else BackgroundRunStatus.FAILED
            )
        )
        self._runs.update_run(
            dispatch_id=dispatch_id,
            status=status,
            task=outcome.task,
            at=launched_at,
            detail=None if outcome.error is None else outcome.error.code,
        )
        return LaunchReport(
            dispatch_id=dispatch_id,
            task=outcome.task,
            status=(LaunchStatus.VERIFIED if outcome.verified else LaunchStatus.NON_VERIFIED),
            detail=(
                "canonical verification succeeded"
                if outcome.verified
                else outcome.stop_reason.value
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class DispatchReport:
    schedule_id: UUID
    dispatch_id: UUID
    launch: LaunchReport | None
    schedule_status: ScheduleStatus
    error_type: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SchedulerTickReport:
    attempted: int
    verified: int
    failures: int
    reports: tuple[DispatchReport, ...]


class Scheduler:
    """Explicitly ticked scheduler: no daemon thread, busy wait, or unbounded polling."""

    def __init__(
        self,
        *,
        store: ScheduledTaskStore,
        launcher: GovernedTaskLauncher,
        clock: WallClock | None = None,
    ) -> None:
        if not isinstance(store, ScheduledTaskStore):
            raise TypeError("store must be ScheduledTaskStore")
        if not isinstance(launcher, GovernedTaskLauncher):
            raise TypeError("launcher must be GovernedTaskLauncher")
        self._store = store
        self._launcher = launcher
        self._clock = SystemWallClock() if clock is None else clock
        self._lock = Lock()
        self._active: dict[UUID, CancellationSource] = {}
        self._shutdown = False

    @property
    def store(self) -> ScheduledTaskStore:
        return self._store

    def register_immediate(
        self,
        *,
        intent: ScheduledTaskIntent,
        schedule_id: UUID | None = None,
        expires_at: datetime | None = None,
    ) -> ScheduledTask:
        """Register work due on the scheduler's trusted injected wall clock."""
        return self.register_one_shot(
            intent=intent,
            due_at=self._clock.now(),
            schedule_id=schedule_id,
            expires_at=expires_at,
        )

    def register_one_shot(
        self,
        *,
        intent: ScheduledTaskIntent,
        due_at: datetime,
        schedule_id: UUID | None = None,
        expires_at: datetime | None = None,
    ) -> ScheduledTask:
        self._running()
        schedule = ScheduledTask.one_shot(
            intent=intent,
            due_at=due_at,
            schedule_id=schedule_id,
            created_at=self._clock.now(),
            expires_at=expires_at,
        )
        self._store.create(schedule)
        return schedule

    def register_recurring(
        self,
        *,
        intent: ScheduledTaskIntent,
        first_due_at: datetime,
        interval: timedelta,
        schedule_id: UUID | None = None,
        max_runs: int | None = None,
    ) -> ScheduledTask:
        self._running()
        schedule = ScheduledTask.recurring(
            intent=intent,
            first_due_at=first_due_at,
            interval=interval,
            schedule_id=schedule_id,
            created_at=self._clock.now(),
            max_runs=max_runs,
        )
        self._store.create(schedule)
        return schedule

    def cancel(self, schedule_id: UUID, *, reason: str = "user cancellation") -> ScheduledTask:
        schedule = self._store.cancel(schedule_id, reason=reason)
        with self._lock:
            source = self._active.get(schedule_id)
        if source is not None:
            source.request_cancellation(reason)
        return schedule

    def recover_after_restart(self) -> tuple[ScheduledTask, ...]:
        self._running()
        recovered_at = self._clock.now()
        schedules = self._store.recover_interrupted_dispatches(recovered_at=recovered_at)
        self._store.recover_interrupted_runs(recovered_at=recovered_at)
        return schedules

    def tick(self, *, max_dispatches: int = 16) -> SchedulerTickReport:
        self._running()
        return self._dispatch(self._store.claim_due(now=self._clock.now(), limit=max_dispatches))

    def dispatch_schedule(self, schedule_id: UUID) -> SchedulerTickReport:
        self._running()
        claim = self._store.claim_one(schedule_id, now=self._clock.now())
        return self._dispatch(()) if claim is None else self._dispatch((claim,))

    def shutdown(self, *, reason: str = "scheduler shutdown") -> None:
        with self._lock:
            self._shutdown = True
            active = tuple(self._active.values())
        for source in active:
            source.request_cancellation(reason)

    def _dispatch(self, claims: tuple[ScheduleClaim, ...]) -> SchedulerTickReport:
        reports = tuple(self._one(claim) for claim in claims)
        verified = sum(report.launch is not None and report.launch.verified for report in reports)
        failures = sum(
            report.error_type is not None
            or (report.launch is not None and report.launch.status is not LaunchStatus.VERIFIED)
            for report in reports
        )
        return SchedulerTickReport(
            attempted=len(claims),
            verified=verified,
            failures=failures,
            reports=reports,
        )

    def _one(self, claim: ScheduleClaim) -> DispatchReport:
        schedule_id = claim.schedule.schedule_id
        cancellation = CancellationSource()
        with self._lock:
            if self._shutdown:
                cancellation.request_cancellation("scheduler shutdown")
            self._active[schedule_id] = cancellation
        try:
            if not self._store.reserve_run(
                dispatch_id=claim.dispatch_id,
                schedule_id=schedule_id,
                intent=claim.schedule.intent,
                created_at=claim.claimed_at,
            ):
                completed = self._store.complete_claim(
                    claim,
                    succeeded=False,
                    completed_at=self._clock.now(),
                    error="duplicate durable dispatch identity",
                )
                return DispatchReport(
                    schedule_id=schedule_id,
                    dispatch_id=claim.dispatch_id,
                    launch=None,
                    schedule_status=completed.status,
                    error_type="DuplicateDispatch",
                )
            launch = self._launcher.launch(
                dispatch_id=claim.dispatch_id,
                schedule_id=schedule_id,
                intent=claim.schedule.intent,
                cancellation_token=cancellation.token,
                launched_at=self._clock.now(),
            )
            completed = self._store.complete_claim(
                claim,
                succeeded=launch.verified,
                completed_at=self._clock.now(),
                error=None if launch.verified else launch.detail,
            )
            return DispatchReport(
                schedule_id=schedule_id,
                dispatch_id=claim.dispatch_id,
                launch=launch,
                schedule_status=completed.status,
            )
        except Exception as exc:
            try:
                reconciled = self._store.mark_reconciliation_required(
                    claim,
                    at=self._clock.now(),
                    reason=f"dispatch raised {type(exc).__name__}",
                )
                status = reconciled.status
            except Exception:
                status = ScheduleStatus.RECONCILIATION_REQUIRED
            try:
                run = self._store.get_run(claim.dispatch_id)
                if run is not None:
                    self._store.update_run(
                        dispatch_id=claim.dispatch_id,
                        status=BackgroundRunStatus.UNCERTAIN,
                        task=run.task,
                        at=self._clock.now(),
                        detail=f"dispatch raised {type(exc).__name__}",
                    )
            except Exception:
                pass
            return DispatchReport(
                schedule_id=schedule_id,
                dispatch_id=claim.dispatch_id,
                launch=None,
                schedule_status=status,
                error_type=type(exc).__name__,
            )
        finally:
            with self._lock:
                self._active.pop(schedule_id, None)

    def _running(self) -> None:
        if self._shutdown:
            raise RuntimeError("scheduler is shut down")


__all__ = [
    "DispatchReport",
    "GovernedTaskLauncher",
    "LaunchReport",
    "LaunchStatus",
    "ScheduledRequestFactory",
    "Scheduler",
    "SchedulerTickReport",
    "SystemWallClock",
    "WallClock",
]
