"""Governed Windows keyboard, text, and clipboard capabilities (A5.06 / N2.23).

This module exposes exactly five governed, active Windows machine operations
and nothing else:

    - ``windows.keyboard.send_text``      — bounded explicit Unicode text entry
    - ``windows.keyboard.send_keys``      — bounded explicit key chords
    - ``windows.clipboard.read_text``     — bounded Unicode-text clipboard read
    - ``windows.clipboard.write_text``    — bounded Unicode-text clipboard write
    - ``windows.clipboard.clear``         — bounded clipboard clear

Every operation is an ordinary canonical
:class:`~agentx.capabilities.abi.Capability`: an inert
:class:`~agentx.capabilities.abi.CapabilityDescriptor` (identity, scope,
required permissions, canonical risk, preconditions, rollback, estimate) plus
``execute`` / ``verify``. Execution reaches the machine only through two
narrow injected native ports:

    - :class:`KeyboardNativePort` — ``send_text`` / ``send_keys``
    - :class:`ClipboardNativePort` — ``read_text`` / ``write_text`` / ``clear``

This module contains **no** Win32 knowledge. It never imports ``ctypes`` or
any native/automation library, and it does not depend on the A5.02 read-only
discovery seam or on any unmerged native-mutation seam. The integration
authority may later bind a concrete Win32-backed implementation of the ports;
until then the default port implementations fail every call with one explicit
canonical precondition error (:data:`NATIVE_SEAM_UNAVAILABLE_ERROR_CODE`)
instead of silently doing nothing or importing anything.

Key model
---------

Key input uses a **closed canonical vocabulary**, never free-form strings and
never raw virtual-key or scan-code integers:

    - :class:`Key` — a closed ``StrEnum`` of addressable keys (letters,
      digits, F1-F12, navigation/editing keys, and US-layout symbol keys);
    - :class:`Modifier` — a closed ``StrEnum`` of the four modifier keys;
    - :class:`KeyChord` — one :class:`Key` plus an explicit
      ``frozenset[Modifier]`` (a modifier is never also a key, and a
      duplicate modifier is impossible by construction);
    - a bounded *sequence* of chords (1..:data:`MAX_KEY_CHORD_SEQUENCE_LENGTH`).

A string such as ``"Ctrl+Alt+Delete"`` is never parsed through any command
syntax: the typed parameter boundary rejects it at construction. Text entry is
explicit Unicode text with bounded length, no implicit interpretation of text
content as commands, and an explicit control-character contract.

Governance / risk
-----------------

Operations are classified individually with the canonical
:func:`~agentx.kernel.risk.assess_risk` model — they do not share one risk
because they sit in one module:

    - text entry: real external effect -> R3, requires ``EXTERNAL_EFFECT``;
    - key input: real external effect -> at least R3, and because the closed
      vocabulary includes destructive hot chords (Alt+F4, Ctrl+Alt+Delete),
      the canonical *static* descriptor declares the conservative worst-case
      ceiling R4 and requires ``EXTERNAL_EFFECT`` + ``DESTRUCTIVE``; the
      per-request risk (R3 vs R4) is computed by
      :func:`classify_key_sequence_risk` and recorded in the execution
      observation, because the canonical descriptor model cannot express
      per-request key semantics;
    - clipboard write / clear: external state mutation -> R3, requires
      ``EXTERNAL_EFFECT``;
    - clipboard read: observation only -> R0, requires ``READ``.

The descriptor is the *ceiling*; it can never be widened by request data.
Hostile request text or clipboard content can never change a descriptor, a
permission, a risk level, an ``AuthorityContext``, a budget, or a gate
decision.

Untrusted data
--------------

Clipboard content and all native-port outcomes are **untrusted data**:

    - clipboard read returns content as inert evidence; it is never executed,
      parsed as authority, granted as permission, fed to a model, or used to
      mark a Task successful;
    - clipboard write content and entry text are inert data sent verbatim;
    - raw outcomes are validated against explicit bounds; a seam that
      misreports is an execution failure, never trusted silently.

Privacy
-------

Operation *messages*, observation *summaries*, capability ``repr`` output, and
audit-record fields never carry the typed text or clipboard payload. The
canonical execution loop already keeps observation evidence out of
:class:`~agentx.kernel.audit.SecurityAuditRecord` values; this module adds no
logging, no persistence, and no other channel for payload content. Read
payload travels only in the observation data channel (local evidence, same
canonical convention as ``filesystem.read_text``).

Verification truth boundary
---------------------------

Native API acceptance is **not** user-visible state: an accepted input batch
does not prove text appeared in the intended field, and an accepted clipboard
write does not prove any application consumed it. No canonical independent
verifier exists for these operations yet (A5.10 state-transition
verification owns that boundary), so every ``verify`` returns an explicit
``passed=False`` verdict stating that verification is unsupported. No success
is ever fabricated from a native success.

Deliberate non-scope
--------------------

No global hotkeys, no keyboard hooks or low-level input capture (no
keylogging of any kind), no background keystroke or clipboard monitoring, no
clipboard history, no hidden listeners, no UI Automation resolution or
invocation, no dialogs, no visual fallback, no mouse, no screenshots, no
shell, no process launch, no state-transition verification implementation, and
no Win32 implementation of any kind in this module.

Owner: A5.06 (clean rebuild, N2.23). Belongs to
``agentx.capabilities.windows``; imports only the standard library and
canonical ``agentx.core`` / ``agentx.kernel`` / capability-ABI contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    CapabilityValidationError,
    CapabilityVersion,
    ExecutionResult,
    ResourceEstimate,
    RollbackDeclaration,
    RollbackSupport,
    VerificationResult,
)
from agentx.capabilities.windows.provider import (
    WindowsSupport,
    unsupported_platform_error,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskAssessment, assess_risk

__all__ = [
    "CLIPBOARD_CONTENT_TOO_LARGE_ERROR_CODE",
    "CRITICAL_KEY_CHORDS",
    "MAX_CLIPBOARD_TEXT_LENGTH",
    "MAX_INPUT_TEXT_LENGTH",
    "MAX_KEY_CHORD_SEQUENCE_LENGTH",
    "NATIVE_SEAM_UNAVAILABLE_ERROR_CODE",
    "SEAM_COUNT_MISMATCH_ERROR_CODE",
    "SEAM_OUTCOME_MALFORMED_ERROR_CODE",
    "UNVERIFIED_MESSAGE_SUFFIX",
    "WINDOWS_CLIPBOARD_CLEAR_IDENTITY",
    "WINDOWS_CLIPBOARD_READ_TEXT_IDENTITY",
    "WINDOWS_CLIPBOARD_WRITE_TEXT_IDENTITY",
    "WINDOWS_SEND_KEYS_IDENTITY",
    "WINDOWS_SEND_TEXT_IDENTITY",
    "CapabilityRequest",
    "ClipboardClearParams",
    "ClipboardNativePort",
    "ClipboardReadTextParams",
    "ClipboardWriteTextParams",
    "Key",
    "KeyChord",
    "KeyboardNativePort",
    "Modifier",
    "RawClipboardClear",
    "RawClipboardText",
    "RawClipboardWrite",
    "RawKeySend",
    "RawTextSend",
    "SendKeysParams",
    "SendTextParams",
    "UnavailableClipboardNativePort",
    "UnavailableKeyboardNativePort",
    "WindowsClipboardClearCapability",
    "WindowsClipboardReadTextCapability",
    "WindowsClipboardWriteTextCapability",
    "WindowsSendKeysCapability",
    "WindowsSendTextCapability",
    "classify_key_sequence_risk",
    "clipboard_clear_request",
    "clipboard_read_request",
    "clipboard_write_request",
    "send_keys_request",
    "send_text_request",
]

# --------------------------------------------------------------------------
# Governed bounds.
# --------------------------------------------------------------------------

#: Maximum length of one explicit text-entry or clipboard-write payload.
MAX_INPUT_TEXT_LENGTH: Final[int] = 4_096

#: Maximum number of explicit key chords in one key-input request.
MAX_KEY_CHORD_SEQUENCE_LENGTH: Final[int] = 64

#: Maximum length of clipboard text carried through the governed channel.
MAX_CLIPBOARD_TEXT_LENGTH: Final[int] = 1_048_576

# --------------------------------------------------------------------------
# Canonical error codes (ordinary AgentXError codes; no new error types).
# --------------------------------------------------------------------------

#: The Win32-backed native seam is not yet bound to the injected port.
NATIVE_SEAM_UNAVAILABLE_ERROR_CODE: Final[str] = (
    "capabilities.windows.keyboard_text_clipboard.native_seam_unavailable"
)

#: A native seam returned an outcome that failed validation (malformed type).
SEAM_OUTCOME_MALFORMED_ERROR_CODE: Final[str] = (
    "capabilities.windows.keyboard_text_clipboard.seam_outcome_malformed"
)

#: A native seam reported a count that disagrees with the explicit request.
SEAM_COUNT_MISMATCH_ERROR_CODE: Final[str] = (
    "capabilities.windows.keyboard_text_clipboard.seam_count_mismatch"
)

#: Clipboard content beyond the governed read bound.
CLIPBOARD_CONTENT_TOO_LARGE_ERROR_CODE: Final[str] = (
    "capabilities.windows.keyboard_text_clipboard.clipboard_content_too_large"
)

# --------------------------------------------------------------------------
# Canonical capability identities (A1.08).
# --------------------------------------------------------------------------

WINDOWS_SEND_TEXT_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.keyboard.send_text"),
    version=CapabilityVersion(1, 0, 0),
)
WINDOWS_SEND_KEYS_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.keyboard.send_keys"),
    version=CapabilityVersion(1, 0, 0),
)
WINDOWS_CLIPBOARD_READ_TEXT_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.clipboard.read_text"),
    version=CapabilityVersion(1, 0, 0),
)
WINDOWS_CLIPBOARD_WRITE_TEXT_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.clipboard.write_text"),
    version=CapabilityVersion(1, 0, 0),
)
WINDOWS_CLIPBOARD_CLEAR_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.clipboard.clear"),
    version=CapabilityVersion(1, 0, 0),
)

#: Explicit marker carried in every successful message of this module.
UNVERIFIED_MESSAGE_SUFFIX: Final[str] = "native acceptance only; state unverified"

# --------------------------------------------------------------------------
# Closed key vocabulary.
# --------------------------------------------------------------------------


class Key(StrEnum):
    """Closed canonical key vocabulary for governed key input.

    A closed vocabulary is the contract: an identifier outside it is rejected
    at construction, so unknown keys and raw virtual-key/scancode integers
    cannot enter the governed channel. Modifier keys are deliberately **not**
    members: modifiers are expressed only through
    :class:`KeyChord.modifiers`.
    """

    A = "a"
    B = "b"
    C = "c"
    D = "d"
    E = "e"
    F = "f"
    G = "g"
    H = "h"
    I = "i"  # noqa: E741
    J = "j"
    K = "k"
    L = "l"
    M = "m"
    N = "n"
    O = "o"  # noqa: E741
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
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    HOME = "home"
    END = "end"
    PAGE_UP = "page_up"
    PAGE_DOWN = "page_down"
    ENTER = "enter"
    TAB = "tab"
    SPACE = "space"
    ESCAPE = "escape"
    BACKSPACE = "backspace"
    DELETE = "delete"
    INSERT = "insert"
    COMMA = "comma"
    PERIOD = "period"
    SEMICOLON = "semicolon"
    APOSTROPHE = "apostrophe"
    MINUS = "minus"
    EQUALS = "equals"
    OPEN_BRACKET = "open_bracket"
    CLOSE_BRACKET = "close_bracket"
    BACKSLASH = "backslash"


class Modifier(StrEnum):
    """Closed canonical modifier-key vocabulary for governed key input."""

    SHIFT = "shift"
    CTRL = "ctrl"
    ALT = "alt"
    WIN = "win"


@dataclass(frozen=True, slots=True)
class KeyChord:
    """One explicit key chord: one :class:`Key` plus an explicit modifier set.

    Structural guarantees: the key is always a non-modifier
    :class:`Key`; the modifier set is a ``frozenset`` of :class:`Modifier`
    values, so duplicate modifiers are impossible and foreign members are
    rejected at construction. A chord is data; it is never parsed from or
    reduced to a free-form string.
    """

    key: Key
    modifiers: frozenset[Modifier] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.key, Key):
            raise TypeError(f"chord key must be a Key, got {type(self.key).__name__}")
        if type(self.modifiers) is not frozenset:
            raise TypeError(
                f"chord modifiers must be a frozenset, got {type(self.modifiers).__name__}"
            )
        for modifier in self.modifiers:
            if not isinstance(modifier, Modifier):
                raise TypeError(
                    "chord modifiers must contain only Modifier values, got "
                    f"{type(modifier).__name__}"
                )

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the canonical JSON-compatible representation."""
        ordered = sorted(self.modifiers, key=lambda modifier: modifier.value)
        modifiers: list[JsonValue] = [modifier.value for modifier in ordered]
        return {"key": self.key.value, "modifiers": modifiers}


# --------------------------------------------------------------------------
# Explicit text contract (entry text and clipboard write text).
# --------------------------------------------------------------------------

#: Control characters explicitly allowed inside governed text payloads:
#: tab, line feed, carriage return. They are sent verbatim as characters and
#: are never interpreted as key commands.
_ALLOWED_TEXT_CONTROL_CHARACTERS: Final[frozenset[str]] = frozenset({"\t", "\n", "\r"})


def _validated_governed_text(value: object, *, field_name: str, max_length: int) -> str:
    """Validate one explicit bounded text payload against the canonical contract.

    Contract: explicit ``str``; non-empty (empty payloads are rejected
    explicitly instead of injecting nothing); bounded length; no NUL; no C0
    control characters other than tab/line-feed/carriage-return; no DEL; no C1
    control characters; no unpaired surrogate code points. Everything else —
    including hostile, instruction-like content — is legal *data* and passes
    through verbatim and inert.
    """
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str, got {type(value).__name__}")
    if not value:
        raise CapabilityValidationError(
            f"{field_name} must not be empty; empty payloads are rejected explicitly"
        )
    if len(value) > max_length:
        raise CapabilityValidationError(f"{field_name} must not exceed {max_length} characters")
    for character in value:
        code_point = ord(character)
        if code_point == 0x00:
            raise CapabilityValidationError(f"{field_name} must not contain NUL")
        if code_point < 0x20 and character not in _ALLOWED_TEXT_CONTROL_CHARACTERS:
            raise CapabilityValidationError(
                f"{field_name} must not contain control characters other than "
                "tab, line feed, and carriage return"
            )
        if code_point == 0x7F:
            raise CapabilityValidationError(f"{field_name} must not contain DEL")
        if 0x80 <= code_point <= 0x9F:
            raise CapabilityValidationError(f"{field_name} must not contain C1 control characters")
        if 0xD800 <= code_point <= 0xDFFF:
            raise CapabilityValidationError(
                f"{field_name} must not contain unpaired surrogate code points"
            )
    return value


# --------------------------------------------------------------------------
# Typed request parameters (A1.08 CapabilityParams).
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SendTextParams(CapabilityParams):
    """Explicit bounded Unicode text-entry parameters.

    ``text`` is inert data: its content is never interpreted as commands,
    hot chords, or instructions of any kind.
    """

    text: str

    def __post_init__(self) -> None:
        _validated_governed_text(self.text, field_name="text", max_length=MAX_INPUT_TEXT_LENGTH)

    def to_dict(self) -> dict[str, JsonValue]:
        return {"text": self.text}


@dataclass(frozen=True, slots=True)
class SendKeysParams(CapabilityParams):
    """Explicit bounded key-chord sequence parameters.

    The sequence is a non-empty ``tuple`` of :class:`KeyChord` values bounded
    by :data:`MAX_KEY_CHORD_SEQUENCE_LENGTH`. Free-form strings (for example
    ``"Ctrl+Alt+Delete"``) are rejected by the typed boundary, never parsed.
    """

    chords: tuple[KeyChord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.chords, tuple):
            raise TypeError(f"chords must be a tuple of KeyChord, got {type(self.chords).__name__}")
        if not self.chords:
            raise CapabilityValidationError(
                "chords must not be empty; an empty key sequence is rejected explicitly"
            )
        if len(self.chords) > MAX_KEY_CHORD_SEQUENCE_LENGTH:
            raise CapabilityValidationError(
                f"chords must not exceed {MAX_KEY_CHORD_SEQUENCE_LENGTH} key chords"
            )
        for chord in self.chords:
            if not isinstance(chord, KeyChord):
                raise TypeError(
                    f"chords must contain only KeyChord values, got {type(chord).__name__}"
                )

    def to_dict(self) -> dict[str, JsonValue]:
        return {"chords": [chord.to_dict() for chord in self.chords]}


@dataclass(frozen=True, slots=True)
class ClipboardReadTextParams(CapabilityParams):
    """Parameters for the governed clipboard text read.

    The supported format is explicit Unicode text only; the operation takes
    no other parameter. Absence of a text format is an explicit result state,
    not an error.
    """

    def to_dict(self) -> dict[str, JsonValue]:
        return {}


@dataclass(frozen=True, slots=True)
class ClipboardWriteTextParams(CapabilityParams):
    """Explicit bounded clipboard text-write parameters."""

    text: str

    def __post_init__(self) -> None:
        _validated_governed_text(self.text, field_name="text", max_length=MAX_CLIPBOARD_TEXT_LENGTH)

    def to_dict(self) -> dict[str, JsonValue]:
        return {"text": self.text}


@dataclass(frozen=True, slots=True)
class ClipboardClearParams(CapabilityParams):
    """Parameters for the governed clipboard clear (no parameters)."""

    def to_dict(self) -> dict[str, JsonValue]:
        return {}


# --------------------------------------------------------------------------
# Per-request key-sequence risk classification.
# --------------------------------------------------------------------------

#: Closed set of key chords whose semantics are critical. Any sequence
#: containing one of these chords is classified at the strongest level. The
#: set is deliberately explicit and small; adding a chord is a contract
#: change, never inferred from request data.
CRITICAL_KEY_CHORDS: Final[frozenset[KeyChord]] = frozenset(
    {
        # Closes the foreground window: a real, user-visible destructive effect.
        KeyChord(key=Key.F4, modifiers=frozenset({Modifier.ALT})),
        # Requests the operating-system secure desktop transition.
        KeyChord(key=Key.DELETE, modifiers=frozenset({Modifier.CTRL, Modifier.ALT})),
    }
)


def classify_key_sequence_risk(chords: tuple[KeyChord, ...]) -> RiskAssessment:
    """Classify one explicit key sequence with the canonical risk model.

    All key input is a real external effect (at least R3 EXTERNAL_EFFECT, with
    explicit state modification and no reversibility). A sequence that
    contains any closed :data:`CRITICAL_KEY_CHORDS` member is classified R4
    CRITICAL. The returned assessment is descriptive data: it grants nothing
    and cannot be used to alter a descriptor or a gate decision.
    """
    if not isinstance(chords, tuple):
        raise TypeError(f"chords must be a tuple, got {type(chords).__name__}")
    if not chords:
        raise ValueError("a key sequence must not be empty")
    for chord in chords:
        if not isinstance(chord, KeyChord):
            raise TypeError(f"chords must contain only KeyChord, got {type(chord).__name__}")
    if any(chord in CRITICAL_KEY_CHORDS for chord in chords):
        return assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=False,
            external_effect=True,
            critical=True,
        )
    return assess_risk(
        read_only=False,
        modifies_state=True,
        reversible=False,
        external_effect=True,
    )


# --------------------------------------------------------------------------
# Raw native outcomes (untrusted seam data, validated before use).
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawTextSend:
    """Verbatim native outcome of one text-entry batch.

    ``character_count`` is the number of characters the seam reports it
    accepted as input events. It is untrusted data: the capability cross-checks
    it against the explicit request and fails the operation on any mismatch.
    """

    character_count: int


@dataclass(frozen=True, slots=True)
class RawKeySend:
    """Verbatim native outcome of one key-chord sequence."""

    chord_count: int


@dataclass(frozen=True, slots=True)
class RawClipboardText:
    """Verbatim native outcome of one clipboard text read.

    ``text`` is ``None`` exactly when the clipboard held no text format.
    Content, when present, is untrusted data carried verbatim and inert.
    """

    text: str | None


@dataclass(frozen=True, slots=True)
class RawClipboardWrite:
    """Verbatim native outcome of one clipboard text write."""

    character_count: int


@dataclass(frozen=True, slots=True)
class RawClipboardClear:
    """Verbatim native outcome of one clipboard clear."""

    previous_was_empty: bool


# --------------------------------------------------------------------------
# Narrow injected native ports.
# --------------------------------------------------------------------------


class KeyboardNativePort(Protocol):
    """Narrow injected seam for governed keyboard/text input.

    A concrete implementation maps the closed canonical vocabulary onto the
    operating-system input mechanism. Tests substitute fakes. The capability
    layer never sees — and never depends on — any native library.
    """

    def send_text(self, text: str) -> Result[RawTextSend, AgentXError]:
        """Send exactly ``text`` as explicit Unicode text entry."""
        ...

    def send_keys(self, chords: tuple[KeyChord, ...]) -> Result[RawKeySend, AgentXError]:
        """Send exactly the bounded explicit key-chord sequence."""
        ...


class ClipboardNativePort(Protocol):
    """Narrow injected seam for governed clipboard operations.

    Only explicit Unicode text is supported. There is no format enumeration,
    no format-specific payload access, and no monitoring: every method is one
    explicit, caller-initiated operation.
    """

    def read_text(self) -> Result[RawClipboardText, AgentXError]:
        """Read the clipboard's explicit Unicode text format, if present."""
        ...

    def write_text(self, text: str) -> Result[RawClipboardWrite, AgentXError]:
        """Write exactly ``text`` as the clipboard's Unicode text format."""
        ...

    def clear(self) -> Result[RawClipboardClear, AgentXError]:
        """Clear the clipboard explicitly."""
        ...


def _seam_unavailable_error(operation: str) -> AgentXError:
    """Build the canonical failure for an unbound native seam."""
    return AgentXError(
        code=NATIVE_SEAM_UNAVAILABLE_ERROR_CODE,
        message=(
            f"Windows keyboard/clipboard native seam is not bound for {operation}; "
            "the integration authority must bind a concrete native port implementation"
        ),
        category=ErrorCategory.PRECONDITION,
        retryability=Retryability.NON_RETRYABLE,
        details={"operation": operation},
    )


class UnavailableKeyboardNativePort:
    """Explicit unmapped-seam placeholder for the keyboard input port.

    Until the integration authority binds a concrete Win32-backed
    :class:`KeyboardNativePort` implementation, every call returns one
    explicit canonical precondition failure. Constructing it is side-effect
    free; no native library is ever loaded and no machine action is attempted.
    """

    __slots__ = ()

    def send_text(self, text: str) -> Result[RawTextSend, AgentXError]:
        return Result.failure(_seam_unavailable_error("send_text"))

    def send_keys(self, chords: tuple[KeyChord, ...]) -> Result[RawKeySend, AgentXError]:
        return Result.failure(_seam_unavailable_error("send_keys"))


class UnavailableClipboardNativePort:
    """Explicit unmapped-seam placeholder for the clipboard port.

    Until the integration authority binds a concrete Win32-backed
    :class:`ClipboardNativePort` implementation, every call returns one
    explicit canonical precondition failure. Constructing it is side-effect
    free; no native library is ever loaded and no machine action is attempted.
    """

    __slots__ = ()

    def read_text(self) -> Result[RawClipboardText, AgentXError]:
        return Result.failure(_seam_unavailable_error("read_text"))

    def write_text(self, text: str) -> Result[RawClipboardWrite, AgentXError]:
        return Result.failure(_seam_unavailable_error("write_text"))

    def clear(self) -> Result[RawClipboardClear, AgentXError]:
        return Result.failure(_seam_unavailable_error("clear"))


# --------------------------------------------------------------------------
# Canonical descriptors (one per operation; the risk ceiling is static).
# --------------------------------------------------------------------------

_SUPPORTED_HOST_PRECONDITION: Final[CapabilityPrecondition] = CapabilityPrecondition(
    name="windows.supported_host",
    description=(
        "The host must evaluate as a supported Windows host through the A5.01 "
        "provider boundary before the operation can run."
    ),
)

_FOREGROUND_FOCUS_PRECONDITION: Final[CapabilityPrecondition] = CapabilityPrecondition(
    name="windows.foreground_focus",
    description=(
        "Input is delivered to the operating-system foreground focus; this "
        "capability does not select, target, or verify a specific window."
    ),
)

_SEND_TEXT_DESCRIPTOR: Final[CapabilityDescriptor] = CapabilityDescriptor(
    identity=WINDOWS_SEND_TEXT_IDENTITY,
    description=(
        "Entry of one explicit bounded Unicode text payload into the foreground "
        "input focus. Text content is inert data and is never interpreted as "
        "commands or key syntax."
    ),
    scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
    required_permissions=frozenset({Permission.EXTERNAL_EFFECT}),
    risk_assessment=assess_risk(
        read_only=False,
        modifies_state=True,
        reversible=False,
        external_effect=True,
    ),
    preconditions=(_SUPPORTED_HOST_PRECONDITION, _FOREGROUND_FOCUS_PRECONDITION),
    rollback=RollbackDeclaration(
        support=RollbackSupport.UNSUPPORTED,
        detail="Entered text cannot be retracted; no prior field state is retained.",
    ),
    estimate=ResourceEstimate(
        wall_clock=timedelta(seconds=1), machine_actions=1, external_cost=Decimal("0")
    ),
)

_SEND_KEYS_DESCRIPTOR: Final[CapabilityDescriptor] = CapabilityDescriptor(
    identity=WINDOWS_SEND_KEYS_IDENTITY,
    description=(
        "Input of one bounded sequence of explicit canonical key chords into "
        "the foreground input focus. The descriptor declares the conservative "
        "worst-case R4 ceiling across the closed key vocabulary because the "
        "canonical static descriptor cannot express per-request key semantics; "
        "the per-request risk (R3, or R4 for closed critical chords) is "
        "computed by classify_key_sequence_risk and recorded in the execution "
        "observation."
    ),
    scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
    required_permissions=frozenset({Permission.EXTERNAL_EFFECT, Permission.DESTRUCTIVE}),
    risk_assessment=assess_risk(
        read_only=False,
        modifies_state=True,
        reversible=False,
        external_effect=True,
        critical=True,
    ),
    preconditions=(_SUPPORTED_HOST_PRECONDITION, _FOREGROUND_FOCUS_PRECONDITION),
    rollback=RollbackDeclaration(
        support=RollbackSupport.UNSUPPORTED,
        detail="Sent key events cannot be retracted; no prior application state is retained.",
    ),
    estimate=ResourceEstimate(
        wall_clock=timedelta(seconds=1), machine_actions=1, external_cost=Decimal("0")
    ),
)

_CLIPBOARD_READ_DESCRIPTOR: Final[CapabilityDescriptor] = CapabilityDescriptor(
    identity=WINDOWS_CLIPBOARD_READ_TEXT_IDENTITY,
    description=(
        "Read the clipboard's explicit Unicode text format, bounded. Absence of "
        "a text format is an explicit result state. Clipboard content is "
        "untrusted data returned as inert evidence."
    ),
    scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
    required_permissions=frozenset({Permission.READ}),
    risk_assessment=assess_risk(
        read_only=True,
        modifies_state=False,
        reversible=False,
        external_effect=False,
    ),
    preconditions=(_SUPPORTED_HOST_PRECONDITION,),
    rollback=RollbackDeclaration(
        support=RollbackSupport.NOT_APPLICABLE,
        detail="Reading clipboard text does not intentionally mutate any state.",
    ),
    estimate=ResourceEstimate(
        wall_clock=timedelta(milliseconds=250), machine_actions=1, external_cost=Decimal("0")
    ),
)

_CLIPBOARD_WRITE_DESCRIPTOR: Final[CapabilityDescriptor] = CapabilityDescriptor(
    identity=WINDOWS_CLIPBOARD_WRITE_TEXT_IDENTITY,
    description=(
        "Write one explicit bounded Unicode text payload as the clipboard's "
        "text format. The clipboard is shared external state visible to other "
        "applications, so the operation is classified as an external effect. "
        "The prior clipboard content is not retained."
    ),
    scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
    required_permissions=frozenset({Permission.EXTERNAL_EFFECT}),
    risk_assessment=assess_risk(
        read_only=False,
        modifies_state=True,
        reversible=False,
        external_effect=True,
    ),
    preconditions=(_SUPPORTED_HOST_PRECONDITION,),
    rollback=RollbackDeclaration(
        support=RollbackSupport.UNSUPPORTED,
        detail="The prior clipboard content is replaced and not retained for rollback.",
    ),
    estimate=ResourceEstimate(
        wall_clock=timedelta(seconds=1), machine_actions=1, external_cost=Decimal("0")
    ),
)

_CLIPBOARD_CLEAR_DESCRIPTOR: Final[CapabilityDescriptor] = CapabilityDescriptor(
    identity=WINDOWS_CLIPBOARD_CLEAR_IDENTITY,
    description=(
        "Explicitly clear the clipboard. Clearing mutates shared external state "
        "visible to other applications and discards the prior clipboard content "
        "without retaining it."
    ),
    scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
    required_permissions=frozenset({Permission.EXTERNAL_EFFECT}),
    risk_assessment=assess_risk(
        read_only=False,
        modifies_state=True,
        reversible=False,
        external_effect=True,
    ),
    preconditions=(_SUPPORTED_HOST_PRECONDITION,),
    rollback=RollbackDeclaration(
        support=RollbackSupport.UNSUPPORTED,
        detail="The cleared clipboard content is discarded and not retained for rollback.",
    ),
    estimate=ResourceEstimate(
        wall_clock=timedelta(milliseconds=500), machine_actions=1, external_cost=Decimal("0")
    ),
)


# --------------------------------------------------------------------------
# Shared execution helpers.
# --------------------------------------------------------------------------


def _execution_failure(error: AgentXError, *, summary: str) -> ExecutionResult:
    """Build the failed execution result carrying the structured error."""
    return ExecutionResult(
        succeeded=False,
        message=error.message,
        observation=CapabilityObservation(summary=summary, data={"error": error.to_dict()}),
    )


def _cancelled_execution(operation: str) -> ExecutionResult:
    """Build the explicit cancelled execution result (no native call)."""
    error = AgentXError(
        code="capabilities.windows.keyboard_text_clipboard.cancelled",
        message=f"{operation} was cooperatively cancelled before native access",
        category=ErrorCategory.CANCELLED,
        retryability=Retryability.UNKNOWN,
    )
    return _execution_failure(error, summary=f"{operation} cancelled before native access")


def _malformed_outcome_error(operation: str, expected: str) -> AgentXError:
    """Build the failure for a native outcome that failed validation."""
    return AgentXError(
        code=SEAM_OUTCOME_MALFORMED_ERROR_CODE,
        message=f"{operation} native seam returned a malformed outcome, expected {expected}",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
        details={"operation": operation},
    )


def _count_mismatch_error(operation: str, expected: int, actual: int) -> AgentXError:
    """Build the failure for a native count that disagrees with the request."""
    return AgentXError(
        code=SEAM_COUNT_MISMATCH_ERROR_CODE,
        message=(
            f"{operation} native seam reported a count that does not match the explicit request"
        ),
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
        details={"operation": operation, "expected_count": expected, "reported_count": actual},
    )


def _unsupported_verification(operation: str) -> VerificationResult:
    """Build the explicit unverified verdict (no success is fabricated)."""
    return VerificationResult(
        passed=False,
        detail=(
            f"{operation}: verification is unsupported. Native API acceptance does "
            "not prove the intended user-visible state, and no canonical independent "
            "verifier exists for this operation (A5.10 state-transition verification "
            "owns that boundary). No success is fabricated from native success."
        ),
    )


def _require_support(support: WindowsSupport) -> None:
    if not isinstance(support, WindowsSupport):
        raise TypeError(f"support must be a WindowsSupport, got {type(support).__name__}")


# --------------------------------------------------------------------------
# Capability implementations.
# --------------------------------------------------------------------------


class WindowsSendTextCapability:
    """Governed bounded Unicode text entry (``windows.keyboard.send_text``)."""

    __slots__ = ("_descriptor", "_keyboard_port", "_support")

    _support: WindowsSupport
    _keyboard_port: KeyboardNativePort
    _descriptor: CapabilityDescriptor

    def __init__(
        self, support: WindowsSupport, keyboard_port: KeyboardNativePort | None = None
    ) -> None:
        """Bind the capability to one support verdict and one keyboard port.

        ``keyboard_port`` defaults to the explicit unmapped-seam placeholder;
        binding a concrete implementation is the integration authority's job.
        """
        _require_support(support)
        object.__setattr__(self, "_support", support)
        object.__setattr__(
            self,
            "_keyboard_port",
            keyboard_port if keyboard_port is not None else UnavailableKeyboardNativePort(),
        )
        object.__setattr__(self, "_descriptor", _SEND_TEXT_DESCRIPTOR)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"WindowsSendTextCapability is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"WindowsSendTextCapability is immutable; cannot delete {name!r}")

    @property
    def descriptor(self) -> CapabilityDescriptor:
        """The inert canonical descriptor governing this capability."""
        return self._descriptor

    def __repr__(self) -> str:
        return (
            f"WindowsSendTextCapability(identity={self._descriptor.identity!s}, "
            f"host={self._support.status.value!r})"
        )

    def execute(
        self,
        request: CapabilityRequest[SendTextParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        """Send the explicit bounded text payload (native acceptance only)."""
        if not isinstance(request.params, SendTextParams):
            raise TypeError(
                f"send_text requires SendTextParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return _cancelled_execution("send_text")
        if not self._support.is_supported:
            return _execution_failure(
                unsupported_platform_error(self._support),
                summary="send_text refused on an unsupported host",
            )
        result = self._keyboard_port.send_text(request.params.text)
        if result.is_failure:
            return _execution_failure(result.unwrap_error(), summary="send_text failed")
        raw = result.unwrap()
        if not isinstance(raw, RawTextSend) or type(raw.character_count) is not int:
            return _execution_failure(
                _malformed_outcome_error("send_text", "a RawTextSend outcome"),
                summary="send_text returned a malformed native outcome",
            )
        expected = len(request.params.text)
        if raw.character_count != expected:
            return _execution_failure(
                _count_mismatch_error("send_text", expected, raw.character_count),
                summary="send_text native count mismatch",
            )
        return ExecutionResult(
            succeeded=True,
            message=f"requested entry of {expected} character(s); {UNVERIFIED_MESSAGE_SUFFIX}",
            observation=CapabilityObservation(
                summary="bounded Unicode text entry requested (unverified)",
                data={"character_count": expected, "input_kind": "unicode_text"},
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[SendTextParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        """Explicit unverified verdict; native acceptance proves nothing here."""
        if not isinstance(request.params, SendTextParams):
            raise TypeError(
                f"send_text requires SendTextParams, got {type(request.params).__name__}"
            )
        if not isinstance(observation, CapabilityObservation):
            raise TypeError(
                f"observation must be a CapabilityObservation, got {type(observation).__name__}"
            )
        if context.observe_stop().should_stop:
            return _unsupported_verification("send_text")
        return _unsupported_verification("send_text")


class WindowsSendKeysCapability:
    """Governed bounded key-chord input (``windows.keyboard.send_keys``)."""

    __slots__ = ("_descriptor", "_keyboard_port", "_support")

    _support: WindowsSupport
    _keyboard_port: KeyboardNativePort
    _descriptor: CapabilityDescriptor

    def __init__(
        self, support: WindowsSupport, keyboard_port: KeyboardNativePort | None = None
    ) -> None:
        """Bind the capability to one support verdict and one keyboard port."""
        _require_support(support)
        object.__setattr__(self, "_support", support)
        object.__setattr__(
            self,
            "_keyboard_port",
            keyboard_port if keyboard_port is not None else UnavailableKeyboardNativePort(),
        )
        object.__setattr__(self, "_descriptor", _SEND_KEYS_DESCRIPTOR)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"WindowsSendKeysCapability is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"WindowsSendKeysCapability is immutable; cannot delete {name!r}")

    @property
    def descriptor(self) -> CapabilityDescriptor:
        """The inert canonical descriptor governing this capability."""
        return self._descriptor

    def __repr__(self) -> str:
        return (
            f"WindowsSendKeysCapability(identity={self._descriptor.identity!s}, "
            f"host={self._support.status.value!r})"
        )

    def execute(
        self,
        request: CapabilityRequest[SendKeysParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        """Send the explicit bounded key-chord sequence (native acceptance only)."""
        if not isinstance(request.params, SendKeysParams):
            raise TypeError(
                f"send_keys requires SendKeysParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return _cancelled_execution("send_keys")
        if not self._support.is_supported:
            return _execution_failure(
                unsupported_platform_error(self._support),
                summary="send_keys refused on an unsupported host",
            )
        risk = classify_key_sequence_risk(request.params.chords)
        result = self._keyboard_port.send_keys(request.params.chords)
        if result.is_failure:
            return _execution_failure(result.unwrap_error(), summary="send_keys failed")
        raw = result.unwrap()
        if not isinstance(raw, RawKeySend) or type(raw.chord_count) is not int:
            return _execution_failure(
                _malformed_outcome_error("send_keys", "a RawKeySend outcome"),
                summary="send_keys returned a malformed native outcome",
            )
        expected = len(request.params.chords)
        if raw.chord_count != expected:
            return _execution_failure(
                _count_mismatch_error("send_keys", expected, raw.chord_count),
                summary="send_keys native count mismatch",
            )
        contains_critical = any(chord in CRITICAL_KEY_CHORDS for chord in request.params.chords)
        return ExecutionResult(
            succeeded=True,
            message=(
                f"requested input of {expected} key chord(s) at risk {risk.level.value}; "
                f"{UNVERIFIED_MESSAGE_SUFFIX}"
            ),
            observation=CapabilityObservation(
                summary="bounded key-chord sequence requested (unverified)",
                data={
                    "chord_count": expected,
                    "keys": [chord.key.value for chord in request.params.chords],
                    "contains_critical_chord": contains_critical,
                    "request_risk_level": risk.level.value,
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[SendKeysParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        """Explicit unverified verdict; native acceptance proves nothing here."""
        if not isinstance(request.params, SendKeysParams):
            raise TypeError(
                f"send_keys requires SendKeysParams, got {type(request.params).__name__}"
            )
        if not isinstance(observation, CapabilityObservation):
            raise TypeError(
                f"observation must be a CapabilityObservation, got {type(observation).__name__}"
            )
        if context.observe_stop().should_stop:
            return _unsupported_verification("send_keys")
        return _unsupported_verification("send_keys")


class WindowsClipboardReadTextCapability:
    """Governed bounded clipboard text read (``windows.clipboard.read_text``)."""

    __slots__ = ("_clipboard_port", "_descriptor", "_support")

    _support: WindowsSupport
    _clipboard_port: ClipboardNativePort
    _descriptor: CapabilityDescriptor

    def __init__(
        self, support: WindowsSupport, clipboard_port: ClipboardNativePort | None = None
    ) -> None:
        """Bind the capability to one support verdict and one clipboard port."""
        _require_support(support)
        object.__setattr__(self, "_support", support)
        object.__setattr__(
            self,
            "_clipboard_port",
            clipboard_port if clipboard_port is not None else UnavailableClipboardNativePort(),
        )
        object.__setattr__(self, "_descriptor", _CLIPBOARD_READ_DESCRIPTOR)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            f"WindowsClipboardReadTextCapability is immutable; cannot set {name!r}"
        )

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"WindowsClipboardReadTextCapability is immutable; cannot delete {name!r}"
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        """The inert canonical descriptor governing this capability."""
        return self._descriptor

    def __repr__(self) -> str:
        return (
            f"WindowsClipboardReadTextCapability(identity={self._descriptor.identity!s}, "
            f"host={self._support.status.value!r})"
        )

    def execute(
        self,
        request: CapabilityRequest[ClipboardReadTextParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        """Read the bounded clipboard text; absence is an explicit state."""
        if not isinstance(request.params, ClipboardReadTextParams):
            raise TypeError(
                f"read_text requires ClipboardReadTextParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return _cancelled_execution("read_text")
        if not self._support.is_supported:
            return _execution_failure(
                unsupported_platform_error(self._support),
                summary="read_text refused on an unsupported host",
            )
        result = self._clipboard_port.read_text()
        if result.is_failure:
            return _execution_failure(result.unwrap_error(), summary="read_text failed")
        raw: object = result.unwrap()
        if not isinstance(raw, RawClipboardText):
            return _execution_failure(
                _malformed_outcome_error("read_text", "a RawClipboardText outcome"),
                summary="read_text returned a malformed native outcome",
            )
        if raw.text is None:
            return ExecutionResult(
                succeeded=True,
                message="clipboard read succeeded: no text format present; "
                + UNVERIFIED_MESSAGE_SUFFIX,
                observation=CapabilityObservation(
                    summary="clipboard text read: no text format present (unverified)",
                    data={"has_text": False, "character_count": 0, "text": ""},
                ),
            )
        if len(raw.text) > MAX_CLIPBOARD_TEXT_LENGTH:
            return _execution_failure(
                AgentXError(
                    code=CLIPBOARD_CONTENT_TOO_LARGE_ERROR_CODE,
                    message="clipboard text exceeds the governed read bound",
                    category=ErrorCategory.RESOURCE,
                    retryability=Retryability.NON_RETRYABLE,
                    details={"max_length": MAX_CLIPBOARD_TEXT_LENGTH},
                ),
                summary="clipboard text exceeded the governed read bound",
            )
        return ExecutionResult(
            succeeded=True,
            message=(
                f"read {len(raw.text)} character(s) of clipboard text; " + UNVERIFIED_MESSAGE_SUFFIX
            ),
            observation=CapabilityObservation(
                summary="bounded clipboard Unicode text read (unverified)",
                data={
                    "has_text": True,
                    "character_count": len(raw.text),
                    "text": raw.text,
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[ClipboardReadTextParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        """Explicit unverified verdict; a read cannot prove its own truth."""
        if not isinstance(request.params, ClipboardReadTextParams):
            raise TypeError(
                f"read_text requires ClipboardReadTextParams, got {type(request.params).__name__}"
            )
        if not isinstance(observation, CapabilityObservation):
            raise TypeError(
                f"observation must be a CapabilityObservation, got {type(observation).__name__}"
            )
        if context.observe_stop().should_stop:
            return _unsupported_verification("read_text")
        return _unsupported_verification("read_text")


class WindowsClipboardWriteTextCapability:
    """Governed bounded clipboard text write (``windows.clipboard.write_text``)."""

    __slots__ = ("_clipboard_port", "_descriptor", "_support")

    _support: WindowsSupport
    _clipboard_port: ClipboardNativePort
    _descriptor: CapabilityDescriptor

    def __init__(
        self, support: WindowsSupport, clipboard_port: ClipboardNativePort | None = None
    ) -> None:
        """Bind the capability to one support verdict and one clipboard port."""
        _require_support(support)
        object.__setattr__(self, "_support", support)
        object.__setattr__(
            self,
            "_clipboard_port",
            clipboard_port if clipboard_port is not None else UnavailableClipboardNativePort(),
        )
        object.__setattr__(self, "_descriptor", _CLIPBOARD_WRITE_DESCRIPTOR)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            f"WindowsClipboardWriteTextCapability is immutable; cannot set {name!r}"
        )

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"WindowsClipboardWriteTextCapability is immutable; cannot delete {name!r}"
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        """The inert canonical descriptor governing this capability."""
        return self._descriptor

    def __repr__(self) -> str:
        return (
            f"WindowsClipboardWriteTextCapability(identity={self._descriptor.identity!s}, "
            f"host={self._support.status.value!r})"
        )

    def execute(
        self,
        request: CapabilityRequest[ClipboardWriteTextParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        """Write the explicit bounded text to the clipboard (acceptance only)."""
        if not isinstance(request.params, ClipboardWriteTextParams):
            raise TypeError(
                f"write_text requires ClipboardWriteTextParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return _cancelled_execution("write_text")
        if not self._support.is_supported:
            return _execution_failure(
                unsupported_platform_error(self._support),
                summary="write_text refused on an unsupported host",
            )
        result = self._clipboard_port.write_text(request.params.text)
        if result.is_failure:
            return _execution_failure(result.unwrap_error(), summary="write_text failed")
        raw = result.unwrap()
        if not isinstance(raw, RawClipboardWrite) or type(raw.character_count) is not int:
            return _execution_failure(
                _malformed_outcome_error("write_text", "a RawClipboardWrite outcome"),
                summary="write_text returned a malformed native outcome",
            )
        expected = len(request.params.text)
        if raw.character_count != expected:
            return _execution_failure(
                _count_mismatch_error("write_text", expected, raw.character_count),
                summary="write_text native count mismatch",
            )
        return ExecutionResult(
            succeeded=True,
            message=(
                f"wrote {expected} character(s) to the clipboard; " + UNVERIFIED_MESSAGE_SUFFIX
            ),
            observation=CapabilityObservation(
                summary="bounded clipboard Unicode text write requested (unverified)",
                data={"character_count": expected, "input_kind": "clipboard_text"},
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[ClipboardWriteTextParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        """Explicit unverified verdict; acceptance proves no consumer state."""
        if not isinstance(request.params, ClipboardWriteTextParams):
            raise TypeError(
                f"write_text requires ClipboardWriteTextParams, got {type(request.params).__name__}"
            )
        if not isinstance(observation, CapabilityObservation):
            raise TypeError(
                f"observation must be a CapabilityObservation, got {type(observation).__name__}"
            )
        if context.observe_stop().should_stop:
            return _unsupported_verification("write_text")
        return _unsupported_verification("write_text")


class WindowsClipboardClearCapability:
    """Governed explicit clipboard clear (``windows.clipboard.clear``)."""

    __slots__ = ("_clipboard_port", "_descriptor", "_support")

    _support: WindowsSupport
    _clipboard_port: ClipboardNativePort
    _descriptor: CapabilityDescriptor

    def __init__(
        self, support: WindowsSupport, clipboard_port: ClipboardNativePort | None = None
    ) -> None:
        """Bind the capability to one support verdict and one clipboard port."""
        _require_support(support)
        object.__setattr__(self, "_support", support)
        object.__setattr__(
            self,
            "_clipboard_port",
            clipboard_port if clipboard_port is not None else UnavailableClipboardNativePort(),
        )
        object.__setattr__(self, "_descriptor", _CLIPBOARD_CLEAR_DESCRIPTOR)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"WindowsClipboardClearCapability is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"WindowsClipboardClearCapability is immutable; cannot delete {name!r}"
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        """The inert canonical descriptor governing this capability."""
        return self._descriptor

    def __repr__(self) -> str:
        return (
            f"WindowsClipboardClearCapability(identity={self._descriptor.identity!s}, "
            f"host={self._support.status.value!r})"
        )

    def execute(
        self,
        request: CapabilityRequest[ClipboardClearParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        """Clear the clipboard explicitly (acceptance only)."""
        if not isinstance(request.params, ClipboardClearParams):
            raise TypeError(
                f"clear requires ClipboardClearParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return _cancelled_execution("clear")
        if not self._support.is_supported:
            return _execution_failure(
                unsupported_platform_error(self._support),
                summary="clear refused on an unsupported host",
            )
        result = self._clipboard_port.clear()
        if result.is_failure:
            return _execution_failure(result.unwrap_error(), summary="clear failed")
        raw: object = result.unwrap()
        if not isinstance(raw, RawClipboardClear) or type(raw.previous_was_empty) is not bool:
            return _execution_failure(
                _malformed_outcome_error("clear", "a RawClipboardClear outcome"),
                summary="clear returned a malformed native outcome",
            )
        previous = "empty" if raw.previous_was_empty else "not empty"
        return ExecutionResult(
            succeeded=True,
            message=f"clipboard cleared (previous was {previous}); " + UNVERIFIED_MESSAGE_SUFFIX,
            observation=CapabilityObservation(
                summary="explicit clipboard clear requested (unverified)",
                data={"previous_was_empty": raw.previous_was_empty},
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[ClipboardClearParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        """Explicit unverified verdict; acceptance proves no consumer state."""
        if not isinstance(request.params, ClipboardClearParams):
            raise TypeError(
                f"clear requires ClipboardClearParams, got {type(request.params).__name__}"
            )
        if not isinstance(observation, CapabilityObservation):
            raise TypeError(
                f"observation must be a CapabilityObservation, got {type(observation).__name__}"
            )
        if context.observe_stop().should_stop:
            return _unsupported_verification("clear")
        return _unsupported_verification("clear")


# --------------------------------------------------------------------------
# Canonical request builders (convenience).
# --------------------------------------------------------------------------


def send_text_request(text: str) -> CapabilityRequest[SendTextParams]:
    """Build a canonical request for ``windows.keyboard.send_text``."""
    return CapabilityRequest(identity=WINDOWS_SEND_TEXT_IDENTITY, params=SendTextParams(text=text))


def send_keys_request(chords: tuple[KeyChord, ...]) -> CapabilityRequest[SendKeysParams]:
    """Build a canonical request for ``windows.keyboard.send_keys``."""
    return CapabilityRequest(
        identity=WINDOWS_SEND_KEYS_IDENTITY, params=SendKeysParams(chords=chords)
    )


def clipboard_read_request() -> CapabilityRequest[ClipboardReadTextParams]:
    """Build a canonical request for ``windows.clipboard.read_text``."""
    return CapabilityRequest(
        identity=WINDOWS_CLIPBOARD_READ_TEXT_IDENTITY, params=ClipboardReadTextParams()
    )


def clipboard_write_request(text: str) -> CapabilityRequest[ClipboardWriteTextParams]:
    """Build a canonical request for ``windows.clipboard.write_text``."""
    return CapabilityRequest(
        identity=WINDOWS_CLIPBOARD_WRITE_TEXT_IDENTITY,
        params=ClipboardWriteTextParams(text=text),
    )


def clipboard_clear_request() -> CapabilityRequest[ClipboardClearParams]:
    """Build a canonical request for ``windows.clipboard.clear``."""
    return CapabilityRequest(
        identity=WINDOWS_CLIPBOARD_CLEAR_IDENTITY, params=ClipboardClearParams()
    )
