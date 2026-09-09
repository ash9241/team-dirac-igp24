from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import stage_pair_sum_multi_stable as stager


def coefficient_line(seed: int) -> str:
    coefficients = [1, seed] + [0] * 22 + [1]
    return ",".join(str(value) for value in coefficients)


def factor(index: int, target_r: int, seed: int, disc: int) -> dict:
    line = coefficient_line(seed)
    return {
        "factorIndex": index,
        "targetR": target_r,
        "coefficientLine": line,
        "coefficientSha256": hashlib.sha256(line.encode()).hexdigest(),
        "polynomialDiscriminantAbs": str(disc),
    }


def certified_row(
    source_label: str = "24T900",
    submission_id: str = "sub-a",
    polynomial_index: int = 1,
    disc_offset: int = 0,
) -> dict:
    candidates = [
        factor(0, 0, 10 + disc_offset, 300 + disc_offset),
        factor(1, 4, 20 + disc_offset, 200 + disc_offset),
        factor(2, 8, 30 + disc_offset, 100 + disc_offset),
    ]
    return {
        "status": "certified_multi",
        "workerExitCode": 0,
        "sourceSubmissionId": submission_id,
        "sourcePolynomialIndex": polynomial_index,
        "sourceLabel": source_label,
        "sourceR": 12,
        "candidates": candidates,
        "orbitTargets": [
            {
                "orbitIndex": 1,
                "orbitSize": 24,
                "targetLabel": "24T101",
                "targetT": 101,
            },
            {
                "orbitIndex": 2,
                "orbitSize": 24,
                "targetLabel": "24T102",
                "targetT": 102,
            },
            {
                "orbitIndex": 3,
                "orbitSize": 24,
                "targetLabel": "24T3000",
                "targetT": 3000,
            },
        ],
        "orbitCertificate": {
            "actualDegrees": [12, 24, 24, 24, 96],
            "expectedDegrees": [12, 24, 24, 24, 96],
            "exponents": [1, 1, 1, 1, 1],
        },
        "assignmentProof": {
            "actualFactorR": [0, 4, 8],
            "compatibleClassIndexes": [7],
            "assignedFactors": 3,
            "totalFactors": 3,
        },
    }


def signature_row(source_label: str = "24T900") -> dict:
    return {
        "sourceLabel": source_label,
        "status": "certified",
        "length24OrbitCount": 3,
        "profiles": [
            {
                "classIndex": 7,
                "sourceR": 12,
                "orbitSignatures": [
                    {
                        "orbitIndex": 1,
                        "targetLabel": "24T101",
                        "targetR": 0,
                    },
                    {
                        "orbitIndex": 2,
                        "targetLabel": "24T102",
                        "targetR": 4,
                    },
                    {
                        "orbitIndex": 3,
                        "targetLabel": "24T3000",
                        "targetR": 8,
                    },
                ],
            }
        ],
    }


def create_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE targets (
                label TEXT NOT NULL,
                t INTEGER NOT NULL,
                r INTEGER NOT NULL,
                team_count INTEGER NOT NULL,
                minimum_disc_abs TEXT,
                discovered INTEGER NOT NULL,
                generated_at TEXT,
                PRIMARY KEY(label,r)
            );
            CREATE TABLE baseline_pairs(label TEXT NOT NULL, r INTEGER NOT NULL);
            CREATE TABLE verifications(label TEXT, r INTEGER, scoreable INTEGER);
            CREATE TABLE polynomials(coefficient_hash TEXT);
            """
        )
        connection.commit()


class StableMultiStagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.candidates = self.root / "candidates.jsonl"
        self.signatures = self.root / "signatures.jsonl"
        self.database = self.root / "ledger.sqlite3"
        self.output = self.root / "manifest.txt"
        self.summary = self.root / "summary.json"
        create_database(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_inputs(self, rows: list[dict], signatures: list[dict]) -> None:
        self.candidates.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        self.signatures.write_text(
            "".join(json.dumps(row) + "\n" for row in signatures),
            encoding="utf-8",
        )

    def test_stages_known_and_partial_cache_pairs_with_exact_projection(self) -> None:
        self.write_inputs([certified_row()], [signature_row()])
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                "INSERT INTO targets VALUES (?,?,?,?,?,?,?)",
                [
                    ("24T101", 101, 0, 0, None, 0, "snapshot-a"),
                    ("24T102", 102, 4, 3, "123", 1, "snapshot-a"),
                ],
            )
            connection.commit()

        summary = stager.stage(
            self.candidates,
            self.signatures,
            self.database,
            self.output,
            self.summary,
        )

        self.assertEqual(summary["assignedFactors"], 3)
        self.assertEqual(summary["selected"], 3)
        self.assertEqual(summary["scoreProjection"]["knownPairs"], 2)
        self.assertEqual(summary["scoreProjection"]["unknownPairs"], 1)
        self.assertEqual(
            summary["scoreProjection"]["knownMarginalScoreExact"], "9/8"
        )
        self.assertEqual(
            summary["scoreProjection"]["upperBoundIncludingUnknownExact"],
            "17/8",
        )
        self.assertEqual(len(self.output.read_text().splitlines()), 3)
        self.assertEqual(json.loads(self.summary.read_text())["selected"], 3)
        pairs = {(row["label"], row["r"]): row for row in summary["selectedPairs"]}
        self.assertEqual(pairs[("24T101", 0)]["projectedMarginalScoreExact"], "1")
        self.assertEqual(pairs[("24T102", 4)]["projectedMarginalScoreExact"], "1/8")
        self.assertIsNone(pairs[("24T3000", 8)]["projectedMarginalScore"])

    def test_deduplicates_pairs_by_smallest_polynomial_discriminant(self) -> None:
        first = certified_row(disc_offset=100)
        second = certified_row(
            source_label="24T901",
            submission_id="sub-b",
            polynomial_index=2,
            disc_offset=0,
        )
        self.write_inputs([first, second], [signature_row(), signature_row("24T901")])
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                "INSERT INTO targets VALUES (?,?,?,?,?,?,?)",
                [
                    ("24T101", 101, 0, 1, None, 1, "snapshot-b"),
                    ("24T102", 102, 4, 1, None, 1, "snapshot-b"),
                    ("24T3000", 3000, 8, 1, None, 1, "snapshot-b"),
                ],
            )
            connection.commit()

        summary = stager.stage(
            self.candidates,
            self.signatures,
            self.database,
            self.output,
            self.summary,
        )

        self.assertEqual(summary["assignedFactors"], 6)
        self.assertEqual(summary["selected"], 3)
        self.assertEqual(summary["skipCounts"], {"duplicate_target_pair": 3})
        self.assertEqual(
            {row["sourceLabel"] for row in summary["selectedPairs"]}, {"24T901"}
        )

    def test_filters_baseline_owned_known_hash_and_cached_invalid_signature(self) -> None:
        rows = [
            {
                "targetLabel": f"24T{index}",
                "targetT": index,
                "targetR": 0,
                "coefficientSha256": f"hash-{index}",
                "coefficientLine": coefficient_line(index),
                "polynomialDiscriminantAbs": index,
            }
            for index in range(101, 106)
        ]
        with closing(sqlite3.connect(self.database)) as connection:
            connection.executemany(
                "INSERT INTO targets VALUES (?,?,?,?,?,?,?)",
                [
                    ("24T101", 101, 0, 0, None, 0, "snapshot-c"),
                    ("24T102", 102, 0, 0, None, 0, "snapshot-c"),
                    ("24T103", 103, 0, 0, None, 0, "snapshot-c"),
                    ("24T104", 104, 4, 0, None, 0, "snapshot-c"),
                    ("24T105", 105, 0, 2, None, 1, "snapshot-c"),
                ],
            )
            connection.execute("INSERT INTO baseline_pairs VALUES ('24T101',0)")
            connection.execute("INSERT INTO verifications VALUES ('24T102',0,1)")
            connection.execute("INSERT INTO polynomials VALUES ('hash-103')")
            connection.commit()

        selected, skips, _cache = stager.filter_current(rows, self.database)

        self.assertEqual([(row["targetLabel"], row["targetR"]) for row in selected], [("24T105", 0)])
        self.assertEqual(
            skips,
            {
                "baseline_pair": 1,
                "locally_owned_pair": 1,
                "known_polynomial_hash": 1,
                "target_signature_missing_in_cache": 1,
            },
        )

    def test_corruption_fails_before_replacing_outputs(self) -> None:
        row = certified_row()
        row["candidates"][0]["coefficientSha256"] = "0" * 64
        self.write_inputs([row], [signature_row()])
        self.output.write_text("keep-manifest\n", encoding="utf-8")
        self.summary.write_text("keep-summary\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "coefficient hash mismatch"):
            stager.stage(
                self.candidates,
                self.signatures,
                self.database,
                self.output,
                self.summary,
            )

        self.assertEqual(self.output.read_text(), "keep-manifest\n")
        self.assertEqual(self.summary.read_text(), "keep-summary\n")

    def test_stored_assignment_proof_must_match_rederived_proof(self) -> None:
        row = certified_row()
        row["assignmentProof"]["compatibleClassIndexes"] = [999]
        self.write_inputs([row], [signature_row()])

        with self.assertRaisesRegex(ValueError, "stored assignment proof disagrees"):
            stager.derive_assignments([row], {"24T900": signature_row()})

    def test_outputs_cannot_overwrite_inputs_or_production_outputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not overwrite"):
            stager.validate_output_paths(
                self.candidates,
                self.signatures,
                self.database,
                self.candidates,
                self.summary,
            )
        with self.assertRaisesRegex(ValueError, "production outputs"):
            stager.validate_output_paths(
                self.candidates,
                self.signatures,
                self.database,
                stager.PRODUCTION_MANIFEST,
                self.summary,
            )


if __name__ == "__main__":
    unittest.main()
