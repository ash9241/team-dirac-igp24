from __future__ import annotations

import json
import unittest

import audit_low_contention_pair_routes as pair_audit
import prepare_gold_profile_backfill as prepare
import prepare_v11_pair_delta as helper
import run_gold_profile_backfill_one as coordinator


class PrepareGoldProfileBackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ranking_text = prepare.RANKING.read_text(encoding="utf-8")
        cls.plan_text = prepare.PLAN.read_text(encoding="utf-8")
        cls.preflight_text = prepare.PREFLIGHT.read_text(encoding="utf-8")
        cls.ranking = json.loads(cls.ranking_text)
        cls.plan = json.loads(cls.plan_text)
        cls.preflight = json.loads(cls.preflight_text)

    def test_batch_is_ranked_deduplicated_and_bounded(self) -> None:
        selection = self.ranking["selection"]
        rows = self.ranking["selectedProfileRows"]
        groups = self.ranking["selectedLabelGroups"]
        pairs = [row["sourcePair"] for row in rows]
        labels = [row["sourceLabel"] for row in groups]
        self.assertGreaterEqual(len(rows), 20)
        self.assertLessEqual(len(rows), 50)
        self.assertEqual(len(rows), selection["selectedProfiles"])
        self.assertEqual(len(groups), selection["selectedSourceLabels"])
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertEqual(len(labels), len(set(labels)))
        self.assertTrue(all(row["freshAnchorCount"] > 0 for row in rows))
        self.assertTrue(all(row["potentialGoldFacingOrbits"] for row in rows))

    def test_selected_input_is_exact_and_prior_is_empty(self) -> None:
        input_rows = helper.read_jsonl(prepare.INPUT)
        selected = {
            (row["sourceLabel"], int(row["sourceR"]))
            for row in self.ranking["selectedProfileRows"]
        }
        self.assertEqual(helper.source_pairs(input_rows), selected)
        self.assertEqual(prepare.EMPTY_PRIOR.read_text(encoding="utf-8"), "")
        completed = helper.read_jsonl(prepare.OUTPUT)
        self.assertEqual(len(completed), 29)
        self.assertEqual(
            {
                (str(row["sourceLabel"]), int(r))
                for row in completed
                for r in row.get("sourceR") or []
            },
            selected,
        )
        self.assertTrue(
            all(
                row.get("status") == "certified"
                and helper.certificate_is_exact(row)
                for row in completed
            )
        )
        self.assertFalse(prepare.RESUME_CHUNK.exists())

    def test_plan_freezes_one_resumable_worker(self) -> None:
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
        self.assertEqual(argv[argv.index("--shard-count") + 1], "1")
        self.assertIn("run_gold_profile_backfill_one.py", execution["resumableCoordinatorCommand"])
        self.assertTrue(execution["oneWorkerAtATimeLockRequired"])
        self.assertFalse(execution["heavyWorkerLaunched"])
        self.assertFalse(execution["submissionAuthorized"])
        self.assertEqual(coordinator.validate_worker_rows([], self.plan), {})

    def test_high_yield_estimates_are_explicit_upper_bounds(self) -> None:
        selection = self.ranking["selection"]
        self.assertGreater(selection["estimatedRoutesUnlockedUpperBound"], 0)
        self.assertGreater(selection["potentialDistinctGoldPairs"], 0)
        self.assertGreater(
            selection["deterministicSingleOrbitConversionUpperBound"], 0
        )
        self.assertIn("upper bounds", selection["estimateQualifier"])
        self.assertGreaterEqual(selection["conditionalFrontierOverlapPairs"], 1)

    def test_preflight_and_metadata_are_light_only(self) -> None:
        self.assertEqual(
            self.preflight["status"],
            "certified_resumable_one_worker_plan_ready",
        )
        self.assertTrue(all(self.preflight["checks"].values()))
        for text in (self.ranking_text, self.plan_text, self.preflight_text):
            self.assertIsNone(pair_audit.COEFFICIENT_LINE_RE.search(text))
            self.assertNotIn('"coefficientLine"', text)
            self.assertNotIn('"coefficients"', text)
        self.assertFalse(self.ranking["coefficientMaterialIncluded"])
        self.assertFalse(self.plan["coefficientMaterialIncluded"])
        self.assertFalse(self.preflight["coefficientMaterialIncluded"])
        self.assertEqual(self.preflight["sideEffects"]["sageRuns"], 0)
        self.assertEqual(self.preflight["sideEffects"]["gapRuns"], 0)
        self.assertEqual(self.preflight["sideEffects"]["networkCalls"], 0)
        self.assertEqual(self.preflight["sideEffects"]["submissionCalls"], 0)


if __name__ == "__main__":
    unittest.main()
