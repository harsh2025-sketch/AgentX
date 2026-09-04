"""Tests for the canonical AgentX foundation configuration."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from agentx.infrastructure.config import (
    AgentXConfig,
    ConfigFileNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    LogLevel,
    default_config_path,
    default_data_dir,
    load_config,
)


def _isolated_environment(tmp_path: Path) -> dict[str, str]:
    """Return deterministic platform-location inputs with no AgentX settings."""
    return {
        "LOCALAPPDATA": str(tmp_path / "Local App Data"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg config"),
        "XDG_DATA_HOME": str(tmp_path / "xdg data"),
    }


def test_defaults_are_typed_and_do_not_create_directories(tmp_path: Path) -> None:
    environ = _isolated_environment(tmp_path)
    expected_config_path = default_config_path(environ)

    config = load_config(environ=environ)

    assert config.profile == "default"
    assert config.data_dir == default_data_dir(environ)
    assert config.log_level is LogLevel.INFO
    assert config.debug is False
    assert not expected_config_path.parent.exists()
    assert not config.data_dir.exists()


def test_explicit_toml_file(tmp_path: Path) -> None:
    config_path = tmp_path / "project settings.toml"
    config_path.write_text(
        'profile = "research"\n'
        'data_dir = "state with spaces"\n'
        'log_level = "warning"\n'
        "debug = true\n",
        encoding="utf-8",
    )

    config = load_config(config_path, environ={})

    assert config == AgentXConfig(
        profile="research",
        data_dir=tmp_path / "state with spaces",
        log_level=LogLevel.WARNING,
        debug=True,
    )


def test_environment_overrides_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'profile = "file"\n'
        f'data_dir = "{(tmp_path / "file-data").as_posix()}"\n'
        'log_level = "INFO"\n'
        "debug = false\n",
        encoding="utf-8",
    )
    environment_data_dir = tmp_path / "environment data"
    environ = {
        "AGENTX_PROFILE": "environment",
        "AGENTX_DATA_DIR": str(environment_data_dir),
        "AGENTX_LOG_LEVEL": "error",
        "AGENTX_DEBUG": "TRUE",
    }

    config = load_config(config_path, environ=environ)

    assert config.profile == "environment"
    assert config.data_dir == environment_data_dir
    assert config.log_level is LogLevel.ERROR
    assert config.debug is True


def test_explicit_runtime_overrides_environment(tmp_path: Path) -> None:
    environment_data_dir = tmp_path / "environment"
    runtime_data_dir = tmp_path / "runtime"
    config = load_config(
        environ={
            "AGENTX_PROFILE": "environment",
            "AGENTX_DATA_DIR": str(environment_data_dir),
            "AGENTX_LOG_LEVEL": "WARNING",
            "AGENTX_DEBUG": "false",
        },
        overrides={
            "profile": "runtime",
            "data_dir": runtime_data_dir,
            "log_level": LogLevel.CRITICAL,
            "debug": True,
        },
    )

    assert config == AgentXConfig(
        profile="runtime",
        data_dir=runtime_data_dir,
        log_level=LogLevel.CRITICAL,
        debug=True,
    )


def test_precedence_across_all_layers(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'profile = "file"\n'
        f'data_dir = "{(tmp_path / "file").as_posix()}"\n'
        'log_level = "DEBUG"\n'
        "debug = true\n",
        encoding="utf-8",
    )

    config = load_config(
        config_path,
        environ={
            "AGENTX_PROFILE": "environment",
            "AGENTX_DATA_DIR": str(tmp_path / "environment"),
            "AGENTX_LOG_LEVEL": "ERROR",
            "AGENTX_DEBUG": "false",
        },
        overrides={
            "profile": "runtime",
            "data_dir": tmp_path / "runtime",
            "log_level": "CRITICAL",
            "debug": True,
        },
    )

    assert config.profile == "runtime"
    assert config.data_dir == tmp_path / "runtime"
    assert config.log_level is LogLevel.CRITICAL
    assert config.debug is True


def test_malformed_toml_fails_clearly(tmp_path: Path) -> None:
    config_path = tmp_path / "broken.toml"
    config_path.write_text('profile = ["unfinished"\n', encoding="utf-8")

    with pytest.raises(ConfigParseError, match="Malformed TOML"):
        load_config(config_path, environ={})


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"AGENTX_DEBUG": "yes"}, "AGENTX_DEBUG must be 'true' or 'false'"),
        ({"AGENTX_LOG_LEVEL": "VERBOSE"}, "log_level"),
        ({"AGENTX_DATA_DIR": "relative/data"}, "must be absolute"),
        ({"AGENTX_PROFILE": "   "}, "profile"),
    ],
)
def test_invalid_environment_values_fail(environment: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigValidationError, match=message):
        load_config(environ=environment)


def test_unknown_toml_key_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('profiel = "typo"\n', encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="profiel"):
        load_config(config_path, environ={})


def test_unknown_agentx_environment_variable_is_rejected() -> None:
    with pytest.raises(ConfigValidationError, match="AGENTX_LOGLEVEL"):
        load_config(environ={"AGENTX_LOGLEVEL": "INFO"})


def test_unknown_runtime_override_is_rejected() -> None:
    with pytest.raises(ConfigValidationError, match="model_provider"):
        load_config(environ={}, overrides={"model_provider": "not-owned-here"})


def test_windows_style_absolute_path_is_preserved() -> None:
    windows_path = r"C:\Users\Agent X\AppData\Local\AgentX\data"

    config = load_config(environ={"AGENTX_DATA_DIR": windows_path})

    assert str(config.data_dir) == windows_path


def test_path_with_spaces_is_supported(tmp_path: Path) -> None:
    path = tmp_path / "AgentX Data With Spaces"

    config = load_config(environ={"AGENTX_DATA_DIR": str(path)})

    assert config.data_dir == path


def test_missing_optional_default_file_uses_defaults(tmp_path: Path) -> None:
    environ = _isolated_environment(tmp_path)

    config = load_config(environ=environ)

    assert config.profile == "default"


def test_explicit_nonexistent_file_fails(tmp_path: Path) -> None:
    missing = tmp_path / "does not exist.toml"

    with pytest.raises(ConfigFileNotFoundError, match="does not exist"):
        load_config(missing, environ={})


def test_non_agentx_environment_values_are_ignored_and_not_exposed(tmp_path: Path) -> None:
    secret_value = "should-never-appear-in-config"
    environment = _isolated_environment(tmp_path) | {
        "OPENAI_API_KEY": secret_value,
        "SOME_PASSWORD": secret_value,
    }

    config = load_config(environ=environment)

    assert secret_value not in repr(config)
    assert config.profile == "default"


def test_os_environment_can_be_used_safely_with_pytest_monkeypatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in tuple(os.environ):
        if name.startswith("AGENTX_"):
            monkeypatch.delenv(name, raising=False)

    if os.name == "nt":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local App Data"))
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg data"))
    monkeypatch.setenv("AGENTX_PROFILE", "monkeypatched")
    monkeypatch.setenv("AGENTX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AGENTX_DEBUG", "false")

    config = load_config()

    assert config.profile == "monkeypatched"


def test_importing_configuration_module_has_no_filesystem_side_effects(tmp_path: Path) -> None:
    working_directory = tmp_path / "import sandbox with spaces"
    working_directory.mkdir()

    result = subprocess.run(
        [sys.executable, "-I", "-c", "import agentx.infrastructure.config"],
        cwd=working_directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert list(working_directory.iterdir()) == []


def test_configuration_model_has_no_secret_fields() -> None:
    names = {field.name for field in fields(AgentXConfig)}
    forbidden_fragments = {"secret", "password", "token", "api_key", "credential"}

    assert names == {"profile", "data_dir", "log_level", "debug"}
    assert not any(fragment in name for name in names for fragment in forbidden_fragments)


def test_default_config_path_does_not_depend_on_current_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environ = _isolated_environment(tmp_path)
    first = default_config_path(environ)
    other_directory = tmp_path / "other working directory"
    other_directory.mkdir()

    monkeypatch.chdir(other_directory)

    assert default_config_path(environ) == first
