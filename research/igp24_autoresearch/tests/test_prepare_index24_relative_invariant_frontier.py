import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "prepare_index24_relative_invariant_frontier.py"
OUTPUTS = (
    ROOT / "data/index24_relative_invariant_frontier_20260722_plan.json",
    ROOT / "data/index24_relative_invariant_frontier_20260722_summary.json",
)


class Index24RelativeInvariantFrontierTest(unittest.TestCase):
    def test_light_preflight_is_coefficient_free_and_nonwriting(self):
        before = {path: path.stat().st_mtime_ns for path in OUTPUTS if path.exists()}
        process = subprocess.run(
            ["python3", str(SCRIPT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        event = json.loads(process.stdout.strip().splitlines()[-1])
        self.assertEqual(event["event"], "index24_relative_invariant_frontier_ok")
        self.assertEqual(event["liveDeterministicRoutes"], 7)
        self.assertEqual(event["distinctLiveTargetPairs"], 6)
        self.assertEqual(event["lowerKummerSubsetProductSafeRows"], 0)
        self.assertEqual(event["heavyArithmeticCalls"], 0)
        self.assertEqual(event["networkCalls"], 0)
        self.assertEqual(event["submissionCalls"], 0)
        self.assertFalse(event["coefficientMaterialIncluded"])
        self.assertFalse(event["writePlan"])
        self.assertNotIn("coefficientLine", process.stdout)
        self.assertNotIn('"coefficients"', process.stdout)
        after = {path: path.stat().st_mtime_ns for path in OUTPUTS if path.exists()}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
