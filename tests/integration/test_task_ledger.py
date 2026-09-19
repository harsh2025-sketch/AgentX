"""Guard exact AX identities, evidence requirements and generated status."""

from __future__ import annotations

import copy
import json
import runpy
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


class TaskLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads((ROOT / "docs" / "TASKS.json").read_text(encoding="utf-8"))
        self.script: dict[str, Any] = runpy.run_path(str(ROOT / "scripts" / "task_ledger.py"))

    def test_all_600_rows_validate_and_summary_is_current(self) -> None:
        self.script["validate"](self.data)
        self.assertEqual(
            self.script["render"](self.data),
            (ROOT / "docs" / "TASKS.md").read_text(encoding="utf-8"),
        )

    def test_milestone_summary_reports_every_acceptance_state(self) -> None:
        rendered = self.script["render"](self.data)
        self.assertIn(
            "| Milestone | Reported complete | Reported partial | Reported remaining | "
            "Verified | Not audited | In progress | Blocked | Not implemented |",
            rendered,
        )
        self.assertIn("| M16 Production and Release |", rendered)
        self.assertIn("| 25 | 0 | 0 | 5 | 0 |", rendered)

    def test_audit_report_covers_exactly_the_canonical_600_tasks(self) -> None:
        report = json.loads((ROOT / "docs" / "AUDIT_600.json").read_text(encoding="utf-8"))
        rows = report["tasks"]
        self.assertEqual(report["audited_task_count"], 600)
        self.assertEqual(
            [row["id"] for row in rows],
            [f"AX-{number:03}" for number in range(1, 601)],
        )
        ledger_states: dict[str, int] = {}
        for task in self.data["tasks"]:
            state = task["acceptance_status"]
            ledger_states[state] = ledger_states.get(state, 0) + 1
        for state in ("VERIFIED", "NOT_AUDITED", "IN_PROGRESS", "BLOCKED", "NOT_IMPLEMENTED"):
            self.assertEqual(report["after"].get(state, 0), ledger_states.get(state, 0))

    def test_duplicate_or_missing_task_is_rejected(self) -> None:
        self.data["tasks"][1] = copy.deepcopy(self.data["tasks"][0])
        with self.assertRaisesRegex(ValueError, "exactly once"):
            self.script["validate"](self.data)

    def test_new_work_cannot_rewrite_the_reported_baseline(self) -> None:
        self.data["tasks"][599]["reported_status"] = "COMPLETE"
        with self.assertRaisesRegex(ValueError, "historical baseline"):
            self.script["validate"](self.data)

    def test_verified_claim_requires_all_four_evidence_categories(self) -> None:
        self.data["tasks"][599]["acceptance_status"] = "VERIFIED"
        with self.assertRaisesRegex(ValueError, "requires implementation"):
            self.script["validate"](self.data)

    def test_dependency_cycle_is_rejected(self) -> None:
        self.data["tasks"][0]["depends_on"] = ["AX-002"]
        self.data["tasks"][1]["depends_on"] = ["AX-001"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.script["validate"](self.data)

    def test_unknown_dependency_is_rejected(self) -> None:
        self.data["tasks"][0]["depends_on"] = ["AX-601"]
        with self.assertRaisesRegex(ValueError, "invalid dependency"):
            self.script["validate"](self.data)

    def test_forward_dependency_is_rejected_after_cycle_validation(self) -> None:
        self.data["tasks"][2]["depends_on"] = ["AX-004"]
        with self.assertRaisesRegex(ValueError, "must precede dependent task"):
            self.script["validate"](self.data)

    def test_wrong_milestone_is_rejected(self) -> None:
        self.data["tasks"][0]["milestone"] = "M16"
        with self.assertRaisesRegex(ValueError, "invalid milestone"):
            self.script["validate"](self.data)


if __name__ == "__main__":
    unittest.main()
