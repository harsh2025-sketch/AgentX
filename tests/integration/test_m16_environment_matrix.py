"""Tests for truthful M16 environment-matrix evidence."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_environment_matrix_script_emits_machine_readable_truth(tmp_path: Path) -> None:
    output = tmp_path / "environment.json"
    result = subprocess.run(
        [sys.executable, "scripts/m16_environment_matrix.py", "--output", str(output)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == 1
    assert isinstance(evidence["matrix"]["windows_10_workstation_proven"], bool)
    assert isinstance(evidence["matrix"]["windows_11_workstation_proven"], bool)
    assert evidence["matrix"]["multi_dpi_proven"] is False
    assert any("Windows Server" in item for item in evidence["limitations"])
