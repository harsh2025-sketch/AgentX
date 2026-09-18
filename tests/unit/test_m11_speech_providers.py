"""Production HTTP STT/TTS provider contract and failure tests for M11."""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from agentx.cognition.speech import (
    HttpSpeechConfig,
    HttpSpeechToTextProvider,
    HttpTextToSpeechProvider,
    SpeechFailureKind,
    SpeechProviderId,
    SpeechSessionId,
    SttRequest,
    TtsRequest,
)
from agentx.core.audio import AudioFormat, AudioFrame
from agentx.core.execution import CancellationSource
from agentx.core.ids import AudioStreamId
from agentx.kernel.secrets import SecretRef, SecretValue

_SECRET = SecretRef("speech/test-m11")
_PROVIDER = SpeechProviderId("m11-http-test")


class _Resolver:
    def resolve(self, reference: SecretRef, /) -> SecretValue:
        if reference != _SECRET:
            raise KeyError(reference)
        return SecretValue("test-secret-material")


class _Handler(BaseHTTPRequestHandler):
    calls: ClassVar[list[tuple[str, str | None]]] = []

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        document = json.loads(raw)
        assert isinstance(document, dict)
        type(self).calls.append((self.path, self.headers.get("Authorization")))

        if self.path == "/slow":
            time.sleep(0.1)
        if self.path == "/bad":
            payload = {"unexpected": True}
        elif "audio_b64" in document:
            payload = {"text": "bounded transcript", "final": True, "confidence": 0.8}
        else:
            payload = {
                "audio_b64": base64.b64encode(b"\x00\x00" * 160).decode("ascii"),
                "sample_rate_hz": 16000,
                "channel_count": 1,
            }
        encoded = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            pass


@contextmanager
def _server() -> Iterator[str]:
    _Handler.calls.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def _frame() -> AudioFrame:
    return AudioFrame(
        stream_id=AudioStreamId.create(),
        format=AudioFormat.PCM_S16LE,
        sample_rate_hz=16000,
        channel_count=1,
        sequence=0,
        timestamp=datetime.now(UTC),
        payload=b"\x01\x00" * 160,
    )


def _config(base: str, path: str, *, timeout: float = 1.0) -> HttpSpeechConfig:
    return HttpSpeechConfig(
        provider_id=_PROVIDER,
        endpoint=f"{base}{path}",
        credential=_SECRET,
        timeout_seconds=timeout,
        allow_loopback_http=True,
    )


def test_http_stt_and_tts_use_real_bounded_transport_and_secret_resolver() -> None:
    with _server() as base:
        resolver = _Resolver()
        cancellation = CancellationSource()
        session = SpeechSessionId.create()
        stt = HttpSpeechToTextProvider(_config(base, "/stt"), secrets=resolver)
        transcript = stt.transcribe(
            SttRequest(
                session_id=session,
                frames=(_frame(),),
                cancellation_token=cancellation.token,
            )
        ).unwrap()
        assert transcript.text == "bounded transcript"
        assert transcript.is_final

        tts = HttpTextToSpeechProvider(_config(base, "/tts"), secrets=resolver)
        response = tts.synthesize(
            TtsRequest(
                session_id=session,
                text="bounded response",
                cancellation_token=cancellation.token,
            )
        ).unwrap()
        assert response.frames[0].sample_rate_hz == 16000
        assert response.frames[0].payload == b"\x00\x00" * 160
        assert [authorization for _path, authorization in _Handler.calls] == [
            "Bearer test-secret-material",
            "Bearer test-secret-material",
        ]


def test_cancelled_speech_call_never_reaches_provider() -> None:
    with _server() as base:
        cancellation = CancellationSource()
        cancellation.request_cancellation("test")
        stt = HttpSpeechToTextProvider(_config(base, "/stt"), secrets=_Resolver())
        result = stt.transcribe(
            SttRequest(
                session_id=SpeechSessionId.create(),
                frames=(_frame(),),
                cancellation_token=cancellation.token,
            )
        )
        assert result.is_failure
        assert result.unwrap_error().details["speech_failure_kind"] == SpeechFailureKind.CANCELLED
        assert _Handler.calls == []


def test_malformed_and_timeout_provider_responses_fail_typed_and_closed() -> None:
    with _server() as base:
        cancellation = CancellationSource()
        bad = HttpSpeechToTextProvider(_config(base, "/bad"), secrets=_Resolver())
        malformed = bad.transcribe(
            SttRequest(
                session_id=SpeechSessionId.create(),
                frames=(_frame(),),
                cancellation_token=cancellation.token,
            )
        )
        assert malformed.is_failure
        assert (
            malformed.unwrap_error().details["speech_failure_kind"]
            == SpeechFailureKind.PROVIDER_RESPONSE
        )

        slow = HttpSpeechToTextProvider(
            _config(base, "/slow", timeout=0.01),
            secrets=_Resolver(),
        )
        timeout = slow.transcribe(
            SttRequest(
                session_id=SpeechSessionId.create(),
                frames=(_frame(),),
                cancellation_token=CancellationSource().token,
            )
        )
        assert timeout.is_failure
        assert timeout.unwrap_error().details["speech_failure_kind"] in {
            SpeechFailureKind.TIMEOUT,
            SpeechFailureKind.UNAVAILABLE,
        }
