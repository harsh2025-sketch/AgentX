"""AX-452 one-way runtime-to-UI telemetry coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from agentx.core.events import ActionPayload, Event, EventType
from agentx.core.runtime_ui_events import (
    RuntimeUiEvent,
    RuntimeUiEventKind,
    project_event_for_ui,
    runtime_ui_disconnected,
    runtime_ui_restarted,
)

_T0 = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def test_runtime_event_projection_preserves_correlation_but_redacts_opaque_data() -> None:
    correlation = uuid4()
    event = Event.create(
        event_type=EventType.ACTION_REQUESTED,
        source="executor",
        correlation_id=correlation,
        task_id="task-1",
        timestamp=_T0,
        payload=ActionPayload(
            name="clipboard.write",
            data={"secret": "TOP-SECRET", "token": "abc", "permission": "ADMIN"},
        ),
    )
    runtime_id = uuid4()

    telemetry = project_event_for_ui(event, runtime_instance_id=runtime_id, sequence=7)

    assert telemetry.kind is RuntimeUiEventKind.EVENT
    assert telemetry.runtime_instance_id == runtime_id
    assert telemetry.sequence == 7
    assert telemetry.correlation_id == correlation
    assert telemetry.task_id == "task-1"
    assert telemetry.state == EventType.ACTION_REQUESTED.value
    assert telemetry.payload == {"name": "clipboard.write", "data": "[redacted]"}
    assert "TOP-SECRET" not in telemetry.to_json()
    assert "abc" not in telemetry.to_json()


def test_disconnect_and_restart_are_explicit_sequence_boundaries() -> None:
    previous = uuid4()
    disconnected = runtime_ui_disconnected(
        runtime_instance_id=previous,
        sequence=9,
        timestamp=_T0,
    )
    current = uuid4()
    restarted = runtime_ui_restarted(
        runtime_instance_id=current,
        previous_runtime_instance_id=previous,
        timestamp=_T0 + timedelta(seconds=1),
    )

    assert disconnected.kind is RuntimeUiEventKind.DISCONNECTED
    assert disconnected.sequence == 9
    assert restarted.kind is RuntimeUiEventKind.RUNTIME_RESTARTED
    assert restarted.sequence == 0
    assert restarted.runtime_instance_id == current
    assert restarted.payload["previous_runtime_instance_id"] == str(previous)


def test_ui_telemetry_round_trip_is_deterministic() -> None:
    record = runtime_ui_disconnected(
        runtime_instance_id=uuid4(),
        sequence=3,
        timestamp=_T0,
    )
    encoded = record.to_json()

    decoded = RuntimeUiEvent.from_json(encoded)
    assert decoded == record
    assert decoded.to_json() == encoded
