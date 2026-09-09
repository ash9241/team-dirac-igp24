from __future__ import annotations

import json
import unittest
from pathlib import Path

import audit_full_ledger_gold_reintersection as full_audit
import audit_low_contention_pair_routes as pair_audit
import prepare_gold_profile_backfill_batch7 as prepare
import prepare_v11_pair_delta as helper


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BATCHES = {
    number: DATA / f"gold_profile_backfill_20260722_batch{number}"
    for number in range(1, 8)
}
PRIOR_COUNTS = [32, 35, 36, 36, 32, 50]


class PrepareGoldProfileBackfillBatch7Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan_text = (BATCHES[7] / "provenance_plan.json").read_text()
        cls.rank_text = (BATCHES[7] / "ranked_profile_plan.json").read_text()
        cls.preflight_text = (BATCHES[7] / "preflight_audit_summary.json").read_text()
        cls.plan = json.loads(cls.plan_text)
        cls.ranking = json.loads(cls.rank_text)
        cls.preflight = json.loads(cls.preflight_text)
        cls.full = json.loads(full_audit.CERTIFICATE.read_text())
        cls.earlier = {
            number: json.loads((BATCHES[number] / "provenance_plan.json").read_text())
            for number in range(1, 7)
        }

    def test_exact_boundary_and_six_immutable_exclusions(self) -> None:
        prior_sets = {
            number: set(plan["selectedSignatures"])
            for number, plan in self.earlier.items()
        }
        prior_union = set().union(*prior_sets.values())
        self.assertEqual(self.plan["batchNumber"], 7)
        for key, value in prepare.EXPECTED_BOUNDARY.items():
            self.assertEqual(self.plan["boundary"][key], value)
            self.assertEqual(self.full["boundary"][key], value)
        self.assertEqual(
            self.plan["artifacts"]["fullGoldAudit"]["sha256"],
            prepare.EXPECTED_FULL_AUDIT_SHA256,
        )
        self.assertEqual([len(prior_sets[n]) for n in range(1, 7)], PRIOR_COUNTS)
        self.assertEqual(len(prior_union), 221)
        self.assertEqual(
            set(self.plan["excludedEarlierBatchSignatures"]), prior_union
        )
        self.assertFalse(set(self.plan["selectedSignatures"]) & prior_union)
        excluded = self.plan["artifacts"]["excludedEarlierBatchPlans"]
        self.assertEqual([row["batchNumber"] for row in excluded], list(range(1, 7)))
        self.assertEqual([row["selectedSignatures"] for row in excluded], PRIOR_COUNTS)

    def test_batches_one_through_six_and_v21_are_exact_coverage(self) -> None:
        required = {
            *(f"data/gold_profile_backfill_20260722_batch{number}/missing_profile_rows.jsonl" for number in range(1, 7)),
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
            7,
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

    def test_maximum_safe_tc0_greedy_ranking(self) -> None:
        selection = self.ranking["selection"]
        selected_rows = self.ranking["selectedProfileRows"]
        self.assertEqual(selection["targetProfiles"], 50)
        self.assertEqual(selection["selectedProfiles"], 50)
        self.assertEqual(selection["selectedSourceLabels"], 10)
        self.assertEqual(selection["potentialDistinctGoldPairs"], 10)
        self.assertEqual(selection["deterministicSingleOrbitConversionUpperBound"], 50)
        self.assertEqual(selection["allCompatibleSafeConversionUpperBound"], 50)
        self.assertEqual(selection["estimatedRoutesUnlockedUpperBound"], 50)
        self.assertEqual(
            self.ranking["candidateCensus"]["rankableProfilePairs"], 93
        )
        self.assertEqual(
            self.ranking["candidateCensus"]["rankableSourceLabels"], 17
        )
        self.assertEqual(
            [row["profileCount"] for row in self.ranking["selectedLabelGroups"]],
            [5] * 10,
        )
        self.assertEqual(
            pair_audit.canonical_digest(self.plan["selectedSignatures"]),
            "ddf35e8c75afa39bc9a41ec7c8f5abc23941c0835629760c88ceda6967376d9a",
        )
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

    def test_worker_is_resumable_one_worker_and_not_launched(self) -> None:
        execution = self.plan["execution"]
        argv = execution["heavyWorkerArgv"]
        self.assertEqual(
            argv[:3],
            ["/usr/local/bin/sage", "-python", "agent_index24_missing_pair_census.sage.py"],
        )
        self.assertEqual(argv.count("--prior-map"), 31)
        self.assertIn("--signature-aware", argv)
        self.assertEqual(argv[argv.index("--shard-count") + 1], "1")
        self.assertEqual(argv[argv.index("--checkpoint-every") + 1], "1")
        self.assertTrue(execution["oneWorkerAtATimeLockRequired"])
        self.assertTrue(execution["requiresRootHeavyClearance"])
        self.assertFalse(execution["heavyWorkerLaunched"])
        self.assertFalse(execution["submissionAuthorized"])
        self.assertEqual(
            execution["resumableCoordinatorCommand"],
            "/usr/bin/caffeinate -i python3 run_gold_profile_backfill_one.py "
            "--execute --plan "
            "data/gold_profile_backfill_20260722_batch7/provenance_plan.json",
        )
        self.assertFalse((BATCHES[7] / "missing_profile_rows.jsonl").exists())
        self.assertFalse((BATCHES[7] / "runtime_resume_chunk.jsonl").exists())

    def test_input_exactly_matches_selected_profiles(self) -> None:
        rows = helper.read_jsonl(BATCHES[7] / "census_input.jsonl")
        selected = {
            (value.rsplit("/r", 1)[0], int(value.rsplit("/r", 1)[1]))
            for value in self.plan["selectedSignatures"]
        }
        self.assertEqual(helper.source_pairs(rows), selected)
        self.assertEqual((BATCHES[7] / "empty_prior_input.jsonl").read_text(), "")

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
