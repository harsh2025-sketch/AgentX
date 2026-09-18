"""Connect canonical model providers to per-run execution metrics.

This is observation-only composition. It never invents token/cost estimates,
retries requests, chooses models, grants authority, or changes kernel budgets.
The owning run must exclusively own its recorder until invocation returns.
"""

from __future__ import annotations

from agentx.cognition.model_provider import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderDescriptor,
)
from agentx.core.errors import AgentXError
from agentx.core.result import Result
from agentx.execution_metrics import (
    ExecutionMetricsRecorder,
    ExecutionMetricsValidationError,
    ModelCallEvent,
)


class InstrumentedModelProvider:
    """Record exactly one event per invocation of the wrapped provider.

    Success/failure results remain unchanged. Expected failures and unexpected
    exceptions count as calls with unknown usage. The recorder stores model
    identities and reported usage only, never request/response text or secrets.
    Calls rejected before entering the provider do not become model calls.
    """

    def __init__(self, *, provider: ModelProvider, recorder: ExecutionMetricsRecorder) -> None:
        if not isinstance(provider, ModelProvider):
            raise TypeError("provider must implement ModelProvider")
        if not isinstance(recorder, ExecutionMetricsRecorder):
            raise TypeError("recorder must be an ExecutionMetricsRecorder")
        self._provider = provider
        self._recorder = recorder

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._provider.descriptor

    def invoke(self, request: ModelRequest) -> Result[ModelResponse, AgentXError]:
        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")
        if not self._recorder.started or self._recorder.finished:
            raise ExecutionMetricsValidationError(
                "instrumentation requires an open metrics session"
            )
        try:
            result = self._provider.invoke(request)
        except Exception:
            # Record an actual attempted invocation, while preserving the
            # provider's programming exception and storing none of its text.
            self._recorder.record_model_call(ModelCallEvent(model_id=request.model_id))
            raise
        usage = None
        if isinstance(result, Result) and result.is_success:
            response = result.unwrap()
            if isinstance(response, ModelResponse) and response.model_id == request.model_id:
                usage = response.usage
        self._recorder.record_model_call(ModelCallEvent(model_id=request.model_id, usage=usage))
        # Validation of provider results remains at the canonical Reasoner
        # boundary; instrumentation cannot repair or bless malformed output.
        return result
