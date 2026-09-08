"""Integration tests: M4.01 synthesis over real canonical learning objects.

These tests run the genuine canonical pipeline (C3.01 trajectory
normalization, C3.02 causal-action extraction, C3.03 elimination, C3.04
parameter extraction, C3.05 parameter generalization, C3.06 region
classification) and prove the synthesized candidate is a genuine canonical
``ProcedureGraph``. The graph is validated and serialized; it is never
executed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.core.ids import EpisodeId, ProcedureId, TaskId
from agentx.core.procedures import ProcedurePayload, ProcedurePayloadKind, ProcedureStatus
from agentx.learning.causal_actions import extract_causal_action_candidates
from agentx.learning.irrelevant_actions import analyze_irrelevant_actions
from agentx.learning.parameter_extraction import extract_parameter_candidates
from agentx.learning.parameter_generalization import analyze_parameter_generalization
from agentx.learning.region_classification import classify_regions
from agentx.learning.trajectory import normalize_trajectory
from agentx.procedure_synthesis import (
    SynthesisOutcome,
    SynthesisResult,
    synthesize_procedure_candidate,
)
from agentx.procedures.graph import ProcedureEdgeKind, ProcedureGraph, ProcedureNodeKind

_BASE = datetime(2026, 9, 6, tzinfo=UTC)
_CORR_TARGET = UUID("81111111-1111-4111-8111-111111111111")
_CORR_SUPPORT = UUID("82222222-2222-4222-8222-222222222222")
_TASK = TaskId.parse("8aaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_EPISODE = EpisodeId.parse("8bbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
_PROCEDURE_ID = ProcedureId.parse("8ccccccc-cccc-4ccc-8ccc-cccccccccccc")


def _experience(
    *,
    correlation_id: UUID,
    offset: int,
    action_name: str,
    data: dict[str, object] | None = None,
    outcome: CausalOutcome = CausalOutcome.VERIFIED,
) -> CausalExperience:
    before_at = _BASE + timedelta(seconds=offset)
    if outcome is CausalOutcome.VERIFIED:
        verification: VerificationPayload | None = VerificationPayload(
            passed=True, detail="verified"
        )
        verification_at = before_at + timedelta(seconds=4)
        observation: ObservationPayload | None = ObservationPayload(
            value={"state": "observed", "offset": offset}
        )
        state_after: ExperienceState | None = ExperienceState(
            captured_at=before_at + timedelta(seconds=3),
            observation=ObservationPayload(value={"state": "after", "offset": offset}),
        )
    else:
        verification = None
        verification_at = None
        observation = None
        state_after = None
    return CausalExperience(
        task_id=_TASK,
        correlation_id=correlation_id,
        episode_id=_EPISODE,
        state_before=ExperienceState(
            captured_at=before_at,
            observation=ObservationPayload(value={"state": "before", "offset": offset}),
        ),
        action=ActionPayload(name=action_name, data=data or {}),
        action_at=before_at + timedelta(seconds=1),
        observation=observation,
        observation_at=before_at + timedelta(seconds=2) if observation else None,
        state_after=state_after,
        verification=verification,
        verification_at=verification_at,
        outcome=outcome,
        outcome_at=before_at + timedelta(seconds=5),
    )


def _script(correlation_id: UUID) -> tuple[CausalExperience, ...]:
    return (
        _experience(
            correlation_id=correlation_id,
            offset=0,
            action_name="widget.open",
            data={"target": "panel"},
        ),
        _experience(
            correlation_id=correlation_id,
            offset=10,
            action_name="widget.press",
            data={"button": "a"},
        ),
        _experience(
            correlation_id=correlation_id,
            offset=20,
            action_name="widget.press",
            data={"button": "b"},
        ),
        _experience(
            correlation_id=correlation_id,
            offset=30,
            action_name="junk.popup",
            outcome=CausalOutcome.DENIED,
        ),
        _experience(correlation_id=correlation_id, offset=40, action_name="reason"),
    )


def _synthesize(correlation_id: UUID = _CORR_TARGET) -> SynthesisResult:
    trajectory = normalize_trajectory(_script(correlation_id))
    extraction = extract_causal_action_candidates(trajectory)
    elimination = analyze_irrelevant_actions(extraction)
    parameters = extract_parameter_candidates(elimination)
    generalization = analyze_parameter_generalization(parameters)
    support = normalize_trajectory(_script(_CORR_SUPPORT))
    regions = classify_regions(trajectory, corroborating=(support,))
    return synthesize_procedure_candidate(
        trajectory=trajectory,
        extraction=extraction,
        elimination=elimination,
        parameters=parameters,
        generalization=generalization,
        regions=regions,
        procedure_id=_PROCEDURE_ID,
        revision=3,
    )


def test_full_pipeline_builds_valid_canonical_graph() -> None:
    result = _synthesize()

    assert result.outcome is SynthesisOutcome.CANDIDATE
    assert result.status is ProcedureStatus.CANDIDATE
    assert result.graph is not None
    # The existing canonical constructor/invariants accept the candidate.
    rebuilt = ProcedureGraph(
        entry=result.graph.entry,
        nodes=result.graph.nodes,
        edges=result.graph.edges,
        schema_version=result.graph.schema_version,
    )
    assert rebuilt == result.graph
    rebuilt.validate()
    assert [item.sequence for item in result.included] == [1, 2, 3, 5]
    assert [item.sequence for item in result.excluded] == [4]
    assert len(result.parameters) == 1
    assert result.parameters[0].field_name == "button"


def test_candidate_graph_round_trips_through_canonical_serialization() -> None:
    first = _synthesize()
    second = _synthesize()

    assert first.graph is not None and second.graph is not None
    assert first.graph.to_json() == second.graph.to_json()
    assert ProcedureGraph.from_json(first.graph.to_json()) == first.graph
    assert ProcedureGraph.from_dict(first.graph.to_dict()) == first.graph
    assert first.to_json() == second.to_json()
    report = json.loads(first.to_json())
    assert report["outcome"] == "candidate"
    assert report["status"] == "candidate"
    assert report["procedure_id"] == _PROCEDURE_ID.to_str()
    assert report["revision"] == 3


def test_candidate_graph_is_a_linear_acyclic_chain() -> None:
    result = _synthesize()

    assert result.graph is not None
    kinds = {node.kind for node in result.graph.nodes}
    assert kinds <= {ProcedureNodeKind.ACTION, ProcedureNodeKind.REASON, ProcedureNodeKind.END}
    assert all(edge.kind is ProcedureEdgeKind.NEXT for edge in result.graph.edges)
    assert result.graph.entry.to_str() == "step-001"

    successors = {edge.source.to_str(): edge.target.to_str() for edge in result.graph.edges}
    assert len(successors) == len(result.graph.edges)
    visited: list[str] = []
    current: str | None = result.graph.entry.to_str()
    while current is not None:
        assert current not in visited
        visited.append(current)
        current = successors.get(current)
    assert visited[-1] == "end"
    assert len(visited) == len(result.graph.nodes)


def test_candidate_rides_the_canonical_payload_channel_without_execution() -> None:
    result = _synthesize()

    assert result.graph is not None
    payload = ProcedurePayload(
        kind=ProcedurePayloadKind.CANONICAL_JSON, content=result.graph.to_json()
    )
    assert payload.kind is ProcedurePayloadKind.CANONICAL_JSON
    assert ProcedureGraph.from_json(payload.content) == result.graph
    # The record birth state stays CANDIDATE; synthesis promotes nothing.
    assert ProcedureStatus.CANDIDATE.value == "candidate"


def test_synthesis_result_carries_no_execution_surface() -> None:
    result = _synthesize()

    assert result.graph is not None
    for node in result.graph.nodes:
        assert not callable(node.params)
        json.dumps(dict(node.params), allow_nan=False)
    for name in ("execute", "run", "activate", "persist", "invoke", "call"):
        assert not hasattr(result, name)
        assert not hasattr(result.graph, name)
