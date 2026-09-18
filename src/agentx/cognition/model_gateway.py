"""Bounded model selection, retry, fallback, health, and budget composition.

This module sits above concrete :class:`ModelProvider` implementations.  It
does not perform HTTP, discover credentials, interpret model output, or grant
authority. Provider/model selection is supplied by trusted composition through
an explicit registry and fallback policy.

Every attempted provider call:

* observes the canonical :class:`ExecutionContext` stop state first;
* reserves a caller-supplied conservative ceiling from the canonical
  :class:`ResourceBudget`;
* invokes one registered provider exactly once;
* records diagnostic provider-health evidence; and
* can retry/fail over only within explicit finite policy bounds.

Model output remains inert data.  Neither response text nor provider error text
can alter retry policy, fallback order, permissions, risk, budgets, or stop
state.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from threading import Lock
from typing import Final

from agentx.cognition.model_provider import (
    ModelCapability,
    ModelDescriptor,
    ModelId,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderDescriptor,
    ProviderFailureKind,
    ProviderId,
    provider_failure,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext, MonotonicClock
from agentx.core.result import Result
from agentx.kernel.resource_budget import (
    BudgetDecision,
    ResourceBudget,
    ResourceDelta,
    ResourceRequest,
)
from agentx.kernel.risk import RiskLevel

__all__ = [
    "BoundedModelGateway",
    "FallbackPolicy",
    "ModelAttemptReservation",
    "ModelCapabilityRegistry",
    "ProviderHealthSnapshot",
    "ProviderHealthTracker",
    "RetryPolicy",
]

_MAX_ATTEMPTS: Final[int] = 16


def _validate_count(value: object, *, name: str, minimum: int = 0) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int")
    if value < minimum or value > _MAX_ATTEMPTS:
        raise ValueError(f"{name} must be in [{minimum}, {_MAX_ATTEMPTS}]")
    return value


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Finite retry policy selected by trusted runtime composition."""

    max_attempts_per_model: int = 1
    retryable_failures: frozenset[ProviderFailureKind] = frozenset(
        {ProviderFailureKind.UNAVAILABLE, ProviderFailureKind.TIMEOUT}
    )

    def __post_init__(self) -> None:
        _validate_count(
            self.max_attempts_per_model,
            name="max_attempts_per_model",
            minimum=1,
        )
        if not isinstance(self.retryable_failures, frozenset):
            raise TypeError("retryable_failures must be a frozenset")
        if any(not isinstance(item, ProviderFailureKind) for item in self.retryable_failures):
            raise TypeError("retryable_failures must contain ProviderFailureKind values")


@dataclass(frozen=True, slots=True)
class FallbackPolicy:
    """Explicit ordered fallback models; provider output cannot modify it."""

    models: tuple[ModelId, ...] = ()
    max_switches: int = 0
    fallback_failures: frozenset[ProviderFailureKind] = frozenset(
        {
            ProviderFailureKind.UNAVAILABLE,
            ProviderFailureKind.TIMEOUT,
            ProviderFailureKind.RESOURCE_LIMITED,
        }
    )

    def __post_init__(self) -> None:
        if not isinstance(self.models, tuple):
            raise TypeError("models must be a tuple")
        if any(not isinstance(model, ModelId) for model in self.models):
            raise TypeError("models must contain ModelId values")
        if len(set(self.models)) != len(self.models):
            raise ValueError("fallback models must be unique")
        switches = _validate_count(self.max_switches, name="max_switches")
        if switches > len(self.models):
            raise ValueError("max_switches cannot exceed configured fallback model count")
        if not isinstance(self.fallback_failures, frozenset):
            raise TypeError("fallback_failures must be a frozenset")
        if any(not isinstance(item, ProviderFailureKind) for item in self.fallback_failures):
            raise TypeError("fallback_failures must contain ProviderFailureKind values")


@dataclass(frozen=True, slots=True)
class ModelAttemptReservation:
    """Conservative per-call resource ceiling reserved before provider I/O.

    Token and cost values are supplied by trusted provider configuration. They
    are never inferred from model text. A successful provider report exceeding
    the reservation fails closed.
    """

    model_tokens: int
    external_cost: Decimal

    def __post_init__(self) -> None:
        if type(self.model_tokens) is not int:
            raise TypeError("model_tokens must be an int")
        if self.model_tokens < 0:
            raise ValueError("model_tokens must not be negative")
        if not isinstance(self.external_cost, Decimal):
            raise TypeError("external_cost must be a Decimal")
        if not self.external_cost.is_finite() or self.external_cost < 0:
            raise ValueError("external_cost must be finite and non-negative")

    def resource_request(self) -> ResourceRequest:
        return ResourceRequest(
            delta=ResourceDelta(
                wall_clock=ResourceDelta.zero().wall_clock,
                model_calls=1,
                model_tokens=self.model_tokens,
                research_queries=0,
                machine_actions=0,
                repair_attempts=0,
                external_cost=self.external_cost,
            ),
            risk_level=RiskLevel.R0,
        )


@dataclass(frozen=True, slots=True)
class ProviderHealthSnapshot:
    provider_id: ProviderId
    attempts: int
    successes: int
    failures: int
    consecutive_failures: int
    last_failure: ProviderFailureKind | None

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, ProviderId):
            raise TypeError("provider_id must be a ProviderId")
        for name in ("attempts", "successes", "failures", "consecutive_failures"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative int")
        if self.successes + self.failures != self.attempts:
            raise ValueError("attempts must equal successes + failures")
        if self.last_failure is not None and not isinstance(
            self.last_failure, ProviderFailureKind
        ):
            raise TypeError("last_failure must be ProviderFailureKind or None")


@dataclass(slots=True)
class _MutableHealth:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_failure: ProviderFailureKind | None = None


class ProviderHealthTracker:
    """Thread-safe diagnostic evidence. Health never grants execution authority."""

    __slots__ = ("_lock", "_states")

    def __init__(self) -> None:
        self._lock = Lock()
        self._states: dict[ProviderId, _MutableHealth] = {}

    def record_success(self, provider_id: ProviderId) -> None:
        if not isinstance(provider_id, ProviderId):
            raise TypeError("provider_id must be a ProviderId")
        with self._lock:
            state = self._states.setdefault(provider_id, _MutableHealth())
            state.attempts += 1
            state.successes += 1
            state.consecutive_failures = 0
            state.last_failure = None

    def record_failure(self, provider_id: ProviderId, kind: ProviderFailureKind) -> None:
        if not isinstance(provider_id, ProviderId):
            raise TypeError("provider_id must be a ProviderId")
        if not isinstance(kind, ProviderFailureKind):
            raise TypeError("kind must be a ProviderFailureKind")
        with self._lock:
            state = self._states.setdefault(provider_id, _MutableHealth())
            state.attempts += 1
            state.failures += 1
            state.consecutive_failures += 1
            state.last_failure = kind

    def snapshot(self, provider_id: ProviderId) -> ProviderHealthSnapshot:
        if not isinstance(provider_id, ProviderId):
            raise TypeError("provider_id must be a ProviderId")
        with self._lock:
            state = self._states.get(provider_id, _MutableHealth())
            return ProviderHealthSnapshot(
                provider_id=provider_id,
                attempts=state.attempts,
                successes=state.successes,
                failures=state.failures,
                consecutive_failures=state.consecutive_failures,
                last_failure=state.last_failure,
            )


class ModelCapabilityRegistry:
    """Explicit provider/model registry with no discovery or dynamic loading."""

    __slots__ = ("_models", "_providers")

    def __init__(self, providers: tuple[ModelProvider, ...] = ()) -> None:
        if not isinstance(providers, tuple):
            raise TypeError("providers must be a tuple")
        self._providers: dict[ProviderId, ModelProvider] = {}
        self._models: dict[ModelId, ModelDescriptor] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ModelProvider) -> None:
        if not isinstance(provider, ModelProvider):
            raise TypeError("provider must implement ModelProvider")
        descriptor = provider.descriptor
        if not isinstance(descriptor, ProviderDescriptor):
            raise TypeError("provider descriptor must be a ProviderDescriptor")
        if descriptor.provider_id in self._providers:
            raise ValueError(f"provider {descriptor.provider_id} is already registered")
        for model in descriptor.models:
            if model.model_id in self._models:
                raise ValueError(f"model {model.model_id} is already registered")
        self._providers[descriptor.provider_id] = provider
        for model in descriptor.models:
            self._models[model.model_id] = model

    def provider_for(self, model_id: ModelId) -> ModelProvider | None:
        if not isinstance(model_id, ModelId):
            raise TypeError("model_id must be a ModelId")
        if model_id not in self._models:
            return None
        return self._providers.get(model_id.provider_id)

    def descriptor_for(self, model_id: ModelId) -> ModelDescriptor | None:
        if not isinstance(model_id, ModelId):
            raise TypeError("model_id must be a ModelId")
        return self._models.get(model_id)

    def models_with(
        self, required: frozenset[ModelCapability]
    ) -> tuple[ModelDescriptor, ...]:
        if not isinstance(required, frozenset):
            raise TypeError("required must be a frozenset")
        if any(not isinstance(item, ModelCapability) for item in required):
            raise TypeError("required must contain ModelCapability values")
        return tuple(
            descriptor
            for _, descriptor in sorted(
                self._models.items(), key=lambda item: str(item[0])
            )
            if required.issubset(descriptor.capabilities)
        )


def _failure_kind(error: AgentXError) -> ProviderFailureKind | None:
    raw = error.details.get("provider_failure_kind")
    if not isinstance(raw, str):
        return None
    try:
        return ProviderFailureKind(raw)
    except ValueError:
        return None


def _stop_failure(context: ExecutionContext, *, clock: MonotonicClock | None) -> AgentXError | None:
    stop = context.observe_stop(clock=clock)
    if stop.cancellation_requested:
        return AgentXError(
            code="model_gateway.cancelled",
            message="model gateway stopped before provider invocation",
            category=ErrorCategory.CANCELLED,
            retryability=Retryability.NON_RETRYABLE,
        )
    if stop.timed_out:
        return AgentXError(
            code="model_gateway.timeout",
            message="model gateway deadline expired before provider invocation",
            category=ErrorCategory.TIMEOUT,
            retryability=Retryability.NON_RETRYABLE,
        )
    return None


class BoundedModelGateway:
    """Explicit bounded multi-provider composition for one canonical run."""

    __slots__ = ("_budget", "_clock", "_health", "_registry")

    def __init__(
        self,
        *,
        registry: ModelCapabilityRegistry,
        budget: ResourceBudget,
        health: ProviderHealthTracker | None = None,
        clock: MonotonicClock | None = None,
    ) -> None:
        if not isinstance(registry, ModelCapabilityRegistry):
            raise TypeError("registry must be a ModelCapabilityRegistry")
        if not isinstance(budget, ResourceBudget):
            raise TypeError("budget must be a ResourceBudget")
        if health is not None and not isinstance(health, ProviderHealthTracker):
            raise TypeError("health must be a ProviderHealthTracker or None")
        self._registry = registry
        self._budget = budget
        self._health = health or ProviderHealthTracker()
        self._clock = clock

    @property
    def health(self) -> ProviderHealthTracker:
        return self._health

    def invoke(
        self,
        request: ModelRequest,
        context: ExecutionContext,
        *,
        reservation: ModelAttemptReservation,
        retry: RetryPolicy | None = None,
        fallback: FallbackPolicy | None = None,
    ) -> Result[ModelResponse, AgentXError]:
        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        if not isinstance(reservation, ModelAttemptReservation):
            raise TypeError("reservation must be a ModelAttemptReservation")
        retry = RetryPolicy() if retry is None else retry
        fallback = FallbackPolicy() if fallback is None else fallback
        if not isinstance(retry, RetryPolicy):
            raise TypeError("retry must be a RetryPolicy or None")
        if not isinstance(fallback, FallbackPolicy):
            raise TypeError("fallback must be a FallbackPolicy or None")

        primary = self._registry.descriptor_for(request.model_id)
        if primary is None:
            return Result.failure(
                provider_failure(
                    ProviderFailureKind.CONFIGURATION,
                    provider_id=request.model_id.provider_id,
                    model_id=request.model_id,
                    message="requested primary model is not registered",
                )
            )

        candidates = (request.model_id, *fallback.models[: fallback.max_switches])
        if len(set(candidates)) != len(candidates):
            return Result.failure(
                provider_failure(
                    ProviderFailureKind.CONFIGURATION,
                    provider_id=request.model_id.provider_id,
                    model_id=request.model_id,
                    message="fallback policy repeats the primary or another candidate model",
                )
            )
        for candidate in candidates[1:]:
            descriptor = self._registry.descriptor_for(candidate)
            if descriptor is None or not primary.capabilities.issubset(descriptor.capabilities):
                return Result.failure(
                    provider_failure(
                        ProviderFailureKind.CONFIGURATION,
                        provider_id=request.model_id.provider_id,
                        model_id=request.model_id,
                        message="fallback model is unregistered or capability-incompatible",
                    )
                )

        last_error: AgentXError | None = None
        for candidate_index, candidate in enumerate(candidates):
            provider = self._registry.provider_for(candidate)
            assert provider is not None
            for attempt in range(retry.max_attempts_per_model):
                stopped = _stop_failure(context, clock=self._clock)
                if stopped is not None:
                    return Result.failure(stopped)

                budget_result = self._budget.check_and_consume(reservation.resource_request())
                if budget_result.decision is not BudgetDecision.ALLOW:
                    return Result.failure(
                        AgentXError(
                            code="model_gateway.budget_denied",
                            message="model invocation would exceed the canonical resource budget",
                            category=ErrorCategory.RESOURCE,
                            retryability=Retryability.NON_RETRYABLE,
                        )
                    )

                candidate_request = ModelRequest(
                    model_id=candidate,
                    content=request.content,
                    max_output_tokens=request.max_output_tokens,
                )
                raw_result: object = provider.invoke(candidate_request)
                if not isinstance(raw_result, Result):
                    self._health.record_failure(candidate.provider_id, ProviderFailureKind.INTERNAL)
                    return Result.failure(
                        provider_failure(
                            ProviderFailureKind.INTERNAL,
                            provider_id=candidate.provider_id,
                            model_id=candidate,
                            message="model provider returned a malformed result",
                        )
                    )
                if raw_result.is_success:
                    response = raw_result.unwrap()
                    if not isinstance(response, ModelResponse) or response.model_id != candidate:
                        self._health.record_failure(
                            candidate.provider_id, ProviderFailureKind.INTERNAL
                        )
                        return Result.failure(
                            provider_failure(
                                ProviderFailureKind.INTERNAL,
                                provider_id=candidate.provider_id,
                                model_id=candidate,
                                message="model provider returned a malformed response",
                            )
                        )
                    tokens = response.usage.accounted_tokens
                    cost = response.usage.external_cost
                    if tokens is not None and tokens > reservation.model_tokens:
                        self._health.record_failure(
                            candidate.provider_id, ProviderFailureKind.RESOURCE_LIMITED
                        )
                        return Result.failure(
                            provider_failure(
                                ProviderFailureKind.RESOURCE_LIMITED,
                                provider_id=candidate.provider_id,
                                model_id=candidate,
                                message="provider usage exceeded the trusted token reservation",
                            )
                        )
                    if cost is not None and cost > reservation.external_cost:
                        self._health.record_failure(
                            candidate.provider_id, ProviderFailureKind.RESOURCE_LIMITED
                        )
                        return Result.failure(
                            provider_failure(
                                ProviderFailureKind.RESOURCE_LIMITED,
                                provider_id=candidate.provider_id,
                                model_id=candidate,
                                message="provider usage exceeded the trusted cost reservation",
                            )
                        )
                    self._health.record_success(candidate.provider_id)
                    return Result.success(response)

                error_obj = raw_result.unwrap_error()
                if not isinstance(error_obj, AgentXError):
                    self._health.record_failure(candidate.provider_id, ProviderFailureKind.INTERNAL)
                    return Result.failure(
                        provider_failure(
                            ProviderFailureKind.INTERNAL,
                            provider_id=candidate.provider_id,
                            model_id=candidate,
                            message="model provider returned a malformed failure",
                        )
                    )
                last_error = error_obj
                kind = _failure_kind(error_obj)
                if kind is None:
                    self._health.record_failure(candidate.provider_id, ProviderFailureKind.INTERNAL)
                    return Result.failure(error_obj)
                self._health.record_failure(candidate.provider_id, kind)

                more_attempts = attempt + 1 < retry.max_attempts_per_model
                if more_attempts and kind in retry.retryable_failures:
                    continue
                more_models = candidate_index + 1 < len(candidates)
                if more_models and kind in fallback.fallback_failures:
                    break
                return Result.failure(error_obj)

        assert last_error is not None
        return Result.failure(last_error)
