"""Tests for the C2.05 semantic-memory read/write service.

Semantic memory is the durable declarative knowledge class. These tests cover
the service boundary only: ingestion, exact retrieval, deterministic
enumeration, verbatim preservation of scope/provenance/status, durability
through the canonical KnowledgeStore, and explicit duplicate behaviour.

Lifecycle policy (C2.08) and semantic/vector retrieval (C2.09) are deliberately
out of scope and are asserted to be absent, not implemented.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from agentx.core.ids import EpisodeId, KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
    ProvenanceReference,
    ScopeDimension,
)
from agentx.core.provenance import (
    EvidenceKind,
    EvidenceReference,
    KnowledgeEvidence,
    ProvenanceRecord,
)
from agentx.hive import semantic_memory as semantic_memory_module
from agentx.hive.semantic_memory import (
    SEMANTIC_KNOWLEDGE_TYPES,
    DuplicateSemanticKnowledgeError,
    IngestionStatusError,
    KnowledgeStorePort,
    NonSemanticKnowledgeError,
    SemanticMemory,
    SemanticMemoryError,
    SemanticMemoryQuery,
    SemanticMemoryQueryError,
)
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 3, 1, 9, 0, 0, tzinfo=UTC)
_T2 = datetime(2026, 6, 15, 8, 30, 15, 500000, tzinfo=UTC)

_WINDOWS_SCOPE = KnowledgeScope(
    dimensions={
        ScopeDimension.OPERATING_SYSTEM: "windows",
        ScopeDimension.APPLICATION: "excel",
    }
)
_WEB_PROVENANCE = ProvenanceReference(
    kind=ProvenanceKind.WEB, reference="https://example.invalid/claim"
)


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / "agentx.sqlite3"


def _store(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(SQLiteDatabase(_database_path(tmp_path)))


def _memory(tmp_path: Path) -> SemanticMemory:
    return SemanticMemory(_store(tmp_path))


def _new(
    content: str = "the invoice template lives in the finance share",
    *,
    knowledge_type: KnowledgeType = KnowledgeType.FACT,
    created_at: datetime = _T0,
    scope: KnowledgeScope | None = None,
    provenance: ProvenanceReference | None = None,
) -> KnowledgeRecord:
    """Build a record in its canonical birth state (UNVERIFIED)."""
    return KnowledgeRecord.create(
        knowledge_type=knowledge_type,
        content=content,
        scope=scope,
        provenance=provenance,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Composition over the canonical store
# ---------------------------------------------------------------------------


def test_knowledge_store_satisfies_the_narrow_port(tmp_path: Path) -> None:
    """Composition needs no new storage: the canonical store IS the port."""
    store: KnowledgeStorePort = _store(tmp_path)

    assert isinstance(store, KnowledgeStorePort)
    assert isinstance(SemanticMemory(store).store, KnowledgeStore)


def test_port_excludes_status_mutation_and_deletion() -> None:
    """The service is structurally unable to transition or destroy knowledge."""
    surface = {name for name in vars(KnowledgeStorePort) if not name.startswith("_")}

    assert surface == {"insert", "get", "list_records"}
    assert not hasattr(SemanticMemory, "update_status")
    for forbidden in ("promote", "verify", "supersede", "resolve_contradiction", "delete"):
        assert not hasattr(SemanticMemory, forbidden)


def test_service_rejects_an_object_that_is_not_a_knowledge_store() -> None:
    with pytest.raises(TypeError):
        SemanticMemory(cast(KnowledgeStorePort, object()))


def test_service_is_immutable(tmp_path: Path) -> None:
    memory = _memory(tmp_path)

    with pytest.raises(FrozenInstanceError):
        memory.store = _store(tmp_path)  # type: ignore[misc]


def test_service_holds_no_second_copy_of_knowledge(tmp_path: Path) -> None:
    """Reads always go to the canonical store; the service caches nothing."""
    store = _store(tmp_path)
    memory = SemanticMemory(store)
    record = _new()

    assert memory.recall(record.knowledge_id) is None

    store.insert(record)

    assert memory.recall(record.knowledge_id) == record


# ---------------------------------------------------------------------------
# Remember canonical knowledge
# ---------------------------------------------------------------------------


def test_remember_stores_canonical_knowledge_verbatim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    memory = SemanticMemory(store)
    record = _new(scope=_WINDOWS_SCOPE, provenance=_WEB_PROVENANCE)

    returned = memory.remember(record)

    assert returned == record
    assert store.get(record.knowledge_id) == record
    assert store.list_records() == (record,)


@pytest.mark.parametrize("knowledge_type", sorted(SEMANTIC_KNOWLEDGE_TYPES))
def test_every_declarative_knowledge_type_is_semantic(
    tmp_path: Path, knowledge_type: KnowledgeType
) -> None:
    memory = _memory(tmp_path)
    record = _new(knowledge_type=knowledge_type)

    assert memory.remember(record).knowledge_type is knowledge_type


def test_semantic_types_are_an_explicit_allow_list() -> None:
    """Types are enumerated, so a future memory class cannot leak in silently."""
    declarative = frozenset(
        {KnowledgeType.FACT, KnowledgeType.OBSERVATION, KnowledgeType.PREFERENCE}
    )

    assert declarative == SEMANTIC_KNOWLEDGE_TYPES
    assert isinstance(SEMANTIC_KNOWLEDGE_TYPES, frozenset)


def test_remember_rejects_a_non_semantic_knowledge_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate a knowledge type owned by another memory class."""
    monkeypatch.setattr(
        semantic_memory_module,
        "SEMANTIC_KNOWLEDGE_TYPES",
        frozenset({KnowledgeType.FACT}),
    )
    store = _store(tmp_path)
    memory = SemanticMemory(store)
    record = _new(knowledge_type=KnowledgeType.OBSERVATION)

    with pytest.raises(NonSemanticKnowledgeError):
        memory.remember(record)

    assert store.list_records() == ()


def test_remember_rejects_a_non_record(tmp_path: Path) -> None:
    memory = _memory(tmp_path)

    with pytest.raises(TypeError):
        memory.remember(cast(KnowledgeRecord, {"content": "not a record"}))


# ---------------------------------------------------------------------------
# Ingestion status: no caller-asserted trust, no automatic promotion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [status for status in KnowledgeStatus if status is not KnowledgeStatus.UNVERIFIED],
)
def test_remember_refuses_pre_labelled_trust(tmp_path: Path, status: KnowledgeStatus) -> None:
    """Arbitrary text cannot arrive already VERIFIED/SUPPORTED/etc."""
    store = _store(tmp_path)
    memory = SemanticMemory(store)
    record = KnowledgeRecord(
        knowledge_id=KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content="the deployment key is rotated weekly",
        created_at=_T0,
        status=status,
    )

    with pytest.raises(IngestionStatusError):
        memory.remember(record)

    assert store.list_records() == ()


def test_remember_refuses_a_verification_timestamp_on_new_knowledge(tmp_path: Path) -> None:
    store = _store(tmp_path)
    memory = SemanticMemory(store)
    record = KnowledgeRecord(
        knowledge_id=KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content="claim with a forged verification time",
        created_at=_T0,
        status=KnowledgeStatus.UNVERIFIED,
        verified_at=_T1,
    )

    with pytest.raises(IngestionStatusError):
        memory.remember(record)

    assert store.list_records() == ()


def test_remembered_knowledge_stays_unverified(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    record = memory.remember(_new(provenance=_WEB_PROVENANCE))

    recalled = memory.recall(record.knowledge_id)

    assert recalled is not None
    assert recalled.status is KnowledgeStatus.UNVERIFIED
    assert recalled.verified_at is None


def test_repeated_claims_and_evidence_never_promote_status(tmp_path: Path) -> None:
    """Neither repetition nor accumulated C2.07 evidence changes trust."""
    memory = _memory(tmp_path)
    claim = "the finance share is read-only for contractors"
    first = memory.remember(_new(claim, created_at=_T0))
    second = memory.remember(_new(claim, created_at=_T1))
    third = memory.remember(_new(claim, created_at=_T2))

    evidence = KnowledgeEvidence(
        knowledge_id=first.knowledge_id,
        references=tuple(
            EvidenceReference(
                kind=EvidenceKind.OBSERVATION,
                reference=f"observation-{index}",
                provenance=_WEB_PROVENANCE,
            )
            for index in range(5)
        ),
    )

    assert len(evidence.references) == 5
    assert {record.status for record in memory.recall_all()} == {KnowledgeStatus.UNVERIFIED}
    assert {record.knowledge_id for record in memory.recall_all()} == {
        first.knowledge_id,
        second.knowledge_id,
        third.knowledge_id,
    }


def test_service_preserves_a_lifecycle_owned_status_transition(tmp_path: Path) -> None:
    """A status set through the store's explicit C2.08 path is returned verbatim."""
    store = _store(tmp_path)
    memory = SemanticMemory(store)
    record = memory.remember(_new())

    updated = store.update_status(record.knowledge_id, KnowledgeStatus.VERIFIED, verified_at=_T1)
    recalled = memory.recall(record.knowledge_id)

    assert recalled == updated
    assert recalled is not None
    assert recalled.status is KnowledgeStatus.VERIFIED
    assert recalled.verified_at == _T1
    assert recalled.content == record.content


# ---------------------------------------------------------------------------
# Duplicate behaviour
# ---------------------------------------------------------------------------


def test_duplicate_identity_is_rejected_and_never_overwrites(tmp_path: Path) -> None:
    store = _store(tmp_path)
    memory = SemanticMemory(store)
    original = memory.remember(_new("original claim"))
    collision = KnowledgeRecord(
        knowledge_id=original.knowledge_id,
        knowledge_type=KnowledgeType.FACT,
        content="replacement claim",
        created_at=_T2,
    )

    with pytest.raises(DuplicateSemanticKnowledgeError):
        memory.remember(collision)

    assert store.get(original.knowledge_id) == original
    assert store.list_records() == (original,)


def test_duplicate_error_is_a_semantic_memory_error() -> None:
    assert issubclass(DuplicateSemanticKnowledgeError, SemanticMemoryError)
    assert issubclass(NonSemanticKnowledgeError, SemanticMemoryError)
    assert issubclass(IngestionStatusError, SemanticMemoryError)
    assert issubclass(SemanticMemoryQueryError, SemanticMemoryError)


def test_identical_content_under_new_identities_is_stored_separately(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    first = memory.remember(_new("same words", created_at=_T0))
    second = memory.remember(_new("same words", created_at=_T1))

    assert first.knowledge_id != second.knowledge_id
    assert len(memory.recall_all()) == 2


# ---------------------------------------------------------------------------
# Exact retrieval
# ---------------------------------------------------------------------------


def test_recall_returns_the_exact_record(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    wanted = memory.remember(_new("wanted claim", created_at=_T0))
    memory.remember(_new("other claim", created_at=_T1))

    assert memory.recall(wanted.knowledge_id) == wanted


def test_recall_of_an_unknown_identity_is_none(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    memory.remember(_new())

    assert memory.recall(KnowledgeId.create()) is None


def test_recall_rejects_a_foreign_identity_type(tmp_path: Path) -> None:
    memory = _memory(tmp_path)

    with pytest.raises(TypeError):
        memory.recall(cast(KnowledgeId, EpisodeId.create()))


def test_recall_ignores_records_owned_by_another_memory_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    memory = SemanticMemory(store)
    record = _new(knowledge_type=KnowledgeType.OBSERVATION)
    store.insert(record)

    monkeypatch.setattr(
        semantic_memory_module,
        "SEMANTIC_KNOWLEDGE_TYPES",
        frozenset({KnowledgeType.FACT}),
    )

    assert memory.recall(record.knowledge_id) is None
    assert memory.recall_all() == ()
    assert store.get(record.knowledge_id) == record


# ---------------------------------------------------------------------------
# Deterministic enumeration
# ---------------------------------------------------------------------------


def test_enumeration_is_deterministic_and_independent_of_write_order(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    third = memory.remember(_new("third", created_at=_T2))
    first = memory.remember(_new("first", created_at=_T0))
    second = memory.remember(_new("second", created_at=_T1))

    listed = memory.recall_all()

    assert listed == (first, second, third)
    assert listed == memory.recall_all()
    assert isinstance(listed, tuple)


def test_enumeration_breaks_timestamp_ties_by_identity(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    records = [memory.remember(_new(f"claim {index}", created_at=_T0)) for index in range(6)]

    listed = memory.recall_all()

    assert listed == tuple(sorted(records, key=lambda item: item.knowledge_id.to_str()))


def test_enumeration_of_an_empty_store_is_empty(tmp_path: Path) -> None:
    assert _memory(tmp_path).recall_all() == ()


# ---------------------------------------------------------------------------
# Structured, exact-match filtering
# ---------------------------------------------------------------------------


def test_filter_by_knowledge_type(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    fact = memory.remember(_new("a fact", knowledge_type=KnowledgeType.FACT, created_at=_T0))
    memory.remember(_new("a preference", knowledge_type=KnowledgeType.PREFERENCE, created_at=_T1))

    query = SemanticMemoryQuery(knowledge_types=frozenset({KnowledgeType.FACT}))

    assert memory.recall_all(query) == (fact,)


def test_filter_by_status(tmp_path: Path) -> None:
    store = _store(tmp_path)
    memory = SemanticMemory(store)
    memory.remember(_new("still unverified", created_at=_T0))
    promoted = memory.remember(_new("explicitly supported", created_at=_T1))
    updated = store.update_status(promoted.knowledge_id, KnowledgeStatus.SUPPORTED)

    query = SemanticMemoryQuery(statuses=frozenset({KnowledgeStatus.SUPPORTED}))

    assert memory.recall_all(query) == (updated,)


def test_filter_by_provenance_kind(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    from_web = memory.remember(_new("web claim", created_at=_T0, provenance=_WEB_PROVENANCE))
    memory.remember(
        _new(
            "user claim",
            created_at=_T1,
            provenance=ProvenanceReference(kind=ProvenanceKind.USER, reference="chat-7"),
        )
    )
    memory.remember(_new("unsourced claim", created_at=_T2))

    query = SemanticMemoryQuery(provenance_kinds=frozenset({ProvenanceKind.WEB}))

    assert memory.recall_all(query) == (from_web,)


def test_filter_by_exact_scope_equality(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    scoped = memory.remember(_new("scoped claim", created_at=_T0, scope=_WINDOWS_SCOPE))
    global_claim = memory.remember(_new("global claim", created_at=_T1))

    assert memory.recall_all(SemanticMemoryQuery(scope=_WINDOWS_SCOPE)) == (scoped,)
    assert memory.recall_all(SemanticMemoryQuery(scope=KnowledgeScope())) == (global_claim,)


def test_filter_by_scope_containment(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    excel = memory.remember(_new("excel claim", created_at=_T0, scope=_WINDOWS_SCOPE))
    windows_only = memory.remember(
        _new(
            "windows claim",
            created_at=_T1,
            scope=KnowledgeScope(dimensions={ScopeDimension.OPERATING_SYSTEM: "windows"}),
        )
    )
    memory.remember(
        _new(
            "linux claim",
            created_at=_T2,
            scope=KnowledgeScope(dimensions={ScopeDimension.OPERATING_SYSTEM: "linux"}),
        )
    )

    windows = KnowledgeScope(dimensions={ScopeDimension.OPERATING_SYSTEM: "windows"})
    query = SemanticMemoryQuery(scope_contains=windows)

    assert memory.recall_all(query) == (excel, windows_only)
    assert memory.recall_all(SemanticMemoryQuery(scope_contains=KnowledgeScope())) == (
        excel,
        windows_only,
        memory.recall_all()[2],
    )


def test_scope_filtering_is_exact_not_fuzzy(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    memory.remember(
        _new(
            "cased scope",
            scope=KnowledgeScope(dimensions={ScopeDimension.OPERATING_SYSTEM: "Windows"}),
        )
    )

    query = SemanticMemoryQuery(
        scope_contains=KnowledgeScope(dimensions={ScopeDimension.OPERATING_SYSTEM: "windows"})
    )

    assert memory.recall_all(query) == ()


def test_criteria_combine_with_and(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    wanted = memory.remember(
        _new("wanted", created_at=_T0, scope=_WINDOWS_SCOPE, provenance=_WEB_PROVENANCE)
    )
    memory.remember(_new("wrong scope", created_at=_T1, provenance=_WEB_PROVENANCE))
    memory.remember(_new("wrong provenance", created_at=_T2, scope=_WINDOWS_SCOPE))

    query = SemanticMemoryQuery(
        knowledge_types=frozenset({KnowledgeType.FACT}),
        statuses=frozenset({KnowledgeStatus.UNVERIFIED}),
        provenance_kinds=frozenset({ProvenanceKind.WEB}),
        scope=_WINDOWS_SCOPE,
        scope_contains=KnowledgeScope(dimensions={ScopeDimension.APPLICATION: "excel"}),
    )

    assert memory.recall_all(query) == (wanted,)


def test_filtering_preserves_deterministic_order(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    memory.remember(_new("c", created_at=_T2, provenance=_WEB_PROVENANCE))
    memory.remember(_new("a", created_at=_T0, provenance=_WEB_PROVENANCE))
    memory.remember(_new("b", created_at=_T1, provenance=_WEB_PROVENANCE))

    query = SemanticMemoryQuery(provenance_kinds=frozenset({ProvenanceKind.WEB}))
    filtered = memory.recall_all(query)

    assert [record.content for record in filtered] == ["a", "b", "c"]
    assert filtered == memory.recall_all()


def test_empty_query_matches_every_semantic_record(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    memory.remember(_new("a", created_at=_T0))
    memory.remember(_new("b", created_at=_T1))

    assert memory.recall_all(SemanticMemoryQuery()) == memory.recall_all()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"knowledge_types": frozenset()},
        {"statuses": frozenset()},
        {"provenance_kinds": frozenset()},
        {"knowledge_types": {KnowledgeType.FACT}},
        {"knowledge_types": frozenset({"fact"})},
        {"statuses": frozenset({KnowledgeType.FACT})},
        {"provenance_kinds": frozenset({KnowledgeStatus.VERIFIED})},
        {"scope": {"os": "windows"}},
        {"scope_contains": "windows"},
    ],
)
def test_malformed_queries_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(SemanticMemoryQueryError):
        SemanticMemoryQuery(**kwargs)  # type: ignore[arg-type]


def test_query_matches_requires_a_canonical_record() -> None:
    with pytest.raises(TypeError):
        SemanticMemoryQuery().matches(cast(KnowledgeRecord, "a claim"))


def test_recall_all_rejects_a_non_query(tmp_path: Path) -> None:
    memory = _memory(tmp_path)

    with pytest.raises(TypeError):
        memory.recall_all(cast(SemanticMemoryQuery, {"statuses": ["verified"]}))


def test_query_is_immutable() -> None:
    query = SemanticMemoryQuery(statuses=frozenset({KnowledgeStatus.UNVERIFIED}))

    with pytest.raises(FrozenInstanceError):
        query.statuses = None  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Scope and provenance preservation
# ---------------------------------------------------------------------------


def test_scope_is_preserved_exactly(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    scope = KnowledgeScope(
        dimensions={
            ScopeDimension.APPLICATION: "excel",
            ScopeDimension.APPLICATION_VERSION: "2024",
            ScopeDimension.OPERATING_SYSTEM: "windows",
            ScopeDimension.ENVIRONMENT: "laptop",
            ScopeDimension.PROJECT: "q3-audit",
            ScopeDimension.CONTEXT: "monthly-close",
        }
    )
    record = memory.remember(_new(scope=scope))

    recalled = memory.recall(record.knowledge_id)

    assert recalled is not None
    assert recalled.scope == scope
    assert recalled.scope.to_dict() == scope.to_dict()


def test_global_scope_is_preserved_and_is_only_applicability(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    record = memory.remember(_new("applies everywhere"))

    recalled = memory.recall(record.knowledge_id)

    assert recalled is not None
    assert recalled.scope == KnowledgeScope()
    assert dict(recalled.scope.dimensions) == {}
    # Global applicability is not permission: the record is still inert data
    # in the birth trust state, with no authority attached anywhere.
    assert recalled.status is KnowledgeStatus.UNVERIFIED


def test_provenance_reference_is_preserved_exactly(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    record = memory.remember(_new(provenance=_WEB_PROVENANCE))

    recalled = memory.recall(record.knowledge_id)

    assert recalled is not None
    assert recalled.provenance == _WEB_PROVENANCE


def test_provenance_of_projects_stored_origin_into_the_canonical_record(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    record = memory.remember(_new(provenance=_WEB_PROVENANCE))

    provenance = memory.provenance_of(record.knowledge_id)

    assert provenance == ProvenanceRecord(knowledge_id=record.knowledge_id, source=_WEB_PROVENANCE)
    assert isinstance(provenance, ProvenanceRecord)
    # Unstored detail is reported as unknown, never invented.
    assert provenance.observed_at is None
    assert provenance.locator is None
    assert provenance.derived_from == ()


def test_provenance_of_is_none_when_nothing_was_recorded(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    record = memory.remember(_new("claim with no recorded origin"))

    assert memory.provenance_of(record.knowledge_id) is None
    assert memory.provenance_of(KnowledgeId.create()) is None


def test_provenance_of_rejects_a_foreign_identity_type(tmp_path: Path) -> None:
    memory = _memory(tmp_path)

    with pytest.raises(TypeError):
        memory.provenance_of(cast(KnowledgeId, EpisodeId.create()))


@pytest.mark.parametrize("kind", sorted(ProvenanceKind))
def test_provenance_channel_never_changes_trust(tmp_path: Path, kind: ProvenanceKind) -> None:
    memory = _memory(tmp_path)
    record = memory.remember(
        _new(provenance=ProvenanceReference(kind=kind, reference=f"{kind.value}-source"))
    )

    recalled = memory.recall(record.knowledge_id)

    assert recalled is not None
    assert recalled.status is KnowledgeStatus.UNVERIFIED
    assert recalled.verified_at is None


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------


def test_knowledge_survives_a_new_service_over_the_same_database(tmp_path: Path) -> None:
    remembered = _memory(tmp_path).remember(_new("durable claim", scope=_WINDOWS_SCOPE))

    reopened = _memory(tmp_path)

    assert reopened.recall(remembered.knowledge_id) == remembered
    assert reopened.recall_all() == (remembered,)


def test_knowledge_survives_a_real_process_restart(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    remembered = _memory(tmp_path).remember(
        _new("durable claim", scope=_WINDOWS_SCOPE, provenance=_WEB_PROVENANCE)
    )

    script = (
        "from pathlib import Path\n"
        "from agentx.core.ids import KnowledgeId\n"
        "from agentx.core.knowledge import KnowledgeStatus\n"
        "from agentx.hive.semantic_memory import SemanticMemory\n"
        "from agentx.infrastructure.knowledge_store import KnowledgeStore\n"
        "from agentx.infrastructure.persistence import SQLiteDatabase\n"
        f"memory = SemanticMemory(KnowledgeStore(SQLiteDatabase(Path({str(path)!r}))))\n"
        f"record = memory.recall(KnowledgeId.parse({remembered.knowledge_id.to_str()!r}))\n"
        "assert record is not None\n"
        "assert record.content == 'durable claim'\n"
        "assert record.status is KnowledgeStatus.UNVERIFIED\n"
        "assert record.scope.to_dict() == {'os': 'windows', 'application': 'excel'}\n"
        "assert record.provenance is not None\n"
        "assert record.provenance.reference == 'https://example.invalid/claim'\n"
        "assert len(memory.recall_all()) == 1\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
