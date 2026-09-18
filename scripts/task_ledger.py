"""Validate the AX ledger and render its human-readable status without dependencies."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "TASKS.json"
SUMMARY = ROOT / "docs" / "TASKS.md"
ACCEPTANCE_STATES = {"NOT_AUDITED", "NOT_IMPLEMENTED", "IN_PROGRESS", "BLOCKED", "VERIFIED"}


def validate(data: dict[str, Any]) -> None:
    if data.get("schema_version") != 1:
        raise ValueError("unsupported ledger schema")
    tasks = data["tasks"]
    expected = [f"AX-{number:03}" for number in range(1, 601)]
    if [task["id"] for task in tasks] != expected:
        raise ValueError("ledger must contain AX-001 through AX-600 exactly once, in order")
    if Counter(task["reported_status"] for task in tasks) != {
        "COMPLETE": 349,
        "PARTIAL": 2,
        "NOT_IMPLEMENTED": 249,
    }:
        raise ValueError("historical baseline must stay separate from new acceptance evidence")
    by_id = {task["id"]: task for task in tasks}
    milestones = data["milestones"]
    if [item["id"] for item in milestones] != [f"M{i}" for i in range(17)]:
        raise ValueError("milestones must be M0 through M16")
    for task in tasks:
        if task["acceptance_status"] not in ACCEPTANCE_STATES:
            raise ValueError(f"invalid acceptance status: {task['id']}")
        if not isinstance(task["title"], str) or not task["title"].strip():
            raise ValueError(f"missing title: {task['id']}")
        milestone = next((m for m in milestones if m["id"] == task["milestone"]), None)
        if milestone is None or not milestone["first_task"] <= task["id"] <= milestone["last_task"]:
            raise ValueError(f"invalid milestone: {task['id']}")
        dependencies = task["depends_on"]
        if len(dependencies) != len(set(dependencies)):
            raise ValueError(f"duplicate dependency: {task['id']}")
        if any(dep not in by_id or dep == task["id"] for dep in dependencies):
            raise ValueError(f"invalid dependency: {task['id']}")
        for evidence in task["evidence"]:
            if not all(evidence.get(key) for key in ("kind", "reference", "note")):
                raise ValueError(f"malformed evidence: {task['id']}")
        if task["acceptance_status"] == "VERIFIED":
            kinds = {e["kind"] for e in task["evidence"]}
            if not {"implementation", "tests", "canonical_commit", "acceptance"} <= kinds:
                raise ValueError(
                    f"VERIFIED requires implementation/tests/commit/acceptance: {task['id']}"
                )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError(f"dependency cycle at {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id[task_id]["depends_on"]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in by_id:
        visit(task_id)


def render(data: dict[str, Any]) -> str:
    lines = [
        "# AgentX AX-001-AX-600 ledger",
        "",
        "Generated from `docs/TASKS.json` by `python scripts/task_ledger.py --write`.",
        "",
        data["status_policy"],
        "",
        data["dependency_coverage"],
        "",
        f"Baseline: `{data['baseline_commit']}`. {data['baseline_source']}",
        "",
        "| Milestone | Reported complete | Reported partial | Reported remaining | "
        "Verified | Not audited | In progress | Blocked | Not implemented |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for milestone in data["milestones"]:
        tasks = [task for task in data["tasks"] if task["milestone"] == milestone["id"]]
        counts = Counter(task["reported_status"] for task in tasks)
        acceptance = Counter(task["acceptance_status"] for task in tasks)
        lines.append(
            f"| {milestone['id']} {milestone['name']} | {counts['COMPLETE']} | "
            f"{counts['PARTIAL']} | {counts['NOT_IMPLEMENTED']} | "
            f"{acceptance['VERIFIED']} | {acceptance['NOT_AUDITED']} | "
            f"{acceptance['IN_PROGRESS']} | {acceptance['BLOCKED']} | "
            f"{acceptance['NOT_IMPLEMENTED']} |"
        )
    lines.extend(
        [
            "",
            "A zero verified-acceptance count means this ledger has not yet recorded a task-level",
            "acceptance audit; it does not mean the existing implementation is absent.",
            "",
            "| Task | Requirement | Reported baseline | Acceptance audit | Known dependencies |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for task in data["tasks"]:
        title = task["title"].replace("|", "\\|")
        lines.append(
            f"| {task['id']} | {title} | {task['reported_status']} | "
            f"{task['acceptance_status']} | {', '.join(task['depends_on'])} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenerate docs/TASKS.md")
    args = parser.parse_args()
    data = json.loads(LEDGER.read_text(encoding="utf-8"))
    validate(data)
    summary = render(data)
    if args.write:
        SUMMARY.write_text(summary, encoding="utf-8", newline="\n")
    elif not SUMMARY.exists() or SUMMARY.read_text(encoding="utf-8") != summary:
        sys.stderr.write("Task summary is stale; run python scripts/task_ledger.py --write\n")
        return 1
    sys.stdout.write("600 task identities, status evidence, dependencies, and summary validated.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
