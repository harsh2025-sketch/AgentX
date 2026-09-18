"""M5 release acceptance: real degradation, repair, restart reuse, and rollback.

The break is a real filesystem environment change.  The acceptance chain uses
the canonical Procedure Graph, L2 strategy, Executor/ActionGate runtime,
independent filesystem verification, typed failure evidence, bounded repair,
shadow runner, replacement transaction, durable ACTIVE selection, and rollback.
No boolean "broken" flag or text declaration supplies failure or verification.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from agentx.active_procedure_reuse import ActiveProcedureReuse
from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.executor import ExecutorRequest
from agentx.capabilities.filesystem import (
    PARENT_MISSING_ERROR_CODE,
    FilesystemWriteTextCapability,
    WriteTextParams,
    write_text_request,
)
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.capabilities.verifier import VerificationRequirement
from agentx.cognition.router import ExecutionLevel, RoutingEvidence
from agentx.compiled_procedure_strategy import (
    CompiledProcedureStrategyBinding,
    GovernedCompiledProcedureStrategy,
)
from agentx.core.events import VerificationPayload
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.failure_diagnosis import (
    DiagnosticConclusion,
    DiagnosticEvidence,
    DiagnosticEvidenceKind,
    package_diagnosis,
)
from agentx.core.failure_localization import (
    FailureLocationKind,
    LocalizationEvidence,
    localize_failure,
)
from agentx.core.failure_taxonomy import FailureCategory, FailureClassification
from agentx.core.ids import ProcedureId
from agentx.core.procedure_execution import ProcedureRunRecord
from agentx.core.procedure_matching import ProcedureCandidate, ProcedureRequirement
from agentx.core.procedure_replacement import (
    EvidencePresence,
    ProcedureReplacementEvidence,
    ProcedureReplacementKind,
    ProcedureReplacementOutcome,
    ProcedureReplacementReason,
    ProcedureReplacementRequest,
    RetiredTargetReactivation,
    TargetIntegrityState,
    assess_procedure_replacement,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.core.repair_budget import (
    ProcedureRepairTarget,
    RepairBudgetDecision,
    RepairBudgetLimits,
    RepairBudgetScope,
    RepairProposalFingerprint,
    RepairTarget,
    assess_repair_attempt,
)
from agentx.core.repair_candidates import derive_repair_candidates
from agentx.core.repair_patch import RepairPatchKind, RepairPatchProposal
from agentx.core.repair_validation import (
    CANONICAL_REPAIR_VALIDATION_CRITERIA,
    RepairValidationCriterion,
    RepairValidationDisposition,
    RepairValidationEvidence,
    RepairValidationOutcome,
)
from agentx.core.shadow_repair import ShadowRepairDisposition, ShadowStepOutcome
from agentx.infrastructure.active_procedure_reader import ActiveProcedureReader
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.kernel.permissions import Permission
from agentx.procedure_degradation import (
    BoundFailureDiagnosis,
    DegradationState,
    assess_procedure_degradation,
)
from agentx.procedure_replacement_transaction import execute_replacement_transaction
from agentx.procedure_reuse_selector import ProcedureReuseSelectionOutcome
from agentx.procedure_rollback import (
    ProcedureRollbackRequest,
    RollbackOutcome,
    execute_procedure_rollback,
)
from agentx.procedures.end import EndNodeSpec
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureEdgeKind,
    ProcedureGraph,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.procedures.interpreter import (
    InterpreterStatus,
    ProcedureInstructionKind,
    ProcedureInterpreter,
    StepCompleted,
)
from agentx.procedures.nodes import ActionNodeSpec
from agentx.repair_patch_materializer import (
    MaterializedProcedureCandidate,
    materialize_repair_patch,
)
from agentx.repair_workflow import (
    RepairWorkflowOutcome,
    RepairWorkflowRequest,
    RepairWorkflowTarget,
    run_repair_workflow,
)
from agentx.shadow_procedure_runner import (
    ShadowCaseRequest,
    ShadowHarnessStep,
    ShadowHarnessTrial,
    ShadowProcedureCandidate,
    ShadowTermination,
    ShadowValidationCase,
    ShadowValidationRun,
    run_shadow_validation,
)
from tests.support.orchestration_harness import (
    OrchestrationHarness,
    default_limits,
    make_envelope,
)

_T0 = datetime(2026, 9, 18, 6, 0, tzinfo=UTC)
_NODE = "write"
_CONTENT = "m5 verified repair payload"
_SCOPE = ProcedureScope({ProcedureScopeDimension.ENVIRONMENT: "m5-filesystem-acceptance"})
_REPAIR_REFERENCE = "m5-real-filesystem-repair-v2"
_PROPOSAL_FINGERPRINT = RepairProposalFingerprint("m5-real-filesystem-repair-v2")


def _graph(path: Path) -> ProcedureGraph:
    return ProcedureGraph(
        entry=ProcedureNodeId(_NODE),
        nodes=(
            ActionNodeSpec(
                capability_name="filesystem.write_text",
                capability_version="1.0.0",
                description="write the M5 acceptance artifact through the governed runtime",
                params={
                    "path": str(path),
                    "content": _CONTENT,
                    "overwrite": True,
                },
            ).to_node(_NODE),
            EndNodeSpec().to_node("done"),
        ),
        edges=(
            ProcedureEdge(
                source=ProcedureNodeId(_NODE),
                target=ProcedureNodeId("done"),
                kind=ProcedureEdgeKind.NEXT,
            ),
        ),
    )


def _active_record(
    *,
    procedure_id: ProcedureId,
    revision: int,
    path: Path,
    created_at: datetime,
) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=procedure_id,
        revision=revision,
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content=_graph(path).to_json(),
        ),
        created_at=created_at,
        status=ProcedureStatus.ACTIVE,
        scope=_SCOPE,
        updated_at=created_at,
    )


def _filesystem_harness() -> OrchestrationHarness:
    harness = OrchestrationHarness(
        authority=frozenset({Permission.WRITE}),
        envelope=make_envelope(max_machine_actions=16),
        register_capability=False,
    )
    harness.registry.register(FilesystemWriteTextCapability())
    return harness


def _write_request_from_content(content: str) -> CapabilityRequest[WriteTextParams]:
    graph = ProcedureGraph.from_json(content)
    node = next(node for node in graph.nodes if node.id == ProcedureNodeId(_NODE))
    assert node.kind is ProcedureNodeKind.ACTION
    spec = ActionNodeSpec.from_params(node.params)
    assert spec.capability_name == "filesystem.write_text"
    assert spec.capability_version == "1.0.0"

    path = spec.params.get("path")
    text = spec.params.get("content")
    overwrite = spec.params.get("overwrite")
    assert isinstance(path, str)
    assert isinstance(text, str)
    assert type(overwrite) is bool
    return write_text_request(path, content=text, overwrite=overwrite)


def _strategy(
    harness: OrchestrationHarness,
    record: ProcedureRecord,
    records: list[ProcedureRunRecord],
) -> GovernedCompiledProcedureStrategy:
    return GovernedCompiledProcedureStrategy(
        executor=harness.executor,
        binding=CompiledProcedureStrategyBinding(
            candidate=ProcedureCandidate(record=record),
            requirement=ProcedureRequirement(scope=_SCOPE),
            action_requests={_NODE: _write_request_from_content(record.payload.content)},
            run_id=uuid4(),
            recorded_at=record.created_at + timedelta(seconds=1),
        ),
        run_sink=records.append,
    )


def _run_active(
    record: ProcedureRecord,
) -> tuple[bool, ClosedLoopOutcome, ProcedureRunRecord]:
    assert record.status is ProcedureStatus.ACTIVE
    harness = _filesystem_harness()
    records: list[ProcedureRunRecord] = []
    strategy = _strategy(harness, record, records)
    loop = harness.agent_loop({ExecutionLevel.L2_COMPILED: strategy})
    task = harness.make_task("write the verified M5 acceptance artifact")
    result = loop.run(
        harness.make_request(
            task=task,
            routing_evidence=RoutingEvidence(verified_reasoning_free_procedure=True),
            requirement=VerificationRequirement(
                {
                    "byte_count": len(_CONTENT.encode("utf-8")),
                    "overwrite_allowed": True,
                }
            ),
            limits=default_limits(
                max_total_attempts=1,
                escalation_permitted=False,
            ),
        )
    ).unwrap()

    assert result.attempt_count == 1
    attempt = result.attempts[0]
    assert attempt.outcome is not None
    assert len(records) == 1
    return result.verified, attempt.outcome, records[0]


def _failure_diagnosis(
    record: ProcedureRecord,
    *,
    outcome: ClosedLoopOutcome,
    run_record: ProcedureRunRecord,
    diagnosed_at: datetime,
) -> BoundFailureDiagnosis:
    assert outcome.kind is LoopOutcome.EXECUTION_FAILED
    assert outcome.error is not None
    assert outcome.execution is not None
    assert outcome.execution.observation is not None
    observation = outcome.execution.observation.to_dict()
    data = observation["data"]
    assert isinstance(data, dict)
    embedded_error = data["error"]
    assert isinstance(embedded_error, dict)
    assert embedded_error["code"] == PARENT_MISSING_ERROR_CODE

    failed_step = run_record.steps[-1]
    assert failed_step.node_id == _NODE

    classification = FailureClassification(
        category=FailureCategory.PROCEDURE,
        summary="The stored procedure path assumption is stale after a real environment change.",
        classified_at=diagnosed_at,
        error_code=outcome.error.code,
        task_id=run_record.task_id,
        correlation_id=run_record.correlation_id,
    )
    localization = localize_failure(
        LocalizationEvidence(
            kind=FailureLocationKind.PROCEDURE_NODE,
            task_id=run_record.task_id,
            procedure_id=record.procedure_id,
            procedure_node_id=failed_step.node_id,
            correlation_id=run_record.correlation_id,
            classification=classification,
        ),
        summary="Canonical run evidence points to the failed filesystem write node.",
        localized_at=diagnosed_at,
    )
    diagnosis = package_diagnosis(
        classification=classification,
        localization=localization,
        summary="The exact write node retains a filesystem path whose parent moved.",
        diagnosed_at=diagnosed_at,
        evidence=(
            DiagnosticEvidence(
                kind=DiagnosticEvidenceKind.CANONICAL_ERROR,
                error_code=outcome.error.code,
            ),
            DiagnosticEvidence(
                kind=DiagnosticEvidenceKind.CHAIN_CORRELATION,
                correlation_id=run_record.correlation_id,
            ),
        ),
        conclusion=DiagnosticConclusion.NODE_IMPLICATED,
    )
    return BoundFailureDiagnosis(
        diagnosis=diagnosis,
        procedure_revision=record.revision,
    )


def _replacement_node(path: Path) -> ProcedureNode:
    return ActionNodeSpec(
        capability_name="filesystem.write_text",
        capability_version="1.0.0",
        description="write the M5 acceptance artifact through the governed runtime",
        params={
            "path": str(path),
            "content": _CONTENT,
            "overwrite": True,
        },
    ).to_node(_NODE)


def _repair_material(
    source: ProcedureRecord,
    diagnosis: BoundFailureDiagnosis,
    *,
    path: Path,
    proposed_at: datetime,
) -> tuple[RepairPatchProposal, MaterializedProcedureCandidate]:
    candidates = derive_repair_candidates(
        diagnosis=diagnosis.diagnosis,
        proposed_at=proposed_at,
    )
    assert len(candidates) == 1
    proposal = RepairPatchProposal(
        kind=RepairPatchKind.NODE_DEFINITION_REPLACEMENT,
        candidate=candidates[0],
        target_procedure_id=source.procedure_id,
        target_revision=source.revision,
        target_node_id=_NODE,
        proposed_definition=_replacement_node(path).to_dict(),
        proposed_at=proposed_at,
    )
    materialized = materialize_repair_patch(source=source, patch=proposal)
    assert materialized.candidate.status is ProcedureStatus.CANDIDATE
    assert materialized.candidate.revision == source.revision + 1
    return proposal, materialized


class _ShadowClock:
    def __init__(self) -> None:
        self._index = 0

    def __call__(self) -> datetime:
        value = _T0 + timedelta(hours=1, seconds=self._index)
        self._index += 1
        return value


class _ShadowRunIds:
    def __init__(self) -> None:
        self._index = 0

    def __call__(self) -> UUID:
        self._index += 1
        return UUID(int=70_000 + self._index)


class _FilesystemShadowPort:
    """Controlled port that executes the candidate through production runtime pieces."""

    def __init__(self) -> None:
        self.calls: list[ShadowCaseRequest] = []

    def execute_case(self, *, request: ShadowCaseRequest) -> ShadowHarnessTrial:
        self.calls.append(request)
        graph = ProcedureGraph.from_json(request.candidate.content)
        interpreter = ProcedureInterpreter(graph=graph)
        state = interpreter.start()
        instruction = interpreter.instruction(state)
        assert instruction.kind is ProcedureInstructionKind.ACTION_REQUIRED
        assert instruction.node_id == ProcedureNodeId(_NODE)

        capability_request = _write_request_from_content(request.candidate.content)
        path = Path(capability_request.params.path)

        # Vary the real filesystem pre-state without changing the candidate.
        if path.parent.exists():
            if request.case.objective == "target absent":
                path.unlink(missing_ok=True)
            elif request.case.objective == "target contains older data":
                path.write_text("older data", encoding="utf-8")
            elif request.case.objective == "target already contains repaired data":
                path.write_text(_CONTENT, encoding="utf-8")

        harness = _filesystem_harness()
        task = harness.make_task("shadow-run repaired filesystem procedure")
        context = ExecutionContext(
            correlation_id=request.correlation_id,
            cancellation_token=CancellationSource().token,
            task_id=task.task_id,
        )
        execution = harness.executor.execute(
            ExecutorRequest(
                task=task,
                capability_request=capability_request,
                context=context,
            )
        ).unwrap()

        if execution.kind is LoopOutcome.VERIFIED:
            assert execution.verification is not None
            assert execution.verification.passed is True
            state = interpreter.advance(
                state,
                StepCompleted(node_kind=ProcedureNodeKind.ACTION),
            )
            assert state.status is InterpreterStatus.TERMINATED
            verification = VerificationPayload(
                passed=True,
                detail=execution.verification.detail,
            )
            step = ShadowHarnessStep(
                node_id=instruction.node_id.to_str(),
                external_effect_observed=True,
                effect_contained=True,
            )
        else:
            verification = None
            step = ShadowHarnessStep(
                node_id=instruction.node_id.to_str(),
                outcome=ShadowStepOutcome.FAILED,
            )

        return ShadowHarnessTrial(
            termination=ShadowTermination.COMPLETED,
            procedure_id=request.procedure_id,
            source_revision=request.source_revision,
            candidate_revision=request.candidate.candidate_revision,
            candidate_fingerprint=request.candidate.candidate_fingerprint,
            verification=verification,
            steps=(step,),
            detail="production ProcedureInterpreter -> Executor -> ActionGate filesystem trial",
        )


def _shadow_run(
    source: ProcedureRecord,
    candidate: ProcedureRecord,
) -> ShadowValidationRun:
    port = _FilesystemShadowPort()
    run = run_shadow_validation(
        procedure_id=source.procedure_id,
        source_revision=source.revision,
        candidate=ShadowProcedureCandidate(
            content=candidate.payload.content,
            candidate_revision=candidate.revision,
        ),
        cases=(
            ShadowValidationCase(
                scope=_SCOPE,
                target_node_id=_NODE,
                objective="target absent",
            ),
            ShadowValidationCase(
                scope=_SCOPE,
                target_node_id=_NODE,
                objective="target contains older data",
            ),
            ShadowValidationCase(
                scope=_SCOPE,
                target_node_id=_NODE,
                objective="target already contains repaired data",
            ),
        ),
        harness=port,
        correlation_id=UUID(int=60_005),
        clock=_ShadowClock(),
        run_id_factory=_ShadowRunIds(),
        detail="M5 varied real-filesystem shadow acceptance",
    )
    assert len(port.calls) == 3
    return run


def _validation_evidence(
    source: ProcedureRecord,
    candidate: ProcedureRecord,
    shadow_run: ShadowValidationRun,
    *,
    evaluated_at: datetime,
) -> tuple[RepairValidationEvidence, ...]:
    # These assertions are the facts represented by the typed evidence below.
    ProcedureGraph.from_json(candidate.payload.content)
    assert candidate.procedure_id == source.procedure_id
    assert candidate.revision == source.revision + 1
    assert candidate.scope == source.scope
    assert shadow_run.all_passed
    assert all(
        trial.verification is not None and trial.verification.passed for trial in shadow_run.trials
    )

    references = {
        RepairValidationCriterion.STRUCTURAL_VALIDITY: "candidate:graph-parsed",
        RepairValidationCriterion.TARGET_BINDING: "shadow:exact-source-candidate-binding",
        RepairValidationCriterion.PRECONDITION_PRESERVATION: "shadow:three-prestate-cases",
        RepairValidationCriterion.POSTCONDITION_COMPATIBILITY: (
            "shadow:verified-write-postcondition"
        ),
        RepairValidationCriterion.REGRESSION_FREE: "shadow:three-related-cases-passed",
        RepairValidationCriterion.VERIFICATION_EVIDENCE: "shadow:canonical-verifier-passed",
        RepairValidationCriterion.SCOPE_COMPATIBILITY: "candidate:scope-preserved",
    }
    return tuple(
        RepairValidationEvidence(
            repair_reference=_REPAIR_REFERENCE,
            procedure_id=source.procedure_id,
            procedure_revision=source.revision,
            procedure_node_id=_NODE,
            criterion=criterion,
            outcome=RepairValidationOutcome.PASSED,
            evidence_reference=references[criterion],
            evaluated_at=evaluated_at,
        )
        for criterion in CANONICAL_REPAIR_VALIDATION_CRITERIA
    )


def _replacement_request(
    active: ProcedureRecord,
    candidate: ProcedureRecord,
    *,
    validation_present: bool,
    shadow_present: bool,
) -> ProcedureReplacementRequest:
    return ProcedureReplacementRequest(
        kind=ProcedureReplacementKind.FORWARD_REPLACEMENT,
        reason=ProcedureReplacementReason.VALIDATED_REPAIR,
        active_revision=active,
        target_revision=candidate,
        known_revisions=(active.revision, candidate.revision),
        evidence=ProcedureReplacementEvidence(
            validation_evidence=(
                EvidencePresence.PRESENT if validation_present else EvidencePresence.ABSENT
            ),
            shadow_evidence=(
                EvidencePresence.PRESENT if shadow_present else EvidencePresence.ABSENT
            ),
            target_integrity=TargetIntegrityState.INTACT,
            evidence_references=(
                "repair-validation:m5",
                "shadow-validation:m5",
            ),
        ),
    )


def _select_active(store: ProcedureStore) -> ProcedureRecord:
    result = ActiveProcedureReuse(ActiveProcedureReader(store), {}).select(
        ProcedureRequirement(scope=_SCOPE)
    )
    assert result.outcome is ProcedureReuseSelectionOutcome.SELECTED
    assert result.selected is not None
    return result.selected.record


def _assert_one_active(store: ProcedureStore, procedure_id: ProcedureId) -> None:
    history = store.history(procedure_id)
    assert sum(record.status is ProcedureStatus.ACTIVE for record in history) == 1


def test_real_break_repair_restart_reuse_and_post_promotion_rollback(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "m5-repair.sqlite3"
    old_parent = tmp_path / "environment-a"
    new_parent = tmp_path / "environment-b"
    old_parent.mkdir()
    old_path = old_parent / "result.txt"
    new_path = new_parent / "result.txt"

    procedure_id = ProcedureId.create()
    v1 = _active_record(
        procedure_id=procedure_id,
        revision=1,
        path=old_path,
        created_at=_T0,
    )
    store = ProcedureStore(SQLiteDatabase(database_path))
    store.insert(v1)

    # Known-good v1 is genuinely executed and independently verified first.
    verified, initial_outcome, initial_run = _run_active(v1)
    assert verified is True
    assert initial_outcome.kind is LoopOutcome.VERIFIED
    assert initial_run.procedure_revision == 1
    assert old_path.read_text(encoding="utf-8") == _CONTENT

    # Real break: move the environment.  v1's stored path now has no parent.
    old_parent.rename(new_parent)
    assert not old_parent.exists()
    assert new_parent.exists()

    failures: list[BoundFailureDiagnosis] = []
    for offset in (10, 20):
        verified, failed_outcome, failed_run = _run_active(v1)
        assert verified is False
        failures.append(
            _failure_diagnosis(
                v1,
                outcome=failed_outcome,
                run_record=failed_run,
                diagnosed_at=_T0 + timedelta(minutes=offset),
            )
        )

    degradation = assess_procedure_degradation(
        procedure=v1,
        failure_diagnoses=failures,
        assessed_at=_T0 + timedelta(minutes=30),
    )
    assert degradation.state is DegradationState.CONFIRMED_DEGRADED
    assert degradation.supporting_failure_indices == (0, 1)
    assert v1.status is ProcedureStatus.ACTIVE

    proposal, materialized = _repair_material(
        v1,
        failures[0],
        path=new_path,
        proposed_at=_T0 + timedelta(minutes=31),
    )
    v2_candidate = materialized.candidate
    # Candidate generation is separate from persistence and promotion.
    assert store.get(procedure_id, 2) is None
    store.insert(v2_candidate)
    stored_v1 = store.get(procedure_id, 1)
    stored_v2 = store.get(procedure_id, 2)
    assert stored_v1 is not None and stored_v1.status is ProcedureStatus.ACTIVE
    assert stored_v2 is not None and stored_v2.status is ProcedureStatus.CANDIDATE

    repair_target = RepairTarget(
        procedure=ProcedureRepairTarget(
            procedure_id=procedure_id,
            revision=1,
        )
    )
    limits = RepairBudgetLimits(
        max_total_attempts=3,
        max_attempts_per_proposal=2,
        max_consecutive_failures_without_progress=2,
        total_attempt_scope=RepairBudgetScope.TARGET,
    )
    budget = assess_repair_attempt(
        target=repair_target,
        proposal=_PROPOSAL_FINGERPRINT,
        history=(),
        limits=limits,
    )
    assert budget.decision is RepairBudgetDecision.ALLOW_CONSIDERATION

    shadow = _shadow_run(v1, v2_candidate)
    assert shadow.all_passed is True
    assert shadow.summary.passed == 3
    assert all(trial.disposition is ShadowRepairDisposition.PASSED for trial in shadow.trials)
    # Shadow execution never promoted the durable candidate.
    stored_v1 = store.get(procedure_id, 1)
    stored_v2 = store.get(procedure_id, 2)
    assert stored_v1 is not None and stored_v1.status is ProcedureStatus.ACTIVE
    assert stored_v2 is not None and stored_v2.status is ProcedureStatus.CANDIDATE

    validation_evidence = _validation_evidence(
        v1,
        v2_candidate,
        shadow,
        evaluated_at=_T0 + timedelta(minutes=40),
    )
    replacement_request = _replacement_request(
        v1,
        v2_candidate,
        validation_present=True,
        shadow_present=True,
    )
    workflow = run_repair_workflow(
        RepairWorkflowRequest(
            target=RepairWorkflowTarget(
                procedure_id=procedure_id,
                revision=1,
            ),
            assessed_at=_T0 + timedelta(minutes=41),
            repair_reference=_REPAIR_REFERENCE,
            proposal_fingerprint=_PROPOSAL_FINGERPRINT,
            budget_limits=limits,
            failure_diagnoses=tuple(failures),
            patch_proposals=(proposal,),
            validation_evidence=validation_evidence,
            required_validation_criteria=CANONICAL_REPAIR_VALIDATION_CRITERIA,
            shadow_results=shadow.trials,
            replacement_request=replacement_request,
        )
    )
    assert workflow.outcome is RepairWorkflowOutcome.CHAIN_COMPLETE
    assert workflow.evidence.validation_report is not None
    assert workflow.evidence.validation_report.disposition is RepairValidationDisposition.VALIDATED
    assert workflow.evidence.replacement_decision is not None
    assert workflow.evidence.replacement_decision.outcome is ProcedureReplacementOutcome.ELIGIBLE

    applied = execute_replacement_transaction(
        store,
        workflow.evidence.replacement_decision,
        v1,
        v2_candidate,
    )
    assert applied.status == "APPLIED"
    _assert_one_active(store, procedure_id)

    # Process-boundary equivalent: discard the runtime objects and reopen SQLite.
    restarted = ProcedureStore(SQLiteDatabase(database_path))
    _assert_one_active(restarted, procedure_id)
    selected_v2 = _select_active(restarted)
    assert selected_v2.revision == 2
    restarted_v1 = restarted.get(procedure_id, 1)
    assert restarted_v1 is not None and restarted_v1.status is ProcedureStatus.RETIRED

    verified, repaired_outcome, repaired_run = _run_active(selected_v2)
    assert verified is True
    assert repaired_outcome.kind is LoopOutcome.VERIFIED
    assert repaired_run.procedure_revision == 2
    assert new_path.read_text(encoding="utf-8") == _CONTENT

    # Later regression: the environment returns to A.  v2 now has the stale path.
    new_parent.rename(old_parent)
    assert old_parent.exists()
    assert not new_parent.exists()

    v2_failures: list[BoundFailureDiagnosis] = []
    for offset in (60, 70):
        verified, failed_outcome, failed_run = _run_active(selected_v2)
        assert verified is False
        v2_failures.append(
            _failure_diagnosis(
                selected_v2,
                outcome=failed_outcome,
                run_record=failed_run,
                diagnosed_at=_T0 + timedelta(minutes=offset),
            )
        )
    v2_degradation = assess_procedure_degradation(
        procedure=selected_v2,
        failure_diagnoses=v2_failures,
        assessed_at=_T0 + timedelta(minutes=75),
    )
    assert v2_degradation.state is DegradationState.CONFIRMED_DEGRADED

    retired_v1 = restarted.get(procedure_id, 1)
    active_v2 = restarted.get(procedure_id, 2)
    assert retired_v1 is not None and retired_v1.status is ProcedureStatus.RETIRED
    assert active_v2 is not None and active_v2.status is ProcedureStatus.ACTIVE
    rollback_decision = assess_procedure_replacement(
        ProcedureReplacementRequest(
            kind=ProcedureReplacementKind.ROLLBACK,
            reason=ProcedureReplacementReason.REGRESSION_ROLLBACK,
            active_revision=active_v2,
            target_revision=retired_v1,
            known_revisions=(1, 2),
            evidence=ProcedureReplacementEvidence(
                validation_evidence=EvidencePresence.PRESENT,
                target_integrity=TargetIntegrityState.INTACT,
                evidence_references=(
                    "degradation:v2:two-node-bound-failures",
                    "history:v1:known-good",
                ),
            ),
            retired_target_reactivation=(RetiredTargetReactivation.CONTROLLED_LIFECYCLE_ACT),
        )
    )
    assert rollback_decision.outcome is ProcedureReplacementOutcome.ELIGIBLE

    rollback = execute_procedure_rollback(
        restarted,
        ProcedureRollbackRequest(
            procedure_id=procedure_id,
            current_revision=2,
            target_revision=1,
            eligibility=rollback_decision,
            requested_at=_T0 + timedelta(minutes=80),
            expected_known_revisions=(1, 2),
        ),
    )
    assert rollback.outcome is RollbackOutcome.APPLIED

    after_rollback = ProcedureStore(SQLiteDatabase(database_path))
    history = after_rollback.history(procedure_id)
    assert tuple(record.revision for record in history) == (1, 2, 3)
    assert tuple(record.status for record in history) == (
        ProcedureStatus.RETIRED,
        ProcedureStatus.RETIRED,
        ProcedureStatus.ACTIVE,
    )
    assert history[2].payload == history[0].payload
    _assert_one_active(after_rollback, procedure_id)

    selected_v3 = _select_active(after_rollback)
    assert selected_v3.revision == 3
    verified, rollback_outcome, rollback_run = _run_active(selected_v3)
    assert verified is True
    assert rollback_outcome.kind is LoopOutcome.VERIFIED
    assert rollback_run.procedure_revision == 3
    assert old_path.read_text(encoding="utf-8") == _CONTENT


def test_bad_repair_shadow_failure_never_promotes_and_known_good_recovers(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "m5-bad-repair.sqlite3"
    old_parent = tmp_path / "environment-a"
    new_parent = tmp_path / "environment-b"
    missing_parent = tmp_path / "never-created"
    old_parent.mkdir()
    old_path = old_parent / "result.txt"
    bad_path = missing_parent / "result.txt"

    procedure_id = ProcedureId.create()
    v1 = _active_record(
        procedure_id=procedure_id,
        revision=1,
        path=old_path,
        created_at=_T0,
    )
    store = ProcedureStore(SQLiteDatabase(database_path))
    store.insert(v1)

    verified, _, _ = _run_active(v1)
    assert verified is True
    old_parent.rename(new_parent)

    diagnoses: list[BoundFailureDiagnosis] = []
    for offset in (10, 20):
        verified, failed_outcome, failed_run = _run_active(v1)
        assert verified is False
        diagnoses.append(
            _failure_diagnosis(
                v1,
                outcome=failed_outcome,
                run_record=failed_run,
                diagnosed_at=_T0 + timedelta(minutes=offset),
            )
        )
    assert (
        assess_procedure_degradation(
            procedure=v1,
            failure_diagnoses=diagnoses,
            assessed_at=_T0 + timedelta(minutes=30),
        ).state
        is DegradationState.CONFIRMED_DEGRADED
    )

    _, materialized = _repair_material(
        v1,
        diagnoses[0],
        path=bad_path,
        proposed_at=_T0 + timedelta(minutes=31),
    )
    bad_candidate = materialized.candidate
    store.insert(bad_candidate)

    shadow = _shadow_run(v1, bad_candidate)
    assert shadow.all_passed is False
    assert shadow.summary.failed == 3
    assert all(trial.disposition is ShadowRepairDisposition.FAILED for trial in shadow.trials)
    assert not missing_parent.exists()

    ineligible = assess_procedure_replacement(
        _replacement_request(
            v1,
            bad_candidate,
            validation_present=False,
            shadow_present=False,
        )
    )
    assert ineligible.outcome is ProcedureReplacementOutcome.INSUFFICIENT_EVIDENCE

    # No lifecycle transaction is called: failed evidence cannot promote itself.
    _assert_one_active(store, procedure_id)
    stored_v1 = store.get(procedure_id, 1)
    stored_v2 = store.get(procedure_id, 2)
    assert stored_v1 is not None and stored_v1.status is ProcedureStatus.ACTIVE
    assert stored_v2 is not None and stored_v2.status is ProcedureStatus.CANDIDATE

    # Restore the known-good environment and prove verified reuse of v1.
    new_parent.rename(old_parent)
    restarted = ProcedureStore(SQLiteDatabase(database_path))
    selected = _select_active(restarted)
    assert selected.revision == 1
    verified, recovered_outcome, recovered_run = _run_active(selected)
    assert verified is True
    assert recovered_outcome.kind is LoopOutcome.VERIFIED
    assert recovered_run.procedure_revision == 1
