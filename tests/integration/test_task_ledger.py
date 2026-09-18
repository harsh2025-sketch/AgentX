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

    def test_wrong_milestone_is_rejected(self) -> None:
        self.data["tasks"][0]["milestone"] = "M16"
        with self.assertRaisesRegex(ValueError, "invalid milestone"):
            self.script["validate"](self.data)


if __name__ == "__main__":
    unittest.main()
