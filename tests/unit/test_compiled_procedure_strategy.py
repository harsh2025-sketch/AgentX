"""Unit contract tests for the N2.04 L2 compiled-procedure strategy adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from tests.support.demo_capability import NoteWriteParams, write_request
from tests.support.orchestration_harness import OrchestrationHarness

from agentx.cognition.router import ExecutionLevel
from agentx.compiled_procedure_strategy import (
    COMPILED_PROCEDURE_STRATEGY_LEVEL,
    CompiledProcedureStrategyBinding,
    CompiledProcedureStrategyBindingError,
    GovernedCompiledProcedureStrategy,
)
from agentx.core.ids import ProcedureId
from agentx.core.procedure_execution import (
    ProcedureRunDisposition,
    ProcedureTaskVerification,
)
from agentx.core.procedure_matching import (
    ProcedureCandidate,
    ProcedureMatchOutcome,
    ProcedureRequirement,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
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
from agentx.procedures.nodes import ActionNodeSpec

_T0 = datetime(2026, 9, 8, 18, 0, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000000204")
_PROCEDURE_ID = ProcedureId(UUID("00000000-0000-0000-0000-000000000204"))


def _scope(os_name: str = "windows") -> ProcedureScope:
    return ProcedureScope({ProcedureScopeDimension.OPERATING_SYSTEM: os_name})


def _graph(*, action_params: dict[str, object] | None = None) -> ProcedureGraph:
    action = ActionNodeSpec(
        capability_name="demo.note.write",
        capability_version="1.0.0",
        description="write a deterministic note through governed execution",
        params={"key": "alpha", "value": "v1"} if action_params is None else action_params,
    )
    return ProcedureGraph(
        entry=ProcedureNodeId("write"),
        nodes=(action.to_node("write"), EndNodeSpec().to_node("done")),
        edges=(
            ProcedureEdge(
                source=ProcedureNodeId("write"),
                target=ProcedureNodeId("done"),
                kind=ProcedureEdgeKind.NEXT,
            ),
        ),
    )


def _record(
    *,
    graph: ProcedureGraph | None = None,
    status: ProcedureStatus = ProcedureStatus.ACTIVE,
    scope: ProcedureScope | None = None,
    payload: ProcedurePayload | None = None,
) -> ProcedureRecord:
    actual_payload = payload
    if actual_payload is None:
        actual_payload = ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content=(graph if graph is not None else _graph()).to_json(),
        )
    return ProcedureRecord(
        procedure_id=_PROCEDURE_ID,
        revision=1,
        payload=actual_payload,
        created_at=_T0,
        status=status,
        scope=_scope() if scope is None else scope,
    )


def _binding(
    *,
    record: ProcedureRecord | None = None,
    requirement: ProcedureRequirement | None = None,
    level: ExecutionLevel = ExecutionLevel.L2_COMPILED,
    action_requests: dict[str, object] | None = None,
) -> CompiledProcedureStrategyBinding:
    requests = (
        {"write": write_request(NoteWriteParams(key="alpha", value="v1"))}
        if action_requests is None
        else action_requests
    )
    return CompiledProcedureStrategyBinding(
        candidate=ProcedureCandidate(record=_record() if record is None else record),
        requirement=(
            ProcedureRequirement(scope=_scope()) if requirement is None else requirement
        ),
        action_requests=requests,  # type: ignore[arg-type]
        run_id=_RUN_ID,
        recorded_at=_T0,
        level=level,
    )


def test_valid_active_applicable_binding_declares_exactly_l2_compiled() -> None:
    binding = _binding()

    assert COMPILED_PROCEDURE_STRATEGY_LEVEL is ExecutionLevel.L2_COMPILED
    assert binding.level is ExecutionLevel.L2_COMPILED
    assert binding.candidate.record.status is ProcedureStatus.ACTIVE
    assert binding.match_result.outcome is ProcedureMatchOutcome.EXACT_MATCH
    assert binding.match_result.structurally_applicable is True
    assert binding.action_sequence == ("write",)


def test_binding_rejects_wrong_execution_level() -> None:
    with pytest.raises(CompiledProcedureStrategyBindingError, match="exactly L2_COMPILED"):
        _binding(level=ExecutionLevel.L1_DIRECT)


@pytest.mark.parametrize("status", [ProcedureStatus.CANDIDATE, ProcedureStatus.RETIRED])
def test_non_active_procedure_is_rejected(status: ProcedureStatus) -> None:
    with pytest.raises(
        CompiledProcedureStrategyBindingError,
        match=r"requires ProcedureStatus.ACTIVE",
    ):
        _binding(record=_record(status=status))


def test_applicability_mismatch_is_rejected_by_canonical_match_result() -> None:
    with pytest.raises(CompiledProcedureStrategyBindingError, match="not structurally applicable"):
        _binding(requirement=ProcedureRequirement(scope=_scope("linux")))


def test_malformed_json_procedure_is_rejected() -> None:
    malformed = ProcedurePayload(
        kind=ProcedurePayloadKind.CANONICAL_JSON,
        content='{"not":"a procedure graph"}',
    )
    with pytest.raises(
        CompiledProcedureStrategyBindingError,
        match="valid canonical ProcedureGraph",
    ):
        _binding(record=_record(payload=malformed))


def test_malformed_action_contract_is_rejected() -> None:
    graph = ProcedureGraph(
        entry=ProcedureNodeId("write"),
        nodes=(
            ProcedureNode(
                id=ProcedureNodeId("write"),
                kind=ProcedureNodeKind.ACTION,
                params={"permission": "ADMIN"},
            ),
            EndNodeSpec().to_node("done"),
        ),
        edges=(
            ProcedureEdge(
                source=ProcedureNodeId("write"),
                target=ProcedureNodeId("done"),
                kind=ProcedureEdgeKind.NEXT,
            ),
        ),
    )
    with pytest.raises(CompiledProcedureStrategyBindingError, match=r"ACTION node.*malformed"):
        _binding(record=_record(graph=graph))


def test_hostile_end_data_is_inert_and_cannot_self_certify_task_success() -> None:
    graph = ProcedureGraph(
        entry=ProcedureNodeId("done"),
        nodes=(
            ProcedureNode(
                id=ProcedureNodeId("done"),
                kind=ProcedureNodeKind.END,
                params={"contract_version": 1, "task_success": True, "verified": True},
            ),
        ),
        edges=(),
    )
    binding = _binding(record=_record(graph=graph), action_requests={})
    harness = OrchestrationHarness()
    adapter = GovernedCompiledProcedureStrategy(executor=harness.executor, binding=binding)
    task = harness.make_task()

    result = adapter.attempt_with_evidence(
        task,
        harness.make_context(task),
        ExecutionLevel.L2_COMPILED,
    )

    assert result.strategy_result.outcome is None
    assert result.run_record is not None
    assert result.run_record.disposition is ProcedureRunDisposition.REACHED_END
    assert result.run_record.task_verification is ProcedureTaskVerification.NOT_ASSESSED
    assert harness.task_status(task).value == "pending"
    assert harness.capability.execute_calls == 0


def test_artifact_reference_payload_fails_closed_without_an_inline_graph() -> None:
    record = _record(
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.ARTIFACT_REFERENCE,
            content="artifact://compiled-procedure/204",
        )
    )
    with pytest.raises(CompiledProcedureStrategyBindingError, match="CANONICAL_JSON"):
        _binding(record=record)


def test_action_request_identity_must_match_the_inert_action_description() -> None:
    graph = ProcedureGraph(
        entry=ProcedureNodeId("write"),
        nodes=(
            ActionNodeSpec(
                capability_name="demo.other.write",
                capability_version="1.0.0",
                params={},
            ).to_node("write"),
            EndNodeSpec().to_node("done"),
        ),
        edges=(
            ProcedureEdge(
                source=ProcedureNodeId("write"),
                target=ProcedureNodeId("done"),
                kind=ProcedureEdgeKind.NEXT,
            ),
        ),
    )
    with pytest.raises(CompiledProcedureStrategyBindingError, match="identity does not match"):
        _binding(record=_record(graph=graph))


def test_attempt_at_wrong_level_is_unavailable_without_execution_or_evidence() -> None:
    harness = OrchestrationHarness()
    adapter = GovernedCompiledProcedureStrategy(executor=harness.executor, binding=_binding())
    task = harness.make_task()
    context = harness.make_context(task)

    result = adapter.attempt_with_evidence(task, context, ExecutionLevel.L1_DIRECT)

    assert result.strategy_result.outcome is None
    assert result.strategy_result.unavailable_reason == (
        "governed compiled procedure strategy is available only for L2_COMPILED"
    )
    assert result.run_record is None
    assert harness.capability.execute_calls == 0
    assert harness.capability.verify_calls == 0


def test_end_only_procedure_is_control_termination_not_task_success() -> None:
    graph = ProcedureGraph(
        entry=ProcedureNodeId("done"),
        nodes=(EndNodeSpec().to_node("done"),),
        edges=(),
    )
    binding = _binding(record=_record(graph=graph), action_requests={})
    harness = OrchestrationHarness()
    adapter = GovernedCompiledProcedureStrategy(executor=harness.executor, binding=binding)
    task = harness.make_task()

    result = adapter.attempt_with_evidence(
        task,
        harness.make_context(task),
        ExecutionLevel.L2_COMPILED,
    )

    assert result.strategy_result.outcome is None
    assert result.run_record is not None
    assert result.run_record.disposition is ProcedureRunDisposition.REACHED_END
    assert result.run_record.task_verification is ProcedureTaskVerification.NOT_ASSESSED
    assert harness.task_status(task).value == "pending"
    assert harness.capability.execute_calls == 0


def test_fixed_binding_inputs_produce_stable_match_and_action_sequence() -> None:
    first = _binding()
    second = _binding()

    assert first.match_result == second.match_result
    assert first.action_sequence == second.action_sequence == ("write",)
    assert first.run_id == second.run_id == _RUN_ID
    assert first.recorded_at == second.recorded_at == _T0
