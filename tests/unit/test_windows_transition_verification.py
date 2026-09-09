"""Unit tests for the N2.25 Windows state-transition verification boundary.

Every test constructs canonical baseline observations directly (A5.02 process
snapshots, A5.03 UIA tree snapshots) and asserts what the verifier does with
them. Nothing here touches a real Windows host: the boundary is pure.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from agentx.capabilities.abi import (
    CapabilityIdentity,
    CapabilityName,
    CapabilityObservation,
    CapabilityVersion,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.windows.process_discovery import (
    MetadataStatus,
    WindowsProcessIdentity,
    WindowsProcessSnapshot,
    WindowsWindowIdentity,
)
from agentx.capabilities.windows.uia_tree import (
    UIAElementReference,
    UIAElementSnapshot,
    UIAElementState,
    UIAFreshness,
    UIAObservationStatus,
    UIAPatternName,
    UIAPatternObservation,
    UIAPropertyName,
    UIAPropertyObservation,
    UIATreeLimits,
    UIATreeSnapshot,
)
from agentx.core.tasks import JsonValue
from agentx.windows_transition_verification import (
    REQUIRED_OBSERVATION_CONTRACT,
    SUPPORTED_TRANSITION_KINDS,
    UNVERIFIABLE_TRANSITION_KINDS,
    NativeExecutionEvidence,
    WindowsElementTarget,
    WindowsObservationContract,
    WindowsObservationEvidence,
    WindowsProcessTarget,
    WindowsRequestedState,
    WindowsSessionTarget,
    WindowsTransitionKind,
    WindowsTransitionRequest,
    WindowsTransitionRequestError,
    WindowsTransitionVerdict,
    WindowsTransitionVerification,
    WindowsTransitionVerifier,
    WindowsVerificationReason,
    WindowsWindowTarget,
)

_HANDLE = 4101
_OTHER_HANDLE = 9902
_PID = 4242
_OTHER_PID = 777
_ENVIRONMENT = "desk-01"
_PRE_AT = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
_POST_AT = datetime(2026, 9, 8, 10, 0, 5, tzinfo=UTC)

_OPERATION = CapabilityIdentity(
    name=CapabilityName("windows.window.set-state"),
    version=CapabilityVersion(major=1, minor=0, patch=0),
)


# ---------------------------------------------------------------------------
# Canonical observation builders (A5.02 / A5.03 contracts, verbatim).
# ---------------------------------------------------------------------------


def _window(
    handle: int = _HANDLE,
    *,
    process_id: int = _PID,
    title: str = "Untitled - Notepad",
    is_visible: bool = True,
) -> WindowsWindowIdentity:
    return WindowsWindowIdentity(
        handle=handle,
        process_id=process_id,
        title=title,
        title_status=MetadataStatus.AVAILABLE,
        class_name="Notepad",
        class_name_status=MetadataStatus.AVAILABLE,
        is_visible=is_visible,
    )


def _process(
    process_id: int = _PID,
    *,
    executable_name: str | None = "notepad.exe",
    executable_name_status: MetadataStatus = MetadataStatus.AVAILABLE,
    window_handles: tuple[int, ...] = (_HANDLE,),
    visible_window_count: int = 1,
) -> WindowsProcessIdentity:
    return WindowsProcessIdentity(
        process_id=process_id,
        parent_process_id=100,
        executable_name=executable_name,
        executable_name_status=executable_name_status,
        executable_path=None,
        executable_path_status=MetadataStatus.ACCESS_DENIED,
        window_handles=window_handles,
        visible_window_count=visible_window_count,
    )


def _process_snapshot(
    *,
    windows: tuple[WindowsWindowIdentity, ...] = (),
    processes: tuple[WindowsProcessIdentity, ...] = (),
    dropped_invalid_entries: int = 0,
) -> WindowsProcessSnapshot:
    return WindowsProcessSnapshot(
        processes=processes,
        windows=windows,
        dropped_invalid_entries=dropped_invalid_entries,
        merged_duplicate_entries=0,
    )


def _property(name: UIAPropertyName, value: JsonValue | None) -> UIAPropertyObservation:
    if value is None:
        return UIAPropertyObservation(
            name=name, status=UIAObservationStatus.UNAVAILABLE, value=None
        )
    return UIAPropertyObservation(name=name, status=UIAObservationStatus.AVAILABLE, value=value)


def _element(
    *,
    path: tuple[int, ...] = (),
    root_window_handle: int = _HANDLE,
    runtime_id: tuple[int, ...] | None = (42, 1),
    process_id: int | None = _PID,
    name: str | None = "Editor",
    value: str | None = None,
    focus: bool | None = False,
    bounds: tuple[float, float, float, float] | None = (0.0, 0.0, 800.0, 600.0),
    state: UIAElementState = UIAElementState.AVAILABLE,
    parent_path: tuple[int, ...] | None = None,
    child_paths: tuple[tuple[int, ...], ...] = (),
) -> UIAElementSnapshot:
    values: dict[UIAPropertyName, JsonValue | None] = {
        UIAPropertyName.RUNTIME_ID: list(runtime_id) if runtime_id is not None else None,
        UIAPropertyName.BOUNDING_RECTANGLE: list(bounds) if bounds is not None else None,
        UIAPropertyName.PROCESS_ID: process_id,
        UIAPropertyName.CONTROL_TYPE: 50000,
        UIAPropertyName.NAME: name,
        UIAPropertyName.HAS_KEYBOARD_FOCUS: focus,
        UIAPropertyName.IS_KEYBOARD_FOCUSABLE: True,
        UIAPropertyName.IS_ENABLED: True,
        UIAPropertyName.AUTOMATION_ID: "edit-1",
        UIAPropertyName.NATIVE_WINDOW_HANDLE: root_window_handle,
        UIAPropertyName.IS_OFFSCREEN: False,
        UIAPropertyName.VALUE: value,
    }
    return UIAElementSnapshot(
        reference=UIAElementReference(
            root_window_handle=root_window_handle,
            path=path,
            runtime_id=runtime_id,
        ),
        parent_path=parent_path,
        child_paths=child_paths,
        state=state,
        properties=tuple(_property(item, values[item]) for item in UIAPropertyName),
        patterns=tuple(
            UIAPatternObservation(
                name=pattern, status=UIAObservationStatus.UNAVAILABLE, supported=None
            )
            for pattern in UIAPatternName
        ),
    )


def _uia_snapshot(
    *elements: UIAElementSnapshot,
    root_window_handle: int = _HANDLE,
    captured_at: datetime = _POST_AT,
    truncated_by_depth: bool = False,
    truncated_by_nodes: bool = False,
) -> UIATreeSnapshot:
    return UIATreeSnapshot(
        root_window_handle=root_window_handle,
        captured_at=captured_at,
        freshness=UIAFreshness.POINT_IN_TIME,
        limits=UIATreeLimits(max_depth=4, max_nodes=32),
        elements=elements if elements else (_element(root_window_handle=root_window_handle),),
        errors=(),
        truncated_by_depth=truncated_by_depth,
        truncated_by_nodes=truncated_by_nodes,
    )


def _evidence(
    snapshot: WindowsProcessSnapshot | UIATreeSnapshot,
    *,
    observation_id: str,
    environment_id: str = _ENVIRONMENT,
) -> WindowsObservationEvidence:
    return WindowsObservationEvidence(
        snapshot=snapshot,
        observation_id=observation_id,
        environment_id=environment_id,
    )


def _native(*, succeeded: bool = True, attempt_id: str = "attempt-1") -> NativeExecutionEvidence:
    return NativeExecutionEvidence(
        attempt_id=attempt_id,
        result=ExecutionResult(
            succeeded=succeeded,
            message="ShowWindow returned TRUE",
            observation=CapabilityObservation(summary="native call returned", data={"win32": 1}),
        ),
    )


def _request(
    kind: WindowsTransitionKind,
    target: object,
    *,
    requested_state: WindowsRequestedState | None = None,
    pre: WindowsObservationEvidence | None = None,
    post: WindowsObservationEvidence | None = None,
    native: NativeExecutionEvidence | None = None,
    environment_id: str = _ENVIRONMENT,
    attempt_id: str = "attempt-1",
) -> WindowsTransitionRequest:
    return WindowsTransitionRequest(
        operation=_OPERATION,
        attempt_id=attempt_id,
        environment_id=environment_id,
        kind=kind,
        target=target,  # type: ignore[arg-type]
        requested_state=requested_state if requested_state is not None else WindowsRequestedState(),
        pre_state=pre,
        post_state=post,
        native_evidence=native,
    )


def _verify(request: WindowsTransitionRequest) -> WindowsTransitionVerification:
    return WindowsTransitionVerifier().evaluate(request)


# ---------------------------------------------------------------------------
# Valid window-state verification.
# ---------------------------------------------------------------------------


def test_window_visible_transition_is_verified_from_post_state_observation() -> None:
    hidden = _process_snapshot(windows=(_window(is_visible=False),), processes=(_process(),))
    shown = _process_snapshot(windows=(_window(is_visible=True),), processes=(_process(),))

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(hidden, observation_id="snap-1"),
            post=_evidence(shown, observation_id="snap-2"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.VERIFIED
    assert result.verified is True
    assert result.reasons == (WindowsVerificationReason.OBSERVED_STATE_MATCHES_REQUEST,)
    assert result.observation_contract is WindowsObservationContract.PROCESS_SNAPSHOT
    assert result.pre_state_observation_id == "snap-1"
    assert result.post_state_observation_id == "snap-2"
    assert result.pre_state_evaluation is WindowsTransitionVerdict.NOT_VERIFIED
    assert result.native_reported_success is None


def test_window_present_and_absent_are_decided_by_the_enumeration() -> None:
    populated = _process_snapshot(windows=(_window(),), processes=(_process(),))
    empty = _process_snapshot(
        processes=(_process(window_handles=(), visible_window_count=0),),
    )

    closed = _verify(
        _request(
            WindowsTransitionKind.WINDOW_ABSENT,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(populated, observation_id="snap-1"),
            post=_evidence(empty, observation_id="snap-2"),
        )
    )
    still_open = _verify(
        _request(
            WindowsTransitionKind.WINDOW_ABSENT,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(populated, observation_id="snap-1"),
            post=_evidence(populated, observation_id="snap-2"),
        )
    )

    assert closed.verdict is WindowsTransitionVerdict.VERIFIED
    assert still_open.verdict is WindowsTransitionVerdict.NOT_VERIFIED
    assert still_open.reasons == (WindowsVerificationReason.OBSERVED_STATE_DIFFERS_FROM_REQUEST,)


def test_window_hidden_request_is_not_verified_when_window_is_visible() -> None:
    visible = _process_snapshot(windows=(_window(is_visible=True),), processes=(_process(),))

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_HIDDEN,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(visible, observation_id="snap-1"),
            post=_evidence(visible, observation_id="snap-2"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.NOT_VERIFIED


def test_window_focus_is_verified_from_uia_keyboard_focus_observation() -> None:
    unfocused = _uia_snapshot(_element(focus=False), captured_at=_PRE_AT)
    focused = _uia_snapshot(_element(focus=True), captured_at=_POST_AT)

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_FOCUSED,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(unfocused, observation_id="uia-1"),
            post=_evidence(focused, observation_id="uia-2"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.VERIFIED
    assert result.observation_contract is WindowsObservationContract.UIA_TREE_SNAPSHOT
    assert result.pre_state_evaluation is WindowsTransitionVerdict.NOT_VERIFIED


def test_focus_observed_on_a_child_element_verifies_the_window_transition() -> None:
    root = _element(path=(), focus=False, child_paths=((0,),))
    child = _element(path=(0,), runtime_id=(42, 2), focus=True, parent_path=())
    snapshot = _uia_snapshot(root, child)

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_FOCUSED,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(
                _uia_snapshot(_element(focus=False), captured_at=_PRE_AT), observation_id="uia-1"
            ),
            post=_evidence(snapshot, observation_id="uia-2"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.VERIFIED


def test_unfocused_window_is_not_verified_and_truncated_tree_is_insufficient() -> None:
    pre = _evidence(_uia_snapshot(_element(focus=False), captured_at=_PRE_AT), observation_id="a")
    complete = _uia_snapshot(_element(focus=False))
    truncated = _uia_snapshot(_element(focus=False), truncated_by_nodes=True)

    negative = _verify(
        _request(
            WindowsTransitionKind.WINDOW_FOCUSED,
            WindowsWindowTarget(handle=_HANDLE),
            pre=pre,
            post=_evidence(complete, observation_id="b"),
        )
    )
    unknown = _verify(
        _request(
            WindowsTransitionKind.WINDOW_FOCUSED,
            WindowsWindowTarget(handle=_HANDLE),
            pre=pre,
            post=_evidence(truncated, observation_id="c"),
        )
    )

    assert negative.verdict is WindowsTransitionVerdict.NOT_VERIFIED
    assert unknown.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert unknown.reasons == (WindowsVerificationReason.INCOMPLETE_OBSERVATION,)


def test_focus_without_any_available_property_is_insufficient() -> None:
    blind = _uia_snapshot(_element(focus=None))

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_FOCUSED,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(
                _uia_snapshot(_element(focus=None), captured_at=_PRE_AT), observation_id="a"
            ),
            post=_evidence(blind, observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.OBSERVED_PROPERTY_UNAVAILABLE,)


def test_window_move_resize_is_verified_within_the_explicit_tolerance() -> None:
    before = _uia_snapshot(_element(bounds=(0.0, 0.0, 800.0, 600.0)), captured_at=_PRE_AT)
    after = _uia_snapshot(_element(bounds=(100.5, 200.0, 900.0, 700.0)))
    state = WindowsRequestedState(
        expected_bounds=(100.0, 200.0, 900.0, 700.0), bounds_tolerance=1.0
    )
    exact = WindowsRequestedState(expected_bounds=(100.0, 200.0, 900.0, 700.0))

    tolerant = _verify(
        _request(
            WindowsTransitionKind.WINDOW_BOUNDS,
            WindowsWindowTarget(handle=_HANDLE),
            requested_state=state,
            pre=_evidence(before, observation_id="a"),
            post=_evidence(after, observation_id="b"),
        )
    )
    strict = _verify(
        _request(
            WindowsTransitionKind.WINDOW_BOUNDS,
            WindowsWindowTarget(handle=_HANDLE),
            requested_state=exact,
            pre=_evidence(before, observation_id="a"),
            post=_evidence(after, observation_id="b"),
        )
    )

    assert tolerant.verdict is WindowsTransitionVerdict.VERIFIED
    assert strict.verdict is WindowsTransitionVerdict.NOT_VERIFIED


def test_window_bounds_without_available_rectangle_is_insufficient() -> None:
    blind = _uia_snapshot(_element(bounds=None))

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_BOUNDS,
            WindowsWindowTarget(handle=_HANDLE),
            requested_state=WindowsRequestedState(expected_bounds=(0.0, 0.0, 10.0, 10.0)),
            pre=_evidence(
                _uia_snapshot(_element(bounds=None), captured_at=_PRE_AT), observation_id="a"
            ),
            post=_evidence(blind, observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.OBSERVED_PROPERTY_UNAVAILABLE,)


# ---------------------------------------------------------------------------
# Exact target binding: evidence for window A never verifies window B.
# ---------------------------------------------------------------------------


def test_uia_snapshot_of_another_window_cannot_verify_this_window() -> None:
    other_window = _uia_snapshot(
        _element(root_window_handle=_OTHER_HANDLE, focus=True),
        root_window_handle=_OTHER_HANDLE,
    )

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_FOCUSED,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(
                _uia_snapshot(
                    _element(root_window_handle=_OTHER_HANDLE),
                    root_window_handle=_OTHER_HANDLE,
                    captured_at=_PRE_AT,
                ),
                observation_id="a",
            ),
            post=_evidence(other_window, observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.TARGET_IDENTITY_MISMATCH,)


def test_recycled_window_handle_owned_by_another_process_is_insufficient() -> None:
    recycled = _process_snapshot(
        windows=(_window(process_id=_OTHER_PID),),
        processes=(_process(process_id=_OTHER_PID),),
    )

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE, process_id=_PID),
            pre=_evidence(recycled, observation_id="a"),
            post=_evidence(recycled, observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.TARGET_IDENTITY_MISMATCH,)


def test_evidence_about_another_element_cannot_verify_the_target_element() -> None:
    typed = _uia_snapshot(_element(path=(), runtime_id=(42, 99), value="hello"))
    target = WindowsElementTarget(
        reference=UIAElementReference(root_window_handle=_HANDLE, path=(), runtime_id=(42, 1))
    )

    result = _verify(
        _request(
            WindowsTransitionKind.TEXT_FIELD_VALUE,
            target,
            requested_state=WindowsRequestedState(expected_text="hello"),
            pre=_evidence(
                _uia_snapshot(_element(runtime_id=(42, 99)), captured_at=_PRE_AT),
                observation_id="a",
            ),
            post=_evidence(typed, observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.TARGET_IDENTITY_MISMATCH,)


def test_target_element_absent_from_the_bounded_tree_is_insufficient() -> None:
    tree = _uia_snapshot(_element(path=()))
    target = WindowsElementTarget(
        reference=UIAElementReference(root_window_handle=_HANDLE, path=(3,), runtime_id=None)
    )

    result = _verify(
        _request(
            WindowsTransitionKind.TEXT_FIELD_VALUE,
            target,
            requested_state=WindowsRequestedState(expected_text="hello"),
            pre=_evidence(_uia_snapshot(captured_at=_PRE_AT), observation_id="a"),
            post=_evidence(tree, observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.TARGET_NOT_OBSERVED,)


def test_vanished_target_element_is_insufficient() -> None:
    tree = _uia_snapshot(_element(value="hello", state=UIAElementState.VANISHED))
    target = WindowsElementTarget(
        reference=UIAElementReference(root_window_handle=_HANDLE, path=(), runtime_id=None)
    )

    result = _verify(
        _request(
            WindowsTransitionKind.TEXT_FIELD_VALUE,
            target,
            requested_state=WindowsRequestedState(expected_text="hello"),
            pre=_evidence(_uia_snapshot(captured_at=_PRE_AT), observation_id="a"),
            post=_evidence(tree, observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.TARGET_NOT_OBSERVED,)


# ---------------------------------------------------------------------------
# Application / process transitions.
# ---------------------------------------------------------------------------


def test_application_process_presence_and_absence() -> None:
    running = _process_snapshot(windows=(_window(),), processes=(_process(),))
    gone = _process_snapshot()

    started = _verify(
        _request(
            WindowsTransitionKind.APPLICATION_PROCESS_PRESENT,
            WindowsProcessTarget(process_id=_PID),
            pre=_evidence(gone, observation_id="a"),
            post=_evidence(running, observation_id="b"),
        )
    )
    terminated = _verify(
        _request(
            WindowsTransitionKind.APPLICATION_PROCESS_ABSENT,
            WindowsProcessTarget(process_id=_PID),
            pre=_evidence(running, observation_id="a"),
            post=_evidence(gone, observation_id="b"),
        )
    )
    not_started = _verify(
        _request(
            WindowsTransitionKind.APPLICATION_PROCESS_PRESENT,
            WindowsProcessTarget(process_id=_PID),
            pre=_evidence(gone, observation_id="a"),
            post=_evidence(gone, observation_id="b"),
        )
    )

    assert started.verdict is WindowsTransitionVerdict.VERIFIED
    assert started.pre_state_evaluation is WindowsTransitionVerdict.NOT_VERIFIED
    assert terminated.verdict is WindowsTransitionVerdict.VERIFIED
    assert not_started.verdict is WindowsTransitionVerdict.NOT_VERIFIED


def test_executable_identity_matches_case_insensitively_and_rejects_a_different_image() -> None:
    notepad = _process_snapshot(windows=(_window(),), processes=(_process(),))
    calculator = _process_snapshot(
        windows=(_window(),),
        processes=(_process(executable_name="Calculator.exe"),),
    )
    state = WindowsRequestedState(expected_executable_name="NOTEPAD.EXE")

    matched = _verify(
        _request(
            WindowsTransitionKind.APPLICATION_EXECUTABLE_IDENTITY,
            WindowsProcessTarget(process_id=_PID),
            requested_state=state,
            pre=_evidence(_process_snapshot(), observation_id="a"),
            post=_evidence(notepad, observation_id="b"),
        )
    )
    mismatched = _verify(
        _request(
            WindowsTransitionKind.APPLICATION_EXECUTABLE_IDENTITY,
            WindowsProcessTarget(process_id=_PID),
            requested_state=state,
            pre=_evidence(_process_snapshot(), observation_id="a"),
            post=_evidence(calculator, observation_id="b"),
        )
    )

    assert matched.verdict is WindowsTransitionVerdict.VERIFIED
    assert mismatched.verdict is WindowsTransitionVerdict.NOT_VERIFIED


def test_executable_identity_without_available_metadata_is_insufficient() -> None:
    denied = _process_snapshot(
        windows=(_window(),),
        processes=(
            _process(
                executable_name=None,
                executable_name_status=MetadataStatus.ACCESS_DENIED,
            ),
        ),
    )

    result = _verify(
        _request(
            WindowsTransitionKind.APPLICATION_EXECUTABLE_IDENTITY,
            WindowsProcessTarget(process_id=_PID),
            requested_state=WindowsRequestedState(expected_executable_name="notepad.exe"),
            pre=_evidence(_process_snapshot(), observation_id="a"),
            post=_evidence(denied, observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.TARGET_METADATA_UNAVAILABLE,)


def test_presenting_a_visible_window_is_observable_but_readiness_is_not() -> None:
    headless = _process_snapshot(
        processes=(_process(window_handles=(), visible_window_count=0),),
    )
    windowed = _process_snapshot(windows=(_window(),), processes=(_process(),))

    presents = _verify(
        _request(
            WindowsTransitionKind.APPLICATION_PRESENTS_WINDOW,
            WindowsProcessTarget(process_id=_PID),
            pre=_evidence(headless, observation_id="a"),
            post=_evidence(windowed, observation_id="b"),
        )
    )
    silent = _verify(
        _request(
            WindowsTransitionKind.APPLICATION_PRESENTS_WINDOW,
            WindowsProcessTarget(process_id=_PID),
            pre=_evidence(headless, observation_id="a"),
            post=_evidence(headless, observation_id="b"),
        )
    )
    readiness = _verify(
        _request(
            WindowsTransitionKind.APPLICATION_READY,
            WindowsProcessTarget(process_id=_PID),
            pre=_evidence(headless, observation_id="a"),
            post=_evidence(windowed, observation_id="b"),
        )
    )

    assert presents.verdict is WindowsTransitionVerdict.VERIFIED
    assert silent.verdict is WindowsTransitionVerdict.NOT_VERIFIED
    assert readiness.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert readiness.reasons == (WindowsVerificationReason.NO_CANONICAL_OBSERVATION_CONTRACT,)


def test_absence_is_inconclusive_when_the_snapshot_dropped_invalid_entries() -> None:
    partial = _process_snapshot(dropped_invalid_entries=3)

    result = _verify(
        _request(
            WindowsTransitionKind.APPLICATION_PROCESS_ABSENT,
            WindowsProcessTarget(process_id=_PID),
            pre=_evidence(
                _process_snapshot(windows=(_window(),), processes=(_process(),)),
                observation_id="a",
            ),
            post=_evidence(partial, observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.INCOMPLETE_OBSERVATION,)


# ---------------------------------------------------------------------------
# Text entry and clipboard.
# ---------------------------------------------------------------------------


def test_text_entry_is_verified_only_from_the_field_value_observation() -> None:
    empty = _uia_snapshot(_element(value=None), captured_at=_PRE_AT)
    typed = _uia_snapshot(_element(value="invoice 42"))
    target = WindowsElementTarget(
        reference=UIAElementReference(root_window_handle=_HANDLE, path=(), runtime_id=(42, 1))
    )

    verified = _verify(
        _request(
            WindowsTransitionKind.TEXT_FIELD_VALUE,
            target,
            requested_state=WindowsRequestedState(expected_text="invoice 42"),
            pre=_evidence(empty, observation_id="a"),
            post=_evidence(typed, observation_id="b"),
        )
    )
    wrong_text = _verify(
        _request(
            WindowsTransitionKind.TEXT_FIELD_VALUE,
            target,
            requested_state=WindowsRequestedState(expected_text="invoice 43"),
            pre=_evidence(empty, observation_id="a"),
            post=_evidence(typed, observation_id="b"),
        )
    )

    assert verified.verdict is WindowsTransitionVerdict.VERIFIED
    assert verified.pre_state_evaluation is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert wrong_text.verdict is WindowsTransitionVerdict.NOT_VERIFIED


def test_text_entry_without_a_field_value_observation_stays_insufficient() -> None:
    no_value = _uia_snapshot(_element(value=None))
    target = WindowsElementTarget(
        reference=UIAElementReference(root_window_handle=_HANDLE, path=(), runtime_id=(42, 1))
    )

    result = _verify(
        _request(
            WindowsTransitionKind.TEXT_FIELD_VALUE,
            target,
            requested_state=WindowsRequestedState(expected_text="invoice 42"),
            pre=_evidence(
                _uia_snapshot(_element(value=None), captured_at=_PRE_AT), observation_id="a"
            ),
            post=_evidence(no_value, observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.OBSERVED_PROPERTY_UNAVAILABLE,)


def test_key_delivery_alone_is_never_verification() -> None:
    result = _verify(
        _request(
            WindowsTransitionKind.KEY_SEQUENCE_SENT,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(_uia_snapshot(captured_at=_PRE_AT), observation_id="a"),
            post=_evidence(_uia_snapshot(), observation_id="b"),
            native=_native(succeeded=True),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert WindowsVerificationReason.NO_CANONICAL_OBSERVATION_CONTRACT in result.reasons


def test_clipboard_has_no_canonical_observation_contract_in_the_baseline() -> None:
    assert (
        REQUIRED_OBSERVATION_CONTRACT[WindowsTransitionKind.CLIPBOARD_TEXT]
        is WindowsObservationContract.NONE
    )

    result = _verify(
        _request(
            WindowsTransitionKind.CLIPBOARD_TEXT,
            WindowsSessionTarget(label="user-session"),
            requested_state=WindowsRequestedState(expected_text="copied text"),
            pre=_evidence(_process_snapshot(), observation_id="a"),
            post=_evidence(_process_snapshot(), observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.NO_CANONICAL_OBSERVATION_CONTRACT,)


@pytest.mark.parametrize(
    "kind",
    [
        WindowsTransitionKind.WINDOW_MINIMIZED,
        WindowsTransitionKind.WINDOW_MAXIMIZED,
        WindowsTransitionKind.WINDOW_RESTORED,
    ],
)
def test_window_visual_state_is_not_observable_from_the_canonical_baseline(
    kind: WindowsTransitionKind,
) -> None:
    result = _verify(
        _request(
            kind,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(
                _process_snapshot(windows=(_window(),), processes=(_process(),)),
                observation_id="a",
            ),
            post=_evidence(
                _process_snapshot(windows=(_window(),), processes=(_process(),)),
                observation_id="b",
            ),
            native=_native(succeeded=True),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons[0] is WindowsVerificationReason.NO_CANONICAL_OBSERVATION_CONTRACT
    assert kind in UNVERIFIABLE_TRANSITION_KINDS


# ---------------------------------------------------------------------------
# Native return is never verification.
# ---------------------------------------------------------------------------


def test_native_success_without_post_state_evidence_is_never_verified() -> None:
    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(
                _process_snapshot(windows=(_window(is_visible=False),), processes=(_process(),)),
                observation_id="a",
            ),
            post=None,
            native=_native(succeeded=True),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.verified is False
    assert result.reasons == (
        WindowsVerificationReason.POST_STATE_OBSERVATION_MISSING,
        WindowsVerificationReason.NATIVE_RESULT_IS_NOT_VERIFICATION,
    )
    assert result.native_reported_success is True
    assert result.post_state_observation_id is None


def test_missing_pre_state_is_insufficient_even_with_a_perfect_post_state() -> None:
    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE),
            pre=None,
            post=_evidence(
                _process_snapshot(windows=(_window(),), processes=(_process(),)),
                observation_id="b",
            ),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.PRE_STATE_OBSERVATION_MISSING,)


def test_native_success_cannot_flip_a_contradicting_post_state_observation() -> None:
    unchanged = _process_snapshot(windows=(_window(is_visible=False),), processes=(_process(),))

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(unchanged, observation_id="a"),
            post=_evidence(unchanged, observation_id="b"),
            native=_native(succeeded=True),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.NOT_VERIFIED
    assert result.reasons == (
        WindowsVerificationReason.OBSERVED_STATE_DIFFERS_FROM_REQUEST,
        WindowsVerificationReason.NATIVE_RESULT_IS_NOT_VERIFICATION,
    )
    assert result.native_reported_success is True


@pytest.mark.parametrize("succeeded", [True, False])
def test_verdict_is_identical_whatever_the_native_result_claims(succeeded: bool) -> None:
    shown = _process_snapshot(windows=(_window(),), processes=(_process(),))
    hidden = _process_snapshot(windows=(_window(is_visible=False),), processes=(_process(),))

    def evaluate(post: WindowsProcessSnapshot) -> WindowsTransitionVerdict:
        return _verify(
            _request(
                WindowsTransitionKind.WINDOW_VISIBLE,
                WindowsWindowTarget(handle=_HANDLE),
                pre=_evidence(hidden, observation_id="a"),
                post=_evidence(post, observation_id="b"),
                native=_native(succeeded=succeeded),
            )
        ).verdict

    assert evaluate(shown) is WindowsTransitionVerdict.VERIFIED
    assert evaluate(hidden) is WindowsTransitionVerdict.NOT_VERIFIED


def test_a_failed_native_call_can_still_be_verified_by_the_observed_state() -> None:
    hidden = _process_snapshot(windows=(_window(is_visible=False),), processes=(_process(),))
    shown = _process_snapshot(windows=(_window(),), processes=(_process(),))

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(hidden, observation_id="a"),
            post=_evidence(shown, observation_id="b"),
            native=_native(succeeded=False),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.VERIFIED
    assert result.native_reported_success is False


# ---------------------------------------------------------------------------
# Envelope: contract, environment, distinctness, canonical freshness.
# ---------------------------------------------------------------------------


def test_wrong_observation_contract_for_the_kind_is_insufficient() -> None:
    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_FOCUSED,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(_process_snapshot(), observation_id="a"),
            post=_evidence(_process_snapshot(), observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.OBSERVATION_CONTRACT_MISMATCH,)


def test_observation_from_another_environment_is_insufficient() -> None:
    shown = _process_snapshot(windows=(_window(),), processes=(_process(),))

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(shown, observation_id="a"),
            post=_evidence(shown, observation_id="b", environment_id="other-desk"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.ENVIRONMENT_IDENTITY_MISMATCH,)


def test_one_snapshot_cannot_be_both_pre_state_and_post_state() -> None:
    shown = _process_snapshot(windows=(_window(),), processes=(_process(),))

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(shown, observation_id="same-snapshot"),
            post=_evidence(shown, observation_id="same-snapshot"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.POST_STATE_NOT_DISTINCT_FROM_PRE_STATE,)


def test_stale_post_state_snapshot_is_rejected_by_the_canonical_timestamp() -> None:
    older = _uia_snapshot(_element(focus=True), captured_at=_PRE_AT)
    newer = _uia_snapshot(_element(focus=False), captured_at=_POST_AT)

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_FOCUSED,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(newer, observation_id="a"),
            post=_evidence(older, observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.STALE_POST_STATE_OBSERVATION,)


def test_simultaneous_uia_snapshots_cannot_bracket_a_transition() -> None:
    same_time_pre = _uia_snapshot(_element(focus=False), captured_at=_POST_AT)
    same_time_post = _uia_snapshot(_element(focus=True), captured_at=_POST_AT)

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_FOCUSED,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(same_time_pre, observation_id="a"),
            post=_evidence(same_time_post, observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons == (WindowsVerificationReason.STALE_POST_STATE_OBSERVATION,)


def test_process_snapshots_carry_no_canonical_timestamp_so_none_is_invented() -> None:
    snapshot = _process_snapshot(windows=(_window(),), processes=(_process(),))
    evidence = _evidence(snapshot, observation_id="a")

    assert evidence.captured_at is None
    assert evidence.contract is WindowsObservationContract.PROCESS_SNAPSHOT


def test_multiple_envelope_problems_are_reported_deterministically() -> None:
    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_FOCUSED,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(_process_snapshot(), observation_id="same", environment_id="elsewhere"),
            post=_evidence(_process_snapshot(), observation_id="same"),
        )
    )

    assert result.reasons == (
        WindowsVerificationReason.OBSERVATION_CONTRACT_MISMATCH,
        WindowsVerificationReason.ENVIRONMENT_IDENTITY_MISMATCH,
        WindowsVerificationReason.POST_STATE_NOT_DISTINCT_FROM_PRE_STATE,
    )


# ---------------------------------------------------------------------------
# Pre-state semantics and determinism.
# ---------------------------------------------------------------------------


def test_pre_state_evaluation_exposes_an_already_satisfied_state() -> None:
    shown = _process_snapshot(windows=(_window(),), processes=(_process(),))

    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(shown, observation_id="a"),
            post=_evidence(shown, observation_id="b"),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.VERIFIED
    assert result.pre_state_evaluation is WindowsTransitionVerdict.VERIFIED


def test_repeated_evaluation_is_deterministic_and_the_verifier_is_stateless() -> None:
    request = _request(
        WindowsTransitionKind.WINDOW_VISIBLE,
        WindowsWindowTarget(handle=_HANDLE),
        pre=_evidence(
            _process_snapshot(windows=(_window(is_visible=False),), processes=(_process(),)),
            observation_id="a",
        ),
        post=_evidence(
            _process_snapshot(windows=(_window(),), processes=(_process(),)),
            observation_id="b",
        ),
    )
    verifier = WindowsTransitionVerifier()

    results = [verifier.evaluate(request) for _ in range(5)]
    results.append(WindowsTransitionVerifier().evaluate(request))

    assert all(result == results[0] for result in results)
    assert len({json.dumps(result.to_dict(), sort_keys=True) for result in results}) == 1


def test_evidence_is_immutable_and_json_serializable() -> None:
    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE, process_id=_PID),
            pre=_evidence(
                _process_snapshot(windows=(_window(is_visible=False),), processes=(_process(),)),
                observation_id="a",
            ),
            post=_evidence(
                _process_snapshot(windows=(_window(),), processes=(_process(),)),
                observation_id="b",
            ),
            native=_native(succeeded=True),
        )
    )
    payload = result.to_dict()

    with pytest.raises(FrozenInstanceError):
        result.verdict = WindowsTransitionVerdict.VERIFIED  # type: ignore[misc]
    assert json.loads(json.dumps(payload)) == payload
    assert payload["verdict"] == "verified"
    assert payload["operation"] == "windows.window.set-state@1.0.0"
    assert payload["target"] == f"window:{_HANDLE}@pid:{_PID}"
    assert payload["native_reported_success"] is True
    assert payload["source"] == "agentx.windows_transition_verification"


# ---------------------------------------------------------------------------
# Request-shape validation (fail closed at construction).
# ---------------------------------------------------------------------------


def test_transition_kind_requires_its_canonical_target_type() -> None:
    with pytest.raises(WindowsTransitionRequestError):
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsProcessTarget(process_id=_PID),
        )
    with pytest.raises(WindowsTransitionRequestError):
        _request(
            WindowsTransitionKind.TEXT_FIELD_VALUE,
            WindowsWindowTarget(handle=_HANDLE),
            requested_state=WindowsRequestedState(expected_text="x"),
        )


def test_expectation_fields_must_match_the_transition_kind_exactly() -> None:
    with pytest.raises(WindowsTransitionRequestError):
        _request(
            WindowsTransitionKind.TEXT_FIELD_VALUE,
            WindowsElementTarget(
                reference=UIAElementReference(root_window_handle=_HANDLE, path=(), runtime_id=None)
            ),
        )
    with pytest.raises(WindowsTransitionRequestError):
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE),
            requested_state=WindowsRequestedState(expected_text="ignored"),
        )
    with pytest.raises(WindowsTransitionRequestError):
        WindowsRequestedState(bounds_tolerance=-1.0)


def test_native_evidence_must_belong_to_the_same_attempt() -> None:
    with pytest.raises(WindowsTransitionRequestError):
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE),
            native=_native(attempt_id="another-attempt"),
        )


def test_malformed_identifiers_and_arguments_are_rejected() -> None:
    with pytest.raises(WindowsTransitionRequestError):
        WindowsObservationEvidence(
            snapshot=_process_snapshot(), observation_id=" ", environment_id=_ENVIRONMENT
        )
    with pytest.raises(TypeError):
        WindowsObservationEvidence(
            snapshot="not a snapshot",  # type: ignore[arg-type]
            observation_id="a",
            environment_id=_ENVIRONMENT,
        )
    with pytest.raises(WindowsTransitionRequestError):
        WindowsRequestedState(expected_executable_name="C:\\apps\\notepad.exe")
    with pytest.raises(TypeError):
        WindowsTransitionVerifier().evaluate("not a request")  # type: ignore[arg-type]


def test_emitted_evidence_cannot_claim_a_verified_verdict_without_the_matching_reason() -> None:
    with pytest.raises(WindowsTransitionRequestError):
        WindowsTransitionVerification(
            operation=_OPERATION,
            attempt_id="attempt-1",
            environment_id=_ENVIRONMENT,
            kind=WindowsTransitionKind.WINDOW_VISIBLE,
            target_descriptor=f"window:{_HANDLE}",
            observation_contract=WindowsObservationContract.PROCESS_SNAPSHOT,
            verdict=WindowsTransitionVerdict.VERIFIED,
            reasons=(WindowsVerificationReason.OBSERVED_STATE_DIFFERS_FROM_REQUEST,),
            pre_state_observation_id="a",
            post_state_observation_id="b",
            pre_state_evaluation=WindowsTransitionVerdict.NOT_VERIFIED,
            native_reported_success=None,
        )
    with pytest.raises(WindowsTransitionRequestError):
        WindowsTransitionVerification(
            operation=_OPERATION,
            attempt_id="attempt-1",
            environment_id=_ENVIRONMENT,
            kind=WindowsTransitionKind.WINDOW_VISIBLE,
            target_descriptor=f"window:{_HANDLE}",
            observation_contract=WindowsObservationContract.PROCESS_SNAPSHOT,
            verdict=WindowsTransitionVerdict.NOT_VERIFIED,
            reasons=(WindowsVerificationReason.OBSERVED_STATE_MATCHES_REQUEST,),
            pre_state_observation_id="a",
            post_state_observation_id="b",
            pre_state_evaluation=WindowsTransitionVerdict.NOT_VERIFIED,
            native_reported_success=None,
        )


# ---------------------------------------------------------------------------
# Vocabulary invariants.
# ---------------------------------------------------------------------------


def test_every_transition_kind_declares_its_observation_contract() -> None:
    every_kind = set(WindowsTransitionKind)
    unverifiable = {
        WindowsTransitionKind.WINDOW_MINIMIZED,
        WindowsTransitionKind.WINDOW_MAXIMIZED,
        WindowsTransitionKind.WINDOW_RESTORED,
        WindowsTransitionKind.APPLICATION_READY,
        WindowsTransitionKind.KEY_SEQUENCE_SENT,
        WindowsTransitionKind.CLIPBOARD_TEXT,
    }

    assert set(REQUIRED_OBSERVATION_CONTRACT) == every_kind
    assert SUPPORTED_TRANSITION_KINDS.isdisjoint(UNVERIFIABLE_TRANSITION_KINDS)
    assert every_kind == (SUPPORTED_TRANSITION_KINDS | UNVERIFIABLE_TRANSITION_KINDS)
    assert set(UNVERIFIABLE_TRANSITION_KINDS) == unverifiable


def test_the_verdict_vocabulary_is_exactly_three_valued() -> None:
    assert {member.value for member in WindowsTransitionVerdict} == {
        "verified",
        "not_verified",
        "insufficient_evidence",
    }


def test_emitted_evidence_is_not_a_canonical_capability_verification_result() -> None:
    result = _verify(
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE),
            pre=_evidence(
                _process_snapshot(windows=(_window(is_visible=False),), processes=(_process(),)),
                observation_id="a",
            ),
            post=_evidence(
                _process_snapshot(windows=(_window(),), processes=(_process(),)),
                observation_id="b",
            ),
        )
    )

    assert type(result) is WindowsTransitionVerification
    assert not issubclass(WindowsTransitionVerification, VerificationResult)
    assert not hasattr(result, "passed")
    assert not hasattr(result, "task")
