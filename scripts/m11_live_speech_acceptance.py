"""Manual live STT/TTS acceptance for the M11 HTTP speech adapters.

Required environment:
  AGENTX_M11_SPEECH_PROVIDER_ID
  AGENTX_M11_STT_ENDPOINT
  AGENTX_M11_TTS_ENDPOINT
  AGENTX_M11_SPEECH_API_KEY
  AGENTX_M11_STT_AUDIO_B64

No credential, transcript text, synthesized audio, or raw input audio is emitted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime

from agentx.cognition.speech import (
    HttpSpeechConfig,
    HttpSpeechToTextProvider,
    HttpTextToSpeechProvider,
    SpeechProviderId,
    SpeechSessionId,
    SttRequest,
    TtsRequest,
)
from agentx.core.audio import AudioFormat, AudioFrame
from agentx.core.execution import CancellationSource
from agentx.core.ids import AudioStreamId
from agentx.kernel.secrets import SecretRef, SecretValue

_SECRET_REF = SecretRef("speech/m11-live")


class _EnvironmentResolver:
    def resolve(self, reference: SecretRef, /) -> SecretValue:
        if reference != _SECRET_REF:
            raise KeyError("unknown speech secret reference")
        value = os.environ.get("AGENTX_M11_SPEECH_API_KEY")
        if not value:
            raise RuntimeError("speech credential unavailable")
        return SecretValue(value)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"required environment variable {name} is unavailable")
    return value.strip()


def _emit(payload: Mapping[str, object]) -> None:
    sys.stdout.write(json.dumps(dict(payload), sort_keys=True) + "\n")


def main() -> int:
    required = (
        "AGENTX_M11_SPEECH_PROVIDER_ID",
        "AGENTX_M11_STT_ENDPOINT",
        "AGENTX_M11_TTS_ENDPOINT",
        "AGENTX_M11_SPEECH_API_KEY",
        "AGENTX_M11_STT_AUDIO_B64",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        _emit({"accepted": False, "reason": "missing_environment", "missing": missing})
        return 2

    provider_id = SpeechProviderId(_required("AGENTX_M11_SPEECH_PROVIDER_ID"))
    resolver = _EnvironmentResolver()
    stt = HttpSpeechToTextProvider(
        HttpSpeechConfig(
            provider_id=provider_id,
            endpoint=_required("AGENTX_M11_STT_ENDPOINT"),
            credential=_SECRET_REF,
        ),
        secrets=resolver,
    )
    tts = HttpTextToSpeechProvider(
        HttpSpeechConfig(
            provider_id=provider_id,
            endpoint=_required("AGENTX_M11_TTS_ENDPOINT"),
            credential=_SECRET_REF,
        ),
        secrets=resolver,
    )
    try:
        audio = base64.b64decode(_required("AGENTX_M11_STT_AUDIO_B64"), validate=True)
    except ValueError:
        _emit({"accepted": False, "reason": "invalid_audio_base64"})
        return 2

    cancellation = CancellationSource()
    session_id = SpeechSessionId.create()
    frame = AudioFrame(
        stream_id=AudioStreamId.create(),
        format=AudioFormat.PCM_S16LE,
        sample_rate_hz=16000,
        channel_count=1,
        sequence=0,
        timestamp=datetime.now(UTC),
        payload=audio,
    )
    stt_result = stt.transcribe(
        SttRequest(
            session_id=session_id,
            frames=(frame,),
            cancellation_token=cancellation.token,
        )
    )
    if stt_result.is_failure:
        _emit(
            {
                "accepted": False,
                "stage": "stt",
                "error_code": stt_result.unwrap_error().code,
            }
        )
        return 1

    transcript = stt_result.unwrap()
    tts_result = tts.synthesize(
        TtsRequest(
            session_id=session_id,
            text="AgentX bounded M11 live speech acceptance.",
            cancellation_token=cancellation.token,
        )
    )
    if tts_result.is_failure:
        _emit(
            {
                "accepted": False,
                "stage": "tts",
                "error_code": tts_result.unwrap_error().code,
            }
        )
        return 1

    synthesized = b"".join(frame.payload for frame in tts_result.unwrap().frames)
    _emit(
        {
            "accepted": True,
            "provider": provider_id.value,
            "stt_final": transcript.is_final,
            "stt_text_sha256": hashlib.sha256(transcript.text.encode()).hexdigest(),
            "tts_audio_sha256": hashlib.sha256(synthesized).hexdigest(),
            "input_audio_exposed": False,
            "transcript_exposed": False,
            "tts_audio_exposed": False,
            "credential_exposed": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
