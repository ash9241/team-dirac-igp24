import importlib.util
import itertools
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "agent_index24_12043_reynolds_preflight.py"
GROUP_ADAPTER = ROOT / "agent_index24_12043_reynolds_group_adapter.sage.py"
RUNBOOK = ROOT / "data/agent_index24_12043_reynolds_invariant_runbook.json"
PLANNED_GROUP_OUTPUT = ROOT / "data/agent_index24_12043_reynolds_group_certificate.json"


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    specification.loader.exec_module(module)
    return module


class Index24Source12043ReynoldsPreflightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module("source12043_reynolds_preflight", PREFLIGHT)

    def test_light_preflight_is_nonwriting_and_fail_closed(self):
        before = {
            path: path.stat().st_mtime_ns
            for path in (RUNBOOK, PLANNED_GROUP_OUTPUT)
            if path.exists()
        }
        process = subprocess.run(
            ["python3", str(PREFLIGHT), "--preflight-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        event = json.loads(process.stdout.strip().splitlines()[-1])
        self.assertEqual(event["event"], "index24_12043_reynolds_preflight_ok")
        self.assertEqual(event["source"]["label"], "24T12043")
        self.assertEqual(event["target"]["label"], "24T12067")
        self.assertTrue(event["formalCosetActionFaithful"])
        self.assertIsNone(event["executableInvariant"])
        self.assertEqual(event["heavyArithmeticCalls"], 0)
        self.assertEqual(event["networkCalls"], 0)
        self.assertEqual(event["submissionCalls"], 0)
        self.assertFalse(event["candidateCoefficientMaterialIncluded"])
        self.assertIn("exactRootActionAlignment", event["unprovedExecutionGates"])
        self.assertIn("specializedConjugatesDistinct", event["unprovedExecutionGates"])
        self.assertNotIn("coefficientLine", process.stdout)
        self.assertNotIn('"coefficients"', process.stdout)
        after = {
            path: path.stat().st_mtime_ns
            for path in (RUNBOOK, PLANNED_GROUP_OUTPUT)
            if path.exists()
        }
        self.assertEqual(after, before)

    def test_group_adapter_preflight_does_not_import_sage_or_write(self):
        before = PLANNED_GROUP_OUTPUT.stat().st_mtime_ns if PLANNED_GROUP_OUTPUT.exists() else None
        process = subprocess.run(
            ["python3", str(GROUP_ADAPTER), "--preflight-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        event = json.loads(process.stdout.strip().splitlines()[-1])
        self.assertEqual(
            event["event"], "index24_12043_reynolds_group_adapter_preflight_ok"
        )
        self.assertEqual(event["maximumSquarefreeDegree"], 4)
        self.assertEqual(event["heavyArithmeticCalls"], 0)
        self.assertIsNone(event["executableInvariant"])
        after = PLANNED_GROUP_OUTPUT.stat().st_mtime_ns if PLANNED_GROUP_OUTPUT.exists() else None
        self.assertEqual(after, before)

    def test_no_mode_fails_closed(self):
        for script in (PREFLIGHT, GROUP_ADAPTER):
            process = subprocess.run(
                ["python3", str(script)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(process.returncode, 0)

    def test_runbook_is_current_coefficient_free_and_nonexecutable(self):
        observed = json.loads(RUNBOOK.read_text())
        expected = self.module.build_runbook()
        self.assertEqual(observed, expected)
        self.assertFalse(observed["coefficientMaterialIncluded"])
        self.assertIsNone(observed["executableInvariant"])
        self.assertEqual(observed["priorityRank"], 1)
        action = observed["abstractActionCertificate"]
        self.assertEqual(action["sourceGroupOrder"], 12288)
        self.assertEqual(action["subgroupOrder"], 512)
        self.assertEqual(action["subgroupIndex"], 24)
        self.assertTrue(action["cosetActionFaithful"])
        self.assertEqual(action["cosetActionTarget"], "24T12067")
        rendered = RUNBOOK.read_text()
        self.assertNotIn("coefficientLine", rendered)
        self.assertNotIn('"coefficients"', rendered)

    def test_universal_seed_is_minimal_in_its_stated_scope(self):
        seed = self.module.universal_seed_exponents(24)
        self.assertEqual(len(seed), 24)
        self.assertEqual(len(set(seed)), 24)
        self.assertEqual(min(seed), 0)
        self.assertEqual(max(seed), 23)
        self.assertEqual(sum(seed), 276)
        certificate = self.module.universal_seed_certificate()
        self.assertEqual(certificate["seedStabilizerOrder"], 1)
        self.assertEqual(certificate["totalDegree"], 276)

    def test_reynolds_support_lemma_on_exact_toy_group(self):
        group = set(itertools.permutations(range(3)))
        subgroup = {permutation for permutation in group if permutation[0] == 0}
        certificate = self.module.exact_reynolds_support_certificate(
            (0, 1, 2), group, subgroup
        )
        self.assertEqual(len(group), 6)
        self.assertEqual(len(subgroup), 2)
        self.assertEqual(certificate["seedStabilizerOrder"], 1)
        self.assertEqual(certificate["supportCardinality"], 2)
        self.assertEqual(certificate["supportStabilizerOrder"], 2)
        self.assertEqual(certificate["cosetConjugateCount"], 3)
        self.assertTrue(certificate["formalConjugatesDistinct"])
        with self.assertRaises(ValueError):
            self.module.exact_reynolds_support_certificate((0, 0, 0), group, subgroup)

    def test_specialization_adapter_requires_every_exact_gate(self):
        certificate = {
            "exactRootActionAlignment": True,
            "formalSupportStabilizerExact": True,
            "resolventCoefficientsExactIntegers": True,
            "specializedConjugatesDistinct": True,
            "resolventIrreducible": True,
            "sourceReceiptRechecked": True,
            "targetStateRechecked": True,
            "resolventDegree": 24,
            "resolventRealRoots": 24,
            "resolventGaloisOrder": 12288,
            "resolventGaloisLabel": "24T12067",
            "resolventDiscriminantNonzero": True,
        }
        self.module.validate_specialization_certificate(certificate)
        certificate["exactRootActionAlignment"] = False
        with self.assertRaises(ValueError):
            self.module.validate_specialization_certificate(certificate)


if __name__ == "__main__":
    unittest.main()
