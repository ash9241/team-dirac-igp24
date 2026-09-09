from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "build_group_only_gap_resolvent_atlas.py"
FRONTIER = ROOT / "data/current_q214_separating_resolvent_frontier_20260806.jsonl"
FIXTURE = ROOT / "tests/fixtures/group_only_gap_atlas_fixture.txt"

spec = importlib.util.spec_from_file_location("group_only_atlas", MODULE_PATH)
assert spec and spec.loader
atlas_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = atlas_module
spec.loader.exec_module(atlas_module)


class GroupOnlyGapAtlasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = atlas_module.load_frontier(FRONTIER)
        cls.config = atlas_module.make_config(FRONTIER, cls.rows, 2, 2)
        cls.fixture_text = FIXTURE.read_text(encoding="utf-8")

    def test_real_frontier_is_validated_and_has_two_twelve_label_catalogs(self):
        self.assertEqual(len(self.rows), 26)
        q214 = self.config.lanes[0]
        self.assertEqual([len(catalog) for catalog in q214.catalogs], [12, 12])
        self.assertEqual(len(q214.labels), 13)  # the catalogs differ only at 24T19594/19595
        self.assertTrue(all(row["submissionReady"] is False for row in self.rows))

    def test_gap_emission_is_deterministic_group_only_and_generic(self):
        first = atlas_module.emit_gap_script(self.config)
        second = atlas_module.emit_gap_script(self.config)
        self.assertEqual(first, second)
        self.assertIn("SizeScreen([100000,]);", first)
        self.assertIn("AllBlocks(group)", first)
        self.assertIn('"signed_subset"', first)
        self.assertIn('"cross_block_pair"', first)
        self.assertIn("ConjugacyClasses(group)", first)
        self.assertIn("labels:=[17014,19727]", first)
        self.assertNotIn("candidateCoefficientLine", first)
        self.assertNotIn("submit", first.lower())

    def test_fixture_parser_and_planner_stay_explicitly_unvalidated(self):
        atlas = atlas_module.parse_gap_output(self.fixture_text, self.config, "fixture")
        self.assertFalse(atlas["validatedWithLocalGap"])
        self.assertEqual(atlas["audit"]["submissionCalls"], 0)
        self.assertEqual([len(lane["groups"]) for lane in atlas["lanes"]], [13, 2])
        plan = atlas_module.build_plan(atlas, self.config, FRONTIER, self.rows)
        self.assertFalse(plan["validatedWithLocalGap"])
        self.assertFalse(plan["submissionReady"])
        self.assertEqual(len(plan["q214"]["candidatePlans"]), 26)
        self.assertEqual(sum(row["pilotRank"] is not None
                             for row in plan["q214"]["candidatePlans"]), 5)
        separator = plan["q109MaximalComparator"]["concreteLowDegreeSeparator"]
        self.assertEqual(separator["actionDegree"], 24)
        self.assertEqual(separator["cycleType"], [4, 4, 4, 4, 8])
        self.assertEqual(separator["absentFrom"], "24T17014")
        self.assertFalse(plan["q109MaximalComparator"]["candidateArithmeticRun"])
        self.assertEqual(plan["q109MaximalComparator"]["priorLocalEvidence"]["status"],
                         "sealed_historical_evidence_only")

    def test_parser_fails_closed_on_truncation_and_order_corruption(self):
        truncated = "\n".join(self.fixture_text.splitlines()[:-1]) + "\n"
        with self.assertRaisesRegex(ValueError, "HEADER and COMPLETE"):
            atlas_module.parse_gap_output(truncated, self.config, "fixture")
        corrupted = self.fixture_text.replace("24:5308416:1", "24:5308415:1", 1)
        with self.assertRaisesRegex(ValueError, "image/kernel order theorem"):
            atlas_module.parse_gap_output(corrupted, self.config, "fixture")

    def test_gap_physical_line_wrapping_is_unfolded(self):
        wrapped = (
            "GROUP_ONLY_ATLAS_V1\tCYCLES\tq109\t24T\\\n"
            "19727\n"
            " \t1.1.2:3;4.4:5\\\n"
            ";8.8:7\n"
        )
        self.assertEqual(
            atlas_module.unfold_gap_protocol_lines(wrapped),
            ["GROUP_ONLY_ATLAS_V1\tCYCLES\tq109\t24T19727\t1.1.2:3;4.4:5;8.8:7"],
        )

    def test_cli_parses_fixture_without_gap_and_marks_outputs_unvalidated(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            command = [
                sys.executable, str(MODULE_PATH), "--parse-output", str(FIXTURE),
                "--fixture", "--max-block-subset", "2", "--max-point-subset", "2",
                "--gap-script", str(directory / "atlas.g"),
                "--atlas", str(directory / "atlas.json"),
                "--plan", str(directory / "plan.json"),
            ]
            result = subprocess.run(command, cwd=ROOT, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            event = json.loads(result.stdout)
            self.assertFalse(event["validatedWithLocalGap"])
            atlas = json.loads((directory / "atlas.json").read_text())
            plan = json.loads((directory / "plan.json").read_text())
            self.assertEqual(atlas["executionKind"], "fixture")
            self.assertIn("unvalidated", plan["status"])
            self.assertEqual(plan["audit"]["submissionCalls"], 0)


if __name__ == "__main__":
    unittest.main()
