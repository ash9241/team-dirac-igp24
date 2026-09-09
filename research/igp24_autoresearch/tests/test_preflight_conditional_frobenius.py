import json
import unittest
from pathlib import Path

import preflight_conditional_frobenius as preflight


ROOT = Path(__file__).resolve().parents[1]
RUNBOOKS = sorted((ROOT / "data").glob("conditional_multi_*_r*_runbook.json"))


class ConditionalFrobeniusPreflightTests(unittest.TestCase):
    def test_runbook_is_coefficient_free_and_guarded(self):
        self.assertGreaterEqual(len(RUNBOOKS), 4)
        for runbook in RUNBOOKS:
            with self.subTest(runbook=runbook.name):
                text = runbook.read_text(encoding="utf-8")
                self.assertNotIn("coefficientLine", text)
                payload = json.loads(text)
                self.assertTrue(payload["commands"]["frobenius"].startswith("test ! -e "))
                self.assertTrue(payload["commands"]["stage"].startswith("test ! -e "))

    def test_live_preflight_reproduces_two_way_gold_range(self):
        for runbook in RUNBOOKS:
            with self.subTest(runbook=runbook.name):
                payload = preflight.load_runbook(runbook)
                result = preflight.analyze(payload, require_absent_output=False)
                self.assertEqual(result["anchoredCandidates"], 1)
                self.assertEqual(result["survivingAssignments"], 2)
                self.assertEqual(result["positiveAssignments"], 1)
                self.assertEqual(result["scoreMinimumExact"], "0")
                self.assertEqual(result["scoreMaximumExact"], "1")


if __name__ == "__main__":
    unittest.main()
