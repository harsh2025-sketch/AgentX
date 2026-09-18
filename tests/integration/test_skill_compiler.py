"""Integration tests: the N2.08 skill compiler over real canonical objects.

These tests run the genuine canonical stage chain end to end and prove the
compiler produces a real canonical ``ProcedureGraph`` candidate, preserves
provenance through every canonical contract, and touches no store, task,
lifecycle, or execution surface while doing it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.core.ids import EpisodeId, ProcedureId, TaskId
from agentx.core.procedures import ProcedureStatus
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.learning.region_classification import RegionClassification
from agentx.procedure_synthesis import SynthesisOutcome
from agentx.procedures.graph import ProcedureEdgeKind, ProcedureGraph
from agentx.skill_compiler import (
    PIPELINE_STAGES,
    CompilerRejectionReason,
    EvidenceStatus,
    SkillCompilationOutcome,
    SkillCompilationResult,
    compile_skill_candidate,
)

_BASE = datetime(2026, 9, 8, tzinfo=UTC)
_CORR_TARGET = UUID("b1111111-1111-4111-8111-111111111111")
_CORR_SUPPORT = UUID("b2222222-2222-4222-8222-222222222222")
_CORR_SUPPORT_2 = UUID("b3333333-3333-4333-8333-333333333333")
_TASK = TaskId.parse("baaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_EPISODE = EpisodeId.parse("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
_PROCEDURE_ID = ProcedureId.parse("bccccccc-cccc-4ccc-8ccc-cccccccccccc")

type _Spec = tuple[str, dict[str, object], CausalOutcome]

_SCRIPT: tuple[_Spec, ...] = (
    ("report.open", {"target": "ledger"}, CausalOutcome.VERIFIED),
    ("report.filter", {"period": "2026-Q3"}, CausalOutcome.VERIFIED),
    ("report.export", {"path": "summary.csv"}, CausalOutcome.VERIFIED),
)


def _experience(
    *,
    correlation_id: UUID,
    offset: int,
    action_name: str,
    data: dict[str, object],
    outcome: CausalOutcome,
) -> CausalExperience:
    before_at = _BASE + timedelta(seconds=offset)
    verification: VerificationPayload | None = None
    verification_at: datetime | None = None
    observation: ObservationPayload | None = ObservationPayload(
        value={"state": "observed", "offset": offset}
    )
    observation_at: datetime | None = before_at + timedelta(seconds=2)
    state_after: ExperienceState | None = ExperienceState(
        captured_at=before_at + timedelta(seconds=3),
        observation=ObservationPayload(value={"state": "after", "offset": offset}),
    )
    if outcome is CausalOutcome.VERIFIED:
        verification = VerificationPayload(passed=True, detail="verified")
        verification_at = before_at + timedelta(seconds=4)
    elif outcome is CausalOutcome.VERIFICATION_FAILED:
        verification = VerificationPayload(passed=False, detail="verification failed")
        verification_at = before_at + timedelta(seconds=4)
    elif outcome is CausalOutcome.DENIED:
        observation = None
        observation_at = None
        state_after = None
    return CausalExperience(
        task_id=_TASK,
        correlation_id=correlation_id,
        episode_id=_EPISODE,
        state_before=ExperienceState(
            captured_at=before_at,
            observation=ObservationPayload(value={"state": "before", "offset": offset}),
        ),
        action=ActionPayload(name=action_name, data=dict(data)),
        action_at=before_at + timedelta(seconds=1),
        observation=observation,
        observation_at=observation_at,
        state_after=state_after,
        verification=verification,
        verification_at=verification_at,
        outcome=outcome,
        outcome_at=before_at + timedelta(seconds=5),
    )


def _run(
    specs: Iterable[_Spec] = _SCRIPT, *, correlation_id: UUID = _CORR_TARGET
) -> tuple[CausalExperience, ...]:
    return tuple(
        _experience(
            correlation_id=correlation_id,
            offset=index * 10,
            action_name=name,
            data=data,
            outcome=outcome,
        )
        for index, (name, data, outcome) in enumerate(specs)
    )


def _compile(
    specs: tuple[_Spec, ...] = _SCRIPT,
    *,
    support: tuple[tuple[UUID, tuple[_Spec, ...]], ...] = ((_CORR_SUPPORT, _SCRIPT),),
) -> SkillCompilationResult:
    return compile_skill_candidate(
        _run(specs),
        corroborating=tuple(
            _run(script, correlation_id=correlation) for correlation, script in support
        ),
        procedure_id=_PROCEDURE_ID,
        revision=1,
    )


# ---------------------------------------------------------------------------
# Full canonical pipeline.
# ---------------------------------------------------------------------------


def test_full_pipeline_builds_a_valid_canonical_candidate_graph() -> None:
    """The compiler output is a real canonical ProcedureGraph, not a copy."""
    result = _compile()

    assert result.outcome is SkillCompilationOutcome.CANDIDATE
    assert result.stages == PIPELINE_STAGES
    graph = result.graph
    assert graph is not None
    assert isinstance(graph, ProcedureGraph)

    rebuilt = ProcedureGraph(
        entry=graph.entry,
        nodes=graph.nodes,
        edges=graph.edges,
        schema_version=graph.schema_version,
    )
    assert rebuilt.to_dict() == graph.to_dict()

    kinds = sorted(node.kind.value for node in graph.nodes)
    assert kinds == ["action", "action", "action", "end"]
    assert {edge.kind for edge in graph.edges} == {ProcedureEdgeKind.NEXT}


def test_repeated_verified_runs_are_classified_deterministic() -> None:
    """Two corroborating verified runs give C3.06 its repeated-success evidence."""
    result = _compile(support=((_CORR_SUPPORT, _SCRIPT), (_CORR_SUPPORT_2, _SCRIPT)))

    assert result.outcome is SkillCompilationOutcome.CANDIDATE
    assert {action.classification for action in result.regions.actions} == {
        RegionClassification.DETERMINISTIC
    }
    assert all(
        item.status is EvidenceStatus.VERIFIED_SUCCESS
        for run in result.evidence
        for item in run.assessments
    )


def test_corroborating_verified_runs_can_parameterize_a_single_action() -> None:
    target: tuple[_Spec, ...] = (
        (
            "filesystem.read_text@1.0.0",
            {"path": "train-a.txt", "max_bytes": 256},
            CausalOutcome.VERIFIED,
        ),
    )
    support: tuple[_Spec, ...] = (
        (
            "filesystem.read_text@1.0.0",
            {"path": "train-b.txt", "max_bytes": 256},
            CausalOutcome.VERIFIED,
        ),
    )

    result = _compile(target, support=((_CORR_SUPPORT, support),))

    assert result.outcome is SkillCompilationOutcome.CANDIDATE
    assert {action.classification for action in result.regions.actions} == {
        RegionClassification.DETERMINISTIC
    }
    assert result.graph is not None
    action = next(node for node in result.graph.nodes if node.kind.value == "action")
    parameters = action.params["parameters"]
    assert isinstance(parameters, dict)
    descriptor = parameters["path"]
    assert isinstance(descriptor, dict)
    assert descriptor["observed_values"] == ["train-a.txt", "train-b.txt"]
    assert descriptor["observation_count"] == 2
    assert action.params["constants"] == {"max_bytes": 256}


def test_pipeline_is_deterministic_across_independent_invocations() -> None:
    """Identical canonical history compiles to a byte-identical report."""
    first = _compile()
    second = _compile()

    assert first.to_json() == second.to_json()
    assert json.loads(first.to_json()) == json.loads(second.to_json())


def test_provenance_survives_every_canonical_contract() -> None:
    """The C3.01 identity and per-step fingerprints reach the candidate report."""
    result = _compile()
    payload = json.loads(result.to_json())

    assert payload["source_trajectory_id"] == str(result.trajectory.trajectory_id)
    fingerprints = [step.source_experience_sha256 for step in result.trajectory.steps]
    assert [
        item["source_experience_sha256"] for item in payload["evidence"][0]["assessments"]
    ] == fingerprints
    assert [
        item["source_experience_sha256"] for item in payload["synthesis"]["included"]
    ] == fingerprints


def test_denied_step_is_excluded_but_preserved_as_evidence() -> None:
    """C3.03 elimination survives composition; nothing is deleted from history."""
    script: tuple[_Spec, ...] = (
        ("report.open", {"target": "ledger"}, CausalOutcome.VERIFIED),
        ("shell.escalate", {"command": "sudo"}, CausalOutcome.DENIED),
        ("report.export", {"path": "summary.csv"}, CausalOutcome.VERIFIED),
    )
    result = _compile(script, support=((_CORR_SUPPORT, script),))

    assert result.outcome is SkillCompilationOutcome.CANDIDATE
    assert len(result.trajectory.steps) == 3
    assert [item.sequence for item in result.synthesis.excluded] == [2]
    graph = result.graph
    assert graph is not None
    assert "shell.escalate" not in graph.to_json()
    assert result.target_evidence.assessments[1].status is EvidenceStatus.DENIED


def test_unverified_history_yields_an_explicit_refusal() -> None:
    """A failed step refuses the candidate and says exactly why."""
    script: tuple[_Spec, ...] = (
        ("report.open", {"target": "ledger"}, CausalOutcome.VERIFIED),
        ("report.filter", {"period": "2026-Q3"}, CausalOutcome.EXECUTION_FAILED),
        ("report.export", {"path": "summary.csv"}, CausalOutcome.VERIFIED),
    )
    result = _compile(script, support=((_CORR_SUPPORT, script),))

    assert result.outcome is SkillCompilationOutcome.REJECTED
    assert CompilerRejectionReason.UNVERIFIED_INCLUDED_ACTION in result.rejection_reasons
    assert result.graph is None
    assert result.status is None


# ---------------------------------------------------------------------------
# No downstream authority is exercised.
# ---------------------------------------------------------------------------


def test_compilation_never_writes_to_the_procedure_store(tmp_path: Path) -> None:
    """A candidate is constructed in memory; no ProcedureStore row appears."""
    store = ProcedureStore(SQLiteDatabase(tmp_path / "agentx.sqlite3"))

    assert store.list_records() == ()

    result = _compile()

    assert result.outcome is SkillCompilationOutcome.CANDIDATE
    assert store.list_records() == ()
    assert store.history(_PROCEDURE_ID) == ()
    assert store.get(_PROCEDURE_ID, 1) is None


def test_compilation_never_transitions_a_task() -> None:
    """Task state is untouched by compiling a candidate from its history."""
    task = Task.create("compile the export skill")
    before = task.to_dict()

    result = _compile()

    assert result.outcome is SkillCompilationOutcome.CANDIDATE
    assert task.status is TaskStatus.PENDING
    assert task.to_dict() == before


def test_candidate_status_is_never_active_end_to_end() -> None:
    """Nothing in the end-to-end report can be read as an active procedure."""
    result = _compile()
    payload = result.to_json()

    assert result.status is ProcedureStatus.CANDIDATE
    assert result.synthesis.outcome is SynthesisOutcome.CANDIDATE
    assert f'"{ProcedureStatus.ACTIVE.value}"' not in payload
    assert f'"{ProcedureStatus.RETIRED.value}"' not in payload
