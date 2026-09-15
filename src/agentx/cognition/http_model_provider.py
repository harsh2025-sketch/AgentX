"""Bounded text-only Chat Completions adapter for the canonical ModelProvider.

Endpoint/model/secret configuration is supplied by trusted composition. Requests
and responses cannot change it. No tool execution, retries, redirects, ambient
credential discovery, or provider selection occurs here.
"""

from __future__ import annotations

import http.client
import json
import math
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal, cast
from urllib.parse import urlsplit

from agentx.cognition.model_provider import (
    ModelCapability,
    ModelDescriptor,
    ModelId,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderDescriptor,
    ProviderFailureKind,
    TextContent,
    provider_failure,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import CancellationToken
from agentx.core.result import Result
from agentx.kernel.secrets import SecretRef, SecretResolver


@dataclass(frozen=True, slots=True, kw_only=True)
class HttpModelConfig:
    """Explicit deployment configuration; no credentials or inferred defaults.

    Use a pinned model ID: the response must report that exact ID. Plain HTTP
    is restricted to explicitly enabled loopback development servers.
    """

    model_id: ModelId
    endpoint: str
    credential: SecretRef | None = None
    timeout_seconds: float = 30.0
    max_request_bytes: int = 262144
    max_response_bytes: int = 1048576
    max_output_tokens: int = 4096
    output_limit_field: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    allow_loopback_http: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, ModelId):
            raise TypeError("model_id must be a ModelId")
        if self.credential is not None and not isinstance(self.credential, SecretRef):
            raise TypeError("credential must be a SecretRef or None")
        if type(self.allow_loopback_http) is not bool:
            raise TypeError("allow_loopback_http must be bool")
        if not isinstance(self.endpoint, str) or not self.endpoint:
            raise ValueError("endpoint must be a non-empty URL")
        if any(ord(c) <= 32 or ord(c) >= 127 for c in self.endpoint):
            raise ValueError("endpoint must contain only printable ASCII without spaces")
        parsed = urlsplit(self.endpoint)
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/")
        ):
            raise ValueError("endpoint must have a host/path and no credentials, query or fragment")
        if parsed.port is not None and parsed.port == 0:
            raise ValueError("endpoint port must be positive")
        local_http = (
            self.allow_loopback_http
            and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "::1"}
        )
        if parsed.scheme != "https" and not local_http:
            raise ValueError("HTTPS required except for explicitly enabled literal loopback HTTP")
        if type(self.timeout_seconds) not in (float, int):
            raise TypeError("timeout_seconds must be numeric")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be finite and in (0, 120]")
        for name, value, ceiling in (
            ("max_request_bytes", self.max_request_bytes, 16777216),
            ("max_response_bytes", self.max_response_bytes, 16777216),
            ("max_output_tokens", self.max_output_tokens, 1048576),
        ):
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError(f"{name} must be a positive bounded integer")
        if self.output_limit_field not in {"max_completion_tokens", "max_tokens"}:
            raise ValueError("unsupported output limit field")


class _Cancelled(Exception):
    pass


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("expected JSON object")
    return cast(dict[str, object], value)


def _counter(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError("usage counters must be integers")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class HttpModelProvider:
    """One HTTP request per invocation, through the existing cognition boundary.

    Socket operations have finite timeouts. Cancellation/deadlines are checked
    before sending and between response chunks, and late results are discarded.
    Blocking DNS/OS calls cannot be forcibly preempted by this synchronous API.
    """

    def __init__(
        self,
        config: HttpModelConfig,
        *,
        secrets: SecretResolver | None = None,
        cancellation: CancellationToken | None = None,
    ) -> None:
        if not isinstance(config, HttpModelConfig):
            raise TypeError("config must be HttpModelConfig")
        if config.credential is not None and secrets is None:
            raise ValueError("credential configuration requires a SecretResolver")
        if cancellation is not None and not isinstance(cancellation, CancellationToken):
            raise TypeError("cancellation must be a CancellationToken")
        self._config = config
        self._secrets = secrets
        self._cancellation = cancellation
        capabilities = frozenset(
            {ModelCapability("content.text.input"), ModelCapability("content.text.output")}
        )
        self._descriptor = ProviderDescriptor(
            provider_id=config.model_id.provider_id,
            capabilities=capabilities,
            models=(ModelDescriptor(model_id=config.model_id, capabilities=capabilities),),
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    def _failure(self, kind: ProviderFailureKind) -> Result[ModelResponse, AgentXError]:
        # Never include exception, response body, headers, prompt or credentials.
        return Result.failure(
            provider_failure(
                kind,
                provider_id=self._config.model_id.provider_id,
                model_id=self._config.model_id,
                message=f"HTTP model invocation failed: {kind.value}",
            )
        )

    def _remaining(self, deadline: float) -> float:
        if self._cancellation is not None and self._cancellation.is_cancelled:
            raise _Cancelled
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    def invoke(self, request: ModelRequest) -> Result[ModelResponse, AgentXError]:
        config = self._config
        if not isinstance(request, ModelRequest) or request.model_id != config.model_id:
            return self._failure(ProviderFailureKind.INVALID_REQUEST)
        limit = request.max_output_tokens or config.max_output_tokens
        if limit > config.max_output_tokens:
            return self._failure(ProviderFailureKind.RESOURCE_LIMITED)
        # Bound the input before JSON allocation as well as the encoded payload.
        if sum(len(item.text) for item in request.content) > config.max_request_bytes:
            return self._failure(ProviderFailureKind.RESOURCE_LIMITED)
        payload = json.dumps(
            {
                "model": request.model_id.value,
                "messages": [{"role": "user", "content": item.text} for item in request.content],
                config.output_limit_field: limit,
                "stream": False,
                "n": 1,
            },
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
        if len(payload) > config.max_request_bytes:
            return self._failure(ProviderFailureKind.RESOURCE_LIMITED)
        started = time.monotonic()
        deadline = started + config.timeout_seconds
        connection: http.client.HTTPConnection | None = None
        try:
            self._remaining(deadline)
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            if config.credential is not None:
                try:
                    assert self._secrets is not None
                    material = self._secrets.resolve(config.credential).reveal()
                    if isinstance(material, bytes):
                        material = material.decode("ascii")
                    if any(ord(c) <= 32 or ord(c) >= 127 for c in material):
                        return self._failure(ProviderFailureKind.AUTHENTICATION)
                    headers["Authorization"] = "Bearer " + material
                except Exception:
                    return self._failure(ProviderFailureKind.AUTHENTICATION)
            parsed = urlsplit(config.endpoint)
            assert parsed.hostname is not None
            connection_type = (
                http.client.HTTPSConnection
                if parsed.scheme == "https"
                else http.client.HTTPConnection
            )
            connection = connection_type(
                parsed.hostname, parsed.port, timeout=self._remaining(deadline)
            )
            connection.request("POST", parsed.path, body=payload, headers=headers)
            self._remaining(deadline)
            if connection.sock is not None:
                connection.sock.settimeout(self._remaining(deadline))
            response = connection.getresponse()
            # http.client never follows redirects or retries, including auth failures.
            if response.status != 200:
                return self._failure(self._http_failure(response.status))
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                return self._failure(ProviderFailureKind.INTERNAL)
            media_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                return self._failure(ProviderFailureKind.INTERNAL)
            chunks: list[bytes] = []
            total = 0
            while True:
                remaining = self._remaining(deadline)
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(65536, config.max_response_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > config.max_response_bytes:
                    return self._failure(ProviderFailureKind.RESOURCE_LIMITED)
                chunks.append(chunk)
            self._remaining(deadline)
            result = self._decode(b"".join(chunks), time.monotonic() - started)
            self._remaining(deadline)
            return Result.success(result)
        except _Cancelled:
            return Result.failure(
                AgentXError(
                    code="model_provider.cancelled",
                    message="HTTP model invocation cancelled",
                    category=ErrorCategory.CANCELLED,
                    retryability=Retryability.NON_RETRYABLE,
                )
            )
        except TimeoutError:
            return self._failure(ProviderFailureKind.TIMEOUT)
        except (OSError, http.client.HTTPException):
            return self._failure(ProviderFailureKind.UNAVAILABLE)
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            return self._failure(ProviderFailureKind.INTERNAL)
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _http_failure(status: int) -> ProviderFailureKind:
        if status in {401, 403}:
            return ProviderFailureKind.AUTHENTICATION
        if status == 429:
            return ProviderFailureKind.RESOURCE_LIMITED
        if status in {408, 504}:
            return ProviderFailureKind.TIMEOUT
        if status >= 500:
            return ProviderFailureKind.UNAVAILABLE
        if 300 <= status < 400:
            return ProviderFailureKind.CONFIGURATION
        return ProviderFailureKind.INVALID_REQUEST

    def _decode(self, raw: bytes, elapsed: float) -> ModelResponse:
        document = _mapping(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object))
        if document.get("model") != self._config.model_id.value:
            raise ValueError("response model differs from the configured pinned model")
        choices = document.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("expected exactly one completion")
        choice = _mapping(choices[0])
        if choice.get("finish_reason") != "stop":
            raise ValueError("incomplete or non-text completion")
        message = _mapping(choice.get("message"))
        if message.get("role") != "assistant" or message.get("tool_calls"):
            raise ValueError("expected assistant text without tool calls")
        if message.get("function_call") or message.get("refusal"):
            raise ValueError("non-text completion")
        text = message.get("content")
        if not isinstance(text, str):
            raise ValueError("expected text content")
        reported_usage = document.get("usage")
        usage = {} if reported_usage is None else _mapping(reported_usage)
        return ModelResponse(
            model_id=self._config.model_id,
            content=(TextContent(text),),
            usage=ModelUsage(
                input_tokens=_counter(usage.get("prompt_tokens")),
                output_tokens=_counter(usage.get("completion_tokens")),
                total_tokens=_counter(usage.get("total_tokens")),
                latency=timedelta(seconds=elapsed),
            ),
        )
