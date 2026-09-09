from __future__ import annotations

import unittest
from pathlib import Path

import audit_low_contention_higher_tc_routes as audit
import audit_low_contention_pair_routes as base
import run_low_contention_sequential as lane


class HigherTcRouteAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = lane.read_json(audit.CERTIFICATE)
        cls.runbooks = cls.certificate["runbooks"]

    def test_tc3_selected_and_tc4_not_evaluated(self) -> None:
        self.assertEqual(self.certificate["selectedTeamCount"], 3)
        self.assertFalse(self.certificate["tc4FallbackEvaluated"])
        self.assertIsNone(self.certificate["tc4Census"])
        self.assertEqual(len(self.runbooks), 23)
        self.assertEqual(self.certificate["runbookProjection"]["marginalScoreExact"], "23/8")

    def test_all_runbooks_are_guarded_fresh_tc3(self) -> None:
        targets = set()
        for row in self.runbooks:
            self.assertEqual(row["target"]["teamCountAtSeal"], 3)
            self.assertEqual(row["target"]["projectedMarginalScoreExact"], "1/8")
            self.assertTrue(row["source"]["notPreviouslyPairConstructed"])
            self.assertFalse(row["guards"]["submissionAuthorized"])
            self.assertTrue(all(
                value for key, value in row["guards"].items()
                if key != "submissionAuthorized"
            ))
            targets.add((row["target"]["label"], row["target"]["r"]))
        self.assertEqual(len(targets), 23)

    def test_best_route_and_output_states_are_fail_closed(self) -> None:
        self.assertEqual(
            self.runbooks[0]["routeId"],
            "hc3_001_24T17215_r18_to_24T17245_r12",
        )
        for row in self.runbooks:
            output = lane.ROOT / row["output"]
            self.assertFalse(output.with_suffix(output.suffix + ".tmp").exists())
            if output.exists():
                paths = lane.planned_paths(row)
                self.assertTrue(paths["postflight"].is_file(), row["routeId"])
                self.assertTrue(paths["manifest"].is_file(), row["routeId"])
                self.assertTrue(paths["stageCertificate"].is_file(), row["routeId"])

    def test_certificate_is_coefficient_free_light_only(self) -> None:
        text = audit.CERTIFICATE.read_text(encoding="utf-8")
        self.assertIsNone(base.COEFFICIENT_LINE_RE.search(text))
        self.assertTrue(all(self.certificate["checks"].values()))
        source = Path(audit.__file__).read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("api_json(", source)
        self.assertEqual(self.certificate["sideEffects"]["sageRuns"], 0)
        self.assertEqual(self.certificate["sideEffects"]["gapRuns"], 0)


if __name__ == "__main__":
    unittest.main()
