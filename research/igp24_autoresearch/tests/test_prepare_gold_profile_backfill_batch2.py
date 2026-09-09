from __future__ import annotations

import json
import unittest
from pathlib import Path

import audit_low_contention_pair_routes as pair_audit
import prepare_v11_pair_delta as helper


ROOT = Path(__file__).resolve().parents[1]
BATCH1 = ROOT / "data/gold_profile_backfill_20260722_batch1"
BATCH2 = ROOT / "data/gold_profile_backfill_20260722_batch2"


class PrepareGoldProfileBackfillBatch2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan_text = (BATCH2 / "provenance_plan.json").read_text(encoding="utf-8")
        cls.rank_text = (BATCH2 / "ranked_profile_plan.json").read_text(encoding="utf-8")
        cls.preflight_text = (BATCH2 / "preflight_audit_summary.json").read_text(
            encoding="utf-8"
        )
        cls.plan = json.loads(cls.plan_text)
        cls.ranking = json.loads(cls.rank_text)
        cls.preflight = json.loads(cls.preflight_text)
        cls.batch1 = json.loads((BATCH1 / "provenance_plan.json").read_text())

    def test_new_boundary_and_immutable_batch_exclusion(self) -> None:
        selected = set(self.plan["selectedSignatures"])
        prior = set(self.batch1["selectedSignatures"])
        self.assertEqual(self.plan["batchNumber"], 2)
        self.assertEqual(self.plan["boundary"]["acceptedScoreablePairs"], 22062)
        self.assertEqual(len(prior), 32)
        self.assertEqual(set(self.plan["excludedEarlierBatchSignatures"]), prior)
        self.assertFalse(selected & prior)
        self.assertGreaterEqual(len(selected), 20)
        self.assertLessEqual(len(selected), 50)

    def test_batch1_and_v20_are_exact_profile_coverage(self) -> None:
        profiles = {
            row["path"] for row in self.plan["artifacts"]["profileArtifactsAtSeal"]
        }
        actions = {row["path"] for row in self.plan["artifacts"]["actionMaps"]}
        batch1 = "data/gold_profile_backfill_20260722_batch1/missing_profile_rows.jsonl"
        v20 = "data/autopilot_pair_delta_20260722_v20/missing_pair_all.jsonl"
        self.assertIn(batch1, profiles)
        self.assertIn(v20, profiles)
        self.assertIn(batch1, actions)
        self.assertIn(v20, actions)

    def test_worker_is_resumable_one_worker_and_not_launched(self) -> None:
        execution = self.plan["execution"]
        argv = execution["heavyWorkerArgv"]
        self.assertEqual(argv[:3], [
            "/usr/local/bin/sage",
            "-python",
            "agent_index24_missing_pair_census.sage.py",
        ])
        self.assertIn("--signature-aware", argv)
        self.assertEqual(argv[argv.index("--checkpoint-every") + 1], "1")
        self.assertIn("gold_profile_backfill_20260722_batch2", " ".join(argv))
        self.assertTrue(execution["oneWorkerAtATimeLockRequired"])
        self.assertFalse(execution["heavyWorkerLaunched"])
        self.assertFalse(execution["submissionAuthorized"])
        completed = helper.read_jsonl(BATCH2 / "missing_profile_rows.jsonl")
        self.assertEqual(len(completed), 25)
        self.assertEqual(
            {
                (str(row["sourceLabel"]), int(r))
                for row in completed
                for r in row.get("sourceR") or []
            },
            {
                (value.rsplit("/r", 1)[0], int(value.rsplit("/r", 1)[1]))
                for value in self.plan["selectedSignatures"]
            },
        )
        self.assertTrue(all(row.get("status") == "certified" for row in completed))

    def test_input_exactly_matches_selected_profiles(self) -> None:
        rows = helper.read_jsonl(BATCH2 / "census_input.jsonl")
        selected = {
            (value.rsplit("/r", 1)[0], int(value.rsplit("/r", 1)[1]))
            for value in self.plan["selectedSignatures"]
        }
        self.assertEqual(helper.source_pairs(rows), selected)
        self.assertEqual((BATCH2 / "empty_prior_input.jsonl").read_text(), "")

    def test_high_value_upper_bounds_and_light_only_metadata(self) -> None:
        selection = self.ranking["selection"]
        self.assertGreater(selection["deterministicSingleOrbitConversionUpperBound"], 0)
        self.assertGreater(selection["allCompatibleSafeConversionUpperBound"], 0)
        self.assertGreater(selection["potentialDistinctGoldPairs"], 0)
        self.assertTrue(all(self.preflight["checks"].values()))
        for text in (self.plan_text, self.rank_text, self.preflight_text):
            self.assertIsNone(pair_audit.COEFFICIENT_LINE_RE.search(text))
            self.assertNotIn('"coefficientLine"', text)
        self.assertEqual(self.preflight["sideEffects"]["sageRuns"], 0)
        self.assertEqual(self.preflight["sideEffects"]["networkCalls"], 0)
        self.assertEqual(self.preflight["sideEffects"]["submissionCalls"], 0)


if __name__ == "__main__":
    unittest.main()
