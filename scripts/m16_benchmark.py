"""Reproducible M16 restart/latency benchmark with controlled model-cost fixture."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from agentx import __version__
from agentx.release import initialize_release_state

_DATASET = "agentx-m16-release-startup-v1"
_CONTROLLED_EFFICIENCY = {
    "evidence_kind": "controlled_fixture",
    "source_test": "tests/integration/test_reuse_experiment.py",
    "cold": {
        "strategy": "novel_plan",
        "model_calls": 12,
        "input_tokens": 9000,
        "output_tokens": 3000,
        "external_cost_usd": "1.50",
    },
    "warm": {
        "strategy": "procedure_reuse",
        "model_calls": 1,
        "input_tokens": 400,
        "output_tokens": 100,
        "external_cost_usd": "0.10",
    },
    "claim_limit": (
        "Instrumentation/comparison evidence only; this is not a live-provider "
        "latency, token, or billing measurement."
    ),
}


def _stats(samples_ns: list[int]) -> dict[str, Any]:
    ordered = sorted(samples_ns)
    p95_index = min(len(ordered) - 1, max(0, (95 * len(ordered) - 1) // 100))
    return {
        "samples_ns": samples_ns,
        "min_ns": min(samples_ns),
        "median_ns": int(statistics.median(samples_ns)),
        "p95_ns": ordered[p95_index],
        "max_ns": max(samples_ns),
    }


def run_benchmark(repetitions: int) -> dict[str, Any]:
    """Measure every requested repetition; failures are not silently discarded."""
    if repetitions < 3:
        raise ValueError("repetitions must be at least 3")

    fresh_samples: list[int] = []
    restart_samples: list[int] = []
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="agentx-m16-benchmark-") as raw:
        root = Path(raw)
        data_dir = root / "state"
        for index in range(repetitions):
            candidate = data_dir if index == 0 else root / f"fresh-{index}"
            started = time.perf_counter_ns()
            try:
                initialize_release_state(environ={}, data_dir=candidate)
            except Exception as exc:
                failures.append(f"fresh[{index}]={type(exc).__name__}:{exc}")
            else:
                fresh_samples.append(time.perf_counter_ns() - started)

        initialize_release_state(environ={}, data_dir=data_dir)
        for index in range(repetitions):
            started = time.perf_counter_ns()
            try:
                initialize_release_state(environ={}, data_dir=data_dir)
            except Exception as exc:
                failures.append(f"restart[{index}]={type(exc).__name__}:{exc}")
            else:
                restart_samples.append(time.perf_counter_ns() - started)

    return {
        "schema_version": 1,
        "dataset": _DATASET,
        "agentx_version": __version__,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "architecture": platform.machine(),
        },
        "methodology": {
            "clock": "time.perf_counter_ns",
            "repetitions": repetitions,
            "failed_runs_included": len(failures),
            "warmup_runs_excluded": 0,
            "seed": None,
        },
        "fresh_start": None if not fresh_samples else _stats(fresh_samples),
        "restart": None if not restart_samples else _stats(restart_samples),
        "controlled_model_token_cost": _CONTROLLED_EFFICIENCY,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_benchmark(args.repetitions)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(encoded)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8", newline="\n")
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
