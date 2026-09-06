"""Tests for the C6.08 cross-scope retrieval protection boundary.

The boundary decides, from the canonical typed ``scope`` fields alone, whether
one candidate record may enter a retrieval result for one request context.
These tests pin the deterministic compatibility matrix, the reason codes, the
fail-closed treatment of malformed data, batch partitioning, and the absence of
any caller-controlled bypass knob.
"""

from __future__ import annotations

import dataclasses
from collections.abc import ItemsView, Iterator, Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import cast

import pytest

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ScopeDimension,
)
from agentx.core.negative_experience import (
    AttemptKind,
    AttemptReference,
    FailureReference,
    NegativeExperienceRecord,
)
from agentx.core.retrieval_scope import (
    RetrievalScopeError,
    RetrievalScopeGuard,
    ScopeAccessReason,
    ScopeDecision,
    ScopeDenial,
    evaluate_record_scope,
)

_T0 = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)

_ALPHA = ScopeDimension.PROJECT
_BETA = "project-beta"
_ALPHA_PROJECT = "project-alpha"

_PROJECT_ALPHA = KnowledgeScope(dimensions={ScopeDimension.PROJECT: _ALPHA_PROJECT})
_PROJECT_BETA = KnowledgeScope(dimensions={ScopeDimension.PROJECT: _BETA})
_ENV_DEV = KnowledgeScope(dimensions={ScopeDimension.ENVIRONMENT: "development"})
_ENV_PROD = KnowledgeScope(dimensions={ScopeDimension.ENVIRONMENT: "production"})
_ALPHA_DEV = KnowledgeScope(
    dimensions={ScopeDimension.PROJECT: _ALPHA_PROJECT, ScopeDimension.ENVIRONMENT: "development"}
)
_ALPHA_PROD = KnowledgeScope(
    dimensions={ScopeDimension.PROJECT: _ALPHA_PROJECT, ScopeDimension.ENVIRONMENT: "production"}
)
_GLOBAL = KnowledgeScope()


def _record(
    content: str = "a claim",
    *,
    scope: KnowledgeScope | None = None,
    knowledge_id: KnowledgeId | None = None,
) -> KnowledgeRecord:
    return KnowledgeRecord(
        knowledge_id=knowledge_id if knowledge_id is not None else KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content=content,
        created_at=_T0,
        status=KnowledgeStatus.UNVERIFIED,
        scope=KnowledgeScope() if scope is None else scope,
    )


def _fresh(scope: KnowledgeScope) -> KnowledgeScope:
    """Return an equal but private scope so tampering never hits shared data."""
    return KnowledgeScope(dimensions=dict(scope.dimensions))


def _proxy(mapping: object) -> Mapping[ScopeDimension, str]:
    """Wrap a tampered mapping exactly like a real scope would hold it."""
    return MappingProxyType(cast("Mapping[ScopeDimension, str]", mapping))


def _negative(*, scope: KnowledgeScope | None = None) -> NegativeExperienceRecord:
    return NegativeExperienceRecord.create(
        attempt=AttemptReference(kind=AttemptKind.APPROACH, reference="fs.write"),
        failure=FailureReference(reason_code="denied"),
        scope=KnowledgeScope() if scope is None else scope,
        observed_at=_T0,
    )


# ---------------------------------------------------------------------------
# The compatibility matrix (same scope works; incompatible scope is denied)
# ---------------------------------------------------------------------------


def test_identical_scoped_record_is_allowed_with_same_scope_reason() -> None:
    decision = evaluate_record_scope(_record(scope=_PROJECT_ALPHA), _PROJECT_ALPHA)
    assert decision == ScopeDecision(ScopeAccessReason.ALLOW_SAME_SCOPE)
    assert decision.allowed


def test_identical_empty_scopes_are_same_scope() -> None:
    decision = evaluate_record_scope(_record(scope=_GLOBAL), _GLOBAL)
    assert decision.reason is ScopeAccessReason.ALLOW_SAME_SCOPE
    assert decision.allowed


def test_global_record_is_visible_to_a_scoped_request() -> None:
    decision = evaluate_record_scope(_record(scope=_GLOBAL), _ALPHA_DEV)
    assert decision.reason is ScopeAccessReason.ALLOW_GLOBAL_RECORD
    assert decision.allowed


def test_record_restrictions_satisfied_by_broader_request_are_allowed() -> None:
    decision = evaluate_record_scope(_record(scope=_PROJECT_ALPHA), _ALPHA_DEV)
    assert decision.reason is ScopeAccessReason.ALLOW_RESTRICTIONS_SATISFIED
    assert decision.allowed


def test_cross_project_scope_is_denied_with_reason_and_dimension() -> None:
    decision = evaluate_record_scope(_record(scope=_PROJECT_BETA), _PROJECT_ALPHA)
    assert decision.reason is ScopeAccessReason.DENY_SCOPE_MISMATCH
    assert decision.dimension is ScopeDimension.PROJECT
    assert not decision.allowed


def test_cross_environment_scope_is_denied() -> None:
    decision = evaluate_record_scope(_record(scope=_ENV_PROD), _ENV_DEV)
    assert decision.reason is ScopeAccessReason.DENY_SCOPE_MISMATCH
    assert decision.dimension is ScopeDimension.ENVIRONMENT


def test_restricted_record_invisible_to_request_that_omits_the_dimension() -> None:
    decision = evaluate_record_scope(_record(scope=_ALPHA_DEV), _PROJECT_ALPHA)
    assert decision.reason is ScopeAccessReason.DENY_RESTRICTION_UNPROVEN
    assert decision.dimension is ScopeDimension.ENVIRONMENT


def test_global_request_cannot_see_any_restricted_record() -> None:
    for scope in (_PROJECT_ALPHA, _ALPHA_DEV, _ENV_PROD):
        decision = evaluate_record_scope(_record(scope=scope), _GLOBAL)
        assert decision.reason is ScopeAccessReason.DENY_RESTRICTION_UNPROVEN
        assert not decision.allowed


def test_no_hierarchical_scope_semantics_are_invented() -> None:
    """Values are opaque exact strings: prefixes are NOT parents."""
    nested = KnowledgeScope(dimensions={ScopeDimension.PROJECT: "alpha/one"})
    parent = KnowledgeScope(dimensions={ScopeDimension.PROJECT: "alpha"})
    assert evaluate_record_scope(_record(scope=nested), parent).reason is (
        ScopeAccessReason.DENY_SCOPE_MISMATCH
    )
    assert evaluate_record_scope(_record(scope=parent), nested).reason is (
        ScopeAccessReason.DENY_SCOPE_MISMATCH
    )


def test_exact_string_equality_only_no_case_or_unicode_folding() -> None:
    upper = KnowledgeScope(dimensions={ScopeDimension.ENVIRONMENT: "PRODUCTION"})
    decision = evaluate_record_scope(_record(scope=_ENV_PROD), upper)
    assert decision.reason is ScopeAccessReason.DENY_SCOPE_MISMATCH


# ---------------------------------------------------------------------------
# Malformed data fails closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tampered_scope",
    [
        None,
        "project-alpha",
        {"project": "project-alpha"},
        42,
        _PROJECT_ALPHA.dimensions,  # raw mapping, not a KnowledgeScope
    ],
    ids=["none", "str", "dict", "int", "bare-mapping"],
)
def test_malformed_record_scope_is_denied_not_raised(tampered_scope: object) -> None:
    record = _record()
    object.__setattr__(record, "scope", tampered_scope)
    decision = evaluate_record_scope(record, _PROJECT_ALPHA)
    assert decision.reason is ScopeAccessReason.DENY_MALFORMED_RECORD_SCOPE
    assert not decision.allowed


def test_scope_attribute_absent_from_candidate_is_denied() -> None:
    class _NoScope:
        pass

    decision = evaluate_record_scope(_NoScope(), _PROJECT_ALPHA)
    assert decision.reason is ScopeAccessReason.DENY_INVALID_RECORD


def test_duck_typed_look_alike_record_is_denied() -> None:
    class _Pretender:
        def __init__(self) -> None:
            self.scope = _PROJECT_ALPHA

    decision = evaluate_record_scope(_Pretender(), _PROJECT_ALPHA)
    assert decision.reason is ScopeAccessReason.DENY_INVALID_RECORD


def test_subclass_of_canonical_record_is_denied() -> None:
    class _Extended(KnowledgeRecord):
        pass

    extended = _Extended(
        knowledge_id=KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content="subclassed smuggler",
        created_at=_T0,
        scope=_PROJECT_ALPHA,
    )
    decision = evaluate_record_scope(extended, _PROJECT_ALPHA)
    assert decision.reason is ScopeAccessReason.DENY_INVALID_RECORD


@pytest.mark.parametrize(
    "dimensions",
    [
        {"project": "project-alpha"},  # string key, not the canonical enum
        {ScopeDimension.PROJECT: "  untrimmed"},
        {ScopeDimension.PROJECT: "trailing "},
        {ScopeDimension.PROJECT: ""},
        {ScopeDimension.PROJECT: 7},
    ],
    ids=["str-key", "leading-space", "trailing-space", "empty", "non-str"],
)
def test_tampered_scope_dimensions_are_denied_malformed(dimensions: object) -> None:
    record = _record(scope=_fresh(_PROJECT_ALPHA))
    object.__setattr__(record.scope, "dimensions", _proxy(dimensions))
    decision = evaluate_record_scope(record, _PROJECT_ALPHA)
    assert decision.reason is ScopeAccessReason.DENY_MALFORMED_RECORD_SCOPE


class _DuplicatingMapping(Mapping[ScopeDimension, str]):
    """A hostile mapping that yields the same dimension key twice."""

    def __init__(self, key: ScopeDimension, value: str) -> None:
        self._key = key
        self._value = value

    def __getitem__(self, key: ScopeDimension) -> str:
        if key is not self._key:
            raise KeyError(key)
        return self._value

    def __iter__(self) -> Iterator[ScopeDimension]:
        return iter([self._key, self._key])

    def __len__(self) -> int:
        return 2


def test_duplicate_dimension_entries_in_a_mapping_look_alike_are_denied() -> None:
    record = _record(scope=_fresh(_PROJECT_ALPHA))
    object.__setattr__(
        record.scope, "dimensions", _DuplicatingMapping(ScopeDimension.PROJECT, _ALPHA_PROJECT)
    )
    decision = evaluate_record_scope(record, _PROJECT_ALPHA)
    assert decision.reason is ScopeAccessReason.DENY_MALFORMED_RECORD_SCOPE


class _HostileIterationMapping(Mapping[ScopeDimension, str]):
    """A mapping whose ``items()`` raises while the boundary is reading it."""

    def __getitem__(self, key: ScopeDimension) -> str:  # pragma: no cover - unused
        raise KeyError(key)

    def __iter__(self) -> Iterator[ScopeDimension]:  # pragma: no cover - unused
        return iter(())

    def __len__(self) -> int:  # pragma: no cover - unused
        return 0

    def items(self) -> ItemsView[ScopeDimension, str]:
        raise RuntimeError("iteration is hostile")


def test_mapping_that_raises_while_being_read_is_denied_not_fatal() -> None:
    record = _record(scope=_fresh(_PROJECT_ALPHA))
    object.__setattr__(record.scope, "dimensions", _HostileIterationMapping())
    guard = RetrievalScopeGuard(_PROJECT_ALPHA)
    assert guard.evaluate(record).reason is ScopeAccessReason.DENY_MALFORMED_RECORD_SCOPE
    # A whole batch survives: the hostile entry denies itself, the rest works.
    partition = guard.partition((record, _record(scope=_PROJECT_ALPHA)))
    assert partition.denied_count == 1
    assert len(partition.allowed) == 1


def test_malformed_request_scope_cannot_build_a_guard() -> None:
    for bad in (None, "any", {"project": "x"}, _PROJECT_ALPHA.dimensions):
        with pytest.raises(RetrievalScopeError):
            RetrievalScopeGuard(bad)  # type: ignore[arg-type]


def test_malformed_request_scope_raises_in_pure_evaluation_too() -> None:
    with pytest.raises(RetrievalScopeError):
        evaluate_record_scope(_record(scope=_GLOBAL), "not-a-scope")  # type: ignore[arg-type]


def test_guard_requires_a_request_scope_argument() -> None:
    with pytest.raises(TypeError):
        RetrievalScopeGuard()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Negative-experience records follow the same rule
# ---------------------------------------------------------------------------


def test_negative_experience_records_use_the_same_boundary() -> None:
    guard = RetrievalScopeGuard(_PROJECT_ALPHA)
    assert guard.evaluate(_negative(scope=_PROJECT_ALPHA)).allowed
    assert guard.evaluate(_negative(scope=_GLOBAL)).allowed
    denied = guard.evaluate(_negative(scope=_PROJECT_BETA))
    assert denied.reason is ScopeAccessReason.DENY_SCOPE_MISMATCH


# ---------------------------------------------------------------------------
# Batch filtering
# ---------------------------------------------------------------------------


def test_partition_filters_a_mixed_batch_and_keeps_input_order() -> None:
    keep_alpha = _record("alpha one", scope=_PROJECT_ALPHA)
    keep_global = _record("global", scope=_GLOBAL)
    drop_beta = _record("beta", scope=_PROJECT_BETA)
    drop_prod_dev = _record("alpha+dev", scope=_ALPHA_DEV)
    corrupt = _record("corrupt")
    object.__setattr__(corrupt, "scope", None)

    partition = RetrievalScopeGuard(_ALPHA_PROD).partition(
        (keep_alpha, corrupt, drop_beta, keep_global, drop_prod_dev)
    )

    assert partition.allowed == (keep_alpha, keep_global)
    assert partition.denied_count == 3
    assert partition.denials == (
        ScopeDenial(index=1, reason=ScopeAccessReason.DENY_MALFORMED_RECORD_SCOPE, dimension=None),
        ScopeDenial(
            index=2,
            reason=ScopeAccessReason.DENY_SCOPE_MISMATCH,
            dimension=ScopeDimension.PROJECT,
        ),
        ScopeDenial(
            index=4,
            reason=ScopeAccessReason.DENY_SCOPE_MISMATCH,
            dimension=ScopeDimension.ENVIRONMENT,
        ),
    )


def test_denial_reports_carry_no_record_data() -> None:
    foreign = _record("CANARY-content-5f3", scope=_PROJECT_BETA)
    partition = RetrievalScopeGuard(_PROJECT_ALPHA).partition((foreign,))
    assert partition.allowed == ()
    denial = partition.denials[0]
    rendered = repr(denial) + str(denial.reason)
    assert "CANARY" not in rendered
    assert foreign.knowledge_id.to_str() not in rendered
    assert _BETA not in rendered


def test_filter_returns_only_allowed_and_preserves_types() -> None:
    alpha = _record(scope=_PROJECT_ALPHA)
    beta = _record(scope=_PROJECT_BETA)
    assert RetrievalScopeGuard(_PROJECT_ALPHA).filter(iter([alpha, beta])) == (alpha,)


def test_partition_rejects_string_like_inputs() -> None:
    guard = RetrievalScopeGuard(_GLOBAL)
    for bad in ("records", b"records"):
        with pytest.raises(TypeError):
            guard.partition(bad)
    assert guard.filter([]) == ()


def test_empty_batch_partitions_to_empty_result() -> None:
    partition = RetrievalScopeGuard(_PROJECT_ALPHA).partition(())
    assert partition.allowed == ()
    assert partition.denials == ()
    assert partition.denied_count == 0


# ---------------------------------------------------------------------------
# Determinism, purity, and absence of bypass surface
# ---------------------------------------------------------------------------


def test_evaluation_is_pure_and_repeatable() -> None:
    record = _record(scope=_ALPHA_PROD)
    guard = RetrievalScopeGuard(_PROJECT_ALPHA)
    first = guard.evaluate(record)
    second = guard.evaluate(record)
    assert first == second
    assert first == ScopeDecision(
        ScopeAccessReason.DENY_RESTRICTION_UNPROVEN, ScopeDimension.ENVIRONMENT
    )
    assert record.scope == _ALPHA_PROD  # never mutated by evaluation


def test_guard_snapshots_the_request_scope_immutably() -> None:
    scope = KnowledgeScope(dimensions={ScopeDimension.PROJECT: _ALPHA_PROJECT})
    guard = RetrievalScopeGuard(scope)
    object.__setattr__(scope, "dimensions", MappingProxyType({}))
    # The guard keeps its original snapshot: a later tampering attempt on the
    # scope object it was given cannot widen its decisions.
    assert guard.evaluate(_record(scope=_PROJECT_ALPHA)).allowed
    assert guard.evaluate(_record(scope=_PROJECT_BETA)).allowed is False


def test_guard_is_frozen_and_has_no_bypass_surface() -> None:
    guard = RetrievalScopeGuard(_PROJECT_ALPHA)
    with pytest.raises(dataclasses.FrozenInstanceError):
        guard.request_scope = _GLOBAL  # type: ignore[misc]
    public = {name for name in dir(guard) if not name.startswith("_")}
    assert public == {"evaluate", "filter", "partition", "request_scope"}
    for forbidden in ("allow_all", "bypass", "override", "unrestricted", "include"):
        assert not any(forbidden in name for name in public)


def test_no_widening_is_possible_by_weakening_the_request_scope() -> None:
    """Monotonicity: dropping dimensions from the request can only deny more."""
    record = _record(scope=_PROJECT_ALPHA)
    strict = RetrievalScopeGuard(_PROJECT_ALPHA).evaluate(record)
    weakened = RetrievalScopeGuard(_GLOBAL).evaluate(record)
    assert strict.allowed
    assert not weakened.allowed


def test_reason_codes_are_a_closed_vocabulary() -> None:
    assert {reason.value for reason in ScopeAccessReason} == {
        "allow_same_scope",
        "allow_global_record",
        "allow_restrictions_satisfied",
        "deny_scope_mismatch",
        "deny_restriction_unproven",
        "deny_malformed_record_scope",
        "deny_invalid_record",
    }
