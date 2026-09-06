"""Governed Windows keyboard, text-entry, and clipboard capabilities (A5.06).

This module exposes the canonical A5.06 operation surface while keeping native
Win32 details isolated in :mod:`agentx.capabilities.windows._native`.
Availability is not authority: capability instances are ordinary A1.08
``Capability`` implementations and must be registered and executed through the
canonical A1.09/A1.10 path by the composition root.

Text supplied for entry and text read from or written to the clipboard is
untrusted data. It is never interpreted as policy, authority, a selector, or a
verification result. Mutation operations deliberately do not fabricate
verification: native API acceptance is execution evidence only, and A5.10 owns
state-transition verification.

A5.06 does not resolve UIA targets, change focus, invoke controls, open dialogs,
perform visual fallback, register global hotkeys, install hooks, capture
keystrokes, or retain clipboard history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final, Protocol

from agentx.capabilities.abi import (
    CapabilityDescriptor,
    CapabilityIdentity,
    CapabilityName,
    CapabilityObservation,
    CapabilityParams,
    CapabilityPlatform,
    CapabilityPrecondition,
    CapabilityRequest,
    CapabilityScope,
    CapabilityVersion,
    ExecutionResult,
    ResourceEstimate,
    RollbackDeclaration,
    RollbackSupport,
    VerificationResult,
)
from agentx.capabilities.windows import _native
from agentx.capabilities.windows.provider import (
    WINDOWS_PROVIDER_IDENTITY,
    WindowsProviderIdentity,
    WindowsSupport,
    unsupported_platform_error,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk

__all__ = [
    "MAX_CLIPBOARD_TEXT_CHARS",
    "MAX_KEY_REPEAT",
    "MAX_TEXT_ENTRY_CHARS",
    "WINDOWS_CLIPBOARD_CLEAR_IDENTITY",
    "WINDOWS_CLIPBOARD_READ_IDENTITY",
    "WINDOWS_CLIPBOARD_WRITE_IDENTITY",
    "WINDOWS_KEY_INPUT_IDENTITY",
    "WINDOWS_TEXT_ENTRY_IDENTITY",
    "ClipboardClearOutcome",
    "ClipboardReadOutcome",
    "ClipboardWriteOutcome",
    "KeyboardClipboardNativeSurface",
    "KeyInputOutcome",
    "TextEntryOutcome",
    "Win32KeyboardClipboardNativeSurface",
    "WindowsClipboardClearCapability",
    "WindowsClipboardClearParams",
    "WindowsClipboardReadCapability",
    "WindowsClipboardReadParams",
    "WindowsClipboardWriteCapability",
    "WindowsClipboardWriteParams",
    "WindowsKey",
    "WindowsKeyInputCapability",
    "WindowsKeyInputParams",
    "WindowsKeyboardClipboard",
    "WindowsModifier",
    "WindowsTextEntryCapability",
    "WindowsTextEntryParams",
    "clipboard_clear_request",
    "clipboard_read_request",
    "clipboard_write_request",
    "key_input_request",
    "text_entry_request",
]

MAX_TEXT_ENTRY_CHARS: Final[int] = 16_384
MAX_CLIPBOARD_TEXT_CHARS: Final[int] = 262_144
MAX_KEY_REPEAT: Final[int] = 20
_MAX_TEXT_ENTRY_UTF16_UNITS: Final[int] = MAX_TEXT_ENTRY_CHARS * 2

_INCONSISTENT_NATIVE_RECEIPT_ERROR_CODE: Final[str] = (
    "capabilities.windows.keyboard_clipboard.inconsistent_native_receipt"
)
_CLIPBOARD_RESULT_TOO_LARGE_ERROR_CODE: Final[str] = (
    "capabilities.windows.clipboard.read_text.result_too_large"
)


class WindowsModifier(StrEnum):
    """Closed modifier vocabulary for one explicitly requested key chord."""

    CONTROL = "control"
    ALT = "alt"
    SHIFT = "shift"
    WINDOWS = "windows"


class WindowsKey(StrEnum):
    """Closed, layout-stable key vocabulary for A5.06 key input.

    Printable punctuation is intentionally absent because its virtual-key
    interpretation depends on the active keyboard layout. Use text entry for
    textual punctuation instead.
    """

    BACKSPACE = "backspace"
    TAB = "tab"
    ENTER = "enter"
    ESCAPE = "escape"
    SPACE = "space"
    PAGE_UP = "page_up"
    PAGE_DOWN = "page_down"
    END = "end"
    HOME = "home"
    LEFT = "left"
    UP = "up"
    RIGHT = "right"
    DOWN = "down"
    INSERT = "insert"
    DELETE = "delete"

    DIGIT_0 = "0"
    DIGIT_1 = "1"
    DIGIT_2 = "2"
    DIGIT_3 = "3"
    DIGIT_4 = "4"
    DIGIT_5 = "5"
    DIGIT_6 = "6"
    DIGIT_7 = "7"
    DIGIT_8 = "8"
    DIGIT_9 = "9"

    A = "a"
    B = "b"
    C = "c"
    D = "d"
    E = "e"
    F = "f"
    G = "g"
    H = "h"
    I = "i"
    J = "j"
    K = "k"
    L = "l"
    M = "m"
    N = "n"
    O = "o"
    P = "p"
    Q = "q"
    R = "r"
    S = "s"
    T = "t"
    U = "u"
    V = "v"
    W = "w"
    X = "x"
    Y = "y"
    Z = "z"

    F1 = "f1"
    F2 = "f2"
    F3 = "f3"
    F4 = "f4"
    F5 = "f5"
    F6 = "f6"
    F7 = "f7"
    F8 = "f8"
    F9 = "f9"
    F10 = "f10"
    F11 = "f11"
    F12 = "f12"
    F13 = "f13"
    F14 = "f14"
    F15 = "f15"
    F16 = "f16"
    F17 = "f17"
    F18 = "f18"
    F19 = "f19"
    F20 = "f20"
    F21 = "f21"
    F22 = "f22"
    F23 = "f23"
    F24 = "f24"


def _utf16_units(text: str, *, field_name: str) -> int:
    """Return strict UTF-16 code-unit length, rejecting lone surrogates."""
    try:
        encoded = text.encode("utf-16-le")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must contain valid Unicode scalar values") from exc
    return len(encoded) // 2


def _validate_text(
    value: object,
    *,
    field_name: str,
    max_chars: int,
    allow_empty: bool,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not allow_empty and not value:
        raise ValueError(f"{field_name} must not be empty")
    if len(value) > max_chars:
        raise ValueError(f"{field_name} must not exceed {max_chars} characters")
    if "\x00" in value:
        raise ValueError(f"{field_name} must not contain NUL characters")
    _utf16_units(value, field_name=field_name)
    return value


def _validate_repeat(value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"repeat must be an int, got {type(value).__name__}")
    if not 1 <= value <= MAX_KEY_REPEAT:
        raise ValueError(f"repeat must be between 1 and {MAX_KEY_REPEAT}")
    return value


def _normalize_modifiers(value: object) -> tuple[WindowsModifier, ...]:
    if not isinstance(value, tuple):
        raise TypeError("modifiers must be a tuple of WindowsModifier values")
    for modifier in value:
        if not isinstance(modifier, WindowsModifier):
            raise TypeError("modifiers must contain only WindowsModifier values")
    if len(set(value)) != len(value):
        raise ValueError("modifiers must not contain duplicates")
    order = {
        WindowsModifier.CONTROL: 0,
        WindowsModifier.ALT: 1,
        WindowsModifier.SHIFT: 2,
        WindowsModifier.WINDOWS: 3,
    }
    return tuple(sorted(value, key=order.__getitem__))


@dataclass(frozen=True, slots=True)
class WindowsTextEntryParams(CapabilityParams):
    """Typed text-entry parameters; content remains untrusted data."""

    text: str

    def __post_init__(self) -> None:
        _validate_text(
            self.text,
            field_name="text",
            max_chars=MAX_TEXT_ENTRY_CHARS,
            allow_empty=False,
        )
        if _utf16_units(self.text, field_name="text") > _MAX_TEXT_ENTRY_UTF16_UNITS:
            raise ValueError(
                f"text must not exceed {_MAX_TEXT_ENTRY_UTF16_UNITS} UTF-16 code units"
            )

    def to_dict(self) -> dict[str, JsonValue]:
        return {"text": self.text}


@dataclass(frozen=True, slots=True)
class WindowsKeyInputParams(CapabilityParams):
    """One primary key plus an optional explicit modifier chord."""

    key: WindowsKey
    modifiers: tuple[WindowsModifier, ...] = ()
    repeat: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.key, WindowsKey):
            raise TypeError(f"key must be a WindowsKey, got {type(self.key).__name__}")
        object.__setattr__(self, "modifiers", _normalize_modifiers(self.modifiers))
        _validate_repeat(self.repeat)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "key": self.key.value,
            "modifiers": [modifier.value for modifier in self.modifiers],
            "repeat": self.repeat,
        }


@dataclass(frozen=True, slots=True)
class WindowsClipboardReadParams(CapabilityParams):
    """Clipboard text read has no caller-controlled parameters."""

    def to_dict(self) -> dict[str, JsonValue]:
        return {}


@dataclass(frozen=True, slots=True)
class WindowsClipboardWriteParams(CapabilityParams):
    """Typed clipboard Unicode-text write parameters."""

    text: str

    def __post_init__(self) -> None:
        _validate_text(
            self.text,
            field_name="clipboard text",
            max_chars=MAX_CLIPBOARD_TEXT_CHARS,
            allow_empty=True,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {"text": self.text}


@dataclass(frozen=True, slots=True)
class WindowsClipboardClearParams(CapabilityParams):
    """Clipboard clear has no caller-controlled parameters."""

    def to_dict(self) -> dict[str, JsonValue]:
        return {}


@dataclass(frozen=True, slots=True)
class TextEntryOutcome:
    """Native acceptance receipt; not proof of application state."""

    character_count: int
    utf16_units: int
    native_events: int

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "character_count": self.character_count,
            "utf16_units": self.utf16_units,
            "native_events": self.native_events,
        }


@dataclass(frozen=True, slots=True)
class KeyInputOutcome:
    """Native key-injection receipt; not proof of the intended state change."""

    key: WindowsKey
    modifiers: tuple[WindowsModifier, ...]
    repeat: int
    native_events: int

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "key": self.key.value,
            "modifiers": [modifier.value for modifier in self.modifiers],
            "repeat": self.repeat,
            "native_events": self.native_events,
        }


@dataclass(frozen=True, slots=True)
class ClipboardReadOutcome:
    """One bounded CF_UNICODETEXT observation from the Windows clipboard."""

    has_text: bool
    text: str | None

    def __post_init__(self) -> None:
        if type(self.has_text) is not bool:
            raise TypeError("has_text must be bool")
        if self.has_text:
            if not isinstance(self.text, str):
                raise TypeError("text must be a string when has_text is true")
        elif self.text is not None:
            raise ValueError("text must be None when has_text is false")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"has_text": self.has_text, "text": self.text}


@dataclass(frozen=True, slots=True)
class ClipboardWriteOutcome:
    """Native clipboard write receipt; not a state-transition verification."""

    character_count: int
    utf16_units: int

    def to_dict(self) -> dict[str, JsonValue]:
        return {"character_count": self.character_count, "utf16_units": self.utf16_units}


@dataclass(frozen=True, slots=True)
class ClipboardClearOutcome:
    """Native EmptyClipboard acceptance receipt; not verification."""

    api_calls_completed: int = 1

    def __post_init__(self) -> None:
        if self.api_calls_completed != 1:
            raise ValueError("api_calls_completed must be exactly 1")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"api_calls_completed": self.api_calls_completed}


class KeyboardClipboardNativeSurface(Protocol):
    """Mockable native seam; implementations must perform no hidden retries."""

    def send_text(self, text: str) -> Result[_native.RawInputReceipt, AgentXError]: ...

    def send_key(
        self,
        key: str,
        modifiers: tuple[str, ...],
        repeat: int,
    ) -> Result[_native.RawInputReceipt, AgentXError]: ...

    def read_clipboard_text(self) -> Result[_native.RawClipboardRead, AgentXError]: ...

    def write_clipboard_text(self, text: str) -> Result[None, AgentXError]: ...

    def clear_clipboard(self) -> Result[None, AgentXError]: ...


class Win32KeyboardClipboardNativeSurface:
    """Thin adapter onto the isolated stdlib Win32 seam."""

    __slots__ = ()

    def send_text(self, text: str) -> Result[_native.RawInputReceipt, AgentXError]:
        return _native.send_text_raw(text)

    def send_key(
        self,
        key: str,
        modifiers: tuple[str, ...],
        repeat: int,
    ) -> Result[_native.RawInputReceipt, AgentXError]:
        return _native.send_key_raw(key, modifiers, repeat)

    def read_clipboard_text(self) -> Result[_native.RawClipboardRead, AgentXError]:
        return _native.read_clipboard_text_raw()

    def write_clipboard_text(self, text: str) -> Result[None, AgentXError]:
        return _native.write_clipboard_text_raw(text)

    def clear_clipboard(self) -> Result[None, AgentXError]:
        return _native.clear_clipboard_raw()


class WindowsKeyboardClipboard:
    """Pure-Python A5.06 operation boundary around one explicit native seam."""

    __slots__ = ("_native_surface", "_support")

    def __init__(
        self,
        support: WindowsSupport,
        *,
        native_surface: KeyboardClipboardNativeSurface | None = None,
    ) -> None:
        if not isinstance(support, WindowsSupport):
            raise TypeError(f"support must be a WindowsSupport, got {type(support).__name__}")
        object.__setattr__(self, "_support", support)
        object.__setattr__(
            self,
            "_native_surface",
            native_surface if native_surface is not None else Win32KeyboardClipboardNativeSurface(),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"WindowsKeyboardClipboard is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"WindowsKeyboardClipboard is immutable; cannot delete {name!r}")

    @property
    def identity(self) -> WindowsProviderIdentity:
        return WINDOWS_PROVIDER_IDENTITY

    @property
    def support(self) -> WindowsSupport:
        return self._support

    def _unsupported(self) -> AgentXError | None:
        return None if self._support.is_supported else unsupported_platform_error(self._support)

    def enter_text(self, text: str) -> Result[TextEntryOutcome, AgentXError]:
        validated = _validate_text(
            text,
            field_name="text",
            max_chars=MAX_TEXT_ENTRY_CHARS,
            allow_empty=False,
        )
        units = _utf16_units(validated, field_name="text")
        if units > _MAX_TEXT_ENTRY_UTF16_UNITS:
            raise ValueError(
                f"text must not exceed {_MAX_TEXT_ENTRY_UTF16_UNITS} UTF-16 code units"
            )
        unsupported = self._unsupported()
        if unsupported is not None:
            return Result.failure(unsupported)
        native = self._native_surface.send_text(validated)
        if native.is_failure:
            return Result.failure(native.unwrap_error())
        receipt = native.unwrap()
        expected = units * 2
        if receipt.events_requested != expected or receipt.events_inserted != expected:
            return Result.failure(_inconsistent_receipt("text entry", expected, receipt))
        return Result.success(
            TextEntryOutcome(
                character_count=len(validated),
                utf16_units=units,
                native_events=receipt.events_inserted,
            )
        )

    def press_key(
        self,
        key: WindowsKey,
        modifiers: tuple[WindowsModifier, ...] = (),
        repeat: int = 1,
    ) -> Result[KeyInputOutcome, AgentXError]:
        if not isinstance(key, WindowsKey):
            raise TypeError(f"key must be a WindowsKey, got {type(key).__name__}")
        normalized = _normalize_modifiers(modifiers)
        repeated = _validate_repeat(repeat)
        unsupported = self._unsupported()
        if unsupported is not None:
            return Result.failure(unsupported)
        raw_modifiers = tuple(modifier.value for modifier in normalized)
        native = self._native_surface.send_key(key.value, raw_modifiers, repeated)
        if native.is_failure:
            return Result.failure(native.unwrap_error())
        receipt = native.unwrap()
        expected = (2 * len(normalized)) + (2 * repeated)
        if receipt.events_requested != expected or receipt.events_inserted != expected:
            return Result.failure(_inconsistent_receipt("key input", expected, receipt))
        return Result.success(
            KeyInputOutcome(
                key=key,
                modifiers=normalized,
                repeat=repeated,
                native_events=receipt.events_inserted,
            )
        )

    def read_clipboard_text(self) -> Result[ClipboardReadOutcome, AgentXError]:
        unsupported = self._unsupported()
        if unsupported is not None:
            return Result.failure(unsupported)
        native = self._native_surface.read_clipboard_text()
        if native.is_failure:
            return Result.failure(native.unwrap_error())
        raw = native.unwrap()
        if raw.has_text:
            if not isinstance(raw.text, str):
                return Result.failure(_malformed_clipboard_result("has_text=true with no text"))
            if len(raw.text) > MAX_CLIPBOARD_TEXT_CHARS:
                return Result.failure(
                    AgentXError(
                        code=_CLIPBOARD_RESULT_TOO_LARGE_ERROR_CODE,
                        message=(
                            "clipboard text exceeds the A5.06 bounded observation limit of "
                            f"{MAX_CLIPBOARD_TEXT_CHARS} characters"
                        ),
                        category=ErrorCategory.RESOURCE,
                        retryability=Retryability.NON_RETRYABLE,
                        details={"max_characters": MAX_CLIPBOARD_TEXT_CHARS},
                    )
                )
            return Result.success(ClipboardReadOutcome(has_text=True, text=raw.text))
        if raw.text is not None:
            return Result.failure(_malformed_clipboard_result("has_text=false with text data"))
        return Result.success(ClipboardReadOutcome(has_text=False, text=None))

    def write_clipboard_text(self, text: str) -> Result[ClipboardWriteOutcome, AgentXError]:
        validated = _validate_text(
            text,
            field_name="clipboard text",
            max_chars=MAX_CLIPBOARD_TEXT_CHARS,
            allow_empty=True,
        )
        units = _utf16_units(validated, field_name="clipboard text")
        unsupported = self._unsupported()
        if unsupported is not None:
            return Result.failure(unsupported)
        native = self._native_surface.write_clipboard_text(validated)
        if native.is_failure:
            return Result.failure(native.unwrap_error())
        return Result.success(
            ClipboardWriteOutcome(character_count=len(validated), utf16_units=units)
        )

    def clear_clipboard(self) -> Result[ClipboardClearOutcome, AgentXError]:
        unsupported = self._unsupported()
        if unsupported is not None:
            return Result.failure(unsupported)
        native = self._native_surface.clear_clipboard()
        if native.is_failure:
            return Result.failure(native.unwrap_error())
        return Result.success(ClipboardClearOutcome())


def _inconsistent_receipt(
    operation: str,
    expected: int,
    receipt: _native.RawInputReceipt,
) -> AgentXError:
    return AgentXError(
        code=_INCONSISTENT_NATIVE_RECEIPT_ERROR_CODE,
        message=f"native {operation} receipt was inconsistent with the requested operation",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.NON_RETRYABLE,
        details={
            "expected_events": expected,
            "events_requested": receipt.events_requested,
            "events_inserted": receipt.events_inserted,
        },
    )


def _malformed_clipboard_result(reason: str) -> AgentXError:
    return AgentXError(
        code="capabilities.windows.clipboard.read_text.malformed_native_result",
        message=f"native clipboard read returned an inconsistent result: {reason}",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.NON_RETRYABLE,
    )


WINDOWS_TEXT_ENTRY_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.text.enter"), version=CapabilityVersion(1, 0, 0)
)
WINDOWS_KEY_INPUT_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.keyboard.press"), version=CapabilityVersion(1, 0, 0)
)
WINDOWS_CLIPBOARD_READ_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.clipboard.read_text"), version=CapabilityVersion(1, 0, 0)
)
WINDOWS_CLIPBOARD_WRITE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.clipboard.write_text"), version=CapabilityVersion(1, 0, 0)
)
WINDOWS_CLIPBOARD_CLEAR_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.clipboard.clear"), version=CapabilityVersion(1, 0, 0)
)


def _windows_precondition() -> CapabilityPrecondition:
    return CapabilityPrecondition(
        name="windows.supported_host",
        description="The A5.01 provider must report a supported Windows host before execution.",
    )


def _foreground_precondition() -> CapabilityPrecondition:
    return CapabilityPrecondition(
        name="windows.foreground_input_target",
        description=(
            "The user-intended input target must already own foreground keyboard focus; "
            "A5.06 does not resolve, select, or change focus."
        ),
    )


def _descriptor(
    *,
    identity: CapabilityIdentity,
    description: str,
    permissions: frozenset[Permission],
    read_only: bool,
    modifies_state: bool,
    external_effect: bool,
    rollback: RollbackDeclaration,
    wall_clock: timedelta,
    preconditions: tuple[CapabilityPrecondition, ...],
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        identity=identity,
        description=description,
        scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
        required_permissions=permissions,
        risk_assessment=assess_risk(
            read_only=read_only,
            modifies_state=modifies_state,
            reversible=False,
            external_effect=external_effect,
        ),
        preconditions=preconditions,
        rollback=rollback,
        estimate=ResourceEstimate(
            wall_clock=wall_clock,
            machine_actions=1,
            external_cost=Decimal("0"),
        ),
    )


def _mutation_verification(operation: str) -> VerificationResult:
    return VerificationResult(
        passed=False,
        detail=(
            f"{operation} produced native execution evidence only; A5.06 does not infer the "
            "user-intended state from an API return, and A5.10 state-transition verification "
            "is deliberately out of scope"
        ),
    )


def _cancelled(operation: str) -> ExecutionResult:
    return ExecutionResult(
        succeeded=False,
        message=f"{operation} was cooperatively cancelled before native execution",
        observation=CapabilityObservation(
            summary=f"{operation} cancelled before native execution",
            data={"cancelled_before_start": True},
        ),
    )


def _execution_failure(operation: str, error: AgentXError) -> ExecutionResult:
    return ExecutionResult(
        succeeded=False,
        message=f"{operation} failed: {error.message}",
        observation=CapabilityObservation(
            summary=f"{operation} native execution failed",
            data={"error": error.to_dict()},
        ),
    )


class WindowsTextEntryCapability:
    """Unicode text entry through SendInput, governed as an R3 external effect."""

    __slots__ = ("_descriptor", "_operations")

    def __init__(self, operations: WindowsKeyboardClipboard) -> None:
        if not isinstance(operations, WindowsKeyboardClipboard):
            raise TypeError("operations must be a WindowsKeyboardClipboard")
        self._operations = operations
        self._descriptor = _descriptor(
            identity=WINDOWS_TEXT_ENTRY_IDENTITY,
            description=(
                "Enter bounded Unicode text into the already-focused Windows input target; "
                "content is untrusted and no target resolution or focus change is performed."
            ),
            permissions=frozenset({Permission.WRITE, Permission.EXTERNAL_EFFECT}),
            read_only=False,
            modifies_state=True,
            external_effect=True,
            rollback=RollbackDeclaration(
                support=RollbackSupport.UNSUPPORTED,
                detail=(
                    "Injected text can trigger arbitrary target behavior and has no generic undo."
                ),
            ),
            wall_clock=timedelta(seconds=2),
            preconditions=(_windows_precondition(), _foreground_precondition()),
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[WindowsTextEntryParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, WindowsTextEntryParams):
            raise TypeError("text entry requires WindowsTextEntryParams")
        if context.observe_stop().should_stop:
            return _cancelled("text entry")
        result = self._operations.enter_text(request.params.text)
        if result.is_failure:
            return _execution_failure("text entry", result.unwrap_error())
        outcome = result.unwrap()
        return ExecutionResult(
            succeeded=True,
            message="Windows native input accepted the complete text-entry event sequence",
            observation=CapabilityObservation(
                summary="text-entry native acceptance evidence",
                data={"operation": "text_entry", **outcome.to_dict()},
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowsTextEntryParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, WindowsTextEntryParams):
            raise TypeError("text entry requires WindowsTextEntryParams")
        return _mutation_verification("text entry")


class WindowsKeyInputCapability:
    """One validated key/chord operation, governed as an R3 external effect."""

    __slots__ = ("_descriptor", "_operations")

    def __init__(self, operations: WindowsKeyboardClipboard) -> None:
        if not isinstance(operations, WindowsKeyboardClipboard):
            raise TypeError("operations must be a WindowsKeyboardClipboard")
        self._operations = operations
        self._descriptor = _descriptor(
            identity=WINDOWS_KEY_INPUT_IDENTITY,
            description=(
                "Send one bounded, closed-vocabulary Windows key or modifier chord to the "
                "already-focused target; no hotkey registration or keystroke capture."
            ),
            permissions=frozenset({Permission.EXECUTE, Permission.EXTERNAL_EFFECT}),
            read_only=False,
            modifies_state=True,
            external_effect=True,
            rollback=RollbackDeclaration(
                support=RollbackSupport.UNSUPPORTED,
                detail="A key chord can trigger arbitrary target actions and has no generic undo.",
            ),
            wall_clock=timedelta(milliseconds=250),
            preconditions=(_windows_precondition(), _foreground_precondition()),
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[WindowsKeyInputParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, WindowsKeyInputParams):
            raise TypeError("key input requires WindowsKeyInputParams")
        if context.observe_stop().should_stop:
            return _cancelled("key input")
        params = request.params
        result = self._operations.press_key(params.key, params.modifiers, params.repeat)
        if result.is_failure:
            return _execution_failure("key input", result.unwrap_error())
        outcome = result.unwrap()
        return ExecutionResult(
            succeeded=True,
            message="Windows native input accepted the complete key event sequence",
            observation=CapabilityObservation(
                summary="key-input native acceptance evidence",
                data={"operation": "key_input", **outcome.to_dict()},
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowsKeyInputParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, WindowsKeyInputParams):
            raise TypeError("key input requires WindowsKeyInputParams")
        return _mutation_verification("key input")


class WindowsClipboardReadCapability:
    """Bounded read of CF_UNICODETEXT; clipboard content remains untrusted."""

    __slots__ = ("_descriptor", "_operations")

    def __init__(self, operations: WindowsKeyboardClipboard) -> None:
        if not isinstance(operations, WindowsKeyboardClipboard):
            raise TypeError("operations must be a WindowsKeyboardClipboard")
        self._operations = operations
        self._descriptor = _descriptor(
            identity=WINDOWS_CLIPBOARD_READ_IDENTITY,
            description=(
                "Read bounded Unicode text from the Windows clipboard without interpreting or "
                "persisting it; returned clipboard content is untrusted data."
            ),
            permissions=frozenset({Permission.READ}),
            read_only=True,
            modifies_state=False,
            external_effect=False,
            rollback=RollbackDeclaration(
                support=RollbackSupport.NOT_APPLICABLE,
                detail="Clipboard read is observation only; there is nothing to roll back.",
            ),
            wall_clock=timedelta(milliseconds=250),
            preconditions=(_windows_precondition(),),
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[WindowsClipboardReadParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, WindowsClipboardReadParams):
            raise TypeError("clipboard read requires WindowsClipboardReadParams")
        if context.observe_stop().should_stop:
            return _cancelled("clipboard read")
        result = self._operations.read_clipboard_text()
        if result.is_failure:
            return _execution_failure("clipboard read", result.unwrap_error())
        outcome = result.unwrap()
        return ExecutionResult(
            succeeded=True,
            message="Windows clipboard Unicode-text read completed",
            observation=CapabilityObservation(
                summary="bounded untrusted Windows clipboard text observation",
                data={"operation": "clipboard_read_text", **outcome.to_dict()},
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowsClipboardReadParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, WindowsClipboardReadParams):
            raise TypeError("clipboard read requires WindowsClipboardReadParams")
        data = observation.to_dict()["data"]
        if not isinstance(data, dict) or data.get("operation") != "clipboard_read_text":
            return VerificationResult(passed=False, detail="clipboard read evidence is malformed")
        has_text = data.get("has_text")
        text = data.get("text")
        if type(has_text) is not bool:
            return VerificationResult(
                passed=False, detail="clipboard read evidence has invalid availability state"
            )
        if has_text and not isinstance(text, str):
            return VerificationResult(
                passed=False, detail="clipboard read evidence says text exists but carries none"
            )
        if not has_text and text is not None:
            return VerificationResult(
                passed=False, detail="clipboard read evidence carries text while unavailable"
            )
        if isinstance(text, str) and len(text) > MAX_CLIPBOARD_TEXT_CHARS:
            return VerificationResult(
                passed=False, detail="clipboard read evidence exceeds the bounded text limit"
            )
        return VerificationResult(
            passed=True,
            detail="clipboard read observation is structurally consistent bounded evidence",
        )


class WindowsClipboardWriteCapability:
    """Bounded Unicode clipboard write governed as an R2 local modification."""

    __slots__ = ("_descriptor", "_operations")

    def __init__(self, operations: WindowsKeyboardClipboard) -> None:
        if not isinstance(operations, WindowsKeyboardClipboard):
            raise TypeError("operations must be a WindowsKeyboardClipboard")
        self._operations = operations
        self._descriptor = _descriptor(
            identity=WINDOWS_CLIPBOARD_WRITE_IDENTITY,
            description=(
                "Replace the Windows clipboard with bounded Unicode text; content is untrusted "
                "and no clipboard history is retained by AgentX."
            ),
            permissions=frozenset({Permission.WRITE}),
            read_only=False,
            modifies_state=True,
            external_effect=False,
            rollback=RollbackDeclaration(
                support=RollbackSupport.UNSUPPORTED,
                detail="A5.06 stores no prior clipboard value and therefore cannot restore it.",
            ),
            wall_clock=timedelta(milliseconds=250),
            preconditions=(_windows_precondition(),),
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[WindowsClipboardWriteParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, WindowsClipboardWriteParams):
            raise TypeError("clipboard write requires WindowsClipboardWriteParams")
        if context.observe_stop().should_stop:
            return _cancelled("clipboard write")
        result = self._operations.write_clipboard_text(request.params.text)
        if result.is_failure:
            return _execution_failure("clipboard write", result.unwrap_error())
        outcome = result.unwrap()
        return ExecutionResult(
            succeeded=True,
            message="Windows clipboard write API accepted the bounded Unicode text",
            observation=CapabilityObservation(
                summary="clipboard-write native acceptance evidence",
                data={"operation": "clipboard_write_text", **outcome.to_dict()},
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowsClipboardWriteParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, WindowsClipboardWriteParams):
            raise TypeError("clipboard write requires WindowsClipboardWriteParams")
        return _mutation_verification("clipboard write")


class WindowsClipboardClearCapability:
    """Explicit clipboard clear governed as an R2 local modification."""

    __slots__ = ("_descriptor", "_operations")

    def __init__(self, operations: WindowsKeyboardClipboard) -> None:
        if not isinstance(operations, WindowsKeyboardClipboard):
            raise TypeError("operations must be a WindowsKeyboardClipboard")
        self._operations = operations
        self._descriptor = _descriptor(
            identity=WINDOWS_CLIPBOARD_CLEAR_IDENTITY,
            description=(
                "Clear the current Windows clipboard without retaining prior clipboard content."
            ),
            permissions=frozenset({Permission.WRITE}),
            read_only=False,
            modifies_state=True,
            external_effect=False,
            rollback=RollbackDeclaration(
                support=RollbackSupport.UNSUPPORTED,
                detail=(
                    "A5.06 stores no clipboard history, so a clear cannot be generically undone."
                ),
            ),
            wall_clock=timedelta(milliseconds=250),
            preconditions=(_windows_precondition(),),
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[WindowsClipboardClearParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, WindowsClipboardClearParams):
            raise TypeError("clipboard clear requires WindowsClipboardClearParams")
        if context.observe_stop().should_stop:
            return _cancelled("clipboard clear")
        result = self._operations.clear_clipboard()
        if result.is_failure:
            return _execution_failure("clipboard clear", result.unwrap_error())
        outcome = result.unwrap()
        return ExecutionResult(
            succeeded=True,
            message="Windows EmptyClipboard API returned successful acceptance",
            observation=CapabilityObservation(
                summary="clipboard-clear native acceptance evidence",
                data={"operation": "clipboard_clear", **outcome.to_dict()},
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowsClipboardClearParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, WindowsClipboardClearParams):
            raise TypeError("clipboard clear requires WindowsClipboardClearParams")
        return _mutation_verification("clipboard clear")


def text_entry_request(text: str) -> CapabilityRequest[WindowsTextEntryParams]:
    return CapabilityRequest(
        identity=WINDOWS_TEXT_ENTRY_IDENTITY,
        params=WindowsTextEntryParams(text=text),
    )


def key_input_request(
    key: WindowsKey,
    modifiers: tuple[WindowsModifier, ...] = (),
    repeat: int = 1,
) -> CapabilityRequest[WindowsKeyInputParams]:
    return CapabilityRequest(
        identity=WINDOWS_KEY_INPUT_IDENTITY,
        params=WindowsKeyInputParams(key=key, modifiers=modifiers, repeat=repeat),
    )


def clipboard_read_request() -> CapabilityRequest[WindowsClipboardReadParams]:
    return CapabilityRequest(
        identity=WINDOWS_CLIPBOARD_READ_IDENTITY,
        params=WindowsClipboardReadParams(),
    )


def clipboard_write_request(text: str) -> CapabilityRequest[WindowsClipboardWriteParams]:
    return CapabilityRequest(
        identity=WINDOWS_CLIPBOARD_WRITE_IDENTITY,
        params=WindowsClipboardWriteParams(text=text),
    )


def clipboard_clear_request() -> CapabilityRequest[WindowsClipboardClearParams]:
    return CapabilityRequest(
        identity=WINDOWS_CLIPBOARD_CLEAR_IDENTITY,
        params=WindowsClipboardClearParams(),
    )
