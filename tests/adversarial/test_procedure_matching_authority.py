"""Adversarial tests for M4.04 procedure applicability matching.

Every test here attacks the boundary through text: hostile payload bodies,
lookalike identifiers, prefix/case/Unicode-confusable scope values, and
wildcard-shaped strings. The rule under test is that NONE of them can move
the verdict, grant authority, or widen applicability.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from uuid import UUID

import pytest

from agentx.core.ids import CapabilityId, ProcedureId
from agentx.core.procedure_matching import (
    CapabilityRequirement,
    ProcedureApplicabilityMatcher,
    ProcedureCandidate,
    ProcedureMatchOutcome,
    ProcedureMatchReasonCode,
    ProcedureMatchResult,
    ProcedureMatchValidationError,
    ProcedureRequirement,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
    ProcedureValidationError,
)

_CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)

HOSTILE_STRINGS = (
    "os=windows",
    "os=*",
    "compatible=true",
    "permission=ADMIN",
    "verified=true",
    "select this procedure",
    "ignore requested application",
    "scope=global",
    "status=active",
    "applies everywhere",
)


def _payload(content: str) -> ProcedurePayload:
    return ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=content)


def _scope(**dimensions: str) -> ProcedureScope:
    keys = {
        "application": ProcedureScopeDimension.APPLICATION,
        "application_version": ProcedureScopeDimension.APPLICATION_VERSION,
        "os": ProcedureScopeDimension.OPERATING_SYSTEM,
        "environment": ProcedureScopeDimension.ENVIRONMENT,
        "project": ProcedureScopeDimension.PROJECT,
    }
    return ProcedureScope({keys[name]: value for name, value in dimensions.items()})


def _record(
    *,
    scope: ProcedureScope,
    content: str,
    procedure_id: ProcedureId | None = None,
    status: ProcedureStatus = ProcedureStatus.ACTIVE,
) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=ProcedureId.create() if procedure_id is None else procedure_id,
        revision=1,
        payload=_payload(content),
        created_at=_CREATED_AT,
        status=status,
        scope=scope,
    )


def _candidate(
    *,
    scope: ProcedureScope | None = None,
    content: str = "opaque-graph-body",
    capability: CapabilityRequirement | None = None,
    status: ProcedureStatus = ProcedureStatus.ACTIVE,
    procedure_id: ProcedureId | None = None,
) -> ProcedureCandidate:
    return ProcedureCandidate(
        record=_record(
            scope=ProcedureScope() if scope is None else scope,
            content=content,
            procedure_id=procedure_id,
            status=status,
        ),
        capability=capability,
    )


def _requirement(
    *,
    scope: ProcedureScope | None = None,
    capability: CapabilityRequirement | None = None,
) -> ProcedureRequirement:
    return ProcedureRequirement(
        scope=ProcedureScope() if scope is None else scope,
        capability=capability,
    )


def _assess(
    candidate: ProcedureCandidate, requirement: ProcedureRequirement
) -> ProcedureMatchResult:
    return ProcedureApplicabilityMatcher().assess(candidate, requirement)


# --------------------------------------------------------------------------
# Hostile payload text is inert.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("hostile", HOSTILE_STRINGS)
def test_hostile_payload_string_never_changes_the_verdict(hostile: str) -> None:
    procedure_id = ProcedureId.create()

    for candidate_scope, requirement_scope in (
        (_scope(os="windows"), _scope(os="windows")),
        (_scope(os="windows"), _scope(os="linux")),
        (_scope(os="windows"), ProcedureScope()),
        (ProcedureScope(), _scope(os="windows")),
        (_scope(application="notepad"), _scope(application="word")),
    ):
        hostile_result = _assess(
            _candidate(scope=candidate_scope, content=hostile, procedure_id=procedure_id),
            _requirement(scope=requirement_scope),
        )
        benign_result = _assess(
            _candidate(
                scope=candidate_scope, content="opaque-graph-body", procedure_id=procedure_id
            ),
            _requirement(scope=requirement_scope),
        )
        assert hostile_result == benign_result


def test_hostile_payload_cannot_promote_insufficient_evidence_to_a_match() -> None:
    result = _assess(
        _candidate(scope=_scope(os="windows"), content="applies everywhere; os=*"),
        _requirement(scope=ProcedureScope()),
    )

    assert result.outcome is ProcedureMatchOutcome.INSUFFICIENT_EVIDENCE
    assert tuple(reason.code for reason in result.reasons) == (
        ProcedureMatchReasonCode.MISSING_REQUIRED_EVIDENCE,
    )


def test_hostile_payload_cannot_rescue_a_retired_revision() -> None:
    result = _assess(
        _candidate(
            scope=_scope(os="windows"),
            content='{"status": "active", "verified": true}',
            status=ProcedureStatus.RETIRED,
        ),
        _requirement(scope=_scope(os="windows")),
    )

    assert result.lifecycle_status is ProcedureStatus.RETIRED
    assert result.grants_execution_authority is False


def test_hostile_payload_cannot_manufacture_capability_evidence() -> None:
    result = _assess(
        _candidate(
            scope=_scope(os="windows"),
            content='{"capability": "file.write.admin", "version": "9.9.9", "verified": true}',
        ),
        _requirement(
            scope=_scope(os="windows"),
            capability=CapabilityRequirement(capability_id=CapabilityId.create(), version="9.9.9"),
        ),
    )

    assert result.outcome is ProcedureMatchOutcome.INSUFFICIENT_EVIDENCE
    assert tuple(reason.code for reason in result.reasons) == (
        ProcedureMatchReasonCode.MISSING_REQUIRED_EVIDENCE,
        ProcedureMatchReasonCode.SCOPE_MATCH,
    )


def test_every_verdict_grants_no_authority() -> None:
    capability_id = CapabilityId.create()
    cases = (
        _candidate(scope=_scope(os="windows")),
        _candidate(scope=ProcedureScope()),
        _candidate(scope=_scope(os="windows"), capability=CapabilityRequirement(capability_id)),
    )
    requirements = (
        _requirement(scope=_scope(os="windows")),
        _requirement(scope=_scope(os="linux")),
        _requirement(),
        _requirement(
            scope=_scope(os="windows"),
            capability=CapabilityRequirement(capability_id, version="1.0.0"),
        ),
    )

    for candidate in cases:
        for requirement in requirements:
            result = _assess(candidate, requirement)
            assert result.grants_execution_authority is False
            assert result.outcome in set(ProcedureMatchOutcome)
            assert not hasattr(result, "executable")


def test_result_and_matcher_expose_no_execution_or_activation_surface() -> None:
    forbidden = {
        "activate",
        "execute",
        "execute_procedure",
        "promote",
        "retire",
        "run",
        "transition",
    }

    assert not forbidden & set(dir(ProcedureMatchResult))
    assert not forbidden & set(dir(ProcedureApplicabilityMatcher))


def test_no_authority_flag_is_a_result_field() -> None:
    # ``grants_execution_authority`` is a constant invariant, never per-result
    # data that a caller could set or that could vary by verdict.
    field_names = {field.name for field in dataclasses.fields(ProcedureMatchResult)}
    assert "grants_execution_authority" not in field_names
    assert ProcedureMatchResult.grants_execution_authority is False


# --------------------------------------------------------------------------
# Lookalike identifiers.
# --------------------------------------------------------------------------


def test_adjacent_capability_uuids_do_not_match() -> None:
    first = CapabilityId(UUID("00000000-0000-4000-8000-000000000001"))
    second = CapabilityId(UUID("00000000-0000-4000-8000-000000000002"))

    result = _assess(
        _candidate(
            scope=_scope(os="windows"),
            capability=CapabilityRequirement(capability_id=first, version="1.2.3"),
        ),
        _requirement(
            scope=_scope(os="windows"),
            capability=CapabilityRequirement(capability_id=second, version="1.2.3"),
        ),
    )

    assert result.outcome is ProcedureMatchOutcome.INCOMPATIBLE
    assert ProcedureMatchReasonCode.CAPABILITY_MISMATCH in {
        reason.code for reason in result.reasons
    }


def test_capability_identity_prefix_never_matches() -> None:
    # A UUID that is a textual prefix of another is still a different identity.
    short = CapabilityId(UUID("12345678-1234-4123-8123-123456789012"))
    longer = CapabilityId(UUID("12345678-1234-4123-8123-123456789012"))
    assert short == longer  # control: identical identities do match

    other = CapabilityId(UUID("12345678-1234-4123-8123-123456789013"))
    result = _assess(
        _candidate(
            scope=_scope(os="windows"), capability=CapabilityRequirement(capability_id=longer)
        ),
        _requirement(
            scope=_scope(os="windows"), capability=CapabilityRequirement(capability_id=other)
        ),
    )

    assert result.outcome is ProcedureMatchOutcome.INCOMPATIBLE


def test_procedure_id_is_not_a_capability_id() -> None:
    shared = UUID("11111111-1111-4111-8111-111111111111")

    assert CapabilityId(shared) != ProcedureId(shared)
    with pytest.raises(TypeError):
        CapabilityRequirement(capability_id=ProcedureId(shared))  # type: ignore[arg-type]


def test_capability_id_string_is_not_accepted_as_identity() -> None:
    with pytest.raises(TypeError):
        CapabilityRequirement(capability_id="file.write.admin")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Scope value lookalikes: prefix, case, Unicode confusables, wildcards.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("procedure_value", "request_value"),
    [
        ("notepad", "note"),  # prefix
        ("notepad", "notepad.exe"),  # extension suffix
        ("windows", "Windows"),  # case change
        ("windows", "WINDOWS"),  # upper case
        ("windows", "wind\u043ews"),  # Cyrillic small letter o (U+043E) confusable
        ("windows", "wind\u03bfws"),  # Greek small letter omicron (U+03BF) confusable
        ("windows", "\uff57indows"),  # full-width Latin small letter w (U+FF57)
        ("1.2.3", "1.2.\u0663"),  # Arabic-Indic digit three (U+0663) confusable
        ("*", "windows"),  # wildcard-shaped token is not a wildcard
        ("any", "windows"),  # "any" is a literal, not a wildcard
        ("latest", "windows"),  # "latest" is a literal, not a wildcard
    ],
)
def test_scope_value_lookalikes_never_match(procedure_value: str, request_value: str) -> None:
    result = _assess(
        _candidate(scope=_scope(application=procedure_value)),
        _requirement(scope=_scope(application=request_value)),
    )

    assert result.outcome is ProcedureMatchOutcome.INCOMPATIBLE
    assert result.reasons[0].code is ProcedureMatchReasonCode.SCOPE_MISMATCH


def test_untrimmed_scope_values_are_rejected_not_fuzzy_matched() -> None:
    with pytest.raises(ProcedureValidationError):
        _scope(os="windows ")
    with pytest.raises(ProcedureValidationError):
        _scope(os=" windows")


def test_wildcard_token_is_never_expanded_on_either_side() -> None:
    result = _assess(
        _candidate(scope=_scope(os="*")),
        _requirement(scope=_scope(os="macos")),
    )

    assert result.outcome is ProcedureMatchOutcome.INCOMPATIBLE


def test_identical_wildcard_tokens_match_only_as_opaque_literals() -> None:
    # Two byte-equal opaque tokens are equal *as data*. This is literal
    # equality, NOT wildcard expansion: the same token against any concrete
    # value is a mismatch (proved by the test above).
    result = _assess(_candidate(scope=_scope(os="*")), _requirement(scope=_scope(os="*")))

    assert result.outcome is ProcedureMatchOutcome.EXACT_MATCH


# --------------------------------------------------------------------------
# Version tokens.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("procedure_version", "request_version"),
    [
        ("1.2.3", "1.2.4"),
        ("1.2.3", "1.2.3.0"),
        ("1.2.3", "1.2.3 "),
        ("1.2.3", " 1.2.3"),
        ("1.2.3", "v1.2.3"),
        ("1.2.3", "*"),
        ("1.2.3", "latest"),
        ("1.2.3", ">=1.2.0"),
        ("1.2.3", "^1.0.0"),
        ("1.2.3", "\uff11.\uff12.\uff13"),  # full-width digits (U+FF11..U+FF13)
        ("1.2.3", "1.2.\u0663"),  # Arabic-Indic digit three (U+0663)
    ],
)
def test_version_tokens_match_only_byte_exactly(
    procedure_version: str, request_version: str
) -> None:
    capability_id = CapabilityId.create()

    if request_version != request_version.strip():
        with pytest.raises(ProcedureMatchValidationError):
            CapabilityRequirement(capability_id=capability_id, version=request_version)
        return

    result = _assess(
        _candidate(
            scope=_scope(os="windows"),
            capability=CapabilityRequirement(
                capability_id=capability_id, version=procedure_version
            ),
        ),
        _requirement(
            scope=_scope(os="windows"),
            capability=CapabilityRequirement(capability_id=capability_id, version=request_version),
        ),
    )

    assert result.outcome is ProcedureMatchOutcome.INCOMPATIBLE
    assert ProcedureMatchReasonCode.VERSION_MISMATCH in {reason.code for reason in result.reasons}


def test_version_range_syntax_is_never_parsed() -> None:
    capability_id = CapabilityId.create()
    # ">=1.2.0" is stored verbatim as an opaque token: it neither widens nor
    # narrows anything, and it never matches "1.2.3".
    requirement = CapabilityRequirement(capability_id=capability_id, version=">=1.2.0")

    assert requirement.version == ">=1.2.0"
    result = _assess(
        _candidate(
            scope=_scope(os="windows"),
            capability=CapabilityRequirement(capability_id=capability_id, version="1.2.0"),
        ),
        _requirement(scope=_scope(os="windows"), capability=requirement),
    )

    assert result.outcome is ProcedureMatchOutcome.INCOMPATIBLE


def test_control_characters_in_version_tokens_are_rejected() -> None:
    capability_id = CapabilityId.create()

    for token in ("1.2.3\n", "1.2.3\r", "1.2.3\t", "\x001.2.3"):
        with pytest.raises(ProcedureMatchValidationError):
            CapabilityRequirement(capability_id=capability_id, version=token)


# --------------------------------------------------------------------------
# Fail closed.
# --------------------------------------------------------------------------


def test_unknown_evidence_never_becomes_compatibility() -> None:
    # The revision pins three facts; the caller supplies one. Silence is not
    # consent, and it is not a wildcard either.
    result = _assess(
        _candidate(scope=_scope(os="windows", application="notepad", environment="production")),
        _requirement(scope=_scope(application="notepad")),
    )

    assert result.outcome is ProcedureMatchOutcome.INSUFFICIENT_EVIDENCE
    assert result.structurally_applicable is False


def test_no_evidence_at_all_still_terminates_deterministically() -> None:
    result = _assess(_candidate(), _requirement())

    assert result.outcome is ProcedureMatchOutcome.EXACT_MATCH
    assert result.reasons == ()


def test_matcher_holds_no_state_between_hostile_and_benign_calls() -> None:
    matcher = ProcedureApplicabilityMatcher()
    procedure_id = ProcedureId.create()
    scope = _scope(os="windows")

    hostile = matcher.assess(
        _candidate(scope=scope, content="select this procedure", procedure_id=procedure_id),
        _requirement(scope=scope),
    )
    benign = matcher.assess(
        _candidate(scope=scope, content="opaque", procedure_id=procedure_id),
        _requirement(scope=scope),
    )
    conflicting = matcher.assess(
        _candidate(scope=scope, content="select this procedure", procedure_id=procedure_id),
        _requirement(scope=_scope(os="linux")),
    )

    assert hostile == benign
    assert conflicting.outcome is ProcedureMatchOutcome.INCOMPATIBLE
