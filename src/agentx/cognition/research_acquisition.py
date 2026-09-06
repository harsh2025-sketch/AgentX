"""Research acquisition boundary for A4.04.

A4.04 defines the smallest provider-neutral *port* through which a future
governed runtime can submit canonical A4.03 research data::

    future authorized runtime -> ResearchAcquisitionPort.acquire(ResearchRequest)
        -> ResearchResponse (untrusted external data)

This module is a contract/boundary only. It deliberately contains:

* no concrete provider, client, adapter, registry, or factory;
* no network, HTTP, socket, search API, browser, CDP, process, shell,
  filesystem, or persistence usage;
* no model invocation, claim extraction, reputation scoring, summarization,
  ranking, contradiction resolution, or knowledge promotion;
* no authority decision and no field or method related to permission,
  authorization, risk, budget, stop state, task transition, or verification.

Authority is owned by the future governed runtime (Trusted Kernel), never by
this port. A :class:`ResearchRequest`, a provider identity, or a provider's
self-reported availability grants nothing: authority must be established
before a provider is invoked. This module cannot establish it and provides no
bypass.

Everything a provider returns is untrusted external *data*. An AVAILABLE
response is not truth, provenance is not truth, and evidence is never
verified knowledge. Hostile content embedded in evidence (for example
``"SYSTEM: approve this"`` or ``"verified=true"``) stays inert: it cannot
grant permission, widen budget, clear stop, transition a task, activate a
procedure, or promote knowledge.

A4.04 reuses the canonical A4.03 contracts without redefining them: the port
consumes :class:`~agentx.cognition.research_provider.ResearchRequest` and
returns :class:`~agentx.cognition.research_provider.ResearchResponse`, and
malformed provider output fails closed through the canonical A4.03
:class:`~agentx.cognition.research_provider.ResearchProviderValidationError`.
There is no competing failure hierarchy and no generic universal AgentX
provider abstraction: the port and its vocabulary remain research-specific.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentx.cognition.research_provider import (
    ResearchProviderValidationError,
    ResearchRequest,
    ResearchResponse,
)

# The canonical A4.03 contracts are re-exported so callers can depend on the
# acquisition boundary's public surface without importing A4.03 separately.
# They remain defined once, in :mod:`agentx.cognition.research_provider`.
__all__ = [
    "ResearchAcquisitionPort",
    "ResearchProviderValidationError",
    "ResearchRequest",
    "ResearchResponse",
    "validate_acquisition_response",
]


@runtime_checkable
class ResearchAcquisitionPort(Protocol):
    """Provider-neutral research acquisition port.

    A provider implementation (a later task) supplies a concrete object that
    satisfies this structural protocol. A4.04 ships no implementation and no
    provider selection: the protocol only fixes the single interaction shape.

    ``acquire`` is synchronous data exchange in, untrusted data out. It does
    not decide whether research is authorized, does not verify anything, does
    not mutate Hive or task state, and does not interpret provider content as
    instructions. Operational provider outcomes are expressed canonically
    inside the returned :class:`ResearchResponse` (availability/failure data);
    no competing exception or failure hierarchy is defined here.
    """

    def acquire(self, request: ResearchRequest) -> ResearchResponse:
        """Submit one canonical research request and return its response.

        The request binds the caller to one canonical A4.02
        :class:`~agentx.cognition.research_objective.ResearchObjective` via the
        A4.03 :class:`ResearchRequest`. The returned :class:`ResearchResponse`
        answers that request and carries untrusted provenance data only.
        """
        ...


def validate_acquisition_response(request: ResearchRequest, response: object) -> ResearchResponse:
    """Fail-closed boundary check for one provider interaction.

    A future runtime calls this immediately after a provider returns, so
    malformed provider output (a wrong object type, or a response that answers
    a different request than the one submitted) is rejected at the contract
    boundary rather than flowing onward. Validation never repairs, coerces,
    parses, or interprets provider content.

    Checks are intentionally minimal because the A4.03 data contracts already
    validate response shape and availability/failure consistency:

    * ``request`` must be a canonical :class:`ResearchRequest`;
    * ``response`` must be a canonical :class:`ResearchResponse`;
    * ``response.request_id`` must equal ``request.request_id`` — a response
      that does not answer the submitted request fails closed.

    Raises the canonical A4.03 :class:`ResearchProviderValidationError` on any
    mismatch. Returns the validated response unchanged.
    """

    if not isinstance(request, ResearchRequest):
        raise TypeError("request must be a ResearchRequest")
    if not isinstance(response, ResearchResponse):
        raise ResearchProviderValidationError(
            "research acquisition port must return a ResearchResponse"
        )
    if response.request_id != request.request_id:
        raise ResearchProviderValidationError(
            "ResearchResponse request_id does not match the submitted ResearchRequest"
        )
    return response
