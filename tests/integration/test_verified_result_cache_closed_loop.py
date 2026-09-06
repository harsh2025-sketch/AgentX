"""A8.07 integration tests: the verified-result cache over the real closed loop.

Every outcome cached here is produced by the real canonical execution path —
the A1.09 registry, C1.07 PermissionEngine/ActionGate, C1.09 EmergencyStop,
C1.08 ResourceBudget, and the demo capability's own ``execute``/``verify`` —
with the real C1.03 EventBus and an audit sink as evidence. Nothing is mocked
except the clock, so freshness and expiry are deterministic without sleeping.

The tests prove, end to end, that the cache:

- stores only outcomes the canonical loop actually verified, and reuses them
  without executing anything again;
- refuses denied, execution-failed, and verification-failed runs, leaving no
  trace of them;
- binds reuse to the real request params, capability version, scope,
  environment, and explicit preconditions;
- expires and invalidates reuse deterministically;
- forgets everything across a restart;
- produces fingerprints that are identical in another process under a
  randomized ``PYTHONHASHSEED``;
- yields *evidence* a Router may consume, while leaving the A2.07 Router itself
  completely untouched.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from agentx.capabilities.abi import (
    CapabilityIdentity,
    CapabilityName,
    CapabilityPlatform,
    CapabilityRequest,
    CapabilityScope,
    CapabilityVersion,
)
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, ClosedLoopOutcome, LoopOutcome
from agentx.capabilities.verified_result_cache import (
    VERIFIED_RESULT_CACHE_SOURCE,
    EntryState,
    EnvironmentIdentity,
    InvalidationCause,
    LookupStatus,
    ProcedureRevision,
    VerifiedResultCache,
    VerifiedResultCacheRejection,
    VerifiedResultEntry,
    VerifiedResultKey,
    VerifiedResultSubmission,
)
from agentx.cognition.router import ExecutionLevel, ExecutionLevelRouter, RoutingEvidence
from agentx.core.events import Event
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.ids import ProcedureId
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.event_bus import EventBus
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.audit import SecurityAuditRecord
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel
from tests.support.demo_capability import DemoNoteCapability, NoteWriteParams, write_request

_T0 = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
_PROCEDURE_ID = ProcedureId.parse("3f2b8c1e-9d47-4a1e-9c62-1b0f5e8d7a64")
_HOST_A = EnvironmentIdentity(name="host-a", attributes={"profile": "user-1"})
_HOST_B = EnvironmentIdentity(name="host-b", attributes={"profile": "user-1"})


class FakeClock:
    """Deterministic clock: freshness needs no real time and no sleeping."""

    def __init__(self, start: datetime = _T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


def make_envelope() -> ResourceEnvelope:
    """A deterministic envelope with zero model resources by construction."""
    return ResourceEnvelope(
        max_wall_clock=timedelta(seconds=60),
        max_model_calls=0,
        max_model_tokens=0,
        max_research_queries=0,
        max_machine_actions=64,
        max_repair_attempts=0,
        max_external_cost=Decimal("0"),
        max_risk_level=RiskLevel.R2,
    )


class Run:
    """One real closed-loop run plus everything needed to cache its outcome."""

    def __init__(
        self,
        outcome: ClosedLoopOutcome,
        request: CapabilityRequest[NoteWriteParams],
        context: ExecutionContext,
    ) -> None:
        self.outcome = outcome
        self.request = request
        self.context = context


class Harness:
    """Wires the canonical A1.10 loop exactly as the A1.10 tests compose it."""

    def __init__(
        self,
        *,
        authority: frozenset[Permission] | None = frozenset({Permission.WRITE}),
        capability: DemoNoteCapability | None = None,
    ) -> None:
        self.capability = capability if capability is not None else DemoNoteCapability()
        self.registry = CapabilityRegistry()
        self.registry.register(self.capability)
        self.bus = EventBus()
        self.events: list[Event] = []
        self.audit_records: list[SecurityAuditRecord] = []
        self.bus.subscribe(self.events.append)
        self.budget = ResourceBudget(make_envelope())
        self.emergency_stop = EmergencyStop()
        self.loop = CapabilityExecutionLoop(
            registry=self.registry,
            action_gate=ActionGate(),
            authority=None if authority is None else AuthorityContext(authority),
            emergency_stop=self.emergency_stop,
            budget=self.budget,
            publish_event=self.bus.publish,
            publish_audit=self.audit_records.append,
        )

    def run(self, *, key: str = "alpha", value: str = "v1") -> Run:
        """Execute one governed run through the real canonical loop."""
        task = Task.create(objective=f"write the demo note for key {key}")
        context = ExecutionContext(
            correlation_id=uuid4(),
            cancellation_token=CancellationSource().token,
            task_id=task.task_id,
        )
        request = write_request(NoteWriteParams(key=key, value=value))
        result = self.loop.run(task, request, context)
        assert result.is_success, "the harness only exercises deterministic terminal states"
        return Run(result.unwrap(), request, context)


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def cache(clock: FakeClock) -> VerifiedResultCache:
    return VerifiedResultCache(clock=clock)


def submission_for(
    run: Run,
    *,
    scope: CapabilityScope | None = None,
    environment: EnvironmentIdentity = _HOST_A,
    procedure: ProcedureRevision | None = None,
    preconditions: dict[str, Any] | None = None,
    ttl: timedelta = timedelta(minutes=5),
    created_at: datetime | None = None,
) -> VerifiedResultSubmission:
    """Build a cache submission from a real run using canonical key material."""
    key = VerifiedResultKey.from_request(
        run.request,
        scope=scope if scope is not None else CapabilityScope(platform=CapabilityPlatform.ANY),
        environment=environment,
        procedure=procedure,
        preconditions={} if preconditions is None else preconditions,
    )
    return VerifiedResultSubmission(
        key=key, outcome=run.outcome, context=run.context, ttl=ttl, created_at=created_at
    )


# ---------------------------------------------------------------------------
# Verified runs are reusable; nothing else ever enters the cache.
# ---------------------------------------------------------------------------


def test_a_real_verified_run_can_be_cached_and_reused(cache: VerifiedResultCache) -> None:
    harness = Harness()
    run = harness.run()

    assert run.outcome.kind is LoopOutcome.VERIFIED
    assert run.outcome.task.status is TaskStatus.SUCCEEDED

    entry = cache.store(submission_for(run))
    lookup = cache.lookup(entry.key)

    assert isinstance(entry, VerifiedResultEntry)
    assert entry.provenance.source == VERIFIED_RESULT_CACHE_SOURCE
    assert entry.provenance.task_id == run.outcome.task.task_id
    assert entry.provenance.correlation_id == run.context.correlation_id
    assert lookup.status is LookupStatus.VERIFIED_HIT
    assert lookup.reusable_outcome is run.outcome
    assert entry.verification is run.outcome.verification
    assert entry.verification.passed is True
    assert entry.observation is run.outcome.observation
    assert entry.observation.data["stored"] is True


def test_reuse_executes_nothing(cache: VerifiedResultCache) -> None:
    harness = Harness()
    run = harness.run()
    entry = cache.store(submission_for(run))
    events_before = len(harness.events)
    audit_before = len(harness.audit_records)

    for _ in range(3):
        assert cache.lookup(entry.key).status is LookupStatus.VERIFIED_HIT
    cache.invalidate_environment(_HOST_A, reason="probe")
    cache.snapshot()

    assert harness.capability.execute_calls == 1
    assert harness.capability.verify_calls == 1
    assert len(harness.events) == events_before
    assert len(harness.audit_records) == audit_before
    assert harness.budget.snapshot().machine_actions == 1


def test_a_verification_failed_run_is_never_cacheable(cache: VerifiedResultCache) -> None:
    harness = Harness(capability=DemoNoteCapability(verification_mode="fail"))
    run = harness.run()

    assert run.outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert run.outcome.task.status is TaskStatus.FAILED
    assert run.outcome.verification is not None
    assert run.outcome.verification.passed is False

    submission = submission_for(run)
    with pytest.raises(VerifiedResultCacheRejection):
        cache.store(submission)

    assert cache.size == 0
    assert cache.lookup(submission.key).status is LookupStatus.MISS


def test_an_execution_failed_run_is_never_cacheable(cache: VerifiedResultCache) -> None:
    harness = Harness(capability=DemoNoteCapability(execution_mode="fail"))
    run = harness.run()

    assert run.outcome.kind is LoopOutcome.EXECUTION_FAILED

    submission = submission_for(run)
    with pytest.raises(VerifiedResultCacheRejection):
        cache.store(submission)
    assert cache.size == 0
    assert cache.lookup(submission.key).status is LookupStatus.MISS


def test_a_raising_capability_run_is_never_cacheable(cache: VerifiedResultCache) -> None:
    harness = Harness(capability=DemoNoteCapability(execution_mode="raise"))
    run = harness.run()

    assert run.outcome.kind is LoopOutcome.EXECUTION_FAILED
    assert run.outcome.verification is None

    with pytest.raises(VerifiedResultCacheRejection):
        cache.store(submission_for(run))
    assert cache.size == 0


def test_a_denied_run_is_never_cacheable(cache: VerifiedResultCache) -> None:
    harness = Harness(authority=None)
    run = harness.run()

    assert run.outcome.kind is LoopOutcome.DENIED
    assert run.outcome.observation is None
    assert harness.capability.execute_calls == 0

    with pytest.raises(VerifiedResultCacheRejection):
        cache.store(submission_for(run))
    assert cache.size == 0


def test_a_succeeded_execution_with_failed_verification_is_not_cacheable(
    cache: VerifiedResultCache,
) -> None:
    """``ExecutionResult.succeeded`` is never success: only verification is."""
    harness = Harness(capability=DemoNoteCapability(verification_mode="fail"))
    run = harness.run()

    assert run.outcome.execution is not None
    assert run.outcome.execution.succeeded is True
    assert run.outcome.kind is not LoopOutcome.VERIFIED

    with pytest.raises(VerifiedResultCacheRejection):
        cache.store(submission_for(run))
    assert cache.lookup(submission_for(run).key).reusable_outcome is None


# ---------------------------------------------------------------------------
# Reuse is bound to the real canonical preconditions.
# ---------------------------------------------------------------------------


def test_reuse_is_bound_to_the_real_request_params(cache: VerifiedResultCache) -> None:
    harness = Harness()
    first = harness.run(key="alpha", value="v1")
    entry = cache.store(submission_for(first))

    second = harness.run(key="alpha", value="v2")
    other_key = VerifiedResultKey.from_request(
        second.request,
        scope=CapabilityScope(platform=CapabilityPlatform.ANY),
        environment=_HOST_A,
    )

    assert other_key != entry.key
    assert cache.lookup(other_key).status is LookupStatus.MISS

    cache.store(submission_for(second))
    assert cache.size == 2
    assert cache.lookup(entry.key).reusable_outcome is first.outcome
    assert cache.lookup(other_key).reusable_outcome is second.outcome


def test_reuse_is_bound_to_the_capability_version(cache: VerifiedResultCache) -> None:
    harness = Harness()
    run = harness.run()
    entry = cache.store(submission_for(run))

    upgraded = VerifiedResultKey(
        capability=CapabilityIdentity(
            name=CapabilityName("demo.note.write"), version=CapabilityVersion(1, 0, 1)
        ),
        params=entry.key.params,
        scope=entry.key.scope,
        environment=entry.key.environment,
    )
    lookup = cache.lookup(upgraded)

    assert lookup.status is LookupStatus.INCOMPATIBLE
    assert lookup.reusable_outcome is None
    assert lookup.reasons == (
        "a stored verified result differs in the canonical fact 'capability_version'",
    )


def test_reuse_is_bound_to_scope_environment_and_preconditions(
    cache: VerifiedResultCache,
) -> None:
    harness = Harness()
    run = harness.run()
    entry = cache.store(
        submission_for(
            run,
            scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
            environment=_HOST_A,
            procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=3),
            preconditions={"mailbox": "drafts"},
        )
    )

    recorded = ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=3)
    probes = (
        submission_for(
            run, scope=CapabilityScope(platform=CapabilityPlatform.ANY), procedure=recorded
        ).key,
        submission_for(run, environment=_HOST_B, procedure=recorded).key,
        submission_for(
            run, procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=4)
        ).key,
        submission_for(run, preconditions={"mailbox": "sent"}, procedure=recorded).key,
    )
    for probe in probes:
        assert probe != entry.key
        lookup = cache.lookup(probe)
        assert lookup.status is LookupStatus.INCOMPATIBLE, probe
        assert lookup.reusable_outcome is None

    # Dropping the procedure changes the operation signature itself rather than
    # one canonical fact about it, so nothing comparable is recorded at all.
    without_procedure = cache.lookup(submission_for(run).key)
    assert without_procedure.status is LookupStatus.MISS
    assert without_procedure.reusable_outcome is None


def test_a_second_verified_run_supersedes_the_recorded_evidence(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    harness = Harness()
    first = harness.run()
    entry = cache.store(submission_for(first))

    clock.advance(timedelta(minutes=1))
    second = harness.run()
    replaced = cache.store(submission_for(second))

    assert replaced.key == entry.key
    assert replaced.fingerprint == entry.fingerprint
    assert replaced.outcome is second.outcome
    assert replaced.provenance.task_id == second.outcome.task.task_id
    assert replaced.created_at == entry.created_at + timedelta(minutes=1)
    assert cache.size == 1
    assert cache.lookup(entry.key).reusable_outcome is second.outcome
    assert harness.capability.execute_calls == 2


# ---------------------------------------------------------------------------
# Freshness, invalidation, and restart over the real loop.
# ---------------------------------------------------------------------------


def test_reuse_expires_at_the_explicit_boundary(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    harness = Harness()
    run = harness.run()
    entry = cache.store(submission_for(run, ttl=timedelta(minutes=10)))

    clock.advance(timedelta(minutes=9, seconds=59))
    assert cache.lookup(entry.key).status is LookupStatus.VERIFIED_HIT

    clock.advance(timedelta(seconds=1))
    stale = cache.lookup(entry.key)
    assert stale.status is LookupStatus.STALE
    assert stale.reusable_outcome is None
    assert stale.entry is entry

    # Expiry never re-executes anything by itself; it only stops reuse.
    assert harness.capability.execute_calls == 1


def test_environment_invalidation_stops_reuse_for_that_environment_only(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    harness = Harness()
    run_a = harness.run(key="alpha", value="v1")
    run_b = harness.run(key="alpha", value="v2")
    entry_a = cache.store(submission_for(run_a, environment=_HOST_A))
    entry_b = cache.store(submission_for(run_b, environment=_HOST_B))

    invalidated = cache.invalidate_environment(_HOST_A, reason="host-a profile was reset")

    assert len(invalidated) == 1
    assert invalidated[0].fingerprint == entry_a.fingerprint
    assert invalidated[0].state is EntryState.INVALIDATED
    assert invalidated[0].invalidation is not None
    assert invalidated[0].invalidation.cause is InvalidationCause.ENVIRONMENT_CHANGED
    assert invalidated[0].invalidation.invalidated_at == clock.now
    assert cache.lookup(entry_a.key).status is LookupStatus.INVALIDATED
    assert cache.lookup(entry_a.key).reusable_outcome is None
    assert cache.lookup(entry_b.key).status is LookupStatus.VERIFIED_HIT
    assert cache.lookup(entry_b.key).reusable_outcome is run_b.outcome
    assert invalidated[0].outcome is run_a.outcome


def test_explicit_invalidation_is_terminal_for_that_result(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    harness = Harness()
    run = harness.run()
    entry = cache.store(submission_for(run))

    cache.invalidate(entry.key, reason="the target application was upgraded")

    lookup = cache.lookup(entry.key)
    assert lookup.status is LookupStatus.INVALIDATED
    assert lookup.reusable_outcome is None

    # Replaying the very evidence that was invalidated is refused, whether it is
    # stamped with its original creation time or with the invalidation instant.
    for replayed in (entry.created_at, clock.now):
        with pytest.raises(VerifiedResultCacheRejection):
            cache.store(submission_for(run, created_at=replayed))
    assert cache.lookup(entry.key).status is LookupStatus.INVALIDATED

    clock.advance(timedelta(seconds=1))
    fresh_run = harness.run()
    revived = cache.store(submission_for(fresh_run))
    assert revived.state is EntryState.ACTIVE
    assert cache.lookup(entry.key).status is LookupStatus.VERIFIED_HIT
    assert cache.lookup(entry.key).reusable_outcome is fresh_run.outcome


def test_a_restart_forgets_every_verified_result(clock: FakeClock) -> None:
    """The cache is in-memory: a new process re-executes and re-verifies."""
    harness = Harness()
    run = harness.run()
    first = VerifiedResultCache(clock=clock)
    entry = first.store(submission_for(run))
    assert first.lookup(entry.key).status is LookupStatus.VERIFIED_HIT

    restarted = VerifiedResultCache(clock=clock)
    assert restarted.size == 0
    assert restarted.lookup(entry.key).status is LookupStatus.MISS
    assert restarted.lookup(entry.key).reusable_outcome is None

    rerun = harness.run()
    restarted.store(submission_for(rerun))
    assert harness.capability.execute_calls == 2
    assert harness.capability.verify_calls == 2
    assert restarted.lookup(entry.key).reusable_outcome is rerun.outcome


# ---------------------------------------------------------------------------
# A hit is Router evidence, never a routing decision (the Router is untouched).
# ---------------------------------------------------------------------------


def test_a_verified_hit_is_l0_evidence_and_a_miss_is_not(
    cache: VerifiedResultCache,
) -> None:
    router = ExecutionLevelRouter()
    harness = Harness()
    run = harness.run()
    entry = cache.store(submission_for(run))
    miss_key = submission_for(harness.run(key="beta")).key

    hit = cache.lookup(entry.key)
    miss = cache.lookup(miss_key)

    assert router.route(RoutingEvidence(verified_reusable_result=hit.is_reusable)).level is (
        ExecutionLevel.L0_CACHE
    )
    assert router.route(RoutingEvidence(verified_reusable_result=miss.is_reusable)).level is (
        ExecutionLevel.L5_EXPLORATORY
    )
    assert harness.capability.execute_calls == 2


def test_stale_and_invalidated_hits_are_not_l0_evidence(
    cache: VerifiedResultCache, clock: FakeClock
) -> None:
    router = ExecutionLevelRouter()
    harness = Harness()
    stale_run = harness.run(key="alpha", value="v1")
    stale_entry = cache.store(submission_for(stale_run, ttl=timedelta(seconds=1)))
    invalidated_run = harness.run(key="alpha", value="v2")
    invalidated_entry = cache.store(submission_for(invalidated_run))
    cache.invalidate(invalidated_entry.key, reason="superseded")
    clock.advance(timedelta(seconds=2))

    for key in (stale_entry.key, invalidated_entry.key):
        lookup = cache.lookup(key)
        assert lookup.status in (LookupStatus.STALE, LookupStatus.INVALIDATED)
        assert lookup.is_reusable is False
        assert router.route(RoutingEvidence(verified_reusable_result=lookup.is_reusable)).level is (
            ExecutionLevel.L5_EXPLORATORY
        )


# ---------------------------------------------------------------------------
# Deterministic keying across processes.
# ---------------------------------------------------------------------------

_FINGERPRINT_SCRIPT = """
from agentx.capabilities.abi import (
    CapabilityIdentity,
    CapabilityName,
    CapabilityPlatform,
    CapabilityScope,
    CapabilityVersion,
)
from agentx.capabilities.verified_result_cache import (
    EnvironmentIdentity,
    ProcedureRevision,
    VerifiedResultKey,
)
from agentx.core.ids import ProcedureId

key = VerifiedResultKey(
    capability=CapabilityIdentity(
        name=CapabilityName("demo.note.write"), version=CapabilityVersion(1, 2, 3)
    ),
    params={
        "zeta": 1,
        "alpha": {"nested": [1, 2, {"b": True, "a": None}]},
        "beta": "text",
        "ratio": 1.5,
    },
    scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
    environment=EnvironmentIdentity(
        name="host-a", attributes={"profile": "user-1", "os": {"build": 22631}}
    ),
    procedure=ProcedureRevision(
        procedure_id=ProcedureId.parse("3f2b8c1e-9d47-4a1e-9c62-1b0f5e8d7a64"), revision=7
    ),
    preconditions={"mailbox": "drafts", "online": True},
)
print(key.canonical_text())
print(key.fingerprint())
print(key.operation_fingerprint())
print(hash(key))
"""


def _child_fingerprints(hash_seed: str) -> tuple[str, str, str]:
    """Build the same key in a child process and return its digests."""
    environment = {**os.environ, "PYTHONHASHSEED": hash_seed}
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _FINGERPRINT_SCRIPT],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        env=environment,
    )
    canonical_text, fingerprint, operation_fingerprint, _child_hash = completed.stdout.splitlines()
    return canonical_text, fingerprint, operation_fingerprint


def _reference_key() -> VerifiedResultKey:
    return VerifiedResultKey(
        capability=CapabilityIdentity(
            name=CapabilityName("demo.note.write"), version=CapabilityVersion(1, 2, 3)
        ),
        params={
            "zeta": 1,
            "alpha": {"nested": [1, 2, {"b": True, "a": None}]},
            "beta": "text",
            "ratio": 1.5,
        },
        scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
        environment=EnvironmentIdentity(
            name="host-a", attributes={"profile": "user-1", "os": {"build": 22631}}
        ),
        procedure=ProcedureRevision(procedure_id=_PROCEDURE_ID, revision=7),
        preconditions={"mailbox": "drafts", "online": True},
    )


@pytest.mark.integration
@pytest.mark.parametrize("hash_seed", ["random", "0", "12345"])
def test_key_fingerprints_are_identical_across_processes(hash_seed: str) -> None:
    """Canonical keying has no PYTHONHASHSEED dependency, in or across processes."""
    reference = _reference_key()

    canonical_text, fingerprint, operation_fingerprint = _child_fingerprints(hash_seed)

    assert canonical_text == reference.canonical_text()
    assert fingerprint == reference.fingerprint()
    assert operation_fingerprint == reference.operation_fingerprint()
    assert len(fingerprint) == 64


@pytest.mark.integration
def test_child_processes_with_different_hash_seeds_agree_with_each_other() -> None:
    randomized = _child_fingerprints("random")
    zero = _child_fingerprints("0")

    assert randomized == zero


def test_a_key_built_from_a_real_request_is_deterministic() -> None:
    request = write_request(NoteWriteParams(key="alpha", value="v1"))
    scope = CapabilityScope(platform=CapabilityPlatform.ANY)

    first = VerifiedResultKey.from_request(request, scope=scope, environment=_HOST_A)
    second = VerifiedResultKey.from_request(request, scope=scope, environment=_HOST_A)
    reordered = VerifiedResultKey(
        capability=first.capability,
        params={"value": "v1", "key": "alpha"},
        scope=scope,
        environment=EnvironmentIdentity(name="host-a", attributes={"profile": "user-1"}),
    )

    assert first.fingerprint() == second.fingerprint()
    assert first.fingerprint() == reordered.fingerprint()
    assert first.canonical_text() == reordered.canonical_text()
