"""Unit tests for the C7.01 runtime-to-UI state protocol."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID, uuid4

import pytest

from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.events import (
    ActionPayload,
    DecisionPayload,
    EmptyPayload,
    Event,
    EventType,
    GoalPayload,
    ObservationPayload,
    SelectionPayload,
    VerificationPayload,
)
from agentx.core.ids import ProcedureId
from agentx.core.tasks import Task, TaskPriority, TaskStatus
from agentx.core.ui_state import (
    CURRENT_UI_STATE_SCHEMA_VERSION,
    UiExecutionLevel,
    UiPermission,
    UiRiskLevel,
    UiStateApproval,
    UiStateDeserializationError,
    UiStateError,
    UiStateExecution,
    UiStatePlan,
    UiStateProjectionError,
    UiStateSnapshot,
    UiStateTask,
    UiStateValidationError,
    UiStateVerification,
    UiVerificationStatus,
    UnsupportedUiStateSchemaVersionError,
    project_state_event,
)

T0 = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 9, 6, 12, 0, 1, tzinfo=UTC)


def _make_task() -> Task:
    return Task.create(objective="organize the downloads folder")


def _pending_snapshot(**overrides: object) -> UiStateSnapshot:
    base: dict[str, object] = {
        "schema_version": CURRENT_UI_STATE_SCHEMA_VERSION,
        "task": UiStateTask(
            task_id=_make_task().task_id, status=TaskStatus.PENDING, priority=TaskPriority.NORMAL
        ),
        "execution": UiStateExecution(level=None, operation=None),
        "verification": UiStateVerification(status=UiVerificationStatus.NOT_STARTED, detail=None),
        "timestamp": T0,
    }
    base.update(overrides)
    return UiStateSnapshot(**base)  # type: ignore[arg-type]


def _running_snapshot(task: Task | None = None) -> UiStateSnapshot:
    resolved_task = _make_task() if task is None else task
    return UiStateSnapshot(
        schema_version=CURRENT_UI_STATE_SCHEMA_VERSION,
        task=UiStateTask(
            task_id=resolved_task.task_id, status=TaskStatus.RUNNING, priority=TaskPriority.HIGH
        ),
        execution=UiStateExecution(
            level=UiExecutionLevel.L2_COMPILED, operation="browser.dom.click"
        ),
        verification=UiStateVerification(status=UiVerificationStatus.NOT_STARTED, detail=None),
        timestamp=T0,
        plan=UiStatePlan(procedure_id=ProcedureId.create(), revision=3),
        approval=UiStateApproval(
            request_id=uuid4(),
            operation="browser.dom.click",
            permission=UiPermission.WRITE,
            risk_level=UiRiskLevel.R2,
        ),
        error=UiStateError(
            code="capability.denied",
            message="capability request denied",
            category=ErrorCategory.PERMISSION,
            retryability=Retryability.NON_RETRYABLE,
            details={"capability": "browser.dom"},
        ),
        progress=0.5,
    )


def _payload_for(event_type: EventType) -> object:
    """Payload required by the canonical envelope for the given event kind."""
    if event_type is EventType.GOAL_RECEIVED:
        return GoalPayload(text="tidy the desk")
    if event_type is EventType.ROUTING_DECISION:
        return DecisionPayload(decision="L2_COMPILED")
    if event_type is EventType.CAPABILITY_SELECTED:
        return SelectionPayload(selection="browser.dom")
    if event_type is EventType.OBSERVATION_RECORDED:
        return ObservationPayload(value={"ok": True})
    if event_type is EventType.ACTION_REQUESTED:
        return ActionPayload(name="browser.dom.click")
    return EmptyPayload()


def _event_for(
    event_type: EventType, task: Task, *, timestamp: datetime, payload: object = None
) -> Event:
    return Event.create(
        event_type=event_type,
        source="agentx.test",
        task_id=task.task_id_str,
        timestamp=timestamp,
        payload=_payload_for(event_type) if payload is None else payload,  # type: ignore[arg-type]
    )


def _wire_of(snapshot: UiStateSnapshot) -> dict[str, object]:
    wire = dict(snapshot.to_dict())
    return {key: value for key, value in wire.items()}


def _wire_section(wire: dict[str, object], section: str) -> dict[str, object]:
    return cast("dict[str, object]", wire[section])


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------


class TestConstruction:
    def test_from_task_builds_initial_observation(self) -> None:
        task = _make_task()
        snapshot = UiStateSnapshot.from_task(task, timestamp=T0)

        assert snapshot.schema_version == CURRENT_UI_STATE_SCHEMA_VERSION
        assert snapshot.task.task_id == task.task_id
        assert snapshot.task.status == TaskStatus.PENDING
        assert snapshot.task.priority == TaskPriority.NORMAL
        assert snapshot.execution.level is None
        assert snapshot.execution.operation is None
        assert snapshot.verification.status == UiVerificationStatus.NOT_STARTED
        assert snapshot.verification.detail is None
        assert snapshot.plan is None
        assert snapshot.approval is None
        assert snapshot.error is None
        assert snapshot.progress is None
        assert snapshot.timestamp == T0
        assert not snapshot.is_terminal

    def test_from_task_uses_canonical_task_fields(self) -> None:
        task = Task.create(objective="book a taxi", priority=TaskPriority.CRITICAL)
        snapshot = UiStateSnapshot.from_task(task, timestamp=T0)

        assert snapshot.task.task_id == task.task_id
        assert snapshot.task.priority == TaskPriority.CRITICAL
        assert snapshot.task.status == task.status

    def test_from_task_rejects_non_task(self) -> None:
        with pytest.raises(TypeError, match="task must be a Task"):
            UiStateSnapshot.from_task(object(), timestamp=T0)  # type: ignore[arg-type]

    def test_timestamp_is_normalized_to_utc(self) -> None:
        task = _make_task()
        local = datetime(2026, 9, 6, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))

        snapshot = UiStateSnapshot.from_task(task, timestamp=local)

        assert snapshot.timestamp == datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
        assert snapshot.timestamp.tzinfo is UTC

    def test_naive_timestamp_is_rejected(self) -> None:
        with pytest.raises(UiStateValidationError, match="timestamp must be timezone-aware"):
            _pending_snapshot(timestamp=datetime(2026, 9, 6, 12, 0, 0))

    def test_unsupported_schema_version_is_rejected(self) -> None:
        with pytest.raises(
            UnsupportedUiStateSchemaVersionError, match="unsupported ui state schema version 2"
        ):
            _pending_snapshot(schema_version=2)

    def test_bool_schema_version_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="schema_version must be an integer"):
            _pending_snapshot(schema_version=True)

    def test_progress_bounds(self) -> None:
        for value in (0.0, 0.25, 1.0, 0, 1):
            assert _pending_snapshot(progress=value).progress == float(value)

        for value in (-0.1, 1.01, float("nan"), float("inf")):
            with pytest.raises(UiStateValidationError, match="progress"):
                _pending_snapshot(progress=value)
        with pytest.raises(TypeError, match="progress must be a number"):
            _pending_snapshot(progress=True)

    def test_wrong_section_types_are_rejected(self) -> None:
        with pytest.raises(TypeError, match="task must be a UiStateTask"):
            _pending_snapshot(task="task-00000000-0000-0000-0000-000000000000")
        with pytest.raises(TypeError, match="plan must be a UiStatePlan"):
            _pending_snapshot(plan={"procedure_id": "x"})
        with pytest.raises(TypeError, match="approval must be a UiStateApproval"):
            _pending_snapshot(approval="APPROVED")


class TestSectionValidation:
    def test_operation_rules(self) -> None:
        with pytest.raises(UiStateValidationError, match="operation must be non-empty"):
            UiStateExecution(level=None, operation="")
        with pytest.raises(UiStateValidationError, match="operation must be non-empty"):
            UiStateExecution(level=None, operation="  ")
        with pytest.raises(UiStateValidationError, match="operation must be non-empty"):
            UiStateExecution(level=None, operation=" padded ")
        with pytest.raises(UiStateValidationError, match="control characters"):
            UiStateExecution(level=None, operation="line\nbreak")
        with pytest.raises(UiStateValidationError, match="control characters"):
            UiStateExecution(level=None, operation="nul\x00byte")
        with pytest.raises(UiStateValidationError, match="must not exceed 256"):
            UiStateExecution(level=None, operation="x" * 257)
        with pytest.raises(TypeError, match="operation must be a string"):
            UiStateExecution(level=None, operation=42)  # type: ignore[arg-type]

        ok = UiStateExecution(level=UiExecutionLevel.L1_DIRECT, operation="a" * 256)
        assert ok.operation == "a" * 256

    def test_level_must_be_a_protocol_level(self) -> None:
        with pytest.raises(TypeError, match="level must be a UiExecutionLevel"):
            UiStateExecution(level="L9_QUANTUM", operation=None)  # type: ignore[arg-type]

    def test_detail_rules(self) -> None:
        with pytest.raises(UiStateValidationError, match="detail must be non-empty"):
            UiStateVerification(status=UiVerificationStatus.PASSED, detail=" ")
        with pytest.raises(UiStateValidationError, match="control characters"):
            UiStateVerification(status=UiVerificationStatus.PASSED, detail="a\r\nb")
        with pytest.raises(UiStateValidationError, match="must not exceed 512"):
            UiStateVerification(status=UiVerificationStatus.PASSED, detail="x" * 513)

    def test_approval_rules(self) -> None:
        with pytest.raises(UiStateValidationError, match="request_id must not be the nil UUID"):
            UiStateApproval(
                request_id=UUID(int=0),
                operation="op",
                permission=UiPermission.READ,
                risk_level=UiRiskLevel.R0,
            )
        with pytest.raises(TypeError, match="permission must be a UiPermission"):
            UiStateApproval(
                request_id=uuid4(),
                operation="op",
                permission="READ",  # type: ignore[arg-type]
                risk_level=UiRiskLevel.R0,
            )
        with pytest.raises(UiStateValidationError, match="operation must be non-empty"):
            UiStateApproval(
                request_id=uuid4(),
                operation="",
                permission=UiPermission.READ,
                risk_level=UiRiskLevel.R0,
            )

    def test_plan_rules(self) -> None:
        with pytest.raises(UiStateValidationError, match="revision must be a positive integer"):
            UiStatePlan(procedure_id=ProcedureId.create(), revision=0)
        with pytest.raises(UiStateValidationError, match="revision must be a positive integer"):
            UiStatePlan(procedure_id=ProcedureId.create(), revision=-1)
        with pytest.raises(TypeError, match="revision must be an integer"):
            UiStatePlan(procedure_id=ProcedureId.create(), revision=True)
        with pytest.raises(TypeError, match="procedure_id must be a ProcedureId"):
            UiStatePlan(procedure_id="procedure-1", revision=1)  # type: ignore[arg-type]

    def test_error_rules(self) -> None:
        with pytest.raises(TypeError, match="category must be an ErrorCategory"):
            UiStateError(
                code="a.b",
                message="m",
                category="validation",  # type: ignore[arg-type]
                retryability=Retryability.UNKNOWN,
            )
        with pytest.raises(UiStateValidationError, match="non-JSON-compatible"):
            UiStateError(
                code="a.b",
                message="m",
                category=ErrorCategory.VALIDATION,
                retryability=Retryability.UNKNOWN,
                details={"callable": len},
            )
        with pytest.raises(UiStateValidationError, match="non-finite float"):
            UiStateError(
                code="a.b",
                message="m",
                category=ErrorCategory.VALIDATION,
                retryability=Retryability.UNKNOWN,
                details={"nan": float("nan")},
            )

    def test_snapshot_is_frozen(self) -> None:
        snapshot = _pending_snapshot()
        with pytest.raises(FrozenInstanceError):
            snapshot.progress = 1.0  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            snapshot.schema_version = 1  # type: ignore[misc]


# --------------------------------------------------------------------------
# Canonical error conversion
# --------------------------------------------------------------------------


class TestErrorConversion:
    def test_from_agentx_error_carries_canonical_fields(self) -> None:
        error = AgentXError(
            code="task.transition_invalid",
            message="task status transition pending -> succeeded is not allowed",
            category=ErrorCategory.CONFLICT,
            retryability=Retryability.NON_RETRYABLE,
            details={"current_status": "pending", "allowed": ["running"]},
        )
        converted = UiStateError.from_agentx_error(error)

        assert converted.code == error.code
        assert converted.message == error.message
        assert converted.category == error.category
        assert converted.retryability == error.retryability
        assert converted.details["current_status"] == "pending"
        assert converted.details["allowed"] == ("running",)

    def test_from_agentx_error_rejects_non_json_details(self) -> None:
        error = AgentXError(
            code="capability.denied",
            message="denied",
            category=ErrorCategory.PERMISSION,
            details={"cause": RuntimeError("internal")},
        )
        with pytest.raises(UiStateValidationError, match="non-JSON-compatible"):
            UiStateError.from_agentx_error(error)

    def test_from_agentx_error_rejects_non_error(self) -> None:
        with pytest.raises(TypeError, match="error must be an AgentXError"):
            UiStateError.from_agentx_error(ValueError("nope"))  # type: ignore[arg-type]

    def test_details_are_defensively_frozen(self) -> None:
        details = {"outer": {"inner": "value"}}
        converted = UiStateError(
            code="a.b",
            message="m",
            category=ErrorCategory.VALIDATION,
            retryability=Retryability.UNKNOWN,
            details=details,
        )
        details["outer"]["inner"] = "mutated"

        assert converted.details["outer"] == {"inner": "value"}


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------


class TestSerialization:
    def test_to_dict_exact_shape(self) -> None:
        task = _make_task()
        snapshot = _running_snapshot(task)
        assert snapshot.plan is not None
        assert snapshot.approval is not None

        assert snapshot.to_dict() == {
            "schema_version": CURRENT_UI_STATE_SCHEMA_VERSION,
            "task": {
                "task_id": task.task_id.to_str(),
                "status": "running",
                "priority": "high",
            },
            "execution": {"level": "L2_COMPILED", "operation": "browser.dom.click"},
            "plan": {"procedure_id": snapshot.plan.procedure_id.to_str(), "revision": 3},
            "verification": {"status": "not_started", "detail": None},
            "approval": {
                "request_id": str(snapshot.approval.request_id),
                "operation": "browser.dom.click",
                "permission": "WRITE",
                "risk_level": "R2",
            },
            "error": {
                "code": "capability.denied",
                "message": "capability request denied",
                "category": "permission",
                "retryability": "non_retryable",
                "details": {"capability": "browser.dom"},
            },
            "progress": 0.5,
            "timestamp": "2026-09-06T12:00:00.000000Z",
        }

    def test_to_json_is_deterministic_canonical_json(self) -> None:
        first = _running_snapshot()

        first_json = first.to_json()
        assert json.loads(first_json) == first.to_dict()
        # Canonical form: sorted keys, compact separators.
        assert first_json == json.dumps(
            first.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        # Re-serializing the same object is byte-identical.
        assert first.to_json() == first_json

    def test_round_trip_json(self) -> None:
        snapshot = _running_snapshot()
        restored = UiStateSnapshot.from_json(snapshot.to_json())

        assert restored == snapshot
        assert restored.to_json() == snapshot.to_json()

    def test_round_trip_with_all_sections_absent(self) -> None:
        snapshot = _pending_snapshot()
        restored = UiStateSnapshot.from_json(snapshot.to_json())

        assert restored == snapshot
        wire = restored.to_dict()
        assert wire["plan"] is None
        assert wire["approval"] is None
        assert wire["error"] is None
        assert wire["progress"] is None
        assert wire["execution"] == {"level": None, "operation": None}

    def test_round_trip_preserves_non_utc_timestamp_instant(self) -> None:
        local = datetime(2026, 9, 6, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        snapshot = _pending_snapshot(timestamp=local)

        restored = UiStateSnapshot.from_json(snapshot.to_json())

        assert restored.timestamp == datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)

    def test_error_details_omitted_when_empty(self) -> None:
        error = UiStateError(
            code="a.b",
            message="m",
            category=ErrorCategory.VALIDATION,
            retryability=Retryability.UNKNOWN,
        )
        assert "details" not in error.to_dict()

        wire = _pending_snapshot(error=error).to_dict()
        assert wire["error"] == {
            "code": "a.b",
            "message": "m",
            "category": "validation",
            "retryability": "unknown",
        }


class TestDeserializationRejections:
    def _wire(self, **top_level_overrides: object) -> dict[str, object]:
        # A fully populated snapshot so every section exists on the wire.
        wire = _wire_of(_running_snapshot())
        wire.update(top_level_overrides)
        return wire

    def test_unknown_top_level_field_is_rejected(self) -> None:
        with pytest.raises(UiStateDeserializationError, match=r"unknown fields.*approved"):
            UiStateSnapshot.from_dict(self._wire(approved=True))

    def test_unknown_section_field_is_rejected(self) -> None:
        wire = self._wire()
        _wire_section(wire, "task")["permission"] = "EXECUTE"
        with pytest.raises(UiStateDeserializationError, match=r"unknown fields.*permission"):
            UiStateSnapshot.from_dict(wire)

    def test_missing_field_is_rejected(self) -> None:
        wire = self._wire()
        del wire["progress"]
        with pytest.raises(UiStateDeserializationError, match=r"missing required fields.*progress"):
            UiStateSnapshot.from_dict(wire)

    @pytest.mark.parametrize(
        "version",
        [0, 2, "1", True, None, 1.0],
    )
    def test_unsupported_or_malformed_schema_version(self, version: object) -> None:
        wire = self._wire(schema_version=version)
        # Integer versions other than the current one fail closed as
        # unsupported versions; non-integer versions are malformed data.
        if version in (0, 2):
            with pytest.raises(UnsupportedUiStateSchemaVersionError):
                UiStateSnapshot.from_dict(wire)
        else:
            with pytest.raises(UiStateDeserializationError):
                UiStateSnapshot.from_dict(wire)

    def test_missing_schema_version_is_rejected(self) -> None:
        wire = self._wire()
        del wire["schema_version"]
        with pytest.raises(
            UiStateDeserializationError, match="missing required field: schema_version"
        ):
            UiStateSnapshot.from_dict(wire)

    def test_malformed_json_text(self) -> None:
        with pytest.raises(UiStateDeserializationError, match="malformed"):
            UiStateSnapshot.from_json("{not json")
        with pytest.raises(UiStateDeserializationError, match="root must be an object"):
            UiStateSnapshot.from_json("[1, 2, 3]")
        with pytest.raises(UiStateDeserializationError, match="root must be an object"):
            UiStateSnapshot.from_json("null")
        with pytest.raises(TypeError, match="ui state JSON must be a string"):
            UiStateSnapshot.from_json(object())  # type: ignore[arg-type]

    def test_nan_progress_json_literal_is_rejected(self) -> None:
        text = json.dumps(_wire_of(_pending_snapshot()), separators=(",", ":"))
        text = text.replace('"progress":null', '"progress":NaN')
        with pytest.raises(UiStateDeserializationError, match="progress must be finite"):
            UiStateSnapshot.from_json(text)

    def test_out_of_range_progress_is_rejected(self) -> None:
        with pytest.raises(UiStateDeserializationError, match="progress"):
            UiStateSnapshot.from_dict(self._wire(progress=1.5))
        with pytest.raises(UiStateDeserializationError, match="progress"):
            UiStateSnapshot.from_dict(self._wire(progress=-0.2))
        with pytest.raises(UiStateDeserializationError, match="progress must be a number or null"):
            UiStateSnapshot.from_dict(self._wire(progress=True))
        with pytest.raises(UiStateDeserializationError, match="progress must be a number or null"):
            UiStateSnapshot.from_dict(self._wire(progress="half"))

    @pytest.mark.parametrize(
        ("section", "field", "bad_value"),
        [
            ("task", "status", "approved"),
            ("task", "status", None),
            ("task", "priority", "urgent"),
            ("task", "task_id", "not-a-uuid"),
            ("task", "task_id", "00000000-0000-0000-0000-000000000000"),
            ("execution", "level", "L9_QUANTUM"),
            ("execution", "operation", 42),
            ("verification", "status", "verified=true"),
            ("approval", "request_id", "not-a-uuid"),
            ("approval", "permission", "SUPERUSER"),
            ("approval", "risk_level", "R9"),
            ("plan", "revision", True),
            ("plan", "revision", 0),
            ("plan", "procedure_id", "not-a-uuid"),
        ],
    )
    def test_malformed_section_field_values(
        self, section: str, field: str, bad_value: object
    ) -> None:
        wire = self._wire()
        _wire_section(wire, section)[field] = bad_value
        with pytest.raises(UiStateDeserializationError):
            UiStateSnapshot.from_dict(wire)

    @pytest.mark.parametrize(
        ("bad_timestamp",),
        [
            ("2026-09-06T12:00:00",),  # naive
            ("yesterday",),
            (1234567890,),
            (None,),
        ],
    )
    def test_malformed_timestamp_values(self, bad_timestamp: object) -> None:
        with pytest.raises(UiStateDeserializationError):
            UiStateSnapshot.from_dict(self._wire(timestamp=bad_timestamp))

    def test_section_must_be_object_or_null(self) -> None:
        with pytest.raises(UiStateDeserializationError, match="plan must be a JSON object or null"):
            UiStateSnapshot.from_dict(self._wire(plan=["procedure-1"]))
        with pytest.raises(
            UiStateDeserializationError, match="approval must be a JSON object or null"
        ):
            UiStateSnapshot.from_dict(self._wire(approval="APPROVED"))
        with pytest.raises(
            UiStateDeserializationError, match="error must be a JSON object or null"
        ):
            UiStateSnapshot.from_dict(self._wire(error=["a.b"]))
        # A well-formed object with fields missing fails inside the section.
        with pytest.raises(UiStateDeserializationError, match="error missing required fields"):
            UiStateSnapshot.from_dict(self._wire(error={"code": "a.b"}))


# --------------------------------------------------------------------------
# Event conversion (projection)
# --------------------------------------------------------------------------


class TestProjection:
    def test_task_started_projects_running(self) -> None:
        task = _make_task()
        snapshot = UiStateSnapshot(
            schema_version=CURRENT_UI_STATE_SCHEMA_VERSION,
            task=UiStateTask(
                task_id=task.task_id, status=TaskStatus.PENDING, priority=TaskPriority.NORMAL
            ),
            execution=UiStateExecution(level=None, operation=None),
            verification=UiStateVerification(status=UiVerificationStatus.NOT_STARTED, detail=None),
            timestamp=T0,
        )
        event = _event_for(EventType.TASK_STARTED, task, timestamp=T1)

        projected = project_state_event(snapshot, event)

        assert projected is not snapshot
        assert projected.task.status == TaskStatus.RUNNING
        assert projected.timestamp == T1
        assert projected.task.task_id == task.task_id
        assert projected.task.priority == snapshot.task.priority
        assert projected.execution == snapshot.execution
        assert projected.verification == snapshot.verification
        assert projected.progress == snapshot.progress

    def test_task_completed_projects_succeeded(self) -> None:
        task = _make_task()
        snapshot = _running_snapshot(task)
        event = _event_for(EventType.TASK_COMPLETED, task, timestamp=T1)

        projected = project_state_event(snapshot, event)

        assert projected.task.status == TaskStatus.SUCCEEDED
        assert projected.is_terminal

    def test_task_failed_projects_failed(self) -> None:
        task = _make_task()
        snapshot = _running_snapshot(task)
        event = _event_for(EventType.TASK_FAILED, task, timestamp=T1)

        projected = project_state_event(snapshot, event)

        assert projected.task.status == TaskStatus.FAILED
        assert projected.is_terminal

    def test_illegal_transition_is_rejected(self) -> None:
        task = _make_task()
        snapshot = UiStateSnapshot(
            schema_version=CURRENT_UI_STATE_SCHEMA_VERSION,
            task=UiStateTask(
                task_id=task.task_id, status=TaskStatus.PENDING, priority=TaskPriority.NORMAL
            ),
            execution=UiStateExecution(level=None, operation=None),
            verification=UiStateVerification(status=UiVerificationStatus.NOT_STARTED, detail=None),
            timestamp=T0,
        )
        event = _event_for(EventType.TASK_COMPLETED, task, timestamp=T1)

        with pytest.raises(UiStateProjectionError, match="not a legal transition"):
            project_state_event(snapshot, event)

    def test_terminal_status_rejects_lifecycle_events(self) -> None:
        task = _make_task()
        snapshot = UiStateSnapshot(
            schema_version=CURRENT_UI_STATE_SCHEMA_VERSION,
            task=UiStateTask(
                task_id=task.task_id, status=TaskStatus.SUCCEEDED, priority=TaskPriority.NORMAL
            ),
            execution=UiStateExecution(level=None, operation=None),
            verification=UiStateVerification(status=UiVerificationStatus.PASSED, detail=None),
            timestamp=T0,
        )
        for event_type in (EventType.TASK_STARTED, EventType.TASK_FAILED):
            event = _event_for(event_type, task, timestamp=T1)
            with pytest.raises(UiStateProjectionError, match="not a legal transition"):
                project_state_event(snapshot, event)

    def test_event_bound_to_different_task_is_rejected(self) -> None:
        task = _make_task()
        other = _make_task()
        snapshot = _running_snapshot(task)
        event = _event_for(EventType.TASK_COMPLETED, other, timestamp=T1)

        with pytest.raises(UiStateProjectionError, match="different task"):
            project_state_event(snapshot, event)

    def test_event_without_task_id_is_rejected(self) -> None:
        task = _make_task()
        snapshot = _running_snapshot(task)
        event = Event.create(event_type=EventType.TASK_STARTED, source="agentx.test", timestamp=T1)

        with pytest.raises(UiStateProjectionError, match="no task_id"):
            project_state_event(snapshot, event)

    def test_event_with_malformed_task_id_is_rejected(self) -> None:
        task = _make_task()
        snapshot = _running_snapshot(task)
        event = Event.create(
            event_type=EventType.TASK_STARTED,
            source="agentx.test",
            task_id="not-a-uuid",
            timestamp=T1,
        )

        with pytest.raises(UiStateProjectionError, match="not a valid TaskId"):
            project_state_event(snapshot, event)

    def test_task_created_cannot_be_projected_onto_existing_snapshot(self) -> None:
        task = _make_task()
        snapshot = _running_snapshot(task)
        event = _event_for(EventType.TASK_CREATED, task, timestamp=T1)

        with pytest.raises(UiStateProjectionError, match=r"task.created cannot be projected"):
            project_state_event(snapshot, event)

    def test_verification_completed_updates_verification_while_running(self) -> None:
        task = _make_task()
        snapshot = _running_snapshot(task)
        event = _event_for(
            EventType.VERIFICATION_COMPLETED,
            task,
            timestamp=T1,
            payload=VerificationPayload(passed=True, detail="all expectations matched"),
        )

        projected = project_state_event(snapshot, event)

        assert projected.verification.status == UiVerificationStatus.PASSED
        assert projected.verification.detail == "all expectations matched"
        assert projected.timestamp == T1
        # The task section is carried through untouched.
        assert projected.task == snapshot.task

    def test_verification_completed_failure_carries_failed_status(self) -> None:
        task = _make_task()
        snapshot = _running_snapshot(task)
        event = _event_for(
            EventType.VERIFICATION_COMPLETED,
            task,
            timestamp=T1,
            payload=VerificationPayload(passed=False, detail="observation mismatch"),
        )

        projected = project_state_event(snapshot, event)

        assert projected.verification.status == UiVerificationStatus.FAILED
        assert projected.verification.detail == "observation mismatch"

    def test_verification_completed_rejected_when_not_running(self) -> None:
        task = _make_task()
        for status in (TaskStatus.PENDING, TaskStatus.SUCCEEDED, TaskStatus.FAILED):
            snapshot = UiStateSnapshot(
                schema_version=CURRENT_UI_STATE_SCHEMA_VERSION,
                task=UiStateTask(task_id=task.task_id, status=status, priority=TaskPriority.NORMAL),
                execution=UiStateExecution(level=None, operation=None),
                verification=UiStateVerification(
                    status=UiVerificationStatus.NOT_STARTED, detail=None
                ),
                timestamp=T0,
            )
            event = _event_for(
                EventType.VERIFICATION_COMPLETED,
                task,
                timestamp=T1,
                payload=VerificationPayload(passed=True),
            )
            with pytest.raises(
                UiStateProjectionError, match="only be projected while the task is running"
            ):
                project_state_event(snapshot, event)

    @pytest.mark.parametrize(
        "event_type",
        [
            EventType.SYSTEM_STARTED,
            EventType.GOAL_RECEIVED,
            EventType.ROUTING_DECISION,
            EventType.CAPABILITY_SELECTED,
            EventType.OBSERVATION_RECORDED,
            EventType.ACTION_REQUESTED,
        ],
    )
    def test_unrelated_events_are_explicit_no_ops(self, event_type: EventType) -> None:
        task = _make_task()
        snapshot = _running_snapshot(task)
        event = _event_for(event_type, task, timestamp=T1)

        projected = project_state_event(snapshot, event)

        assert projected is snapshot

    def test_projection_is_deterministic(self) -> None:
        task = _make_task()
        event = _event_for(EventType.TASK_STARTED, task, timestamp=T1)

        first = project_state_event(UiStateSnapshot.from_task(task, timestamp=T0), event)
        second = project_state_event(UiStateSnapshot.from_task(task, timestamp=T0), event)

        assert first == second
        assert first.to_json() == second.to_json()
        assert first.task.status == TaskStatus.RUNNING

    def test_projection_does_not_mutate_inputs(self) -> None:
        task = _make_task()
        snapshot = _running_snapshot(task)
        before = snapshot.to_json()
        event = _event_for(EventType.TASK_COMPLETED, task, timestamp=T1)

        project_state_event(snapshot, event)

        assert snapshot.to_json() == before
        assert snapshot.task.status == TaskStatus.RUNNING

    def test_projection_requires_canonical_types(self) -> None:
        task = _make_task()
        snapshot = _running_snapshot(task)
        with pytest.raises(TypeError, match="event must be an Event"):
            project_state_event(snapshot, object())  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="snapshot must be a UiStateSnapshot"):
            project_state_event(
                object(),  # type: ignore[arg-type]
                _event_for(EventType.TASK_COMPLETED, task, timestamp=T1),
            )

    def test_full_lifecycle_projection_sequence(self) -> None:
        task = _make_task()
        t_start = datetime(2026, 9, 6, 12, 0, 5, tzinfo=UTC)
        t_verify = datetime(2026, 9, 6, 12, 0, 20, tzinfo=UTC)
        t_done = datetime(2026, 9, 6, 12, 0, 30, tzinfo=UTC)

        snapshot = UiStateSnapshot.from_task(task, timestamp=T0)
        running = project_state_event(
            snapshot, _event_for(EventType.TASK_STARTED, task, timestamp=t_start)
        )
        verified = project_state_event(
            running,
            _event_for(
                EventType.VERIFICATION_COMPLETED,
                task,
                timestamp=t_verify,
                payload=VerificationPayload(passed=True, detail="ok"),
            ),
        )
        done = project_state_event(
            verified, _event_for(EventType.TASK_COMPLETED, task, timestamp=t_done)
        )

        assert running.task.status == TaskStatus.RUNNING
        assert verified.verification.status == UiVerificationStatus.PASSED
        assert done.task.status == TaskStatus.SUCCEEDED
        assert done.verification.status == UiVerificationStatus.PASSED
        assert done.timestamp == t_done
