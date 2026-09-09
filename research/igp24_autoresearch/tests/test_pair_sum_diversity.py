from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import build_pair_sum_diversity as diversity


def create_ledger(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE verifications (
                submission_id TEXT NOT NULL,
                polynomial_index INTEGER NOT NULL,
                label TEXT,
                t INTEGER,
                r INTEGER,
                scoreable INTEGER NOT NULL,
                disc_source TEXT,
                field_disc_abs TEXT,
                PRIMARY KEY (submission_id, polynomial_index)
            );
            CREATE TABLE polynomials (
                submission_id TEXT NOT NULL,
                polynomial_index INTEGER NOT NULL,
                original_line TEXT NOT NULL,
                coefficient_hash TEXT NOT NULL,
                PRIMARY KEY (submission_id, polynomial_index)
            );
            CREATE TABLE targets (
                label TEXT NOT NULL,
                r INTEGER NOT NULL,
                team_count INTEGER NOT NULL,
                PRIMARY KEY (label, r)
            );
            CREATE TABLE baseline_pairs (
                label TEXT NOT NULL,
                r INTEGER NOT NULL,
                PRIMARY KEY (label, r)
            );
            """
        )


def insert_source(
    path: Path,
    submission_id: str,
    polynomial_index: int,
    *,
    label: str = "24T10",
    r: int = 4,
    scoreable: int = 1,
    disc_source: str = "exact_nfdisc",
    nfdisc: str | None = "100",
    coefficient_hash: str = "hash-a",
    original_line: str = "1,0,1",
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO polynomials VALUES (?, ?, ?, ?)",
            (submission_id, polynomial_index, original_line, coefficient_hash),
        )
        connection.execute(
            "INSERT INTO verifications VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                submission_id,
                polynomial_index,
                label,
                int(label[3:]),
                r,
                scoreable,
                disc_source,
                nfdisc,
            ),
        )


class PairSumDiversityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "ledger.sqlite3"
        create_ledger(self.database)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def test_source_parser_accepts_explicit_even_signatures(self) -> None:
        self.assertEqual(diversity.parse_source_pair("24T14849/r20"), ("24T14849", 20))
        self.assertEqual(diversity.parse_source_pair("24T10/4"), ("24T10", 4))
        for invalid in ("24T0/r4", "24T10/r3", "24T10/r26", "10/r4"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(argparse.ArgumentTypeError):
                    diversity.parse_source_pair(invalid)

    def test_selection_is_one_untested_shortest_row_per_exact_nfdisc(self) -> None:
        # nfdisc 100: the shortest row was tested, so the next untested
        # presentation must be selected.
        insert_source(
            self.database,
            "sub-tested",
            0,
            nfdisc="100",
            coefficient_hash="hash-tested-100",
            original_line="1",
        )
        insert_source(
            self.database,
            "sub-fallback",
            0,
            nfdisc="100",
            coefficient_hash="hash-fallback-100",
            original_line="12345",
        )

        # nfdisc 200: choose by coefficient length, independent of insertion
        # order or lexical submission order.
        insert_source(
            self.database,
            "sub-long",
            0,
            nfdisc="200",
            coefficient_hash="hash-long-200",
            original_line="123456789",
        )
        insert_source(
            self.database,
            "sub-short",
            0,
            nfdisc="200",
            coefficient_hash="hash-short-200",
            original_line="12",
        )

        # nfdisc 300 is exhausted, while non-exact and unscoreable rows never
        # enter the source bank.
        insert_source(
            self.database,
            "sub-tested-300",
            0,
            nfdisc="300",
            coefficient_hash="hash-tested-300",
        )
        insert_source(
            self.database,
            "sub-mixed",
            0,
            nfdisc=None,
            disc_source="mixed_disc",
            coefficient_hash="hash-mixed",
        )
        insert_source(
            self.database,
            "sub-unscoreable",
            0,
            nfdisc="400",
            scoreable=0,
            coefficient_hash="hash-unscoreable",
        )

        route = {
            ("24T10", 4): {
                "sourceLabel": "24T10",
                "sourceR": 4,
                "targetLabel": "24T20",
                "targetT": 20,
                "orbitIndex": 1,
                "liveGoldTargetR": [12],
            }
        }
        with closing(self.connect()) as connection:
            tasks, diagnostics = diversity.select_source_tasks(
                connection,
                route,
                {"hash-tested-100", "hash-tested-300"},
            )

        self.assertEqual(
            [row["sourceCoefficientSha256"] for row in tasks],
            ["hash-fallback-100", "hash-short-200"],
        )
        self.assertEqual(
            [row["sourceFieldDiscAbs"] for row in tasks], ["100", "200"]
        )
        self.assertEqual(diagnostics[0]["exactSourceRows"], 5)
        self.assertEqual(diagnostics[0]["distinctExactNfdisc"], 3)
        self.assertEqual(diagnostics[0]["testedSourceRowsSkipped"], 2)
        self.assertEqual(diagnostics[0]["nfdiscsWithTestedHashes"], 2)
        self.assertEqual(diagnostics[0]["nfdiscsNeverTested"], 1)
        self.assertEqual(diagnostics[0]["nfdiscsExhaustedByTestedHashes"], 1)
        self.assertEqual(diagnostics[0]["selectedDistinctNfdisc"], 2)

    def test_retained_outputs_resolve_source_hashes_from_provenance(self) -> None:
        insert_source(
            self.database,
            "sub-a",
            3,
            coefficient_hash="ledger-hash",
        )
        retained = self.root / "retained.jsonl"
        retained.write_text(
            json.dumps(
                {
                    "sourceSubmissionId": "sub-a",
                    "sourcePolynomialIndex": 3,
                }
            )
            + "\n"
            + json.dumps({"sourceCoefficientSha256": "direct-hash"})
            + "\n",
            encoding="utf-8",
        )
        with closing(self.connect()) as connection:
            hashes, counts = diversity.load_tested_source_hashes(
                connection, [retained, retained]
            )
        self.assertEqual(hashes, {"ledger-hash", "direct-hash"})
        self.assertEqual(counts, {str(retained.resolve()): 2})

    def test_route_validation_requires_one_action_and_a_live_gold_label(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("INSERT INTO targets VALUES ('24T20', 12, 0)")
        single = {
            "24T10": {
                "sourceLabel": "24T10",
                "length24OrbitCount": 1,
                "targets": [
                    {
                        "targetLabel": "24T20",
                        "targetT": 20,
                        "orbitIndex": 1,
                        "orbitSize": 24,
                    }
                ],
            }
        }
        with closing(self.connect()) as connection:
            routes = diversity.validate_routes(
                connection, [("24T10", 4)], single
            )
        self.assertEqual(routes[("24T10", 4)]["liveGoldTargetR"], [12])

        multi = {
            "24T10": {
                "sourceLabel": "24T10",
                "length24OrbitCount": 2,
                "targets": [{"targetLabel": "24T20"}, {"targetLabel": "24T30"}],
            }
        }
        with closing(self.connect()) as connection:
            with self.assertRaisesRegex(ValueError, "not a single action"):
                diversity.validate_routes(connection, [("24T10", 4)], multi)

        no_gold = {
            "24T30": {
                "sourceLabel": "24T30",
                "length24OrbitCount": 1,
                "targets": [
                    {
                        "targetLabel": "24T40",
                        "targetT": 40,
                        "orbitIndex": 1,
                        "orbitSize": 24,
                    }
                ],
            }
        }
        with closing(self.connect()) as connection:
            with self.assertRaisesRegex(ValueError, "no live unowned"):
                diversity.validate_routes(connection, [("24T30", 4)], no_gold)

    def test_worker_call_is_single_action_and_checks_provenance(self) -> None:
        task = {
            "sourceSubmissionId": "sub-a",
            "sourcePolynomialIndex": 7,
            "sourceLabel": "24T10",
            "sourceR": 4,
            "sourceFieldDiscAbs": "100",
            "sourceCoefficientSha256": "source-hash",
            "sourceCoefficientBytes": 10,
            "targetLabel": "24T20",
            "targetT": 20,
            "orbitIndex": 1,
            "liveGoldTargetR": [12],
        }
        payload = {
            "status": "certified",
            "sourceSubmissionId": "sub-a",
            "sourcePolynomialIndex": 7,
            "sourceLabel": "24T10",
            "sourceR": 4,
            "targetLabel": "24T20",
            "targetT": 20,
            "targetR": 12,
            "coefficientLine": "1,2,1",
            "coefficientSha256": "candidate-hash",
            "polynomialDiscriminantAbs": "123",
        }
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload) + "\n",
            stderr="proof log",
        )
        with mock.patch.object(diversity.subprocess, "run", return_value=completed) as run:
            result = diversity.run_task(
                task, timeout=10, transforms="1,3", reduction="best"
            )
        command = run.call_args.args[0]
        self.assertIn("--expected-target", command)
        self.assertEqual(command[command.index("--expected-target") + 1], "24T20")
        self.assertNotIn("--all-degree-24", command)
        self.assertEqual(result["status"], "certified")
        self.assertEqual(result["sourceFieldDiscAbs"], "100")

    def test_manifest_uses_only_current_unknown_gold_and_best_polynomial(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute("INSERT INTO targets VALUES ('24T20', 12, 0)")
            connection.execute("INSERT INTO targets VALUES ('24T30', 12, 0)")
            connection.execute("INSERT INTO baseline_pairs VALUES ('24T30', 12)")

        def candidate(label: str, line: str, disc: int) -> dict:
            return {
                "status": "certified",
                "sourceLabel": "24T10",
                "sourceR": 4,
                "sourceFieldDiscAbs": "100",
                "targetLabel": label,
                "targetT": int(label[3:]),
                "targetR": 12,
                "coefficientLine": line,
                "coefficientSha256": hashlib.sha256(line.encode()).hexdigest(),
                "polynomialDiscriminantAbs": str(disc),
            }

        rows = [
            candidate("24T20", "1,2,1", 500),
            candidate("24T20", "2,3,1", 100),
            candidate("24T30", "3,4,1", 10),
        ]
        with closing(self.connect()) as connection:
            diversity.annotate_current_target_state(connection, rows)
        selected = diversity.select_manifest_rows(rows)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["coefficientLine"], "2,3,1")
        self.assertFalse(rows[2]["valuableGold"])

    def test_output_paths_cannot_collide_or_replace_production(self) -> None:
        candidate = self.root / "candidates.jsonl"
        summary = self.root / "summary.json"
        manifest = self.root / "manifest.txt"
        diversity.validate_output_paths(candidate, summary, manifest)
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            diversity.validate_output_paths(candidate, candidate, manifest)
        with self.assertRaisesRegex(ValueError, "protected production path"):
            diversity.validate_output_paths(
                diversity.DATA / "pair_sum_candidates.jsonl", summary, manifest
            )


if __name__ == "__main__":
    unittest.main()
