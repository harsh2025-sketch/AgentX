"""Unit tests for the C4.01 canonical failure taxonomy contract."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.failure_taxonomy import (
    CANONICAL_FAILURE_CATEGORIES,
    FAILURE_CLASSIFICATION_SCHEMA_VERSION,
    FailureCategory,
    FailureClassification,
    FailureClassificationDeserializationError,
    FailureClassificationValidationError,
    UnsupportedFailureClassificationSchemaVersionError,
)
from agentx.core.ids import EpisodeId, NegativeExperienceId, TaskId

_NOW = datetime(2026, 9, 5, 12, 30, 15, 123456, tzinfo=UTC)

_EXPECTED_CATEGORY_VALUES = (
    "transient",
    "precondition",
    "environment",
    "permission",
    "dependency",
    "ui_change",
    "api_change",
    "capability",
    "procedure",
    "knowledge",
    "plan",
    "verification",
    "unknown",
)


def _classification(**overrides: object) -> FailureClassification:
    payload: dict[str, object] = {
        "category": FailureCategory.TRANSIENT,
        "summary": "Observed a momentary disturbance during the attempt.",
        "classified_at": _NOW,
    }
    payload.update(overrides)
    return FailureClassification(**payload)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_canonical_categories_are_exactly_the_architecture_vocabulary() -> None:
    assert tuple(member.value for member in FailureCategory) == _EXPECTED_CATEGORY_VALUES
    assert tuple(FailureCategory) == CANONICAL_FAILURE_CATEGORIES
    assert len(CANONICAL_FAILURE_CATEGORIES) == 13


def test_category_names_match_the_canonical_architecture_names() -> None:
    assert {member.name for member in FailureCategory} == {
        "TRANSIENT",
        "PRECONDITION",
        "ENVIRONMENT",
        "PERMISSION",
        "DEPENDENCY",
        "UI_CHANGE",
        "API_CHANGE",
        "CAPABILITY",
        "PROCEDURE",
        "KNOWLEDGE",
        "PLAN",
        "VERIFICATION",
        "UNKNOWN",
    }


def test_category_values_are_unique_and_lowercase() -> None:
    values = [member.value for member in FailureCategory]

    assert len(set(values)) == len(values)
    assert all(value == value.lower() for value in values)


def test_categories_are_unordered_and_carry_no_severity_or_policy_attributes() -> None:
    member = FailureCategory.PERMISSION

    for attribute in (
        "severity",
        "confidence",
        "probability",
        "score",
        "weight",
        "retryable",
        "retry",
        "repair",
        "remedy",
        "rank",
    ):
        assert not hasattr(member, attribute)


def test_schema_version_is_one() -> None:
    assert FAILURE_CLASSIFICATION_SCHEMA_VERSION == 1


# ---------------------------------------------------------------------------
# Construction and validation
# ---------------------------------------------------------------------------


def test_minimal_classification_exposes_its_canonical_fields() -> None:
    record = _classification()

    assert record.category is FailureCategory.TRANSIENT
    assert record.summary == "Observed a momentary disturbance during the attempt."
    assert record.classified_at == _NOW
    assert record.detail is None
    assert record.error_code is None
    assert record.task_id is None
    assert record.episode_id is None
    assert record.negative_experience_id is None
    assert record.correlation_id is None
    assert record.schema_version == FAILURE_CLASSIFICATION_SCHEMA_VERSION


def test_unknown_is_a_valid_first_class_classification() -> None:
    record = _classification(
        category=FailureCategory.UNKNOWN,
        summary="Evidence does not justify a more specific class.",
    )

    assert record.category is FailureCategory.UNKNOWN
    assert record.is_unknown is True
    assert _classification().is_unknown is False


def test_every_canonical_category_can_be_constructed() -> None:
    for category in CANONICAL_FAILURE_CATEGORIES:
        record = _classification(category=category)

        assert record.category is category


@pytest.mark.parametrize(
    "bad_category",
    ["permission", "PERMISSION", "unknown", 3, None, object()],
)
def test_category_must_be_a_typed_member_never_a_string(bad_category: object) -> None:
    with pytest.raises(FailureClassificationValidationError, match="category"):
        _classification(category=bad_category)


@pytest.mark.parametrize(
    "bad_summary",
    ["", "   ", " leading", "trailing ", "line\nbreak", "tab\tstop", "null\x00byte", 7, None],
)
def test_summary_must_be_non_empty_trimmed_single_line_text(bad_summary: object) -> None:
    with pytest.raises(FailureClassificationValidationError, match="summary"):
        _classification(summary=bad_summary)


def test_summary_length_is_bounded() -> None:
    assert _classification(summary="s" * 512).summary == "s" * 512

    with pytest.raises(FailureClassificationValidationError, match="summary"):
        _classification(summary="s" * 513)


def test_detail_is_optional_but_validated() -> None:
    assert _classification(detail="Longer inert observation text.").detail == (
        "Longer inert observation text."
    )
    assert _classification(detail="first line\nsecond line").detail == "first line\nsecond line"

    for bad_detail in ("", " padded ", "null\x00byte", 5):
        with pytest.raises(FailureClassificationValidationError, match="detail"):
            _classification(detail=bad_detail)

    with pytest.raises(FailureClassificationValidationError, match="detail"):
        _classification(detail="d" * 4097)


def test_classified_at_must_be_timezone_aware_and_normalizes_to_utc() -> None:
    aware = datetime(2026, 9, 5, 14, 0, tzinfo=timezone(timedelta(hours=2)))

    assert _classification(classified_at=aware).classified_at == aware.astimezone(UTC)

    for bad_timestamp in (datetime(2026, 9, 5, 14, 0), "2026-09-05T14:00:00Z", 0):
        with pytest.raises(FailureClassificationValidationError, match="classified_at"):
            _classification(classified_at=bad_timestamp)


def test_schema_version_is_validated() -> None:
    with pytest.raises(FailureClassificationValidationError, match="schema_version"):
        _classification(schema_version=2)
    with pytest.raises(FailureClassificationValidationError, match="schema_version"):
        _classification(schema_version=True)
    with pytest.raises(FailureClassificationValidationError, match="schema_version"):
        _classification(schema_version="1")


# ---------------------------------------------------------------------------
# Evidence references
# ---------------------------------------------------------------------------


def test_error_code_reference_reuses_the_canonical_agentx_error_code() -> None:
    error = AgentXError(
        code="capability.not_found",
        message="The requested capability is not registered.",
        category=ErrorCategory.NOT_FOUND,
    )
    record = _classification(category=FailureCategory.CAPABILITY, error_code=error.code)

    assert record.error_code == "capability.not_found"
    assert record.error_code == error.to_dict()["code"]


@pytest.mark.parametrize(
    "bad_code",
    ["", "   ", " padded ", "with\nnewline", "with\x00null", 42],
)
def test_error_code_reference_is_validated(bad_code: object) -> None:
    with pytest.raises(FailureClassificationValidationError, match="error_code"):
        _classification(error_code=bad_code)


def test_error_code_reference_length_is_bounded() -> None:
    with pytest.raises(FailureClassificationValidationError, match="error_code"):
        _classification(error_code="c" * 257)


def test_identity_references_must_be_canonical_typed_ids() -> None:
    task_id = TaskId.create()
    episode_id = EpisodeId.create()
    negative_experience_id = NegativeExperienceId.create()
    correlation_id = uuid4()

    record = _classification(
        task_id=task_id,
        episode_id=episode_id,
        negative_experience_id=negative_experience_id,
        correlation_id=correlation_id,
    )

    assert record.task_id == task_id
    assert record.episode_id == episode_id
    assert record.negative_experience_id == negative_experience_id
    assert record.correlation_id == correlation_id


def test_identity_references_reject_foreign_or_untyped_values() -> None:
    with pytest.raises(FailureClassificationValidationError, match="task_id"):
        _classification(task_id=str(uuid4()))
    with pytest.raises(FailureClassificationValidationError, match="episode_id"):
        _classification(episode_id=TaskId.create())
    with pytest.raises(FailureClassificationValidationError, match="negative_experience_id"):
        _classification(negative_experience_id=EpisodeId.create())
    with pytest.raises(FailureClassificationValidationError, match="correlation_id"):
        _classification(correlation_id=str(uuid4()))
    with pytest.raises(FailureClassificationValidationError, match="correlation_id"):
        _classification(correlation_id=UUID(int=0))


# ---------------------------------------------------------------------------
# Immutability, equality, hashing
# ---------------------------------------------------------------------------


def test_records_are_immutable() -> None:
    record = _classification()

    with pytest.raises(FrozenInstanceError):
        record.category = FailureCategory.PERMISSION  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        record.summary = "rewritten"  # type: ignore[misc]
    assert not hasattr(record, "__dict__")
    assert FailureClassification.__slots__


def test_equality_and_hashing_are_structural() -> None:
    first = _classification()
    second = _classification()
    different = _classification(category=FailureCategory.PLAN)

    assert first == second
    assert hash(first) == hash(second)
    assert first != different
    assert len({first, second, different}) == 2
    unrelated: object = "not a classification"
    assert first != unrelated


def test_equality_distinguishes_every_canonical_category() -> None:
    records = {_classification(category=category) for category in CANONICAL_FAILURE_CATEGORIES}

    assert len(records) == len(CANONICAL_FAILURE_CATEGORIES)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_to_dict_is_the_exact_canonical_shape() -> None:
    record = _classification()

    assert record.to_dict() == {
        "schema_version": 1,
        "category": "transient",
        "summary": "Observed a momentary disturbance during the attempt.",
        "classified_at": "2026-09-05T12:30:15.123456Z",
        "detail": None,
        "error_code": None,
        "task_id": None,
        "episode_id": None,
        "negative_experience_id": None,
        "correlation_id": None,
    }


def test_to_json_is_deterministic_and_sorted() -> None:
    record = _classification(detail="inert detail", error_code="capability.not_found")

    encoded = record.to_json()

    assert encoded == record.to_json()
    assert list(json.loads(encoded)) == sorted(json.loads(encoded))
    assert ", " not in encoded


def test_every_canonical_category_round_trips_deterministically() -> None:
    for category in CANONICAL_FAILURE_CATEGORIES:
        record = _classification(
            category=category,
            summary=f"Observed {category.value} failure.",
            detail="inert detail",
            error_code="execution.failed",
            task_id=TaskId.create(),
            episode_id=EpisodeId.create(),
            negative_experience_id=NegativeExperienceId.create(),
            correlation_id=uuid4(),
        )

        encoded = record.to_json()
        restored = FailureClassification.from_json(encoded)

        assert restored == record
        assert restored.category is category
        assert restored.to_json() == encoded
        assert FailureClassification.from_dict(record.to_dict()) == record


def test_from_dict_rejects_unknown_category_values_instead_of_downgrading() -> None:
    payload = _classification().to_dict()
    payload["category"] = "definitely_not_canonical"

    with pytest.raises(FailureClassificationDeserializationError, match="category"):
        FailureClassification.from_dict(payload)


def test_from_dict_rejects_category_shaped_hostile_strings() -> None:
    payload = _classification().to_dict()

    for hostile in ("PERMISSION", " permission", "permission=WRITE", "unknown ", ""):
        payload["category"] = hostile
        with pytest.raises(FailureClassificationDeserializationError, match="category"):
            FailureClassification.from_dict(payload)


def test_from_dict_requires_the_exact_field_set() -> None:
    payload = _classification().to_dict()
    missing = dict(payload)
    del missing["summary"]

    with pytest.raises(FailureClassificationDeserializationError, match="missing"):
        FailureClassification.from_dict(missing)

    extra = dict(payload)
    extra["confidence"] = 0.99
    with pytest.raises(FailureClassificationDeserializationError, match="unknown fields"):
        FailureClassification.from_dict(extra)


def test_from_dict_rejects_unsupported_or_malformed_schema_versions() -> None:
    payload = _classification().to_dict()

    payload_v2 = dict(payload)
    payload_v2["schema_version"] = 2
    with pytest.raises(UnsupportedFailureClassificationSchemaVersionError):
        FailureClassification.from_dict(payload_v2)

    payload_bool = dict(payload)
    payload_bool["schema_version"] = True
    with pytest.raises(FailureClassificationDeserializationError, match="schema_version"):
        FailureClassification.from_dict(payload_bool)

    payload_missing = dict(payload)
    del payload_missing["schema_version"]
    with pytest.raises(FailureClassificationDeserializationError, match="schema_version"):
        FailureClassification.from_dict(payload_missing)


def test_from_dict_rejects_malformed_field_types() -> None:
    payload = _classification().to_dict()

    for field, value, match in (
        ("summary", 5, "summary"),
        ("detail", 5, "detail"),
        ("error_code", 5, "error_code"),
        ("classified_at", 5, "classified_at"),
        ("classified_at", "not-a-timestamp", "classified_at"),
        ("classified_at", "2026-09-05T12:30:15.123456", "timezone-aware"),
        ("task_id", "not-a-uuid", "task_id"),
        ("episode_id", 5, "episode_id"),
        ("negative_experience_id", "not-a-uuid", "negative_experience_id"),
        ("correlation_id", "not-a-uuid", "correlation_id"),
        ("correlation_id", str(UUID(int=0)), "correlation_id"),
    ):
        broken = dict(payload)
        broken[field] = value
        with pytest.raises(FailureClassificationDeserializationError, match=match):
            FailureClassification.from_dict(broken)


def test_from_dict_surfaces_value_validation_as_deserialization_failure() -> None:
    payload = _classification().to_dict()
    payload["summary"] = "  untrimmed  "

    with pytest.raises(FailureClassificationDeserializationError, match="summary"):
        FailureClassification.from_dict(payload)


def test_from_json_fails_closed_on_hostile_or_malformed_input() -> None:
    with pytest.raises(FailureClassificationDeserializationError, match="malformed"):
        FailureClassification.from_json("{not json")
    with pytest.raises(FailureClassificationDeserializationError, match="object"):
        FailureClassification.from_json("[]")
    with pytest.raises(FailureClassificationDeserializationError, match="object"):
        FailureClassification.from_json('"permission"')
    with pytest.raises(FailureClassificationDeserializationError, match="text"):
        FailureClassification.from_json(b"{}")  # type: ignore[arg-type]


def test_timestamps_round_trip_with_offset_normalization() -> None:
    aware = datetime(2026, 9, 5, 14, 0, 0, 500000, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    record = _classification(classified_at=aware)

    restored = FailureClassification.from_json(record.to_json())

    assert restored.classified_at == aware
    assert restored.to_dict()["classified_at"] == "2026-09-05T08:30:00.500000Z"


def test_serialization_uses_no_object_hooks_or_executable_types() -> None:
    record = _classification(detail="unicode détail ✓")

    decoded = json.loads(record.to_json())

    assert isinstance(decoded, dict)
    assert all(isinstance(key, str) for key in decoded)
    assert all(value is None or isinstance(value, str | int) for value in decoded.values())
