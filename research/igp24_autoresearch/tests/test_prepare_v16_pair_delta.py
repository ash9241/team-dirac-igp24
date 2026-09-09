from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import prepare_v16_pair_delta as prepare


class PrepareV16PairDeltaTests(unittest.TestCase):
    def test_pinned_v15_boundary_has_sole_exact_signature_miss(self) -> None:
        rows, pairs = prepare.validate_v15_boundary()
        self.assertEqual(len(rows), prepare.EXPECTED_V15_GROUP_ROWS)
        self.assertEqual(len(pairs), prepare.EXPECTED_V15_OWNED_PAIRS)

        certificate = prepare.helper.read_json(
            prepare.ROOT / "data/v15_10512_r16_frobenius_certificate.json"
        )
        summary = prepare.helper.read_json(
            prepare.ROOT / "data/v15_10512_r16_to_11924_summary.json"
        )
        routes = [route for row in prepare.helper.read_jsonl(
            prepare.V15 / "missing_pair_all.jsonl"
        ) for route in row.get("routes") or []]
        assignments = [
            assignment
            for assignment in certificate["rows"][0]["assignments"]
            if assignment["targetLabel"] == prepare.V15_ROUTE_TARGET
        ]
        self.assertEqual(len(routes), 1)
        self.assertEqual(assignments[0]["targetR"], 8)
        self.assertEqual(summary["status"], "exact_signature_miss")
        self.assertNotIn(summary["realizedTargetR"], prepare.V15_ROUTE_GOLD_R)

    def test_prior_map_chain_is_complete_through_pinned_v15(self) -> None:
        _, pairs = prepare.validate_v15_boundary()
        artifacts, signature_pairs = prepare.validate_prior_maps(pairs)
        self.assertEqual(len(artifacts), len(prepare.PRIOR_MAPS))
        self.assertEqual(
            artifacts[-1]["path"],
            "data/autopilot_pair_delta_20260722_v15/missing_pair_all.jsonl",
        )
        self.assertEqual(
            artifacts[-1]["sha256"],
            prepare.V15_PINS[artifacts[-1]["path"]],
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
                "V16": output_root,
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
                events = []
                for _ in range(2):
                    stdout = io.StringIO()
                    with contextlib.redirect_stdout(stdout):
                        self.assertEqual(prepare.main(), 0)
                    events.append(json.loads(stdout.getvalue().strip().splitlines()[-1]))

            event = events[-1]
            self.assertEqual(event["status"], "prepared_awaiting_boundary_confirmation")
            self.assertFalse(event["heavyCommandFrozen"])
            self.assertFalse(event["heavyWorkerLaunched"])
            self.assertTrue(event["outputAbsentGuardEnabled"])
            self.assertGreaterEqual(event["deltaPairs"], 1)
            self.assertEqual(events[0]["deltaPairs"], events[1]["deltaPairs"])

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
            self.assertTrue(preflight["checks"]["v15SoleRouteExactMissClosure"])
            self.assertTrue(preflight["checks"]["priorMapChainCompleteThroughV15"])
            self.assertFalse(preflight["coefficientMaterialIncluded"])
            self.assertFalse(preflight["credentialMaterialIncluded"])
            self.assertFalse(replacements["OUTPUT"].exists())

            prepare.assert_coefficient_free(
                [json.loads(line) for line in replacements["GROUP_INPUT"].read_text().splitlines()],
                "generated group input",
            )
            prepare.assert_coefficient_free(
                [json.loads(line) for line in replacements["CENSUS_INPUT"].read_text().splitlines()],
                "generated census input",
            )
            for name in ("GROUP_SUMMARY", "INVENTORY", "PLAN", "PREFLIGHT"):
                prepare.assert_coefficient_free(
                    json.loads(replacements[name].read_text(encoding="utf-8")),
                    f"generated {name}",
                )

    def test_existing_worker_output_blocks_all_preparation(self) -> None:
        data_root = prepare.ROOT / "data"
        with tempfile.TemporaryDirectory(dir=data_root) as temporary:
            output = Path(temporary) / "missing_pair_all.jsonl"
            output.write_text("sealed\n", encoding="utf-8")
            args = argparse.Namespace(
                finalize_heavy_plan=False,
                expected_delta_pairs=None,
            )
            with mock.patch.object(prepare, "OUTPUT", output), mock.patch.object(
                prepare, "parse_args", return_value=args
            ):
                with self.assertRaises(FileExistsError):
                    prepare.main()


if __name__ == "__main__":
    unittest.main()
