"""Unit tests for the synchronous in-process AgentX EventBus."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from threading import Barrier, Lock, Thread

import pytest

from agentx.core.events import ActionPayload, Event, EventCategory, EventType
from agentx.infrastructure.event_bus import EventBus, Subscription


def _event(event_type: EventType = EventType.TASK_CREATED) -> Event:
    payload = None
    if event_type in {
        EventType.ACTION_REQUESTED,
        EventType.ACTION_COMPLETED,
        EventType.ACTION_FAILED,
    }:
        payload = ActionPayload(name="test.action")
    return Event.create(event_type=event_type, source="tests", payload=payload)


def test_subscribe_and_publish_all_events() -> None:
    bus = EventBus()
    received: list[Event] = []
    event = _event()

    subscription = bus.subscribe(received.append)
    report = bus.publish(event)

    assert subscription.active
    assert received == [event]
    assert report.event_id == event.event_id
    assert report.matched_subscribers == 1
    assert report.successful_deliveries == 1
    assert report.failed_deliveries == 0
    assert report.ok


def test_multiple_subscribers_run_in_registration_order() -> None:
    bus = EventBus()
    calls: list[str] = []

    bus.subscribe(lambda _event: calls.append("first"))
    bus.subscribe(lambda _event: calls.append("second"))
    bus.subscribe(lambda _event: calls.append("third"))

    report = bus.publish(_event())

    assert calls == ["first", "second", "third"]
    assert report.matched_subscribers == 3
    assert report.successful_deliveries == 3


def test_event_type_filtering() -> None:
    bus = EventBus()
    received: list[EventType] = []
    bus.subscribe(
        lambda event: received.append(event.event_type),
        event_type=EventType.TASK_STARTED,
    )

    first = bus.publish(_event(EventType.TASK_CREATED))
    second = bus.publish(_event(EventType.TASK_STARTED))

    assert received == [EventType.TASK_STARTED]
    assert first.matched_subscribers == 0
    assert second.matched_subscribers == 1


def test_event_category_filtering() -> None:
    bus = EventBus()
    received: list[EventType] = []
    bus.subscribe(
        lambda event: received.append(event.event_type),
        category=EventCategory.ACTION,
    )

    task_report = bus.publish(_event(EventType.TASK_CREATED))
    action_report = bus.publish(_event(EventType.ACTION_REQUESTED))

    assert received == [EventType.ACTION_REQUESTED]
    assert task_report.matched_subscribers == 0
    assert action_report.matched_subscribers == 1


def test_filter_arguments_are_mutually_exclusive() -> None:
    bus = EventBus()

    with pytest.raises(ValueError, match="mutually exclusive"):
        bus.subscribe(
            lambda _event: None,
            event_type=EventType.TASK_CREATED,
            category=EventCategory.TASK,
        )


def test_unsubscribe_and_dispose_are_idempotent() -> None:
    bus = EventBus()
    received: list[Event] = []
    subscription = bus.subscribe(received.append)

    was_active = subscription.active
    first_unsubscribe = subscription.unsubscribe()
    second_unsubscribe = subscription.unsubscribe()
    dispose_after_removal = subscription.dispose()
    is_active_after = subscription.active

    assert was_active is True
    assert first_unsubscribe is True
    assert second_unsubscribe is False
    assert dispose_after_removal is False
    assert is_active_after is False

    report = bus.publish(_event())
    assert received == []
    assert report.matched_subscribers == 0


def test_duplicate_subscriptions_are_independent_registrations() -> None:
    bus = EventBus()
    received: list[Event] = []

    first = bus.subscribe(received.append)
    second = bus.subscribe(received.append)
    event = _event()

    first_report = bus.publish(event)
    assert received == [event, event]
    assert first_report.matched_subscribers == 2

    assert first.unsubscribe()
    assert second.active
    received.clear()

    second_report = bus.publish(event)
    assert received == [event]
    assert second_report.matched_subscribers == 1


def test_subscriber_exception_is_isolated_and_reported() -> None:
    bus = EventBus()
    calls: list[str] = []

    def failing(_event: Event) -> None:
        calls.append("failing")
        raise RuntimeError("handler failed")

    failing_subscription = bus.subscribe(failing)
    bus.subscribe(lambda _event: calls.append("after"))

    report = bus.publish(_event())

    assert calls == ["failing", "after"]
    assert report.matched_subscribers == 2
    assert report.successful_deliveries == 1
    assert report.failed_deliveries == 1
    assert not report.ok
    failure = report.failures[0]
    assert failure.subscription_id == failing_subscription.subscription_id
    assert isinstance(failure.exception, RuntimeError)
    assert str(failure.exception) == "handler failed"


def test_subscribe_during_publish_applies_only_to_later_publish() -> None:
    bus = EventBus()
    calls: list[str] = []
    added: list[Subscription] = []

    def late(_event: Event) -> None:
        calls.append("late")

    def first(_event: Event) -> None:
        calls.append("first")
        if not added:
            added.append(bus.subscribe(late))

    bus.subscribe(first)

    first_report = bus.publish(_event())
    assert calls == ["first"]
    assert first_report.matched_subscribers == 1

    calls.clear()
    second_report = bus.publish(_event())
    assert calls == ["first", "late"]
    assert second_report.matched_subscribers == 2


def test_unsubscribe_during_publish_uses_snapshot_for_current_delivery() -> None:
    bus = EventBus()
    calls: list[str] = []
    later_holder: list[Subscription] = []

    def first(_event: Event) -> None:
        calls.append("first")
        assert later_holder[0].unsubscribe()

    def later(_event: Event) -> None:
        calls.append("later")

    bus.subscribe(first)
    later_holder.append(bus.subscribe(later))

    first_report = bus.publish(_event())
    assert calls == ["first", "later"]
    assert first_report.matched_subscribers == 2
    assert not later_holder[0].active

    calls.clear()
    second_report = bus.publish(_event())
    assert calls == ["first"]
    assert second_report.matched_subscribers == 1


def test_nested_publish_is_synchronous_and_reentrant() -> None:
    bus = EventBus()
    calls: list[str] = []
    nested_event = _event(EventType.TASK_STARTED)
    nested_reports = []

    def first(event: Event) -> None:
        calls.append(f"first:{event.event_type.value}")
        if event.event_type is EventType.TASK_CREATED:
            nested_reports.append(bus.publish(nested_event))

    def second(event: Event) -> None:
        calls.append(f"second:{event.event_type.value}")

    bus.subscribe(first)
    bus.subscribe(second)

    outer_report = bus.publish(_event(EventType.TASK_CREATED))

    assert calls == [
        "first:task.created",
        "first:task.started",
        "second:task.started",
        "second:task.created",
    ]
    assert outer_report.matched_subscribers == 2
    assert len(nested_reports) == 1
    assert nested_reports[0].matched_subscribers == 2


def test_event_bus_instances_do_not_share_subscriptions() -> None:
    first_bus = EventBus()
    second_bus = EventBus()
    received: list[Event] = []
    event = _event()

    first_bus.subscribe(received.append)

    second_report = second_bus.publish(event)
    assert received == []
    assert second_report.matched_subscribers == 0

    first_report = first_bus.publish(event)
    assert received == [event]
    assert first_report.matched_subscribers == 1


def test_publish_preserves_event_immutability_and_identity() -> None:
    bus = EventBus()
    event = _event(EventType.ACTION_REQUESTED)
    before = event.to_json()
    received: list[Event] = []
    bus.subscribe(received.append)

    report = bus.publish(event)

    assert received[0] is event
    assert event.to_json() == before
    assert report.ok
    with pytest.raises(FrozenInstanceError):
        event.__setattr__("source", "mutated")


def test_multithreaded_publish_and_subscription_bookkeeping() -> None:
    bus = EventBus()
    event = _event()
    start = Barrier(5)
    count_lock = Lock()
    error_lock = Lock()
    permanent_deliveries = 0
    errors: list[Exception] = []

    def permanent_handler(_event: Event) -> None:
        nonlocal permanent_deliveries
        with count_lock:
            permanent_deliveries += 1

    bus.subscribe(permanent_handler)

    def publisher() -> None:
        try:
            start.wait()
            for _ in range(100):
                report = bus.publish(event)
                if not report.ok:
                    raise AssertionError("unexpected handler failure")
        except Exception as exc:
            with error_lock:
                errors.append(exc)

    def mutator() -> None:
        try:
            start.wait()
            for _ in range(200):
                subscription = bus.subscribe(lambda _event: None)
                subscription.unsubscribe()
        except Exception as exc:
            with error_lock:
                errors.append(exc)

    threads = [
        Thread(target=publisher),
        Thread(target=publisher),
        Thread(target=publisher),
        Thread(target=mutator),
        Thread(target=mutator),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert permanent_deliveries == 300
