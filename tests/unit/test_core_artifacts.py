"""Tests for the canonical inert C2.04 ArtifactRecord contract."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from agentx.core.artifacts import (
    CURRENT_ARTIFACT_SCHEMA_VERSION,
    ArtifactKind,
    ArtifactRecord,
    ArtifactValidationError,
    UnsupportedArtifactSchemaVersionError,
)
from agentx.core.ids import ArtifactId, TaskId

_T0 = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)


def _record() -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=ArtifactId.create(),
        kind=ArtifactKind.FILE,
        created_at=_T0,
        locator=r"C:\AgentX\artifacts\report.pdf",
        task_id=TaskId.create(),
        correlation_id=uuid4(),
        media_type="application/pdf",
        size_bytes=1234,
        sha256="a" * 64,
    )


def test_canonical_artifact_record_construction() -> None:
    record = _record()

    assert isinstance(record.artifact_id, ArtifactId)
    assert record.kind is ArtifactKind.FILE
    assert record.created_at == _T0
    assert record.locator.endswith("report.pdf")
    assert record.media_type == "application/pdf"
    assert record.size_bytes == 1234
    assert record.sha256 == "a" * 64


def test_create_uses_canonical_identity_and_utc_time() -> None:
    record = ArtifactRecord.create(kind=ArtifactKind.REFERENCE, locator="artifact:opaque-1")

    assert isinstance(record.artifact_id, ArtifactId)
    assert record.created_at.tzinfo is UTC
    assert record.task_id is None
    assert record.correlation_id is None


def test_timezone_is_normalized_to_utc() -> None:
    local = datetime(
        2026,
        1,
        2,
        8,
        34,
        5,
        678901,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    record = ArtifactRecord.create(
        kind=ArtifactKind.REFERENCE,
        locator="artifact:timezone-test",
        created_at=local,
    )

    assert record.created_at == _T0
    assert record.created_at.tzinfo is UTC


def test_deterministic_serialization_round_trip() -> None:
    record = _record()
    first = record.to_json()
    second = record.to_json()

    assert first == second
    assert ArtifactRecord.from_json(first) == record
    assert json.loads(first)["schema_version"] == CURRENT_ARTIFACT_SCHEMA_VERSION


def test_serialization_is_canonical_and_has_exact_fields() -> None:
    payload = _record().to_dict()

    assert set(payload) == {
        "schema_version",
        "artifact_id",
        "kind",
        "created_at",
        "task_id",
        "correlation_id",
        "locator",
        "media_type",
        "size_bytes",
        "sha256",
    }
    assert payload["created_at"] == "2026-01-02T03:04:05.678901Z"


def test_unsupported_schema_is_rejected() -> None:
    payload = _record().to_dict()
    payload["schema_version"] = CURRENT_ARTIFACT_SCHEMA_VERSION + 1

    with pytest.raises(UnsupportedArtifactSchemaVersionError):
        ArtifactRecord.from_dict(payload)


def test_malformed_identity_is_rejected() -> None:
    payload = _record().to_dict()
    payload["artifact_id"] = "not-a-uuid"

    with pytest.raises(ArtifactValidationError, match="artifact_id"):
        ArtifactRecord.from_dict(payload)

    payload["artifact_id"] = str(UUID(int=0))
    with pytest.raises(ArtifactValidationError, match="artifact_id"):
        ArtifactRecord.from_dict(payload)


def test_invalid_timestamp_is_rejected() -> None:
    with pytest.raises(ArtifactValidationError, match="timezone-aware"):
        ArtifactRecord.create(
            kind=ArtifactKind.FILE,
            locator="x",
            created_at=datetime(2026, 1, 1),
        )

    payload = _record().to_dict()
    payload["created_at"] = "not-a-time"
    with pytest.raises(ArtifactValidationError, match="created_at"):
        ArtifactRecord.from_dict(payload)


def test_invalid_artifact_kind_is_rejected() -> None:
    with pytest.raises(ArtifactValidationError, match="ArtifactKind"):
        ArtifactRecord(
            artifact_id=ArtifactId.create(),
            kind="executable",  # type: ignore[arg-type]
            created_at=_T0,
            locator="x",
        )

    payload = _record().to_dict()
    payload["kind"] = "executable"
    with pytest.raises(ArtifactValidationError, match="unknown artifact kind"):
        ArtifactRecord.from_dict(payload)


def test_size_digest_and_text_validation_fail_closed() -> None:
    with pytest.raises(ArtifactValidationError, match="size_bytes"):
        ArtifactRecord.create(
            kind=ArtifactKind.FILE,
            locator="x",
            size_bytes=-1,
        )
    with pytest.raises(ArtifactValidationError, match="sha256"):
        ArtifactRecord.create(
            kind=ArtifactKind.FILE,
            locator="x",
            sha256="A" * 64,
        )
    with pytest.raises(ArtifactValidationError, match="locator"):
        ArtifactRecord.create(kind=ArtifactKind.FILE, locator=" x ")


def test_unknown_or_missing_serialized_fields_are_rejected() -> None:
    payload = _record().to_dict()
    payload["unexpected"] = True
    with pytest.raises(ArtifactValidationError, match="exactly"):
        ArtifactRecord.from_dict(payload)

    payload = _record().to_dict()
    del payload["locator"]
    with pytest.raises(ArtifactValidationError, match="exactly"):
        ArtifactRecord.from_dict(payload)


def test_malformed_json_or_non_object_json_is_rejected() -> None:
    with pytest.raises(ArtifactValidationError, match="malformed"):
        ArtifactRecord.from_json("{not-json")
    with pytest.raises(ArtifactValidationError, match="object"):
        ArtifactRecord.from_json("[]")


def test_record_is_immutable() -> None:
    record = _record()

    with pytest.raises(FrozenInstanceError):
        record.locator = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        record.sha256 = None  # type: ignore[misc]
