from __future__ import annotations

import json
import unittest

import prepare_v11_pair_delta as helper
import prepare_v21_pair_delta as prepare


EXPECTED_DELTA = {
    "24T990:24",
    "24T3032:24",
    "24T3344:24",
    "24T6194:0",
    "24T6194:12",
    "24T6194:24",
    "24T7848:24",
    "24T7949:12",
    "24T8190:24",
    "24T8192:24",
    "24T9153:12",
    "24T10390:20",
    "24T12348:0",
    "24T12740:24",
    "24T12798:20",
    "24T12917:20",
    "24T14760:24",
    "24T15253:8",
    "24T16247:24",
    "24T16386:0",
    "24T16386:16",
    "24T17101:12",
    "24T17117:24",
    "24T17119:24",
    "24T17187:4",
    "24T17191:12",
    "24T17212:4",
    "24T17216:4",
    "24T17611:24",
    "24T19033:12",
    "24T20493:24",
    "24T21055:24",
}


class PrepareV21PairDeltaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.summary = helper.read_json(prepare.GROUP_SUMMARY)
        cls.inventory = helper.read_json(prepare.INVENTORY)
        cls.plan = helper.read_json(prepare.PLAN)
        cls.preflight = helper.read_json(prepare.PREFLIGHT)
        cls.census = helper.read_jsonl(prepare.CENSUS_INPUT)

    def test_exact_22030_to_22062_delta(self) -> None:
        self.assertEqual(self.summary["baseOwnedPairs"], 22_030)
        self.assertEqual(self.summary["snapshotOwnedPairs"], 22_062)
        self.assertEqual(self.summary["deltaPairs"], 32)
        self.assertEqual(self.summary["deltaLabels"], 29)
        self.assertTrue(self.summary["heavyPlanFinalized"])
        self.assertEqual(self.inventory["deltaPairCount"], 32)
        self.assertEqual(self.inventory["deltaLabelCount"], 29)
        self.assertEqual(set(self.inventory["deltaPairs"]), EXPECTED_DELTA)
        self.assertEqual(
            set(self.inventory["acceptedAnchorsByPair"]), EXPECTED_DELTA
        )
        self.assertEqual(
            sum(bool(row.get("isOwnedSource")) for row in self.census), 29
        )

    def test_five_receipts_are_exact_delta_union(self) -> None:
        receipts = self.inventory["receiptBoundary"]
        self.assertEqual(set(receipts), set(prepare.VERIFIED_RECEIPTS))
        self.assertEqual(
            sum(row["verifiedAcceptedScoreable"] for row in receipts.values()),
            32,
        )
        self.assertEqual(
            sum(row["distinctPairs"] for row in receipts.values()), 32
        )
        collision = self.inventory["collisionCensus"]
        self.assertEqual(collision["baselinePairCollisions"], 0)
        self.assertEqual(collision["priorFrozenSignatureCollisions"], 0)
        self.assertEqual(collision["unanchoredDeltaPairs"], 0)
        self.assertEqual(collision["receiptUnionSymmetricDifference"], 0)

    def test_guarded_single_worker_chain_through_v20(self) -> None:
        execution = self.plan["execution"]
        self.assertEqual(self.plan["status"], "ready_for_one_heavy_worker")
        self.assertTrue(execution["heavyCommandFrozen"])
        self.assertFalse(execution["heavyWorkerLaunched"])
        self.assertEqual(execution["maximumHeavyConcurrency"], 1)
        self.assertEqual(execution["expectedDeltaPairsAtFinalization"], 32)
        self.assertEqual(len(self.plan["artifacts"]["priorMaps"]), 24)
        command = execution["guardedPlannedCommand"]
        self.assertEqual(command[:2], ["/bin/sh", "-c"])
        self.assertIn("test ! -e data/autopilot_pair_delta_20260722_v21/missing_pair_all.jsonl", command[2])
        self.assertIn("missing_pair_all.jsonl.tmp", command[2])
        self.assertIn("/usr/bin/caffeinate -i /usr/local/bin/sage", command[2])
        self.assertIn("agent_index24_missing_pair_census.sage.py", command[2])
        self.assertIn(
            "--prior-input data/autopilot_pair_delta_20260722_v20/group_input.jsonl",
            command[2],
        )
        self.assertNotIn("batch2", command[2].lower())
        completed = helper.read_jsonl(prepare.OUTPUT)
        self.assertEqual(len(completed), 29)
        self.assertEqual(
            {
                (str(row["sourceLabel"]), int(r))
                for row in completed
                for r in row.get("sourceR") or []
            },
            helper.source_pairs(helper.read_jsonl(prepare.CENSUS_INPUT)),
        )
        self.assertTrue(
            all(
                row.get("status") == "certified"
                and helper.certificate_is_exact(row)
                for row in completed
            )
        )
        self.assertFalse(
            prepare.OUTPUT.with_suffix(prepare.OUTPUT.suffix + ".tmp").exists()
        )

    def test_preflight_is_fully_certified_and_light_only(self) -> None:
        self.assertEqual(
            self.preflight["status"], "certified_safe_for_one_heavy_worker"
        )
        self.assertTrue(all(self.preflight["checks"].values()))
        worker = self.preflight["workerPreflight"]
        self.assertEqual(worker["selectedRows"], 29)
        self.assertEqual(worker["selectedSignatures"], 32)
        self.assertEqual(worker["maximumHeavyConcurrency"], 1)
        side_effects = self.preflight["sideEffects"]
        self.assertFalse(side_effects["heavyWorkerLaunched"])
        self.assertEqual(side_effects["networkCalls"], 0)
        self.assertEqual(side_effects["submissionCalls"], 0)
        self.assertEqual(side_effects["ledgerWrites"], 0)
        self.assertTrue(side_effects["writesLimitedToV21Artifacts"])
        self.assertFalse(side_effects["batch2ArtifactsTouched"])

    def test_metadata_is_coefficient_and_credential_safe(self) -> None:
        payload = json.dumps(
            [self.summary, self.inventory, self.plan, self.preflight],
            separators=(",", ":"),
            sort_keys=True,
        ).lower()
        self.assertNotIn('"coefficientline"', payload)
        self.assertNotIn('"coefficients"', payload)
        self.assertNotIn('"authorization"', payload)
        self.assertNotIn("bearer ", payload)
        self.assertFalse(self.plan["coefficientMaterialIncluded"])
        self.assertFalse(self.preflight["coefficientMaterialIncluded"])
        self.assertFalse(self.preflight["credentialMaterialIncluded"])


if __name__ == "__main__":
    unittest.main()
