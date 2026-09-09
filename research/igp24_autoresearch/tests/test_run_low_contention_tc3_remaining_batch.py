from __future__ import annotations

import unittest
from pathlib import Path

import run_low_contention_remaining_batch as exact
import run_low_contention_sequential as lane
import run_low_contention_tc3_remaining_batch as batch


class RemainingTc3BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = lane.read_json(batch.ROUTE_CERTIFICATE)
        cls.routes = cls.certificate["runbooks"][1:]

    def test_exactly_twenty_two_remaining_tc3_routes(self) -> None:
        self.assertEqual(len(self.certificate["runbooks"]), 23)
        self.assertEqual(len(self.routes), 22)
        self.assertTrue(all(row["target"]["teamCountAtSeal"] == 3 for row in self.routes))

    def test_required_hc3_001_receipt_is_intact(self) -> None:
        self.assertEqual(len(exact.required_receipt_hashes(batch.REQUIRED_RECEIPT)), 1)

    def test_all_remaining_artifact_states_are_fail_closed(self) -> None:
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

    def test_no_network_or_commit_path(self) -> None:
        source = Path(batch.__file__).read_text(encoding="utf-8")
        self.assertNotIn("api_json(", source)
        self.assertNotIn('"--commit"', source)
        self.assertNotIn("command_submit", source)


if __name__ == "__main__":
    unittest.main()
