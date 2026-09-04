"""Synchronous in-process transport for canonical AgentX events.

The EventBus transports immutable :class:`~agentx.core.events.Event`
records only. Publishing an event does not grant permission, execute an action,
or otherwise turn event data into authority.

Delivery semantics are deliberately small and deterministic:

* subscriptions are invoked in registration order;
* every publish snapshots the active subscriptions before invoking handlers;
* subscriptions added or removed during a publish affect later publishes only;
* handler callbacks execute synchronously on the publisher's thread;
* nested/reentrant publishes are allowed and take their own fresh snapshot;
* ordinary handler exceptions are isolated, recorded in the publish report, and
  do not prevent later handlers in the same snapshot from running;
* bookkeeping is protected by a standard-library lock, but callbacks run
  outside that lock. Concurrent publishers may therefore invoke a handler
  concurrently, and handler-level thread safety remains the subscriber's
  responsibility.

There is no process-global bus, background worker, persistence, replay, dynamic
code loading, or event-payload execution in this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from uuid import UUID
from weakref import ReferenceType, ref

from agentx.core.events import Event, EventCategory, EventType

type EventHandler = Callable[[Event], None]


@dataclass(frozen=True, slots=True)
class HandlerFailure:
    """A subscriber exception captured while publishing one event."""

    subscription_id: int
    exception: Exception


@dataclass(frozen=True, slots=True)
class PublishReport:
    """Outcome of one synchronous publish operation."""

    event_id: UUID
    matched_subscribers: int
    successful_deliveries: int
    failures: tuple[HandlerFailure, ...]

    @property
    def failed_deliveries(self) -> int:
        """Return the number of handlers that raised an ordinary exception."""
        return len(self.failures)

    @property
    def ok(self) -> bool:
        """Return whether every matching subscriber completed successfully."""
        return not self.failures


@dataclass(frozen=True, slots=True)
class _SubscriptionRecord:
    subscription_id: int
    handler: EventHandler
    event_type: EventType | None
    category: EventCategory | None

    def matches(self, event: Event) -> bool:
        if self.event_type is not None:
            return event.event_type is self.event_type
        if self.category is not None:
            return event.category is self.category
        return True


class Subscription:
    """Explicit handle for one EventBus registration.

    Each call to :meth:`EventBus.subscribe` creates an independent registration,
    even when the same callable and filter are supplied more than once.
    ``unsubscribe`` and ``dispose`` are idempotent: the first successful removal
    returns ``True`` and later calls return ``False``.
    """

    def __init__(self, bus: EventBus, subscription_id: int) -> None:
        self._bus_ref: ReferenceType[EventBus] = ref(bus)
        self._subscription_id = subscription_id
        self._lock = Lock()

    @property
    def subscription_id(self) -> int:
        """Return the bus-local opaque registration identifier."""
        return self._subscription_id

    @property
    def active(self) -> bool:
        """Return whether this registration is currently active on its bus."""
        bus = self._bus_ref()
        return bus is not None and bus._contains(self._subscription_id)

    def unsubscribe(self) -> bool:
        """Remove this registration if still active."""
        with self._lock:
            bus = self._bus_ref()
            if bus is None:
                return False
            return bus._unsubscribe(self._subscription_id)

    def dispose(self) -> bool:
        """Alias for :meth:`unsubscribe` for explicit lifetime management."""
        return self.unsubscribe()


class EventBus:
    """Thread-safe bookkeeping with synchronous, snapshot-based event delivery."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._subscriptions: dict[int, _SubscriptionRecord] = {}
        self._next_subscription_id = 1

    def subscribe(
        self,
        handler: EventHandler,
        *,
        event_type: EventType | None = None,
        category: EventCategory | None = None,
    ) -> Subscription:
        """Register ``handler`` for all events, one type, or one category.

        ``event_type`` and ``category`` are mutually exclusive. Repeating the
        same handler creates another independent registration and therefore
        another invocation when the filter matches.
        """
        if not callable(handler):
            raise TypeError("handler must be callable")
        if event_type is not None and not isinstance(event_type, EventType):
            raise TypeError("event_type must be an EventType or None")
        if category is not None and not isinstance(category, EventCategory):
            raise TypeError("category must be an EventCategory or None")
        if event_type is not None and category is not None:
            raise ValueError("event_type and category filters are mutually exclusive")

        with self._lock:
            subscription_id = self._next_subscription_id
            self._next_subscription_id += 1
            self._subscriptions[subscription_id] = _SubscriptionRecord(
                subscription_id=subscription_id,
                handler=handler,
                event_type=event_type,
                category=category,
            )
        return Subscription(self, subscription_id)

    def publish(self, event: Event) -> PublishReport:
        """Synchronously deliver ``event`` to a snapshot of matching subscribers.

        The subscription snapshot is taken under the bookkeeping lock, then the
        lock is released before any user callback is invoked. A subscription
        removed after snapshot creation still receives this publish; one added
        after snapshot creation waits until a later publish.

        Ordinary :class:`Exception` instances from handlers are captured in the
        returned report and delivery continues. ``BaseException`` subclasses
        such as ``KeyboardInterrupt`` and ``SystemExit`` are intentionally not
        swallowed.
        """
        if not isinstance(event, Event):
            raise TypeError("event must be a canonical Event")

        with self._lock:
            snapshot = tuple(self._subscriptions.values())

        matching = tuple(record for record in snapshot if record.matches(event))
        failures: list[HandlerFailure] = []
        successful_deliveries = 0

        for record in matching:
            try:
                record.handler(event)
            except Exception as exc:
                failures.append(
                    HandlerFailure(
                        subscription_id=record.subscription_id,
                        exception=exc,
                    )
                )
            else:
                successful_deliveries += 1

        return PublishReport(
            event_id=event.event_id,
            matched_subscribers=len(matching),
            successful_deliveries=successful_deliveries,
            failures=tuple(failures),
        )

    def _contains(self, subscription_id: int) -> bool:
        with self._lock:
            return subscription_id in self._subscriptions

    def _unsubscribe(self, subscription_id: int) -> bool:
        with self._lock:
            return self._subscriptions.pop(subscription_id, None) is not None


__all__ = [
    "EventBus",
    "EventHandler",
    "HandlerFailure",
    "PublishReport",
    "Subscription",
]
