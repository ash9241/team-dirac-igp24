import hashlib
import importlib.util
import json
import sqlite3
import tempfile
import textwrap
import unittest
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PREPARER_PATH = ROOT / "prepare_f5_untried_wave.py"
ADAPTER_PATH = ROOT / "run_f5_untried_compact_plan.sage.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def candidate(
    preparer,
    source_label: str,
    source_r: int,
    canonical: str,
    target_label: str,
    height: int,
    extra_pairs=(),
):
    return {
        "actionSha256": "d" * 64,
        "canonicalQuotientSha256": canonical,
        "coefficientHeightBits": height,
        "possibleGoldPairs": [
            {"label": target_label, "r": source_r},
            *({"label": label, "r": r_value} for label, r_value in extra_pairs),
        ],
        "source": {
            "coefficientSha256": "e" * 64,
            "label": source_label,
            "polynomialIndex": 0,
            "r": source_r,
            "submissionId": "sub_test",
            "t": 1,
        },
    }


class PrepareCompactF5WaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.preparer = load(PREPARER_PATH, "prepare_f5_untried_wave_test")
        cls.adapter = load(ADAPTER_PATH, "run_f5_untried_compact_plan_test")

    def test_priority_322_then_min_height_unique_pair_and_signature_core(self):
        p = self.preparer
        rows = {}
        priority_key = (
            p.PRIORITY_TAIL_SOURCE[0],
            p.PRIORITY_TAIL_SOURCE[1],
            p.PRIORITY_TAIL_CANONICAL_SHA256,
        )
        rows[priority_key] = candidate(
            p,
            priority_key[0],
            priority_key[1],
            priority_key[2],
            p.PRIORITY_TAIL_PAIR[0],
            p.PRIORITY_TAIL_HEIGHT_BITS,
        )
        # Same aligned pair: the 12-bit representative must win over 20 bits.
        rows[("24T1", 8, "a" * 64)] = candidate(
            p, "24T1", 8, "a" * 64, "24T101", 20
        )
        rows[("24T2", 8, "b" * 64)] = candidate(
            p, "24T2", 8, "b" * 64, "24T101", 12
        )
        rows[("24T3", 12, "c" * 64)] = candidate(
            p, "24T3", 12, "c" * 64, "24T102", 16
        )
        # Duplicate source signature for another pair is rejected globally.
        rows[("24T3", 12, "f" * 64)] = candidate(
            p, "24T3", 12, "f" * 64, "24T103", 17
        )
        # Unaligned and 1702-bit tail candidates cannot enter.
        unaligned = candidate(p, "24T4", 16, "1" * 64, "24T104", 10)
        unaligned["possibleGoldPairs"] = [{"label": "24T104", "r": 20}]
        rows[("24T4", 16, "1" * 64)] = unaligned
        rows[("24T5", 20, "2" * 64)] = candidate(
            p, "24T5", 20, "2" * 64, "24T105", 1702
        )

        selected, _ = p.select_aligned(rows, 4, 322, 256, 1)
        self.assertEqual(selected[0]["canonicalQuotientSha256"], priority_key[2])
        self.assertEqual(selected[0]["selectionTier"], "priority_tail")
        self.assertEqual(
            [row["coefficientHeightBits"] for row in selected[1:]], [12, 16]
        )
        self.assertEqual(len(selected), 3)
        signatures = {(row["source"]["label"], row["source"]["r"]) for row in selected}
        aligned_pairs = {
            (pair["label"], pair["r"])
            for row in selected
            for pair in row["possibleGoldPairs"]
            if pair["r"] == row["source"]["r"]
        }
        self.assertEqual(len(signatures), len(selected))
        self.assertEqual(len(aligned_pairs), len(selected))

        reversed_rows = dict(reversed(list(rows.items())))
        permuted, _ = p.select_aligned(reversed_rows, 4, 322, 256, 1)
        self.assertEqual(
            json.dumps(selected, separators=(",", ":"), sort_keys=True),
            json.dumps(permuted, separators=(",", ":"), sort_keys=True),
        )

        # Reproduce the base worker's exact canonical-JSON subset gate.  The
        # selected rows must replace their unannotated census counterparts in
        # the sealed frontier index.
        collision = candidate(
            p,
            "24T999",
            4,
            selected[1]["canonicalQuotientSha256"],
            "24T1999",
            30,
        )
        census_rows = [*rows.values(), collision]
        unannotated_exact = {
            json.dumps(row, separators=(",", ":"), sort_keys=True)
            for row in census_rows
        }
        selected_by_identity = {p.frontier_identity(row): row for row in selected}
        frontier_rows = [
            selected_by_identity.get(p.frontier_identity(row), row)
            for row in census_rows
        ]
        selected_exact = {
            json.dumps(row, separators=(",", ":"), sort_keys=True)
            for row in selected
        }
        frontier_exact = {
            json.dumps(row, separators=(",", ":"), sort_keys=True)
            for row in frontier_rows
        }
        self.assertFalse(selected_exact <= unannotated_exact)
        self.assertTrue(selected_exact <= frontier_exact)
        self.assertEqual(
            len({p.frontier_identity(row) for row in frontier_rows}),
            len(census_rows),
        )
        self.assertEqual(
            sum(
                {
                    "newGoldPairsAtSelection",
                    "selectionRank",
                    "selectionTier",
                    "signatureAligned",
                }
                <= set(row)
                for row in frontier_rows
            ),
            len(selected),
        )
        self.assertTrue(
            all(
                {
                    "newGoldPairsAtSelection",
                    "selectionRank",
                    "selectionTier",
                    "signatureAligned",
                }
                <= set(row)
                for row in selected
            )
        )
        audit = p.audit_selected_frontier(rows, selected, set(), set())
        self.assertTrue(audit["passed"])
        self.assertTrue(all(audit["checks"].values()))
        reserved = {
            (
                selected[0]["possibleGoldPairs"][0]["label"],
                selected[0]["possibleGoldPairs"][0]["r"],
            )
        }
        reservation_failure = p.audit_selected_frontier(
            rows, selected, set(), reserved
        )
        self.assertFalse(reservation_failure["passed"])
        self.assertFalse(
            reservation_failure["checks"]["coveredPairsUnreserved"]
        )

    def test_nested_target_and_candidate_lists_do_not_crash(self):
        result = defaultdict(set)
        digest = "a" * 64
        value = {
            "target": [{"not": "a mapping target"}],
            "candidate": ["not", "a", "mapping"],
            "nested": [
                {"candidateSha256": digest, "targetLabel": "24T7", "targetR": 4}
            ],
        }
        self.preparer.walk_candidate_targets(value, result)
        self.assertEqual(result[digest], {("24T7", 4)})

    def test_claim_pairs_use_worker_root_semantics_on_nested_and_live_corpus(self):
        p = self.preparer
        nested = {
            "targetLabel": "24T1",
            "targetR": 4,
            "source": {"label": "24T999", "r": 20},
            "pairs": [{"label": "24T998", "r": 16}],
        }
        self.assertEqual(p.root_claim_pair(nested), ("24T1", 4))
        self.assertIsNone(
            p.root_claim_pair({"pairs": [{"label": "24T998", "r": 16}]})
        )
        destination = ROOT / "data" / "nonexistent_claim_parity_destination"
        for path in p.claim_paths(destination):
            value = json.loads(path.read_text(encoding="utf-8"))
            expected = None
            if isinstance(value, dict):
                label = value.get("targetLabel", value.get("label"))
                signature = value.get("targetR", value.get("r"))
                if isinstance(label, str) and signature is not None:
                    expected = (label, int(signature))
            self.assertEqual(p.root_claim_pair(value), expected, str(path))

    def test_receipt_only_queued_submission_is_reported(self):
        p = self.preparer
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            data = base / "data"
            outbox = base / "outbox"
            receipts = base / "receipts"
            destination = data / "new_wave"
            data.mkdir()
            outbox.mkdir()
            receipts.mkdir()
            line = ",".join(["0"] * 24 + ["1"])
            manifest = outbox / "queued.txt"
            manifest.write_text(line + "\n", encoding="utf-8")
            receipt = {
                "manifest": str(manifest),
                "manifestHash": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                "response": {"submissionId": "sub_receipt_only", "queuedCount": 2},
            }
            (receipts / "sub_receipt_only.json").write_text(
                json.dumps(receipt), encoding="utf-8"
            )
            connection = sqlite3.connect(":memory:")
            connection.executescript(
                """
                CREATE TABLE submissions(submission_id TEXT, queued_count INTEGER);
                CREATE TABLE polynomials(
                    submission_id TEXT, polynomial_index INTEGER, coefficient_hash TEXT
                );
                CREATE TABLE verifications(
                    submission_id TEXT, polynomial_index INTEGER, label TEXT, r INTEGER
                );
                """
            )
            with mock.patch.multiple(
                p, ROOT=base, DATA=data, OUTBOX=outbox, RECEIPTS=receipts
            ):
                _, _, _, _, _, _, health = p.collect_reservations(
                    connection, destination
                )
            connection.close()
            self.assertEqual(
                health["queuedReceiptSubmissions"],
                [{"submissionId": "sub_receipt_only", "queuedCount": 2}],
            )

    def test_report_ownership_is_fail_closed(self):
        p = self.preparer
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "preview.json"
            report.write_text(json.dumps({"schemaVersion": "other", "waveId": "wave1"}))
            with self.assertRaisesRegex(ValueError, "not owned"):
                p.validate_report_ownership(report, "wave2")
            report.write_text(
                json.dumps(
                    {
                        "schemaVersion": "f5-untried-wave-preparation-report-v1",
                        "waveId": "wave2",
                    }
                )
            )
            p.validate_report_ownership(report, "wave2")

    def test_compact_adapter_admits_exact_plan_count_only(self):
        snippet = textwrap.dedent(self.adapter.NEW_ADMISSION)
        selected = [{"canonicalQuotientSha256": f"{index:064x}"} for index in range(19)]
        exec(snippet, {"selected": selected, "plan": {"frontier": {"selectedSources": 19}}})
        with self.assertRaisesRegex(ValueError, "disagree"):
            exec(
                snippet,
                {"selected": selected, "plan": {"frontier": {"selectedSources": 18}}},
            )
        with self.assertRaisesRegex(ValueError, "outside"):
            exec(
                snippet,
                {"selected": selected, "plan": {"frontier": {"selectedSources": 51}}},
            )

    def test_pinned_stale_queue_exception_is_exact_and_strict(self):
        p = self.preparer
        now = datetime(2026, 7, 22, 8, 0, tzinfo=timezone.utc)
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE submissions(
                submission_id TEXT PRIMARY KEY, queued_count INTEGER,
                verified_count INTEGER, failed_count INTEGER, updated_at TEXT,
                raw_json TEXT, synced_at REAL
            );
            CREATE TABLE polynomials(
                submission_id TEXT, polynomial_index INTEGER,
                coefficients TEXT, coefficient_hash TEXT
            );
            CREATE TABLE verifications(submission_id TEXT, polynomial_index INTEGER);
            CREATE TABLE failures(submission_id TEXT, polynomial_index INTEGER);
            """
        )
        unique_lines = [f"coefficient-row-{index}" for index in range(296)]
        lines = unique_lines + unique_lines[:4]
        queued_rows = []
        offset = 0
        for submission_id in p.EXPECTED_STALE_QUEUE_IDS:
            updated = now - timedelta(days=15)
            raw = {
                "submissionId": submission_id,
                "payload": {
                    "queuedPolynomials": [
                        {"polynomialIndex": index, "status": "queued"}
                        for index in range(25)
                    ]
                },
                "verifiedPolynomials": [],
                "failedPolynomials": [],
                "updatedAt": updated.isoformat().replace("+00:00", "Z"),
            }
            connection.execute(
                "INSERT INTO submissions VALUES(?,?,?,?,?,?,?)",
                (
                    submission_id,
                    25,
                    0,
                    0,
                    updated.isoformat().replace("+00:00", "Z"),
                    json.dumps(raw),
                    (now - timedelta(hours=1)).timestamp(),
                ),
            )
            for polynomial_index in range(25):
                line = lines[offset + polynomial_index]
                connection.execute(
                    "INSERT INTO polynomials VALUES(?,?,?,?)",
                    (
                        submission_id,
                        polynomial_index,
                        line,
                        hashlib.sha256(line.encode()).hexdigest(),
                    ),
                )
            offset += 25
            queued_rows.append(
                {
                    "submissionId": submission_id,
                    "queuedCount": 25,
                    "verifiedCount": 0,
                    "failedCount": 0,
                    "updatedAt": updated.isoformat().replace("+00:00", "Z"),
                }
            )
        boundary = p.queued_boundary(connection, queued_rows)
        selected = [
            {
                "source": {"coefficientSha256": "f" * 64},
            }
        ]
        with mock.patch.multiple(
            p,
            EXPECTED_STALE_QUEUE_BOUNDARY_SHA256=boundary["indexSha256"],
            EXPECTED_STALE_QUEUE_RAW_STATE_SHA256=boundary[
                "rawStateBoundarySha256"
            ],
            EXPECTED_STALE_QUEUE_POLYNOMIAL_BOUNDARY_SHA256=boundary[
                "polynomialRowHashBoundarySha256"
            ],
        ):
            certificate = p.certify_stale_queue_exception(
                connection, queued_rows, set(), selected, now=now
            )
            self.assertTrue(certificate["certified"])
            receipt_failure = p.certify_stale_queue_exception(
                connection,
                queued_rows,
                {p.EXPECTED_STALE_QUEUE_IDS[0]},
                selected,
                now=now,
            )
            self.assertFalse(receipt_failure["certified"])
            self.assertFalse(receipt_failure["checks"]["noLocalReceipts"])
        connection.close()

    def test_adapter_and_planner_pin_nested_prior_and_atomic_publication(self):
        base = self.adapter.BASE_WORKER.read_text(encoding="utf-8")
        self.assertEqual(base.count(self.adapter.OLD_ADMISSION), 1)
        self.assertEqual(base.count(self.adapter.OLD_PRIOR_BOUNDARY), 1)
        self.assertIn('DATA.glob("f5_untried*")', self.adapter.NEW_PRIOR_BOUNDARY)
        self.assertIn('plan["artifacts"]["allPriorPlans"]', self.adapter.NEW_PRIOR_BOUNDARY)
        source = PREPARER_PATH.read_text(encoding="utf-8")
        self.assertIn("assert_census_stable()", source)
        self.assertIn("os.rename(staging, destination)", source)
        self.assertNotIn("atomic_text(plan_path, plan_text)", source)
        self.assertIn('"coefficientFreePlan"', source)


if __name__ == "__main__":
    unittest.main()
