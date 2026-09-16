from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.ids import KnowledgeId, TaskId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeType,
    ProvenanceKind,
    ProvenanceReference,
)
from agentx.core.provenance import EvidenceKind, EvidenceReference
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.world_model import (
    FilesystemState,
    LinkVerificationStatus,
    WorldEntityId,
    WorldEntityKind,
    WorldFreshness,
    WorldHiveLink,
    WorldHiveLinkage,
    WorldModel,
    WorldModelValidationError,
)

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
SOURCE = ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="tests.integration.world")


def _evidence(reference: str = "obs-1") -> tuple[EvidenceReference, ...]:
    return (
        EvidenceReference(
            kind=EvidenceKind.OBSERVATION,
            reference=reference,
            provenance=SOURCE,
            observed_at=NOW,
        ),
    )


def test_ax421_durable_link_survives_restart_without_becoming_current_world_truth(
    tmp_path: Path,
) -> None:
    store = KnowledgeStore(SQLiteDatabase((tmp_path / "agentx.db").resolve()))
    target = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="historical application relationship",
        provenance=SOURCE,
        created_at=NOW - timedelta(days=1),
    )
    store.insert(target)
    entity_id = WorldEntityId("env-local", WorldEntityKind.APPLICATION, "app:editor")
    link = WorldHiveLink(
        world_entity_id=entity_id,
        knowledge_id=target.knowledge_id,
        evidence=_evidence(),
        observed_at=NOW,
        environment_id="env-local",
        verification_status=LinkVerificationStatus.UNVERIFIED,
        durable=True,
    )

    first = WorldHiveLinkage(knowledge_store=store)
    persisted_id = first.add(link)
    assert persisted_id is not None

    restarted = WorldModel(knowledge_store=store)
    loaded = restarted.hive.load_durable()
    assert loaded == (link,)
    assert restarted.hive.links_for(entity_id) == (link,)
    current = restarted.cache.lookup(entity_id, at=NOW, refresh=False)
    assert current.freshness is WorldFreshness.UNKNOWN
    assert current.value is None


def test_ax421_current_world_observation_is_not_auto_promoted_to_verified_hive(
    tmp_path: Path,
) -> None:
    store = KnowledgeStore(SQLiteDatabase((tmp_path / "agentx.db").resolve()))
    target = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.OBSERVATION,
        content="window was visible yesterday",
        provenance=SOURCE,
        created_at=NOW - timedelta(days=1),
    )
    store.insert(target)
    entity_id = WorldEntityId("env-local", WorldEntityKind.WINDOW, "hwnd:7|pid:2")
    link = WorldHiveLink(
        world_entity_id=entity_id,
        knowledge_id=target.knowledge_id,
        evidence=_evidence("world-current"),
        observed_at=NOW,
        environment_id="env-local",
        durable=True,
    )

    linkage = WorldHiveLinkage(knowledge_store=store)
    persisted_id = linkage.add(link)
    assert persisted_id is not None
    persisted_link_record = store.get(persisted_id)
    assert persisted_link_record is not None
    assert persisted_link_record.status.value == "unverified"


def test_filesystem_chain_task_binding_mutation_invalidation_refresh_and_isolation(
    tmp_path: Path,
) -> None:
    path_a = (tmp_path / "a.txt").resolve()
    path_b = (tmp_path / "b.txt").resolve()
    path_a.write_text("a", encoding="utf-8")
    path_b.write_text("b", encoding="utf-8")
    world = WorldModel()
    task_a = TaskId.create()
    task_b = TaskId.create()
    entity_a = world.track_filesystem_path(
        str(path_a),
        environment_id="env-local",
        windows=False,
        source=SOURCE,
        ttl=timedelta(seconds=30),
        task_id=task_a,
    )
    entity_b = world.track_filesystem_path(
        str(path_b),
        environment_id="env-local",
        windows=False,
        source=SOURCE,
        ttl=timedelta(seconds=30),
        task_id=task_b,
    )
    assert isinstance(world.cache.lookup(entity_a, at=datetime.now(UTC)).value, FilesystemState)
    assert isinstance(world.cache.lookup(entity_b, at=datetime.now(UTC)).value, FilesystemState)

    cancel_a = CancellationSource()
    cancel_b = CancellationSource()
    context_a = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=cancel_a.token,
        task_id=task_a,
    )
    context_b = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=cancel_b.token,
        task_id=task_b,
    )
    world.tasks.bind(context_a, entity_ids=(entity_a,), evidence=_evidence("a"), updated_at=NOW)
    world.tasks.bind(context_b, entity_ids=(entity_b,), evidence=_evidence("b"), updated_at=NOW)

    path_a.write_text("changed", encoding="utf-8")
    world.invalidate_filesystem_path(entity_a, reason="governed mutation")
    assert world.tasks.get(task_a) is None
    assert world.tasks.get(task_b) is not None
    refreshed = world.cache.lookup(entity_a, at=datetime.now(UTC), refresh=True)
    assert isinstance(refreshed.value, FilesystemState)
    assert refreshed.value.size_bytes == len("changed")
    unaffected = world.cache.lookup(entity_b, at=datetime.now(UTC), refresh=False)
    assert unaffected.freshness is WorldFreshness.FRESH


def test_world_state_types_have_no_authority_surface() -> None:
    public_forbidden = {
        "grant_permission",
        "permission",
        "authority",
        "risk",
        "action_gate",
        "emergency_stop",
        "execute",
        "verify",
        "mark_task_succeeded",
    }
    state_classes = (
        FilesystemState,
        WorldHiveLink,
    )
    for state_class in state_classes:
        assert public_forbidden.isdisjoint(set(dir(state_class)))


def test_world_hive_relationship_can_preserve_contradiction_and_supersession_ids() -> None:
    target_a = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="claim a",
        provenance=SOURCE,
        created_at=NOW,
    )
    target_b = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="claim b",
        provenance=SOURCE,
        created_at=NOW,
    )
    entity_id = WorldEntityId("env-local", WorldEntityKind.APPLICATION, "app:x")
    link = WorldHiveLink(
        world_entity_id=entity_id,
        knowledge_id=target_a.knowledge_id,
        evidence=_evidence(),
        observed_at=NOW,
        environment_id="env-local",
        verification_status=LinkVerificationStatus.CONTRADICTED,
        contradiction_ids=(target_b.knowledge_id,),
        supersedes_ids=(target_b.knowledge_id,),
        durable=False,
    )
    assert link.contradiction_ids == (target_b.knowledge_id,)
    assert link.supersedes_ids == (target_b.knowledge_id,)
    assert link.verification_status is LinkVerificationStatus.CONTRADICTED


def test_ax421_restart_rejects_malformed_durable_link_payload(tmp_path: Path) -> None:
    store = KnowledgeStore(SQLiteDatabase((tmp_path / "agentx.db").resolve()))
    malformed = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.OBSERVATION,
        content="{not valid json",
        provenance=ProvenanceReference(
            kind=ProvenanceKind.DERIVED,
            reference="agentx.world_model.link/v1",
        ),
        created_at=NOW,
    )
    store.insert(malformed)

    with pytest.raises(WorldModelValidationError, match="malformed"):
        WorldHiveLinkage(knowledge_store=store).load_durable()


def test_ax421_restart_rejects_scalar_type_coercion_in_durable_link(tmp_path: Path) -> None:
    store = KnowledgeStore(SQLiteDatabase((tmp_path / "agentx.db").resolve()))
    malformed = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.OBSERVATION,
        content=(
            '{"contradiction_ids":[],"durable":"false","environment_id":"env-local",'
            '"evidence":[{"kind":"observation","observed_at":"2026-09-15T10:00:00Z",'
            '"provenance":{"kind":"system","reference":"tests.integration.world"},'
            '"reference":"obs-1"}],"knowledge_id":"knowledge:00000000-0000-0000-0000-000000000001",'
            '"observed_at":"2026-09-15T10:00:00Z","schema_version":1,'
            '"supersedes_ids":[],"verification_status":"unverified",'
            '"world_entity":{"environment_id":"env-local","kind":"application","value":"app:x"}}'
        ),
        provenance=ProvenanceReference(
            kind=ProvenanceKind.DERIVED, reference="agentx.world_model.link/v1"
        ),
        created_at=NOW,
    )
    store.insert(malformed)
    with pytest.raises(WorldModelValidationError, match="durable must be a bool"):
        WorldHiveLinkage(knowledge_store=store).load_durable()


def test_ax420_ax421_relationship_metadata_is_explicitly_bounded() -> None:
    entity_id = WorldEntityId("env-local", WorldEntityKind.APPLICATION, "app:x")
    too_much_evidence = tuple(_evidence(f"e-{index}")[0] for index in range(129))
    with pytest.raises(WorldModelValidationError, match="evidence is unbounded"):
        WorldHiveLink(
            world_entity_id=entity_id,
            knowledge_id=KnowledgeId.create(),
            evidence=too_much_evidence,
            observed_at=NOW,
            environment_id="env-local",
        )
