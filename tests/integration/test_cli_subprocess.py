"""Integration tests: run the CLI exactly as a user would, in a child process.

These use ``sys.executable`` (never a bare ``python`` on PATH) and argument
lists (never ``shell=True``) so they behave identically on Windows and POSIX.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from agentx import __version__

pytestmark = pytest.mark.integration


def _run_module(*args: str) -> subprocess.CompletedProcess[str]:
    # Fixed argv list, no shell, trusted interpreter: portable and injection-safe.
    return subprocess.run(
        [sys.executable, "-m", "agentx", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_python_m_agentx_version() -> None:
    result = _run_module("--version")

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
