import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAGE = Path("/usr/local/bin/sage")
SCRIPT = ROOT / "agent_f9_k4_incidence_pilot.sage.py"
class K4AllowlistPreflightTest(unittest.TestCase):
    def assert_preflight(
        self,
        selector,
        stem,
        source_label,
        source_r,
        polynomial_index,
        coefficient_sha256,
        quotient_sha256,
        target_pair,
    ):
        outputs = (
            ROOT / f"data/{stem}_results.jsonl",
            ROOT / f"data/{stem}_summary.json",
            ROOT / f"outbox/{stem}_live.txt",
        )
        before = {path: path.stat().st_mtime_ns for path in outputs if path.exists()}
        process = subprocess.run(
            [
                str(SAGE),
                "-python",
                str(SCRIPT),
                "--source",
                selector,
                "--preflight-only",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if before:
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("not overwrite existing outputs", process.stderr)
        else:
            self.assertEqual(process.returncode, 0, process.stderr)
            event = json.loads(process.stdout.strip().splitlines()[-1])
            self.assertEqual(event["event"], "k4_preflight_ok")
            self.assertEqual(event["heavyArithmeticCalls"], 0)
            self.assertEqual(event["networkCalls"], 0)
            self.assertEqual(event["submissionCalls"], 0)
            self.assertEqual(len(event["sources"]), 1)
            source = event["sources"][0]
            self.assertEqual(source["source"]["submissionId"], "sub_43f61dfb464e4f41829f5c417a1a3df5")
            self.assertEqual(source["source"]["coefficientSha256"], coefficient_sha256)
            self.assertEqual(source["source"]["label"], source_label)
            self.assertEqual(source["source"]["r"], source_r)
            self.assertEqual(source["source"]["polynomialIndex"], polynomial_index)
            self.assertEqual(source["sourceQuotientPolynomialSha256"], quotient_sha256)
            target = source["eligibleTargetStates"][target_pair]
            self.assertEqual(target["teamCount"], 0)
            self.assertFalse(target["discovered"])
            self.assertFalse(target["baseline"])
            self.assertFalse(target["owned"])
            self.assertIsNone(target["minimumDiscAbs"])

        after = {path: path.stat().st_mtime_ns for path in outputs if path.exists()}
        self.assertEqual(after, before)

    def test_18462_r4_is_fail_closed_and_preflight_is_nonwriting(self):
        self.assert_preflight(
            "24T18462/r4",
            "agent_f9_k4_18462_r4",
            "24T18462",
            4,
            116,
            "9feb41587c0e6993fcd150a63ff3fa2e21662db36776384eddee8cc873b97851",
            "1c906fbd771fade0f33d0c9f5e554a9a6d4d2f93611a8430250d5ae85325495a",
            "24T6120/r16",
        )

    def test_18462_r20_is_fail_closed_and_preflight_is_nonwriting(self):
        self.assert_preflight(
            "24T18462/r20",
            "agent_f9_k4_18462_r20",
            "24T18462",
            20,
            364,
            "2ea56946883b19fcb1b723435a40061fd8bede6fec9130129b6fcc62fd2604fa",
            "db7948b8e72ad1cb9b8051b66e67c5e0ddc99c2730089b36e2560e67bee7067e",
            "24T6120/r16",
        )

    def test_10428_r12_multiplicity4_preflight_is_nonwriting(self):
        self.assert_preflight(
            "24T10428/r12",
            "agent_f9_k4_10428_r12",
            "24T10428",
            12,
            953,
            "52b7bd77695a5a248926dbbf2eb3628a1ac5bbacaf7e9894bf69b296cd81f5ec",
            "4a0b158b7c2d01559eccad4c6b832ca58d366d0aab2fb00091f6dbe8774434bc",
            "24T3514/r16",
        )

    def test_19712_r16_multiplicity2_overlap_preflight_is_nonwriting(self):
        self.assert_preflight(
            "24T19712/r16",
            "agent_f9_k4_19712_r16",
            "24T19712",
            16,
            873,
            "722d18b5990ef20fee43a92a05630850d1b39aaf921921d352289d5c4d143f13",
            "d1a9a5e82b034e76ad641692698e4288d96452c6e46470315c3129c0384d2436",
            "24T15335/r20",
        )


if __name__ == "__main__":
    unittest.main()
