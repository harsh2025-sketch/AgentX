"""Failure-injection proofs for AX-260-265 filesystem mutations."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from agentx.capabilities.filesystem_structural import (
    FilesystemDeleteDirectoryCapability,
    FilesystemMoveFileCapability,
    delete_directory_request,
    move_file_request,
)
from agentx.core.execution import CancellationSource, ExecutionContext


def _context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
    )


def test_move_os_failure_never_fabricates_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_bytes(b"payload")

    def fail_rename(self: Path, target: Path) -> Path:
        raise PermissionError("injected denial")

    monkeypatch.setattr(Path, "rename", fail_rename)
    capability = FilesystemMoveFileCapability()
    request = move_file_request(str(source.resolve()), str(destination.resolve()))

    execution = capability.execute(request, _context())

    assert execution.succeeded is False
    assert source.read_bytes() == b"payload"
    assert not destination.exists()


def test_directory_delete_race_fails_closed_when_rmdir_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "empty-at-preflight"
    target.mkdir()

    def fail_rmdir(self: Path) -> None:
        raise OSError("injected post-preflight race")

    monkeypatch.setattr(Path, "rmdir", fail_rmdir)
    capability = FilesystemDeleteDirectoryCapability()
    request = delete_directory_request(str(target.resolve()))

    execution = capability.execute(request, _context())

    assert execution.succeeded is False
    assert target.is_dir()
    assert capability.verify(request, execution.observation, _context()).passed is False
