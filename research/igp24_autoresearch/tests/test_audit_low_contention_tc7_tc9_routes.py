from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import audit_low_contention_pair_routes as base
import audit_low_contention_tc7_tc9_routes as audit
import run_low_contention_sequential as lane


class LowContentionTc7Tc9AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = lane.read_json(audit.CERTIFICATE)
        cls.runbooks = cls.certificate["runbooks"]

    def test_named_receipts_and_all_outboxes_are_excluded(self) -> None:
        named = self.certificate["namedReceiptAudit"]
        self.assertEqual(len(named), 3)
        self.assertEqual(
            {row["submissionId"] for row in named},
            set(audit.NAMED_RECEIPTS),
        )
        for row in named:
            self.assertEqual(
                row["exactHashesExcluded"],
                audit.NAMED_RECEIPTS[row["submissionId"]],
            )
            self.assertEqual(
                row["exactPairsExcluded"],
                audit.NAMED_RECEIPTS[row["submissionId"]],
            )
            self.assertEqual(row["rejectedCount"], 0)
        index = lane.read_json(audit.OUTBOX_INDEX)
        self.assertTrue(all(index["checks"].values()))
        self.assertEqual(
            index["outboxFiles"], len(index["artifacts"])
        )

    def test_manifest_hashes_accept_trailing_polynomial_metadata(self) -> None:
        line = ",".join(["1"] + ["0"] * 23 + ["1"])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manifest.txt"
            path.write_text(
                f"{line} # poly_disc_primes=[2,3,5]\n",
                encoding="utf-8",
            )
            self.assertEqual(
                audit.manifest_hashes(path, expected=1),
                {hashlib.sha256(line.encode("ascii")).hexdigest()},
            )

    def test_routes_are_distinct_exact_single_orbit_and_ranked(self) -> None:
        targets = set()
        source_hashes = set()
        source_keys = set()
        team_counts = []
        for row in self.runbooks:
            targets.add((row["target"]["label"], row["target"]["r"]))
            source_hashes.add(row["source"]["coefficientSha256"])
            source_keys.add(
                (
                    row["source"]["submissionId"],
                    row["source"]["polynomialIndex"],
                )
            )
            team_counts.append(row["target"]["teamCountAtSeal"])
            self.assertEqual(row["exactAction"]["length24OrbitCount"], 1)
            self.assertTrue(
                row["exactAction"][
                    "deterministicAcrossCompatibleClasses"
                ]
            )
            self.assertFalse(row["guards"]["submissionAuthorized"])
            self.assertTrue(
                all(
                    value
                    for key, value in row["guards"].items()
                    if key != "submissionAuthorized"
                )
            )
        self.assertEqual(len(targets), len(self.runbooks))
        self.assertEqual(len(source_hashes), len(self.runbooks))
        self.assertEqual(len(source_keys), len(self.runbooks))
        self.assertEqual(team_counts, sorted(team_counts))
        self.assertEqual(team_counts.count(7), 9)
        self.assertEqual(team_counts.count(8), 11)
        self.assertEqual(team_counts.count(9), 10)

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
