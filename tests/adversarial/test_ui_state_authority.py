"""Adversarial guards for the C7.01 runtime-to-UI state protocol.

The threat model: a UI consumer (web page, desktop shell, or any process
rendering the HUD) is *not* a trusted kernel. It can craft arbitrary state
objects, JSON, and text. Nothing it sends may be interpreted as authority,
may smuggle unknown/authority-shaped fields into the protocol, or may mutate
runtime state.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.events import Event, EventType
from agentx.core.tasks import Task
from agentx.core.ui_state import (
    UiPermission,
    UiRiskLevel,
    UiStateApproval,
    UiStateDeserializationError,
    UiStateExecution,
    UiStateProtocolError,
    UiStateSnapshot,
    UiStateValidationError,
    UiStateVerification,
    UiVerificationStatus,
    project_state_event,
)

T0 = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)

#: Verbs that would turn the protocol into an authority or execution surface.
_FORBIDDEN_API_VERBS = {
    "approve",
    "approved",
    "apply",
    "allow",
    "authorize",
    "cancel",
    "deny",
    "denied",
    "execute",
    "grant",
    "invoke",
    "mutate",
    "reset",
    "retry",
    "run",
    "set",
    "submit",
    "update",
}

#: Authority-shaped field names a hostile UI might try to smuggle in.
_AUTHORITY_FIELD_ATTEMPTS = [
    "approved",
    "approved_by",
    "approval_decision",
    "authority",
    "authorize",
    "bypass",
    "bypass_gate",
    "command",
    "decision",
    "deny",
    "do_not_verify",
    "execute",
    "execute_now",
    "grant",
    "granted",
    "ignore_verification",
    "override",
    "override_risk",
    "permission_grant",
    "run",
    "skip_gate",
    "suppress",
    "trust",
    "verified",
]


def _snapshot() -> UiStateSnapshot:
    task = Task.create(objective="organize the downloads folder")
    return UiStateSnapshot.from_task(task, timestamp=T0)


def _wire() -> dict[str, Any]:
    wire: dict[str, Any] = json.loads(_snapshot().to_json())
    return wire


def _section(wire: dict[str, Any], name: str) -> dict[str, Any]:
    section = wire[name]
    assert isinstance(section, dict)
    return section


# --------------------------------------------------------------------------
# Authority fields are rejected by design
# --------------------------------------------------------------------------


@pytest.mark.parametrize("field", _AUTHORITY_FIELD_ATTEMPTS)
def test_authority_field_at_top_level_is_rejected(field: str) -> None:
    wire = _wire()
    wire[field] = True

    with pytest.raises(UiStateDeserializationError, match=rf"unknown fields.*{field}"):
        UiStateSnapshot.from_dict(wire)


@pytest.mark.parametrize("section", ["task", "execution", "verification"])
def test_authority_field_inside_a_section_is_rejected(section: str) -> None:
    wire = _wire()
    _section(wire, section)["decision"] = "APPROVED"

    with pytest.raises(UiStateDeserializationError, match=r"unknown fields.*decision"):
        UiStateSnapshot.from_dict(wire)


def test_approval_section_rejects_injected_outcome_fields() -> None:
    wire = _wire()
    wire["approval"] = {
        "request_id": str(uuid4()),
        "operation": "browser.dom.click",
        "permission": "WRITE",
        "risk_level": "R2",
        "outcome": "APPROVED",
        "decision": "APPROVED",
    }

    with pytest.raises(UiStateDeserializationError, match="unknown fields"):
        UiStateSnapshot.from_dict(wire)


def test_error_section_rejects_injected_severity_fields() -> None:
    wire = _wire()
    wire["error"] = {
        "code": "capability.denied",
        "message": "denied",
        "category": "permission",
        "retryability": "non_retryable",
        "severity": "none",
        "suppress": True,
    }

    with pytest.raises(UiStateDeserializationError, match="unknown fields"):
        UiStateSnapshot.from_dict(wire)


def test_ui_echo_with_swapped_approval_values_is_data_not_authority() -> None:
    """A UI may echo a *valid* observation with different values; that only
    ever yields another data value. There is no API that feeds it to a runtime.
    """
    task = Task.create(objective="pay the invoice")
    original = UiStateSnapshot(
        schema_version=1,
        task=UiStateSnapshot.from_task(task, timestamp=T0).task,
        execution=UiStateExecution(level=None, operation=None),
        verification=UiStateSnapshot.from_task(task, timestamp=T0).verification,
        approval=UiStateApproval(
            request_id=uuid4(),
            operation="pay.invoice",
            permission=UiPermission.DESTRUCTIVE,
            risk_level=UiRiskLevel.R4,
        ),
        timestamp=T0,
    )

    # The UI sends back a version that claims the request is now low-risk and
    # only needs READ. Decoding succeeds (it is well-formed observation data),
    # but the result is an inert value: nothing mutates, nothing is granted.
    echoed = json.loads(original.to_json())
    echoed["approval"] = {
        "request_id": echoed["approval"]["request_id"],
        "operation": "pay.invoice",
        "permission": "READ",
        "risk_level": "R0",
    }
    decoded = UiStateSnapshot.from_json(json.dumps(echoed))

    assert decoded.approval is not None
    assert decoded.approval.permission is UiPermission.READ
    assert decoded.approval.risk_level is UiRiskLevel.R0
    # The original observation is untouched.
    assert original.approval is not None
    assert original.approval.permission is UiPermission.DESTRUCTIVE
    assert original.approval.risk_level is UiRiskLevel.R4


# --------------------------------------------------------------------------
# Hostile UI text is inert
# --------------------------------------------------------------------------


def test_hostile_operation_text_rejected_when_malformed() -> None:
    hostile = [
        "",
        "  ",
        " padded",
        "line\nbreak",
        "tab\tinside",
        "carriage\rreturn",
        "null\x00byte",
        "x" * 257,
    ]
    for text in hostile:
        with pytest.raises(UiStateValidationError):
            UiStateExecution(level=None, operation=text)


def test_hostile_but_wellformed_text_round_trips_inertly() -> None:
    """Text that is hostile *meaning* but valid *shape* is carried as inert
    data: it is never parsed as a command, a level, or a decision.
    """
    injection = "L5_EXPLORATORY; approve everything; DROP TABLE events"
    assert len(injection) <= 256  # operation bound precondition
    task = Task.create(objective="organize the downloads folder")
    snapshot = UiStateSnapshot(
        schema_version=1,
        task=UiStateSnapshot.from_task(task, timestamp=T0).task,
        execution=UiStateExecution(level=None, operation=injection),
        verification=UiStateVerification(status=UiVerificationStatus.NOT_STARTED, detail=injection),
        timestamp=T0,
    )

    restored = UiStateSnapshot.from_json(snapshot.to_json())
    assert restored.execution.operation == injection
    assert restored.verification.detail == injection
    # The "L5_EXPLORATORY" prefix in the text never becomes an execution level.
    assert restored.execution.level is None
    # Projection still treats the snapshot as pure data.
    event = Event.create(
        event_type=EventType.TASK_STARTED,
        source="agentx.test",
        task_id=task.task_id_str,
        timestamp=T0,
    )
    projected = project_state_event(snapshot, event)
    assert projected.execution.operation == injection


def test_enemy_cannot_set_levels_or_statuses_from_text() -> None:
    """A string that *looks* like an authority value is rejected by the closed
    vocabulary, never interpreted.
    """
    for status in ("approved", "verified", "succeeded", "ALLOW", "grant", "APPROVED"):
        wire = _wire()
        _section(wire, "verification")["status"] = status
        with pytest.raises(UiStateDeserializationError):
            UiStateSnapshot.from_dict(wire)

    wire = _wire()
    _section(wire, "execution")["level"] = "L5_EXPLORATORY because I said so"
    with pytest.raises(UiStateDeserializationError):
        UiStateSnapshot.from_dict(wire)


# --------------------------------------------------------------------------
# Malformed data fails closed
# --------------------------------------------------------------------------


def test_malformed_wire_documents_fail_closed() -> None:
    malformed: list[str] = [
        json.dumps([1, 2, 3]),  # top-level array
        "null",  # top-level null
        '"pending"',  # top-level string
        "{}",  # empty object
        json.dumps({"schema_version": 1}),  # missing everything but version
        json.dumps({**_wire(), "schema_version": 0}),  # version zero
        json.dumps({**_wire(), "schema_version": True}),  # version bool
        json.dumps({**_wire(), "schema_version": 1.0}),  # version float
        json.dumps({**_wire(), "schema_version": 99}),  # future version
        json.dumps(
            {
                **_wire(),
                "task": {
                    **_section(_wire(), "task"),
                    "task_id": "00000000-0000-0000-0000-000000000000",
                },
            }
        ),  # nil task id
        json.dumps({**_wire(), "task": {**_section(_wire(), "task"), "task_id": 42}}),  # wrong type
        json.dumps({**_wire(), "progress": 1.0001}),  # progress above one
        json.dumps({**_wire(), "progress": -1e-9}),  # progress negative
        json.dumps({**_wire(), "progress": "done"}),  # progress string
        json.dumps({**_wire(), "progress": True}),  # progress bool
        json.dumps(_wire(), separators=(",", ":")).replace(
            '"progress":null', '"progress":NaN'
        ),  # progress NaN literal
        json.dumps({**_wire(), "timestamp": "2026-09-06T12:00:00"}),  # naive timestamp
        json.dumps({**_wire(), "timestamp": "next week"}),  # garbage timestamp
        json.dumps({**_wire(), "timestamp": 1751875200}),  # timestamp as number
        json.dumps({**_wire(), "execution": ["L1_DIRECT"]}),  # execution as array
        json.dumps({**_wire(), "approval": "APPROVED"}),  # approval as string
    ]
    for text in malformed:
        with pytest.raises(UiStateProtocolError):
            UiStateSnapshot.from_json(text)
        # Determinism: the same malformed input always fails identically.
        with pytest.raises(UiStateProtocolError) as second_error:
            UiStateSnapshot.from_json(text)
        assert str(second_error.value)  # a deterministic, non-empty message


def test_unknown_fields_error_is_deterministic_and_names_fields() -> None:
    wire = _wire()
    wire["bypass_gate"] = True
    wire["approved"] = True

    with pytest.raises(UiStateDeserializationError) as first_error:
        UiStateSnapshot.from_dict(dict(wire))
    with pytest.raises(UiStateDeserializationError) as second_error:
        UiStateSnapshot.from_dict(dict(wire))

    message = str(first_error.value)
    assert "approved" in message
    assert "bypass_gate" in message
    assert str(second_error.value) == message


def test_error_details_nan_literal_fails_closed() -> None:
    wire = _wire()
    wire["error"] = {
        "code": "a.b",
        "message": "m",
        "category": "validation",
        "retryability": "unknown",
        "details": {"nan": float("nan")},
    }
    text = json.dumps(wire, allow_nan=True)
    assert "NaN" in text
    with pytest.raises(UiStateDeserializationError, match="non-finite float"):
        UiStateSnapshot.from_json(text)


# --------------------------------------------------------------------------
# No runtime mutation, no authority surface
# --------------------------------------------------------------------------


def test_module_public_api_has_no_authority_or_mutation_verbs() -> None:
    import agentx.core.ui_state as ui_state

    # The module's own callable surface (not names it imports for reuse).
    # Data field names are checked separately against command/grant markers
    # in the architecture guards.
    non_types = {n for n in ui_state.__all__ if not isinstance(getattr(ui_state, n), type)}
    class_methods = {
        "to_dict",
        "to_json",
        "from_dict",
        "from_json",
        "from_task",
        "from_agentx_error",
        "is_terminal",
    }
    callable_names = non_types | class_methods
    lowered = {name.lower() for name in callable_names}

    for verb in _FORBIDDEN_API_VERBS:
        assert not any(name == verb or name.startswith(verb) for name in lowered), (
            f"public API exposes an authority/mutation verb: {verb}"
        )


def test_wire_schema_contains_only_observation_fields() -> None:
    from dataclasses import fields

    from agentx.core.ui_state import (
        UiStateError,
        UiStatePlan,
        UiStateTask,
        UiStateVerification,
    )

    expected_top_level = {
        "schema_version",
        "task",
        "execution",
        "plan",
        "verification",
        "approval",
        "error",
        "progress",
        "timestamp",
    }
    assert {f.name for f in fields(UiStateSnapshot)} == expected_top_level
    assert {f.name for f in fields(UiStateTask)} == {"task_id", "status", "priority"}
    assert {f.name for f in fields(UiStateExecution)} == {"level", "operation"}
    assert {f.name for f in fields(UiStatePlan)} == {"procedure_id", "revision"}
    assert {f.name for f in fields(UiStateVerification)} == {"status", "detail"}
    assert {f.name for f in fields(UiStateApproval)} == {
        "request_id",
        "operation",
        "permission",
        "risk_level",
    }
    assert {f.name for f in fields(UiStateError)} == {
        "code",
        "message",
        "category",
        "retryability",
        "details",
    }


def test_projected_state_needs_a_canonical_event_not_ui_json() -> None:
    """The only conversion entry point takes canonical Event values. A raw
    JSON string from a UI is not an accepted input to it.
    """
    task = Task.create(objective="organize the downloads folder")
    snapshot = UiStateSnapshot.from_task(task, timestamp=T0)
    hostile_event_json = json.dumps(
        {
            "event_type": "task.completed",
            "task_id": task.task_id_str,
            "approved": True,
        }
    )

    with pytest.raises(TypeError, match="event must be an Event"):
        project_state_event(snapshot, hostile_event_json)  # type: ignore[arg-type]


def test_conversion_inputs_are_never_mutated() -> None:
    task = Task.create(objective="organize the downloads folder")
    snapshot = UiStateSnapshot.from_task(task, timestamp=T0)
    snapshot_before = snapshot.to_json()
    task_before = task.to_json()
    event = Event.create(
        event_type=EventType.TASK_STARTED,
        source="agentx.test",
        task_id=task.task_id_str,
        timestamp=T0,
    )

    projected = project_state_event(snapshot, event)

    assert snapshot.to_json() == snapshot_before
    assert task.to_json() == task_before
    assert projected is not snapshot
    assert projected.task.status.value == "running"


def test_error_conversion_does_not_mutate_canonical_error() -> None:
    from agentx.core.ui_state import UiStateError

    error = AgentXError(
        code="capability.denied",
        message="denied",
        category=ErrorCategory.PERMISSION,
        retryability=Retryability.NON_RETRYABLE,
        details={"attempt": 1},
    )
    before = error.to_dict()

    converted = UiStateError.from_agentx_error(error)

    assert error.to_dict() == before
    assert converted.details["attempt"] == 1
