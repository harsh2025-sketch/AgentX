"""Validate the M16 release corpus manifest."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any


def test_m16_acceptance_manifest_binds_all_six_release_corpora() -> None:
    root = Path(__file__).resolve().parents[2]
    script: dict[str, Any] = runpy.run_path(str(root / "scripts" / "m16_corpus.py"))
    manifest = script["load_manifest"]()
    assert set(manifest["corpora"]) == {
        "windows",
        "browser",
        "memory",
        "learning",
        "repair",
        "security",
    }
    assert manifest["corpora"]["security"]["task"] == "AX-595"
