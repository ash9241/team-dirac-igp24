from __future__ import annotations

import unittest
from fractions import Fraction
from pathlib import Path

import audit_low_contention_pair_routes as base
import audit_low_contention_tc10_tc12_routes as audit
import run_deterministic_frontier_coordinator as coordinator
import run_low_contention_sequential as lane


class LowContentionTc10Tc12AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificate = lane.read_json(audit.CERTIFICATE)
        cls.runbooks = cls.certificate["runbooks"]

    def test_layer_counts_projection_and_best_route(self) -> None:
        counts = self.certificate["runbookProjection"][
            "teamCountDistribution"
        ]
        self.assertEqual(counts, {"10": 13, "11": 8, "12": 3})
        expected = Fraction(13, 2**10) + Fraction(8, 2**11) + Fraction(
            3, 2**12
        )
        self.assertEqual(expected, Fraction(71, 4096))
        self.assertEqual(
            self.certificate["runbookProjection"]["marginalScoreExact"],
            "71/4096",
        )
        best = self.certificate["bestRoute"]
        self.assertEqual(
            best["routeId"],
            "hc10_001_24T6436_r24_to_24T5580_r24",
        )
        self.assertEqual(best["sourcePair"], "24T6436/r24")
        self.assertEqual(best["targetPair"], "24T5580/r24")
        self.assertEqual(best["projectedMarginalScoreExact"], "1/1024")
        self.assertEqual(best["priorityRank"], 1)

    def test_queued_v17_gold_and_all_outboxes_are_excluded(self) -> None:
        required = self.certificate["requiredReceiptAudit"]
        self.assertEqual(len(required), len(audit.REQUIRED_RECEIPTS))
        by_id = {row["submissionId"]: row for row in required}
        self.assertIn(audit.V17_RECEIPT_ID, by_id)
        v17 = by_id[audit.V17_RECEIPT_ID]
        self.assertEqual(v17["declaredPolynomials"], 2)
        self.assertEqual(v17["exactHashesExcluded"], 2)
        self.assertEqual(v17["exactPairsExcluded"], 2)
        self.assertEqual(v17["rejectedCount"], 0)
        queued = self.certificate["queuedV17GoldExclusion"]
        self.assertEqual(
            set(queued["pairs"]),
            {"24T11827/r16", "24T19066/r16"},
        )
        self.assertTrue(queued["receiptExcluded"])
        self.assertTrue(queued["outboxExcluded"])
        self.assertIn(audit.TC7_RECEIPT_ID, by_id)
        tc7_receipt = by_id[audit.TC7_RECEIPT_ID]
        self.assertEqual(tc7_receipt["declaredPolynomials"], 9)
        self.assertEqual(tc7_receipt["exactHashesExcluded"], 9)
        self.assertEqual(tc7_receipt["exactPairsExcluded"], 9)
        self.assertEqual(tc7_receipt["rejectedCount"], 0)
        tc7 = self.certificate["queuedTc7BatchExclusion"]
        self.assertEqual(
            set(tc7["pairs"]),
            {
                "24T17117/r24",
                "24T7848/r24",
                "24T8190/r24",
                "24T8192/r24",
                "24T17187/r4",
                "24T12798/r20",
                "24T10390/r20",
                "24T7949/r12",
                "24T19033/r12",
            },
        )
        self.assertTrue(tc7["receiptExcluded"])
        self.assertTrue(tc7["outboxExcluded"])
        index = lane.read_json(audit.OUTBOX_INDEX)
        self.assertTrue(all(index["checks"].values()))
        self.assertEqual(index["outboxFiles"], len(index["artifacts"]))

    def test_routes_are_distinct_fresh_exact_single_orbit_and_ranked(self) -> None:
        targets = set()
        source_hashes = set()
        source_keys = set()
        team_counts = []
        for priority, row in enumerate(self.runbooks, start=1):
            self.assertEqual(row["priorityRank"], priority)
            targets.add((row["target"]["label"], row["target"]["r"]))
            source_hashes.add(row["source"]["coefficientSha256"])
            source_keys.add(
                (
                    row["source"]["submissionId"],
                    row["source"]["polynomialIndex"],
                )
            )
            team_counts.append(row["target"]["teamCountAtSeal"])
            self.assertEqual(row["exactAction"]["length24OrbitCount"], 1)
            self.assertTrue(
                row["exactAction"][
                    "deterministicAcrossCompatibleClasses"
                ]
            )
            self.assertTrue(row["routeReliability"]["exactDeterministic"])
            self.assertFalse(row["guards"]["submissionAuthorized"])
            self.assertTrue(
                all(
                    value
                    for key, value in row["guards"].items()
                    if key != "submissionAuthorized"
                )
            )
        self.assertEqual(len(targets), len(self.runbooks))
        self.assertEqual(len(source_hashes), len(self.runbooks))
        self.assertEqual(len(source_keys), len(self.runbooks))
        self.assertEqual(team_counts, sorted(team_counts))
        self.assertEqual(team_counts.count(10), 13)
        self.assertEqual(team_counts.count(11), 8)
        self.assertEqual(team_counts.count(12), 3)

    def test_generic_coordinator_accepts_finalized_route_contract(self) -> None:
        root = Path(audit.__file__).resolve().parent
        config = coordinator.Config(
            root=root,
            data=root / "data",
            outbox=root / "outbox",
            receipts=root / "receipts",
            database=root / "data/ledger.sqlite3",
            certificate=audit.CERTIFICATE,
            batch_name="tc10_tc12_contract_only",
            reservations=(),
        )
        certificate, routes = coordinator.validate_certificate(config)
        self.assertEqual(len(routes), 24)
        self.assertTrue(
            certificate["coordinatorCompatibility"][
                "finalizedRouteContract"
            ]
        )
        self.assertEqual(
            [row["priorityRank"] for row in routes],
            list(range(1, 25)),
        )

    def test_certificate_is_coefficient_free_and_light_only(self) -> None:
        text = audit.CERTIFICATE.read_text(encoding="utf-8")
        self.assertIsNone(base.COEFFICIENT_LINE_RE.search(text))
        self.assertTrue(all(self.certificate["checks"].values()))
        source = Path(audit.__file__).read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("api_json(", source)
        self.assertNotIn('"--commit"', source)
        effects = self.certificate["sideEffects"]
        self.assertEqual(effects["sageRuns"], 0)
        self.assertEqual(effects["gapRuns"], 0)
        self.assertEqual(effects["polynomialArithmeticRuns"], 0)
        self.assertEqual(effects["networkCalls"], 0)
        self.assertEqual(effects["submissionCalls"], 0)
        self.assertEqual(effects["ledgerWrites"], 0)


if __name__ == "__main__":
    unittest.main()
