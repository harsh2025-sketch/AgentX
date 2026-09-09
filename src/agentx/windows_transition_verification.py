"""Windows state-transition verification boundary (N2.25).

This module is the canonical verification boundary for Windows mutations. It
exists to enforce one sentence, structurally::

    NATIVE API SUCCESS != INTENDED USER-VISIBLE STATE SUCCESS

A Win32/UIA call that returns ``TRUE`` proves that a native entry point
accepted a request. It proves nothing about the state a user would see. This
module therefore decides a requested Windows transition **only** from typed
post-state observation evidence produced by the canonical read-only Windows
observation contracts, and never from an execution return value.

What it consumes
----------------

Exactly four explicit, typed inputs, all caller-supplied::

    exact operation identity   (CapabilityIdentity + attempt id + environment)
    + requested transition     (WindowsTransitionKind + target + requested state)
    + pre-state observation    (canonical Windows snapshot, explicitly identified)
    + post-state observation   (canonical Windows snapshot, explicitly identified)
        -> WindowsTransitionVerification

The result vocabulary is a three-valued verdict — ``VERIFIED``,
``NOT_VERIFIED``, ``INSUFFICIENT_EVIDENCE`` — because "we cannot tell" is a
distinct and safety-relevant answer from "we can tell it did not happen".

Observation contracts reused (no new truth system)
--------------------------------------------------

Only canonical baseline observations are consumed; this module defines no
observation of its own and performs no reads:

    - A5.02 :class:`~agentx.capabilities.windows.process_discovery.WindowsProcessSnapshot`
      — top-level window enumeration (handle, owning PID, visibility) and
      process enumeration (PID, executable name/path with explicit
      :class:`~agentx.capabilities.windows.process_discovery.MetadataStatus`,
      owned window handles, visible window count).
    - A5.03 :class:`~agentx.capabilities.windows.uia_tree.UIATreeSnapshot`
      — bounded point-in-time UI Automation tree with ``captured_at``,
      truncation flags, and closed-vocabulary property observations
      (``has_keyboard_focus``, ``bounding_rectangle``, ``value``,
      ``process_id``).
    - A1.08 :class:`~agentx.capabilities.abi.ExecutionResult` — carried only as
      *execution* evidence (see below), never as verification evidence.

If the baseline exposes no observation that could decide a transition kind —
window minimize/maximize/restore visual state, clipboard content, application
readiness, key delivery — the verdict is always ``INSUFFICIENT_EVIDENCE``.
No visual/OCR fallback is invented, no live UIA call is made, and no model is
consulted.

Native return is not verification
---------------------------------

:class:`NativeExecutionEvidence` may be attached to a request. It is recorded
in the emitted evidence as ``native_reported_success`` for audit only. The
verdict functions never read it: the same request with
``ExecutionResult(succeeded=True)`` and with ``succeeded=False`` produces the
same verdict, and a request with a native success but no post-state
observation is ``INSUFFICIENT_EVIDENCE`` — never ``VERIFIED``.

Exact request binding
---------------------

Evidence for window A can never verify window B. Every verdict is bound to the
exact capability/operation identity, the exact attempt, the exact environment
identity, the exact target identity (window handle plus optional owning PID,
process id, or canonical
:class:`~agentx.capabilities.windows.uia_tree.UIAElementReference` including
runtime id), and the exact pre/post observation identities. A UIA snapshot
rooted at a different window, an element whose runtime id changed, or a window
handle now owned by a different process yields ``INSUFFICIENT_EVIDENCE``
rather than a convenient match.

Freshness / staleness
---------------------

Only the canonical freshness model is used; no wall-clock threshold is
invented, because the baseline defines none:

    - pre-state and post-state must carry *distinct* observation identities —
      one snapshot cannot be both the before and the after of a mutation;
    - where the canonical contract carries a timestamp (A5.03
      ``UIATreeSnapshot.captured_at``), the post-state must be strictly newer
      than the pre-state;
    - the A5.02 snapshot carries no canonical timestamp, so no time judgment
      is made about it — identity distinctness is the only staleness rule
      the baseline supports;
    - environment identity must agree across request, pre-state and post-state.

Verification is not task success
--------------------------------

A ``VERIFIED`` transition says one local Windows state predicate held in one
post-state observation. It does not say the user's Task succeeded. This module
emits evidence only: it transitions no Task, touches no Task contract, and the
canonical task verifier/orchestration remains the sole owner of task-level
success.

Hostile observations are inert
------------------------------

Observed window titles, element names/values and process paths may contain
``verified=true``, ``task_success=true``, ``permission=ADMIN``, ``risk=R0`` or
``skip_action_gate=true``. They are ordinary observed data. Verdicts come from
typed field equality and state predicates on closed vocabularies only; no
observed string is ever parsed as an instruction, and reason codes are a fixed
closed vocabulary that never echoes observed content.

No actions, no authority
------------------------

This module performs no mutation: no Win32 call, no UIA invocation, no click,
no typing, no launch, no clipboard write, no capability execution. It imports
no kernel module, so it cannot call ``ActionGate``, grant ``Permission``,
alter ``RiskAssessment`` or ``ResourceEnvelope``, or clear ``EmergencyStop``.
It is stateless, pure and deterministic: identical inputs always produce an
equal result.

Owner: N2.25. It lives at the ``agentx`` namespace root (alongside
``agentx.procedure_validation``) because it composes canonical
``agentx.capabilities`` observation/execution contracts with nothing else; it
adds no subsystem and widens no boundary edge.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from agentx.capabilities.abi import CapabilityIdentity, ExecutionResult
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
    UIAObservationStatus,
    UIAPropertyName,
    UIATreeSnapshot,
)
from agentx.core.tasks import JsonValue

__all__ = [
    "REQUIRED_OBSERVATION_CONTRACT",
    "SUPPORTED_TRANSITION_KINDS",
    "UNVERIFIABLE_TRANSITION_KINDS",
    "WINDOWS_TRANSITION_VERIFICATION_SOURCE",
    "NativeExecutionEvidence",
    "WindowsElementTarget",
    "WindowsObservationContract",
    "WindowsObservationEvidence",
    "WindowsProcessTarget",
    "WindowsRequestedState",
    "WindowsSessionTarget",
    "WindowsTransitionKind",
    "WindowsTransitionRequest",
    "WindowsTransitionRequestError",
    "WindowsTransitionTarget",
    "WindowsTransitionVerdict",
    "WindowsTransitionVerification",
    "WindowsTransitionVerifier",
    "WindowsVerificationReason",
    "WindowsWindowTarget",
]

#: Stable identifier of this boundary, for reporting and documentation. The
#: verifier publishes no events and persists nothing.
WINDOWS_TRANSITION_VERIFICATION_SOURCE: Final[str] = "agentx.windows_transition_verification"

_MAX_IDENTIFIER_LENGTH: Final[int] = 128
_MAX_EXECUTABLE_NAME_LENGTH: Final[int] = 260
_MAX_EXPECTED_TEXT_LENGTH: Final[int] = 4096
_MAX_COORDINATE: Final[float] = 1_000_000.0
_MAX_TOLERANCE: Final[float] = 10_000.0

_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")
_PATH_SEPARATORS: Final[tuple[str, ...]] = ("\\", "/", ":")


class WindowsTransitionRequestError(ValueError):
    """Raised when a transition request or its evidence is malformed.

    This is a request-shape error only. It never carries, encodes, or implies
    a verdict: verdicts are :class:`WindowsTransitionVerification` values.
    """


# ---------------------------------------------------------------------------
# Closed vocabularies.
# ---------------------------------------------------------------------------


class WindowsTransitionVerdict(StrEnum):
    """The complete three-valued verdict vocabulary.

    ``INSUFFICIENT_EVIDENCE`` is deliberately distinct from ``NOT_VERIFIED``:
    "the baseline cannot observe this" and "the observation says the requested
    state does not hold" are different facts, and collapsing them would let a
    caller mistake blindness for a negative result.
    """

    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class WindowsObservationContract(StrEnum):
    """Which canonical baseline observation contract decides a transition kind.

    ``NONE`` marks transition kinds the canonical baseline cannot observe at
    all; they are always ``INSUFFICIENT_EVIDENCE``.
    """

    PROCESS_SNAPSHOT = "windows_process_snapshot"
    UIA_TREE_SNAPSHOT = "uia_tree_snapshot"
    NONE = "none"


class WindowsTransitionKind(StrEnum):
    """Exact requested-transition vocabulary.

    Window kinds decided by the A5.02 enumeration:

    - ``WINDOW_PRESENT`` / ``WINDOW_ABSENT`` — the handle does (not) appear in
      the top-level window enumeration.
    - ``WINDOW_VISIBLE`` / ``WINDOW_HIDDEN`` — the enumerated window's
      ``is_visible`` flag (Win32 ``IsWindowVisible`` semantics). This is *not*
      a pixel-visibility claim and *not* a restored/minimized claim: Windows
      reports a minimized window as visible.

    Window kinds decided by the A5.03 bounded UIA snapshot:

    - ``WINDOW_FOCUSED`` — keyboard focus observed inside the snapshot rooted
      at the target window.
    - ``WINDOW_BOUNDS`` — the root element's bounding rectangle matches the
      requested rectangle within an explicit tolerance (move/resize).

    Application kinds decided by the A5.02 enumeration:

    - ``APPLICATION_PROCESS_PRESENT`` / ``APPLICATION_PROCESS_ABSENT``.
    - ``APPLICATION_EXECUTABLE_IDENTITY`` — the observed image name of that PID
      equals the requested executable name (Windows case-insensitive).
    - ``APPLICATION_PRESENTS_WINDOW`` — that PID currently owns at least one
      visible top-level window. This is explicitly *not* readiness.

    Text kind decided by the A5.03 snapshot:

    - ``TEXT_FIELD_VALUE`` — the canonical ``value`` property observation of
      the exact target element equals the requested text. Text entry is only
      ever verified through this post-state observation of the field itself.

    Kinds the canonical baseline cannot observe (always
    ``INSUFFICIENT_EVIDENCE``): ``WINDOW_MINIMIZED``, ``WINDOW_MAXIMIZED``,
    ``WINDOW_RESTORED`` (no canonical window visual-state observation),
    ``APPLICATION_READY`` (no canonical readiness observation),
    ``CLIPBOARD_TEXT`` (no canonical clipboard observation surface), and
    ``KEY_SEQUENCE_SENT`` (no canonical post-state observation of key
    delivery).
    """

    WINDOW_PRESENT = "window_present"
    WINDOW_ABSENT = "window_absent"
    WINDOW_VISIBLE = "window_visible"
    WINDOW_HIDDEN = "window_hidden"
    WINDOW_FOCUSED = "window_focused"
    WINDOW_BOUNDS = "window_bounds"
    WINDOW_MINIMIZED = "window_minimized"
    WINDOW_MAXIMIZED = "window_maximized"
    WINDOW_RESTORED = "window_restored"
    APPLICATION_PROCESS_PRESENT = "application_process_present"
    APPLICATION_PROCESS_ABSENT = "application_process_absent"
    APPLICATION_EXECUTABLE_IDENTITY = "application_executable_identity"
    APPLICATION_PRESENTS_WINDOW = "application_presents_window"
    APPLICATION_READY = "application_ready"
    TEXT_FIELD_VALUE = "text_field_value"
    KEY_SEQUENCE_SENT = "key_sequence_sent"
    CLIPBOARD_TEXT = "clipboard_text"


class WindowsVerificationReason(StrEnum):
    """Deterministic closed reason vocabulary.

    Reasons never echo observed content: hostile window titles, element values
    or executable paths cannot leak through a reason code.
    """

    OBSERVED_STATE_MATCHES_REQUEST = "observed_state_matches_request"
    OBSERVED_STATE_DIFFERS_FROM_REQUEST = "observed_state_differs_from_request"
    NO_CANONICAL_OBSERVATION_CONTRACT = "no_canonical_observation_contract"
    PRE_STATE_OBSERVATION_MISSING = "pre_state_observation_missing"
    POST_STATE_OBSERVATION_MISSING = "post_state_observation_missing"
    OBSERVATION_CONTRACT_MISMATCH = "observation_contract_mismatch"
    ENVIRONMENT_IDENTITY_MISMATCH = "environment_identity_mismatch"
    POST_STATE_NOT_DISTINCT_FROM_PRE_STATE = "post_state_not_distinct_from_pre_state"
    STALE_POST_STATE_OBSERVATION = "stale_post_state_observation"
    TARGET_IDENTITY_MISMATCH = "target_identity_mismatch"
    TARGET_NOT_OBSERVED = "target_not_observed"
    OBSERVED_PROPERTY_UNAVAILABLE = "observed_property_unavailable"
    TARGET_METADATA_UNAVAILABLE = "target_metadata_unavailable"
    INCOMPLETE_OBSERVATION = "incomplete_observation"
    NATIVE_RESULT_IS_NOT_VERIFICATION = "native_result_is_not_verification"


#: Which canonical observation contract each transition kind requires. Kinds
#: mapped to ``NONE`` cannot be decided from the canonical baseline at all.
REQUIRED_OBSERVATION_CONTRACT: Final[Mapping[WindowsTransitionKind, WindowsObservationContract]] = (
    MappingProxyType(
        {
            WindowsTransitionKind.WINDOW_PRESENT: WindowsObservationContract.PROCESS_SNAPSHOT,
            WindowsTransitionKind.WINDOW_ABSENT: WindowsObservationContract.PROCESS_SNAPSHOT,
            WindowsTransitionKind.WINDOW_VISIBLE: WindowsObservationContract.PROCESS_SNAPSHOT,
            WindowsTransitionKind.WINDOW_HIDDEN: WindowsObservationContract.PROCESS_SNAPSHOT,
            WindowsTransitionKind.WINDOW_FOCUSED: WindowsObservationContract.UIA_TREE_SNAPSHOT,
            WindowsTransitionKind.WINDOW_BOUNDS: WindowsObservationContract.UIA_TREE_SNAPSHOT,
            WindowsTransitionKind.WINDOW_MINIMIZED: WindowsObservationContract.NONE,
            WindowsTransitionKind.WINDOW_MAXIMIZED: WindowsObservationContract.NONE,
            WindowsTransitionKind.WINDOW_RESTORED: WindowsObservationContract.NONE,
            WindowsTransitionKind.APPLICATION_PROCESS_PRESENT: (
                WindowsObservationContract.PROCESS_SNAPSHOT
            ),
            WindowsTransitionKind.APPLICATION_PROCESS_ABSENT: (
                WindowsObservationContract.PROCESS_SNAPSHOT
            ),
            WindowsTransitionKind.APPLICATION_EXECUTABLE_IDENTITY: (
                WindowsObservationContract.PROCESS_SNAPSHOT
            ),
            WindowsTransitionKind.APPLICATION_PRESENTS_WINDOW: (
                WindowsObservationContract.PROCESS_SNAPSHOT
            ),
            WindowsTransitionKind.APPLICATION_READY: WindowsObservationContract.NONE,
            WindowsTransitionKind.TEXT_FIELD_VALUE: WindowsObservationContract.UIA_TREE_SNAPSHOT,
            WindowsTransitionKind.KEY_SEQUENCE_SENT: WindowsObservationContract.NONE,
            WindowsTransitionKind.CLIPBOARD_TEXT: WindowsObservationContract.NONE,
        }
    )
)

#: Transition kinds the canonical baseline cannot observe. They are declared
#: (so a caller can name the transition it attempted) but never verifiable.
UNVERIFIABLE_TRANSITION_KINDS: Final[frozenset[WindowsTransitionKind]] = frozenset(
    kind
    for kind, contract in REQUIRED_OBSERVATION_CONTRACT.items()
    if contract is WindowsObservationContract.NONE
)

#: Transition kinds that a canonical baseline observation can decide.
SUPPORTED_TRANSITION_KINDS: Final[frozenset[WindowsTransitionKind]] = frozenset(
    set(WindowsTransitionKind) - UNVERIFIABLE_TRANSITION_KINDS
)


# ---------------------------------------------------------------------------
# Validation helpers.
# ---------------------------------------------------------------------------


def _validate_identifier(value: object, *, field_name: str, max_length: int) -> str:
    """Validate one bounded, trimmed, control-character-free identifier."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise WindowsTransitionRequestError(f"{field_name} must be non-empty and trimmed")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise WindowsTransitionRequestError(f"{field_name} must not contain control characters")
    if len(value) > max_length:
        raise WindowsTransitionRequestError(f"{field_name} must not exceed {max_length} characters")
    return value


def _validate_positive_int(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if value < 1:
        raise WindowsTransitionRequestError(f"{field_name} must be a positive integer")
    return value


def _validate_process_id(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise WindowsTransitionRequestError(f"{field_name} must be a non-negative integer")
    return value


def _validate_coordinate(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} must be a real number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise WindowsTransitionRequestError(f"{field_name} must be finite")
    if abs(number) > _MAX_COORDINATE:
        raise WindowsTransitionRequestError(
            f"{field_name} must be within +/-{_MAX_COORDINATE} device units"
        )
    return number


# ---------------------------------------------------------------------------
# Targets: exact identity of what the transition was requested for.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowsWindowTarget:
    """Exact identity of one target top-level window.

    ``handle`` is the Win32 HWND as of the transition attempt. Handles are
    recycled by the OS, so ``process_id`` may be supplied as an additional
    binding: when it is, an observation whose window handle belongs to a
    different process cannot verify anything about this target.
    """

    handle: int
    process_id: int | None = None

    def __post_init__(self) -> None:
        _validate_positive_int(self.handle, field_name="window target handle")
        if self.process_id is not None:
            _validate_process_id(self.process_id, field_name="window target process_id")

    @property
    def descriptor(self) -> str:
        """Deterministic bounded identity string (no observed content)."""
        if self.process_id is None:
            return f"window:{self.handle}"
        return f"window:{self.handle}@pid:{self.process_id}"


@dataclass(frozen=True, slots=True)
class WindowsProcessTarget:
    """Exact identity of one target process."""

    process_id: int

    def __post_init__(self) -> None:
        _validate_process_id(self.process_id, field_name="process target process_id")

    @property
    def descriptor(self) -> str:
        """Deterministic bounded identity string (no observed content)."""
        return f"process:{self.process_id}"


@dataclass(frozen=True, slots=True)
class WindowsElementTarget:
    """Exact identity of one target UI element.

    The canonical A5.03
    :class:`~agentx.capabilities.windows.uia_tree.UIAElementReference` is
    reused verbatim: root window handle, snapshot-local path, and the optional
    native runtime id. When the runtime id is present it is an additional
    binding — an element at the same path with a different runtime id is a
    different element and verifies nothing.
    """

    reference: UIAElementReference

    def __post_init__(self) -> None:
        if not isinstance(self.reference, UIAElementReference):
            raise TypeError(
                f"element target reference must be a UIAElementReference, "
                f"got {type(self.reference).__name__}"
            )

    @property
    def descriptor(self) -> str:
        """Deterministic bounded identity string (no observed content)."""
        path = ".".join(str(index) for index in self.reference.path) or "root"
        runtime = (
            "-".join(str(item) for item in self.reference.runtime_id)
            if self.reference.runtime_id is not None
            else "none"
        )
        return f"element:{self.reference.root_window_handle}/{path}#{runtime}"


@dataclass(frozen=True, slots=True)
class WindowsSessionTarget:
    """Identity of a session-scoped target (for example the clipboard).

    ``label`` is caller-supplied request data, bounded and inert; it is never
    parsed and never grants anything.
    """

    label: str

    def __post_init__(self) -> None:
        _validate_identifier(self.label, field_name="session target label", max_length=64)

    @property
    def descriptor(self) -> str:
        """Deterministic bounded identity string (no observed content)."""
        return f"session:{self.label}"


#: Every target shape the verifier accepts.
WindowsTransitionTarget = (
    WindowsWindowTarget | WindowsProcessTarget | WindowsElementTarget | WindowsSessionTarget
)

_ALLOWED_TARGET_TYPES: Final[Mapping[WindowsTransitionKind, tuple[type, ...]]] = MappingProxyType(
    {
        WindowsTransitionKind.WINDOW_PRESENT: (WindowsWindowTarget,),
        WindowsTransitionKind.WINDOW_ABSENT: (WindowsWindowTarget,),
        WindowsTransitionKind.WINDOW_VISIBLE: (WindowsWindowTarget,),
        WindowsTransitionKind.WINDOW_HIDDEN: (WindowsWindowTarget,),
        WindowsTransitionKind.WINDOW_FOCUSED: (WindowsWindowTarget,),
        WindowsTransitionKind.WINDOW_BOUNDS: (WindowsWindowTarget,),
        WindowsTransitionKind.WINDOW_MINIMIZED: (WindowsWindowTarget,),
        WindowsTransitionKind.WINDOW_MAXIMIZED: (WindowsWindowTarget,),
        WindowsTransitionKind.WINDOW_RESTORED: (WindowsWindowTarget,),
        WindowsTransitionKind.APPLICATION_PROCESS_PRESENT: (WindowsProcessTarget,),
        WindowsTransitionKind.APPLICATION_PROCESS_ABSENT: (WindowsProcessTarget,),
        WindowsTransitionKind.APPLICATION_EXECUTABLE_IDENTITY: (WindowsProcessTarget,),
        WindowsTransitionKind.APPLICATION_PRESENTS_WINDOW: (WindowsProcessTarget,),
        WindowsTransitionKind.APPLICATION_READY: (WindowsProcessTarget,),
        WindowsTransitionKind.TEXT_FIELD_VALUE: (WindowsElementTarget,),
        WindowsTransitionKind.KEY_SEQUENCE_SENT: (WindowsWindowTarget, WindowsElementTarget),
        WindowsTransitionKind.CLIPBOARD_TEXT: (WindowsSessionTarget,),
    }
)


# ---------------------------------------------------------------------------
# Requested state: the exact state the caller asked Windows to reach.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowsRequestedState:
    """The exact requested end state, as typed values (never free text rules).

    Exactly the fields a transition kind needs must be populated; supplying a
    field a kind does not use is a request error, so an expectation can never
    be silently ignored.
    """

    expected_bounds: tuple[float, float, float, float] | None = None
    bounds_tolerance: float = 0.0
    expected_text: str | None = None
    expected_executable_name: str | None = None

    def __post_init__(self) -> None:
        if self.expected_bounds is not None:
            if not isinstance(self.expected_bounds, tuple) or len(self.expected_bounds) != 4:
                raise WindowsTransitionRequestError(
                    "expected_bounds must be a 4-tuple of (left, top, right, bottom)"
                )
            normalized = tuple(
                _validate_coordinate(item, field_name=f"expected_bounds[{index}]")
                for index, item in enumerate(self.expected_bounds)
            )
            object.__setattr__(self, "expected_bounds", normalized)
        tolerance = _validate_coordinate(self.bounds_tolerance, field_name="bounds_tolerance")
        if tolerance < 0.0 or tolerance > _MAX_TOLERANCE:
            raise WindowsTransitionRequestError(
                f"bounds_tolerance must be between 0 and {_MAX_TOLERANCE}"
            )
        object.__setattr__(self, "bounds_tolerance", tolerance)
        if self.expected_text is not None:
            if not isinstance(self.expected_text, str):
                raise TypeError(
                    f"expected_text must be a string, got {type(self.expected_text).__name__}"
                )
            if len(self.expected_text) > _MAX_EXPECTED_TEXT_LENGTH:
                raise WindowsTransitionRequestError(
                    f"expected_text must not exceed {_MAX_EXPECTED_TEXT_LENGTH} characters"
                )
        if self.expected_executable_name is not None:
            _validate_identifier(
                self.expected_executable_name,
                field_name="expected_executable_name",
                max_length=_MAX_EXECUTABLE_NAME_LENGTH,
            )
            if any(separator in self.expected_executable_name for separator in _PATH_SEPARATORS):
                raise WindowsTransitionRequestError(
                    "expected_executable_name must be an image base name, not a path"
                )

    @property
    def populated_fields(self) -> frozenset[str]:
        """Which expectation fields carry a value (tolerance is a modifier)."""
        populated: set[str] = set()
        if self.expected_bounds is not None:
            populated.add("expected_bounds")
        if self.expected_text is not None:
            populated.add("expected_text")
        if self.expected_executable_name is not None:
            populated.add("expected_executable_name")
        return frozenset(populated)


_REQUIRED_EXPECTATION_FIELDS: Final[Mapping[WindowsTransitionKind, frozenset[str]]] = (
    MappingProxyType(
        {
            WindowsTransitionKind.WINDOW_BOUNDS: frozenset({"expected_bounds"}),
            WindowsTransitionKind.APPLICATION_EXECUTABLE_IDENTITY: frozenset(
                {"expected_executable_name"}
            ),
            WindowsTransitionKind.TEXT_FIELD_VALUE: frozenset({"expected_text"}),
            WindowsTransitionKind.CLIPBOARD_TEXT: frozenset({"expected_text"}),
        }
    )
)


# ---------------------------------------------------------------------------
# Evidence inputs.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowsObservationEvidence:
    """One canonical Windows observation with an explicit snapshot identity.

    The snapshot is a canonical baseline value (A5.02 or A5.03) and is read
    only. ``observation_id`` is the caller's identity for that read: it is how
    a pre-state and a post-state are told apart, and how a consumer can bind
    an emitted verdict back to the exact snapshots it came from.
    ``environment_id`` is the identity of the machine/session the observation
    describes.
    """

    snapshot: WindowsProcessSnapshot | UIATreeSnapshot
    observation_id: str
    environment_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, WindowsProcessSnapshot | UIATreeSnapshot):
            raise TypeError(
                "snapshot must be a canonical WindowsProcessSnapshot or UIATreeSnapshot, "
                f"got {type(self.snapshot).__name__}"
            )
        _validate_identifier(
            self.observation_id,
            field_name="observation_id",
            max_length=_MAX_IDENTIFIER_LENGTH,
        )
        _validate_identifier(
            self.environment_id,
            field_name="environment_id",
            max_length=_MAX_IDENTIFIER_LENGTH,
        )

    @property
    def contract(self) -> WindowsObservationContract:
        """Which canonical observation contract this evidence carries."""
        if isinstance(self.snapshot, UIATreeSnapshot):
            return WindowsObservationContract.UIA_TREE_SNAPSHOT
        return WindowsObservationContract.PROCESS_SNAPSHOT

    @property
    def captured_at(self) -> datetime | None:
        """The canonical capture timestamp, when the contract carries one.

        A5.03 snapshots carry ``captured_at``; the A5.02 snapshot contract
        defines no timestamp, and none is invented here.
        """
        if isinstance(self.snapshot, UIATreeSnapshot):
            return self.snapshot.captured_at
        return None


@dataclass(frozen=True, slots=True)
class NativeExecutionEvidence:
    """Execution evidence from the mutation attempt — never verification.

    The canonical A1.08 :class:`~agentx.capabilities.abi.ExecutionResult` says
    a native entry point accepted a request and produced an observation. It is
    recorded for audit as ``native_reported_success`` and is *never* read by
    any verdict function: a native success cannot create, upgrade, or rescue a
    verdict.
    """

    attempt_id: str
    result: ExecutionResult

    def __post_init__(self) -> None:
        _validate_identifier(
            self.attempt_id, field_name="attempt_id", max_length=_MAX_IDENTIFIER_LENGTH
        )
        if not isinstance(self.result, ExecutionResult):
            raise TypeError(
                f"result must be a canonical ExecutionResult, got {type(self.result).__name__}"
            )


@dataclass(frozen=True, slots=True)
class WindowsTransitionRequest:
    """One fully explicit verification request.

    Nothing is implicit: the operation identity, the attempt, the environment,
    the requested transition, the target, the requested state and both
    observations are all supplied by the caller. There is no permission field,
    no risk or budget field, no Task field, no capability handle, no callable
    predicate, and no model/text field — anything of that shape would smuggle
    authority or a second truth system into a data object.
    """

    operation: CapabilityIdentity
    attempt_id: str
    environment_id: str
    kind: WindowsTransitionKind
    target: WindowsTransitionTarget
    requested_state: WindowsRequestedState = field(default_factory=WindowsRequestedState)
    pre_state: WindowsObservationEvidence | None = None
    post_state: WindowsObservationEvidence | None = None
    native_evidence: NativeExecutionEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, CapabilityIdentity):
            raise TypeError(
                "operation must be a canonical CapabilityIdentity, "
                f"got {type(self.operation).__name__}"
            )
        _validate_identifier(
            self.attempt_id, field_name="attempt_id", max_length=_MAX_IDENTIFIER_LENGTH
        )
        _validate_identifier(
            self.environment_id,
            field_name="environment_id",
            max_length=_MAX_IDENTIFIER_LENGTH,
        )
        if not isinstance(self.kind, WindowsTransitionKind):
            raise TypeError(f"kind must be a WindowsTransitionKind, got {type(self.kind).__name__}")
        allowed_targets = _ALLOWED_TARGET_TYPES[self.kind]
        if not isinstance(self.target, allowed_targets):
            expected = " or ".join(item.__name__ for item in allowed_targets)
            raise WindowsTransitionRequestError(
                f"transition {self.kind.value!r} requires a {expected} target, "
                f"got {type(self.target).__name__}"
            )
        if not isinstance(self.requested_state, WindowsRequestedState):
            raise TypeError(
                "requested_state must be a WindowsRequestedState, "
                f"got {type(self.requested_state).__name__}"
            )
        required = _REQUIRED_EXPECTATION_FIELDS.get(self.kind, frozenset())
        populated = self.requested_state.populated_fields
        if populated != required:
            raise WindowsTransitionRequestError(
                f"transition {self.kind.value!r} requires exactly the expectation fields "
                f"{sorted(required)}, got {sorted(populated)}"
            )
        if (
            self.requested_state.bounds_tolerance != 0.0
            and self.requested_state.expected_bounds is None
        ):
            raise WindowsTransitionRequestError(
                "bounds_tolerance is meaningful only with expected_bounds"
            )
        for name, evidence in (("pre_state", self.pre_state), ("post_state", self.post_state)):
            if evidence is not None and not isinstance(evidence, WindowsObservationEvidence):
                raise TypeError(
                    f"{name} must be a WindowsObservationEvidence, got {type(evidence).__name__}"
                )
        if self.native_evidence is not None:
            if not isinstance(self.native_evidence, NativeExecutionEvidence):
                raise TypeError(
                    "native_evidence must be a NativeExecutionEvidence, "
                    f"got {type(self.native_evidence).__name__}"
                )
            if self.native_evidence.attempt_id != self.attempt_id:
                raise WindowsTransitionRequestError(
                    "native_evidence must belong to the same attempt as the request"
                )


# ---------------------------------------------------------------------------
# Emitted verification evidence.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowsTransitionVerification:
    """Immutable canonical verification evidence for one requested transition.

    It is evidence, not authority and not task success: it grants nothing,
    transitions no Task, executes nothing, and is not a canonical
    :class:`~agentx.capabilities.abi.VerificationResult` (that verdict is
    manufactured only by the canonical capability execution loop).

    ``pre_state_evaluation`` applies the very same predicate to the pre-state
    observation, so a consumer can distinguish "the state changed" from "the
    state already held before the attempt" without this module guessing intent.
    ``native_reported_success`` is recorded for audit only and never
    contributed to ``verdict``.
    """

    operation: CapabilityIdentity
    attempt_id: str
    environment_id: str
    kind: WindowsTransitionKind
    target_descriptor: str
    observation_contract: WindowsObservationContract
    verdict: WindowsTransitionVerdict
    reasons: tuple[WindowsVerificationReason, ...]
    pre_state_observation_id: str | None
    post_state_observation_id: str | None
    pre_state_evaluation: WindowsTransitionVerdict
    native_reported_success: bool | None

    def __post_init__(self) -> None:
        if not isinstance(self.verdict, WindowsTransitionVerdict):
            raise TypeError("verdict must be a WindowsTransitionVerdict")
        if not isinstance(self.reasons, tuple) or not self.reasons:
            raise WindowsTransitionRequestError("reasons must be a non-empty tuple")
        if any(not isinstance(reason, WindowsVerificationReason) for reason in self.reasons):
            raise TypeError("reasons must contain WindowsVerificationReason members")
        matched = WindowsVerificationReason.OBSERVED_STATE_MATCHES_REQUEST
        if self.verdict is WindowsTransitionVerdict.VERIFIED:
            if self.reasons != (matched,):
                raise WindowsTransitionRequestError(
                    "a verified transition carries exactly the matching-state reason"
                )
        elif matched in self.reasons:
            raise WindowsTransitionRequestError(
                "only a verified transition may carry the matching-state reason"
            )
        if not isinstance(self.pre_state_evaluation, WindowsTransitionVerdict):
            raise TypeError("pre_state_evaluation must be a WindowsTransitionVerdict")
        if self.native_reported_success is not None and (
            type(self.native_reported_success) is not bool
        ):
            raise TypeError("native_reported_success must be a bool or None")

    @property
    def verified(self) -> bool:
        """Convenience view; ``verdict`` remains the authoritative field."""
        return self.verdict is WindowsTransitionVerdict.VERIFIED

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the canonical JSON-compatible evidence representation."""
        return {
            "source": WINDOWS_TRANSITION_VERIFICATION_SOURCE,
            "operation": str(self.operation),
            "attempt_id": self.attempt_id,
            "environment_id": self.environment_id,
            "kind": self.kind.value,
            "target": self.target_descriptor,
            "observation_contract": self.observation_contract.value,
            "verdict": self.verdict.value,
            "reasons": [reason.value for reason in self.reasons],
            "pre_state_observation_id": self.pre_state_observation_id,
            "post_state_observation_id": self.post_state_observation_id,
            "pre_state_evaluation": self.pre_state_evaluation.value,
            "native_reported_success": self.native_reported_success,
        }


# ---------------------------------------------------------------------------
# Pure predicates over canonical observations.
#
# Every function below reads only the requested transition, the exact target
# and one canonical snapshot. None of them can see the native execution
# result, so no verdict can ever be derived from it.
# ---------------------------------------------------------------------------

_Outcome = tuple[WindowsTransitionVerdict, WindowsVerificationReason]

_MATCH: Final[_Outcome] = (
    WindowsTransitionVerdict.VERIFIED,
    WindowsVerificationReason.OBSERVED_STATE_MATCHES_REQUEST,
)
_DIFFERS: Final[_Outcome] = (
    WindowsTransitionVerdict.NOT_VERIFIED,
    WindowsVerificationReason.OBSERVED_STATE_DIFFERS_FROM_REQUEST,
)


def _insufficient(reason: WindowsVerificationReason) -> _Outcome:
    return (WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE, reason)


def _decide(matched: bool) -> _Outcome:
    return _MATCH if matched else _DIFFERS


def _find_window(snapshot: WindowsProcessSnapshot, handle: int) -> WindowsWindowIdentity | None:
    for window in snapshot.windows:
        if window.handle == handle:
            return window
    return None


def _find_process(
    snapshot: WindowsProcessSnapshot, process_id: int
) -> WindowsProcessIdentity | None:
    for process in snapshot.processes:
        if process.process_id == process_id:
            return process
    return None


def _find_element(snapshot: UIATreeSnapshot, path: tuple[int, ...]) -> UIAElementSnapshot | None:
    for element in snapshot.elements:
        if element.reference.path == path:
            return element
    return None


def _predicate_enumerated_window(
    kind: WindowsTransitionKind,
    target: WindowsWindowTarget,
    snapshot: WindowsProcessSnapshot,
) -> _Outcome:
    """Decide a window transition from the A5.02 top-level enumeration.

    The enumeration is a complete top-level window walk, so a missing handle
    is genuine evidence of absence — unless the snapshot itself reports that
    raw entries were dropped as invalid, in which case absence is inconclusive.
    """
    observed = _find_window(snapshot, target.handle)
    if observed is None:
        if snapshot.dropped_invalid_entries > 0:
            return _insufficient(WindowsVerificationReason.INCOMPLETE_OBSERVATION)
        return _decide(kind is WindowsTransitionKind.WINDOW_ABSENT)
    if target.process_id is not None and observed.process_id != target.process_id:
        # The handle now belongs to a different process: recycled handle, so
        # this observation describes a different window than the target.
        return _insufficient(WindowsVerificationReason.TARGET_IDENTITY_MISMATCH)
    if kind is WindowsTransitionKind.WINDOW_ABSENT:
        return _DIFFERS
    if kind is WindowsTransitionKind.WINDOW_PRESENT:
        return _MATCH
    if kind is WindowsTransitionKind.WINDOW_VISIBLE:
        return _decide(observed.is_visible is True)
    if kind is WindowsTransitionKind.WINDOW_HIDDEN:
        return _decide(observed.is_visible is False)
    raise AssertionError(f"unhandled enumerated-window transition {kind.value!r}")


def _predicate_process(
    kind: WindowsTransitionKind,
    target: WindowsProcessTarget,
    requested_state: WindowsRequestedState,
    snapshot: WindowsProcessSnapshot,
) -> _Outcome:
    """Decide an application transition from the A5.02 process enumeration."""
    observed = _find_process(snapshot, target.process_id)
    if observed is None:
        if snapshot.dropped_invalid_entries > 0:
            return _insufficient(WindowsVerificationReason.INCOMPLETE_OBSERVATION)
        return _decide(kind is WindowsTransitionKind.APPLICATION_PROCESS_ABSENT)
    if kind is WindowsTransitionKind.APPLICATION_PROCESS_ABSENT:
        return _DIFFERS
    if kind is WindowsTransitionKind.APPLICATION_PROCESS_PRESENT:
        return _MATCH
    if kind is WindowsTransitionKind.APPLICATION_PRESENTS_WINDOW:
        # Owning a visible top-level window is the only read-only "an
        # application is up" signal the baseline exposes. It is not readiness.
        return _decide(observed.visible_window_count > 0)
    if kind is WindowsTransitionKind.APPLICATION_EXECUTABLE_IDENTITY:
        if (
            observed.executable_name_status is not MetadataStatus.AVAILABLE
            or observed.executable_name is None
        ):
            return _insufficient(WindowsVerificationReason.TARGET_METADATA_UNAVAILABLE)
        expected_name = requested_state.expected_executable_name
        if expected_name is None:  # pragma: no cover - guaranteed by request validation
            return _insufficient(WindowsVerificationReason.TARGET_METADATA_UNAVAILABLE)
        # Windows image names are case-insensitive; comparison is typed
        # equality on a normalized string, never a substring or pattern match.
        return _decide(observed.executable_name.casefold() == expected_name.casefold())
    raise AssertionError(f"unhandled process transition {kind.value!r}")


def _predicate_uia_window(
    kind: WindowsTransitionKind,
    target: WindowsWindowTarget,
    requested_state: WindowsRequestedState,
    snapshot: UIATreeSnapshot,
) -> _Outcome:
    """Decide a window transition from the A5.03 bounded UIA snapshot."""
    if snapshot.root_window_handle != target.handle:
        return _insufficient(WindowsVerificationReason.TARGET_IDENTITY_MISMATCH)
    root = _find_element(snapshot, ())
    if root is None or root.state is UIAElementState.VANISHED:
        return _insufficient(WindowsVerificationReason.TARGET_NOT_OBSERVED)
    if target.process_id is not None:
        observed_pid = root.process_id
        if observed_pid is not None and observed_pid != target.process_id:
            return _insufficient(WindowsVerificationReason.TARGET_IDENTITY_MISMATCH)
    if kind is WindowsTransitionKind.WINDOW_FOCUSED:
        return _predicate_focus(snapshot)
    if kind is WindowsTransitionKind.WINDOW_BOUNDS:
        return _predicate_bounds(root, requested_state)
    raise AssertionError(f"unhandled UIA window transition {kind.value!r}")


def _predicate_focus(snapshot: UIATreeSnapshot) -> _Outcome:
    """Decide keyboard focus inside the snapshot rooted at the target window.

    Focus is verified when some observed element of that window reports
    ``has_keyboard_focus=True``. When no element exposes the property the
    answer is unknown; when every observed element reports ``False`` but the
    traversal was truncated, the unobserved remainder keeps it unknown.
    """
    observed_any = False
    for element in snapshot.elements:
        observation = element.property_observation(UIAPropertyName.HAS_KEYBOARD_FOCUS)
        if observation.status is not UIAObservationStatus.AVAILABLE:
            continue
        if type(observation.value) is not bool:
            continue
        observed_any = True
        if observation.value is True:
            return _MATCH
    if not observed_any:
        return _insufficient(WindowsVerificationReason.OBSERVED_PROPERTY_UNAVAILABLE)
    if snapshot.truncated_by_depth or snapshot.truncated_by_nodes:
        return _insufficient(WindowsVerificationReason.INCOMPLETE_OBSERVATION)
    return _DIFFERS


def _predicate_bounds(root: UIAElementSnapshot, requested_state: WindowsRequestedState) -> _Outcome:
    """Decide a move/resize from the canonical bounding-rectangle observation."""
    observation = root.property_observation(UIAPropertyName.BOUNDING_RECTANGLE)
    if observation.status is not UIAObservationStatus.AVAILABLE:
        return _insufficient(WindowsVerificationReason.OBSERVED_PROPERTY_UNAVAILABLE)
    observed = root.bounding_rectangle
    expected = requested_state.expected_bounds
    if observed is None or expected is None:
        return _insufficient(WindowsVerificationReason.OBSERVED_PROPERTY_UNAVAILABLE)
    tolerance = requested_state.bounds_tolerance
    return _decide(
        all(
            abs(observed_value - expected_value) <= tolerance
            for observed_value, expected_value in zip(observed, expected, strict=True)
        )
    )


def _predicate_element_value(
    target: WindowsElementTarget,
    requested_state: WindowsRequestedState,
    snapshot: UIATreeSnapshot,
) -> _Outcome:
    """Decide text entry from the canonical ``value`` observation of the field.

    Text entry is never inferred from the fact that keys were sent: without an
    available post-state ``value`` observation of the exact target element the
    answer is ``INSUFFICIENT_EVIDENCE``.
    """
    reference = target.reference
    if snapshot.root_window_handle != reference.root_window_handle:
        return _insufficient(WindowsVerificationReason.TARGET_IDENTITY_MISMATCH)
    element = _find_element(snapshot, reference.path)
    if element is None or element.state is UIAElementState.VANISHED:
        return _insufficient(WindowsVerificationReason.TARGET_NOT_OBSERVED)
    if reference.runtime_id is not None and element.reference.runtime_id != reference.runtime_id:
        return _insufficient(WindowsVerificationReason.TARGET_IDENTITY_MISMATCH)
    observation = element.property_observation(UIAPropertyName.VALUE)
    if observation.status is not UIAObservationStatus.AVAILABLE:
        return _insufficient(WindowsVerificationReason.OBSERVED_PROPERTY_UNAVAILABLE)
    observed_text = element.value
    expected_text = requested_state.expected_text
    if observed_text is None or expected_text is None:
        return _insufficient(WindowsVerificationReason.OBSERVED_PROPERTY_UNAVAILABLE)
    return _decide(observed_text == expected_text)


def _predicate(
    kind: WindowsTransitionKind,
    target: WindowsTransitionTarget,
    requested_state: WindowsRequestedState,
    snapshot: WindowsProcessSnapshot | UIATreeSnapshot,
) -> _Outcome:
    """Apply the transition predicate to one canonical observation."""
    contract = REQUIRED_OBSERVATION_CONTRACT[kind]
    if contract is WindowsObservationContract.PROCESS_SNAPSHOT:
        if not isinstance(snapshot, WindowsProcessSnapshot):  # pragma: no cover - envelope checked
            return _insufficient(WindowsVerificationReason.OBSERVATION_CONTRACT_MISMATCH)
        if isinstance(target, WindowsWindowTarget):
            return _predicate_enumerated_window(kind, target, snapshot)
        if isinstance(target, WindowsProcessTarget):
            return _predicate_process(kind, target, requested_state, snapshot)
        raise AssertionError(f"unhandled process-snapshot target {type(target).__name__}")
    if contract is WindowsObservationContract.UIA_TREE_SNAPSHOT:
        if not isinstance(snapshot, UIATreeSnapshot):  # pragma: no cover - envelope checked
            return _insufficient(WindowsVerificationReason.OBSERVATION_CONTRACT_MISMATCH)
        if isinstance(target, WindowsWindowTarget):
            return _predicate_uia_window(kind, target, requested_state, snapshot)
        if isinstance(target, WindowsElementTarget):
            return _predicate_element_value(target, requested_state, snapshot)
        raise AssertionError(f"unhandled UIA target {type(target).__name__}")
    return _insufficient(WindowsVerificationReason.NO_CANONICAL_OBSERVATION_CONTRACT)


# ---------------------------------------------------------------------------
# The verification boundary.
# ---------------------------------------------------------------------------


class WindowsTransitionVerifier:
    """Pure deterministic verification boundary for Windows mutations.

    The verifier is stateless: it holds no collaborators, no clock, no cache,
    no counters and no history, so identical requests always produce equal
    evidence. It performs no mutation of any kind and reaches no authority: it
    only reads typed fields of caller-supplied canonical observations.

    It does not perform the transition it verifies, does not own or consult
    ``ActionGate``, does not manufacture a canonical
    :class:`~agentx.capabilities.abi.VerificationResult`, and does not decide
    task success.
    """

    __slots__ = ()

    def evaluate(self, request: WindowsTransitionRequest) -> WindowsTransitionVerification:
        """Evaluate one requested transition against its state evidence.

        Fail closed: missing, mismatched, stale or contract-inconsistent
        evidence yields ``INSUFFICIENT_EVIDENCE``; an observation that
        contradicts the requested state yields ``NOT_VERIFIED``; only a
        canonical post-state observation of the exact target satisfying the
        exact requested state yields ``VERIFIED``.

        Raises :class:`TypeError` for a non-request argument, which is a
        programming error rather than a verdict. Nothing else raises: evidence
        problems are verdicts, not exceptions.
        """
        if not isinstance(request, WindowsTransitionRequest):
            raise TypeError(
                f"request must be a WindowsTransitionRequest, got {type(request).__name__}"
            )

        contract = REQUIRED_OBSERVATION_CONTRACT[request.kind]
        native_reported_success = (
            request.native_evidence.result.succeeded
            if request.native_evidence is not None
            else None
        )
        pre_state = request.pre_state
        post_state = request.post_state

        if contract is WindowsObservationContract.NONE:
            # The canonical baseline exposes no observation that could decide
            # this transition. No visual fallback, no inference from the
            # native return, no guess: the honest answer is "cannot tell".
            return self._emit(
                request,
                contract=contract,
                verdict=WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE,
                reasons=(WindowsVerificationReason.NO_CANONICAL_OBSERVATION_CONTRACT,),
                pre_state_evaluation=WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE,
                native_reported_success=native_reported_success,
            )

        envelope = _check_envelope(request, contract)
        if envelope:
            return self._emit(
                request,
                contract=contract,
                verdict=WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE,
                reasons=envelope,
                pre_state_evaluation=WindowsTransitionVerdict.INSUFFICIENT_EVIDENCE,
                native_reported_success=native_reported_success,
            )
        if pre_state is None or post_state is None:  # pragma: no cover - envelope guarantees
            raise AssertionError("envelope check must reject missing observations")

        pre_verdict, _ = _predicate(
            request.kind, request.target, request.requested_state, pre_state.snapshot
        )
        verdict, reason = _predicate(
            request.kind, request.target, request.requested_state, post_state.snapshot
        )
        return self._emit(
            request,
            contract=contract,
            verdict=verdict,
            reasons=(reason,),
            pre_state_evaluation=pre_verdict,
            native_reported_success=native_reported_success,
        )

    @staticmethod
    def _emit(
        request: WindowsTransitionRequest,
        *,
        contract: WindowsObservationContract,
        verdict: WindowsTransitionVerdict,
        reasons: tuple[WindowsVerificationReason, ...],
        pre_state_evaluation: WindowsTransitionVerdict,
        native_reported_success: bool | None,
    ) -> WindowsTransitionVerification:
        """Assemble immutable evidence; the verdict is already decided here.

        The only place ``native_reported_success`` appears is as a recorded
        audit field: it is appended to the reason list purely as an explicit
        statement that a claimed native success did not verify anything.
        """
        final_reasons = reasons
        if native_reported_success is True and verdict is not WindowsTransitionVerdict.VERIFIED:
            final_reasons = (
                *reasons,
                WindowsVerificationReason.NATIVE_RESULT_IS_NOT_VERIFICATION,
            )
        return WindowsTransitionVerification(
            operation=request.operation,
            attempt_id=request.attempt_id,
            environment_id=request.environment_id,
            kind=request.kind,
            target_descriptor=request.target.descriptor,
            observation_contract=contract,
            verdict=verdict,
            reasons=final_reasons,
            pre_state_observation_id=(
                request.pre_state.observation_id if request.pre_state is not None else None
            ),
            post_state_observation_id=(
                request.post_state.observation_id if request.post_state is not None else None
            ),
            pre_state_evaluation=pre_state_evaluation,
            native_reported_success=native_reported_success,
        )


def _check_envelope(
    request: WindowsTransitionRequest,
    contract: WindowsObservationContract,
) -> tuple[WindowsVerificationReason, ...]:
    """Validate evidence identity, contract agreement and canonical freshness.

    Returns the deterministic, ordered reasons why the supplied evidence
    cannot decide the request, or an empty tuple when the evidence envelope is
    sound. This never consults the native execution result.
    """
    reasons: list[WindowsVerificationReason] = []
    pre_state = request.pre_state
    post_state = request.post_state

    if pre_state is None:
        reasons.append(WindowsVerificationReason.PRE_STATE_OBSERVATION_MISSING)
    if post_state is None:
        reasons.append(WindowsVerificationReason.POST_STATE_OBSERVATION_MISSING)
    if pre_state is None or post_state is None:
        return tuple(reasons)

    if pre_state.contract is not contract or post_state.contract is not contract:
        reasons.append(WindowsVerificationReason.OBSERVATION_CONTRACT_MISMATCH)
    if (
        pre_state.environment_id != request.environment_id
        or post_state.environment_id != request.environment_id
    ):
        reasons.append(WindowsVerificationReason.ENVIRONMENT_IDENTITY_MISMATCH)
    if pre_state.observation_id == post_state.observation_id:
        # One snapshot cannot be both the before and the after of a mutation.
        reasons.append(WindowsVerificationReason.POST_STATE_NOT_DISTINCT_FROM_PRE_STATE)

    pre_captured = pre_state.captured_at
    post_captured = post_state.captured_at
    if pre_captured is not None and post_captured is not None and post_captured <= pre_captured:
        # Canonical freshness: where the contract timestamps its reads, a
        # post-state must be strictly newer than the pre-state it follows.
        reasons.append(WindowsVerificationReason.STALE_POST_STATE_OBSERVATION)

    return tuple(reasons)
