"""Restart-safe, versioned migration for AgentX TOML configuration files.

The foundation config loader remains read-only. This module owns the explicit
write path used by release/upgrade tooling. Migration is deliberately narrow:
legacy unversioned/schema-0 files become schema 1 without rewriting settings,
and a same-directory temporary file plus atomic replacement prevents a partial
write from becoming the canonical configuration.
"""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from agentx.infrastructure.config import (
    CURRENT_CONFIG_SCHEMA_VERSION,
    ConfigError,
    load_config,
)

__all__ = [
    "ConfigMigrationError",
    "ConfigMigrationResult",
    "migrate_config_file",
]

_SCHEMA_LINE: Final = re.compile(
    r"(?m)^(\s*schema_version\s*=\s*)0(\s*(?:#.*)?)$"
)


class ConfigMigrationError(ConfigError):
    """Raised when a configuration migration cannot complete safely."""


@dataclass(frozen=True, slots=True)
class ConfigMigrationResult:
    """Outcome of one explicit configuration migration."""

    path: Path
    from_version: int
    to_version: int
    changed: bool


def migrate_config_file(path: str | Path) -> ConfigMigrationResult:
    """Migrate one existing AgentX TOML file to the current schema atomically.

    The migration is deterministic and idempotent. Unknown settings, malformed
    TOML, unsafe/newer versions, and invalid values fail before replacement.
    A crash before :func:`os.replace` leaves the original file untouched.
    """
    source = Path(path).expanduser()
    if not source.is_absolute():
        source = Path.cwd() / source
    if not source.is_file():
        raise ConfigMigrationError(f"configuration file does not exist: {source}")

    try:
        raw_bytes = source.read_bytes()
        document = cast(dict[str, object], tomllib.loads(raw_bytes.decode("utf-8")))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigMigrationError(f"unable to read configuration file {source}: {exc}") from exc

    raw_version = document.get("schema_version", 0)
    if type(raw_version) is not int:
        raise ConfigMigrationError("schema_version must be an integer")
    version = cast(int, raw_version)
    if version < 0:
        raise ConfigMigrationError("schema_version must not be negative")
    if version > CURRENT_CONFIG_SCHEMA_VERSION:
        raise ConfigMigrationError(
            f"schema_version {version} is newer than this build supports "
            f"({CURRENT_CONFIG_SCHEMA_VERSION})"
        )
    if version not in (0, CURRENT_CONFIG_SCHEMA_VERSION):
        raise ConfigMigrationError(f"unsupported schema_version {version}")

    # Validate the source before changing bytes. Legacy version 0 is readable
    # by the canonical loader, but invalid/unknown settings are still rejected.
    try:
        load_config(source, environ={})
    except ConfigError as exc:
        raise ConfigMigrationError(f"configuration validation failed: {exc}") from exc

    if version == CURRENT_CONFIG_SCHEMA_VERSION:
        return ConfigMigrationResult(
            path=source,
            from_version=version,
            to_version=version,
            changed=False,
        )

    text = raw_bytes.decode("utf-8")
    if "schema_version" in document:
        migrated, replacements = _SCHEMA_LINE.subn(r"\g<1>1\2", text, count=1)
        if replacements != 1:
            raise ConfigMigrationError("schema_version 0 is not a supported top-level assignment")
    else:
        migrated = f"schema_version = {CURRENT_CONFIG_SCHEMA_VERSION}\n{text}"

    original_mode = source.stat().st_mode & 0o777
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{source.name}.",
            suffix=".migrating",
            dir=source.parent,
            delete=False,
        ) as stream:
            stream.write(migrated)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)

        if original_mode:
            temporary_path.chmod(original_mode)

        # Validate exactly the bytes that will become canonical.
        load_config(temporary_path, environ={})
        temporary_path.replace(source)
        temporary_path = None
    except (OSError, ConfigError) as exc:
        raise ConfigMigrationError(f"configuration migration failed safely: {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return ConfigMigrationResult(
        path=source,
        from_version=version,
        to_version=CURRENT_CONFIG_SCHEMA_VERSION,
        changed=True,
    )
