from __future__ import annotations

import unittest
from pathlib import Path

import audit_low_contention_pair_routes as base
import audit_low_contention_tc4_routes as audit
import run_low_contention_sequential as lane


class LowContentionTc4AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = lane.read_json(audit.CERTIFICATE)
        cls.runbooks = cls.certificate["runbooks"]

    def test_tc3_seal_is_required_and_tc4_scope_is_exact(self) -> None:
        seal = lane.read_json(audit.TC3_SEAL)
        self.assertEqual(
            seal["status"],
            "sealed_23_of_23_exact_candidates_receipt_mapped",
        )
        self.assertEqual(seal["routeCount"], 23)
        self.assertEqual(
            self.certificate["scope"],
            "deterministic_tc4_fresh_untested_anchors",
        )
        self.assertEqual(
            self.certificate["testedAnchorExclusion"][
                "cachedNovelReadyCandidates"
            ],
            0,
        )

    def test_runbooks_are_distinct_guarded_tc4_routes(self) -> None:
        self.assertGreater(len(self.runbooks), 0)
        targets = set()
        for row in self.runbooks:
            self.assertEqual(row["target"]["teamCountAtSeal"], 4)
            self.assertEqual(
                row["target"]["projectedMarginalScoreExact"], "1/16"
            )
            self.assertEqual(row["exactAction"]["length24OrbitCount"], 1)
            self.assertTrue(
                row["exactAction"]["deterministicAcrossCompatibleClasses"]
            )
            self.assertTrue(row["source"]["notPreviouslyPairConstructed"])
            self.assertFalse(row["guards"]["submissionAuthorized"])
            self.assertTrue(
                all(
                    value
                    for key, value in row["guards"].items()
                    if key != "submissionAuthorized"
                )
            )
            targets.add((row["target"]["label"], row["target"]["r"]))
        self.assertEqual(len(targets), len(self.runbooks))

    def test_output_states_are_fail_closed_and_commands_are_handoffs_only(self) -> None:
        for row in self.runbooks:
            output = lane.ROOT / row["output"]
            self.assertFalse(
                output.with_suffix(output.suffix + ".tmp").exists()
            )
            if output.exists():
                paths = lane.planned_paths(row)
                self.assertTrue(paths["postflight"].is_file(), row["routeId"])
                self.assertTrue(paths["manifest"].is_file(), row["routeId"])
                self.assertTrue(paths["stageCertificate"].is_file(), row["routeId"])
            self.assertEqual(row["heavyCommand"][0:3], [
                "/usr/local/bin/sage", "-python", "pair_sum_one.sage.py"
            ])

    def test_certificate_is_coefficient_free_and_audit_is_light_only(self) -> None:
        certificate_text = audit.CERTIFICATE.read_text(encoding="utf-8")
        self.assertIsNone(base.COEFFICIENT_LINE_RE.search(certificate_text))
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
