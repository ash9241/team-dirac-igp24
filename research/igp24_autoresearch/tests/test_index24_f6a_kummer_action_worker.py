import importlib.util
import json
import math
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLANNER = ROOT / "prepare_index24_f6a_kummer_action_plan.py"
WORKER = ROOT / "run_index24_f6a_kummer_action_worker.sage.py"
PLAN_OUTPUT = ROOT / "data/index24_f6a_kummer_action_plan.json"
CHECKPOINT = ROOT / "data/index24_f6a_kummer_action_checkpoint.json"
CENSUS = ROOT / "data/index24_f6a_kummer_action_census.json"


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    specification.loader.exec_module(module)
    return module


class Index24F6AKummerActionWorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.light = load_module("index24_f6a_kummer_light", PLANNER)
        cls.worker = load_module("index24_f6a_kummer_worker", WORKER)

    def test_planner_preflight_is_nonwriting_coefficient_free_and_sage_free(self):
        before = PLAN_OUTPUT.stat().st_mtime_ns if PLAN_OUTPUT.exists() else None
        process = subprocess.run(
            ["python3", str(PLANNER), "--preflight-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        event = json.loads(process.stdout.strip().splitlines()[-1])
        self.assertEqual(event["event"], "index24_f6a_kummer_action_preflight_ok")
        self.assertEqual(event["freshTasks"], 55)
        self.assertEqual(event["excludedTasks"], 22)
        self.assertTrue(event["actionOnly"])
        self.assertEqual(event["heavyArithmeticCalls"], 0)
        self.assertEqual(event["networkCalls"], 0)
        self.assertEqual(event["submissionCalls"], 0)
        self.assertFalse(event["coefficientMaterialIncluded"])
        self.assertFalse(event["writePlan"])
        self.assertNotIn('"coefficients"', process.stdout)
        self.assertNotIn("coefficientLine", process.stdout)
        after = PLAN_OUTPUT.stat().st_mtime_ns if PLAN_OUTPUT.exists() else None
        self.assertEqual(after, before)

    def test_worker_preflight_is_nonwriting_coefficient_free_and_sage_free(self):
        before = {
            path: path.stat().st_mtime_ns
            for path in (CHECKPOINT, CENSUS)
            if path.exists()
        }
        process = subprocess.run(
            ["python3", str(WORKER), "--preflight-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        event = json.loads(process.stdout.strip().splitlines()[-1])
        self.assertEqual(
            event["event"], "index24_f6a_kummer_action_worker_preflight_ok"
        )
        self.assertEqual(event["freshTasks"], 55)
        self.assertEqual(event["excludedTasks"], 22)
        self.assertTrue(event["actionOnly"])
        self.assertEqual(event["heavyArithmeticCalls"], 0)
        self.assertEqual(event["networkCalls"], 0)
        self.assertEqual(event["submissionCalls"], 0)
        self.assertFalse(event["coefficientMaterialIncluded"])
        self.assertEqual(event["resourceGates"]["maxSubsetUniverse"], 924)
        self.assertNotIn('"coefficients"', process.stdout)
        self.assertNotIn("coefficientLine", process.stdout)
        after = {
            path: path.stat().st_mtime_ns
            for path in (CHECKPOINT, CENSUS)
            if path.exists()
        }
        self.assertEqual(after, before)

    def test_no_mode_fails_closed(self):
        for script in (PLANNER, WORKER):
            process = subprocess.run(
                ["python3", str(script)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(process.returncode, 0)

    def test_plan_scopes_only_fresh_source_k_tasks(self):
        plan = self.light.build_plan()
        self.assertEqual(plan["schemaVersion"], "index24-f6a-kummer-action-plan-v1")
        self.assertTrue(plan["actionOnly"])
        self.assertEqual(plan["heavyArithmeticCalls"], 0)
        self.assertEqual(len(plan["tasks"]), 55)
        self.assertEqual(len(plan["exclusions"]), 22)

        sizes_by_source = {}
        for task in plan["tasks"]:
            sizes_by_source.setdefault(task["sourceLabel"], set()).add(task["subsetSize"])
            self.assertEqual(
                task["factorEnumerationDegree"], math.comb(12, task["subsetSize"])
            )
            self.assertLessEqual(task["factorEnumerationDegree"], 924)
            self.assertEqual(task["quotientOrbitLengthRequired"], 12)
            self.assertEqual(task["inducedActionDegree"], 24)
        for label in ("24T5786", "24T6436", "24T11202", "24T14141", "24T14292"):
            self.assertEqual(sizes_by_source[label], set(range(2, 12)))
        self.assertEqual(sizes_by_source["24T10913"], set(range(7, 12)))
        self.assertNotIn("24T12043", sizes_by_source)
        self.assertTrue(all(1 not in sizes for sizes in sizes_by_source.values()))

        excluded = {
            (row["sourceLabel"], row["subsetSize"], row["type"])
            for row in plan["exclusions"]
        }
        self.assertTrue(
            all(
                ("24T12043", k, "theorem_closed_source_route") in excluded
                for k in range(1, 12)
            )
        )
        self.assertTrue(
            all(
                ("24T10913", k, "prior_exact_negative") in excluded
                for k in range(2, 7)
            )
        )

    def test_plan_has_exact_safe_action_and_root_alignment_contracts(self):
        plan = self.light.build_plan()
        pairs = {
            (target["label"], target["r"])
            for route in plan["structuralRoutes"]
            for target in route["targets"]
        }
        self.assertEqual(
            pairs,
            {
                ("24T5653", 8),
                ("24T5653", 16),
                ("24T6910", 16),
                ("24T10916", 20),
                ("24T11204", 8),
                ("24T11204", 16),
                ("24T11566", 16),
                ("24T14142", 16),
            },
        )
        contract = plan["dispatcherContractAfterExactActionHit"]
        self.assertIn("every degree-12 component", contract["factorSelection"])
        self.assertIn("no unproved root numbering", contract["rootActionAlignment"])
        self.assertTrue(
            any("exact Galois label" in gate for gate in contract["finalExactGates"])
        )
        self.assertTrue(
            any("monic irreducible of degree 24" in gate for gate in contract["finalExactGates"])
        )
        self.assertIn("exact TransitiveIdentification", plan["arithmeticAuthorizationRule"])

        rendered = json.dumps(plan, sort_keys=True)
        self.assertNotIn('"coefficients"', rendered)
        self.assertNotIn("coefficientLine", rendered)
        self.light.assert_coefficient_free(plan)

    def test_plan_hash_is_stable_across_mutable_age_checks(self):
        first = self.worker.stable_plan_sha256(self.light.build_plan())
        second = self.worker.stable_plan_sha256(self.light.build_plan())
        self.assertEqual(first, second)

    def test_subset_orbits_and_binary_rank_are_exact_without_sage(self):
        identity = list(range(12))
        orbits = self.worker.subset_orbits([identity], 1, 924)
        self.assertEqual(len(orbits), 12)
        self.assertTrue(all(len(orbit) == 1 for orbit in orbits))
        self.assertEqual(self.worker.binary_rank([[1, 0], [0, 1]]), 2)
        self.assertEqual(self.worker.binary_rank([[1, 1], [1, 1]]), 1)
        with self.assertRaises(ResourceWarning):
            self.worker.subset_orbits([identity], 6, 923)

    def test_resource_gates_fail_closed(self):
        process = subprocess.run(
            [
                "python3",
                str(WORKER),
                "--preflight-only",
                "--max-wall-seconds",
                "3601",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(process.returncode, 0)
        process = subprocess.run(
            [
                "python3",
                str(WORKER),
                "--preflight-only",
                "--max-subset-universe",
                "923",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
