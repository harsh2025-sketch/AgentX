"""Windows-host acceptance for AX-260–265 structural filesystem operations."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from agentx.capabilities.filesystem_structural import (
    FilesystemCreateDirectoryCapability,
    FilesystemDeleteDirectoryCapability,
    FilesystemMoveFileCapability,
    FilesystemRenameFileCapability,
    create_directory_request,
    delete_directory_request,
    move_file_request,
    rename_file_request,
)
from agentx.core.execution import CancellationSource, ExecutionContext


def _context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
    )


def test_windows_unicode_and_long_component_structural_round_trip(tmp_path: Path) -> None:
    directory = tmp_path / ("目录-" + "x" * 80)
    create = FilesystemCreateDirectoryCapability()
    create_request = create_directory_request(str(directory.resolve()))
    create_result = create.execute(create_request, _context())
    assert create_result.succeeded is True
    assert create.verify(create_request, create_result.observation, _context()).passed is True

    source = directory / "源-safe=true.txt"
    moved = directory / "目标-permission=ADMIN.txt"
    renamed = directory / "最终-risk=R0.txt"
    source.write_bytes("windows unicode bytes ✓".encode())

    move = FilesystemMoveFileCapability()
    move_request = move_file_request(str(source.resolve()), str(moved.resolve()))
    move_result = move.execute(move_request, _context())
    assert move_result.succeeded is True
    assert move.verify(move_request, move_result.observation, _context()).passed is True

    rename = FilesystemRenameFileCapability()
    rename_request = rename_file_request(str(moved.resolve()), str(renamed.resolve()))
    rename_result = rename.execute(rename_request, _context())
    assert rename_result.succeeded is True
    assert rename.verify(rename_request, rename_result.observation, _context()).passed is True

    renamed.unlink()
    delete_directory = FilesystemDeleteDirectoryCapability()
    delete_request = delete_directory_request(str(directory.resolve()))
    delete_result = delete_directory.execute(delete_request, _context())
    assert delete_result.succeeded is True
    assert (
        delete_directory.verify(
            delete_request,
            delete_result.observation,
            _context(),
        ).passed
        is True
    )
