from __future__ import annotations

import unittest
from pathlib import Path

import run_low_contention_remaining_batch as batch
import run_low_contention_sequential as lane


class RemainingLowContentionBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _plan, _certificate, planned = lane.validate_plan(
            lane.DEFAULT_PLAN, lane.DEFAULT_CERTIFICATE
        )
        cls.by_id = {row["routeId"]: row for row in planned}

    def test_route_order_is_exact_remaining_sealed_tail(self) -> None:
        self.assertEqual(len(batch.ROUTE_IDS), 6)
        self.assertEqual(len(set(batch.ROUTE_IDS)), 6)
        self.assertTrue(all(route_id in self.by_id for route_id in batch.ROUTE_IDS))
        planned_order = [row["routeId"] for row in self.by_id.values()]
        self.assertEqual(
            [route_id for route_id in planned_order if route_id in batch.ROUTE_IDS],
            list(batch.ROUTE_IDS),
        )

    def test_every_worker_command_remains_exactly_pinned(self) -> None:
        for route_id in batch.ROUTE_IDS:
            lane.validate_heavy_command(self.by_id[route_id])

    def test_required_newest_receipt_is_intact(self) -> None:
        self.assertEqual(len(batch.required_receipt_hashes(batch.REQUIRED_RECEIPT)), 1)

    def test_batch_has_no_network_or_commit_path(self) -> None:
        source = Path(batch.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"--commit"', source)
        self.assertNotIn("api_json(", source)
        self.assertNotIn("command_submit", source)

    def test_combined_artifacts_are_project_scoped(self) -> None:
        self.assertTrue(batch.BATCH_MANIFEST.is_relative_to(lane.OUTBOX))
        self.assertTrue(batch.BATCH_CERTIFICATE.is_relative_to(lane.DATA))


if __name__ == "__main__":
    unittest.main()
