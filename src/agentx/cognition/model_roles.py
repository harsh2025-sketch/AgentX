"""Logical model-role contracts above the provider-neutral A2.01 boundary.

A2.02 assigns configured models to explicit cognitive responsibilities. Roles
describe what cognitive responsibility a model may fill; they are not providers,
machine capabilities, agents, permissions, authority levels, or execution
routing decisions.

Bindings are configuration data only. They cannot grant authority, alter risk or
resource policy, execute machine capabilities, mutate tasks, verify actions, or
make model output trusted.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.cognition.model_provider import ModelCapability, ModelDescriptor, ModelId

__all__ = [
    "CANONICAL_MODEL_ROLE_DEFINITIONS",
    "ModelRole",
    "ModelRoleBinding",
    "ModelRoleBindings",
    "ModelRoleDefinition",
    "model_role_definition",
]


class ModelRole(StrEnum):
    """Canonical logical cognitive responsibilities for configured models."""

    ROUTER = "ROUTER"
    REASONING = "REASONING"
    VISION = "VISION"
    GROUNDING = "GROUNDING"
    RESEARCH = "RESEARCH"
    CRITIC = "CRITIC"
    REALTIME = "REALTIME"


@dataclass(frozen=True, slots=True)
class ModelRoleDefinition:
    """Immutable role definition expressed in canonical A2.01 capabilities."""

    role: ModelRole
    required_capabilities: frozenset[ModelCapability]

    def __post_init__(self) -> None:
        if not isinstance(self.role, ModelRole):
            raise TypeError("role must be a ModelRole")
        if not isinstance(self.required_capabilities, frozenset):
            raise TypeError("required_capabilities must be a frozenset")
        if not self.required_capabilities:
            raise ValueError("required_capabilities must not be empty")
        for capability in self.required_capabilities:
            if not isinstance(capability, ModelCapability):
                raise TypeError("required_capabilities must contain only ModelCapability values")

    def missing_capabilities(self, model: ModelDescriptor) -> tuple[ModelCapability, ...]:
        """Return missing interface capabilities in deterministic name order."""

        if not isinstance(model, ModelDescriptor):
            raise TypeError("model must be a ModelDescriptor")
        return tuple(
            sorted(
                self.required_capabilities - model.capabilities,
                key=lambda capability: capability.name,
            )
        )

    def is_compatible(self, model: ModelDescriptor) -> bool:
        """Return whether ``model`` explicitly satisfies every role requirement."""

        return not self.missing_capabilities(model)


_TEXT_INPUT: Final = ModelCapability("content.text.input")
_TEXT_OUTPUT: Final = ModelCapability("content.text.output")
_IMAGE_INPUT: Final = ModelCapability("content.image.input")

_TEXT_ROLE_REQUIREMENTS: Final = frozenset({_TEXT_INPUT, _TEXT_OUTPUT})
_VISION_ROLE_REQUIREMENTS: Final = frozenset({_IMAGE_INPUT, _TEXT_OUTPUT})

CANONICAL_MODEL_ROLE_DEFINITIONS: Final[tuple[ModelRoleDefinition, ...]] = (
    ModelRoleDefinition(ModelRole.ROUTER, _TEXT_ROLE_REQUIREMENTS),
    ModelRoleDefinition(ModelRole.REASONING, _TEXT_ROLE_REQUIREMENTS),
    ModelRoleDefinition(ModelRole.VISION, _VISION_ROLE_REQUIREMENTS),
    ModelRoleDefinition(ModelRole.GROUNDING, _TEXT_ROLE_REQUIREMENTS),
    ModelRoleDefinition(ModelRole.RESEARCH, _TEXT_ROLE_REQUIREMENTS),
    ModelRoleDefinition(ModelRole.CRITIC, _TEXT_ROLE_REQUIREMENTS),
    ModelRoleDefinition(ModelRole.REALTIME, _TEXT_ROLE_REQUIREMENTS),
)

_CANONICAL_ROLE_ORDER: Final[tuple[ModelRole, ...]] = tuple(
    definition.role for definition in CANONICAL_MODEL_ROLE_DEFINITIONS
)


def model_role_definition(role: ModelRole) -> ModelRoleDefinition:
    """Return the canonical immutable definition for ``role``."""

    if not isinstance(role, ModelRole):
        raise TypeError("role must be a ModelRole")
    for definition in CANONICAL_MODEL_ROLE_DEFINITIONS:
        if definition.role is role:
            return definition
    raise AssertionError(f"missing canonical definition for role: {role!r}")


@dataclass(frozen=True, slots=True)
class ModelRoleBinding:
    """Explicit compatible binding from one logical role to one configured model."""

    role: ModelRole
    model: ModelDescriptor

    def __post_init__(self) -> None:
        if not isinstance(self.role, ModelRole):
            raise TypeError("role must be a ModelRole")
        if not isinstance(self.model, ModelDescriptor):
            raise TypeError("model must be a ModelDescriptor")

        missing = model_role_definition(self.role).missing_capabilities(self.model)
        if missing:
            names = ", ".join(capability.name for capability in missing)
            raise ValueError(
                f"model {self.model.model_id} is incompatible with role {self.role.value}; "
                f"missing required capabilities: {names}"
            )

    @property
    def model_id(self) -> ModelId:
        """Return the provider-qualified A2.01 identity of the bound model."""

        return self.model.model_id


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelRoleBindings:
    """Deterministic explicit one-model-per-role binding collection.

    A physical model may appear in multiple bindings for different roles. A
    logical role appears at most once: A2.02 defines no ranking, fallback, or
    automatic selection among multiple models.
    """

    bindings: tuple[ModelRoleBinding, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.bindings, tuple):
            raise TypeError("bindings must be a tuple")

        seen_roles: set[ModelRole] = set()
        for binding in self.bindings:
            if not isinstance(binding, ModelRoleBinding):
                raise TypeError("bindings must contain only ModelRoleBinding values")
            if binding.role in seen_roles:
                raise ValueError(
                    f"role {binding.role.value} must have exactly one explicit binding"
                )
            seen_roles.add(binding.role)

        ordered = tuple(
            sorted(
                self.bindings,
                key=lambda binding: _CANONICAL_ROLE_ORDER.index(binding.role),
            )
        )
        object.__setattr__(self, "bindings", ordered)

    def __iter__(self) -> Iterator[ModelRoleBinding]:
        return iter(self.bindings)

    def __len__(self) -> int:
        return len(self.bindings)

    @property
    def roles(self) -> tuple[ModelRole, ...]:
        """Return bound roles in canonical deterministic order."""

        return tuple(binding.role for binding in self.bindings)

    def for_role(self, role: ModelRole) -> ModelRoleBinding | None:
        """Return the explicit binding for ``role``, or ``None`` when unbound."""

        if not isinstance(role, ModelRole):
            raise TypeError("role must be a ModelRole")
        for binding in self.bindings:
            if binding.role is role:
                return binding
        return None
