"""Provider-neutral cognitive model contracts for AgentX.

A2.01 defines the smallest boundary through which cognition components can ask
configured cognitive providers for model work. The provider is a resource, not
an authority: responses are untrusted data and never execute machine
capabilities, mutate Task state, grant permission, alter risk, enlarge resource
budgets, or verify external actions.

This module intentionally contains no concrete provider, network client,
credential loading, retry loop, provider selection, prompt framework, tool
execution, streaming runtime, or local-model runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result

__all__ = [
    "ModelCapability",
    "ModelDescriptor",
    "ModelId",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ModelUsage",
    "ProviderDescriptor",
    "ProviderFailureKind",
    "ProviderId",
    "TextContent",
    "provider_failure",
]

_MAX_PROVIDER_ID_LENGTH: Final[int] = 128
_MAX_MODEL_ID_LENGTH: Final[int] = 256
_MAX_CAPABILITY_NAME_LENGTH: Final[int] = 128
_MAX_COUNTER: Final[int] = (1 << 63) - 1


def _validate_machine_identifier(value: object, *, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty and trimmed")
    if len(value) > max_length:
        raise ValueError(f"{field_name} must not exceed {max_length} characters")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise ValueError(f"{field_name} must not contain whitespace or control characters")
    return value


def _validate_capabilities(
    value: object,
    *,
    field_name: str,
) -> frozenset[ModelCapability]:
    if not isinstance(value, frozenset):
        raise TypeError(f"{field_name} must be a frozenset")
    for capability in value:
        if not isinstance(capability, ModelCapability):
            raise TypeError(f"{field_name} must contain only ModelCapability values")
    return value


def _validate_content_tuple(value: object, *, field_name: str) -> tuple[TextContent, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple")
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    for item in value:
        if not isinstance(item, TextContent):
            raise TypeError(f"{field_name} must contain only TextContent values")
    return value


def _validate_optional_counter(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int or None")
    if value < 0:
        raise ValueError(f"{field_name} must not be negative")
    if value > _MAX_COUNTER:
        raise OverflowError(f"{field_name} exceeds the supported counter range")
    return value


def _validate_optional_latency(value: object) -> timedelta | None:
    if value is None:
        return None
    if not isinstance(value, timedelta):
        raise TypeError("latency must be a timedelta or None")
    if value < timedelta(0):
        raise ValueError("latency must not be negative")
    return value


def _validate_optional_cost(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal):
        raise TypeError("external_cost must be a Decimal or None")
    if not value.is_finite():
        raise ValueError("external_cost must be finite")
    if value < 0:
        raise ValueError("external_cost must not be negative")
    return value


def _validate_message(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("message must be a string")
    if not value or value != value.strip():
        raise ValueError("message must be non-empty and trimmed")
    return value


@dataclass(frozen=True, slots=True, order=True)
class ProviderId:
    """Stable configured provider identity, independent of any vendor SDK."""

    value: str

    def __post_init__(self) -> None:
        _validate_machine_identifier(
            self.value,
            field_name="provider_id",
            max_length=_MAX_PROVIDER_ID_LENGTH,
        )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class ModelId:
    """Provider-qualified model identity.

    Model identifiers are provider-owned strings, not AgentX ``DomainId``
    values. Qualification by :class:`ProviderId` prevents accidental identity
    collisions between unrelated providers.
    """

    provider_id: ProviderId
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, ProviderId):
            raise TypeError("model_id.provider_id must be a ProviderId")
        _validate_machine_identifier(
            self.value,
            field_name="model_id",
            max_length=_MAX_MODEL_ID_LENGTH,
        )

    def __str__(self) -> str:
        return f"{self.provider_id.value}/{self.value}"


@dataclass(frozen=True, slots=True, order=True)
class ModelCapability:
    """Typed, extensible declaration of provider/model cognitive capability.

    Capability names describe model-interface support only. They are not kernel
    permissions and can never grant machine authority. A2.01 deliberately does
    not freeze a speculative universal modality or model-role taxonomy.
    """

    name: str

    def __post_init__(self) -> None:
        _validate_machine_identifier(
            self.name,
            field_name="capability.name",
            max_length=_MAX_CAPABILITY_NAME_LENGTH,
        )


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    """Immutable declaration of one model and its advertised capabilities."""

    model_id: ModelId
    capabilities: frozenset[ModelCapability]

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, ModelId):
            raise TypeError("model_id must be a ModelId")
        _validate_capabilities(self.capabilities, field_name="model capabilities")


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Immutable declaration of one provider instance and exposed models.

    Provider capabilities are an upper bound on the model-interface
    capabilities exposed by this configured provider. Every listed model must
    belong to this provider and declare only capabilities present at the
    provider level.
    """

    provider_id: ProviderId
    capabilities: frozenset[ModelCapability]
    models: tuple[ModelDescriptor, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, ProviderId):
            raise TypeError("provider_id must be a ProviderId")
        provider_capabilities = _validate_capabilities(
            self.capabilities,
            field_name="provider capabilities",
        )
        if not isinstance(self.models, tuple):
            raise TypeError("models must be a tuple")

        seen_models: set[ModelId] = set()
        for model in self.models:
            if not isinstance(model, ModelDescriptor):
                raise TypeError("models must contain only ModelDescriptor values")
            if model.model_id.provider_id != self.provider_id:
                raise ValueError("every model must belong to provider_id")
            if model.model_id in seen_models:
                raise ValueError("models must not contain duplicate model identities")
            if not model.capabilities.issubset(provider_capabilities):
                raise ValueError("model capabilities must be a subset of provider capabilities")
            seen_models.add(model.model_id)


@dataclass(frozen=True, slots=True)
class TextContent:
    """Text content carried as inert provider input or output data.

    A2.01 intentionally standardizes only this minimal content form. Future
    modalities can extend the content boundary without making arbitrary vendor
    JSON canonical today.
    """

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("text content must be a string")
        if not self.text:
            raise ValueError("text content must not be empty")
        if "\x00" in self.text:
            raise ValueError("text content must not contain NUL characters")


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelRequest:
    """Minimal immutable request for provider-neutral model work."""

    model_id: ModelId
    content: tuple[TextContent, ...]
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, ModelId):
            raise TypeError("model_id must be a ModelId")
        _validate_content_tuple(self.content, field_name="request.content")
        if self.max_output_tokens is not None:
            value = _validate_optional_counter(
                self.max_output_tokens,
                field_name="max_output_tokens",
            )
            if value == 0:
                raise ValueError("max_output_tokens must be greater than zero when provided")


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelUsage:
    """Immutable provider-reported accounting data.

    Token counts are exact non-negative integers when reported. ``total_tokens``
    is aggregate input-plus-output usage and must be consistent with component
    counts when both are known. ``latency`` mirrors C1.08 elapsed accounting as
    ``timedelta``. ``external_cost`` mirrors C1.08 exact cost accounting as a
    finite non-negative ``Decimal`` in the caller-selected accounting unit.

    Usage is descriptive only. It cannot grant, replace, or enlarge a
    ``ResourceEnvelope``.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency: timedelta | None = None
    external_cost: Decimal | None = None

    def __post_init__(self) -> None:
        input_tokens = _validate_optional_counter(self.input_tokens, field_name="input_tokens")
        output_tokens = _validate_optional_counter(self.output_tokens, field_name="output_tokens")
        total_tokens = _validate_optional_counter(self.total_tokens, field_name="total_tokens")
        _validate_optional_latency(self.latency)
        _validate_optional_cost(self.external_cost)

        known_components = tuple(
            value for value in (input_tokens, output_tokens) if value is not None
        )
        if total_tokens is not None and any(total_tokens < value for value in known_components):
            raise ValueError("total_tokens must not be smaller than a reported component")
        if input_tokens is not None and output_tokens is not None:
            aggregate = input_tokens + output_tokens
            if aggregate > _MAX_COUNTER:
                raise OverflowError("aggregate token usage exceeds the supported counter range")
            if total_tokens is not None and total_tokens != aggregate:
                raise ValueError(
                    "total_tokens must equal input_tokens + output_tokens when both are reported"
                )

    @property
    def accounted_tokens(self) -> int | None:
        """Return exact aggregate token usage when enough data was reported."""

        if self.total_tokens is not None:
            return self.total_tokens
        if self.input_tokens is not None and self.output_tokens is not None:
            return self.input_tokens + self.output_tokens
        return None


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelResponse:
    """Successful provider response containing only inert model output data."""

    model_id: ModelId
    content: tuple[TextContent, ...]
    usage: ModelUsage

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, ModelId):
            raise TypeError("model_id must be a ModelId")
        _validate_content_tuple(self.content, field_name="response.content")
        if not isinstance(self.usage, ModelUsage):
            raise TypeError("usage must be a ModelUsage")


class ProviderFailureKind(StrEnum):
    """Predictable expected provider failure categories for caller handling."""

    UNAVAILABLE = "unavailable"
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION = "authentication"
    CONFIGURATION = "configuration"
    RESOURCE_LIMITED = "resource_limited"
    TIMEOUT = "timeout"
    INTERNAL = "internal"


def _failure_policy(kind: ProviderFailureKind) -> tuple[ErrorCategory, Retryability]:
    if kind is ProviderFailureKind.UNAVAILABLE:
        return ErrorCategory.DEPENDENCY, Retryability.RETRYABLE
    if kind is ProviderFailureKind.INVALID_REQUEST:
        return ErrorCategory.VALIDATION, Retryability.NON_RETRYABLE
    if kind is ProviderFailureKind.AUTHENTICATION:
        return ErrorCategory.DEPENDENCY, Retryability.NON_RETRYABLE
    if kind is ProviderFailureKind.CONFIGURATION:
        return ErrorCategory.PRECONDITION, Retryability.NON_RETRYABLE
    if kind is ProviderFailureKind.RESOURCE_LIMITED:
        return ErrorCategory.RESOURCE, Retryability.UNKNOWN
    if kind is ProviderFailureKind.TIMEOUT:
        return ErrorCategory.TIMEOUT, Retryability.RETRYABLE
    if kind is ProviderFailureKind.INTERNAL:
        return ErrorCategory.DEPENDENCY, Retryability.UNKNOWN
    raise AssertionError(f"unsupported ProviderFailureKind: {kind!r}")


def provider_failure(
    kind: ProviderFailureKind,
    *,
    provider_id: ProviderId,
    message: str,
    model_id: ModelId | None = None,
) -> AgentXError:
    """Build a canonical ``AgentXError`` for an expected provider failure.

    A2.01 adds no competing global error hierarchy. Expected provider failures
    remain explicit through ``Result.failure(AgentXError)`` while this factory
    gives them a controlled provider-specific classification and error code.
    """

    if not isinstance(kind, ProviderFailureKind):
        raise TypeError("kind must be a ProviderFailureKind")
    if not isinstance(provider_id, ProviderId):
        raise TypeError("provider_id must be a ProviderId")
    _validate_message(message)
    if model_id is not None:
        if not isinstance(model_id, ModelId):
            raise TypeError("model_id must be a ModelId or None")
        if model_id.provider_id != provider_id:
            raise ValueError("model_id must belong to provider_id")

    category, retryability = _failure_policy(kind)
    details: dict[str, str] = {
        "provider_id": provider_id.value,
        "provider_failure_kind": kind.value,
    }
    if model_id is not None:
        details["model_id"] = model_id.value

    return AgentXError(
        code=f"model_provider.{kind.value}",
        message=message,
        category=category,
        retryability=retryability,
        details=details,
    )


@runtime_checkable
class ModelProvider(Protocol):
    """Synchronous provider-neutral cognitive resource boundary.

    Implementations may be remote or local in later tasks, but A2.01 defines no
    concrete implementation. ``invoke`` performs cognitive/model work only and
    returns expected operational failure through the canonical ``Result``
    contract. It grants no machine-execution authority.
    """

    @property
    def descriptor(self) -> ProviderDescriptor:
        """Return the immutable configured provider/model capability declaration."""
        ...

    def invoke(self, request: ModelRequest) -> Result[ModelResponse, AgentXError]:
        """Request one non-streaming unit of cognitive/model work."""
        ...
