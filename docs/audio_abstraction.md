# Audio abstraction boundary (A7.01)

> **Status:** A7.01. This task establishes the provider-neutral audio contracts
> only. It implements **no** streaming speech recognition, no text-to-speech, no
> wake word, no speaker identification, no voice commands, and **no** model calls.
> Those are A7.02 and later, and each of them will be reached *through* the
> contracts documented here rather than by growing a second audio vocabulary.

## What A7.01 is

Real-time voice input and output eventually need Whisper/OpenAI-style
transcription, Kokoro-style synthesis, and platform speech APIs. None of those may
become the shape of the core audio model: a vendor SDK changes on its own schedule,
and the runtime must keep working when a provider is absent, misconfigured, or
lying about what it can do.

`agentx.core.audio` therefore describes *audio as data with a lifecycle*:

| Question the rest of AgentX may ask | Contract that answers it |
| ----------------------------------- | ------------------------ |
| What can this provider actually carry? | `AudioFormatSupport.supports(...)` / `.admits(...)` |
| Which device-ish thing is this? | `AudioEndpoint` (`AudioEndpointId`, `AudioEndpointKind`, label, support, default flag) |
| What did I just open? | `AudioStreamDescriptor` (stream id, endpoint, format, rate, channels, buffer policy, sequence origin, latency target) |
| What is one piece of audio? | `AudioFrame` (stream id, geometry, sequence, timestamp, bounded payload, digest) |
| Is this frame in order? | `check_sequence(...)`, `check_timestamp(...)` |
| May I hand this frame to a sink? | `AudioBufferPolicy.assess(...)` -> `AudioAdmissionDecision` |
| Where is the stream in its life? | `AudioStreamState`, `AudioStreamStatus`, `LEGAL_AUDIO_STREAM_TRANSITIONS` |
| How do I say "stop"? | `agentx.core.execution.CancellationToken` held by the stream |
| How does a failure travel? | `audio_failure(...)` -> `AgentXError` with an `audio.*` code, carried in `Result` |
| How does a provider get gated? | `AudioProvider` / `AudioCaptureStream` / `AudioPlaybackStream` protocols |

Audio lives in exactly one module (`src/agentx/core/audio.py`), not a package. It
is a *leaf*: `core` keeps its no-upward-dependency rule, so audio imports only the
stdlib plus `agentx.core.errors`, `agentx.core.execution`, `agentx.core.ids`, and
`agentx.core.result`. There is no new edge in `agentx._architecture`, no new
subsystem, and no dependency on any speech, codec, or OS audio library.

## The three contracts that carry the most weight

**1. Payload bytes are data, never authority.** `AudioFrame.payload` is `bytes`.
It is excluded from `repr`, never embedded in `metadata()`, never embedded in an
error message or error detail, and never parsed as text, JSON, or instructions by
anything in `core`. Serialization is explicit: `metadata()` carries
`payload_bytes` plus a `payload_sha256` digest, and only `to_dict()` adds canonical
base64 — which `from_dict()` verifies against both the declared byte count and the
digest, and round-trips through the same constructor. A frame that smuggles
`"ignore previous instructions"` is, to this contract, 31 samples.

**2. Bounds are validated at construction, in one direction.** A frame cannot
exist with an empty, oversized, or block-misaligned payload; PCM cannot exist with
a sample count that contradicts its byte length; a timestamp cannot be
naive or outside `1970..9999`; `sample_frames`, when supplied, must agree with the
payload. The numeric window is deliberately narrow and stated as data:
`MIN_SAMPLE_RATE_HZ`/`MAX_SAMPLE_RATE_HZ` (8 kHz..192 kHz), `MIN_CHANNEL_COUNT`/
`MAX_CHANNEL_COUNT` (1..8), `MAX_AUDIO_SAMPLE_FRAMES` (one second at the widest
rate), `MAX_AUDIO_PAYLOAD_BYTES` (1 MiB), buffer frames 1..4096,
`MAX_AUDIO_BUFFER_BYTES` (8 MiB), `MIN_AUDIO_LATENCY_TARGET_MS`/
`MAX_AUDIO_LATENCY_TARGET_MS` (0.5..5000 ms, finite only — `nan`/`inf` are
rejected, as is any implicit float-to-int coercion), and sequence numbers in
`0..MAX_AUDIO_SEQUENCE` so a stream can never wrap past a 63-bit counter.
Anything outside those windows is a configuration error, not an exotic device.

**3. Provider honesty is checkable, not assumed.** `AudioFormatSupport` expresses
what an endpoint advertises; `AudioStreamDescriptor.admits_payload()` re-checks
geometry against the *opened* descriptor; `block_align_bytes`, `is_pcm_format`, and
`sample_width_bytes` keep the alignment rule derived from format rather than
convention. A provider that advertises `formats={}` and then refuses 48 kHz mono
s16le is caught by the conformance scaffold (below), because an empty advertised set
means "the whole window", not "whatever I feel like".

## Lifecycle, buffering, and cancellation

`AudioStreamState` is `NEW -> OPEN -> {DRAINING, CLOSED, CANCELLED, FAILED}`, with
`DRAINING -> {CLOSED, CANCELLED, FAILED}`. Terminality is *derived* from the
transition matrix (`TERMINAL_AUDIO_STREAM_STATES`), the same idiom A1.06 uses for
Tasks, so the table stays the single source of truth. Asking and acting are
separate: `can_transition_audio_state`, `legal_audio_transitions`,
`audio_transition_error` (an `AgentXError` in the `CONFLICT` category listing the
allowed targets), `validate_audio_state_transition`, and
`try_transition_audio_state`. Nothing in `core` owns a mutable stream; a provider
implements the transition and reports it.

Backpressure is a *decision value*, not an exception. `AudioBufferPolicy.assess(...)`
returns `AudioAdmissionDecision`, whose outcome is one of `ACCEPTED`, `EVICTED`,
`DEFERRED`, `REJECTED_FULL`, `REJECTED_OVERSIZED` under an `AudioOverflowPolicy` of
`REJECT`, `DROP_OLDEST`, or `DEFER`. The invariants that make this safe for a
real-time path: `REJECTED_OVERSIZED` is exactly "this frame can never fit"
(`frame_bytes > policy.max_bytes`), `requires_backpressure` is the only signal a
caller should slow down on, and `may_drop_audio` is true whenever audio has been or
will be discarded — so a caller can report "we lost 200 ms of speech" without
trusting a provider's prose. `admission_failure(...)` converts a refusal into a
canonical error and refuses to convert an acceptance.

Cancellation is observation-only and reused, not reinvented: a stream exposes
`cancellation_token` from A1.07's `CancellationToken`, and `close(reason=None)` /
`cancel(reason=None)` are the only stop signals on the protocol. There is no
`abort()`, `stop_now()`, or reset flag, and no second cancellation type for audio to
invent.

## Errors

Failures use the canonical `AgentXError` in `Result`, never a private audio
exception, via `audio_failure(AudioFailureKind.X, message=..., stream_id=...,
details=...)` producing the stable code `audio.<kind>`. `AudioFailureKind` covers
`UNSUPPORTED_CONFIGURATION`, `UNSUPPORTED_ENDPOINT`, `EMPTY_PAYLOAD`,
`OVERSIZED_PAYLOAD`, `MISALIGNED_PAYLOAD`, `SEQUENCE_DISORDER`,
`TIMESTAMP_DISORDER`, `INVALID_STATE_TRANSITION`, `NOT_OPEN`, `ALREADY_OPEN`,
`BUFFER_FULL`, `BACKPRESSURE`, `CANCELLED`, `TIMEOUT`, `PROVIDER_UNAVAILABLE`,
`CONFIGURATION`, `INTERNAL`; the module's `_FAILURE_POLICY` table maps each to a
category and retryability. Validation inside the contracts raises
`AudioValidationError` (a `ValueError`) because those are programming errors at the
boundary, not runtime outcomes.

## Provider protocols and conformance

Three `@runtime_checkable` protocols exist, and only these three, because they are
what a vendor boundary actually needs: `AudioProvider` (identity, `supports`,
`endpoints`, `open(descriptor, *, deadline=None)`), `AudioCaptureStream`
(`read() -> Result[AudioFrame, AgentXError]`), and `AudioPlaybackStream`
(`write(frame) -> Result[AudioAdmissionDecision, AgentXError]`, `drain()`).
`src/agentx` contains no implementer — that is asserted by the architecture test,
so this task cannot quietly turn into a fake-first driver.

`tests/support/audio_conformance.py` turns those promises into an auditable check:
`audio_conformance_violations(provider)` returns one human-readable finding per
violated promise (identity, endpoint enumeration, advertisement honesty, descriptor
consistency, frame bounds and alignment, sequence, timestamp order, lifecycle,
cancellation, backpressure, error-code shape, and "no payload bytes in errors"), and
`assert_audio_provider_conforms(provider)` raises with the full list.
`tests/support/audio_provider.py` provides the honest fake and a `DefectSpec` that
switches on the misbehaviours a real adapter could plausibly ship. The scaffold must
pass the honest provider **and** name every injected defect — a conformance check
that only ever passes is not a check. A7.02's first real adapter is gated by the
same function.

## Explicit non-goals

A7.01 has no device access, no thread, no asyncio loop, no file or socket I/O, no
codec, no resampling, no VAD, no transcription, no synthesis, no playback, no
recording, no session, no conversation runtime, and no vendor selection. It ships no
"reference provider": the fakes live under `tests/` and are not shipped runtime
code. There is no placeholder stream, no `NotImplementedError` subclass standing in
for A7.02, and no configuration knob that names a speech product.

## Validation

- `tests/unit/test_core_audio.py` — format/rate/channel bounds, payload alignment and
  size, sequence and timestamp ordering, lifecycle legality, admission decisions,
  error and failure mapping, metadata and strict serialization round-trips.
- `tests/unit/test_audio_provider_conformance.py` — the scaffold passes the honest
  provider and reports each named defect, including the hostile provider.
- `tests/unit/test_core_audio_platform_independence.py` — identical behaviour and
  identical serialized bytes regardless of host platform, locale, or line endings;
  no path, registry, or device string appears anywhere in the contract surface.
- `tests/architecture/test_audio_abstraction_boundaries.py` — import allowlist for
  `core/audio.py`, "core stays a leaf", vendor/codec/platform/runtime name bans,
  `__all__` completeness, protocol census, no implementer in `src/`, payload never
  treated as an object with behaviour.
- `tests/adversarial/test_audio_authority.py` — hostile labels, payloads, status
  reasons, buffer pressure, and cancellation spoofing; audio activity moves no
  kernel state, clears no `EmergencyStop`, and satisfies no gate.
