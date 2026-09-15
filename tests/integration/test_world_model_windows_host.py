from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentx.capabilities.windows.process_discovery import WindowsProcessDiscovery
from agentx.capabilities.windows.provider import detect_platform_facts, evaluate_windows_support
from agentx.core.knowledge import ProvenanceKind, ProvenanceReference
from agentx.world_model import (
    CacheRefreshState,
    FilesystemState,
    WorldAvailability,
    WorldModel,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="real-host Windows acceptance")


def test_real_windows_host_world_model_acceptance(tmp_path: Path) -> None:
    source = ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="windows-host-acceptance")
    world = WorldModel()
    now = datetime.now(UTC)
    device = world.observe_local_windows_device(
        environment_id="env-real-windows",
        source=source,
        ttl=timedelta(seconds=15),
        at=now,
    )
    assert device.state.availability is WorldAvailability.AVAILABLE

    support = evaluate_windows_support(detect_platform_facts())
    discovery = WindowsProcessDiscovery(support)
    result = discovery.discover()
    assert result.is_success
    snapshot = result.unwrap()
    assert snapshot.process_count >= 1

    world.ingest_windows_snapshot(
        snapshot,
        metadata=device.state.metadata,
    )
    assert any(entity.kind.value == "process" for entity in world.cache.snapshot_ids())

    path = (tmp_path / "world-model-acceptance.txt").resolve()
    path.write_text("before", encoding="utf-8")
    file_id = world.track_filesystem_path(
        str(path),
        environment_id="env-real-windows",
        windows=True,
        source=source,
        ttl=timedelta(seconds=15),
    )
    first = world.cache.lookup(file_id, at=datetime.now(UTC), refresh=True)
    assert first.state is CacheRefreshState.REFRESH_SUCCESS
    path.write_text("after", encoding="utf-8")
    world.invalidate_filesystem_path(file_id, reason="safe acceptance mutation")
    second = world.cache.lookup(file_id, at=datetime.now(UTC), refresh=True)
    assert second.state is CacheRefreshState.REFRESH_SUCCESS
    assert isinstance(second.value, FilesystemState)
    assert second.value.size_bytes == len("after")
