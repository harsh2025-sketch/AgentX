"""M12 proactive recommendation, notification, quota and stale-state policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from enum import StrEnum
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agentx.core.scheduling import ScheduledTaskIntent
from agentx.infrastructure.schedule_store import (
    DuplicateScheduleError,
    ScheduledTaskStore,
    ScheduleStoreError,
)
from agentx.scheduling import Scheduler, SchedulerTickReport
from agentx.world_model import WorldEntityId, WorldFreshness, WorldModel


def _timezone(value: str) -> tzinfo:
    if value in {"UTC", "Etc/UTC"}:
        return UTC
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("unknown timezone") from exc


class NotificationPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    SECURITY = "security"


@dataclass(frozen=True, slots=True, kw_only=True)
class Notification:
    notification_id: UUID
    source: str
    message: str
    priority: NotificationPriority
    created_at: datetime
    expires_at: datetime | None = None
    action_reference: str | None = None

    @classmethod
    def create(
        cls,
        *,
        source: str,
        message: str,
        priority: NotificationPriority,
        created_at: datetime,
        expires_at: datetime | None = None,
        action_reference: str | None = None,
    ) -> Notification:
        return cls(
            notification_id=uuid4(),
            source=source,
            message=message,
            priority=priority,
            created_at=created_at,
            expires_at=expires_at,
            action_reference=action_reference,
        )


class NotificationSink(Protocol):
    def notify(self, notification: Notification) -> None: ...


class AttentionState(StrEnum):
    AVAILABLE = "available"
    BUSY = "busy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfidencePolicy:
    minimum: float

    def __post_init__(self) -> None:
        value = float(self.minimum)
        if not 0.0 <= value <= 1.0:
            raise ValueError("minimum confidence must be in [0, 1]")
        object.__setattr__(self, "minimum", value)

    def admits(self, confidence: float) -> bool:
        value = float(confidence)
        return 0.0 <= value <= 1.0 and value >= self.minimum


@dataclass(frozen=True, slots=True, kw_only=True)
class QuietHoursPolicy:
    start: time
    end: time
    timezone: str

    def __post_init__(self) -> None:
        if self.start == self.end:
            raise ValueError("quiet hour boundaries must differ")
        _timezone(self.timezone)

    def is_quiet(self, moment: datetime) -> bool:
        local = moment.astimezone(_timezone(self.timezone)).time().replace(tzinfo=None)
        if self.start < self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end


class OptInPolicy:
    """Missing or corrupt durable state is fail-closed opt-out."""

    def __init__(
        self,
        store: ScheduledTaskStore,
        *,
        key: str = "proactivity.opt_in",
    ) -> None:
        self._store = store
        self._key = key

    def set_enabled(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        self._store.set_policy(self._key, enabled)

    def revoke(self) -> None:
        self.set_enabled(False)

    def enabled(self) -> bool:
        try:
            return self._store.get_policy(self._key) is True
        except ScheduleStoreError:
            return False


@dataclass(frozen=True, slots=True, kw_only=True)
class BackgroundQuotaLimits:
    window: timedelta
    max_resource_units: int
    max_model_calls: int
    max_machine_actions: int


class BackgroundQuotaPolicy:
    """Additional background ceiling; can only deny, never widen C1.08."""

    def __init__(
        self,
        *,
        store: ScheduledTaskStore,
        limits: BackgroundQuotaLimits,
        scope: str = "m12.background",
    ) -> None:
        self._store = store
        self._limits = limits
        self._scope = scope

    def consume(self, intent: ScheduledTaskIntent, *, now: datetime) -> bool:
        return self._store.consume_quota(
            scope=self._scope,
            now=now,
            window=self._limits.window,
            resource_units=intent.estimated_resource_units,
            model_calls=intent.estimated_model_calls,
            machine_actions=intent.estimated_machine_actions,
            maxima=(
                self._limits.max_resource_units,
                self._limits.max_model_calls,
                self._limits.max_machine_actions,
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class WorldStateReference:
    entity_id: WorldEntityId
    state_digest: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ProactiveRecommendation:
    recommendation_id: UUID
    intent: ScheduledTaskIntent
    confidence: float
    observed_at: datetime
    expires_at: datetime
    world_state: tuple[WorldStateReference, ...] = ()
    requires_approval: bool = False

    @classmethod
    def create(
        cls,
        *,
        intent: ScheduledTaskIntent,
        confidence: float,
        observed_at: datetime,
        expires_at: datetime,
        world_state: tuple[WorldStateReference, ...] = (),
        requires_approval: bool = False,
    ) -> ProactiveRecommendation:
        return cls(
            recommendation_id=uuid4(),
            intent=intent,
            confidence=float(confidence),
            observed_at=observed_at,
            expires_at=expires_at,
            world_state=world_state,
            requires_approval=requires_approval,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProactiveApproval:
    """Short-lived exact M12 evidence; never permission or ActionGate authority."""

    approval_id: UUID
    recommendation_id: UUID
    intent_digest: str
    issued_at: datetime
    expires_at: datetime
    state_digests: tuple[str, ...]

    @classmethod
    def issue(
        cls,
        recommendation: ProactiveRecommendation,
        *,
        issued_at: datetime,
        ttl: timedelta,
    ) -> ProactiveApproval:
        if not timedelta(seconds=1) <= ttl <= timedelta(hours=24):
            raise ValueError("approval ttl must be 1 second..24 hours")
        return cls(
            approval_id=uuid4(),
            recommendation_id=recommendation.recommendation_id,
            intent_digest=_intent_digest(recommendation.intent),
            issued_at=issued_at,
            expires_at=issued_at + ttl,
            state_digests=tuple(item.state_digest for item in recommendation.world_state),
        )

    def matches(
        self,
        recommendation: ProactiveRecommendation,
        *,
        now: datetime,
    ) -> bool:
        return (
            now < self.expires_at
            and self.recommendation_id == recommendation.recommendation_id
            and self.intent_digest == _intent_digest(recommendation.intent)
            and self.state_digests
            == tuple(item.state_digest for item in recommendation.world_state)
        )


class StaleActionGuard:
    """Captures and revalidates actual M10 WorldModel cache state."""

    def __init__(self, world_model: WorldModel) -> None:
        self._world = world_model

    def capture(
        self,
        entity_id: WorldEntityId,
        *,
        at: datetime,
    ) -> WorldStateReference:
        lookup = self._world.cache.lookup(entity_id, at=at, refresh=False)
        if lookup.freshness is not WorldFreshness.FRESH or lookup.value is None:
            raise ValueError("world state must be fresh")
        return WorldStateReference(
            entity_id=entity_id,
            state_digest=_world_digest(lookup.value),
        )

    def is_current(
        self,
        references: tuple[WorldStateReference, ...],
        *,
        at: datetime,
    ) -> bool:
        for reference in references:
            lookup = self._world.cache.lookup(
                reference.entity_id,
                at=at,
                refresh=True,
            )
            if lookup.freshness is not WorldFreshness.FRESH or lookup.value is None:
                return False
            if _world_digest(lookup.value) != reference.state_digest:
                return False
        return True


class ProactiveDecision(StrEnum):
    SCHEDULED = "scheduled"
    OPTED_OUT = "opted_out"
    LOW_CONFIDENCE = "low_confidence"
    QUIET_HOURS = "quiet_hours"
    ATTENTION_UNAVAILABLE = "attention_unavailable"
    EXPIRED = "expired"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_EXPIRED_OR_MISMATCHED = "approval_expired_or_mismatched"
    STALE_WORLD_STATE = "stale_world_state"
    QUOTA_EXHAUSTED = "quota_exhausted"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProactiveExecutionReport:
    decision: ProactiveDecision
    scheduler_report: SchedulerTickReport | None = None


class ProactiveController:
    """Fail-closed proactive execution policy before governed scheduling."""

    def __init__(
        self,
        *,
        scheduler: Scheduler,
        opt_in: OptInPolicy,
        confidence: ConfidencePolicy,
        quota: BackgroundQuotaPolicy,
        stale_guard: StaleActionGuard,
        quiet_hours: QuietHoursPolicy | None = None,
        notifications: NotificationSink | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._opt_in = opt_in
        self._confidence = confidence
        self._quota = quota
        self._stale = stale_guard
        self._quiet_hours = quiet_hours
        self._notifications = notifications

    def execute(
        self,
        recommendation: ProactiveRecommendation,
        *,
        now: datetime,
        attention: AttentionState,
        approval: ProactiveApproval | None = None,
    ) -> ProactiveExecutionReport:
        decision = self._preflight(
            recommendation,
            now=now,
            attention=attention,
            approval=approval,
        )
        if decision is not None:
            self._notify(recommendation, decision, now)
            return ProactiveExecutionReport(decision=decision)
        if not self._quota.consume(recommendation.intent, now=now):
            decision = ProactiveDecision.QUOTA_EXHAUSTED
            self._notify(recommendation, decision, now)
            return ProactiveExecutionReport(decision=decision)
        schedule_id = uuid5(
            NAMESPACE_URL,
            f"agentx:m12:proactive:{recommendation.recommendation_id}",
        )
        try:
            self._scheduler.register_one_shot(
                intent=recommendation.intent,
                due_at=now,
                schedule_id=schedule_id,
                expires_at=recommendation.expires_at,
            )
        except DuplicateScheduleError:
            if self._scheduler.store.get(schedule_id) is None:
                raise
        return ProactiveExecutionReport(
            decision=ProactiveDecision.SCHEDULED,
            scheduler_report=self._scheduler.dispatch_schedule(schedule_id),
        )

    def _preflight(
        self,
        recommendation: ProactiveRecommendation,
        *,
        now: datetime,
        attention: AttentionState,
        approval: ProactiveApproval | None,
    ) -> ProactiveDecision | None:
        if not self._opt_in.enabled():
            return ProactiveDecision.OPTED_OUT
        if now >= recommendation.expires_at:
            return ProactiveDecision.EXPIRED
        if not self._confidence.admits(recommendation.confidence):
            return ProactiveDecision.LOW_CONFIDENCE
        if self._quiet_hours is not None and self._quiet_hours.is_quiet(now):
            return ProactiveDecision.QUIET_HOURS
        if attention is not AttentionState.AVAILABLE:
            return ProactiveDecision.ATTENTION_UNAVAILABLE
        if not self._stale.is_current(recommendation.world_state, at=now):
            return ProactiveDecision.STALE_WORLD_STATE
        if recommendation.requires_approval:
            if approval is None:
                return ProactiveDecision.APPROVAL_REQUIRED
            if not approval.matches(recommendation, now=now):
                return ProactiveDecision.APPROVAL_EXPIRED_OR_MISMATCHED
        return None

    def _notify(
        self,
        recommendation: ProactiveRecommendation,
        decision: ProactiveDecision,
        now: datetime,
    ) -> None:
        if self._notifications is None:
            return
        self._notifications.notify(
            Notification.create(
                source="agentx.proactivity",
                message=f"Proactive action not executed: {decision.value}.",
                priority=NotificationPriority.NORMAL,
                created_at=now,
                expires_at=recommendation.expires_at,
                action_reference=str(recommendation.recommendation_id),
            )
        )


def _world_digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8", errors="replace")).hexdigest()


def _intent_digest(intent: ScheduledTaskIntent) -> str:
    raw = json.dumps(intent.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


__all__ = [
    "AttentionState",
    "BackgroundQuotaLimits",
    "BackgroundQuotaPolicy",
    "ConfidencePolicy",
    "Notification",
    "NotificationPriority",
    "NotificationSink",
    "OptInPolicy",
    "ProactiveApproval",
    "ProactiveController",
    "ProactiveDecision",
    "ProactiveExecutionReport",
    "ProactiveRecommendation",
    "QuietHoursPolicy",
    "StaleActionGuard",
    "WorldStateReference",
]
