from __future__ import annotations

import unittest

import prepare_v11_pair_delta as helper
import prepare_v18_pair_delta as prepare


class PrepareV18PairDeltaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.summary = helper.read_json(prepare.GROUP_SUMMARY)
        cls.inventory = helper.read_json(prepare.INVENTORY)
        cls.plan = helper.read_json(prepare.PLAN)
        cls.preflight = helper.read_json(prepare.PREFLIGHT)

    def test_frozen_boundary_and_exact_delta(self) -> None:
        self.assertEqual(self.summary["baseOwnedPairs"], 22_003)
        self.assertEqual(self.summary["snapshotOwnedPairs"], 22_020)
        self.assertEqual(self.summary["deltaPairs"], 17)
        self.assertEqual(self.summary["deltaLabels"], 17)
        self.assertTrue(self.summary["heavyPlanFinalized"])
        self.assertEqual(self.inventory["deltaPairCount"], 17)
        self.assertEqual(self.inventory["deltaLabelCount"], 17)
        self.assertEqual(
            set(self.inventory["acceptedAnchorsByPair"]),
            set(self.inventory["deltaPairs"]),
        )

    def test_receipts_and_queued_twist_boundary(self) -> None:
        self.assertEqual(
            set(self.inventory["receiptBoundary"]),
            set(prepare.RECEIPT_BOUNDARY),
        )
        for submission_id, expected in prepare.RECEIPT_BOUNDARY.items():
            row = self.inventory["receiptBoundary"][submission_id]
            self.assertEqual(row["verifiedAcceptedScoreable"], expected)
            self.assertEqual(row["distinctPairs"], expected)
        self.assertEqual(
            self.inventory["queuedForV19Excluded"],
            prepare.QUEUED_FOR_V19,
        )

    def test_guarded_worker_chain_through_v17(self) -> None:
        execution = self.plan["execution"]
        self.assertEqual(self.plan["status"], "ready_for_one_heavy_worker")
        self.assertTrue(execution["heavyCommandFrozen"])
        self.assertFalse(execution["heavyWorkerLaunched"])
        self.assertEqual(execution["expectedDeltaPairsAtFinalization"], 17)
        self.assertEqual(len(self.plan["artifacts"]["priorMaps"]), 21)
        command = execution["guardedPlannedCommand"]
        self.assertEqual(command[:2], ["/bin/sh", "-c"])
        self.assertIn("test ! -e", command[2])
        self.assertIn("agent_index24_missing_pair_census.sage.py", command[2])
        self.assertIn("--prior-input data/autopilot_pair_delta_20260722_v17/group_input.jsonl", command[2])
        self.assertFalse(prepare.OUTPUT.exists())

    def test_preflight_is_light_and_fully_certified(self) -> None:
        self.assertEqual(
            self.preflight["status"], "certified_safe_for_one_heavy_worker"
        )
        self.assertTrue(all(self.preflight["checks"].values()))
        self.assertEqual(self.preflight["workerPreflight"]["selectedRows"], 17)
        self.assertEqual(
            self.preflight["workerPreflight"]["selectedSignatures"], 17
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
