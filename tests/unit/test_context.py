"""M2.05: bounded context data, canonical identity, provenance, and wire behavior."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest

from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.context import (
    CONTEXT_SCHEMA_VERSION,
    DEFAULT_CONTEXT_LIMITS,
    MAX_CONTEXT_BYTES,
    MAX_CONTEXT_DETAIL_DEPTH,
    MAX_CONTEXT_DETAIL_NODES,
    MAX_CONTEXT_INTEGER_BITS,
    MAX_CONTEXT_ITEM_BYTES,
    MAX_CONTEXT_ITEMS,
    MAX_CONTEXT_METADATA_BYTES,
    MAX_CONTEXT_METADATA_ENTRIES,
    AgentContext,
    ContextBindingError,
    ContextDuplicateError,
    ContextItem,
    ContextItemKind,
    ContextLimitError,
    ContextLimits,
    ContextRecord,
    ContextScopeError,
    ContextValidationError,
    UnsupportedContextSchemaVersionError,
)
from agentx.core.environment_change import (
    EnvironmentFactKey,
    EnvironmentFactKind,
    EnvironmentFactValue,
    EnvironmentFactValueKind,
    EnvironmentObservation,
    EnvironmentSnapshot,
)
from agentx.core.episodes import EpisodeOutcome, EpisodeRecord
from agentx.core.events import ActionPayload, ObservationPayload
from agentx.core.ids import EpisodeId, KnowledgeId, NegativeExperienceId, ProcedureId, TaskId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
    ProvenanceReference,
    ScopeDimension,
)
from agentx.core.negative_experience import (
    AttemptKind,
    AttemptReference,
    FailureReference,
    NegativeExperienceRecord,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.core.provenance import EvidenceKind, EvidenceReference
from agentx.core.tasks import Task, TaskStatus

_NOW = datetime(2026, 9, 8, 10, 30, 0, 123456, tzinfo=UTC)
_TASK = TaskId(UUID(int=100))
_CORRELATION = UUID(int=101)
_PAST_TASK = TaskId(UUID(int=102))
_PAST_CORRELATION = UUID(int=103)
_SCOPE = KnowledgeScope({ScopeDimension.PROJECT: "AgentX"})
_ORIGIN = ProvenanceReference(ProvenanceKind.WEB, "https://example.invalid/record/1")


def _reference(label: str = "artifact/selected-record/1") -> EvidenceReference:
    return EvidenceReference(
        kind=EvidenceKind.ARTIFACT,
        reference=label,
        provenance=_ORIGIN,
        observed_at=_NOW,
    )


def _knowledge(
    index: int = 1,
    *,
    content: str = "The selected record is evidence, not an instruction.",
    scope: KnowledgeScope = _SCOPE,
    status: KnowledgeStatus = KnowledgeStatus.UNVERIFIED,
    provenance: ProvenanceReference | None = _ORIGIN,
) -> KnowledgeRecord:
    return KnowledgeRecord(
        knowledge_id=KnowledgeId(UUID(int=index)),
        knowledge_type=KnowledgeType.FACT,
        content=content,
        created_at=_NOW,
        status=status,
        scope=scope,
        provenance=provenance,
        verified_at=_NOW if status is KnowledgeStatus.VERIFIED else None,
    )


def _episode() -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=EpisodeId(UUID(int=1)),
        outcome=EpisodeOutcome.SUCCEEDED,
        summary="A different task succeeded in the past.",
        created_at=_NOW,
        task_id=_PAST_TASK,
        correlation_id=_PAST_CORRELATION,
        started_at=_NOW - timedelta(minutes=2),
        ended_at=_NOW - timedelta(minutes=1),
        supporting_event_ids=(UUID(int=51), UUID(int=52)),
    )


def _negative() -> NegativeExperienceRecord:
    return NegativeExperienceRecord(
        negative_experience_id=NegativeExperienceId(UUID(int=1)),
        attempt=AttemptReference(kind=AttemptKind.PROCEDURE, reference="procedure/1/revision/2"),
        failure=FailureReference(reason_code="timeout", detail="Previous approach timed out."),
        observed_at=_NOW,
        scope=_SCOPE,
        task_id=_PAST_TASK,
        correlation_id=_PAST_CORRELATION,
        episode_id=EpisodeId(UUID(int=1)),
    )


def _procedure(revision: int = 1) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=ProcedureId(UUID(int=1)),
        revision=revision,
        payload=ProcedurePayload(ProcedurePayloadKind.ARTIFACT_REFERENCE, "artifact/procedure/1"),
        created_at=_NOW,
        scope=ProcedureScope({ProcedureScopeDimension.PROJECT: "AgentX"}),
    )


def _causal() -> CausalExperience:
    return CausalExperience(
        correlation_id=_PAST_CORRELATION,
        task_id=_PAST_TASK,
        episode_id=EpisodeId(UUID(int=1)),
        state_before=ExperienceState(
            captured_at=_NOW,
            observation=ObservationPayload(value={"files": ["one", {"two": False}]}),
        ),
        action=ActionPayload(name="historical.action", data={"arguments": [1, 2]}),
        action_at=_NOW,
        outcome=CausalOutcome.DENIED,
        outcome_at=_NOW,
        outcome_detail="Historical denial; this is not a retry prohibition.",
    )


def _environment() -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        scope=_SCOPE,
        evidence=_reference("observation-file/1"),
        observations=(
            EnvironmentObservation(
                fact=EnvironmentFactKey(
                    kind=EnvironmentFactKind.APPLICATION_VERSION, subject="agentx"
                ),
                value=EnvironmentFactValue(kind=EnvironmentFactValueKind.TEXT, text="1.2.3"),
                observed_at=_NOW - timedelta(hours=1),
                ttl=timedelta(minutes=5),
                provenance=_ORIGIN,
            ),
        ),
    )


def _context(
    *records: ContextRecord,
    scope: KnowledgeScope = _SCOPE,
    limits: ContextLimits = DEFAULT_CONTEXT_LIMITS,
    metadata: Mapping[str, object] | None = None,
) -> AgentContext:
    return AgentContext(
        task_id=_TASK,
        correlation_id=_CORRELATION,
        created_at=_NOW,
        items=tuple(ContextItem(record=record) for record in records),
        scope=scope,
        limits=limits,
        metadata={} if metadata is None else metadata,
    )


def _read(
    raw: Mapping[str, object], *, limits: ContextLimits = DEFAULT_CONTEXT_LIMITS
) -> AgentContext:
    return AgentContext.from_dict(
        raw,
        expected_task_id=_TASK,
        expected_correlation_id=_CORRELATION,
        limits=limits,
    )


def _read_json(text: str) -> AgentContext:
    return AgentContext.from_json(
        text, expected_task_id=_TASK, expected_correlation_id=_CORRELATION
    )


def _size(raw: object) -> int:
    return len(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def test_empty_context_is_explicit_and_task_bound() -> None:
    context = _context(limits=ContextLimits(max_items=0, max_metadata_entries=0))
    assert context.items == ()
    assert context.task_id == _TASK
    assert context.correlation_id == _CORRELATION
    assert context.schema_version == CONTEXT_SCHEMA_VERSION == 1
    assert context.metadata == {}
    assert _read_json(context.to_json()) == context


@pytest.mark.parametrize("status", list(KnowledgeStatus))
def test_single_knowledge_preserves_all_canonical_statuses_and_provenance(
    status: KnowledgeStatus,
) -> None:
    original = _knowledge(status=status)
    context = _context(original)
    item = context.items[0]
    assert item.kind is ContextItemKind.KNOWLEDGE
    assert type(item.record) is KnowledgeRecord
    assert item.record is not original
    assert item.record.status is status
    assert item.record.verified_at == original.verified_at
    assert item.record.provenance == _ORIGIN
    assert item.record.scope == _SCOPE
    assert item.to_dict()["record"] == original.to_dict()
    assert _read_json(context.to_json()).items[0].record == original


@pytest.mark.parametrize("outcome", list(EpisodeOutcome))
def test_episode_preserves_historical_task_correlation_and_event_provenance(
    outcome: EpisodeOutcome,
) -> None:
    original = replace(_episode(), outcome=outcome)
    context = _context(original)
    record = cast(EpisodeRecord, context.items[0].record)
    assert context.items[0].kind is ContextItemKind.EPISODE
    assert record.task_id == _PAST_TASK != context.task_id
    assert record.correlation_id == _PAST_CORRELATION != context.correlation_id
    assert record.supporting_event_ids == original.supporting_event_ids
    assert record.outcome is outcome
    assert context.items[0].scope is None
    assert _read(context.to_dict()) == context


def test_negative_experience_keeps_failed_attempt_and_all_references() -> None:
    original = _negative()
    context = _context(original)
    item = context.items[0]
    assert item.kind is ContextItemKind.NEGATIVE_EXPERIENCE
    assert item.record == original
    assert item.scope == _SCOPE
    assert item.to_dict()["record"] == original.to_dict()
    assert _read_json(context.to_json()) == context


@pytest.mark.parametrize("status", list(ProcedureStatus))
def test_procedure_reference_pins_revision_scope_status_and_opaque_payload(
    status: ProcedureStatus,
) -> None:
    original = replace(_procedure(7), status=status, updated_at=_NOW)
    context = _context(original)
    item = context.items[0]
    assert item.kind is ContextItemKind.PROCEDURE_REFERENCE
    assert type(item.record) is ProcedureRecord
    assert item.record.procedure_id == original.procedure_id
    assert item.record.revision == 7
    assert item.record.status is status
    assert item.record.payload == original.payload
    assert type(item.scope) is ProcedureScope
    assert _read_json(context.to_json()) == context


def test_causal_chain_is_kept_whole_without_inventing_an_id() -> None:
    original = _causal()
    context = _context(original)
    item = context.items[0]
    assert item.kind is ContextItemKind.CAUSAL_EXPERIENCE
    assert item.identity is None
    assert item.scope is None
    assert item.record == original
    assert item.to_dict()["record"] == original.to_dict()
    assert _read(context.to_dict()) == context


def test_environment_fact_preserves_scope_provenance_and_ttl_without_assessing_freshness() -> None:
    original = _environment()
    context = _context(original)
    item = context.items[0]
    assert item.kind is ContextItemKind.ENVIRONMENT_FACT
    assert item.record == original
    assert item.scope == original.scope
    assert item.identity is None  # an evidence-file locator is not an observation ID
    assert original.observations[0].expires_at < context.created_at
    assert _read_json(context.to_json()) == context  # no freshness/relevance filtering


@pytest.mark.parametrize("count", [0, 2])
def test_environment_item_cannot_hide_a_snapshot_of_many_facts(count: int) -> None:
    original = _environment()
    with pytest.raises(ContextValidationError, match="exactly one observation"):
        ContextItem(record=replace(original, observations=original.observations * count))


def test_user_context_uses_inert_canonical_observation_with_honest_missing_origin() -> None:
    context = _context(ObservationPayload(value={"note": "User-provided evidence", "count": 2}))
    item = context.items[0]
    assert item.kind is ContextItemKind.USER_SUPPLIED_CONTEXT
    assert type(item.record) is ObservationPayload
    assert item.record_reference is None
    assert item.identity is None
    assert item.scope is None
    assert item.to_dict()["record_reference"] is None
    assert _read_json(context.to_json()) == context


def test_mixed_context_preserves_exact_caller_order_without_ranking() -> None:
    records: tuple[ContextRecord, ...] = (
        _procedure(),
        _negative(),
        _knowledge(),
        _environment(),
        _causal(),
        _episode(),
        ObservationPayload(value="a user note"),
    )
    context = _context(*records)
    assert tuple(item.record for item in context.items) == records
    assert {item.kind for item in context.items} == set(ContextItemKind)
    assert _context(*records).to_json() == context.to_json()
    assert _read_json(context.to_json()).to_json() == context.to_json()
    assert _context(*reversed(records)).to_json() != context.to_json()


@pytest.mark.parametrize("changed", [False, True])
def test_duplicate_canonical_identity_is_refused_not_counted_merged_or_promoted(
    changed: bool,
) -> None:
    first = _knowledge()
    second = (
        replace(first, content="different evidence", status=KnowledgeStatus.VERIFIED)
        if changed
        else first
    )
    with pytest.raises(ContextDuplicateError, match="identity"):
        _context(first, second)
    assert first.status is KnowledgeStatus.UNVERIFIED


@pytest.mark.parametrize("record", [_episode(), _negative(), _procedure()])
def test_every_other_canonical_record_identity_is_duplicate_checked(record: ContextRecord) -> None:
    with pytest.raises(ContextDuplicateError):
        _context(record, record)


def test_same_text_different_id_and_different_id_domains_are_not_duplicates() -> None:
    context = _context(
        _knowledge(1, content="same"),
        _knowledge(2, content="same"),
        _episode(),
        _negative(),
        _procedure(),
    )
    assert len(context.items) == 5
    assert len({item.identity for item in context.items}) == 5


def test_different_procedure_revisions_are_distinct_records() -> None:
    context = _context(_procedure(1), _procedure(2))
    assert len(context.items) == 2
    assert context.items[0].identity != context.items[1].identity


@pytest.mark.parametrize("record", [_causal(), _environment(), ObservationPayload(value="same")])
def test_unknown_identity_does_not_fabricate_content_based_deduplication(
    record: ContextRecord,
) -> None:
    context = _context(record, record)
    assert len(context.items) == 2
    assert all(item.identity is None for item in context.items)
    assert _read(context.to_dict()) == context


def test_correlation_is_not_unique_causal_record_identity() -> None:
    first = _causal()
    second = replace(first, action=ActionPayload(name="another.attempt"))
    assert first.correlation_id == second.correlation_id
    assert len(_context(first, second).items) == 2


def test_explicit_record_reference_preserved_and_used_only_when_canonical_id_is_absent() -> None:
    record = _causal()
    reference = _reference()
    item = ContextItem(record=record, record_reference=reference)
    assert item.record_reference == reference
    assert item.record_reference is not reference
    assert ContextItem.from_dict(item.to_dict()) == item
    with pytest.raises(ContextDuplicateError):
        AgentContext(
            task_id=_TASK, correlation_id=_CORRELATION, created_at=_NOW, items=(item, item)
        )
    another = ContextItem(record=record, record_reference=_reference("artifact/selected-record/2"))
    context = AgentContext(
        task_id=_TASK, correlation_id=_CORRELATION, created_at=_NOW, items=(item, another)
    )
    assert len(context.items) == 2


def test_reference_identity_ignores_acquisition_time_but_preserves_source_namespace() -> None:
    first = ContextItem(record=_causal(), record_reference=_reference())
    later = ContextItem(
        record=_causal(),
        record_reference=replace(_reference(), observed_at=_NOW + timedelta(days=1)),
    )
    different_origin = ContextItem(
        record=_causal(),
        record_reference=replace(
            _reference(), provenance=ProvenanceReference(ProvenanceKind.USER, "message/2")
        ),
    )
    assert first.identity == later.identity
    assert first.identity != different_origin.identity
    assert first.record_reference != later.record_reference


def test_alternate_retrieval_labels_cannot_disguise_duplicate_canonical_record_ids() -> None:
    items = tuple(
        ContextItem(record=_knowledge(), record_reference=_reference(label)) for label in ("a", "b")
    )
    with pytest.raises(ContextDuplicateError):
        AgentContext(task_id=_TASK, correlation_id=_CORRELATION, created_at=_NOW, items=items)


def test_missing_canonical_provenance_is_not_filled_from_a_retrieval_label() -> None:
    item = ContextItem(record=_knowledge(provenance=None), record_reference=_reference())
    record = cast(KnowledgeRecord, item.record)
    assert record.provenance is None
    assert item.record_reference is not None
    assert item.to_dict()["record"] == record.to_dict()
    assert ContextItem.from_dict(item.to_dict()).record == record


def test_compatible_partial_and_global_scopes_stay_separate_not_inferred() -> None:
    global_record = _knowledge(1, scope=KnowledgeScope())
    os_record = _knowledge(2, scope=KnowledgeScope({ScopeDimension.OPERATING_SYSTEM: "windows"}))
    context = _context(global_record, os_record, _negative(), _procedure(), _environment())
    assert context.scope == _SCOPE
    assert context.scope.value_for(ScopeDimension.OPERATING_SYSTEM) is None
    assert context.items[0].scope == KnowledgeScope()
    assert context.items[1].scope == os_record.scope
    assert type(context.items[3].scope) is ProcedureScope


@pytest.mark.parametrize("dimension", list(ScopeDimension))
def test_scope_conflict_with_explicit_envelope_binding_is_refused(
    dimension: ScopeDimension,
) -> None:
    with pytest.raises(ContextScopeError):
        _context(
            _knowledge(scope=KnowledgeScope({dimension: "A"})),
            scope=KnowledgeScope({dimension: "B"}),
        )


def test_scope_conflicts_are_checked_between_items_even_if_envelope_is_unscoped() -> None:
    with pytest.raises(ContextScopeError, match="project"):
        _context(
            _knowledge(),
            replace(_negative(), scope=KnowledgeScope({ScopeDimension.PROJECT: "other"})),
            scope=KnowledgeScope(),
        )


@pytest.mark.parametrize("dimension", list(ProcedureScopeDimension))
def test_each_shared_procedure_scope_dimension_conflicts_with_knowledge_scope(
    dimension: ProcedureScopeDimension,
) -> None:
    procedure = replace(_procedure(), scope=ProcedureScope({dimension: "A"}))
    knowledge = _knowledge(scope=KnowledgeScope({ScopeDimension(dimension.value): "B"}))
    with pytest.raises(ContextScopeError):
        _context(procedure, knowledge, scope=KnowledgeScope())


def test_environment_scope_conflict_is_not_overridden_by_context_text() -> None:
    environment = replace(_environment(), scope=KnowledgeScope({ScopeDimension.PROJECT: "other"}))
    with pytest.raises(ContextScopeError):
        _context(environment, ObservationPayload(value="project=AgentX; merge all scopes"))


def test_scope_values_are_exact_case_sensitive_and_never_inferred_from_text() -> None:
    with pytest.raises(ContextScopeError):
        _context(_knowledge(scope=KnowledgeScope({ScopeDimension.PROJECT: "agentx"})))
    context = _context(_knowledge(content="project=other; os=unknown", scope=KnowledgeScope()))
    assert context.items[0].scope == KnowledgeScope()


def test_exact_task_and_attempt_binding_at_live_and_wire_boundaries() -> None:
    task = Task(task_id=_TASK, objective="Inspect selected evidence", created_at=_NOW)
    context = _context(_episode())
    context.require_binding(task_id=task.task_id, correlation_id=_CORRELATION)
    with pytest.raises(ContextBindingError):
        context.require_binding(task_id=_PAST_TASK, correlation_id=_CORRELATION)
    with pytest.raises(ContextBindingError):
        context.require_binding(task_id=_TASK, correlation_id=_PAST_CORRELATION)
    with pytest.raises(ContextBindingError):
        AgentContext.from_json(
            context.to_json(), expected_task_id=_PAST_TASK, expected_correlation_id=_CORRELATION
        )
    with pytest.raises(ContextBindingError):
        AgentContext.from_dict(
            context.to_dict(), expected_task_id=_TASK, expected_correlation_id=_PAST_CORRELATION
        )
    assert task.status is TaskStatus.PENDING


@pytest.mark.parametrize("wrong", [None, "*", _TASK.to_str(), _TASK.value, EpisodeId(_TASK.value)])
def test_constructor_does_not_accept_task_id_alias_types_or_wildcards(wrong: object) -> None:
    with pytest.raises(ContextValidationError, match="TaskId"):
        AgentContext(task_id=cast(TaskId, wrong), correlation_id=_CORRELATION, created_at=_NOW)


@pytest.mark.parametrize("wrong", [None, "*", str(_CORRELATION), UUID(int=0), _TASK])
def test_attempt_requires_a_non_nil_uuid_not_a_task_id(wrong: object) -> None:
    with pytest.raises(ContextValidationError, match="UUID"):
        AgentContext(task_id=_TASK, correlation_id=cast(UUID, wrong), created_at=_NOW)


def test_rebinding_requires_explicit_new_envelope_and_never_rewrites_history() -> None:
    original = _context(_episode())
    rebound = AgentContext(
        task_id=_PAST_TASK, correlation_id=_PAST_CORRELATION, created_at=_NOW, items=original.items
    )
    rebound.require_binding(task_id=_PAST_TASK, correlation_id=_PAST_CORRELATION)
    with pytest.raises(ContextBindingError):
        original.require_binding(task_id=_PAST_TASK, correlation_id=_PAST_CORRELATION)
    assert rebound.items == original.items
    assert original.task_id == _TASK


def test_timestamps_are_explicit_utc_normalized_and_not_a_freshness_filter() -> None:
    offset = timezone(timedelta(hours=5, minutes=30))
    context = AgentContext(
        task_id=_TASK, correlation_id=_CORRELATION, created_at=_NOW.astimezone(offset)
    )
    assert context.created_at == _NOW
    assert context.created_at.tzinfo is UTC
    assert context.to_dict()["created_at"] == "2026-09-08T10:30:00.123456Z"
    raw = context.to_dict()
    raw["created_at"] = _NOW.astimezone(offset).isoformat()
    assert _read(raw) == context


@pytest.mark.parametrize("wrong", [None, "now", datetime(2026, 9, 8)])
def test_naive_or_non_datetime_creation_time_is_refused(wrong: object) -> None:
    with pytest.raises(ContextValidationError):
        AgentContext(task_id=_TASK, correlation_id=_CORRELATION, created_at=cast(datetime, wrong))


def test_item_count_has_an_inclusive_hard_ceiling_and_no_truncation() -> None:
    records = tuple(_knowledge(index) for index in range(1, MAX_CONTEXT_ITEMS + 2))
    assert len(_context(*records[:-1]).items) == MAX_CONTEXT_ITEMS
    with pytest.raises(ContextLimitError, match="item count"):
        _context(*records)
    with pytest.raises(ContextLimitError, match="item count"):
        _context(_knowledge(), limits=ContextLimits(max_items=0))


@pytest.mark.parametrize("field_name", list(DEFAULT_CONTEXT_LIMITS.to_dict()))
@pytest.mark.parametrize("bad", [None, True, -1, 10**9, "unlimited", 1.5])
def test_limits_cannot_be_unbounded_raised_or_loosely_typed(field_name: str, bad: object) -> None:
    raw: dict[str, object] = dict(DEFAULT_CONTEXT_LIMITS.to_dict())
    raw[field_name] = bad
    with pytest.raises(ContextValidationError):
        ContextLimits.from_dict(raw)


def test_byte_limits_must_be_positive_and_limit_wire_has_exact_fields() -> None:
    for name in ("max_total_bytes", "max_item_bytes", "max_metadata_bytes"):
        raw: dict[str, object] = dict(DEFAULT_CONTEXT_LIMITS.to_dict())
        raw[name] = 0
        with pytest.raises(ContextLimitError):
            ContextLimits.from_dict(raw)
    raw = dict(DEFAULT_CONTEXT_LIMITS.to_dict())
    raw["budget"] = 123
    with pytest.raises(ContextValidationError):
        ContextLimits.from_dict(raw)


def test_per_item_bytes_count_record_provenance_reference_and_json_overhead() -> None:
    overhead = _size(ContextItem(record=_knowledge(content="x")).to_dict()) - 1
    at_limit = ContextItem(record=_knowledge(content="x" * (MAX_CONTEXT_ITEM_BYTES - overhead)))
    assert _size(at_limit.to_dict()) == MAX_CONTEXT_ITEM_BYTES
    with pytest.raises(ContextLimitError):
        ContextItem(record=_knowledge(content="x" * (MAX_CONTEXT_ITEM_BYTES - overhead + 1)))
    with pytest.raises(ContextLimitError):
        ContextItem(record=cast(KnowledgeRecord, at_limit.record), record_reference=_reference())


def test_item_limit_can_be_tightened_with_exact_inclusive_byte_accounting() -> None:
    record = _knowledge()
    item_size = _size(ContextItem(record=record).to_dict())
    assert len(_context(record, limits=ContextLimits(max_item_bytes=item_size)).items) == 1
    with pytest.raises(ContextLimitError):
        _context(record, limits=ContextLimits(max_item_bytes=item_size - 1))


def test_serialized_bytes_are_utf8_bytes_not_characters_and_include_escapes() -> None:
    original = _knowledge(content='é\n"evidence"')
    item = ContextItem(record=original)
    size = _size(item.to_dict())
    assert _context(original, limits=ContextLimits(max_item_bytes=size)).items[0] == item
    with pytest.raises(ContextLimitError):
        _context(original, limits=ContextLimits(max_item_bytes=size - 1))
    with pytest.raises(ContextLimitError):
        ContextItem(record=_knowledge(content="é" * (MAX_CONTEXT_ITEM_BYTES // 2)))


def test_total_byte_limit_includes_every_field_and_fails_instead_of_dropping_items() -> None:
    limits = ContextLimits(max_total_bytes=1_000)
    base = _context(_knowledge(content="x"), limits=limits)
    content = "x" * (1_001 - len(base.to_json().encode("utf-8")))
    boundary = _context(_knowledge(content=content), limits=limits)
    assert len(boundary.to_json().encode("utf-8")) == 1_000
    assert _read(boundary.to_dict(), limits=limits) == boundary
    with pytest.raises(ContextLimitError):
        _context(_knowledge(content=content + "x"), limits=limits)
    with pytest.raises(ContextLimitError):
        _context(*(_knowledge(index, content="x" * 9_000) for index in range(1, 33)))


def test_input_json_bytes_are_bounded_before_parsing_including_whitespace() -> None:
    with pytest.raises(ContextLimitError):
        _read_json(" " * (MAX_CONTEXT_BYTES + 1))
    with pytest.raises(ContextLimitError):
        _read_json("é" * MAX_CONTEXT_BYTES)


def test_metadata_is_bounded_by_recursive_key_count_and_serialized_bytes() -> None:
    metadata = {"nested": {str(index): index for index in range(MAX_CONTEXT_METADATA_ENTRIES - 1)}}
    assert _context(metadata=metadata).metadata["nested"] == metadata["nested"]
    metadata["nested"]["extra"] = 1
    with pytest.raises(ContextLimitError, match="metadata entry"):
        _context(metadata=metadata)
    overhead = _size({"value": "x"}) - 1
    at_limit = {"value": "x" * (MAX_CONTEXT_METADATA_BYTES - overhead)}
    assert _context(metadata=at_limit).metadata == at_limit
    with pytest.raises(ContextLimitError):
        _context(metadata={"value": at_limit["value"] + "x"})


def test_depth_nodes_cycles_and_integer_width_are_explicitly_bounded() -> None:
    nested: object = "leaf"
    for _ in range(MAX_CONTEXT_DETAIL_DEPTH + 1):
        nested = [nested]
    with pytest.raises(ContextLimitError, match="depth"):
        ContextItem(record=ObservationPayload(value=nested))
    with pytest.raises(ContextLimitError, match="node"):
        ContextItem(record=ObservationPayload(value=[None] * MAX_CONTEXT_DETAIL_NODES))
    cycle: dict[str, object] = {}
    cycle["again"] = cycle
    with pytest.raises(ContextLimitError, match="cycles"):
        _context(metadata=cycle)
    assert _context(ObservationPayload(value=1 << (MAX_CONTEXT_INTEGER_BITS - 1))).items
    with pytest.raises(ContextLimitError, match="integer"):
        ContextItem(record=ObservationPayload(value=1 << MAX_CONTEXT_INTEGER_BITS))


def test_receiver_limits_are_independent_from_serialized_limit_claims() -> None:
    context = _context(_knowledge())
    with pytest.raises(ContextLimitError, match="receiving"):
        _read(context.to_dict(), limits=ContextLimits(max_items=1))
    tight = _context(_knowledge(), limits=ContextLimits(max_items=1))
    assert _read(tight.to_dict(), limits=ContextLimits(max_items=1)) == tight
    raw = tight.to_dict()
    cast(dict[str, object], raw["limits"])["max_items"] = MAX_CONTEXT_ITEMS + 1
    with pytest.raises(ContextLimitError):
        _read(raw)


def test_envelope_items_limits_and_canonical_nested_records_are_immutable() -> None:
    context = _context(_knowledge(), _causal())
    with pytest.raises(FrozenInstanceError):
        context.task_id = _PAST_TASK  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        context.items = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        context.items[0].record_reference = _reference()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        context.limits.max_items = 500  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        cast(KnowledgeRecord, context.items[0].record).status = KnowledgeStatus.VERIFIED  # type: ignore[misc]
    with pytest.raises(TypeError):
        cast(dict[ScopeDimension, str], context.scope.dimensions)[ScopeDimension.PROJECT] = "other"
    assert not hasattr(context, "__dict__")


def test_metadata_and_observation_data_are_defensively_copied_and_deep_frozen() -> None:
    inner: dict[str, object] = {"sequence": [1, {"leaf": "unchanged"}]}
    original = ObservationPayload(value={"nested": inner})
    context = _context(original, _causal(), metadata={"nested": inner})
    cast(list[object], inner["sequence"]).append("later")
    inner["extra"] = "later"
    nested = cast(Mapping[str, object], context.metadata["nested"])
    assert nested == {"sequence": (1, {"leaf": "unchanged"})}
    with pytest.raises(TypeError):
        cast(dict[str, object], nested)["extra"] = "mutation"
    sequence = cast(tuple[object, ...], nested["sequence"])
    with pytest.raises(TypeError):
        cast(dict[str, object], sequence[1])["leaf"] = "mutation"
    payload = cast(ObservationPayload, context.items[0].record)
    assert payload is not original
    with pytest.raises(TypeError):
        cast(dict[str, object], payload.value)["new"] = "mutation"
    causal = cast(CausalExperience, context.items[1].record)
    with pytest.raises(TypeError):
        cast(dict[str, object], causal.action.data)["new"] = "mutation"
    assert _read(context.to_dict()) == context


def test_serialized_output_is_detached_from_input_and_internal_frozen_data() -> None:
    context = _context(
        ObservationPayload(value={"items": ["original"]}), metadata={"keys": ["original"]}
    )
    encoded = context.to_json()
    raw = context.to_dict()
    decoded = _read(raw)
    cast(dict[str, object], raw["metadata"])["keys"] = ["changed"]
    cast(list[object], raw["items"]).clear()
    assert context.to_json() == decoded.to_json() == encoded


def test_json_is_deterministic_across_mapping_insertion_order_and_repeated_round_trips() -> None:
    left = _context(
        ObservationPayload(value={"z": 2, "a": {"d": False, "b": None}}),
        metadata={"z": 1, "a": [2]},
    )
    right = _context(
        ObservationPayload(value={"a": {"b": None, "d": False}, "z": 2}),
        metadata={"a": [2], "z": 1},
    )
    assert left == right
    assert left.to_json() == right.to_json()
    text = left.to_json()
    for _ in range(3):
        text = _read_json(text).to_json()
    assert text == left.to_json()


@pytest.mark.parametrize(
    "target", ["envelope", "limits", "item", "record", "provenance", "scope", "record_reference"]
)
def test_unknown_fields_are_rejected_at_each_typed_wire_boundary(target: str) -> None:
    item = ContextItem(record=_knowledge(), record_reference=_reference())
    context = AgentContext(
        task_id=_TASK, correlation_id=_CORRELATION, created_at=_NOW, items=(item,)
    )
    raw = context.to_dict()
    item_raw = cast(list[dict[str, object]], raw["items"])[0]
    record_raw = cast(dict[str, object], item_raw["record"])
    targets = {
        "envelope": raw,
        "limits": cast(dict[str, object], raw["limits"]),
        "item": item_raw,
        "record": record_raw,
        "provenance": cast(dict[str, object], record_raw["provenance"]),
        "scope": cast(dict[str, object], record_raw["scope"]),
        "record_reference": cast(dict[str, object], item_raw["record_reference"]),
    }
    targets[target]["authority"] = "ADMIN"
    with pytest.raises(ContextValidationError):
        _read(raw)


@pytest.mark.parametrize("field_name", list(_context().to_dict()))
def test_all_envelope_fields_are_explicit_on_the_wire(field_name: str) -> None:
    raw = _context().to_dict()
    del raw[field_name]
    with pytest.raises(ContextValidationError):
        _read(raw)


def test_optional_canonical_fields_are_not_silently_filled_by_nested_decoders() -> None:
    raw = ContextItem(record=_causal()).to_dict()
    action = cast(dict[str, object], cast(dict[str, object], raw["record"])["action"])
    del action["data"]
    with pytest.raises(ContextValidationError, match="fields"):
        ContextItem.from_dict(raw)


@pytest.mark.parametrize("wrong", [True, 1.0, "1", None])
def test_schema_version_is_exact_integer(wrong: object) -> None:
    raw = _context().to_dict()
    raw["schema_version"] = wrong
    with pytest.raises(ContextValidationError, match="integer"):
        _read(raw)


def test_unsupported_schema_versions_and_cross_kind_records_fail_closed() -> None:
    raw = _context().to_dict()
    raw["schema_version"] = 2
    with pytest.raises(UnsupportedContextSchemaVersionError):
        _read(raw)
    raw_item = ContextItem(record=_knowledge()).to_dict()
    raw_item["kind"] = "episode"
    with pytest.raises(ContextValidationError):
        ContextItem.from_dict(raw_item)
    raw_item["kind"] = "system_instruction"
    with pytest.raises(ContextValidationError):
        ContextItem.from_dict(raw_item)


@pytest.mark.parametrize(
    "text", ["", "[]", "null", "1", "{", '{"items": [}', '{"x": NaN}', '{"x": Infinity}']
)
def test_malformed_or_nonobject_json_is_explicitly_refused(text: str) -> None:
    with pytest.raises(ContextValidationError):
        _read_json(text)


def test_duplicate_json_member_names_and_escaped_aliases_cannot_drop_evidence() -> None:
    text = _context().to_json()
    for key in ('"items"', '"\\u0069tems"'):
        with pytest.raises(ContextValidationError, match="duplicate JSON"):
            _read_json(text[:-1] + "," + key + ":[]}")
    with pytest.raises(ContextValidationError, match="duplicate JSON"):
        _read_json(text.replace('"metadata":{}', '"metadata":{"nested":{"key":1,"key":2}}'))
    # Duplicate-looking syntax inside literal data is not interpreted.
    context = _context(ObservationPayload(value='{"key":1,"key":2} \\" [ ] { }'))
    assert _read_json(context.to_json()) == context


def test_overdeep_json_is_rejected_before_stdlib_recursive_decoding() -> None:
    with pytest.raises(ContextLimitError, match="nesting"):
        _read_json("[" * 1_500 + "0" + "]" * 1_500)


def test_wire_duplicate_record_identity_and_scope_conflict_cannot_bypass_constructors() -> None:
    raw = _context(_knowledge()).to_dict()
    items = cast(list[object], raw["items"])
    items.append(items[0])
    with pytest.raises(ContextDuplicateError):
        _read(raw)
    raw = _context(_knowledge()).to_dict()
    raw["scope"] = {"project": "other"}
    with pytest.raises(ContextScopeError):
        _read(raw)


def test_canonical_uuid_values_round_trip_exactly_without_new_ids() -> None:
    context = _context(_knowledge(), _episode(), _negative(), _procedure(), _causal())
    decoded = _read(context.to_dict())
    assert decoded.task_id.value == _TASK.value
    assert decoded.correlation_id == _CORRELATION
    assert [item.identity for item in decoded.items] == [item.identity for item in context.items]
    assert decoded.to_dict() == context.to_dict()


def test_no_global_trust_or_current_success_field_is_introduced() -> None:
    context = _context(
        _knowledge(status=KnowledgeStatus.VERIFIED),
        _episode(),
        replace(_procedure(), status=ProcedureStatus.ACTIVE),
    )
    forbidden = {
        "trusted",
        "verified",
        "permission",
        "authority",
        "risk",
        "budget",
        "succeeded",
        "status",
    }
    assert forbidden.isdisjoint(context.to_dict())
    assert all(forbidden.isdisjoint(item.to_dict()) for item in context.items)
    assert not any(hasattr(context, name) for name in forbidden)


def test_complete_item_depth_boundary_round_trips_and_the_next_level_is_refused() -> None:
    value: object = "leaf"
    # item -> record -> value adds two levels to the JSON payload's nesting.
    for _ in range(MAX_CONTEXT_DETAIL_DEPTH - 2):
        value = [value]
    context = _context(ObservationPayload(value=value))
    assert _read_json(context.to_json()) == context
    with pytest.raises(ContextLimitError, match="depth"):
        ContextItem(record=ObservationPayload(value=[value]))


def test_complete_item_node_boundary_includes_wrapper_fields_and_round_trips() -> None:
    # Wire nodes: item object, kind, record object, value array, null reference,
    # plus each empty array. This is below the independent byte ceiling.
    payload = ObservationPayload(value=[[] for _ in range(MAX_CONTEXT_DETAIL_NODES - 5)])
    context = _context(payload)
    assert _read_json(context.to_json()) == context
    with pytest.raises(ContextLimitError, match="node"):
        ContextItem(
            record=ObservationPayload(value=[[] for _ in range(MAX_CONTEXT_DETAIL_NODES - 4)])
        )


@pytest.mark.parametrize("field_name", ["task_id", "correlation_id"])
@pytest.mark.parametrize("wrong", [None, True, "*", "not-an-id", str(UUID(int=0))])
def test_wire_task_and_correlation_id_validation_has_no_wildcard(
    field_name: str, wrong: object
) -> None:
    raw = _context().to_dict()
    raw[field_name] = wrong
    with pytest.raises(ContextValidationError):
        _read(raw)


def test_oversized_plain_object_is_refused_before_unbounded_field_scanning() -> None:
    raw: dict[str, object] = {str(index): None for index in range(MAX_CONTEXT_DETAIL_NODES + 1)}
    with pytest.raises(ContextLimitError, match="object member"):
        _read(raw)
    with pytest.raises(ContextLimitError, match="object member"):
        ContextLimits.from_dict(raw)
