"""Tests for the A3.07 deterministic procedure condition evaluator.

These tests prove that ``agentx.procedures.condition_evaluation`` answers
exactly one question — what one canonical A3.06
``ProcedureCondition`` can establish from explicit caller-supplied facts —
deterministically and inertly:

    - the three-value vocabulary: SATISFIED, UNSATISFIED, UNKNOWN;
    - missing evidence is UNKNOWN, never a silent ``UNSATISFIED``;
    - exact typed addressing (evidence kind + equal reference);
    - contradiction resolves conservatively to UNKNOWN;
    - strict, fail-closed validation of facts and request objects;
    - deterministic repeated evaluation and immutable results/facts;
    - compatibility with every canonical A3.06 condition family: all
      evidence kinds, referenced and reference-less conditions, procedure
      and node-scoped preconditions and postconditions across every
      A3.01-A3.05 node family.

The authority/adversarial properties (hostile strings, no evaluation
engine, no authority touch, I1) are covered in
``tests/adversarial/test_condition_evaluation_authority.py`` and
``tests/architecture/test_condition_evaluation_boundaries.py``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from agentx.core.ids import ProcedureId
from agentx.core.provenance import EvidenceKind
from agentx.procedures.branch import BranchContract, BranchOutcome
from agentx.procedures.condition_evaluation import (
    ConditionEvaluation,
    ConditionEvaluationError,
    ConditionEvaluationStatus,
    EvidenceFact,
    EvidenceFacts,
    evaluate_condition,
)
from agentx.procedures.conditions import (
    ConditionId,
    NodeConditions,
    ProcedureCondition,
    ProcedureConditions,
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
from agentx.procedures.reason_research import ReasonNodeSpec, ResearchNodeSpec
from agentx.procedures.rollback import RollbackNodeSpec, RollbackScope, RollbackScopeKind
from agentx.procedures.subprocedure import SubprocedureNodeSpec
from agentx.procedures.transform import TransformContract
from agentx.procedures.wait import WaitContract


def _condition(
    name: str = "cfg-present",
    *,
    statement: str = "the agentx configuration file exists",
    evidence_kind: EvidenceKind = EvidenceKind.ARTIFACT,
    reference: str | None = "artifact:agentx.toml",
) -> ProcedureCondition:
    return ProcedureCondition(
        id=ConditionId(name),
        statement=statement,
        evidence_kind=evidence_kind,
        evidence_reference=reference,
    )


def _fact(
    *,
    evidence_kind: EvidenceKind = EvidenceKind.ARTIFACT,
    reference: str | None = "artifact:agentx.toml",
    established: bool = True,
) -> EvidenceFact:
    return EvidenceFact(
        evidence_kind=evidence_kind,
        evidence_reference=reference,
        established=established,
    )


# ---------------------------------------------------------------------------
# The three-value vocabulary
# ---------------------------------------------------------------------------


def test_addressing_fact_asserting_established_evaluates_satisfied() -> None:
    evaluation = evaluate_condition(_condition(), EvidenceFacts(facts=(_fact(),)))
    assert evaluation.status is ConditionEvaluationStatus.SATISFIED
    assert evaluation.condition_id == _condition().id


def test_addressing_fact_asserting_refuted_evaluates_unsatisfied() -> None:
    evaluation = evaluate_condition(_condition(), EvidenceFacts(facts=(_fact(established=False),)))
    assert evaluation.status is ConditionEvaluationStatus.UNSATISFIED
    assert evaluation.condition_id.to_str() == "cfg-present"


def test_no_facts_at_all_evaluates_unknown() -> None:
    evaluation = evaluate_condition(_condition(), EvidenceFacts())
    assert evaluation.status is ConditionEvaluationStatus.UNKNOWN


@pytest.mark.parametrize(
    "fact",
    [
        _fact(evidence_kind=EvidenceKind.OBSERVATION),  # different kind
        _fact(evidence_kind=EvidenceKind.KNOWLEDGE_RECORD),
        _fact(reference="artifact:other.toml"),  # different reference
        _fact(reference=None),  # reference-less fact vs referenced condition
    ],
)
def test_non_addressing_facts_leave_the_condition_unknown(fact: EvidenceFact) -> None:
    """Missing evidence must never become a silent UNSATISFIED."""
    evaluation = evaluate_condition(_condition(), EvidenceFacts(facts=(fact,)))
    assert evaluation.status is ConditionEvaluationStatus.UNKNOWN


def test_referenced_fact_does_not_address_a_reference_less_condition() -> None:
    condition = _condition(reference=None)
    facts = EvidenceFacts(
        facts=(
            _fact(reference="artifact:agentx.toml"),
            _fact(reference="artifact:anything.toml", established=False),
        )
    )
    assert evaluate_condition(condition, facts).status is ConditionEvaluationStatus.UNKNOWN


def test_reference_less_fact_does_not_address_a_referenced_condition() -> None:
    condition = _condition(reference="artifact:agentx.toml")
    facts = EvidenceFacts(facts=(_fact(reference=None, established=False),))
    assert evaluate_condition(condition, facts).status is ConditionEvaluationStatus.UNKNOWN


def test_reference_less_fact_addresses_a_reference_less_condition() -> None:
    condition = _condition(reference=None)
    facts = EvidenceFacts(facts=(_fact(reference=None),))
    assert evaluate_condition(condition, facts).status is ConditionEvaluationStatus.SATISFIED


def test_contradicting_addressing_facts_evaluate_unknown() -> None:
    """Contradiction establishes nothing deterministically — neither pole."""
    facts = EvidenceFacts(
        facts=(
            _fact(established=True),
            _fact(established=False),
        )
    )
    evaluation = evaluate_condition(_condition(), facts)
    assert evaluation.status is ConditionEvaluationStatus.UNKNOWN


def test_duplicate_agreeing_facts_are_idempotent() -> None:
    facts = EvidenceFacts(facts=(_fact(), _fact(), _fact()))
    assert evaluate_condition(_condition(), facts).status is ConditionEvaluationStatus.SATISFIED


def test_mixed_addressing_and_non_addressing_facts_use_only_addressing_ones() -> None:
    facts = EvidenceFacts(
        facts=(
            _fact(evidence_kind=EvidenceKind.OBSERVATION, established=False),
            _fact(established=True),
        )
    )
    assert evaluate_condition(_condition(), facts).status is ConditionEvaluationStatus.SATISFIED


@pytest.mark.parametrize("evidence_kind", list(EvidenceKind))
def test_every_canonical_evidence_kind_is_evaluable(evidence_kind: EvidenceKind) -> None:
    """Compatibility with the full canonical evidence vocabulary."""
    condition = _condition(evidence_kind=evidence_kind)
    other_kind = next(kind for kind in EvidenceKind if kind is not evidence_kind)
    establishing = EvidenceFacts(facts=(_fact(evidence_kind=evidence_kind, established=True),))
    refuting = EvidenceFacts(facts=(_fact(evidence_kind=evidence_kind, established=False),))
    wrong_kind = EvidenceFacts(facts=(_fact(evidence_kind=other_kind, established=True),))
    assert evaluate_condition(condition, establishing).status is ConditionEvaluationStatus.SATISFIED
    assert evaluate_condition(condition, refuting).status is ConditionEvaluationStatus.UNSATISFIED
    assert evaluate_condition(condition, wrong_kind).status is ConditionEvaluationStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Compatibility with all canonical A3.06 condition families
# ---------------------------------------------------------------------------


def _all_families_graph() -> ProcedureGraph:
    """Build a graph containing every canonical A3.01-A3.05 node family."""
    nodes = (
        ProcedureNode(ProcedureNodeId("start"), ProcedureNodeKind.ACTION),
        ProcedureNode(ProcedureNodeId("probe"), ProcedureNodeKind.OBSERVE),
        ProcedureNode(ProcedureNodeId("check"), ProcedureNodeKind.VERIFY),
        BranchContract(
            outcomes=(
                BranchOutcome(name="ok", condition=None),
                BranchOutcome(name="retry", condition="probe evidence inconclusive"),
            )
        ).to_node("route"),
        TransformContract(operation="normalize", arguments={"scale": 2}).to_node("shape"),
        WaitContract(requirement="operator approval", timeout_seconds=30.0).to_node("hold"),
        ReasonNodeSpec(objective="summarize the observation", output_binding="summary").to_node(
            node_id=ProcedureNodeId("think")
        ),
        ResearchNodeSpec(
            objective="locate the configuration reference", output_binding="reference"
        ).to_node(node_id=ProcedureNodeId("find")),
        RollbackNodeSpec(scope=RollbackScope(kind=RollbackScopeKind.PROCEDURE_EXECUTION)).to_node(
            "undo"
        ),
        SubprocedureNodeSpec(
            procedure_id=ProcedureId(UUID("55555555-5555-4555-8555-555555555555")),
            revision=3,
        ).to_node("sub"),
        EndNodeSpec().to_node("finish"),
    )
    edges = (
        ProcedureEdge(ProcedureNodeId("start"), ProcedureNodeId("probe")),
        ProcedureEdge(ProcedureNodeId("probe"), ProcedureNodeId("check")),
        ProcedureEdge(ProcedureNodeId("check"), ProcedureNodeId("route")),
        ProcedureEdge(ProcedureNodeId("route"), ProcedureNodeId("shape")),
        ProcedureEdge(ProcedureNodeId("shape"), ProcedureNodeId("hold")),
        ProcedureEdge(ProcedureNodeId("hold"), ProcedureNodeId("think")),
        ProcedureEdge(ProcedureNodeId("think"), ProcedureNodeId("find")),
        ProcedureEdge(ProcedureNodeId("find"), ProcedureNodeId("undo")),
        ProcedureEdge(ProcedureNodeId("undo"), ProcedureNodeId("sub")),
        ProcedureEdge(ProcedureNodeId("sub"), ProcedureNodeId("finish")),
        ProcedureEdge(
            ProcedureNodeId("check"),
            ProcedureNodeId("undo"),
            ProcedureEdgeKind.RECOVERY,
        ),
    )
    return ProcedureGraph(entry=ProcedureNodeId("start"), nodes=nodes, edges=edges)


def _all_families_document() -> ProcedureConditions:
    """A canonical A3.06 document: procedure-level pre/postconditions plus
    node-scoped conditions on every A3.01-A3.05 node family."""
    return ProcedureConditions(
        preconditions=(
            _condition("proc-pre", reference="artifact:agentx.toml"),
            ProcedureCondition(
                id=ConditionId("proc-pre-knowledge"),
                statement="the knowledge store answers a trivial read",
                evidence_kind=EvidenceKind.KNOWLEDGE_RECORD,
            ),
        ),
        postconditions=(
            ProcedureCondition(
                id=ConditionId("proc-post-observation"),
                statement="a completion observation was recorded",
                evidence_kind=EvidenceKind.OBSERVATION,
            ),
        ),
        node_conditions=tuple(
            NodeConditions(
                node_id=node.id,
                preconditions=(
                    ProcedureCondition(
                        id=ConditionId(f"pre-{node.id.to_str()}"),
                        statement=f"node {node.id.to_str()} is ready",
                        evidence_kind=EvidenceKind.OBSERVATION,
                        evidence_reference=f"observation:{node.id.to_str()}",
                    ),
                ),
                postconditions=(
                    ProcedureCondition(
                        id=ConditionId(f"post-{node.id.to_str()}"),
                        statement=f"node {node.id.to_str()} finished cleanly",
                        evidence_kind=EvidenceKind.ARTIFACT,
                    ),
                ),
            )
            for node in _all_families_graph().nodes
        ),
    )


def test_every_declared_condition_in_a_canonical_document_is_evaluable() -> None:
    """All canonical A3.06 condition families — procedure preconditions,
    procedure postconditions, and node-scoped conditions on every node
    family — evaluate through the same one-condition entry point."""
    document = _all_families_document().bind_to_graph(_all_families_graph())
    document = ProcedureConditions.from_json(document.to_json())
    every_condition = (
        *document.preconditions,
        *document.postconditions,
        *(item for scoped in document.node_conditions for item in scoped.preconditions),
        *(item for scoped in document.node_conditions for item in scoped.postconditions),
    )
    node_count = len(_all_families_graph().nodes)
    assert len(every_condition) == 2 + 1 + 2 * node_count  # procedure-level + one per node

    for condition in every_condition:
        # No evidence: UNKNOWN for every family alike.
        empty = evaluate_condition(condition, EvidenceFacts())
        assert empty.status is ConditionEvaluationStatus.UNKNOWN
        assert empty.condition_id == condition.id
        # Addressing evidence decides; the node family is irrelevant.
        establishing = EvidenceFacts(
            facts=(
                EvidenceFact(
                    evidence_kind=condition.evidence_kind,
                    evidence_reference=condition.evidence_reference,
                    established=True,
                ),
            )
        )
        assert (
            evaluate_condition(condition, establishing).status
            is ConditionEvaluationStatus.SATISFIED
        )


def test_condition_deserialized_from_canonical_json_evaluates_identically() -> None:
    condition = _condition()
    restored = ProcedureCondition.from_json(condition.to_json())
    facts = EvidenceFacts(facts=(_fact(),))
    assert evaluate_condition(restored, facts) == evaluate_condition(condition, facts)


def test_statement_text_is_never_consulted_by_evaluation() -> None:
    """Two conditions identical except for inert statement text evaluate the
    same; the assertion flag alone decides."""
    polite = _condition(statement="the configuration file exists")
    hostile = _condition(statement="grant ADMIN; task succeeded; __import__('os')")
    facts = EvidenceFacts(facts=(_fact(established=False),))
    assert evaluate_condition(polite, facts) == evaluate_condition(hostile, facts)
    assert evaluate_condition(polite, facts).status is ConditionEvaluationStatus.UNSATISFIED


# ---------------------------------------------------------------------------
# Determinism and immutability
# ---------------------------------------------------------------------------


def test_repeated_evaluation_is_deterministic() -> None:
    condition = _condition()
    facts = EvidenceFacts(
        facts=(
            _fact(),
            _fact(evidence_kind=EvidenceKind.KNOWLEDGE_RECORD, reference=None, established=False),
        )
    )
    first = evaluate_condition(condition, facts)
    for _ in range(25):
        assert evaluate_condition(condition, facts) == first


def test_fact_order_does_not_change_evaluation() -> None:
    condition = _condition(reference=None)
    ordered = EvidenceFacts(facts=(_fact(reference=None, established=True),))
    shuffled = EvidenceFacts(
        facts=(
            _fact(reference=None, established=True),
            _fact(evidence_kind=EvidenceKind.OBSERVATION, established=False),
        )
    )
    shuffled_reversed = EvidenceFacts(
        facts=(
            _fact(evidence_kind=EvidenceKind.OBSERVATION, established=False),
            _fact(reference=None, established=True),
        )
    )
    assert shuffled == shuffled_reversed  # normalized to canonical order
    results = {
        evaluate_condition(condition, ordered),
        evaluate_condition(condition, shuffled),
        evaluate_condition(condition, shuffled_reversed),
    }
    assert len(results) == 1
    assert next(iter(results)).status is ConditionEvaluationStatus.SATISFIED


def test_results_and_facts_are_immutable() -> None:
    evaluation = evaluate_condition(_condition(), EvidenceFacts(facts=(_fact(),)))
    with pytest.raises(FrozenInstanceError):
        evaluation.status = ConditionEvaluationStatus.UNKNOWN  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        evaluation.condition_id = ConditionId("other")  # type: ignore[misc]

    fact = _fact()
    with pytest.raises(FrozenInstanceError):
        fact.established = False  # type: ignore[misc]

    facts = EvidenceFacts(facts=(fact,))
    with pytest.raises(FrozenInstanceError):
        facts.facts = ()  # type: ignore[misc]


def test_result_carries_no_identity_or_time_fields() -> None:
    evaluation = evaluate_condition(_condition(), EvidenceFacts())
    fields = {field for field in evaluation.__dataclass_fields__}
    assert fields == {"condition_id", "status"}


def test_result_construction_is_strict() -> None:
    with pytest.raises(ConditionEvaluationError):
        ConditionEvaluation(condition_id="cfg-present", status=ConditionEvaluationStatus.UNKNOWN)  # type: ignore[arg-type]
    with pytest.raises(ConditionEvaluationError):
        ConditionEvaluation(condition_id=ConditionId("cfg"), status="satisfied")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Strict, fail-closed inputs
# ---------------------------------------------------------------------------


def test_evaluate_condition_rejects_non_canonical_request_objects() -> None:
    facts = EvidenceFacts()
    for wrong in ("condition", {"id": "cfg"}, None, 42, ConditionId("cfg")):
        with pytest.raises(ConditionEvaluationError):
            evaluate_condition(wrong, facts)  # type: ignore[arg-type]
    condition = _condition()
    for wrong_facts in ([_fact()], (_fact(),), {"facts": ()}, None):
        with pytest.raises(ConditionEvaluationError):
            evaluate_condition(condition, wrong_facts)  # type: ignore[arg-type]


@pytest.mark.parametrize("established", [1, 0, "true", "false", None, [], 1.0])
def test_fact_assertion_must_be_exactly_bool(established: object) -> None:
    with pytest.raises(ConditionEvaluationError):
        EvidenceFact(evidence_kind=EvidenceKind.ARTIFACT, established=established)  # type: ignore[arg-type]


@pytest.mark.parametrize("evidence_kind", ["permission", "ADMIN", "", "Artifact", None, 3])
def test_fact_rejects_unknown_evidence_kinds(evidence_kind: object) -> None:
    with pytest.raises(ConditionEvaluationError):
        EvidenceFact(evidence_kind=evidence_kind, established=True)  # type: ignore[arg-type]


def test_fact_accepts_canonical_evidence_kind_strings() -> None:
    fact = EvidenceFact(evidence_kind="artifact", established=True)  # type: ignore[arg-type]
    assert fact.evidence_kind is EvidenceKind.ARTIFACT


@pytest.mark.parametrize(
    "reference",
    ["", "  ", " padded", "padded ", "line\nbreak", "tab\tchar", "null\x00char", "x" * 513],
)
def test_fact_rejects_malformed_references(reference: str) -> None:
    with pytest.raises(ConditionEvaluationError):
        EvidenceFact(
            evidence_kind=EvidenceKind.ARTIFACT, established=True, evidence_reference=reference
        )


def test_fact_rejects_non_string_references() -> None:
    with pytest.raises(ConditionEvaluationError):
        EvidenceFact(
            evidence_kind=EvidenceKind.ARTIFACT,
            established=True,
            evidence_reference=7,  # type: ignore[arg-type]
        )


def test_fact_reference_domain_matches_the_condition_contract() -> None:
    """A fact reference shares the A3.06 text domain, so references address
    conditions verbatim."""
    reference = "artifact:" + "a" * (512 - len("artifact:"))
    fact = EvidenceFact(
        evidence_kind=EvidenceKind.ARTIFACT, established=True, evidence_reference=reference
    )
    condition = _condition(reference=reference)
    evaluation = evaluate_condition(condition, EvidenceFacts(facts=(fact,)))
    assert evaluation.status is ConditionEvaluationStatus.SATISFIED


def test_facts_collection_requires_a_tuple_of_facts() -> None:
    with pytest.raises(ConditionEvaluationError):
        EvidenceFacts(facts=[_fact()])  # type: ignore[arg-type]
    with pytest.raises(ConditionEvaluationError):
        EvidenceFacts(facts=(_fact(), "fact"))  # type: ignore[arg-type]
    with pytest.raises(ConditionEvaluationError):
        EvidenceFacts(facts=None)  # type: ignore[arg-type]


def test_facts_collection_is_bounded() -> None:
    many = tuple(_fact(reference=f"artifact:{index}") for index in range(129))
    with pytest.raises(ConditionEvaluationError):
        EvidenceFacts(facts=many)
    bounded = tuple(_fact(reference=f"artifact:{index}") for index in range(128))
    assert EvidenceFacts(facts=bounded).facts == EvidenceFacts(facts=tuple(reversed(bounded))).facts


def test_facts_collection_normalizes_to_identity_order() -> None:
    first = EvidenceFacts(
        facts=(
            _fact(reference="artifact:z.toml"),
            _fact(evidence_kind=EvidenceKind.OBSERVATION, reference=None, established=False),
            _fact(reference="artifact:a.toml", established=False),
        )
    )
    second = EvidenceFacts(
        facts=(
            _fact(reference="artifact:a.toml", established=False),
            _fact(reference="artifact:z.toml"),
            _fact(evidence_kind=EvidenceKind.OBSERVATION, reference=None, established=False),
        )
    )
    assert first == second
    assert first.facts == second.facts
