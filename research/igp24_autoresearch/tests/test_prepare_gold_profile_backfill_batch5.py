from __future__ import annotations

import json
import unittest
from pathlib import Path

import audit_full_ledger_gold_reintersection as full_audit
import audit_low_contention_pair_routes as pair_audit
import prepare_gold_profile_backfill_batch5 as prepare
import prepare_v11_pair_delta as helper


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BATCHES = {
    number: DATA / f"gold_profile_backfill_20260722_batch{number}"
    for number in (1, 2, 3, 4, 5)
}


class PrepareGoldProfileBackfillBatch5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan_text = (BATCHES[5] / "provenance_plan.json").read_text(
            encoding="utf-8"
        )
        cls.rank_text = (BATCHES[5] / "ranked_profile_plan.json").read_text(
            encoding="utf-8"
        )
        cls.preflight_text = (
            BATCHES[5] / "preflight_audit_summary.json"
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
            for number in (1, 2, 3, 4)
        }

    def test_exact_boundary_and_four_immutable_exclusions(self) -> None:
        prior_sets = {
            number: set(plan["selectedSignatures"])
            for number, plan in self.earlier.items()
        }
        prior_union = set().union(*prior_sets.values())
        selected = set(self.plan["selectedSignatures"])
        self.assertEqual(self.plan["batchNumber"], 5)
        for key, value in prepare.EXPECTED_BOUNDARY.items():
            self.assertEqual(self.plan["boundary"][key], value)
            self.assertEqual(self.full["boundary"][key], value)
        self.assertEqual(
            self.plan["artifacts"]["fullGoldAudit"]["sha256"],
            prepare.EXPECTED_FULL_AUDIT_SHA256,
        )
        self.assertEqual([len(prior_sets[n]) for n in (1, 2, 3, 4)], [32, 35, 36, 36])
        self.assertEqual(sum(map(len, prior_sets.values())), 139)
        self.assertEqual(len(prior_union), 139)
        self.assertEqual(
            set(self.plan["excludedEarlierBatchSignatures"]), prior_union
        )
        self.assertFalse(selected & prior_union)
        excluded = self.plan["artifacts"]["excludedEarlierBatchPlans"]
        self.assertEqual([row["batchNumber"] for row in excluded], [1, 2, 3, 4])
        self.assertEqual(
            [row["selectedSignatures"] for row in excluded], [32, 35, 36, 36]
        )

    def test_batches_one_through_four_and_v21_are_exact_coverage(self) -> None:
        required = {
            *(f"data/gold_profile_backfill_20260722_batch{number}/missing_profile_rows.jsonl" for number in (1, 2, 3, 4)),
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
            5,
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
        self.assertEqual(selection["selectedProfiles"], 32)
        self.assertEqual(selection["selectedSourceLabels"], 8)
        self.assertEqual(selection["potentialDistinctGoldPairs"], 9)
        self.assertEqual(selection["deterministicSingleOrbitConversionUpperBound"], 25)
        self.assertEqual(selection["allCompatibleSafeConversionUpperBound"], 39)
        self.assertEqual(selection["estimatedRoutesUnlockedUpperBound"], 39)
        self.assertEqual(
            self.ranking["candidateCensus"]["rankableProfilePairs"], 175
        )
        self.assertEqual(
            self.ranking["candidateCensus"]["rankableSourceLabels"], 35
        )
        self.assertEqual(
            [row["profileCount"] for row in self.ranking["selectedLabelGroups"]],
            [3, 3, 3, 3, 7, 4, 4, 5],
        )
        self.assertEqual(
            pair_audit.canonical_digest(self.plan["selectedSignatures"]),
            "e7f04d08db6c9f9144023fc69b4a330a3c88b099a05151fd928dcb46b7b95183",
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
        self.assertEqual(argv.count("--prior-map"), 29)
        self.assertIn("--signature-aware", argv)
        self.assertEqual(argv[argv.index("--shard-count") + 1], "1")
        self.assertEqual(argv[argv.index("--checkpoint-every") + 1], "1")
        self.assertIn("gold_profile_backfill_20260722_batch5", " ".join(argv))
        self.assertTrue(execution["oneWorkerAtATimeLockRequired"])
        self.assertTrue(execution["requiresRootHeavyClearance"])
        self.assertFalse(execution["heavyWorkerLaunched"])
        self.assertFalse(execution["submissionAuthorized"])
        self.assertEqual(
            execution["resumableCoordinatorCommand"],
            "/usr/bin/caffeinate -i python3 run_gold_profile_backfill_one.py "
            "--execute --plan "
            "data/gold_profile_backfill_20260722_batch5/provenance_plan.json",
        )
        self.assertFalse((BATCHES[5] / "missing_profile_rows.jsonl").exists())
        self.assertFalse((BATCHES[5] / "runtime_resume_chunk.jsonl").exists())

    def test_input_exactly_matches_selected_profiles(self) -> None:
        rows = helper.read_jsonl(BATCHES[5] / "census_input.jsonl")
        selected = {
            (value.rsplit("/r", 1)[0], int(value.rsplit("/r", 1)[1]))
            for value in self.plan["selectedSignatures"]
        }
        self.assertEqual(helper.source_pairs(rows), selected)
        self.assertEqual((BATCHES[5] / "empty_prior_input.jsonl").read_text(), "")

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
