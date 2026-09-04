"""Integration tests: run the CLI exactly as a user would, in a child process.

These use ``sys.executable`` (never a bare ``python`` on PATH) and argument
lists (never ``shell=True``) so they behave identically on Windows and POSIX.
``-I`` prevents the source tree or ``PYTHONPATH`` from masking packaging bugs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agentx import __version__

pytestmark = pytest.mark.integration


def _run_module(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    # Isolated mode proves the installed artifact works without PYTHONPATH/source-tree leakage.
    return subprocess.run(
        [sys.executable, "-I", "-m", "agentx", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_python_m_agentx_version_from_unrelated_working_directory(tmp_path: Path) -> None:
    workdir = tmp_path / "unrelated working directory"
    workdir.mkdir()

    result = _run_module("--version", cwd=workdir)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"agentx {__version__}"
    assert result.stderr == ""


def test_python_m_agentx_help() -> None:
    result = _run_module("--help")

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage: agentx")


def test_python_m_agentx_with_no_arguments_succeeds() -> None:
    result = _run_module()

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage: agentx")


def test_import_is_quiet_and_does_not_write_to_working_directory(tmp_path: Path) -> None:
    workdir = tmp_path / "clean import"
    workdir.mkdir()

    result = subprocess.run(
        [sys.executable, "-I", "-c", "import agentx"],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert not any(workdir.iterdir())
