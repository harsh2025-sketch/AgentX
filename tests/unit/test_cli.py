"""In-process unit tests for the ``agentx`` command-line interface."""

from __future__ import annotations

import pytest

from agentx import __main__ as cli
from agentx import __version__


def test_version_flag_prints_version_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == f"{cli.PROG} {__version__}"
    assert captured.err == ""


def test_help_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert captured.out.startswith(f"usage: {cli.PROG}")
    assert "--version" in captured.out


def test_no_arguments_prints_help_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith(f"usage: {cli.PROG}")


def test_unknown_argument_exits_with_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--definitely-not-a-flag"])

    # argparse's conventional exit code for a usage error.
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "unrecognized arguments" in captured.err
