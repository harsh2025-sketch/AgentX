"""Validate/run the machine-readable M16 acceptance corpus."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "M16_ACCEPTANCE_CORPUS.json"


def load_manifest() -> dict[str, Any]:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError("unsupported M16 corpus schema")
    corpora = raw.get("corpora")
    if not isinstance(corpora, dict) or not corpora:
        raise ValueError("M16 corpus must define corpora")
    expected_tasks = {
        "windows": "AX-590",
        "browser": "AX-591",
        "memory": "AX-592",
        "learning": "AX-593",
        "repair": "AX-594",
        "security": "AX-595",
    }
    for name, task in expected_tasks.items():
        entry = corpora.get(name)
        if not isinstance(entry, dict) or entry.get("task") != task:
            raise ValueError(f"invalid {name} corpus task binding")
        tests = entry.get("tests")
        if not isinstance(tests, list) or not tests:
            raise ValueError(f"{name} corpus has no tests")
        for relative in tests:
            if not isinstance(relative, str) or not relative.startswith("tests/"):
                raise ValueError(f"invalid {name} corpus path")
            if not (ROOT / relative).is_file():
                raise ValueError(f"missing M16 corpus test: {relative}")
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    manifest = load_manifest()
    if not args.run:
        sys.stdout.write("M16 acceptance corpus validated.\n")
        return 0
    tests = sorted(
        {
            path
            for entry in manifest["corpora"].values()
            for path in entry["tests"]
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *tests],
        cwd=ROOT,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
