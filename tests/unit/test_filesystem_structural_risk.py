"""Unit tests for the request-sensitive filesystem structural risk policy (N2.22).

The classification matrix is table-driven so that every canonical operation
kind and every target-state fact combination is pinned explicitly. The core
invariant under test is request sensitivity: materially different requests in
the same operation family must not share a risk classification.
"""

from __future__ import annotations

import dataclasses

import pytest

from agentx.capabilities.filesystem_structural_risk import (
    MAX_PATH_LENGTH,
    DeletionScope,
    FilesystemStructuralOperationKind,
    FilesystemStructuralRiskPolicy,
    FilesystemStructuralRiskRequest,
    FilesystemStructuralRiskResult,
    TargetState,
    classify_filesystem_structural_risk,
)
from agentx.kernel.risk import RiskAssessment, RiskLevel

Op = FilesystemStructuralOperationKind
State = TargetState
Scope = DeletionScope

NEUTRAL_SOURCE = "/src/neutral"
NEUTRAL_DEST = "/dst/neutral"


def _request(
    operation: Op,
    *,
    source_state: State = State.UNKNOWN,
    destination_state: State = State.UNKNOWN,
    overwrite_requested: bool = False,
    replacement_requested: bool = False,
    deletion_scope: Scope = Scope.UNKNOWN,
    source_path: str | None = NEUTRAL_SOURCE,
    destination_path: str = NEUTRAL_DEST,
) -> FilesystemStructuralRiskRequest:
    # Only the structural shape is normalized: create/delete operations have
    # no source path. Every fact value is passed through untouched so that
    # malformed-fact tests exercise the real contract validation.
    if operation in (Op.CREATE_DIRECTORY, Op.CREATE_FILE, Op.DELETE):
        source_path = None
    return FilesystemStructuralRiskRequest(
        operation=operation,
        source_path=source_path,
        destination_path=destination_path,
        source_state=source_state,
        destination_state=destination_state,
        overwrite_requested=overwrite_requested,
        replacement_requested=replacement_requested,
        deletion_scope=deletion_scope,
    )


# ---------------------------------------------------------------------------
# Classification matrix: (request kwargs, level, destructive, data_loss,
# state_change, insufficient_facts).
# ---------------------------------------------------------------------------

MATRIX: tuple[tuple[dict[str, object], RiskLevel, bool, bool, bool, tuple[str, ...]], ...] = (
    # CREATE_DIRECTORY
    (
        {"operation": Op.CREATE_DIRECTORY, "destination_state": State.ABSENT},
        RiskLevel.R1,
        False,
        False,
        True,
        (),
    ),
    (
        {"operation": Op.CREATE_DIRECTORY, "destination_state": State.PRESENT},
        RiskLevel.R0,
        False,
        False,
        False,
        (),
    ),
    (
        {
            "operation": Op.CREATE_DIRECTORY,
            "destination_state": State.PRESENT,
            "overwrite_requested": True,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        (),
    ),
    (
        {"operation": Op.CREATE_DIRECTORY, "destination_state": State.UNKNOWN},
        RiskLevel.R1,
        False,
        False,
        True,
        ("destination_state",),
    ),
    (
        {
            "operation": Op.CREATE_DIRECTORY,
            "destination_state": State.UNKNOWN,
            "overwrite_requested": True,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        ("destination_state",),
    ),
    # CREATE_FILE
    (
        {"operation": Op.CREATE_FILE, "destination_state": State.ABSENT},
        RiskLevel.R1,
        False,
        False,
        True,
        (),
    ),
    (
        {"operation": Op.CREATE_FILE, "destination_state": State.PRESENT},
        RiskLevel.R0,
        False,
        False,
        False,
        (),
    ),
    (
        {
            "operation": Op.CREATE_FILE,
            "destination_state": State.PRESENT,
            "overwrite_requested": True,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        (),
    ),
    (
        {"operation": Op.CREATE_FILE, "destination_state": State.UNKNOWN},
        RiskLevel.R1,
        False,
        False,
        True,
        ("destination_state",),
    ),
    (
        {
            "operation": Op.CREATE_FILE,
            "destination_state": State.UNKNOWN,
            "overwrite_requested": True,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        ("destination_state",),
    ),
    # MOVE
    (
        {"operation": Op.MOVE, "source_state": State.ABSENT, "destination_state": State.ABSENT},
        RiskLevel.R0,
        False,
        False,
        False,
        (),
    ),
    (
        {
            "operation": Op.MOVE,
            "source_state": State.ABSENT,
            "destination_state": State.PRESENT,
            "overwrite_requested": True,
        },
        RiskLevel.R0,
        False,
        False,
        False,
        (),
    ),
    (
        {"operation": Op.MOVE, "source_state": State.PRESENT, "destination_state": State.ABSENT},
        RiskLevel.R1,
        False,
        False,
        True,
        (),
    ),
    (
        {"operation": Op.MOVE, "source_state": State.PRESENT, "destination_state": State.PRESENT},
        RiskLevel.R0,
        False,
        False,
        False,
        (),
    ),
    (
        {
            "operation": Op.MOVE,
            "source_state": State.PRESENT,
            "destination_state": State.PRESENT,
            "overwrite_requested": True,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        (),
    ),
    (
        {"operation": Op.MOVE, "source_state": State.UNKNOWN, "destination_state": State.ABSENT},
        RiskLevel.R1,
        False,
        False,
        True,
        ("source_state",),
    ),
    (
        {"operation": Op.MOVE, "source_state": State.UNKNOWN, "destination_state": State.PRESENT},
        RiskLevel.R0,
        False,
        False,
        False,
        (),
    ),
    (
        {
            "operation": Op.MOVE,
            "source_state": State.UNKNOWN,
            "destination_state": State.PRESENT,
            "overwrite_requested": True,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        ("source_state",),
    ),
    (
        {"operation": Op.MOVE, "source_state": State.PRESENT, "destination_state": State.UNKNOWN},
        RiskLevel.R1,
        False,
        False,
        True,
        ("destination_state",),
    ),
    (
        {
            "operation": Op.MOVE,
            "source_state": State.PRESENT,
            "destination_state": State.UNKNOWN,
            "overwrite_requested": True,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        ("destination_state",),
    ),
    # RENAME
    (
        {"operation": Op.RENAME, "source_state": State.ABSENT, "destination_state": State.ABSENT},
        RiskLevel.R0,
        False,
        False,
        False,
        (),
    ),
    (
        {"operation": Op.RENAME, "source_state": State.PRESENT, "destination_state": State.ABSENT},
        RiskLevel.R1,
        False,
        False,
        True,
        (),
    ),
    (
        {"operation": Op.RENAME, "source_state": State.PRESENT, "destination_state": State.PRESENT},
        RiskLevel.R0,
        False,
        False,
        False,
        (),
    ),
    (
        {
            "operation": Op.RENAME,
            "source_state": State.PRESENT,
            "destination_state": State.PRESENT,
            "replacement_requested": True,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        (),
    ),
    (
        {
            "operation": Op.RENAME,
            "source_state": State.UNKNOWN,
            "destination_state": State.PRESENT,
            "replacement_requested": True,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        ("source_state",),
    ),
    (
        {
            "operation": Op.RENAME,
            "source_state": State.PRESENT,
            "destination_state": State.UNKNOWN,
            "replacement_requested": True,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        ("destination_state",),
    ),
    # DELETE
    (
        {"operation": Op.DELETE, "destination_state": State.ABSENT},
        RiskLevel.R0,
        False,
        False,
        False,
        (),
    ),
    (
        {"operation": Op.DELETE, "destination_state": State.PRESENT, "deletion_scope": Scope.EMPTY},
        RiskLevel.R1,
        False,
        False,
        True,
        (),
    ),
    (
        {
            "operation": Op.DELETE,
            "destination_state": State.PRESENT,
            "deletion_scope": Scope.NON_EMPTY,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        (),
    ),
    (
        {
            "operation": Op.DELETE,
            "destination_state": State.PRESENT,
            "deletion_scope": Scope.UNKNOWN,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        ("deletion_scope",),
    ),
    (
        {"operation": Op.DELETE, "destination_state": State.UNKNOWN, "deletion_scope": Scope.EMPTY},
        RiskLevel.R1,
        False,
        False,
        True,
        ("destination_state",),
    ),
    (
        {
            "operation": Op.DELETE,
            "destination_state": State.UNKNOWN,
            "deletion_scope": Scope.NON_EMPTY,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        ("destination_state",),
    ),
    (
        {
            "operation": Op.DELETE,
            "destination_state": State.UNKNOWN,
            "deletion_scope": Scope.UNKNOWN,
        },
        RiskLevel.R4,
        True,
        True,
        True,
        ("deletion_scope", "destination_state"),
    ),
)


def _matrix_id(
    case: int, row: tuple[dict[str, object], RiskLevel, bool, bool, bool, tuple[str, ...]]
) -> str:
    facts = row[0]
    return (
        f"{case}-"
        f"{facts['operation']}-"
        f"source={facts.get('source_state')}-"
        f"destination={facts.get('destination_state')}-"
        f"overwrite={facts.get('overwrite_requested', False)}-"
        f"replacement={facts.get('replacement_requested', False)}-"
        f"scope={facts.get('deletion_scope')}"
    )


@pytest.mark.parametrize(
    ("facts", "level", "destructive", "data_loss", "state_change", "insufficient"),
    MATRIX,
    ids=[_matrix_id(index, row) for index, row in enumerate(MATRIX)],
)
def test_classification_matrix(
    facts: dict[str, object],
    level: RiskLevel,
    destructive: bool,
    data_loss: bool,
    state_change: bool,
    insufficient: tuple[str, ...],
) -> None:
    request = _request(**facts)  # type: ignore[arg-type]
    result = classify_filesystem_structural_risk(request)

    assert result.assessment.level is level
    assert result.assessment.destructive is destructive
    assert result.assessment.effective_level is level
    assert result.data_loss_possible is data_loss
    assert result.state_change_possible is state_change
    assert result.insufficient_facts == insufficient


def test_create_only_conflict_is_no_op_not_creation() -> None:
    """A create request against a verified-present target cannot mutate anything."""
    result = classify_filesystem_structural_risk(
        _request(Op.CREATE_FILE, destination_state=State.PRESENT)
    )

    assert result.assessment.level is RiskLevel.R0
    assert result.state_change_possible is False
    assert result.assessment.read_only is True
    assert result.assessment.modifies_state is False
    assert "fails before any mutation" in result.assessment.reason


def test_deterministic_repeated_classification() -> None:
    request = _request(
        Op.MOVE,
        source_state=State.UNKNOWN,
        destination_state=State.UNKNOWN,
        overwrite_requested=True,
    )

    results = [classify_filesystem_structural_risk(request) for _ in range(3)]

    assert results[0] == results[1] == results[2]
    assert (
        results[0].assessment.reason == results[1].assessment.reason == results[2].assessment.reason
    )


def test_classification_does_not_mutate_request() -> None:
    request = _request(
        Op.MOVE,
        source_state=State.PRESENT,
        destination_state=State.PRESENT,
        overwrite_requested=True,
    )
    original = dataclasses.asdict(request)

    classify_filesystem_structural_risk(request)

    assert dataclasses.asdict(request) == original
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.destination_path = "/other"  # type: ignore[misc]


def test_result_is_immutable() -> None:
    result = classify_filesystem_structural_risk(
        _request(Op.CREATE_FILE, destination_state=State.ABSENT)
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.data_loss_possible = True  # type: ignore[misc]


def test_result_carries_canonical_risk_assessment() -> None:
    result = classify_filesystem_structural_risk(
        _request(Op.MOVE, source_state=State.PRESENT, destination_state=State.ABSENT)
    )

    assert isinstance(result, FilesystemStructuralRiskResult)
    assert isinstance(result.assessment, RiskAssessment)
    # The stored level can never undercut the characteristics' floor.
    assert result.assessment.effective_level == result.assessment.level
    assert result.assessment.reason == result.assessment.reason.strip()
    assert result.assessment.reason
    # Local filesystem structure changes are not external effects or critical.
    assert result.assessment.external_effect is False
    assert result.assessment.critical is False


def test_policy_object_form_matches_function_form() -> None:
    request = _request(
        Op.RENAME,
        source_state=State.PRESENT,
        destination_state=State.PRESENT,
        replacement_requested=True,
    )

    policy_result = FilesystemStructuralRiskPolicy().classify(request)
    function_result = classify_filesystem_structural_risk(request)

    assert policy_result == function_result


def test_non_request_input_is_rejected() -> None:
    with pytest.raises(TypeError):
        classify_filesystem_structural_risk("create_file")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Fail-closed property: an unknown risk-relevant fact resolves to the maximum
# classification across its consistent target states, never the lower one.
# ---------------------------------------------------------------------------


# (unknown-fact request, absent-world pin, present-world pin, expected level,
# expected insufficient facts)
FAIL_CLOSED_CASES: tuple[
    tuple[dict[str, object], dict[str, object], dict[str, object], RiskLevel, tuple[str, ...]], ...
] = (
    (
        {"operation": Op.CREATE_FILE, "destination_state": State.UNKNOWN},
        {"destination_state": State.ABSENT},
        {"destination_state": State.PRESENT},
        RiskLevel.R1,
        ("destination_state",),
    ),
    (
        {
            "operation": Op.CREATE_FILE,
            "destination_state": State.UNKNOWN,
            "overwrite_requested": True,
        },
        {"destination_state": State.ABSENT},
        {"destination_state": State.PRESENT},
        RiskLevel.R4,
        ("destination_state",),
    ),
    (
        {"operation": Op.MOVE, "source_state": State.PRESENT, "destination_state": State.UNKNOWN},
        {"destination_state": State.ABSENT},
        {"destination_state": State.PRESENT},
        RiskLevel.R1,
        ("destination_state",),
    ),
    (
        {
            "operation": Op.MOVE,
            "source_state": State.PRESENT,
            "destination_state": State.UNKNOWN,
            "overwrite_requested": True,
        },
        {"destination_state": State.ABSENT},
        {"destination_state": State.PRESENT},
        RiskLevel.R4,
        ("destination_state",),
    ),
    (
        {"operation": Op.MOVE, "source_state": State.UNKNOWN, "destination_state": State.ABSENT},
        {"source_state": State.ABSENT},
        {"source_state": State.PRESENT},
        RiskLevel.R1,
        ("source_state",),
    ),
    (
        {
            "operation": Op.RENAME,
            "source_state": State.PRESENT,
            "destination_state": State.UNKNOWN,
            "replacement_requested": True,
        },
        {"destination_state": State.ABSENT},
        {"destination_state": State.PRESENT},
        RiskLevel.R4,
        ("destination_state",),
    ),
    (
        {
            "operation": Op.RENAME,
            "source_state": State.UNKNOWN,
            "destination_state": State.PRESENT,
            "replacement_requested": True,
        },
        {"source_state": State.ABSENT},
        {"source_state": State.PRESENT},
        RiskLevel.R4,
        ("source_state",),
    ),
)


@pytest.mark.parametrize(
    ("unknown_facts", "absent_pin", "present_pin", "level", "insufficient"),
    FAIL_CLOSED_CASES,
    ids=[
        "create-file-unknown",
        "create-file-unknown-overwrite",
        "move-unknown-destination",
        "move-unknown-destination-overwrite",
        "move-unknown-source",
        "rename-unknown-destination-replace",
        "rename-unknown-source-replace",
    ],
)
def test_unknown_facts_resolve_to_highest_applicable_risk(
    unknown_facts: dict[str, object],
    absent_pin: dict[str, object],
    present_pin: dict[str, object],
    level: RiskLevel,
    insufficient: tuple[str, ...],
) -> None:
    unknown_result = classify_filesystem_structural_risk(_request(**unknown_facts))  # type: ignore[arg-type]
    absent_result = classify_filesystem_structural_risk(
        _request(**{**unknown_facts, **absent_pin})  # type: ignore[arg-type]
    )
    present_result = classify_filesystem_structural_risk(
        _request(**{**unknown_facts, **present_pin})  # type: ignore[arg-type]
    )

    # Fail closed: the unknown fact resolves to the maximum of the consistent
    # target-state worlds, never to the lower one.
    assert unknown_result.assessment.level is level
    assert unknown_result.assessment.level == max(
        absent_result.assessment.level, present_result.assessment.level
    )
    assert unknown_result.data_loss_possible == (
        absent_result.data_loss_possible or present_result.data_loss_possible
    )
    assert unknown_result.insufficient_facts == insufficient


# Deletion pins: a verified-absent target cannot carry a content claim, so the
# consistent pins are (present, scope) plus (absent) only when scope is unknown.
FAIL_CLOSED_DELETE_CASES: tuple[
    tuple[dict[str, object], tuple[dict[str, object], ...], RiskLevel, tuple[str, ...]], ...
] = (
    (
        {"operation": Op.DELETE, "destination_state": State.UNKNOWN, "deletion_scope": Scope.EMPTY},
        ({"destination_state": State.PRESENT},),
        RiskLevel.R1,
        ("destination_state",),
    ),
    (
        {
            "operation": Op.DELETE,
            "destination_state": State.UNKNOWN,
            "deletion_scope": Scope.NON_EMPTY,
        },
        ({"destination_state": State.PRESENT},),
        RiskLevel.R4,
        ("destination_state",),
    ),
    (
        {
            "operation": Op.DELETE,
            "destination_state": State.UNKNOWN,
            "deletion_scope": Scope.UNKNOWN,
        },
        (
            {"destination_state": State.ABSENT},
            {"destination_state": State.PRESENT, "deletion_scope": Scope.EMPTY},
            {"destination_state": State.PRESENT, "deletion_scope": Scope.NON_EMPTY},
        ),
        RiskLevel.R4,
        ("deletion_scope", "destination_state"),
    ),
    (
        {
            "operation": Op.DELETE,
            "destination_state": State.PRESENT,
            "deletion_scope": Scope.UNKNOWN,
        },
        (
            {"deletion_scope": Scope.EMPTY},
            {"deletion_scope": Scope.NON_EMPTY},
        ),
        RiskLevel.R4,
        ("deletion_scope",),
    ),
)


@pytest.mark.parametrize(
    ("unknown_facts", "pins", "level", "insufficient"),
    FAIL_CLOSED_DELETE_CASES,
    ids=[
        "delete-unknown-target-verified-empty",
        "delete-unknown-target-verified-non-empty",
        "delete-unknown-target-unknown-scope",
        "delete-present-target-unknown-scope",
    ],
)
def test_unknown_deletion_facts_resolve_to_highest_applicable_risk(
    unknown_facts: dict[str, object],
    pins: tuple[dict[str, object], ...],
    level: RiskLevel,
    insufficient: tuple[str, ...],
) -> None:
    unknown_result = classify_filesystem_structural_risk(_request(**unknown_facts))  # type: ignore[arg-type]

    assert unknown_result.assessment.level is level
    for pin in pins:
        pinned = classify_filesystem_structural_risk(
            _request(**{**unknown_facts, **pin})  # type: ignore[arg-type]
        )
        assert unknown_result.assessment.level >= pinned.assessment.level
        assert unknown_result.data_loss_possible or not pinned.data_loss_possible
    assert unknown_result.insufficient_facts == insufficient


def test_unknown_source_state_never_lowers_move_risk() -> None:
    """An unknown source is treated as present; it can never be assumed gone."""
    known = classify_filesystem_structural_risk(
        _request(Op.MOVE, source_state=State.PRESENT, destination_state=State.ABSENT)
    )
    unknown = classify_filesystem_structural_risk(
        _request(Op.MOVE, source_state=State.UNKNOWN, destination_state=State.ABSENT)
    )

    assert unknown.assessment.level is known.assessment.level
    assert unknown.state_change_possible is True
    assert "source_state" in unknown.insufficient_facts


def test_unknown_facts_that_do_not_change_outcome_are_not_reported() -> None:
    result = classify_filesystem_structural_risk(
        _request(Op.MOVE, source_state=State.UNKNOWN, destination_state=State.PRESENT)
    )

    assert result.assessment.level is RiskLevel.R0
    assert result.state_change_possible is False
    assert result.insufficient_facts == ()


def test_deletion_scope_unknown_is_treated_as_possibly_containing_data() -> None:
    verified_empty = classify_filesystem_structural_risk(
        _request(Op.DELETE, destination_state=State.PRESENT, deletion_scope=Scope.EMPTY)
    )
    unverified = classify_filesystem_structural_risk(
        _request(Op.DELETE, destination_state=State.PRESENT, deletion_scope=Scope.UNKNOWN)
    )

    assert verified_empty.assessment.level is RiskLevel.R1
    assert unverified.assessment.level is RiskLevel.R4
    assert unverified.data_loss_possible is True
    assert "deletion_scope" in unverified.insufficient_facts


# ---------------------------------------------------------------------------
# Malformed and contradictory requests fail explicitly.
# ---------------------------------------------------------------------------


def test_malformed_operation_kind_rejected() -> None:
    with pytest.raises(TypeError):
        FilesystemStructuralRiskRequest(
            operation="explode",  # type: ignore[arg-type]
            source_path=None,
            destination_path=NEUTRAL_DEST,
        )
    with pytest.raises(TypeError):
        FilesystemStructuralRiskRequest(
            operation=None,  # type: ignore[arg-type]
            source_path=None,
            destination_path=NEUTRAL_DEST,
        )


@pytest.mark.parametrize(
    ("fact", "value"),
    (
        ("source_state", "present"),
        ("source_state", None),
        ("destination_state", 3),
        ("deletion_scope", "empty"),
    ),
)
def test_wrongly_typed_facts_rejected(fact: str, value: object) -> None:
    with pytest.raises(TypeError):
        FilesystemStructuralRiskRequest(
            operation=Op.MOVE,
            source_path=NEUTRAL_SOURCE,
            destination_path=NEUTRAL_DEST,
            **{fact: value},  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("fact", "value"),
    (
        ("overwrite_requested", "true"),
        ("overwrite_requested", 1),
        ("replacement_requested", 0),
    ),
)
def test_non_bool_flags_rejected(fact: str, value: object) -> None:
    with pytest.raises(TypeError):
        FilesystemStructuralRiskRequest(
            operation=Op.MOVE,
            source_path=NEUTRAL_SOURCE,
            destination_path=NEUTRAL_DEST,
            **{fact: value},  # type: ignore[arg-type]
        )


def test_move_requires_source_path() -> None:
    with pytest.raises(ValueError, match="source_path"):
        FilesystemStructuralRiskRequest(
            operation=Op.MOVE,
            source_path=None,
            destination_path=NEUTRAL_DEST,
        )


def test_create_rejects_source_path() -> None:
    with pytest.raises(ValueError, match="source_path"):
        FilesystemStructuralRiskRequest(
            operation=Op.CREATE_FILE,
            source_path=NEUTRAL_SOURCE,
            destination_path=NEUTRAL_DEST,
        )


def test_create_rejects_replacement_flag() -> None:
    with pytest.raises(ValueError, match="replacement_requested"):
        FilesystemStructuralRiskRequest(
            operation=Op.CREATE_FILE,
            source_path=None,
            destination_path=NEUTRAL_DEST,
            replacement_requested=True,
        )


def test_move_rejects_replacement_flag() -> None:
    with pytest.raises(ValueError, match="replacement_requested"):
        _request(Op.MOVE, replacement_requested=True)


def test_rename_rejects_overwrite_flag() -> None:
    with pytest.raises(ValueError, match="overwrite_requested"):
        _request(Op.RENAME, overwrite_requested=True)


def test_delete_rejects_overwrite_and_replacement_flags() -> None:
    with pytest.raises(ValueError, match="overwrite_requested"):
        _request(Op.DELETE, overwrite_requested=True)
    with pytest.raises(ValueError, match="replacement_requested"):
        FilesystemStructuralRiskRequest(
            operation=Op.DELETE,
            source_path=None,
            destination_path=NEUTRAL_DEST,
            replacement_requested=True,
        )


def test_delete_rejects_content_scope_outside_delete() -> None:
    with pytest.raises(ValueError, match="deletion_scope"):
        _request(Op.MOVE, deletion_scope=Scope.NON_EMPTY)


def test_delete_absent_target_cannot_carry_content_claim() -> None:
    for scope in (Scope.EMPTY, Scope.NON_EMPTY):
        with pytest.raises(ValueError, match="contradictory facts"):
            _request(Op.DELETE, destination_state=State.ABSENT, deletion_scope=scope)


def test_non_move_rename_cannot_carry_source_state() -> None:
    with pytest.raises(ValueError, match="source_state"):
        _request(Op.CREATE_FILE, source_state=State.PRESENT)


@pytest.mark.parametrize(
    "path",
    (
        "",
        "   ",
        "/dst/untrimmed ",
        "x" * (MAX_PATH_LENGTH + 1),
    ),
)
def test_malformed_paths_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        FilesystemStructuralRiskRequest(
            operation=Op.CREATE_FILE,
            source_path=None,
            destination_path=path,
        )


def test_non_string_paths_rejected() -> None:
    with pytest.raises(TypeError):
        FilesystemStructuralRiskRequest(
            operation=Op.CREATE_FILE,
            source_path=None,
            destination_path=42,  # type: ignore[arg-type]
        )
