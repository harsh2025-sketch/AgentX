"""Incomplete or restricted visibility must never imply a missing capability."""

from unittest import TestCase

from agentx.capabilities.abi import (
    CapabilityIdentity,
    CapabilityName,
    CapabilityVersion,
)
from agentx.capability_gap import CapabilityGapDetector


def identity(major: int = 1) -> CapabilityIdentity:
    return CapabilityIdentity(
        name=CapabilityName("files.read"),
        version=CapabilityVersion(major=major, minor=0, patch=0),
    )


class CapabilityInventoryVisibilityTests(TestCase):
    def test_partial_inventory_reports_unknown_not_missing(self) -> None:
        required = identity()
        report = CapabilityGapDetector(()).detect((required,))
        self.assertEqual(report.unknown, (required,))
        self.assertEqual(report.gaps, ())
        self.assertFalse(report.is_satisfied)

    def test_complete_inventory_can_prove_absence(self) -> None:
        required = identity()
        report = CapabilityGapDetector((), inventory_complete=True).detect((required,))
        self.assertEqual(tuple(gap.required for gap in report.gaps), (required,))
        self.assertEqual(report.unknown, ())
        self.assertFalse(report.is_satisfied)

    def test_restriction_takes_precedence_over_presence_and_completeness(self) -> None:
        required = identity()
        for inventory in ((), (required,)):
            report = CapabilityGapDetector(
                inventory, inventory_complete=True, restricted=(required,)
            ).detect((required,))
            self.assertEqual(report.restricted, (required,))
            self.assertEqual(report.available, ())
            self.assertEqual(report.gaps, ())
            self.assertFalse(report.is_satisfied)

    def test_restricted_alternative_is_not_suggested(self) -> None:
        older, required = identity(), identity(2)
        report = CapabilityGapDetector(
            (older,), inventory_complete=True, restricted=(older,)
        ).detect((required,))
        self.assertEqual(report.gaps[0].alternatives, ())

    def test_present_identity_remains_descriptive_available_evidence(self) -> None:
        required = identity()
        report = CapabilityGapDetector((required,)).detect((required,))
        self.assertEqual(report.available, (required,))
        self.assertTrue(report.is_satisfied)

    def test_completeness_rejects_truthy_untyped_values(self) -> None:
        with self.assertRaisesRegex(TypeError, "must be a bool"):
            CapabilityGapDetector((), inventory_complete="true")  # type: ignore[arg-type]
