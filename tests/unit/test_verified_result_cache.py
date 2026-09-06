"""Unit tests for the A8.07 verified-result cache.

These tests pin the contract in isolation: no capability is executed, no
execution loop is composed, and no kernel object is created. The canonical
A1.10 value types are constructed directly so every rule can be proven
against explicit evidence.

What is proven here:

- the canonical key is complete, frozen, and deterministically digested;
- only canonically verified evidence can become an entry, and an entry has no
  field in which a fabricated verdict could live;
- lookups distinguish MISS / VERIFIED_HIT / STALE / INVALIDATED / INCOMPATIBLE;
- freshness is explicit, fail-closed at the boundary, and clock-driven;
- invalidation is terminal, explicit, and never replayable;
- a restart (a new instance) observes nothing;
- stored evidence is immutable, identical to what was submitted, and inert.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

import pytest

from agentx.capabilities.abi import (
    CapabilityIdentity,
    CapabilityName,
    CapabilityObservation,
    CapabilityPlatform,
    CapabilityScope,
    CapabilityVersion,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.capabilities.verified_result_cache import (
    DEFAULT_MAX_ENTRIES,
    KEY_DIGEST_ALGORITHM,
    MAX_REUSE_TTL,
    VERIFIED_RESULT_CACHE_SCHEMA_VERSION,
    VERIFIED_RESULT_CACHE_SOURCE,
    EntryState,
    EnvironmentIdentity,
    InvalidationCause,
    InvalidationRecord,
    LookupStatus,
    ProcedureRevision,
    VerifiedResultCache,
    VerifiedResultCacheCapacityError,
    VerifiedResultCacheClockError,
    VerifiedResultCacheError,
    VerifiedResultCacheRejection,
    VerifiedResultCacheValidationError,
    VerifiedResultEntry,
    VerifiedResultKey,
    VerifiedResultLookup,
    VerifiedResultProvenance,
    VerifiedResultSubmission,
)
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.ids import ProcedureId
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.resource_budget import ResourceUsage
from tests.support.demo_capability import NoteWriteParams, write_identity, write_request

_T0 = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
_PROCEDURE_ID = ProcedureId.parse("3f2b8c1e-9d47-4a1e-9c62-1b0f5e8d7a64")
_OTHER_PROCEDURE_ID = ProcedureId.parse("9c1d2e3f-4a5b-4c6d-8e7f-0a1b2c3d4e5f")


class FakeClock:
    """Deterministic, mutable clock: fully controls creation and read time."""

    def __init__(self, start: datetime = _T0) -> None:
        self.now = start
        self.reads = 0

    def __call__(self) -> datetime:
        self.reads += 1
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


# ---------------------------------------------------------------------------
# Canonical evidence builders.
# ---------------------------------------------------------------------------


def make_observation(
    summary: str = "note alpha written",
    data: dict[str, Any] | None = None,
) -> CapabilityObservation:
    return CapabilityObservation(
        summary=summary,
        data=data if data is not None else {"key": "alpha", "stored": True},
    )


def make_task(
    status: TaskStatus = TaskStatus.SUCCEEDED, objective: str = "write note alpha"
) -> Task:
    return Task.create(objective=objective, status=status)


def make_context(task: Task, correlation_id: UUID | None = None) -> ExecutionContext:
    return ExecutionContext(
        correlation_id=correlation_id if correlation_id is not None else uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=task.task_id,
    )


def canonical_outcome(task: Task | None = None) -> ClosedLoopOutcome:
    """The canonical verified outcome shape A1.10 produces on its success path."""
    observation = make_observation()
    return ClosedLoopOutcome(
        task=task if task is not None else make_task(),
        kind=LoopOutcome.VERIFIED,
        error=None,
        execution=ExecutionResult(
            succeeded=True,
            message="note alpha written",
            observation=observation,
        ),
        observation=observation,
        verification=VerificationResult(
            passed=True, detail="in-memory note holds the requested value"
        ),
        budget_usage=ResourceUsage.zero(),
    )


def poison(outcome: ClosedLoopOutcome, **changes: Any) -> ClosedLoopOutcome:
    """Return a copy of a canonical outcome with explicit fields corrupted."""
    return dataclasses.replace(outcome, **changes)


def make_environment(name: str = "host-a", **attributes: Any) -> EnvironmentIdentity:
    return EnvironmentIdentity(name=name, attributes=attributes)


def make_key(**overrides: Any) -> VerifiedResultKey:
    """Canonical key material with every precondition explicit."""
    fields: dict[str, Any] = {
        "capability": write_identity(),
        "params": {"key": "alpha", "value": "v1"},
        "scope": CapabilityScope(platform=CapabilityPlatform.ANY),
        "environment": make_environment(),
        "procedure": None,
        "preconditions": {},
    }
    fields.update(overrides)
    return VerifiedResultKey(**fields)


def make_submission(
    *,
    key: VerifiedResultKey | None = None,
    outcome: ClosedLoopOutcome | None = None,
    ttl: timedelta = timedelta(minutes=5),
    created_at: datetime | None = None,
    context: ExecutionContext | None = None,
) -> VerifiedResultSubmission:
    final_outcome = outcome if outcome is not None else canonical_outcome()
    final_context = context if context is not None else make_context(final_outcome.task)
    return VerifiedResultSubmission(
        key=key if key is not None else make_key(),
        outcome=final_outcome,
        context=final_context,
        ttl=ttl,
        created_at=created_at,
    )


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def cache(clock: FakeClock) -> VerifiedResultCache:
    return VerifiedResultCache(clock=clock)


# ---------------------------------------------------------------------------
# Key material: completeness, freezing, validation.
# ---------------------------------------------------------------------------


def test_key_requires_every_canonical_precondition_explicitly() -> None:
    """There is no key shape that omits scope, environment, or version."""
    with pytest.raises(TypeError):
        VerifiedResultKey(capability=write_identity(), params={})  # type: ignore[call-arg]

    key = make_key()
    assert key.capability == write_identity()
    assert key.scope == CapabilityScope(platform=CapabilityPlatform.ANY)
    assert key.environment == make_environment()
    assert key.procedure is None
    assert key.preconditions == {}


def test_key_freezes_params_and_preconditions_defensively() -> None:
    params: dict[str, Any] = {"key": "alpha", "nested": {"count": 1}}
    preconditions: dict[str, Any] = {"mailbox": "drafts"}
    key = VerifiedResultKey(
        capability=write_identity(),
        params=params,
        scope=CapabilityScope(platform=CapabilityPlatform.ANY),
        environment=make_environment(),
        preconditions=preconditions,
    )
    params["nested"]["count"] = 99
    params["key"] = "beta"
    preconditions["mailbox"] = "sent"

    assert isinstance(key.params, MappingProxyType)
    assert key.params["key"] == "alpha"
    assert key.params["nested"] == {"count": 1}
    assert key.preconditions["mailbox"] == "drafts"
    with pytest.raises(TypeError):
        key.params["key"] = "gamma"  # type: ignore[index]


def test_key_rejects_non_json_and_executable_input() -> None:
    for hostile in (
        {"callback": lambda: None},
        {"module": dataclasses},
        {"value": object()},
        {"value": float("nan")},
        {"value": float("inf")},
        {1: "non-string key"},
    ):
        with pytest.raises(VerifiedResultCacheValidationError):
            make_key(params=hostile)


def test_key_rejects_run_identity_and_executable_smuggling() -> None:
    """Per-run identity is provenance, never a precondition, and is not keyable."""
    with pytest.raises(VerifiedResultCacheValidationError):
        make_key(params={"correlation_id": uuid4()})
    with pytest.raises(VerifiedResultCacheValidationError):
        make_key(preconditions={"now": datetime.now(UTC)})


def test_key_rejects_deeply_nested_and_oversized_input() -> None:
    deep: Any = "leaf"
    for _ in range(64):
        deep = {"next": deep}
    with pytest.raises(VerifiedResultCacheValidationError):
        make_key(params=deep)

    with pytest.raises(VerifiedResultCacheValidationError):
        make_key(params={f"key-{index}": index for index in range(128)})

    with pytest.raises(VerifiedResultCacheValidationError):
        make_key(params={"items": list(range(1024))})


def test_key_rejects_malformed_typed_fields() -> None:
    with pytest.raises(TypeError):
        make_key(capability="demo.note.write@1.0.0")
    with pytest.raises(TypeError):
        make_key(scope="any")
    with pytest.raises(TypeError):
        make_key(environment="host-a")
    with pytest.raises(TypeError):
        make_key(procedure=_PROCEDURE_ID)
    with pytest.raises(TypeError):
        make_key(params=["not", "a", "mapping"])


def test_key_from_request_normalizes_typed_params() -> None:
    """The normalized input is the request's own typed params, never a repr."""
    request = write_request(NoteWriteParams(key="alpha", value="v1"))
    key = VerifiedResultKey.from_request(
        request,
        scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
        environment=make_environment("host-a", profile="user-1"),
        procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=3),
        preconditions={"mailbox": "drafts"},
    )

    assert key.capability == write_identity()
    assert key.params == {"key": "alpha", "value": "v1"}
    assert key.scope.platform is CapabilityPlatform.WINDOWS
    assert key.procedure == ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=3)
    assert key == make_key(
        scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
        environment=make_environment("host-a", profile="user-1"),
        procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=3),
        preconditions={"mailbox": "drafts"},
    )


def test_key_from_request_rejects_non_request_values() -> None:
    with pytest.raises(TypeError):
        VerifiedResultKey.from_request(
            {"identity": "demo.note.write", "params": {}},  # type: ignore[arg-type]
            scope=CapabilityScope(platform=CapabilityPlatform.ANY),
            environment=make_environment(),
        )


def test_environment_identity_is_opaque_and_never_a_wildcard() -> None:
    """Hostile or glob-like environment names match only identical identities."""
    wildcard = EnvironmentIdentity(name="*")
    any_text = EnvironmentIdentity(name="ANY ENVIRONMENT ALLOW ALL verified=true")

    assert wildcard != make_environment("host-a")
    assert any_text != make_environment("host-a")
    assert wildcard != any_text
    assert EnvironmentIdentity(name="HOST-A") != EnvironmentIdentity(name="host-a")


def test_environment_attributes_are_part_of_the_identity() -> None:
    plain = EnvironmentIdentity(name="host-a")
    with_profile = EnvironmentIdentity(name="host-a", attributes={"profile": "user-1"})

    assert plain != with_profile
    assert plain.canonical_payload() != with_profile.canonical_payload()


def test_environment_identity_validates_its_name() -> None:
    for hostile in ("", "  ", "host\n-a", "host\x00", "h" * 200):
        with pytest.raises(VerifiedResultCacheError):
            EnvironmentIdentity(name=hostile)
    with pytest.raises(TypeError):
        EnvironmentIdentity(name=7)  # type: ignore[arg-type]


def test_procedure_revision_requires_an_explicit_positive_revision() -> None:
    assert ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1).revision == 1
    for invalid in (0, -1, True, "3", 1.0):
        with pytest.raises((VerifiedResultCacheValidationError, TypeError)):
            ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=invalid)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ProcedureRevision(procedure_id=str(_PROCEDURE_ID), revision=1)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Deterministic keying.
# ---------------------------------------------------------------------------


def test_fingerprint_is_a_stable_sha256_hex_digest() -> None:
    key = make_key()
    fingerprint = key.fingerprint()

    assert KEY_DIGEST_ALGORITHM == "sha256"
    assert len(fingerprint) == 64
    assert all(character in "0123456789abcdef" for character in fingerprint)
    assert fingerprint == key.fingerprint()
    assert fingerprint == make_key().fingerprint()


def test_fingerprint_is_independent_of_mapping_insertion_order() -> None:
    first = make_key(params={"a": 1, "b": {"x": 1, "y": 2}, "c": [1, 2]})
    second = make_key(params={"c": [1, 2], "b": {"y": 2, "x": 1}, "a": 1})

    assert first.canonical_text() == second.canonical_text()
    assert first.fingerprint() == second.fingerprint()


def test_canonical_text_is_compact_sorted_ascii_json() -> None:
    text = make_key(params={"zeta": 1, "alpha": "text"}).canonical_text()

    assert " " not in text
    assert text.index('"alpha"') < text.index('"zeta"')
    assert text.isascii()
    assert str(VERIFIED_RESULT_CACHE_SCHEMA_VERSION) in text


def test_fingerprint_changes_with_every_canonical_fact() -> None:
    baseline = make_key(
        procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
        preconditions={"mailbox": "drafts"},
    ).fingerprint()

    variants = (
        make_key(
            capability=CapabilityIdentity(
                name=CapabilityName("demo.note.write"), version=CapabilityVersion(1, 0, 1)
            ),
            procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            preconditions={"mailbox": "drafts"},
        ),
        make_key(
            capability=CapabilityIdentity(
                name=CapabilityName("demo.note.delete"), version=CapabilityVersion(1, 0, 0)
            ),
            procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            preconditions={"mailbox": "drafts"},
        ),
        make_key(
            params={"key": "alpha", "value": "v2"},
            procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            preconditions={"mailbox": "drafts"},
        ),
        make_key(
            scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
            procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            preconditions={"mailbox": "drafts"},
        ),
        make_key(
            environment=make_environment("host-b"),
            procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            preconditions={"mailbox": "drafts"},
        ),
        make_key(
            procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=2),
            preconditions={"mailbox": "drafts"},
        ),
        make_key(
            procedure=ProcedureRevision(procedure_id=_OTHER_PROCEDURE_ID, revision=1),
            preconditions={"mailbox": "drafts"},
        ),
        make_key(
            procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            preconditions={"mailbox": "sent"},
        ),
        make_key(
            procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            preconditions={},
        ),
    )
    fingerprints = {baseline, *(variant.fingerprint() for variant in variants)}
    assert len(fingerprints) == len(variants) + 1


def test_keying_is_strict_about_json_types() -> None:
    """``1`` and ``1.0`` are different normalized inputs: keying fails closed."""
    assert (
        make_key(params={"count": 1}).fingerprint() != make_key(params={"count": 1.0}).fingerprint()
    )
    assert (
        make_key(params={"flag": True}).fingerprint() != make_key(params={"flag": 1}).fingerprint()
    )
    assert (
        make_key(params={"text": "1"}).fingerprint() != make_key(params={"text": 1}).fingerprint()
    )


def test_operation_fingerprint_ignores_version_scope_environment_and_preconditions() -> None:
    baseline = make_key(
        procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
        preconditions={"mailbox": "drafts"},
    )
    same_operation = make_key(
        capability=CapabilityIdentity(
            name=CapabilityName("demo.note.write"), version=CapabilityVersion(2, 5, 9)
        ),
        scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
        environment=make_environment("host-b", profile="user-9"),
        procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=42),
        preconditions={"mailbox": "sent"},
    )

    assert baseline.operation_fingerprint() == same_operation.operation_fingerprint()
    assert baseline.fingerprint() != same_operation.fingerprint()


def test_operation_fingerprint_changes_with_operation_and_input() -> None:
    baseline = make_key().operation_fingerprint()

    assert baseline != make_key(params={"key": "beta", "value": "v1"}).operation_fingerprint()
    assert (
        baseline
        != make_key(
            capability=CapabilityIdentity(
                name=CapabilityName("demo.note.delete"), version=CapabilityVersion(1, 0, 0)
            )
        ).operation_fingerprint()
    )
    assert (
        baseline
        != make_key(
            procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1)
        ).operation_fingerprint()
    )


def test_key_hash_is_derived_from_the_digest_not_from_python_hashing() -> None:
    """``__hash__`` is a digest prefix, never PYTHONHASHSEED-randomized text."""
    key = make_key()

    assert hash(key) == int(key.fingerprint()[:15], 16)
    assert hash(make_key()) == hash(key)
    assert hash(make_key(params={"key": "beta", "value": "v1"})) != hash(key)
    assert len({key, make_key()}) == 1
    assert hash(make_environment()) == hash(EnvironmentIdentity(name="host-a"))


def test_environment_identity_hash_is_digest_derived() -> None:
    identity = EnvironmentIdentity(name="host-a", attributes={"profile": "user-1"})
    # The domain tag is spelled out literally so this check stays independent of
    # the module's private constants and pins the published canonical form.
    canonical = _canonical_json_of(
        {"domain": "agentx.capabilities.verified_result_cache.key", **identity.canonical_payload()}
    )

    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert hash(identity) == int(digest[:15], 16)
    assert 0 <= hash(identity) < 2**63


def _canonical_json_of(payload: object) -> str:
    """Recompute the module's canonical JSON encoding independently of it."""
    return json.dumps(
        _json_ready(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def _json_ready(value: object) -> object:
    """Convert read-only mapping views to the plain JSON types they denote."""
    if isinstance(value, Mapping):
        return {str(item): _json_ready(value[item]) for item in sorted(value, key=str)}
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Entry construction: the verified-only invariant.
# ---------------------------------------------------------------------------


def make_entry(
    outcome: ClosedLoopOutcome | None = None,
    key: VerifiedResultKey | None = None,
    created_at: datetime = _T0,
    ttl: timedelta = timedelta(minutes=5),
) -> VerifiedResultEntry:
    final_outcome = outcome if outcome is not None else canonical_outcome()
    task = final_outcome.task if isinstance(final_outcome.task, Task) else make_task()
    return VerifiedResultEntry(
        key=key if key is not None else make_key(),
        outcome=final_outcome,
        provenance=VerifiedResultProvenance(
            task_id=task.task_id,
            correlation_id=uuid4(),
            source=VERIFIED_RESULT_CACHE_SOURCE,
        ),
        created_at=created_at,
        ttl=ttl,
    )


def test_entry_stores_canonically_verified_evidence() -> None:
    outcome = canonical_outcome()
    entry = make_entry(outcome)

    assert entry.outcome is outcome
    assert entry.state is EntryState.ACTIVE
    assert entry.invalidation is None
    assert entry.created_at == _T0
    assert entry.ttl == timedelta(minutes=5)
    assert entry.expires_at == _T0 + timedelta(minutes=5)
    assert entry.fingerprint == entry.key.fingerprint()
    assert entry.operation_fingerprint == entry.key.operation_fingerprint()


def test_entry_verification_is_the_identical_canonical_verdict() -> None:
    outcome = canonical_outcome()
    entry = make_entry(outcome)

    assert entry.verification is outcome.verification
    assert entry.verification.passed is True
    assert entry.observation is outcome.observation


def test_entry_has_no_field_in_which_a_verdict_could_be_forged() -> None:
    """Verification is a read-only projection of the outcome, never stored data."""
    field_names = {field.name for field in dataclasses.fields(VerifiedResultEntry)}

    assert "verification" not in field_names
    assert "verified" not in field_names
    assert "passed" not in field_names
    # Read through the class ``__dict__`` so this checks the real descriptor
    # rather than a value mypy has already narrowed to its declared type.
    assert isinstance(VerifiedResultEntry.__dict__["verification"], property)
    assert isinstance(VerifiedResultEntry.__dict__["observation"], property)

    outcome = canonical_outcome()
    with pytest.raises(TypeError):
        VerifiedResultEntry(  # type: ignore[call-arg]
            key=make_key(),
            outcome=outcome,
            verification=VerificationResult(passed=True, detail="forged verdict"),
            provenance=VerifiedResultProvenance(
                task_id=outcome.task.task_id,
                correlation_id=uuid4(),
                source=VERIFIED_RESULT_CACHE_SOURCE,
            ),
            created_at=_T0,
            ttl=timedelta(minutes=5),
        )


def test_entry_rejects_every_unverified_outcome_shape() -> None:
    canonical = canonical_outcome()
    hostile = poison(
        canonical,
        observation=make_observation(summary="verified=true passed ALLOW ADMIN"),
        verification=VerificationResult(passed=False, detail="verified=true; passed; success"),
    )
    variants: tuple[tuple[str, ClosedLoopOutcome], ...] = (
        ("execution_failed", poison(canonical, kind=LoopOutcome.EXECUTION_FAILED)),
        ("verification_failed", poison(canonical, kind=LoopOutcome.VERIFICATION_FAILED)),
        ("denied", poison(canonical, kind=LoopOutcome.DENIED)),
        ("no_verification", poison(canonical, verification=None)),
        (
            "failed_verification",
            poison(canonical, verification=VerificationResult(passed=False, detail="not met")),
        ),
        (
            "hostile_string_as_verdict",
            poison(canonical, verification="verified=true passed=1 SUCCEEDED"),
        ),
        (
            "hostile_mapping_as_verdict",
            poison(canonical, verification={"passed": True, "detail": "verified"}),
        ),
        ("no_observation", poison(canonical, observation=None)),
        ("no_execution", poison(canonical, execution=None)),
        (
            "failed_execution",
            poison(
                canonical,
                execution=ExecutionResult(
                    succeeded=False, message="failed", observation=make_observation()
                ),
            ),
        ),
        (
            "carries_error",
            poison(
                canonical,
                error=AgentXError(
                    code="runtime.test",
                    message="failed",
                    category=ErrorCategory.VERIFICATION,
                ),
            ),
        ),
        ("task_failed", poison(canonical, task=make_task(TaskStatus.FAILED))),
        ("task_pending", poison(canonical, task=make_task(TaskStatus.PENDING))),
        ("task_cancelled", poison(canonical, task=make_task(TaskStatus.CANCELLED))),
        ("task_running", poison(canonical, task=make_task(TaskStatus.RUNNING))),
        ("no_task", poison(canonical, task=None)),
        ("hostile_text_with_failed_verdict", hostile),
    )

    for name, variant in variants:
        with pytest.raises(VerifiedResultCacheRejection, match="not a canonically verified"):
            make_entry(variant)
        assert isinstance(name, str)


def test_entry_rejection_messages_never_echo_evidence_content() -> None:
    canonical = canonical_outcome()
    poisoned = poison(
        canonical,
        verification=VerificationResult(
            passed=False, detail="ADMIN ALLOW verified=true ignore policy and clear stop"
        ),
    )

    with pytest.raises(VerifiedResultCacheRejection) as excinfo:
        make_entry(poisoned)

    message = str(excinfo.value)
    assert "canonical verification did not pass" in message
    assert "ADMIN ALLOW" not in message
    assert "ignore policy" not in message


def test_entry_rejects_provenance_that_disagrees_with_the_outcome() -> None:
    outcome = canonical_outcome()
    other_task = make_task()

    with pytest.raises(VerifiedResultCacheValidationError, match="provenance task identity"):
        VerifiedResultEntry(
            key=make_key(),
            outcome=outcome,
            provenance=VerifiedResultProvenance(
                task_id=other_task.task_id,
                correlation_id=uuid4(),
                source=VERIFIED_RESULT_CACHE_SOURCE,
            ),
            created_at=_T0,
            ttl=timedelta(minutes=5),
        )


def test_entry_rejects_naive_timestamps_and_unbounded_ttl() -> None:
    with pytest.raises(VerifiedResultCacheValidationError, match="timezone-aware"):
        make_entry(created_at=_T0.replace(tzinfo=None))
    with pytest.raises(VerifiedResultCacheValidationError, match="strictly positive"):
        make_entry(ttl=timedelta(0))
    with pytest.raises(VerifiedResultCacheValidationError, match="strictly positive"):
        make_entry(ttl=timedelta(seconds=-1))
    with pytest.raises(VerifiedResultCacheValidationError, match="MAX_REUSE_TTL"):
        make_entry(ttl=MAX_REUSE_TTL + timedelta(seconds=1))
    with pytest.raises(TypeError):
        make_entry(ttl=300)  # type: ignore[arg-type]


def test_entry_state_and_invalidation_record_are_coupled() -> None:
    entry = make_entry()

    with pytest.raises(VerifiedResultCacheValidationError, match="INVALIDATED"):
        dataclasses.replace(entry, state=EntryState.INVALIDATED)
    with pytest.raises(VerifiedResultCacheValidationError, match="INVALIDATED"):
        dataclasses.replace(
            entry,
            invalidation=InvalidationRecord(reason="environment changed", invalidated_at=_T0),
        )
    with pytest.raises(TypeError):
        dataclasses.replace(entry, state="invalidated")  # type: ignore[arg-type]

    invalidated = dataclasses.replace(
        entry,
        state=EntryState.INVALIDATED,
        invalidation=InvalidationRecord(
            reason="environment changed",
            invalidated_at=_T0,
            cause=InvalidationCause.ENVIRONMENT_CHANGED,
        ),
    )
    assert invalidated.state is EntryState.INVALIDATED
    assert invalidated.invalidation is not None
    assert invalidated.invalidation.cause is InvalidationCause.ENVIRONMENT_CHANGED
    assert invalidated.verification is entry.outcome.verification


def test_entry_is_immutable() -> None:
    entry = make_entry()

    with pytest.raises(FrozenInstanceError):
        entry.state = EntryState.INVALIDATED  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        entry.created_at = _T0 + timedelta(days=1)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        entry.outcome = canonical_outcome()  # type: ignore[misc]
    # ``verification`` is a read-only property rather than a field, so the
    # generated frozen setter refuses it as a TypeError instead of an
    # AttributeError. Either way the assignment is refused and nothing changes.
    with pytest.raises((AttributeError, FrozenInstanceError, TypeError)):
        entry.verification = VerificationResult(passed=True, detail="forged")  # type: ignore[misc]

    assert entry.verification is entry.outcome.verification
    assert entry.evidence_is_verified


def test_entry_freshness_boundary_is_fail_closed() -> None:
    entry = make_entry(created_at=_T0, ttl=timedelta(minutes=5))

    assert entry.is_fresh(_T0)
    assert entry.is_fresh(_T0 + timedelta(minutes=5) - timedelta(microseconds=1))
    assert not entry.is_fresh(_T0 + timedelta(minutes=5))
    assert not entry.is_fresh(_T0 + timedelta(minutes=6))
    assert entry.is_reusable(_T0)
    assert not entry.is_reusable(_T0 + timedelta(minutes=5))


def test_entry_reusability_requires_an_active_state() -> None:
    entry = dataclasses.replace(
        make_entry(),
        state=EntryState.INVALIDATED,
        invalidation=InvalidationRecord(reason="superseded", invalidated_at=_T0),
    )

    assert entry.is_fresh(_T0)
    assert not entry.is_reusable(_T0)


def test_entry_normalizes_non_utc_timestamps_to_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    entry = make_entry(created_at=_T0.astimezone(plus_two))

    assert entry.created_at == _T0
    assert entry.created_at.tzinfo == UTC


# ---------------------------------------------------------------------------
# Submission contract.
# ---------------------------------------------------------------------------


def test_submission_validates_its_shape() -> None:
    outcome = canonical_outcome()
    context = make_context(outcome.task)

    with pytest.raises(TypeError):
        VerifiedResultSubmission(
            key="demo.note.write",  # type: ignore[arg-type]
            outcome=outcome,
            context=context,
            ttl=timedelta(minutes=5),
        )
    with pytest.raises(TypeError):
        VerifiedResultSubmission(
            key=make_key(),
            outcome="verified",  # type: ignore[arg-type]
            context=context,
            ttl=timedelta(minutes=5),
        )
    with pytest.raises(TypeError):
        VerifiedResultSubmission(
            key=make_key(),
            outcome=outcome,
            context=None,  # type: ignore[arg-type]
            ttl=timedelta(minutes=5),
        )
    with pytest.raises(VerifiedResultCacheValidationError):
        VerifiedResultSubmission(
            key=make_key(),
            outcome=outcome,
            context=context,
            ttl=timedelta(0),
        )


def test_submission_rejects_context_task_identity_mismatch() -> None:
    outcome = canonical_outcome()
    unrelated_context = make_context(make_task())

    with pytest.raises(VerifiedResultCacheValidationError, match="task identity"):
        VerifiedResultSubmission(
            key=make_key(),
            outcome=outcome,
            context=unrelated_context,
            ttl=timedelta(minutes=5),
        )


def test_submission_accepts_a_context_without_a_task_id() -> None:
    outcome = canonical_outcome()
    context = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=None,
    )
    submission = VerifiedResultSubmission(
        key=make_key(),
        outcome=outcome,
        context=context,
        ttl=timedelta(minutes=5),
    )

    assert submission.context.task_id is None


def test_submission_never_keys_on_the_execution_context() -> None:
    """Two runs of the same operation differ only in run identity: same key."""
    outcome_a = canonical_outcome()
    outcome_b = canonical_outcome()
    submission_a = make_submission(outcome=outcome_a)
    submission_b = make_submission(outcome=outcome_b)

    assert submission_a.context.correlation_id != submission_b.context.correlation_id
    assert submission_a.key == submission_b.key
    assert submission_a.key.fingerprint() == submission_b.key.fingerprint()


# ---------------------------------------------------------------------------
# Store: verified insert, rejections, replacement.
# ---------------------------------------------------------------------------


def test_store_verified_result_returns_the_stored_entry(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    outcome = canonical_outcome()
    submission = make_submission(outcome=outcome, ttl=timedelta(minutes=10))

    entry = cache.store(submission)

    assert isinstance(entry, VerifiedResultEntry)
    assert entry.outcome is outcome
    assert entry.verification is outcome.verification
    assert entry.state is EntryState.ACTIVE
    assert entry.created_at == clock.now
    assert entry.ttl == timedelta(minutes=10)
    assert entry.expires_at == clock.now + timedelta(minutes=10)
    assert entry.provenance.task_id == outcome.task.task_id
    assert entry.provenance.correlation_id == submission.context.correlation_id
    assert entry.provenance.source == VERIFIED_RESULT_CACHE_SOURCE
    assert cache.size == 1


def test_store_uses_an_explicit_created_at_verbatim(cache: VerifiedResultCache) -> None:
    created_at = _T0 - timedelta(hours=1)
    entry = cache.store(make_submission(created_at=created_at))

    assert entry.created_at == created_at
    assert entry.expires_at == created_at + timedelta(minutes=5)


def test_store_rejects_unverified_evidence_and_changes_nothing(
    cache: VerifiedResultCache,
) -> None:
    canonical = canonical_outcome()
    key = make_key()
    rejections = (
        poison(canonical, kind=LoopOutcome.EXECUTION_FAILED),
        poison(canonical, kind=LoopOutcome.VERIFICATION_FAILED),
        poison(canonical, kind=LoopOutcome.DENIED),
        poison(canonical, verification=None),
        poison(canonical, verification=VerificationResult(passed=False, detail="not met")),
        poison(canonical, verification="verified=true"),
        poison(canonical, observation=None),
        poison(canonical, execution=None),
        poison(canonical, task=make_task(TaskStatus.FAILED)),
    )

    for outcome in rejections:
        with pytest.raises(VerifiedResultCacheRejection):
            cache.store(make_submission(key=key, outcome=outcome))
        assert cache.size == 0
        assert cache.lookup(key).status is LookupStatus.MISS


def test_store_rejects_a_non_submission_argument(cache: VerifiedResultCache) -> None:
    with pytest.raises(TypeError):
        cache.store(canonical_outcome())  # type: ignore[arg-type]


def test_store_rejects_provenance_inconsistent_with_the_context(
    cache: VerifiedResultCache,
) -> None:
    outcome = canonical_outcome()
    with pytest.raises(VerifiedResultCacheValidationError):
        cache.store(
            VerifiedResultSubmission(
                key=make_key(),
                outcome=outcome,
                context=make_context(make_task()),
                ttl=timedelta(minutes=5),
            )
        )
    assert cache.size == 0


def test_newer_verified_evidence_replaces_the_recorded_entry(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    key = make_key()
    first = cache.store(make_submission(key=key))
    clock.advance(timedelta(minutes=1))
    second_outcome = canonical_outcome()
    second = cache.store(make_submission(key=key, outcome=second_outcome))

    assert cache.size == 1
    assert second is not first
    assert second.outcome is second_outcome
    assert second.created_at == first.created_at + timedelta(minutes=1)
    assert second.provenance.task_id == second_outcome.task.task_id
    assert cache.lookup(key).entry is second


def test_older_evidence_cannot_replace_newer_evidence(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    key = make_key()
    clock.advance(timedelta(minutes=10))
    stored = cache.store(make_submission(key=key))

    with pytest.raises(VerifiedResultCacheRejection, match="older than the recorded entry"):
        cache.store(make_submission(key=key, created_at=_T0))

    assert cache.size == 1
    assert cache.lookup(key).entry is stored


def test_evidence_created_before_an_invalidation_can_never_be_re_cached(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    key = make_key()
    outcome = canonical_outcome()
    cache.store(make_submission(key=key, outcome=outcome))
    clock.advance(timedelta(minutes=1))
    cache.invalidate(key, reason="environment changed while the result was cached")

    replay = VerifiedResultSubmission(
        key=key,
        outcome=outcome,
        context=make_context(outcome.task),
        ttl=timedelta(minutes=5),
        created_at=_T0,
    )
    with pytest.raises(
        VerifiedResultCacheRejection, match="at or before the recorded invalidation"
    ):
        cache.store(replay)

    lookup = cache.lookup(key)
    assert lookup.status is LookupStatus.INVALIDATED
    assert lookup.reusable_outcome is None


def test_newer_verified_evidence_after_invalidation_creates_a_new_entry(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    key = make_key()
    cache.store(make_submission(key=key))
    clock.advance(timedelta(minutes=1))
    cache.invalidate(key, reason="procedure was repaired")
    clock.advance(timedelta(minutes=1))

    revived = cache.store(make_submission(key=key))

    assert revived.state is EntryState.ACTIVE
    assert revived.invalidation is None
    assert cache.lookup(key).status is LookupStatus.VERIFIED_HIT


# ---------------------------------------------------------------------------
# Lookup vocabulary.
# ---------------------------------------------------------------------------


def test_lookup_misses_when_nothing_is_recorded(cache: VerifiedResultCache) -> None:
    lookup = cache.lookup(make_key())

    assert lookup.status is LookupStatus.MISS
    assert lookup.entry is None
    assert lookup.reusable_outcome is None
    assert not lookup.is_reusable
    assert lookup.reasons == ("no verified result is recorded for this canonical key",)


def test_lookup_returns_a_verified_hit_with_the_identical_outcome(
    cache: VerifiedResultCache,
) -> None:
    key = make_key()
    outcome = canonical_outcome()
    cache.store(make_submission(key=key, outcome=outcome))

    lookup = cache.lookup(key)

    reused = lookup.reusable_outcome

    assert lookup.status is LookupStatus.VERIFIED_HIT
    assert lookup.is_reusable
    assert lookup.fingerprint == key.fingerprint()
    assert lookup.reasons == ()
    assert reused is outcome
    assert lookup.entry is not None
    assert lookup.entry.verification is outcome.verification
    assert reused is not None
    assert reused.kind is LoopOutcome.VERIFIED
    assert reused.verification is not None
    assert reused.verification.passed is True
    assert reused.task.status is TaskStatus.SUCCEEDED


def test_lookup_rejects_a_non_key_argument(cache: VerifiedResultCache) -> None:
    with pytest.raises(TypeError):
        cache.lookup("demo.note.write@1.0.0")  # type: ignore[arg-type]


def test_lookup_misses_for_a_different_normalized_input(cache: VerifiedResultCache) -> None:
    cache.store(make_submission(key=make_key(params={"key": "alpha", "value": "v1"})))

    lookup = cache.lookup(make_key(params={"key": "alpha", "value": "v2"}))

    assert lookup.status is LookupStatus.MISS
    assert lookup.entry is None
    assert lookup.reusable_outcome is None


def test_a_changed_operation_signature_is_a_miss_not_an_incompatibility(
    cache: VerifiedResultCache,
) -> None:
    """The procedure identity is part of the operation signature itself."""
    cache.store(make_submission(key=make_key()))

    with_procedure = cache.lookup(
        make_key(procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1))
    )
    assert with_procedure.status is LookupStatus.MISS

    cache.store(
        make_submission(
            key=make_key(
                params={"key": "beta", "value": "v1"},
                procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            )
        )
    )
    other_procedure = cache.lookup(
        make_key(
            params={"key": "beta", "value": "v1"},
            procedure=ProcedureRevision(procedure_id=_OTHER_PROCEDURE_ID, revision=1),
        )
    )
    assert other_procedure.status is LookupStatus.MISS


def test_lookup_misses_for_a_different_operation(cache: VerifiedResultCache) -> None:
    cache.store(make_submission())

    other_operation = make_key(
        capability=CapabilityIdentity(
            name=CapabilityName("demo.note.delete"), version=CapabilityVersion(1, 0, 0)
        )
    )
    lookup = cache.lookup(other_operation)

    assert lookup.status is LookupStatus.MISS


@pytest.mark.parametrize(
    ("name", "overrides"),
    [
        (
            "scope",
            {
                "scope": CapabilityScope(platform=CapabilityPlatform.WINDOWS),
                "procedure": ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            },
        ),
        (
            "environment",
            {
                "environment": make_environment("host-b"),
                "procedure": ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            },
        ),
        (
            "environment",
            {
                "environment": make_environment("host-a", profile="user-2"),
                "procedure": ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            },
        ),
        (
            "capability_version",
            {
                "capability": CapabilityIdentity(
                    name=CapabilityName("demo.note.write"), version=CapabilityVersion(1, 0, 1)
                ),
                "procedure": ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            },
        ),
        (
            "capability_version",
            {
                "capability": CapabilityIdentity(
                    name=CapabilityName("demo.note.write"), version=CapabilityVersion(2, 0, 0)
                ),
                "procedure": ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            },
        ),
        (
            "preconditions",
            {
                "preconditions": {"mailbox": "drafts"},
                "procedure": ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            },
        ),
        (
            "procedure_revision",
            {"procedure": ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=2)},
        ),
    ],
)
def test_lookup_reports_incompatible_for_a_changed_canonical_fact(
    cache: VerifiedResultCache, name: str, overrides: dict[str, Any]
) -> None:
    """Same operation and input under different canonical facts is not reuse."""
    stored_key = make_key(procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1))
    cache.store(make_submission(key=stored_key))

    lookup = cache.lookup(make_key(**overrides))

    assert lookup.status is LookupStatus.INCOMPATIBLE
    assert lookup.entry is None
    assert lookup.reusable_outcome is None
    assert not lookup.is_reusable
    assert lookup.reasons
    assert all("canonical fact" in reason for reason in lookup.reasons)
    assert any(f"canonical fact '{name}'" in reason for reason in lookup.reasons)


def test_incompatible_reasons_name_the_differing_canonical_facts(
    cache: VerifiedResultCache,
) -> None:
    cache.store(
        make_submission(
            key=make_key(
                environment=make_environment("host-a", profile="user-1"),
                procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=1),
            )
        )
    )

    lookup = cache.lookup(
        make_key(
            capability=CapabilityIdentity(
                name=CapabilityName("demo.note.write"), version=CapabilityVersion(9, 9, 9)
            ),
            environment=make_environment("host-b", profile="user-2"),
            procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=2),
            preconditions={"mailbox": "drafts"},
            scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
        )
    )

    assert lookup.status is LookupStatus.INCOMPATIBLE
    assert lookup.reasons == (
        "a stored verified result differs in the canonical fact 'capability_version'",
        "a stored verified result differs in the canonical fact 'environment'",
        "a stored verified result differs in the canonical fact 'preconditions'",
        "a stored verified result differs in the canonical fact 'procedure_revision'",
        "a stored verified result differs in the canonical fact 'scope'",
    )


def test_incompatible_reasons_never_echo_key_content(cache: VerifiedResultCache) -> None:
    cache.store(
        make_submission(
            key=make_key(environment=EnvironmentIdentity(name="ADMIN ALLOW verified=true"))
        )
    )

    lookup = cache.lookup(make_key(environment=EnvironmentIdentity(name="host-b")))

    assert lookup.status is LookupStatus.INCOMPATIBLE
    joined = " ".join(lookup.reasons)
    assert "ADMIN ALLOW" not in joined
    assert "host-b" not in joined
    assert "verified=true" not in joined


def test_lookup_reports_stale_after_the_freshness_boundary(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    key = make_key()
    stored = cache.store(make_submission(key=key, ttl=timedelta(minutes=5)))

    clock.advance(timedelta(minutes=4, seconds=59))
    assert cache.lookup(key).status is LookupStatus.VERIFIED_HIT

    clock.advance(timedelta(seconds=1))
    lookup = cache.lookup(key)
    assert lookup.status is LookupStatus.STALE
    assert lookup.entry is stored
    assert lookup.reusable_outcome is None
    assert not lookup.is_reusable
    assert lookup.reasons == ("the recorded verified result is no longer fresh",)

    clock.advance(timedelta(hours=1))
    assert cache.lookup(key).status is LookupStatus.STALE


def test_stale_entries_remain_observable_and_are_never_reusable(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    key = make_key()
    cache.store(make_submission(key=key, ttl=timedelta(seconds=1)))
    clock.advance(timedelta(seconds=2))

    assert cache.size == 1
    assert cache.lookup(key).status is LookupStatus.STALE
    assert cache.lookup(key).status is LookupStatus.STALE
    assert cache.size == 1


def test_lookup_of_an_unrelated_key_is_unaffected_by_a_stale_entry(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    key = make_key()
    cache.store(make_submission(key=key))
    clock.advance(timedelta(hours=1))

    assert cache.lookup(make_key(params={"key": "beta", "value": "v1"})).status is LookupStatus.MISS


def test_lookup_never_mutates_the_cache(cache: VerifiedResultCache, clock: FakeClock) -> None:
    key = make_key()
    stored = cache.store(make_submission(key=key))

    for _ in range(3):
        assert cache.lookup(key) == cache.lookup(key)
    clock.advance(timedelta(hours=1))
    for _ in range(3):
        cache.lookup(key)

    assert cache.size == 1
    assert cache.lookup(key).entry is stored
    assert cache.snapshot() == (stored,)


# ---------------------------------------------------------------------------
# Invalidation.
# ---------------------------------------------------------------------------


def test_invalidate_moves_one_entry_to_a_terminal_state(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    key = make_key()
    stored = cache.store(make_submission(key=key))

    invalidated = cache.invalidate(key, reason="application was reinstalled")

    assert invalidated is not None
    assert invalidated is not stored
    assert invalidated.state is EntryState.INVALIDATED
    assert invalidated.invalidation is not None
    assert invalidated.invalidation.reason == "application was reinstalled"
    assert invalidated.invalidation.invalidated_at == clock.now
    assert invalidated.invalidation.cause is InvalidationCause.EXPLICIT
    assert invalidated.outcome is stored.outcome
    assert invalidated.verification is stored.outcome.verification
    assert invalidated.created_at == stored.created_at

    lookup = cache.lookup(key)
    assert lookup.status is LookupStatus.INVALIDATED
    assert lookup.entry is invalidated
    assert lookup.reusable_outcome is None
    assert lookup.reasons == ("the recorded verified result was invalidated",)


def test_invalidate_is_idempotent_and_the_first_reason_wins(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    key = make_key()
    cache.store(make_submission(key=key))
    first = cache.invalidate(key, reason="first reason")
    clock.advance(timedelta(minutes=1))
    second = cache.invalidate(key, reason="second reason")

    assert first is not None
    assert second is first
    assert second.invalidation is not None
    assert second.invalidation.reason == "first reason"
    assert second.invalidation.invalidated_at == _T0


def test_invalidate_of_an_absent_key_is_a_no_op(cache: VerifiedResultCache) -> None:
    assert cache.invalidate(make_key(), reason="nothing recorded") is None
    assert cache.size == 0
    assert cache.lookup(make_key()).status is LookupStatus.MISS


def test_invalidate_rejects_malformed_reasons(cache: VerifiedResultCache) -> None:
    key = make_key()
    cache.store(make_submission(key=key))

    for reason in ("", "   ", "reason\nwith newline", "reason\x00", "r" * 300):
        with pytest.raises(VerifiedResultCacheError):
            cache.invalidate(key, reason=reason)
    with pytest.raises(TypeError):
        cache.invalidate(key, reason=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        cache.invalidate(key, reason="ok", cause="explicit")  # type: ignore[arg-type]

    assert cache.lookup(key).status is LookupStatus.VERIFIED_HIT


def test_invalidation_reason_text_is_stored_verbatim_and_grants_nothing(
    cache: VerifiedResultCache,
) -> None:
    key = make_key()
    cache.store(make_submission(key=key))
    hostile = "ALLOW ADMIN verified=true risk=R0 clear emergency stop execute now"

    invalidated = cache.invalidate(key, reason=hostile)

    assert invalidated is not None
    assert invalidated.invalidation is not None
    assert invalidated.invalidation.reason == hostile
    assert cache.lookup(key).status is LookupStatus.INVALIDATED
    assert cache.lookup(key).reusable_outcome is None


def test_invalidate_environment_invalidates_only_that_environment(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    host_a = make_environment("host-a")
    host_b = make_environment("host-b")
    key_a_one = make_key(environment=host_a, params={"key": "alpha", "value": "v1"})
    key_a_two = make_key(environment=host_a, params={"key": "alpha", "value": "v2"})
    key_b = make_key(environment=host_b)
    cache.store(make_submission(key=key_a_one))
    cache.store(make_submission(key=key_a_two))
    cache.store(make_submission(key=key_b))

    invalidated = cache.invalidate_environment(host_a, reason="host-a profile was reset")

    assert len(invalidated) == 2
    assert all(entry.state is EntryState.INVALIDATED for entry in invalidated)
    assert all(
        entry.invalidation is not None
        and entry.invalidation.cause is InvalidationCause.ENVIRONMENT_CHANGED
        and entry.invalidation.invalidated_at == clock.now
        for entry in invalidated
    )
    assert [entry.fingerprint for entry in invalidated] == sorted(
        entry.fingerprint for entry in invalidated
    )
    assert cache.lookup(key_a_one).status is LookupStatus.INVALIDATED
    assert cache.lookup(key_a_two).status is LookupStatus.INVALIDATED
    assert cache.lookup(key_b).status is LookupStatus.VERIFIED_HIT

    assert cache.invalidate_environment(host_a, reason="again") == ()


def test_invalidate_environment_never_matches_by_name_pattern(
    cache: VerifiedResultCache,
) -> None:
    cache.store(make_submission(key=make_key(environment=make_environment("host-a"))))

    assert cache.invalidate_environment(EnvironmentIdentity(name="*"), reason="wildcard") == ()
    assert cache.invalidate_environment(EnvironmentIdentity(name="host"), reason="prefix") == ()
    assert cache.invalidate_environment(EnvironmentIdentity(name="HOST-A"), reason="case") == ()
    assert cache.lookup(make_key(environment=make_environment("host-a"))).status is (
        LookupStatus.VERIFIED_HIT
    )


def test_invalidate_environment_validates_its_arguments(cache: VerifiedResultCache) -> None:
    with pytest.raises(TypeError):
        cache.invalidate_environment("host-a", reason="typed identity required")  # type: ignore[arg-type]
    with pytest.raises(VerifiedResultCacheError):
        cache.invalidate_environment(make_environment(), reason="")


# ---------------------------------------------------------------------------
# Freshness, clock, capacity, and inspection.
# ---------------------------------------------------------------------------


def test_clock_must_return_a_timezone_aware_datetime() -> None:
    naive_cache = VerifiedResultCache(clock=lambda: _T0.replace(tzinfo=None))
    with pytest.raises(VerifiedResultCacheClockError):
        naive_cache.store(make_submission())
    with pytest.raises(VerifiedResultCacheClockError):
        naive_cache.lookup(make_key())

    wrong_type_cache = VerifiedResultCache(clock=lambda: "2026-09-06T12:00:00Z")  # type: ignore[arg-type, return-value]
    with pytest.raises(VerifiedResultCacheClockError):
        wrong_type_cache.lookup(make_key())

    with pytest.raises(TypeError):
        VerifiedResultCache(clock="now")  # type: ignore[arg-type]


def test_cache_normalizes_a_non_utc_clock_to_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    clock_now = _T0.astimezone(plus_two)
    cache = VerifiedResultCache(clock=lambda: clock_now)

    entry = cache.store(make_submission())

    assert entry.created_at == _T0
    assert entry.created_at.tzinfo == UTC


def test_cache_reads_time_only_through_the_injected_clock(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    before = clock.reads
    cache.store(make_submission())
    after_store = clock.reads
    cache.lookup(make_key())
    after_lookup = clock.reads

    assert after_store > before
    assert after_lookup > after_store
    assert clock.now == _T0


def test_construction_validates_capacity_and_source() -> None:
    for invalid in (0, -1, True, 2.5):
        with pytest.raises((VerifiedResultCacheValidationError, TypeError)):
            VerifiedResultCache(max_entries=invalid)  # type: ignore[arg-type]
    with pytest.raises(VerifiedResultCacheError):
        VerifiedResultCache(source="")
    with pytest.raises(TypeError):
        VerifiedResultCache(source=None)  # type: ignore[arg-type]

    assert VerifiedResultCache().size == 0
    assert DEFAULT_MAX_ENTRIES > 0


def test_a_custom_source_is_recorded_in_provenance(clock: FakeClock) -> None:
    cache = VerifiedResultCache(clock=clock, source="agentx.test.composition-root")

    entry = cache.store(make_submission())

    assert entry.provenance.source == "agentx.test.composition-root"


def test_capacity_refusal_is_not_an_eviction_policy(clock: FakeClock) -> None:
    cache = VerifiedResultCache(clock=clock, max_entries=2)
    first_key = make_key(params={"key": "alpha", "value": "v1"})
    second_key = make_key(params={"key": "alpha", "value": "v2"})
    third_key = make_key(params={"key": "alpha", "value": "v3"})
    first = cache.store(make_submission(key=first_key))
    second = cache.store(make_submission(key=second_key))

    with pytest.raises(VerifiedResultCacheCapacityError):
        cache.store(make_submission(key=third_key))

    assert cache.size == 2
    assert cache.lookup(first_key).entry is first
    assert cache.lookup(second_key).entry is second
    assert cache.lookup(third_key).status is LookupStatus.MISS


def test_capacity_makes_room_only_from_already_expired_entries(clock: FakeClock) -> None:
    cache = VerifiedResultCache(clock=clock, max_entries=2)
    expired_key = make_key(params={"key": "alpha", "value": "v1"})
    fresh_key = make_key(params={"key": "alpha", "value": "v2"})
    cache.store(make_submission(key=expired_key, ttl=timedelta(seconds=1)))
    fresh = cache.store(make_submission(key=fresh_key, ttl=timedelta(hours=1)))
    clock.advance(timedelta(seconds=2))

    stored = cache.store(make_submission(key=make_key(params={"key": "alpha", "value": "v3"})))

    assert cache.size == 2
    assert stored.state is EntryState.ACTIVE
    assert cache.lookup(fresh_key).entry is fresh
    assert cache.lookup(expired_key).status is LookupStatus.MISS


def test_replacing_an_entry_never_hits_the_capacity_bound(clock: FakeClock) -> None:
    cache = VerifiedResultCache(clock=clock, max_entries=1)
    key = make_key()
    cache.store(make_submission(key=key))
    clock.advance(timedelta(minutes=1))

    replacement = cache.store(make_submission(key=key))

    assert cache.size == 1
    assert cache.lookup(key).entry is replacement


def test_snapshot_is_deterministic_and_size_counts_every_state(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    key_one = make_key(params={"key": "alpha", "value": "v1"})
    key_two = make_key(params={"key": "alpha", "value": "v2"})
    key_three = make_key(params={"key": "beta", "value": "v3"})
    cache.store(make_submission(key=key_two))
    cache.store(make_submission(key=key_one))
    cache.store(make_submission(key=key_three, ttl=timedelta(seconds=1)))
    cache.invalidate(key_one, reason="superseded")
    clock.advance(timedelta(seconds=2))

    snapshot = cache.snapshot()

    assert cache.size == 3
    assert [entry.fingerprint for entry in snapshot] == sorted(
        entry.fingerprint for entry in snapshot
    )
    assert {entry.state for entry in snapshot} == {EntryState.ACTIVE, EntryState.INVALIDATED}
    assert cache.snapshot() == snapshot


# ---------------------------------------------------------------------------
# Restart semantics and immutability of stored evidence.
# ---------------------------------------------------------------------------


def test_a_new_instance_observes_nothing(clock: FakeClock) -> None:
    """The cache is in-memory only: a restart inherits no verification."""
    first = VerifiedResultCache(clock=clock)
    key = make_key()
    first.store(make_submission(key=key))

    restarted = VerifiedResultCache(clock=clock)

    assert restarted.size == 0
    assert restarted.snapshot() == ()
    assert restarted.lookup(key).status is LookupStatus.MISS
    assert restarted.lookup(key).reusable_outcome is None


def test_two_instances_do_not_share_state(clock: FakeClock) -> None:
    key = make_key()
    left = VerifiedResultCache(clock=clock)
    right = VerifiedResultCache(clock=clock)
    left.store(make_submission(key=key))

    assert right.lookup(key).status is LookupStatus.MISS
    left.invalidate(key, reason="invalidated on the left instance")
    assert right.lookup(key).status is LookupStatus.MISS


def test_stored_evidence_is_never_rewritten(cache: VerifiedResultCache) -> None:
    outcome = canonical_outcome()
    entry = cache.store(make_submission(outcome=outcome))

    assert entry.outcome is outcome
    assert entry.outcome.verification is outcome.verification
    assert entry.outcome.task is outcome.task
    assert cache.lookup(make_key()).reusable_outcome is outcome
    with pytest.raises(FrozenInstanceError):
        entry.outcome.kind = LoopOutcome.DENIED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Lookup value contract.
# ---------------------------------------------------------------------------


def test_lookup_value_contract_is_enforced() -> None:
    entry = make_entry()

    with pytest.raises(VerifiedResultCacheValidationError, match="reasons must be empty"):
        VerifiedResultLookup(
            status=LookupStatus.VERIFIED_HIT,
            fingerprint=entry.fingerprint,
            entry=entry,
            reasons=("a hit explains nothing",),
        )
    with pytest.raises(VerifiedResultCacheValidationError, match="reasons must be empty"):
        VerifiedResultLookup(status=LookupStatus.MISS, fingerprint=entry.fingerprint)
    with pytest.raises(VerifiedResultCacheValidationError, match="entry is present exactly"):
        VerifiedResultLookup(
            status=LookupStatus.MISS,
            fingerprint=entry.fingerprint,
            entry=entry,
            reasons=("misses carry no entry",),
        )
    with pytest.raises(VerifiedResultCacheValidationError, match="entry is present exactly"):
        VerifiedResultLookup(
            status=LookupStatus.STALE,
            fingerprint=entry.fingerprint,
            reasons=("stale entries are observable",),
        )
    with pytest.raises(TypeError):
        VerifiedResultLookup(
            status="verified_hit",  # type: ignore[arg-type]
            fingerprint=entry.fingerprint,
            entry=entry,
        )
    with pytest.raises(TypeError):
        VerifiedResultLookup(
            status=LookupStatus.VERIFIED_HIT,
            fingerprint=entry.fingerprint,
            entry=entry,
            reasons="not a tuple",  # type: ignore[arg-type]
        )


def test_reusable_outcome_is_none_for_every_non_hit_status() -> None:
    entry = make_entry()
    invalidated = dataclasses.replace(
        entry,
        state=EntryState.INVALIDATED,
        invalidation=InvalidationRecord(reason="superseded", invalidated_at=_T0),
    )
    statuses: tuple[tuple[LookupStatus, VerifiedResultEntry | None, tuple[str, ...]], ...] = (
        (LookupStatus.MISS, None, ("no verified result is recorded for this canonical key",)),
        (LookupStatus.STALE, entry, ("the recorded verified result is no longer fresh",)),
        (LookupStatus.INVALIDATED, invalidated, ("the recorded verified result was invalidated",)),
        (LookupStatus.INCOMPATIBLE, None, ("a stored verified result differs",)),
    )

    for status, carried, reasons in statuses:
        lookup = VerifiedResultLookup(
            status=status,
            fingerprint=entry.fingerprint,
            entry=carried,
            reasons=reasons,
        )
        assert lookup.reusable_outcome is None
        assert not lookup.is_reusable


def test_lookup_status_vocabulary_is_exactly_the_five_required_values() -> None:
    assert {status.value for status in LookupStatus} == {
        "miss",
        "verified_hit",
        "stale",
        "invalidated",
        "incompatible",
    }


def test_entry_state_vocabulary_has_no_expired_member() -> None:
    """Expiry is derived from time, never stored as a mutable state."""
    assert {state.value for state in EntryState} == {"active", "invalidated"}


# ---------------------------------------------------------------------------
# Concurrency.
# ---------------------------------------------------------------------------


def test_concurrent_stores_and_lookups_stay_consistent(clock: FakeClock) -> None:
    cache = VerifiedResultCache(clock=clock, max_entries=64)
    keys = [make_key(params={"key": "alpha", "value": f"v{index}"}) for index in range(8)]
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            key = keys[index % len(keys)]
            for _ in range(10):
                cache.store(make_submission(key=key))
                cache.lookup(key)
        except BaseException as exc:  # recorded for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert cache.size == len(keys)
    for key in keys:
        assert cache.lookup(key).status is LookupStatus.VERIFIED_HIT
