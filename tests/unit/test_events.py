"""Unit tests for the canonical AgentX event contract."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from agentx.core.events import (
    CURRENT_EVENT_SCHEMA_VERSION,
    ActionPayload,
    DecisionPayload,
    EmptyPayload,
    Event,
    EventCategory,
    EventType,
    EventValidationError,
    GoalPayload,
    ObservationPayload,
    SelectionPayload,
    UnsupportedEventSchemaVersionError,
    VerificationPayload,
)


def test_event_creation_generates_unique_ids_and_utc_timestamps() -> None:
    first = Event.create(event_type=EventType.SYSTEM_STARTED, source="agentx.runtime")
    second = Event.create(event_type=EventType.SYSTEM_STARTED, source="agentx.runtime")

    assert isinstance(first.event_id, UUID)
    assert first.event_id != second.event_id
    assert first.timestamp.tzinfo is UTC
    assert first.timestamp.utcoffset() == timedelta(0)
    assert first.schema_version == CURRENT_EVENT_SCHEMA_VERSION
    assert first.correlation_id == first.event_id
    assert first.causation_id is None
    assert first.task_id is None


def test_supplied_aware_timestamp_is_normalized_to_utc() -> None:
    india = timezone(timedelta(hours=5, minutes=30))
    supplied = datetime(2026, 9, 5, 5, 30, tzinfo=india)

    event = Event.create(
        event_type=EventType.SYSTEM_STARTED,
        source="agentx.runtime",
        timestamp=supplied,
    )

    assert event.timestamp == datetime(2026, 9, 5, 0, 0, tzinfo=UTC)
    assert event.timestamp.tzinfo is UTC


def test_taxonomy_reserves_required_categories_and_uses_controlled_prefixes() -> None:
    required = {
        EventCategory.SYSTEM,
        EventCategory.TASK,
        EventCategory.ROUTING,
        EventCategory.POLICY,
        EventCategory.CAPABILITY,
        EventCategory.ACTION,
        EventCategory.OBSERVATION,
        EventCategory.VERIFICATION,
        EventCategory.HIVE,
        EventCategory.PROCEDURE,
        EventCategory.REASONING,
        EventCategory.RESEARCH,
        EventCategory.LEARNING,
        EventCategory.REPAIR,
        EventCategory.SECURITY,
        EventCategory.RESOURCE,
        EventCategory.HUMAN,
    }

    assert required <= set(EventCategory)
    for event_type in EventType:
        prefix, separator, suffix = event_type.value.partition(".")
        assert separator == "."
        assert prefix == event_type.category.value
        assert suffix


def test_unknown_event_type_is_rejected() -> None:
    raw = Event.create(event_type=EventType.SYSTEM_STARTED, source="agentx.runtime").to_dict()
    raw["event_type"] = "task.invented"

    with pytest.raises(EventValidationError, match="unknown event_type"):
        Event.from_dict(raw)


def test_serialization_is_json_compatible_and_deterministic() -> None:
    event = Event.create(
        event_type=EventType.ACTION_REQUESTED,
        source="agentx.executor",
        task_id="task-opaque-42",
        payload=ActionPayload(
            name="window.focus",
            data={"title": "Notes", "attempt": 1, "flags": [True, None]},
        ),
        metadata={"origin": "unit-test", "labels": ["day-1", "offline"]},
        timestamp=datetime(2026, 9, 5, tzinfo=UTC),
    )

    encoded = event.to_dict()
    json_text = event.to_json()

    assert encoded["event_type"] == "action.requested"
    assert encoded["timestamp"] == "2026-09-05T00:00:00.000000Z"
    assert encoded["schema_version"] == CURRENT_EVENT_SCHEMA_VERSION
    assert json.loads(json_text) == encoded
    assert event.to_json() == json_text


def test_deserialization_round_trip_preserves_event() -> None:
    event = Event.create(
        event_type=EventType.POLICY_DECISION,
        source="agentx.policy",
        payload=DecisionPayload(decision="allow", reason="read-only operation"),
        metadata={"confidence": 0.95},
        timestamp=datetime(2026, 9, 5, 1, 2, 3, 456789, tzinfo=UTC),
    )

    restored_from_dict = Event.from_dict(event.to_dict())
    restored_from_json = Event.from_json(event.to_json())

    assert restored_from_dict == event
    assert restored_from_json == event


def test_correlation_and_causation_preserve_execution_chain() -> None:
    root = Event.create(
        event_type=EventType.TASK_CREATED,
        source="agentx.task",
        task_id="task-123",
    )
    child = Event.create(
        event_type=EventType.ACTION_REQUESTED,
        source="agentx.executor",
        correlation_id=root.correlation_id,
        causation_id=root.event_id,
        task_id=root.task_id,
        payload=ActionPayload(name="example.action"),
    )

    restored = Event.from_json(child.to_json())

    assert restored.correlation_id == root.correlation_id
    assert restored.causation_id == root.event_id
    assert restored.task_id == "task-123"


def test_optional_task_association_serializes_as_null() -> None:
    event = Event.create(event_type=EventType.SYSTEM_STARTED, source="agentx.runtime")

    assert event.task_id is None
    assert event.to_dict()["task_id"] is None
    assert Event.from_dict(event.to_dict()).task_id is None


def test_malformed_envelope_fields_are_rejected() -> None:
    event = Event.create(event_type=EventType.SYSTEM_STARTED, source="agentx.runtime")

    missing = event.to_dict()
    del missing["source"]
    with pytest.raises(EventValidationError, match="missing required fields"):
        Event.from_dict(missing)

    unknown = event.to_dict()
    unknown["execute"] = True
    with pytest.raises(EventValidationError, match="unknown fields"):
        Event.from_dict(unknown)

    bad_id = event.to_dict()
    bad_id["event_id"] = "not-a-uuid"
    with pytest.raises(EventValidationError, match="valid UUID"):
        Event.from_dict(bad_id)

    with pytest.raises(EventValidationError, match="JSON is malformed"):
        Event.from_json("{")

    with pytest.raises(EventValidationError, match="root must be an object"):
        Event.from_json("[]")


def test_naive_timestamp_and_self_causation_are_rejected() -> None:
    with pytest.raises(EventValidationError, match="timezone-aware"):
        Event.create(
            event_type=EventType.SYSTEM_STARTED,
            source="agentx.runtime",
            timestamp=datetime(2026, 9, 5),
        )

    event = Event.create(event_type=EventType.SYSTEM_STARTED, source="agentx.runtime")
    raw = event.to_dict()
    raw["causation_id"] = raw["event_id"]

    with pytest.raises(EventValidationError, match="must not reference the event itself"):
        Event.from_dict(raw)


def test_unsupported_schema_version_fails_clearly() -> None:
    raw = Event.create(event_type=EventType.SYSTEM_STARTED, source="agentx.runtime").to_dict()
    raw["schema_version"] = CURRENT_EVENT_SCHEMA_VERSION + 1

    with pytest.raises(
        UnsupportedEventSchemaVersionError,
        match="unsupported event schema version",
    ):
        Event.from_dict(raw)


def test_payload_type_is_validated_for_event_kind() -> None:
    with pytest.raises(EventValidationError, match="payload type EmptyPayload is invalid"):
        Event.create(
            event_type=EventType.POLICY_DECISION,
            source="agentx.policy",
            payload=EmptyPayload(),
        )

    raw = Event.create(
        event_type=EventType.POLICY_DECISION,
        source="agentx.policy",
        payload=DecisionPayload(decision="deny"),
    ).to_dict()
    raw["payload"] = {"decision": 7}

    with pytest.raises(EventValidationError, match=r"payload\.decision must be a string"):
        Event.from_dict(raw)


def test_payload_variants_validate_json_and_required_fields() -> None:
    assert GoalPayload(text="open the notes app").to_dict()["text"] == "open the notes app"
    assert SelectionPayload(selection="windows.ui").to_dict()["selection"] == "windows.ui"
    assert VerificationPayload(passed=True).to_dict()["passed"] is True

    with pytest.raises(EventValidationError, match="non-JSON-compatible"):
        ObservationPayload(value=object())

    with pytest.raises(EventValidationError, match="non-finite float"):
        ObservationPayload(value=float("nan"))

    with pytest.raises(EventValidationError, match="missing required fields"):
        Event.from_dict(
            {
                **Event.create(
                    event_type=EventType.VERIFICATION_COMPLETED,
                    source="agentx.verifier",
                    payload=VerificationPayload(passed=True),
                ).to_dict(),
                "payload": {},
            }
        )


def test_event_and_payload_records_are_immutable_and_defensively_copy_json() -> None:
    metadata: dict[str, object] = {"labels": ["before"]}
    payload_data: dict[str, object] = {"arguments": [1, 2]}
    payload = ActionPayload(name="example.action", data=payload_data)
    event = Event.create(
        event_type=EventType.ACTION_REQUESTED,
        source="agentx.executor",
        payload=payload,
        metadata=metadata,
    )

    with pytest.raises(FrozenInstanceError):
        event.__setattr__("source", "changed")
    with pytest.raises(FrozenInstanceError):
        payload.__setattr__("name", "changed")

    labels = metadata["labels"]
    arguments = payload_data["arguments"]
    assert isinstance(labels, list)
    assert isinstance(arguments, list)
    labels.append("after")
    arguments.append(3)

    encoded = event.to_dict()
    encoded_metadata = encoded["metadata"]
    encoded_payload = encoded["payload"]
    assert isinstance(encoded_metadata, dict)
    assert isinstance(encoded_payload, dict)
    assert encoded_metadata["labels"] == ["before"]
    assert encoded_payload["data"] == {"arguments": [1, 2]}
