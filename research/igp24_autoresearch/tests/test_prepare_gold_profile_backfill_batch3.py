from __future__ import annotations

import json
import unittest
from pathlib import Path

import audit_low_contention_pair_routes as pair_audit
import prepare_v11_pair_delta as helper


ROOT = Path(__file__).resolve().parents[1]
BATCH1 = ROOT / "data/gold_profile_backfill_20260722_batch1"
BATCH2 = ROOT / "data/gold_profile_backfill_20260722_batch2"
BATCH3 = ROOT / "data/gold_profile_backfill_20260722_batch3"


class PrepareGoldProfileBackfillBatch3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan_text = (BATCH3 / "provenance_plan.json").read_text(encoding="utf-8")
        cls.rank_text = (BATCH3 / "ranked_profile_plan.json").read_text(
            encoding="utf-8"
        )
        cls.preflight_text = (BATCH3 / "preflight_audit_summary.json").read_text(
            encoding="utf-8"
        )
        cls.plan = json.loads(cls.plan_text)
        cls.ranking = json.loads(cls.rank_text)
        cls.preflight = json.loads(cls.preflight_text)
        cls.batch1 = json.loads((BATCH1 / "provenance_plan.json").read_text())
        cls.batch2 = json.loads((BATCH2 / "provenance_plan.json").read_text())

    def test_current_boundary_and_union_of_immutable_exclusions(self) -> None:
        prior1 = set(self.batch1["selectedSignatures"])
        prior2 = set(self.batch2["selectedSignatures"])
        selected = set(self.plan["selectedSignatures"])
        self.assertEqual(self.plan["batchNumber"], 3)
        self.assertEqual(self.plan["boundary"]["acceptedScoreablePairs"], 22062)
        self.assertEqual(self.plan["boundary"]["acceptedScoreableAnchorRows"], 927813)
        self.assertEqual(len(prior1), 32)
        self.assertEqual(len(prior2), 35)
        self.assertFalse(prior1 & prior2)
        self.assertEqual(
            set(self.plan["excludedEarlierBatchSignatures"]), prior1 | prior2
        )
        self.assertFalse(selected & (prior1 | prior2))
        excluded_plans = self.plan["artifacts"]["excludedEarlierBatchPlans"]
        self.assertEqual([row["batchNumber"] for row in excluded_plans], [1, 2])
        self.assertEqual([row["selectedSignatures"] for row in excluded_plans], [32, 35])

    def test_batch1_batch2_and_v21_are_exact_profile_and_action_coverage(self) -> None:
        required = {
            "data/gold_profile_backfill_20260722_batch1/missing_profile_rows.jsonl",
            "data/gold_profile_backfill_20260722_batch2/missing_profile_rows.jsonl",
            "data/autopilot_pair_delta_20260722_v21/missing_pair_all.jsonl",
        }
        profiles = {
            row["path"] for row in self.plan["artifacts"]["profileArtifactsAtSeal"]
        }
        actions = {row["path"] for row in self.plan["artifacts"]["actionMaps"]}
        self.assertTrue(required <= profiles)
        self.assertTrue(required <= actions)
        self.assertEqual(
            self.ranking["candidateCensus"]["requiredEarlierProfileArtifactsLoaded"],
            3,
        )
        self.assertTrue(
            self.preflight["checks"]["requiredExactActionProfileAuditFlagsTrue"]
        )
        for relative in required:
            rows = pair_audit.read_jsonl(ROOT / relative)
            self.assertTrue(rows)
            self.assertTrue(
                all(
                    row.get("status") == "certified"
                    and helper.certificate_is_exact(row)
                    for row in rows
                )
            )

    def test_greedy_tc0_safe_ranking_pauses_conditional_priority(self) -> None:
        selection = self.ranking["selection"]
        selected_rows = self.ranking["selectedProfileRows"]
        self.assertGreaterEqual(selection["selectedProfiles"], 20)
        self.assertLessEqual(selection["selectedProfiles"], 50)
        self.assertGreater(selection["deterministicSingleOrbitConversionUpperBound"], 0)
        self.assertGreater(selection["allCompatibleSafeConversionUpperBound"], 0)
        self.assertGreater(selection["potentialDistinctGoldPairs"], 0)
        self.assertEqual(
            selection["deterministicSingleOrbitConversionUpperBound"],
            sum(bool(row["singleOrbitAction"]) for row in selected_rows),
        )
        self.assertEqual(
            self.ranking["rankingOrder"][0],
            "new distinct current tc0 gold pairs covered per profile",
        )
        self.assertNotIn("conditional-frontier", " ".join(self.ranking["rankingOrder"]))

    def test_worker_is_resumable_one_worker_and_not_launched(self) -> None:
        execution = self.plan["execution"]
        argv = execution["heavyWorkerArgv"]
        self.assertEqual(
            argv[:3],
            [
                "/usr/local/bin/sage",
                "-python",
                "agent_index24_missing_pair_census.sage.py",
            ],
        )
        self.assertIn("--signature-aware", argv)
        self.assertEqual(argv[argv.index("--checkpoint-every") + 1], "1")
        self.assertIn("gold_profile_backfill_20260722_batch3", " ".join(argv))
        self.assertTrue(execution["oneWorkerAtATimeLockRequired"])
        self.assertFalse(execution["heavyWorkerLaunched"])
        self.assertFalse(execution["submissionAuthorized"])
        self.assertEqual(
            execution["resumableCoordinatorCommand"],
            "/usr/bin/caffeinate -i python3 run_gold_profile_backfill_one.py "
            "--execute --plan "
            "data/gold_profile_backfill_20260722_batch3/provenance_plan.json",
        )
        self.assertFalse((BATCH3 / "missing_profile_rows.jsonl").exists())
        self.assertFalse((BATCH3 / "runtime_resume_chunk.jsonl").exists())

    def test_input_exactly_matches_selected_profiles(self) -> None:
        rows = helper.read_jsonl(BATCH3 / "census_input.jsonl")
        selected = {
            (value.rsplit("/r", 1)[0], int(value.rsplit("/r", 1)[1]))
            for value in self.plan["selectedSignatures"]
        }
        self.assertEqual(helper.source_pairs(rows), selected)
        self.assertEqual((BATCH3 / "empty_prior_input.jsonl").read_text(), "")

    def test_light_only_metadata_and_preflight(self) -> None:
        self.assertTrue(all(self.preflight["checks"].values()))
        for text in (self.plan_text, self.rank_text, self.preflight_text):
            self.assertIsNone(pair_audit.COEFFICIENT_LINE_RE.search(text))
            self.assertNotIn('"coefficientLine"', text)
        self.assertEqual(self.preflight["sideEffects"]["sageRuns"], 0)
        self.assertEqual(self.preflight["sideEffects"]["networkCalls"], 0)
        self.assertEqual(self.preflight["sideEffects"]["submissionCalls"], 0)


if __name__ == "__main__":
    unittest.main()
