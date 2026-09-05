"""Unit tests for the canonical AgentX Task schema (``agentx.core.tasks``).

Covers:
    - creation (generated and explicit TaskId)
    - objective validation
    - UTC-aware creation timestamps
    - controlled status vocabulary without transition logic
    - controlled priority vocabulary that grants no authority
    - optional parent link and self-parent rejection
    - immutability and defensive metadata copying
    - deterministic serialization round trips
    - malformed deserialization
    - stable TaskId string for future C1.02 ``Event.task_id`` integration
    - absence of EventBus / persistence coupling
    - core remains a dependency leaf
"""

from __future__ import annotations

import ast
import json
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import cast
from uuid import UUID

import pytest

from agentx.core.ids import TaskId
from agentx.core.tasks import (
    TASK_SCHEMA_VERSION,
    JsonValue,
    Task,
    TaskDeserializationError,
    TaskPriority,
    TaskStatus,
    TaskValidationError,
    UnsupportedTaskSchemaVersionError,
)

_TASKS_MODULE = Path(__file__).resolve().parents[2] / "src" / "agentx" / "core" / "tasks.py"

_FIXED_CREATED_AT = datetime(2026, 9, 4, 12, 30, 45, 123456, tzinfo=UTC)


def _task_id() -> TaskId:
    """Create a canonical TaskId.

    ``DomainId.create`` is annotated with ``Self`` typing, so the concrete
    ``TaskId`` type is preserved directly.
    """
    return TaskId.create()


def _parsed_task_id(value: str) -> TaskId:
    return TaskId.parse(value)


# ---------------------------------------------------------------------------
# Creation and identity
# ---------------------------------------------------------------------------


class TestCreation:
    def test_create_generates_canonical_task_id(self) -> None:
        task = Task.create(objective="summarize the inbox")

        assert isinstance(task.task_id, TaskId)
        assert task.task_id.domain == "task"
        assert isinstance(task.task_id.value, UUID)
        assert task.task_id.value.int != 0

    def test_generated_task_ids_are_unique(self) -> None:
        ids = {Task.create(objective="objective").task_id.to_str() for _ in range(50)}
        assert len(ids) == 50

    def test_explicit_task_id_is_preserved(self) -> None:
        task_id = _task_id()
        task = Task.create(objective="summarize the inbox", task_id=task_id)

        assert task.task_id == task_id
        assert task.task_id_str == task_id.to_str()

    def test_create_applies_expected_defaults(self) -> None:
        task = Task.create(objective="summarize the inbox")

        assert task.objective == "summarize the inbox"
        assert task.status is TaskStatus.PENDING
        assert task.priority is TaskPriority.NORMAL
        assert task.parent_task_id is None
        assert task.is_root is True
        assert dict(task.metadata) == {}

    def test_direct_construction_is_validated(self) -> None:
        task = Task(
            task_id=_task_id(),
            objective="open the notes app",
            status=TaskStatus.RUNNING,
            priority=TaskPriority.HIGH,
            created_at=_FIXED_CREATED_AT,
        )

        assert task.status is TaskStatus.RUNNING
        assert task.priority is TaskPriority.HIGH
        assert task.created_at == _FIXED_CREATED_AT

    def test_raw_uuid_is_rejected_in_favour_of_task_id(self) -> None:
        with pytest.raises(TypeError, match="task_id must be a TaskId"):
            Task.create(objective="summarize the inbox", task_id=UUID(int=1))  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="task_id must be a TaskId"):
            Task(task_id=str(_task_id()), objective="summarize the inbox")  # type: ignore[arg-type]

    def test_stable_task_id_string_is_a_canonical_uuid(self) -> None:
        task = Task.create(objective="summarize the inbox")

        stable = task.task_id_str
        assert isinstance(stable, str)
        assert stable == str(task.task_id.value)
        assert stable == task.to_dict()["task_id"]
        # Round-trips through the canonical parser; usable as Event.task_id.
        assert _parsed_task_id(stable) == task.task_id
        assert task.to_dict()["task_id"] == Task(task_id=task.task_id, objective="x").task_id_str


# ---------------------------------------------------------------------------
# Objective validation
# ---------------------------------------------------------------------------


class TestObjective:
    @pytest.mark.parametrize("objective", ["", "   ", "\n", "\t"])
    def test_empty_or_blank_objective_is_rejected(self, objective: str) -> None:
        with pytest.raises(TaskValidationError, match="non-empty"):
            Task.create(objective=objective)

    def test_untrimmed_objective_is_rejected(self) -> None:
        with pytest.raises(TaskValidationError, match="trimmed"):
            Task.create(objective=" summarize the inbox ")

    def test_non_string_objective_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="objective must be a string"):
            Task.create(objective=42)  # type: ignore[arg-type]

    def test_control_characters_are_rejected(self) -> None:
        with pytest.raises(TaskValidationError, match="control characters"):
            Task.create(objective="summarize\x00the inbox")

    def test_objective_is_required_and_explicit(self) -> None:
        with pytest.raises(TypeError):
            Task.create()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


class TestCreatedAt:
    def test_generated_timestamp_is_utc_aware(self) -> None:
        task = Task.create(objective="summarize the inbox")

        assert task.created_at.tzinfo is UTC
        assert task.created_at.utcoffset() == timedelta(0)

    def test_naive_timestamp_is_rejected(self) -> None:
        with pytest.raises(TaskValidationError, match="timezone-aware"):
            Task.create(
                objective="summarize the inbox",
                created_at=datetime(2026, 9, 4, 12, 0, 0),
            )

    def test_non_utc_timestamp_is_normalized_to_utc(self) -> None:
        india = timezone(timedelta(hours=5, minutes=30))
        supplied = datetime(2026, 9, 4, 18, 0, tzinfo=india)

        task = Task.create(objective="summarize the inbox", created_at=supplied)

        assert task.created_at == datetime(2026, 9, 4, 12, 30, tzinfo=UTC)
        assert task.created_at.tzinfo is UTC

    def test_non_datetime_timestamp_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="created_at must be a datetime"):
            Task.create(
                objective="summarize the inbox",
                created_at="2026-09-04T12:00:00Z",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# Status vocabulary (no state machine)
# ---------------------------------------------------------------------------


class TestStatus:
    def test_vocabulary_is_controlled_and_small(self) -> None:
        assert [member.value for member in TaskStatus] == [
            "pending",
            "running",
            "succeeded",
            "failed",
            "cancelled",
        ]

    def test_default_status_is_pending(self) -> None:
        assert Task.create(objective="x").status is TaskStatus.PENDING

    def test_status_is_serialized_by_value(self) -> None:
        task = Task.create(objective="x", status=TaskStatus.FAILED)
        assert task.to_dict()["status"] == "failed"

    def test_non_vocabulary_status_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="status must be a TaskStatus"):
            Task.create(objective="x", status="completed")  # type: ignore[arg-type]

    def test_no_transition_logic_exists(self) -> None:
        forbidden = {"transition", "can_transition", "advance", "complete", "start", "fail"}
        assert forbidden.isdisjoint(dir(Task))
        assert "TaskStateMachine" not in dir(Task)

    def test_status_field_cannot_be_mutated(self) -> None:
        task = Task.create(objective="x")
        with pytest.raises(FrozenInstanceError):
            task.status = TaskStatus.RUNNING  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Priority (scheduling hint, never authority)
# ---------------------------------------------------------------------------


class TestPriority:
    def test_vocabulary_is_controlled_and_small(self) -> None:
        assert [member.value for member in TaskPriority] == ["low", "normal", "high", "critical"]

    def test_default_priority_is_normal(self) -> None:
        assert Task.create(objective="x").priority is TaskPriority.NORMAL

    def test_explicit_priority_round_trips(self) -> None:
        task = Task.create(objective="x", priority=TaskPriority.CRITICAL)
        assert Task.from_dict(task.to_dict()).priority is TaskPriority.CRITICAL

    def test_non_vocabulary_priority_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="priority must be a TaskPriority"):
            Task.create(objective="x", priority="urgent")  # type: ignore[arg-type]

    def test_priority_carries_no_authority_api(self) -> None:
        forbidden = {
            "bypass",
            "bypass_policy",
            "can_bypass",
            "escalate",
            "grant",
            "permissions",
            "allowed_capabilities",
            "requires_permission",
        }
        assert forbidden.isdisjoint(dir(Task))
        assert forbidden.isdisjoint(dir(TaskPriority))

    def test_priority_does_not_change_any_other_field(self) -> None:
        parent = _task_id()
        low = Task.create(objective="x", priority=TaskPriority.LOW, parent_task_id=parent)
        critical = Task.create(objective="x", priority=TaskPriority.CRITICAL, parent_task_id=parent)

        assert {field.name for field in fields(low)} == {field.name for field in fields(critical)}
        assert low.parent_task_id == critical.parent_task_id


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


class TestHierarchy:
    def test_parent_task_id_is_optional(self) -> None:
        assert Task.create(objective="x").parent_task_id is None
        assert Task.from_dict(Task.create(objective="x").to_dict()).parent_task_id is None

    def test_child_task_links_to_parent(self) -> None:
        parent = Task.create(objective="clean the workspace")
        child = Task.create(objective="empty the recycle bin", parent_task_id=parent.task_id)

        assert child.parent_task_id == parent.task_id
        assert child.is_root is False
        assert child.is_child_of(parent.task_id) is True
        assert parent.is_child_of(child.task_id) is False
        assert child.is_child_of(None) is False

    def test_grandchild_shape_is_representable(self) -> None:
        root = Task.create(objective="root")
        child = Task.create(objective="child", parent_task_id=root.task_id)
        grandchild = Task.create(objective="grandchild", parent_task_id=child.task_id)

        assert grandchild.parent_task_id == child.task_id
        assert child.parent_task_id == root.task_id

    def test_direct_self_parenting_is_rejected(self) -> None:
        task_id = _task_id()

        with pytest.raises(TaskValidationError, match="must not be its own parent"):
            Task.create(objective="x", task_id=task_id, parent_task_id=task_id)

        with pytest.raises(TaskValidationError, match="must not be its own parent"):
            Task(task_id=task_id, objective="x", parent_task_id=task_id)

    def test_non_task_id_parent_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="parent_task_id must be a TaskId or None"):
            Task.create(objective="x", parent_task_id="parent")  # type: ignore[arg-type]

    def test_parent_link_round_trips(self) -> None:
        parent = Task.create(objective="parent")
        child = Task.create(objective="child", parent_task_id=parent.task_id)

        restored = Task.from_json(child.to_json())
        assert restored.parent_task_id == parent.task_id


# ---------------------------------------------------------------------------
# Immutability and metadata
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_task_fields_cannot_be_assigned_or_deleted(self) -> None:
        task = Task.create(objective="x")

        with pytest.raises(FrozenInstanceError):
            task.objective = "changed"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            task.task_id = _task_id()  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            del task.objective

    def test_metadata_is_exposed_as_an_immutable_mapping(self) -> None:
        task = Task.create(objective="x", metadata={"attempt": 1})

        assert isinstance(task.metadata, MappingProxyType)
        with pytest.raises(TypeError):
            task.metadata["attempt"] = 2  # type: ignore[index]

    def test_source_mapping_is_defensively_copied(self) -> None:
        source: dict[str, object] = {"labels": ["before"]}
        task = Task.create(objective="x", metadata=source)

        source["injected"] = True
        labels = source["labels"]
        assert isinstance(labels, list)
        labels.append("after")

        assert dict(task.metadata) == {"labels": ("before",)}

    def test_nested_structures_are_frozen(self) -> None:
        task = Task.create(
            objective="x",
            metadata={"args": [1, {"deep": ["value"]}], "flag": True, "ratio": 0.5},
        )

        assert task.metadata["args"] == (1, MappingProxyType({"deep": ("value",)}))
        assert isinstance(task.metadata["args"], tuple)
        nested = task.metadata["args"][1]
        assert isinstance(nested, MappingProxyType)
        with pytest.raises(TypeError):
            nested["deep"] = "mutated"  # type: ignore[index]

    def test_metadata_rejects_non_json_values(self) -> None:
        with pytest.raises(TaskValidationError, match="non-JSON-compatible"):
            Task.create(objective="x", metadata={"callback": lambda: None})

        with pytest.raises(TaskValidationError, match="non-finite float"):
            Task.create(objective="x", metadata={"ratio": float("inf")})

        with pytest.raises(TaskValidationError, match="non-string metadata key"):
            Task.create(objective="x", metadata={1: "one"})  # type: ignore[dict-item]

    def test_metadata_is_not_a_secret_store(self) -> None:
        with pytest.raises(TaskValidationError, match="secret store"):
            Task.create(objective="x", metadata={"api_key": "abc123"})

    def test_metadata_carries_no_authority(self) -> None:
        with pytest.raises(TaskValidationError, match="authority or policy bypass"):
            Task.create(objective="x", metadata={"bypass_policy": True})

    def test_non_mapping_metadata_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="metadata must be a mapping"):
            Task.create(objective="x", metadata=["not", "a", "mapping"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_to_dict_is_json_compatible_and_complete(self) -> None:
        parent = _task_id()
        task = Task.create(
            objective="summarize the inbox",
            task_id=_parsed_task_id("11111111-1111-4111-8111-111111111111"),
            status=TaskStatus.RUNNING,
            priority=TaskPriority.HIGH,
            parent_task_id=parent,
            created_at=_FIXED_CREATED_AT,
            metadata={"attempt": 1, "labels": ["email"]},
        )

        encoded = task.to_dict()

        assert encoded == {
            "schema_version": TASK_SCHEMA_VERSION,
            "task_id": "11111111-1111-4111-8111-111111111111",
            "objective": "summarize the inbox",
            "status": "running",
            "priority": "high",
            "parent_task_id": parent.to_str(),
            "created_at": "2026-09-04T12:30:45.123456Z",
            "metadata": {"attempt": 1, "labels": ["email"]},
        }
        assert json.loads(task.to_json()) == encoded

    def test_to_json_is_deterministic(self) -> None:
        task = Task.create(objective="x", metadata={"b": 1, "a": [2, 3]})

        assert task.to_json() == task.to_json()
        assert task.to_json() == json.dumps(task.to_dict(), sort_keys=True, separators=(",", ":"))

    def test_round_trip_preserves_the_task(self) -> None:
        parent = Task.create(objective="parent")
        task = Task.create(
            objective="summarize the inbox",
            status=TaskStatus.SUCCEEDED,
            priority=TaskPriority.CRITICAL,
            parent_task_id=parent.task_id,
            created_at=_FIXED_CREATED_AT,
            metadata={"attempt": 2, "labels": ["email", "offline"], "ratio": 0.25},
        )

        assert Task.from_dict(task.to_dict()) == task
        assert Task.from_json(task.to_json()) == task

    def test_round_trip_keeps_timestamps_utc(self) -> None:
        task = Task.create(objective="x", created_at=_FIXED_CREATED_AT)
        restored = Task.from_dict(task.to_dict())

        assert restored.created_at == _FIXED_CREATED_AT
        assert restored.created_at.tzinfo is UTC
        assert restored.created_at.utcoffset() == timedelta(0)

    def test_naive_iso_timestamp_is_rejected_on_decode(self) -> None:
        raw = Task.create(objective="x").to_dict()
        raw["created_at"] = "2026-09-04T12:30:45"

        with pytest.raises(TaskDeserializationError, match="timezone-aware"):
            Task.from_dict(raw)


class TestMalformedDeserialization:
    def _raw(self) -> dict[str, JsonValue]:
        return Task.create(objective="summarize the inbox").to_dict()

    def test_malformed_json_text_fails_clearly(self) -> None:
        with pytest.raises(TaskDeserializationError, match="task JSON is malformed"):
            Task.from_json("{")

    def test_non_object_json_root_fails(self) -> None:
        with pytest.raises(TaskDeserializationError, match="root must be an object"):
            Task.from_json("[]")

    def test_non_string_json_input_fails(self) -> None:
        with pytest.raises(TypeError, match="task JSON must be a string"):
            Task.from_json(None)  # type: ignore[arg-type]

    def test_missing_and_unknown_fields_fail(self) -> None:
        missing = self._raw()
        del missing["objective"]
        with pytest.raises(
            TaskDeserializationError, match=r"missing required fields: \['objective'\]"
        ):
            Task.from_dict(missing)

        unknown = self._raw()
        unknown["executor"] = "agentx.executor"
        with pytest.raises(TaskDeserializationError, match=r"unknown fields: \['executor'\]"):
            Task.from_dict(unknown)

    def test_missing_schema_version_fails(self) -> None:
        raw = self._raw()
        del raw["schema_version"]
        with pytest.raises(TaskDeserializationError, match="schema_version"):
            Task.from_dict(raw)

    def test_unsupported_schema_version_fails(self) -> None:
        raw = self._raw()
        raw["schema_version"] = TASK_SCHEMA_VERSION + 1
        with pytest.raises(
            UnsupportedTaskSchemaVersionError, match="unsupported task schema version"
        ):
            Task.from_dict(raw)

    def test_invalid_task_ids_fail(self) -> None:
        raw = self._raw()
        raw["task_id"] = "not-a-uuid"
        with pytest.raises(TaskDeserializationError, match="not a valid TaskId"):
            Task.from_dict(raw)

        raw = self._raw()
        raw["parent_task_id"] = "00000000-0000-0000-0000-000000000000"
        with pytest.raises(TaskDeserializationError, match="not a valid TaskId"):
            Task.from_dict(raw)

        raw = self._raw()
        raw["task_id"] = 7
        with pytest.raises(TypeError, match="task_id must be a string"):
            Task.from_dict(raw)

    def test_self_parenting_is_detected_on_decode(self) -> None:
        raw = self._raw()
        raw["parent_task_id"] = raw["task_id"]

        with pytest.raises(TaskValidationError, match="must not be its own parent"):
            Task.from_dict(raw)

    def test_unknown_status_and_priority_fail(self) -> None:
        raw = self._raw()
        raw["status"] = "paused"
        with pytest.raises(TaskDeserializationError, match="status must be one of"):
            Task.from_dict(raw)

        raw = self._raw()
        raw["priority"] = "urgent"
        with pytest.raises(TaskDeserializationError, match="priority must be one of"):
            Task.from_dict(raw)

    def test_invalid_objective_and_metadata_fail(self) -> None:
        raw = self._raw()
        raw["objective"] = "   "
        with pytest.raises(TaskValidationError, match="non-empty"):
            Task.from_dict(raw)

        raw = self._raw()
        # Deliberately smuggle a non-JSON value past the type checker: decoding
        # untrusted input must validate it, not trust it.
        raw["metadata"] = cast(JsonValue, {"callback": object()})
        with pytest.raises(TaskValidationError, match="non-JSON-compatible"):
            Task.from_dict(raw)

        raw = self._raw()
        raw["metadata"] = None
        assert Task.from_dict(raw).metadata == MappingProxyType({})

    def test_invalid_timestamp_fails(self) -> None:
        raw = self._raw()
        raw["created_at"] = "not-a-timestamp"
        with pytest.raises(TaskDeserializationError, match="ISO-8601 timestamp"):
            Task.from_dict(raw)

        raw = self._raw()
        raw["created_at"] = 1756989045
        with pytest.raises(TypeError, match="created_at must be a string"):
            Task.from_dict(raw)


# ---------------------------------------------------------------------------
# Boundary / coupling guards
# ---------------------------------------------------------------------------


def _code_without_docstrings() -> str:
    """Return the module's code with every docstring removed, lowercased.

    Used to assert that *behaviour* is free of forbidden coupling; the
    docstrings legitimately explain that this schema is deliberately not
    coupled to the EventBus, to SQLite, or to a state machine.
    """
    tree = ast.parse(_TASKS_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree).lower()


def _module_imports() -> set[str]:
    tree = ast.parse(_TASKS_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is not None
            imported.add(node.module)
    return imported


class TestBoundaryCoupling:
    def test_task_schema_imports_only_stdlib_and_canonical_ids(self) -> None:
        assert _module_imports() == {
            "json",
            "math",
            "collections.abc",
            "dataclasses",
            "datetime",
            "enum",
            "types",
            "typing",
            "agentx.core.ids",
            "__future__",
        }

    def test_no_eventbus_or_persistence_coupling(self) -> None:
        code = _code_without_docstrings()
        for forbidden in ("event_bus", "eventbus", "publish", "subscribe", "sqlite", "persistence"):
            assert forbidden not in code

    def test_no_execution_or_scheduling_behaviour(self) -> None:
        code = _code_without_docstrings()
        for forbidden in ("subprocess", "threading", "asyncio", "exec(", "eval(", "schedul"):
            assert forbidden not in code

    def test_no_state_machine_or_task_manager_surface(self) -> None:
        tree = ast.parse(_TASKS_MODULE.read_text(encoding="utf-8"))
        defined = {
            node.name for node in tree.body if isinstance(node, ast.ClassDef | ast.FunctionDef)
        }
        assert not {"TaskManager", "TaskStateMachine", "TaskStore"} & defined

    def test_task_exposes_data_contract_only(self) -> None:
        public = {name for name in dir(Task) if not name.startswith("_")}
        assert public == {
            "create",
            "created_at",
            "from_dict",
            "from_json",
            "is_child_of",
            "is_root",
            "metadata",
            "objective",
            "parent_task_id",
            "priority",
            "status",
            "task_id",
            "task_id_str",
            "to_dict",
            "to_json",
        }
