from __future__ import annotations

import unittest

import prepare_v11_pair_delta as helper
import prepare_v17_pair_delta as prepare


class PrepareV17PairDeltaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.summary = helper.read_json(prepare.GROUP_SUMMARY)
        cls.inventory = helper.read_json(prepare.INVENTORY)
        cls.plan = helper.read_json(prepare.PLAN)
        cls.preflight = helper.read_json(prepare.PREFLIGHT)

    def test_exact_delta_and_v16_boundary_are_frozen(self) -> None:
        self.assertEqual(self.summary["baseOwnedPairs"], 21_956)
        self.assertEqual(self.summary["snapshotOwnedPairs"], 22_003)
        self.assertEqual(self.summary["deltaPairs"], 47)
        self.assertEqual(self.summary["deltaLabels"], 39)
        self.assertTrue(self.summary["heavyPlanFinalized"])
        self.assertEqual(self.inventory["deltaPairCount"], 47)
        self.assertEqual(self.inventory["deltaLabelCount"], 39)
        self.assertEqual(
            set(self.inventory["acceptedAnchorsByPair"]),
            set(self.inventory["deltaPairs"]),
        )

    def test_receipt_boundary_and_v18_exclusion_are_explicit(self) -> None:
        self.assertEqual(
            set(self.inventory["receiptBoundary"]),
            set(prepare.RECEIPT_BOUNDARY),
        )
        for submission_id, expected in prepare.RECEIPT_BOUNDARY.items():
            row = self.inventory["receiptBoundary"][submission_id]
            self.assertEqual(row["verifiedAcceptedScoreable"], expected)
            self.assertEqual(row["distinctPairs"], expected)
        self.assertEqual(
            self.inventory["queuedForV18Excluded"],
            prepare.QUEUED_FOR_V18,
        )

    def test_guarded_one_worker_command_is_frozen_but_not_run(self) -> None:
        execution = self.plan["execution"]
        self.assertEqual(self.plan["status"], "ready_for_one_heavy_worker")
        self.assertTrue(execution["heavyCommandFrozen"])
        self.assertFalse(execution["heavyWorkerLaunched"])
        self.assertEqual(execution["expectedDeltaPairsAtFinalization"], 47)
        command = execution["guardedPlannedCommand"]
        self.assertEqual(command[:2], ["/bin/sh", "-c"])
        self.assertIn("test ! -e", command[2])
        self.assertIn("agent_index24_missing_pair_census.sage.py", command[2])
        self.assertIn("--shard-count 1", command[2])
        self.assertEqual(len(self.plan["artifacts"]["priorMaps"]), 20)
        self.assertFalse(prepare.OUTPUT.exists())

    def test_preflight_checks_and_side_effects(self) -> None:
        self.assertEqual(
            self.preflight["status"], "certified_safe_for_one_heavy_worker"
        )
        self.assertTrue(all(self.preflight["checks"].values()))
        self.assertEqual(self.preflight["workerPreflight"]["selectedRows"], 39)
        self.assertEqual(
            self.preflight["workerPreflight"]["selectedSignatures"], 47
        )
        self.assertTrue(
            self.preflight["workerPreflight"]["heavyCommandFrozen"]
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
