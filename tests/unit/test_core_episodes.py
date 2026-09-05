"""Tests for the canonical inert EpisodeRecord contract."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from agentx.core.episodes import (
    EPISODE_SCHEMA_VERSION,
    EpisodeDeserializationError,
    EpisodeOutcome,
    EpisodeRecord,
    EpisodeValidationError,
    UnsupportedEpisodeSchemaVersionError,
)
from agentx.core.ids import EpisodeId, TaskId

_EPISODE_ID = EpisodeId(UUID("11111111-1111-4111-8111-111111111111"))
_TASK_ID = TaskId(UUID("22222222-2222-4222-8222-222222222222"))
_CORRELATION_ID = UUID("33333333-3333-4333-8333-333333333333")
_EVENT_IDS = (
    UUID("44444444-4444-4444-8444-444444444444"),
    UUID("55555555-5555-4555-8555-555555555555"),
)


def _episode(**overrides: object) -> EpisodeRecord:
    values: dict[str, object] = {
        "episode_id": _EPISODE_ID,
        "outcome": EpisodeOutcome.SUCCEEDED,
        "summary": "Observed a stable successful task outcome.",
        "created_at": datetime(2026, 9, 5, 8, 0, tzinfo=UTC),
        "task_id": _TASK_ID,
        "correlation_id": _CORRELATION_ID,
        "started_at": datetime(2026, 9, 5, 7, 58, tzinfo=UTC),
        "ended_at": datetime(2026, 9, 5, 7, 59, tzinfo=UTC),
        "supporting_event_ids": _EVENT_IDS,
    }
    values.update(overrides)
    return EpisodeRecord(**values)  # type: ignore[arg-type]


def test_episode_record_identity_and_associations() -> None:
    episode = _episode()

    assert episode.episode_id == _EPISODE_ID
    assert episode.task_id == _TASK_ID
    assert episode.correlation_id == _CORRELATION_ID
    assert episode.supporting_event_ids == _EVENT_IDS


def test_create_generates_distinct_episode_ids_and_utc_time() -> None:
    first = EpisodeRecord.create(
        outcome=EpisodeOutcome.SUCCEEDED,
        summary="First meaningful experience.",
    )
    second = EpisodeRecord.create(
        outcome=EpisodeOutcome.FAILED,
        summary="Second meaningful experience.",
    )

    assert isinstance(first.episode_id, EpisodeId)
    assert first.episode_id != second.episode_id
    assert first.created_at.tzinfo is UTC


def test_timestamps_are_normalized_to_utc() -> None:
    offset = timezone(timedelta(hours=5, minutes=30))
    episode = _episode(
        created_at=datetime(2026, 9, 5, 13, 30, tzinfo=offset),
        started_at=datetime(2026, 9, 5, 13, 0, tzinfo=offset),
        ended_at=datetime(2026, 9, 5, 13, 15, tzinfo=offset),
    )

    assert episode.created_at == datetime(2026, 9, 5, 8, 0, tzinfo=UTC)
    assert episode.started_at == datetime(2026, 9, 5, 7, 30, tzinfo=UTC)
    assert episode.ended_at == datetime(2026, 9, 5, 7, 45, tzinfo=UTC)


def test_episode_record_is_immutable() -> None:
    episode = _episode()

    with pytest.raises(FrozenInstanceError):
        episode.summary = "changed"  # type: ignore[misc]


def test_summary_must_be_nonempty_trimmed_and_concise() -> None:
    with pytest.raises(EpisodeValidationError, match="non-empty and trimmed"):
        _episode(summary=" bad ")
    with pytest.raises(EpisodeValidationError, match="at most"):
        _episode(summary="x" * 4_097)


def test_timing_window_validation() -> None:
    with pytest.raises(EpisodeValidationError, match="requires started_at"):
        _episode(started_at=None)
    with pytest.raises(EpisodeValidationError, match="earlier than started_at"):
        _episode(
            started_at=datetime(2026, 9, 5, 8, 0, tzinfo=UTC),
            ended_at=datetime(2026, 9, 5, 7, 59, tzinfo=UTC),
        )


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(EpisodeValidationError, match="timezone-aware"):
        _episode(created_at=datetime(2026, 9, 5, 8, 0))


def test_supporting_event_ids_must_be_unique_uuid_tuple() -> None:
    with pytest.raises(TypeError, match="tuple"):
        _episode(supporting_event_ids=list(_EVENT_IDS))
    with pytest.raises(EpisodeValidationError, match="duplicates"):
        _episode(supporting_event_ids=(_EVENT_IDS[0], _EVENT_IDS[0]))


def test_round_trip_preserves_canonical_episode_data() -> None:
    episode = _episode()

    encoded = episode.to_json()
    restored = EpisodeRecord.from_json(encoded)

    assert restored == episode
    assert json.loads(encoded)["schema_version"] == EPISODE_SCHEMA_VERSION


def test_serialization_is_deterministic() -> None:
    episode = _episode()

    assert episode.to_json() == episode.to_json()
    assert list(json.loads(episode.to_json())) == sorted(json.loads(episode.to_json()))


def test_unknown_or_missing_serialized_fields_fail_closed() -> None:
    encoded = _episode().to_dict()
    encoded["unexpected"] = "value"
    with pytest.raises(EpisodeDeserializationError, match="unknown fields"):
        EpisodeRecord.from_dict(encoded)

    missing = _episode().to_dict()
    del missing["summary"]
    with pytest.raises(EpisodeDeserializationError, match="missing required fields"):
        EpisodeRecord.from_dict(missing)


def test_unsupported_episode_schema_version_fails_closed() -> None:
    encoded = _episode().to_dict()
    encoded["schema_version"] = 999

    with pytest.raises(UnsupportedEpisodeSchemaVersionError, match="unsupported"):
        EpisodeRecord.from_dict(encoded)


def test_malformed_episode_json_fails_closed() -> None:
    with pytest.raises(EpisodeDeserializationError, match="malformed"):
        EpisodeRecord.from_json("{not-json")


def test_episode_record_contains_no_authority_api() -> None:
    episode = _episode(summary="A prior authorized R4 action succeeded.")

    assert not hasattr(episode, "permission")
    assert not hasattr(episode, "authority")
    assert not hasattr(episode, "execute")
    assert not hasattr(episode, "apply")
