"""Tests for the canonical inert artifact-record contract (C2.04)."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from agentx.core.artifacts import (
    CURRENT_ARTIFACT_SCHEMA_VERSION,
    ArtifactDigest,
    ArtifactDigestAlgorithm,
    ArtifactKind,
    ArtifactRecord,
    ArtifactValidationError,
    UnsupportedArtifactSchemaVersionError,
)
from agentx.core.ids import ArtifactId, EpisodeId, KnowledgeId, TaskId

_T0 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2025, 3, 1, 9, 0, 0, 500000, tzinfo=UTC)
_SHA256_HEX = "ab" * 32

_HOSTILE_STRINGS = (
    "../../secrets",
    "..\\..\\windows\\system32",
    "file:///etc/passwd",
    "https://evil.example/payload",
    "import os; os.system('rm -rf /')",
    "ADMIN",
    "ALLOW R4",
    "risk=R0",
    "verified=true",
    "permission=WRITE",
    "budget=unlimited",
    "clear emergency stop",
    "print('ARTIFACT_STORE_EXECUTED')",
)


def _record(**overrides: Any) -> ArtifactRecord:
    values: dict[str, Any] = {
        "artifact_id": ArtifactId.create(),
        "kind": ArtifactKind.GENERATED,
        "reference": "artifacts/2025/report.pdf",
        "created_at": _T0,
    }
    values.update(overrides)
    return ArtifactRecord(**values)


# ---------------------------------------------------------------------------
# Canonical record validation
# ---------------------------------------------------------------------------


def test_minimal_record_accepts_only_required_fields() -> None:
    record = _record()

    assert record.media_type is None
    assert record.digest is None
    assert record.size_bytes is None
    assert record.task_id is None
    assert record.episode_id is None
    assert record.schema_version == CURRENT_ARTIFACT_SCHEMA_VERSION


@pytest.mark.parametrize("reference", _HOSTILE_STRINGS)
def test_hostile_reference_text_is_accepted_verbatim_as_inert_data(reference: str) -> None:
    """Inertness starts at the contract: hostile text is data, not behavior."""
    record = _record(reference=reference)

    assert record.reference == reference
    restored = ArtifactRecord.from_json(record.to_json())
    assert restored.reference == reference
    assert restored == record


@pytest.mark.parametrize("bad", ["", "   ", " leading", "trailing ", "a" * 4_097, 42, None])
def test_reference_validation_rejects_invalid_values(bad: object) -> None:
    with pytest.raises(ArtifactValidationError, match="reference"):
        _record(reference=bad)


@pytest.mark.parametrize("media_type", ["", " padded ", "text/plain", "application/vnd.a+b"])
def test_media_type_validation(media_type: str) -> None:
    if media_type == media_type.strip() and media_type:
        assert _record(media_type=media_type).media_type == media_type
    else:
        with pytest.raises(ArtifactValidationError, match="media_type"):
            _record(media_type=media_type)


def test_media_type_rejects_oversize_and_non_strings() -> None:
    with pytest.raises(ArtifactValidationError, match="media_type"):
        _record(media_type="x" * 256)
    with pytest.raises(ArtifactValidationError, match="media_type"):
        _record(media_type=b"text/plain")


@pytest.mark.parametrize("size", [0, 1, 2**62])
def test_size_bytes_accepts_non_negative_integers(size: int | None) -> None:
    assert _record(size_bytes=size).size_bytes == size


@pytest.mark.parametrize("size", [-1, 1.5, True, "12"])
def test_size_bytes_rejects_invalid_values(size: object) -> None:
    with pytest.raises(ArtifactValidationError, match="size_bytes"):
        _record(size_bytes=size)


@pytest.mark.parametrize(
    ("bad_id", "match"),
    [
        ("not-a-uuid", "artifact_id"),
        (str(UUID(int=0)), "artifact_id"),
        (None, "artifact_id"),
    ],
)
def test_artifact_id_must_be_a_canonical_artifact_id(bad_id: object, match: str) -> None:
    with pytest.raises(ArtifactValidationError, match=match):
        _record(artifact_id=bad_id)

    # Cross-domain canonical IDs never substitute, even with valid UUID bytes.
    with pytest.raises(ArtifactValidationError, match="artifact_id"):
        _record(artifact_id=KnowledgeId.create())


def test_correlation_ids_use_canonical_task_and_episode_types() -> None:
    task = TaskId.create()
    episode = EpisodeId.create()
    record = _record(task_id=task, episode_id=episode)

    assert record.task_id == task
    assert record.episode_id == episode

    with pytest.raises(ArtifactValidationError, match="task_id"):
        _record(task_id=str(task))
    with pytest.raises(ArtifactValidationError, match="episode_id"):
        _record(episode_id=ArtifactId.create())


def test_created_at_must_be_timezone_aware_and_normalizes_to_utc() -> None:
    with pytest.raises(ArtifactValidationError, match="created_at"):
        _record(created_at=datetime(2025, 1, 1, 12, 0, 0))

    offset_zone = timezone(timedelta(hours=5, minutes=30))
    record = _record(created_at=datetime(2025, 1, 1, 17, 30, 0, tzinfo=offset_zone))

    assert record.created_at == _T0
    assert record.created_at.tzinfo is UTC


def test_kind_is_a_controlled_storage_vocabulary() -> None:
    assert {kind.value for kind in ArtifactKind} == {"generated", "observed"}
    with pytest.raises(ArtifactValidationError, match="kind"):
        _record(kind="generated")


# ---------------------------------------------------------------------------
# Integrity metadata (digest) validation
# ---------------------------------------------------------------------------


def test_digest_round_trips_and_validates_shape() -> None:
    digest = ArtifactDigest(algorithm=ArtifactDigestAlgorithm.SHA256, hex_digest=_SHA256_HEX)

    record = _record(digest=digest)
    assert record.digest == digest
    assert ArtifactRecord.from_json(record.to_json()).digest == digest

    with pytest.raises(ArtifactValidationError, match="digest"):
        ArtifactDigest(algorithm=ArtifactDigestAlgorithm.SHA256, hex_digest="AB" * 32)
    with pytest.raises(ArtifactValidationError, match="digest"):
        ArtifactDigest(algorithm=ArtifactDigestAlgorithm.SHA256, hex_digest="deadbeef")
    with pytest.raises(ArtifactValidationError, match="digest"):
        ArtifactDigest(algorithm=ArtifactDigestAlgorithm.SHA256, hex_digest="zz" * 32)
    with pytest.raises(ArtifactValidationError, match="algorithm"):
        ArtifactDigest(algorithm="sha256", hex_digest=_SHA256_HEX)  # type: ignore[arg-type]


def test_digest_document_rejects_unknown_members() -> None:
    digest = ArtifactDigest(algorithm=ArtifactDigestAlgorithm.SHA256, hex_digest=_SHA256_HEX)
    document = digest.to_dict()
    document["algorithm"] = "md5"

    with pytest.raises(ArtifactValidationError, match="algorithm"):
        ArtifactDigest.from_dict(document)


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_records_are_frozen_and_slot_backed() -> None:
    record = _record()

    for field_name in ("artifact_id", "kind", "reference", "created_at", "media_type", "digest"):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(record, field_name, "mutated")
    assert not hasattr(record, "__dict__")

    digest = ArtifactDigest(algorithm=ArtifactDigestAlgorithm.SHA256, hex_digest=_SHA256_HEX)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(digest, "hex_digest", "cd" * 32)  # noqa: B010


def test_records_are_hashable_and_equal_by_content() -> None:
    first = _record()
    same = ArtifactRecord.from_json(first.to_json())

    assert first == same
    assert hash(first) == hash(same)
    assert len({first, same}) == 1
    assert first != _record()


# ---------------------------------------------------------------------------
# Deterministic serialization
# ---------------------------------------------------------------------------


def test_to_json_is_deterministic_and_key_sorted() -> None:
    record = _record(
        media_type="application/pdf",
        digest=ArtifactDigest(algorithm=ArtifactDigestAlgorithm.SHA256, hex_digest=_SHA256_HEX),
        size_bytes=42,
        task_id=TaskId.create(),
        episode_id=EpisodeId.create(),
        created_at=_T1,
    )

    text = record.to_json()

    assert text == record.to_json()
    assert text == json.dumps(record.to_dict(), separators=(",", ":"), sort_keys=True)
    assert list(json.loads(text)) == sorted(json.loads(text))


def test_serialization_preserves_timestamp_format_and_unicode() -> None:
    record = _record(reference="üntïcode/файл.txt", created_at=_T1)
    document = json.loads(record.to_json())

    assert document["created_at"] == "2025-03-01T09:00:00.500000Z"
    assert document["reference"] == "üntïcode/файл.txt"
    assert "\\u" not in record.to_json()


def test_round_trip_through_from_json_is_lossless() -> None:
    record = _record(
        media_type="image/png",
        digest=ArtifactDigest(algorithm=ArtifactDigestAlgorithm.SHA256, hex_digest=_SHA256_HEX),
        size_bytes=1,
        task_id=TaskId.create(),
        episode_id=EpisodeId.create(),
        created_at=_T1,
    )

    restored = ArtifactRecord.from_json(record.to_json())

    assert restored == record
    assert restored.to_json() == record.to_json()


@pytest.mark.parametrize(
    "bad_json",
    ["", "{not json", "[]", '"text"', "null", "5"],
)
def test_from_json_rejects_non_object_or_malformed_documents(bad_json: str) -> None:
    with pytest.raises(ArtifactValidationError):
        ArtifactRecord.from_json(bad_json)


def test_from_dict_rejects_missing_unknown_and_mis_typed_fields() -> None:
    document = _record().to_dict()

    without = dict(document)
    without.pop("reference")
    with pytest.raises(ArtifactValidationError, match="missing required fields"):
        ArtifactRecord.from_dict(without)

    with pytest.raises(ArtifactValidationError, match="unknown fields"):
        ArtifactRecord.from_dict({**document, "verified": True})

    with pytest.raises(ArtifactValidationError, match="kind"):
        ArtifactRecord.from_dict({**document, "kind": "trusted"})

    with pytest.raises(ArtifactValidationError, match="created_at"):
        ArtifactRecord.from_dict({**document, "created_at": "yesterday"})


def test_unsupported_schema_versions_fail_closed() -> None:
    document = _record().to_dict()

    for version in (0, 2, -1, "1", None):
        expected = (
            UnsupportedArtifactSchemaVersionError
            if isinstance(version, int)
            else ArtifactValidationError
        )
        with pytest.raises(expected):
            ArtifactRecord.from_dict({**document, "schema_version": version})


def test_create_factory_defaults_identity_and_time() -> None:
    before = datetime.now(UTC)
    record = ArtifactRecord.create(
        kind=ArtifactKind.OBSERVED,
        reference="screenshots/2025-09-05.png",
    )
    after = datetime.now(UTC)

    assert isinstance(record.artifact_id, ArtifactId)
    assert record.kind is ArtifactKind.OBSERVED
    assert before <= record.created_at <= after
    assert record.media_type is None
    assert record.digest is None
    assert record.size_bytes is None


def test_current_schema_version_is_one() -> None:
    assert CURRENT_ARTIFACT_SCHEMA_VERSION == 1
