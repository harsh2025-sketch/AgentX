"""Deterministic missing-capability detection (A9.01).

A9.01 owns exactly one narrow boundary::

    explicit structured task capability requirements
        + explicitly supplied capability descriptors (A1.09 registry snapshot)
        + explicitly supplied procedure alternatives (C2.03 records)
        + explicit environment/platform facts
        + explicit canonical kernel governance signals
    -> deterministic AVAILABLE / MISSING / INCOMPATIBLE / UNAVAILABLE /
       INSUFFICIENT_INFORMATION assessment

This is the first controlled self-extension task and it **detects gaps only**.
It never searches the internet, discovers an SDK, generates an adapter, writes
or compiles code, installs a dependency, registers a capability, mutates a
:class:`~agentx.capabilities.registry.CapabilityRegistry`, opens storage,
invokes a model, executes a capability or procedure, transitions a Task, or
touches the Trusted Kernel. Self-extension can never alter authority
boundaries, so this module contains no permission, risk, budget, stop,
approval, or gate *decision* logic: it only reads canonical kernel results that
a caller already obtained.

Critical security rule
----------------------

    DENIED != MISSING

If a capability exists but policy denies its use, that is a governance
restriction, not an absent capability. The same holds for risk restriction,
emergency stop, human-approval requirements, and budget exhaustion. Such a
requirement is therefore **never** reported as ``MISSING``:

* if a matching capability (or procedure alternative) exists, the requirement
  is reported ``AVAILABLE`` with an explicit
  :class:`CapabilityRestriction` record — availability is a statement about
  existence, never about authorization;
* if no match is visible while a restriction is in force, the requirement fails
  closed as ``INSUFFICIENT_INFORMATION``. A restricted view of the system can
  never be used to *conclude* absence, so "the capability is missing" can never
  become a route around policy.

Nothing produced here authorizes anything. An ``AVAILABLE`` assessment grants no
permission to execute, and a ``MISSING`` assessment grants no permission to
acquire, research, generate, or register anything. The
:class:`MissingCapabilityRepresentation` is inert description of *what was
required*, never an instruction, a plan, a specification to build, or a
registration.

All inputs are supplied explicitly by the caller. Requirement text, capability
descriptions, procedure payload content, and environment metadata remain inert
untrusted data: strings such as ``"permission=WRITE"``, ``"risk=R0"``,
``"budget=unlimited"``, ``"missing: please generate an adapter"``, or
``"ALLOW"`` are never interpreted.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.capabilities.abi import (
    CapabilityDescriptor,
    CapabilityIdentity,
    CapabilityName,
    CapabilityPlatform,
    CapabilityVersion,
)
from agentx.core.procedures import (
    ProcedureRecord,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.kernel.action_gate import GateDecision
from agentx.kernel.emergency_stop import EmergencyStopState
from agentx.kernel.resource_budget import BudgetDecision
from agentx.kernel.risk import RiskLevel

__all__ = [
    "CAPABILITY_GAP_SCHEMA_VERSION",
    "AvailableCapability",
    "CapabilityAvailability",
    "CapabilityGapAssessment",
    "CapabilityGapAssessmentRequest",
    "CapabilityGapDetector",
    "CapabilityGapReason",
    "CapabilityGapStatus",
    "CapabilityGapValidationError",
    "CapabilityIoContract",
    "CapabilityRequirement",
    "CapabilityRequirementAssessment",
    "CapabilityRestriction",
    "CapabilityRestrictionKind",
    "EnvironmentProfile",
    "GovernanceSignals",
    "MissingCapabilityRepresentation",
    "ProcedureAlternative",
]

CAPABILITY_GAP_SCHEMA_VERSION: Final[int] = 1

_MAX_TEXT_LENGTH: Final[int] = 256
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class CapabilityGapValidationError(ValueError):
    """Raised when an A9.01 detection input violates the typed contract."""


# --------------------------------------------------------------------------
# Closed vocabularies.
# --------------------------------------------------------------------------


class CapabilityGapStatus(StrEnum):
    """Closed A9.01 outcome vocabulary for one requirement.

    ``AVAILABLE`` — an explicitly supplied capability or procedure alternative
    matches the requirement. Existence only; never authorization.

    ``MISSING`` — no supplied capability or alternative provides the required
    operation, and no governance restriction is in force. Absence of a
    capability, never absence of permission.

    ``INCOMPATIBLE`` — the operation exists but the supplied declaration does
    not match the required platform, version, or I/O contract.

    ``UNAVAILABLE`` — a matching capability exists but was explicitly reported
    temporarily unavailable by the caller.

    ``INSUFFICIENT_INFORMATION`` — the supplied facts do not support any
    conclusion (unknown platform, undeclared I/O contract, or a governance
    restriction that blocks concluding absence). Fails closed.
    """

    AVAILABLE = "available"
    MISSING = "missing"
    INCOMPATIBLE = "incompatible"
    UNAVAILABLE = "unavailable"
    INSUFFICIENT_INFORMATION = "insufficient_information"


class CapabilityGapReason(StrEnum):
    """Closed explanation vocabulary bound to one assessment."""

    REGISTERED_CAPABILITY_MATCHES = "registered_capability_matches"
    PROCEDURE_ALTERNATIVE_MATCHES = "procedure_alternative_matches"
    NO_PROVIDER_FOR_OPERATION = "no_provider_for_operation"
    PLATFORM_INCOMPATIBLE = "platform_incompatible"
    VERSION_INCOMPATIBLE = "version_incompatible"
    IO_CONTRACT_INCOMPATIBLE = "io_contract_incompatible"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    UNKNOWN_ENVIRONMENT = "unknown_environment"
    UNDECLARED_IO_CONTRACT = "undeclared_io_contract"
    GOVERNANCE_RESTRICTED = "governance_restricted"


class CapabilityRestrictionKind(StrEnum):
    """Closed vocabulary of governance restrictions.

    Every member means *the system declines to use an operation right now*. No
    member ever means *the operation is absent*.
    """

    PERMISSION_DENIED = "permission_denied"
    HUMAN_APPROVAL_REQUIRED = "human_approval_required"
    RISK_RESTRICTED = "risk_restricted"
    EMERGENCY_STOP = "emergency_stop"
    BUDGET_EXHAUSTED = "budget_exhausted"


class CapabilityAvailability(StrEnum):
    """Explicit caller-reported operational availability of one capability."""

    OPERATIONAL = "operational"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------
# Small validators.
# --------------------------------------------------------------------------


def _validate_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise CapabilityGapValidationError(f"{field_name} must be non-empty and trimmed")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise CapabilityGapValidationError(f"{field_name} must not contain control characters")
    if len(value) > _MAX_TEXT_LENGTH:
        raise CapabilityGapValidationError(
            f"{field_name} must not exceed {_MAX_TEXT_LENGTH} characters"
        )
    return value


def _validate_optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _validate_text(value, field_name=field_name)


def _require_tuple_of(value: object, kind: type, *, field_name: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple, got {type(value).__name__}")
    for item in value:
        if not isinstance(item, kind):
            raise CapabilityGapValidationError(
                f"{field_name} entries must be {kind.__name__} values"
            )
    return value


def _version_tuple(version: CapabilityVersion) -> tuple[int, int, int]:
    return (version.major, version.minor, version.patch)


# --------------------------------------------------------------------------
# Requirement-side contracts.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilityIoContract:
    """Input/output characteristics of an operation, where known.

    Both fields are optional because a structured task requirement often knows
    only part of the shape. ``None`` means *not known*, never *anything goes*:
    an unknown side is never treated as a mismatch, and a requirement that
    knows a side while the supplied capability declares nothing yields
    ``INSUFFICIENT_INFORMATION`` rather than a guess.

    The values are opaque caller-chosen labels (for example ``"file_path"`` or
    ``"process_list"``). This module compares them exactly and never parses,
    coerces, infers, or interprets them.
    """

    input_kind: str | None = None
    output_kind: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "input_kind", _validate_optional_text(self.input_kind, field_name="input_kind")
        )
        object.__setattr__(
            self, "output_kind", _validate_optional_text(self.output_kind, field_name="output_kind")
        )

    @property
    def is_empty(self) -> bool:
        """Return whether neither side of the contract is known."""

        return self.input_kind is None and self.output_kind is None


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentProfile:
    """Explicit environment/platform facts for one assessment.

    ``platform`` is ``None`` when the caller does not know the platform. An
    unknown platform is never assumed, guessed, or detected here: requirements
    that depend on it resolve to ``INSUFFICIENT_INFORMATION``.
    """

    platform: CapabilityPlatform | None = None
    environment_id: str | None = None

    def __post_init__(self) -> None:
        if self.platform is not None and not isinstance(self.platform, CapabilityPlatform):
            raise TypeError("platform must be a CapabilityPlatform or None")
        object.__setattr__(
            self,
            "environment_id",
            _validate_optional_text(self.environment_id, field_name="environment_id"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilityRequirement:
    """One explicit structured capability requirement of a task.

    ``operation`` is the canonical required operation identity (an A1.08
    :class:`~agentx.capabilities.abi.CapabilityName`); ``category`` is an
    optional coarse grouping label used only for description. Neither is
    interpreted, resolved, or looked up anywhere outside the explicitly
    supplied inputs.
    """

    requirement_id: str
    operation: CapabilityName
    category: str | None = None
    minimum_version: CapabilityVersion | None = None
    platform: CapabilityPlatform | None = None
    io_contract: CapabilityIoContract = CapabilityIoContract()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "requirement_id", _validate_text(self.requirement_id, field_name="requirement_id")
        )
        if not isinstance(self.operation, CapabilityName):
            raise TypeError("operation must be a CapabilityName")
        object.__setattr__(
            self, "category", _validate_optional_text(self.category, field_name="category")
        )
        if self.minimum_version is not None and not isinstance(
            self.minimum_version, CapabilityVersion
        ):
            raise TypeError("minimum_version must be a CapabilityVersion or None")
        if self.platform is not None and not isinstance(self.platform, CapabilityPlatform):
            raise TypeError("platform must be a CapabilityPlatform or None")
        if not isinstance(self.io_contract, CapabilityIoContract):
            raise TypeError("io_contract must be a CapabilityIoContract")


# --------------------------------------------------------------------------
# Supply-side contracts.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class AvailableCapability:
    """One capability the caller explicitly supplied from a registry snapshot.

    The A1.09 registry is never opened, queried, or mutated here: the caller
    passes descriptors it already obtained. A descriptor is inert data, and
    listing one grants nothing.
    """

    descriptor: CapabilityDescriptor
    io_contract: CapabilityIoContract = CapabilityIoContract()
    availability: CapabilityAvailability = CapabilityAvailability.UNKNOWN

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, CapabilityDescriptor):
            raise TypeError("descriptor must be a CapabilityDescriptor")
        if not isinstance(self.io_contract, CapabilityIoContract):
            raise TypeError("io_contract must be a CapabilityIoContract")
        if not isinstance(self.availability, CapabilityAvailability):
            raise TypeError("availability must be a CapabilityAvailability")

    @property
    def identity(self) -> CapabilityIdentity:
        """Return the descriptor's canonical identity."""

        return self.descriptor.identity


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureAlternative:
    """An explicitly supplied procedure revision offered for one operation.

    The C2.03 record is embedded by value and never parsed, compiled,
    activated, executed, or promoted. Only its canonical status and scope
    fields are read; ``payload`` content stays opaque. The caller states which
    operation the procedure is being offered for — this module never infers it
    from payload text.

    Only ``ACTIVE`` revisions can satisfy a requirement: a ``CANDIDATE`` or
    ``RETIRED`` revision is inert history and satisfies nothing.
    """

    operation: CapabilityName
    record: ProcedureRecord

    def __post_init__(self) -> None:
        if not isinstance(self.operation, CapabilityName):
            raise TypeError("operation must be a CapabilityName")
        if not isinstance(self.record, ProcedureRecord):
            raise TypeError("record must be a ProcedureRecord")


@dataclass(frozen=True, slots=True, kw_only=True)
class GovernanceSignals:
    """Canonical kernel outcomes the caller already obtained, as inert data.

    A9.01 evaluates no permission, no risk, no budget, and no stop state: it
    only *reads* results produced by the Trusted Kernel. Every populated field
    describes a restriction on *using* an operation and never an absence of
    one.

    ``restricted_risk_level`` records that policy currently restricts the risk
    level of the required operation; it is descriptive and can never lower or
    raise risk anywhere.
    """

    gate_decision: GateDecision | None = None
    budget_decision: BudgetDecision | None = None
    emergency_stop_state: EmergencyStopState | None = None
    restricted_risk_level: RiskLevel | None = None

    def __post_init__(self) -> None:
        if self.gate_decision is not None and not isinstance(self.gate_decision, GateDecision):
            raise TypeError("gate_decision must be a GateDecision or None")
        if self.budget_decision is not None and not isinstance(
            self.budget_decision, BudgetDecision
        ):
            raise TypeError("budget_decision must be a BudgetDecision or None")
        if self.emergency_stop_state is not None and not isinstance(
            self.emergency_stop_state, EmergencyStopState
        ):
            raise TypeError("emergency_stop_state must be an EmergencyStopState or None")
        if self.restricted_risk_level is not None and not isinstance(
            self.restricted_risk_level, RiskLevel
        ):
            raise TypeError("restricted_risk_level must be a RiskLevel or None")

    def restrictions(self) -> tuple[CapabilityRestriction, ...]:
        """Return the explicit restrictions implied by these canonical signals."""

        found: list[CapabilityRestriction] = []
        if self.gate_decision is GateDecision.DENY:
            found.append(
                CapabilityRestriction(
                    kind=CapabilityRestrictionKind.PERMISSION_DENIED,
                    detail="ActionGate returned DENY for the required operation.",
                )
            )
        elif self.gate_decision is GateDecision.REQUIRE_CONFIRMATION:
            found.append(
                CapabilityRestriction(
                    kind=CapabilityRestrictionKind.HUMAN_APPROVAL_REQUIRED,
                    detail="ActionGate returned REQUIRE_CONFIRMATION for the required operation.",
                )
            )
        if self.restricted_risk_level is not None:
            found.append(
                CapabilityRestriction(
                    kind=CapabilityRestrictionKind.RISK_RESTRICTED,
                    detail=(
                        "Policy currently restricts the required operation at risk level "
                        f"{self.restricted_risk_level.value}."
                    ),
                )
            )
        if self.emergency_stop_state is EmergencyStopState.STOP_REQUESTED:
            found.append(
                CapabilityRestriction(
                    kind=CapabilityRestrictionKind.EMERGENCY_STOP,
                    detail="Emergency stop has been requested.",
                )
            )
        if self.budget_decision is BudgetDecision.DENY:
            found.append(
                CapabilityRestriction(
                    kind=CapabilityRestrictionKind.BUDGET_EXHAUSTED,
                    detail="The resource budget denied the required operation.",
                )
            )
        return tuple(sorted(found, key=lambda item: item.kind.value))


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilityRestriction:
    """One inert record that governance declines use of an operation.

    A restriction is never a gap. It exists so a caller can see *why* a
    requirement was not concluded missing, and it authorizes nothing, clears
    nothing, and reverses nothing.
    """

    kind: CapabilityRestrictionKind
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CapabilityRestrictionKind):
            raise TypeError("kind must be a CapabilityRestrictionKind")
        object.__setattr__(self, "detail", _validate_text(self.detail, field_name="detail"))


# --------------------------------------------------------------------------
# Result contracts.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class MissingCapabilityRepresentation:
    """Inert structured description of one requirement found MISSING.

    It records the required operation/category, the input/output
    characteristics where known, the platform/environment the requirement
    applies to, the closed reason, the evidence considered, and the scope in
    which the conclusion holds.

    It is a description, not a work order. There is deliberately no field or
    method naming an SDK, dependency, adapter, package, endpoint, source code,
    installation, registration, or approval: A9.01 detects gaps and nothing
    else, and later self-extension tasks are not enabled by this record.
    """

    operation: CapabilityName
    category: str | None
    io_contract: CapabilityIoContract
    platform: CapabilityPlatform | None
    environment_id: str | None
    reason: CapabilityGapReason
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.operation, CapabilityName):
            raise TypeError("operation must be a CapabilityName")
        object.__setattr__(
            self, "category", _validate_optional_text(self.category, field_name="category")
        )
        if not isinstance(self.io_contract, CapabilityIoContract):
            raise TypeError("io_contract must be a CapabilityIoContract")
        if self.platform is not None and not isinstance(self.platform, CapabilityPlatform):
            raise TypeError("platform must be a CapabilityPlatform or None")
        object.__setattr__(
            self,
            "environment_id",
            _validate_optional_text(self.environment_id, field_name="environment_id"),
        )
        if not isinstance(self.reason, CapabilityGapReason):
            raise TypeError("reason must be a CapabilityGapReason")
        evidence = _require_tuple_of(self.evidence, str, field_name="evidence")
        for item in evidence:
            _validate_text(item, field_name="evidence entry")

    @property
    def scope(self) -> str:
        """Return the human-readable scope in which the gap was concluded.

        The scope is deliberately narrow: one operation, on one platform, in
        one environment, against exactly the evidence supplied to this
        assessment. It never generalizes to other tasks, environments, or
        future states of the system.
        """

        platform = "unknown" if self.platform is None else self.platform.value
        environment = "unspecified" if self.environment_id is None else self.environment_id
        return f"operation={self.operation.value};platform={platform};environment={environment}"


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilityRequirementAssessment:
    """Deterministic result for one explicit requirement.

    ``status`` and ``reason`` are the whole verdict. ``matched_identity`` names
    the supplied capability that matched (when one did) and remains a name, not
    an authorization. ``restrictions`` records governance facts that were in
    force; their presence structurally forbids a ``MISSING`` status.
    """

    requirement: CapabilityRequirement
    status: CapabilityGapStatus
    reason: CapabilityGapReason
    matched_identity: CapabilityIdentity | None = None
    restrictions: tuple[CapabilityRestriction, ...] = ()
    missing_representation: MissingCapabilityRepresentation | None = None
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.requirement, CapabilityRequirement):
            raise TypeError("requirement must be a CapabilityRequirement")
        if not isinstance(self.status, CapabilityGapStatus):
            raise TypeError("status must be a CapabilityGapStatus")
        if not isinstance(self.reason, CapabilityGapReason):
            raise TypeError("reason must be a CapabilityGapReason")
        if self.matched_identity is not None and not isinstance(
            self.matched_identity, CapabilityIdentity
        ):
            raise TypeError("matched_identity must be a CapabilityIdentity or None")
        _require_tuple_of(self.restrictions, CapabilityRestriction, field_name="restrictions")
        if self.missing_representation is not None and not isinstance(
            self.missing_representation, MissingCapabilityRepresentation
        ):
            raise TypeError(
                "missing_representation must be a MissingCapabilityRepresentation or None"
            )
        evidence = _require_tuple_of(self.evidence, str, field_name="evidence")
        for item in evidence:
            _validate_text(item, field_name="evidence entry")

        is_missing = self.status is CapabilityGapStatus.MISSING
        if is_missing and self.restrictions:
            raise CapabilityGapValidationError(
                "a restricted requirement must never be reported MISSING: "
                "denial, risk restriction, emergency stop and budget exhaustion "
                "are not missing capabilities"
            )
        if is_missing and self.missing_representation is None:
            raise CapabilityGapValidationError(
                "a MISSING assessment must carry a MissingCapabilityRepresentation"
            )
        if not is_missing and self.missing_representation is not None:
            raise CapabilityGapValidationError(
                "only a MISSING assessment may carry a MissingCapabilityRepresentation"
            )
        if is_missing and self.missing_representation is not None:
            if self.missing_representation.operation != self.requirement.operation:
                raise CapabilityGapValidationError(
                    "missing_representation.operation must match the requirement operation"
                )
            if self.missing_representation.reason is not self.reason:
                raise CapabilityGapValidationError(
                    "missing_representation.reason must match the assessment reason"
                )

    @property
    def is_missing(self) -> bool:
        """Return whether the requirement was concluded to be a capability gap."""

        return self.status is CapabilityGapStatus.MISSING

    @property
    def is_restricted(self) -> bool:
        """Return whether governance restrictions were in force (never a gap)."""

        return bool(self.restrictions)


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilityGapAssessmentRequest:
    """Explicit immutable input to :class:`CapabilityGapDetector`.

    Every collection is canonicalized so results never depend on incidental
    caller ordering.
    """

    requirements: tuple[CapabilityRequirement, ...]
    available_capabilities: tuple[AvailableCapability, ...] = ()
    procedure_alternatives: tuple[ProcedureAlternative, ...] = ()
    environment: EnvironmentProfile = EnvironmentProfile()
    governance: GovernanceSignals = GovernanceSignals()

    def __post_init__(self) -> None:
        requirements = _require_tuple_of(
            self.requirements, CapabilityRequirement, field_name="requirements"
        )
        if not requirements:
            raise CapabilityGapValidationError("requirements must not be empty")
        seen: set[str] = set()
        for requirement in self.requirements:
            if requirement.requirement_id in seen:
                raise CapabilityGapValidationError(
                    "requirements must have unique requirement_id values: "
                    f"{requirement.requirement_id!r}"
                )
            seen.add(requirement.requirement_id)
        _require_tuple_of(
            self.available_capabilities, AvailableCapability, field_name="available_capabilities"
        )
        identities: set[CapabilityIdentity] = set()
        for capability in self.available_capabilities:
            if capability.identity in identities:
                raise CapabilityGapValidationError(
                    f"available_capabilities must be unique: {capability.identity}"
                )
            identities.add(capability.identity)
        _require_tuple_of(
            self.procedure_alternatives, ProcedureAlternative, field_name="procedure_alternatives"
        )
        if not isinstance(self.environment, EnvironmentProfile):
            raise TypeError("environment must be an EnvironmentProfile")
        if not isinstance(self.governance, GovernanceSignals):
            raise TypeError("governance must be a GovernanceSignals")

        object.__setattr__(
            self,
            "requirements",
            tuple(sorted(self.requirements, key=lambda item: item.requirement_id)),
        )
        object.__setattr__(
            self,
            "available_capabilities",
            tuple(sorted(self.available_capabilities, key=_capability_sort_key)),
        )
        object.__setattr__(
            self,
            "procedure_alternatives",
            tuple(sorted(self.procedure_alternatives, key=_alternative_sort_key)),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class CapabilityGapAssessment:
    """Ephemeral deterministic A9.01 result.

    The assessment is data only. It carries no permission, authority, risk,
    budget, stop, task-transition, model, research, acquisition, generation,
    registration, or persistence field.
    """

    schema_version: int = CAPABILITY_GAP_SCHEMA_VERSION
    requirement_assessments: tuple[CapabilityRequirementAssessment, ...]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise TypeError("schema_version must be an int")
        if self.schema_version != CAPABILITY_GAP_SCHEMA_VERSION:
            raise CapabilityGapValidationError(
                f"unsupported capability gap schema version {self.schema_version}"
            )
        assessments = _require_tuple_of(
            self.requirement_assessments,
            CapabilityRequirementAssessment,
            field_name="requirement_assessments",
        )
        if not assessments:
            raise CapabilityGapValidationError("requirement_assessments must not be empty")
        object.__setattr__(
            self,
            "requirement_assessments",
            tuple(sorted(assessments, key=lambda item: item.requirement.requirement_id)),
        )

    @property
    def missing_requirements(self) -> tuple[CapabilityRequirementAssessment, ...]:
        """Return only the requirements concluded to be genuine capability gaps."""

        return tuple(item for item in self.requirement_assessments if item.is_missing)

    @property
    def restricted_requirements(self) -> tuple[CapabilityRequirementAssessment, ...]:
        """Return requirements held back by governance (explicitly not gaps)."""

        return tuple(item for item in self.requirement_assessments if item.is_restricted)

    @property
    def has_missing_capability(self) -> bool:
        """Return whether any requirement was concluded MISSING."""

        return bool(self.missing_requirements)


def _capability_sort_key(capability: AvailableCapability) -> tuple[str, int, int, int]:
    identity = capability.identity
    return (identity.name.value, *_version_tuple(identity.version))


def _alternative_sort_key(alternative: ProcedureAlternative) -> tuple[str, str, int]:
    return (
        alternative.operation.value,
        alternative.record.procedure_id.to_str(),
        alternative.record.revision,
    )


# --------------------------------------------------------------------------
# Detector.
# --------------------------------------------------------------------------


def _platform_matches(declared: CapabilityPlatform, required: CapabilityPlatform) -> bool:
    if declared is CapabilityPlatform.ANY or required is CapabilityPlatform.ANY:
        return True
    return declared is required


def _io_verdict(
    required: CapabilityIoContract,
    declared: CapabilityIoContract,
) -> CapabilityGapReason | None:
    """Return a mismatch/insufficiency reason, or ``None`` when compatible."""

    for side in ("input_kind", "output_kind"):
        needed = getattr(required, side)
        if needed is None:
            continue
        offered = getattr(declared, side)
        if offered is None:
            return CapabilityGapReason.UNDECLARED_IO_CONTRACT
        if offered != needed:
            return CapabilityGapReason.IO_CONTRACT_INCOMPATIBLE
    return None


class CapabilityGapDetector:
    """Pure stateless A9.01 detector for explicit capability requirements.

    The detector performs no I/O, persistence, retrieval, registry mutation,
    model invocation, research, code generation, dependency installation,
    capability execution, procedure activation, permission evaluation, or event
    publication. Given identical inputs it always returns identical results.
    """

    __slots__ = ()

    def assess(self, request: CapabilityGapAssessmentRequest) -> CapabilityGapAssessment:
        """Assess every explicit requirement against explicitly supplied facts."""

        if not isinstance(request, CapabilityGapAssessmentRequest):
            raise TypeError("request must be a CapabilityGapAssessmentRequest")
        restrictions = request.governance.restrictions()
        assessments = tuple(
            self._assess_requirement(requirement, request, restrictions)
            for requirement in request.requirements
        )
        return CapabilityGapAssessment(requirement_assessments=assessments)

    def _assess_requirement(
        self,
        requirement: CapabilityRequirement,
        request: CapabilityGapAssessmentRequest,
        restrictions: tuple[CapabilityRestriction, ...],
    ) -> CapabilityRequirementAssessment:
        environment = request.environment
        required_platform = (
            requirement.platform if requirement.platform is not None else environment.platform
        )

        if required_platform is None:
            return self._insufficient(
                requirement,
                CapabilityGapReason.UNKNOWN_ENVIRONMENT,
                restrictions,
                evidence=("no platform was supplied by the requirement or the environment",),
            )

        named = tuple(
            capability
            for capability in request.available_capabilities
            if capability.identity.name == requirement.operation
        )
        evidence: list[str] = [
            f"supplied capability declarations for operation: {len(named)}",
            f"supplied procedure alternatives: {len(request.procedure_alternatives)}",
        ]

        compatible: list[AvailableCapability] = []
        platform_mismatch = False
        version_mismatch = False
        io_mismatch = False
        io_undeclared = False

        for capability in named:
            descriptor = capability.descriptor
            if not _platform_matches(descriptor.scope.platform, required_platform):
                platform_mismatch = True
                evidence.append(
                    f"{descriptor.identity} declares platform "
                    f"{descriptor.scope.platform.value}, required {required_platform.value}"
                )
                continue
            if requirement.minimum_version is not None and _version_tuple(
                descriptor.identity.version
            ) < _version_tuple(requirement.minimum_version):
                version_mismatch = True
                evidence.append(
                    f"{descriptor.identity} is below required minimum version "
                    f"{requirement.minimum_version.to_str()}"
                )
                continue
            io_reason = _io_verdict(requirement.io_contract, capability.io_contract)
            if io_reason is CapabilityGapReason.IO_CONTRACT_INCOMPATIBLE:
                io_mismatch = True
                evidence.append(f"{descriptor.identity} declares an incompatible I/O contract")
                continue
            if io_reason is CapabilityGapReason.UNDECLARED_IO_CONTRACT:
                io_undeclared = True
                evidence.append(f"{descriptor.identity} declares no comparable I/O contract")
                continue
            compatible.append(capability)

        if compatible:
            best = max(compatible, key=lambda item: _version_tuple(item.identity.version))
            if best.availability is CapabilityAvailability.TEMPORARILY_UNAVAILABLE:
                operational = tuple(
                    item
                    for item in compatible
                    if item.availability is not CapabilityAvailability.TEMPORARILY_UNAVAILABLE
                )
                if not operational:
                    evidence.append(
                        f"{best.identity} was explicitly reported temporarily unavailable"
                    )
                    return CapabilityRequirementAssessment(
                        requirement=requirement,
                        status=CapabilityGapStatus.UNAVAILABLE,
                        reason=CapabilityGapReason.TEMPORARILY_UNAVAILABLE,
                        matched_identity=best.identity,
                        restrictions=restrictions,
                        evidence=tuple(evidence),
                    )
                best = max(operational, key=lambda item: _version_tuple(item.identity.version))
            evidence.append(f"{best.identity} matches the requirement")
            return CapabilityRequirementAssessment(
                requirement=requirement,
                status=CapabilityGapStatus.AVAILABLE,
                reason=CapabilityGapReason.REGISTERED_CAPABILITY_MATCHES,
                matched_identity=best.identity,
                restrictions=restrictions,
                evidence=tuple(evidence),
            )

        alternative = self._matching_alternative(requirement, request, required_platform)
        if alternative is not None:
            evidence.append(
                "active procedure revision "
                f"{alternative.record.procedure_id.to_str()}@{alternative.record.revision} "
                "is offered for this operation"
            )
            return CapabilityRequirementAssessment(
                requirement=requirement,
                status=CapabilityGapStatus.AVAILABLE,
                reason=CapabilityGapReason.PROCEDURE_ALTERNATIVE_MATCHES,
                restrictions=restrictions,
                evidence=tuple(evidence),
            )

        if platform_mismatch or version_mismatch or io_mismatch:
            reason = (
                CapabilityGapReason.PLATFORM_INCOMPATIBLE
                if platform_mismatch
                else CapabilityGapReason.VERSION_INCOMPATIBLE
                if version_mismatch
                else CapabilityGapReason.IO_CONTRACT_INCOMPATIBLE
            )
            return CapabilityRequirementAssessment(
                requirement=requirement,
                status=CapabilityGapStatus.INCOMPATIBLE,
                reason=reason,
                restrictions=restrictions,
                evidence=tuple(evidence),
            )

        if io_undeclared:
            return self._insufficient(
                requirement,
                CapabilityGapReason.UNDECLARED_IO_CONTRACT,
                restrictions,
                evidence=tuple(evidence),
            )

        if restrictions:
            # DENIED != MISSING. A restricted view can never conclude absence.
            return self._insufficient(
                requirement,
                CapabilityGapReason.GOVERNANCE_RESTRICTED,
                restrictions,
                evidence=(
                    *evidence,
                    "governance restrictions are in force; absence cannot be concluded",
                ),
            )

        representation = MissingCapabilityRepresentation(
            operation=requirement.operation,
            category=requirement.category,
            io_contract=requirement.io_contract,
            platform=required_platform,
            environment_id=environment.environment_id,
            reason=CapabilityGapReason.NO_PROVIDER_FOR_OPERATION,
            evidence=tuple(evidence),
        )
        return CapabilityRequirementAssessment(
            requirement=requirement,
            status=CapabilityGapStatus.MISSING,
            reason=CapabilityGapReason.NO_PROVIDER_FOR_OPERATION,
            restrictions=(),
            missing_representation=representation,
            evidence=tuple(evidence),
        )

    @staticmethod
    def _matching_alternative(
        requirement: CapabilityRequirement,
        request: CapabilityGapAssessmentRequest,
        required_platform: CapabilityPlatform,
    ) -> ProcedureAlternative | None:
        """Return the first ACTIVE in-scope procedure offered for the operation.

        Only canonical record fields are read. The procedure is neither
        activated nor executed, and its payload is never interpreted.
        """

        for alternative in request.procedure_alternatives:
            if alternative.operation != requirement.operation:
                continue
            record = alternative.record
            if record.status is not ProcedureStatus.ACTIVE:
                continue
            scoped_os = record.scope.value_for(ProcedureScopeDimension.OPERATING_SYSTEM)
            if scoped_os is not None and scoped_os != required_platform.value:
                continue
            return alternative
        return None

    @staticmethod
    def _insufficient(
        requirement: CapabilityRequirement,
        reason: CapabilityGapReason,
        restrictions: tuple[CapabilityRestriction, ...],
        *,
        evidence: tuple[str, ...],
    ) -> CapabilityRequirementAssessment:
        return CapabilityRequirementAssessment(
            requirement=requirement,
            status=CapabilityGapStatus.INSUFFICIENT_INFORMATION,
            reason=reason,
            restrictions=restrictions,
            evidence=evidence,
        )
