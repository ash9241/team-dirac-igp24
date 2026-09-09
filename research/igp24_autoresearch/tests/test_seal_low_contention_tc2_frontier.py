from __future__ import annotations

import unittest
from pathlib import Path

import run_low_contention_sequential as lane
import seal_low_contention_tc2_frontier as seal


class SealLowContentionTc2FrontierTests(unittest.TestCase):
    def test_two_receipts_are_intact_one_plus_thirteen(self) -> None:
        receipts, by_hash = seal.load_receipts()
        self.assertEqual(set(receipts), set(seal.RECEIPT_IDS))
        self.assertEqual(receipts[seal.FIRST_RECEIPT]["manifest"]["polynomials"], 1)
        self.assertEqual(receipts[seal.TAIL_RECEIPT]["manifest"]["polynomials"], 13)
        self.assertEqual(len(by_hash), 14)

    def test_route_frontier_has_fourteen_complete_artifact_chains(self) -> None:
        certificate = lane.read_json(seal.ROUTE_CERTIFICATE)
        self.assertEqual(len(certificate["runbooks"]), 14)
        for row in certificate["runbooks"]:
            paths = lane.planned_paths(row)
            self.assertTrue(paths["result"].is_file())
            self.assertTrue(paths["postflight"].is_file())
            self.assertTrue(paths["manifest"].is_file())
            self.assertTrue(paths["stageCertificate"].is_file())

    def test_sealer_has_no_network_or_heavy_path(self) -> None:
        source = Path(seal.__file__).read_text(encoding="utf-8")
        self.assertNotIn("api_json(", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn('"--commit"', source)


if __name__ == "__main__":
    unittest.main()
