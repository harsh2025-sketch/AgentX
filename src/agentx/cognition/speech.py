"""Provider-neutral speech contracts and bounded HTTPS STT/TTS adapters for M11.

Speech provider output is untrusted data. These contracts carry no permission,
authority, risk, budget, capability, verification, or executable surface.
"""

from __future__ import annotations

import base64
import http.client
import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, cast, runtime_checkable
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from agentx.core.audio import AudioFormat, AudioFrame
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import CancellationToken
from agentx.core.ids import AudioStreamId
from agentx.core.result import Result
from agentx.kernel.secrets import SecretRef, SecretResolver

__all__ = [
    "HttpSpeechConfig",
    "HttpSpeechToTextProvider",
    "HttpTextToSpeechProvider",
    "SpeechFailureKind",
    "SpeechProviderId",
    "SpeechSessionId",
    "SpeechToTextProvider",
    "SttRequest",
    "SttTranscript",
    "TextToSpeechProvider",
    "TtsRequest",
    "TtsResponse",
    "speech_failure",
]

_MAX_TEXT = 64_000
_MAX_AUDIO_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True, order=True)
class SpeechProviderId:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value or self.value != self.value.strip():
            raise ValueError("speech provider id must be non-empty trimmed text")
        if len(self.value) > 128 or any(ord(ch) < 33 or ord(ch) > 126 for ch in self.value):
            raise ValueError("speech provider id must be bounded printable ASCII")


@dataclass(frozen=True, slots=True, order=True)
class SpeechSessionId:
    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID) or self.value.int == 0:
            raise ValueError("speech session id must be a non-nil UUID")

    @classmethod
    def create(cls) -> SpeechSessionId:
        return cls(uuid4())


class SpeechFailureKind(StrEnum):
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION = "authentication"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    RESOURCE_LIMITED = "resource_limited"
    PROVIDER_RESPONSE = "provider_response"
    CONFIGURATION = "configuration"


def speech_failure(kind: SpeechFailureKind, message: str) -> AgentXError:
    policy = {
        SpeechFailureKind.INVALID_REQUEST: (ErrorCategory.VALIDATION, Retryability.NON_RETRYABLE),
        SpeechFailureKind.AUTHENTICATION: (ErrorCategory.PERMISSION, Retryability.NON_RETRYABLE),
        SpeechFailureKind.UNAVAILABLE: (ErrorCategory.DEPENDENCY, Retryability.RETRYABLE),
        SpeechFailureKind.TIMEOUT: (ErrorCategory.TIMEOUT, Retryability.RETRYABLE),
        SpeechFailureKind.CANCELLED: (ErrorCategory.CANCELLED, Retryability.NON_RETRYABLE),
        SpeechFailureKind.RESOURCE_LIMITED: (ErrorCategory.RESOURCE, Retryability.NON_RETRYABLE),
        SpeechFailureKind.PROVIDER_RESPONSE: (ErrorCategory.DEPENDENCY, Retryability.UNKNOWN),
        SpeechFailureKind.CONFIGURATION: (ErrorCategory.PRECONDITION, Retryability.NON_RETRYABLE),
    }
    category, retryability = policy[kind]
    return AgentXError(
        code=f"speech.{kind.value}",
        message=message,
        category=category,
        retryability=retryability,
        details={"speech_failure_kind": kind.value},
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class SttRequest:
    session_id: SpeechSessionId
    frames: tuple[AudioFrame, ...]
    cancellation_token: CancellationToken
    language: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, SpeechSessionId):
            raise TypeError("session_id must be SpeechSessionId")
        if not isinstance(self.frames, tuple) or not self.frames:
            raise ValueError("frames must be a non-empty tuple")
        if any(not isinstance(frame, AudioFrame) for frame in self.frames):
            raise TypeError("frames must contain only AudioFrame values")
        if not isinstance(self.cancellation_token, CancellationToken):
            raise TypeError("cancellation_token must be CancellationToken")
        first = self.frames[0]
        if any(frame.stream_id != first.stream_id for frame in self.frames):
            raise ValueError("all STT frames must belong to one audio stream")
        if sum(frame.byte_count for frame in self.frames) > _MAX_AUDIO_BYTES:
            raise ValueError("STT audio exceeds the canonical request bound")
        if self.language is not None and (
            not isinstance(self.language, str)
            or not self.language.strip()
            or len(self.language) > 32
        ):
            raise ValueError("language must be bounded non-empty text or None")


@dataclass(frozen=True, slots=True, kw_only=True)
class SttTranscript:
    session_id: SpeechSessionId
    text: str
    is_final: bool
    provider_id: SpeechProviderId
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, SpeechSessionId):
            raise TypeError("session_id must be SpeechSessionId")
        if not isinstance(self.text, str) or not self.text.strip() or len(self.text) > _MAX_TEXT:
            raise ValueError("transcript text must be bounded non-empty text")
        if type(self.is_final) is not bool:
            raise TypeError("is_final must be bool")
        if not isinstance(self.provider_id, SpeechProviderId):
            raise TypeError("provider_id must be SpeechProviderId")
        if self.confidence is not None:
            if not isinstance(self.confidence, int | float):
                raise TypeError("confidence must be numeric or None")
            if not math.isfinite(float(self.confidence)) or not 0.0 <= self.confidence <= 1.0:
                raise ValueError("confidence must be finite in [0, 1]")


@dataclass(frozen=True, slots=True, kw_only=True)
class TtsRequest:
    session_id: SpeechSessionId
    text: str
    cancellation_token: CancellationToken
    voice: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, SpeechSessionId):
            raise TypeError("session_id must be SpeechSessionId")
        if not isinstance(self.text, str) or not self.text.strip() or len(self.text) > _MAX_TEXT:
            raise ValueError("TTS text must be bounded non-empty text")
        if not isinstance(self.cancellation_token, CancellationToken):
            raise TypeError("cancellation_token must be CancellationToken")
        if self.voice is not None and (
            not isinstance(self.voice, str) or not self.voice.strip() or len(self.voice) > 128
        ):
            raise ValueError("voice must be bounded non-empty text or None")


@dataclass(frozen=True, slots=True, kw_only=True)
class TtsResponse:
    session_id: SpeechSessionId
    frames: tuple[AudioFrame, ...]
    provider_id: SpeechProviderId

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, SpeechSessionId):
            raise TypeError("session_id must be SpeechSessionId")
        if not isinstance(self.frames, tuple) or not self.frames:
            raise ValueError("frames must be a non-empty tuple")
        if any(not isinstance(frame, AudioFrame) for frame in self.frames):
            raise TypeError("frames must contain only AudioFrame values")
        if not isinstance(self.provider_id, SpeechProviderId):
            raise TypeError("provider_id must be SpeechProviderId")


@runtime_checkable
class SpeechToTextProvider(Protocol):
    @property
    def provider_id(self) -> SpeechProviderId: ...

    def transcribe(self, request: SttRequest) -> Result[SttTranscript, AgentXError]: ...


@runtime_checkable
class TextToSpeechProvider(Protocol):
    @property
    def provider_id(self) -> SpeechProviderId: ...

    def synthesize(self, request: TtsRequest) -> Result[TtsResponse, AgentXError]: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class HttpSpeechConfig:
    provider_id: SpeechProviderId
    endpoint: str
    credential: SecretRef | None = None
    timeout_seconds: float = 30.0
    max_request_bytes: int = _MAX_AUDIO_BYTES
    max_response_bytes: int = _MAX_AUDIO_BYTES
    allow_loopback_http: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, SpeechProviderId):
            raise TypeError("provider_id must be SpeechProviderId")
        if self.credential is not None and not isinstance(self.credential, SecretRef):
            raise TypeError("credential must be SecretRef or None")
        parsed = urlsplit(self.endpoint)
        local_http = (
            self.allow_loopback_http
            and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "::1"}
        )
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
        ):
            raise ValueError("endpoint must have host/path and no embedded credentials")
        if parsed.scheme != "https" and not local_http:
            raise ValueError("HTTPS required except for explicitly enabled loopback HTTP")
        if not isinstance(self.timeout_seconds, int | float):
            raise TypeError("timeout_seconds must be numeric")
        if not math.isfinite(float(self.timeout_seconds)) or not 0 < self.timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be finite in (0, 120]")
        for name, value in (
            ("max_request_bytes", self.max_request_bytes),
            ("max_response_bytes", self.max_response_bytes),
        ):
            if type(value) is not int or not 1024 <= value <= 16 * 1024 * 1024:
                raise ValueError(f"{name} must be a bounded positive integer")


class _CancelledError(Exception):
    pass


class _HttpSpeechAdapter:
    def __init__(
        self,
        config: HttpSpeechConfig,
        *,
        secrets: SecretResolver | None = None,
    ) -> None:
        if not isinstance(config, HttpSpeechConfig):
            raise TypeError("config must be HttpSpeechConfig")
        if config.credential is not None and secrets is None:
            raise ValueError("credential configuration requires SecretResolver")
        self._config = config
        self._secrets = secrets

    @property
    def provider_id(self) -> SpeechProviderId:
        return self._config.provider_id

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._config.credential is None:
            return headers
        assert self._secrets is not None
        material = self._secrets.resolve(self._config.credential).reveal()
        if isinstance(material, bytes):
            material = material.decode("ascii")
        if any(ord(ch) <= 32 or ord(ch) >= 127 for ch in material):
            raise ValueError("credential material is not valid HTTP bearer text")
        headers["Authorization"] = f"Bearer {material}"
        return headers

    def _post(
        self,
        payload: dict[str, object],
        token: CancellationToken,
    ) -> Result[dict[str, object], AgentXError]:
        if token.is_cancelled:
            return Result.failure(
                speech_failure(SpeechFailureKind.CANCELLED, "speech call cancelled")
            )
        body = json.dumps(
            payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode("ascii")
        if len(body) > self._config.max_request_bytes:
            return Result.failure(
                speech_failure(
                    SpeechFailureKind.RESOURCE_LIMITED,
                    "speech request exceeds byte bound",
                )
            )
        deadline = time.monotonic() + float(self._config.timeout_seconds)
        connection: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        try:
            headers = self._headers()
            parsed = urlsplit(self._config.endpoint)
            assert parsed.hostname is not None
            connection_type = (
                http.client.HTTPSConnection
                if parsed.scheme == "https"
                else http.client.HTTPConnection
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            connection = connection_type(parsed.hostname, parsed.port, timeout=remaining)
            connection.request("POST", parsed.path, body=body, headers=headers)
            if token.is_cancelled:
                raise _CancelledError
            response = connection.getresponse()
            if response.status in {401, 403}:
                return Result.failure(
                    speech_failure(
                        SpeechFailureKind.AUTHENTICATION,
                        "speech provider authentication failed",
                    )
                )
            if response.status == 429:
                return Result.failure(
                    speech_failure(
                        SpeechFailureKind.RESOURCE_LIMITED,
                        "speech provider rate limited request",
                    )
                )
            if response.status >= 500:
                return Result.failure(
                    speech_failure(
                        SpeechFailureKind.UNAVAILABLE,
                        "speech provider is unavailable",
                    )
                )
            if response.status != 200:
                return Result.failure(
                    speech_failure(
                        SpeechFailureKind.PROVIDER_RESPONSE,
                        "speech provider rejected request",
                    )
                )
            chunks: list[bytes] = []
            total = 0
            while True:
                if token.is_cancelled:
                    raise _CancelledError
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(
                    min(65_536, self._config.max_response_bytes + 1 - total)
                )
                if not chunk:
                    break
                total += len(chunk)
                if total > self._config.max_response_bytes:
                    return Result.failure(
                        speech_failure(
                            SpeechFailureKind.RESOURCE_LIMITED,
                            "speech provider response exceeds byte bound",
                        )
                    )
                chunks.append(chunk)
            decoded = json.loads(b"".join(chunks).decode("utf-8"))
            if not isinstance(decoded, dict) or any(
                not isinstance(key, str) for key in decoded
            ):
                raise ValueError("provider response must be a JSON object")
            return Result.success(cast(dict[str, object], decoded))
        except _CancelledError:
            return Result.failure(
                speech_failure(SpeechFailureKind.CANCELLED, "speech call cancelled")
            )
        except TimeoutError:
            return Result.failure(
                speech_failure(SpeechFailureKind.TIMEOUT, "speech provider timed out")
            )
        except (OSError, http.client.HTTPException):
            return Result.failure(
                speech_failure(
                    SpeechFailureKind.UNAVAILABLE,
                    "speech provider unavailable",
                )
            )
        except (ValueError, TypeError, UnicodeError):
            return Result.failure(
                speech_failure(
                    SpeechFailureKind.PROVIDER_RESPONSE,
                    "speech provider returned invalid data",
                )
            )
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()


class HttpSpeechToTextProvider(_HttpSpeechAdapter):
    """Concrete bounded HTTPS STT provider with an explicit JSON transport."""

    def transcribe(self, request: SttRequest) -> Result[SttTranscript, AgentXError]:
        if not isinstance(request, SttRequest):
            raise TypeError("request must be SttRequest")
        first = request.frames[0]
        if any(
            frame.format != first.format
            or frame.sample_rate_hz != first.sample_rate_hz
            or frame.channel_count != first.channel_count
            for frame in request.frames
        ):
            return Result.failure(
                speech_failure(
                    SpeechFailureKind.INVALID_REQUEST,
                    "STT frames have inconsistent layout",
                )
            )
        result = self._post(
            {
                "session_id": str(request.session_id.value),
                "format": first.format.value,
                "sample_rate_hz": first.sample_rate_hz,
                "channel_count": first.channel_count,
                "audio_b64": base64.b64encode(
                    b"".join(frame.payload for frame in request.frames)
                ).decode("ascii"),
                "language": request.language,
            },
            request.cancellation_token,
        )
        if result.is_failure:
            return Result.failure(result.unwrap_error())
        data = result.unwrap()
        text = data.get("text")
        final = data.get("final", True)
        confidence = data.get("confidence")
        if not isinstance(text, str) or type(final) is not bool:
            return Result.failure(
                speech_failure(
                    SpeechFailureKind.PROVIDER_RESPONSE,
                    "STT provider response is malformed",
                )
            )
        if confidence is not None and not isinstance(confidence, int | float):
            return Result.failure(
                speech_failure(
                    SpeechFailureKind.PROVIDER_RESPONSE,
                    "STT confidence is malformed",
                )
            )
        try:
            transcript = SttTranscript(
                session_id=request.session_id,
                text=text,
                is_final=final,
                provider_id=self.provider_id,
                confidence=None if confidence is None else float(confidence),
            )
        except (TypeError, ValueError):
            return Result.failure(
                speech_failure(
                    SpeechFailureKind.PROVIDER_RESPONSE,
                    "STT provider response is invalid",
                )
            )
        if request.cancellation_token.is_cancelled:
            return Result.failure(
                speech_failure(SpeechFailureKind.CANCELLED, "speech call cancelled")
            )
        return Result.success(transcript)


class HttpTextToSpeechProvider(_HttpSpeechAdapter):
    """Concrete bounded HTTPS TTS provider returning canonical PCM audio."""

    def synthesize(self, request: TtsRequest) -> Result[TtsResponse, AgentXError]:
        if not isinstance(request, TtsRequest):
            raise TypeError("request must be TtsRequest")
        result = self._post(
            {
                "session_id": str(request.session_id.value),
                "text": request.text,
                "voice": request.voice,
                "format": AudioFormat.PCM_S16LE.value,
            },
            request.cancellation_token,
        )
        if result.is_failure:
            return Result.failure(result.unwrap_error())
        data = result.unwrap()
        encoded = data.get("audio_b64")
        sample_rate = data.get("sample_rate_hz")
        channels = data.get("channel_count")
        if (
            not isinstance(encoded, str)
            or type(sample_rate) is not int
            or type(channels) is not int
        ):
            return Result.failure(
                speech_failure(
                    SpeechFailureKind.PROVIDER_RESPONSE,
                    "TTS provider response is malformed",
                )
            )
        try:
            payload = base64.b64decode(encoded, validate=True)
            frame = AudioFrame(
                stream_id=AudioStreamId.create(),
                format=AudioFormat.PCM_S16LE,
                sample_rate_hz=sample_rate,
                channel_count=channels,
                sequence=0,
                timestamp=datetime.now(UTC),
                payload=payload,
            )
        except (ValueError, TypeError):
            return Result.failure(
                speech_failure(
                    SpeechFailureKind.PROVIDER_RESPONSE,
                    "TTS provider audio is invalid",
                )
            )
        if request.cancellation_token.is_cancelled:
            return Result.failure(
                speech_failure(SpeechFailureKind.CANCELLED, "speech call cancelled")
            )
        return Result.success(
            TtsResponse(
                session_id=request.session_id,
                frames=(frame,),
                provider_id=self.provider_id,
            )
        )
