"""Reusable A7.01 audio-provider conformance scaffold (test support only).

``audio_conformance_violations`` drives any object that claims to satisfy
``agentx.core.audio.AudioProvider`` through the contract in-process and returns a
readable tuple of findings. It names no vendor, opens no device, and knows nothing
about transcription or synthesis: everything it checks is a promise A7.01 makes.
A future real adapter (A7.02 and friends) is conformant when this scaffold reports
nothing for it, which is how a vendor implementation should be gated instead of
being trusted because it "worked on one machine".

Promises checked
----------------

1. Structural protocol shape and provider-qualified endpoint identity.
2. Enumeration honesty: ``supports()`` may not promise what the provider's own
   endpoints deny.
3. A stream never contradicts the descriptor it was opened with.
4. Frames are bounded, block-aligned, and carry the stream's own identity.
5. Sequence numbers continue exactly: no gap, no duplicate, no regression.
6. Timestamps never regress and stay inside the declared gap bound.
7. Lifecycle moves obey the canonical transition matrix and terminate once.
8. Cancellation is real: reported *and* observable through the token.
9. A bounded sink refuses or evicts instead of silently overflowing.
10. Audio payload bytes never leak into an error message.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

from agentx.core.audio import (
    DEFAULT_MAX_TIMESTAMP_GAP,
    MAX_AUDIO_PAYLOAD_BYTES,
    AudioAdmissionDecision,
    AudioBufferPolicy,
    AudioCaptureStream,
    AudioEndpoint,
    AudioEndpointKind,
    AudioFormat,
    AudioFormatSupport,
    AudioFrame,
    AudioPlaybackStream,
    AudioProvider,
    AudioProviderId,
    AudioStreamDescriptor,
    AudioStreamState,
    AudioStreamStatus,
    block_align_bytes,
    check_sequence,
    check_timestamp,
    is_pcm_format,
    is_terminal_audio_state,
)
from agentx.core.errors import AgentXError
from agentx.core.ids import AudioStreamId
from agentx.core.result import Result
from tests.support.audio_provider import payload_for

#: Maximum frames the scaffold asks one capture stream to produce.
CONFORMANCE_FRAME_LIMIT: Final[int] = 8

#: Frame spacing the scaffold writes into a sink.
CONFORMANCE_FRAME_INTERVAL: Final[timedelta] = timedelta(milliseconds=20)

_EXOTIC_SAMPLE_RATE_HZ: Final[int] = 44_100

#: Probe order: raw PCM first, so block alignment and sample-count agreement are
#: exercised whenever a provider can serve them at all.
_FORMAT_PROBE_ORDER: Final[tuple[AudioFormat, ...]] = (
    AudioFormat.PCM_S16LE,
    AudioFormat.PCM_F32LE,
    AudioFormat.FLAC,
    AudioFormat.OPUS,
)


def audio_conformance_violations(
    provider: object,
    *,
    frame_limit: int = CONFORMANCE_FRAME_LIMIT,
) -> tuple[str, ...]:
    """Return every way ``provider`` fails the A7.01 audio provider contract.

    An empty tuple means the provider is conformant for everything this scaffold
    can observe in-process. A non-empty tuple lists independent, human-readable
    findings, one per violated promise.
    """

    if not isinstance(provider, AudioProvider):
        return ("provider does not structurally satisfy the AudioProvider protocol",)

    findings: list[str] = []
    _identity_findings(provider, findings)
    endpoints = _endpoint_findings(provider, findings)
    _support_honesty_findings(provider, findings)
    if not endpoints:
        findings.append("provider must enumerate at least one source and one sink endpoint")
        return tuple(findings)
    sources = tuple(item for item in endpoints if item.kind is AudioEndpointKind.SOURCE)
    sinks = tuple(item for item in endpoints if item.kind is AudioEndpointKind.SINK)
    if not sources or not sinks:
        findings.append("provider must enumerate at least one source and one sink endpoint")
        return tuple(findings)

    capture = _open_stream(provider, sources[0], findings, role="capture")
    if capture is not None and isinstance(capture, AudioCaptureStream):
        _capture_findings(provider, capture, findings, frame_limit=frame_limit)
    playback_endpoint = sinks[0]
    playback_descriptor = _descriptor_for(playback_endpoint)
    playback = _open(provider, playback_descriptor, findings, role="playback")
    if playback is not None and isinstance(playback, AudioPlaybackStream):
        _backpressure_findings(playback, playback_descriptor, findings)
        _drain_findings(provider, playback_descriptor, findings)
        _lifecycle_findings(provider, playback_descriptor, findings, role="playback")
        _cancellation_findings(provider, playback_descriptor, findings, role="playback")
    return tuple(findings)


def assert_audio_provider_conforms(
    provider: object,
    *,
    frame_limit: int = CONFORMANCE_FRAME_LIMIT,
) -> None:
    """Raise with every conformance finding when ``provider`` breaks the contract."""

    violations = audio_conformance_violations(provider, frame_limit=frame_limit)
    if violations:
        joined = "\n  - ".join(violations)
        raise AssertionError(f"audio provider is not A7.01 conformant:\n  - {joined}")


# --------------------------------------------------------------------------
# Shape, identity, enumeration.
# --------------------------------------------------------------------------


def _record(findings: list[str], satisfied: bool, message: str) -> bool:
    if not satisfied:
        findings.append(message)
    return satisfied


def _identity_findings(provider: AudioProvider, findings: list[str]) -> None:
    _record(
        findings,
        isinstance(provider.provider_id, AudioProviderId),
        "provider_id must be an AudioProviderId",
    )


def _endpoint_findings(provider: AudioProvider, findings: list[str]) -> tuple[AudioEndpoint, ...]:
    result = provider.endpoints()
    if not _record(findings, result.is_success, "endpoints() must succeed for a ready provider"):
        return ()
    endpoints = result.unwrap()
    if not _record(findings, isinstance(endpoints, tuple), "endpoints() must return a tuple"):
        return ()

    seen: set[str] = set()
    defaults: dict[AudioEndpointKind, int] = {}
    for endpoint in endpoints:
        name = str(endpoint.endpoint_id)
        _record(
            findings,
            endpoint.provider_id == provider.provider_id,
            f"{name}: endpoint is not owned by this provider",
        )
        _record(
            findings,
            bool(endpoint.support.formats),
            f"{name}: endpoint advertises no format",
        )
        _record(
            findings,
            endpoint.label.strip() == endpoint.label,
            f"{name}: endpoint label must be trimmed",
        )
        _record(findings, name not in seen, f"duplicate endpoint identity {name}")
        seen.add(name)
        defaults[endpoint.kind] = defaults.get(endpoint.kind, 0) + int(endpoint.is_default)
    for kind, count in defaults.items():
        _record(
            findings,
            count <= 1,
            f"at most one default {kind.value} endpoint may be advertised",
        )
    return tuple(endpoints)


def _support_honesty_findings(provider: AudioProvider, findings: list[str]) -> None:
    result = provider.endpoints(AudioEndpointKind.SOURCE)
    if not result.is_success or not result.unwrap():
        return
    advertised = result.unwrap()[0].support
    channels = advertised.channel_counts or frozenset({1})
    covers_exotic = any(
        advertised.supports(
            fmt=fmt,
            sample_rate_hz=_EXOTIC_SAMPLE_RATE_HZ,
            channel_count=count,
        )
        for fmt in advertised.formats
        for count in channels
    )
    exotic = AudioFormatSupport(
        formats=advertised.formats,
        sample_rates_hz=frozenset({_EXOTIC_SAMPLE_RATE_HZ}),
        channel_counts=advertised.channel_counts,
        max_payload_bytes=advertised.max_payload_bytes,
    )
    _record(
        findings,
        provider.supports(exotic) is covers_exotic,
        "supports() disagrees with the provider's own endpoint advertisement",
    )


# --------------------------------------------------------------------------
# Opening and reading.
# --------------------------------------------------------------------------


def _descriptor_for(endpoint: AudioEndpoint, *, max_frames: int = 4) -> AudioStreamDescriptor:
    advertised = endpoint.support
    fmt = _preferred_format(advertised.formats)
    rate = next(iter(sorted(advertised.sample_rates_hz or {48_000})))
    channels = next(iter(sorted(advertised.channel_counts or {1})))
    return AudioStreamDescriptor(
        stream_id=AudioStreamId.create(),
        endpoint=endpoint,
        format=fmt,
        sample_rate_hz=rate,
        channel_count=channels,
        buffer=AudioBufferPolicy(max_frames=max_frames, max_bytes=4 * 1024),
    )


def _preferred_format(formats: frozenset[AudioFormat]) -> AudioFormat:
    """Probe with raw PCM when possible, so geometry checks actually apply."""

    for candidate in _FORMAT_PROBE_ORDER:
        if candidate in formats:
            return candidate
    return next(iter(sorted(formats, key=lambda item: item.value)))


def _open(
    provider: AudioProvider,
    descriptor: AudioStreamDescriptor,
    findings: list[str],
    *,
    role: str,
) -> AudioCaptureStream | AudioPlaybackStream | None:
    result = provider.open(descriptor)
    if not _record(findings, result.is_success, f"{role}: open() failed ({_code(result)})"):
        return None
    handle = result.unwrap()
    _record(
        findings,
        isinstance(handle, AudioCaptureStream if role == "capture" else AudioPlaybackStream),
        f"{role}: handle does not satisfy its stream protocol",
    )
    _record(
        findings,
        handle.descriptor == descriptor,
        f"{role}: handle contradicts the descriptor it was opened with",
    )
    _record(
        findings,
        handle.status.state is AudioStreamState.OPEN,
        f"{role}: a freshly opened stream must be OPEN, got {handle.status.state.value}",
    )
    return handle


def _open_stream(
    provider: AudioProvider,
    endpoint: AudioEndpoint,
    findings: list[str],
    *,
    role: str,
) -> AudioCaptureStream | AudioPlaybackStream | None:
    return _open(provider, _descriptor_for(endpoint), findings, role=role)


def _capture_findings(
    provider: AudioProvider,
    stream: AudioCaptureStream,
    findings: list[str],
    *,
    frame_limit: int,
) -> None:
    descriptor = stream.descriptor
    frames: list[AudioFrame] = []
    exhausted = False
    for _ in range(frame_limit):
        reported: object = stream.read()
        if not isinstance(reported, Result):
            findings.append("capture: read() must return a Result")
            break
        result: Result[AudioFrame, AgentXError] = reported
        if result.is_failure:
            _capture_error_findings(result.unwrap_error(), frames, findings)
            exhausted = True
            break
        frames.append(result.unwrap())

    _record(findings, bool(frames), "capture: an OPEN stream produced no frame")
    _record(
        findings,
        stream.status.state is AudioStreamState.OPEN,
        f"capture: status must be OPEN while transferring, got {stream.status.state.value}",
    )
    _frame_findings(frames, descriptor, findings)
    _terminal_read_findings(stream, findings)
    _lifecycle_findings(provider, descriptor, findings, role="capture")
    _cancellation_findings(provider, descriptor, findings, role="capture")
    _record(
        findings,
        exhausted or len(frames) < frame_limit,
        "capture: an exhausted stream must stop producing frames and say why",
    )


def _capture_error_findings(
    error: AgentXError,
    frames: list[AudioFrame],
    findings: list[str],
) -> None:
    _record(
        findings,
        error.code.startswith("audio."),
        "capture: read failure must be an audio.* code",
    )
    _record(findings, not _leaks_payload(error, frames), "capture: error text leaked payload bytes")


def _frame_findings(
    frames: list[AudioFrame],
    descriptor: AudioStreamDescriptor,
    findings: list[str],
) -> None:
    align = block_align_bytes(descriptor.format, descriptor.channel_count)
    pcm = is_pcm_format(descriptor.format)
    expected = descriptor.sequence_origin
    previous_timestamp: datetime | None = None
    for index, frame in enumerate(frames):
        where = f"capture: frame {index}"
        _record(
            findings,
            frame.stream_id == descriptor.stream_id,
            f"{where}: frame carries a foreign stream id",
        )
        _record(findings, frame.format is descriptor.format, f"{where}: format contradicts open")
        _record(
            findings,
            frame.sample_rate_hz == descriptor.sample_rate_hz,
            f"{where}: sample rate contradicts open",
        )
        _record(
            findings,
            frame.channel_count == descriptor.channel_count,
            f"{where}: channel count contradicts open",
        )
        _record(
            findings,
            0 < frame.byte_count <= MAX_AUDIO_PAYLOAD_BYTES,
            f"{where}: payload is not bounded",
        )
        _record(
            findings,
            frame.byte_count <= descriptor.endpoint.support.max_payload_bytes,
            f"{where}: payload exceeds the advertised endpoint bound",
        )
        if align is not None:
            _record(findings, frame.byte_count % align == 0, f"{where}: misaligned PCM payload")
            if pcm:
                _record(
                    findings,
                    frame.sample_frames == frame.byte_count // align,
                    f"{where}: sample count disagrees with payload length",
                )
        sequence = check_sequence(expected=expected, observed=frame.sequence)
        _record(
            findings,
            sequence.is_valid,
            f"{where}: sequence {frame.sequence} is not {expected} "
            f"({', '.join(sorted(issue.value for issue in sequence.issues))})",
        )
        if sequence.next_expected is not None:
            expected = sequence.next_expected
        timing = check_timestamp(
            observed=frame.timestamp,
            previous=previous_timestamp,
            max_gap=DEFAULT_MAX_TIMESTAMP_GAP,
        )
        _record(
            findings,
            timing.is_valid,
            f"{where}: timestamp order violated "
            f"({', '.join(sorted(issue.value for issue in timing.issues))})",
        )
        previous_timestamp = frame.timestamp
        duration = frame.duration
        _record(
            findings,
            duration is None or timedelta(0) < duration <= timedelta(seconds=10),
            f"{where}: derived duration is not a plausible bounded span",
        )


# --------------------------------------------------------------------------
# Terminal behaviour, lifecycle, cancellation.
# --------------------------------------------------------------------------


def _terminal_read_findings(stream: AudioCaptureStream, findings: list[str]) -> None:
    if not is_terminal_audio_state(stream.status.state):
        return
    result = stream.read()
    _record(
        findings,
        result.is_failure,
        "capture: a terminal stream must refuse read() instead of producing frames",
    )


def _fresh_stream(
    provider: AudioProvider,
    descriptor: AudioStreamDescriptor,
    findings: list[str],
    *,
    role: str,
) -> AudioCaptureStream | AudioPlaybackStream | None:
    return _open(provider, descriptor, findings, role=role)


def _lifecycle_findings(
    provider: AudioProvider,
    descriptor: AudioStreamDescriptor,
    findings: list[str],
    *,
    role: str,
) -> None:
    stream = _fresh_stream(provider, descriptor, findings, role=role)
    if stream is None:
        return
    closed = stream.close(reason="conformance")
    if _record(findings, closed.is_success, f"{role}: close() must succeed once ({_code(closed)})"):
        status = closed.unwrap()
        _record(
            findings,
            isinstance(status, AudioStreamStatus) and status.state is AudioStreamState.CLOSED,
            f"{role}: a closed stream must report CLOSED",
        )
    _record(
        findings,
        is_terminal_audio_state(stream.status.state),
        f"{role}: status must stay terminal after close, got {stream.status.state.value}",
    )
    again = stream.close(reason="conformance")
    _record(
        findings,
        again.is_failure,
        f"{role}: closing an already terminal stream must be refused, not acknowledged",
    )


def _drain_findings(
    provider: AudioProvider,
    descriptor: AudioStreamDescriptor,
    findings: list[str],
) -> None:
    """A sink must acknowledge the transition to DRAINING and refuse to repeat it."""

    stream = _fresh_stream(provider, descriptor, findings, role="playback")
    if not isinstance(stream, AudioPlaybackStream):
        return
    drained = stream.drain()
    if _record(findings, drained.is_success, f"playback: drain() must succeed ({_code(drained)})"):
        status = drained.unwrap()
        _record(
            findings,
            isinstance(status, AudioStreamStatus) and status.state is AudioStreamState.DRAINING,
            "playback: drain() must report DRAINING",
        )
    _record(
        findings,
        stream.drain().is_failure,
        "playback: draining an already draining stream must be refused",
    )


def _cancellation_findings(
    provider: AudioProvider,
    descriptor: AudioStreamDescriptor,
    findings: list[str],
    *,
    role: str,
) -> None:
    stream = _fresh_stream(provider, descriptor, findings, role=role)
    if stream is None:
        return
    cancelled = stream.cancel(reason="conformance")
    if _record(
        findings, cancelled.is_success, f"{role}: cancel() must succeed ({_code(cancelled)})"
    ):
        _record(
            findings,
            cancelled.unwrap().state is AudioStreamState.CANCELLED,
            f"{role}: a cancelled stream must report CANCELLED",
        )
    _record(
        findings,
        stream.cancellation_token.is_cancelled,
        f"{role}: cancel() was reported but the cancellation token says otherwise",
    )
    _record(
        findings,
        stream.status.state is AudioStreamState.CANCELLED,
        f"{role}: status must follow the cancellation, got {stream.status.state.value}",
    )
    after = (
        stream.read()
        if isinstance(stream, AudioCaptureStream)
        else stream.write(_scratch_frame(descriptor, sequence=0))
    )
    _record(
        findings,
        after.is_failure,
        f"{role}: a cancelled stream must refuse work instead of continuing",
    )


# --------------------------------------------------------------------------
# Backpressure.
# --------------------------------------------------------------------------


def _backpressure_findings(
    stream: AudioPlaybackStream,
    descriptor: AudioStreamDescriptor,
    findings: list[str],
) -> None:
    policy = descriptor.buffer
    accepted = 0
    refused = 0
    accepted_bytes = 0
    for index in range(policy.max_frames + 3):
        frame = _scratch_frame(descriptor, sequence=index)
        reported_write: object = stream.write(frame)
        if not isinstance(reported_write, Result):
            findings.append("playback: write() must return a Result")
            return
        result: Result[AudioAdmissionDecision, AgentXError] = reported_write
        if result.is_failure:
            refused += 1
            _record(
                findings,
                not _leaks_payload(result.unwrap_error(), [frame]),
                "playback: error text leaked payload bytes",
            )
            continue
        decision = result.unwrap()
        if not _record(
            findings,
            isinstance(decision, AudioAdmissionDecision),
            "playback: write() must report an AudioAdmissionDecision",
        ):
            continue
        _record(
            findings,
            decision.policy.max_frames == policy.max_frames,
            "playback: the sink widened the declared frame bound",
        )
        _record(
            findings,
            decision.policy.max_bytes == policy.max_bytes,
            "playback: the sink widened the declared byte bound",
        )
        if decision.is_accepted:
            accepted += 1
            accepted_bytes += frame.byte_count
        else:
            refused += 1

    _record(
        findings,
        accepted <= policy.max_frames,
        f"playback: accepted {accepted} frames into a buffer bounded at {policy.max_frames}",
    )
    _record(
        findings,
        accepted_bytes <= policy.max_bytes,
        f"playback: accepted {accepted_bytes} bytes into a buffer bounded at {policy.max_bytes}",
    )
    _record(
        findings,
        refused > 0,
        "playback: an over-capacity write must be refused or evict, never silently absorbed",
    )
    drained = stream.drain()
    _record(findings, drained.is_success, f"playback: drain() must succeed ({_code(drained)})")
    late = stream.write(_scratch_frame(descriptor, sequence=policy.max_frames + 4))
    _record(
        findings,
        late.is_failure,
        "playback: a draining stream must refuse further frames",
    )


# --------------------------------------------------------------------------
# Small shared helpers.
# --------------------------------------------------------------------------


def _scratch_frame(
    descriptor: AudioStreamDescriptor,
    *,
    sequence: int,
) -> AudioFrame:
    """One frame that matches ``descriptor`` exactly, on the scaffold's clock."""

    return AudioFrame(
        stream_id=descriptor.stream_id,
        format=descriptor.format,
        sample_rate_hz=descriptor.sample_rate_hz,
        channel_count=descriptor.channel_count,
        sequence=descriptor.sequence_origin + sequence,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC) + CONFORMANCE_FRAME_INTERVAL * sequence,
        payload=payload_for(
            sequence,
            fmt=descriptor.format,
            channel_count=descriptor.channel_count,
        ),
    )


def _leaks_payload(error: AgentXError, frames: list[AudioFrame]) -> bool:
    """Whether ``error`` kept audio data out of every text field it carries."""

    text = f"{error.code} {error.message} {dict(error.details)}"
    return any(frame.payload.decode("latin-1") in text for frame in frames)


def _code(result: Result[Any, AgentXError]) -> str:
    if result.is_success:
        return "unexpected success"
    return result.unwrap_error().code
