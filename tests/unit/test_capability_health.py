"""Unit tests for the M7.03 canonical capability-health assessment contract."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import pytest

from agentx.core.capability_health import (
    CANONICAL_CAPABILITY_HEALTH_EVIDENCE_ANCHORS,
    CANONICAL_CAPABILITY_HEALTH_EVIDENCE_FACTS,
    CANONICAL_CAPABILITY_HEALTH_EVIDENCE_KINDS,
    CANONICAL_CAPABILITY_HEALTH_FACTS,
    CANONICAL_CAPABILITY_HEALTH_REASONS,
    CANONICAL_CAPABILITY_HEALTH_STATES,
    CAPABILITY_HEALTH_SCHEMA_VERSION,
    MAX_HEALTH_EVIDENCE_RECORDS,
    MAX_HEALTH_EVIDENCE_TTL,
    CapabilityHealthAssessment,
    CapabilityHealthConflict,
    CapabilityHealthEvidence,
    CapabilityHealthEvidenceKind,
    CapabilityHealthFact,
    CapabilityHealthReason,
    CapabilityHealthState,
    CapabilityHealthSubject,
    CapabilityHealthValidationError,
    CapabilityVersionKey,
    assess_capability_health,
)
from agentx.core.environment_change import EnvironmentObservation
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.ids import CapabilityId

_NOW = datetime(2026, 9, 8, 12, 30, 15, 123456, tzinfo=UTC)
_TTL = timedelta(minutes=30)
_CAPABILITY_ID = CapabilityId.create()
_SUBJECT = CapabilityHealthSubject(
    capability_id=_CAPABILITY_ID, version=CapabilityVersionKey(major=1, minor=2, patch=3)
)


def _evidence(
    kind: CapabilityHealthEvidenceKind,
    fact: CapabilityHealthFact,
    *,
    at: datetime,
    ttl: timedelta = _TTL,
    detail: str | None = None,
    subject: CapabilityHealthSubject | None = None,
) -> CapabilityHealthEvidence:
    return CapabilityHealthEvidence(
        subject=subject if subject is not None else _SUBJECT,
        kind=kind,
        fact=fact,
        observed_at=at,
        ttl=ttl,
        detail=detail,
    )


def _ordered(*records: CapabilityHealthEvidence) -> tuple[CapabilityHealthEvidence, ...]:
    return tuple(sorted(records, key=lambda item: item.canonical_order))


def _assess(
    *records: CapabilityHealthEvidence,
    at: datetime = _NOW,
    detail: str | None = None,
) -> CapabilityHealthAssessment:
    return assess_capability_health(
        subject=_SUBJECT,
        evidence=_ordered(*records),
        assessed_at=at,
        summary="capability health probe",
        detail=detail,
    )


def _ago(**kwargs: float) -> datetime:
    return _NOW - timedelta(**kwargs)


# --------------------------------------------------------------------------
# Vocabularies.
# --------------------------------------------------------------------------


def test_state_vocabulary_is_the_closed_five_state_canonical_set() -> None:
    assert CANONICAL_CAPABILITY_HEALTH_STATES == (
        CapabilityHealthState.UNKNOWN,
        CapabilityHealthState.AVAILABLE,
        CapabilityHealthState.DEGRADED,
        CapabilityHealthState.UNAVAILABLE,
        CapabilityHealthState.UNSUPPORTED,
    )
    assert [state.value for state in CANONICAL_CAPABILITY_HEALTH_STATES] == [
        "unknown",
        "available",
        "degraded",
        "unavailable",
        "unsupported",
    ]
    # No scalar confidence, score, or probability member exists in the enum.
    assert not any(member.isnumeric() for member in CapabilityHealthState)


def test_evidence_kind_vocabulary_is_closed_and_canonically_ordered() -> None:
    assert CANONICAL_CAPABILITY_HEALTH_EVIDENCE_KINDS == (
        CapabilityHealthEvidenceKind.PLATFORM_SUPPORT,
        CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
        CapabilityHealthEvidenceKind.DEPENDENCY_AVAILABILITY,
        CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
        CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
        CapabilityHealthEvidenceKind.ENVIRONMENT_CHANGE,
    )


def test_fact_vocabulary_is_closed_and_every_fact_has_exactly_one_channel() -> None:
    assert len(CANONICAL_CAPABILITY_HEALTH_FACTS) == 15
    assigned = [
        fact for facts in CANONICAL_CAPABILITY_HEALTH_EVIDENCE_FACTS.values() for fact in facts
    ]
    # Every fact belongs to exactly one evidence channel: no fact is legal in
    # two channels, so a fact can never be coerced into a different meaning.
    assert sorted(fact.value for fact in assigned) == sorted(
        fact.value for fact in CANONICAL_CAPABILITY_HEALTH_FACTS
    )
    assert len(assigned) == len(set(assigned))
    assert set(CANONICAL_CAPABILITY_HEALTH_EVIDENCE_FACTS) == set(
        CANONICAL_CAPABILITY_HEALTH_EVIDENCE_KINDS
    )


def test_evidence_kinds_are_anchored_to_landed_canonical_vocabulary_names() -> None:
    """M7.03 indexes existing vocabularies; it is not a new world model."""
    assert set(CANONICAL_CAPABILITY_HEALTH_EVIDENCE_ANCHORS) == set(
        CANONICAL_CAPABILITY_HEALTH_EVIDENCE_KINDS
    )
    anchors = CANONICAL_CAPABILITY_HEALTH_EVIDENCE_ANCHORS

    assert anchors[CapabilityHealthEvidenceKind.PLATFORM_SUPPORT] == (
        "platform_identity",
        "environment",
    )
    assert anchors[CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY] == (
        "capability_availability",
        "capability",
    )
    assert anchors[CapabilityHealthEvidenceKind.DEPENDENCY_AVAILABILITY] == (
        "dependency_availability",
        "dependency",
    )
    assert anchors[CapabilityHealthEvidenceKind.EXECUTION_OUTCOME] == ("capability", "transient")
    assert anchors[CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME] == ("verification",)
    assert anchors[CapabilityHealthEvidenceKind.ENVIRONMENT_CHANGE] == (
        "relevant_change_detected",
        "no_relevant_change",
    )


def test_anchor_names_really_exist_in_the_landed_canonical_contracts() -> None:
    from agentx.core.environment_change import EnvironmentChangeResult, EnvironmentFactKind
    from agentx.core.failure_taxonomy import FailureCategory

    existing = {member.value for member in EnvironmentFactKind}
    existing |= {member.value for member in FailureCategory}
    existing |= {member.value for member in EnvironmentChangeResult}

    for names in CANONICAL_CAPABILITY_HEALTH_EVIDENCE_ANCHORS.values():
        for name in names:
            assert name in existing


def test_reason_vocabulary_is_closed_and_canonically_ordered() -> None:
    assert CANONICAL_CAPABILITY_HEALTH_REASONS[0] is CapabilityHealthReason.NO_EVIDENCE
    assert len(CANONICAL_CAPABILITY_HEALTH_REASONS) == len(set(CANONICAL_CAPABILITY_HEALTH_REASONS))
    for fact in CANONICAL_CAPABILITY_HEALTH_FACTS:
        # Every observable fact has a mirror reason, so a state is always
        # explainable by the typed facts that produced it.
        assert fact.value in {reason.value for reason in CANONICAL_CAPABILITY_HEALTH_REASONS}


# --------------------------------------------------------------------------
# Identity and version binding.
# --------------------------------------------------------------------------


def test_subject_binds_a_canonical_capability_id_and_an_explicit_version() -> None:
    assert _SUBJECT.capability_id == _CAPABILITY_ID
    assert _SUBJECT.version.to_str() == "1.2.3"
    assert str(_SUBJECT) == f"{_CAPABILITY_ID}@1.2.3"
    assert _SUBJECT.to_dict() == {
        "capability_id": _CAPABILITY_ID.to_str(),
        "version": {"major": 1, "minor": 2, "patch": 3},
    }


def test_subject_rejects_a_non_canonical_capability_id() -> None:
    with pytest.raises(TypeError):
        CapabilityHealthSubject(
            capability_id=cast(CapabilityId, str(_CAPABILITY_ID)),
            version=CapabilityVersionKey(1, 0, 0),
        )


def test_version_key_rejects_wildcards_ranges_and_partial_versions() -> None:
    for raw in (
        "",
        " ",
        "1",
        "1.2",
        "1.2.3.4",
        "1.2.*",
        "1.*.3",
        "*",
        "latest",
        "any",
        "v1.2.3",
        "1.2.3 ",
        " 1.2.3",
        "1.2.-3",
        "1.2.+3",
        "01.2.3x",
    ):
        with pytest.raises(CapabilityHealthValidationError):
            CapabilityVersionKey.from_str(raw)


def test_version_key_equality_is_exact_with_no_prefix_matching() -> None:
    assert CapabilityVersionKey(1, 2, 3) == CapabilityVersionKey(1, 2, 3)
    assert CapabilityVersionKey(1, 2, 3) != CapabilityVersionKey(1, 2, 0)
    assert CapabilityVersionKey(1, 2, 3) != CapabilityVersionKey(1, 0, 3)
    assert CapabilityVersionKey(1, 2, 3) != CapabilityVersionKey(0, 2, 3)
    assert CapabilityVersionKey.from_str("1.2.3").to_str() == "1.2.3"


def test_version_key_rejects_negative_and_non_integer_components() -> None:
    with pytest.raises(CapabilityHealthValidationError):
        CapabilityVersionKey(-1, 0, 0)
    with pytest.raises(TypeError):
        CapabilityVersionKey(cast(int, "1"), 0, 0)
    with pytest.raises(TypeError):
        CapabilityVersionKey(cast(int, True), 0, 0)


def test_evidence_for_the_same_capability_id_at_a_different_version_is_rejected() -> None:
    """Evidence for version 1 never establishes health for version 2."""
    other = CapabilityHealthSubject(
        capability_id=_CAPABILITY_ID, version=CapabilityVersionKey(major=2, minor=0, patch=0)
    )
    record = _evidence(
        CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
        CapabilityHealthFact.EXECUTION_SUCCEEDED,
        at=_ago(minutes=1),
        subject=other,
    )
    verified = _evidence(
        CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
        CapabilityHealthFact.VERIFICATION_PASSED,
        at=_ago(minutes=1),
        subject=other,
    )

    with pytest.raises(CapabilityHealthValidationError, match="exactly the assessed subject"):
        _assess(record, verified)


def test_evidence_for_a_different_capability_id_is_rejected() -> None:
    other = CapabilityHealthSubject(
        capability_id=CapabilityId.create(), version=CapabilityVersionKey(1, 2, 3)
    )
    record = _evidence(
        CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
        CapabilityHealthFact.PROVIDER_AVAILABLE,
        at=_ago(minutes=1),
        subject=other,
    )

    with pytest.raises(CapabilityHealthValidationError):
        _assess(record)


def test_a_lookalike_capability_id_string_is_not_a_capability_id() -> None:
    for raw in ("not-a-uuid", "capability." + _CAPABILITY_ID.to_str(), "0" * 36, ""):
        with pytest.raises(Exception):  # noqa: B017 - canonical ID parse errors vary
            CapabilityId.parse(raw)


# --------------------------------------------------------------------------
# Evidence record validation.
# --------------------------------------------------------------------------


def test_evidence_rejects_a_fact_outside_its_own_channel() -> None:
    with pytest.raises(CapabilityHealthValidationError, match="never coerced"):
        _evidence(
            CapabilityHealthEvidenceKind.PLATFORM_SUPPORT,
            CapabilityHealthFact.PROVIDER_AVAILABLE,
            at=_ago(minutes=1),
        )
    with pytest.raises(CapabilityHealthValidationError):
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_PASSED,
            at=_ago(minutes=1),
        )


def test_evidence_rejects_naive_timestamps_and_bad_ttls() -> None:
    naive = datetime(2026, 9, 8, 12, 0, 0)
    with pytest.raises(CapabilityHealthValidationError, match="timezone-aware"):
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_AVAILABLE,
            at=naive,
        )
    for bad_ttl in (timedelta(0), timedelta(seconds=-1)):
        with pytest.raises(CapabilityHealthValidationError):
            _evidence(
                CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
                CapabilityHealthFact.PROVIDER_AVAILABLE,
                at=_ago(minutes=1),
                ttl=bad_ttl,
            )


def test_evidence_normalizes_the_timestamp_to_utc() -> None:
    tokyo = timezone(timedelta(hours=9))
    record = _evidence(
        CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
        CapabilityHealthFact.PROVIDER_AVAILABLE,
        at=datetime(2026, 9, 8, 21, 30, 15, 123456, tzinfo=tokyo),
    )
    assert record.observed_at == _NOW
    assert record.observed_at.tzinfo == UTC


def test_evidence_rejects_an_unsupported_schema_version() -> None:
    with pytest.raises(CapabilityHealthValidationError, match="schema version"):
        CapabilityHealthEvidence(
            subject=_SUBJECT,
            kind=CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            fact=CapabilityHealthFact.PROVIDER_AVAILABLE,
            observed_at=_ago(minutes=1),
            ttl=_TTL,
            schema_version=CAPABILITY_HEALTH_SCHEMA_VERSION + 1,
        )


# --------------------------------------------------------------------------
# Freshness.
# --------------------------------------------------------------------------


def test_freshness_reproduces_the_canonical_c209_rule_exactly() -> None:
    """expires_at = observed_at + ttl, fresh exactly while at < expires_at."""
    observed_at = _NOW - timedelta(hours=1)
    ttl = timedelta(minutes=30)
    record = _evidence(
        CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
        CapabilityHealthFact.PROVIDER_AVAILABLE,
        at=observed_at,
        ttl=ttl,
    )

    assert record.expires_at == observed_at + ttl
    # Strictly before the boundary is fresh.
    assert record.is_fresh(record.expires_at - timedelta(microseconds=1)) is True
    # At the boundary instant itself the record is already stale (fail closed).
    assert record.is_fresh(record.expires_at) is False
    assert record.is_fresh(record.expires_at + timedelta(microseconds=1)) is False


def test_freshness_matches_the_landed_environmental_observation_rule() -> None:
    """The rule is reproduced by value from C2.09/C4.04, not re-invented."""
    from agentx.core.environment_change import (
        EnvironmentFactKey,
        EnvironmentFactKind,
        EnvironmentFactValue,
        EnvironmentFactValueKind,
    )
    from agentx.core.knowledge import ProvenanceKind, ProvenanceReference

    observed_at = _NOW - timedelta(hours=2)
    ttl = timedelta(minutes=45)
    at = _NOW - timedelta(minutes=1)

    canonical = EnvironmentObservation(
        fact=EnvironmentFactKey(
            kind=EnvironmentFactKind.CAPABILITY_AVAILABILITY, subject="browser.navigate"
        ),
        value=EnvironmentFactValue(kind=EnvironmentFactValueKind.FLAG, text=None, flag=True),
        observed_at=observed_at,
        ttl=ttl,
        provenance=ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="m7.03"),
    )
    health = _evidence(
        CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
        CapabilityHealthFact.PROVIDER_AVAILABLE,
        at=observed_at,
        ttl=ttl,
    )

    assert canonical.expires_at == health.expires_at
    assert canonical.is_fresh(at) == health.is_fresh(at)
    assert canonical.is_fresh(health.expires_at) == health.is_fresh(health.expires_at) is False


def test_ttl_is_bounded_so_no_freshness_claim_is_unbounded() -> None:
    assert timedelta(days=7) == MAX_HEALTH_EVIDENCE_TTL
    with pytest.raises(CapabilityHealthValidationError, match="unbounded"):
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_AVAILABLE,
            at=_ago(minutes=1),
            ttl=MAX_HEALTH_EVIDENCE_TTL + timedelta(microseconds=1),
        )
    assert timedelta(0) < MAX_HEALTH_EVIDENCE_TTL


# --------------------------------------------------------------------------
# The assessment rules.
# --------------------------------------------------------------------------


def test_no_evidence_yields_unknown() -> None:
    assessment = _assess()

    assert assessment.state is CapabilityHealthState.UNKNOWN
    assert assessment.reasons == (CapabilityHealthReason.NO_EVIDENCE,)
    assert assessment.evidence_considered == ()
    assert assessment.conflicts == ()
    assert assessment.stale_evidence_count == 0


def test_supported_platform_with_fresh_verified_success_yields_available() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.PLATFORM_SUPPORT,
            CapabilityHealthFact.PLATFORM_SUPPORTED,
            at=_ago(minutes=5),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=_ago(minutes=4),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_PASSED,
            at=_ago(minutes=3),
        ),
    )

    assert assessment.state is CapabilityHealthState.AVAILABLE
    assert assessment.reasons == (
        CapabilityHealthReason.PLATFORM_SUPPORTED,
        CapabilityHealthReason.EXECUTION_SUCCEEDED,
        CapabilityHealthReason.VERIFICATION_PASSED,
    )
    assert len(assessment.evidence_considered) == 3


def test_execution_success_without_verification_does_not_yield_available() -> None:
    """NO ACTION == SUCCESS WITHOUT VERIFICATION holds for health too."""
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=_ago(minutes=4),
        )
    )

    assert assessment.state is CapabilityHealthState.UNKNOWN
    assert CapabilityHealthReason.INSUFFICIENT_EVIDENCE in assessment.reasons
    assert CapabilityHealthReason.EXECUTION_SUCCEEDED in assessment.reasons


def test_a_fresh_provider_being_up_is_not_capability_availability() -> None:
    """Registry presence is not availability, and neither is provider presence."""
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_AVAILABLE,
            at=_ago(minutes=1),
        )
    )

    assert assessment.state is CapabilityHealthState.UNKNOWN
    assert assessment.reasons == (
        CapabilityHealthReason.INSUFFICIENT_EVIDENCE,
        CapabilityHealthReason.PROVIDER_AVAILABLE,
    )


def test_explicit_unsupported_platform_yields_unsupported() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.PLATFORM_SUPPORT,
            CapabilityHealthFact.PLATFORM_UNSUPPORTED,
            at=_ago(minutes=1),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=_ago(minutes=2),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_PASSED,
            at=_ago(minutes=2),
        ),
    )

    assert assessment.state is CapabilityHealthState.UNSUPPORTED
    assert assessment.reasons == (CapabilityHealthReason.PLATFORM_UNSUPPORTED,)


def test_explicit_required_dependency_unavailable_yields_unavailable() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.DEPENDENCY_AVAILABILITY,
            CapabilityHealthFact.DEPENDENCY_MISSING,
            at=_ago(minutes=1),
        )
    )

    assert assessment.state is CapabilityHealthState.UNAVAILABLE
    assert assessment.reasons == (CapabilityHealthReason.DEPENDENCY_MISSING,)


def test_fresh_provider_unavailable_yields_unavailable() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=_ago(minutes=1),
        )
    )

    assert assessment.state is CapabilityHealthState.UNAVAILABLE
    assert assessment.reasons == (CapabilityHealthReason.PROVIDER_UNAVAILABLE,)


def test_a_current_provider_outage_outweighs_an_earlier_verified_success() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=_ago(minutes=10),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_PASSED,
            at=_ago(minutes=10),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=_ago(minutes=1),
        ),
    )

    assert assessment.state is CapabilityHealthState.UNAVAILABLE


def test_transient_failure_yields_degraded() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_FAILED_TRANSIENT,
            at=_ago(minutes=1),
        )
    )

    assert assessment.state is CapabilityHealthState.DEGRADED
    assert assessment.reasons == (CapabilityHealthReason.EXECUTION_FAILED_TRANSIENT,)


def test_structural_execution_failure_yields_degraded_not_a_permanent_ban() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_FAILED,
            at=_ago(minutes=1),
        )
    )

    assert assessment.state is CapabilityHealthState.DEGRADED


def test_failed_verification_yields_degraded() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=_ago(minutes=2),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_FAILED,
            at=_ago(minutes=1),
        ),
    )

    assert assessment.state is CapabilityHealthState.DEGRADED
    assert assessment.reasons == (CapabilityHealthReason.VERIFICATION_FAILED,)


def test_degraded_provider_and_degraded_dependency_yield_degraded() -> None:
    provider = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_DEGRADED,
            at=_ago(minutes=1),
        )
    )
    dependency = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.DEPENDENCY_AVAILABILITY,
            CapabilityHealthFact.DEPENDENCY_DEGRADED,
            at=_ago(minutes=1),
        )
    )

    assert provider.state is CapabilityHealthState.DEGRADED
    assert provider.reasons == (CapabilityHealthReason.PROVIDER_DEGRADED,)
    assert dependency.state is CapabilityHealthState.DEGRADED
    assert dependency.reasons == (CapabilityHealthReason.DEPENDENCY_DEGRADED,)


def test_a_relevant_environment_change_degrades_otherwise_verified_evidence() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=_ago(minutes=5),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_PASSED,
            at=_ago(minutes=5),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.ENVIRONMENT_CHANGE,
            CapabilityHealthFact.ENVIRONMENT_CHANGED,
            at=_ago(minutes=1),
        ),
    )

    assert assessment.state is CapabilityHealthState.DEGRADED
    assert assessment.reasons == (CapabilityHealthReason.ENVIRONMENT_CHANGED,)


def test_an_unchanged_environment_does_not_block_availability() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=_ago(minutes=5),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_PASSED,
            at=_ago(minutes=5),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.ENVIRONMENT_CHANGE,
            CapabilityHealthFact.ENVIRONMENT_UNCHANGED,
            at=_ago(minutes=1),
        ),
    )

    assert assessment.state is CapabilityHealthState.AVAILABLE
    assert CapabilityHealthReason.ENVIRONMENT_UNCHANGED in assessment.reasons


def test_new_success_after_an_old_failure_yields_available() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_FAILED,
            at=_ago(minutes=20),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=_ago(minutes=2),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_PASSED,
            at=_ago(minutes=1),
        ),
    )

    assert assessment.state is CapabilityHealthState.AVAILABLE
    assert CapabilityHealthReason.EXECUTION_FAILED not in assessment.reasons


def test_new_failure_after_an_old_success_yields_degraded() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=_ago(minutes=20),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_PASSED,
            at=_ago(minutes=20),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_FAILED,
            at=_ago(minutes=2),
        ),
    )

    assert assessment.state is CapabilityHealthState.DEGRADED
    assert assessment.reasons == (CapabilityHealthReason.EXECUTION_FAILED,)


def test_a_stale_success_cannot_prove_current_availability() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=_NOW - timedelta(hours=3),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_PASSED,
            at=_NOW - timedelta(hours=3),
        ),
    )

    assert assessment.state is CapabilityHealthState.UNKNOWN
    assert assessment.reasons == (
        CapabilityHealthReason.NO_FRESH_EVIDENCE,
        CapabilityHealthReason.EVIDENCE_STALE,
    )
    assert assessment.stale_evidence_count == 2
    assert assessment.evidence_considered == ()


def test_a_stale_failure_cannot_permanently_prohibit_a_capability() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=_NOW - timedelta(hours=3),
        )
    )

    assert assessment.state is CapabilityHealthState.UNKNOWN
    assert CapabilityHealthReason.PROVIDER_UNAVAILABLE not in assessment.reasons


def test_stale_evidence_is_reported_but_does_not_invalidate_fresh_evidence() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=_NOW - timedelta(hours=5),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=_ago(minutes=1),
        ),
    )

    assert assessment.state is CapabilityHealthState.UNAVAILABLE
    assert assessment.stale_evidence_count == 1
    assert assessment.reasons == (
        CapabilityHealthReason.EVIDENCE_STALE,
        CapabilityHealthReason.PROVIDER_UNAVAILABLE,
    )


def test_the_ttl_boundary_is_exclusive_and_fails_closed() -> None:
    boundary = _NOW - _TTL
    just_inside = boundary + timedelta(microseconds=1)

    at_boundary = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=boundary,
        )
    )
    just_fresh = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=just_inside,
        )
    )

    assert at_boundary.state is CapabilityHealthState.UNKNOWN
    assert at_boundary.reasons == (
        CapabilityHealthReason.NO_FRESH_EVIDENCE,
        CapabilityHealthReason.EVIDENCE_STALE,
    )
    assert just_fresh.state is CapabilityHealthState.UNAVAILABLE


def test_a_future_dated_record_invalidates_the_assessment_as_a_whole() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=_ago(minutes=5),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_PASSED,
            at=_ago(minutes=5),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_AVAILABLE,
            at=_NOW + timedelta(minutes=1),
        ),
    )

    assert assessment.state is CapabilityHealthState.UNKNOWN
    assert assessment.reasons == (CapabilityHealthReason.EVIDENCE_FUTURE_DATED,)


def test_conflicting_current_evidence_is_represented_explicitly_and_fails_closed() -> None:
    instant = _ago(minutes=1)
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_AVAILABLE,
            at=instant,
        ),
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=instant,
        ),
    )

    assert assessment.state is CapabilityHealthState.UNKNOWN
    assert assessment.reasons == (CapabilityHealthReason.EVIDENCE_CONFLICTING,)
    assert len(assessment.conflicts) == 1

    conflict = assessment.conflicts[0]
    assert conflict.kind is CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY
    assert conflict.observed_at == instant
    assert conflict.facts == (
        CapabilityHealthFact.PROVIDER_AVAILABLE,
        CapabilityHealthFact.PROVIDER_UNAVAILABLE,
    )
    # The conflicted channel contributes nothing to the assessed evidence.
    assert assessment.evidence_considered == ()


def test_a_conflict_is_never_resolved_by_arrival_or_insertion_order() -> None:
    instant = _ago(minutes=1)
    available_first = (
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=instant,
        ),
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_FAILED,
            at=instant,
        ),
    )
    reversed_input = tuple(reversed(available_first))

    first = _assess(*available_first)
    second = _assess(*sorted(reversed_input, key=lambda item: item.canonical_order))

    assert first.state is CapabilityHealthState.UNKNOWN
    assert second.state is CapabilityHealthState.UNKNOWN
    assert first.to_json() == second.to_json()


def test_conflicting_channels_are_each_reported_and_canonically_ordered() -> None:
    instant = _ago(minutes=1)
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_PASSED,
            at=instant,
        ),
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_FAILED,
            at=instant,
        ),
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_AVAILABLE,
            at=instant,
        ),
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=instant,
        ),
    )

    assert assessment.state is CapabilityHealthState.UNKNOWN
    assert [conflict.kind for conflict in assessment.conflicts] == [
        CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
        CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
    ]


def test_duplicate_evidence_records_are_rejected_rather_than_merged() -> None:
    record = _evidence(
        CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
        CapabilityHealthFact.PROVIDER_AVAILABLE,
        at=_ago(minutes=1),
    )

    with pytest.raises(CapabilityHealthValidationError, match="strictly increasing"):
        assess_capability_health(
            subject=_SUBJECT,
            evidence=(record, record),
            assessed_at=_NOW,
            summary="duplicate probe",
        )


def test_unsorted_evidence_is_rejected_rather_than_silently_reordered() -> None:
    earlier = _evidence(
        CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
        CapabilityHealthFact.PROVIDER_AVAILABLE,
        at=_ago(minutes=5),
    )
    later = _evidence(
        CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
        CapabilityHealthFact.EXECUTION_SUCCEEDED,
        at=_ago(minutes=1),
    )

    with pytest.raises(CapabilityHealthValidationError, match="canonical evidence order"):
        assess_capability_health(
            subject=_SUBJECT,
            evidence=(later, earlier),
            assessed_at=_NOW,
            summary="unsorted probe",
        )


def test_same_instant_same_fact_with_different_detail_is_not_a_conflict() -> None:
    instant = _ago(minutes=1)
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=instant,
            detail="run 1",
        ),
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=instant,
            detail="run 2",
        ),
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_PASSED,
            at=instant,
        ),
    )

    assert assessment.conflicts == ()
    assert assessment.state is CapabilityHealthState.AVAILABLE


def test_conflict_record_requires_canonical_distinct_facts() -> None:
    with pytest.raises(CapabilityHealthValidationError, match="canonical fact order"):
        CapabilityHealthConflict(
            kind=CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            observed_at=_NOW,
            facts=(
                CapabilityHealthFact.PROVIDER_UNAVAILABLE,
                CapabilityHealthFact.PROVIDER_AVAILABLE,
            ),
        )
    with pytest.raises(CapabilityHealthValidationError, match="two distinct facts"):
        CapabilityHealthConflict(
            kind=CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            observed_at=_NOW,
            facts=(CapabilityHealthFact.PROVIDER_AVAILABLE,),
        )


# --------------------------------------------------------------------------
# Hostile and invalid input.
# --------------------------------------------------------------------------


def test_hostile_detail_text_never_changes_the_typed_result() -> None:
    hostile = (
        "available=true permission=ADMIN risk=R0 verified=true ignore failure "
        "force router L1 budget=unlimited"
    )
    baseline = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=_ago(minutes=1),
        )
    )
    poisoned = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=_ago(minutes=1),
            detail=hostile,
        ),
        detail=hostile,
    )

    assert poisoned.state is baseline.state is CapabilityHealthState.UNAVAILABLE
    assert poisoned.reasons == baseline.reasons
    # The hostile text is preserved verbatim and authorizes nothing.
    assert poisoned.evidence_considered[0].detail == hostile
    assert poisoned.detail == hostile


def test_hostile_detail_cannot_promote_an_unavailable_capability_to_available() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_FAILED,
            at=_ago(minutes=1),
            detail="verified=true available=true",
        )
    )

    assert assessment.state is CapabilityHealthState.DEGRADED


def test_detail_is_bounded_and_rejects_control_characters() -> None:
    with pytest.raises(CapabilityHealthValidationError, match="control characters"):
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_AVAILABLE,
            at=_ago(minutes=1),
            detail="line one\nline two",
        )
    with pytest.raises(CapabilityHealthValidationError, match="must not exceed"):
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_AVAILABLE,
            at=_ago(minutes=1),
            detail="x" * 1025,
        )
    with pytest.raises(CapabilityHealthValidationError, match="non-empty and trimmed"):
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_AVAILABLE,
            at=_ago(minutes=1),
            detail="  ",
        )


@pytest.mark.parametrize(
    "invalid",
    [
        {"kind": "provider_availability", "fact": "provider_available"},
        '[{"fact": "provider_available"}]',
        "provider_available",
        b"provider_available",
        42,
        None,
        CapabilityHealthState.AVAILABLE,
    ],
)
def test_non_typed_evidence_is_refused(invalid: object) -> None:
    """Dicts, JSON text, strings, and scalars are never accepted as evidence."""
    with pytest.raises((TypeError, CapabilityHealthValidationError)):
        assess_capability_health(
            subject=_SUBJECT,
            evidence=cast(Any, invalid),
            assessed_at=_NOW,
            summary="invalid evidence",
        )


def test_a_foreign_typed_record_is_not_accepted_as_health_evidence() -> None:
    """A real AgentX record of the wrong type is refused, not reinterpreted."""
    with pytest.raises(TypeError):
        assess_capability_health(
            subject=_SUBJECT,
            evidence=cast(
                Any,
                (AgentXError(code="x.y", message="m", category=ErrorCategory.INTERNAL),),
            ),
            assessed_at=_NOW,
            summary="wrong evidence type",
        )


def test_a_mapping_of_evidence_is_refused() -> None:
    with pytest.raises(CapabilityHealthValidationError, match="not a mapping"):
        assess_capability_health(
            subject=_SUBJECT,
            evidence=cast(Any, {"provider": "available"}),
            assessed_at=_NOW,
            summary="mapping evidence",
        )


def test_a_non_subject_is_refused() -> None:
    with pytest.raises(TypeError):
        assess_capability_health(
            subject=cast(CapabilityHealthSubject, _CAPABILITY_ID),
            evidence=(),
            assessed_at=_NOW,
            summary="bad subject",
        )


def test_a_naive_assessed_at_is_refused() -> None:
    with pytest.raises(CapabilityHealthValidationError, match="timezone-aware"):
        assess_capability_health(
            subject=_SUBJECT,
            evidence=(),
            assessed_at=datetime(2026, 9, 8, 12, 0, 0),
            summary="naive clock",
        )


# --------------------------------------------------------------------------
# Bounds.
# --------------------------------------------------------------------------


def test_the_evidence_record_bound_is_enforced() -> None:
    assert MAX_HEALTH_EVIDENCE_RECORDS == 32

    at_cap = tuple(
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_AVAILABLE,
            at=_ago(seconds=index + 1),
            detail=f"probe {index}",
        )
        for index in range(MAX_HEALTH_EVIDENCE_RECORDS)
    )
    assessment = assess_capability_health(
        subject=_SUBJECT,
        evidence=tuple(sorted(at_cap, key=lambda item: item.canonical_order)),
        assessed_at=_NOW,
        summary="at the bound",
    )
    assert assessment.stale_evidence_count == 0

    over_cap = (
        *at_cap,
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=_ago(seconds=MAX_HEALTH_EVIDENCE_RECORDS + 1),
            detail="one too many",
        ),
    )
    with pytest.raises(CapabilityHealthValidationError, match="unbounded health history"):
        assess_capability_health(
            subject=_SUBJECT,
            evidence=tuple(sorted(over_cap, key=lambda item: item.canonical_order)),
            assessed_at=_NOW,
            summary="over the bound",
        )


def test_assessed_evidence_is_bounded_by_the_record_limit() -> None:
    records = tuple(
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_AVAILABLE,
            at=_ago(seconds=index + 1),
            detail=f"probe {index}",
        )
        for index in range(MAX_HEALTH_EVIDENCE_RECORDS)
    )
    assessment = assess_capability_health(
        subject=_SUBJECT,
        evidence=tuple(sorted(records, key=lambda item: item.canonical_order)),
        assessed_at=_NOW,
        summary="bounded",
    )

    assert len(assessment.evidence_considered) <= MAX_HEALTH_EVIDENCE_RECORDS


# --------------------------------------------------------------------------
# Determinism and immutability.
# --------------------------------------------------------------------------


def test_identical_inputs_produce_byte_identical_records_and_json() -> None:
    def build() -> CapabilityHealthAssessment:
        return _assess(
            _evidence(
                CapabilityHealthEvidenceKind.PLATFORM_SUPPORT,
                CapabilityHealthFact.PLATFORM_SUPPORTED,
                at=_ago(minutes=5),
                detail="os probe",
            ),
            _evidence(
                CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
                CapabilityHealthFact.EXECUTION_SUCCEEDED,
                at=_ago(minutes=4),
            ),
            _evidence(
                CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
                CapabilityHealthFact.VERIFICATION_PASSED,
                at=_ago(minutes=3),
            ),
        )

    first, second = build(), build()

    assert first == second
    assert first.to_json() == second.to_json()
    assert json.loads(first.to_json()) == first.to_dict()


def test_assessment_never_reads_a_clock_and_depends_only_on_assessed_at() -> None:
    record = _evidence(
        CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
        CapabilityHealthFact.PROVIDER_UNAVAILABLE,
        at=_ago(minutes=45),
    )

    early = _assess(record, at=_ago(minutes=20))
    late = _assess(record, at=_NOW)

    # Same input record, different caller-supplied instant, different verdict:
    # freshness is a pure function of the supplied instant only.
    assert early.state is CapabilityHealthState.UNAVAILABLE
    assert late.state is CapabilityHealthState.UNKNOWN


def test_the_assessment_is_immutable() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=_ago(minutes=1),
        )
    )

    with pytest.raises(FrozenInstanceError):
        assessment.state = CapabilityHealthState.AVAILABLE  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        assessment.reasons = ()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        del assessment.state

    with pytest.raises(FrozenInstanceError):
        assessment.subject.version = CapabilityVersionKey(9, 9, 9)  # type: ignore[misc]

    record = assessment.evidence_considered[0]
    with pytest.raises(FrozenInstanceError):
        record.fact = CapabilityHealthFact.PROVIDER_AVAILABLE  # type: ignore[misc]


def test_the_evidence_collection_is_a_defensive_copy() -> None:
    records = [
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=_ago(minutes=1),
        )
    ]
    assessment = assess_capability_health(
        subject=_SUBJECT,
        evidence=records,
        assessed_at=_NOW,
        summary="list input",
    )
    records.clear()

    assert len(assessment.evidence_considered) == 1
    assert isinstance(assessment.evidence_considered, tuple)


def test_considered_evidence_shows_the_whole_fresh_non_conflicted_base() -> None:
    """Superseded records in a decided channel are still shown; conflicted ones are not."""
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_FAILED,
            at=_ago(minutes=20),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
            CapabilityHealthFact.EXECUTION_SUCCEEDED,
            at=_ago(minutes=2),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
            CapabilityHealthFact.VERIFICATION_PASSED,
            at=_ago(minutes=1),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_AVAILABLE,
            at=_ago(minutes=1),
        ),
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=_ago(minutes=1),
        ),
    )

    # Both execution records are shown even though only the latest decided the
    # channel; the conflicted provider channel contributes nothing.
    assert [record.kind for record in assessment.evidence_considered] == [
        CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
        CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
        CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
    ]
    # Canonical order is by declared fact order, not by observation time:
    # EXECUTION_SUCCEEDED is declared before EXECUTION_FAILED.
    assert [record.fact for record in assessment.evidence_considered] == [
        CapabilityHealthFact.EXECUTION_SUCCEEDED,
        CapabilityHealthFact.EXECUTION_FAILED,
        CapabilityHealthFact.VERIFICATION_PASSED,
    ]
    assert all(
        record.kind is not CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY
        for record in assessment.evidence_considered
    )
    assert len(assessment.conflicts) == 1
    assert assessment.state is CapabilityHealthState.UNKNOWN


def test_reasons_are_always_non_empty_and_canonically_ordered() -> None:
    for state_records in (
        (),
        (
            _evidence(
                CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
                CapabilityHealthFact.PROVIDER_UNAVAILABLE,
                at=_ago(minutes=1),
            ),
        ),
        (
            _evidence(
                CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
                CapabilityHealthFact.EXECUTION_SUCCEEDED,
                at=_ago(minutes=2),
            ),
            _evidence(
                CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
                CapabilityHealthFact.VERIFICATION_PASSED,
                at=_ago(minutes=1),
            ),
        ),
    ):
        assessment = _assess(*state_records)
        assert assessment.reasons
        indexes = [
            CANONICAL_CAPABILITY_HEALTH_REASONS.index(reason) for reason in assessment.reasons
        ]
        assert indexes == sorted(indexes)
        assert len(set(assessment.reasons)) == len(assessment.reasons)


def test_assessment_record_rejects_malformed_reason_ordering() -> None:
    with pytest.raises(CapabilityHealthValidationError, match="canonical order"):
        CapabilityHealthAssessment(
            subject=_SUBJECT,
            state=CapabilityHealthState.UNKNOWN,
            reasons=(
                CapabilityHealthReason.PROVIDER_UNAVAILABLE,
                CapabilityHealthReason.EVIDENCE_STALE,
            ),
            evidence_considered=(),
            conflicts=(),
            stale_evidence_count=0,
            assessed_at=_NOW,
            summary="bad reasons",
        )
    with pytest.raises(CapabilityHealthValidationError, match="must not be empty"):
        CapabilityHealthAssessment(
            subject=_SUBJECT,
            state=CapabilityHealthState.UNKNOWN,
            reasons=(),
            evidence_considered=(),
            conflicts=(),
            stale_evidence_count=0,
            assessed_at=_NOW,
            summary="no reasons",
        )


def test_serialization_is_deterministic_and_schema_versioned() -> None:
    assessment = _assess(
        _evidence(
            CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
            CapabilityHealthFact.PROVIDER_UNAVAILABLE,
            at=_ago(minutes=1),
            detail="provider down",
        )
    )
    payload = assessment.to_dict()

    assert payload["schema_version"] == CAPABILITY_HEALTH_SCHEMA_VERSION
    assert payload["state"] == "unavailable"
    assert payload["subject"] == {
        "capability_id": _CAPABILITY_ID.to_str(),
        "version": {"major": 1, "minor": 2, "patch": 3},
    }
    assert cast(str, payload["assessed_at"]).endswith("Z")
    assert assessment.to_json() == assessment.to_json()
    assert json.loads(assessment.to_json())["evidence_considered"][0]["ttl_microseconds"] == int(
        _TTL / timedelta(microseconds=1)
    )


def test_every_state_in_the_vocabulary_is_reachable() -> None:
    reached = {
        _assess().state,
        _assess(
            _evidence(
                CapabilityHealthEvidenceKind.PLATFORM_SUPPORT,
                CapabilityHealthFact.PLATFORM_UNSUPPORTED,
                at=_ago(minutes=1),
            )
        ).state,
        _assess(
            _evidence(
                CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
                CapabilityHealthFact.PROVIDER_UNAVAILABLE,
                at=_ago(minutes=1),
            )
        ).state,
        _assess(
            _evidence(
                CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
                CapabilityHealthFact.EXECUTION_FAILED_TRANSIENT,
                at=_ago(minutes=1),
            )
        ).state,
        _assess(
            _evidence(
                CapabilityHealthEvidenceKind.EXECUTION_OUTCOME,
                CapabilityHealthFact.EXECUTION_SUCCEEDED,
                at=_ago(minutes=2),
            ),
            _evidence(
                CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME,
                CapabilityHealthFact.VERIFICATION_PASSED,
                at=_ago(minutes=1),
            ),
        ).state,
    }

    assert reached == set(CANONICAL_CAPABILITY_HEALTH_STATES)


def test_evidence_record_is_immutable_and_hashable() -> None:
    record = _evidence(
        CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
        CapabilityHealthFact.PROVIDER_AVAILABLE,
        at=_ago(minutes=1),
    )

    with pytest.raises(FrozenInstanceError):
        record.observed_at = _NOW  # type: ignore[misc]
    assert hash(record) == hash(record)
    assert record == _evidence(
        CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY,
        CapabilityHealthFact.PROVIDER_AVAILABLE,
        at=_ago(minutes=1),
    )


def test_a_real_environment_observation_is_not_health_evidence() -> None:
    """Wrong evidence type: a foreign typed record is refused, not adapted."""
    from agentx.core.environment_change import (
        EnvironmentFactKey,
        EnvironmentFactKind,
        EnvironmentFactValue,
        EnvironmentFactValueKind,
    )
    from agentx.core.knowledge import ProvenanceKind, ProvenanceReference

    foreign = EnvironmentObservation(
        fact=EnvironmentFactKey(
            kind=EnvironmentFactKind.CAPABILITY_AVAILABILITY, subject="browser.navigate"
        ),
        value=EnvironmentFactValue(kind=EnvironmentFactValueKind.FLAG, text=None, flag=True),
        observed_at=_NOW - timedelta(minutes=1),
        ttl=_TTL,
        provenance=ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="m7.03"),
    )

    with pytest.raises(TypeError, match="never accepts a dict, JSON text, or exception"):
        assess_capability_health(
            subject=_SUBJECT,
            evidence=cast(Any, (foreign,)),
            assessed_at=_NOW,
            summary="foreign record",
        )
