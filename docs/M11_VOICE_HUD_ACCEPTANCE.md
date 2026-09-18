# M11 Voice Interaction & HUD Acceptance

## Scope

Milestone M11 covers **AX-436 through AX-460**. The implementation campaign
started from canonical main:

- `STARTING_MAIN_SHA=446771fab52278b4f12fa752fb000bd4668a5241`
- baseline M11 acceptance: **2/25 VERIFIED**
- verified foundations retained: AX-436 audio abstraction and AX-452 runtime-to-UI event protocol.

This document describes production implementation and reproducible evidence. It
does not treat real hardware or live provider evidence as interchangeable with
deterministic CI acceptance.

## Architecture

M11 preserves existing AgentX authority boundaries:

```text
microphone/audio
  -> provider-neutral audio contract
  -> STT provider
  -> transcript (untrusted data)
  -> canonical Task objective
  -> AgentLoop
  -> governed strategy / Executor
  -> ActionGate + permissions + EmergencyStop + ResourceBudget
  -> capability execution
  -> independent verification
  -> AX-452 redacted runtime telemetry
  -> HUD state
  -> TTS
  -> speaker
```

No voice component directly executes shell, browser, filesystem, Windows, or
other machine actions.

### Audio

`agentx.core.audio` remains the provider-neutral AX-436 foundation.
`agentx.capabilities.windows.audio_provider.WinMmAudioProvider` is the concrete
Windows microphone/speaker adapter. It:

- loads WinMM lazily, not at import time;
- exposes explicit microphone and speaker endpoints;
- supports bounded PCM S16LE streams;
- propagates typed provider/device failures;
- supports explicit close and idempotent cancellation;
- holds raw audio only in bounded memory;
- releases WinMM handles on all return paths.

### STT and TTS

`agentx.cognition.speech` defines provider-neutral STT/TTS contracts and real
bounded HTTP adapters:

- `SpeechToTextProvider` / `SttRequest` / `SttTranscript`;
- `TextToSpeechProvider` / `TtsRequest` / `TtsResponse`;
- `HttpSpeechToTextProvider`;
- `HttpTextToSpeechProvider`.

Configuration is explicit. Credentials are supplied only through the canonical
`SecretRef` / `SecretResolver` boundary. Requests and responses are bounded,
timeouts and cancellation are explicit, redirects/retries are not ambient, and
provider text remains data rather than authority.

### Realtime voice

`agentx.cognition.realtime_voice` owns:

- explicit realtime lifecycle states;
- deterministic PCM energy VAD;
- bounded turn-end detection;
- bounded turn audio;
- stale-result rejection after cancellation;
- TTS/playback interruption and barge-in;
- `ComposedRealtimeVoiceProvider`, which creates fresh sessions from injected
  audio/STT/TTS providers with a finite open/recovery attempt ceiling.

### Voice to AgentX trust boundary

`agentx.voice_runtime.VoiceTaskBridge` converts transcript text only into a
canonical Task objective with `input_modality=voice`. Routing evidence,
verification requirements, limits, permissions, risk, budgets, capability
selection and approval are never parsed from transcript text.

The bridge calls the existing canonical `AgentLoop`. Machine execution still
flows through the existing Executor / CapabilityExecutionLoop and Trusted Kernel
controls.

### Spoken confirmation

`SpokenConfirmationProtocol` issues ephemeral confirmations bound to the exact
canonical `HumanApprovalRequest`. A valid approval requires the exact
`confirm <nonce>` phrase while the request is active and fresh.

Confirmation is:

- exact-request bound;
- nonce bound;
- expiring;
- non-replayable;
- invalidated on explicit cancellation;
- converted only into canonical `HumanApprovalDecision` evidence.

It does not itself grant permission. The canonical execution loop still applies
ActionGate, authority, risk, emergency-stop, resource and verification rules.

### Cancellation and barge-in

`RealtimeVoiceSession` owns audio/STT/TTS cancellation. `VoiceTaskBridge`
owns governed task cancellation. `VoiceHudRuntime` composes both owners so
barge-in propagates across stale playback and the active AgentX task.

Late STT/TTS results are rejected by cancellation/generation checks rather than
reviving a cancelled turn.

### HUD and telemetry

AX-452 remains the one-way runtime-to-UI protocol. `agentx.hud.HudModel`
reduces ordered `RuntimeUiEvent` telemetry into typed states:

- idle;
- listening;
- reasoning;
- executing;
- verifying;
- speaking;
- recovering;
- awaiting confirmation;
- cancelled;
- failed.

Duplicate and late sequence numbers are ignored. Runtime instance replacement
requires the canonical restart event. Verification state is driven by typed
runtime evidence rather than a provider/action return string.

`HudCommandGateway` deduplicates UI command IDs, rejects stale task bindings,
checks state-sensitive controls and delegates to a controller boundary. It
cannot directly mutate Task, ActionGate, permission, risk or verification state.

`TkHudApp` is a real desktop surface. Tk is loaded only when the interactive
application is explicitly run, so headless CI validates the state/control
architecture without pretending to be interactive-host evidence.

## Telemetry

M11 telemetry uses AX-452 envelopes and preserves runtime instance, sequence,
correlation and task identity. Raw transcript/audio/provider payloads are not
placed in HUD telemetry.

Production sources include:

- listening/session state from realtime voice;
- reasoning only when canonical routing facts actually require a reasoning
  strategy;
- canonical action/execution events through `project_event_for_ui`;
- explicit verification state from canonical orchestration outcome;
- recovery state when canonical recovery/repair telemetry is projected.

## Privacy

- Audio frame payloads are excluded from repr and are not persisted by M11.
- Voice turn buffers are bounded and cleared after processing/cancellation.
- Transcript text is not emitted to HUD telemetry.
- HTTP speech failures do not include response bodies, prompts or credentials.
- Provider credentials are resolved only immediately before transport and are
  never included in Result details or UI events.
- Manual acceptance scripts emit hashes/metadata only, never raw audio,
  transcript text or credential material.
- Restart creates no implicit microphone capture, playback, provider session or
  confirmation restoration.

## Failure and security coverage

M11 tests cover:

- unsupported/non-Windows audio host;
- microphone/speaker lifecycle and cancellation;
- STT/TTS provider cancellation;
- malformed provider response;
- provider timeout/unavailability;
- silence/noise/speech VAD;
- bounded end-of-turn;
- concurrent barge-in during playback;
- stale result rejection;
- duplicate HUD commands;
- stale HUD task commands;
- expired, cancelled and replayed confirmation;
- hostile spoken claims attempting to grant permission, lower risk, increase
  budget, bypass ActionGate, clear emergency stop, ignore cancellation,
  fabricate verification, activate procedures, install capabilities or execute
  shell/code.

The invariant is:

```text
AUDIO / TRANSCRIPT / PROVIDER OUTPUT = DATA
```

never authority.

## Deterministic production-path acceptance

`tests/integration/test_m11_voice_hud_e2e.py` composes controlled audio
adapters with the production realtime session, STT/TTS contracts,
`VoiceTaskBridge`, canonical `AgentLoop`, governed capability strategy,
independent verification, AX-452 telemetry and HUD state.

The path is:

```text
controlled microphone frames
 -> realtime VAD/session
 -> STT transcript
 -> canonical Task/AgentLoop
 -> ActionGate-governed capability
 -> independent verification
 -> HUD telemetry/state
 -> TTS
 -> controlled speaker
```

The test deliberately embeds hostile authority claims in the transcript and
proves they remain inert Task objective data.

## Real-environment acceptance entry points

These are intentionally separate from default CI:

```bash
python scripts/m11_real_audio_acceptance.py
python scripts/m11_live_speech_acceptance.py
python scripts/m11_interactive_hud.py
```

### Real microphone / speaker

`m11_real_audio_acceptance.py` records one short bounded PCM frame through the
production WinMM provider and plays it through the production speaker provider.
It persists no audio.

### Live STT / TTS

`m11_live_speech_acceptance.py` requires explicit deployment configuration:

- `AGENTX_M11_SPEECH_PROVIDER_ID`
- `AGENTX_M11_STT_ENDPOINT`
- `AGENTX_M11_TTS_ENDPOINT`
- `AGENTX_M11_SPEECH_API_KEY`
- `AGENTX_M11_STT_AUDIO_B64`

It invokes the production HTTP STT and TTS adapters once each and emits only
sanitized hashes/metadata.

### Interactive HUD

`m11_interactive_hud.py` launches the real Tk surface on an interactive desktop.

## Known environment limitations

The default canonical CI runner does not guarantee an attached physical
microphone/speaker, live speech-provider credentials, or an interactive user
desktop. Therefore those three evidence classes must be reported independently
from deterministic implementation acceptance.

No task should be described as having real hardware/provider/UI evidence unless
the corresponding entry point was actually run successfully in that
environment.

## Validation

Canonical quality gate:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy
python scripts/task_ledger.py
python -m pytest
```

Targeted M11 suites include:

```bash
python -m pytest -q tests/unit/test_m11_audio_provider.py
python -m pytest -q tests/unit/test_m11_speech_providers.py
python -m pytest -q tests/unit/test_m11_realtime_voice.py
python -m pytest -q tests/unit/test_m11_hud.py
python -m pytest -q tests/integration/test_m11_voice_runtime_acceptance.py
python -m pytest -q tests/integration/test_m11_voice_hud_e2e.py
python -m pytest -q tests/adversarial/test_m11_voice_authority.py
```

## Second-pass audit result

The second-pass implementation audit closed the production gaps found during the
campaign, including wiring turn-end detection into the realtime session, emitting
listening/executing/recovering/verifying/speaking/cancellation/idle telemetry from
the production composition, isolating HUD subscriber failures from runtime
authority, registering the WinMM native seam under the existing Windows
architecture guard, and carrying one AX-452 correlation ID through each complete
voice turn.

Canonical M11 task result: **AX-436..AX-460 = 25/25 VERIFIED** for the exact
milestone requirements.

Implementation validation evidence:

- implementation head: `5d01d8625535477677dc02b32011d164a61972fb`
- C1.01 quality run: `35369274913` / run #1180
- runtime-only installation: PASS
- Ruff lint: PASS
- Ruff format: PASS
- strict mypy: PASS
- task-ledger validation: PASS
- Windows-host acceptance: PASS
- M7 real-browser regression: PASS
- full pytest: **11,316 passed, 9 skipped**
- physical microphone/speaker acceptance: **NOT RUN**
- live external STT/TTS provider acceptance: **NOT RUN**
- interactive desktop HUD acceptance: **NOT RUN**

The three real-environment classes above remain explicitly distinct from the
deterministic production-path evidence and are not claimed as executed.
