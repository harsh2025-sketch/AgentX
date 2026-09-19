"""M16 benchmark integrity tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _module() -> ModuleType:
    path = Path("scripts/m16_benchmark.py").resolve()
    spec = importlib.util.spec_from_file_location("m16_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_restart_benchmark_records_all_runs_and_labels_controlled_cost() -> None:
    result = _module().run_benchmark(3)
    assert result["methodology"]["repetitions"] == 3
    assert result["methodology"]["warmup_runs_excluded"] == 0
    assert result["failures"] == []
    assert len(result["fresh_start"]["samples_ns"]) == 3
    assert len(result["restart"]["samples_ns"]) == 3
    controlled = result["controlled_model_token_cost"]
    assert controlled["evidence_kind"] == "controlled_fixture"
    assert "not a live-provider" in controlled["claim_limit"]
    assert controlled["cold"]["model_calls"] == 12
    assert controlled["warm"]["model_calls"] == 1
