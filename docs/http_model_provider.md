# Concrete HTTP model provider (N2.28)

`agentx.cognition.http_model_provider.HttpModelProvider` implements the existing
`ModelProvider` protocol. It composes with `Reasoner` and the configured text model
roles without adding an architecture edge or runtime dependency.

## Configuration and use

Trusted composition supplies `HttpModelConfig` with the exact Chat Completions
endpoint, provider-qualified **pinned** model ID, finite limits, and optional
`SecretRef`. For authenticated endpoints, supply the canonical `SecretResolver`.
The resolver is invoked only when a request is ready to send. No environment
variable, file, browser session, or credential store is searched automatically.

```python
from agentx.cognition.http_model_provider import HttpModelConfig, HttpModelProvider
from agentx.cognition.model_provider import ModelId, ProviderId
from agentx.kernel.secrets import SecretRef

# `resolver` is supplied by the trusted application composition.
config = HttpModelConfig(
    model_id=ModelId(ProviderId("configured-provider"), "your-pinned-model-id"),
    endpoint="https://api.openai.com/v1/chat/completions",
    credential=SecretRef("models/primary"),
    max_output_tokens=4096,
    timeout_seconds=30,
)
provider = HttpModelProvider(config, secrets=resolver)
# Supply `provider` to the existing Reasoner with its matching ModelRoleBinding.
```

Local compatible servers may use literal `127.0.0.1` or `::1` over HTTP only with
`allow_loopback_http=True`. Other deployments require HTTPS certificate
verification. Endpoints cannot contain embedded credentials, queries, fragments,
spaces or control characters. Set `output_limit_field="max_tokens"` only for
servers requiring that compatible field instead of `max_completion_tokens`.

The wire format follows the [Chat Completions API reference](https://developers.openai.com/api/reference/resources/chat).
Compatibility means the documented text subset, not every vendor extension.
Use a pinned model rather than an alias that resolves to a different returned ID.

## Boundaries

- One non-streaming POST per invocation; no retry, redirect, fallback or tool calls.
- All canonical input text is serialized as user content, without interpreting
  embedded role/authority claims.
- Only a single complete assistant text result is accepted. Truncation, refusal,
  tool/function calls, model mismatch, duplicate JSON keys, malformed content and
  inconsistent usage fail explicitly.
- Input characters and serialized request bytes are bounded before transport;
  response reading stops at the configured byte ceiling.
- HTTP status failures normalize to the existing provider error taxonomy. Error
  messages never copy the request, response body, exception text or credentials.
- Usage remains provider-reported data. Missing counters/cost remain unavailable;
  latency is measured and no price is inferred. These facts cannot widen budgets.
- Capability execution, task verification, permissions and promotion remain owned
  by their existing components. This adapter grants none of them.

## Cancellation and limitations

An optional canonical `CancellationToken` supports a task-scoped provider instance.
Cancellation is observed before sending, between response reads, and before
returning a result. Reads use finite socket timeouts and a monotonic elapsed-time
check. The synchronous canonical interface cannot forcibly interrupt DNS, secret
resolution or a blocking OS operation; do not describe this as hard real-time
cancellation. A request already sent can incur provider charges even if its result
is subsequently cancelled. There are no background threads in the adapter.

This implements a concrete transport adapter, not the missing L5 strategy, a
research-source provider, a production credential backend, or a real paid-model
acceptance result. Deployment must supply those resources explicitly. Text-only
scope means no streaming, vision, realtime audio, tools or automatic fallback.

## Verification

`tests/integration/test_http_model_provider.py` uses a real loopback HTTP server
and the canonical Reasoner. It exercises request serialization, secret resolution,
inert hostile output, usage, error sanitization, redirect/retry refusal, malformed
responses, byte/token limits, cancellation and timeout. It also checks that the
adapter imports no action, learning, Hive or procedure execution owners.

Local command (stdlib only):

```text
python -m unittest discover -s tests/integration -p test_http_model_provider.py -v
```

These tests do not establish real-model quality or the whole adaptive flywheel.
Repository-wide Windows C1.01 remains required before canonical acceptance.
