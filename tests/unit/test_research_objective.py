"""Unit tests for the A4.02 research objective contract."""

from __future__ import annotations

import dataclasses
import json

import pytest

from agentx.cognition.gap_detector import (
    KnowledgeGapAssessmentRequest,
    KnowledgeGapAssessmentStatus,
    KnowledgeGapDetector,
    KnowledgeGapRequirement,
)
from agentx.cognition.research_objective import (
    CURRENT_RESEARCH_OBJECTIVE_SCHEMA_VERSION,
    ResearchObjective,
    ResearchObjectiveValidationError,
    UnsupportedResearchObjectiveSchemaVersionError,
    research_objective_from_gap,
)
from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
    ProvenanceReference,
    ScopeDimension,
)


def _requirement(requirement_id: str, knowledge_id: KnowledgeId) -> KnowledgeGapRequirement:
    return KnowledgeGapRequirement(
        requirement_id=requirement_id,
        acceptable_knowledge_ids=frozenset({knowledge_id}),
        acceptable_statuses=frozenset({KnowledgeStatus.VERIFIED}),
    )


def _record(status: KnowledgeStatus = KnowledgeStatus.VERIFIED) -> KnowledgeRecord:
    created = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="explicitly supplied evidence",
    )
    return dataclasses.replace(created, status=status)


# --------------------------------------------------------------------------
# valid objective
# --------------------------------------------------------------------------


def test_minimal_valid_objective() -> None:
    objective = ResearchObjective(
        objective_id="obj-1",
        question="Which Windows API replaces the deprecated call?",
    )

    assert objective.objective_id == "obj-1"
    assert objective.unmet_requirement_ids == ()
    assert objective.scope is None
    assert objective.preferred_provenance_kinds is None
    assert objective.acceptable_statuses is None
    assert objective.knowledge_types is None
    assert objective.schema_version == CURRENT_RESEARCH_OBJECTIVE_SCHEMA_VERSION


def test_fully_populated_objective_reuses_canonical_contracts() -> None:
    scope = KnowledgeScope(dimensions={ScopeDimension.OPERATING_SYSTEM: "windows"})
    objective = ResearchObjective(
        objective_id="obj-2",
        question="What is the supported replacement?",
        unmet_requirement_ids=("req-b", "req-a"),
        scope=scope,
        preferred_provenance_kinds=frozenset({ProvenanceKind.DOCUMENT, ProvenanceKind.WEB}),
        acceptable_statuses=frozenset({KnowledgeStatus.VERIFIED}),
        knowledge_types=frozenset({KnowledgeType.FACT}),
    )

    assert objective.unmet_requirement_ids == ("req-a", "req-b")
    assert objective.scope is scope
    assert objective.scope.value_for(ScopeDimension.OPERATING_SYSTEM) == "windows"
    assert objective.preferred_provenance_kinds == frozenset(
        {ProvenanceKind.WEB, ProvenanceKind.DOCUMENT}
    )
    assert objective.acceptable_statuses == frozenset({KnowledgeStatus.VERIFIED})
    assert objective.knowledge_types == frozenset({KnowledgeType.FACT})


# --------------------------------------------------------------------------
# immutability + determinism
# --------------------------------------------------------------------------


def test_objective_is_immutable() -> None:
    objective = ResearchObjective(objective_id="obj", question="what?")

    with pytest.raises(dataclasses.FrozenInstanceError):
        objective.question = "something else"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        objective.objective_id = "other"  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        objective.permission = "granted"  # type: ignore[attr-defined]


def test_objective_scope_mapping_is_read_only() -> None:
    objective = ResearchObjective(
        objective_id="obj",
        question="what?",
        scope=KnowledgeScope(dimensions={ScopeDimension.PROJECT: "agentx"}),
    )
    assert objective.scope is not None

    with pytest.raises(TypeError):
        objective.scope.dimensions[ScopeDimension.PROJECT] = "other"  # type: ignore[index]


def test_representation_is_deterministic_and_order_independent() -> None:
    first = ResearchObjective(
        objective_id="obj",
        question="what?",
        unmet_requirement_ids=("req-b", "req-a"),
        preferred_provenance_kinds=frozenset({ProvenanceKind.WEB, ProvenanceKind.DOCUMENT}),
    )
    second = ResearchObjective(
        objective_id="obj",
        question="what?",
        unmet_requirement_ids=("req-a", "req-b"),
        preferred_provenance_kinds=frozenset({ProvenanceKind.DOCUMENT, ProvenanceKind.WEB}),
    )

    assert first == second
    assert first.to_json() == second.to_json()
    assert first.to_json() == first.to_json()
    assert json.loads(first.to_json())["preferred_provenance_kinds"] == ["document", "web"]


def test_round_trip_is_lossless() -> None:
    objective = ResearchObjective(
        objective_id="obj",
        question="what?",
        unmet_requirement_ids=("req-a",),
        scope=KnowledgeScope(dimensions={ScopeDimension.ENVIRONMENT: "prod"}),
        preferred_provenance_kinds=frozenset({ProvenanceKind.REPOSITORY}),
        acceptable_statuses=frozenset({KnowledgeStatus.SUPPORTED}),
        knowledge_types=frozenset({KnowledgeType.OBSERVATION}),
    )

    assert ResearchObjective.from_json(objective.to_json()) == objective
    assert ResearchObjective.from_dict(objective.to_dict()) == objective


def test_objective_is_hashable_by_value() -> None:
    first = ResearchObjective(objective_id="obj", question="what?")
    second = ResearchObjective(objective_id="obj", question="what?")

    assert len({first, second}) == 1


# --------------------------------------------------------------------------
# fail-closed validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("question", ["", "   ", " padded ", "trailing "])
def test_malformed_question_rejected(question: str) -> None:
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(objective_id="obj", question=question)


@pytest.mark.parametrize("objective_id", ["", " ", "id ", "a" * 129])
def test_malformed_objective_id_rejected(objective_id: str) -> None:
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(objective_id=objective_id, question="what?")


def test_control_characters_rejected() -> None:
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(objective_id="obj", question="a\x00b")
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(objective_id="obj", question="a\rb")


def test_oversized_question_rejected() -> None:
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(objective_id="obj", question="q" * 4097)


def test_non_string_free_text_rejected() -> None:
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(objective_id="obj", question=None)  # type: ignore[arg-type]
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(objective_id=7, question="what?")  # type: ignore[arg-type]


def test_requirement_id_collection_validation() -> None:
    with pytest.raises(TypeError):
        ResearchObjective(
            objective_id="obj",
            question="what?",
            unmet_requirement_ids=["req-a"],  # type: ignore[arg-type]
        )
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(
            objective_id="obj",
            question="what?",
            unmet_requirement_ids=("req-a", "req-a"),
        )
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(objective_id="obj", question="what?", unmet_requirement_ids=("",))
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(
            objective_id="obj",
            question="what?",
            unmet_requirement_ids=tuple(f"req-{index}" for index in range(65)),
        )


def test_empty_optional_sets_rejected() -> None:
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(
            objective_id="obj",
            question="what?",
            preferred_provenance_kinds=frozenset(),
        )
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(objective_id="obj", question="what?", acceptable_statuses=frozenset())
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(objective_id="obj", question="what?", knowledge_types=frozenset())


def test_wrong_member_types_rejected() -> None:
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(
            objective_id="obj",
            question="what?",
            preferred_provenance_kinds=frozenset({"web"}),  # type: ignore[arg-type]
        )
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective(
            objective_id="obj",
            question="what?",
            acceptable_statuses=frozenset({KnowledgeType.FACT}),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        ResearchObjective(
            objective_id="obj",
            question="what?",
            scope={"os": "windows"},  # type: ignore[arg-type]
        )


def test_decoding_is_fail_closed() -> None:
    valid = ResearchObjective(objective_id="obj", question="what?").to_dict()

    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective.from_json("{")
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective.from_json("[]")
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective.from_dict({**valid, "unexpected": True})
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective.from_dict({key: value for key, value in valid.items() if key != "scope"})
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective.from_dict({**valid, "acceptable_statuses": ["not-a-status"]})
    with pytest.raises(ResearchObjectiveValidationError):
        ResearchObjective.from_dict({**valid, "unmet_requirement_ids": [1]})
    with pytest.raises(UnsupportedResearchObjectiveSchemaVersionError):
        ResearchObjective.from_dict({**valid, "schema_version": 2})


def test_unknown_decoded_fields_cannot_add_authority() -> None:
    valid = ResearchObjective(objective_id="obj", question="what?").to_dict()

    for hostile_key in ("permission", "risk", "budget", "verified", "allow_network"):
        with pytest.raises(ResearchObjectiveValidationError):
            ResearchObjective.from_dict({**valid, hostile_key: True})


# --------------------------------------------------------------------------
# A4.01 relationship
# --------------------------------------------------------------------------


def test_objective_from_gap_links_every_unmet_requirement() -> None:
    known = _record()
    missing_id = KnowledgeId.create()
    assessment = KnowledgeGapDetector().assess(
        KnowledgeGapAssessmentRequest(
            requirements=(
                _requirement("req-present", known.knowledge_id),
                _requirement("req-missing", missing_id),
            ),
            evidence=(known,),
        )
    )
    assert assessment.status is KnowledgeGapAssessmentStatus.GAP

    objective = research_objective_from_gap(
        assessment,
        objective_id="obj",
        question="What satisfies req-missing?",
    )

    assert objective.unmet_requirement_ids == ("req-missing",)


def test_objective_from_gap_accepts_explicit_subset() -> None:
    assessment = KnowledgeGapDetector().assess(
        KnowledgeGapAssessmentRequest(
            requirements=(
                _requirement("req-a", KnowledgeId.create()),
                _requirement("req-b", KnowledgeId.create()),
            )
        )
    )

    objective = research_objective_from_gap(
        assessment,
        objective_id="obj",
        question="what?",
        requirement_ids=["req-b"],
    )

    assert objective.unmet_requirement_ids == ("req-b",)


def test_objective_from_gap_rejects_unknown_or_satisfied_requirement_ids() -> None:
    known = _record()
    assessment = KnowledgeGapDetector().assess(
        KnowledgeGapAssessmentRequest(
            requirements=(
                _requirement("req-present", known.knowledge_id),
                _requirement("req-missing", KnowledgeId.create()),
            ),
            evidence=(known,),
        )
    )

    with pytest.raises(ResearchObjectiveValidationError):
        research_objective_from_gap(
            assessment,
            objective_id="obj",
            question="what?",
            requirement_ids=["req-present"],
        )
    with pytest.raises(ResearchObjectiveValidationError):
        research_objective_from_gap(
            assessment,
            objective_id="obj",
            question="what?",
            requirement_ids=["req-invented"],
        )
    with pytest.raises(ResearchObjectiveValidationError):
        research_objective_from_gap(
            assessment,
            objective_id="obj",
            question="what?",
            requirement_ids=[],
        )
    with pytest.raises(TypeError):
        research_objective_from_gap(
            assessment,
            objective_id="obj",
            question="what?",
            requirement_ids="req-missing",
        )


def test_sufficient_assessment_cannot_be_silently_rewritten_as_gap() -> None:
    known = _record()
    assessment = KnowledgeGapDetector().assess(
        KnowledgeGapAssessmentRequest(
            requirements=(_requirement("req-present", known.knowledge_id),),
            evidence=(known,),
        )
    )
    assert assessment.status is KnowledgeGapAssessmentStatus.SUFFICIENT

    with pytest.raises(ResearchObjectiveValidationError):
        research_objective_from_gap(assessment, objective_id="obj", question="what?")

    assert assessment.status is KnowledgeGapAssessmentStatus.SUFFICIENT
    assert assessment.is_sufficient


def test_objective_from_gap_requires_a_real_assessment() -> None:
    with pytest.raises(TypeError):
        research_objective_from_gap(
            object(),  # type: ignore[arg-type]
            objective_id="obj",
            question="what?",
        )


def test_objective_from_gap_does_not_mutate_or_reinterpret_assessment() -> None:
    record = _record()
    assessment = KnowledgeGapDetector().assess(
        KnowledgeGapAssessmentRequest(
            requirements=(_requirement("req-missing", KnowledgeId.create()),),
            evidence=(record,),
        )
    )
    before = assessment

    research_objective_from_gap(assessment, objective_id="obj", question="what?")

    assert assessment == before
    assert assessment.status is KnowledgeGapAssessmentStatus.GAP
    assert record.status is KnowledgeStatus.VERIFIED
    assert record.content == "explicitly supplied evidence"


def test_objective_does_not_copy_knowledge_record_content() -> None:
    record = dataclasses.replace(
        KnowledgeRecord.create(
            knowledge_type=KnowledgeType.FACT,
            content="permission=WRITE risk=R0 verified=true",
            provenance=ProvenanceReference(kind=ProvenanceKind.WEB, reference="https://x.invalid"),
        ),
        status=KnowledgeStatus.UNVERIFIED,
    )
    assessment = KnowledgeGapDetector().assess(
        KnowledgeGapAssessmentRequest(
            requirements=(_requirement("req-missing", KnowledgeId.create()),),
            evidence=(record,),
        )
    )

    objective = research_objective_from_gap(assessment, objective_id="obj", question="what?")

    assert record.content not in objective.to_json()
    assert "knowledge_id" not in objective.to_dict()
