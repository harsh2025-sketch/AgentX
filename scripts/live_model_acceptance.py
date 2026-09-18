"""Manual live-provider acceptance entry point for M8.

This script is intentionally not part of default CI because real-provider
credentials are an external deployment dependency. It fails closed unless all
configuration is explicitly supplied, rejects loopback/non-HTTPS endpoints,
resolves the credential only through the canonical SecretResolver boundary,
performs exactly one bounded provider call, and emits sanitized JSON evidence.

Environment:
  AGENTX_LIVE_PROVIDER_ID
  AGENTX_LIVE_MODEL_ID
  AGENTX_LIVE_MODEL_ENDPOINT
  AGENTX_LIVE_MODEL_API_KEY

No secret or model response text is printed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from urllib.parse import urlsplit

from agentx.cognition.http_model_provider import HttpModelConfig, HttpModelProvider
from agentx.cognition.model_provider import ModelId, ModelRequest, ProviderId, TextContent
from agentx.kernel.secrets import SecretRef, SecretValue

_SECRET_REF = SecretRef("models/live-acceptance")
_REQUIRED_ENV = (
    "AGENTX_LIVE_PROVIDER_ID",
    "AGENTX_LIVE_MODEL_ID",
    "AGENTX_LIVE_MODEL_ENDPOINT",
    "AGENTX_LIVE_MODEL_API_KEY",
)


class _EnvironmentResolver:
    """Trusted composition resolver for this explicit acceptance process only."""

    __slots__ = ()

    def resolve(self, reference: SecretRef, /) -> SecretValue:
        if reference != _SECRET_REF:
            raise KeyError("unknown live-acceptance secret reference")
        material = os.environ.get("AGENTX_LIVE_MODEL_API_KEY")
        if material is None or not material:
            raise RuntimeError("live model credential is unavailable")
        return SecretValue(material)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"required environment variable {name} is unavailable")
    return value.strip()


def main() -> int:
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        sys.stdout.write(
            json.dumps(
                {
                    "accepted": False,
                    "reason": "missing_environment",
                    "missing": missing,
                },
                sort_keys=True,
            )
            + "\n"
        )
        return 2

    provider_id = _required("AGENTX_LIVE_PROVIDER_ID")
    model_value = _required("AGENTX_LIVE_MODEL_ID")
    endpoint = _required("AGENTX_LIVE_MODEL_ENDPOINT")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or parsed.hostname in {"127.0.0.1", "::1", "localhost"}:
        sys.stdout.write(
            json.dumps(
                {
                    "accepted": False,
                    "reason": "live acceptance requires a non-loopback HTTPS endpoint",
                },
                sort_keys=True,
            )
            + "\n"
        )
        return 2

    model_id = ModelId(ProviderId(provider_id), model_value)
    provider = HttpModelProvider(
        HttpModelConfig(
            model_id=model_id,
            endpoint=endpoint,
            credential=_SECRET_REF,
            timeout_seconds=30,
            max_output_tokens=64,
            max_request_bytes=32_768,
            max_response_bytes=262_144,
        ),
        secrets=_EnvironmentResolver(),
    )
    prompt = (
        "Return one short plain-text sentence confirming that this is a bounded "
        "AgentX live-provider acceptance request. Do not call tools."
    )
    requested_at = datetime.now(UTC)
    result = provider.invoke(
        ModelRequest(
            model_id=model_id,
            content=(TextContent(prompt),),
            max_output_tokens=64,
        )
    )
    completed_at = datetime.now(UTC)
    if result.is_failure:
        error = result.unwrap_error()
        sys.stdout.write(
            json.dumps(
                {
                    "accepted": False,
                    "provider": provider_id,
                    "model": model_value,
                    "requested_at": requested_at.isoformat(),
                    "completed_at": completed_at.isoformat(),
                    "error_code": error.code,
                },
                sort_keys=True,
            )
            + "\n"
        )
        return 1

    response = result.unwrap()
    text = "".join(item.text for item in response.content)
    usage = response.usage
    evidence = {
        "accepted": True,
        "provider": provider_id,
        "model": model_value,
        "requested_at": requested_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "request_max_output_tokens": 64,
        "model_calls": 1,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "latency_seconds": (
            None if usage.latency is None else usage.latency.total_seconds()
        ),
        "external_cost": None if usage.external_cost is None else str(usage.external_cost),
        "response_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "response_text_exposed": False,
        "credential_exposed": False,
    }
    sys.stdout.write(json.dumps(evidence, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
