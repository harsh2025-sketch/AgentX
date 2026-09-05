"""Unit tests for the C4.02 canonical failure-localization contract."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from agentx.core.failure_localization import (
    CANONICAL_FAILURE_LOCATION_KINDS,
    FAILURE_LOCALIZATION_SCHEMA_VERSION,
    FailureLocalization,
    FailureLocalizationDeserializationError,
    FailureLocalizationValidationError,
    FailureLocationKind,
    LocalizationEvidence,
    UnsupportedFailureLocalizationSchemaVersionError,
    localize_failure,
)
from agentx.core.failure_taxonomy import (
    FailureCategory,
    FailureClassification,
)
from agentx.core.ids import (
    CapabilityId,
    EpisodeId,
    NegativeExperienceId,
    ProcedureId,
    TaskId,
)

_NOW = datetime(2026, 9, 5, 12, 30, 15, 123456, tzinfo=UTC)

_EXPECTED_KIND_VALUES = (
    "task",
    "capability",
    "procedure",
    "procedure_node",
    "action",
    "observation",
    "verification",
    "environment",
    "dependency",
    "unknown",
)


def _classification(**overrides: object) -> FailureClassification:
    payload: dict[str, object] = {
        "category": FailureCategory.PROCEDURE,
        "summary": "Observed a procedure-class failure.",
        "classified_at": _NOW,
    }
    payload.update(overrides)
    return FailureClassification(**payload)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> LocalizationEvidence:
    payload: dict[str, object] = {"kind": FailureLocationKind.UNKNOWN}
    payload.update(overrides)
    return LocalizationEvidence(**payload)  # type: ignore[arg-type]


def _localization(**overrides: object) -> FailureLocalization:
    payload: dict[str, object] = {
        "kind": FailureLocationKind.UNKNOWN,
        "summary": "Evidence does not justify a more specific target.",
        "localized_at": _NOW,
    }
    payload.update(overrides)
    return FailureLocalization(**payload)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_canonical_kinds_are_exactly_the_architecture_vocabulary() -> None:
    assert tuple(member.value for member in FailureLocationKind) == _EXPECTED_KIND_VALUES
    assert tuple(FailureLocationKind) == CANONICAL_FAILURE_LOCATION_KINDS
    assert len(CANONICAL_FAILURE_LOCATION_KINDS) == 10


def test_kind_names_match_the_canonical_architecture_names() -> None:
    assert {member.name for member in FailureLocationKind} == {
        "TASK",
        "CAPABILITY",
        "PROCEDURE",
        "PROCEDURE_NODE",
        "ACTION",
        "OBSERVATION",
        "VERIFICATION",
        "ENVIRONMENT",
        "DEPENDENCY",
        "UNKNOWN",
    }


def test_kind_values_are_unique_and_lowercase() -> None:
    values = [member.value for member in FailureLocationKind]

    assert len(set(values)) == len(values)
    assert all(value == value.lower() for value in values)


def test_kinds_are_unordered_and_carry_no_severity_or_policy_attributes() -> None:
    member = FailureLocationKind.PROCEDURE_NODE

    for attribute in (
        "severity",
        "confidence",
        "probability",
        "score",
        "weight",
        "retryable",
        "retry",
        "repair",
        "remedy",
        "rank",
        "diagnosis",
        "patch",
    ):
        assert not hasattr(member, attribute)


def test_schema_version_is_one() -> None:
    assert FAILURE_LOCALIZATION_SCHEMA_VERSION == 1


# ---------------------------------------------------------------------------
# Explicit localization to supported target types
# ---------------------------------------------------------------------------


def test_unknown_is_a_valid_first_class_unlocalized_result() -> None:
    record = _localization()

    assert record.kind is FailureLocationKind.UNKNOWN
    assert record.is_unlocalized is True
    assert record.is_unknown is True
    assert record.task_id is None
    assert record.capability_id is None
    assert record.procedure_id is None
    assert record.procedure_node_id is None


def test_task_localization_requires_and_reuses_canonical_task_id() -> None:
    task_id = TaskId.create()
    record = _localization(
        kind=FailureLocationKind.TASK,
        summary="Failure observed under the named task.",
        task_id=task_id,
    )

    assert record.kind is FailureLocationKind.TASK
    assert record.task_id is task_id
    assert record.is_unlocalized is False


def test_capability_localization_requires_and_reuses_canonical_capability_id() -> None:
    capability_id = CapabilityId.create()
    record = _localization(
        kind=FailureLocationKind.CAPABILITY,
        summary="Failure observed against the named capability.",
        capability_id=capability_id,
    )

    assert record.capability_id is capability_id


def test_procedure_localization_requires_and_reuses_canonical_procedure_id() -> None:
    procedure_id = ProcedureId.create()
    record = _localization(
        kind=FailureLocationKind.PROCEDURE,
        summary="Failure observed against the named procedure.",
        procedure_id=procedure_id,
    )

    assert record.procedure_id is procedure_id
    assert record.procedure_node_id is None


def test_procedure_node_localization_requires_procedure_and_node_identity() -> None:
    procedure_id = ProcedureId.create()
    record = _localization(
        kind=FailureLocationKind.PROCEDURE_NODE,
        summary="Evidence names a specific procedure node.",
        procedure_id=procedure_id,
        procedure_node_id="node-verify-postcondition",
    )

    assert record.kind is FailureLocationKind.PROCEDURE_NODE
    assert record.procedure_id is procedure_id
    assert record.procedure_node_id == "node-verify-postcondition"


def test_action_localization_requires_correlation_id_and_may_name_the_action() -> None:
    correlation_id = uuid4()
    record = _localization(
        kind=FailureLocationKind.ACTION,
        summary="Evidence points at the action/attempt stage.",
        correlation_id=correlation_id,
        action_name="files.write@1.2.3",
    )

    assert record.kind is FailureLocationKind.ACTION
    assert record.correlation_id == correlation_id
    assert record.action_name == "files.write@1.2.3"


def test_observation_and_verification_localization_require_correlation_id() -> None:
    correlation_id = uuid4()

    observation = _localization(
        kind=FailureLocationKind.OBSERVATION,
        summary="Evidence points at the observation stage.",
        correlation_id=correlation_id,
    )
    verification = _localization(
        kind=FailureLocationKind.VERIFICATION,
        summary="Evidence points at the verification stage.",
        correlation_id=correlation_id,
    )

    assert observation.kind is FailureLocationKind.OBSERVATION
    assert verification.kind is FailureLocationKind.VERIFICATION
    assert observation.correlation_id == correlation_id
    assert verification.correlation_id == correlation_id


def test_environment_and_dependency_localization_require_explicit_refs() -> None:
    environment = _localization(
        kind=FailureLocationKind.ENVIRONMENT,
        summary="Evidence names an environment reference.",
        environment_ref="windows.display.scale",
    )
    dependency = _localization(
        kind=FailureLocationKind.DEPENDENCY,
        summary="Evidence names a dependency reference.",
        dependency_ref="browser.cdp.endpoint",
    )

    assert environment.environment_ref == "windows.display.scale"
    assert dependency.dependency_ref == "browser.cdp.endpoint"


def test_every_canonical_kind_can_be_constructed_with_matching_evidence() -> None:
    correlation_id = uuid4()
    builders: dict[FailureLocationKind, dict[str, object]] = {
        FailureLocationKind.TASK: {"task_id": TaskId.create()},
        FailureLocationKind.CAPABILITY: {"capability_id": CapabilityId.create()},
        FailureLocationKind.PROCEDURE: {"procedure_id": ProcedureId.create()},
        FailureLocationKind.PROCEDURE_NODE: {
            "procedure_id": ProcedureId.create(),
            "procedure_node_id": "n1",
        },
        FailureLocationKind.ACTION: {"correlation_id": correlation_id},
        FailureLocationKind.OBSERVATION: {"correlation_id": correlation_id},
        FailureLocationKind.VERIFICATION: {"correlation_id": correlation_id},
        FailureLocationKind.ENVIRONMENT: {"environment_ref": "env.key"},
        FailureLocationKind.DEPENDENCY: {"dependency_ref": "dep.key"},
        FailureLocationKind.UNKNOWN: {},
    }

    for kind in CANONICAL_FAILURE_LOCATION_KINDS:
        record = _localization(kind=kind, summary=f"Localized to {kind.value}.", **builders[kind])
        assert record.kind is kind


# ---------------------------------------------------------------------------
# Evidence contract and localize_failure
# ---------------------------------------------------------------------------


def test_localize_failure_packages_explicit_evidence_without_inference() -> None:
    task_id = TaskId.create()
    classification = _classification(category=FailureCategory.CAPABILITY)
    evidence = LocalizationEvidence(
        kind=FailureLocationKind.CAPABILITY,
        capability_id=CapabilityId.create(),
        task_id=task_id,
        episode_id=EpisodeId.create(),
        correlation_id=uuid4(),
        classification=classification,
    )

    record = localize_failure(
        evidence,
        summary="Capability identity was explicit in structured evidence.",
        localized_at=_NOW,
        detail="inert detail",
    )

    assert record.kind is FailureLocationKind.CAPABILITY
    assert record.capability_id is evidence.capability_id
    assert record.task_id is task_id
    assert record.classification is classification
    assert record.classification is not None
    assert record.classification.category is FailureCategory.CAPABILITY
    assert record.summary == "Capability identity was explicit in structured evidence."


def test_localize_failure_rejects_non_evidence_input() -> None:
    with pytest.raises(FailureLocalizationValidationError, match="LocalizationEvidence"):
        localize_failure(
            "permission denied on button API verify",  # type: ignore[arg-type]
            summary="must not accept free text as evidence",
            localized_at=_NOW,
        )


def test_category_alone_cannot_fabricate_a_location() -> None:
    classification = _classification(category=FailureCategory.PROCEDURE)

    # Linking a PROCEDURE classification without procedure identity still
    # requires an explicit kind; UNKNOWN remains valid and does not invent
    # a procedure/node target from the category.
    unlocalized = localize_failure(
        LocalizationEvidence(kind=FailureLocationKind.UNKNOWN, classification=classification),
        summary="Category alone does not name a location.",
        localized_at=_NOW,
    )

    assert unlocalized.is_unlocalized is True
    assert unlocalized.procedure_id is None
    assert unlocalized.procedure_node_id is None
    assert unlocalized.classification is not None
    assert unlocalized.classification.category is FailureCategory.PROCEDURE

    with pytest.raises(FailureLocalizationValidationError, match="procedure_id"):
        LocalizationEvidence(
            kind=FailureLocationKind.PROCEDURE,
            classification=classification,
        )

    with pytest.raises(FailureLocalizationValidationError, match="procedure_node_id"):
        LocalizationEvidence(
            kind=FailureLocationKind.PROCEDURE_NODE,
            procedure_id=ProcedureId.create(),
            classification=_classification(category=FailureCategory.VERIFICATION),
        )


def test_verification_category_does_not_auto_localize_to_verification() -> None:
    classification = _classification(category=FailureCategory.VERIFICATION)

    record = localize_failure(
        LocalizationEvidence(kind=FailureLocationKind.UNKNOWN, classification=classification),
        summary="VERIFICATION category does not identify a verifier condition.",
        localized_at=_NOW,
    )

    assert record.kind is FailureLocationKind.UNKNOWN
    assert record.classification is not None
    assert record.classification.category is FailureCategory.VERIFICATION


# ---------------------------------------------------------------------------
# Validation / malformed references
# ---------------------------------------------------------------------------


def test_unknown_rejects_target_specific_identity_fields() -> None:
    with pytest.raises(FailureLocalizationValidationError, match="target-specific"):
        _localization(capability_id=CapabilityId.create())
    with pytest.raises(FailureLocalizationValidationError, match="target-specific"):
        _localization(procedure_id=ProcedureId.create())
    with pytest.raises(FailureLocalizationValidationError, match="target-specific"):
        _localization(environment_ref="env")
    with pytest.raises(FailureLocalizationValidationError, match="target-specific"):
        _localization(action_name="files.write@1.0.0")


def test_target_kinds_reject_missing_required_identity() -> None:
    with pytest.raises(FailureLocalizationValidationError, match="task_id"):
        _localization(kind=FailureLocationKind.TASK, summary="missing task")
    with pytest.raises(FailureLocalizationValidationError, match="capability_id"):
        _localization(kind=FailureLocationKind.CAPABILITY, summary="missing capability")
    with pytest.raises(FailureLocalizationValidationError, match="procedure_id"):
        _localization(kind=FailureLocationKind.PROCEDURE, summary="missing procedure")
    with pytest.raises(FailureLocalizationValidationError, match="correlation_id"):
        _localization(kind=FailureLocationKind.ACTION, summary="missing correlation")
    with pytest.raises(FailureLocalizationValidationError, match="correlation_id"):
        _localization(kind=FailureLocationKind.OBSERVATION, summary="missing correlation")
    with pytest.raises(FailureLocalizationValidationError, match="correlation_id"):
        _localization(kind=FailureLocationKind.VERIFICATION, summary="missing correlation")
    with pytest.raises(FailureLocalizationValidationError, match="environment_ref"):
        _localization(kind=FailureLocationKind.ENVIRONMENT, summary="missing env")
    with pytest.raises(FailureLocalizationValidationError, match="dependency_ref"):
        _localization(kind=FailureLocationKind.DEPENDENCY, summary="missing dep")


def test_foreign_target_fields_are_rejected_for_declared_kind() -> None:
    with pytest.raises(FailureLocalizationValidationError, match="foreign"):
        _localization(
            kind=FailureLocationKind.CAPABILITY,
            summary="capability with procedure smuggle",
            capability_id=CapabilityId.create(),
            procedure_id=ProcedureId.create(),
        )
    with pytest.raises(FailureLocalizationValidationError, match="foreign"):
        _localization(
            kind=FailureLocationKind.PROCEDURE,
            summary="procedure with node smuggle",
            procedure_id=ProcedureId.create(),
            procedure_node_id="n1",
        )
    with pytest.raises(FailureLocalizationValidationError, match="foreign"):
        _localization(
            kind=FailureLocationKind.ACTION,
            summary="action with capability smuggle",
            correlation_id=uuid4(),
            capability_id=CapabilityId.create(),
        )


def test_identity_references_must_be_canonical_typed_ids() -> None:
    with pytest.raises(FailureLocalizationValidationError, match="task_id"):
        _localization(kind=FailureLocationKind.TASK, summary="bad", task_id=str(uuid4()))
    with pytest.raises(FailureLocalizationValidationError, match="capability_id"):
        _localization(
            kind=FailureLocationKind.CAPABILITY,
            summary="bad",
            capability_id=TaskId.create(),
        )
    with pytest.raises(FailureLocalizationValidationError, match="procedure_id"):
        _localization(
            kind=FailureLocationKind.PROCEDURE,
            summary="bad",
            procedure_id=CapabilityId.create(),
        )
    with pytest.raises(FailureLocalizationValidationError, match="correlation_id"):
        _localization(
            kind=FailureLocationKind.ACTION,
            summary="bad",
            correlation_id=str(uuid4()),
        )
    with pytest.raises(FailureLocalizationValidationError, match="correlation_id"):
        _localization(
            kind=FailureLocationKind.ACTION,
            summary="bad",
            correlation_id=UUID(int=0),
        )


def test_string_refs_reject_malformed_values() -> None:
    procedure_id = ProcedureId.create()
    with pytest.raises(FailureLocalizationValidationError, match="procedure_node_id"):
        _localization(
            kind=FailureLocationKind.PROCEDURE_NODE,
            summary="bad node",
            procedure_id=procedure_id,
            procedure_node_id="  padded  ",
        )
    with pytest.raises(FailureLocalizationValidationError, match="procedure_node_id"):
        _localization(
            kind=FailureLocationKind.PROCEDURE_NODE,
            summary="bad node",
            procedure_id=procedure_id,
            procedure_node_id="has\x00null",
        )
    with pytest.raises(FailureLocalizationValidationError, match="environment_ref"):
        _localization(
            kind=FailureLocationKind.ENVIRONMENT,
            summary="bad env",
            environment_ref="",
        )
    with pytest.raises(FailureLocalizationValidationError, match="action_name"):
        _localization(
            kind=FailureLocationKind.ACTION,
            summary="bad action",
            correlation_id=uuid4(),
            action_name="x" * 257,
        )


def test_kind_must_be_typed_enum_member_not_text() -> None:
    with pytest.raises(FailureLocalizationValidationError, match="FailureLocationKind"):
        FailureLocalization(
            kind="capability",  # type: ignore[arg-type]
            summary="string kind is rejected",
            localized_at=_NOW,
            capability_id=CapabilityId.create(),
        )


def test_summary_and_detail_validation_matches_c401_style_rules() -> None:
    with pytest.raises(FailureLocalizationValidationError, match="summary"):
        _localization(summary="  padded  ")
    with pytest.raises(FailureLocalizationValidationError, match="summary"):
        _localization(summary="")
    with pytest.raises(FailureLocalizationValidationError, match="summary"):
        _localization(summary="has\nnewline")
    with pytest.raises(FailureLocalizationValidationError, match="detail"):
        _localization(detail="  padded  ")
    with pytest.raises(FailureLocalizationValidationError, match="localized_at"):
        _localization(localized_at=datetime(2026, 9, 5, 12, 0, 0))


def test_linked_classification_must_be_canonical_type() -> None:
    with pytest.raises(FailureLocalizationValidationError, match="classification"):
        _localization(classification={"category": "procedure"})


# ---------------------------------------------------------------------------
# Immutability, equality, hashing
# ---------------------------------------------------------------------------


def test_records_are_immutable() -> None:
    record = _localization()

    with pytest.raises(FrozenInstanceError):
        record.kind = FailureLocationKind.TASK  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        record.summary = "rewritten"  # type: ignore[misc]
    assert not hasattr(record, "__dict__")
    assert FailureLocalization.__slots__
    assert LocalizationEvidence.__slots__


def test_equality_and_hashing_are_structural() -> None:
    first = _localization()
    second = _localization()
    different = _localization(
        kind=FailureLocationKind.TASK,
        summary="task target",
        task_id=TaskId.create(),
    )

    assert first == second
    assert hash(first) == hash(second)
    assert first != different
    unrelated: object = "not a localization"
    assert first != unrelated


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_to_dict_is_the_exact_canonical_shape() -> None:
    record = _localization()

    assert record.to_dict() == {
        "schema_version": 1,
        "kind": "unknown",
        "summary": "Evidence does not justify a more specific target.",
        "localized_at": "2026-09-05T12:30:15.123456Z",
        "detail": None,
        "task_id": None,
        "capability_id": None,
        "procedure_id": None,
        "procedure_node_id": None,
        "action_name": None,
        "environment_ref": None,
        "dependency_ref": None,
        "episode_id": None,
        "negative_experience_id": None,
        "correlation_id": None,
        "classification": None,
    }


def test_to_json_is_deterministic_and_sorted() -> None:
    record = _localization(detail="inert detail")

    encoded = record.to_json()

    assert encoded == record.to_json()
    assert list(json.loads(encoded)) == sorted(json.loads(encoded))
    assert ", " not in encoded


def test_every_canonical_kind_round_trips_deterministically() -> None:
    correlation_id = uuid4()
    builders: dict[FailureLocationKind, dict[str, object]] = {
        FailureLocationKind.TASK: {
            "task_id": TaskId.create(),
            "correlation_id": correlation_id,
        },
        FailureLocationKind.CAPABILITY: {
            "capability_id": CapabilityId.create(),
            "task_id": TaskId.create(),
            "correlation_id": correlation_id,
        },
        FailureLocationKind.PROCEDURE: {
            "procedure_id": ProcedureId.create(),
            "correlation_id": correlation_id,
        },
        FailureLocationKind.PROCEDURE_NODE: {
            "procedure_id": ProcedureId.create(),
            "procedure_node_id": "node-a",
            "correlation_id": correlation_id,
        },
        FailureLocationKind.ACTION: {
            "correlation_id": correlation_id,
            "action_name": "files.write@1.0.0",
            "task_id": TaskId.create(),
        },
        FailureLocationKind.OBSERVATION: {
            "correlation_id": correlation_id,
            "task_id": TaskId.create(),
        },
        FailureLocationKind.VERIFICATION: {
            "correlation_id": correlation_id,
            "task_id": TaskId.create(),
        },
        FailureLocationKind.ENVIRONMENT: {
            "environment_ref": "os.locale",
            "correlation_id": correlation_id,
        },
        FailureLocationKind.DEPENDENCY: {
            "dependency_ref": "svc.mail",
            "correlation_id": correlation_id,
        },
        FailureLocationKind.UNKNOWN: {},
    }

    for kind in CANONICAL_FAILURE_LOCATION_KINDS:
        record = _localization(
            kind=kind,
            summary=f"Localized to {kind.value}.",
            detail="inert detail",
            episode_id=EpisodeId.create(),
            negative_experience_id=NegativeExperienceId.create(),
            classification=_classification(),
            **builders[kind],
        )

        encoded = record.to_json()
        restored = FailureLocalization.from_json(encoded)

        assert restored == record
        assert restored.kind is kind
        assert restored.to_json() == encoded
        assert FailureLocalization.from_dict(record.to_dict()) == record
        if restored.classification is not None:
            assert restored.classification == record.classification


def test_from_dict_rejects_unknown_kind_values_instead_of_downgrading() -> None:
    payload = _localization().to_dict()
    payload["kind"] = "definitely_not_canonical"

    with pytest.raises(FailureLocalizationDeserializationError, match="kind"):
        FailureLocalization.from_dict(payload)


def test_from_dict_rejects_kind_shaped_hostile_strings() -> None:
    payload = _localization().to_dict()

    for hostile in ("CAPABILITY", " capability", "permission", "unknown ", "", "procedure_node "):
        payload["kind"] = hostile
        with pytest.raises(FailureLocalizationDeserializationError, match="kind"):
            FailureLocalization.from_dict(payload)


def test_from_dict_requires_the_exact_field_set() -> None:
    payload = _localization().to_dict()
    missing = dict(payload)
    del missing["summary"]

    with pytest.raises(FailureLocalizationDeserializationError, match="missing"):
        FailureLocalization.from_dict(missing)

    extra = dict(payload)
    extra["confidence"] = 0.99
    with pytest.raises(FailureLocalizationDeserializationError, match="unknown fields"):
        FailureLocalization.from_dict(extra)


def test_from_dict_rejects_unsupported_or_malformed_schema_versions() -> None:
    payload = _localization().to_dict()

    payload_v2 = dict(payload)
    payload_v2["schema_version"] = 2
    with pytest.raises(UnsupportedFailureLocalizationSchemaVersionError):
        FailureLocalization.from_dict(payload_v2)

    payload_bool = dict(payload)
    payload_bool["schema_version"] = True
    with pytest.raises(FailureLocalizationDeserializationError, match="schema_version"):
        FailureLocalization.from_dict(payload_bool)

    payload_missing = dict(payload)
    del payload_missing["schema_version"]
    with pytest.raises(FailureLocalizationDeserializationError, match="schema_version"):
        FailureLocalization.from_dict(payload_missing)


def test_from_dict_rejects_malformed_field_types() -> None:
    payload = _localization().to_dict()

    for field, value, match in (
        ("summary", 5, "summary"),
        ("detail", 5, "detail"),
        ("localized_at", 5, "localized_at"),
        ("localized_at", "not-a-timestamp", "localized_at"),
        ("localized_at", "2026-09-05T12:30:15.123456", "timezone-aware"),
        ("task_id", "not-a-uuid", "task_id"),
        ("capability_id", "not-a-uuid", "capability_id"),
        ("procedure_id", 5, "procedure_id"),
        ("procedure_node_id", 5, "procedure_node_id"),
        ("action_name", 5, "action_name"),
        ("environment_ref", 5, "environment_ref"),
        ("dependency_ref", 5, "dependency_ref"),
        ("episode_id", 5, "episode_id"),
        ("negative_experience_id", "not-a-uuid", "negative_experience_id"),
        ("correlation_id", "not-a-uuid", "correlation_id"),
        ("correlation_id", str(UUID(int=0)), "correlation_id"),
        ("classification", "not-an-object", "classification"),
    ):
        broken = dict(payload)
        broken[field] = value
        with pytest.raises(FailureLocalizationDeserializationError, match=match):
            FailureLocalization.from_dict(broken)


def test_from_dict_rejects_kind_evidence_mismatch_on_decode() -> None:
    payload = _localization(
        kind=FailureLocationKind.CAPABILITY,
        summary="capability localization",
        capability_id=CapabilityId.create(),
    ).to_dict()
    payload["capability_id"] = None

    with pytest.raises(FailureLocalizationDeserializationError, match="capability_id"):
        FailureLocalization.from_dict(payload)


def test_from_json_fails_closed_on_hostile_or_malformed_input() -> None:
    with pytest.raises(FailureLocalizationDeserializationError, match="malformed"):
        FailureLocalization.from_json("{not json")
    with pytest.raises(FailureLocalizationDeserializationError, match="object"):
        FailureLocalization.from_json("[]")
    with pytest.raises(FailureLocalizationDeserializationError, match="object"):
        FailureLocalization.from_json('"capability"')
    with pytest.raises(FailureLocalizationDeserializationError, match="text"):
        FailureLocalization.from_json(b"{}")  # type: ignore[arg-type]


def test_timestamps_round_trip_with_offset_normalization() -> None:
    aware = datetime(2026, 9, 5, 14, 0, 0, 500000, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    record = _localization(localized_at=aware)

    restored = FailureLocalization.from_json(record.to_json())

    assert restored.localized_at == aware
    assert restored.to_dict()["localized_at"] == "2026-09-05T08:30:00.500000Z"


def test_nested_classification_is_preserved_and_validated() -> None:
    classification = _classification(
        category=FailureCategory.PERMISSION,
        summary="Observed a permission-class failure.",
        error_code="permission.denied",
        task_id=TaskId.create(),
    )
    record = _localization(
        kind=FailureLocationKind.TASK,
        summary="Task-localized with linked classification.",
        task_id=classification.task_id,
        classification=classification,
    )

    restored = FailureLocalization.from_json(record.to_json())

    assert restored.classification == classification
    assert restored.classification is not None
    assert restored.classification.category is FailureCategory.PERMISSION
    assert restored.classification.error_code == "permission.denied"

    payload = record.to_dict()
    classification_payload = payload["classification"]
    assert isinstance(classification_payload, dict)
    nested = dict(classification_payload)
    nested["category"] = "not_a_real_category"
    payload["classification"] = nested
    with pytest.raises(FailureLocalizationDeserializationError, match="classification"):
        FailureLocalization.from_dict(payload)


def test_serialization_uses_no_object_hooks_or_executable_types() -> None:
    record = _localization(detail="unicode détail ✓")

    decoded = json.loads(record.to_json())

    assert isinstance(decoded, dict)
    assert all(isinstance(key, str) for key in decoded)
    for value in decoded.values():
        assert value is None or isinstance(value, str | int | dict)


def test_c401_classification_is_unchanged_by_localization_module() -> None:
    """C4.02 must not alter C4.01 vocabulary or record shape."""
    from agentx.core.failure_taxonomy import (
        CANONICAL_FAILURE_CATEGORIES,
        FAILURE_CLASSIFICATION_SCHEMA_VERSION,
        FailureCategory,
    )

    assert FAILURE_CLASSIFICATION_SCHEMA_VERSION == 1
    assert len(CANONICAL_FAILURE_CATEGORIES) == 13
    assert FailureCategory.UNKNOWN.value == "unknown"
    assert FailureCategory.PROCEDURE.value == "procedure"

    original = _classification()
    linked = _localization(classification=original)
    assert linked.classification == original
    assert original.to_json() == _classification().to_json()
