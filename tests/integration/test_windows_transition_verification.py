"""Integration coverage: N2.25 verification over real canonical observations.

These tests do not hand-build snapshots. They drive the canonical read-only
Windows observation producers — A5.02 ``WindowsProcessDiscovery`` and A5.03
``WindowsUIATreeInspection`` — through deterministic fake native seams, and
feed the resulting canonical snapshots to the verification boundary exactly as
a composition root would after attempting a mutation.

The mutation itself is never performed here: the seams simply return the
"before" and "after" pictures of the desktop. The verifier only reads them.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentx.capabilities.abi import (
    CapabilityIdentity,
    CapabilityName,
    CapabilityObservation,
    CapabilityVersion,
    ExecutionResult,
)
from agentx.capabilities.windows import _uia_native
from agentx.capabilities.windows.process_discovery import WindowsProcessSnapshot
from agentx.capabilities.windows.provider import (
    PlatformFacts,
    WindowsSupport,
    evaluate_windows_support,
)
from agentx.capabilities.windows.uia_tree import (
    NativeUIATreeSurface,
    UIAElementReference,
    UIAPatternName,
    UIAPropertyName,
    UIATreeLimits,
    UIATreeSnapshot,
    WindowsUIATreeInspection,
)
from agentx.core.errors import AgentXError
from agentx.core.result import Result
from agentx.windows_transition_verification import (
    NativeExecutionEvidence,
    WindowsElementTarget,
    WindowsObservationEvidence,
    WindowsProcessTarget,
    WindowsRequestedState,
    WindowsTransitionKind,
    WindowsTransitionRequest,
    WindowsTransitionVerdict,
    WindowsTransitionVerifier,
    WindowsVerificationReason,
    WindowsWindowTarget,
)
from tests.support.fake_windows_native import (
    FakeWindowsNative,
    path_ok,
    raw_process,
    raw_window,
    windows_discovery,
)

_HANDLE = 5150
_PID = 3300
_ENVIRONMENT = "integration-desk"
_OPERATION = CapabilityIdentity(
    name=CapabilityName("windows.window.transition"),
    version=CapabilityVersion(major=1, minor=0, patch=0),
)


def _support() -> WindowsSupport:
    return evaluate_windows_support(
        PlatformFacts(system="Windows", release="11", version="10.0.26100", machine="AMD64")
    )


def _discover(
    *,
    windows: list[object],
    processes: list[object],
) -> tuple[WindowsProcessSnapshot, FakeWindowsNative]:
    fake = FakeWindowsNative(
        processes=list(processes),  # type: ignore[arg-type]
        windows=list(windows),  # type: ignore[arg-type]
        path_queries={_PID: path_ok("C:\\Windows\\System32\\notepad.exe")},
    )
    outcome = windows_discovery(fake).discover()
    assert outcome.is_success
    return outcome.unwrap(), fake


class _FakeUIASurface(NativeUIATreeSurface):
    """Deterministic UIA seam returning one preloaded raw tree."""

    def __init__(self, tree: _uia_native.RawUIATree) -> None:
        self.tree = tree
        self.calls: list[int] = []

    def inspect_window(
        self,
        window_handle: int,
        limits: UIATreeLimits,
    ) -> Result[_uia_native.RawUIATree, AgentXError]:
        self.calls.append(window_handle)
        return Result.success(self.tree)


def _raw_property(name: str, value: object | None) -> _uia_native.RawUIAProperty:
    if value is None:
        return _uia_native.RawUIAProperty(name=name, value=None, unavailable=True)
    return _uia_native.RawUIAProperty(name=name, value=value, unavailable=False)


def _raw_tree(
    *,
    focus: bool,
    bounds: tuple[float, float, float, float],
    value: str | None,
) -> _uia_native.RawUIATree:
    values: dict[str, object | None] = {
        UIAPropertyName.RUNTIME_ID.value: (7, 11),
        UIAPropertyName.BOUNDING_RECTANGLE.value: bounds,
        UIAPropertyName.PROCESS_ID.value: _PID,
        UIAPropertyName.CONTROL_TYPE.value: 50032,
        UIAPropertyName.NAME.value: "Untitled - Notepad",
        UIAPropertyName.HAS_KEYBOARD_FOCUS.value: focus,
        UIAPropertyName.IS_KEYBOARD_FOCUSABLE.value: True,
        UIAPropertyName.IS_ENABLED.value: True,
        UIAPropertyName.AUTOMATION_ID.value: "editor",
        UIAPropertyName.NATIVE_WINDOW_HANDLE.value: _HANDLE,
        UIAPropertyName.IS_OFFSCREEN.value: False,
        UIAPropertyName.VALUE.value: value,
    }
    element = _uia_native.RawUIAElement(
        sequence=0,
        parent_sequence=None,
        depth=0,
        child_index=0,
        properties=tuple(_raw_property(item.value, values[item.value]) for item in UIAPropertyName),
        pattern_properties=tuple(
            _raw_property(pattern.value, pattern is UIAPatternName.VALUE)
            for pattern in UIAPatternName
        ),
    )
    return _uia_native.RawUIATree(
        elements=(element,),
        errors=(),
        truncated_by_depth=False,
        truncated_by_nodes=False,
    )


def _inspect(
    tree: _uia_native.RawUIATree, captured_at: datetime
) -> tuple[UIATreeSnapshot, _FakeUIASurface]:
    surface = _FakeUIASurface(tree)
    inspection = WindowsUIATreeInspection(
        _support(), native_surface=surface, clock=lambda: captured_at
    )
    outcome = inspection.inspect(_HANDLE, limits=UIATreeLimits(max_depth=2, max_nodes=16))
    assert outcome.is_success
    return outcome.unwrap(), surface


def _evidence(
    snapshot: WindowsProcessSnapshot | UIATreeSnapshot, observation_id: str
) -> WindowsObservationEvidence:
    return WindowsObservationEvidence(
        snapshot=snapshot,
        observation_id=observation_id,
        environment_id=_ENVIRONMENT,
    )


def _native_success() -> NativeExecutionEvidence:
    return NativeExecutionEvidence(
        attempt_id="attempt-42",
        result=ExecutionResult(
            succeeded=True,
            message="native mutation reported success",
            observation=CapabilityObservation(summary="win32 returned TRUE", data={"result": 1}),
        ),
    )


def _request(
    kind: WindowsTransitionKind,
    target: object,
    *,
    pre: WindowsObservationEvidence,
    post: WindowsObservationEvidence,
    requested_state: WindowsRequestedState | None = None,
    native: NativeExecutionEvidence | None = None,
) -> WindowsTransitionRequest:
    return WindowsTransitionRequest(
        operation=_OPERATION,
        attempt_id="attempt-42",
        environment_id=_ENVIRONMENT,
        kind=kind,
        target=target,  # type: ignore[arg-type]
        requested_state=requested_state if requested_state is not None else WindowsRequestedState(),
        pre_state=pre,
        post_state=post,
        native_evidence=native,
    )


@pytest.mark.integration
def test_application_launch_is_verified_from_two_real_discovery_snapshots() -> None:
    before, _ = _discover(windows=[], processes=[])
    after, fake_after = _discover(
        windows=[raw_window(_HANDLE, process_id=_PID, title="Untitled - Notepad")],
        processes=[raw_process(_PID, executable_name="notepad.exe")],
    )
    verifier = WindowsTransitionVerifier()

    launched = verifier.evaluate(
        _request(
            WindowsTransitionKind.APPLICATION_PROCESS_PRESENT,
            WindowsProcessTarget(process_id=_PID),
            pre=_evidence(before, "discovery-1"),
            post=_evidence(after, "discovery-2"),
            native=_native_success(),
        )
    )
    identity = verifier.evaluate(
        _request(
            WindowsTransitionKind.APPLICATION_EXECUTABLE_IDENTITY,
            WindowsProcessTarget(process_id=_PID),
            requested_state=WindowsRequestedState(expected_executable_name="notepad.exe"),
            pre=_evidence(before, "discovery-1"),
            post=_evidence(after, "discovery-2"),
        )
    )
    presents_window = verifier.evaluate(
        _request(
            WindowsTransitionKind.APPLICATION_PRESENTS_WINDOW,
            WindowsProcessTarget(process_id=_PID),
            pre=_evidence(before, "discovery-1"),
            post=_evidence(after, "discovery-2"),
        )
    )

    assert launched.verdict is WindowsTransitionVerdict.VERIFIED
    assert identity.verdict is WindowsTransitionVerdict.VERIFIED
    assert presents_window.verdict is WindowsTransitionVerdict.VERIFIED
    # Verification read the snapshots only; it never touched the native seam.
    fake_after.assert_only_read_methods_called()
    assert fake_after.read_method_calls.count("enumerate_windows") == 1


@pytest.mark.integration
def test_native_success_with_an_unchanged_desktop_is_not_verified() -> None:
    hidden_window = raw_window(
        _HANDLE, process_id=_PID, title="Untitled - Notepad", is_visible=False
    )
    before, _ = _discover(
        windows=[hidden_window], processes=[raw_process(_PID, executable_name="notepad.exe")]
    )
    after, fake_after = _discover(
        windows=[hidden_window], processes=[raw_process(_PID, executable_name="notepad.exe")]
    )

    result = WindowsTransitionVerifier().evaluate(
        _request(
            WindowsTransitionKind.WINDOW_VISIBLE,
            WindowsWindowTarget(handle=_HANDLE, process_id=_PID),
            pre=_evidence(before, "discovery-1"),
            post=_evidence(after, "discovery-2"),
            native=_native_success(),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.NOT_VERIFIED
    assert result.native_reported_success is True
    assert WindowsVerificationReason.NATIVE_RESULT_IS_NOT_VERIFICATION in result.reasons
    assert fake_after.read_method_calls.count("enumerate_processes") == 1


@pytest.mark.integration
def test_window_move_and_focus_are_verified_from_real_uia_snapshots() -> None:
    before, _ = _inspect(
        _raw_tree(focus=False, bounds=(0.0, 0.0, 800.0, 600.0), value=None),
        datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
    )
    after, surface_after = _inspect(
        _raw_tree(focus=True, bounds=(120.0, 240.0, 920.0, 840.0), value=None),
        datetime(2026, 9, 8, 12, 0, 3, tzinfo=UTC),
    )
    verifier = WindowsTransitionVerifier()

    moved = verifier.evaluate(
        _request(
            WindowsTransitionKind.WINDOW_BOUNDS,
            WindowsWindowTarget(handle=_HANDLE, process_id=_PID),
            requested_state=WindowsRequestedState(
                expected_bounds=(120.0, 240.0, 920.0, 840.0), bounds_tolerance=0.5
            ),
            pre=_evidence(before, "uia-1"),
            post=_evidence(after, "uia-2"),
            native=_native_success(),
        )
    )
    focused = verifier.evaluate(
        _request(
            WindowsTransitionKind.WINDOW_FOCUSED,
            WindowsWindowTarget(handle=_HANDLE, process_id=_PID),
            pre=_evidence(before, "uia-1"),
            post=_evidence(after, "uia-2"),
        )
    )

    assert moved.verdict is WindowsTransitionVerdict.VERIFIED
    assert moved.pre_state_evaluation is WindowsTransitionVerdict.NOT_VERIFIED
    assert focused.verdict is WindowsTransitionVerdict.VERIFIED
    # One inspection per snapshot; the verifier itself performed no UIA read.
    assert surface_after.calls == [_HANDLE]


@pytest.mark.integration
def test_text_entry_is_verified_from_the_real_value_property_observation() -> None:
    before, _ = _inspect(
        _raw_tree(focus=True, bounds=(0.0, 0.0, 800.0, 600.0), value=None),
        datetime(2026, 9, 8, 13, 0, tzinfo=UTC),
    )
    after, _ = _inspect(
        _raw_tree(focus=True, bounds=(0.0, 0.0, 800.0, 600.0), value="quarterly report"),
        datetime(2026, 9, 8, 13, 0, 2, tzinfo=UTC),
    )
    target = WindowsElementTarget(
        reference=UIAElementReference(root_window_handle=_HANDLE, path=(), runtime_id=(7, 11))
    )

    typed = WindowsTransitionVerifier().evaluate(
        _request(
            WindowsTransitionKind.TEXT_FIELD_VALUE,
            target,
            requested_state=WindowsRequestedState(expected_text="quarterly report"),
            pre=_evidence(before, "uia-1"),
            post=_evidence(after, "uia-2"),
            native=_native_success(),
        )
    )
    before_typing = WindowsTransitionVerifier().evaluate(
        _request(
            WindowsTransitionKind.TEXT_FIELD_VALUE,
            target,
            requested_state=WindowsRequestedState(expected_text="quarterly report"),
            pre=_evidence(after, "uia-2"),
            post=_evidence(before, "uia-1"),
        )
    )

    assert typed.verdict is WindowsTransitionVerdict.VERIFIED
    # The reversed pair is both stale and value-less: never a silent success.
    assert before_typing.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert before_typing.reasons == (WindowsVerificationReason.STALE_POST_STATE_OBSERVATION,)


@pytest.mark.integration
def test_a_snapshot_of_one_window_cannot_verify_a_transition_of_another() -> None:
    before, _ = _inspect(
        _raw_tree(focus=False, bounds=(0.0, 0.0, 800.0, 600.0), value=None),
        datetime(2026, 9, 8, 14, 0, tzinfo=UTC),
    )
    after, _ = _inspect(
        _raw_tree(focus=True, bounds=(0.0, 0.0, 800.0, 600.0), value=None),
        datetime(2026, 9, 8, 14, 0, 1, tzinfo=UTC),
    )

    result = WindowsTransitionVerifier().evaluate(
        _request(
            WindowsTransitionKind.WINDOW_FOCUSED,
            WindowsWindowTarget(handle=_HANDLE + 1),
            pre=_evidence(before, "uia-1"),
            post=_evidence(after, "uia-2"),
            native=_native_success(),
        )
    )

    assert result.verdict is WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE
    assert result.reasons[0] is WindowsVerificationReason.TARGET_IDENTITY_MISMATCH


@pytest.mark.integration
def test_verification_evidence_round_trips_as_canonical_json() -> None:
    before, _ = _discover(windows=[], processes=[])
    after, _ = _discover(
        windows=[raw_window(_HANDLE, process_id=_PID)],
        processes=[raw_process(_PID, executable_name="notepad.exe")],
    )

    result = WindowsTransitionVerifier().evaluate(
        _request(
            WindowsTransitionKind.WINDOW_PRESENT,
            WindowsWindowTarget(handle=_HANDLE, process_id=_PID),
            pre=_evidence(before, "discovery-1"),
            post=_evidence(after, "discovery-2"),
        )
    )
    payload = result.to_dict()

    assert payload == {
        "source": "agentx.windows_transition_verification",
        "operation": "windows.window.transition@1.0.0",
        "attempt_id": "attempt-42",
        "environment_id": _ENVIRONMENT,
        "kind": "window_present",
        "target": f"window:{_HANDLE}@pid:{_PID}",
        "observation_contract": "windows_process_snapshot",
        "verdict": "verified",
        "reasons": ["observed_state_matches_request"],
        "pre_state_observation_id": "discovery-1",
        "post_state_observation_id": "discovery-2",
        "pre_state_evaluation": "not_verified",
        "native_reported_success": None,
    }
