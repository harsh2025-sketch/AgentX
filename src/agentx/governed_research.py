"""Governed research-acquisition capability used by the clean L5 boundary.

The A4.04 ResearchAcquisitionPort is data exchange only and intentionally owns
no authority.  This adapter is the composition point that makes an acquisition
an ordinary canonical capability operation, so the existing Executor/A1.10 path
owns permission, risk, budget, emergency-stop, cancellation and verification.

LOCAL mode is only for an explicitly side-effect-free in-process/local port.
EXTERNAL mode conservatively declares an external effect (R3) and therefore
remains confirmation-gated by the canonical ActionGate.  Provider output is
serialized as untrusted observation data; verification proves only that a
canonical response for the submitted request was returned.  It never verifies
the truth of external evidence or promotes knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from agentx.capabilities.abi import (
    CapabilityDescriptor,
    CapabilityIdentity,
    CapabilityName,
    CapabilityObservation,
    CapabilityParams,
    CapabilityPlatform,
    CapabilityRequest,
    CapabilityScope,
    CapabilityVersion,
    ExecutionResult,
    ResourceEstimate,
    RollbackDeclaration,
    RollbackSupport,
    VerificationResult,
)
from agentx.cognition.research_acquisition import (
    ResearchAcquisitionPort,
    validate_acquisition_response,
)
from agentx.cognition.research_provider import (
    ResearchProviderAvailability,
    ResearchRequest,
    research_response_from_json,
)
from agentx.core.execution import ExecutionContext
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk

__all__ = [
    "ResearchAcquisitionMode",
    "ResearchAcquireParams",
    "GovernedResearchAcquisitionCapability",
    "governed_research_request",
]


class ResearchAcquisitionMode(StrEnum):
    """Closed transport-risk class selected by trusted composition."""

    LOCAL = "local"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class ResearchAcquireParams(CapabilityParams):
    """Typed request parameters; the research request remains inert data."""

    request: ResearchRequest

    def __post_init__(self) -> None:
        if not isinstance(self.request, ResearchRequest):
            raise TypeError("request must be a ResearchRequest")

    def to_dict(self) -> dict[str, JsonValue]:
        raw = self.request.to_dict()
        # ResearchObjective.to_dict is JSON-compatible by contract.
        return {"request": raw}  # type: ignore[return-value]


def _identity(mode: ResearchAcquisitionMode) -> CapabilityIdentity:
    return CapabilityIdentity(
        name=CapabilityName(f"research.acquire.{mode.value}"),
        version=CapabilityVersion(1, 0, 0),
    )


def _descriptor(mode: ResearchAcquisitionMode) -> CapabilityDescriptor:
    external = mode is ResearchAcquisitionMode.EXTERNAL
    return CapabilityDescriptor(
        identity=_identity(mode),
        description=(
            "Submit one pre-bound canonical research request through an injected "
            "research acquisition port. Returned evidence remains untrusted data."
        ),
        scope=CapabilityScope(platform=CapabilityPlatform.ANY),
        required_permissions=frozenset(
            {Permission.EXTERNAL_EFFECT if external else Permission.READ}
        ),
        risk_assessment=assess_risk(
            read_only=not external,
            modifies_state=False,
            reversible=False,
            external_effect=external,
        ),
        preconditions=(),
        rollback=RollbackDeclaration(
            support=RollbackSupport.NOT_APPLICABLE,
            detail="Research acquisition does not mutate AgentX canonical state.",
        ),
        estimate=ResourceEstimate(
            wall_clock=timedelta(seconds=10),
            machine_actions=1,
            external_cost=Decimal("0"),
        ),
    )


class GovernedResearchAcquisitionCapability:
    """Capability adapter around one explicitly supplied A4.04 acquisition port."""

    __slots__ = ("_descriptor", "_port")

    def __init__(
        self,
        *,
        port: ResearchAcquisitionPort,
        mode: ResearchAcquisitionMode = ResearchAcquisitionMode.EXTERNAL,
    ) -> None:
        if not isinstance(port, ResearchAcquisitionPort):
            raise TypeError("port must satisfy ResearchAcquisitionPort")
        if not isinstance(mode, ResearchAcquisitionMode):
            raise TypeError("mode must be a ResearchAcquisitionMode")
        self._port = port
        self._descriptor = _descriptor(mode)

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[ResearchAcquireParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, ResearchAcquireParams):
            raise TypeError("research capability requires ResearchAcquireParams")
        stop = context.observe_stop()
        if stop.should_stop:
            return ExecutionResult(
                succeeded=False,
                message="research acquisition stopped before provider invocation",
                observation=CapabilityObservation(
                    summary="research acquisition stopped",
                    data={"request_id": request.params.request.request_id},
                ),
            )
        try:
            response = validate_acquisition_response(
                request.params.request,
                self._port.acquire(request.params.request),
            )
        except Exception:
            return ExecutionResult(
                succeeded=False,
                message="research provider failed at the acquisition boundary",
                observation=CapabilityObservation(
                    summary="research provider failure",
                    data={"request_id": request.params.request.request_id},
                ),
            )
        available = response.availability is ResearchProviderAvailability.AVAILABLE
        return ExecutionResult(
            succeeded=available,
            message=(
                "research provider returned a canonical available response"
                if available
                else "research provider did not serve the request"
            ),
            observation=CapabilityObservation(
                summary="untrusted research acquisition response",
                data={
                    "request_id": response.request_id,
                    "provider_id": response.research_provider_id.research_provider_id,
                    "availability": response.availability.value,
                    "evidence_count": len(response.evidence),
                    "response_json": response.to_json(),
                    "research_verified": False,
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[ResearchAcquireParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, ResearchAcquireParams):
            raise TypeError("research capability requires ResearchAcquireParams")
        raw = observation.data.get("response_json")
        if not isinstance(raw, str):
            return VerificationResult(
                passed=False,
                detail="no canonical research response was observed",
            )
        try:
            response = research_response_from_json(raw)
        except Exception:
            return VerificationResult(
                passed=False,
                detail="observed research response is not canonical",
            )
        passed = (
            response.request_id == request.params.request.request_id
            and response.availability is ResearchProviderAvailability.AVAILABLE
            and observation.data.get("research_verified") is False
        )
        return VerificationResult(
            passed=passed,
            detail=(
                "canonical acquisition response matches the request; evidence remains unverified"
                if passed
                else "acquisition response did not satisfy the structural request check"
            ),
        )


def governed_research_request(
    request: ResearchRequest,
    *,
    mode: ResearchAcquisitionMode = ResearchAcquisitionMode.EXTERNAL,
) -> CapabilityRequest[ResearchAcquireParams]:
    """Build the exact typed request for a governed research capability."""

    if not isinstance(request, ResearchRequest):
        raise TypeError("request must be a ResearchRequest")
    if not isinstance(mode, ResearchAcquisitionMode):
        raise TypeError("mode must be a ResearchAcquisitionMode")
    return CapabilityRequest(
        identity=_identity(mode),
        params=ResearchAcquireParams(request=request),
    )
