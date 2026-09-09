from __future__ import annotations

import unittest
from pathlib import Path

import audit_postv18_nonpair_closure as audit


class PostV18NonpairClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = __import__("json").loads(
            audit.CERTIFICATE.read_text(encoding="utf-8")
        )
        cls.runbook = __import__("json").loads(
            audit.RUNBOOK.read_text(encoding="utf-8")
        )

    def test_exact_25_row_boundary_and_no_cached_hits(self) -> None:
        boundary = self.certificate["sourceBoundary"]
        self.assertEqual(boundary["rows"], 25)
        self.assertEqual(boundary["distinctKeys"], 25)
        self.assertEqual(boundary["distinctHashes"], 25)
        self.assertEqual(boundary["distinctPairs"], 25)
        census = self.certificate["exactCacheCensus"]
        self.assertEqual(census["normalizedDirectSourceCandidates"], 0)
        self.assertEqual(census["eligibleBeforePairDedup"], 0)
        self.assertEqual(census["selectedExactHits"], 0)
        self.assertIsNone(self.certificate["manifest"])
        self.assertFalse(audit.MANIFEST.exists())

    def test_all_requested_family_caches_are_closed(self) -> None:
        families = self.certificate["familyDirectMatchAudit"]
        self.assertEqual(set(families), set(audit.FAMILY_PATHS))
        for value in families.values():
            self.assertEqual(value["directSourcePairObjects"], 0)
            self.assertEqual(value["directAnchorHashObjects"], 0)

    def test_two_even_twist_sources_have_guarded_commands(self) -> None:
        self.assertEqual(len(self.runbook["sources"]), 2)
        self.assertEqual(
            {(row["label"], row["r"]) for row in self.runbook["sources"]},
            {("24T4525", 24), ("24T12337", 24)},
        )
        self.assertIn("test ! -e", self.runbook["actionCommand"])
        self.assertIn("build_even_twist_action_subset.sage.py", self.runbook["actionCommand"])
        self.assertIn("test ! -e", self.runbook["workerCommand"])
        self.assertIn("even_twist_delta_scan.sage.py", self.runbook["workerCommand"])
        self.assertFalse(self.runbook["submissionAuthorized"])
        self.assertTrue(self.runbook["outputsAbsentAtSeal"])

    def test_certificate_is_coefficient_free_light_only(self) -> None:
        text = audit.CERTIFICATE.read_text(encoding="utf-8")
        self.assertIsNone(audit.prior_audit.COEFFICIENT_RE.search(text))
        self.assertTrue(all(self.certificate["checks"].values()))
        self.assertFalse(self.certificate["coefficientMaterialIncluded"])
        self.assertEqual(self.certificate["sideEffects"]["sageRuns"], 0)
        self.assertEqual(self.certificate["sideEffects"]["gapRuns"], 0)
        self.assertEqual(self.certificate["sideEffects"]["networkCalls"], 0)
        self.assertEqual(self.certificate["sideEffects"]["submissionCalls"], 0)
        source = Path(audit.__file__).read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("api_json(", source)


if __name__ == "__main__":
    unittest.main()
