"""Shared deterministic fakes for the shadow-procedure validation runner tests.

These are *test-only* support helpers (never shipped runtime code). They build
canonical typed harness facts deterministically so the runner's orchestration,
binding, boundedness, and evidence-emission logic can be exercised without any
real procedure execution, store, model, or subprocess.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from agentx.core.events import VerificationPayload
from agentx.core.ids import ProcedureId
from agentx.core.shadow_repair import ShadowRevisionOutcome, ShadowStepOutcome
from agentx.shadow_procedure_runner import (
    ShadowCaseRequest,
    ShadowHarnessStep,
    ShadowHarnessTrial,
    ShadowTermination,
)

#: A handler builds one deterministic harness trial from a request.
TrialHandler = Callable[[ShadowCaseRequest], ShadowHarnessTrial]


def observed_step(
    *,
    node_id: str | None = None,
    failed: bool = False,
    external_effect_observed: bool = False,
    effect_contained: bool = False,
) -> ShadowHarnessStep:
    """Build one deterministic typed harness step."""
    return ShadowHarnessStep(
        node_id=node_id,
        outcome=ShadowStepOutcome.FAILED if failed else ShadowStepOutcome.OBSERVED,
        external_effect_observed=external_effect_observed,
        effect_contained=effect_contained,
    )


def build_trial(
    request: ShadowCaseRequest,
    *,
    termination: ShadowTermination = ShadowTermination.COMPLETED,
    verification: VerificationPayload | None = None,
    steps: tuple[ShadowHarnessStep, ...] = (),
    procedure_id: ProcedureId | None = None,
    source_revision: int | None = None,
    candidate_revision: int | None = None,
    candidate_fingerprint: str | None = None,
    original_outcome: ShadowRevisionOutcome | None = None,
    regression_node_ids: tuple[str, ...] = (),
    detail: str | None = None,
) -> ShadowHarnessTrial:
    """Build a trial that echoes the request identity unless overridden.

    Pass an explicit identity value (different from the request) to exercise
    binding rejection. To distinguish 'echo the request' from 'set None' we
    always echo unless an override keyword is supplied and not None; a None
    override never differs from echoing a fingerprint-identified candidate.
    """
    return ShadowHarnessTrial(
        termination=termination,
        procedure_id=request.procedure_id if procedure_id is None else procedure_id,
        source_revision=request.source_revision if source_revision is None else source_revision,
        candidate_revision=(
            request.candidate.candidate_revision
            if candidate_revision is None
            else candidate_revision
        ),
        candidate_fingerprint=(
            request.candidate.candidate_fingerprint
            if candidate_fingerprint is None
            else candidate_fingerprint
        ),
        verification=verification,
        steps=steps,
        original_outcome=original_outcome,
        regression_node_ids=regression_node_ids,
        detail=detail,
    )


def default_passing_handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
    """Deterministic default: the candidate passes with one observed step."""
    return build_trial(
        request,
        termination=ShadowTermination.COMPLETED,
        verification=VerificationPayload(passed=True, detail="canonical verification truth"),
        steps=(observed_step(node_id=request.case.target_node_id),),
        original_outcome=ShadowRevisionOutcome.FAILED,
    )


@dataclass
class RecordingShadowHarness:
    """Deterministic injected execution port driven by a test handler.

    Echoes the request's declared identity by default so ordinary tests pass
    binding automatically; a test may build a mismatched trial to exercise
    binding rejection.
    """

    handler: TrialHandler = field(default_factory=lambda: default_passing_handler)
    calls: list[ShadowCaseRequest] = field(default_factory=list)

    def execute_case(self, *, request: ShadowCaseRequest) -> ShadowHarnessTrial:
        self.calls.append(request)
        return self.handler(request)


def make_port(handler: TrialHandler = default_passing_handler) -> RecordingShadowHarness:
    """Convenience constructor matching the :class:`ShadowExecutionPort` shape."""
    return RecordingShadowHarness(handler=handler)
