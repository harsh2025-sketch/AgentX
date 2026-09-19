"""Canonical Capability ABI for AgentX (A1.08).

A Capability is a machine-operation boundary: the smallest production-quality
contract through which future AgentX execution code can describe and invoke a
governed machine operation.

Architectural principle
-----------------------

A Capability is **not** authority. The existence, discovery, possession, import,
or construction of a Capability never grants permission to execute it. Future
execution is governed by the Trusted Kernel. This module therefore keeps the
following concepts in deliberately separate contracts and never collapses them
into one object or boolean:

    Capability / Permission / Risk / Budget / ExecutionContext / Verification.

Contract inventory
------------------

    - :class:`CapabilityName` / :class:`CapabilityVersion` /
      :class:`CapabilityIdentity`: stable, validated identity plus an explicit
      typed version. Versioning is ``major.minor.patch`` integers, never an
      opaque string.
    - :class:`CapabilityPlatform` / :class:`CapabilityScope`: the
      platform/environment scope in which execution is meaningful.
    - :class:`CapabilityParams` / :class:`CapabilityRequest`: the typed
      parameter boundary. Concrete capabilities declare frozen dataclasses
      derived from :class:`CapabilityParams`; raw ``dict`` values and
      ``**kwargs`` are rejected at the boundary. There is no universal schema
      language here.
    - :class:`CapabilityPrecondition`: explicit, declarative preconditions.
      They are data only: this module evaluates nothing, satisfies nothing,
      plans nothing, mutates no Task state, and executes no recovery.
    - :class:`CapabilityDescriptor`: the inert description binding identity,
      human description, scope, required canonical permissions, canonical risk
      assessment, preconditions, rollback declaration, and resource estimate.
      A descriptor never authorizes anything.
    - :class:`Capability`: the ``Protocol`` boundary for the execution
      operation itself, with ``execute`` and ``verify`` as separate methods.
    - :class:`CapabilityObservation`: evidence/data produced by execution.
      Observation never grants permission.
    - :class:`ExecutionResult`: the invocation outcome. ``succeeded=True``
      means only that the invocation produced a result; it must never be read
      as proof that an external state transition happened.
    - :class:`VerificationResult`: the separate verdict on whether expected
      state/postconditions were achieved. The invariant is preserved::

          NO ACTION == SUCCESS WITHOUT VERIFICATION.

      A verification verdict never grants future permission.
    - :class:`RollbackSupport` / :class:`RollbackDeclaration`: the explicit
      ``supported`` / ``unsupported`` / ``not_applicable`` distinction. A
      declaration performs no rollback and grants no authority; rollback
      orchestration belongs to future work.
    - :class:`ResourceEstimate`: the narrow descriptive estimate of expected
      cost (wall-clock, machine actions, external cost). An estimate grants no
      budget and can never enlarge a
      :class:`~agentx.kernel.resource_budget.ResourceEnvelope`.

ExecutionContext relationship
-----------------------------

:meth:`Capability.execute` and :meth:`Capability.verify` accept the canonical
:class:`~agentx.core.execution.ExecutionContext` as execution-scoped context
only: task/correlation identity, cooperative cancellation observation, and an
optional monotonic deadline. The context carries no permissions, no risk or
budget overrides, and no kernel bypass; it grants no authority.

Security invariants
-------------------

This module proves, and its tests enforce, that:

    - capability existence, identity, and metadata grant no authority;
    - :class:`~agentx.core.tasks.TaskPriority` cannot change required
      permissions or risk;
    - model-generated text cannot change required permissions or risk;
    - :class:`~agentx.core.execution.ExecutionContext` cannot bypass kernel
      authority;
    - observations, verification results, rollback declarations, and resource
      estimates grant nothing and enlarge nothing.

Deliberate non-scope
--------------------

This module is the interface contract only. It does not implement the
Capability Registry (A1.09), closed-loop execution (A1.10), concrete Windows or
browser capabilities, shell/file execution, a Task Manager, Executor, Planner,
Reasoner, or Verifier subsystem. It performs no Action Gate invocation, no
budget consumption, no event publication, no persistence, no plugin discovery,
no dynamic imports, and no dependency installation.

Owner: A1.08. Belongs to ``agentx.capabilities``; imports only the standard
library, ``agentx.core`` contracts, and the canonical ``agentx.kernel``
permission/risk contracts.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Generic, Protocol, TypeVar

from agentx.core.execution import ExecutionContext
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskAssessment

__all__ = [
    "Capability",
    "CapabilityDescriptor",
    "CapabilityIdentity",
    "CapabilityName",
    "CapabilityObservation",
    "CapabilityParams",
    "CapabilityPlatform",
    "CapabilityPrecondition",
    "CapabilityRequest",
    "CapabilityScope",
    "CapabilityValidationError",
    "CapabilityVersion",
    "ExecutionResult",
    "ResourceEstimate",
    "RollbackDeclaration",
    "RollbackSupport",
    "VerificationResult",
]

_CAPABILITY_NAME_PATTERN: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_MAX_NAME_LENGTH: Final[int] = 128
_MAX_SHORT_TEXT_LENGTH: Final[int] = 512
_MAX_LONG_TEXT_LENGTH: Final[int] = 1024
_MAX_COUNTER: Final[int] = (1 << 63) - 1
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class CapabilityValidationError(ValueError):
    """Raised when a Capability ABI contract receives a malformed value."""


def _validate_text(value: object, *, field_name: str, max_length: int) -> str:
    """Validate explicit, non-empty, trimmed human-readable contract text."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise CapabilityValidationError(f"{field_name} must be non-empty and trimmed")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise CapabilityValidationError(f"{field_name} must not contain control characters")
    if len(value) > max_length:
        raise CapabilityValidationError(f"{field_name} must not exceed {max_length} characters")
    return value


def _validate_counter(value: object, *, field_name: str) -> int:
    """Validate an explicit non-negative integer counter (bools rejected)."""
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise CapabilityValidationError(f"{field_name} must not be negative")
    if value > _MAX_COUNTER:
        raise OverflowError(f"{field_name} exceeds the supported counter range")
    return value


def _freeze_json(value: object, *, path: str) -> object:
    """Validate JSON compatibility and return an immutable defensive copy."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CapabilityValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CapabilityValidationError(f"{path} contains a non-string object key")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        )
    raise CapabilityValidationError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _freeze_json_object(value: object, *, path: str) -> Mapping[str, object]:
    """Validate a JSON object mapping and return its immutable frozen copy."""
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a mapping, got {type(value).__name__}")
    frozen = _freeze_json(value, path=path)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
        raise AssertionError("JSON object freezing produced a non-mapping")
    return frozen


def _to_json_value(value: object, *, path: str) -> JsonValue:
    """Convert an internally frozen JSON value back to JSON-compatible primitives."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):  # defensive; construction already rejects this
            raise CapabilityValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):  # defensive; construction already rejects this
                raise CapabilityValidationError(f"{path} contains a non-string object key")
            result[key] = _to_json_value(item, path=f"{path}.{key}")
        return result
    if isinstance(value, tuple | list):
        return [_to_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise CapabilityValidationError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _to_json_object(value: Mapping[str, object], *, path: str) -> dict[str, JsonValue]:
    """Convert a frozen observation mapping to a plain JSON-compatible dict."""
    converted = _to_json_value(value, path=path)
    if not isinstance(converted, dict):  # pragma: no cover - mapping always converts to dict
        raise AssertionError("JSON object conversion produced a non-dict")
    return converted


# --------------------------------------------------------------------------
# Identity / version.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityName:
    """Stable validated capability name, e.g. ``"mail.compose_draft"``.

    Names are lowercase alphanumeric segments separated by single ``.``/``_``
    /``-`` characters. A name is descriptive identity only and grants no
    authority.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError(f"capability name must be a string, got {type(self.value).__name__}")
        if not self.value or self.value != self.value.strip():
            raise CapabilityValidationError("capability name must be non-empty and trimmed")
        if len(self.value) > _MAX_NAME_LENGTH:
            raise CapabilityValidationError(
                f"capability name must not exceed {_MAX_NAME_LENGTH} characters"
            )
        if _CAPABILITY_NAME_PATTERN.fullmatch(self.value) is None:
            raise CapabilityValidationError(
                "capability name must be lowercase alphanumeric segments separated by "
                f"single '._-' characters, got {self.value!r}"
            )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CapabilityVersion:
    """Explicit typed capability version as ``major.minor.patch`` integers.

    Versions are structured data, never an opaque string: ``major`` marks
    incompatible contract changes, ``minor`` marks backwards-compatible
    additions, and ``patch`` marks backwards-compatible fixes.
    """

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        _validate_counter(self.major, field_name="version.major")
        _validate_counter(self.minor, field_name="version.minor")
        _validate_counter(self.patch, field_name="version.patch")

    def to_str(self) -> str:
        """Return the canonical ``"major.minor.patch"`` form."""
        return f"{self.major}.{self.minor}.{self.patch}"

    def __str__(self) -> str:
        return self.to_str()

    @classmethod
    def from_str(cls, raw: str) -> CapabilityVersion:
        """Parse the canonical ``"major.minor.patch"`` form.

        Raises:
            TypeError: if ``raw`` is not a string (a programming error).
            CapabilityValidationError: if ``raw`` is not exactly three
                dot-separated non-negative integer components.
        """
        if not isinstance(raw, str):
            raise TypeError(f"version string must be a string, got {type(raw).__name__}")
        if not raw or raw != raw.strip():
            raise CapabilityValidationError("version string must be non-empty and trimmed")
        parts = raw.split(".")
        if len(parts) != 3 or any(not part.isascii() or not part.isdigit() for part in parts):
            raise CapabilityValidationError(
                "version string must have the form 'major.minor.patch' with "
                f"non-negative integer components, got {raw!r}"
            )
        try:
            major, minor, patch = (int(part) for part in parts)
        except ValueError as exc:  # pragma: no cover - guarded by the digit check
            raise CapabilityValidationError(
                f"version string has invalid components: {raw!r}"
            ) from exc
        return cls(major=major, minor=minor, patch=patch)


@dataclass(frozen=True, slots=True)
class CapabilityIdentity:
    """Stable identity binding a validated name to an explicit typed version."""

    name: CapabilityName
    version: CapabilityVersion

    def __post_init__(self) -> None:
        if not isinstance(self.name, CapabilityName):
            raise TypeError(f"name must be a CapabilityName, got {type(self.name).__name__}")
        if not isinstance(self.version, CapabilityVersion):
            raise TypeError(
                f"version must be a CapabilityVersion, got {type(self.version).__name__}"
            )

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


# --------------------------------------------------------------------------
# Platform / environment scope.
# --------------------------------------------------------------------------


class CapabilityPlatform(StrEnum):
    """Controlled platform vocabulary for capability scope."""

    WINDOWS = "windows"
    LINUX = "linux"
    MACOS = "macos"
    ANDROID = "android"
    ANY = "any"


@dataclass(frozen=True, slots=True)
class CapabilityScope:
    """Declaration of the platform scope in which execution is meaningful.

    Scope is descriptive only: it routes future selection and never grants
    authority, permission, or a kernel bypass.
    """

    platform: CapabilityPlatform

    def __post_init__(self) -> None:
        if not isinstance(self.platform, CapabilityPlatform):
            raise TypeError(
                f"platform must be a CapabilityPlatform, got {type(self.platform).__name__}"
            )


# --------------------------------------------------------------------------
# Parameters / request contract.
# --------------------------------------------------------------------------


class CapabilityParams(ABC):
    """Typed parameter boundary for one concrete capability.

    Concrete capabilities declare their parameters as immutable (frozen
    dataclass) subclasses of this ABC. The ABI accepts only such typed values:
    raw ``dict`` mappings and ``**kwargs`` are rejected at the
    :class:`CapabilityRequest` boundary. This ABC defines no universal schema
    language; each capability owns its own explicit parameter contract.
    """

    @abstractmethod
    def to_dict(self) -> dict[str, JsonValue]:
        """Return the JSON-compatible representation of these parameters."""
        ...


P = TypeVar("P", bound=CapabilityParams)


@dataclass(frozen=True, slots=True)
class CapabilityRequest(Generic[P]):  # noqa: UP046
    """Inert invocation request binding an identity to typed parameters.

    The request carries no ExecutionContext (context is passed separately to
    ``execute``/``verify``), no permissions, no risk overrides, and no
    authority of any kind.
    """

    identity: CapabilityIdentity
    params: P

    def __post_init__(self) -> None:
        if not isinstance(self.identity, CapabilityIdentity):
            raise TypeError(
                f"identity must be a CapabilityIdentity, got {type(self.identity).__name__}"
            )
        if isinstance(self.params, dict) or not isinstance(self.params, CapabilityParams):
            raise TypeError(
                "params must be a typed CapabilityParams, got "
                f"{type(self.params).__name__}; raw dicts and **kwargs are rejected"
            )


# --------------------------------------------------------------------------
# Preconditions.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityPrecondition:
    """One explicit declarative precondition for a capability.

    Preconditions are data only. This module provides no evaluation, no
    satisfaction planning, and no recovery: a failed precondition in future
    execution must never automatically mutate Task state.
    """

    name: str
    description: str

    def __post_init__(self) -> None:
        _validate_text(self.name, field_name="precondition.name", max_length=_MAX_NAME_LENGTH)
        _validate_text(
            self.description,
            field_name="precondition.description",
            max_length=_MAX_SHORT_TEXT_LENGTH,
        )


# --------------------------------------------------------------------------
# Observation.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityObservation:
    """Evidence/data produced by a capability invocation.

    An observation may carry untrusted external content as JSON-compatible
    data. It is evidence only: it never grants permission and never verifies
    anything by itself.
    """

    summary: str
    data: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_text(
            self.summary, field_name="observation.summary", max_length=_MAX_SHORT_TEXT_LENGTH
        )
        object.__setattr__(
            self,
            "data",
            _freeze_json_object(self.data, path="observation.data"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the canonical JSON-compatible representation."""
        return {
            "summary": self.summary,
            "data": _to_json_object(self.data, path="observation.data"),
        }


# --------------------------------------------------------------------------
# Execution result.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Outcome of one capability invocation.

    ``succeeded=True`` means only that the invocation produced an execution
    result with attached observation evidence. It must never be read as proof
    that a requested external state transition happened; that verdict belongs
    to :class:`VerificationResult` from a separate ``verify`` call.
    """

    succeeded: bool
    message: str
    observation: CapabilityObservation

    def __post_init__(self) -> None:
        if type(self.succeeded) is not bool:
            raise TypeError(f"succeeded must be bool, got {type(self.succeeded).__name__}")
        _validate_text(
            self.message, field_name="execution.message", max_length=_MAX_SHORT_TEXT_LENGTH
        )
        if not isinstance(self.observation, CapabilityObservation):
            raise TypeError(
                "observation must be a CapabilityObservation, "
                f"got {type(self.observation).__name__}"
            )


# --------------------------------------------------------------------------
# Verification result.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Separate verdict on whether expected state/postconditions were achieved.

    Verification is determined after execution against the invocation's
    observation evidence. A verdict describes one check only: it never grants
    future permission and never authorizes another action.
    """

    passed: bool
    detail: str

    def __post_init__(self) -> None:
        if type(self.passed) is not bool:
            raise TypeError(f"passed must be bool, got {type(self.passed).__name__}")
        _validate_text(
            self.detail, field_name="verification.detail", max_length=_MAX_LONG_TEXT_LENGTH
        )


# --------------------------------------------------------------------------
# Rollback declaration.
# --------------------------------------------------------------------------


class RollbackSupport(StrEnum):
    """Controlled rollback-support vocabulary for a capability."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class RollbackDeclaration:
    """Explicit declaration of whether an operation is reversible.

    The declaration is descriptive only: it performs no rollback, orchestrates
    no recovery, and grants no authority. Not every operation is reversible,
    and this contract refuses to pretend otherwise.
    """

    support: RollbackSupport
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.support, RollbackSupport):
            raise TypeError(f"support must be a RollbackSupport, got {type(self.support).__name__}")
        _validate_text(self.detail, field_name="rollback.detail", max_length=_MAX_LONG_TEXT_LENGTH)


# --------------------------------------------------------------------------
# Resource / cost estimate.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResourceEstimate:
    """Narrow descriptive estimate of the expected cost of one invocation.

    The estimate reuses plain standard-library value types only; it does not
    duplicate the kernel :class:`~agentx.kernel.resource_budget.ResourceEnvelope`
    or :class:`~agentx.kernel.resource_budget.ResourceBudget` contracts. An
    estimate is advisory data for future budgeting: it grants no budget and
    can never enlarge a resource envelope.
    """

    wall_clock: timedelta
    machine_actions: int
    external_cost: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.wall_clock, timedelta):
            raise TypeError(f"wall_clock must be a timedelta, got {type(self.wall_clock).__name__}")
        if self.wall_clock < timedelta(0):
            raise CapabilityValidationError("wall_clock must not be negative")
        _validate_counter(self.machine_actions, field_name="machine_actions")
        if not isinstance(self.external_cost, Decimal):
            raise TypeError(
                f"external_cost must be a Decimal, got {type(self.external_cost).__name__}"
            )
        if not self.external_cost.is_finite():
            raise CapabilityValidationError("external_cost must be finite")
        if self.external_cost < 0:
            raise CapabilityValidationError("external_cost must not be negative")

    @classmethod
    def zero(cls) -> ResourceEstimate:
        """Return an explicit zero-cost estimate for side-effect-free reads."""
        return cls(
            wall_clock=timedelta(0),
            machine_actions=0,
            external_cost=Decimal("0"),
        )


# --------------------------------------------------------------------------
# Capability descriptor.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """Inert description of one governed capability.

    The descriptor binds every declarative facet of the ABI: identity (with
    explicit version), human description, platform scope, required canonical
    permissions, canonical risk assessment, explicit preconditions, rollback
    declaration, and resource estimate. Every field is required so that no
    facet is ever implicit.

    A descriptor never authorizes execution. Required permissions name the
    canonical :class:`~agentx.kernel.permissions.Permission` values the future
    Trusted Kernel must check; the risk assessment reuses the canonical
    :class:`~agentx.kernel.risk.RiskAssessment` and never duplicates the
    ``R0``-``R4`` model. Description text, however suggestive, cannot widen
    permissions or lower risk.
    """

    identity: CapabilityIdentity
    description: str
    scope: CapabilityScope
    required_permissions: frozenset[Permission]
    risk_assessment: RiskAssessment
    preconditions: tuple[CapabilityPrecondition, ...]
    rollback: RollbackDeclaration
    estimate: ResourceEstimate

    def __post_init__(self) -> None:
        if not isinstance(self.identity, CapabilityIdentity):
            raise TypeError(
                f"identity must be a CapabilityIdentity, got {type(self.identity).__name__}"
            )
        _validate_text(
            self.description,
            field_name="descriptor.description",
            max_length=_MAX_LONG_TEXT_LENGTH,
        )
        if not isinstance(self.scope, CapabilityScope):
            raise TypeError(f"scope must be a CapabilityScope, got {type(self.scope).__name__}")
        if not isinstance(self.required_permissions, frozenset):
            raise TypeError("required_permissions must be a frozenset of Permission values")
        for permission in self.required_permissions:
            if not isinstance(permission, Permission):
                raise TypeError("required_permissions must contain only Permission values")
        if not isinstance(self.risk_assessment, RiskAssessment):
            raise TypeError(
                "risk_assessment must be a RiskAssessment, "
                f"got {type(self.risk_assessment).__name__}"
            )
        if not isinstance(self.preconditions, tuple):
            raise TypeError(
                "preconditions must be a tuple of CapabilityPrecondition, "
                f"got {type(self.preconditions).__name__}"
            )
        for precondition in self.preconditions:
            if not isinstance(precondition, CapabilityPrecondition):
                raise TypeError(
                    "preconditions must contain only CapabilityPrecondition values, "
                    f"got {type(precondition).__name__}"
                )
        if not isinstance(self.rollback, RollbackDeclaration):
            raise TypeError(
                f"rollback must be a RollbackDeclaration, got {type(self.rollback).__name__}"
            )
        if not isinstance(self.estimate, ResourceEstimate):
            raise TypeError(
                f"estimate must be a ResourceEstimate, got {type(self.estimate).__name__}"
            )


# --------------------------------------------------------------------------
# Capability operation boundary.
# --------------------------------------------------------------------------


class Capability(Protocol[P]):
    """Typed operation boundary for one governed capability.

    ``execute`` invokes the machine operation and returns an
    :class:`ExecutionResult` carrying observation evidence. ``verify``
    separately determines whether the expected state/postconditions were
    achieved and returns a :class:`VerificationResult`.

    Both methods receive the canonical
    :class:`~agentx.core.execution.ExecutionContext` as execution-scoped
    context only; the context grants no authority. Neither method mutates Task
    state, invokes the Action Gate, consumes budget, publishes events, or
    performs rollback orchestration: those belong to future kernel and runtime
    layers. Malformed inputs are programming errors (``TypeError`` /
    :class:`CapabilityValidationError`); expected operational outcomes are
    returned as values, never raised.
    """

    @property
    def descriptor(self) -> CapabilityDescriptor:
        """Return the inert descriptor governing this capability."""
        ...

    def execute(self, request: CapabilityRequest[P], context: ExecutionContext) -> ExecutionResult:
        """Invoke the operation and return its result with observation evidence."""
        ...

    def verify(
        self,
        request: CapabilityRequest[P],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        """Determine whether the expected state was achieved for ``observation``."""
        ...
