from __future__ import annotations

import json
import unittest
from pathlib import Path

import audit_full_ledger_gold_reintersection as full_audit
import audit_low_contention_pair_routes as pair_audit
import prepare_gold_profile_backfill_batch4 as prepare
import prepare_v11_pair_delta as helper


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BATCHES = {
    number: DATA / f"gold_profile_backfill_20260722_batch{number}"
    for number in (1, 2, 3, 4)
}
V21 = DATA / "autopilot_pair_delta_20260722_v21"


class PrepareGoldProfileBackfillBatch4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan_text = (BATCHES[4] / "provenance_plan.json").read_text(
            encoding="utf-8"
        )
        cls.rank_text = (BATCHES[4] / "ranked_profile_plan.json").read_text(
            encoding="utf-8"
        )
        cls.preflight_text = (
            BATCHES[4] / "preflight_audit_summary.json"
        ).read_text(encoding="utf-8")
        cls.plan = json.loads(cls.plan_text)
        cls.ranking = json.loads(cls.rank_text)
        cls.preflight = json.loads(cls.preflight_text)
        cls.full = json.loads(full_audit.CERTIFICATE.read_text(encoding="utf-8"))
        cls.earlier = {
            number: json.loads(
                (BATCHES[number] / "provenance_plan.json").read_text(
                    encoding="utf-8"
                )
            )
            for number in (1, 2, 3)
        }

    def test_current_boundary_and_union_of_three_immutable_exclusions(self) -> None:
        prior_sets = {
            number: set(plan["selectedSignatures"])
            for number, plan in self.earlier.items()
        }
        prior_union = set().union(*prior_sets.values())
        selected = set(self.plan["selectedSignatures"])
        self.assertEqual(self.plan["batchNumber"], 4)
        for key, value in prepare.EXPECTED_BOUNDARY.items():
            self.assertEqual(self.plan["boundary"][key], value)
            self.assertEqual(self.full["boundary"][key], value)
        self.assertEqual(
            self.plan["artifacts"]["fullGoldAudit"]["sha256"],
            prepare.EXPECTED_FULL_AUDIT_SHA256,
        )
        self.assertEqual([len(prior_sets[n]) for n in (1, 2, 3)], [32, 35, 36])
        self.assertFalse(prior_sets[1] & prior_sets[2])
        self.assertFalse(prior_sets[1] & prior_sets[3])
        self.assertFalse(prior_sets[2] & prior_sets[3])
        self.assertEqual(
            set(self.plan["excludedEarlierBatchSignatures"]), prior_union
        )
        self.assertFalse(selected & prior_union)
        excluded = self.plan["artifacts"]["excludedEarlierBatchPlans"]
        self.assertEqual([row["batchNumber"] for row in excluded], [1, 2, 3])
        self.assertEqual([row["selectedSignatures"] for row in excluded], [32, 35, 36])

    def test_all_three_batches_and_v21_are_exact_coverage(self) -> None:
        required = {
            "data/gold_profile_backfill_20260722_batch1/missing_profile_rows.jsonl",
            "data/gold_profile_backfill_20260722_batch2/missing_profile_rows.jsonl",
            "data/gold_profile_backfill_20260722_batch3/missing_profile_rows.jsonl",
            "data/autopilot_pair_delta_20260722_v21/missing_pair_all.jsonl",
        }
        profiles = {
            row["path"] for row in self.plan["artifacts"]["profileArtifactsAtSeal"]
        }
        actions = {row["path"] for row in self.plan["artifacts"]["actionMaps"]}
        self.assertTrue(required <= profiles)
        self.assertTrue(required <= actions)
        self.assertEqual(
            self.ranking["candidateCensus"][
                "requiredEarlierProfileArtifactsLoaded"
            ],
            4,
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

    def test_greedy_tc0_safe_conversion_ranking(self) -> None:
        selection = self.ranking["selection"]
        selected_rows = self.ranking["selectedProfileRows"]
        self.assertGreaterEqual(selection["selectedProfiles"], 20)
        self.assertLessEqual(selection["selectedProfiles"], 50)
        self.assertGreater(selection["potentialDistinctGoldPairs"], 0)
        self.assertEqual(
            selection["deterministicSingleOrbitConversionUpperBound"],
            sum(bool(row["singleOrbitAction"]) for row in selected_rows),
        )
        self.assertEqual(
            selection["allCompatibleSafeConversionUpperBound"],
            sum(int(row["estimatedRoutesUnlockedUpperBound"]) for row in selected_rows),
        )
        self.assertEqual(
            self.ranking["rankingOrder"][0],
            "new distinct current tc0 gold pairs covered per profile",
        )
        self.assertNotIn(
            "conditional-frontier", " ".join(self.ranking["rankingOrder"])
        )

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
        self.assertIn("gold_profile_backfill_20260722_batch4", " ".join(argv))
        self.assertTrue(execution["oneWorkerAtATimeLockRequired"])
        self.assertFalse(execution["heavyWorkerLaunched"])
        self.assertFalse(execution["submissionAuthorized"])
        self.assertEqual(
            execution["resumableCoordinatorCommand"],
            "/usr/bin/caffeinate -i python3 run_gold_profile_backfill_one.py "
            "--execute --plan "
            "data/gold_profile_backfill_20260722_batch4/provenance_plan.json",
        )
        self.assertFalse((BATCHES[4] / "missing_profile_rows.jsonl").exists())
        self.assertFalse((BATCHES[4] / "runtime_resume_chunk.jsonl").exists())

    def test_input_exactly_matches_selected_profiles(self) -> None:
        rows = helper.read_jsonl(BATCHES[4] / "census_input.jsonl")
        selected = {
            (value.rsplit("/r", 1)[0], int(value.rsplit("/r", 1)[1]))
            for value in self.plan["selectedSignatures"]
        }
        self.assertEqual(helper.source_pairs(rows), selected)
        self.assertEqual((BATCHES[4] / "empty_prior_input.jsonl").read_text(), "")

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
