"""Typed, dependency-free configuration loading for AgentX foundations.

Configuration precedence is deterministic and intentionally small:

    built-in defaults < TOML file < AGENTX_ environment variables < overrides

This module performs no filesystem writes and is not a secret store.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from typing import Final, cast

__all__ = [
    "AgentXConfig",
    "ConfigError",
    "ConfigFileError",
    "ConfigFileNotFoundError",
    "ConfigParseError",
    "ConfigValidationError",
    "LogLevel",
    "default_config_path",
    "default_data_dir",
    "load_config",
]

_DEFAULT_PROFILE: Final = "default"
_DEFAULT_LOG_LEVEL: Final = "INFO"
_DEFAULT_DEBUG: Final = False
_ENV_PREFIX: Final = "AGENTX_"
_ALLOWED_FIELDS: Final = frozenset({"profile", "data_dir", "log_level", "debug"})
_ENV_TO_FIELD: Final = {
    "AGENTX_PROFILE": "profile",
    "AGENTX_DATA_DIR": "data_dir",
    "AGENTX_LOG_LEVEL": "log_level",
    "AGENTX_DEBUG": "debug",
}


class ConfigError(ValueError):
    """Base class for AgentX configuration failures."""


class ConfigFileError(ConfigError):
    """Raised when a configuration file cannot be used."""


class ConfigFileNotFoundError(ConfigFileError):
    """Raised when an explicitly requested configuration file does not exist."""


class ConfigParseError(ConfigFileError):
    """Raised when TOML syntax is malformed."""


class ConfigValidationError(ConfigError):
    """Raised when a supported configuration source contains an invalid value."""


class LogLevel(StrEnum):
    """Supported foundation log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class AgentXConfig:
    """Canonical typed AgentX foundation configuration."""

    profile: str
    data_dir: Path
    log_level: LogLevel
    debug: bool


def default_config_path(environ: Mapping[str, str] | None = None) -> Path:
    """Return the canonical platform configuration path without creating it."""
    env = os.environ if environ is None else environ
    if os.name == "nt":
        base = _environment_base(env, "LOCALAPPDATA", Path.home() / "AppData" / "Local")
        return base / "AgentX" / "config.toml"

    base = _environment_base(env, "XDG_CONFIG_HOME", Path.home() / ".config")
    return base / "agentx" / "config.toml"


def default_data_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Return the canonical platform data directory without creating it."""
    env = os.environ if environ is None else environ
    if os.name == "nt":
        base = _environment_base(env, "LOCALAPPDATA", Path.home() / "AppData" / "Local")
        return base / "AgentX" / "data"

    base = _environment_base(env, "XDG_DATA_HOME", Path.home() / ".local" / "share")
    return base / "agentx"


def load_config(
    config_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, object] | None = None,
) -> AgentXConfig:
    """Load AgentX configuration using deterministic source precedence.

    The default config file is optional. Passing ``config_path`` makes that file
    explicit and therefore required. Unknown keys are rejected in TOML,
    ``AGENTX_`` environment variables, and runtime overrides.
    """
    env = os.environ if environ is None else environ
    selected_path = (
        default_config_path(env) if config_path is None else _explicit_config_path(config_path)
    )

    values: dict[str, object] = {
        "profile": _DEFAULT_PROFILE,
        "data_dir": default_data_dir(env),
        "log_level": LogLevel(_DEFAULT_LOG_LEVEL),
        "debug": _DEFAULT_DEBUG,
    }

    if selected_path.is_file():
        _apply_layer(
            values,
            _read_toml(selected_path),
            source=f"configuration file {selected_path}",
            data_dir_base=selected_path.parent,
        )
    elif config_path is not None:
        if selected_path.exists():
            raise ConfigFileError(f"Configuration path is not a file: {selected_path}")
        raise ConfigFileNotFoundError(
            f"Explicit configuration file does not exist: {selected_path}"
        )
    elif selected_path.exists():
        raise ConfigFileError(f"Default configuration path is not a file: {selected_path}")

    _apply_layer(values, _environment_layer(env), source="environment variables")
    if overrides is not None:
        _apply_layer(values, overrides, source="runtime overrides")

    return AgentXConfig(
        profile=cast(str, values["profile"]),
        data_dir=cast(Path, values["data_dir"]),
        log_level=cast(LogLevel, values["log_level"]),
        debug=cast(bool, values["debug"]),
    )


def _environment_base(environ: Mapping[str, str], name: str, fallback: Path) -> Path:
    raw = environ.get(name)
    if not raw:
        return fallback
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_absolute() else fallback


def _explicit_config_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if _is_absolute_path(path, str(value)):
        return path
    return Path.cwd() / path


def _read_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as stream:
            return cast(dict[str, object], tomllib.load(stream))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigParseError(f"Malformed TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigFileError(f"Unable to read configuration file {path}: {exc}") from exc


def _environment_layer(environ: Mapping[str, str]) -> dict[str, object]:
    layer: dict[str, object] = {}
    unknown: list[str] = []

    for name, raw in environ.items():
        if not name.startswith(_ENV_PREFIX):
            continue
        field_name = _ENV_TO_FIELD.get(name)
        if field_name is None:
            unknown.append(name)
            continue
        layer[field_name] = _parse_environment_value(field_name, raw, name)

    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigValidationError(f"Unknown AgentX environment variable(s): {names}")
    return layer


def _parse_environment_value(field_name: str, raw: str, variable_name: str) -> object:
    if field_name == "debug":
        normalized = raw.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        raise ConfigValidationError(f"{variable_name} must be 'true' or 'false'; got {raw!r}")
    return raw


def _apply_layer(
    values: dict[str, object],
    layer: Mapping[str, object],
    *,
    source: str,
    data_dir_base: Path | None = None,
) -> None:
    unknown = sorted(set(layer) - _ALLOWED_FIELDS)
    if unknown:
        names = ", ".join(unknown)
        raise ConfigValidationError(f"Unknown configuration key(s) in {source}: {names}")

    for key, raw in layer.items():
        values[key] = _coerce_value(key, raw, source=source, data_dir_base=data_dir_base)


def _coerce_value(
    key: str,
    raw: object,
    *,
    source: str,
    data_dir_base: Path | None,
) -> object:
    if key == "profile":
        return _coerce_profile(raw, source)
    if key == "data_dir":
        return _coerce_data_dir(raw, source=source, base=data_dir_base)
    if key == "log_level":
        return _coerce_log_level(raw, source)
    if key == "debug":
        if isinstance(raw, bool):
            return raw
        raise ConfigValidationError(f"debug from {source} must be a boolean; got {raw!r}")
    raise AssertionError(f"Unhandled configuration key: {key}")


def _coerce_profile(raw: object, source: str) -> str:
    if not isinstance(raw, str):
        raise ConfigValidationError(f"profile from {source} must be a string; got {raw!r}")
    profile = raw.strip()
    if not profile or any(character in profile for character in ("\x00", "\r", "\n")):
        raise ConfigValidationError(f"profile from {source} must be a non-empty single-line string")
    return profile


def _coerce_log_level(raw: object, source: str) -> LogLevel:
    if isinstance(raw, LogLevel):
        return raw
    if not isinstance(raw, str):
        raise ConfigValidationError(f"log_level from {source} must be a string; got {raw!r}")
    try:
        return LogLevel(raw.strip().upper())
    except ValueError as exc:
        allowed = ", ".join(level.value for level in LogLevel)
        raise ConfigValidationError(
            f"log_level from {source} must be one of {allowed}; got {raw!r}"
        ) from exc


def _coerce_data_dir(raw: object, *, source: str, base: Path | None) -> Path:
    if not isinstance(raw, (str, Path)):
        raise ConfigValidationError(f"data_dir from {source} must be a path string; got {raw!r}")
    if isinstance(raw, str) and not raw.strip():
        raise ConfigValidationError(f"data_dir from {source} must not be empty")

    path = Path(raw).expanduser()
    if _is_absolute_path(path, str(raw)):
        return path
    if base is not None:
        return base / path
    raise ConfigValidationError(
        f"data_dir from {source} must be absolute; relative paths are only allowed in TOML files"
    )


def _is_absolute_path(path: Path, raw: str) -> bool:
    """Recognize native and Windows absolute paths without rewriting separators."""
    return path.is_absolute() or PureWindowsPath(raw).is_absolute()
