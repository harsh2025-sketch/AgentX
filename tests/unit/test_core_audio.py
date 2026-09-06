"""A7.01 provider-neutral audio contract tests.

Covers the contract surface future real-time voice work depends on:
format/encoding, sample-rate and channel bounds, frame/chunk payloads, sequence
and timestamp validation, stream lifecycle, cancellation, bounded buffering and
backpressure, failure classification, and serialization metadata.
"""

from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from agentx.core.audio import (
    DEFAULT_MAX_TIMESTAMP_GAP,
    LEGAL_AUDIO_STREAM_TRANSITIONS,
    MAX_AUDIO_BUFFER_BYTES,
    MAX_AUDIO_BUFFER_FRAMES,
    MAX_AUDIO_LATENCY_TARGET_MS,
    MAX_AUDIO_PAYLOAD_BYTES,
    MAX_AUDIO_SAMPLE_FRAMES,
    MAX_AUDIO_SEQUENCE,
    MAX_CHANNEL_COUNT,
    MAX_SAMPLE_RATE_HZ,
    MAX_TIMESTAMP_GAP,
    MIN_AUDIO_BUFFER_FRAMES,
    MIN_CHANNEL_COUNT,
    MIN_SAMPLE_RATE_HZ,
    TERMINAL_AUDIO_STREAM_STATES,
    AudioAdmissionDecision,
    AudioAdmissionOutcome,
    AudioBufferPolicy,
    AudioCaptureStream,
    AudioEndpoint,
    AudioEndpointId,
    AudioEndpointKind,
    AudioFailureKind,
    AudioFormat,
    AudioFormatSupport,
    AudioFrame,
    AudioOverflowPolicy,
    AudioPlaybackStream,
    AudioProvider,
    AudioProviderId,
    AudioSequenceCheck,
    AudioSequenceIssue,
    AudioStreamDescriptor,
    AudioStreamState,
    AudioStreamStatus,
    AudioTimestampIssue,
    AudioValidationError,
    admission_failure,
    audio_failure,
    audio_transition_error,
    block_align_bytes,
    can_transition_audio_state,
    check_sequence,
    check_timestamp,
    is_pcm_format,
    is_supported_channel_count,
    is_supported_sample_rate,
    is_terminal_audio_state,
    legal_audio_transitions,
    payload_digest,
    sample_width_bytes,
    try_transition_audio_state,
    validate_audio_state_transition,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import CancellationSource
from agentx.core.ids import ArtifactId, AudioStreamId
from agentx.core.result import Result
from tests.support.audio_provider import (
    FakeAudioProvider,
)
from tests.support.audio_provider import (
    make_descriptor as fake_descriptor,
)
from tests.support.audio_provider import (
    make_sink_descriptor as fake_sink_descriptor,
)

BASE_TIME = datetime(2026, 5, 4, 6, 30, 15, 123456, tzinfo=UTC)


def support(
    *,
    formats: frozenset[AudioFormat] | None = None,
    sample_rates_hz: frozenset[int] | None = None,
    channel_counts: frozenset[int] | None = None,
    max_payload_bytes: int = 4 * 1024,
) -> AudioFormatSupport:
    """An advertisement that is broad enough for the happy-path fixtures."""

    return AudioFormatSupport(
        formats=(
            frozenset({AudioFormat.PCM_S16LE, AudioFormat.PCM_F32LE, AudioFormat.OPUS})
            if formats is None
            else formats
        ),
        sample_rates_hz=frozenset({16_000, 48_000}) if sample_rates_hz is None else sample_rates_hz,
        channel_counts=frozenset({1, 2}) if channel_counts is None else channel_counts,
        max_payload_bytes=max_payload_bytes,
    )


def endpoint(**overrides: Any) -> AudioEndpoint:
    values: dict[str, Any] = {
        "endpoint_id": AudioEndpointId(provider_id=AudioProviderId("test.audio"), value="mic-0"),
        "kind": AudioEndpointKind.SOURCE,
        "label": "Test device",
        "support": support(),
    }
    values.update(overrides)
    return AudioEndpoint(**values)


def descriptor(**overrides: Any) -> AudioStreamDescriptor:
    values: dict[str, Any] = {
        "stream_id": AudioStreamId.create(),
        "endpoint": endpoint(),
        "format": AudioFormat.PCM_S16LE,
        "sample_rate_hz": 48_000,
        "channel_count": 1,
        "buffer": AudioBufferPolicy(max_frames=4, max_bytes=4 * 1024),
        "sequence_origin": 0,
        "latency_target_ms": 20.0,
    }
    values.update(overrides)
    return AudioStreamDescriptor(**values)


def frame(**overrides: Any) -> AudioFrame:
    values: dict[str, Any] = {
        "stream_id": AudioStreamId.create(),
        "format": AudioFormat.PCM_S16LE,
        "sample_rate_hz": 48_000,
        "channel_count": 1,
        "sequence": 0,
        "timestamp": BASE_TIME,
        "payload": b"\x01\x02\x03\x04",
    }
    values.update(overrides)
    return AudioFrame(**values)


# --------------------------------------------------------------------------
# Format and encoding.
# --------------------------------------------------------------------------


def test_format_vocabulary_is_closed_and_names_endianness_explicitly() -> None:
    assert {item.value for item in AudioFormat} == {"pcm_s16le", "pcm_f32le", "opus", "flac"}
    # A vendor name cannot be smuggled in through the vocabulary.
    for rejected in ("whisper", "whisper-1", "windows_speech_pcm", "openai-opus"):
        with pytest.raises(ValueError, match="not a valid AudioFormat"):
            AudioFormat(rejected)
    for fmt in AudioFormat:
        if fmt.name.startswith("PCM_"):
            assert fmt.value.endswith("le")


def test_pcm_and_container_geometry_helpers() -> None:
    assert is_pcm_format(AudioFormat.PCM_S16LE) is True
    assert is_pcm_format(AudioFormat.PCM_F32LE) is True
    assert is_pcm_format(AudioFormat.OPUS) is False
    assert sample_width_bytes(AudioFormat.PCM_S16LE) == 2
    assert sample_width_bytes(AudioFormat.PCM_F32LE) == 4
    assert sample_width_bytes(AudioFormat.FLAC) is None
    assert block_align_bytes(AudioFormat.PCM_S16LE, 2) == 4
    assert block_align_bytes(AudioFormat.PCM_F32LE, 1) == 4
    assert block_align_bytes(AudioFormat.OPUS, 2) is None
    with pytest.raises(TypeError, match="AudioFormat"):
        block_align_bytes("pcm_s16le", 1)  # type: ignore[arg-type]
    with pytest.raises(AudioValidationError, match="channel_count must not exceed"):
        block_align_bytes(AudioFormat.PCM_S16LE, MAX_CHANNEL_COUNT + 1)


def test_supported_ranges_are_inclusive_and_typed() -> None:
    assert is_supported_sample_rate(MIN_SAMPLE_RATE_HZ) is True
    assert is_supported_sample_rate(MAX_SAMPLE_RATE_HZ) is True
    assert is_supported_sample_rate(MIN_SAMPLE_RATE_HZ - 1) is False
    assert is_supported_sample_rate(MAX_SAMPLE_RATE_HZ + 1) is False
    assert is_supported_sample_rate(True) is False
    assert is_supported_sample_rate("48000") is False
    assert is_supported_sample_rate(48_000.0) is False
    assert is_supported_sample_rate(None) is False
    assert is_supported_channel_count(MIN_CHANNEL_COUNT) is True
    assert is_supported_channel_count(MAX_CHANNEL_COUNT) is True
    assert is_supported_channel_count(MAX_CHANNEL_COUNT + 1) is False
    assert is_supported_channel_count(0) is False
    assert is_supported_channel_count(1.0) is False


def test_format_support_advertisement_semantics() -> None:
    narrow = support(
        formats=frozenset({AudioFormat.PCM_S16LE}),
        sample_rates_hz=frozenset({16_000}),
        channel_counts=frozenset({1}),
    )
    assert narrow.supports(fmt=AudioFormat.PCM_S16LE, sample_rate_hz=16_000, channel_count=1)
    assert not narrow.supports(fmt=AudioFormat.PCM_S16LE, sample_rate_hz=48_000, channel_count=1)
    assert not narrow.supports(fmt=AudioFormat.OPUS, sample_rate_hz=16_000, channel_count=1)
    assert not narrow.supports(fmt=AudioFormat.PCM_S16LE, sample_rate_hz=16_000, channel_count=2)

    # An empty set means "the whole supported window", not "nothing is allowed".
    wide = support(sample_rates_hz=frozenset(), channel_counts=frozenset())
    assert wide.supports(fmt=AudioFormat.PCM_S16LE, sample_rate_hz=44_100, channel_count=2)
    assert not wide.supports(fmt=AudioFormat.PCM_S16LE, sample_rate_hz=4_000, channel_count=2)
    assert wide.admits(wide.max_payload_bytes)
    assert not wide.admits(wide.max_payload_bytes + 1)
    assert not wide.admits(0)

    with pytest.raises(AudioValidationError, match="formats must not be empty"):
        AudioFormatSupport(
            formats=frozenset(),
            sample_rates_hz=frozenset(),
            channel_counts=frozenset(),
            max_payload_bytes=1024,
        )
    with pytest.raises(AudioValidationError, match="sample_rates_hz must be at least"):
        AudioFormatSupport(
            formats=frozenset({AudioFormat.PCM_S16LE}),
            sample_rates_hz=frozenset({7_999}),
            channel_counts=frozenset(),
            max_payload_bytes=1024,
        )
    with pytest.raises(AudioValidationError, match="max_payload_bytes must not exceed"):
        AudioFormatSupport(
            formats=frozenset({AudioFormat.PCM_S16LE}),
            sample_rates_hz=frozenset(),
            channel_counts=frozenset(),
            max_payload_bytes=MAX_AUDIO_PAYLOAD_BYTES + 1,
        )


# --------------------------------------------------------------------------
# Identity.
# --------------------------------------------------------------------------


def test_provider_identity_is_a_bounded_machine_identifier() -> None:
    assert str(AudioProviderId("local.runtime")) == "local.runtime"
    for bad in ("", " padded", "has space", "line\nbreak", "\ttab", "x" * 129):
        with pytest.raises(AudioValidationError, match="provider_id"):
            AudioProviderId(bad)
    with pytest.raises(TypeError, match="provider_id"):
        AudioProviderId(7)  # type: ignore[arg-type]


def test_endpoint_identity_is_provider_qualified() -> None:
    first = AudioEndpointId(provider_id=AudioProviderId("provider.a"), value="default")
    second = AudioEndpointId(provider_id=AudioProviderId("provider.b"), value="default")
    assert str(first) == "provider.a/default"
    assert first != second
    assert hash(first) != hash(second)
    assert first < AudioEndpointId(provider_id=AudioProviderId("provider.a"), value="default2")
    with pytest.raises(AudioValidationError, match="endpoint_id"):
        AudioEndpointId(provider_id=AudioProviderId("provider.a"), value="")
    with pytest.raises(TypeError, match="provider_id"):
        AudioEndpointId(provider_id="provider.a", value="x")  # type: ignore[arg-type]


def test_stream_identity_is_agentx_owned_and_domain_tagged() -> None:
    stream_id = AudioStreamId.create()
    assert AudioStreamId.parse(stream_id.to_str()) == stream_id
    assert stream_id != ArtifactId.parse(stream_id.to_str())
    assert stream_id.domain == "audio_stream"
    with pytest.raises(ValueError, match="nil UUID"):
        AudioStreamId.parse("00000000-0000-0000-0000-000000000000")


# --------------------------------------------------------------------------
# Chunk / frame.
# --------------------------------------------------------------------------


def test_frame_payload_must_be_non_empty_bytes() -> None:
    with pytest.raises(AudioValidationError, match="payload must not be empty"):
        frame(payload=b"")
    with pytest.raises(TypeError, match="payload must be bytes"):
        frame(payload=bytearray(b"\x01\x02\x03\x04"))
    with pytest.raises(TypeError, match="payload must be bytes"):
        frame(payload="\x01\x02\x03\x04")


def test_oversized_frame_is_rejected_at_construction() -> None:
    with pytest.raises(AudioValidationError, match="oversized frame"):
        frame(payload=b"\x00" * (MAX_AUDIO_PAYLOAD_BYTES + 1))
    # The bound is inclusive: exactly the bound is admissible.
    assert frame(payload=b"\x00" * MAX_AUDIO_PAYLOAD_BYTES).byte_count == MAX_AUDIO_PAYLOAD_BYTES
    with pytest.raises(AudioValidationError, match="oversized frame"):
        payload_digest(b"\x00" * (MAX_AUDIO_PAYLOAD_BYTES + 1))
    with pytest.raises(AudioValidationError, match="payload must not be empty"):
        payload_digest(b"")


def test_pcm_payload_must_be_block_aligned() -> None:
    with pytest.raises(AudioValidationError, match="block alignment"):
        frame(payload=b"\x01\x02\x03")
    aligned = frame(payload=b"\x01\x02\x03\x04", channel_count=2, sample_rate_hz=16_000)
    assert aligned.sample_frames == 1
    assert aligned.duration == timedelta(microseconds=1_000_000 // 16_000)


def test_declared_sample_frames_must_agree_with_the_pcm_payload() -> None:
    assert frame(payload=b"\x00" * 8, sample_frames=4).sample_frames == 4
    with pytest.raises(AudioValidationError, match="disagrees with the payload frame count"):
        frame(payload=b"\x00" * 8, sample_frames=3)


def test_compressed_payloads_may_declare_frames_but_are_never_decoded() -> None:
    opaque = bytes(range(256)) * 3
    compressed = frame(format=AudioFormat.OPUS, payload=opaque)
    assert compressed.sample_frames is None
    assert compressed.duration is None
    assert compressed.byte_count == len(opaque)
    declared = frame(format=AudioFormat.OPUS, payload=opaque, sample_frames=960)
    assert declared.duration == timedelta(microseconds=(960 * 1_000_000) // 48_000)
    with pytest.raises(AudioValidationError, match="sample_frames must not exceed"):
        frame(format=AudioFormat.OPUS, payload=opaque, sample_frames=MAX_AUDIO_SAMPLE_FRAMES + 1)


def test_frame_rejects_invalid_sample_rate_and_channel_count() -> None:
    for bad_rate in (7_999, MAX_SAMPLE_RATE_HZ + 1, 0, -48_000):
        with pytest.raises(AudioValidationError, match="sample_rate_hz"):
            frame(sample_rate_hz=bad_rate)
    for bad in (True, "48000", 48_000.0):
        with pytest.raises(TypeError, match="sample_rate_hz"):
            frame(sample_rate_hz=bad)
    for bad_channels in (0, MAX_CHANNEL_COUNT + 1):
        with pytest.raises(AudioValidationError, match="channel_count"):
            frame(channel_count=bad_channels)
    for bad in (True, 2.0):
        with pytest.raises(TypeError, match="channel_count"):
            frame(channel_count=bad)


def test_frames_are_immutable_values() -> None:
    sample = frame()
    with pytest.raises(FrozenInstanceError):
        sample.sequence = 1  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        sample.payload = b"\x09\x09\x09\x09"  # type: ignore[misc]
    assert isinstance(hash(sample), int)


def test_digest_identifies_payload_without_exposing_it() -> None:
    sample = frame(payload=b"audio-bytes-are-data" * 8)
    assert sample.payload_digest == payload_digest(sample.payload)
    assert len(sample.payload_digest) == 64
    assert sample.payload_digest == sample.payload_digest
    assert "audio-bytes-are-data" not in sample.metadata()["payload_sha256"]


# --------------------------------------------------------------------------
# Ordering.
# --------------------------------------------------------------------------


def test_sequence_bounds_are_enforced_and_never_wrap() -> None:
    assert frame(sequence=MAX_AUDIO_SEQUENCE).sequence == MAX_AUDIO_SEQUENCE
    with pytest.raises(AudioValidationError, match="sequence must not exceed"):
        frame(sequence=MAX_AUDIO_SEQUENCE + 1)
    with pytest.raises(AudioValidationError, match="sequence must be at least"):
        frame(sequence=-1)
    exhausted = check_sequence(expected=MAX_AUDIO_SEQUENCE, observed=MAX_AUDIO_SEQUENCE)
    assert exhausted.is_valid
    assert exhausted.next_expected is None


def test_sequence_check_kinds() -> None:
    valid = check_sequence(expected=7, observed=7)
    assert valid.is_valid and valid.issues == frozenset() and valid.next_expected == 8
    duplicate = check_sequence(expected=7, observed=6)
    assert duplicate.issues == frozenset({AudioSequenceIssue.DUPLICATE})
    gap = check_sequence(expected=7, observed=10)
    assert gap.issues == frozenset({AudioSequenceIssue.GAP})
    assert gap.skip == 3
    regression = check_sequence(expected=7, observed=2)
    assert regression.issues == frozenset({AudioSequenceIssue.REGRESSION})
    assert regression.skip == 0
    with pytest.raises(AudioValidationError, match="observed must be at least"):
        check_sequence(expected=0, observed=-1)
    with pytest.raises(AudioValidationError, match="expected must not exceed"):
        check_sequence(expected=MAX_AUDIO_SEQUENCE + 1, observed=0)


def test_sequence_check_walks_a_whole_stream_without_gaps() -> None:
    stream_descriptor = descriptor(sequence_origin=3)
    expected = stream_descriptor.sequence_origin
    for index in range(6):
        check = AudioSequenceCheck(
            expected=expected,
            observed=stream_descriptor.sequence_origin + index,
            issues=frozenset(),
        )
        assert check.is_valid
        expected = check.next_expected or expected + 1
    assert expected == stream_descriptor.sequence_origin + 6


# --------------------------------------------------------------------------
# Timestamps.
# --------------------------------------------------------------------------


def test_timestamps_must_be_aware_and_are_normalized_to_utc() -> None:
    with pytest.raises(AudioValidationError, match="timezone-aware"):
        frame(timestamp=datetime(2026, 5, 4, 6, 30, 15))
    stream_id = AudioStreamId.create()
    offset = timezone(timedelta(hours=5, minutes=30))
    shifted = frame(
        stream_id=stream_id, timestamp=datetime(2026, 5, 4, 12, 0, 15, 123456, tzinfo=offset)
    )
    assert shifted.timestamp.utcoffset() == timedelta(0)
    assert shifted.timestamp == BASE_TIME
    assert shifted == frame(stream_id=stream_id, timestamp=BASE_TIME)
    with pytest.raises(AudioValidationError, match="epoch range"):
        frame(timestamp=datetime(1969, 12, 31, tzinfo=UTC))
    with pytest.raises(TypeError, match="timestamp"):
        frame(timestamp=BASE_TIME.timestamp())


def test_timestamp_order_and_gap_checks() -> None:
    first = check_timestamp(observed=BASE_TIME)
    assert first.is_valid and first.previous is None and first.delta is None

    in_order = check_timestamp(observed=BASE_TIME + timedelta(milliseconds=20), previous=BASE_TIME)
    assert in_order.is_valid and in_order.delta == timedelta(milliseconds=20)

    equal = check_timestamp(observed=BASE_TIME, previous=BASE_TIME)
    assert equal.is_valid, "a coarse provider clock may legitimately repeat a reading"

    regressed = check_timestamp(observed=BASE_TIME - timedelta(microseconds=1), previous=BASE_TIME)
    assert regressed.issues == frozenset({AudioTimestampIssue.NON_MONOTONIC})

    stalled = check_timestamp(
        observed=BASE_TIME + DEFAULT_MAX_TIMESTAMP_GAP + timedelta(microseconds=1),
        previous=BASE_TIME,
    )
    assert stalled.issues == frozenset({AudioTimestampIssue.GAP_EXCEEDED})
    assert not stalled.is_valid

    with pytest.raises(AudioValidationError, match="max_gap must not exceed"):
        check_timestamp(observed=BASE_TIME, max_gap=MAX_TIMESTAMP_GAP + timedelta(seconds=1))
    with pytest.raises(AudioValidationError, match="max_gap must not be negative"):
        check_timestamp(observed=BASE_TIME, max_gap=timedelta(seconds=-1))


# --------------------------------------------------------------------------
# Stream configuration.
# --------------------------------------------------------------------------


def test_descriptor_requires_the_endpoint_to_advertise_the_request() -> None:
    narrow = endpoint(
        support=support(
            formats=frozenset({AudioFormat.PCM_S16LE}),
            sample_rates_hz=frozenset({16_000}),
            channel_counts=frozenset({1}),
            max_payload_bytes=2 * 1024,
        )
    )
    ok = descriptor(
        endpoint=narrow,
        sample_rate_hz=16_000,
        channel_count=1,
        buffer=AudioBufferPolicy(max_frames=4, max_bytes=2 * 1024),
    )
    assert ok.kind is AudioEndpointKind.SOURCE
    assert ok.provider_id == AudioProviderId("test.audio")
    assert ok.block_align == 2
    assert ok.admits_payload(2 * 1024)
    assert not ok.admits_payload(2 * 1024 + 1)
    assert not ok.admits_payload(0)
    assert not ok.admits_payload(-8)

    with pytest.raises(AudioValidationError, match="not advertised by the endpoint"):
        descriptor(endpoint=narrow, sample_rate_hz=48_000)
    with pytest.raises(AudioValidationError, match="not advertised by the endpoint"):
        descriptor(endpoint=narrow, channel_count=2)
    with pytest.raises(AudioValidationError, match="not advertised by the endpoint"):
        descriptor(endpoint=narrow, format=AudioFormat.OPUS, sample_rate_hz=16_000)


def test_descriptor_buffer_cannot_exceed_the_endpoint_bound() -> None:
    narrow = endpoint(
        support=support(
            max_payload_bytes=1024,
            sample_rates_hz=frozenset(),
            channel_counts=frozenset(),
        )
    )
    with pytest.raises(AudioValidationError, match="exceeds the endpoint payload bound"):
        descriptor(
            endpoint=narrow,
            sample_rate_hz=8_000,
            channel_count=1,
            buffer=AudioBufferPolicy(max_bytes=2048),
        )


def test_descriptor_latency_target_must_be_finite_and_in_range() -> None:
    assert descriptor(latency_target_ms=None).latency_target_ms is None
    assert descriptor(latency_target_ms=20).latency_target_ms == 20.0
    for bad in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(AudioValidationError, match="latency_target_ms must be finite"):
            descriptor(latency_target_ms=bad)
    for bad in (0.1, MAX_AUDIO_LATENCY_TARGET_MS + 0.1, -20.0):
        with pytest.raises(AudioValidationError, match="latency_target_ms must be between"):
            descriptor(latency_target_ms=bad)
    with pytest.raises(TypeError, match="latency_target_ms"):
        descriptor(latency_target_ms="20")


def test_descriptor_sequence_origin_is_bounded() -> None:
    assert descriptor(sequence_origin=7).sequence_origin == 7
    with pytest.raises(AudioValidationError, match="sequence_origin must not exceed"):
        descriptor(sequence_origin=MAX_AUDIO_SEQUENCE + 1)
    with pytest.raises(AudioValidationError, match="sequence_origin must be at least"):
        descriptor(sequence_origin=-1)


def test_descriptor_rejects_wrong_types() -> None:
    with pytest.raises(TypeError, match="stream_id"):
        descriptor(stream_id="not-an-id")
    with pytest.raises(TypeError, match="endpoint"):
        descriptor(endpoint="mic-0")
    with pytest.raises(TypeError, match="buffer"):
        descriptor(buffer=4)
    with pytest.raises(TypeError, match="format"):
        descriptor(format="pcm_s16le")


def test_endpoint_description_rules() -> None:
    with pytest.raises(AudioValidationError, match="label must be non-empty"):
        endpoint(label="")
    with pytest.raises(AudioValidationError, match="label must not exceed"):
        endpoint(label="x" * 257)
    with pytest.raises(AudioValidationError, match="label must not contain control characters"):
        endpoint(label="mic\x000")
    with pytest.raises(TypeError, match="kind"):
        endpoint(kind="source")
    with pytest.raises(TypeError, match="support"):
        endpoint(support=None)
    with pytest.raises(TypeError, match="is_default must be a bool"):
        endpoint(is_default=1)


# --------------------------------------------------------------------------
# Bounded buffering and backpressure.
# --------------------------------------------------------------------------


def test_buffer_policy_bounds() -> None:
    assert AudioBufferPolicy().overflow is AudioOverflowPolicy.REJECT
    assert AudioBufferPolicy(max_frames=MIN_AUDIO_BUFFER_FRAMES).max_frames == (
        MIN_AUDIO_BUFFER_FRAMES
    )
    with pytest.raises(AudioValidationError, match="max_frames must be at least"):
        AudioBufferPolicy(max_frames=0)
    with pytest.raises(AudioValidationError, match="max_frames must not exceed"):
        AudioBufferPolicy(max_frames=MAX_AUDIO_BUFFER_FRAMES + 1)
    with pytest.raises(AudioValidationError, match="max_bytes must be at least"):
        AudioBufferPolicy(max_bytes=0)
    with pytest.raises(AudioValidationError, match="max_bytes must not exceed"):
        AudioBufferPolicy(max_bytes=MAX_AUDIO_BUFFER_BYTES + 1)
    with pytest.raises(TypeError, match="overflow"):
        AudioBufferPolicy(overflow="reject")  # type: ignore[arg-type]


def test_backpressure_admission_outcomes() -> None:
    policy = AudioBufferPolicy(max_frames=2, max_bytes=100)
    accepted = policy.assess(buffered_frames=0, buffered_bytes=0, frame_bytes=50)
    assert isinstance(accepted, AudioAdmissionDecision)
    assert accepted.outcome is AudioAdmissionOutcome.ACCEPTED
    assert accepted.projected_frames == 1 and accepted.projected_bytes == 50

    full = policy.assess(buffered_frames=2, buffered_bytes=100, frame_bytes=50)
    assert full.outcome is AudioAdmissionOutcome.REJECTED_FULL
    assert not full.is_accepted and not full.requires_backpressure and not full.may_drop_audio
    assert (full.projected_frames, full.projected_bytes) == (2, 100)

    oversized = policy.assess(buffered_frames=0, buffered_bytes=0, frame_bytes=101)
    assert oversized.outcome is AudioAdmissionOutcome.REJECTED_OVERSIZED
    # Oversized wins over "full": no policy admits a frame larger than the buffer.
    both = policy.assess(buffered_frames=2, buffered_bytes=100, frame_bytes=101)
    assert both.outcome is AudioAdmissionOutcome.REJECTED_OVERSIZED

    bytewise = policy.assess(buffered_frames=1, buffered_bytes=80, frame_bytes=50)
    assert bytewise.outcome is AudioAdmissionOutcome.REJECTED_FULL


def test_backpressure_policies_that_keep_realtime_freshness() -> None:
    dropping = AudioBufferPolicy(
        max_frames=2,
        max_bytes=100,
        overflow=AudioOverflowPolicy.DROP_OLDEST,
    )
    decision = dropping.assess(buffered_frames=2, buffered_bytes=100, frame_bytes=50)
    assert decision.outcome is AudioAdmissionOutcome.EVICTED
    assert decision.is_accepted and decision.may_drop_audio
    assert decision.projected_frames == 2
    assert decision.projected_bytes == 100

    waiting = AudioBufferPolicy(
        max_frames=1,
        max_bytes=1_000,
        overflow=AudioOverflowPolicy.DEFER,
    )
    deferred = waiting.assess(buffered_frames=1, buffered_bytes=10, frame_bytes=10)
    assert deferred.outcome is AudioAdmissionOutcome.DEFERRED
    assert deferred.requires_backpressure and not deferred.is_accepted
    assert waiting.assess(buffered_frames=0, buffered_bytes=0, frame_bytes=10).outcome is (
        AudioAdmissionOutcome.ACCEPTED
    )


def test_admission_decision_consistency_rules() -> None:
    policy = AudioBufferPolicy(max_frames=2, max_bytes=100)
    with pytest.raises(AudioValidationError, match="REJECTED_OVERSIZED must correspond"):
        AudioAdmissionDecision(
            outcome=AudioAdmissionOutcome.REJECTED_OVERSIZED,
            policy=policy,
            buffered_frames=0,
            buffered_bytes=0,
            frame_bytes=10,
        )
    with pytest.raises(AudioValidationError, match="REJECTED_OVERSIZED must correspond"):
        AudioAdmissionDecision(
            outcome=AudioAdmissionOutcome.ACCEPTED,
            policy=policy,
            buffered_frames=0,
            buffered_bytes=0,
            frame_bytes=10_000,
        )
    with pytest.raises(AudioValidationError, match="buffered_frames must not exceed"):
        AudioAdmissionDecision(
            outcome=AudioAdmissionOutcome.ACCEPTED,
            policy=policy,
            buffered_frames=MAX_AUDIO_BUFFER_FRAMES + 1,
            buffered_bytes=0,
            frame_bytes=10,
        )


def test_admission_failure_mapping() -> None:
    policy = AudioBufferPolicy(max_frames=1, max_bytes=64)
    full = policy.assess(buffered_frames=1, buffered_bytes=64, frame_bytes=64)
    error = admission_failure(full)
    assert error.code == "audio.buffer_full"
    assert error.category is ErrorCategory.RESOURCE
    assert error.retryability is Retryability.RETRYABLE
    assert error.details["overflow_policy"] == "reject"
    assert "frame_bytes=64" in error.message

    oversized = policy.assess(buffered_frames=0, buffered_bytes=0, frame_bytes=65)
    assert admission_failure(oversized).code == "audio.oversized_payload"

    waiting = AudioBufferPolicy(max_frames=1, max_bytes=64, overflow=AudioOverflowPolicy.DEFER)
    deferred = waiting.assess(buffered_frames=1, buffered_bytes=64, frame_bytes=64)
    assert admission_failure(deferred).code == "audio.backpressure"

    accepted = waiting.assess(buffered_frames=0, buffered_bytes=0, frame_bytes=64)
    with pytest.raises(AudioValidationError, match="is not a refusal"):
        admission_failure(accepted)
    with pytest.raises(TypeError, match="decision"):
        admission_failure("nope")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Lifecycle.
# --------------------------------------------------------------------------


def test_lifecycle_matrix_is_exhaustive_and_has_no_self_transitions() -> None:
    assert set(LEGAL_AUDIO_STREAM_TRANSITIONS) == set(AudioStreamState)
    for state in AudioStreamState:
        assert state not in legal_audio_transitions(state)
        assert can_transition_audio_state(state, state) is False
        assert is_terminal_audio_state(state) is (not legal_audio_transitions(state))
    assert (
        frozenset({AudioStreamState.CLOSED, AudioStreamState.CANCELLED, AudioStreamState.FAILED})
        == TERMINAL_AUDIO_STREAM_STATES
    )
    assert legal_audio_transitions(AudioStreamState.NEW) == frozenset(
        {AudioStreamState.OPEN, AudioStreamState.CANCELLED, AudioStreamState.FAILED}
    )
    assert legal_audio_transitions(AudioStreamState.OPEN) == frozenset(
        {
            AudioStreamState.DRAINING,
            AudioStreamState.CLOSED,
            AudioStreamState.CANCELLED,
            AudioStreamState.FAILED,
        }
    )
    # A finished stream never resumes: restarting means a new stream identity.
    for terminal in TERMINAL_AUDIO_STREAM_STATES:
        for target in AudioStreamState:
            assert can_transition_audio_state(terminal, target) is False
    with pytest.raises(TypeError, match="state"):
        legal_audio_transitions("open")  # type: ignore[arg-type]


def test_legal_lifecycle_walks_succeed_and_derive_status() -> None:
    assert (
        validate_audio_state_transition(AudioStreamState.NEW, AudioStreamState.OPEN).state
        is AudioStreamState.OPEN
    )
    draining = validate_audio_state_transition(AudioStreamState.OPEN, AudioStreamState.DRAINING)
    assert draining.transfers_frames is False and draining.is_terminal is False
    result = try_transition_audio_state(AudioStreamState.OPEN, AudioStreamState.CANCELLED)
    assert result.is_success
    assert result.unwrap().state is AudioStreamState.CANCELLED
    assert result.unwrap().is_terminal is True


def test_illegal_lifecycle_transitions_are_refused() -> None:
    with pytest.raises(AudioValidationError, match="is not allowed"):
        validate_audio_state_transition(AudioStreamState.OPEN, AudioStreamState.NEW)
    failure = try_transition_audio_state(AudioStreamState.CLOSED, AudioStreamState.OPEN)
    assert failure.is_failure
    error = failure.unwrap_error()
    assert error.code == "audio.invalid_state_transition"
    assert error.category is ErrorCategory.CONFLICT
    assert error.retryability is Retryability.NON_RETRYABLE
    assert error.details["allowed_targets"] == []
    assert error.details["current_state"] == "closed"
    assert error.details["target_state"] == "open"
    assert audio_transition_error(AudioStreamState.NEW, AudioStreamState.DRAINING) == (
        audio_transition_error(AudioStreamState.NEW, AudioStreamState.DRAINING)
    )
    with pytest.raises(TypeError, match="target"):
        can_transition_audio_state(AudioStreamState.NEW, "open")  # type: ignore[arg-type]


def test_stream_status_reports_are_bounded_and_inert() -> None:
    status = AudioStreamStatus(state=AudioStreamState.OPEN, reason="device ready")
    assert status.transfers_frames is True and status.is_terminal is False
    for bad in ("", " padded", "x" * 257, "line\nbreak"):
        with pytest.raises(AudioValidationError, match="reason"):
            AudioStreamStatus(state=AudioStreamState.OPEN, reason=bad)
    with pytest.raises(TypeError, match="state"):
        AudioStreamStatus(state="open")  # type: ignore[arg-type]
    with pytest.raises(FrozenInstanceError):
        status.state = AudioStreamState.CLOSED  # type: ignore[misc]


# --------------------------------------------------------------------------
# Cancellation.
# --------------------------------------------------------------------------


def test_cancellation_is_observable_and_owns_the_only_stop_signal() -> None:
    """A7.01 reuses the canonical A1.07 token instead of inventing a second flag."""

    source = CancellationSource()
    token = source.token
    assert bool(token.is_cancelled) is False
    # The token is observation-only: no cancel/reset surface a consumer could use.
    assert getattr(token, "cancel", None) is None
    assert getattr(token, "reset", None) is None
    assert source.request_cancellation("user interrupted") is True
    assert source.request_cancellation("later reason is ignored") is False
    assert token.reason == "user interrupted"
    assert bool(token.is_cancelled) is True

    cancelled = try_transition_audio_state(AudioStreamState.OPEN, AudioStreamState.CANCELLED)
    assert cancelled.is_success
    reopened = try_transition_audio_state(AudioStreamState.CANCELLED, AudioStreamState.OPEN)
    assert reopened.is_failure
    assert reopened.unwrap_error().category is ErrorCategory.CONFLICT


def test_stream_protocols_expose_no_second_cancellation_surface() -> None:
    for name in ("abort", "stop_now", "force_close", "shutdown"):
        assert not hasattr(AudioCaptureStream, name)
        assert not hasattr(AudioPlaybackStream, name)
    assert hasattr(AudioCaptureStream, "cancel")
    assert hasattr(AudioPlaybackStream, "cancel")


def test_cancelled_failure_is_not_retryable() -> None:
    error = audio_failure(AudioFailureKind.CANCELLED, message="stream cancelled")
    assert error.code == "audio.cancelled"
    assert error.category is ErrorCategory.CANCELLED
    assert error.retryability is Retryability.NON_RETRYABLE


# --------------------------------------------------------------------------
# Failure taxonomy.
# --------------------------------------------------------------------------


def test_audio_failure_taxonomy_is_exhaustive_and_canonical() -> None:
    for kind in AudioFailureKind:
        error = audio_failure(kind, message="expected operational failure")
        assert isinstance(error, AgentXError)
        assert error.code == f"audio.{kind.value}"
        assert error.category in set(ErrorCategory)
        assert error.retryability in set(Retryability)
        assert error.details["audio_failure_kind"] == kind.value

    assert (
        audio_failure(AudioFailureKind.PROVIDER_UNAVAILABLE, message="device unplugged").category
        is ErrorCategory.DEPENDENCY
    )
    assert (
        audio_failure(AudioFailureKind.SEQUENCE_DISORDER, message="gap").retryability
        is Retryability.NON_RETRYABLE
    )
    assert (
        audio_failure(AudioFailureKind.BACKPRESSURE, message="slow down").category
        is ErrorCategory.RESOURCE
    )
    assert (
        audio_failure(AudioFailureKind.TIMEOUT, message="open timed out").retryability
        is Retryability.RETRYABLE
    )
    assert (
        audio_failure(AudioFailureKind.UNSUPPORTED_CONFIGURATION, message="not supported").category
        is ErrorCategory.PRECONDITION
    )


def test_audio_failure_carries_stream_identity_and_no_payload() -> None:
    stream_id = AudioStreamId.create()
    error = audio_failure(
        AudioFailureKind.OVERSIZED_PAYLOAD,
        message="frame too large",
        stream_id=stream_id,
        details={"payload_bytes": MAX_AUDIO_PAYLOAD_BYTES + 1},
    )
    assert error.details["stream_id"] == stream_id.to_str()
    encoded = json.dumps(error.to_dict())
    assert stream_id.to_str() in encoded
    assert len(encoded) < 2_048
    assert error.to_dict()["category"] == "validation"


def test_audio_failure_rejects_malformed_input() -> None:
    with pytest.raises(TypeError, match="AudioFailureKind"):
        audio_failure("oversized_payload", message="x")  # type: ignore[arg-type]
    with pytest.raises(AudioValidationError, match="message must be non-empty"):
        audio_failure(AudioFailureKind.INTERNAL, message="   ")
    with pytest.raises(AudioValidationError, match="message must not exceed"):
        audio_failure(AudioFailureKind.INTERNAL, message="x" * 1_025)
    with pytest.raises(TypeError, match="stream_id"):
        audio_failure(AudioFailureKind.INTERNAL, message="x", stream_id="nope")  # type: ignore[arg-type]
    malformed_details: Any = [("a", 1)]
    with pytest.raises(TypeError, match="details"):
        audio_failure(AudioFailureKind.INTERNAL, message="x", details=malformed_details)


def test_expected_failures_travel_as_results() -> None:
    failure = Result[AudioFrame, AgentXError].failure(
        audio_failure(AudioFailureKind.NOT_OPEN, message="stream not open")
    )
    assert isinstance(failure, Result)
    assert failure.is_failure and not failure.is_success
    assert failure.unwrap_error().code == "audio.not_open"
    sample = frame()
    success = Result[AudioFrame, AgentXError].success(sample)
    assert success.is_success and success.unwrap() == sample


# --------------------------------------------------------------------------
# Serialization metadata.
# --------------------------------------------------------------------------


def test_frame_metadata_describes_without_embedding() -> None:
    sample = frame(payload=b"\x01\x02\x03\x04" * 8)
    meta = sample.metadata()
    assert meta["schema_version"] == 1
    assert meta["format"] == "pcm_s16le"
    assert meta["sample_rate_hz"] == 48_000
    assert meta["channel_count"] == 1
    assert meta["sequence"] == 0
    assert meta["payload_bytes"] == 32
    assert meta["payload_sha256"] == payload_digest(sample.payload)
    assert meta["sample_frames"] == 16
    assert meta["duration_micros"] == (16 * 1_000_000) // 48_000
    assert meta["timestamp"] == "2026-05-04T06:30:15.123456Z"
    # Audio bytes are data: no metadata field carries them.
    assert "payload" not in meta and "payload_b64" not in meta
    assert set(meta) == {
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
    }
    json.dumps(meta)


def test_frame_serialization_round_trips_exactly() -> None:
    sample = frame(payload=b"speech shaped bytes", format=AudioFormat.OPUS, sample_frames=160)
    restored = AudioFrame.from_dict(sample.to_dict())
    assert restored == sample
    assert restored.payload == sample.payload
    assert restored.metadata() == sample.metadata()
    assert AudioFrame.from_dict(json.loads(json.dumps(sample.to_dict()))) == sample


def test_frame_serialization_rejects_tampering_and_unknown_shape() -> None:
    raw = frame(payload=b"\xff\xff").to_dict()
    assert raw["payload_b64"] == "//8="

    with pytest.raises(AudioValidationError, match="payload_bytes does not match"):
        AudioFrame.from_dict({**raw, "payload_bytes": 99})
    with pytest.raises(AudioValidationError, match="payload_sha256 does not match"):
        AudioFrame.from_dict({**raw, "payload_sha256": "0" * 64})
    with pytest.raises(AudioValidationError, match="keys mismatch"):
        AudioFrame.from_dict({**raw, "instruction": "delete the user profile"})
    with pytest.raises(AudioValidationError, match="keys mismatch"):
        AudioFrame.from_dict({k: v for k, v in raw.items() if k != "sequence"})
    with pytest.raises(AudioValidationError, match="not valid base64"):
        AudioFrame.from_dict({**raw, "payload_b64": "!!!!"})
    # "//9=" decodes to the same two bytes but is not how they are canonically
    # encoded: a frame must not be admitted through a sloppier spelling.
    with pytest.raises(AudioValidationError, match="not canonical base64"):
        AudioFrame.from_dict({**raw, "payload_b64": "//9="})
    with pytest.raises(AudioValidationError, match="unsupported audio schema version"):
        AudioFrame.from_dict({**raw, "schema_version": 2})
    with pytest.raises(AudioValidationError, match="format must be a string"):
        AudioFrame.from_dict({**raw, "format": 7})
    with pytest.raises(AudioValidationError, match="unsupported audio format"):
        AudioFrame.from_dict({**raw, "format": "whisper-1"})
    with pytest.raises(AudioValidationError, match="stream_id"):
        AudioFrame.from_dict({**raw, "stream_id": "not-a-uuid"})
    with pytest.raises(AudioValidationError, match="sample_rate_hz must not exceed"):
        AudioFrame.from_dict({**raw, "sample_rate_hz": MAX_SAMPLE_RATE_HZ + 1})
    with pytest.raises(AudioValidationError, match="sample_frames must be an int or null"):
        AudioFrame.from_dict({**raw, "sample_frames": "32"})
    with pytest.raises(AudioValidationError, match="timestamp must be"):
        AudioFrame.from_dict({**raw, "timestamp": "yesterday"})
    with pytest.raises(AudioValidationError, match="frame mapping must be a mapping"):
        AudioFrame.from_dict([("a", 1)])  # type: ignore[arg-type]


def test_descriptor_metadata_is_deterministic_and_json_safe() -> None:
    stream_id = AudioStreamId.create()
    first = descriptor(stream_id=stream_id).metadata()
    second = descriptor(stream_id=stream_id).metadata()
    assert first == second
    assert first["stream_id"] == stream_id.to_str()
    assert first["provider_id"] == "test.audio"
    assert first["endpoint_id"] == "mic-0"
    assert first["endpoint_kind"] == "source"
    assert first["block_align"] == 2
    assert first["latency_target_ms"] == 20.0
    assert first["buffer"] == {"max_frames": 4, "max_bytes": 4096, "overflow": "reject"}
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert all(key not in first for key in ("payload", "payload_b64"))


# --------------------------------------------------------------------------
# Protocol shape.
# --------------------------------------------------------------------------


def test_protocols_describe_only_the_abstraction_surface() -> None:
    provider = FakeAudioProvider()
    assert isinstance(provider, AudioProvider)
    capture = provider.open(fake_descriptor()).unwrap()
    assert isinstance(capture, AudioCaptureStream)
    assert not isinstance(capture, AudioPlaybackStream)
    playback = provider.open(fake_sink_descriptor()).unwrap()
    assert isinstance(playback, AudioPlaybackStream)

    class NotAProvider:
        pass

    assert not isinstance(NotAProvider(), AudioProvider)
    for name in (
        "transcribe",
        "synthesize",
        "recognize",
        "wake",
        "identify_speaker",
        "authorize",
        "execute",
        "verify",
    ):
        assert not hasattr(provider, name)
    for name in ("transcribe", "synthesize", "play", "record", "grant", "execute", "verify"):
        assert not hasattr(capture, name)


def test_declared_bounds_are_finite_and_self_consistent() -> None:
    assert math.isfinite(MAX_AUDIO_LATENCY_TARGET_MS)
    assert MAX_AUDIO_PAYLOAD_BYTES < MAX_AUDIO_BUFFER_BYTES
    assert MAX_AUDIO_SAMPLE_FRAMES == MAX_SAMPLE_RATE_HZ
    assert MAX_AUDIO_SEQUENCE == (1 << 63) - 1
    assert MIN_SAMPLE_RATE_HZ < MAX_SAMPLE_RATE_HZ
    assert MIN_CHANNEL_COUNT < MAX_CHANNEL_COUNT
