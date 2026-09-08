"""Integration coverage for A5.03 observation followed by N2.24 resolution."""

from __future__ import annotations

from datetime import UTC, datetime

from agentx.capabilities.windows import _uia_native
from agentx.capabilities.windows.provider import (
    PlatformFacts,
    WindowsSupport,
    evaluate_windows_support,
)
from agentx.capabilities.windows.uia_target_resolution import (
    UIATargetQuery,
    UIATargetResolutionStatus,
    resolve_uia_target,
)
from agentx.capabilities.windows.uia_tree import (
    UIAFreshness,
    UIAPatternName,
    UIAPropertyName,
    UIATreeLimits,
    UIATreeSnapshot,
    WindowsUIATreeInspection,
)
from agentx.core.errors import AgentXError
from agentx.core.result import Result

_RUNTIME = datetime(2026, 9, 8, 0, 0, tzinfo=UTC)


def _support() -> WindowsSupport:
    return evaluate_windows_support(
        PlatformFacts(system="Windows", release="11", version="test", machine="AMD64")
    )


def _raw_property(
    name: str,
    value: object | None,
    *,
    unavailable: bool = False,
) -> _uia_native.RawUIAProperty:
    return _uia_native.RawUIAProperty(
        name=name,
        value=value,
        unavailable=unavailable,
    )


def _raw_element(
    sequence: int,
    *,
    parent_sequence: int | None,
    depth: int,
    child_index: int,
    name: object,
    automation_id: object,
) -> _uia_native.RawUIAElement:
    values = {
        "runtime_id": _raw_property("runtime_id", (42, sequence + 1)),
        "bounding_rectangle": _raw_property("bounding_rectangle", (0.0, 0.0, 100.0, 20.0)),
        "process_id": _raw_property("process_id", 123),
        "control_type": _raw_property("control_type", 50000),
        "name": _raw_property("name", name),
        "has_keyboard_focus": _raw_property("has_keyboard_focus", False),
        "is_keyboard_focusable": _raw_property("is_keyboard_focusable", True),
        "is_enabled": _raw_property("is_enabled", True),
        "automation_id": _raw_property("automation_id", automation_id),
        "native_window_handle": _raw_property("native_window_handle", 100),
        "is_offscreen": _raw_property("is_offscreen", False),
        "value": _raw_property("value", None, unavailable=True),
    }
    patterns = tuple(_raw_property(pattern.value, False) for pattern in UIAPatternName)
    return _uia_native.RawUIAElement(
        sequence=sequence,
        parent_sequence=parent_sequence,
        depth=depth,
        child_index=child_index,
        properties=tuple(values[item.value] for item in UIAPropertyName),
        pattern_properties=patterns,
    )


def _tree(*elements: _uia_native.RawUIAElement) -> _uia_native.RawUIATree:
    return _uia_native.RawUIATree(
        elements=elements,
        errors=(),
        truncated_by_depth=False,
        truncated_by_nodes=False,
    )


class FakeSurface:
    def __init__(self, tree: _uia_native.RawUIATree) -> None:
        self.tree = tree
        self.calls: list[tuple[int, UIATreeLimits]] = []

    def inspect_window(
        self,
        window_handle: int,
        limits: UIATreeLimits,
    ) -> Result[_uia_native.RawUIATree, AgentXError]:
        self.calls.append((window_handle, limits))
        return Result.success(self.tree)


def _observe(
    *elements: _uia_native.RawUIAElement,
) -> tuple[WindowsUIATreeInspection, FakeSurface, UIATreeSnapshot]:
    surface = FakeSurface(_tree(*elements))
    inspection = WindowsUIATreeInspection(
        _support(),
        native_surface=surface,
        clock=lambda: _RUNTIME,
    )
    outcome = inspection.inspect(100, limits=UIATreeLimits(max_depth=1, max_nodes=8))
    assert outcome.is_success
    return inspection, surface, outcome.unwrap()


def test_observation_then_resolution_resolves_exact_automation_id() -> None:
    _inspection, surface, snapshot = _observe(
        _raw_element(
            0,
            parent_sequence=None,
            depth=0,
            child_index=0,
            name="Dialog",
            automation_id="dialog",
        ),
        _raw_element(
            1,
            parent_sequence=0,
            depth=1,
            child_index=0,
            name="Save",
            automation_id="save-button",
        ),
    )

    result = resolve_uia_target(snapshot, UIATargetQuery(automation_id="save-button"))

    assert result.status is UIATargetResolutionStatus.RESOLVED
    assert result.unique_match is not None
    assert result.unique_match.automation_id == "save-button"
    assert result.unique_match.name == "Save"
    assert result.snapshot is snapshot
    assert result.unique_match.reference.path == (0,)
    assert len(surface.calls) == 1


def test_resolution_observes_only_once_and_never_re_reads() -> None:
    _inspection, surface, snapshot = _observe(
        _raw_element(
            0,
            parent_sequence=None,
            depth=0,
            child_index=0,
            name="Dialog",
            automation_id="dialog",
        ),
        _raw_element(
            1,
            parent_sequence=0,
            depth=1,
            child_index=0,
            name="OK",
            automation_id="ok",
        ),
    )

    first = resolve_uia_target(snapshot, UIATargetQuery(name="OK"))
    second = resolve_uia_target(snapshot, UIATargetQuery(automation_id="missing"))

    assert first.status is UIATargetResolutionStatus.RESOLVED
    assert second.status is UIATargetResolutionStatus.NOT_FOUND
    assert surface.calls == [(100, UIATreeLimits(max_depth=1, max_nodes=8))]


def test_observation_snapshot_provenance_is_preserved_in_resolution() -> None:
    _inspection, _surface, snapshot = _observe(
        _raw_element(
            0,
            parent_sequence=None,
            depth=0,
            child_index=0,
            name="Dialog",
            automation_id="dialog",
        ),
    )

    result = resolve_uia_target(snapshot, UIATargetQuery(automation_id="dialog"))

    assert result.snapshot.captured_at == _RUNTIME
    assert result.snapshot.freshness is UIAFreshness.POINT_IN_TIME
    assert result.status is UIATargetResolutionStatus.RESOLVED
    assert result.unique_match is not None
    assert result.unique_match.reference.root_window_handle == 100


def test_ambiguous_and_file_not_found_outcomes_are_explicit() -> None:
    _inspection, _surface, snapshot = _observe(
        _raw_element(
            0,
            parent_sequence=None,
            depth=0,
            child_index=0,
            name="Dialog",
            automation_id="dialog",
        ),
        _raw_element(
            1,
            parent_sequence=0,
            depth=1,
            child_index=0,
            name="OK",
            automation_id="ok-first",
        ),
        _raw_element(
            2,
            parent_sequence=0,
            depth=1,
            child_index=1,
            name="OK",
            automation_id="ok-second",
        ),
    )

    ambiguous = resolve_uia_target(snapshot, UIATargetQuery(name="OK"))
    missing = resolve_uia_target(snapshot, UIATargetQuery(name="Cancel"))

    assert ambiguous.status is UIATargetResolutionStatus.AMBIGUOUS
    assert missing.status is UIATargetResolutionStatus.NOT_FOUND
