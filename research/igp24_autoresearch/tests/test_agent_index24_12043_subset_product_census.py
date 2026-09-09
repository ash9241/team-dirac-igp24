import importlib.util
import hashlib
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "agent_index24_12043_subset_product_census.sage.py"
OUTPUT = ROOT / "data/agent_index24_12043_subset_product_census.json"


class Index24Source12043SubsetProductCensusTest(unittest.TestCase):
    def test_python_preflight_is_light_nonwriting_and_coefficient_free(self):
        before = OUTPUT.stat().st_mtime_ns if OUTPUT.exists() else None
        process = subprocess.run(
            ["python3", str(SCRIPT), "--preflight-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        event = json.loads(process.stdout.strip().splitlines()[-1])
        self.assertEqual(event["event"], "index24_12043_subset_product_preflight_ok")
        self.assertEqual(event["source"]["label"], "24T12043")
        self.assertEqual(event["source"]["r"], 24)
        self.assertTrue(event["source"]["fullyAdjudicated"])
        self.assertEqual(event["target"]["label"], "24T12067")
        self.assertEqual(event["target"]["r"], 24)
        state = event["target"]["state"]
        self.assertTrue(state["fresh"])
        self.assertEqual(state["teamCount"], 0)
        self.assertFalse(state["baseline"])
        self.assertFalse(state["owned"])
        self.assertEqual(event["heavyArithmeticCalls"], 0)
        self.assertEqual(event["networkCalls"], 0)
        self.assertEqual(event["submissionCalls"], 0)
        self.assertFalse(event["candidateCoefficientMaterialIncluded"])
        self.assertNotIn('"coefficients"', process.stdout)
        self.assertNotIn("coefficientLine", process.stdout)
        after = OUTPUT.stat().st_mtime_ns if OUTPUT.exists() else None
        self.assertEqual(after, before)

    def test_no_mode_fails_closed(self):
        process = subprocess.run(
            ["python3", str(SCRIPT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(process.returncode, 0)

    def test_subset_orbit_helper_is_exact_and_sage_free(self):
        specification = importlib.util.spec_from_file_location("source12043_census", SCRIPT)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        identity = list(range(12))
        orbits = module.subset_orbits([identity], 1)
        self.assertEqual(len(orbits), 12)
        self.assertTrue(all(len(orbit) == 1 for orbit in orbits))

    def test_mutable_state_recheck_uses_named_rows_and_live_guards(self):
        specification = importlib.util.spec_from_file_location("source12043_recheck", SCRIPT)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        source, state = module.mutable_state_recheck()
        self.assertEqual(source["label"], "24T12043")
        self.assertTrue(source["fullyAdjudicated"])
        self.assertTrue(state["fresh"])
        self.assertEqual(state["teamCount"], 0)
        self.assertFalse(state["baseline"])
        self.assertFalse(state["owned"])

    def test_published_census_is_exact_fail_closed_certificate(self):
        if not OUTPUT.exists():
            self.skipTest("isolated census artifact has not been launched yet")
        raw = OUTPUT.read_bytes()
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            "07b1c3e1b2276d0692dba10121278bc3686cce14d2fd8a741cf5c59a9e2d5977",
        )
        payload = json.loads(raw)
        self.assertEqual(payload["status"], "certified_no_exact_subset_product_invariant")
        self.assertEqual(payload["exactExecutableHitCount"], 0)
        self.assertIsNone(payload["executableInvariant"])
        self.assertEqual(len(payload["actionRows"]), 9)
        self.assertFalse(any(row.get("targetT") == 12067 for row in payload["actionRows"]))
        self.assertEqual(
            {row["subsetSize"] for row in payload["actionRows"]},
            {1, 2, 3, 4, 6, 8, 9, 10, 11},
        )
        rendered = raw.decode()
        self.assertNotIn('"coefficients"', rendered)
        self.assertNotIn("coefficientLine", rendered)


if __name__ == "__main__":
    unittest.main()
