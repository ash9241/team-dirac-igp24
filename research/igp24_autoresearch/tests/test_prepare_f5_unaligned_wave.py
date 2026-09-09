from __future__ import annotations

import argparse
import importlib.util
import json
import unittest
from collections import Counter
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "prepare_f5_unaligned_wave.py"


def load_module():
    spec = importlib.util.spec_from_file_location("prepare_f5_unaligned_wave", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class EmpiricalUnalignedSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def row(
        self,
        label: str,
        source_r: int,
        canonical: str,
        pairs: list[tuple[str, int]],
        utility: Fraction,
        trials: int = 8,
        height: int = 32,
    ) -> dict:
        p = self.module
        return {
            "actionSha256": "a" * 64,
            "canonicalQuotientSha256": canonical,
            "coefficientHeightBits": height,
            "empiricalTransition": {
                "allowedTargetSignatures": sorted({r for _, r in pairs} | {source_r}),
                "profileTrials": trials,
                "profileGoldTransitions": 1,
                "goldToAllowedRatio": p.fraction_payload(Fraction(len(pairs), len(pairs) + 1)),
                "rawPosterior": p.fraction_payload(utility),
                "confidenceShrunkPosterior": p.fraction_payload(utility),
                "heightCost": p.fraction_payload(Fraction(1, 1)),
                "utility": p.fraction_payload(utility),
            },
            "possibleGoldPairs": [
                {"label": pair[0], "r": pair[1]} for pair in pairs
            ],
            "signatureAligned": False,
            "source": {
                "coefficientSha256": canonical[::-1],
                "label": label,
                "polynomialIndex": 0,
                "r": source_r,
                "submissionId": "sub_test",
                "t": 1,
            },
        }

    def test_transition_estimate_exact_fraction_and_zero_trial_calibration(self):
        p = self.module
        counts = Counter({0: 6, 4: 4})
        estimate = p.transition_estimate(counts, (0, 4, 8, 12), {4, 8}, 80)
        expected_raw = (Fraction(4) + Fraction(1, 2) * 2) / (
            Fraction(10) + Fraction(1, 2) * 4
        )
        expected_posterior = (
            10 * expected_raw + 10 * Fraction(3, 65)
        ) / 20
        self.assertEqual(estimate["raw"], expected_raw)
        self.assertEqual(estimate["posterior"], expected_posterior)
        self.assertEqual(estimate["cost"], Fraction(528, 512))
        empty = p.transition_estimate(Counter(), (0, 4, 8), {8}, 32)
        self.assertEqual(empty["posterior"], Fraction(3, 65))

    def test_representative_and_greedy_selection_are_deterministic_and_disjoint(self):
        p = self.module
        rows = [
            self.row("24T1", 0, "1" * 64, [("24T9", 4), ("24T9", 8)], Fraction(1, 2)),
            self.row("24T1", 0, "2" * 64, [("24T9", 4), ("24T9", 8)], Fraction(1, 3), height=16),
            self.row("24T2", 4, "3" * 64, [("24T8", 0)], Fraction(2, 5)),
            self.row("24T3", 8, "4" * 64, [("24T9", 4)], Fraction(9, 10)),
            self.row("24T4", 12, "5" * 64, [("24T7", 0)], Fraction(1, 4)),
        ]
        representatives = p.one_representative_per_source_signature(rows)
        self.assertEqual(len(representatives), 4)
        self.assertEqual(
            next(row for row in representatives if row["source"]["label"] == "24T1")[
                "canonicalQuotientSha256"
            ],
            "1" * 64,
        )
        selected = p.greedy_select(representatives, 3)
        reversed_selected = p.greedy_select(list(reversed(representatives)), 3)
        self.assertEqual(
            json.dumps(selected, separators=(",", ":"), sort_keys=True),
            json.dumps(reversed_selected, separators=(",", ":"), sort_keys=True),
        )
        all_pairs = [pair for row in selected for pair in p.candidate_pairs(row)]
        self.assertEqual(len(all_pairs), len(set(all_pairs)))
        self.assertTrue(all(row["pairOverlapAtSelection"] == 0 for row in selected))

    def test_profile_validation_rejects_invalid_gold_subset(self):
        p = self.module
        with self.assertRaisesRegex(ValueError, "nonempty subset"):
            p.transition_estimate(Counter({0: 1}), (0, 4), {8}, 16)
        with self.assertRaisesRegex(ValueError, "no allowed"):
            p.profile_key(0, [])

    def test_live_reference_aggregate_and_frozen_validation_boundary(self):
        p = self.module
        args = argparse.Namespace(
            wave_id="f5_unaligned_frontier_20260722_wave5",
            destination=Path("data/f5_unaligned_frontier_20260722_wave5"),
            manifest=Path("outbox/f5_unaligned_frontier_20260722_wave5.txt"),
            report=None,
            max_sources=50,
            height_cap_bits=256,
            allow_pinned_stale_queue=True,
            stdout_only=True,
            aggregate_only=True,
            seal=False,
        )
        state = p.build_state(args)
        preview = state["preview"]
        self.assertEqual(preview["blockers"], [])
        self.assertEqual(preview["training"]["developmentRows"], 256)
        self.assertEqual(preview["training"]["holdoutRows"], 115)
        self.assertEqual(preview["training"]["resolvedRows"], 371)
        self.assertEqual(preview["training"]["profiles"], 36)
        self.assertEqual(
            preview["training"]["modelFreezePolicy"],
            "validated_original_371_only",
        )
        post_validation = preview["training"][
            "postValidationEvidenceExcludedFromModel"
        ]
        self.assertEqual(post_validation["resolvedRows"], 4)
        self.assertEqual(len(post_validation["artifacts"]), 1)
        self.assertTrue(post_validation["includedInPriorAndSourceExclusions"])
        validation = preview["training"]["validation"]
        self.assertEqual(validation["rows"], 115)
        self.assertAlmostEqual(validation["topSignatureAccuracy"], 0.739, places=3)
        self.assertAlmostEqual(validation["meanAssignedProbability"], 0.624, places=3)
        self.assertAlmostEqual(validation["logLoss"], 0.724, places=3)
        self.assertAlmostEqual(validation["uniformLogLoss"], 1.122, places=3)
        unseen = validation["unseenSourceLabels"]
        self.assertEqual(unseen["rows"], 18)
        self.assertAlmostEqual(unseen["topSignatureAccuracy"], 0.722, places=3)
        self.assertAlmostEqual(unseen["logLoss"], 1.054, places=3)
        self.assertAlmostEqual(unseen["uniformLogLoss"], 1.405, places=3)
        selection = preview["selection"]
        self.assertEqual(selection["selectedSources"], 50)
        self.assertEqual(selection["selectedSourceSignatures"], 50)
        self.assertEqual(selection["selectedCanonicalSources"], 50)
        self.assertEqual(selection["priorResultSourceSignatureOverlap"], 0)
        self.assertEqual(selection["distinctPairs"], 74)
        self.assertEqual(selection["multiGoldSources"], 18)
        self.assertEqual(selection["medianHeightBits"], 34)
        self.assertEqual(selection["maximumHeightBits"], 150)
        self.assertEqual(selection["pairSlotOverlap"], 0)
        self.assertEqual(
            selection["coefficientFreeSelectionSha256"],
            "e4ac3458f6981ea3e9a5078f92c404098fc89996a5640ceaa1e5b4398d46275e",
        )
        self.assertTrue(preview["reference"]["matches"])
        self.assertFalse(
            (ROOT / "data/f5_unaligned_frontier_20260722_wave5").exists()
        )
        self.assertFalse(
            (ROOT / "outbox/f5_unaligned_frontier_20260722_wave5.txt").exists()
        )


if __name__ == "__main__":
    unittest.main()
