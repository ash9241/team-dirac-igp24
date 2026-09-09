from __future__ import annotations

import unittest
from pathlib import Path

import run_low_contention_sequential as lane
import seal_low_contention_tc1_frontier as seal


class SealLowContentionTc1FrontierTests(unittest.TestCase):
    def test_expected_mapping_is_exactly_twelve_routes_seven_receipts(self) -> None:
        self.assertEqual(len(seal.EXPECTED_ROUTE_RECEIPTS), 12)
        self.assertEqual(len(set(seal.EXPECTED_ROUTE_RECEIPTS.values())), 7)
        self.assertEqual(set(seal.EXPECTED_ROUTE_RECEIPTS.values()), set(seal.RECEIPT_IDS))

    def test_every_route_result_postflight_and_receipt_exists(self) -> None:
        routes = lane.route_map(lane.read_json(lane.DEFAULT_CERTIFICATE))
        self.assertEqual(set(routes), set(seal.EXPECTED_ROUTE_RECEIPTS))
        for route_id, route in routes.items():
            paths = lane.planned_paths(route)
            self.assertTrue(paths["result"].is_file(), route_id)
            self.assertTrue(paths["postflight"].is_file(), route_id)
        for submission_id in seal.RECEIPT_IDS:
            self.assertTrue((lane.RECEIPTS / f"{submission_id}.json").is_file())

    def test_sealer_has_no_network_or_heavy_path(self) -> None:
        source = Path(seal.__file__).read_text(encoding="utf-8")
        self.assertNotIn("api_json(", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn('"--commit"', source)
        self.assertNotIn("/usr/local/bin/sage", source)


if __name__ == "__main__":
    unittest.main()
