from __future__ import annotations

import json
import unittest
from pathlib import Path

import audit_low_contention_pair_routes as base
import audit_low_contention_tc2_routes as audit
import run_low_contention_sequential as lane


class LowContentionTc2AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = lane.read_json(audit.CERTIFICATE)
        cls.runbooks = cls.certificate["runbooks"]

    def test_certificate_is_coefficient_free_and_fully_guarded(self) -> None:
        self.assertEqual(
            self.certificate["scope"],
            "deterministic_tc2_fresh_untested_anchors",
        )
        self.assertFalse(self.certificate["coefficientMaterialIncluded"])
        self.assertIsNone(
            base.COEFFICIENT_LINE_RE.search(
                audit.CERTIFICATE.read_text(encoding="utf-8")
            )
        )
        self.assertTrue(all(self.certificate["checks"].values()))

    def test_fourteen_distinct_tc2_runbooks_are_ready(self) -> None:
        self.assertEqual(len(self.runbooks), 14)
        targets = set()
        for row in self.runbooks:
            self.assertEqual(row["target"]["teamCountAtSeal"], 2)
            self.assertEqual(row["target"]["projectedMarginalScoreExact"], "1/4")
            self.assertTrue(row["source"]["notPreviouslyPairConstructed"])
            self.assertFalse(row["guards"]["submissionAuthorized"])
            self.assertTrue(
                all(
                    value for key, value in row["guards"].items()
                    if key != "submissionAuthorized"
                )
            )
            targets.add((row["target"]["label"], row["target"]["r"]))
        self.assertEqual(len(targets), 14)

    def test_best_route_and_output_states_are_fail_closed(self) -> None:
        self.assertEqual(
            self.runbooks[0]["routeId"],
            "lc2_001_24T17161_r14_to_24T17189_r8",
        )
        for row in self.runbooks:
            output = lane.ROOT / row["output"]
            self.assertFalse(output.with_suffix(output.suffix + ".tmp").exists())
            if output.exists():
                paths = lane.planned_paths(row)
                self.assertTrue(paths["postflight"].is_file(), row["routeId"])
                self.assertTrue(paths["manifest"].is_file(), row["routeId"])
                self.assertTrue(paths["stageCertificate"].is_file(), row["routeId"])

    def test_audit_is_light_only(self) -> None:
        source = Path(audit.__file__).read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("api_json(", source)
        self.assertNotIn('"--commit"', source)
        self.assertEqual(self.certificate["sideEffects"]["sageRuns"], 0)
        self.assertEqual(self.certificate["sideEffects"]["gapRuns"], 0)


if __name__ == "__main__":
    unittest.main()
