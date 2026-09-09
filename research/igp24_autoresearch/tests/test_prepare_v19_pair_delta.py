from __future__ import annotations

import unittest

import prepare_v11_pair_delta as helper
import prepare_v19_pair_delta as prepare


class PrepareV19PairDeltaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.summary = helper.read_json(prepare.GROUP_SUMMARY)
        cls.inventory = helper.read_json(prepare.INVENTORY)
        cls.plan = helper.read_json(prepare.PLAN)
        cls.preflight = helper.read_json(prepare.PREFLIGHT)

    def test_exact_eight_pair_seven_label_delta(self) -> None:
        self.assertEqual(self.summary["baseOwnedPairs"], 22_020)
        self.assertEqual(self.summary["snapshotOwnedPairs"], 22_028)
        self.assertEqual(self.summary["deltaPairs"], 8)
        self.assertEqual(self.summary["deltaLabels"], 7)
        self.assertTrue(self.summary["heavyPlanFinalized"])
        self.assertEqual(self.inventory["deltaPairCount"], 8)
        self.assertEqual(self.inventory["deltaLabelCount"], 7)
        self.assertEqual(
            set(self.inventory["acceptedAnchorsByPair"]),
            set(self.inventory["deltaPairs"]),
        )

    def test_receipt_boundaries(self) -> None:
        self.assertEqual(
            set(self.inventory["receiptBoundary"]),
            set(prepare.VERIFIED_RECEIPT),
        )
        row = next(iter(self.inventory["receiptBoundary"].values()))
        self.assertEqual(row["verifiedAcceptedScoreable"], 8)
        self.assertEqual(row["distinctPairs"], 8)
        self.assertEqual(
            self.inventory["queuedForV20Excluded"], prepare.QUEUED_FOR_V20
        )

    def test_guarded_chain_through_v18(self) -> None:
        execution = self.plan["execution"]
        self.assertEqual(self.plan["status"], "ready_for_one_heavy_worker")
        self.assertTrue(execution["heavyCommandFrozen"])
        self.assertFalse(execution["heavyWorkerLaunched"])
        self.assertEqual(execution["expectedDeltaPairsAtFinalization"], 8)
        self.assertEqual(len(self.plan["artifacts"]["priorMaps"]), 22)
        command = execution["guardedPlannedCommand"]
        self.assertIn("test ! -e", command[2])
        self.assertIn("agent_index24_missing_pair_census.sage.py", command[2])
        self.assertIn(
            "--prior-input data/autopilot_pair_delta_20260722_v18/group_input.jsonl",
            command[2],
        )
        self.assertFalse(prepare.OUTPUT.exists())

    def test_preflight_fully_certified_light_only(self) -> None:
        self.assertEqual(
            self.preflight["status"], "certified_safe_for_one_heavy_worker"
        )
        self.assertTrue(all(self.preflight["checks"].values()))
        self.assertEqual(self.preflight["workerPreflight"]["selectedRows"], 7)
        self.assertEqual(
            self.preflight["workerPreflight"]["selectedSignatures"], 8
        )
        self.assertFalse(
            self.preflight["sideEffects"]["heavyWorkerLaunched"]
        )
        self.assertEqual(self.preflight["sideEffects"]["networkCalls"], 0)
        self.assertEqual(self.preflight["sideEffects"]["submissionCalls"], 0)
        self.assertFalse(self.preflight["coefficientMaterialIncluded"])
        self.assertFalse(self.preflight["credentialMaterialIncluded"])


if __name__ == "__main__":
    unittest.main()
