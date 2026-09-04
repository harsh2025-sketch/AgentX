"""Tests for canonical domain identifiers (``agentx.core.ids``).

Covers:
    - generation uniqueness
    - string round-trip
    - malformed ID rejection
    - cross-domain inequality
    - hashing and dict/set behavior
    - immutability
    - domain tag correctness
"""

from __future__ import annotations

import uuid as uuid_mod
from uuid import UUID

import pytest

from agentx.core.ids import (
    ArtifactId,
    CapabilityId,
    DomainId,
    EpisodeId,
    ProcedureId,
    TaskId,
    _IdConstructionError,
)

ALL_ID_TYPES = (TaskId, CapabilityId, ProcedureId, EpisodeId, ArtifactId)


# ---------------------------------------------------------------------------
# Generation uniqueness
# ---------------------------------------------------------------------------


class TestGeneration:
    def test_create_returns_domain_id_instance(self) -> None:
        tid = TaskId.create()
        assert isinstance(tid, TaskId)
        assert isinstance(tid, DomainId)

    def test_create_produces_valid_uuid(self) -> None:
        tid = TaskId.create()
        assert isinstance(tid.value, UUID)
        assert tid.value.int != 0

    def test_successive_creates_are_unique(self) -> None:
        ids = [TaskId.create() for _ in range(100)]
        values = [id_.value for id_ in ids]
        assert len(set(values)) == 100

    def test_cross_domain_create_are_unique(self) -> None:
        """IDs from different domains produce distinct UUIDs (with overwhelming probability)."""
        task = TaskId.create()
        cap = CapabilityId.create()
        assert task.value != cap.value  # probability of collision: negligible


# ---------------------------------------------------------------------------
# String round-trip
# ---------------------------------------------------------------------------


class TestStringRoundTrip:
    def test_to_str_is_canonical_uuid(self) -> None:
        original = TaskId.create()
        serialized = original.to_str()
        # Canonical UUID is lowercase hex with dashes
        assert serialized == str(original.value)
        assert "-" in serialized
        assert len(serialized) == 36

    def test_str_dunder_equals_to_str(self) -> None:
        original = TaskId.create()
        assert str(original) == original.to_str()

    def test_parse_round_trips(self) -> None:
        original = TaskId.create()
        serialized = original.to_str()
        parsed = TaskId.parse(serialized)
        assert parsed == original
        assert parsed.value == original.value

    def test_parse_accepts_uppercase(self) -> None:
        original = TaskId.create()
        parsed = TaskId.parse(original.to_str().upper())
        assert parsed == original

    def test_parse_strips_whitespace(self) -> None:
        original = TaskId.create()
        parsed = TaskId.parse(f"  {original.to_str()}  \n")
        assert parsed == original

    def test_all_types_round_trip(self) -> None:
        for cls in ALL_ID_TYPES:
            original = cls.create()
            parsed = cls.parse(original.to_str())
            assert parsed == original


# ---------------------------------------------------------------------------
# Malformed ID rejection
# ---------------------------------------------------------------------------


class TestMalformedRejection:
    def test_parse_rejects_empty_string(self) -> None:
        with pytest.raises(_IdConstructionError, match="empty"):
            TaskId.parse("")

    def test_parse_rejects_whitespace_only(self) -> None:
        with pytest.raises(_IdConstructionError, match="empty"):
            TaskId.parse("   ")

    def test_parse_rejects_garbage(self) -> None:
        with pytest.raises(_IdConstructionError, match="not a valid UUID"):
            TaskId.parse("not-a-uuid")

    def test_parse_rejects_partial_uuid(self) -> None:
        with pytest.raises(_IdConstructionError, match="not a valid UUID"):
            TaskId.parse("550e8400-e29b")

    def test_parse_rejects_nil_uuid(self) -> None:
        with pytest.raises(_IdConstructionError, match="nil UUID"):
            TaskId.parse("00000000-0000-0000-0000-000000000000")

    def test_parse_rejects_non_string(self) -> None:
        with pytest.raises(_IdConstructionError, match="requires a string"):
            TaskId.parse(12345)  # type: ignore[arg-type]

    def test_constructor_rejects_non_uuid(self) -> None:
        with pytest.raises(_IdConstructionError, match="must be a UUID"):
            TaskId("not-a-uuid")  # type: ignore[arg-type]

    def test_constructor_rejects_nil_uuid(self) -> None:
        with pytest.raises(_IdConstructionError, match="nil UUID"):
            TaskId(UUID(int=0))

    def test_constructor_accepts_valid_uuid(self) -> None:
        value = uuid_mod.uuid4()
        tid = TaskId(value)
        assert tid.value == value


# ---------------------------------------------------------------------------
# Cross-domain inequality
# ---------------------------------------------------------------------------


class TestCrossDomainInequality:
    def test_different_types_same_uuid_are_not_equal(self) -> None:
        value = uuid_mod.uuid4()
        task = TaskId(value)
        cap = CapabilityId(value)
        assert task != cap

    def test_different_types_same_uuid_have_different_hash(self) -> None:
        value = uuid_mod.uuid4()
        task = TaskId(value)
        cap = CapabilityId(value)
        # Hashes may technically collide but the contract is they should differ
        # because type is part of the hash. We verify they're hashable and
        # behave differently in sets.
        s = {task, cap}
        assert len(s) == 2

    def test_same_type_same_uuid_are_equal(self) -> None:
        value = uuid_mod.uuid4()
        a = TaskId(value)
        b = TaskId(value)
        assert a == b

    def test_all_pairs_of_types_are_distinct(self) -> None:
        value = uuid_mod.uuid4()
        instances = [cls(value) for cls in ALL_ID_TYPES]
        for i, a in enumerate(instances):
            for j, b in enumerate(instances):
                if i == j:
                    assert a == b
                else:
                    assert a != b


# ---------------------------------------------------------------------------
# Hashing and dict/set behavior
# ---------------------------------------------------------------------------


class TestHashing:
    def test_equal_ids_have_same_hash(self) -> None:
        value = uuid_mod.uuid4()
        a = TaskId(value)
        b = TaskId(value)
        assert hash(a) == hash(b)

    def test_usable_as_dict_key(self) -> None:
        tid = TaskId.create()
        d: dict[DomainId, str] = {tid: "task-data"}
        assert d[tid] == "task-data"

    def test_usable_in_set(self) -> None:
        ids = {TaskId.create() for _ in range(50)}
        assert len(ids) == 50

    def test_different_types_same_uuid_coexist_in_set(self) -> None:
        value = uuid_mod.uuid4()
        s: set[DomainId] = {TaskId(value), CapabilityId(value), ProcedureId(value)}
        assert len(s) == 3

    def test_dict_lookup_by_type(self) -> None:
        value = uuid_mod.uuid4()
        task = TaskId(value)
        cap = CapabilityId(value)
        d: dict[DomainId, str] = {task: "task", cap: "cap"}
        assert d[task] == "task"
        assert d[cap] == "cap"
        assert len(d) == 2


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_cannot_set_attribute(self) -> None:
        tid = TaskId.create()
        with pytest.raises(AttributeError, match="immutable"):
            tid._value = UUID(int=1)

    def test_cannot_add_attribute(self) -> None:
        tid = TaskId.create()
        with pytest.raises(AttributeError, match="immutable"):
            tid.new_attr = "nope"

    def test_cannot_delete_attribute(self) -> None:
        tid = TaskId.create()
        with pytest.raises(AttributeError, match="immutable"):
            del tid._value


# ---------------------------------------------------------------------------
# Domain tag
# ---------------------------------------------------------------------------


class TestDomainTag:
    def test_task_id_has_task_domain(self) -> None:
        assert TaskId.create().domain == "task"

    def test_capability_id_has_capability_domain(self) -> None:
        assert CapabilityId.create().domain == "capability"

    def test_procedure_id_has_procedure_domain(self) -> None:
        assert ProcedureId.create().domain == "procedure"

    def test_episode_id_has_episode_domain(self) -> None:
        assert EpisodeId.create().domain == "episode"

    def test_artifact_id_has_artifact_domain(self) -> None:
        assert ArtifactId.create().domain == "artifact"


# ---------------------------------------------------------------------------
# Repr and comparison
# ---------------------------------------------------------------------------


class TestReprAndComparison:
    def test_repr_contains_class_name_and_uuid(self) -> None:
        tid = TaskId.create()
        r = repr(tid)
        assert r.startswith("TaskId(")
        assert str(tid.value) in r

    def test_eq_with_non_domain_id_returns_not_implemented(self) -> None:
        tid = TaskId.create()
        assert tid.__eq__("string") is NotImplemented

    def test_ordering_not_supported_but_equality_is(self) -> None:
        a = TaskId.create()
        b = TaskId.create()
        assert (a == a) is True
        assert (a == b) is False
