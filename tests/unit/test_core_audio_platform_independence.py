"""A7.01 platform-independence tests for the audio contracts.

AgentX is Windows-first, but the audio boundary must behave identically on every
host, because the same frames later cross a device adapter, a journal record, and a
network transport. These tests pin the properties that make that true: no host byte
order, no local timezone, no locale-sensitive formatting, no filesystem or platform
sniffing in identity, and integer-only arithmetic in derived timing.
"""

from __future__ import annotations

import json
import platform
import sys
from datetime import UTC, datetime, timedelta, timezone

from agentx.core import audio as audio_module
from agentx.core.audio import (
    MAX_AUDIO_PAYLOAD_BYTES,
    AudioBufferPolicy,
    AudioEndpoint,
    AudioEndpointId,
    AudioEndpointKind,
    AudioFormat,
    AudioFormatSupport,
    AudioFrame,
    AudioProviderId,
    AudioStreamDescriptor,
    block_align_bytes,
    payload_digest,
    sample_width_bytes,
)
from agentx.core.ids import AudioStreamId

INSTANT = datetime(2026, 7, 15, 12, 34, 56, 654321, tzinfo=UTC)


def build(
    stream_id: AudioStreamId,
    *,
    timestamp: datetime,
    payload: bytes = b"\x01\x02\x03\x04",
) -> AudioFrame:
    return AudioFrame(
        stream_id=stream_id,
        format=AudioFormat.PCM_S16LE,
        sample_rate_hz=48_000,
        channel_count=1,
        sequence=0,
        timestamp=timestamp,
        payload=payload,
    )


def test_host_byte_order_never_changes_the_contract() -> None:
    """The encoding name fixes byte order, so every host reads the same bytes."""

    assert sys.byteorder in {"little", "big"}
    assert all(fmt.value.endswith("le") for fmt in AudioFormat if fmt.name.startswith("PCM_")), (
        "endianness must be part of the format name, never inferred from the host"
    )
    little_endian_frame = build(AudioStreamId.create(), timestamp=INSTANT, payload=b"\x01\x00")
    assert little_endian_frame.byte_count == 2
    assert little_endian_frame.sample_frames == 1
    assert block_align_bytes(AudioFormat.PCM_S16LE, 2) == 4
    assert sample_width_bytes(AudioFormat.PCM_F32LE) == 4


def test_serialized_form_is_identical_across_timezone_settings() -> None:
    stream_id = AudioStreamId.create()
    west = INSTANT.astimezone(timezone(timedelta(hours=-7)))
    east = INSTANT.astimezone(timezone(timedelta(hours=9, minutes=30)))
    local_offset = datetime(2026, 7, 15, 5, 34, 56, 654321, tzinfo=timezone(timedelta(hours=-7)))

    assert build(stream_id, timestamp=west).to_dict() == build(stream_id, timestamp=east).to_dict()
    assert (
        build(stream_id, timestamp=local_offset).to_dict()
        == build(stream_id, timestamp=INSTANT).to_dict()
    )
    encoded = json.dumps(build(stream_id, timestamp=west).to_dict())
    assert "2026-07-15T12:34:56.654321Z" in encoded
    assert "PDT" not in encoded and "IST" not in encoded


def test_derived_timing_is_integer_math_with_no_float_rounding() -> None:
    """997 samples at 44.1 kHz is exact integer division on every platform."""

    frame = AudioFrame(
        stream_id=AudioStreamId.create(),
        format=AudioFormat.OPUS,
        sample_rate_hz=44_100,
        channel_count=2,
        sequence=0,
        timestamp=INSTANT,
        payload=b"\x00" * 64,
        sample_frames=997,
    )
    assert frame.duration == timedelta(microseconds=(997 * 1_000_000) // 44_100)
    assert (frame.duration or timedelta()).microseconds == 22607
    metadata = frame.metadata()
    assert metadata["duration_micros"] == (997 * 1_000_000) // 44_100
    assert isinstance(metadata["duration_micros"], int)


def test_digest_and_base64_are_platform_neutral_text() -> None:
    digest = payload_digest(bytes(range(256)))
    frame = build(AudioStreamId.create(), timestamp=INSTANT, payload=bytes(range(4)))
    assert digest.isascii() and len(digest) == 64
    assert frame.to_dict()["payload_b64"].isascii()
    assert all(not isinstance(value, float) for value in frame.metadata().values())


def test_stream_identity_does_not_depend_on_host_name_or_filesystem() -> None:
    """UUIDv4 only: no MAC address, hostname, PID, or path is part of identity."""

    ids = {AudioStreamId.create() for _ in range(200)}
    assert len(ids) == 200
    for stream_id in ids:
        assert stream_id.value.version == 4
        text = stream_id.to_str()
        assert text.isascii() and text.islower()
        assert "\\" not in text and "/" not in text
        assert platform.node() not in text
        assert str(sys.executable) not in text


def test_untrusted_labels_are_preserved_but_never_reformatted() -> None:
    support = AudioFormatSupport(
        formats=frozenset({AudioFormat.PCM_S16LE}),
        sample_rates_hz=frozenset({48_000}),
        channel_counts=frozenset({1}),
        max_payload_bytes=4 * 1024,
    )
    labelled = AudioEndpoint(
        endpoint_id=AudioEndpointId(provider_id=AudioProviderId("provider"), value="μ-0"),
        kind=AudioEndpointKind.SOURCE,
        label="Mikrofon (Realtek) — 2 kanāli",
        support=support,
    )
    descriptor = AudioStreamDescriptor(
        stream_id=AudioStreamId.create(),
        endpoint=labelled,
        format=AudioFormat.PCM_S16LE,
        sample_rate_hz=48_000,
        channel_count=1,
        buffer=AudioBufferPolicy(max_bytes=4 * 1024),
    )
    assert labelled.label == "Mikrofon (Realtek) — 2 kanāli"
    assert str(labelled.endpoint_id) == "provider/μ-0"
    # Metadata stays machine-readable ASCII-safe JSON: untrusted text is not echoed
    # into it, so a device name can never smuggle formatting into a journal line.
    assert labelled.label not in json.dumps(descriptor.metadata())
    json.dumps(descriptor.metadata(), ensure_ascii=True)


def test_no_line_ending_or_path_assumptions_in_serialized_output() -> None:
    blob = json.dumps(build(AudioStreamId.create(), timestamp=INSTANT).to_dict())
    assert "\r" not in blob
    assert "\n" not in blob
    assert MAX_AUDIO_PAYLOAD_BYTES > 0
    assert "\\" not in blob
    # Path separators have no business in a frame record.
    assert "/" not in blob.replace("+/=", "")


def test_module_never_reads_the_environment_or_platform() -> None:
    """Behaviour cannot fork on host facts, so nothing may consult them."""

    for forbidden in ("platform", "sys", "os", "pathlib", "socket", "ctypes", "locale"):
        assert not hasattr(audio_module, forbidden), forbidden
