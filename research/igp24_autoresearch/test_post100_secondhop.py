from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
import unittest
from fractions import Fraction
from pathlib import Path

import stage_single_exact_census as single


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "ledger.sqlite3"
RUNBOOK = DATA / "post100_secondhop_15578_r4_f5_runbook.json"
AUDIT = DATA / "post100_secondhop_audit_certificate.json"


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class Post100SecondHopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runbook = json.loads(RUNBOOK.read_text())
        cls.audit = json.loads(AUDIT.read_text())
        cls.connection = sqlite3.connect(f"file:{DB.resolve()}?mode=ro", uri=True)
        cls.connection.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_source_receipt_and_ledger_provenance(self) -> None:
        source = self.runbook["source"]
        provenance = self.runbook["sourceProvenance"]
        for key in ("construction", "receipt"):
            pinned = provenance[key]
            self.assertEqual(sha256_path(ROOT / pinned["path"]), pinned["sha256"])
        construction = json.loads((ROOT / provenance["construction"]["path"]).read_text())
        receipt = json.loads((ROOT / provenance["receipt"]["path"]).read_text())
        self.assertFalse(construction["coefficientMaterialIncluded"])
        self.assertEqual(construction["target"]["coefficientSha256"], source["coefficientSha256"])
        self.assertEqual(receipt["manifestHash"], construction["manifest"]["sha256"])
        self.assertTrue(receipt["commit"])
        self.assertEqual(receipt["knownLocalHashes"], 0)
        self.assertEqual(receipt["response"]["submissionId"], source["submissionId"])
        self.assertEqual(receipt["response"]["rejectedCount"], 0)
        row = self.connection.execute(
            "SELECT p.coefficient_hash,v.status,v.label,v.r,v.scoreable,v.in_baseline,"
            "v.scoring_status FROM polynomials p JOIN verifications v "
            "USING(submission_id,polynomial_index) WHERE p.submission_id=? "
            "AND p.polynomial_index=?",
            (source["submissionId"], source["polynomialIndex"]),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["coefficient_hash"], source["coefficientSha256"])
        self.assertEqual((row["status"], row["label"], row["r"]), ("accepted", "24T15578", 4))
        self.assertEqual((row["scoreable"], row["in_baseline"], row["scoring_status"]), (1, 0, "scoreable"))

    def test_unique_cached_action(self) -> None:
        exact = self.runbook["exactAction"]
        matches = []
        for pinned in self.runbook["inputs"]["pairProductActionShards"]:
            path = ROOT / pinned["path"]
            self.assertEqual(sha256_path(path), pinned["sha256"])
            matches.extend(row for row in jsonl(path) if row.get("sourceLabel") == "24T15578")
        self.assertEqual(len(matches), 1)
        action = matches[0]
        digest = hashlib.sha256(
            json.dumps(action, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        self.assertEqual(digest, exact["actionRowSha256"])
        self.assertEqual(action["targetLabel"], "24T9490")
        self.assertEqual(len(action["pairOrbit"]), 12)
        self.assertEqual(
            set(action["sourceSignatureToPossibleTargetSignatures"]["4"]),
            {0, 4, 8, 12, 16},
        )
        twist_pin = self.runbook["inputs"]["twistActionMap"]
        self.assertEqual(sha256_path(ROOT / twist_pin["path"]), twist_pin["sha256"])
        twist = [row for row in jsonl(ROOT / twist_pin["path"]) if row.get("sourceLabel") == "24T15578"]
        self.assertEqual(len(twist), 1)
        self.assertEqual(twist[0]["systemCount"], 1)

    def test_all_compatible_outcomes_are_currently_safe(self) -> None:
        _, receipt_pairs, _ = single.receipt_exclusions(
            ROOT / "receipts", DATA, self.connection, {}
        )
        expected = {
            int(row["r"]): (int(row["teamCount"]), row["marginalScoreExact"])
            for row in self.runbook["compatibleOutcomesAtPreparation"]
        }
        self.assertEqual(set(expected), {0, 4, 8, 12, 16})
        for signature, (team_count, score) in expected.items():
            pair = ("24T9490", signature)
            target = self.connection.execute(
                "SELECT team_count FROM targets WHERE label=? AND r=?", pair
            ).fetchone()
            self.assertIsNotNone(target)
            self.assertEqual(target["team_count"], team_count)
            self.assertEqual(str(Fraction(1, 2**team_count)), score)
            self.assertFalse(
                self.connection.execute(
                    "SELECT 1 FROM baseline_pairs WHERE label=? AND r=?", pair
                ).fetchone()
            )
            self.assertFalse(
                self.connection.execute(
                    "SELECT 1 FROM verifications WHERE label=? AND r=? LIMIT 1", pair
                ).fetchone()
            )
            self.assertNotIn(pair, receipt_pairs)

    def test_worker_and_absent_output_guard_are_sealed(self) -> None:
        worker = self.runbook["inputs"]["worker"]
        worker_path = ROOT / worker["path"]
        self.assertEqual(sha256_path(worker_path), worker["sha256"])
        ast.parse(worker_path.read_text(), str(worker_path))
        paths = [ROOT / path for path in self.runbook["absentOutputGuard"]["paths"]]
        present = [path.exists() for path in paths]
        self.assertIn(sum(present), (0, len(paths)), "isolated outputs are partially present")
        command = self.runbook["commands"]["execute"]
        for path in self.runbook["absentOutputGuard"]["paths"]:
            self.assertIn(f"test ! -e {path}", command)
        self.assertIn("deterministic tc1 worker", " ".join(self.runbook["prerequisites"]))

    def test_audit_and_runbook_are_coefficient_free(self) -> None:
        pattern = re.compile(r"(?<![0-9])-?[0-9]+(?:,-?[0-9]+){24}(?![0-9])")
        for path in (RUNBOOK, AUDIT):
            text = path.read_text()
            self.assertIsNone(pattern.search(text))
            value = json.loads(text)
            self.assertFalse(value["coefficientMaterialIncluded"])
        self.assertEqual(self.audit["recommendedNextRun"]["runbook"]["sha256"], sha256_path(RUNBOOK))


if __name__ == "__main__":
    unittest.main()
