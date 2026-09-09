from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
import unittest
from pathlib import Path

from stage_v14_negative_twist import exact_real_root_count


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
CERTIFICATE = DATA / "low_contention_tc2_nonpair_closure_certificate.json"
SUMMARY = DATA / "low_contention_tc2_nonpair_closure_summary.json"
RUNBOOK = DATA / "low_contention_tc2_even_twist_guarded_runbook.json"
BUILDER = ROOT / "build_even_twist_action_subset.sage.py"
COEFFICIENT_RE = re.compile(r"(?<![0-9])-?[0-9]+(?:,-?[0-9]+){24}(?![0-9])")


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Tc2NonpairClosureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = json.loads(CERTIFICATE.read_text())
        cls.summary = json.loads(SUMMARY.read_text())
        cls.runbook = json.loads(RUNBOOK.read_text())
        cls.connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
        cls.connection.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_coefficient_free_seals_and_hashes(self) -> None:
        for path in (CERTIFICATE, SUMMARY, RUNBOOK):
            text = path.read_text()
            self.assertIsNone(COEFFICIENT_RE.search(text))
            self.assertFalse(json.loads(text)["coefficientMaterialIncluded"])
        self.assertEqual(
            self.summary["certificate"]["sha256"], sha256_path(CERTIFICATE)
        )
        self.assertEqual(self.summary["runbook"]["sha256"], sha256_path(RUNBOOK))

    def test_exact_14_source_boundary(self) -> None:
        boundary = self.certificate["sourceBoundary"]
        sources = boundary["sources"]
        self.assertEqual(boundary["acceptedScoreableSignatures"], 14)
        self.assertEqual(boundary["acceptedScoreableAnchorHashes"], 14)
        self.assertEqual(len(sources), 14)
        self.assertEqual(len({row["coefficientSha256"] for row in sources}), 14)
        for source in sources:
            row = self.connection.execute(
                "SELECT p.coefficient_hash,v.status,v.label,v.r,v.scoreable,v.in_baseline,"
                "v.scoring_status FROM polynomials p JOIN verifications v "
                "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
                "AND p.polynomial_index=?",
                (source["submissionId"], source["polynomialIndex"]),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["coefficient_hash"], source["coefficientSha256"])
            self.assertEqual((row["status"], row["label"], row["r"]), (
                "accepted", source["label"], source["r"]
            ))
            self.assertEqual(
                (row["scoreable"], row["in_baseline"], row["scoring_status"]),
                (1, 0, "scoreable"),
            )

    def test_exact_twist_signatures_and_source_models(self) -> None:
        expected = {"24T10408": (12, 12), "24T17207": (8, 4)}
        for source in self.runbook["sources"]:
            row = self.connection.execute(
                "SELECT coefficients FROM polynomials WHERE submission_id=? AND polynomial_index=?",
                (source["submissionId"], source["polynomialIndex"]),
            ).fetchone()
            values = [int(value) for value in row[0].split(",")]
            self.assertTrue(all(values[index] == 0 for index in range(1, 25, 2)))
            quotient_real, _ = exact_real_root_count(values[::2])
            source_r, negative_r = expected[source["label"]]
            self.assertEqual(source["r"], source_r)
            self.assertEqual(2 * quotient_real - source_r, negative_r)
            self.assertEqual(source["exactNegativeR"], negative_r)

    def test_guarded_runbook_and_builder_filter(self) -> None:
        ast.parse(BUILDER.read_text(), str(BUILDER))
        source = BUILDER.read_text()
        self.assertIn('"--source-label"', source)
        self.assertTrue(self.runbook["absentOutputGuard"]["allAbsentAtPreparation"])
        action = self.runbook["commands"]["buildTwoLabelActionSubset"]
        execute = self.runbook["commands"]["executeTwoHashPinnedTwists"]
        for path in self.runbook["absentOutputGuard"]["paths"]:
            if path.endswith("action_subset.jsonl"):
                self.assertIn(f"test ! -e {path}", action)
            else:
                self.assertIn(f"test ! -e {path}", execute)
        self.assertNotIn("submit", action.lower())
        self.assertNotIn("submit", execute.lower())
        self.assertEqual(self.runbook["sideEffectsAtPreparation"]["heavyWorkersLaunched"], 0)

    def test_closure_counts(self) -> None:
        closure = self.certificate["closure"]
        self.assertEqual(closure["alreadyExactSafeRows"], 0)
        self.assertEqual(closure["manifestsStagedByAudit"], 0)
        self.assertEqual(closure["unresolvedExecutableCachedRoutes"], 0)
        self.assertEqual(closure["guardedRunbooksPrepared"], 1)
        self.assertEqual(closure["guardedRunbookSourceHashes"], 2)
        exact = self.certificate["families"]["exactSavedCandidateRejoin"]
        for family in ("single", "stableMulti", "exactFrobenius", "unresolvedFrobenius"):
            self.assertEqual(exact[family]["hits"], 0)


if __name__ == "__main__":
    unittest.main()
