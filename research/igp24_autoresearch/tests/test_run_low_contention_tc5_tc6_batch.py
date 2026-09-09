from __future__ import annotations

import unittest
from pathlib import Path

import run_low_contention_sequential as lane
import run_low_contention_tc5_tc6_batch as batch
import sair_api


class FinalTc5Tc6BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = lane.read_json(batch.ROUTE_CERTIFICATE)
        cls.routes = cls.certificate["runbooks"]

    def test_four_tc5_then_ten_tc6_and_best_route_first(self) -> None:
        self.assertEqual(len(self.routes), 14)
        self.assertEqual(
            self.routes[0]["routeId"],
            "hc5_001_24T12235_r24_to_24T12337_r24",
        )
        self.assertEqual(
            [row["target"]["teamCountAtSeal"] for row in self.routes],
            [5] * 4 + [6] * 10,
        )

    def test_boundary_and_tc4_receipt_lineage_are_current(self) -> None:
        if batch.BATCH_CERTIFICATE.exists():
            executed = lane.read_json(batch.BATCH_CERTIFICATE)
            actual = executed["executionBoundaryBeforeAndAfter"]
            batch.validate_boundary(self.certificate, actual)
            lineage = executed["tc4ReceiptLineage"]
        else:
            with lane.connect_ro() as connection:
                actual = batch.boundary_snapshot(connection)
                receipt_hashes, receipt_pairs, _audit = lane.receipt_snapshot(connection)
            batch.validate_boundary(self.certificate, actual)
            lineage = batch.validate_receipt_lineage(
                self.certificate, receipt_hashes, receipt_pairs
            )
        self.assertEqual(lineage["exactHashesExcluded"], 22)
        self.assertEqual(lineage["exactPairsExcluded"], 22)

    def test_artifact_states_are_fail_closed_and_actions_exact(self) -> None:
        for row in self.routes:
            state = lane.artifact_state(row)
            self.assertFalse(state["resultTemporary"], (row["routeId"], state))
            if state["result"]:
                self.assertTrue(state["postflight"], row["routeId"])
                self.assertTrue(state["manifest"], row["routeId"])
                self.assertTrue(state["stageCertificate"], row["routeId"])
            else:
                self.assertFalse(
                    state["postflight"] or state["manifest"] or state["stageCertificate"],
                    (row["routeId"], state),
                )
            batch.validate_route(row)

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
            row for row in certificate["routes"]
            if row["status"].startswith("certified_exact_")
        ]
        self.assertEqual(certificate["successes"], 14)
        self.assertEqual(certificate["failClosedSkips"], 0)
        self.assertEqual(certificate["teamCountDistribution"], {"5": 4, "6": 10})
        self.assertEqual(certificate["projectedMarginalScoreExact"], "9/32")
        self.assertFalse(certificate["combinedOfflineDryRun"]["commit"])
        self.assertEqual(mapping["routeCount"], 14)
        self.assertEqual(mapping["distinctCandidateHashes"], 14)
        self.assertEqual(mapping["distinctTargetPairs"], 14)
        self.assertEqual(hashes, [row["candidateSha256"] for row in successful])
        self.assertEqual(hashes, [row["candidateSha256"] for row in mapping["mappings"]])
        for route, report in zip(self.routes, successful, strict=True):
            postflight = lane.read_json(lane.planned_paths(route)["postflight"])
            self.assertTrue(postflight["candidate"]["irreducible"])
            self.assertTrue(postflight["candidate"]["exactTargetAssignment"])
            self.assertTrue(
                postflight["actionAssignment"]["deterministicAcrossCompatibleClasses"]
            )
            self.assertEqual(
                postflight["candidate"]["coefficientSha256"], report["candidateSha256"]
            )


if __name__ == "__main__":
    unittest.main()
