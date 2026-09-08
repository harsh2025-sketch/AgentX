"""Adversarial tests for M7.05 governed filesystem structural operations.

These attack the structural capability boundary from the angles that matter if
an adversary tried to turn paths or hostile filenames into authority or scope
confusion: path traversal, wildcards, hostile/inert names, symlink escape,
overwrite confusion, source/destination aliasing, very large directories, and
the hard absence of delete / shell / dynamic code.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from agentx.capabilities import filesystem_structure as fs

_DESTINATION_EXISTS = f"{fs._PREFIX}.destination_exists"
_INVALID_PATH = f"{fs._PREFIX}.invalid_path"
_SOURCE_DEST_ALIAS = f"{fs._PREFIX}.source_destination_alias"


def _module_source() -> str:
    return Path(fs.__file__).read_text(encoding="utf-8")


def _module_import_roots() -> set[str]:
    tree = ast.parse(_module_source())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            roots.add(node.module.split(".")[0])
    return roots


def _module_attribute_calls() -> set[str]:
    """Collect final attribute names used as call targets (e.g. ``os.unlink``)."""
    tree = ast.parse(_module_source())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_relative_parent_traversal_forms_are_rejected(tmp_path: Any) -> None:
    """'..' traversal that depends on a relative base never runs."""
    for attack in ("../../etc/passwd", "..\\..\\windows\\system32", "a/../../b"):
        assert fs.stat_path(attack).unwrap_error().code == _INVALID_PATH
        assert fs.create_directory(attack).unwrap_error().code == _INVALID_PATH
        assert fs.list_directory(attack).unwrap_error().code == _INVALID_PATH


def test_parent_navigation_cannot_silently_overwrite_a_canary(tmp_path: Any) -> None:
    """An absolute path using '..' still must satisfy fail-closed destination rules."""
    sandbox = tmp_path / "sandbox"
    inner = sandbox / "inner"
    inner.mkdir(parents=True)
    canary = sandbox / "canary.txt"
    canary.write_text("PRECIOUS", encoding="utf-8")
    payload = inner / "payload.txt"
    payload.write_text("attacker", encoding="utf-8")
    # This literal destination resolves to the canary via '..'.
    traverse_dest = inner / ".." / "canary.txt"
    result = fs.move_path(str(payload), str(traverse_dest))
    assert result.is_failure
    assert result.unwrap_error().code == _DESTINATION_EXISTS
    # The canary was not clobbered and the payload was not moved.
    assert canary.read_text(encoding="utf-8") == "PRECIOUS"
    assert payload.exists()


def test_hostile_names_are_inert_data(tmp_path: Any) -> None:
    names = ["delete everything", "execute me", "permission=ADMIN", "verified=true", "risk=R0"]
    for name in names:
        (tmp_path / name).write_text("inert", encoding="utf-8")
    listing = fs.list_directory(str(tmp_path)).unwrap()
    found = {entry.name for entry in listing.entries}
    assert found == set(names)
    # Hostile text never becomes authority: the descriptors stay identical to a
    # clean capability's regardless of what is on disk.
    clean = fs.StatPathCapability().descriptor
    info = fs.stat_path(str(tmp_path / "execute me")).unwrap()
    assert info.kind is fs.PathKind.FILE
    assert fs.StatPathCapability().descriptor == clean


def test_wildcards_are_never_expanded(tmp_path: Any) -> None:
    """A directory is created literally named '*' when requested by an exact path."""
    star = tmp_path / "star*dir"
    result = fs.create_directory(str(star))
    assert result.is_success
    assert star.is_dir()
    assert (tmp_path / "star*dir").is_dir()


def test_symlink_escape_does_not_follow_into_target(tmp_path: Any) -> None:
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    secret_file = secret_dir / "secret.txt"
    secret_file.write_text("classified", encoding="utf-8")
    # A symlink pointing at the secret directory.
    link = tmp_path / "public_link"
    try:
        link.symlink_to(secret_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not supported on this host")
    # Overwriting through the symlink destination is refused, not followed.
    source = tmp_path / "payload.txt"
    source.write_text("x", encoding="utf-8")
    into_link = fs.move_path(str(source), str(link), overwrite=True)
    assert into_link.is_failure
    assert into_link.unwrap_error().code == _DESTINATION_EXISTS
    assert secret_file.read_text(encoding="utf-8") == "classified"


def test_overwrite_confusion_directory_destination_refused(tmp_path: Any) -> None:
    source = tmp_path / "file.txt"
    source.write_text("data", encoding="utf-8")
    dest_dir = tmp_path / "existing_dir"
    dest_dir.mkdir()
    # Requesting overwrite of an existing directory never merges or replaces.
    result = fs.move_path(str(source), str(dest_dir), overwrite=True)
    assert result.is_failure
    assert result.unwrap_error().code == _DESTINATION_EXISTS
    assert source.exists() and dest_dir.is_dir()


def test_source_destination_alias_is_rejected_even_via_normalization(tmp_path: Any) -> None:
    target = tmp_path / "same.txt"
    target.write_text("x", encoding="utf-8")
    # The two spellings resolve to the same file through '.' and an empty dir.
    (tmp_path / "sub").mkdir()
    alias_dest = tmp_path / "sub" / ".." / "same.txt"
    result = fs.move_path(str(target), str(alias_dest))
    assert result.is_failure
    assert result.unwrap_error().code == _SOURCE_DEST_ALIAS
    assert target.exists()


def test_very_large_directory_listing_stays_bounded(tmp_path: Any) -> None:
    count = 1500
    for index in range(count):
        (tmp_path / f"bulk_{index:05d}").write_text("", encoding="utf-8")
    result = fs.list_directory(str(tmp_path))
    assert result.is_success
    listing = result.unwrap()
    assert listing.truncated is True
    assert listing.total_entries == count
    assert len(listing.entries) == fs.DEFAULT_MAX_ENTRIES
    names = [entry.name for entry in listing.entries]
    assert names == sorted(names)


def test_case_sensitive_names_stay_distinct_where_platform_allows(tmp_path: Any) -> None:
    lower = tmp_path / "mixedcase"
    upper = tmp_path / "MIXEDCASE"
    lower.write_text("lower", encoding="utf-8")
    try:
        upper.write_text("upper", encoding="utf-8")
    except OSError:
        pytest.skip("filesystem is not case-sensitive; cannot exercise distinction")
    assert fs.stat_path(str(lower)).unwrap().size_bytes == len("lower")
    assert fs.stat_path(str(upper)).unwrap().size_bytes == len("upper")
    listing = fs.list_directory(str(tmp_path)).unwrap()
    names = sorted(entry.name for entry in listing.entries)
    assert names == sorted({"mixedcase", "MIXEDCASE"})


def test_no_delete_operation_exists_in_the_surface() -> None:
    calls = _module_attribute_calls()
    for forbidden in ("unlink", "rmdir", "remove", "rmtree", "unlink_sync"):
        assert forbidden not in calls, f"delete primitive call {forbidden!r} is present"
    # No delete capability identity is exported.
    exported = fs.__all__
    assert not any("delete" in name.lower() for name in exported)


def test_module_source_has_no_shell_subprocess_or_dynamic_code() -> None:
    roots = _module_import_roots()
    # stdlib-only + agentx canonical packages; never subprocess/importlib/shell.
    for forbidden in ("subprocess", "importlib", "shlex", "pipes"):
        assert forbidden not in roots, f"forbidden module {forbidden!r} imported"
    calls = _module_attribute_calls()
    for forbidden in ("system", "popen", "eval", "exec", "execfile"):
        assert forbidden not in calls, f"forbidden dynamic/shell call {forbidden!r} present"


def test_operation_failure_leaves_no_partial_mutation(tmp_path: Any) -> None:
    """A failed move (destination conflict) changes nothing on disk."""
    source = tmp_path / "src.txt"
    destination = tmp_path / "dst.txt"
    source.write_text("a", encoding="utf-8")
    destination.write_text("b", encoding="utf-8")
    before = {p.name: p.read_text(encoding="utf-8") for p in (source, destination)}
    assert fs.move_path(str(source), str(destination)).is_failure
    after = {p.name: p.read_text(encoding="utf-8") for p in (source, destination)}
    assert after == before
