"""Cross-device routing, DAG binding, governed handoff and applicability for M13."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from agentx.capabilities.abi import CapabilityIdentity, CapabilityRequest
from agentx.capabilities.device import DeviceDescriptor, DeviceId, DevicePlatform
from agentx.capabilities.device_registry import (
    DeviceEnvironmentIdentity,
    DeviceRegistry,
)
from agentx.capabilities.executor import Executor, ExecutorRequest
from agentx.capabilities.runtime import ApprovalDecisions, ClosedLoopOutcome
from agentx.core.causal_experience import CausalExperience
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.ids import TaskId
from agentx.core.procedure_matching import (
    CapabilityRequirement,
    ProcedureRequirement,
)
from agentx.core.procedures import ProcedureScope, ProcedureScopeDimension
from agentx.core.result import Result
from agentx.core.task_decomposition import TaskDecomposition

__all__ = [
    "CrossDeviceCausalEpisode",
    "CrossDeviceTaskDAG",
    "DeviceCausalStep",
    "DeviceHandoff",
    "DeviceHandoffDirection",
    "DeviceHandoffExecutor",
    "DeviceRequirement",
    "DeviceRouter",
    "DeviceTaskBinding",
    "procedure_requirement_for_device",
]


@dataclass(frozen=True, slots=True)
class DeviceRequirement:
    """Explicit routing facts; absence is never a wildcard for ambiguous devices."""

    capability: CapabilityIdentity
    platform: DevicePlatform | None = None
    device_id: DeviceId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.capability, CapabilityIdentity):
            raise TypeError("capability must be CapabilityIdentity")
        if self.platform is not None and not isinstance(self.platform, DevicePlatform):
            raise TypeError("platform must be DevicePlatform or None")
        if self.device_id is not None and not isinstance(self.device_id, DeviceId):
            raise TypeError("device_id must be DeviceId or None")


class DeviceRouter:
    """Deterministic fail-closed device selection from fresh registry evidence."""

    def __init__(self, registry: DeviceRegistry) -> None:
        if not isinstance(registry, DeviceRegistry):
            raise TypeError("registry must be DeviceRegistry")
        self._registry = registry

    def select(
        self,
        requirement: DeviceRequirement,
        *,
        now: datetime,
        max_age: timedelta,
    ) -> Result[DeviceDescriptor, AgentXError]:
        if not isinstance(requirement, DeviceRequirement):
            raise TypeError("requirement must be DeviceRequirement")
        available = self._registry.available(
            now=now,
            max_age=max_age,
            platform=requirement.platform,
        )
        matches: list[DeviceDescriptor] = []
        for descriptor in available:
            if requirement.device_id is not None and descriptor.device_id != requirement.device_id:
                continue
            advertised = self._registry.capability_advertisements(
                descriptor.device_id,
                now=now,
                max_age=max_age,
            )
            if requirement.capability in advertised:
                matches.append(descriptor)
        if not matches:
            return Result.failure(
                AgentXError(
                    code="device.routing.no_applicable_device",
                    message="no fresh available device satisfies the required capability",
                    category=ErrorCategory.PRECONDITION,
                    retryability=Retryability.RETRYABLE,
                )
            )
        if len(matches) != 1:
            return Result.failure(
                AgentXError(
                    code="device.routing.ambiguous",
                    message="multiple devices satisfy the requirement; explicit disambiguation required",
                    category=ErrorCategory.CONFLICT,
                    retryability=Retryability.NON_RETRYABLE,
                    details={"candidate_count": len(matches)},
                )
            )
        return Result.success(matches[0])


@dataclass(frozen=True, slots=True)
class DeviceTaskBinding:
    task_id: TaskId
    requirement: DeviceRequirement

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be TaskId")
        if not isinstance(self.requirement, DeviceRequirement):
            raise TypeError("requirement must be DeviceRequirement")


@dataclass(frozen=True, slots=True)
class CrossDeviceTaskDAG:
    """Canonical TaskDecomposition plus explicit per-node device requirements."""

    decomposition: TaskDecomposition
    bindings: tuple[DeviceTaskBinding, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.decomposition, TaskDecomposition):
            raise TypeError("decomposition must be TaskDecomposition")
        if not isinstance(self.bindings, tuple):
            raise TypeError("bindings must be tuple")
        by_task: dict[TaskId, DeviceRequirement] = {}
        node_ids = {node.task_id for node in self.decomposition.nodes}
        for binding in self.bindings:
            if not isinstance(binding, DeviceTaskBinding):
                raise TypeError("bindings must contain DeviceTaskBinding")
            if binding.task_id not in node_ids:
                raise ValueError("device binding references a task outside the canonical DAG")
            if binding.task_id in by_task:
                raise ValueError("duplicate device binding for task")
            by_task[binding.task_id] = binding.requirement
        missing = node_ids - set(by_task)
        if missing:
            raise ValueError("every cross-device DAG node requires an explicit device binding")

    def requirement_for(self, task_id: TaskId) -> DeviceRequirement:
        if not isinstance(task_id, TaskId):
            raise TypeError("task_id must be TaskId")
        for binding in self.bindings:
            if binding.task_id == task_id:
                return binding.requirement
        raise KeyError(task_id)


class DeviceHandoffDirection(StrEnum):
    PC_TO_PHONE = "pc_to_phone"
    PHONE_TO_PC = "phone_to_pc"
    BROWSER_TO_PHONE = "browser_to_phone"


@dataclass(frozen=True, slots=True)
class DeviceHandoff:
    """Inert handoff lineage. It carries no authority or fresh permission context."""

    direction: DeviceHandoffDirection
    source_device: DeviceId | None
    target_device: DeviceId
    task_id: TaskId
    capability: CapabilityIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.direction, DeviceHandoffDirection):
            raise TypeError("direction must be DeviceHandoffDirection")
        if self.source_device is not None and not isinstance(self.source_device, DeviceId):
            raise TypeError("source_device must be DeviceId or None")
        if not isinstance(self.target_device, DeviceId):
            raise TypeError("target_device must be DeviceId")
        if not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be TaskId")
        if not isinstance(self.capability, CapabilityIdentity):
            raise TypeError("capability must be CapabilityIdentity")


class DeviceHandoffExecutor:
    """Route then delegate the exact canonical ExecutorRequest without authority reset."""

    def __init__(self, *, router: DeviceRouter, executor: Executor) -> None:
        if not isinstance(router, DeviceRouter):
            raise TypeError("router must be DeviceRouter")
        if not isinstance(executor, Executor):
            raise TypeError("executor must be Executor")
        self._router = router
        self._executor = executor

    def execute(
        self,
        *,
        handoff: DeviceHandoff,
        request: ExecutorRequest,
        now: datetime,
        max_age: timedelta,
        approvals: ApprovalDecisions = (),
    ) -> Result[ClosedLoopOutcome, AgentXError]:
        if not isinstance(handoff, DeviceHandoff):
            raise TypeError("handoff must be DeviceHandoff")
        if not isinstance(request, ExecutorRequest):
            raise TypeError("request must be ExecutorRequest")
        if request.task.task_id != handoff.task_id:
            return Result.failure(
                AgentXError(
                    code="device.handoff.task_mismatch",
                    message="handoff task identity does not match canonical ExecutorRequest",
                    category=ErrorCategory.VALIDATION,
                    retryability=Retryability.NON_RETRYABLE,
                )
            )
        if request.capability_request.identity != handoff.capability:
            return Result.failure(
                AgentXError(
                    code="device.handoff.capability_mismatch",
                    message="handoff capability does not match canonical ExecutorRequest",
                    category=ErrorCategory.VALIDATION,
                    retryability=Retryability.NON_RETRYABLE,
                )
            )
        selected = self._router.select(
            DeviceRequirement(
                capability=handoff.capability,
                device_id=handoff.target_device,
            ),
            now=now,
            max_age=max_age,
        )
        if selected.is_failure:
            return Result.failure(selected.unwrap_error())
        target = selected.unwrap()
        serial = getattr(request.capability_request.params, "serial", None)
        if serial is not None and serial != target.device_id.value:
            return Result.failure(
                AgentXError(
                    code="device.handoff.target_mismatch",
                    message="capability request targets a different device than the governed handoff",
                    category=ErrorCategory.VALIDATION,
                    retryability=Retryability.NON_RETRYABLE,
                )
            )
        # Deliberately pass the exact request and its exact ExecutionContext.
        # Authority/budget/stop state remains owned by the existing execution loop.
        return self._executor.execute(request, approvals=approvals)


def procedure_requirement_for_device(
    *,
    descriptor: DeviceDescriptor,
    capability: CapabilityIdentity | None = None,
    base_scope: ProcedureScope | None = None,
) -> ProcedureRequirement:
    """Bind procedure applicability to the exact stable device environment identity."""
    if not isinstance(descriptor, DeviceDescriptor):
        raise TypeError("descriptor must be DeviceDescriptor")
    scope = ProcedureScope() if base_scope is None else base_scope
    if not isinstance(scope, ProcedureScope):
        raise TypeError("base_scope must be ProcedureScope or None")
    environment = DeviceEnvironmentIdentity.from_descriptor(descriptor).to_key()
    existing = scope.value_for(ProcedureScopeDimension.ENVIRONMENT)
    if existing is not None and existing != environment:
        raise ValueError("base procedure scope conflicts with target device environment")
    dimensions = dict(scope.dimensions)
    dimensions[ProcedureScopeDimension.ENVIRONMENT] = environment
    requirement_capability = (
        None
        if capability is None
        else CapabilityRequirement(capability_id=_capability_id(capability), version=str(capability.version))
    )
    return ProcedureRequirement(
        scope=ProcedureScope(dimensions=dimensions),
        capability=requirement_capability,
    )


def _capability_id(identity: CapabilityIdentity) -> object:
    """Convert ABI identity name/version to an existing registered UUID is impossible here.

    Device applicability is primarily represented through the canonical ENVIRONMENT
    scope. Capability identity matching remains the responsibility of callers that
    already possess the canonical CapabilityId binding.
    """
    from agentx.core.ids import CapabilityId

    # This helper is intentionally unreachable for now because ABI identities are
    # names/versions whereas ProcedureRequirement uses registry UUID identity.
    # Fail closed instead of fabricating an ID from external text.
    raise ValueError(
        "capability applicability requires the caller's canonical CapabilityId binding; "
        f"cannot derive one from {identity}"
    )


@dataclass(frozen=True, slots=True)
class DeviceCausalStep:
    device_id: DeviceId
    experience: CausalExperience

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, DeviceId):
            raise TypeError("device_id must be DeviceId")
        if not isinstance(self.experience, CausalExperience):
            raise TypeError("experience must be CausalExperience")


@dataclass(frozen=True, slots=True)
class CrossDeviceCausalEpisode:
    """Ordered canonical causal experiences spanning device boundaries."""

    steps: tuple[DeviceCausalStep, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.steps, tuple) or not self.steps:
            raise ValueError("steps must be a non-empty tuple")
        first = self.steps[0]
        if not isinstance(first, DeviceCausalStep):
            raise TypeError("steps must contain DeviceCausalStep")
        correlation_id = first.experience.correlation_id
        task_id = first.experience.task_id
        for step in self.steps:
            if not isinstance(step, DeviceCausalStep):
                raise TypeError("steps must contain DeviceCausalStep")
            if step.experience.correlation_id != correlation_id:
                raise ValueError("cross-device causal episode must preserve correlation lineage")
            if step.experience.task_id != task_id:
                raise ValueError("cross-device causal episode must preserve task lineage")

    @property
    def devices(self) -> tuple[DeviceId, ...]:
        ordered: list[DeviceId] = []
        for step in self.steps:
            if not ordered or ordered[-1] != step.device_id:
                ordered.append(step.device_id)
        return tuple(ordered)
