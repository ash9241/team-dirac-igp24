from __future__ import annotations

import json
import unittest
from pathlib import Path

import finalize_tc3_even_twist_batch as finalizer
import run_low_contention_sequential as lane
import sair_api


class FinalTc3EvenTwistBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = lane.read_json(finalizer.FINAL_CERTIFICATE)
        cls.mapping = lane.read_json(finalizer.FINAL_MAPPING)
        cls.runbook = lane.read_json(finalizer.RUNBOOK)
        cls.raw = finalizer.jsonl(finalizer.RAW_RESULTS)

    def test_eight_anchors_each_have_exact_positive_and_negative_twists(self) -> None:
        self.assertEqual(len(self.runbook["rankedSources"]), 8)
        self.assertEqual(len(self.raw), 16)
        self.assertEqual(sum(row["twistSign"] == "positive" for row in self.raw), 8)
        self.assertEqual(sum(row["twistSign"] == "negative" for row in self.raw), 8)
        for row in self.raw:
            self.assertTrue(row["twistIrreducible"])
            self.assertTrue(row["sourceSquarefreeModRamificationPrime"])
            self.assertEqual(row["twistDirectRealRootCount"], row["targetR"])
            self.assertEqual(
                row["actionResolutionMethod"], "all-block-systems-same-target"
            )

    def test_final_manifest_and_mapping_are_exact_distinct_eight(self) -> None:
        _lines, hashes = sair_api.validated_manifest(finalizer.FINAL_MANIFEST)
        mappings = self.mapping["mappings"]
        self.assertEqual(len(hashes), 8)
        self.assertEqual(hashes, [row["candidateSha256"] for row in mappings])
        self.assertEqual(len(set(hashes)), 8)
        self.assertEqual(len({row["targetPair"] for row in mappings}), 8)
        self.assertEqual(self.mapping["routeCount"], 8)
        self.assertEqual(self.mapping["distinctCandidateHashes"], 8)
        self.assertEqual(self.mapping["distinctTargetPairs"], 8)

    def test_newest_receipts_and_outbox_postfilter_are_certified(self) -> None:
        receipt_ids = {
            row["submissionId"]
            for row in self.certificate["newestReceiptCrossCheck"]
        }
        self.assertEqual(receipt_ids, set(finalizer.NEWEST_RECEIPTS))
        self.assertEqual(self.certificate["selectedSafeRows"], 8)
        self.assertEqual(self.certificate["failClosedExclusions"], 0)
        self.assertEqual(self.certificate["projectedMarginalScoreExact"], "133/256")
        self.assertTrue(all(self.certificate["checks"].values()))
        self.assertFalse(self.certificate["combinedOfflineDryRun"]["commit"])
        self.assertEqual(self.certificate["sideEffects"]["networkCalls"], 0)
        self.assertEqual(self.certificate["sideEffects"]["submissionCalls"], 0)
        self.assertEqual(
            self.certificate["sideEffects"]["maximumConcurrentSageGapWorkers"], 1
        )

    def test_public_certificates_exclude_coefficient_material(self) -> None:
        paths = [finalizer.FINAL_CERTIFICATE, finalizer.FINAL_MAPPING]
        paths.extend(
            lane.ROOT / row["path"] for row in self.certificate["postflights"]
        )
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(lane.COEFFICIENT_PAYLOAD_RE.search(text), path)
            value = json.loads(text)
            self.assertFalse(value.get("coefficientMaterialIncluded", False), path)

    def test_finalizer_has_no_network_or_commit_path(self) -> None:
        source = Path(finalizer.__file__).read_text(encoding="utf-8")
        self.assertNotIn("api_json(", source)
        self.assertNotIn('"--commit"', source)
        self.assertNotIn("command_submit", source)


if __name__ == "__main__":
    unittest.main()
