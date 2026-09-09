import json
import unittest
from pathlib import Path

import seal_conditional_multi_exact_misses as seal


ROOT = Path(__file__).resolve().parents[1]


class ConditionalMultiExactMissSealTests(unittest.TestCase):
    def test_live_rebuild_seals_eight_coefficient_free_misses(self):
        payload = seal.build_certificate()
        rendered = json.dumps(payload, sort_keys=True)
        self.assertEqual(payload["summary"]["exactMisses"], 8)
        self.assertEqual(payload["summary"]["liveRows"], 0)
        self.assertEqual(len(payload["rows"]), 8)
        self.assertTrue(all(row["manifestAbsent"] for row in payload["rows"]))
        self.assertEqual(
            payload["rootSignatureInference"]["duplicateLabelExceptionalRootMisses"],
            6,
        )
        self.assertEqual(
            payload["rootSignatureInference"]["targetedPositiveBranchesRealized"],
            0,
        )
        self.assertNotIn("coefficientLine", rendered)
        self.assertNotIn("coefficients", rendered)


if __name__ == "__main__":
    unittest.main()
