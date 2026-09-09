from __future__ import annotations

import json
import unittest

import audit_low_contention_pair_routes as pair_audit
import seal_conditional_factor_calibration as calibration


class ConditionalFactorCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = calibration.CERTIFICATE.read_text(encoding="utf-8")
        cls.value = json.loads(cls.text)

    def test_strict_packet_scope_and_outcomes(self) -> None:
        census = self.value["strictPacketCensus"]
        self.assertEqual(census["independentlyFactoredConditionalPackets"], 14)
        self.assertEqual(census["exactFrobeniusMultiPackets"], 10)
        self.assertEqual(census["directExactSinglePackets"], 4)
        self.assertEqual(census["freshGoldSuccesses"], 0)
        self.assertEqual(census["coveredOrNongoldMisses"], 14)
        self.assertEqual(len(self.value["packets"]), 14)

    def test_clean_mass_calibration_is_exact_and_separate(self) -> None:
        value = self.value["calibration"]
        self.assertEqual(value["cleanCompatibleClassMassPackets"], 6)
        self.assertEqual(value["cleanNominalExpectedHitsExact"], "105689/36225")
        self.assertEqual(
            value["cleanNominalIidZeroHitProbabilityExact"], "28672/3234375"
        )
        legacy = [
            row for row in self.value["packets"]
            if row["scopeClass"] == "legacy_post_factor_assignment_prior"
        ]
        self.assertEqual(len(legacy), 8)
        self.assertTrue(all("not_class_mass" in row["nominal"]["metric"] for row in legacy))

    def test_pending_lane_is_paused_without_deterministic_evidence(self) -> None:
        decision = self.value["decision"]
        self.assertTrue(decision["automaticExecutionPaused"])
        self.assertEqual(decision["independentDeterministicEvidenceTasks"], [])
        self.assertGreater(decision["pendingTasks"], 100)
        self.assertTrue(
            all(
                row["automaticHeavyExecutionAuthorized"] is False
                and row["independentDeterministicEvidence"] is False
                for row in self.value["pendingConditionalTasks"]
            )
        )

    def test_metadata_is_coefficient_free_and_light_only(self) -> None:
        self.assertIsNone(pair_audit.COEFFICIENT_LINE_RE.search(self.text))
        self.assertFalse(self.value["coefficientMaterialIncluded"])
        self.assertTrue(all(self.value["checks"].values()))
        self.assertEqual(self.value["sideEffects"]["heavyWorkersLaunched"], 0)
        self.assertEqual(self.value["sideEffects"]["networkCalls"], 0)
        self.assertEqual(self.value["sideEffects"]["submissionCalls"], 0)


if __name__ == "__main__":
    unittest.main()
