from __future__ import annotations

import unittest
from pathlib import Path

import run_low_contention_remaining_batch as exact_batch
import run_low_contention_sequential as lane
import run_low_contention_tc2_remaining_batch as batch


class RemainingTc2BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = lane.read_json(batch.ROUTE_CERTIFICATE)
        cls.routes = cls.certificate["runbooks"][1:]

    def test_exactly_thirteen_remaining_routes(self) -> None:
        self.assertEqual(len(self.certificate["runbooks"]), 14)
        self.assertEqual(len(self.routes), 13)
        self.assertTrue(all(row["target"]["teamCountAtSeal"] == 2 for row in self.routes))

    def test_required_lc2_001_receipt_is_intact(self) -> None:
        self.assertEqual(len(exact_batch.required_receipt_hashes(batch.REQUIRED_RECEIPT)), 1)

    def test_every_remaining_output_state_is_fail_closed(self) -> None:
        for row in self.routes:
            state = lane.artifact_state(row)
            self.assertFalse(state["resultTemporary"], (row["routeId"], state))
            if state["result"]:
                self.assertTrue(state["postflight"], row["routeId"])
                self.assertTrue(state["manifest"], row["routeId"])
                self.assertTrue(state["stageCertificate"], row["routeId"])
            else:
                self.assertFalse(
                    state["postflight"] or state["manifest"] or state["stageCertificate"],
                    (row["routeId"], state),
                )

    def test_batch_has_no_network_or_commit_path(self) -> None:
        source = Path(batch.__file__).read_text(encoding="utf-8")
        self.assertNotIn("api_json(", source)
        self.assertNotIn('"--commit"', source)
        self.assertNotIn("command_submit", source)


if __name__ == "__main__":
    unittest.main()
