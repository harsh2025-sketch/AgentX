"""Integration tests for N2.12: the cold-vs-warm experiment harness end to end.

These run the harness in real child interpreter processes with ``-I``
(isolated mode, no ``PYTHONPATH``/source-tree leakage) from unrelated working
directories, exactly as a user of the installed artifact would. The two
scripted phases are fully deterministic — fixed UUIDs, fixed timestamps, no
clock, no sleep, no model, no network — so the canonical comparative evidence
is byte-identical across runs and processes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

_EXPERIMENT_SCRIPT = """
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from agentx.core.events import VerificationPayload
from agentx.core.ids import EpisodeId, ProcedureId, TaskId
from agentx.core.reuse_efficiency import (
    ExecutionEfficiencyEvidence,
    ExecutionEvidenceOutcome,
    ProcedureRevisionRef,
    ReuseMode,
)
from agentx.reuse_experiment import (
    ExperimentPhase,
    PhaseExecutionRequest,
    ReuseExperimentHarness,
    ReuseExperimentResult,
    ReuseExperimentSpec,
)

TASK = TaskId(UUID(int=60_001))
COLD_EPISODE = EpisodeId(UUID(int=60_101))
WARM_EPISODE = EpisodeId(UUID(int=60_102))
PROCEDURE = ProcedureRevisionRef(ProcedureId(UUID(int=60_201)), 3)
START = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


class ScriptedRunner:
    def __init__(self, evidence):
        self.evidence = evidence
        self.requests = []

    def run_phase(self, request):
        self.requests.append(request)
        return self.evidence


def cold_evidence():
    return ExecutionEfficiencyEvidence(
        task_id=TASK,
        episode_id=COLD_EPISODE,
        correlation_id=UUID(int=60_301),
        mode=ReuseMode.NOVEL_PLAN,
        outcome=ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
        evidence_source="integration.cold_instrumentation",
        evidence_reference=UUID(int=60_401),
        started_at=START,
        ended_at=START + timedelta(seconds=120),
        model_calls=12,
        model_input_tokens=9_000,
        model_output_tokens=3_000,
        research_queries=4,
        machine_actions=9,
        repair_attempts=2,
        external_cost=Decimal("1.50"),
        cost_unit="USD",
        verification=VerificationPayload(passed=True, detail="cold run verified"),
        verification_source="integration.verifier",
        verification_reference=UUID(int=60_501),
    )


def warm_evidence():
    return ExecutionEfficiencyEvidence(
        task_id=TASK,
        episode_id=WARM_EPISODE,
        correlation_id=UUID(int=60_302),
        mode=ReuseMode.PROCEDURE_REUSE,
        outcome=ExecutionEvidenceOutcome.VERIFIED_SUCCESS,
        evidence_source="integration.warm_instrumentation",
        evidence_reference=UUID(int=60_402),
        started_at=START + timedelta(hours=1),
        ended_at=START + timedelta(hours=1, seconds=30),
        model_calls=1,
        model_input_tokens=400,
        model_output_tokens=100,
        research_queries=0,
        machine_actions=2,
        repair_attempts=0,
        external_cost=Decimal("0.10"),
        cost_unit="USD",
        procedure=PROCEDURE,
        verification=VerificationPayload(passed=True, detail="warm run verified"),
        verification_source="integration.verifier",
        verification_reference=UUID(int=60_502),
    )


cold_runner = ScriptedRunner(cold_evidence())
warm_runner = ScriptedRunner(warm_evidence())
harness = ReuseExperimentHarness(cold_runner=cold_runner, warm_runner=warm_runner)
spec = ReuseExperimentSpec(
    experiment_id=UUID(int=60_601),
    cold_task_id=TASK,
    warm_task_id=TASK,
    expected_warm_procedure=PROCEDURE,
)
result = harness.run(spec)
assert [r.phase for r in cold_runner.requests] == [ExperimentPhase.COLD]
assert [r.phase for r in warm_runner.requests] == [ExperimentPhase.WARM]
round_tripped = ReuseExperimentResult.from_json(result.to_json())
assert round_tripped == result
print(result.to_json())
"""


def _run_python(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _unrelated_directory(tmp_path: Path) -> Path:
    workdir = tmp_path / "unrelated working directory"
    workdir.mkdir()
    return workdir


def _parse_result(stdout: str) -> dict[str, Any]:
    raw: object = json.loads(stdout.strip())
    assert isinstance(raw, dict)
    return raw


def test_harness_experiment_is_deterministic_across_processes(tmp_path: Path) -> None:
    workdir = _unrelated_directory(tmp_path)

    first = _run_python(_EXPERIMENT_SCRIPT, workdir)
    second = _run_python(_EXPERIMENT_SCRIPT, workdir)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout

    raw = _parse_result(first.stdout)
    assert raw["schema_version"] == 1
    assert raw["verdict"] == "warm_improved"
    assert raw["cold_classification"] == "cold"
    assert raw["warm_classification"] == "warm"
    assert raw["spec"]["expected_warm_procedure"]["revision"] == 3
    comparison = raw["comparison"]
    assert comparison is not None
    assert comparison["disposition"] == "improved"
    metrics = {metric["metric"]: metric for metric in comparison["metrics"]}
    assert metrics["model_calls"]["delta"] == "-11"
    assert metrics["model_tokens"]["delta"] == "-11500"
    assert metrics["duration_microseconds"]["delta"] == "-90000000"
    assert metrics["external_cost"]["delta"] == "-1.40"
    assert metrics["research_queries"]["change"] == "improved"
    assert metrics["machine_actions"]["change"] == "improved"
    assert metrics["repair_attempts"]["change"] == "improved"


def test_harness_module_imports_from_installed_artifact_in_isolation(tmp_path: Path) -> None:
    workdir = _unrelated_directory(tmp_path)

    result = _run_python(
        "import agentx.reuse_experiment as m; "
        "print(m.REUSE_EXPERIMENT_SCHEMA_VERSION, m.classify_reuse_mode.__name__)",
        workdir,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1 classify_reuse_mode"


def test_cheaper_failed_warm_phase_stays_a_regression_in_child_process(tmp_path: Path) -> None:
    workdir = _unrelated_directory(tmp_path)
    script = _EXPERIMENT_SCRIPT.replace(
        "outcome=ExecutionEvidenceOutcome.VERIFIED_SUCCESS,\n"
        '        evidence_source="integration.warm_instrumentation",',
        "outcome=ExecutionEvidenceOutcome.FAILED,\n"
        '        evidence_source="integration.warm_instrumentation",',
    ).replace(
        'verification=VerificationPayload(passed=True, detail="warm run verified"),',
        "verification=VerificationPayload(\n"
        '            passed=False, detail="verified=true warm_is_better=true"\n'
        "        ),",
    )

    result = _run_python(script, workdir)

    assert result.returncode == 0, result.stderr
    raw = _parse_result(result.stdout)
    assert raw["verdict"] == "warm_regressed"
    assert any("lost verified success" in reason for reason in raw["reasons"])


def test_invalid_phase_classification_yields_no_comparison_in_child_process(
    tmp_path: Path,
) -> None:
    workdir = _unrelated_directory(tmp_path)
    script = (
        _EXPERIMENT_SCRIPT.replace(
            "mode=ReuseMode.PROCEDURE_REUSE,", "mode=ReuseMode.NOVEL_PLAN,", 1
        )
        .replace("expected_warm_procedure=PROCEDURE,", "")
        .replace("        procedure=PROCEDURE,\n", "")
    )

    result = _run_python(script, workdir)

    assert result.returncode == 0, result.stderr
    raw = _parse_result(result.stdout)
    assert raw["verdict"] == "invalid_experiment"
    assert raw["comparison"] is None
    assert (
        "warm phase evidence mode novel_plan does not claim reusable prior execution"
        in raw["reasons"]
    )


def test_related_task_experiment_across_different_tasks_in_child_process(
    tmp_path: Path,
) -> None:
    workdir = _unrelated_directory(tmp_path)
    script = (
        _EXPERIMENT_SCRIPT.replace(
            "from agentx.core.reuse_efficiency import (",
            "from agentx.core.reuse_efficiency import (\n    TaskRelationshipEvidence,",
        )
        .replace(
            "warm_task_id=TASK,",
            "warm_task_id=TaskId(UUID(int=60_002)),\n"
            "    relationship=TaskRelationshipEvidence(\n"
            "        relationship=__import__(\n"
            "            'agentx.core.reuse_efficiency', fromlist=['TaskRelationship']\n"
            "        ).TaskRelationship.RELATED_TASK_REUSE,\n"
            "        source='integration.related_tasks',\n"
            "        reference=UUID(int=60_701),\n"
            "    ),",
        )
        .replace(
            "task_id=TASK,\n        episode_id=WARM_EPISODE",
            "task_id=TaskId(UUID(int=60_002)),\n        episode_id=WARM_EPISODE",
        )
    )

    result = _run_python(script, workdir)

    assert result.returncode == 0, result.stderr
    raw = _parse_result(result.stdout)
    assert raw["verdict"] == "warm_improved"
    assert raw["comparison"]["relationship"]["relationship"] == "related_task_reuse"
    assert any("related-task reuse" in reason for reason in raw["reasons"])


def test_result_rejects_tampered_verdict_in_child_process(tmp_path: Path) -> None:
    workdir = _unrelated_directory(tmp_path)
    script = _EXPERIMENT_SCRIPT.replace(
        "print(result.to_json())",
        "raw = json.loads(result.to_json())\n"
        "flip = 'warm_regressed' if result.verdict.value == 'warm_improved' "
        "else 'warm_improved'\n"
        "raw['verdict'] = flip\n"
        "try:\n"
        "    ReuseExperimentResult.from_dict(raw)\n"
        "except Exception as exc:\n"
        "    print('REJECTED', type(exc).__name__)\n"
        "else:\n"
        "    raise SystemExit('tampered verdict was accepted')",
    ).replace("import json\n", "import json\n", 1)

    result = _run_python(script, workdir)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("REJECTED ReuseExperimentError")
