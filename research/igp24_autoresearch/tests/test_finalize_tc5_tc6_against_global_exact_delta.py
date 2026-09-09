from __future__ import annotations

import unittest
from pathlib import Path

import finalize_tc5_tc6_against_global_exact_delta as finalizer
import run_low_contention_sequential as lane
import sair_api


class FinalGlobalDeltaFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = lane.read_json(finalizer.FINAL_CERTIFICATE)
        cls.mapping = lane.read_json(finalizer.FINAL_MAPPING)
        cls.delta = lane.read_json(finalizer.DELTA_CERTIFICATE)

    def test_global_delta_is_pinned_exact_three(self) -> None:
        self.assertEqual(
            lane.sha256_path(finalizer.DELTA_CERTIFICATE),
            finalizer.DELTA_CERTIFICATE_SHA256,
        )
        self.assertEqual(
            lane.sha256_path(finalizer.DELTA_MANIFEST),
            finalizer.DELTA_MANIFEST_SHA256,
        )
        self.assertEqual(len(self.delta["selected"]), 3)
        self.assertTrue(all(self.delta["checks"].values()))

    def test_final_manifest_is_duplicate_free_and_exactly_mapped(self) -> None:
        _lines, hashes = sair_api.validated_manifest(finalizer.FINAL_MANIFEST)
        reserved_hashes = {
            row["coefficientSha256"] for row in self.delta["selected"]
        }
        reserved_pairs = {row["pair"] for row in self.delta["selected"]}
        mapped_hashes = [
            row["candidateSha256"] for row in self.mapping["mappings"]
        ]
        mapped_pairs = {
            row["targetPair"] for row in self.mapping["mappings"]
        }
        self.assertEqual(len(hashes), 14)
        self.assertEqual(hashes, mapped_hashes)
        self.assertFalse(set(hashes) & reserved_hashes)
        self.assertFalse(mapped_pairs & reserved_pairs)
        self.assertEqual(len(set(hashes)), 14)
        self.assertEqual(len(mapped_pairs), 14)

    def test_final_certificate_is_safe_and_supersedes_original(self) -> None:
        self.assertEqual(self.certificate["excludedRows"], 0)
        self.assertEqual(self.certificate["finalRows"], 14)
        self.assertEqual(self.certificate["pairOverlap"], [])
        self.assertEqual(self.certificate["hashOverlap"], [])
        self.assertTrue(all(self.certificate["checks"].values()))
        self.assertTrue(
            self.certificate["originalCombinedManifestSupersededForSubmission"]
        )
        self.assertFalse(self.certificate["offlineDryRun"]["commit"])
        self.assertEqual(self.certificate["sideEffects"]["networkCalls"], 0)
        self.assertEqual(self.certificate["sideEffects"]["submissionCalls"], 0)

    def test_filter_program_has_no_network_or_commit_path(self) -> None:
        source = Path(finalizer.__file__).read_text(encoding="utf-8")
        self.assertNotIn("api_json(", source)
        self.assertNotIn('"--commit"', source)
        self.assertNotIn("command_submit", source)


if __name__ == "__main__":
    unittest.main()
