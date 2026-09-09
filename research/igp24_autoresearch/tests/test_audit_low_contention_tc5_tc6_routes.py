from __future__ import annotations

import unittest
from pathlib import Path

import audit_low_contention_pair_routes as base
import audit_low_contention_tc5_tc6_routes as audit
import run_low_contention_sequential as lane


class LowContentionTc5Tc6AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = lane.read_json(audit.CERTIFICATE)
        cls.runbooks = cls.certificate["runbooks"]

    def test_joint_frontier_is_nonempty_distinct_and_upside_ranked(self) -> None:
        self.assertGreater(len(self.runbooks), 0)
        targets = {
            (row["target"]["label"], row["target"]["r"])
            for row in self.runbooks
        }
        self.assertEqual(len(targets), len(self.runbooks))
        team_counts = [
            row["target"]["teamCountAtSeal"] for row in self.runbooks
        ]
        self.assertEqual(team_counts, sorted(team_counts))
        self.assertEqual(set(team_counts), {5, 6})

    def test_every_route_is_exact_single_orbit_and_fresh(self) -> None:
        for row in self.runbooks:
            self.assertEqual(row["exactAction"]["length24OrbitCount"], 1)
            self.assertTrue(
                row["exactAction"][
                    "deterministicAcrossCompatibleClasses"
                ]
            )
            self.assertTrue(row["routeReliability"]["exactDeterministic"])
            self.assertTrue(row["source"]["notPreviouslyPairConstructed"])
            self.assertFalse(row["guards"]["submissionAuthorized"])
            self.assertTrue(
                all(
                    value
                    for key, value in row["guards"].items()
                    if key != "submissionAuthorized"
                )
            )

    def test_outputs_absent_and_post_tc4_reaudit_is_mandatory(self) -> None:
        self.assertTrue(
            self.certificate["executionBoundary"][
                "fullAuditRerunAfterTc4Required"
            ]
        )
        for row in self.runbooks:
            output = lane.ROOT / row["output"]
            self.assertFalse(output.exists())
            self.assertFalse(
                output.with_suffix(output.suffix + ".tmp").exists()
            )
            self.assertTrue(
                row["guards"]["postTc4FullAuditRerunRequired"]
            )

    def test_certificate_is_coefficient_free_and_light_only(self) -> None:
        text = audit.CERTIFICATE.read_text(encoding="utf-8")
        self.assertIsNone(base.COEFFICIENT_LINE_RE.search(text))
        self.assertTrue(all(self.certificate["checks"].values()))
        source = Path(audit.__file__).read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("api_json(", source)
        self.assertNotIn('"--commit"', source)
        self.assertEqual(self.certificate["sideEffects"]["sageRuns"], 0)
        self.assertEqual(self.certificate["sideEffects"]["gapRuns"], 0)
        self.assertEqual(self.certificate["sideEffects"]["networkCalls"], 0)
        self.assertEqual(
            self.certificate["sideEffects"]["submissionCalls"], 0
        )


if __name__ == "__main__":
    unittest.main()
