"""Unit tests for the C4.04 canonical environment-change detection contract."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from agentx.core.environment_change import (
    CANONICAL_ENVIRONMENT_CHANGE_REASONS,
    CANONICAL_ENVIRONMENT_CHANGE_RESULTS,
    CANONICAL_ENVIRONMENT_FACT_ANCHORS,
    CANONICAL_ENVIRONMENT_FACT_KINDS,
    CANONICAL_ENVIRONMENT_FACT_VALUE_KINDS,
    ENVIRONMENT_CHANGE_DETECTION_SCHEMA_VERSION,
    EnvironmentChangeDeserializationError,
    EnvironmentChangeDetection,
    EnvironmentChangeReason,
    EnvironmentChangeResult,
    EnvironmentChangeValidationError,
    EnvironmentFactChange,
    EnvironmentFactKey,
    EnvironmentFactKind,
    EnvironmentFactValue,
    EnvironmentFactValueKind,
    EnvironmentObservation,
    EnvironmentSnapshot,
    UnsupportedEnvironmentChangeSchemaVersionError,
    detect_environment_change,
)
from agentx.core.failure_diagnosis import (
    DiagnosticEvidence,
    DiagnosticEvidenceKind,
    FailureDiagnosis,
    package_diagnosis,
)
from agentx.core.failure_localization import FailureLocalization, FailureLocationKind
from agentx.core.failure_taxonomy import FailureCategory, FailureClassification
from agentx.core.ids import ProcedureId
from agentx.core.knowledge import (
    KnowledgeScope,
    ProvenanceKind,
    ProvenanceReference,
    ScopeDimension,
)
from agentx.core.provenance import EvidenceKind, EvidenceReference
from agentx.hive.environmental_cache import EnvironmentalCacheEntry

_NOW = datetime(2026, 9, 5, 12, 30, 15, 123456, tzinfo=UTC)
_TTL = timedelta(hours=6)
_LATER = _NOW + timedelta(minutes=30)
_COMPARE_AT = _NOW + timedelta(hours=1)

_SCOPE = KnowledgeScope(
    dimensions={
        ScopeDimension.OPERATING_SYSTEM: "windows",
        ScopeDimension.ENVIRONMENT: "workstation-a",
        ScopeDimension.APPLICATION: "contoso-inventory",
    }
)

_EXPECTED_FACT_KIND_VALUES = (
    "platform_identity",
    "application_identity",
    "application_version",
    "capability_availability",
    "capability_version",
    "api_schema",
    "ui_structure",
    "dependency_availability",
    "context_configuration",
)

_EXPECTED_VALUE_KIND_VALUES = ("text", "boolean", "absent")

_EXPECTED_RESULT_VALUES = (
    "insufficient_evidence",
    "relevant_change_detected",
    "no_relevant_change",
)

_EXPECTED_DETECTION_FIELDS = {
    "schema_version",
    "diagnosis",
    "result",
    "reason_codes",
    "scope",
    "baseline",
    "current",
    "changed_facts",
    "unchanged_facts",
    "compared_at",
    "summary",
    "detail",
}


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------
def _classification(**overrides: object) -> FailureClassification:
    payload: dict[str, object] = {
        "category": FailureCategory.ENVIRONMENT,
        "summary": "Observed an environment-class failure.",
        "classified_at": _NOW,
    }
    payload.update(overrides)
    return FailureClassification(**payload)  # type: ignore[arg-type]


def _localization(**overrides: object) -> FailureLocalization:
    payload: dict[str, object] = {
        "kind": FailureLocationKind.PROCEDURE_NODE,
        "summary": "Localized to a procedure node.",
        "localized_at": _NOW,
        "procedure_id": ProcedureId.create(),
        "procedure_node_id": "node-open-report",
    }
    payload.update(overrides)
    return FailureLocalization(**payload)  # type: ignore[arg-type]


def _diagnosis(**overrides: object) -> FailureDiagnosis:
    payload: dict[str, object] = {
        "classification": _classification(),
        "localization": _localization(),
        "summary": "Diagnosed the localized node failure.",
        "diagnosed_at": _NOW,
        "evidence": (
            DiagnosticEvidence(
                kind=DiagnosticEvidenceKind.CHAIN_CORRELATION, correlation_id=uuid4()
            ),
        ),
    }
    payload.update(overrides)
    return package_diagnosis(**payload)  # type: ignore[arg-type]


def _evidence(reference: str) -> EvidenceReference:
    return EvidenceReference(
        kind=EvidenceKind.OBSERVATION,
        reference=reference,
        provenance=ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="env-sensor"),
        observed_at=_NOW,
    )


def _text(value: str) -> EnvironmentFactValue:
    return EnvironmentFactValue(kind=EnvironmentFactValueKind.TEXT, text=value)


def _flag(value: bool) -> EnvironmentFactValue:
    return EnvironmentFactValue(kind=EnvironmentFactValueKind.FLAG, flag=value)


_ABSENT = EnvironmentFactValue(kind=EnvironmentFactValueKind.ABSENT)


def _fact(kind: EnvironmentFactKind, subject: str) -> EnvironmentFactKey:
    return EnvironmentFactKey(kind=kind, subject=subject)


def _observation(
    kind: EnvironmentFactKind,
    subject: str,
    value: EnvironmentFactValue,
    *,
    observed_at: datetime = _NOW,
    ttl: timedelta = _TTL,
) -> EnvironmentObservation:
    return EnvironmentObservation(
        fact=_fact(kind, subject),
        value=value,
        observed_at=observed_at,
        ttl=ttl,
        provenance=ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="env-sensor"),
    )


def _snapshot(
    reference: str,
    observations: tuple[EnvironmentObservation, ...] = (),
    *,
    scope: KnowledgeScope = _SCOPE,
) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        scope=scope, evidence=_evidence(reference), observations=observations
    )


def _detect(
    *,
    baseline: EnvironmentSnapshot | None,
    current: EnvironmentSnapshot | None,
    scope: KnowledgeScope = _SCOPE,
    compared_at: datetime = _COMPARE_AT,
    **overrides: object,
) -> EnvironmentChangeDetection:
    payload: dict[str, object] = {
        "diagnosis": _diagnosis(),
        "scope": scope,
        "baseline": baseline,
        "current": current,
        "compared_at": compared_at,
        "summary": "Compared the two environment snapshots.",
    }
    payload.update(overrides)
    return detect_environment_change(**payload)  # type: ignore[arg-type]


_VERSION = EnvironmentFactKind.APPLICATION_VERSION
_AVAILABILITY = EnvironmentFactKind.CAPABILITY_AVAILABILITY


# --------------------------------------------------------------------------
# canonical vocabularies
# --------------------------------------------------------------------------
def test_canonical_vocabularies_are_closed_and_stable() -> None:
    assert tuple(item.value for item in CANONICAL_ENVIRONMENT_FACT_KINDS) == (
        _EXPECTED_FACT_KIND_VALUES
    )
    assert tuple(item.value for item in CANONICAL_ENVIRONMENT_FACT_VALUE_KINDS) == (
        _EXPECTED_VALUE_KIND_VALUES
    )
    assert tuple(item.value for item in CANONICAL_ENVIRONMENT_CHANGE_RESULTS) == (
        _EXPECTED_RESULT_VALUES
    )
    assert tuple(item.value for item in CANONICAL_ENVIRONMENT_CHANGE_REASONS) == (
        "baseline_missing",
        "current_missing",
        "scope_mismatch",
        "no_facts_observed",
        "conflicting_observations",
        "future_observation",
        "unordered_observations",
        "stale_baseline_observation",
        "stale_current_observation",
        "fact_unobserved_in_baseline",
        "fact_unobserved_in_current",
        "fact_value_changed",
        "all_compared_facts_unchanged",
    )
    assert ENVIRONMENT_CHANGE_DETECTION_SCHEMA_VERSION == 1


def test_result_vocabulary_has_exactly_three_members() -> None:
    assert len(CANONICAL_ENVIRONMENT_CHANGE_RESULTS) == 3
    assert EnvironmentChangeResult.INSUFFICIENT_EVIDENCE in set(
        CANONICAL_ENVIRONMENT_CHANGE_RESULTS
    )


def test_every_fact_kind_is_anchored_to_a_landed_canonical_vocabulary() -> None:
    """C4.04 indexes existing representations; it invents no world model."""
    assert set(CANONICAL_ENVIRONMENT_FACT_ANCHORS) == set(CANONICAL_ENVIRONMENT_FACT_KINDS)

    canonical_names = (
        {dimension.value for dimension in ScopeDimension}
        | {category.value for category in FailureCategory}
        | {kind.value for kind in FailureLocationKind}
    )
    for kind, anchors in CANONICAL_ENVIRONMENT_FACT_ANCHORS.items():
        assert anchors, kind
        for anchor in anchors:
            assert anchor in canonical_names, (kind, anchor)

    assert CANONICAL_ENVIRONMENT_FACT_ANCHORS[EnvironmentFactKind.APPLICATION_VERSION] == (
        ScopeDimension.APPLICATION_VERSION.value,
    )
    assert (
        ScopeDimension.APPLICATION.value
        in CANONICAL_ENVIRONMENT_FACT_ANCHORS[EnvironmentFactKind.APPLICATION_IDENTITY]
    )
    assert (
        FailureCategory.UI_CHANGE.value
        in CANONICAL_ENVIRONMENT_FACT_ANCHORS[EnvironmentFactKind.UI_STRUCTURE]
    )


def test_environment_scope_reuses_the_canonical_c202_scope() -> None:
    snapshot = _snapshot("obs-1", (_observation(_VERSION, "contoso-inventory", _text("3.1.0")),))
    assert isinstance(snapshot.scope, KnowledgeScope)
    assert snapshot.scope.value_for(ScopeDimension.OPERATING_SYSTEM) == "windows"
    assert snapshot.scope.to_dict() == _SCOPE.to_dict()


# --------------------------------------------------------------------------
# fact keys and typed values
# --------------------------------------------------------------------------
def test_fact_key_equality_is_kind_and_subject() -> None:
    assert _fact(_VERSION, "contoso-inventory") == _fact(_VERSION, "contoso-inventory")
    assert _fact(_VERSION, "contoso-inventory") != _fact(
        EnvironmentFactKind.APPLICATION_IDENTITY, "contoso-inventory"
    )
    assert _fact(_VERSION, "contoso-inventory") != _fact(_VERSION, "contoso-reports")


@pytest.mark.parametrize("subject", ["", "  ", " padded ", "a\nb", "a\x7fb", 7, None])
def test_fact_key_rejects_malformed_subjects(subject: object) -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        _fact(_VERSION, subject)  # type: ignore[arg-type]


def test_fact_key_rejects_a_non_typed_kind() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentFactKey(kind="application_version", subject="contoso-inventory")  # type: ignore[arg-type]


def test_text_value_requires_text_and_no_flag() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentFactValue(kind=EnvironmentFactValueKind.TEXT)
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentFactValue(kind=EnvironmentFactValueKind.TEXT, text="3.1.0", flag=True)


def test_flag_value_requires_a_real_bool() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentFactValue(kind=EnvironmentFactValueKind.FLAG)
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentFactValue(kind=EnvironmentFactValueKind.FLAG, flag="true")  # type: ignore[arg-type]
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentFactValue(kind=EnvironmentFactValueKind.FLAG, flag=1)  # type: ignore[arg-type]


def test_absent_value_carries_no_payload() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentFactValue(kind=EnvironmentFactValueKind.ABSENT, text="3.1.0")
    assert _ABSENT.is_absent is True
    assert _text("3.1.0").is_absent is False


def test_comparison_is_type_aware_and_never_coerces() -> None:
    assert _text("true") != _flag(True)
    assert _text("1") != _flag(True)
    assert _text("absent") != _ABSENT
    assert _text("3.1.0") == _text("3.1.0")
    assert _flag(True) != _flag(False)


def test_text_comparison_is_exact_with_no_normalization() -> None:
    """No case folding and no version parsing: distinct text differs."""
    assert _text("3.10.0") != _text("3.9.0")
    assert _text("Windows") != _text("windows")


def test_fact_key_is_immutable() -> None:
    key = _fact(_VERSION, "contoso-inventory")
    with pytest.raises(FrozenInstanceError):
        key.subject = "other"  # type: ignore[misc]


# --------------------------------------------------------------------------
# observations and the canonical C2.09 freshness rule
# --------------------------------------------------------------------------
def test_observation_rejects_non_positive_or_non_timedelta_ttl() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        _observation(_VERSION, "contoso-inventory", _text("3.1.0"), ttl=timedelta(0))
    with pytest.raises(EnvironmentChangeValidationError):
        _observation(_VERSION, "contoso-inventory", _text("3.1.0"), ttl=timedelta(hours=-1))
    with pytest.raises(EnvironmentChangeValidationError):
        _observation(_VERSION, "contoso-inventory", _text("3.1.0"), ttl=3600)  # type: ignore[arg-type]


def test_observation_rejects_naive_timestamps() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        _observation(
            _VERSION,
            "contoso-inventory",
            _text("3.1.0"),
            observed_at=datetime(2026, 9, 5, 12, 0, 0),
        )


def test_observation_requires_a_canonical_provenance_reference() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentObservation(
            fact=_fact(_VERSION, "contoso-inventory"),
            value=_text("3.1.0"),
            observed_at=_NOW,
            ttl=_TTL,
            provenance={"kind": "system", "reference": "env-sensor"},  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(hours=-1), True),
        (timedelta(hours=5, minutes=59, seconds=59), True),
        (timedelta(hours=6), False),
        (timedelta(hours=7), False),
    ],
)
def test_freshness_matches_the_canonical_c209_rule_exactly(
    offset: timedelta, expected: bool
) -> None:
    """C4.04 reproduces C2.09's freshness contract instead of inventing one."""
    observation = _observation(_VERSION, "contoso-inventory", _text("3.1.0"))
    moment = _NOW + offset

    cache_entry = EnvironmentalCacheEntry(
        key="application_version:contoso-inventory",
        value="3.1.0",
        observed_at=_NOW,
        ttl=_TTL,
    )

    assert observation.expires_at == cache_entry.expires_at
    assert observation.is_fresh(moment) is expected
    assert observation.is_fresh(moment) == cache_entry.is_fresh(moment)


def test_snapshot_requires_canonical_scope_and_evidence() -> None:
    observation = _observation(_VERSION, "contoso-inventory", _text("3.1.0"))
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentSnapshot(
            scope={"environment": "workstation-a"},  # type: ignore[arg-type]
            evidence=_evidence("obs-1"),
            observations=(observation,),
        )
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentSnapshot(
            scope=_SCOPE,
            evidence="obs-1",  # type: ignore[arg-type]
            observations=(observation,),
        )
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentSnapshot(scope=_SCOPE, evidence=_evidence("obs-1"), observations=[observation])  # type: ignore[arg-type]


def test_snapshot_may_be_empty_because_absence_is_not_change() -> None:
    assert _snapshot("obs-empty").observations == ()


# --------------------------------------------------------------------------
# detection: the three-way output
# --------------------------------------------------------------------------
def test_same_environment_reports_no_relevant_change() -> None:
    baseline = _snapshot(
        "obs-prior",
        (
            _observation(_VERSION, "contoso-inventory", _text("3.1.0")),
            _observation(_AVAILABILITY, "browser.dom.select", _flag(True)),
        ),
    )
    current = _snapshot(
        "obs-current",
        (
            _observation(_VERSION, "contoso-inventory", _text("3.1.0"), observed_at=_LATER),
            _observation(_AVAILABILITY, "browser.dom.select", _flag(True), observed_at=_LATER),
        ),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.result is EnvironmentChangeResult.NO_RELEVANT_CHANGE
    assert record.reason_codes == (EnvironmentChangeReason.ALL_COMPARED_FACTS_UNCHANGED,)
    assert record.changed_facts == ()
    assert [item.subject for item in record.unchanged_facts] == [
        "contoso-inventory",
        "browser.dom.select",
    ]
    assert record.has_relevant_change is False


def test_changed_application_version_is_detected() -> None:
    baseline = _snapshot(
        "obs-prior", (_observation(_VERSION, "contoso-inventory", _text("3.1.0")),)
    )
    current = _snapshot(
        "obs-current",
        (_observation(_VERSION, "contoso-inventory", _text("3.2.0"), observed_at=_LATER),),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.result is EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED
    assert record.reason_codes == (EnvironmentChangeReason.FACT_VALUE_CHANGED,)
    assert record.has_relevant_change is True
    assert len(record.changed_facts) == 1
    change = record.changed_facts[0]
    assert change.fact == _fact(_VERSION, "contoso-inventory")
    assert change.baseline_value == _text("3.1.0")
    assert change.current_value == _text("3.2.0")
    assert change.baseline_observed_at == _NOW
    assert change.current_observed_at == _LATER


def test_changed_availability_is_detected() -> None:
    baseline = _snapshot(
        "obs-prior", (_observation(_AVAILABILITY, "browser.dom.select", _flag(True)),)
    )
    current = _snapshot(
        "obs-current",
        (_observation(_AVAILABILITY, "browser.dom.select", _flag(False), observed_at=_LATER),),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.result is EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED
    assert record.changed_facts[0].baseline_value == _flag(True)
    assert record.changed_facts[0].current_value == _flag(False)


def test_explicit_absence_difference_is_a_change() -> None:
    """An explicitly observed absence is meaningful; a missing observation is not."""
    baseline = _snapshot("obs-prior", (_observation(_AVAILABILITY, "edge.webview", _flag(True)),))
    current = _snapshot(
        "obs-current",
        (_observation(_AVAILABILITY, "edge.webview", _ABSENT, observed_at=_LATER),),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.result is EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED
    assert record.changed_facts[0].current_value.is_absent is True


def test_type_change_on_the_same_fact_is_a_change_not_a_coercion() -> None:
    baseline = _snapshot("obs-prior", (_observation(_AVAILABILITY, "edge.webview", _text("true")),))
    current = _snapshot(
        "obs-current",
        (_observation(_AVAILABILITY, "edge.webview", _flag(True), observed_at=_LATER),),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.result is EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED


def test_partial_observations_are_insufficient_evidence_not_a_change() -> None:
    baseline = _snapshot(
        "obs-prior",
        (
            _observation(_VERSION, "contoso-inventory", _text("3.1.0")),
            _observation(_AVAILABILITY, "browser.dom.select", _flag(True)),
        ),
    )
    current = _snapshot(
        "obs-current",
        (_observation(_VERSION, "contoso-inventory", _text("3.1.0"), observed_at=_LATER),),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.result is EnvironmentChangeResult.INSUFFICIENT_EVIDENCE
    assert record.reason_codes == (EnvironmentChangeReason.FACT_UNOBSERVED_IN_CURRENT,)
    assert record.changed_facts == ()
    assert record.unchanged_facts == (_fact(_VERSION, "contoso-inventory"),)
    assert record.is_insufficient_evidence is True


def test_new_coverage_on_the_current_side_is_not_a_change() -> None:
    baseline = _snapshot(
        "obs-prior", (_observation(_VERSION, "contoso-inventory", _text("3.1.0")),)
    )
    current = _snapshot(
        "obs-current",
        (
            _observation(_VERSION, "contoso-inventory", _text("3.1.0"), observed_at=_LATER),
            _observation(_AVAILABILITY, "browser.dom.select", _flag(True), observed_at=_LATER),
        ),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.result is EnvironmentChangeResult.INSUFFICIENT_EVIDENCE
    assert record.reason_codes == (EnvironmentChangeReason.FACT_UNOBSERVED_IN_BASELINE,)
    assert record.changed_facts == ()


def test_missing_baseline_is_insufficient_evidence() -> None:
    current = _snapshot(
        "obs-current", (_observation(_VERSION, "contoso-inventory", _text("3.1.0")),)
    )

    record = _detect(baseline=None, current=current)

    assert record.result is EnvironmentChangeResult.INSUFFICIENT_EVIDENCE
    assert record.reason_codes == (EnvironmentChangeReason.BASELINE_MISSING,)
    assert record.baseline is None
    assert record.baseline_evidence is None
    assert record.current_evidence is not None
    assert record.changed_facts == ()
    assert record.unchanged_facts == ()


def test_missing_current_is_insufficient_evidence() -> None:
    baseline = _snapshot(
        "obs-prior", (_observation(_VERSION, "contoso-inventory", _text("3.1.0")),)
    )

    record = _detect(baseline=baseline, current=None)

    assert record.reason_codes == (EnvironmentChangeReason.CURRENT_MISSING,)
    assert record.current_evidence is None


def test_both_sides_missing_reports_both_reasons_in_canonical_order() -> None:
    record = _detect(baseline=None, current=None)

    assert record.result is EnvironmentChangeResult.INSUFFICIENT_EVIDENCE
    assert record.reason_codes == (
        EnvironmentChangeReason.BASELINE_MISSING,
        EnvironmentChangeReason.CURRENT_MISSING,
    )


def test_stale_baseline_is_insufficient_evidence_even_when_values_differ() -> None:
    baseline = _snapshot(
        "obs-prior",
        (
            _observation(
                _VERSION,
                "contoso-inventory",
                _text("3.1.0"),
                observed_at=_NOW - timedelta(hours=12),
            ),
        ),
    )
    current = _snapshot(
        "obs-current", (_observation(_VERSION, "contoso-inventory", _text("3.2.0")),)
    )

    record = _detect(baseline=baseline, current=current)

    assert record.result is EnvironmentChangeResult.INSUFFICIENT_EVIDENCE
    assert record.reason_codes == (EnvironmentChangeReason.STALE_BASELINE_OBSERVATION,)
    assert record.changed_facts == ()


def test_stale_current_is_insufficient_evidence() -> None:
    baseline = _snapshot(
        "obs-prior", (_observation(_VERSION, "contoso-inventory", _text("3.1.0")),)
    )
    current = _snapshot(
        "obs-current",
        (
            _observation(
                _VERSION,
                "contoso-inventory",
                _text("3.1.0"),
                observed_at=_LATER,
                ttl=timedelta(minutes=1),
            ),
        ),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.reason_codes == (EnvironmentChangeReason.STALE_CURRENT_OBSERVATION,)


def test_conflicting_observations_invalidate_the_whole_comparison() -> None:
    baseline = _snapshot(
        "obs-prior",
        (
            _observation(_VERSION, "contoso-inventory", _text("3.1.0")),
            _observation(_VERSION, "contoso-inventory", _text("3.2.0")),
        ),
    )
    current = _snapshot(
        "obs-current",
        (_observation(_VERSION, "contoso-inventory", _text("3.2.0"), observed_at=_LATER),),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.result is EnvironmentChangeResult.INSUFFICIENT_EVIDENCE
    assert record.reason_codes == (EnvironmentChangeReason.CONFLICTING_OBSERVATIONS,)
    assert record.changed_facts == ()


def test_duplicated_identical_observations_are_still_conflicting() -> None:
    baseline = _snapshot(
        "obs-prior",
        (
            _observation(_VERSION, "contoso-inventory", _text("3.1.0")),
            _observation(_VERSION, "contoso-inventory", _text("3.1.0")),
        ),
    )
    current = _snapshot(
        "obs-current",
        (_observation(_VERSION, "contoso-inventory", _text("3.1.0"), observed_at=_LATER),),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.reason_codes == (EnvironmentChangeReason.CONFLICTING_OBSERVATIONS,)


def test_future_dated_observation_is_rejected_as_untrustworthy() -> None:
    baseline = _snapshot(
        "obs-prior", (_observation(_VERSION, "contoso-inventory", _text("3.1.0")),)
    )
    current = _snapshot(
        "obs-current",
        (
            _observation(
                _VERSION,
                "contoso-inventory",
                _text("3.2.0"),
                observed_at=_COMPARE_AT + timedelta(hours=1),
            ),
        ),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.reason_codes == (EnvironmentChangeReason.FUTURE_OBSERVATION,)
    assert record.changed_facts == ()


def test_temporally_unordered_snapshots_are_insufficient_evidence() -> None:
    baseline = _snapshot(
        "obs-prior",
        (_observation(_VERSION, "contoso-inventory", _text("3.1.0"), observed_at=_LATER),),
    )
    current = _snapshot(
        "obs-current", (_observation(_VERSION, "contoso-inventory", _text("3.2.0")),)
    )

    record = _detect(baseline=baseline, current=current)

    assert record.reason_codes == (EnvironmentChangeReason.UNORDERED_OBSERVATIONS,)


def test_different_environment_scope_is_never_compared() -> None:
    other_scope = KnowledgeScope(dimensions={ScopeDimension.ENVIRONMENT: "staging"})
    baseline = _snapshot(
        "obs-prior",
        (_observation(_VERSION, "contoso-inventory", _text("3.1.0")),),
        scope=other_scope,
    )
    current = _snapshot(
        "obs-current",
        (_observation(_VERSION, "contoso-inventory", _text("3.2.0"), observed_at=_LATER),),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.result is EnvironmentChangeResult.INSUFFICIENT_EVIDENCE
    assert record.reason_codes == (EnvironmentChangeReason.SCOPE_MISMATCH,)
    assert record.changed_facts == ()
    # the record still shows both sides so the mismatch is auditable
    assert record.baseline is baseline
    assert record.current is current


def test_unscoped_and_scoped_snapshots_are_a_scope_mismatch() -> None:
    baseline = _snapshot(
        "obs-prior",
        (_observation(_VERSION, "contoso-inventory", _text("3.1.0")),),
        scope=KnowledgeScope(),
    )
    current = _snapshot(
        "obs-current",
        (_observation(_VERSION, "contoso-inventory", _text("3.1.0"), observed_at=_LATER),),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.reason_codes == (EnvironmentChangeReason.SCOPE_MISMATCH,)


def test_empty_comparison_is_insufficient_evidence_not_no_change() -> None:
    record = _detect(baseline=_snapshot("obs-prior"), current=_snapshot("obs-current"))

    assert record.result is EnvironmentChangeResult.INSUFFICIENT_EVIDENCE
    assert record.reason_codes == (EnvironmentChangeReason.NO_FACTS_OBSERVED,)
    assert record.unchanged_facts == ()


def test_multiple_changes_and_unchanged_facts_are_reported_in_canonical_order() -> None:
    baseline = _snapshot(
        "obs-prior",
        (
            _observation(
                EnvironmentFactKind.UI_STRUCTURE, "#submit-button", _text("button.primary")
            ),
            _observation(_VERSION, "contoso-inventory", _text("3.1.0")),
            _observation(_AVAILABILITY, "browser.dom.select", _flag(True)),
            _observation(EnvironmentFactKind.API_SCHEMA, "/api/v1/reports", _text("v1")),
        ),
    )
    current = _snapshot(
        "obs-current",
        (
            _observation(
                EnvironmentFactKind.UI_STRUCTURE,
                "#submit-button",
                _text("button.secondary"),
                observed_at=_LATER,
            ),
            _observation(_VERSION, "contoso-inventory", _text("3.2.0"), observed_at=_LATER),
            _observation(_AVAILABILITY, "browser.dom.select", _flag(True), observed_at=_LATER),
            _observation(
                EnvironmentFactKind.API_SCHEMA, "/api/v1/reports", _text("v1"), observed_at=_LATER
            ),
        ),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.result is EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED
    assert [item.fact.kind for item in record.changed_facts] == [
        _VERSION,
        EnvironmentFactKind.UI_STRUCTURE,
    ]
    assert [item.kind for item in record.unchanged_facts] == [
        _AVAILABILITY,
        EnvironmentFactKind.API_SCHEMA,
    ]


def test_detected_change_wins_over_partial_coverage() -> None:
    baseline = _snapshot(
        "obs-prior",
        (
            _observation(_VERSION, "contoso-inventory", _text("3.1.0")),
            _observation(_AVAILABILITY, "browser.dom.select", _flag(True)),
        ),
    )
    current = _snapshot(
        "obs-current",
        (_observation(_VERSION, "contoso-inventory", _text("3.2.0"), observed_at=_LATER),),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.result is EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED
    assert record.reason_codes == (EnvironmentChangeReason.FACT_VALUE_CHANGED,)
    assert record.unchanged_facts == ()


# --------------------------------------------------------------------------
# determinism and provenance
# --------------------------------------------------------------------------
def test_detection_is_deterministic_and_byte_identical() -> None:
    baseline = _snapshot(
        "obs-prior", (_observation(_VERSION, "contoso-inventory", _text("3.1.0")),)
    )
    current = _snapshot(
        "obs-current",
        (_observation(_VERSION, "contoso-inventory", _text("3.2.0"), observed_at=_LATER),),
    )
    diagnosis = _diagnosis()

    first = detect_environment_change(
        diagnosis=diagnosis,
        scope=_SCOPE,
        baseline=baseline,
        current=current,
        compared_at=_COMPARE_AT,
        summary="Compared the two environment snapshots.",
    )
    second = detect_environment_change(
        diagnosis=diagnosis,
        scope=_SCOPE,
        baseline=baseline,
        current=current,
        compared_at=_COMPARE_AT,
        summary="Compared the two environment snapshots.",
    )

    assert first == second
    assert first.to_json() == second.to_json()


def test_embedded_diagnosis_is_preserved_by_value() -> None:
    diagnosis = _diagnosis()
    record = _detect(
        baseline=_snapshot("obs-prior"), current=_snapshot("obs-current"), diagnosis=diagnosis
    )

    assert record.diagnosis == diagnosis
    assert record.diagnosis.to_json() == diagnosis.to_json()
    restored = EnvironmentChangeDetection.from_json(record.to_json())
    assert restored.diagnosis.to_json() == diagnosis.to_json()


def test_prior_and_current_evidence_references_are_carried() -> None:
    baseline = _snapshot(
        "obs-prior", (_observation(_VERSION, "contoso-inventory", _text("3.1.0")),)
    )
    current = _snapshot(
        "obs-current",
        (_observation(_VERSION, "contoso-inventory", _text("3.1.0"), observed_at=_LATER),),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.baseline_evidence == baseline.evidence
    assert record.current_evidence == current.evidence
    assert record.baseline_evidence is not None
    assert record.baseline_evidence.reference == "obs-prior"
    assert record.current_evidence is not None
    assert record.current_evidence.reference == "obs-current"
    assert record.baseline_evidence.kind is EvidenceKind.OBSERVATION


def test_observation_provenance_survives_a_round_trip() -> None:
    baseline = _snapshot(
        "obs-prior",
        (_observation(EnvironmentFactKind.DEPENDENCY_AVAILABILITY, "contoso-api", _flag(True)),),
    )
    current = _snapshot(
        "obs-current",
        (
            _observation(
                EnvironmentFactKind.DEPENDENCY_AVAILABILITY,
                "contoso-api",
                _flag(True),
                observed_at=_LATER,
            ),
        ),
    )

    record = _detect(baseline=baseline, current=current)
    restored = EnvironmentChangeDetection.from_json(record.to_json())

    assert restored == record
    assert restored.baseline is not None
    provenance = restored.baseline.observations[0].provenance
    assert provenance.kind is ProvenanceKind.SYSTEM
    assert provenance.reference == "env-sensor"
    assert restored.baseline.observations[0].ttl == _TTL


# --------------------------------------------------------------------------
# record-level validation
# --------------------------------------------------------------------------
def _record(**overrides: object) -> EnvironmentChangeDetection:
    payload: dict[str, object] = {
        "diagnosis": _diagnosis(),
        "result": EnvironmentChangeResult.NO_RELEVANT_CHANGE,
        "reason_codes": (EnvironmentChangeReason.ALL_COMPARED_FACTS_UNCHANGED,),
        "scope": _SCOPE,
        "baseline": _snapshot(
            "obs-prior", (_observation(_VERSION, "contoso-inventory", _text("3.1.0")),)
        ),
        "current": _snapshot(
            "obs-current",
            (_observation(_VERSION, "contoso-inventory", _text("3.1.0"), observed_at=_LATER),),
        ),
        "changed_facts": (),
        "unchanged_facts": (_fact(_VERSION, "contoso-inventory"),),
        "compared_at": _COMPARE_AT,
        "summary": "Compared the two environment snapshots.",
    }
    payload.update(overrides)
    return EnvironmentChangeDetection(**payload)  # type: ignore[arg-type]


def test_relevant_change_requires_at_least_one_explicit_changed_fact() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        _record(
            result=EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED,
            reason_codes=(EnvironmentChangeReason.FACT_VALUE_CHANGED,),
        )


def test_changed_facts_are_only_representable_with_a_detected_change() -> None:
    change = EnvironmentFactChange(
        fact=_fact(_VERSION, "contoso-inventory"),
        baseline_value=_text("3.1.0"),
        current_value=_text("3.2.0"),
        baseline_observed_at=_NOW,
        current_observed_at=_LATER,
    )
    with pytest.raises(EnvironmentChangeValidationError):
        _record(changed_facts=(change,), unchanged_facts=())


def test_reason_codes_must_be_canonical_ordered_and_non_empty() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        _record(reason_codes=())
    with pytest.raises(EnvironmentChangeValidationError):
        _record(
            reason_codes=(
                EnvironmentChangeReason.ALL_COMPARED_FACTS_UNCHANGED,
                EnvironmentChangeReason.ALL_COMPARED_FACTS_UNCHANGED,
            )
        )
    with pytest.raises(EnvironmentChangeValidationError):
        _record(reason_codes=("all_compared_facts_unchanged",))


def test_scope_mismatch_reason_requires_an_actual_scope_difference() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        _record(
            result=EnvironmentChangeResult.INSUFFICIENT_EVIDENCE,
            reason_codes=(EnvironmentChangeReason.SCOPE_MISMATCH,),
            unchanged_facts=(),
        )


def test_a_fact_cannot_be_both_changed_and_unchanged() -> None:
    change = EnvironmentFactChange(
        fact=_fact(_VERSION, "contoso-inventory"),
        baseline_value=_text("3.1.0"),
        current_value=_text("3.2.0"),
        baseline_observed_at=_NOW,
        current_observed_at=_LATER,
    )
    with pytest.raises(EnvironmentChangeValidationError):
        _record(
            result=EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED,
            reason_codes=(EnvironmentChangeReason.FACT_VALUE_CHANGED,),
            changed_facts=(change,),
            unchanged_facts=(_fact(_VERSION, "contoso-inventory"),),
        )


def test_changed_and_unchanged_facts_must_be_in_canonical_order() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        _record(
            unchanged_facts=(
                _fact(EnvironmentFactKind.UI_STRUCTURE, "#submit-button"),
                _fact(_VERSION, "contoso-inventory"),
            )
        )


def test_fact_change_requires_differing_values_and_ordered_timestamps() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentFactChange(
            fact=_fact(_VERSION, "contoso-inventory"),
            baseline_value=_text("3.1.0"),
            current_value=_text("3.1.0"),
            baseline_observed_at=_NOW,
            current_observed_at=_LATER,
        )
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentFactChange(
            fact=_fact(_VERSION, "contoso-inventory"),
            baseline_value=_text("3.1.0"),
            current_value=_text("3.2.0"),
            baseline_observed_at=_LATER,
            current_observed_at=_NOW,
        )


def test_record_requires_a_typed_canonical_diagnosis() -> None:
    for foreign in (
        {"category": "environment"},
        "environment",
        FailureCategory.ENVIRONMENT,
        None,
    ):
        with pytest.raises(EnvironmentChangeValidationError):
            _record(diagnosis=foreign)


def test_record_rejects_a_foreign_schema_version() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        _record(schema_version=2)


@pytest.mark.parametrize("summary", ["", "  ", " padded ", "line\nbreak", "bell\x07", 7, None])
def test_record_rejects_malformed_summaries(summary: object) -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        _record(summary=summary)


def test_record_is_immutable() -> None:
    record = _record()
    with pytest.raises(FrozenInstanceError):
        record.summary = "rewritten"  # type: ignore[misc]


def test_detect_refuses_non_typed_inputs() -> None:
    with pytest.raises(EnvironmentChangeValidationError):
        detect_environment_change(
            diagnosis={"category": "environment"},  # type: ignore[arg-type]
            scope=_SCOPE,
            baseline=None,
            current=None,
            compared_at=_COMPARE_AT,
            summary="x",
        )
    with pytest.raises(EnvironmentChangeValidationError):
        detect_environment_change(
            diagnosis=_diagnosis(),
            scope={"environment": "workstation-a"},  # type: ignore[arg-type]
            baseline=None,
            current=None,
            compared_at=_COMPARE_AT,
            summary="x",
        )
    with pytest.raises(EnvironmentChangeValidationError):
        detect_environment_change(
            diagnosis=_diagnosis(),
            scope=_SCOPE,
            baseline={"observations": []},  # type: ignore[arg-type]
            current=None,
            compared_at=_COMPARE_AT,
            summary="x",
        )


def test_detect_never_reads_a_clock() -> None:
    """Freshness is evaluated only against the caller-supplied instant."""
    ttl = timedelta(minutes=5)
    baseline = _snapshot(
        "obs-prior",
        (_observation(_VERSION, "contoso-inventory", _text("3.1.0"), ttl=ttl),),
    )
    current = _snapshot(
        "obs-current",
        (
            _observation(
                _VERSION,
                "contoso-inventory",
                _text("3.1.0"),
                observed_at=_NOW + timedelta(minutes=1),
                ttl=ttl,
            ),
        ),
    )

    fresh = _detect(baseline=baseline, current=current, compared_at=_NOW + timedelta(minutes=2))
    stale = _detect(baseline=baseline, current=current, compared_at=_NOW + timedelta(minutes=10))

    assert fresh.result is EnvironmentChangeResult.NO_RELEVANT_CHANGE
    assert stale.result is EnvironmentChangeResult.INSUFFICIENT_EVIDENCE
    assert stale.reason_codes == (
        EnvironmentChangeReason.STALE_BASELINE_OBSERVATION,
        EnvironmentChangeReason.STALE_CURRENT_OBSERVATION,
    )


# --------------------------------------------------------------------------
# serialization
# --------------------------------------------------------------------------
def test_serialized_field_set_is_exact() -> None:
    record = _record()
    payload = record.to_dict()

    assert set(payload) == _EXPECTED_DETECTION_FIELDS
    assert payload["schema_version"] == ENVIRONMENT_CHANGE_DETECTION_SCHEMA_VERSION
    assert payload["result"] == "no_relevant_change"
    assert payload["reason_codes"] == ["all_compared_facts_unchanged"]


def test_json_round_trip_is_lossless_and_stable() -> None:
    record = _record(
        result=EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED,
        reason_codes=(EnvironmentChangeReason.FACT_VALUE_CHANGED,),
        changed_facts=(
            EnvironmentFactChange(
                fact=_fact(_VERSION, "contoso-inventory"),
                baseline_value=_text("3.1.0"),
                current_value=_text("3.2.0"),
                baseline_observed_at=_NOW,
                current_observed_at=_LATER,
            ),
        ),
        unchanged_facts=(),
        detail="The application version moved between the two observations.",
    )
    text = record.to_json()

    restored = EnvironmentChangeDetection.from_json(text)

    assert restored == record
    assert restored.to_json() == text
    assert json.loads(text)["detail"] is not None


def test_decoding_rejects_unknown_and_missing_fields() -> None:
    payload = json.loads(_record().to_json())

    payload_with_extra = dict(payload)
    payload_with_extra["authorized"] = True
    with pytest.raises(EnvironmentChangeDeserializationError):
        EnvironmentChangeDetection.from_dict(payload_with_extra)

    payload_missing = dict(payload)
    del payload_missing["reason_codes"]
    with pytest.raises(EnvironmentChangeDeserializationError):
        EnvironmentChangeDetection.from_dict(payload_missing)


def test_decoding_rejects_unknown_enum_strings_instead_of_downgrading() -> None:
    for field, value in (
        ("result", "no_change_at_all"),
        ("reason_codes", ["because"]),
    ):
        payload = json.loads(_record().to_json())
        payload[field] = value
        with pytest.raises(EnvironmentChangeDeserializationError):
            EnvironmentChangeDetection.from_dict(payload)

    payload = json.loads(_record().to_json())
    payload["baseline"]["observations"][0]["fact"]["kind"] = "mood"
    with pytest.raises(EnvironmentChangeDeserializationError):
        EnvironmentChangeDetection.from_dict(payload)

    payload = json.loads(_record().to_json())
    payload["baseline"]["observations"][0]["value"]["kind"] = "number"
    with pytest.raises(EnvironmentChangeDeserializationError):
        EnvironmentChangeDetection.from_dict(payload)


def test_decoding_rejects_an_unsupported_schema_version() -> None:
    payload = json.loads(_record().to_json())
    payload["schema_version"] = 2

    with pytest.raises(UnsupportedEnvironmentChangeSchemaVersionError):
        EnvironmentChangeDetection.from_dict(payload)


def test_decoding_rejects_malformed_json_and_non_object_roots() -> None:
    with pytest.raises(EnvironmentChangeDeserializationError):
        EnvironmentChangeDetection.from_json("{not json")
    with pytest.raises(EnvironmentChangeDeserializationError):
        EnvironmentChangeDetection.from_json("[1, 2, 3]")
    with pytest.raises(EnvironmentChangeDeserializationError):
        EnvironmentChangeDetection.from_json(7)  # type: ignore[arg-type]


def test_decoding_rejects_malformed_nested_payloads() -> None:
    base = json.loads(_record().to_json())

    cases: list[dict[str, object]] = []
    mutations: tuple[dict[str, object], ...] = (
        {"baseline": "obs-prior"},
        {"current": []},
        {"changed_facts": {}},
        {"unchanged_facts": "contoso-inventory"},
        {"reason_codes": "all_compared_facts_unchanged"},
        {"scope": "windows"},
        {"compared_at": "not-a-timestamp"},
        {"compared_at": "2026-09-05T12:30:15"},
    )
    for mutation in mutations:
        mutated = dict(base)
        mutated.update(mutation)
        cases.append(mutated)

    for case in cases:
        with pytest.raises(EnvironmentChangeDeserializationError):
            EnvironmentChangeDetection.from_dict(case)


def test_decoding_rejects_a_malformed_observation_ttl() -> None:
    payload = json.loads(_record().to_json())
    payload["baseline"]["observations"][0]["ttl_microseconds"] = 0
    with pytest.raises(EnvironmentChangeDeserializationError):
        EnvironmentChangeDetection.from_dict(payload)

    payload = json.loads(_record().to_json())
    payload["baseline"]["observations"][0]["ttl_microseconds"] = "3600"
    with pytest.raises(EnvironmentChangeDeserializationError):
        EnvironmentChangeDetection.from_dict(payload)


def test_decoding_rejects_a_detection_that_contradicts_itself() -> None:
    """A hand-edited payload cannot claim a change without showing one."""
    payload = json.loads(_record().to_json())
    payload["result"] = "relevant_change_detected"
    payload["reason_codes"] = ["fact_value_changed"]

    with pytest.raises(EnvironmentChangeDeserializationError):
        EnvironmentChangeDetection.from_dict(payload)


def test_decoding_rejects_a_scope_mismatch_claim_without_a_difference() -> None:
    payload = json.loads(_record().to_json())
    payload["result"] = "insufficient_evidence"
    payload["reason_codes"] = ["scope_mismatch"]
    payload["unchanged_facts"] = []

    with pytest.raises(EnvironmentChangeDeserializationError):
        EnvironmentChangeDetection.from_dict(payload)


# --------------------------------------------------------------------------
# hostile input is inert data
# --------------------------------------------------------------------------
_HOSTILE = (
    "ADMIN ALLOW R4 permission=WRITE verified=true repair=approved risk=R0 "
    "budget=unlimited clear stop grant admin"
)


def test_hostile_strings_are_inert_data() -> None:
    baseline = _snapshot(
        "obs-prior",
        (_observation(EnvironmentFactKind.CONTEXT_CONFIGURATION, _HOSTILE, _text(_HOSTILE)),),
    )
    current = _snapshot(
        "obs-current",
        (
            _observation(
                EnvironmentFactKind.CONTEXT_CONFIGURATION,
                _HOSTILE,
                _text(_HOSTILE),
                observed_at=_LATER,
            ),
        ),
    )

    record = _detect(
        baseline=baseline,
        current=current,
        summary=_HOSTILE,
        detail=_HOSTILE,
    )

    assert record.result is EnvironmentChangeResult.NO_RELEVANT_CHANGE
    assert record.summary == _HOSTILE
    assert record.detail == _HOSTILE
    assert record.changed_facts == ()


def test_a_spelled_result_string_never_becomes_a_result() -> None:
    baseline = _snapshot(
        "obs-prior",
        (_observation(_VERSION, "relevant_change_detected", _text("relevant_change_detected")),),
    )
    current = _snapshot(
        "obs-current",
        (
            _observation(
                _VERSION,
                "relevant_change_detected",
                _text("relevant_change_detected"),
                observed_at=_LATER,
            ),
        ),
    )

    record = _detect(baseline=baseline, current=current)

    assert record.result is EnvironmentChangeResult.NO_RELEVANT_CHANGE


def test_a_hostile_summary_cannot_promote_the_result() -> None:
    """A C4.01 category or summary text is never consulted by detection."""
    for category in (
        FailureCategory.ENVIRONMENT,
        FailureCategory.UI_CHANGE,
        FailureCategory.API_CHANGE,
        FailureCategory.UNKNOWN,
    ):
        diagnosis = _diagnosis(
            classification=_classification(category=category, summary=_HOSTILE), summary=_HOSTILE
        )
        baseline = _snapshot(
            "obs-prior", (_observation(_VERSION, "contoso-inventory", _text("3.1.0")),)
        )
        current = _snapshot(
            "obs-current",
            (_observation(_VERSION, "contoso-inventory", _text("3.1.0"), observed_at=_LATER),),
        )

        record = detect_environment_change(
            diagnosis=diagnosis,
            scope=_SCOPE,
            baseline=baseline,
            current=current,
            compared_at=_COMPARE_AT,
            summary=_HOSTILE,
        )

        assert record.result is EnvironmentChangeResult.NO_RELEVANT_CHANGE


def test_snapshot_and_value_serialization_is_exact() -> None:
    observation = _observation(_AVAILABILITY, "browser.dom.select", _flag(True))
    snapshot = _snapshot("obs-prior", (observation,))

    assert snapshot.to_dict() == {
        "scope": _SCOPE.to_dict(),
        "evidence": _evidence("obs-prior").to_dict(),
        "observations": [
            {
                "fact": {"kind": "capability_availability", "subject": "browser.dom.select"},
                "value": {"kind": "boolean", "text": None, "flag": True},
                "observed_at": "2026-09-05T12:30:15.123456Z",
                "ttl_microseconds": 21_600_000_000,
                "provenance": {"kind": "system", "reference": "env-sensor"},
            }
        ],
    }
    assert EnvironmentSnapshot.from_dict(snapshot.to_dict()) == snapshot


def test_value_and_key_round_trip() -> None:
    for value in (_text("3.1.0"), _flag(False), _ABSENT):
        assert EnvironmentFactValue.from_dict(value.to_dict()) == value
    key = _fact(EnvironmentFactKind.PLATFORM_IDENTITY, "host-os")
    assert EnvironmentFactKey.from_dict(key.to_dict()) == key


def test_public_surface_is_exactly_the_documented_contract() -> None:
    import agentx.core.environment_change as module

    assert set(module.__all__) == {
        "CANONICAL_ENVIRONMENT_CHANGE_REASONS",
        "CANONICAL_ENVIRONMENT_CHANGE_RESULTS",
        "CANONICAL_ENVIRONMENT_FACT_ANCHORS",
        "CANONICAL_ENVIRONMENT_FACT_KINDS",
        "CANONICAL_ENVIRONMENT_FACT_VALUE_KINDS",
        "ENVIRONMENT_CHANGE_DETECTION_SCHEMA_VERSION",
        "EnvironmentChangeDeserializationError",
        "EnvironmentChangeDetection",
        "EnvironmentChangeReason",
        "EnvironmentChangeResult",
        "EnvironmentChangeValidationError",
        "EnvironmentFactChange",
        "EnvironmentFactKey",
        "EnvironmentFactKind",
        "EnvironmentFactValue",
        "EnvironmentFactValueKind",
        "EnvironmentObservation",
        "EnvironmentSnapshot",
        "UnsupportedEnvironmentChangeSchemaVersionError",
        "detect_environment_change",
    }
    for name in module.__all__:
        assert hasattr(module, name), name


def test_detection_carries_no_authority_or_repair_field() -> None:
    payload = json.loads(_record().to_json())

    for forbidden in (
        "selected",
        "authorized",
        "executed",
        "verified",
        "applied",
        "safe",
        "correct",
        "risk",
        "permission",
        "patch",
        "repair",
        "confidence",
        "score",
    ):
        assert forbidden not in payload
        assert forbidden not in payload["baseline"]
        assert forbidden not in payload["current"]


def test_any_type_annotation_is_not_a_runtime_cast() -> None:
    """Guard the ``Any`` escape hatch: typed inputs are still validated."""
    hostile: Any = EnvironmentFactValue(kind=EnvironmentFactValueKind.TEXT, text="3.1.0")
    assert isinstance(hostile, EnvironmentFactValue)
    with pytest.raises(EnvironmentChangeValidationError):
        EnvironmentObservation(
            fact=_fact(_VERSION, "contoso-inventory"),
            value=hostile,
            observed_at=_NOW,
            ttl=_TTL,
            provenance=None,  # type: ignore[arg-type]
        )
