"""Concurrency proofs for filesystem structural mutations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from agentx.capabilities.filesystem_structural import (
    FilesystemCreateDirectoryCapability,
    FilesystemMoveFileCapability,
    create_directory_request,
    move_file_request,
)
from agentx.core.execution import CancellationSource, ExecutionContext


def _context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
    )


def test_concurrent_create_same_directory_has_exactly_one_success(tmp_path: Path) -> None:
    target = tmp_path / "race"
    request = create_directory_request(str(target.resolve()))

    def attempt() -> bool:
        return FilesystemCreateDirectoryCapability().execute(request, _context()).succeeded

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _index: attempt(), range(2)))

    assert sum(results) == 1
    assert target.is_dir()


def test_concurrent_move_same_source_cannot_duplicate_file_identity(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destinations = (tmp_path / "a.txt", tmp_path / "b.txt")
    source.write_bytes(b"one-source-only")

    def attempt(destination: Path) -> bool:
        capability = FilesystemMoveFileCapability()
        request = move_file_request(str(source.resolve()), str(destination.resolve()))
        return capability.execute(request, _context()).succeeded

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(attempt, destinations))

    assert sum(results) == 1
    existing = tuple(path for path in destinations if path.exists())
    assert len(existing) == 1
    assert existing[0].read_bytes() == b"one-source-only"
    assert not source.exists()
