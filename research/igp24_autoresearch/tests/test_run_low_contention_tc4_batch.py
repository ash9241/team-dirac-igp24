from __future__ import annotations

import unittest
from pathlib import Path

import run_low_contention_sequential as lane
import run_low_contention_tc4_batch as batch
import sair_api


class Tc4BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = lane.read_json(batch.ROUTE_CERTIFICATE)
        cls.routes = cls.certificate["runbooks"]

    def test_exactly_twenty_two_tc4_routes_first_signal_pinned(self) -> None:
        self.assertEqual(len(self.routes), 22)
        self.assertEqual(
            self.routes[0]["routeId"],
            "hc4_001_24T6195_r12_to_24T6237_r4",
        )
        self.assertTrue(
            all(row["target"]["teamCountAtSeal"] == 4 for row in self.routes)
        )

    def test_all_artifact_states_are_fail_closed_and_commands_are_exact(self) -> None:
        for row in self.routes:
            state = lane.artifact_state(row)
            self.assertFalse(state["resultTemporary"], (row["routeId"], state))
            if state["result"]:
                self.assertTrue(state["postflight"], row["routeId"])
                self.assertTrue(state["manifest"], row["routeId"])
                self.assertTrue(state["stageCertificate"], row["routeId"])
            else:
                self.assertFalse(
                    state["postflight"]
                    or state["manifest"]
                    or state["stageCertificate"],
                    (row["routeId"], state),
                )
            lane.validate_heavy_command(row)
            self.assertEqual(row["exactAction"]["length24OrbitCount"], 1)
            self.assertTrue(row["exactAction"]["deterministicAcrossCompatibleClasses"])

    def test_required_tc3_receipt_seal_is_intact(self) -> None:
        fact = batch.validate_tc3_seal()
        self.assertEqual(fact["path"], "data/low_contention_tc3_frontier_receipt_mapping.json")

    def test_no_network_or_commit_path(self) -> None:
        source = Path(batch.__file__).read_text(encoding="utf-8")
        self.assertNotIn("api_json(", source)
        self.assertNotIn('"--commit"', source)
        self.assertNotIn("command_submit", source)

    def test_completed_batch_is_exact_postflighted_and_mapping_ready(self) -> None:
        if not batch.BATCH_CERTIFICATE.exists():
            self.skipTest("batch has not executed")
        certificate = lane.read_json(batch.BATCH_CERTIFICATE)
        mapping = lane.read_json(batch.MAPPING_READY)
        _lines, hashes = sair_api.validated_manifest(batch.BATCH_MANIFEST)
        successful = [
            row
            for row in certificate["routes"]
            if row["status"].startswith("certified_exact_")
        ]
        self.assertEqual(certificate["successes"], 22)
        self.assertEqual(certificate["failClosedSkips"], 0)
        self.assertEqual(certificate["projectedMarginalScoreExact"], "11/8")
        self.assertFalse(certificate["combinedOfflineDryRun"]["commit"])
        self.assertEqual(certificate["combinedOfflineDryRun"]["polynomials"], 22)
        self.assertEqual(mapping["routeCount"], 22)
        self.assertEqual(mapping["distinctCandidateHashes"], 22)
        self.assertEqual(mapping["distinctTargetPairs"], 22)
        self.assertEqual(hashes, [row["candidateSha256"] for row in successful])
        self.assertEqual(hashes, [row["candidateSha256"] for row in mapping["mappings"]])
        for route, report in zip(self.routes, successful, strict=True):
            postflight = lane.read_json(lane.planned_paths(route)["postflight"])
            self.assertEqual(
                postflight["status"],
                "certified_exact_novel_live_not_staged_not_submitted",
            )
            self.assertTrue(postflight["candidate"]["irreducible"])
            self.assertTrue(postflight["candidate"]["exactTargetAssignment"])
            self.assertEqual(
                postflight["candidate"]["coefficientSha256"],
                report["candidateSha256"],
            )


if __name__ == "__main__":
    unittest.main()
