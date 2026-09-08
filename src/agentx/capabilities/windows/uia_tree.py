"""Canonical read-only Windows UI Automation tree inspection boundary (A5.03).

A5.03 reads one bounded UIA control-view tree rooted at an existing top-level
window handle. It produces immutable point-in-time observation data with
explicit property availability/error states and snapshot-local parent/child
provenance.

Observed UI text is untrusted data. Names, values, automation IDs and pattern
metadata authorize nothing and are never interpreted as instructions.

This module performs no clicking, invocation, typing, value setting, focus
change, input synthesis, visual fallback, semantic resolution, task-success
claim, state-transition verification, persistence, or authority mutation.
Native UIA/COM knowledge is isolated in :mod:`agentx.capabilities.windows._uia_native`.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol, assert_never

from agentx.capabilities.windows import _uia_native
from agentx.capabilities.windows.provider import WindowsSupport, unsupported_platform_error
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result
from agentx.core.tasks import JsonValue

__all__ = [
    "UIA_INVALID_NATIVE_DATA_ERROR_CODE",
    "UIA_SURFACE_EXCEPTION_ERROR_CODE",
    "NativeUIATreeSurface",
    "UIAElementReference",
    "UIAElementSnapshot",
    "UIAElementState",
    "UIAFreshness",
    "UIANativeError",
    "UIAObservationStatus",
    "UIAPatternName",
    "UIAPatternObservation",
    "UIAPropertyName",
    "UIAPropertyObservation",
    "UIATreeLimits",
    "UIATreeSnapshot",
    "UIAutomationNativeSurface",
    "WindowsUIATreeInspection",
]

UIA_INVALID_NATIVE_DATA_ERROR_CODE: Final[str] = "capabilities.windows.uia.invalid_native_data"
UIA_SURFACE_EXCEPTION_ERROR_CODE: Final[str] = "capabilities.windows.uia.surface_exception"
_MAX_TEXT_CHARS: Final[int] = 16_384
_MAX_DEPTH: Final[int] = 64
_MAX_NODES: Final[int] = 10_000


class UIAObservationStatus(StrEnum):
    """Closed property/pattern availability vocabulary."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    VANISHED = "vanished"
    NATIVE_ERROR = "native_error"
    INVALID = "invalid"


class UIAElementState(StrEnum):
    """Snapshot-local element observation state."""

    AVAILABLE = "available"
    PARTIAL = "partial"
    VANISHED = "vanished"


class UIAFreshness(StrEnum):
    """A5.03 records a point-in-time read, never a live element promise."""

    POINT_IN_TIME = "point_in_time"


class UIAPropertyName(StrEnum):
    RUNTIME_ID = "runtime_id"
    BOUNDING_RECTANGLE = "bounding_rectangle"
    PROCESS_ID = "process_id"
    CONTROL_TYPE = "control_type"
    NAME = "name"
    HAS_KEYBOARD_FOCUS = "has_keyboard_focus"
    IS_KEYBOARD_FOCUSABLE = "is_keyboard_focusable"
    IS_ENABLED = "is_enabled"
    AUTOMATION_ID = "automation_id"
    NATIVE_WINDOW_HANDLE = "native_window_handle"
    IS_OFFSCREEN = "is_offscreen"
    VALUE = "value"


class UIAPatternName(StrEnum):
    """Pattern-availability metadata only; no pattern object is exposed."""

    DOCK = "dock"
    EXPAND_COLLAPSE = "expand_collapse"
    GRID_ITEM = "grid_item"
    GRID = "grid"
    INVOKE = "invoke"
    MULTIPLE_VIEW = "multiple_view"
    RANGE_VALUE = "range_value"
    SCROLL = "scroll"
    SCROLL_ITEM = "scroll_item"
    SELECTION_ITEM = "selection_item"
    SELECTION = "selection"
    TABLE = "table"
    TABLE_ITEM = "table_item"
    TEXT = "text"
    TOGGLE = "toggle"
    TRANSFORM = "transform"
    VALUE = "value"
    WINDOW = "window"


@dataclass(frozen=True, slots=True)
class UIATreeLimits:
    """Mandatory finite traversal limits; no unlimited mode exists."""

    max_depth: int = 8
    max_nodes: int = 512

    def __post_init__(self) -> None:
        if type(self.max_depth) is not int:
            raise TypeError("max_depth must be an int")
        if not 0 <= self.max_depth <= _MAX_DEPTH:
            raise ValueError(f"max_depth must be between 0 and {_MAX_DEPTH}")
        if type(self.max_nodes) is not int:
            raise TypeError("max_nodes must be an int")
        if not 1 <= self.max_nodes <= _MAX_NODES:
            raise ValueError(f"max_nodes must be between 1 and {_MAX_NODES}")


@dataclass(frozen=True, slots=True)
class UIAPropertyObservation:
    """One normalized property observation; text remains inert data."""

    name: UIAPropertyName
    status: UIAObservationStatus
    value: JsonValue | None
    hresult: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, UIAPropertyName):
            raise TypeError("name must be a UIAPropertyName")
        if not isinstance(self.status, UIAObservationStatus):
            raise TypeError("status must be a UIAObservationStatus")
        if self.status is UIAObservationStatus.AVAILABLE:
            if self.value is None:
                raise ValueError("available property must carry a value")
            if self.hresult is not None:
                raise ValueError("available property cannot carry an HRESULT")
        elif self.value is not None:
            raise ValueError("non-available property cannot carry a value")
        if self.status in {UIAObservationStatus.NATIVE_ERROR, UIAObservationStatus.VANISHED}:
            if type(self.hresult) is not int:
                raise TypeError("native-error/vanished property requires integer HRESULT")
        elif self.hresult is not None:
            raise ValueError("HRESULT is valid only for native-error/vanished properties")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name.value,
            "status": self.status.value,
            "value": self.value,
            "hresult": self.hresult,
        }


@dataclass(frozen=True, slots=True)
class UIAPatternObservation:
    """Observation-only metadata describing whether UIA advertises a pattern."""

    name: UIAPatternName
    status: UIAObservationStatus
    supported: bool | None
    hresult: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, UIAPatternName):
            raise TypeError("name must be a UIAPatternName")
        if not isinstance(self.status, UIAObservationStatus):
            raise TypeError("status must be a UIAObservationStatus")
        if self.status is UIAObservationStatus.AVAILABLE:
            if type(self.supported) is not bool:
                raise TypeError("available pattern observation requires bool supported")
            if self.hresult is not None:
                raise ValueError("available pattern observation cannot carry HRESULT")
        elif self.supported is not None:
            raise ValueError("non-available pattern observation cannot claim support")
        if self.status in {UIAObservationStatus.NATIVE_ERROR, UIAObservationStatus.VANISHED}:
            if type(self.hresult) is not int:
                raise TypeError("native-error/vanished pattern requires integer HRESULT")
        elif self.hresult is not None:
            raise ValueError("HRESULT is valid only for native-error/vanished patterns")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name.value,
            "status": self.status.value,
            "supported": self.supported,
            "hresult": self.hresult,
        }


@dataclass(frozen=True, slots=True)
class UIAElementReference:
    """Snapshot-local identity with optional native UIA runtime ID."""

    root_window_handle: int
    path: tuple[int, ...]
    runtime_id: tuple[int, ...] | None

    def __post_init__(self) -> None:
        if type(self.root_window_handle) is not int or self.root_window_handle <= 0:
            raise ValueError("root_window_handle must be a positive int")
        if not isinstance(self.path, tuple):
            raise TypeError("path must be a tuple")
        if any(type(index) is not int or index < 0 for index in self.path):
            raise ValueError("path entries must be non-negative ints")
        if self.runtime_id is not None:
            if not isinstance(self.runtime_id, tuple) or not self.runtime_id:
                raise ValueError("runtime_id must be a non-empty tuple when present")
            if any(type(item) is not int for item in self.runtime_id):
                raise TypeError("runtime_id entries must be ints")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "root_window_handle": self.root_window_handle,
            "path": list(self.path),
            "runtime_id": list(self.runtime_id) if self.runtime_id is not None else None,
        }


@dataclass(frozen=True, slots=True)
class UIANativeError:
    """Traversal error preserved as inert snapshot diagnostics."""

    operation: str
    hresult: int
    element_path: tuple[int, ...] | None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, str) or not self.operation:
            raise TypeError("operation must be a non-empty string")
        if type(self.hresult) is not int:
            raise TypeError("hresult must be an int")
        if self.element_path is not None:
            if not isinstance(self.element_path, tuple):
                raise TypeError("element_path must be a tuple")
            if any(type(item) is not int or item < 0 for item in self.element_path):
                raise ValueError("element_path entries must be non-negative ints")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "operation": self.operation,
            "hresult": self.hresult,
            "element_path": list(self.element_path) if self.element_path is not None else None,
        }


@dataclass(frozen=True, slots=True)
class UIAElementSnapshot:
    """One immutable UIA element observation in a bounded tree snapshot."""

    reference: UIAElementReference
    parent_path: tuple[int, ...] | None
    child_paths: tuple[tuple[int, ...], ...]
    state: UIAElementState
    properties: tuple[UIAPropertyObservation, ...]
    patterns: tuple[UIAPatternObservation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.reference, UIAElementReference):
            raise TypeError("reference must be a UIAElementReference")
        if self.parent_path is not None:
            if not isinstance(self.parent_path, tuple):
                raise TypeError("parent_path must be a tuple")
            if any(type(item) is not int or item < 0 for item in self.parent_path):
                raise ValueError("parent_path entries must be non-negative ints")
        if not isinstance(self.child_paths, tuple):
            raise TypeError("child_paths must be a tuple")
        if any(not isinstance(path, tuple) for path in self.child_paths):
            raise TypeError("child_paths entries must be tuples")
        if not isinstance(self.state, UIAElementState):
            raise TypeError("state must be a UIAElementState")
        if not isinstance(self.properties, tuple):
            raise TypeError("properties must be a tuple")
        if tuple(item.name for item in self.properties) != tuple(UIAPropertyName):
            raise ValueError("properties must contain every UIAPropertyName exactly once in order")
        if not isinstance(self.patterns, tuple):
            raise TypeError("patterns must be a tuple")
        if tuple(item.name for item in self.patterns) != tuple(UIAPatternName):
            raise ValueError("patterns must contain every UIAPatternName exactly once in order")

    def property_observation(self, name: UIAPropertyName) -> UIAPropertyObservation:
        """Return one closed-vocabulary property observation without inference."""
        if not isinstance(name, UIAPropertyName):
            raise TypeError("name must be a UIAPropertyName")
        return self.properties[tuple(UIAPropertyName).index(name)]

    @property
    def process_id(self) -> int | None:
        value = self.property_observation(UIAPropertyName.PROCESS_ID).value
        return value if type(value) is int else None

    @property
    def control_type(self) -> int | None:
        value = self.property_observation(UIAPropertyName.CONTROL_TYPE).value
        return value if type(value) is int else None

    @property
    def automation_id(self) -> str | None:
        value = self.property_observation(UIAPropertyName.AUTOMATION_ID).value
        return value if isinstance(value, str) else None

    @property
    def name(self) -> str | None:
        value = self.property_observation(UIAPropertyName.NAME).value
        return value if isinstance(value, str) else None

    @property
    def value(self) -> str | None:
        value = self.property_observation(UIAPropertyName.VALUE).value
        return value if isinstance(value, str) else None

    @property
    def is_enabled(self) -> bool | None:
        value = self.property_observation(UIAPropertyName.IS_ENABLED).value
        return value if type(value) is bool else None

    @property
    def is_offscreen(self) -> bool | None:
        value = self.property_observation(UIAPropertyName.IS_OFFSCREEN).value
        return value if type(value) is bool else None

    @property
    def is_visible(self) -> bool | None:
        """Positive form of UIA IsOffscreen, not a pixel-visibility claim."""
        offscreen = self.is_offscreen
        return None if offscreen is None else not offscreen

    @property
    def has_keyboard_focus(self) -> bool | None:
        value = self.property_observation(UIAPropertyName.HAS_KEYBOARD_FOCUS).value
        return value if type(value) is bool else None

    @property
    def is_keyboard_focusable(self) -> bool | None:
        value = self.property_observation(UIAPropertyName.IS_KEYBOARD_FOCUSABLE).value
        return value if type(value) is bool else None

    @property
    def native_window_handle(self) -> int | None:
        value = self.property_observation(UIAPropertyName.NATIVE_WINDOW_HANDLE).value
        return value if type(value) is int else None

    @property
    def bounding_rectangle(self) -> tuple[float, float, float, float] | None:
        value = self.property_observation(UIAPropertyName.BOUNDING_RECTANGLE).value
        if not isinstance(value, list) or len(value) != 4:
            return None
        numbers: list[float] = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return None
            number = float(item)
            if not math.isfinite(number):
                return None
            numbers.append(number)
        return (numbers[0], numbers[1], numbers[2], numbers[3])

    @property
    def supported_patterns(self) -> tuple[UIAPatternName, ...]:
        return tuple(
            pattern.name
            for pattern in self.patterns
            if pattern.status is UIAObservationStatus.AVAILABLE and pattern.supported is True
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "reference": self.reference.to_dict(),
            "parent_path": list(self.parent_path) if self.parent_path is not None else None,
            "child_paths": [list(path) for path in self.child_paths],
            "state": self.state.value,
            "properties": [item.to_dict() for item in self.properties],
            "patterns": [item.to_dict() for item in self.patterns],
            "supported_patterns": [item.value for item in self.supported_patterns],
            "is_visible": self.is_visible,
        }


@dataclass(frozen=True, slots=True)
class UIATreeSnapshot:
    """Immutable point-in-time bounded tree observation."""

    root_window_handle: int
    captured_at: datetime
    freshness: UIAFreshness
    limits: UIATreeLimits
    elements: tuple[UIAElementSnapshot, ...]
    errors: tuple[UIANativeError, ...]
    truncated_by_depth: bool
    truncated_by_nodes: bool

    def __post_init__(self) -> None:
        if type(self.root_window_handle) is not int or self.root_window_handle <= 0:
            raise ValueError("root_window_handle must be a positive int")
        if not isinstance(self.captured_at, datetime) or self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        if not isinstance(self.freshness, UIAFreshness):
            raise TypeError("freshness must be a UIAFreshness")
        if not isinstance(self.limits, UIATreeLimits):
            raise TypeError("limits must be UIATreeLimits")
        if not isinstance(self.elements, tuple):
            raise TypeError("elements must be a tuple")
        if not isinstance(self.errors, tuple):
            raise TypeError("errors must be a tuple")
        if type(self.truncated_by_depth) is not bool or type(self.truncated_by_nodes) is not bool:
            raise TypeError("truncation flags must be bools")
        if len(self.elements) > self.limits.max_nodes:
            raise ValueError("elements exceed max_nodes")

        paths = tuple(element.reference.path for element in self.elements)
        if len(paths) != len(set(paths)):
            raise ValueError("element paths must be unique")
        if self.elements and paths[0] != ():
            raise ValueError("first element must be root path")
        path_set = set(paths)
        for element in self.elements:
            if element.reference.root_window_handle != self.root_window_handle:
                raise ValueError("all references must share root_window_handle")
            if len(element.reference.path) > self.limits.max_depth:
                raise ValueError("element path exceeds max_depth")
            if element.parent_path is not None and element.parent_path not in path_set:
                raise ValueError("parent_path must reference an element in snapshot")
            if any(path not in path_set for path in element.child_paths):
                raise ValueError("child_paths must reference elements in snapshot")

    @property
    def node_count(self) -> int:
        return len(self.elements)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "root_window_handle": self.root_window_handle,
            "captured_at": self.captured_at.isoformat(),
            "freshness": self.freshness.value,
            "limits": {
                "max_depth": self.limits.max_depth,
                "max_nodes": self.limits.max_nodes,
            },
            "node_count": self.node_count,
            "truncated_by_depth": self.truncated_by_depth,
            "truncated_by_nodes": self.truncated_by_nodes,
            "elements": [element.to_dict() for element in self.elements],
            "errors": [error.to_dict() for error in self.errors],
        }


class NativeUIATreeSurface(Protocol):
    """Injected read-only native adapter seam used by A5.03."""

    def inspect_window(
        self,
        window_handle: int,
        limits: UIATreeLimits,
    ) -> Result[_uia_native.RawUIATree, AgentXError]: ...


class UIAutomationNativeSurface:
    """Default adapter over the isolated native UIA seam."""

    __slots__ = ()

    def inspect_window(
        self,
        window_handle: int,
        limits: UIATreeLimits,
    ) -> Result[_uia_native.RawUIATree, AgentXError]:
        return _uia_native.inspect_uia_tree_raw(
            window_handle,
            max_depth=limits.max_depth,
            max_nodes=limits.max_nodes,
        )


def _invalid_native_data(message: str) -> AgentXError:
    return AgentXError(
        code=UIA_INVALID_NATIVE_DATA_ERROR_CODE,
        message=message,
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
        details={},
    )


def _surface_exception(exc: Exception) -> AgentXError:
    return AgentXError(
        code=UIA_SURFACE_EXCEPTION_ERROR_CODE,
        message="Windows UI Automation adapter raised while reading the tree",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
        details={"exception_type": type(exc).__name__},
    )


def _status_from_raw(raw: _uia_native.RawUIAProperty) -> UIAObservationStatus:
    hresult: object = raw.hresult
    unavailable: object = raw.unavailable
    if hresult is not None:
        if type(hresult) is not int:
            return UIAObservationStatus.INVALID
        if hresult == _uia_native.UIA_ELEMENT_NOT_AVAILABLE_HRESULT:
            return UIAObservationStatus.VANISHED
        return UIAObservationStatus.NATIVE_ERROR
    if type(unavailable) is not bool:
        return UIAObservationStatus.INVALID
    if unavailable:
        return UIAObservationStatus.UNAVAILABLE
    return UIAObservationStatus.AVAILABLE


def _valid_runtime_id(value: object) -> list[JsonValue] | None:
    if not isinstance(value, tuple) or not value:
        return None
    runtime_id: list[JsonValue] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        runtime_id.append(item)
    return runtime_id


def _valid_rectangle(value: object) -> list[JsonValue] | None:
    if not isinstance(value, tuple) or len(value) != 4:
        return None
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        number = float(item)
        if not math.isfinite(number):
            return None
        numbers.append(number)
    if numbers[2] < 0 or numbers[3] < 0:
        return None
    normalized_numbers: list[JsonValue] = [*numbers]
    return normalized_numbers


def _normalized_value(name: UIAPropertyName, value: object) -> JsonValue | None:
    if name is UIAPropertyName.RUNTIME_ID:
        return _valid_runtime_id(value)
    if name is UIAPropertyName.BOUNDING_RECTANGLE:
        return _valid_rectangle(value)
    if name in {
        UIAPropertyName.PROCESS_ID,
        UIAPropertyName.CONTROL_TYPE,
        UIAPropertyName.NATIVE_WINDOW_HANDLE,
    }:
        return value if type(value) is int and value >= 0 else None
    if name in {
        UIAPropertyName.HAS_KEYBOARD_FOCUS,
        UIAPropertyName.IS_KEYBOARD_FOCUSABLE,
        UIAPropertyName.IS_ENABLED,
        UIAPropertyName.IS_OFFSCREEN,
    }:
        return value if type(value) is bool else None
    if name in {UIAPropertyName.NAME, UIAPropertyName.AUTOMATION_ID, UIAPropertyName.VALUE}:
        if not isinstance(value, str) or len(value) > _MAX_TEXT_CHARS:
            return None
        return value
    assert_never(name)


def _normalize_property(
    name: UIAPropertyName,
    raw_properties: tuple[_uia_native.RawUIAProperty, ...],
) -> UIAPropertyObservation:
    matches = tuple(item for item in raw_properties if item.name == name.value)
    if len(matches) != 1:
        return UIAPropertyObservation(name=name, status=UIAObservationStatus.INVALID, value=None)
    raw = matches[0]
    status = _status_from_raw(raw)
    if status is not UIAObservationStatus.AVAILABLE:
        hresult = raw.hresult if type(raw.hresult) is int else None
        if status not in {UIAObservationStatus.NATIVE_ERROR, UIAObservationStatus.VANISHED}:
            hresult = None
        return UIAPropertyObservation(name=name, status=status, value=None, hresult=hresult)
    value = _normalized_value(name, raw.value)
    if value is None:
        return UIAPropertyObservation(name=name, status=UIAObservationStatus.INVALID, value=None)
    return UIAPropertyObservation(name=name, status=status, value=value)


def _normalize_pattern(
    name: UIAPatternName,
    raw_patterns: tuple[_uia_native.RawUIAProperty, ...],
) -> UIAPatternObservation:
    matches = tuple(item for item in raw_patterns if item.name == name.value)
    if len(matches) != 1:
        return UIAPatternObservation(name=name, status=UIAObservationStatus.INVALID, supported=None)
    raw = matches[0]
    status = _status_from_raw(raw)
    if status is not UIAObservationStatus.AVAILABLE:
        hresult = raw.hresult if type(raw.hresult) is int else None
        if status not in {UIAObservationStatus.NATIVE_ERROR, UIAObservationStatus.VANISHED}:
            hresult = None
        return UIAPatternObservation(
            name=name,
            status=status,
            supported=None,
            hresult=hresult,
        )
    if type(raw.value) is not bool:
        return UIAPatternObservation(name=name, status=UIAObservationStatus.INVALID, supported=None)
    return UIAPatternObservation(name=name, status=status, supported=raw.value)


def _element_state(
    properties: tuple[UIAPropertyObservation, ...],
    patterns: tuple[UIAPatternObservation, ...],
) -> UIAElementState:
    statuses = tuple(item.status for item in properties) + tuple(item.status for item in patterns)
    if UIAObservationStatus.VANISHED in statuses:
        return UIAElementState.VANISHED
    if any(
        status in {UIAObservationStatus.NATIVE_ERROR, UIAObservationStatus.INVALID}
        for status in statuses
    ):
        return UIAElementState.PARTIAL
    return UIAElementState.AVAILABLE


def _object_tuple(value: object) -> tuple[object, ...] | None:
    if not isinstance(value, tuple):
        return None
    return value


def _raw_property_tuple(value: object) -> tuple[_uia_native.RawUIAProperty, ...] | None:
    items = _object_tuple(value)
    if items is None:
        return None
    properties: list[_uia_native.RawUIAProperty] = []
    for item in items:
        if not isinstance(item, _uia_native.RawUIAProperty):
            return None
        properties.append(item)
    return tuple(properties)


def _runtime_id_tuple(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, list) or not value:
        return None
    runtime_id: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        runtime_id.append(item)
    return tuple(runtime_id)


def _normalize_tree(
    raw: object,
    *,
    root_window_handle: int,
    captured_at: datetime,
    limits: UIATreeLimits,
) -> Result[UIATreeSnapshot, AgentXError]:
    if not isinstance(raw, _uia_native.RawUIATree):
        return Result.failure(_invalid_native_data("native UIA surface returned wrong tree type"))

    raw_elements = _object_tuple(raw.elements)
    raw_errors = _object_tuple(raw.errors)
    if raw_elements is None or raw_errors is None:
        return Result.failure(_invalid_native_data("native UIA tree collections are malformed"))

    truncated_by_depth_value: object = raw.truncated_by_depth
    truncated_by_nodes_value: object = raw.truncated_by_nodes
    if type(truncated_by_depth_value) is not bool or type(truncated_by_nodes_value) is not bool:
        return Result.failure(
            _invalid_native_data("native UIA tree has malformed truncation flags")
        )
    truncated_by_depth = truncated_by_depth_value
    truncated_by_nodes = truncated_by_nodes_value
    if len(raw_elements) > limits.max_nodes:
        return Result.failure(_invalid_native_data("native UIA tree exceeded max_nodes"))

    paths_by_sequence: dict[int, tuple[int, ...]] = {}
    parent_by_sequence: dict[int, tuple[int, ...] | None] = {}
    parts: list[
        tuple[
            UIAElementReference,
            tuple[int, ...] | None,
            tuple[UIAPropertyObservation, ...],
            tuple[UIAPatternObservation, ...],
        ]
    ] = []

    for expected_sequence, element_value in enumerate(raw_elements):
        if not isinstance(element_value, _uia_native.RawUIAElement):
            return Result.failure(
                _invalid_native_data("native UIA tree contains wrong element type")
            )
        element = element_value

        sequence_value: object = element.sequence
        if type(sequence_value) is not int or sequence_value != expected_sequence:
            return Result.failure(
                _invalid_native_data("native UIA element sequence is not contiguous")
            )
        depth_value: object = element.depth
        if type(depth_value) is not int or not 0 <= depth_value <= limits.max_depth:
            return Result.failure(_invalid_native_data("native UIA element depth is invalid"))
        depth = depth_value
        child_index_value: object = element.child_index
        if type(child_index_value) is not int or child_index_value < 0:
            return Result.failure(_invalid_native_data("native UIA child index is invalid"))
        child_index = child_index_value

        properties = _raw_property_tuple(element.properties)
        patterns_raw = _raw_property_tuple(element.pattern_properties)
        if properties is None or patterns_raw is None:
            return Result.failure(
                _invalid_native_data("native UIA property collections are malformed")
            )

        parent_sequence_value: object = element.parent_sequence
        if expected_sequence == 0:
            if parent_sequence_value is not None or depth != 0:
                return Result.failure(
                    _invalid_native_data("native UIA root relationship is invalid")
                )
            path: tuple[int, ...] = ()
            parent_path: tuple[int, ...] | None = None
        else:
            if (
                type(parent_sequence_value) is not int
                or parent_sequence_value not in paths_by_sequence
            ):
                return Result.failure(_invalid_native_data("native UIA parent sequence is invalid"))
            parent_sequence = parent_sequence_value
            parent_path = paths_by_sequence[parent_sequence]
            path = (*parent_path, child_index)
            if len(path) != depth:
                return Result.failure(
                    _invalid_native_data("native UIA depth/path relationship is invalid")
                )
            if path in paths_by_sequence.values():
                return Result.failure(_invalid_native_data("native UIA element path is duplicated"))

        normalized_properties = tuple(
            _normalize_property(name, properties) for name in UIAPropertyName
        )
        normalized_patterns = tuple(
            _normalize_pattern(name, patterns_raw) for name in UIAPatternName
        )
        runtime_value = normalized_properties[
            tuple(UIAPropertyName).index(UIAPropertyName.RUNTIME_ID)
        ].value
        runtime_id = _runtime_id_tuple(runtime_value)
        reference = UIAElementReference(
            root_window_handle=root_window_handle,
            path=path,
            runtime_id=runtime_id,
        )
        paths_by_sequence[expected_sequence] = path
        parent_by_sequence[expected_sequence] = parent_path
        parts.append((reference, parent_path, normalized_properties, normalized_patterns))

    child_paths: dict[tuple[int, ...], list[tuple[int, ...]]] = {
        path: [] for path in paths_by_sequence.values()
    }
    for sequence, parent_path in parent_by_sequence.items():
        if parent_path is not None:
            child_paths[parent_path].append(paths_by_sequence[sequence])

    elements = tuple(
        UIAElementSnapshot(
            reference=reference,
            parent_path=parent_path,
            child_paths=tuple(child_paths[reference.path]),
            state=_element_state(properties, patterns),
            properties=properties,
            patterns=patterns,
        )
        for reference, parent_path, properties, patterns in parts
    )

    errors: list[UIANativeError] = []
    for error_value in raw_errors:
        if not isinstance(error_value, _uia_native.RawUIAError):
            return Result.failure(_invalid_native_data("native UIA tree contains wrong error type"))
        error = error_value
        operation_value: object = error.operation
        hresult_value: object = error.hresult
        if (
            not isinstance(operation_value, str)
            or not operation_value
            or type(hresult_value) is not int
        ):
            return Result.failure(_invalid_native_data("native UIA error is malformed"))
        operation = operation_value
        hresult = hresult_value
        error_path: tuple[int, ...] | None = None
        error_sequence_value: object = error.sequence
        if error_sequence_value is not None:
            if type(error_sequence_value) is not int or error_sequence_value not in paths_by_sequence:
                return Result.failure(
                    _invalid_native_data("native UIA error references unknown element")
                )
            error_path = paths_by_sequence[error_sequence_value]
        errors.append(UIANativeError(operation=operation, hresult=hresult, element_path=error_path))

    return Result.success(
        UIATreeSnapshot(
            root_window_handle=root_window_handle,
            captured_at=captured_at,
            freshness=UIAFreshness.POINT_IN_TIME,
            limits=limits,
            elements=elements,
            errors=tuple(errors),
            truncated_by_depth=truncated_by_depth,
            truncated_by_nodes=truncated_by_nodes,
        )
    )


class WindowsUIATreeInspection:
    """Read one fresh bounded UIA tree; expose no action-capable surface."""

    __slots__ = ("_clock", "_native_surface", "_support")

    _clock: Callable[[], datetime]
    _native_surface: NativeUIATreeSurface
    _support: WindowsSupport

    def __init__(
        self,
        support: WindowsSupport,
        *,
        native_surface: NativeUIATreeSurface | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(support, WindowsSupport):
            raise TypeError("support must be WindowsSupport")
        object.__setattr__(
            self,
            "_native_surface",
            native_surface if native_surface is not None else UIAutomationNativeSurface(),
        )
        object.__setattr__(self, "_support", support)
        object.__setattr__(
            self,
            "_clock",
            clock if clock is not None else lambda: datetime.now(UTC),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"WindowsUIATreeInspection is immutable; cannot set {name!r}")

    def inspect(
        self,
        window_handle: int,
        *,
        limits: UIATreeLimits | None = None,
    ) -> Result[UIATreeSnapshot, AgentXError]:
        if type(window_handle) is not int or window_handle <= 0:
            raise ValueError("window_handle must be a positive int")
        selected_limits = limits if limits is not None else UIATreeLimits()
        if not isinstance(selected_limits, UIATreeLimits):
            raise TypeError("limits must be UIATreeLimits")
        if not self._support.is_supported:
            return Result.failure(unsupported_platform_error(self._support))

        captured_at = self._clock()
        if not isinstance(captured_at, datetime) or captured_at.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        try:
            raw_result_object: object = self._native_surface.inspect_window(
                window_handle, selected_limits
            )
        except Exception as exc:  # native/adapter failures never escape this boundary
            return Result.failure(_surface_exception(exc))
        if not isinstance(raw_result_object, Result):
            return Result.failure(_invalid_native_data("native UIA surface returned non-Result"))
        if raw_result_object.is_failure:
            error_object: object = raw_result_object.unwrap_error()
            if not isinstance(error_object, AgentXError):
                return Result.failure(
                    _invalid_native_data("native UIA surface returned invalid failure data")
                )
            return Result.failure(error_object)
        raw_tree_object: object = raw_result_object.unwrap()
        return _normalize_tree(
            raw_tree_object,
            root_window_handle=window_handle,
            captured_at=captured_at,
            limits=selected_limits,
        )
