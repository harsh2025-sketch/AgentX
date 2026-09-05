"""Minimal Reasoner boundary for explicitly configured cognitive model work.

A2.03 defines one deterministic orchestration boundary above A2.01/A2.02:
resolve the explicitly configured REASONING binding, verify that a supplied
ModelProvider exposes that exact provider-qualified model, observe canonical
ExecutionContext stop state, invoke the provider exactly once, and wrap the
successful cognitive response as inert data.

The Reasoner is not an authority boundary, execution router, planner, verifier,
Task manager, provider registry, retry engine, prompt framework, tool caller, or
capability executor. Model output remains untrusted cognitive data regardless
of its text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from agentx.cognition.model_provider import (
    ModelId,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderDescriptor,
    TextContent,
)
from agentx.cognition.model_roles import (
    ModelRole,
    ModelRoleBinding,
    ModelRoleBindings,
    model_role_definition,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext, MonotonicClock
from agentx.core.ids import TaskId
from agentx.core.result import Result

__all__ = [
    "Reasoner",
    "ReasonerRequest",
    "ReasonerResult",
]

_MAX_COUNTER: Final[int] = (1 << 63) - 1


def _validate_optional_output_limit(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError("max_output_tokens must be an int or None")
    if value <= 0:
        raise ValueError("max_output_tokens must be greater than zero when provided")
    if value > _MAX_COUNTER:
        raise OverflowError("max_output_tokens exceeds the supported counter range")
    return value


def _reasoner_error(
    *,
    code: str,
    message: str,
    category: ErrorCategory,
    retryability: Retryability = Retryability.NON_RETRYABLE,
    details: dict[str, object] | None = None,
) -> AgentXError:
    return AgentXError(
        code=code,
        message=message,
        category=category,
        retryability=retryability,
        details=details,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ReasonerRequest:
    """Immutable input for one reasoning operation.

    ``execution_context`` is the canonical A1.07 correlation/cancellation/
    deadline contract. ``instruction`` is inert A2.01 text content. The request
    intentionally contains no authority, risk, budget, Task object, tool,
    capability, provider-selection hint, metadata bag, or prompt framework.
    """

    execution_context: ExecutionContext
    instruction: TextContent
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.execution_context, ExecutionContext):
            raise TypeError("execution_context must be an ExecutionContext")
        if not isinstance(self.instruction, TextContent):
            raise TypeError("instruction must be TextContent")
        _validate_optional_output_limit(self.max_output_tokens)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReasonerResult:
    """Immutable cognitive output correlated to one execution context.

    The wrapped :class:`ModelResponse` remains provider/model output data. This
    type deliberately has no verification, authority, permission, execution,
    risk, budget, Task-transition, or machine-success field.
    """

    correlation_id: UUID
    task_id: TaskId | None
    response: ModelResponse

    def __post_init__(self) -> None:
        if not isinstance(self.correlation_id, UUID):
            raise TypeError("correlation_id must be a UUID")
        if self.correlation_id.int == 0:
            raise ValueError("correlation_id must not be the nil UUID")
        if self.task_id is not None and not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be a TaskId or None")
        if not isinstance(self.response, ModelResponse):
            raise TypeError("response must be a ModelResponse")

    @property
    def model_id(self) -> ModelId:
        """Return the exact provider-qualified model identity used."""

        return self.response.model_id

    @property
    def content(self) -> tuple[TextContent, ...]:
        """Return inert model-generated content without interpreting it."""

        return self.response.content

    @property
    def usage(self) -> ModelUsage:
        """Return descriptive A2.01 usage metadata."""

        return self.response.usage


class Reasoner:
    """Deterministic one-call runtime boundary for the REASONING model role.

    The supplied ``ModelRoleBindings`` remain the only model-role configuration
    source. Only ``ModelRole.REASONING`` is resolved; no other role is ever used
    as fallback. The supplied provider must explicitly expose the bound model.
    A provider failure is propagated unchanged and is never converted into
    fabricated cognitive output.
    """

    __slots__ = ("_bindings", "_clock", "_provider")

    def __init__(
        self,
        *,
        bindings: ModelRoleBindings,
        provider: ModelProvider,
        clock: MonotonicClock | None = None,
    ) -> None:
        if not isinstance(bindings, ModelRoleBindings):
            raise TypeError("bindings must be ModelRoleBindings")
        if not isinstance(provider, ModelProvider):
            raise TypeError("provider must satisfy ModelProvider")
        self._bindings = bindings
        self._provider = provider
        self._clock = clock

    @property
    def reasoning_binding(self) -> ModelRoleBinding | None:
        """Return the explicit REASONING binding, or ``None`` when unconfigured."""

        return self._bindings.for_role(ModelRole.REASONING)

    def reason(self, request: ReasonerRequest) -> Result[ReasonerResult, AgentXError]:
        """Perform one bounded reasoning invocation with no retry or fallback."""

        raw_request: object = request
        if not isinstance(raw_request, ReasonerRequest):
            return Result[ReasonerResult, AgentXError].failure(
                _reasoner_error(
                    code="reasoner.invalid_request",
                    message="reasoner request must be a ReasonerRequest",
                    category=ErrorCategory.VALIDATION,
                )
            )
        request = raw_request

        binding = self.reasoning_binding
        if binding is None:
            return Result[ReasonerResult, AgentXError].failure(
                _reasoner_error(
                    code="reasoner.binding_missing",
                    message="no explicit REASONING model binding is configured",
                    category=ErrorCategory.PRECONDITION,
                )
            )

        if binding.role is not ModelRole.REASONING:
            return Result[ReasonerResult, AgentXError].failure(
                _reasoner_error(
                    code="reasoner.binding_invalid",
                    message="Reasoner requires a REASONING model-role binding",
                    category=ErrorCategory.PRECONDITION,
                )
            )

        if not model_role_definition(ModelRole.REASONING).is_compatible(binding.model):
            return Result[ReasonerResult, AgentXError].failure(
                _reasoner_error(
                    code="reasoner.binding_incompatible",
                    message="configured REASONING model no longer satisfies role requirements",
                    category=ErrorCategory.PRECONDITION,
                    details={"model_id": str(binding.model_id)},
                )
            )

        raw_descriptor: object = self._provider.descriptor
        provider_error = self._provider_mismatch(binding, raw_descriptor)
        if provider_error is not None:
            return Result[ReasonerResult, AgentXError].failure(provider_error)

        stop = request.execution_context.observe_stop(clock=self._clock)
        if stop.cancellation_requested:
            details: dict[str, object] = {
                "correlation_id": str(request.execution_context.correlation_id),
                "deadline_status": stop.deadline_status.value,
            }
            if request.execution_context.task_id is not None:
                details["task_id"] = str(request.execution_context.task_id)
            if stop.cancellation_reason is not None:
                details["cancellation_reason"] = stop.cancellation_reason
            return Result[ReasonerResult, AgentXError].failure(
                _reasoner_error(
                    code="reasoner.cancelled",
                    message="reasoning was cancelled before provider invocation",
                    category=ErrorCategory.CANCELLED,
                    details=details,
                )
            )

        if stop.timed_out:
            details = {
                "correlation_id": str(request.execution_context.correlation_id),
                "deadline_status": stop.deadline_status.value,
            }
            if request.execution_context.task_id is not None:
                details["task_id"] = str(request.execution_context.task_id)
            return Result[ReasonerResult, AgentXError].failure(
                _reasoner_error(
                    code="reasoner.timeout",
                    message="reasoning deadline expired before provider invocation",
                    category=ErrorCategory.TIMEOUT,
                    details=details,
                )
            )

        model_request = ModelRequest(
            model_id=binding.model_id,
            content=(request.instruction,),
            max_output_tokens=request.max_output_tokens,
        )

        raw_provider_result: object = self._provider.invoke(model_request)
        if not isinstance(raw_provider_result, Result):
            return Result[ReasonerResult, AgentXError].failure(
                self._invalid_provider_response(
                    binding,
                    "provider returned a non-Result outcome",
                )
            )
        provider_result = raw_provider_result

        if provider_result.is_failure:
            provider_failure = provider_result.unwrap_error()
            if isinstance(provider_failure, AgentXError):
                return Result[ReasonerResult, AgentXError].failure(provider_failure)
            return Result[ReasonerResult, AgentXError].failure(
                self._invalid_provider_response(
                    binding,
                    "provider failure did not contain AgentXError",
                )
            )

        response = provider_result.unwrap()
        if not isinstance(response, ModelResponse):
            return Result[ReasonerResult, AgentXError].failure(
                self._invalid_provider_response(
                    binding,
                    "provider success did not contain ModelResponse",
                )
            )
        if response.model_id != binding.model_id:
            return Result[ReasonerResult, AgentXError].failure(
                _reasoner_error(
                    code="reasoner.provider_response_model_mismatch",
                    message="provider response model does not match configured REASONING model",
                    category=ErrorCategory.DEPENDENCY,
                    retryability=Retryability.UNKNOWN,
                    details={
                        "configured_model_id": str(binding.model_id),
                        "response_model_id": str(response.model_id),
                    },
                )
            )

        return Result[ReasonerResult, AgentXError].success(
            ReasonerResult(
                correlation_id=request.execution_context.correlation_id,
                task_id=request.execution_context.task_id,
                response=response,
            )
        )

    @staticmethod
    def _provider_mismatch(
        binding: ModelRoleBinding,
        descriptor: object,
    ) -> AgentXError | None:
        if not isinstance(descriptor, ProviderDescriptor):
            return _reasoner_error(
                code="reasoner.provider_mismatch",
                message="provider descriptor is malformed",
                category=ErrorCategory.PRECONDITION,
            )
        if descriptor.provider_id != binding.model_id.provider_id:
            return _reasoner_error(
                code="reasoner.provider_mismatch",
                message="configured REASONING model belongs to a different provider",
                category=ErrorCategory.PRECONDITION,
                details={
                    "configured_provider_id": binding.model_id.provider_id.value,
                    "supplied_provider_id": descriptor.provider_id.value,
                },
            )
        if binding.model_id not in {model.model_id for model in descriptor.models}:
            return _reasoner_error(
                code="reasoner.provider_mismatch",
                message="supplied provider does not expose the configured REASONING model",
                category=ErrorCategory.PRECONDITION,
                details={"model_id": str(binding.model_id)},
            )
        return None

    @staticmethod
    def _invalid_provider_response(binding: ModelRoleBinding, message: str) -> AgentXError:
        return _reasoner_error(
            code="reasoner.provider_response_invalid",
            message=message,
            category=ErrorCategory.DEPENDENCY,
            retryability=Retryability.UNKNOWN,
            details={"model_id": str(binding.model_id)},
        )
