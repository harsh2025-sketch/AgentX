"""Unit tests for M7.05 governed filesystem structural operations.

These tests exercise the module-level operation functions (which return
``Result`` values and never raise for expected outcomes) and the canonical
``Capability`` wrappers (descriptor / ``execute`` / ``verify``) directly
against a real temporary filesystem. They never depend on Worker-01's text
read/write surface and never import the governed loop (that lives in the
integration file).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from agentx.capabilities import filesystem_structure as fs
from agentx.core.errors import ErrorCategory
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskLevel

# Operation-layer error codes under test.
_INVALID_PATH = f"{fs._PREFIX}.invalid_path"
_DESTINATION_EXISTS = f"{fs._PREFIX}.destination_exists"
_NOT_FOUND = f"{fs._PREFIX}.not_found"


def _context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=None,
    )


def _execute(capability: Any, request: Any) -> Any:
    return capability.execute(request, _context())


# --------------------------------------------------------------------------
# stat_path
# --------------------------------------------------------------------------


def test_stat_file(tmp_path: Any) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("hello", encoding="utf-8")
    result = fs.stat_path(str(target))
    assert result.is_success
    info = result.unwrap()
    assert info.exists is True
    assert info.kind is fs.PathKind.FILE
    assert info.size_bytes == len("hello")
    assert info.is_symlink is False


def test_stat_directory(tmp_path: Any) -> None:
    target = tmp_path / "adir"
    target.mkdir()
    result = fs.stat_path(str(target))
    assert result.is_success
    info = result.unwrap()
    assert info.exists is True
    assert info.kind is fs.PathKind.DIRECTORY
    # Directory size is not reported as a file size.
    assert info.size_bytes is None


def test_stat_not_found_reports_absence_as_data(tmp_path: Any) -> None:
    result = fs.stat_path(str(tmp_path / "missing"))
    assert result.is_success
    info = result.unwrap()
    assert info.exists is False
    assert info.kind is fs.PathKind.OTHER


# --------------------------------------------------------------------------
# list_directory
# --------------------------------------------------------------------------


def test_list_directory_deterministic_ordering(tmp_path: Any) -> None:
    for name in ("zeta.txt", "alpha.txt", "MidFile", "000_first"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    result = fs.list_directory(str(tmp_path))
    assert result.is_success
    listing = result.unwrap()
    names = [entry.name for entry in listing.entries]
    assert names == sorted(names)
    assert listing.truncated is False
    assert listing.total_entries == 4


def test_list_directory_is_bounded(tmp_path: Any) -> None:
    for index in range(fs.DEFAULT_MAX_ENTRIES + 20):
        (tmp_path / f"file_{index:04d}").write_text("x", encoding="utf-8")
    result = fs.list_directory(str(tmp_path))
    assert result.is_success
    listing = result.unwrap()
    assert listing.truncated is True
    assert listing.total_entries == fs.DEFAULT_MAX_ENTRIES + 20
    assert len(listing.entries) == fs.DEFAULT_MAX_ENTRIES
    # Explicit small bound is honoured.
    small = fs.list_directory(str(tmp_path), max_entries=5).unwrap()
    assert len(small.entries) == 5


def test_list_directory_exceeding_hard_bound_is_rejected(tmp_path: Any) -> None:
    result = fs.list_directory(str(tmp_path), max_entries=fs.HARD_MAX_ENTRIES + 1)
    assert result.is_failure
    assert result.unwrap_error().category is ErrorCategory.VALIDATION


def test_list_directory_missing_or_non_directory(tmp_path: Any) -> None:
    missing = fs.list_directory(str(tmp_path / "nope"))
    assert missing.is_failure
    assert missing.unwrap_error().code == _NOT_FOUND
    file_target = tmp_path / "afile.txt"
    file_target.write_text("x", encoding="utf-8")
    wrong_kind = fs.list_directory(str(file_target))
    assert wrong_kind.is_failure
    assert wrong_kind.unwrap_error().code == f"{fs._PREFIX}.wrong_path_kind"


# --------------------------------------------------------------------------
# create_directory
# --------------------------------------------------------------------------


def test_create_directory(tmp_path: Any) -> None:
    target = tmp_path / "created"
    result = fs.create_directory(str(target))
    assert result.is_success
    assert target.is_dir()
    info = fs.stat_path(str(target)).unwrap()
    assert info.exists and info.kind is fs.PathKind.DIRECTORY


def test_create_directory_existing_is_conflict(tmp_path: Any) -> None:
    target = tmp_path / "exists"
    target.mkdir()
    result = fs.create_directory(str(target))
    assert result.is_failure
    assert result.unwrap_error().code == _DESTINATION_EXISTS
    assert result.unwrap_error().category is ErrorCategory.CONFLICT


def test_create_directory_requires_existing_parent(tmp_path: Any) -> None:
    result = fs.create_directory(str(tmp_path / "no" / "such" / "parent"))
    assert result.is_failure
    assert result.unwrap_error().code == f"{fs._PREFIX}.parent_not_found"
    # No parents=True equivalent: nothing was created.
    assert not (tmp_path / "no").exists()


def test_create_directory_does_not_overwrite_existing_file(tmp_path: Any) -> None:
    target = tmp_path / "occupied"
    target.write_text("data", encoding="utf-8")
    result = fs.create_directory(str(target))
    assert result.is_failure
    assert result.unwrap_error().code == _DESTINATION_EXISTS
    assert target.read_text(encoding="utf-8") == "data"


# --------------------------------------------------------------------------
# move_path
# --------------------------------------------------------------------------


def test_move_file(tmp_path: Any) -> None:
    source = tmp_path / "from.txt"
    destination = tmp_path / "to.txt"
    source.write_text("content", encoding="utf-8")
    result = fs.move_path(str(source), str(destination))
    assert result.is_success
    assert not source.exists()
    assert destination.exists()
    assert destination.read_text(encoding="utf-8") == "content"


def test_move_destination_exists_fails_closed(tmp_path: Any) -> None:
    source = tmp_path / "from.txt"
    destination = tmp_path / "to.txt"
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    result = fs.move_path(str(source), str(destination))
    assert result.is_failure
    assert result.unwrap_error().code == _DESTINATION_EXISTS
    # Nothing moved and the existing destination is untouched.
    assert source.exists()
    assert destination.read_text(encoding="utf-8") == "old"


def test_move_overwrite_file_over_file(tmp_path: Any) -> None:
    source = tmp_path / "from.txt"
    destination = tmp_path / "to.txt"
    source.write_text("replacement", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    result = fs.move_path(str(source), str(destination), overwrite=True)
    assert result.is_success
    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "replacement"


def test_move_does_not_overwrite_directory_or_symlink(tmp_path: Any) -> None:
    source = tmp_path / "from.txt"
    source.write_text("x", encoding="utf-8")
    dest_dir = tmp_path / "target_dir"
    dest_dir.mkdir()
    into_dir = fs.move_path(str(source), str(dest_dir), overwrite=True)
    assert into_dir.is_failure
    assert into_dir.unwrap_error().code == _DESTINATION_EXISTS
    # Directory source cannot overwrite a file destination either.
    source_dir = tmp_path / "sdir"
    source_dir.mkdir()
    dest_file = tmp_path / "dfile"
    dest_file.write_text("y", encoding="utf-8")
    dir_over_file = fs.move_path(str(source_dir), str(dest_file), overwrite=True)
    assert dir_over_file.is_failure
    assert dir_over_file.unwrap_error().code == _DESTINATION_EXISTS


def test_move_missing_source_is_not_found(tmp_path: Any) -> None:
    result = fs.move_path(str(tmp_path / "absent"), str(tmp_path / "dest"))
    assert result.is_failure
    assert result.unwrap_error().code == _NOT_FOUND


def test_move_source_equals_destination_is_rejected(tmp_path: Any) -> None:
    source = tmp_path / "same.txt"
    source.write_text("x", encoding="utf-8")
    result = fs.move_path(str(source), str(source))
    assert result.is_failure
    assert result.unwrap_error().code == f"{fs._PREFIX}.source_destination_alias"


# --------------------------------------------------------------------------
# Path validation: relative / NUL / empty / wildcards / Unicode
# --------------------------------------------------------------------------


def test_relative_path_rejected_everywhere(tmp_path: Any) -> None:
    assert fs.stat_path("relative/path").unwrap_error().code == _INVALID_PATH
    assert fs.list_directory("relative/path").unwrap_error().code == _INVALID_PATH
    assert fs.create_directory("relative/path").unwrap_error().code == _INVALID_PATH
    assert fs.move_path("relative/src", "relative/dst").unwrap_error().code == _INVALID_PATH
    # A relative *destination* alone is also rejected.
    assert (
        fs.move_path(str(tmp_path / "anything"), "relative/dst").unwrap_error().code
        == _INVALID_PATH
    )


def test_embedded_nul_rejected() -> None:
    path = "/tmp/path\x00with_nul"
    assert fs.stat_path(path).unwrap_error().code == _INVALID_PATH
    assert fs.create_directory(path).unwrap_error().code == _INVALID_PATH
    assert fs.move_path(path, "/tmp/x").unwrap_error().code == _INVALID_PATH


def test_empty_path_rejected() -> None:
    assert fs.stat_path("").unwrap_error().code == _INVALID_PATH
    assert fs.create_directory("").unwrap_error().code == _INVALID_PATH


def test_wildcard_is_not_interpreted(tmp_path: Any) -> None:
    """A literal '*' character in a name is data, never a glob."""
    literal = tmp_path / "wild*.txt"
    literal.write_text("literal name", encoding="utf-8")
    info = fs.stat_path(str(literal)).unwrap()
    assert info.exists is True and info.kind is fs.PathKind.FILE
    # Moving by the exact literal name moves the real file.
    destination = tmp_path / "renamed.txt"
    result = fs.move_path(str(literal), str(destination))
    assert result.is_success
    assert not literal.exists() and destination.exists()


def test_unicode_path_operations(tmp_path: Any) -> None:
    target = tmp_path / "café-日本語-😀"
    result = fs.create_directory(str(target))
    assert result.is_success
    assert target.is_dir()
    listing = fs.list_directory(str(tmp_path)).unwrap()
    assert [entry.name for entry in listing.entries] == [target.name]
    subfile = target / "ünïcode.txt"
    subfile.write_text("data", encoding="utf-8")
    moved = target / "moved-ünïcode.txt"
    move_result = fs.move_path(str(subfile), str(moved))
    assert move_result.is_success
    assert moved.exists() and not subfile.exists()


def test_unbounded_path_string_is_rejected(tmp_path: Any) -> None:
    long_path = str(tmp_path / ("x" * fs.MAX_PATH_CHARS))
    assert fs.stat_path(long_path).unwrap_error().code == _INVALID_PATH


# --------------------------------------------------------------------------
# Symlink handling (skipped where unsupported, e.g. restricted Windows CI)
# --------------------------------------------------------------------------


def test_stat_reports_symlink_without_following(tmp_path: Any) -> None:
    real = tmp_path / "real.txt"
    real.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not supported on this host")
    info = fs.stat_path(str(link)).unwrap()
    # The link itself is reported, never its target content/type.
    assert info.kind is fs.PathKind.SYMLINK
    assert info.is_symlink is True
    assert info.size_bytes is None


def test_listing_reports_symlink_kind(tmp_path: Any) -> None:
    real = tmp_path / "real.txt"
    real.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not supported on this host")
    listing = fs.list_directory(str(tmp_path)).unwrap()
    by_name = {entry.name: entry for entry in listing.entries}
    assert by_name["link.txt"].kind is fs.PathKind.SYMLINK
    assert by_name["real.txt"].kind is fs.PathKind.FILE


def test_create_directory_over_symlink_is_conflict(tmp_path: Any) -> None:
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    link = tmp_path / "dirlink"
    try:
        link.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not supported on this host")
    result = fs.create_directory(str(link))
    assert result.is_failure
    assert result.unwrap_error().code == _DESTINATION_EXISTS
    # The real directory was not disturbed.
    assert real_dir.is_dir()


def test_move_of_a_symlink_moves_the_link_not_its_target(tmp_path: Any) -> None:
    real = tmp_path / "real.txt"
    real.write_text("content", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not supported on this host")
    destination = tmp_path / "moved_link.txt"
    result = fs.move_path(str(link), str(destination))
    assert result.is_success
    assert not link.exists() and destination.is_symlink()
    # The symlink target file remains intact.
    assert real.read_text(encoding="utf-8") == "content"


# --------------------------------------------------------------------------
# Permission / risk descriptors
# --------------------------------------------------------------------------


def test_read_capabilities_declare_read_permission_and_r0_risk() -> None:
    for capability in (fs.StatPathCapability(), fs.ListDirectoryCapability()):
        descriptor = capability.descriptor
        assert descriptor.required_permissions == frozenset({Permission.READ})
        assert Permission.WRITE not in descriptor.required_permissions
        assert descriptor.risk_assessment.effective_level is RiskLevel.R0


def test_mutation_capabilities_declare_write_permission_and_do_not_lower_risk() -> None:
    for capability in (
        fs.CreateDirectoryCapability(),
        fs.MovePathCapability(),
    ):
        descriptor = capability.descriptor
        assert descriptor.required_permissions == frozenset({Permission.WRITE})
        # Mutations are state-changing with external effect: never R0/R1.
        assert descriptor.risk_assessment.effective_level not in (RiskLevel.R0, RiskLevel.R1)


def test_descriptors_declare_required_preconditions() -> None:
    for capability in (
        fs.StatPathCapability(),
        fs.ListDirectoryCapability(),
        fs.CreateDirectoryCapability(),
        fs.MovePathCapability(),
    ):
        names = [pre.name for pre in capability.descriptor.preconditions]
        assert "explicit_absolute_path" in names


# --------------------------------------------------------------------------
# Capability execute / verify behaviour
# --------------------------------------------------------------------------


def test_stat_capability_execute_and_verify(tmp_path: Any) -> None:
    target = tmp_path / "cap.txt"
    target.write_text("abc", encoding="utf-8")
    capability = fs.StatPathCapability()
    execution = capability.execute(fs.stat_path_request(str(target)), _context())
    assert execution.succeeded is True
    verification = capability.verify(
        fs.stat_path_request(str(target)), execution.observation, _context()
    )
    assert verification.passed is True


def test_create_directory_capability_verify_requires_independent_evidence(
    tmp_path: Any,
) -> None:
    target = tmp_path / "cap_dir"
    capability = fs.CreateDirectoryCapability()
    execution = capability.execute(fs.create_directory_request(str(target)), _context())
    assert execution.succeeded is True
    # Verify passes when the directory truly exists.
    assert (
        capability.verify(
            fs.create_directory_request(str(target)), execution.observation, _context()
        ).passed
        is True
    )
    # Remove the directory after execution: independent verification now fails,
    # proving verification is not satisfied by the execution result alone.
    target.rmdir()
    failed = capability.verify(
        fs.create_directory_request(str(target)), execution.observation, _context()
    )
    assert failed.passed is False


def test_list_directory_capability_verify_validates_bound_and_order(tmp_path: Any) -> None:
    for index in range(12):
        (tmp_path / f"n_{index:02d}").write_text("x", encoding="utf-8")
    capability = fs.ListDirectoryCapability()
    request = fs.list_directory_request(str(tmp_path), max_entries=5)
    execution = capability.execute(request, _context())
    assert execution.succeeded is True
    verification = capability.verify(request, execution.observation, _context())
    assert verification.passed is True


def test_move_capability_verify_confirms_source_absent_and_destination_present(
    tmp_path: Any,
) -> None:
    source = tmp_path / "cap_from.txt"
    destination = tmp_path / "cap_to.txt"
    source.write_text("body", encoding="utf-8")
    capability = fs.MovePathCapability()
    request = fs.move_path_request(str(source), str(destination))
    execution = capability.execute(request, _context())
    assert execution.succeeded is True
    verification = capability.verify(request, execution.observation, _context())
    assert verification.passed is True


def test_move_capability_denies_overwrite_without_flag_through_execute(
    tmp_path: Any,
) -> None:
    source = tmp_path / "from.txt"
    destination = tmp_path / "to.txt"
    source.write_text("a", encoding="utf-8")
    destination.write_text("b", encoding="utf-8")
    capability = fs.MovePathCapability()
    execution = capability.execute(fs.move_path_request(str(source), str(destination)), _context())
    assert execution.succeeded is False
    # Fail-closed: neither path changed.
    assert source.exists() and destination.read_text(encoding="utf-8") == "b"
