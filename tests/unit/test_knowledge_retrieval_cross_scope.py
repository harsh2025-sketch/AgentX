"""C6.08 cross-scope protection wired into the C2.09 retrieval boundary.

A guarded ``KnowledgeRetrieval`` applies the scope-compatibility filter after
its structured applicability filters — including on exact-identity point
lookups — and denied records never enter the returned tuple. Without a guard
the documented C2.09 behaviour is unchanged.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ScopeDimension,
)
from agentx.core.retrieval_scope import RetrievalScopeGuard, ScopeAccessReason
from agentx.infrastructure.knowledge_retrieval import (
    DEFAULT_RETRIEVAL_STATUSES,
    KnowledgeRetrieval,
    KnowledgeRetrievalQuery,
)
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = datetime(2026, 1, 2, tzinfo=UTC)
_T2 = datetime(2026, 1, 3, tzinfo=UTC)
_T3 = datetime(2026, 1, 4, tzinfo=UTC)

_PROJECT_ALPHA = KnowledgeScope(dimensions={ScopeDimension.PROJECT: "alpha"})
_PROJECT_BETA = KnowledgeScope(dimensions={ScopeDimension.PROJECT: "beta"})
_ENV_PROD = KnowledgeScope(dimensions={ScopeDimension.ENVIRONMENT: "prod"})
_ENV_DEV = KnowledgeScope(
    dimensions={ScopeDimension.PROJECT: "alpha", ScopeDimension.ENVIRONMENT: "dev"}
)


def _store(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(SQLiteDatabase(tmp_path / "agentx.sqlite3"))


def _record(
    content: str,
    *,
    scope: KnowledgeScope,
    created_at: datetime,
    status: KnowledgeStatus = KnowledgeStatus.UNVERIFIED,
    knowledge_id: KnowledgeId | None = None,
) -> KnowledgeRecord:
    return KnowledgeRecord(
        knowledge_id=knowledge_id if knowledge_id is not None else KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content=content,
        created_at=created_at,
        status=status,
        scope=scope,
    )


@pytest.fixture
def store(tmp_path: Path) -> KnowledgeStore:
    store = _store(tmp_path)
    store.insert(_record("alpha claim", scope=_PROJECT_ALPHA, created_at=_T0))
    store.insert(_record("global claim", scope=KnowledgeScope(), created_at=_T1))
    store.insert(_record("beta claim", scope=_PROJECT_BETA, created_at=_T2))
    store.insert(
        _record(
            "alpha dev only",
            scope=_ENV_DEV,
            created_at=_T3,
            status=KnowledgeStatus.SUPERSEDED,
        )
    )
    return store


def _guarded(store: KnowledgeStore, scope: KnowledgeScope = _PROJECT_ALPHA) -> KnowledgeRetrieval:
    return KnowledgeRetrieval(store, RetrievalScopeGuard(scope))


# ---------------------------------------------------------------------------
# Same-scope retrieval works; incompatible scope is denied
# ---------------------------------------------------------------------------


def test_guarded_unfiltered_retrieval_returns_only_visible_scopes(store: KnowledgeStore) -> None:
    retrieval = _guarded(store)

    contents = tuple(record.content for record in retrieval.retrieve())
    assert contents == ("alpha claim", "global claim")


def test_same_scope_point_lookup_works(store: KnowledgeStore) -> None:
    retrieval = _guarded(store)
    alpha = store.list_records()[0]

    result = retrieval.retrieve(KnowledgeRetrievalQuery(knowledge_id=alpha.knowledge_id))
    assert result == (alpha,)


def test_crafted_point_lookup_for_foreign_scope_returns_empty(store: KnowledgeStore) -> None:
    retrieval = _guarded(store)
    beta = store.list_records()[2]

    assert retrieval.retrieve(KnowledgeRetrievalQuery(knowledge_id=beta.knowledge_id)) == ()


def test_nonexistent_crafted_id_returns_empty(store: KnowledgeStore) -> None:
    retrieval = _guarded(store)

    absent = KnowledgeRetrievalQuery(knowledge_id=KnowledgeId.create())
    assert retrieval.retrieve(absent) == ()


def test_cross_environment_record_requires_the_environment_dimension(store: KnowledgeStore) -> None:
    # alpha/prod cannot see anything restricted to alpha/dev; the record's
    # project restriction is also unproven for a context without a project.
    prod = KnowledgeRetrieval(store, RetrievalScopeGuard(_ENV_PROD))
    assert [record.content for record in prod.retrieve()] == ["global claim"]
    # The alpha/dev context sees its own-scoped claim and project/global data,
    # while beta remains invisible.
    dev = KnowledgeRetrieval(store, RetrievalScopeGuard(_ENV_DEV))
    assert [record.content for record in dev.retrieve()] == [
        "alpha claim",
        "global claim",
    ]


def test_explicit_status_override_still_denies_foreign_scope(store: KnowledgeStore) -> None:
    retrieval = _guarded(store)
    superseded_beta = store.list_records()[2]

    # An explicit "read history" policy never escapes scope protection.
    override = KnowledgeRetrievalQuery(
        knowledge_id=superseded_beta.knowledge_id,
        statuses=frozenset({KnowledgeStatus.UNVERIFIED, KnowledgeStatus.SUPERSEDED}),
    )
    assert retrieval.retrieve(override) == ()
    beta_reader = _guarded(store, _PROJECT_BETA)
    assert [record.content for record in beta_reader.retrieve(override)] == ["beta claim"]


# ---------------------------------------------------------------------------
# No caller-controlled bypass
# ---------------------------------------------------------------------------


def test_query_fields_cannot_disable_the_guard(store: KnowledgeStore) -> None:
    retrieval = _guarded(store)

    broad = KnowledgeRetrievalQuery(scope=KnowledgeScope())  # "match everything" filter
    assert tuple(record.content for record in retrieval.retrieve(broad)) == (
        "alpha claim",
        "global claim",
    )


def test_retrieval_query_exposes_no_scope_escape_field() -> None:
    names = {field_.name for field_ in dataclasses.fields(KnowledgeRetrievalQuery)}
    assert names == {
        "knowledge_id",
        "knowledge_types",
        "statuses",
        "scope",
        "provenance_kind",
        "provenance_reference",
    }
    for escape in ("bypass", "allow_all", "ignore_scope", "scope_override"):
        assert escape not in names


def test_guard_field_cannot_be_cleared_on_a_guarded_retrieval(store: KnowledgeStore) -> None:
    retrieval = _guarded(store)

    with pytest.raises(dataclasses.FrozenInstanceError):
        retrieval.scope_guard = None  # type: ignore[misc]
    assert tuple(record.content for record in retrieval.retrieve()) == (
        "alpha claim",
        "global claim",
    )


def test_malformed_guard_rejected_at_construction(store: KnowledgeStore) -> None:
    with pytest.raises((TypeError, ValueError)):
        KnowledgeRetrieval(store, scope_guard="*" * 3)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Determinism and unguarded backward compatibility
# ---------------------------------------------------------------------------


def test_guarded_retrieval_is_deterministic(store: KnowledgeStore) -> None:
    retrieval = _guarded(store)

    assert retrieval.retrieve() == retrieval.retrieve()
    assert retrieval.retrieve(KnowledgeRetrievalQuery()) == retrieval.retrieve()


def test_unguarded_retrieval_is_unchanged(store: KnowledgeStore) -> None:
    retrieval = KnowledgeRetrieval(store)

    contents = tuple(record.content for record in retrieval.retrieve())
    assert contents == ("alpha claim", "global claim", "beta claim")


def test_status_policy_and_scope_policy_apply_independently(store: KnowledgeStore) -> None:
    # The alpha/dev record is SUPERSEDED: excluded by the default status
    # policy first...
    dev_reader = KnowledgeRetrieval(store, RetrievalScopeGuard(_ENV_DEV))
    assert [record.content for record in dev_reader.retrieve()] == [
        "alpha claim",
        "global claim",
    ]
    # ...and becomes visible to the exactly matching context when status is
    # explicitly requested: the two policies compose, each in its own direction.
    visible = KnowledgeRetrievalQuery(
        statuses=DEFAULT_RETRIEVAL_STATUSES | {KnowledgeStatus.SUPERSEDED}
    )
    assert [record.content for record in dev_reader.retrieve(visible)] == [
        "alpha claim",
        "global claim",
        "alpha dev only",
    ]
    # The alpha project context (no environment named) still fails the
    # isolation check on that same superseded record while history is open.
    alpha_reader = _guarded(store)
    assert [record.content for record in alpha_reader.retrieve(visible)] == [
        "alpha claim",
        "global claim",
    ]


def test_guard_reason_code_is_available_for_the_denied_candidate(store: KnowledgeStore) -> None:
    retrieval = _guarded(store)
    beta = store.list_records()[2]
    guard = retrieval.scope_guard
    assert guard is not None

    decision = guard.evaluate(beta)
    assert not decision.allowed
    assert decision.reason is ScopeAccessReason.DENY_SCOPE_MISMATCH
