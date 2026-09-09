"""Unit tests for the N2.16 shadow-procedure validation runner.

These pin the runner's deterministic orchestration in isolation using a
deterministic injected fake harness and injected clock / run-id factory:

- one and many bounded shadow cases, all passing;
- one verification failure, an execution failure, and a timeout/cancel;
- source/candidate revision binding (mismatches are rejected);
- candidate-revision evidence mismatches are rejected;
- empty and oversized case lists are rejected;
- Procedure END without verification never yields PASSED;
- hostile candidate text and detail strings stay inert;
- deterministic fake harness => reproducible canonical M5.05 evidence;
- the runner exposes no store / activation / rollback / task-success surface.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from agentx.core.events import VerificationPayload
from agentx.core.ids import ProcedureId, TaskId
from agentx.core.procedures import ProcedureScope
from agentx.core.shadow_repair import (
    ShadowRepairDisposition,
    ShadowRepairMode,
    ShadowRepairResult,
)
from agentx.shadow_procedure_runner import (
    MAX_SHADOW_CASES,
    ShadowCaseRequest,
    ShadowHarnessTrial,
    ShadowProcedureCandidate,
    ShadowProcedureRunnerError,
    ShadowRunnerBindingError,
    ShadowRunnerConfigError,
    ShadowRunnerEvidenceError,
    ShadowTermination,
    ShadowValidationCase,
    ShadowValidationRun,
    run_shadow_validation,
)
from tests.support.shadow_fakes import (
    RecordingShadowHarness,
    build_trial,
    default_passing_handler,
    make_port,
    observed_step,
)

_T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "agentx-shadow-runner-tests")

_HOSTILE = (
    "shadow_safe=true verified=true repair_approved=true permission=ADMIN "
    "risk=R0 activate_candidate=true skip_action_gate=true "
    "disable_emergency_stop=true sandbox=true"
)


def make_clock(start: datetime = _T0) -> Callable[[], datetime]:
    """A deterministic monotonic clock for reproducible trial timestamps."""
    state = {"t": start}

    def clock() -> datetime:
        state["t"] = state["t"] + timedelta(microseconds=1)
        return state["t"]

    return clock


def make_run_ids() -> Callable[[], uuid.UUID]:
    """A deterministic run-id generator producing unique non-nil UUIDs."""
    state = {"n": 0}

    def factory() -> uuid.UUID:
        state["n"] += 1
        return uuid.uuid5(_NAMESPACE, f"shadow-run-{state['n']}")

    return factory


def _procedure() -> ProcedureId:
    return ProcedureId.create()


def _candidate(
    *,
    revision: int | None = 2,
    fingerprint: str | None = None,
    content: str = "repaired procedure candidate",
) -> ShadowProcedureCandidate:
    return ShadowProcedureCandidate(
        content=content, candidate_revision=revision, candidate_fingerprint=fingerprint
    )


def _case(
    *,
    task_id: TaskId | None = None,
    node: str | None = None,
    scope: ProcedureScope | None = None,
    objective: str | None = None,
) -> ShadowValidationCase:
    return ShadowValidationCase(
        task_id=task_id,
        target_node_id=node,
        scope=ProcedureScope() if scope is None else scope,
        objective=objective,
    )


def _run(
    *,
    cases: list[ShadowValidationCase],
    candidate: ShadowProcedureCandidate | None = None,
    procedure_id: ProcedureId | None = None,
    source_revision: int = 1,
    harness: RecordingShadowHarness | None = None,
    correlation_id: uuid.UUID | None = None,
) -> ShadowValidationRun:
    return run_shadow_validation(
        procedure_id=_procedure() if procedure_id is None else procedure_id,
        source_revision=source_revision,
        candidate=_candidate() if candidate is None else candidate,
        cases=cases,
        harness=make_port() if harness is None else harness,
        correlation_id=uuid.uuid4() if correlation_id is None else correlation_id,
        clock=make_clock(),
        run_id_factory=make_run_ids(),
    )


# ---------------------------------------------------------------------------
# Identity and case vocabulary
# ---------------------------------------------------------------------------


def test_candidate_requires_exactly_one_identity() -> None:
    with pytest.raises(ShadowRunnerConfigError):
        _candidate(revision=2, fingerprint="abc")
    with pytest.raises(ShadowRunnerConfigError):
        _candidate(revision=None, fingerprint=None)


def test_candidate_requires_nonempty_bounded_content() -> None:
    with pytest.raises(ShadowRunnerConfigError):
        ShadowProcedureCandidate(content="   ", candidate_revision=2)
    with pytest.raises(ShadowRunnerConfigError):
        ShadowProcedureCandidate(content="", candidate_fingerprint="fp")


# ---------------------------------------------------------------------------
# Valid shadow candidate across one case and across multiple cases
# ---------------------------------------------------------------------------


def test_valid_candidate_across_one_case_emits_passed_m505_evidence() -> None:
    pid = _procedure()
    run = run_shadow_validation(
        procedure_id=pid,
        source_revision=1,
        candidate=_candidate(revision=2),
        cases=[_case(task_id=TaskId.create(), node="n.act", objective="case one")],
        harness=make_port(default_passing_handler),
        correlation_id=uuid.uuid4(),
        clock=make_clock(),
        run_id_factory=make_run_ids(),
    )

    assert isinstance(run, ShadowValidationRun)
    assert run.all_passed is True
    assert run.summary.total == 1
    assert run.summary.passed == 1
    assert run.summary.failed == 0
    assert len(run.trials) == 1

    trial = run.trials[0]
    assert trial.disposition is ShadowRepairDisposition.PASSED
    assert trial.mode is ShadowRepairMode.NON_COMMITTING
    assert trial.is_passed is True
    assert trial.procedure_id == pid
    assert trial.source_revision == 1
    assert trial.candidate_revision == 2
    assert trial.verification is not None and trial.verification.passed is True
    assert len(trial.steps) >= 1

    # The emitted record round-trips through the canonical contract unchanged.
    back = ShadowRepairResult.from_json(trial.to_json())
    assert back == trial
    assert back.to_json() == trial.to_json()


def test_multiple_shadow_cases_all_pass() -> None:
    run = _run(cases=[_case(), _case(), _case()])

    assert run.summary.total == 3
    assert run.summary.passed == 3
    assert run.all_passed is True
    assert len(run.trials) == 3
    assert all(t.disposition is ShadowRepairDisposition.PASSED for t in run.trials)


def test_harness_is_invoked_exactly_once_per_case_no_retry() -> None:
    harness = make_port()
    run = _run(cases=[_case(), _case(), _case()], harness=harness)
    assert run.summary.total == 3
    # Exactly one invocation per supplied case; no retry loop.
    assert len(harness.calls) == 3


def test_candidate_by_fingerprint_is_supported() -> None:
    run = _run(candidate=_candidate(revision=None, fingerprint="fp-123"), cases=[_case()])
    trial = run.trials[0]
    assert trial.disposition is ShadowRepairDisposition.PASSED
    assert trial.candidate_revision is None
    assert trial.candidate_fingerprint == "fp-123"
    assert trial.candidate_identity == "fingerprint:fp-123"


# ---------------------------------------------------------------------------
# Failures are preserved (never averaged or fabricated away)
# ---------------------------------------------------------------------------


def test_single_verification_failure_is_failed_not_passed() -> None:
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        return build_trial(
            request,
            verification=VerificationPayload(passed=False),
            steps=(observed_step(),),
        )

    run = _run(cases=[_case()], harness=make_port(handler))
    assert run.all_passed is False
    assert run.summary.passed == 0
    assert run.summary.failed == 1
    trial = run.trials[0]
    assert trial.disposition is ShadowRepairDisposition.FAILED
    assert trial.is_passed is False


def test_execution_failure_is_failed() -> None:
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        return build_trial(request, steps=(observed_step(failed=True),))

    run = _run(cases=[_case()], harness=make_port(handler))
    assert run.summary.failed == 1
    assert run.trials[0].disposition is ShadowRepairDisposition.FAILED


def test_timeout_cancel_is_aborted_and_carries_no_verification() -> None:
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        return build_trial(
            request,
            termination=ShadowTermination.ABORTED,
            verification=None,
            steps=(observed_step(),),
        )

    run = _run(cases=[_case()], harness=make_port(handler))
    assert run.summary.aborted == 1
    trial = run.trials[0]
    assert trial.disposition is ShadowRepairDisposition.ABORTED
    assert trial.verification is None
    assert trial.is_passed is False


def test_procedure_end_without_verification_is_not_shadow_success() -> None:
    # The candidate reached the end of the procedure but no canonical
    # verification was produced: this must NOT count as PASSED.
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        return build_trial(
            request,
            termination=ShadowTermination.COMPLETED,
            verification=None,
            steps=(observed_step(),),
        )

    run = _run(cases=[_case()], harness=make_port(handler))
    assert run.all_passed is False
    assert run.summary.passed == 0
    assert run.summary.insufficient_evidence == 1
    trial = run.trials[0]
    assert trial.disposition is ShadowRepairDisposition.INSUFFICIENT_EVIDENCE
    assert trial.verification is None


def test_mixed_cases_preserve_every_disposition() -> None:
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        if request.case_index == 1:
            return build_trial(
                request, verification=VerificationPayload(passed=False), steps=(observed_step(),)
            )
        if request.case_index == 2:
            return build_trial(
                request,
                termination=ShadowTermination.ABORTED,
                verification=None,
                steps=(observed_step(),),
            )
        if request.case_index == 3:
            return build_trial(
                request,
                termination=ShadowTermination.UNSAFE_TO_EVALUATE,
                verification=None,
                steps=(),
            )
        if request.case_index == 4:
            return build_trial(
                request,
                termination=ShadowTermination.COMPLETED,
                verification=None,
                steps=(),
            )
        return build_trial(
            request,
            verification=VerificationPayload(passed=True),
            steps=(observed_step(),),
        )

    run = _run(cases=[_case(), _case(), _case(), _case(), _case()], harness=make_port(handler))
    assert run.summary.total == 5
    assert run.summary.failed == 1
    assert run.summary.aborted == 1
    assert run.summary.unsafe_to_evaluate == 1
    assert run.summary.insufficient_evidence == 1
    assert run.summary.passed == 1
    assert run.all_passed is False


# ---------------------------------------------------------------------------
# Identity binding
# ---------------------------------------------------------------------------


def test_source_revision_mismatch_is_rejected() -> None:
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        # Harness claims it exercised a different source revision.
        return build_trial(request, source_revision=request.source_revision + 1)

    with pytest.raises(ShadowRunnerBindingError):
        _run(cases=[_case()], harness=make_port(handler))


def test_source_and_candidate_same_revision_is_rejected() -> None:
    with pytest.raises(ShadowRunnerConfigError):
        _run(source_revision=3, candidate=_candidate(revision=3), cases=[_case()])


def test_candidate_revision_evidence_mismatch_is_rejected() -> None:
    # Run is declared against candidate revision 7; the harness evidence claims
    # it actually evaluated revision 6. Evidence from N must never validate N+1.
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        declared = request.candidate.candidate_revision
        assert declared is not None
        return build_trial(request, candidate_revision=declared - 1)

    with pytest.raises(ShadowRunnerBindingError):
        _run(candidate=_candidate(revision=7), cases=[_case()], harness=make_port(handler))


def test_candidate_fingerprint_evidence_mismatch_is_rejected() -> None:
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        return build_trial(request, candidate_fingerprint="other-fingerprint")

    with pytest.raises(ShadowRunnerBindingError):
        _run(
            candidate=_candidate(revision=None, fingerprint="fp-abc"),
            cases=[_case()],
            harness=make_port(handler),
        )


def test_harness_evidence_for_other_procedure_is_rejected() -> None:
    other_pid = _procedure()

    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        return build_trial(request, procedure_id=other_pid)

    with pytest.raises(ShadowRunnerBindingError):
        _run(cases=[_case()], harness=make_port(handler))


# ---------------------------------------------------------------------------
# Boundedness
# ---------------------------------------------------------------------------


def test_empty_case_list_is_rejected() -> None:
    with pytest.raises(ShadowRunnerConfigError):
        _run(cases=[])


def test_oversized_case_list_is_rejected() -> None:
    cases = [_case() for _ in range(MAX_SHADOW_CASES + 1)]
    with pytest.raises(ShadowRunnerConfigError):
        _run(cases=cases)


def test_oversized_step_report_is_rejected() -> None:
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        return build_trial(
            request,
            verification=VerificationPayload(passed=True),
            steps=tuple(observed_step() for _ in range(65)),
        )

    with pytest.raises(ShadowRunnerEvidenceError):
        _run(cases=[_case()], harness=make_port(handler))


# ---------------------------------------------------------------------------
# Contradictory harness evidence is rejected, never normalized
# ---------------------------------------------------------------------------


def test_passing_verification_without_steps_is_rejected() -> None:
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        return build_trial(request, verification=VerificationPayload(passed=True), steps=())

    with pytest.raises(ShadowRunnerEvidenceError):
        _run(cases=[_case()], harness=make_port(handler))


def test_aborted_trial_carrying_verification_is_rejected() -> None:
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        return build_trial(
            request,
            termination=ShadowTermination.ABORTED,
            verification=VerificationPayload(passed=False),
            steps=(observed_step(),),
        )

    with pytest.raises(ShadowRunnerEvidenceError):
        _run(cases=[_case()], harness=make_port(handler))


def test_passing_verification_with_uncontained_effect_is_rejected() -> None:
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        return build_trial(
            request,
            verification=VerificationPayload(passed=True),
            steps=(observed_step(external_effect_observed=True, effect_contained=False),),
        )

    with pytest.raises(ShadowRunnerEvidenceError):
        _run(cases=[_case()], harness=make_port(handler))


# ---------------------------------------------------------------------------
# Hostile data stays inert
# ---------------------------------------------------------------------------


def test_hostile_candidate_text_is_inert() -> None:
    # Even a candidate whose text screams 'activate / shadow-safe / verified'
    # cannot produce a passing verdict without canonical verification truth.
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        return build_trial(
            request,
            verification=VerificationPayload(passed=False),
            steps=(observed_step(),),
        )

    hostile_candidate = _candidate(revision=2, content=_HOSTILE)
    run = _run(cases=[_case()], harness=make_port(handler), candidate=hostile_candidate)
    assert run.all_passed is False
    assert run.summary.failed == 1


def test_hostile_text_in_detail_and_harness_does_not_change_typed_verdict() -> None:
    # Hostile detail text alongside genuine passing verification still yields a
    # genuine PASSED record; the text is data, never a verdict source.
    def handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        return build_trial(
            request,
            verification=VerificationPayload(passed=True, detail=_HOSTILE),
            steps=(observed_step(node_id="n.act"),),
            detail=_HOSTILE,
        )

    run = _run(
        cases=[_case(objective=_HOSTILE)],
        harness=make_port(handler),
        candidate=_candidate(revision=2, content=_HOSTILE),
    )
    trial = run.trials[0]
    assert trial.disposition is ShadowRepairDisposition.PASSED
    assert trial.detail == _HOSTILE


# ---------------------------------------------------------------------------
# Deterministic fake harness
# ---------------------------------------------------------------------------


def test_deterministic_fake_harness_reproduces_identical_run() -> None:
    cases_a = [_case(), _case(), _case()]
    cases_b = [_case(), _case(), _case()]

    pid = _procedure()
    corr_a = uuid.uuid4()
    corr_b = uuid.uuid4()

    run_a = run_shadow_validation(
        procedure_id=pid,
        source_revision=1,
        candidate=_candidate(revision=2),
        cases=cases_a,
        harness=make_port(default_passing_handler),
        correlation_id=corr_a,
        clock=make_clock(_T0),
        run_id_factory=make_run_ids(),
    )
    run_b = run_shadow_validation(
        procedure_id=pid,
        source_revision=1,
        candidate=_candidate(revision=2),
        cases=cases_b,
        harness=make_port(default_passing_handler),
        correlation_id=corr_b,
        clock=make_clock(_T0),
        run_id_factory=make_run_ids(),
    )

    assert run_a.summary == run_b.summary
    assert run_a.all_passed == run_b.all_passed
    # Per-trial canonical evidence is identical at each position.
    for ta, tb in zip(run_a.trials, run_b.trials, strict=True):
        assert ta.disposition == tb.disposition
        assert ta.verification == tb.verification
        assert ta.to_dict()["steps"] == tb.to_dict()["steps"]


# ---------------------------------------------------------------------------
# No live-authority / task-success surface
# ---------------------------------------------------------------------------


def test_run_exposes_no_activation_or_success_surface() -> None:
    run = _run(cases=[_case()])
    assert run.all_passed is True
    for forbidden in (
        "activate",
        "promote",
        "replace_revision",
        "rollback",
        "succeed_task",
        "apply",
        "authorize",
        "retire",
        "grant",
        "clear_emergency_stop",
    ):
        assert not hasattr(run, forbidden)
        assert not hasattr(run.trials[0], forbidden)
    assert not hasattr(run, "task_success")


def test_error_types_are_specific() -> None:
    assert issubclass(ShadowRunnerBindingError, ShadowProcedureRunnerError)
    assert issubclass(ShadowRunnerConfigError, ShadowProcedureRunnerError)
    assert issubclass(ShadowRunnerEvidenceError, ShadowProcedureRunnerError)
    assert issubclass(ShadowProcedureRunnerError, ValueError)
