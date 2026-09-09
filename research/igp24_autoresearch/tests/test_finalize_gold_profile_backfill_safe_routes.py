from __future__ import annotations

import hashlib
import json
import unittest

import audit_full_ledger_gold_reintersection as audit
import finalize_gold_profile_backfill_safe_routes as finalize


class GoldProfileBackfillSafeRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = finalize.CERTIFICATE.read_text(encoding="utf-8")
        cls.certificate = json.loads(cls.text)
        cls.summary = json.loads(finalize.SUMMARY.read_text(encoding="utf-8"))

    def test_batch7_has_no_deterministic_or_all_compatible_route(self) -> None:
        census = self.certificate["newRouteCensus"]
        self.assertEqual(census["newDeterministicSingleOrbitRoutes"], 0)
        self.assertEqual(census["newAllCompatibleSafeRoutes"], 0)
        self.assertEqual(census["isolatedFactorRunbooks"], 0)
        self.assertEqual(census["distinctPossibleGoldPairs"], 0)
        self.assertEqual(self.certificate["runbooks"], [])
        self.assertEqual(self.summary["status"], "certified_no_new_safe_routes")
        self.assertIsNone(self.summary["bestNextHeavyCommand"])

    def test_batch7_profile_artifact_is_exactly_pinned(self) -> None:
        expected = audit.artifact(finalize.BACKFILL)
        self.assertEqual(self.certificate["profileBackfill"], expected)
        self.assertEqual(
            self.certificate["boundary"]["acceptedScoreablePairs"], 22062
        )

    def test_current_full_reintersection_is_pinned(self) -> None:
        current = hashlib.sha256(audit.CERTIFICATE.read_bytes()).hexdigest()
        self.assertEqual(self.certificate["fullReintersection"]["sha256"], current)
        self.assertTrue(all(self.certificate["checks"].values()))

    def test_light_only_and_summary_pin(self) -> None:
        self.assertIsNone(audit.pair_audit.COEFFICIENT_LINE_RE.search(self.text))
        self.assertNotIn('"coefficientLine"', self.text)
        self.assertFalse(self.certificate["coefficientMaterialIncluded"])
        self.assertEqual(self.certificate["sideEffects"]["sageRuns"], 0)
        self.assertEqual(self.certificate["sideEffects"]["networkCalls"], 0)
        self.assertEqual(self.certificate["sideEffects"]["submissionCalls"], 0)
        digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        self.assertEqual(self.summary["certificate"]["sha256"], digest)
        self.assertFalse(self.summary["heavyWorkerLaunched"])


if __name__ == "__main__":
    unittest.main()
