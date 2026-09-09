from __future__ import annotations

import unittest
from pathlib import Path

import audit_low_contention_pair_routes as base
import finalize_low_contention_tc5_tc6_post_tc4 as finalizer
import run_low_contention_sequential as lane


class FinalTc5Tc6PostTc4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = lane.read_json(finalizer.CERTIFICATE)
        cls.runbooks = cls.certificate["runbooks"]

    def test_tc4_receipt_is_fully_accounted(self) -> None:
        receipt = self.certificate["tc4Receipt"]
        self.assertEqual(receipt["submissionId"], finalizer.TC4_SUBMISSION_ID)
        self.assertEqual(receipt["declaredPolynomials"], 22)
        self.assertEqual(receipt["rejectedCount"], 0)
        self.assertEqual(receipt["exactHashesExcluded"], 22)
        self.assertEqual(receipt["exactPairsExcluded"], 22)

    def test_all_provisional_identities_survived_exactly(self) -> None:
        delta = self.certificate["provisionalToFinalDelta"]
        self.assertEqual(delta["provisionalRunbooks"], 14)
        self.assertEqual(delta["finalRunbooks"], 14)
        self.assertEqual(delta["exactIdentitySurvivors"], 14)
        self.assertEqual(delta["droppedExactIdentities"], 0)
        self.assertEqual(delta["changedAnchorRouteCount"], 0)

    def test_final_routes_are_guarded_distinct_and_ranked(self) -> None:
        targets = set()
        team_counts = []
        for row in self.runbooks:
            targets.add((row["target"]["label"], row["target"]["r"]))
            team_counts.append(row["target"]["teamCountAtSeal"])
            self.assertTrue(row["guards"]["tc4ReceiptHashesExcludedAtSeal"])
            self.assertTrue(row["guards"]["sourceAnchorRevalidatedAfterTc4"])
            self.assertTrue(row["guards"]["targetPairRevalidatedAfterTc4"])
            self.assertFalse(row["guards"]["submissionAuthorized"])
            self.assertEqual(row["exactAction"]["length24OrbitCount"], 1)
            self.assertTrue(
                row["exactAction"][
                    "deterministicAcrossCompatibleClasses"
                ]
            )
        self.assertEqual(len(targets), len(self.runbooks))
        self.assertEqual(team_counts, sorted(team_counts))
        self.assertEqual(team_counts.count(5), 4)
        self.assertEqual(team_counts.count(6), 10)

    def test_certificate_is_coefficient_free_and_light_only(self) -> None:
        text = finalizer.CERTIFICATE.read_text(encoding="utf-8")
        self.assertIsNone(base.COEFFICIENT_LINE_RE.search(text))
        self.assertTrue(all(self.certificate["checks"].values()))
        source = Path(finalizer.__file__).read_text(encoding="utf-8")
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
