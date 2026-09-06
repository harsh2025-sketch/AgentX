"""Provider-neutral audio contracts for AgentX real-time voice work.

A7.01 defines the smallest set of shared *data* contracts that future audio
subsystems (streaming speech-to-text A7.02, text-to-speech, wake word, speaker
identification, voice commands) must be able to agree on without naming a
vendor. Nothing in this module knows about Whisper, OpenAI, Kokoro, Windows
Speech, ``winsound``, a microphone driver, a network transport, or a codec
library: audio bytes are inert data, and the vocabulary here is only about
format identity, framing, ordering, timing, lifecycle, buffering, and failure
classification.

Contract shape
--------------

    - Format/encoding:      :class:`AudioFormat` with :func:`is_pcm_format`,
                            :func:`sample_width_bytes`,
                            :func:`block_align_bytes`.
    - Sample rate/channels: bounded validated integers
                            (:data:`MIN_SAMPLE_RATE_HZ` ..
                            :data:`MAX_SAMPLE_RATE_HZ`,
                            :data:`MIN_CHANNEL_COUNT` ..
                            :data:`MAX_CHANNEL_COUNT`).
    - Frame/chunk:          :class:`AudioFrame` — one bounded, sequenced,
                            timestamped payload belonging to exactly one stream.
    - Stream identity:      :class:`~agentx.core.ids.AudioStreamId` (AgentX-owned
                            UUID) versus :class:`AudioEndpointId` (provider-owned
                            opaque string).
    - Source/sink identity: :class:`AudioEndpoint` + :class:`AudioEndpointKind`.
    - Lifecycle:            :class:`AudioStreamState` with the exhaustive
                            :data:`LEGAL_AUDIO_STREAM_TRANSITIONS` matrix and
                            pure transition helpers. Terminality is *derived*
                            from that matrix, so it can never drift.
    - Cancellation:         the canonical A1.07 cooperative
                            :class:`~agentx.core.execution.CancellationToken`;
                            A7.01 adds no competing cancellation mechanism.
    - Backpressure:         :class:`AudioBufferPolicy` +
                            :class:`AudioAdmissionDecision` — a bounded-buffer
                            admission *contract*, not a queue implementation.
    - Errors:               canonical :class:`~agentx.core.errors.AgentXError`
                            values built by :func:`audio_failure` and carried
                            inside :class:`~agentx.core.result.Result`.

Invariants
----------

1. **Audio bytes are data.** A payload is length-checked, digest-hashed and
   copied as bytes. Nothing decodes it, transcribes it, or treats its content as
   instruction. Text that arrives from a provider (endpoint labels, status
   reasons) is likewise bounded, untrusted description.
2. **Bounds are closed.** Sample rate, channel count, payload size, frame
   duration, buffer capacity, sequence numbers, and latencies all have explicit
   supported ranges, and out-of-range input is a rejected construction, never a
   silently clamped value.
3. **Ordering is explicit.** Sequence numbers never wrap, so a replayed frame
   can never masquerade as a fresh one. Timestamps within a stream must not
   regress and must stay inside a declared gap bound.
4. **Lifecycle is a matrix, not a convention.** Every legal move is listed;
   illegal moves are refused with a structured error and there is no implicit
   self-transition, no reopening, and no "probably fine" default.
5. **Nothing here is authority.** A descriptor, endpoint advertisement, or
   provider status is data. None of it grants permission, marks an action
   verified, or bypasses the kernel.

Deliberate non-scope
--------------------

This module implements no audio I/O, no codec or resampler, no device
enumeration, no streaming STT (A7.02), no TTS, no wake word, no speaker
identification, no voice-command semantics, no model call, no session or
conversation runtime, no threading or asyncio scheduling, and no persistence. It
performs no I/O at all and imports only the standard library plus sibling
``agentx.core`` contracts, so ``agentx.core`` stays a dependency leaf with
respect to the other canonical subsystems.

Owner: A7.01. Belongs to ``agentx.core``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Protocol, runtime_checkable

from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import CancellationToken, Deadline
from agentx.core.ids import AudioStreamId
from agentx.core.result import Result

__all__ = [
    "AUDIO_CONTRACT_SCHEMA_VERSION",
    "DEFAULT_MAX_TIMESTAMP_GAP",
    "LEGAL_AUDIO_STREAM_TRANSITIONS",
    "MAX_AUDIO_BUFFER_BYTES",
    "MAX_AUDIO_BUFFER_FRAMES",
    "MAX_AUDIO_LATENCY_TARGET_MS",
    "MAX_AUDIO_PAYLOAD_BYTES",
    "MAX_AUDIO_SAMPLE_FRAMES",
    "MAX_AUDIO_SEQUENCE",
    "MAX_CHANNEL_COUNT",
    "MAX_SAMPLE_RATE_HZ",
    "MAX_TIMESTAMP_GAP",
    "MIN_AUDIO_BUFFER_FRAMES",
    "MIN_AUDIO_LATENCY_TARGET_MS",
    "MIN_CHANNEL_COUNT",
    "MIN_SAMPLE_RATE_HZ",
    "TERMINAL_AUDIO_STREAM_STATES",
    "AudioAdmissionDecision",
    "AudioAdmissionOutcome",
    "AudioBufferPolicy",
    "AudioCaptureStream",
    "AudioEndpoint",
    "AudioEndpointId",
    "AudioEndpointKind",
    "AudioFailureKind",
    "AudioFormat",
    "AudioFormatSupport",
    "AudioFrame",
    "AudioOverflowPolicy",
    "AudioPlaybackStream",
    "AudioProvider",
    "AudioProviderId",
    "AudioSequenceCheck",
    "AudioSequenceIssue",
    "AudioStreamDescriptor",
    "AudioStreamState",
    "AudioStreamStatus",
    "AudioTimestampCheck",
    "AudioTimestampIssue",
    "AudioValidationError",
    "admission_failure",
    "audio_failure",
    "audio_transition_error",
    "block_align_bytes",
    "can_transition_audio_state",
    "check_sequence",
    "check_timestamp",
    "is_pcm_format",
    "is_supported_channel_count",
    "is_supported_sample_rate",
    "is_terminal_audio_state",
    "legal_audio_transitions",
    "payload_digest",
    "sample_width_bytes",
    "try_transition_audio_state",
    "validate_audio_state_transition",
]

#: Version stamped into every serialized audio metadata mapping.
AUDIO_CONTRACT_SCHEMA_VERSION: Final[int] = 1

#: Inclusive bounds for a supported nominal sample rate, in hertz. Outside this
#: window a value is a configuration error rather than an exotic device: 8 kHz is
#: the lowest rate any speech pipeline uses and 192 kHz is far beyond what a
#: personal-assistant capture or playback path needs.
MIN_SAMPLE_RATE_HZ: Final[int] = 8_000
MAX_SAMPLE_RATE_HZ: Final[int] = 192_000

#: Inclusive channel-count bounds. These contracts cover voice capture/playback
#: plus small microphone arrays; wider layouts are a configuration error, not a
#: supported-but-unadvertised case.
MIN_CHANNEL_COUNT: Final[int] = 1
MAX_CHANNEL_COUNT: Final[int] = 8

#: Upper bound on the samples-per-channel count one frame may carry. That is one
#: second at the widest supported rate, which keeps a derived frame duration
#: finite and small even for compressed payloads.
MAX_AUDIO_SAMPLE_FRAMES: Final[int] = MAX_SAMPLE_RATE_HZ

#: Hard upper bound on one frame payload. Real-time voice frames are tiny (20 ms
#: of 48 kHz mono 16-bit PCM is 1920 bytes); a megabyte is already two orders of
#: magnitude past any legitimate frame, and the bound is what stops a "frame"
#: from becoming an unbounded file in an in-process buffer.
MAX_AUDIO_PAYLOAD_BYTES: Final[int] = 1 * 1024 * 1024

#: Bounds for a declared bounded buffer. A backpressure contract with an
#: unbounded limit is not a backpressure contract.
MIN_AUDIO_BUFFER_FRAMES: Final[int] = 1
MAX_AUDIO_BUFFER_FRAMES: Final[int] = 4_096
MAX_AUDIO_BUFFER_BYTES: Final[int] = 8 * 1024 * 1024

#: Bounds for the optional end-to-end latency target a stream declares.
MIN_AUDIO_LATENCY_TARGET_MS: Final[float] = 0.5
MAX_AUDIO_LATENCY_TARGET_MS: Final[float] = 5_000.0

#: Largest valid sequence number. A stream that reaches it must start a new
#: stream instead of wrapping, because a wrapped sequence silently defeats
#: ordering, gap detection, and duplicate detection.
MAX_AUDIO_SEQUENCE: Final[int] = (1 << 63) - 1

#: Gap bounds between consecutive frames of one stream. The default is what a
#: live voice path tolerates before a stall is a defect rather than a hiccup.
DEFAULT_MAX_TIMESTAMP_GAP: Final[timedelta] = timedelta(seconds=5)
MAX_TIMESTAMP_GAP: Final[timedelta] = timedelta(seconds=60)

_MAX_PROVIDER_ID_LENGTH: Final[int] = 128
_MAX_ENDPOINT_ID_LENGTH: Final[int] = 512
_MAX_LABEL_LENGTH: Final[int] = 256
_MAX_REASON_LENGTH: Final[int] = 256
_MAX_MESSAGE_LENGTH: Final[int] = 1_024
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")
_EPOCH_FLOOR: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)
_EPOCH_CEILING: Final[datetime] = datetime(9999, 12, 31, tzinfo=UTC)


class AudioValidationError(ValueError):
    """Raised when an A7.01 audio contract is malformed.

    This is a programming-error signal: violated invariants never become a
    ``Result`` failure. Expected operational failure (a device that is not
    present, a full buffer, a cancelled stream) is carried as an
    :class:`~agentx.core.errors.AgentXError` produced by :func:`audio_failure`.
    """


# --------------------------------------------------------------------------
# Format, encoding, and sample-layout vocabulary.
# --------------------------------------------------------------------------


class AudioFormat(StrEnum):
    """Closed vocabulary of transport-neutral audio encodings.

    Names are exact about layout (signed 16-bit little-endian, float32
    little-endian) so behavior never depends on host byte order. Only encodings a
    real-time voice pipeline actually negotiates are listed: container formats,
    metadata wrappers, and vendor wire names are deliberately absent, and a
    vendor cannot widen this set from outside ``agentx.core``.
    """

    PCM_S16LE = "pcm_s16le"
    PCM_F32LE = "pcm_f32le"
    OPUS = "opus"
    FLAC = "flac"


#: Bytes per sample per channel for the uncompressed formats. Compressed
#: containers have no fixed width, so they are absent rather than zero.
_SAMPLE_WIDTH_BYTES: Final[Mapping[AudioFormat, int]] = MappingProxyType(
    {
        AudioFormat.PCM_S16LE: 2,
        AudioFormat.PCM_F32LE: 4,
    }
)


def _validated_format(fmt: object, *, field_name: str) -> AudioFormat:
    if not isinstance(fmt, AudioFormat):
        raise TypeError(f"{field_name} must be an AudioFormat, got {type(fmt).__name__}")
    return fmt


def is_pcm_format(fmt: AudioFormat) -> bool:
    """Return ``True`` when the format is uncompressed, block-aligned PCM."""

    return _validated_format(fmt, field_name="format") in _SAMPLE_WIDTH_BYTES


def sample_width_bytes(fmt: AudioFormat) -> int | None:
    """Return bytes per sample per channel for PCM, or ``None`` for containers."""

    return _SAMPLE_WIDTH_BYTES.get(_validated_format(fmt, field_name="format"))


def block_align_bytes(fmt: AudioFormat, channel_count: int) -> int | None:
    """Return the PCM block alignment for ``fmt``/``channel_count``, else ``None``.

    A PCM payload must be an exact multiple of ``channels * bytes_per_sample``;
    a remainder means a truncated or spliced payload and is never accepted.
    """

    width = sample_width_bytes(fmt)
    if width is None:
        return None
    channels = _validated_int(
        channel_count,
        field_name="channel_count",
        minimum=MIN_CHANNEL_COUNT,
        maximum=MAX_CHANNEL_COUNT,
    )
    return width * channels


def is_supported_sample_rate(sample_rate_hz: object) -> bool:
    """Whether ``sample_rate_hz`` is an ``int`` inside the supported window."""

    if isinstance(sample_rate_hz, bool) or not isinstance(sample_rate_hz, int):
        return False
    return MIN_SAMPLE_RATE_HZ <= sample_rate_hz <= MAX_SAMPLE_RATE_HZ


def is_supported_channel_count(channel_count: object) -> bool:
    """Whether ``channel_count`` is an ``int`` inside the supported window."""

    if isinstance(channel_count, bool) or not isinstance(channel_count, int):
        return False
    return MIN_CHANNEL_COUNT <= channel_count <= MAX_CHANNEL_COUNT


# --------------------------------------------------------------------------
# Shared validators.
# --------------------------------------------------------------------------


def _validated_int(value: object, *, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if value < minimum:
        raise AudioValidationError(f"{field_name} must be at least {minimum}")
    if value > maximum:
        raise AudioValidationError(f"{field_name} must not exceed {maximum}")
    return value


def _validated_finite_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} must be a finite number, got {type(value).__name__}")
    try:
        number = float(value)
    except OverflowError as exc:
        raise AudioValidationError(f"{field_name} must be finite") from exc
    if not math.isfinite(number):
        raise AudioValidationError(f"{field_name} must be finite")
    return number


def _validated_text(
    value: object,
    *,
    field_name: str,
    max_length: int,
    allow_spaces: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise AudioValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > max_length:
        raise AudioValidationError(f"{field_name} must not exceed {max_length} characters")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise AudioValidationError(f"{field_name} must not contain control characters")
    if not allow_spaces and any(character.isspace() for character in value):
        raise AudioValidationError(f"{field_name} must not contain whitespace")
    return value


def _validated_optional_reason(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _validated_text(
        value,
        field_name=field_name,
        max_length=_MAX_REASON_LENGTH,
        allow_spaces=True,
    )


def _validated_payload(value: object) -> bytes:
    """Accept exactly ``bytes`` for audio data.

    ``bytearray``/``memoryview`` are rejected rather than copied: a frame is a
    frozen value, and a caller still holding a mutable buffer could change the
    audio underneath it after validation and after the digest was recorded.
    """

    if type(value) is not bytes:
        raise TypeError("payload must be bytes")
    if not value:
        raise AudioValidationError("payload must not be empty")
    if len(value) > MAX_AUDIO_PAYLOAD_BYTES:
        raise AudioValidationError(
            f"payload must not exceed {MAX_AUDIO_PAYLOAD_BYTES} bytes (oversized frame)"
        )
    return value


def _validated_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"timestamp must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise AudioValidationError("timestamp must be timezone-aware")
    normalized = value.astimezone(UTC)
    # A provider clock reporting dates outside the Unix epoch window is broken,
    # not merely unusual; rejecting it keeps ordering and gaps meaningful.
    if not _EPOCH_FLOOR <= normalized < _EPOCH_CEILING:
        raise AudioValidationError("timestamp is outside the supported epoch range")
    return normalized


def _validated_span(value: object, *, field_name: str, maximum: timedelta) -> timedelta:
    if not isinstance(value, timedelta):
        raise TypeError(f"{field_name} must be a timedelta, got {type(value).__name__}")
    if value < timedelta(0):
        raise AudioValidationError(f"{field_name} must not be negative")
    if value > maximum:
        raise AudioValidationError(f"{field_name} must not exceed {maximum}")
    return value


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return _validated_timestamp(value)
    if not isinstance(value, str):
        raise AudioValidationError("timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AudioValidationError("timestamp must be a valid ISO-8601 datetime") from exc
    return _validated_timestamp(parsed)


def payload_digest(payload: bytes) -> str:
    """Return the lowercase hex SHA-256 of ``payload``.

    A digest lets a journal, audit record, or conformance harness talk about one
    frame's bytes without embedding them. Audio is data: it is identified here,
    never interpreted.
    """

    return hashlib.sha256(_validated_payload(payload)).hexdigest()


# --------------------------------------------------------------------------
# Identity.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class AudioProviderId:
    """Opaque identity of one configured audio provider.

    Naming a provider is not selecting one. This is the same neutral identifier
    slot ``agentx.cognition.model_provider`` exposes, so a composition root can
    say which configured adapter owns an endpoint without ``agentx.core``
    importing an adapter, a vendor SDK, or a device driver.
    """

    value: str

    def __post_init__(self) -> None:
        _validated_text(self.value, field_name="provider_id", max_length=_MAX_PROVIDER_ID_LENGTH)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class AudioEndpointId:
    """Provider-owned identity of one capture source or playback sink.

    Endpoint identity stays provider-qualified, so two adapters that both call
    something ``default`` cannot collide. Unlike :class:`AudioStreamId` this is
    *not* an AgentX UUID: it is an opaque, untrusted string from outside.
    """

    provider_id: AudioProviderId
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, AudioProviderId):
            raise TypeError(
                f"provider_id must be an AudioProviderId, got {type(self.provider_id).__name__}"
            )
        _validated_text(
            self.value,
            field_name="endpoint_id",
            max_length=_MAX_ENDPOINT_ID_LENGTH,
        )

    def __str__(self) -> str:
        return f"{self.provider_id.value}/{self.value}"


class AudioEndpointKind(StrEnum):
    """Direction of one audio endpoint relative to AgentX."""

    SOURCE = "source"
    SINK = "sink"


# --------------------------------------------------------------------------
# Capability advertisement and endpoint description.
# --------------------------------------------------------------------------


def _validated_format_set(value: object) -> frozenset[AudioFormat]:
    if not isinstance(value, frozenset):
        raise TypeError("formats must be a frozenset")
    if not value:
        raise AudioValidationError("formats must not be empty")
    for fmt in value:
        _validated_format(fmt, field_name="formats")
    return value


def _validated_int_set(
    value: object,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
    allow_empty: bool,
) -> frozenset[int]:
    if not isinstance(value, frozenset):
        raise TypeError(f"{field_name} must be a frozenset")
    if not value and not allow_empty:
        raise AudioValidationError(f"{field_name} must not be empty")
    for item in value:
        _validated_int(item, field_name=field_name, minimum=minimum, maximum=maximum)
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioFormatSupport:
    """What one endpoint (or provider) honestly advertises it can do.

    An empty ``sample_rates_hz`` or ``channel_counts`` set means "anything inside
    the canonical supported window", which is how a device that accepts a
    continuous range is represented without enumerating thousands of rates. A
    listed value is a promise: conformance checking treats a frame outside it as
    a provider defect, never as a value to fall back on.
    """

    formats: frozenset[AudioFormat]
    sample_rates_hz: frozenset[int]
    channel_counts: frozenset[int]
    max_payload_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "formats", _validated_format_set(self.formats))
        object.__setattr__(
            self,
            "sample_rates_hz",
            _validated_int_set(
                self.sample_rates_hz,
                field_name="sample_rates_hz",
                minimum=MIN_SAMPLE_RATE_HZ,
                maximum=MAX_SAMPLE_RATE_HZ,
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "channel_counts",
            _validated_int_set(
                self.channel_counts,
                field_name="channel_counts",
                minimum=MIN_CHANNEL_COUNT,
                maximum=MAX_CHANNEL_COUNT,
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "max_payload_bytes",
            _validated_int(
                self.max_payload_bytes,
                field_name="max_payload_bytes",
                minimum=1,
                maximum=MAX_AUDIO_PAYLOAD_BYTES,
            ),
        )

    def supports(
        self,
        *,
        fmt: AudioFormat,
        sample_rate_hz: int,
        channel_count: int,
    ) -> bool:
        """Whether this advertisement covers one concrete configuration."""

        if fmt not in self.formats:
            return False
        if not is_supported_sample_rate(sample_rate_hz):
            return False
        if not is_supported_channel_count(channel_count):
            return False
        return self._covers(self.sample_rates_hz, sample_rate_hz) and self._covers(
            self.channel_counts, channel_count
        )

    @staticmethod
    def _covers(advertised: frozenset[int], value: int) -> bool:
        """Empty advertisement means "the whole supported window"."""

        return not advertised or value in advertised

    def admits(self, payload_bytes: int) -> bool:
        """Whether a payload of ``payload_bytes`` fits the advertised bound."""

        return 0 < payload_bytes <= self.max_payload_bytes


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioEndpoint:
    """Immutable description of one capture source or playback sink.

    ``label`` is untrusted external text: it is bounded and free of control
    characters, and it never affects validation, admission, or lifecycle. An
    endpoint description states what exists; it is never permission to use it.
    """

    endpoint_id: AudioEndpointId
    kind: AudioEndpointKind
    label: str
    support: AudioFormatSupport
    is_default: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint_id, AudioEndpointId):
            raise TypeError(
                f"endpoint_id must be an AudioEndpointId, got {type(self.endpoint_id).__name__}"
            )
        if not isinstance(self.kind, AudioEndpointKind):
            raise TypeError(f"kind must be an AudioEndpointKind, got {type(self.kind).__name__}")
        _validated_text(
            self.label,
            field_name="label",
            max_length=_MAX_LABEL_LENGTH,
            allow_spaces=True,
        )
        if not isinstance(self.support, AudioFormatSupport):
            raise TypeError(f"support must be an AudioFormatSupport, got {self.support!r}")
        if not isinstance(self.is_default, bool):
            raise TypeError("is_default must be a bool")

    @property
    def provider_id(self) -> AudioProviderId:
        """The provider that owns this endpoint identity."""

        return self.endpoint_id.provider_id


# --------------------------------------------------------------------------
# Bounded buffering / backpressure contract.
# --------------------------------------------------------------------------


class AudioOverflowPolicy(StrEnum):
    """What a bounded buffer is allowed to do when it is full.

    ``REJECT`` is the fail-closed default: the caller is refused and no audio is
    discarded. ``DROP_OLDEST`` trades completeness for freshness, which is what
    a live playback path usually wants, so it must be requested explicitly.
    ``DEFER`` declares that the caller must wait for capacity; this contract only
    states that, it does not implement waiting.
    """

    REJECT = "reject"
    DROP_OLDEST = "drop_oldest"
    DEFER = "defer"


class AudioAdmissionOutcome(StrEnum):
    """Result of asking a bounded buffer to admit one frame."""

    ACCEPTED = "accepted"
    EVICTED = "evicted"
    DEFERRED = "deferred"
    REJECTED_FULL = "rejected_full"
    REJECTED_OVERSIZED = "rejected_oversized"


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioAdmissionDecision:
    """Immutable record of one bounded-buffer admission decision.

    The decision is pure: it reports the occupancy the caller declared plus what
    this policy implies next, and it mutates nothing. A provider's real queue is
    its own business; what is contractual is that capacity is bounded, that
    overflow obeys the declared policy, and that refusal is reported instead of
    hidden.
    """

    outcome: AudioAdmissionOutcome
    policy: AudioBufferPolicy
    buffered_frames: int
    buffered_bytes: int
    frame_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, AudioAdmissionOutcome):
            raise TypeError("outcome must be an AudioAdmissionOutcome")
        if not isinstance(self.policy, AudioBufferPolicy):
            raise TypeError("policy must be an AudioBufferPolicy")
        _validated_int(
            self.buffered_frames,
            field_name="buffered_frames",
            minimum=0,
            maximum=MAX_AUDIO_BUFFER_FRAMES,
        )
        _validated_int(
            self.buffered_bytes,
            field_name="buffered_bytes",
            minimum=0,
            maximum=MAX_AUDIO_BUFFER_BYTES,
        )
        _validated_int(
            self.frame_bytes,
            field_name="frame_bytes",
            minimum=1,
            maximum=MAX_AUDIO_PAYLOAD_BYTES,
        )
        oversized = self.frame_bytes > self.policy.max_bytes
        if (self.outcome is AudioAdmissionOutcome.REJECTED_OVERSIZED) != oversized:
            raise AudioValidationError(
                "REJECTED_OVERSIZED must correspond exactly to a frame larger than policy.max_bytes"
            )

    @property
    def is_accepted(self) -> bool:
        """Whether the frame may be handed to the buffer."""

        return self.outcome in (
            AudioAdmissionOutcome.ACCEPTED,
            AudioAdmissionOutcome.EVICTED,
        )

    @property
    def requires_backpressure(self) -> bool:
        """Whether the caller must wait for capacity instead of being refused."""

        return self.outcome is AudioAdmissionOutcome.DEFERRED

    @property
    def may_drop_audio(self) -> bool:
        """Whether admitting this frame is allowed to discard older audio."""

        return self.outcome is AudioAdmissionOutcome.EVICTED

    @property
    def projected_frames(self) -> int:
        """Buffered frame count after a successful admission under this policy."""

        if not self.is_accepted:
            return self.buffered_frames
        return min(self.buffered_frames + 1, self.policy.max_frames)

    @property
    def projected_bytes(self) -> int:
        """Buffered byte count after a successful admission under this policy."""

        if not self.is_accepted:
            return self.buffered_bytes
        return min(self.buffered_bytes + self.frame_bytes, self.policy.max_bytes)


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioBufferPolicy:
    """Bounded capacity plus the overflow rule for one stream.

    Frame-count and byte limits are independent hard ceilings: a provider may not
    widen one by shrinking the other, and a frame larger than the whole buffer is
    refused regardless of the overflow policy.
    """

    max_frames: int = 64
    max_bytes: int = 256 * 1024
    overflow: AudioOverflowPolicy = AudioOverflowPolicy.REJECT

    def __post_init__(self) -> None:
        _validated_int(
            self.max_frames,
            field_name="max_frames",
            minimum=MIN_AUDIO_BUFFER_FRAMES,
            maximum=MAX_AUDIO_BUFFER_FRAMES,
        )
        _validated_int(
            self.max_bytes,
            field_name="max_bytes",
            minimum=1,
            maximum=MAX_AUDIO_BUFFER_BYTES,
        )
        if not isinstance(self.overflow, AudioOverflowPolicy):
            raise TypeError(
                f"overflow must be an AudioOverflowPolicy, got {type(self.overflow).__name__}"
            )

    def assess(
        self,
        *,
        buffered_frames: int,
        buffered_bytes: int,
        frame_bytes: int,
    ) -> AudioAdmissionDecision:
        """Decide whether one more frame fits, mutating nothing.

        ``buffered_frames``/``buffered_bytes`` are the occupancy the caller
        reports. Because this contract owns no queue of its own, the same pure
        function serves a capture ring, a playback queue, and a test fixture.
        """

        frames = _validated_int(
            buffered_frames,
            field_name="buffered_frames",
            minimum=0,
            maximum=MAX_AUDIO_BUFFER_FRAMES,
        )
        occupied = _validated_int(
            buffered_bytes,
            field_name="buffered_bytes",
            minimum=0,
            maximum=MAX_AUDIO_BUFFER_BYTES,
        )
        size = _validated_int(
            frame_bytes,
            field_name="frame_bytes",
            minimum=1,
            maximum=MAX_AUDIO_PAYLOAD_BYTES,
        )
        over_capacity = frames >= self.max_frames or occupied + size > self.max_bytes
        if size > self.max_bytes:
            # No overflow policy can admit a frame larger than the whole buffer.
            outcome = AudioAdmissionOutcome.REJECTED_OVERSIZED
        elif not over_capacity:
            outcome = AudioAdmissionOutcome.ACCEPTED
        elif self.overflow is AudioOverflowPolicy.DEFER:
            outcome = AudioAdmissionOutcome.DEFERRED
        elif self.overflow is AudioOverflowPolicy.DROP_OLDEST:
            outcome = AudioAdmissionOutcome.EVICTED
        else:
            outcome = AudioAdmissionOutcome.REJECTED_FULL
        return AudioAdmissionDecision(
            outcome=outcome,
            policy=self,
            buffered_frames=frames,
            buffered_bytes=occupied,
            frame_bytes=size,
        )


# --------------------------------------------------------------------------
# Stream configuration.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioStreamDescriptor:
    """Immutable configuration of one audio stream against one endpoint.

    Layout and buffer policy are validated *against the endpoint's own
    advertisement*, so a provider that claims only 16 kHz mono cannot be handed a
    48 kHz stereo stream by a caller who did not look. A descriptor is a request
    and a record, never a grant: it carries no permission, no priority, and no
    retry policy.
    """

    stream_id: AudioStreamId
    endpoint: AudioEndpoint
    format: AudioFormat
    sample_rate_hz: int
    channel_count: int
    buffer: AudioBufferPolicy
    sequence_origin: int = 0
    latency_target_ms: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stream_id, AudioStreamId):
            raise TypeError(
                f"stream_id must be an AudioStreamId, got {type(self.stream_id).__name__}"
            )
        if not isinstance(self.endpoint, AudioEndpoint):
            raise TypeError(
                f"endpoint must be an AudioEndpoint, got {type(self.endpoint).__name__}"
            )
        fmt = _validated_format(self.format, field_name="format")
        sample_rate = _validated_int(
            self.sample_rate_hz,
            field_name="sample_rate_hz",
            minimum=MIN_SAMPLE_RATE_HZ,
            maximum=MAX_SAMPLE_RATE_HZ,
        )
        channels = _validated_int(
            self.channel_count,
            field_name="channel_count",
            minimum=MIN_CHANNEL_COUNT,
            maximum=MAX_CHANNEL_COUNT,
        )
        if not isinstance(self.buffer, AudioBufferPolicy):
            raise TypeError(
                f"buffer must be an AudioBufferPolicy, got {type(self.buffer).__name__}"
            )
        _validated_int(
            self.sequence_origin,
            field_name="sequence_origin",
            minimum=0,
            maximum=MAX_AUDIO_SEQUENCE,
        )
        if self.latency_target_ms is not None:
            latency = _validated_finite_number(
                self.latency_target_ms,
                field_name="latency_target_ms",
            )
            if not MIN_AUDIO_LATENCY_TARGET_MS <= latency <= MAX_AUDIO_LATENCY_TARGET_MS:
                raise AudioValidationError(
                    "latency_target_ms must be between "
                    f"{MIN_AUDIO_LATENCY_TARGET_MS} and {MAX_AUDIO_LATENCY_TARGET_MS}"
                )
            object.__setattr__(self, "latency_target_ms", latency)
        if not self.endpoint.support.supports(
            fmt=fmt,
            sample_rate_hz=sample_rate,
            channel_count=channels,
        ):
            raise AudioValidationError(
                "format/sample rate/channel count are not advertised by the endpoint"
            )
        if self.buffer.max_bytes > self.endpoint.support.max_payload_bytes:
            raise AudioValidationError("buffer.max_bytes exceeds the endpoint payload bound")
        object.__setattr__(self, "sample_rate_hz", sample_rate)
        object.__setattr__(self, "channel_count", channels)

    @property
    def kind(self) -> AudioEndpointKind:
        """Whether this stream captures from a source or feeds a sink."""

        return self.endpoint.kind

    @property
    def provider_id(self) -> AudioProviderId:
        """The provider that owns the endpoint this stream is bound to."""

        return self.endpoint.provider_id

    @property
    def block_align(self) -> int | None:
        """PCM block alignment for this stream, or ``None`` for containers."""

        return block_align_bytes(self.format, self.channel_count)

    def admits_payload(self, payload_bytes: int) -> bool:
        """Whether a payload size fits the contract bound and this buffer policy."""

        if not 0 < payload_bytes <= MAX_AUDIO_PAYLOAD_BYTES:
            return False
        return payload_bytes <= self.buffer.max_bytes

    def metadata(self) -> dict[str, Any]:
        """Return deterministic, JSON-safe configuration metadata.

        Nothing device-sensitive beyond the opaque identifiers is included, and
        no audio data is present because a descriptor never carries any.
        """

        return {
            "schema_version": AUDIO_CONTRACT_SCHEMA_VERSION,
            "stream_id": self.stream_id.to_str(),
            "provider_id": self.provider_id.value,
            "endpoint_id": self.endpoint.endpoint_id.value,
            "endpoint_kind": self.endpoint.kind.value,
            "format": self.format.value,
            "sample_rate_hz": self.sample_rate_hz,
            "channel_count": self.channel_count,
            "block_align": self.block_align,
            "sequence_origin": self.sequence_origin,
            "latency_target_ms": self.latency_target_ms,
            "buffer": {
                "max_frames": self.buffer.max_frames,
                "max_bytes": self.buffer.max_bytes,
                "overflow": self.buffer.overflow.value,
            },
        }


# --------------------------------------------------------------------------
# Frames.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioFrame:
    """One bounded, sequenced, timestamped chunk of audio for one stream.

    ``payload`` is opaque data: nothing here decodes it or treats its content as
    instruction. The only structural rules applied to it are the size bound and
    PCM block alignment. ``sample_frames`` (samples per channel) is optional for
    compressed payloads because a frame count is a decode-time fact; when it is
    given for PCM it must agree with the payload length, so a provider cannot
    misreport how long a frame lasts.
    """

    stream_id: AudioStreamId
    format: AudioFormat
    sample_rate_hz: int
    channel_count: int
    sequence: int
    timestamp: datetime
    # Payload is excluded from repr(): a frame is logged as *what* it is, never as
    # a megabyte of audio pasted into a console or journal line.
    payload: bytes = field(repr=False)
    sample_frames: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stream_id, AudioStreamId):
            raise TypeError(
                f"stream_id must be an AudioStreamId, got {type(self.stream_id).__name__}"
            )
        fmt = _validated_format(self.format, field_name="format")
        sample_rate = _validated_int(
            self.sample_rate_hz,
            field_name="sample_rate_hz",
            minimum=MIN_SAMPLE_RATE_HZ,
            maximum=MAX_SAMPLE_RATE_HZ,
        )
        channels = _validated_int(
            self.channel_count,
            field_name="channel_count",
            minimum=MIN_CHANNEL_COUNT,
            maximum=MAX_CHANNEL_COUNT,
        )
        _validated_int(
            self.sequence,
            field_name="sequence",
            minimum=0,
            maximum=MAX_AUDIO_SEQUENCE,
        )
        payload = _validated_payload(self.payload)
        align = block_align_bytes(fmt, channels)
        frames: int | None
        if align is None:
            frames = self.sample_frames
            if frames is not None:
                _validated_int(
                    frames,
                    field_name="sample_frames",
                    minimum=1,
                    maximum=MAX_AUDIO_SAMPLE_FRAMES,
                )
        else:
            if len(payload) % align:
                raise AudioValidationError(
                    f"payload length {len(payload)} is not a multiple of the PCM block alignment "
                    f"{align} for {fmt.value} with {channels} channel(s)"
                )
            derived = len(payload) // align
            declared = self.sample_frames
            if declared is not None and declared != derived:
                raise AudioValidationError(
                    f"sample_frames {declared} disagrees with the payload frame count {derived}"
                )
            frames = derived

        object.__setattr__(self, "sample_rate_hz", sample_rate)
        object.__setattr__(self, "channel_count", channels)
        object.__setattr__(self, "timestamp", _validated_timestamp(self.timestamp))
        object.__setattr__(self, "sample_frames", frames)

    @property
    def byte_count(self) -> int:
        """Length of :attr:`payload` in bytes."""

        return len(self.payload)

    @property
    def duration(self) -> timedelta | None:
        """Play time this frame covers, or ``None`` when it cannot be derived."""

        if self.sample_frames is None:
            return None
        return timedelta(microseconds=(self.sample_frames * 1_000_000) // self.sample_rate_hz)

    @property
    def payload_digest(self) -> str:
        """Lowercase hex SHA-256 of :attr:`payload`."""

        return hashlib.sha256(self.payload).hexdigest()

    def metadata(self) -> dict[str, Any]:
        """Return JSON-safe metadata for this frame, without its bytes.

        Audio payload belongs in a payload store, never in a metadata journal, so
        this mapping describes the frame — identity, layout, ordering, timing,
        size, digest — and carries zero audio data.
        """

        duration = self.duration
        return {
            "schema_version": AUDIO_CONTRACT_SCHEMA_VERSION,
            "stream_id": self.stream_id.to_str(),
            "format": self.format.value,
            "sample_rate_hz": self.sample_rate_hz,
            "channel_count": self.channel_count,
            "sequence": self.sequence,
            "timestamp": _format_timestamp(self.timestamp),
            "payload_bytes": len(self.payload),
            "payload_sha256": self.payload_digest,
            "sample_frames": self.sample_frames,
            "duration_micros": None if duration is None else duration // timedelta(microseconds=1),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return frame metadata plus the payload as bounded base64 text.

        The payload stays data, not interpretation: it is encoded so a frame can
        cross a text-safe boundary without assuming codec or container support.
        """

        data = self.metadata()
        data["payload_b64"] = base64.b64encode(self.payload).decode("ascii")
        return data

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> AudioFrame:
        """Rebuild a frame from :meth:`to_dict` output, re-validating everything.

        Unknown keys, missing keys, a payload that disagrees with the recorded
        size or digest, and non-canonical base64 are all rejected: serialization
        is a round trip, not a trust boundary to be waved through.
        """

        if not isinstance(raw, Mapping):
            raise AudioValidationError("frame mapping must be a mapping")
        expected = frozenset(
            {
                "schema_version",
                "stream_id",
                "format",
                "sample_rate_hz",
                "channel_count",
                "sequence",
                "timestamp",
                "payload_bytes",
                "payload_sha256",
                "sample_frames",
                "duration_micros",
                "payload_b64",
            }
        )
        keys = frozenset(raw)
        if keys != expected:
            missing = sorted(expected - keys)
            unexpected = sorted(keys - expected)
            raise AudioValidationError(
                f"frame mapping keys mismatch (missing={missing}, unexpected={unexpected})"
            )
        version = raw["schema_version"]
        if version != AUDIO_CONTRACT_SCHEMA_VERSION:
            raise AudioValidationError(
                f"unsupported audio schema version {version!r}; "
                f"expected {AUDIO_CONTRACT_SCHEMA_VERSION}"
            )
        return cls(
            stream_id=_parse_stream_id(raw["stream_id"]),
            format=_parse_format(raw["format"]),
            sample_rate_hz=_validated_int(
                raw["sample_rate_hz"],
                field_name="sample_rate_hz",
                minimum=MIN_SAMPLE_RATE_HZ,
                maximum=MAX_SAMPLE_RATE_HZ,
            ),
            channel_count=_validated_int(
                raw["channel_count"],
                field_name="channel_count",
                minimum=MIN_CHANNEL_COUNT,
                maximum=MAX_CHANNEL_COUNT,
            ),
            sequence=_validated_int(
                raw["sequence"],
                field_name="sequence",
                minimum=0,
                maximum=MAX_AUDIO_SEQUENCE,
            ),
            timestamp=_parse_timestamp(raw["timestamp"]),
            payload=_decode_payload(raw),
            sample_frames=_parse_optional_int(raw["sample_frames"], field_name="sample_frames"),
        )


def _parse_stream_id(value: object) -> AudioStreamId:
    if not isinstance(value, str):
        raise AudioValidationError("stream_id must be a string")
    try:
        return AudioStreamId.parse(value)
    except ValueError as exc:
        raise AudioValidationError(f"stream_id is not a valid audio stream id: {exc}") from exc


def _parse_format(value: object) -> AudioFormat:
    if not isinstance(value, str):
        raise AudioValidationError("format must be a string")
    try:
        return AudioFormat(value)
    except ValueError as exc:
        raise AudioValidationError(f"unsupported audio format {value!r}") from exc


def _parse_optional_int(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise AudioValidationError(f"{field_name} must be an int or null")
    return value


def _decode_payload(raw: Mapping[str, Any]) -> bytes:
    encoded = raw["payload_b64"]
    if not isinstance(encoded, str):
        raise AudioValidationError("payload_b64 must be a string")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AudioValidationError("payload_b64 is not valid base64") from exc
    if base64.b64encode(payload).decode("ascii") != encoded:
        raise AudioValidationError("payload_b64 is not canonical base64")
    if raw["payload_bytes"] != len(payload):
        raise AudioValidationError("payload_bytes does not match the decoded payload")
    digest = raw["payload_sha256"]
    if not isinstance(digest, str) or digest != hashlib.sha256(payload).hexdigest():
        raise AudioValidationError("payload_sha256 does not match the decoded payload")
    return payload


# --------------------------------------------------------------------------
# Ordering and timing checks.
# --------------------------------------------------------------------------


class AudioSequenceIssue(StrEnum):
    """Ways a frame sequence number can fail the ordering contract."""

    DUPLICATE = "duplicate"
    REGRESSION = "regression"
    GAP = "gap"


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioSequenceCheck:
    """Outcome of checking one observed sequence number against the expectation."""

    expected: int
    observed: int
    issues: frozenset[AudioSequenceIssue]

    def __post_init__(self) -> None:
        _validated_int(
            self.expected,
            field_name="expected",
            minimum=0,
            maximum=MAX_AUDIO_SEQUENCE,
        )
        _validated_int(
            self.observed,
            field_name="observed",
            minimum=0,
            maximum=MAX_AUDIO_SEQUENCE,
        )
        if not isinstance(self.issues, frozenset) or any(
            not isinstance(issue, AudioSequenceIssue) for issue in self.issues
        ):
            raise TypeError("issues must be a frozenset of AudioSequenceIssue")

    @property
    def is_valid(self) -> bool:
        """Whether the observed sequence continues the stream exactly."""

        return not self.issues

    @property
    def skip(self) -> int:
        """How many sequence numbers were skipped (zero unless there is a gap)."""

        if AudioSequenceIssue.GAP not in self.issues:
            return 0
        return self.observed - self.expected

    @property
    def next_expected(self) -> int | None:
        """Expected sequence for the following frame, or ``None`` when exhausted.

        A stream that consumed :data:`MAX_AUDIO_SEQUENCE` must start a new
        stream. Silent wrap-around is rejected on purpose: it would make a
        replayed early frame look perfectly in order.
        """

        if self.observed >= MAX_AUDIO_SEQUENCE:
            return None
        return self.observed + 1


def check_sequence(*, expected: int, observed: int) -> AudioSequenceCheck:
    """Compare ``observed`` against the ``expected`` next sequence number.

    Pure and stateless: the numbers belong to the caller's stream tracker, so the
    same function serves a capture source counting what it produced and a
    consumer counting what it received.
    """

    expectation = _validated_int(
        expected,
        field_name="expected",
        minimum=0,
        maximum=MAX_AUDIO_SEQUENCE,
    )
    value = _validated_int(
        observed,
        field_name="observed",
        minimum=0,
        maximum=MAX_AUDIO_SEQUENCE,
    )
    issues: set[AudioSequenceIssue] = set()
    if value == expectation - 1:
        issues.add(AudioSequenceIssue.DUPLICATE)
    elif value < expectation:
        issues.add(AudioSequenceIssue.REGRESSION)
    elif value > expectation:
        issues.add(AudioSequenceIssue.GAP)
    return AudioSequenceCheck(expected=expectation, observed=value, issues=frozenset(issues))


class AudioTimestampIssue(StrEnum):
    """Ways a frame timestamp can fail the timing contract."""

    NON_MONOTONIC = "non_monotonic"
    GAP_EXCEEDED = "gap_exceeded"


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioTimestampCheck:
    """Outcome of checking one frame timestamp against the previous frame."""

    observed: datetime
    previous: datetime | None
    max_gap: timedelta
    issues: frozenset[AudioTimestampIssue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed", _validated_timestamp(self.observed))
        if self.previous is not None:
            object.__setattr__(self, "previous", _validated_timestamp(self.previous))
        _validated_span(self.max_gap, field_name="max_gap", maximum=MAX_TIMESTAMP_GAP)
        if not isinstance(self.issues, frozenset) or any(
            not isinstance(issue, AudioTimestampIssue) for issue in self.issues
        ):
            raise TypeError("issues must be a frozenset of AudioTimestampIssue")

    @property
    def is_valid(self) -> bool:
        """Whether the timestamp is in order and inside the declared gap bound."""

        return not self.issues

    @property
    def delta(self) -> timedelta | None:
        """Elapsed time since the previous frame, or ``None`` for the first frame."""

        if self.previous is None:
            return None
        return self.observed - self.previous


def check_timestamp(
    *,
    observed: datetime,
    previous: datetime | None = None,
    max_gap: timedelta = DEFAULT_MAX_TIMESTAMP_GAP,
) -> AudioTimestampCheck:
    """Check one stream timestamp against the previous one and a gap bound.

    Equal timestamps are accepted, because two frames may legitimately carry the
    same reading from a coarse provider clock. A regression is always a defect:
    silently reordering audio is how a stream loses words.
    """

    validated = _validated_timestamp(observed)
    gap = _validated_span(max_gap, field_name="max_gap", maximum=MAX_TIMESTAMP_GAP)
    prior = None if previous is None else _validated_timestamp(previous)
    issues: set[AudioTimestampIssue] = set()
    if prior is not None:
        if validated < prior:
            issues.add(AudioTimestampIssue.NON_MONOTONIC)
        elif validated - prior > gap:
            issues.add(AudioTimestampIssue.GAP_EXCEEDED)
    return AudioTimestampCheck(
        observed=validated,
        previous=prior,
        max_gap=gap,
        issues=frozenset(issues),
    )


# --------------------------------------------------------------------------
# Stream lifecycle.
# --------------------------------------------------------------------------


class AudioStreamState(StrEnum):
    """Closed lifecycle vocabulary for one audio stream.

    ``NEW`` is constructed-but-unopened, ``OPEN`` transfers frames, ``DRAINING``
    stops accepting new frames while queued audio finishes, and
    ``CLOSED``/``CANCELLED``/``FAILED`` are terminal. There is no ``PAUSED`` and
    no re-entry: a stream that stopped is a finished stream, and restarting means
    opening a new one so identity, sequence, and timestamps stay unambiguous.
    """

    NEW = "new"
    OPEN = "open"
    DRAINING = "draining"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    FAILED = "failed"


def _validated_state(state: object, *, field_name: str) -> AudioStreamState:
    if not isinstance(state, AudioStreamState):
        raise TypeError(f"{field_name} must be an AudioStreamState, got {type(state).__name__}")
    return state


#: The exhaustive lifecycle matrix: every state maps to the exact set of states it
#: may move to, including itself. An empty set means terminal, so terminality has
#: a single source of truth.
LEGAL_AUDIO_STREAM_TRANSITIONS: Final[Mapping[AudioStreamState, frozenset[AudioStreamState]]] = (
    MappingProxyType(
        {
            AudioStreamState.NEW: frozenset(
                {
                    AudioStreamState.OPEN,
                    AudioStreamState.CANCELLED,
                    AudioStreamState.FAILED,
                }
            ),
            AudioStreamState.OPEN: frozenset(
                {
                    AudioStreamState.DRAINING,
                    AudioStreamState.CLOSED,
                    AudioStreamState.CANCELLED,
                    AudioStreamState.FAILED,
                }
            ),
            AudioStreamState.DRAINING: frozenset(
                {
                    AudioStreamState.CLOSED,
                    AudioStreamState.CANCELLED,
                    AudioStreamState.FAILED,
                }
            ),
            AudioStreamState.CLOSED: frozenset(),
            AudioStreamState.CANCELLED: frozenset(),
            AudioStreamState.FAILED: frozenset(),
        }
    )
)

#: States with no outgoing transition, derived from the matrix above.
TERMINAL_AUDIO_STREAM_STATES: Final[frozenset[AudioStreamState]] = frozenset(
    state for state in AudioStreamState if not LEGAL_AUDIO_STREAM_TRANSITIONS[state]
)


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioStreamStatus:
    """Immutable lifecycle observation of one stream.

    ``reason`` is bounded, untrusted explanatory text from a provider. It never
    changes the state, the legality of a transition, or any admission decision.
    """

    state: AudioStreamState
    reason: str | None = None

    def __post_init__(self) -> None:
        _validated_state(self.state, field_name="state")
        _validated_optional_reason(self.reason, field_name="reason")

    @property
    def is_terminal(self) -> bool:
        """Whether no further transition is possible."""

        return is_terminal_audio_state(self.state)

    @property
    def transfers_frames(self) -> bool:
        """Whether frames may legitimately move in this state."""

        return self.state is AudioStreamState.OPEN


def legal_audio_transitions(state: AudioStreamState) -> frozenset[AudioStreamState]:
    """Return the states ``state`` may move to; an empty set means terminal."""

    return LEGAL_AUDIO_STREAM_TRANSITIONS[_validated_state(state, field_name="state")]


def is_terminal_audio_state(state: AudioStreamState) -> bool:
    """Whether ``state`` has no outgoing transition."""

    return _validated_state(state, field_name="state") in TERMINAL_AUDIO_STREAM_STATES


def can_transition_audio_state(current: AudioStreamState, target: AudioStreamState) -> bool:
    """Whether ``current -> target`` is legal. Self-transitions never are."""

    validated_current = _validated_state(current, field_name="current")
    validated_target = _validated_state(target, field_name="target")
    return validated_target in LEGAL_AUDIO_STREAM_TRANSITIONS[validated_current]


def audio_transition_error(
    current: AudioStreamState,
    target: AudioStreamState,
) -> AgentXError:
    """Return the structured failure for an illegal ``current -> target`` move."""

    validated_current = _validated_state(current, field_name="current")
    validated_target = _validated_state(target, field_name="target")
    return audio_failure(
        AudioFailureKind.INVALID_STATE_TRANSITION,
        message=(
            f"audio stream state transition {validated_current.value} -> "
            f"{validated_target.value} is not allowed"
        ),
        details={
            "current_state": validated_current.value,
            "target_state": validated_target.value,
            "allowed_targets": sorted(
                state.value for state in legal_audio_transitions(validated_current)
            ),
        },
    )


def validate_audio_state_transition(
    current: AudioStreamState,
    target: AudioStreamState,
) -> AudioStreamStatus:
    """Return the confirmed target status, or raise when the move is illegal."""

    if not can_transition_audio_state(current, target):
        raise AudioValidationError(str(audio_transition_error(current, target)))
    return AudioStreamStatus(state=_validated_state(target, field_name="target"))


def try_transition_audio_state(
    current: AudioStreamState,
    target: AudioStreamState,
) -> Result[AudioStreamStatus, AgentXError]:
    """Return the transitioned status as a ``Result`` for cross-boundary callers."""

    if not can_transition_audio_state(current, target):
        return Result[AudioStreamStatus, AgentXError].failure(
            audio_transition_error(current, target)
        )
    return Result[AudioStreamStatus, AgentXError].success(AudioStreamStatus(state=target))


# --------------------------------------------------------------------------
# Failure taxonomy.
# --------------------------------------------------------------------------


class AudioFailureKind(StrEnum):
    """Expected operational audio failures a caller must handle.

    Contract violations (a malformed frame, an out-of-range sample rate) are
    programming errors and raise :class:`AudioValidationError` instead. This
    vocabulary is for failures a correct caller can still meet at runtime.
    """

    UNSUPPORTED_CONFIGURATION = "unsupported_configuration"
    UNSUPPORTED_ENDPOINT = "unsupported_endpoint"
    EMPTY_PAYLOAD = "empty_payload"
    OVERSIZED_PAYLOAD = "oversized_payload"
    MISALIGNED_PAYLOAD = "misaligned_payload"
    SEQUENCE_DISORDER = "sequence_disorder"
    TIMESTAMP_DISORDER = "timestamp_disorder"
    INVALID_STATE_TRANSITION = "invalid_state_transition"
    NOT_OPEN = "not_open"
    ALREADY_OPEN = "already_open"
    BUFFER_FULL = "buffer_full"
    BACKPRESSURE = "backpressure"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CONFIGURATION = "configuration"
    INTERNAL = "internal"


_FAILURE_POLICY: Final[Mapping[AudioFailureKind, tuple[ErrorCategory, Retryability]]] = (
    MappingProxyType(
        {
            AudioFailureKind.UNSUPPORTED_CONFIGURATION: (
                ErrorCategory.PRECONDITION,
                Retryability.NON_RETRYABLE,
            ),
            AudioFailureKind.UNSUPPORTED_ENDPOINT: (
                ErrorCategory.NOT_FOUND,
                Retryability.NON_RETRYABLE,
            ),
            AudioFailureKind.EMPTY_PAYLOAD: (ErrorCategory.VALIDATION, Retryability.NON_RETRYABLE),
            AudioFailureKind.OVERSIZED_PAYLOAD: (
                ErrorCategory.VALIDATION,
                Retryability.NON_RETRYABLE,
            ),
            AudioFailureKind.MISALIGNED_PAYLOAD: (
                ErrorCategory.VALIDATION,
                Retryability.NON_RETRYABLE,
            ),
            AudioFailureKind.SEQUENCE_DISORDER: (
                ErrorCategory.CONFLICT,
                Retryability.NON_RETRYABLE,
            ),
            AudioFailureKind.TIMESTAMP_DISORDER: (
                ErrorCategory.CONFLICT,
                Retryability.NON_RETRYABLE,
            ),
            AudioFailureKind.INVALID_STATE_TRANSITION: (
                ErrorCategory.CONFLICT,
                Retryability.NON_RETRYABLE,
            ),
            AudioFailureKind.NOT_OPEN: (ErrorCategory.PRECONDITION, Retryability.RETRYABLE),
            AudioFailureKind.ALREADY_OPEN: (ErrorCategory.CONFLICT, Retryability.NON_RETRYABLE),
            AudioFailureKind.BUFFER_FULL: (ErrorCategory.RESOURCE, Retryability.RETRYABLE),
            AudioFailureKind.BACKPRESSURE: (ErrorCategory.RESOURCE, Retryability.RETRYABLE),
            AudioFailureKind.CANCELLED: (ErrorCategory.CANCELLED, Retryability.NON_RETRYABLE),
            AudioFailureKind.TIMEOUT: (ErrorCategory.TIMEOUT, Retryability.RETRYABLE),
            AudioFailureKind.PROVIDER_UNAVAILABLE: (
                ErrorCategory.DEPENDENCY,
                Retryability.RETRYABLE,
            ),
            AudioFailureKind.CONFIGURATION: (
                ErrorCategory.PRECONDITION,
                Retryability.NON_RETRYABLE,
            ),
            AudioFailureKind.INTERNAL: (ErrorCategory.INTERNAL, Retryability.UNKNOWN),
        }
    )
)


def audio_failure(
    kind: AudioFailureKind,
    *,
    message: str,
    stream_id: AudioStreamId | None = None,
    details: Mapping[str, Any] | None = None,
) -> AgentXError:
    """Build a canonical ``AgentXError`` for an expected audio failure.

    A7.01 adds no competing error hierarchy: failures stay
    ``Result.failure(AgentXError)`` values under a stable ``audio.*`` code, with
    the optional stream identity and caller-supplied details as the only
    structured context. Audio payload bytes never appear in an error.
    """

    if not isinstance(kind, AudioFailureKind):
        raise TypeError(f"kind must be an AudioFailureKind, got {type(kind).__name__}")
    _validated_text(
        message,
        field_name="message",
        max_length=_MAX_MESSAGE_LENGTH,
        allow_spaces=True,
    )
    if stream_id is not None and not isinstance(stream_id, AudioStreamId):
        raise TypeError(f"stream_id must be an AudioStreamId or None, got {stream_id!r}")
    if details is not None and not isinstance(details, Mapping):
        raise TypeError("details must be a mapping or None")

    category, retryability = _FAILURE_POLICY[kind]
    error_details: dict[str, Any] = {"audio_failure_kind": kind.value}
    if stream_id is not None:
        error_details["stream_id"] = stream_id.to_str()
    if details:
        error_details.update(details)
    return AgentXError(
        code=f"audio.{kind.value}",
        message=message,
        category=category,
        retryability=retryability,
        details=error_details,
    )


def admission_failure(
    decision: AudioAdmissionDecision,
    *,
    stream_id: AudioStreamId | None = None,
) -> AgentXError:
    """Translate a refused admission decision into its canonical failure value.

    Accepted, evicting, and deferring decisions have nothing to report and raise
    instead, so a caller cannot invent a "why did this fail" string for a
    decision that did not fail.
    """

    if not isinstance(decision, AudioAdmissionDecision):
        raise TypeError(f"decision must be an AudioAdmissionDecision, got {type(decision)!r}")
    kind: AudioFailureKind
    match decision.outcome:
        case AudioAdmissionOutcome.REJECTED_FULL:
            kind = AudioFailureKind.BUFFER_FULL
        case AudioAdmissionOutcome.REJECTED_OVERSIZED:
            kind = AudioFailureKind.OVERSIZED_PAYLOAD
        case AudioAdmissionOutcome.DEFERRED:
            kind = AudioFailureKind.BACKPRESSURE
        case _:
            raise AudioValidationError(
                f"{decision.outcome.value} is not a refusal and has no failure value"
            )
    return audio_failure(
        kind,
        message=(
            f"audio buffer refused the frame: outcome={decision.outcome.value} "
            f"buffered_frames={decision.buffered_frames}/{decision.policy.max_frames} "
            f"buffered_bytes={decision.buffered_bytes}/{decision.policy.max_bytes} "
            f"frame_bytes={decision.frame_bytes}"
        ),
        stream_id=stream_id,
        details={
            "overflow_policy": decision.policy.overflow.value,
            "max_frames": decision.policy.max_frames,
            "max_bytes": decision.policy.max_bytes,
            "frame_bytes": decision.frame_bytes,
        },
    )


# --------------------------------------------------------------------------
# Provider-facing protocols.
# --------------------------------------------------------------------------


@runtime_checkable
class AudioCaptureStream(Protocol):
    """One capture stream handed out by an :class:`AudioProvider`.

    A provider that supports a source endpoint returns an object with this shape;
    A7.01 implements none of them. ``status`` may only move along
    :data:`LEGAL_AUDIO_STREAM_TRANSITIONS`, ``read`` must produce frames that
    satisfy the descriptor the stream was opened with, and both must refuse work
    once the stream is terminal instead of pretending to succeed.
    """

    @property
    def descriptor(self) -> AudioStreamDescriptor:
        """The immutable configuration this stream was opened with."""
        ...

    @property
    def status(self) -> AudioStreamStatus:
        """The current lifecycle observation."""
        ...

    @property
    def cancellation_token(self) -> CancellationToken:
        """Read-only cooperative cancellation signal owned by the provider."""
        ...

    def read(self) -> Result[AudioFrame, AgentXError]:
        """Return the next frame, or the reason none can be produced."""
        ...

    def close(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        """Finish the stream gracefully, exactly once."""
        ...

    def cancel(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        """Request cooperative cancellation; idempotent, first request wins."""
        ...


@runtime_checkable
class AudioPlaybackStream(Protocol):
    """One playback (sink) stream handed out by an :class:`AudioProvider`.

    ``write`` reports the bounded-buffer admission decision instead of silently
    accepting, so a caller that overruns a real-time sink learns about it
    explicitly instead of discovering it as dropped speech.
    """

    @property
    def descriptor(self) -> AudioStreamDescriptor:
        """The immutable configuration this stream was opened with."""
        ...

    @property
    def status(self) -> AudioStreamStatus:
        """The current lifecycle observation."""
        ...

    @property
    def cancellation_token(self) -> CancellationToken:
        """Read-only cooperative cancellation signal owned by the provider."""
        ...

    def write(self, frame: AudioFrame) -> Result[AudioAdmissionDecision, AgentXError]:
        """Submit one frame to the sink, or explain why it was not admitted."""
        ...

    def drain(self) -> Result[AudioStreamStatus, AgentXError]:
        """Stop accepting frames while queued audio finishes."""
        ...

    def close(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        """Finish the stream gracefully, exactly once."""
        ...

    def cancel(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        """Request cooperative cancellation; idempotent, first request wins."""
        ...


@runtime_checkable
class AudioProvider(Protocol):
    """Provider-neutral audio adapter boundary.

    A provider enumerates endpoints and opens streams. It is a resource, not an
    authority: it performs no transcription, synthesis, or machine action, and
    nothing it reports — endpoint labels, advertised support, lifecycle status,
    or frames — grants permission or counts as verification. A7.01 selects no
    provider, registers none, and implements none.
    """

    @property
    def provider_id(self) -> AudioProviderId:
        """The configured provider identity this adapter answers to."""
        ...

    def supports(self, config: AudioFormatSupport) -> bool:
        """Whether this provider can honestly serve the requested advertisement."""
        ...

    def endpoints(
        self,
        kind: AudioEndpointKind | None = None,
    ) -> Result[tuple[AudioEndpoint, ...], AgentXError]:
        """Enumerate sources, sinks, or both, as inert discovery data."""
        ...

    def open(
        self,
        descriptor: AudioStreamDescriptor,
        *,
        deadline: Deadline | None = None,
    ) -> Result[AudioCaptureStream | AudioPlaybackStream, AgentXError]:
        """Open one stream against ``descriptor`` and return its handle."""
        ...
