from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentx.core.knowledge import ProvenanceKind, ProvenanceReference
from agentx.world_model import (
    CacheRefreshState,
    FilesystemExistence,
    WorldFreshness,
    WorldModel,
)

SOURCE = ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="tests.m10.environment")


def test_m10_real_filesystem_change_is_detected_invalidated_and_reobserved(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "input.txt"
    second_path = tmp_path / "stable.txt"
    first_path.write_text("state-a", encoding="utf-8")
    second_path.write_text("unchanged", encoding="utf-8")
    model = WorldModel()
    first_id = model.track_filesystem_path(
        str(first_path),
        environment_id="env-real-fs",
        windows=False,
        source=SOURCE,
        ttl=timedelta(hours=1),
    )
    second_id = model.track_filesystem_path(
        str(second_path),
        environment_id="env-real-fs",
        windows=False,
        source=SOURCE,
        ttl=timedelta(hours=1),
    )
    at = datetime.now(UTC)

    first_a = model.cache.lookup(first_id, at=at)
    second_a = model.cache.lookup(second_id, at=at)
    assert first_a.state is CacheRefreshState.REFRESH_SUCCESS
    assert second_a.state is CacheRefreshState.REFRESH_SUCCESS
    assert first_a.value is not None
    before_modified_ns = first_a.value.modified_ns  # type: ignore[union-attr]

    # Real external mutation: bypass WorldModel/cache/invalidation entirely.
    first_path.write_text("state-b-is-different", encoding="utf-8")

    first_b = model.cache.lookup(first_id, at=datetime.now(UTC))
    second_b = model.cache.lookup(second_id, at=datetime.now(UTC))

    assert first_b.state is CacheRefreshState.REFRESH_SUCCESS
    assert first_b.freshness is WorldFreshness.FRESH
    assert first_b.value is not None
    assert first_b.value.modified_ns != before_modified_ns  # type: ignore[union-attr]
    assert second_b.state is CacheRefreshState.FRESH_HIT
    assert second_b.freshness is WorldFreshness.FRESH


def test_m10_real_delete_and_recreate_change_state_without_manual_invalidation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mutable.txt"
    path.write_text("present", encoding="utf-8")
    model = WorldModel()
    entity_id = model.track_filesystem_path(
        str(path),
        environment_id="env-real-delete",
        windows=False,
        source=SOURCE,
        ttl=timedelta(hours=1),
    )

    present = model.cache.lookup(entity_id, at=datetime.now(UTC))
    assert present.value is not None
    assert present.value.existence is FilesystemExistence.EXISTS  # type: ignore[union-attr]

    path.unlink()
    missing = model.cache.lookup(entity_id, at=datetime.now(UTC))
    assert missing.state is CacheRefreshState.REFRESH_SUCCESS
    assert missing.value is not None
    assert missing.value.existence is FilesystemExistence.MISSING  # type: ignore[union-attr]

    path.write_text("recreated-with-new-identity", encoding="utf-8")
    recreated = model.cache.lookup(entity_id, at=datetime.now(UTC))
    assert recreated.state is CacheRefreshState.REFRESH_SUCCESS
    assert recreated.value is not None
    assert recreated.value.existence is FilesystemExistence.EXISTS  # type: ignore[union-attr]


def test_m10_restart_boundary_does_not_restore_ephemeral_world_cache(tmp_path: Path) -> None:
    marker = tmp_path / "cache-proof.txt"
    marker.write_text("before-restart", encoding="utf-8")
    script = (
        "from datetime import timedelta\n"
        "from agentx.core.knowledge import ProvenanceKind, ProvenanceReference\n"
        "from agentx.world_model import WorldModel\n"
        f"p={str(marker)!r}\n"
        "m=WorldModel()\n"
        "eid=m.track_filesystem_path(p, environment_id='env-restart', windows=False, "
        "source=ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference='restart-a'), "
        "ttl=timedelta(hours=24))\n"
        "from datetime import UTC, datetime\n"
        "assert m.cache.lookup(eid, at=datetime.now(UTC)).value is not None\n"
        "assert len(m.cache)==1\n"
    )
    first = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr

    marker.write_text("changed-while-agent-stopped", encoding="utf-8")
    second_script = (
        "from agentx.world_model import WorldModel\nm=WorldModel()\nassert len(m.cache)==0\n"
    )
    second = subprocess.run(
        [sys.executable, "-c", second_script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 0, second.stderr
