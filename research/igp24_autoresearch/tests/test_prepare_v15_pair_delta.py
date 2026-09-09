from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import prepare_v15_pair_delta as prepare


class PrepareV15PairDeltaTests(unittest.TestCase):
    def test_pinned_v14_boundary_has_exact_two_of_two_lineage_closure(self) -> None:
        rows, pairs = prepare.validate_v14_boundary()
        self.assertEqual(len(rows), prepare.EXPECTED_V14_GROUP_ROWS)
        self.assertEqual(len(pairs), prepare.EXPECTED_V14_OWNED_PAIRS)

        certificate = prepare.helper.read_json(
            prepare.V14 / "route_triage_certificate.json"
        )
        counts = certificate["counts"]
        self.assertEqual(counts["conditionalRoutes"], 2)
        self.assertEqual(counts["cachedExactLineageClosures"], 2)
        self.assertEqual(counts["exactMisses"], 2)
        self.assertEqual(counts["unresolvedExecutableRoutes"], 0)

    def test_prior_map_chain_is_complete_through_pinned_v14(self) -> None:
        _, pairs = prepare.validate_v14_boundary()
        artifacts, signature_pairs = prepare.validate_prior_maps(pairs)
        self.assertEqual(len(artifacts), len(prepare.PRIOR_MAPS))
        self.assertEqual(
            artifacts[-1]["path"],
            "data/autopilot_pair_delta_20260722_v14/missing_pair_all.jsonl",
        )
        self.assertEqual(
            artifacts[-1]["sha256"],
            prepare.V14_PINS[artifacts[-1]["path"]],
        )
        self.assertTrue(signature_pairs <= pairs)

    def test_coefficient_and_credential_scan_rejects_sensitive_material(self) -> None:
        with self.assertRaises(ValueError):
            prepare.assert_coefficient_free({"coefficients": [0] * 25}, "fixture")
        with self.assertRaises(ValueError):
            prepare.assert_coefficient_free(
                {"line": ",".join(str(index) for index in range(25))},
                "fixture",
            )
        with self.assertRaises(ValueError):
            prepare.assert_coefficient_free(
                {"authorization": "Bearer redacted"},
                "fixture",
            )

    def test_default_phase_is_light_nonfinal_and_rerunnable(self) -> None:
        data_root = prepare.ROOT / "data"
        with tempfile.TemporaryDirectory(dir=data_root) as temporary:
            output_root = Path(temporary)
            replacements = {
                "V15": output_root,
                "GROUP_INPUT": output_root / "group_input.jsonl",
                "GROUP_SUMMARY": output_root / "group_input_summary.json",
                "CENSUS_INPUT": output_root / "census_input.jsonl",
                "INVENTORY": output_root / "exact_source_inventory.json",
                "PLAN": output_root / "provenance_plan.json",
                "PREFLIGHT": output_root / "preflight_audit_summary.json",
                "OUTPUT": output_root / "missing_pair_all.jsonl",
            }
            stack = contextlib.ExitStack()
            with stack:
                for name, value in replacements.items():
                    stack.enter_context(mock.patch.object(prepare, name, value))
                stack.enter_context(mock.patch.object(
                    prepare,
                    "parse_args",
                    return_value=argparse.Namespace(
                        finalize_heavy_plan=False,
                        expected_delta_pairs=None,
                    ),
                ))
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(prepare.main(), 0)

            event = json.loads(stdout.getvalue().strip().splitlines()[-1])
            self.assertEqual(event["status"], "prepared_awaiting_boundary_confirmation")
            self.assertFalse(event["heavyCommandFrozen"])
            self.assertFalse(event["heavyWorkerLaunched"])
            self.assertTrue(event["outputAbsentGuardEnabled"])
            self.assertGreaterEqual(event["deltaPairs"], 2)

            plan = json.loads(replacements["PLAN"].read_text(encoding="utf-8"))
            preflight = json.loads(
                replacements["PREFLIGHT"].read_text(encoding="utf-8")
            )
            self.assertIsNone(plan["execution"]["guardedPlannedCommand"])
            self.assertFalse(plan["execution"]["heavyCommandFrozen"])
            self.assertFalse(plan["execution"]["heavyWorkerLaunched"])
            self.assertEqual(
                preflight["status"],
                "certified_light_snapshot_awaiting_boundary_confirmation",
            )
            self.assertTrue(preflight["checks"]["v14LineageClosureTwoOfTwo"])
            self.assertTrue(preflight["checks"]["priorMapChainCompleteThroughV14"])
            self.assertFalse(preflight["coefficientMaterialIncluded"])
            self.assertFalse(preflight["credentialMaterialIncluded"])


if __name__ == "__main__":
    unittest.main()
