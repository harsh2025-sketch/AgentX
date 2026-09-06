"""Isolated read-only Windows UI Automation native seam for A5.03.

The public A5.03 contract lives in :mod:`agentx.capabilities.windows.uia_tree`.
This module is deliberately lower-level: it contains the only UI Automation
COM/``ctypes`` knowledge needed to read one bounded control-view tree.

Importing this module is side-effect free on every platform. ``ctypes`` and
Windows DLLs are imported/loaded only inside :func:`inspect_uia_tree_raw`, and
an off-Windows call returns an explicit canonical error instead of importing a
Windows-only dependency.

Every native operation is observational. The seam obtains an element from an
existing HWND, reads current UIA properties, and navigates the control-view
walker. It exposes no invoke/value-setting/focus/input API and performs no
fallback to vision or semantic resolution. UI-provided strings are returned
verbatim as untrusted data.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import Any, Final

from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result

__all__ = [
    "UIA_ELEMENT_NOT_AVAILABLE_HRESULT",
    "UIA_NATIVE_FAILURE_ERROR_CODE",
    "UIA_NATIVE_UNAVAILABLE_ERROR_CODE",
    "RawUIAElement",
    "RawUIAError",
    "RawUIAProperty",
    "RawUIATree",
    "inspect_uia_tree_raw",
    "is_uia_native_available",
]

UIA_NATIVE_UNAVAILABLE_ERROR_CODE: Final[str] = "capabilities.windows.uia.native_unavailable"
UIA_NATIVE_FAILURE_ERROR_CODE: Final[str] = "capabilities.windows.uia.native_failure"

# UIA_E_ELEMENTNOTAVAILABLE from UIAutomationClient.h, represented as the
# signed 32-bit HRESULT returned by ctypes.c_long on Windows.
UIA_ELEMENT_NOT_AVAILABLE_HRESULT: Final[int] = -2147220991

_WIN32_PLATFORM: Final[str] = "win32"
_MAX_NATIVE_NODES: Final[int] = 10_000
_MAX_NATIVE_DEPTH: Final[int] = 64

# IUIAutomation property identifiers. These are stable UI Automation contract
# identifiers, not semantic guesses about application content.
_PROPERTY_SPECS: Final[tuple[tuple[str, int], ...]] = (
    ("runtime_id", 30000),
    ("bounding_rectangle", 30001),
    ("process_id", 30002),
    ("control_type", 30003),
    ("name", 30005),
    ("has_keyboard_focus", 30008),
    ("is_keyboard_focusable", 30009),
    ("is_enabled", 30010),
    ("automation_id", 30011),
    ("native_window_handle", 30020),
    ("is_offscreen", 30022),
)

# Pattern-availability properties are observations only. A5.03 never obtains
# a pattern object and therefore cannot invoke or mutate through one.
_PATTERN_SPECS: Final[tuple[tuple[str, int], ...]] = (
    ("dock", 30027),
    ("expand_collapse", 30028),
    ("grid_item", 30029),
    ("grid", 30030),
    ("invoke", 30031),
    ("multiple_view", 30032),
    ("range_value", 30033),
    ("scroll", 30034),
    ("scroll_item", 30035),
    ("selection_item", 30036),
    ("selection", 30037),
    ("table", 30038),
    ("table_item", 30039),
    ("text", 30040),
    ("toggle", 30041),
    ("transform", 30042),
    ("value", 30043),
    ("window", 30044),
)

_UIA_VALUE_VALUE_PROPERTY_ID: Final[int] = 30045


@dataclass(frozen=True, slots=True)
class RawUIAProperty:
    """One verbatim property read from a UIA element.

    ``hresult`` is ``None`` when the COM call itself succeeded. ``unavailable``
    means UIA returned an empty/not-supported variant rather than a value.
    Values are intentionally typed as ``object`` here so the public boundary
    can reject malformed native/fake data deterministically.
    """

    name: str
    value: object | None
    hresult: int | None = None
    unavailable: bool = False


@dataclass(frozen=True, slots=True)
class RawUIAError:
    """One native navigation/read error captured as inert diagnostic data."""

    operation: str
    hresult: int
    sequence: int | None


@dataclass(frozen=True, slots=True)
class RawUIAElement:
    """One native element record in bounded deterministic traversal order."""

    sequence: int
    parent_sequence: int | None
    depth: int
    child_index: int
    properties: tuple[RawUIAProperty, ...]
    pattern_properties: tuple[RawUIAProperty, ...]


@dataclass(frozen=True, slots=True)
class RawUIATree:
    """Raw bounded UIA traversal result before public normalization."""

    elements: tuple[RawUIAElement, ...]
    errors: tuple[RawUIAError, ...]
    truncated_by_depth: bool
    truncated_by_nodes: bool


def is_uia_native_available() -> bool:
    """Return whether the native UIA seam can be attempted on this host."""
    return sys.platform == _WIN32_PLATFORM


def _native_error(*, operation: str, hresult: int | None = None) -> AgentXError:
    details: dict[str, object] = {"operation": operation}
    if hresult is not None:
        details["hresult"] = hresult
    return AgentXError(
        code=UIA_NATIVE_FAILURE_ERROR_CODE,
        message=(
            f"Windows UI Automation read failed during {operation}"
            if hresult is None
            else f"Windows UI Automation read failed during {operation} with HRESULT {hresult}"
        ),
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
        details=details,
    )


def _unavailable_error() -> AgentXError:
    return AgentXError(
        code=UIA_NATIVE_UNAVAILABLE_ERROR_CODE,
        message=f"Windows UI Automation native surface is unavailable on {sys.platform!r}",
        category=ErrorCategory.PRECONDITION,
        retryability=Retryability.NON_RETRYABLE,
        details={"sys_platform": sys.platform},
    )


def _require_bound(value: object, *, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def inspect_uia_tree_raw(
    window_handle: int,
    *,
    max_depth: int,
    max_nodes: int,
) -> Result[RawUIATree, AgentXError]:
    """Read one bounded UIA control-view tree rooted at ``window_handle``.

    The traversal is iterative and bounded by both depth and total node count.
    A disappearing element/property/navigation edge is represented as data and
    never triggers an uncontrolled retry. Unexpected native exceptions become
    one canonical operation-level failure.
    """
    _require_bound(window_handle, name="window_handle", minimum=1, maximum=2**63 - 1)
    _require_bound(max_depth, name="max_depth", minimum=0, maximum=_MAX_NATIVE_DEPTH)
    _require_bound(max_nodes, name="max_nodes", minimum=1, maximum=_MAX_NATIVE_NODES)
    if not is_uia_native_available():
        return Result.failure(_unavailable_error())

    try:
        return _inspect_uia_tree_windows(
            window_handle=window_handle,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
    except Exception as exc:  # pragma: no cover - defensive native boundary
        return Result.failure(
            AgentXError(
                code=UIA_NATIVE_FAILURE_ERROR_CODE,
                message="Windows UI Automation read raised an unexpected native exception",
                category=ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                details={"operation": "tree inspection", "exception_type": type(exc).__name__},
            )
        )


def _inspect_uia_tree_windows(
    *,
    window_handle: int,
    max_depth: int,
    max_nodes: int,
) -> Result[RawUIATree, AgentXError]:
    """Windows-only implementation. All native imports are intentionally local."""
    import ctypes
    import uuid

    HRESULT = ctypes.c_long
    ULONG = ctypes.c_ulong
    PROPERTYID = ctypes.c_int
    CLSCTX_INPROC_SERVER = 0x1
    COINIT_APARTMENTTHREADED = 0x2
    RPC_E_CHANGED_MODE = -2147417850
    VT_EMPTY = 0
    VT_NULL = 1
    VT_I4 = 3
    VT_R8 = 5
    VT_BSTR = 8
    VT_DISPATCH = 9
    VT_BOOL = 11
    VT_UNKNOWN = 13
    VT_UI4 = 19
    VT_ARRAY = 0x2000

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        ]

        @classmethod
        def parse(cls, text: str) -> GUID:
            parsed = uuid.UUID(text)
            fields = parsed.fields
            data4 = bytes((fields[3], fields[4])) + fields[5].to_bytes(6, "big")
            return cls(fields[0], fields[1], fields[2], (ctypes.c_ubyte * 8)(*data4))

    class VARIANT_VALUE(ctypes.Union):
        _fields_ = [
            ("lVal", ctypes.c_long),
            ("ulVal", ctypes.c_ulong),
            ("boolVal", ctypes.c_short),
            ("dblVal", ctypes.c_double),
            ("bstrVal", ctypes.c_void_p),
            ("parray", ctypes.c_void_p),
            ("punkVal", ctypes.c_void_p),
            ("pdispVal", ctypes.c_void_p),
        ]

    class VARIANT(ctypes.Structure):
        _anonymous_ = ("value",)
        _fields_ = [
            ("vt", ctypes.c_ushort),
            ("wReserved1", ctypes.c_ushort),
            ("wReserved2", ctypes.c_ushort),
            ("wReserved3", ctypes.c_ushort),
            ("value", VARIANT_VALUE),
        ]

    ole32 = ctypes.WinDLL("ole32")
    oleaut32 = ctypes.WinDLL("oleaut32")

    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    ole32.CoCreateInstance.restype = HRESULT
    ole32.CoCreateInstance.argtypes = (
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    )
    ole32.CoUninitialize.restype = None
    ole32.CoUninitialize.argtypes = ()

    oleaut32.VariantClear.restype = HRESULT
    oleaut32.VariantClear.argtypes = (ctypes.POINTER(VARIANT),)
    oleaut32.SafeArrayGetDim.restype = ctypes.c_uint
    oleaut32.SafeArrayGetDim.argtypes = (ctypes.c_void_p,)
    oleaut32.SafeArrayGetLBound.restype = HRESULT
    oleaut32.SafeArrayGetLBound.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_long),
    )
    oleaut32.SafeArrayGetUBound.restype = HRESULT
    oleaut32.SafeArrayGetUBound.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_long),
    )
    oleaut32.SafeArrayAccessData.restype = HRESULT
    oleaut32.SafeArrayAccessData.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    )
    oleaut32.SafeArrayUnaccessData.restype = HRESULT
    oleaut32.SafeArrayUnaccessData.argtypes = (ctypes.c_void_p,)

    def failed(hr: int) -> bool:
        return int(hr) < 0

    def method(pointer: Any, index: int, *argtypes: Any) -> Any:
        vtable = ctypes.cast(
            pointer,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        address = vtable[index]
        return ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)(address)

    def release(pointer: Any) -> None:
        if not pointer:
            return
        vtable = ctypes.cast(
            pointer,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        function = ctypes.WINFUNCTYPE(ULONG, ctypes.c_void_p)(vtable[2])
        function(pointer)

    def safearray_values(array: Any, ctype: Any) -> tuple[object, ...] | None:
        if not array or oleaut32.SafeArrayGetDim(array) != 1:
            return None
        lower = ctypes.c_long()
        upper = ctypes.c_long()
        if failed(oleaut32.SafeArrayGetLBound(array, 1, ctypes.byref(lower))):
            return None
        if failed(oleaut32.SafeArrayGetUBound(array, 1, ctypes.byref(upper))):
            return None
        if upper.value < lower.value:
            return ()
        data = ctypes.c_void_p()
        hr = oleaut32.SafeArrayAccessData(array, ctypes.byref(data))
        if failed(hr) or not data:
            return None
        try:
            count = upper.value - lower.value + 1
            values = ctypes.cast(data, ctypes.POINTER(ctype))
            return tuple(values[index] for index in range(count))
        finally:
            oleaut32.SafeArrayUnaccessData(array)

    def variant_value(variant: Any) -> tuple[object | None, bool]:
        vt = int(variant.vt)
        if vt in (VT_EMPTY, VT_NULL, VT_UNKNOWN, VT_DISPATCH):
            return (None, True)
        if vt == VT_I4:
            return (int(variant.lVal), False)
        if vt == VT_UI4:
            return (int(variant.ulVal), False)
        if vt == VT_R8:
            value = float(variant.dblVal)
            return (value if math.isfinite(value) else None, not math.isfinite(value))
        if vt == VT_BOOL:
            return (bool(variant.boolVal), False)
        if vt == VT_BSTR:
            if not variant.bstrVal:
                return ("", False)
            return (ctypes.wstring_at(variant.bstrVal), False)
        if vt == (VT_ARRAY | VT_I4):
            values = safearray_values(variant.parray, ctypes.c_long)
            return (values, values is None)
        if vt == (VT_ARRAY | VT_R8):
            values = safearray_values(variant.parray, ctypes.c_double)
            return (values, values is None)
        return (None, True)

    def read_property(element: Any, name: str, property_id: int) -> RawUIAProperty:
        variant = VARIANT()
        getter = method(element, 10, PROPERTYID, ctypes.POINTER(VARIANT))
        hr = int(getter(element, property_id, ctypes.byref(variant)))
        if failed(hr):
            return RawUIAProperty(name=name, value=None, hresult=hr)
        try:
            value, unavailable = variant_value(variant)
            return RawUIAProperty(name=name, value=value, unavailable=unavailable)
        finally:
            oleaut32.VariantClear(ctypes.byref(variant))

    def read_element(
        element: Any,
        *,
        sequence: int,
        parent_sequence: int | None,
        depth: int,
        child_index: int,
    ) -> RawUIAElement:
        properties = [read_property(element, name, prop_id) for name, prop_id in _PROPERTY_SPECS]
        patterns = [read_property(element, name, prop_id) for name, prop_id in _PATTERN_SPECS]
        value_support = next((item for item in patterns if item.name == "value"), None)
        if value_support is not None and value_support.hresult is None and value_support.value is True:
            properties.append(read_property(element, "value", _UIA_VALUE_VALUE_PROPERTY_ID))
        else:
            properties.append(RawUIAProperty(name="value", value=None, unavailable=True))
        return RawUIAElement(
            sequence=sequence,
            parent_sequence=parent_sequence,
            depth=depth,
            child_index=child_index,
            properties=tuple(properties),
            pattern_properties=tuple(patterns),
        )

    initialized = False
    automation = ctypes.c_void_p()
    walker = ctypes.c_void_p()
    root = ctypes.c_void_p()
    init_hr = int(ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED))
    if init_hr >= 0:
        initialized = True
    elif init_hr != RPC_E_CHANGED_MODE:
        return Result.failure(_native_error(operation="COM initialization", hresult=init_hr))

    try:
        clsid = GUID.parse("ff48dba4-60ef-4201-aa87-54103eef594e")
        iid = GUID.parse("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee")
        create_hr = int(
            ole32.CoCreateInstance(
                ctypes.byref(clsid),
                None,
                CLSCTX_INPROC_SERVER,
                ctypes.byref(iid),
                ctypes.byref(automation),
            )
        )
        if failed(create_hr) or not automation:
            return Result.failure(_native_error(operation="UIAutomation client creation", hresult=create_hr))

        element_from_handle = method(
            automation,
            6,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        root_hr = int(
            element_from_handle(
                automation,
                ctypes.c_void_p(window_handle),
                ctypes.byref(root),
            )
        )
        if failed(root_hr) or not root:
            return Result.failure(_native_error(operation="element from window handle", hresult=root_hr))

        get_control_walker = method(
            automation,
            14,
            ctypes.POINTER(ctypes.c_void_p),
        )
        walker_hr = int(get_control_walker(automation, ctypes.byref(walker)))
        if failed(walker_hr) or not walker:
            return Result.failure(_native_error(operation="control-view walker creation", hresult=walker_hr))

        first_child = method(
            walker,
            4,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        next_sibling = method(
            walker,
            6,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )

        # Stack entries own one COM reference and are always released after
        # processing (or in the final cleanup when the node bound is reached).
        stack: list[tuple[Any, int | None, int, int]] = [(root, None, 0, 0)]
        root = ctypes.c_void_p()
        elements: list[RawUIAElement] = []
        errors: list[RawUIAError] = []
        truncated_depth = False
        truncated_nodes = False

        try:
            while stack:
                if len(elements) >= max_nodes:
                    truncated_nodes = True
                    break

                element, parent_sequence, depth, child_index = stack.pop()
                sequence = len(elements)
                try:
                    elements.append(
                        read_element(
                            element,
                            sequence=sequence,
                            parent_sequence=parent_sequence,
                            depth=depth,
                            child_index=child_index,
                        )
                    )

                    if depth >= max_depth:
                        probe = ctypes.c_void_p()
                        hr = int(first_child(walker, element, ctypes.byref(probe)))
                        if failed(hr):
                            errors.append(
                                RawUIAError(
                                    operation="first_child",
                                    hresult=hr,
                                    sequence=sequence,
                                )
                            )
                        elif probe:
                            truncated_depth = True
                            release(probe)
                        continue

                    remaining_capacity = max_nodes - len(elements) - len(stack)
                    if remaining_capacity <= 0:
                        probe = ctypes.c_void_p()
                        hr = int(first_child(walker, element, ctypes.byref(probe)))
                        if failed(hr):
                            errors.append(
                                RawUIAError(
                                    operation="first_child",
                                    hresult=hr,
                                    sequence=sequence,
                                )
                            )
                        elif probe:
                            truncated_nodes = True
                            release(probe)
                        continue

                    children: list[tuple[Any, int | None, int, int]] = []
                    child = ctypes.c_void_p()
                    hr = int(first_child(walker, element, ctypes.byref(child)))
                    if failed(hr):
                        errors.append(
                            RawUIAError(operation="first_child", hresult=hr, sequence=sequence)
                        )
                        continue

                    next_index = 0
                    while child:
                        if len(children) >= remaining_capacity:
                            truncated_nodes = True
                            release(child)
                            child = ctypes.c_void_p()
                            break
                        children.append((child, sequence, depth + 1, next_index))
                        next_index += 1
                        sibling = ctypes.c_void_p()
                        hr = int(next_sibling(walker, child, ctypes.byref(sibling)))
                        if failed(hr):
                            errors.append(
                                RawUIAError(
                                    operation="next_sibling",
                                    hresult=hr,
                                    sequence=sequence,
                                )
                            )
                            break
                        child = sibling

                    stack.extend(reversed(children))
                finally:
                    release(element)
        finally:
            for pending, _, _, _ in stack:
                release(pending)

        return Result.success(
            RawUIATree(
                elements=tuple(elements),
                errors=tuple(errors),
                truncated_by_depth=truncated_depth,
                truncated_by_nodes=truncated_nodes,
            )
        )
    finally:
        release(root)
        release(walker)
        release(automation)
        if initialized:
            ole32.CoUninitialize()
