"""Unit tests for the A4.04 research acquisition boundary."""

from __future__ import annotations

import dataclasses

import pytest

import agentx.cognition.research_acquisition as acquisition
from agentx.cognition.research_objective import ResearchObjective
from agentx.cognition.research_provider import (
    ResearchProviderAvailability,
    ResearchProviderFailure,
    ResearchProviderIdentity,
    ResearchProviderValidationError,
    ResearchRequest,
    ResearchResponse,
)
from agentx.core.knowledge import ProvenanceKind, ProvenanceReference


def _objective(objective_id: str = "objective-1") -> ResearchObjective:
    return ResearchObjective(
        objective_id=objective_id,
        question="What is the canonical answer?",
    )


def _request(request_id: str = "request-1") -> ResearchRequest:
    return ResearchRequest(request_id=request_id, objective=_objective())


def _identity(provider_id: str = "provider-1") -> ResearchProviderIdentity:
    return ResearchProviderIdentity(research_provider_id=provider_id)


def _response(
    request_id: str = "request-1",
    *,
    provider_id: str = "provider-1",
    availability: ResearchProviderAvailability = ResearchProviderAvailability.AVAILABLE,
    evidence: tuple[ProvenanceReference, ...] = (),
    failure: ResearchProviderFailure | None = None,
) -> ResearchResponse:
    return ResearchResponse(
        request_id=request_id,
        research_provider_id=_identity(provider_id),
        availability=availability,
        evidence=evidence,
        failure=failure,
    )


class _DeterministicPort:
    """Inert deterministic stand-in used to exercise the port protocol in tests.

    It performs no I/O: it returns a fixed canonical response built from the
    submitted request. A4.04 ships no real provider; this is test scaffolding.
    """

    def __init__(self, response: ResearchResponse | None = None) -> None:
        self._response = response
        self.seen: list[ResearchRequest] = []

    def acquire(self, request: ResearchRequest) -> ResearchResponse:
        self.seen.append(request)
        if self._response is not None:
            return self._response
        return _response(request_id=request.request_id)


def test_port_is_protocol_with_single_acquire_member() -> None:
    assert hasattr(acquisition.ResearchAcquisitionPort, "acquire")
    assert not dataclasses.is_dataclass(acquisition.ResearchAcquisitionPort)


def test_port_structural_compatibility_accepts_conforming_provider() -> None:
    port = _DeterministicPort()

    assert isinstance(port, acquisition.ResearchAcquisitionPort)


def test_port_structural_compatibility_rejects_non_conforming_object() -> None:
    class _NotAPort:
        def search(self) -> None: ...

    assert not isinstance(_NotAPort(), acquisition.ResearchAcquisitionPort)
    assert not isinstance(object(), acquisition.ResearchAcquisitionPort)


def test_port_acquire_passes_canonical_request_through_unchanged() -> None:
    port = _DeterministicPort()
    request = _request("request-pass-through")

    response = port.acquire(request)

    assert port.seen == [request]
    assert isinstance(response, ResearchResponse)
    assert response.request_id == "request-pass-through"


def test_port_is_research_specific_not_generic_provider_abstraction() -> None:
    assert acquisition.ResearchAcquisitionPort.__name__ == "ResearchAcquisitionPort"
    assert acquisition.ResearchAcquisitionPort.__module__ == (
        "agentx.cognition.research_acquisition"
    )
    assert not hasattr(acquisition, "Provider")
    assert not hasattr(acquisition, "ProviderPort")


def test_validate_accepts_matching_canonical_response() -> None:
    request = _request("request-ok")
    response = _response("request-ok")

    assert acquisition.validate_acquisition_response(request, response) is response


def test_validate_rejects_non_canonical_response() -> None:
    request = _request()

    with pytest.raises(ResearchProviderValidationError):
        acquisition.validate_acquisition_response(request, {"request_id": "request-1"})
    with pytest.raises(ResearchProviderValidationError):
        acquisition.validate_acquisition_response(request, None)
    with pytest.raises(ResearchProviderValidationError):
        acquisition.validate_acquisition_response(request, "verified=true")


def test_validate_rejects_response_for_a_different_request() -> None:
    request = _request("request-A")
    response = _response("request-B")

    with pytest.raises(ResearchProviderValidationError):
        acquisition.validate_acquisition_response(request, response)


def test_validate_rejects_non_canonical_request_type() -> None:
    with pytest.raises(TypeError):
        acquisition.validate_acquisition_response(
            "request-1",  # type: ignore[arg-type]
            _response(),
        )


def test_validate_round_trips_available_evidence_as_untrusted_data() -> None:
    request = _request()
    provenance = ProvenanceReference(kind=ProvenanceKind.WEB, reference="https://x.invalid/e")
    response = _response(evidence=(provenance,))

    validated = acquisition.validate_acquisition_response(request, response)

    assert validated.evidence == (provenance,)
    assert validated.availability is ResearchProviderAvailability.AVAILABLE


def test_validate_accepts_canonical_error_response() -> None:
    request = _request()
    response = _response(
        availability=ResearchProviderAvailability.ERROR,
        failure=ResearchProviderFailure.RATE_LIMITED,
    )

    assert acquisition.validate_acquisition_response(request, response) is response


def test_malformed_response_cannot_be_constructed_at_boundary() -> None:
    # Availability/failure contradiction is rejected by the canonical A4.03
    # contract before this boundary could ever validate it.
    with pytest.raises(ResearchProviderValidationError):
        _response(failure=ResearchProviderFailure.UNKNOWN)


def test_boundary_has_no_state_or_registration() -> None:
    module_names = {name.lower() for name in vars(acquisition)}

    assert "registry" not in module_names
    assert "store" not in module_names
    assert "cache" not in module_names
    assert not hasattr(acquisition, "current_provider")
