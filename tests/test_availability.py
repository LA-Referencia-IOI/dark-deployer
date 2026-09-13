from __future__ import annotations

import unittest
from pathlib import Path

from deployment_v3.availability import analyze
from deployment_v3.planner import build_plan


ROOT = Path(__file__).resolve().parents[1]


class AvailabilityTests(unittest.TestCase):
    def test_reports_group_and_machine_quorum_risk(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")
        report = analyze(plan)
        self.assertEqual(report.validators, 4)
        self.assertEqual(report.quorum, 3)
        self.assertEqual(report.validator_failures_tolerated, 1)
        self.assertTrue(any("machine local" in warning for warning in report.warnings))
        machine_loss = next(item for item in report.scenarios if item.kind == "machine" and item.target == "local")
        self.assertEqual(machine_loss.consensus, "quorum_lost")
        self.assertEqual(machine_loss.applications_rpc, "primary_lost")
        self.assertEqual(machine_loss.storage, "publication_unavailable")

    def test_primary_rpc_loss_is_distinct_from_consensus_loss(self):
        plan = build_plan(ROOT / "examples" / "operator-inventory" / "production-five-host.json")
        report = analyze(plan)
        loss = next(item for item in report.scenarios if item.kind == "primary_rpc")
        self.assertEqual(loss.consensus, "continues")
        self.assertEqual(loss.applications_rpc, "primary_lost")


if __name__ == "__main__":
    unittest.main()
