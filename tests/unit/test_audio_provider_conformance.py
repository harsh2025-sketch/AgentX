"""A7.01 provider conformance scaffolding tests.

The scaffold in ``tests.support.audio_conformance`` is how a future real audio
adapter (A7.02 and later) gets gated: it must pass an honest in-memory provider and
must name every dishonesty the fixtures can produce. Both directions are tested,
because a conformance check that only ever passes is not a check.
"""

from __future__ import annotations

import pytest

from agentx.core.audio import AudioEndpointKind, AudioFormat
from tests.support.audio_conformance import (
    CONFORMANCE_FRAME_LIMIT,
    assert_audio_provider_conforms,
    audio_conformance_violations,
)
from tests.support.audio_provider import (
    DefectSpec,
    FakeAudioProvider,
    HostileAudioProvider,
    narrow_formats,
)


def findings_for(defects: DefectSpec) -> tuple[str, ...]:
    return audio_conformance_violations(FakeAudioProvider(defects=defects))


def test_honest_in_memory_provider_is_conformant() -> None:
    assert_audio_provider_conforms(FakeAudioProvider())
    assert audio_conformance_violations(FakeAudioProvider()) == ()


def test_conformance_is_over_configuration_shape_not_a_vendor_choice() -> None:
    """A provider advertising one narrow format is still judged conformant."""

    provider = FakeAudioProvider(support=narrow_formats(), frame_count=2)
    assert audio_conformance_violations(provider) == ()
    endpoints = provider.endpoints().unwrap()
    assert {item.kind for item in endpoints} == {
        AudioEndpointKind.SOURCE,
        AudioEndpointKind.SINK,
    }
    assert narrow_formats().formats == frozenset({AudioFormat.PCM_S16LE})


def test_non_provider_objects_are_rejected_without_running_anything() -> None:
    for candidate in (None, "whisper", object(), 42, [FakeAudioProvider()]):
        violations = audio_conformance_violations(candidate)
        assert violations == (
            "provider does not structurally satisfy the AudioProvider protocol",
        ), candidate


@pytest.mark.parametrize(
    ("defects", "expected_fragment"),
    [
        (DefectSpec(skip_frames=True), "sequence"),
        (DefectSpec(replay_frames=True), "sequence"),
        (DefectSpec(regress_timestamps=True), "timestamp order violated"),
        (DefectSpec(wrong_channel_count=True), "channel count contradicts open"),
        (DefectSpec(wrong_sample_rate=True), "sample rate contradicts open"),
        (DefectSpec(foreign_stream=True), "foreign stream id"),
        (DefectSpec(exceeds_endpoint_bound=True), "exceeds the advertised endpoint bound"),
        (DefectSpec(unbounded_sink=True), "never silently absorbed"),
        (DefectSpec(frozen_status=True), "status must stay terminal after close"),
        (DefectSpec(ignores_cancellation=True), "cancellation token says otherwise"),
    ],
)
def test_scaffold_names_each_defect_independently(
    defects: DefectSpec, expected_fragment: str
) -> None:
    violations = findings_for(defects)
    assert violations, defects
    assert any(expected_fragment in finding for finding in violations), (defects, violations)
    with pytest.raises(AssertionError, match=expected_fragment):
        assert_audio_provider_conforms(FakeAudioProvider(defects=defects))


def test_scaffold_reports_one_finding_per_violated_promise() -> None:
    many = findings_for(
        DefectSpec(
            skip_frames=True,
            regress_timestamps=True,
            wrong_channel_count=True,
            unbounded_sink=True,
        )
    )
    # Three capture frames plus their sink-side consequences: the harness does not
    # collapse distinct promises into one generic "provider is broken" message.
    assert len(many) >= 4
    assert all(finding.strip() for finding in many)
    assert len(set(many)) == len(many)


def test_hostile_provider_fails_loudly_and_specifically() -> None:
    violations = audio_conformance_violations(HostileAudioProvider())
    assert len(violations) >= 5
    joined = "\n".join(violations)
    for promise in (
        "supports() disagrees",
        "sequence",
        "channel count contradicts open",
        "status must stay terminal after close",
        "cancellation token says otherwise",
        "must refuse work",
        "does not satisfy its stream protocol",
    ):
        assert promise in joined, promise
    with pytest.raises(AssertionError, match=r"not A7\.01 conformant"):
        assert_audio_provider_conforms(HostileAudioProvider())


def test_findings_never_quote_audio_bytes() -> None:
    provider = FakeAudioProvider(defects=DefectSpec(exceeds_endpoint_bound=True))
    violations = audio_conformance_violations(provider)
    assert violations
    for finding in violations:
        assert "b'" not in finding
        assert not any(character in finding for character in ("\x00", "\n"))


def test_probe_limit_is_observable_and_bounded() -> None:
    assert CONFORMANCE_FRAME_LIMIT > 0
    # A provider that never produces a frame is a failure, not a silent pass.
    violations = audio_conformance_violations(FakeAudioProvider(frame_count=0))
    assert any("produced no frame" in finding for finding in violations)


def test_opened_stream_must_report_open_state() -> None:
    # A provider that hands out an already-dead stream but calls it OPEN is lying twice.
    provider = FakeAudioProvider(defects=DefectSpec(starts_closed=True))
    violations = audio_conformance_violations(provider)
    assert any("must be OPEN" in finding for finding in violations)
