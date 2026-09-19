"""M16 release startup and CLI acceptance."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agentx import __version__
from agentx.infrastructure.recovery import RecoveryDisposition
from agentx.release import initialize_release_state


def test_fresh_initialize_and_restart_reopen_are_healthy(tmp_path: Path) -> None:
    data_dir = tmp_path / "state"

    first = initialize_release_state(environ={}, data_dir=data_dir)
    second = initialize_release_state(environ={}, data_dir=data_dir)

    assert first.database_path.is_file()
    assert first.schema_version >= 9
    assert first.recovery.disposition is RecoveryDisposition.HEALTHY
    assert first.read_only is False
    assert second.schema_version == first.schema_version
    assert second.recovery.disposition is RecoveryDisposition.HEALTHY


def test_installed_cli_init_and_doctor_from_new_process(tmp_path: Path) -> None:
    data_dir = tmp_path / "installed state"
    workdir = tmp_path / "unrelated"
    workdir.mkdir()

    init_result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "agentx",
            "init",
            "--data-dir",
            str(data_dir),
        ],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert init_result.returncode == 0, init_result.stderr
    init_payload = json.loads(init_result.stdout)
    assert init_payload["version"] == __version__
    assert init_payload["recovery"] == "HEALTHY"

    doctor_result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "agentx",
            "doctor",
            "--data-dir",
            str(data_dir),
        ],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert doctor_result.returncode == 0, doctor_result.stderr
    doctor_payload = json.loads(doctor_result.stdout)
    assert doctor_payload["schema_version"] == init_payload["schema_version"]
    assert doctor_payload["recovery"] == "HEALTHY"
