from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import stage_frobenius_gold as stager


def coefficient_line(linear: int) -> str:
    coefficients = [1, linear] + [0] * 22 + [1]
    return ",".join(str(value) for value in coefficients)


def digest(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def create_ledger(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE targets (
                label TEXT NOT NULL,
                r INTEGER NOT NULL,
                team_count INTEGER NOT NULL,
                generated_at TEXT,
                PRIMARY KEY(label,r)
            );
            CREATE TABLE baseline_pairs (
                label TEXT NOT NULL,
                r INTEGER NOT NULL,
                PRIMARY KEY(label,r)
            );
            CREATE TABLE verifications (
                label TEXT,
                r INTEGER,
                scoreable INTEGER
            );
            CREATE TABLE polynomials (coefficient_hash TEXT);
            """
        )


class FrobeniusGoldStagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "ledger.sqlite3"
        self.candidates = self.root / "multi.jsonl"
        self.certificate = self.root / "certificate.json"
        self.output = self.root / "manifest.txt"
        create_ledger(self.database)

        first_line = coefficient_line(0)
        second_line = coefficient_line(1)
        self.candidate_row = {
            "status": "certified_multi",
            "sourceSubmissionId": "sub-source",
            "sourcePolynomialIndex": 7,
            "sourceLabel": "24T10",
            "sourceR": 8,
            "orbitTargets": [
                {
                    "targetLabel": "24T20",
                    "targetT": 20,
                    "orbitSize": 24,
                },
                {
                    "targetLabel": "24T30",
                    "targetT": 30,
                    "orbitSize": 24,
                },
            ],
            "candidates": [
                {
                    "factorIndex": 0,
                    "targetR": 4,
                    "coefficientLine": first_line,
                    "coefficientSha256": digest(first_line),
                    "polynomialDiscriminantAbs": "200",
                },
                {
                    "factorIndex": 1,
                    "targetR": 8,
                    "coefficientLine": second_line,
                    "coefficientSha256": digest(second_line),
                    "polynomialDiscriminantAbs": "100",
                },
            ],
        }
        candidate_text = (
            json.dumps(self.candidate_row, separators=(",", ":"), sort_keys=True)
            + "\n"
        )
        self.candidates.write_text(candidate_text, encoding="utf-8")
        self.certificate_value = {
            "method": stager.CERTIFICATE_METHOD,
            "inputSha256": hashlib.sha256(candidate_text.encode("utf-8")).hexdigest(),
            "selectedInputRowsSha256": stager.selected_rows_digest(
                [self.candidate_row]
            ),
            "summary": {
                "rows": 1,
                "resolved": 1,
                "unresolved": 0,
                "contradiction": 0,
            },
            "rows": [
                {
                    "status": "resolved",
                    "sourceSubmissionId": "sub-source",
                    "sourcePolynomialIndex": 7,
                    "sourceLabel": "24T10",
                    "sourceR": 8,
                    "assignments": [
                        {
                            "factorIndex": 0,
                            "coefficientSha256": digest(first_line),
                            "targetLabel": "24T20",
                            "targetT": 20,
                            "targetR": 4,
                        },
                        {
                            "factorIndex": 1,
                            "coefficientSha256": digest(second_line),
                            "targetLabel": "24T30",
                            "targetT": 30,
                            "targetR": 8,
                        },
                    ],
                }
            ],
        }
        self.write_certificate()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_certificate(self) -> None:
        self.certificate.write_text(
            json.dumps(self.certificate_value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_stages_only_current_unowned_nonbaseline_gold(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO targets VALUES ('24T20',4,0,'snapshot-a')"
            )
            connection.execute(
                "INSERT INTO targets VALUES ('24T30',8,2,'snapshot-a')"
            )

        summary = stager.stage(
            self.certificate, self.candidates, self.database, self.output
        )

        self.assertEqual(summary["selected"], 1)
        self.assertEqual(summary["skipCounts"], {"not_team_count_zero": 1})
        self.assertEqual(summary["selectedPairs"][0]["label"], "24T20")
        self.assertEqual(
            self.output.read_text(encoding="utf-8"),
            self.candidate_row["candidates"][0]["coefficientLine"] + "\n",
        )
        self.assertFalse(list(self.root.glob(".manifest.txt.*.tmp")))

    def test_rejects_hash_mismatch_without_replacing_manifest(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO targets VALUES ('24T20',4,0,'snapshot-a')"
            )
        self.certificate_value["rows"][0]["assignments"][0][
            "coefficientSha256"
        ] = "0" * 64
        self.write_certificate()
        self.output.write_text("preserve-me\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "assignment hash mismatch"):
            stager.stage(
                self.certificate, self.candidates, self.database, self.output
            )
        self.assertEqual(self.output.read_text(encoding="utf-8"), "preserve-me\n")

    def test_refuses_unresolved_certificate(self) -> None:
        self.certificate_value["summary"].update(resolved=0, unresolved=1)
        self.certificate_value["rows"][0]["status"] = "unresolved"
        self.certificate_value["rows"][0].pop("assignments")
        self.write_certificate()

        with self.assertRaisesRegex(ValueError, "unresolved"):
            stager.stage(
                self.certificate, self.candidates, self.database, self.output
            )
        self.assertFalse(self.output.exists())

    def test_local_ownership_causes_empty_stage_to_fail_safely(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO targets VALUES ('24T20',4,0,'snapshot-a')"
            )
            connection.execute(
                "INSERT INTO targets VALUES ('24T30',8,0,'snapshot-a')"
            )
            connection.execute("INSERT INTO verifications VALUES ('24T20',4,1)")
            connection.execute("INSERT INTO verifications VALUES ('24T30',8,1)")

        with self.assertRaisesRegex(ValueError, "no current nonbaseline"):
            stager.stage(
                self.certificate, self.candidates, self.database, self.output
            )
        self.assertFalse(self.output.exists())

    def test_certificate_must_hash_the_original_jsonl(self) -> None:
        self.certificate_value["inputSha256"] = "f" * 64
        self.write_certificate()
        with self.assertRaisesRegex(ValueError, "input SHA-256"):
            stager.stage(
                self.certificate, self.candidates, self.database, self.output
            )

    def test_baseline_and_known_hash_are_excluded(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO targets VALUES ('24T20',4,0,'snapshot-a')"
            )
            connection.execute(
                "INSERT INTO targets VALUES ('24T30',8,0,'snapshot-a')"
            )
            connection.execute("INSERT INTO baseline_pairs VALUES ('24T20',4)")
            connection.execute(
                "INSERT INTO polynomials VALUES (?)",
                (self.candidate_row["candidates"][1]["coefficientSha256"],),
            )
        joined = stager.join_resolved_assignments(
            self.certificate_value, [self.candidate_row], self.candidates
        )
        selected, skips = stager.filter_live_gold(joined, self.database)
        self.assertEqual(selected, [])
        self.assertEqual(skips, {"baseline_pair": 1, "known_polynomial_hash": 1})


if __name__ == "__main__":
    unittest.main()
