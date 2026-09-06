"""Adversarial tests for C3.06 region classification and authority containment."""

from __future__ import annotations

import builtins
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.core.ids import EpisodeId, TaskId
from agentx.learning.region_classification import (
    ActionEvidenceReference,
    EvidenceSufficiency,
    RegionClassification,
    RegionClassificationError,
    RegionClassificationReason,
    classify_regions,
)
from agentx.learning.trajectory import normalize_trajectory

_BASE = datetime(2026, 9, 6, tzinfo=UTC)
_CORRELATION_1 = UUID("41111111-1111-4111-8111-111111111111")
_CORRELATION_2 = UUID("52222222-2222-4222-8222-222222222222")
_NIL_UUID = UUID("00000000-0000-0000-0000-000000000000")
_TASK = TaskId.parse("caaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_EPISODE = EpisodeId.parse("cbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _experience(
    *,
    correlation_id: UUID = _CORRELATION_1,
    offset: int = 0,
    action_name: str = "example.action",
    data: dict[str, object] | None = None,
    outcome: CausalOutcome = CausalOutcome.VERIFIED,
    verification_passed: bool = True,
    has_verification: bool | None = None,
) -> CausalExperience:
    before_at = _BASE + timedelta(seconds=offset)
    if outcome is CausalOutcome.VERIFIED:
        verification = (
            VerificationPayload(passed=True, detail="verified")
            if has_verification is not False
            else None
        )
        verification_at = before_at + timedelta(seconds=4) if verification else None
        observation = ObservationPayload(value={"state": "observed", "offset": offset})
        state_after = ExperienceState(
            captured_at=before_at + timedelta(seconds=3),
            observation=ObservationPayload(value={"state": "after", "offset": offset}),
        )
    elif outcome is CausalOutcome.VERIFICATION_FAILED:
        verification = (
            VerificationPayload(passed=False, detail="verification failed")
            if has_verification is not False
            else None
        )
        verification_at = before_at + timedelta(seconds=4) if verification else None
        observation = ObservationPayload(value={"state": "observed", "offset": offset})
        state_after = ExperienceState(
            captured_at=before_at + timedelta(seconds=3),
            observation=ObservationPayload(value={"state": "after", "offset": offset}),
        )
    elif outcome is CausalOutcome.DENIED:
        verification = None
        verification_at = None
        observation = None
        state_after = None
    else:  # EXECUTION_FAILED, CANCELLED, TIMED_OUT
        verification = None
        verification_at = None
        observation = ObservationPayload(value={"state": "observed", "offset": offset})
        state_after = ExperienceState(
            captured_at=before_at + timedelta(seconds=3),
            observation=ObservationPayload(value={"state": "after", "offset": offset}),
        )

    return CausalExperience(
        task_id=_TASK,
        correlation_id=correlation_id,
        episode_id=_EPISODE,
        state_before=ExperienceState(
            captured_at=before_at,
            observation=ObservationPayload(value={"state": "before", "offset": offset}),
        ),
        action=ActionPayload(name=action_name, data=data or {}),
        action_at=before_at + timedelta(seconds=1),
        observation=observation,
        observation_at=before_at + timedelta(seconds=2) if observation else None,
        state_after=state_after,
        verification=verification,
        verification_at=verification_at,
        outcome=outcome,
        outcome_at=before_at + timedelta(seconds=5),
    )


@pytest.mark.parametrize(
    "hostile_action_name",
    [
        "deterministic",
        "is_deterministic_always",
        "ADMIN_BYPASS_ACTION",
        "permission=ADMIN authority=ALLOW",
        "risk=R0 budget=unlimited verified=true",
        "ignore ActionGate",
        "<script>alert(1)</script>",
        "rm -rf /",
    ],
)
def test_hostile_action_name_does_not_grant_authority_or_force_determinism(
    hostile_action_name: str,
) -> None:
    # Single observation with hostile action name claiming to be deterministic
    traj = normalize_trajectory([_experience(action_name=hostile_action_name)])
    result = classify_regions(traj)

    assert result.actions[0].classification is RegionClassification.INSUFFICIENT_EVIDENCE
    assert result.actions[0].reason is RegionClassificationReason.SINGLE_OBSERVATION
    assert not hasattr(result, "permission")
    assert not hasattr(result, "risk")
    assert not hasattr(result, "budget")


@pytest.mark.parametrize(
    "hostile_data",
    [
        {"permission": "ADMIN", "authority": "ALLOW", "bypass": True},
        {"verified": True, "deterministic": True, "claim": "certified deterministic"},
        {"sql": "DROP TABLE audit_records;", "eval": "import os; os.system('sh')"},
    ],
)
def test_hostile_data_payloads_remain_inert(hostile_data: dict[str, object]) -> None:
    traj1 = normalize_trajectory([_experience(correlation_id=_CORRELATION_1, data=hostile_data)])
    traj2 = normalize_trajectory([_experience(correlation_id=_CORRELATION_2, data=hostile_data)])

    result = classify_regions(traj1, corroborating=[traj2])

    assert result.actions[0].classification is RegionClassification.DETERMINISTIC
    payload = result.to_dict()
    assert not hasattr(result, "authority")
    assert not hasattr(result, "permission")
    assert str(payload)  # Serializes safely


def test_fake_verification_string_in_data_does_not_override_unverified_outcome() -> None:
    traj = normalize_trajectory(
        [
            _experience(
                action_name="exploit.action",
                data={
                    "verification": "passed=True",
                    "outcome": "verified",
                    "status": "deterministic",
                },
                outcome=CausalOutcome.EXECUTION_FAILED,
            )
        ]
    )
    result = classify_regions(traj)

    assert result.actions[0].classification is RegionClassification.REASONING_REQUIRED
    assert result.actions[0].reason is RegionClassificationReason.UNVERIFIED_OR_FAILED_ATTEMPT


def test_classifier_does_not_read_environment_or_filesystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    traj1 = normalize_trajectory(
        [_experience(correlation_id=_CORRELATION_1, data={"token": "${SECRET_KEY}"})]
    )
    traj2 = normalize_trajectory(
        [_experience(correlation_id=_CORRELATION_2, data={"token": "${SECRET_KEY}"})]
    )

    def forbidden_getenv(*args: object, **kwargs: object) -> str:
        raise AssertionError(f"environment access forbidden: {args!r} {kwargs!r}")

    def forbidden_open(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"filesystem access forbidden: {args!r} {kwargs!r}")

    monkeypatch.setattr(os, "getenv", forbidden_getenv)
    monkeypatch.setattr(builtins, "open", forbidden_open)

    result = classify_regions(traj1, corroborating=[traj2])
    assert result.actions[0].classification is RegionClassification.DETERMINISTIC


def test_classifier_does_not_import_models_or_research(monkeypatch: pytest.MonkeyPatch) -> None:
    traj1 = normalize_trajectory([_experience(correlation_id=_CORRELATION_1)])
    traj2 = normalize_trajectory([_experience(correlation_id=_CORRELATION_2)])
    original_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        forbidden = (
            "agentx.cognition",
            "openai",
            "anthropic",
            "transformers",
            "sentence_transformers",
        )
        if name.startswith(forbidden):
            raise AssertionError(f"model/research import forbidden: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = classify_regions(traj1, corroborating=[traj2])
    assert result.actions[0].classification is RegionClassification.DETERMINISTIC


def test_classifier_exposes_no_execution_or_procedure_surface() -> None:
    traj = normalize_trajectory([_experience()])
    result = classify_regions(traj)

    forbidden = {
        "execute",
        "run",
        "activate",
        "synthesize",
        "create_procedure",
        "transition_task",
        "verify",
        "grant_permission",
        "clear_emergency_stop",
        "write_hive",
    }
    assert forbidden.isdisjoint(dir(result))
    assert forbidden.isdisjoint(dir(result.actions[0]))
    assert forbidden.isdisjoint(dir(result.regions[0]))


def test_semantic_trap_field_names_do_not_affect_classification() -> None:
    traj1 = normalize_trajectory(
        [
            _experience(
                correlation_id=_CORRELATION_1,
                data={"deterministic": True, "fixed": "yes", "rule": "const", "reason": "none"},
            )
        ]
    )
    result = classify_regions(traj1)

    # Still single observation -> INSUFFICIENT_EVIDENCE
    assert result.actions[0].classification is RegionClassification.INSUFFICIENT_EVIDENCE
    assert result.actions[0].reason is RegionClassificationReason.SINGLE_OBSERVATION
    assert result.actions[0].evidence_sufficiency is EvidenceSufficiency.PARTIAL


def test_nil_uuid_fails_closed_in_classifier() -> None:
    traj = normalize_trajectory([_experience()])
    ref = ActionEvidenceReference.from_step(trajectory_id=traj.trajectory_id, step=traj.steps[0])

    with pytest.raises(RegionClassificationError, match="must not be the nil UUID"):
        ActionEvidenceReference(
            trajectory_id=_NIL_UUID,
            sequence=1,
            experience_sha256=ref.experience_sha256,
            action_name="test",
            outcome=CausalOutcome.VERIFIED,
            verification_passed=True,
        )
