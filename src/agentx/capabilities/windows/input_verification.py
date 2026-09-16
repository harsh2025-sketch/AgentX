"""Independent verification adapters for Windows input and clipboard actions.

AX-296 / AX-297 complete the verification boundary without changing the
existing native execution seams. Each adapter owns two deliberately distinct
ports: one port performs the machine action through the landed capability and a
second port independently observes the resulting state. Native/API acceptance
is therefore never promoted to verification.

Clipboard verification uses a separate read-back port and never places the
clipboard payload in verification detail. Keyboard/text verification uses an
injected independent state verifier because keyboard API acceptance cannot, by
itself, prove the intended application state. Verification evidence is narrow
(source plus opaque UUID reference) and grants no authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from agentx.capabilities.abi import (
    CapabilityDescriptor,
    CapabilityObservation,
    CapabilityRequest,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.windows.keyboard_text_clipboard import (
    MAX_CLIPBOARD_TEXT_LENGTH,
    ClipboardClearParams,
    ClipboardNativePort,
    ClipboardReadTextParams,
    ClipboardWriteTextParams,
    KeyboardNativePort,
    KeyChord,
    RawClipboardText,
    SendKeysParams,
    SendTextParams,
    WindowsClipboardClearCapability,
    WindowsClipboardReadTextCapability,
    WindowsClipboardWriteTextCapability,
    WindowsSendKeysCapability,
    WindowsSendTextCapability,
)
from agentx.capabilities.windows.provider import WindowsSupport
from agentx.core.errors import AgentXError
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result

__all__ = [
    "ClipboardReadbackPort",
    "IndependentVerificationEvidence",
    "KeyboardActionVerificationPort",
    "VerifiedWindowsClipboardClearCapability",
    "VerifiedWindowsClipboardReadTextCapability",
    "VerifiedWindowsClipboardWriteTextCapability",
    "VerifiedWindowsSendKeysCapability",
    "VerifiedWindowsSendTextCapability",
]

_MAX_SOURCE_LENGTH = 256


def _source(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("verification source must be a string")
    if not value or value != value.strip():
        raise ValueError("verification source must be non-empty and trimmed")
    if len(value) > _MAX_SOURCE_LENGTH:
        raise ValueError("verification source exceeds the bounded length")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ValueError("verification source must not contain control lines")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class IndependentVerificationEvidence:
    """Opaque, non-authoritative evidence emitted by an independent verifier."""

    passed: bool
    source: str
    evidence_reference: UUID

    def __post_init__(self) -> None:
        if type(self.passed) is not bool:
            raise TypeError("passed must be bool")
        object.__setattr__(self, "source", _source(self.source))
        if not isinstance(self.evidence_reference, UUID):
            raise TypeError("evidence_reference must be a UUID")
        if self.evidence_reference.int == 0:
            raise ValueError("evidence_reference must not be the nil UUID")


@runtime_checkable
class KeyboardActionVerificationPort(Protocol):
    """Independent observer for the intended state after keyboard/text input."""

    def verify_text_entry(
        self,
        expected_text: str,
    ) -> Result[IndependentVerificationEvidence, AgentXError]:
        """Observe the target independently and compare it with expected text."""
        ...

    def verify_key_sequence(
        self,
        expected_chords: tuple[KeyChord, ...],
    ) -> Result[IndependentVerificationEvidence, AgentXError]:
        """Observe the intended postcondition independently after key input."""
        ...


@runtime_checkable
class ClipboardReadbackPort(Protocol):
    """Read-only independent clipboard observation seam."""

    @property
    def source(self) -> str:
        """Stable identity of the independent observation source."""
        ...

    def read_text(self) -> Result[RawClipboardText, AgentXError]:
        """Read current clipboard text state independently of the action port."""
        ...


def _observation_data(observation: CapabilityObservation) -> Mapping[str, object] | None:
    if not isinstance(observation, CapabilityObservation):
        raise TypeError("observation must be a CapabilityObservation")
    data = observation.to_dict()["data"]
    if not isinstance(data, dict) or "error" in data:
        return None
    return data


def _verification_error(prefix: str, error: AgentXError) -> VerificationResult:
    return VerificationResult(
        passed=False,
        detail=f"{prefix}: independent verifier failed with {error.code}",
    )


def _keyboard_verdict(
    operation: str,
    result: Result[IndependentVerificationEvidence, AgentXError],
) -> VerificationResult:
    if result.is_failure:
        return _verification_error(operation, result.unwrap_error())
    evidence: object = result.unwrap()
    if not isinstance(evidence, IndependentVerificationEvidence):
        return VerificationResult(
            passed=False,
            detail=f"{operation}: independent verifier returned malformed evidence",
        )
    verdict = "passed" if evidence.passed else "failed"
    return VerificationResult(
        passed=evidence.passed,
        detail=(
            f"{operation}: independent verification {verdict}; "
            f"source={evidence.source}; evidence={evidence.evidence_reference}"
        ),
    )


def _readback(
    port: ClipboardReadbackPort,
    *,
    operation: str,
) -> tuple[RawClipboardText | None, VerificationResult | None]:
    try:
        _source(port.source)
    except (TypeError, ValueError):
        return None, VerificationResult(
            passed=False,
            detail=f"{operation}: independent clipboard source identity is invalid",
        )
    result = port.read_text()
    if result.is_failure:
        return None, _verification_error(operation, result.unwrap_error())
    raw: object = result.unwrap()
    if not isinstance(raw, RawClipboardText):
        return None, VerificationResult(
            passed=False,
            detail=f"{operation}: independent clipboard read-back was malformed",
        )
    if raw.text is not None and len(raw.text) > MAX_CLIPBOARD_TEXT_LENGTH:
        return None, VerificationResult(
            passed=False,
            detail=(f"{operation}: independent clipboard read-back exceeded the governed bound"),
        )
    return raw, None


class VerifiedWindowsSendTextCapability:
    """Existing send-text execution plus a distinct independent verifier."""

    __slots__ = ("_inner", "_verification_port")

    def __init__(
        self,
        support: WindowsSupport,
        *,
        execution_port: KeyboardNativePort,
        verification_port: KeyboardActionVerificationPort,
    ) -> None:
        execution_object: object = execution_port
        if execution_object is verification_port:
            raise ValueError("execution and verification ports must be distinct objects")
        if not isinstance(verification_port, KeyboardActionVerificationPort):
            raise TypeError("verification_port must implement KeyboardActionVerificationPort")
        self._inner = WindowsSendTextCapability(support, keyboard_port=execution_port)
        self._verification_port = verification_port

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._inner.descriptor

    def execute(
        self,
        request: CapabilityRequest[SendTextParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        return self._inner.execute(request, context)

    def verify(
        self,
        request: CapabilityRequest[SendTextParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, SendTextParams):
            raise TypeError("send_text requires SendTextParams")
        if context.observe_stop().should_stop:
            return VerificationResult(
                passed=False,
                detail="send_text: verification stop condition is active",
            )
        data = _observation_data(observation)
        if data is None or data.get("character_count") != len(request.params.text):
            return VerificationResult(
                passed=False,
                detail=("send_text: execution observation lacks matching typed count evidence"),
            )
        return _keyboard_verdict(
            "send_text",
            self._verification_port.verify_text_entry(request.params.text),
        )


class VerifiedWindowsSendKeysCapability:
    """Existing key-sequence execution plus a distinct independent verifier."""

    __slots__ = ("_inner", "_verification_port")

    def __init__(
        self,
        support: WindowsSupport,
        *,
        execution_port: KeyboardNativePort,
        verification_port: KeyboardActionVerificationPort,
    ) -> None:
        execution_object: object = execution_port
        if execution_object is verification_port:
            raise ValueError("execution and verification ports must be distinct objects")
        if not isinstance(verification_port, KeyboardActionVerificationPort):
            raise TypeError("verification_port must implement KeyboardActionVerificationPort")
        self._inner = WindowsSendKeysCapability(support, keyboard_port=execution_port)
        self._verification_port = verification_port

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._inner.descriptor

    def execute(
        self,
        request: CapabilityRequest[SendKeysParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        return self._inner.execute(request, context)

    def verify(
        self,
        request: CapabilityRequest[SendKeysParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, SendKeysParams):
            raise TypeError("send_keys requires SendKeysParams")
        if context.observe_stop().should_stop:
            return VerificationResult(
                passed=False,
                detail="send_keys: verification stop condition is active",
            )
        data = _observation_data(observation)
        if data is None or data.get("chord_count") != len(request.params.chords):
            return VerificationResult(
                passed=False,
                detail=("send_keys: execution observation lacks matching typed count evidence"),
            )
        return _keyboard_verdict(
            "send_keys",
            self._verification_port.verify_key_sequence(request.params.chords),
        )


class _ClipboardVerifiedBase:
    __slots__ = ("_verification_port",)

    def __init__(
        self,
        *,
        execution_port: ClipboardNativePort,
        verification_port: ClipboardReadbackPort,
    ) -> None:
        execution_object: object = execution_port
        if execution_object is verification_port:
            raise ValueError("execution and verification ports must be distinct objects")
        if not isinstance(verification_port, ClipboardReadbackPort):
            raise TypeError("verification_port must implement ClipboardReadbackPort")
        _source(verification_port.source)
        self._verification_port = verification_port

    def _readback(
        self,
        *,
        operation: str,
    ) -> tuple[RawClipboardText | None, VerificationResult | None]:
        return _readback(self._verification_port, operation=operation)


class VerifiedWindowsClipboardReadTextCapability(_ClipboardVerifiedBase):
    """Clipboard read independently confirmed by a second read seam."""

    __slots__ = ("_inner",)

    def __init__(
        self,
        support: WindowsSupport,
        *,
        execution_port: ClipboardNativePort,
        verification_port: ClipboardReadbackPort,
    ) -> None:
        super().__init__(
            execution_port=execution_port,
            verification_port=verification_port,
        )
        self._inner = WindowsClipboardReadTextCapability(
            support,
            clipboard_port=execution_port,
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._inner.descriptor

    def execute(
        self,
        request: CapabilityRequest[ClipboardReadTextParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        return self._inner.execute(request, context)

    def verify(
        self,
        request: CapabilityRequest[ClipboardReadTextParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, ClipboardReadTextParams):
            raise TypeError("read_text requires ClipboardReadTextParams")
        if context.observe_stop().should_stop:
            return VerificationResult(
                passed=False,
                detail="clipboard_read: verification stop condition is active",
            )
        data = _observation_data(observation)
        if data is None:
            return VerificationResult(
                passed=False,
                detail="clipboard_read: execution observation is malformed",
            )
        has_text = data.get("has_text")
        text = data.get("text")
        character_count = data.get("character_count")
        if (
            type(has_text) is not bool
            or not isinstance(text, str)
            or type(character_count) is not int
        ):
            return VerificationResult(
                passed=False,
                detail=("clipboard_read: execution observation lacks typed clipboard evidence"),
            )
        raw, failure = self._readback(operation="clipboard_read")
        if failure is not None:
            return failure
        assert raw is not None
        current = raw.text
        expected = text if has_text else None
        if current != expected or (expected is not None and len(expected) != character_count):
            return VerificationResult(
                passed=False,
                detail=("clipboard_read: independent read-back differs from execution evidence"),
            )
        return VerificationResult(
            passed=True,
            detail=(
                "clipboard_read: independently confirmed exact clipboard text state; "
                f"source={_source(self._verification_port.source)}"
            ),
        )


class VerifiedWindowsClipboardWriteTextCapability(_ClipboardVerifiedBase):
    """Clipboard write independently confirmed by a separate read-back seam."""

    __slots__ = ("_inner",)

    def __init__(
        self,
        support: WindowsSupport,
        *,
        execution_port: ClipboardNativePort,
        verification_port: ClipboardReadbackPort,
    ) -> None:
        super().__init__(
            execution_port=execution_port,
            verification_port=verification_port,
        )
        self._inner = WindowsClipboardWriteTextCapability(
            support,
            clipboard_port=execution_port,
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._inner.descriptor

    def execute(
        self,
        request: CapabilityRequest[ClipboardWriteTextParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        return self._inner.execute(request, context)

    def verify(
        self,
        request: CapabilityRequest[ClipboardWriteTextParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, ClipboardWriteTextParams):
            raise TypeError("write_text requires ClipboardWriteTextParams")
        if context.observe_stop().should_stop:
            return VerificationResult(
                passed=False,
                detail="clipboard_write: verification stop condition is active",
            )
        data = _observation_data(observation)
        if data is None or data.get("character_count") != len(request.params.text):
            return VerificationResult(
                passed=False,
                detail=(
                    "clipboard_write: execution observation lacks matching typed count evidence"
                ),
            )
        raw, failure = self._readback(operation="clipboard_write")
        if failure is not None:
            return failure
        assert raw is not None
        if raw.text != request.params.text:
            return VerificationResult(
                passed=False,
                detail=("clipboard_write: independent read-back does not match intended text"),
            )
        return VerificationResult(
            passed=True,
            detail=(
                "clipboard_write: exact state independently confirmed without payload logging; "
                f"source={_source(self._verification_port.source)}"
            ),
        )


class VerifiedWindowsClipboardClearCapability(_ClipboardVerifiedBase):
    """Clipboard clear independently confirmed by a separate read-back seam."""

    __slots__ = ("_inner",)

    def __init__(
        self,
        support: WindowsSupport,
        *,
        execution_port: ClipboardNativePort,
        verification_port: ClipboardReadbackPort,
    ) -> None:
        super().__init__(
            execution_port=execution_port,
            verification_port=verification_port,
        )
        self._inner = WindowsClipboardClearCapability(
            support,
            clipboard_port=execution_port,
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._inner.descriptor

    def execute(
        self,
        request: CapabilityRequest[ClipboardClearParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        return self._inner.execute(request, context)

    def verify(
        self,
        request: CapabilityRequest[ClipboardClearParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, ClipboardClearParams):
            raise TypeError("clear requires ClipboardClearParams")
        if context.observe_stop().should_stop:
            return VerificationResult(
                passed=False,
                detail="clipboard_clear: verification stop condition is active",
            )
        if _observation_data(observation) is None:
            return VerificationResult(
                passed=False,
                detail="clipboard_clear: execution observation is malformed",
            )
        raw, failure = self._readback(operation="clipboard_clear")
        if failure is not None:
            return failure
        assert raw is not None
        if raw.text is not None:
            return VerificationResult(
                passed=False,
                detail=("clipboard_clear: independent read-back still observes clipboard text"),
            )
        return VerificationResult(
            passed=True,
            detail=(
                "clipboard_clear: absence of clipboard text independently confirmed; "
                f"source={_source(self._verification_port.source)}"
            ),
        )
