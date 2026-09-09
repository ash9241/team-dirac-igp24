from __future__ import annotations

import hashlib
import json
import unittest

import audit_full_ledger_gold_reintersection as audit
import audit_low_contention_pair_routes as pair_audit


class FullLedgerGoldReintersectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate_text = audit.CERTIFICATE.read_text(encoding="utf-8")
        cls.certificate = json.loads(cls.certificate_text)
        cls.summary = json.loads(audit.SUMMARY.read_text(encoding="utf-8"))

    def test_post_backfill_safe_frontier_is_internally_complete(self) -> None:
        zero = self.certificate["zeroCertificate"]
        self.assertTrue(zero["immediateExactOrGuaranteedSafeFrontierExhausted"])
        self.assertEqual(zero["immediateCandidateCount"], 0)
        self.assertEqual(zero["alreadyExactHits"], 0)
        self.assertEqual(zero["savedAllCompatibleExactOptionSets"], 0)
        self.assertEqual(zero["deterministicSingleOrbitRunbooks"], 0)
        self.assertEqual(zero["allCompatibleSafeRoutes"], 0)
        self.assertGreater(zero["conditionalRoutesRemain"], 0)
        policy = self.certificate["conditionalExecutionPolicy"]
        self.assertFalse(policy["automaticHeavyExecutionAuthorized"])
        self.assertEqual(policy["strictResolvedPackets"], 14)
        self.assertEqual(self.certificate["boundary"]["acceptedScoreablePairs"], 22062)

    def test_latest_action_and_seven_profile_batches_are_included(self) -> None:
        corpus = self.certificate["sealedPairCorpus"]
        gaps = self.certificate["staleOrIncompleteCoverage"]
        self.assertEqual(corpus["priorMaps"], 23)
        self.assertEqual(corpus["actionMapsIncludingCompletedV20"], 32)
        self.assertTrue(corpus["v20OutputComplete"])
        self.assertTrue(corpus["v21OutputComplete"])
        self.assertEqual(corpus["v21Signatures"], 32)
        self.assertTrue(corpus["profileBackfillOutputComplete"])
        self.assertEqual(corpus["profileBackfillSignatures"], 32)
        self.assertTrue(corpus["profileBackfillBatch2OutputComplete"])
        self.assertEqual(corpus["profileBackfillBatch2Signatures"], 35)
        self.assertTrue(corpus["profileBackfillBatch3OutputComplete"])
        self.assertEqual(corpus["profileBackfillBatch3Signatures"], 36)
        self.assertTrue(corpus["profileBackfillBatch4OutputComplete"])
        self.assertEqual(corpus["profileBackfillBatch4Signatures"], 36)
        self.assertTrue(corpus["profileBackfillBatch5OutputComplete"])
        self.assertEqual(corpus["profileBackfillBatch5Signatures"], 32)
        self.assertTrue(corpus["profileBackfillBatch6OutputComplete"])
        self.assertEqual(corpus["profileBackfillBatch6Signatures"], 50)
        self.assertTrue(corpus["profileBackfillBatch7OutputComplete"])
        self.assertEqual(corpus["profileBackfillBatch7Signatures"], 50)
        self.assertEqual(corpus["profileBackfillCombinedSignatures"], 271)
        self.assertFalse(gaps["v20CensusOutputAbsent"])
        self.assertFalse(gaps["v21CensusOutputAbsent"])
        self.assertFalse(gaps["profileBackfillBatch3OutputAbsent"])
        self.assertFalse(gaps["profileBackfillBatch4OutputAbsent"])
        self.assertFalse(gaps["profileBackfillBatch5OutputAbsent"])
        self.assertFalse(gaps["profileBackfillBatch6OutputAbsent"])
        self.assertFalse(gaps["profileBackfillBatch7OutputAbsent"])
        self.assertEqual(gaps["missingExactActionPairCount"], 0)
        self.assertEqual(gaps["missingExactActionPairs"], [])

    def test_routes_and_target_index_are_deduplicated(self) -> None:
        classes = self.certificate["classifications"]
        routes = classes["conditionalRoutes"]
        route_ids = [row["routeId"] for row in routes]
        all_route_ids = set(route_ids) | {
            row["routeId"]
            for key in (
                "deterministicSingleOrbitRunbooks",
                "allCompatibleSafeRoutes",
            )
            for row in classes[key]
        }
        self.assertEqual(len(route_ids), len(set(route_ids)))
        self.assertTrue(all(row["certaintyClass"] == "conditional" for row in routes))
        self.assertTrue(all(row["currentGoldTargetPairs"] for row in routes))
        targets = classes["conditionalTargetIndex"]
        pairs = [row["pair"] for row in targets]
        self.assertEqual(len(pairs), len(set(pairs)))
        self.assertEqual(
            set(row["bestRouteId"] for row in targets) - all_route_ids, set()
        )
        self.assertEqual(len(routes), self.summary["conditionalRoutes"])
        self.assertEqual(len(targets), self.summary["conditionalDistinctTargets"])

    def test_certificate_is_coefficient_free_and_light_only(self) -> None:
        self.assertIsNone(pair_audit.COEFFICIENT_LINE_RE.search(self.certificate_text))
        self.assertNotIn('"coefficientLine"', self.certificate_text)
        self.assertNotIn('"coefficients"', self.certificate_text)
        self.assertFalse(self.certificate["coefficientMaterialIncluded"])
        self.assertFalse(self.certificate["credentialMaterialIncluded"])
        self.assertTrue(all(self.certificate["checks"].values()))
        self.assertEqual(
            self.certificate["sideEffects"],
            {
                "gapRuns": 0,
                "heavyWorkersLaunched": 0,
                "ledgerWrites": 0,
                "networkCalls": 0,
                "polynomialArithmeticRuns": 0,
                "sageRuns": 0,
                "submissionCalls": 0,
            },
        )

    def test_summary_pins_certificate(self) -> None:
        digest = hashlib.sha256(self.certificate_text.encode("utf-8")).hexdigest()
        self.assertEqual(self.summary["certificate"]["sha256"], digest)
        self.assertEqual(
            self.summary["status"],
            "certified_zero_immediate_gold_frontier_conditional_only",
        )


if __name__ == "__main__":
    unittest.main()
