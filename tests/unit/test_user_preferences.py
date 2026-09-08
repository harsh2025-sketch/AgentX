"""Unit tests for the M6.04 explicit user-preference model contract."""

from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest

from agentx.core.knowledge import (
    KnowledgeScope,
    ProvenanceKind,
    ProvenanceReference,
    ScopeDimension,
)
from agentx.core.provenance import EvidenceKind, EvidenceReference
from agentx.core.user_preferences import (
    CANONICAL_PREFERENCE_KEYS,
    CANONICAL_PREFERENCE_SOURCES,
    MAX_PREFERENCE_EVIDENCE_REFERENCES,
    MAX_PREFERENCE_NOTE_LENGTH,
    MAX_PREFERENCE_SCOPE_VALUE_LENGTH,
    MAX_PREFERENCE_VALUE_DEPTH,
    MAX_PREFERENCE_VALUE_NODES,
    MAX_PREFERENCE_VALUE_STRING_LENGTH,
    USER_PREFERENCE_SCHEMA_VERSION,
    PreferenceKey,
    PreferenceSource,
    UnsupportedUserPreferenceSchemaVersionError,
    UserPreference,
    UserPreferenceDeserializationError,
    UserPreferenceValidationError,
)

_T0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 3, 2, 9, 30, 0, 500000, tzinfo=UTC)
_PID_A = UUID("11111111-1111-4111-8111-111111111111")
_PID_B = UUID("22222222-2222-4222-8222-222222222222")


def _provenance() -> ProvenanceReference:
    return ProvenanceReference(kind=ProvenanceKind.USER, reference="user-settings-dialog")


def _evidence(reference: str = "utterance-1") -> EvidenceReference:
    return EvidenceReference(
        kind=EvidenceKind.OBSERVATION,
        reference=reference,
        provenance=_provenance(),
    )


def _scoped() -> KnowledgeScope:
    return KnowledgeScope(
        dimensions={
            ScopeDimension.APPLICATION: "notes-app",
            ScopeDimension.ENVIRONMENT: "workstation-a",
        }
    )


# ---------------------------------------------------------------------------
# Valid construction
# ---------------------------------------------------------------------------


def test_valid_explicit_preference() -> None:
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.INTERACTION_STYLE,
        value="concise",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )

    assert record.preference_id == _PID_A
    assert record.key is PreferenceKey.INTERACTION_STYLE
    assert record.value == "concise"
    assert record.source is PreferenceSource.USER_EXPLICIT
    assert record.recorded_at == _T0
    assert record.scope == KnowledgeScope()
    assert record.supersedes is None
    assert record.evidence == ()
    assert record.note is None
    assert record.schema_version == USER_PREFERENCE_SCHEMA_VERSION == 1


def test_every_canonical_key_is_representable() -> None:
    assert set(CANONICAL_PREFERENCE_KEYS) == set(PreferenceKey)
    assert len(CANONICAL_PREFERENCE_KEYS) == 6
    for key in PreferenceKey:
        record = UserPreference(
            preference_id=uuid4(),
            key=key,
            value="v",
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
        )
        assert record.key is key


def test_source_vocabulary_is_exactly_explicit_and_correction() -> None:
    assert set(CANONICAL_PREFERENCE_SOURCES) == {
        PreferenceSource.USER_EXPLICIT,
        PreferenceSource.USER_CORRECTION,
    }
    assert len(CANONICAL_PREFERENCE_SOURCES) == 2
    assert [s.value for s in CANONICAL_PREFERENCE_SOURCES] == [
        "user_explicit",
        "user_correction",
    ]


def test_user_correction_points_at_earlier_record() -> None:
    original = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.DEFAULT_APPLICATION,
        value="app-a",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )
    correction = UserPreference(
        preference_id=_PID_B,
        key=PreferenceKey.DEFAULT_APPLICATION,
        value="app-b",
        source=PreferenceSource.USER_CORRECTION,
        recorded_at=_T1,
        supersedes=original.preference_id,
        note="user corrected the default",
    )

    assert correction.source is PreferenceSource.USER_CORRECTION
    assert correction.supersedes == original.preference_id
    assert correction.value == "app-b"
    # Prior record is untouched historical data.
    assert original.value == "app-a"
    assert original.supersedes is None
    assert original.source is PreferenceSource.USER_EXPLICIT


def test_correction_without_supersedes_is_representable() -> None:
    # "where appropriate": a correction need not always name a prior record.
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value={"wrap": 100},
        source=PreferenceSource.USER_CORRECTION,
        recorded_at=_T0,
    )
    assert record.supersedes is None


def test_supersedes_self_is_rejected() -> None:
    with pytest.raises(UserPreferenceValidationError, match="supersedes"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value="x",
            source=PreferenceSource.USER_CORRECTION,
            recorded_at=_T0,
            supersedes=_PID_A,
        )


def test_global_preference_uses_empty_scope() -> None:
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.NOTIFICATION_PREFERENCE,
        value={"email": False},
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )
    assert record.scope == KnowledgeScope()
    assert dict(record.scope.dimensions) == {}


def test_scoped_preference_reuses_canonical_scope() -> None:
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.DEFAULT_APPLICATION,
        value="notes-app",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
        scope=_scoped(),
    )
    assert record.scope.value_for(ScopeDimension.APPLICATION) == "notes-app"
    assert record.scope.value_for(ScopeDimension.ENVIRONMENT) == "workstation-a"
    assert record.scope.value_for(ScopeDimension.PROJECT) is None


def test_unicode_value_and_note_are_plain_data() -> None:
    value = {"greeting": "héllo — émoji: 🧠 ok\nsecond line"}
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.INTERACTION_STYLE,
        value=value,
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
        note="préférence — 日本語 🧠",
    )
    assert record.to_dict()["value"] == value
    assert record.note == "préférence — 日本語 🧠"


def test_nested_json_value_round_trips() -> None:
    value = {
        "style": "concise",
        "levels": [1, 2.5, True, None, {"nested": ["a", "b"]}],
        "empty": {},
    }
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.INTERACTION_STYLE,
        value=value,
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )
    assert record.to_dict()["value"] == value
    assert UserPreference.from_dict(record.to_dict()) == record


@pytest.mark.parametrize(
    "scalar",
    [True, False, 0, 42, -7, 2.5, "", "x", None],
)
def test_scalar_values_are_accepted(scalar: object) -> None:
    record = UserPreference(
        preference_id=uuid4(),
        key=PreferenceKey.FORMAT_PREFERENCE,
        value=scalar,
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )
    assert record.to_dict()["value"] == scalar


def test_tuple_input_freezes_to_tuple_and_serializes_as_list() -> None:
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.WORKFLOW_PREFERENCE,
        value=("a", "b"),
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )
    assert record.value == ("a", "b")
    assert record.to_dict()["value"] == ["a", "b"]


def test_evidence_is_carried_verbatim() -> None:
    first = _evidence("utterance-1")
    second = _evidence("utterance-2")
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.WORKFLOW_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
        evidence=(first, second),
    )
    assert record.evidence == (first, second)
    assert UserPreference.from_dict(record.to_dict()).evidence == (first, second)


def test_create_generates_identity_and_defaults_to_global_scope() -> None:
    first = UserPreference.create(
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )
    second = UserPreference.create(
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )
    assert first.preference_id != second.preference_id
    assert first.scope == KnowledgeScope()
    assert first.recorded_at == _T0


# ---------------------------------------------------------------------------
# Immutability and caller-mutation isolation
# ---------------------------------------------------------------------------


def test_record_is_immutable() -> None:
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.INTERACTION_STYLE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )
    with pytest.raises(FrozenInstanceError):
        record.value = "y"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        record.note = "y"  # type: ignore[misc]


def test_value_is_deep_frozen() -> None:
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.INTERACTION_STYLE,
        value={"a": {"b": [1, 2]}},
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )
    assert isinstance(record.value, MappingProxyType)
    inner = record.value["a"]
    assert isinstance(inner, MappingProxyType)
    assert isinstance(inner["b"], tuple)
    with pytest.raises(TypeError):
        record.value["a"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        inner["b"] = ()  # type: ignore[index]


def test_caller_mutation_of_input_does_not_escape() -> None:
    raw: dict[str, object] = {"a": [1, 2], "b": "x"}
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.INTERACTION_STYLE,
        value=raw,
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )
    raw["a"] = "mutated"
    raw["b"] = "mutated"
    assert record.to_dict()["value"] == {"a": [1, 2], "b": "x"}


def test_caller_mutation_of_output_does_not_escape() -> None:
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.INTERACTION_STYLE,
        value={"a": [1, 2]},
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )
    exported = record.to_dict()
    value = exported["value"]
    assert isinstance(value, dict)
    value["a"] = "mutated"
    assert record.to_dict()["value"] == {"a": [1, 2]}


def test_scope_mutation_does_not_escape() -> None:
    dimensions = {ScopeDimension.APPLICATION: "notes-app"}
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.DEFAULT_APPLICATION,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
        scope=KnowledgeScope(dimensions=dimensions),
    )
    dimensions[ScopeDimension.APPLICATION] = "mutated"
    assert record.scope.value_for(ScopeDimension.APPLICATION) == "notes-app"


# ---------------------------------------------------------------------------
# Key / source validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad", ["interaction_style", "INTERACTION_STYLE", "", "unknown", 123, None]
)
def test_invalid_key_type_is_rejected(bad: object) -> None:
    with pytest.raises(UserPreferenceValidationError, match="key"):
        UserPreference(
            preference_id=_PID_A,
            key=bad,  # type: ignore[arg-type]
            value="x",
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
        )


def test_unknown_key_string_rejected_on_decode() -> None:
    raw = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    ).to_dict()
    raw["key"] = "favorite_color"
    with pytest.raises(UserPreferenceDeserializationError, match="unknown preference key"):
        UserPreference.from_dict(raw)


@pytest.mark.parametrize("bad", ["user_explicit", "USER_EXPLICIT", "inferred", "", None, 123])
def test_invalid_source_type_is_rejected(bad: object) -> None:
    with pytest.raises(UserPreferenceValidationError, match="source"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value="x",
            source=bad,  # type: ignore[arg-type]
            recorded_at=_T0,
        )


@pytest.mark.parametrize("bad", ["inferred", "research", "model", "system", "verified"])
def test_unknown_source_string_rejected_on_decode(bad: str) -> None:
    raw = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    ).to_dict()
    raw["source"] = bad
    with pytest.raises(UserPreferenceDeserializationError, match="unknown preference source"):
        UserPreference.from_dict(raw)


def test_inference_is_never_labelled_explicit() -> None:
    # No inferred/research member exists in the closed vocabulary.
    assert "inferred" not in {s.value for s in PreferenceSource}
    assert "research" not in {s.value for s in PreferenceSource}
    assert len(PreferenceSource) == 2


# ---------------------------------------------------------------------------
# Value domain
# ---------------------------------------------------------------------------


def test_nan_is_rejected() -> None:
    with pytest.raises(UserPreferenceValidationError, match="non-finite"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value=math.nan,
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
        )


def test_infinity_is_rejected() -> None:
    for bad in (math.inf, -math.inf):
        with pytest.raises(UserPreferenceValidationError, match="non-finite"):
            UserPreference(
                preference_id=_PID_A,
                key=PreferenceKey.FORMAT_PREFERENCE,
                value=bad,
                source=PreferenceSource.USER_EXPLICIT,
                recorded_at=_T0,
            )


def test_nested_nan_is_rejected() -> None:
    with pytest.raises(UserPreferenceValidationError, match="non-finite"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value={"a": [1.0, math.nan]},
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
        )


def test_callable_is_rejected() -> None:
    with pytest.raises(UserPreferenceValidationError, match="non-JSON-compatible"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value=lambda: "x",
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
        )


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(b"bytes", id="bytes"),
        pytest.param(bytearray(b"x"), id="bytearray"),
        pytest.param({1, 2}, id="set"),
        pytest.param(object(), id="object"),
        pytest.param(_T0, id="datetime"),
        pytest.param(_PID_A, id="uuid"),
    ],
)
def test_non_json_values_are_rejected(bad: object) -> None:
    with pytest.raises(UserPreferenceValidationError, match="non-JSON-compatible"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value=bad,
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
        )


def test_non_string_dict_key_is_rejected() -> None:
    with pytest.raises(UserPreferenceValidationError, match="non-string object key"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value={1: "x"},
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
        )


def test_reference_cycle_is_rejected() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(UserPreferenceValidationError, match="cycle"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value=cyclic,
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
        )


def test_dict_cycle_is_rejected() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(UserPreferenceValidationError, match="cycle"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value=cyclic,
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
        )


def test_oversized_string_value_is_rejected() -> None:
    with pytest.raises(UserPreferenceValidationError, match="maximum string length"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value="x" * (MAX_PREFERENCE_VALUE_STRING_LENGTH + 1),
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
        )


def test_oversized_nesting_depth_is_rejected() -> None:
    nested: object = "leaf"
    for _ in range(MAX_PREFERENCE_VALUE_DEPTH + 2):
        nested = [nested]
    with pytest.raises(UserPreferenceValidationError, match="maximum value depth"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value=nested,
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
        )


def test_oversized_node_count_is_rejected() -> None:
    with pytest.raises(UserPreferenceValidationError, match="maximum"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value=list(range(MAX_PREFERENCE_VALUE_NODES + 1)),
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
        )


def test_max_depth_boundary_is_accepted() -> None:
    nested: object = "leaf"
    for _ in range(MAX_PREFERENCE_VALUE_DEPTH):
        nested = [nested]
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value=nested,
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )
    assert record.to_dict()["value"] is not None


# ---------------------------------------------------------------------------
# Bounds: scope / evidence / note / identity
# ---------------------------------------------------------------------------


def test_oversized_scope_value_is_rejected() -> None:
    scope = KnowledgeScope(
        dimensions={ScopeDimension.APPLICATION: "x" * (MAX_PREFERENCE_SCOPE_VALUE_LENGTH + 1)}
    )
    with pytest.raises(UserPreferenceValidationError, match="scope"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.DEFAULT_APPLICATION,
            value="x",
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
            scope=scope,
        )


def test_scope_must_be_canonical_scope() -> None:
    with pytest.raises(UserPreferenceValidationError, match="scope"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.DEFAULT_APPLICATION,
            value="x",
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
            scope={"application": "x"},  # type: ignore[arg-type]
        )


def test_oversized_evidence_count_is_rejected() -> None:
    evidence = tuple(_evidence(f"u-{i}") for i in range(MAX_PREFERENCE_EVIDENCE_REFERENCES + 1))
    with pytest.raises(UserPreferenceValidationError, match="evidence"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.WORKFLOW_PREFERENCE,
            value="x",
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
            evidence=evidence,
        )


def test_evidence_must_be_tuple_of_evidence_reference() -> None:
    with pytest.raises(UserPreferenceValidationError, match="evidence"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.WORKFLOW_PREFERENCE,
            value="x",
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
            evidence=[_evidence()],  # type: ignore[arg-type]
        )
    with pytest.raises(UserPreferenceValidationError, match="evidence"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.WORKFLOW_PREFERENCE,
            value="x",
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
            evidence=("not-evidence",),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("bad", ["", "   ", " leading", "trailing ", 123])
def test_invalid_note_is_rejected(bad: object) -> None:
    with pytest.raises(UserPreferenceValidationError, match="note"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value="x",
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
            note=bad,  # type: ignore[arg-type]
        )


def test_oversized_note_is_rejected() -> None:
    with pytest.raises(UserPreferenceValidationError, match="note"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value="x",
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
            note="x" * (MAX_PREFERENCE_NOTE_LENGTH + 1),
        )


def test_nil_preference_id_is_rejected() -> None:
    with pytest.raises(UserPreferenceValidationError, match="nil UUID"):
        UserPreference(
            preference_id=UUID(int=0),
            key=PreferenceKey.FORMAT_PREFERENCE,
            value="x",
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
        )


def test_nil_supersedes_is_rejected() -> None:
    with pytest.raises(UserPreferenceValidationError, match="nil UUID"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value="x",
            source=PreferenceSource.USER_CORRECTION,
            recorded_at=_T0,
            supersedes=UUID(int=0),
        )


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(UserPreferenceValidationError, match="timezone-aware"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value="x",
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=datetime(2026, 3, 1, 12, 0, 0),
        )


def test_timestamp_is_normalized_to_utc() -> None:
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=datetime(2026, 3, 1, 14, 0, 0, tzinfo=timezone(timedelta(hours=2))),
    )
    assert record.recorded_at == datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)


def test_naive_timestamp_string_rejected_on_decode() -> None:
    raw = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    ).to_dict()
    raw["recorded_at"] = "2026-03-01T12:00:00"
    with pytest.raises(UserPreferenceDeserializationError, match="timezone-aware"):
        UserPreference.from_dict(raw)


def test_malformed_timestamp_string_rejected_on_decode() -> None:
    raw = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    ).to_dict()
    raw["recorded_at"] = "not-a-timestamp"
    with pytest.raises(UserPreferenceDeserializationError, match="ISO-8601"):
        UserPreference.from_dict(raw)


# ---------------------------------------------------------------------------
# Conflicts and corrections
# ---------------------------------------------------------------------------


def test_conflicting_preferences_remain_distinct() -> None:
    first = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.CONFIRMATION_PREFERENCE,
        value="always-confirm",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
        scope=_scoped(),
    )
    second = UserPreference(
        preference_id=_PID_B,
        key=PreferenceKey.CONFIRMATION_PREFERENCE,
        value="never-confirm",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T1,
        scope=_scoped(),
    )
    # Same key and scope, contradictory values: two separate records, no merge.
    assert first != second
    assert first.key == second.key
    assert first.scope == second.scope
    assert first.value != second.value
    assert first.supersedes is None
    assert second.supersedes is None


def test_correction_does_not_mutate_prior_record() -> None:
    original_dict = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.NOTIFICATION_PREFERENCE,
        value={"email": True},
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    ).to_dict()
    UserPreference(
        preference_id=_PID_B,
        key=PreferenceKey.NOTIFICATION_PREFERENCE,
        value={"email": False},
        source=PreferenceSource.USER_CORRECTION,
        recorded_at=_T1,
        supersedes=_PID_A,
    )
    restored = UserPreference.from_dict(original_dict)
    assert restored.to_dict() == original_dict


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_exact_field_set() -> None:
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    )
    assert set(record.to_dict()) == {
        "schema_version",
        "preference_id",
        "key",
        "value",
        "source",
        "scope",
        "recorded_at",
        "supersedes",
        "evidence",
        "note",
    }


def test_deterministic_serialization() -> None:
    kwargs: dict[str, object] = {
        "preference_id": _PID_A,
        "key": PreferenceKey.WORKFLOW_PREFERENCE,
        "value": {"b": [3, 2, 1], "a": {"z": 1, "y": 2}},
        "source": PreferenceSource.USER_EXPLICIT,
        "recorded_at": _T0,
        "scope": _scoped(),
        "evidence": (_evidence("u-2"), _evidence("u-1")),
        "note": "note",
    }
    first = UserPreference(**kwargs)  # type: ignore[arg-type]
    second = UserPreference(**kwargs)  # type: ignore[arg-type]
    assert first.to_json() == second.to_json()
    # Canonical JSON: sorted keys, compact separators, unicode preserved.
    assert first.to_json() == json.dumps(
        first.to_dict(), ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    )


def test_serialization_uses_utc_zulu() -> None:
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=datetime(2026, 3, 1, 14, 0, 0, tzinfo=timezone(timedelta(hours=2))),
    )
    exported = record.to_dict()
    assert exported["recorded_at"] == "2026-03-01T12:00:00.000000Z"


def test_round_trip() -> None:
    record = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.NOTIFICATION_PREFERENCE,
        value={"email": False, "tags": ["a", "b"]},
        source=PreferenceSource.USER_CORRECTION,
        recorded_at=_T1,
        scope=_scoped(),
        supersedes=_PID_B,
        evidence=(_evidence(),),
        note="note",
    )
    assert UserPreference.from_dict(record.to_dict()) == record
    assert UserPreference.from_json(record.to_json()) == record


def test_unknown_field_rejected() -> None:
    raw = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    ).to_dict()
    raw["authorized"] = True
    with pytest.raises(UserPreferenceDeserializationError, match="unknown fields"):
        UserPreference.from_dict(raw)


def test_missing_field_rejected() -> None:
    raw = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    ).to_dict()
    del raw["source"]
    with pytest.raises(UserPreferenceDeserializationError, match="missing required fields"):
        UserPreference.from_dict(raw)


def test_unsupported_schema_version_rejected() -> None:
    raw = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    ).to_dict()
    raw["schema_version"] = 999
    with pytest.raises(UnsupportedUserPreferenceSchemaVersionError, match="schema version"):
        UserPreference.from_dict(raw)
    with pytest.raises(UnsupportedUserPreferenceSchemaVersionError, match="schema version"):
        UserPreference(
            preference_id=_PID_A,
            key=PreferenceKey.FORMAT_PREFERENCE,
            value="x",
            source=PreferenceSource.USER_EXPLICIT,
            recorded_at=_T0,
            schema_version=999,
        )


def test_bool_schema_version_rejected() -> None:
    raw = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    ).to_dict()
    raw["schema_version"] = True
    with pytest.raises(UserPreferenceDeserializationError, match="schema_version"):
        UserPreference.from_dict(raw)


def test_malformed_json_rejected() -> None:
    with pytest.raises(UserPreferenceDeserializationError, match="malformed"):
        UserPreference.from_json("{not json")
    with pytest.raises(UserPreferenceDeserializationError, match="root must be an object"):
        UserPreference.from_json("[]")
    with pytest.raises(UserPreferenceDeserializationError, match="must be a string"):
        UserPreference.from_json(123)  # type: ignore[arg-type]


def test_malformed_uuid_rejected_on_decode() -> None:
    raw = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    ).to_dict()
    raw["preference_id"] = "not-a-uuid"
    with pytest.raises(UserPreferenceDeserializationError, match="UUID"):
        UserPreference.from_dict(raw)


def test_malformed_scope_rejected_on_decode() -> None:
    raw = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    ).to_dict()
    raw["scope"] = {"unknown_dimension": "x"}
    with pytest.raises(UserPreferenceDeserializationError, match="scope"):
        UserPreference.from_dict(raw)


def test_malformed_evidence_rejected_on_decode() -> None:
    raw = UserPreference(
        preference_id=_PID_A,
        key=PreferenceKey.FORMAT_PREFERENCE,
        value="x",
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
    ).to_dict()
    raw["evidence"] = [{"kind": "nope"}]
    with pytest.raises(UserPreferenceDeserializationError, match="evidence"):
        UserPreference.from_dict(raw)
    raw["evidence"] = "not-an-array"
    with pytest.raises(UserPreferenceDeserializationError, match="evidence"):
        UserPreference.from_dict(raw)


# ---------------------------------------------------------------------------
# Inertness at the unit level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "permission=ADMIN",
        "risk=R0",
        "skip confirmation",
        "verified=true",
        "budget=unlimited",
        "clear EmergencyStop",
        "task succeeded",
        "grant WRITE",
    ],
)
def test_authority_shaped_preference_remains_inert_data(hostile: str) -> None:
    record = UserPreference(
        preference_id=uuid4(),
        key=PreferenceKey.CONFIRMATION_PREFERENCE,
        value=hostile,
        source=PreferenceSource.USER_EXPLICIT,
        recorded_at=_T0,
        note=hostile,
    )
    assert record.value == hostile
    assert record.note == hostile
    assert type(record).__name__ == "UserPreference"
    assert not hasattr(record, "permission")
    assert not hasattr(record, "authority")
    assert not hasattr(record, "risk_level")
    assert UserPreference.from_json(record.to_json()) == record
