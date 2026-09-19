"""M16 configuration migration acceptance."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentx.infrastructure.config import (
    CURRENT_CONFIG_SCHEMA_VERSION,
    ConfigValidationError,
    load_config,
)
from agentx.infrastructure.config_migration import ConfigMigrationError, migrate_config_file


def test_legacy_config_migrates_atomically_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'profile = "legacy"\n'
        'data_dir = "state"\n'
        'log_level = "warning"\n'
        "debug = false\n",
        encoding="utf-8",
    )

    before = load_config(path, environ={})
    first = migrate_config_file(path)
    after = load_config(path, environ={})
    second = migrate_config_file(path)

    assert first.changed is True
    assert first.from_version == 0
    assert first.to_version == CURRENT_CONFIG_SCHEMA_VERSION
    assert second.changed is False
    assert after == before
    assert path.read_text(encoding="utf-8").startswith("schema_version = 1\n")
    assert not list(tmp_path.glob("*.migrating"))


def test_explicit_schema_zero_migrates_without_rewriting_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "schema_version = 0 # legacy\n"
        'profile = "legacy"\n'
        f'data_dir = "{tmp_path.as_posix()}"\n',
        encoding="utf-8",
    )

    result = migrate_config_file(path)

    assert result.changed is True
    assert "schema_version = 1 # legacy" in path.read_text(encoding="utf-8")
    assert load_config(path, environ={}).profile == "legacy"


def test_current_config_reopens_without_change(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    content = (
        "schema_version = 1\n"
        'profile = "current"\n'
        f'data_dir = "{tmp_path.as_posix()}"\n'
    )
    path.write_text(content, encoding="utf-8")

    result = migrate_config_file(path)

    assert result.changed is False
    assert path.read_text(encoding="utf-8") == content


def test_unknown_or_newer_config_is_never_silently_migrated(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.toml"
    unknown.write_text('profiel = "typo"\n', encoding="utf-8")
    with pytest.raises(ConfigMigrationError, match="validation failed"):
        migrate_config_file(unknown)

    newer = tmp_path / "newer.toml"
    newer.write_text("schema_version = 999\n", encoding="utf-8")
    with pytest.raises(ConfigMigrationError, match="newer"):
        migrate_config_file(newer)
    with pytest.raises(ConfigValidationError, match="newer"):
        load_config(newer, environ={})


def test_interrupted_atomic_replace_preserves_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.toml"
    original = 'profile = "safe"\n'
    path.write_text(original, encoding="utf-8")

    def fail_replace(_self: Path, _target: Path) -> Path:
        raise OSError("simulated interruption")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(ConfigMigrationError, match="failed safely"):
        migrate_config_file(path)

    assert path.read_text(encoding="utf-8") == original
    assert load_config(path, environ={}).profile == "safe"
    assert not list(tmp_path.glob("*.migrating"))
