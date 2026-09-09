from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import stage_frobenius_gold as frobenius
import stage_verifier_anchor_6087 as stager


def coefficient_line(seed: int) -> str:
    coefficients = [seed + 1, seed] + [0] * 22 + [1]
    return ",".join(str(value) for value in coefficients)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def create_ledger(path: Path) -> None:
    with sqlite3.connect(path) as connection:
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
            CREATE TABLE baseline_pairs (label TEXT, r INTEGER);
            CREATE TABLE verifications (
                submission_id TEXT,
                polynomial_index INTEGER,
                status TEXT,
                label TEXT,
                t INTEGER,
                r INTEGER,
                scoreable INTEGER
            );
            CREATE TABLE polynomials (
                submission_id TEXT,
                polynomial_index INTEGER,
                original_line TEXT,
                coefficient_hash TEXT
            );
            CREATE TABLE submission_receipts (
                submission_id TEXT PRIMARY KEY,
                manifest_path TEXT NOT NULL,
                manifest_hash TEXT NOT NULL,
                raw_json TEXT NOT NULL
            );
            """
        )


class VerifierAnchor6087StageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.data = self.root / "data"
        self.receipts = self.root / "receipts"
        self.outbox = self.root / "outbox"
        self.data.mkdir()
        self.receipts.mkdir()
        self.outbox.mkdir()
        self.database = self.data / "ledger.sqlite3"
        self.input = self.data / "packets.jsonl"
        self.proof = self.data / "frobenius.json"
        self.manifest = self.outbox / "anchor.txt"
        self.certificate = self.data / "anchor_certificate.json"
        self.summary = self.data / "anchor_summary.json"
        create_ledger(self.database)

        self.lines = [coefficient_line(index) for index in (10, 20, 30)]
        self.hashes = [digest(line) for line in self.lines]
        candidates = [
            {
                "coefficientBytes": len(line.encode("ascii")),
                "coefficientLine": line,
                "coefficientSha256": digest(line),
                "factorIndex": index,
                "polynomialDiscriminantAbs": str(1000 + index),
                "targetR": target_r,
            }
            for index, (line, target_r) in enumerate(zip(self.lines, (8, 4, 0)))
        ]
        packet = {
            "candidates": candidates,
            "orbitTargets": [
                {
                    "imageOrder": 3072,
                    "kernelOrder": 1,
                    "orbitIndex": 1,
                    "orbitSize": 24,
                    "targetLabel": "24T6407",
                    "targetT": 6407,
                },
                {
                    "imageOrder": 3072,
                    "kernelOrder": 1,
                    "orbitIndex": 2,
                    "orbitSize": 24,
                    "targetLabel": "24T6080",
                    "targetT": 6080,
                },
                {
                    "imageOrder": 3072,
                    "kernelOrder": 1,
                    "orbitIndex": 3,
                    "orbitSize": 24,
                    "targetLabel": "24T6364",
                    "targetT": 6364,
                },
            ],
            "sourceLabel": stager.SOURCE_KEY[2],
            "sourcePolynomialIndex": stager.SOURCE_KEY[1],
            "sourceR": stager.SOURCE_KEY[3],
            "sourceSubmissionId": stager.SOURCE_KEY[0],
            "status": "certified_multi",
            "workerExitCode": 0,
        }
        input_text = json.dumps(packet, separators=(",", ":"), sort_keys=True) + "\n"
        self.input.write_text(input_text, encoding="utf-8")
        self.input_sha = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
        proof_row = {
            "remainingLabelAssignments": [
                list(stager.EXPECTED_REMAINING_ASSIGNMENTS[0]),
                list(stager.EXPECTED_REMAINING_ASSIGNMENTS[1]),
            ],
            "remainingSlotAssignmentCount": 2,
            "sourceLabel": stager.SOURCE_KEY[2],
            "sourcePolynomialIndex": stager.SOURCE_KEY[1],
            "sourceR": stager.SOURCE_KEY[3],
            "sourceSubmissionId": stager.SOURCE_KEY[0],
            "status": "unresolved",
            "targetSlots": [
                {
                    "orbitIndex": slot["orbitIndex"],
                    "slotPosition": position,
                    "targetLabel": slot["targetLabel"],
                    "targetT": slot["targetT"],
                }
                for position, slot in enumerate(packet["orbitTargets"])
            ],
        }
        proof = {
            "input": str(self.input.resolve()),
            "inputSha256": self.input_sha,
            "jointFallbackEnabled": True,
            "method": frobenius.CERTIFICATE_METHOD,
            "primeBoundExclusive": 5000,
            "rows": [proof_row],
            "selectedInputRowsSha256": frobenius.selected_rows_digest([packet]),
            "summary": {"contradiction": 0, "resolved": 0, "rows": 1, "unresolved": 1},
        }
        self.proof.write_text(
            json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.proof_sha = stager.sha256_path(self.proof)

        source_line = coefficient_line(99)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO polynomials VALUES (?,?,?,?)",
                (stager.SOURCE_KEY[0], stager.SOURCE_KEY[1], source_line, digest(source_line)),
            )
            connection.execute(
                "INSERT INTO verifications VALUES (?,?,?,?,?,?,?)",
                (
                    stager.SOURCE_KEY[0],
                    stager.SOURCE_KEY[1],
                    "accepted",
                    stager.SOURCE_KEY[2],
                    6087,
                    stager.SOURCE_KEY[3],
                    1,
                ),
            )
            connection.execute(
                "INSERT INTO polynomials VALUES (?,?,?,?)",
                ("sub-anchor", 0, self.lines[0], self.hashes[0]),
            )
            connection.execute(
                "INSERT INTO verifications VALUES (?,?,?,?,?,?,?)",
                ("sub-anchor", 0, "accepted", "24T6080", 6080, 8, 1),
            )
            connection.execute(
                "INSERT INTO targets VALUES (?,?,?,?,?,?,?)",
                ("24T6364", 6364, 4, 2, "100", 1, "snapshot-a"),
            )
            connection.execute(
                "INSERT INTO targets VALUES (?,?,?,?,?,?,?)",
                ("24T6407", 6407, 0, 3, "200", 1, "snapshot-a"),
            )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def run_stage(self) -> dict:
        return stager.stage(
            root=self.root,
            data_dir=self.data,
            receipts_dir=self.receipts,
            database=self.database,
            input_path=self.input,
            frobenius_path=self.proof,
            manifest=self.manifest,
            certificate_output=self.certificate,
            summary_output=self.summary,
            expected_input_sha256=self.input_sha,
            expected_frobenius_sha256=self.proof_sha,
            expected_anchor_hash=self.hashes[0],
            expected_certificates=1,
        )

    def test_seals_two_forced_shared_pairs_without_coefficient_leak(self) -> None:
        result = self.run_stage()

        self.assertEqual(result["polynomials"], 2)
        self.assertEqual(result["projectedMarginalScoreExact"], "3/8")
        self.assertEqual(
            self.manifest.read_text(encoding="ascii"),
            self.lines[1] + "\n" + self.lines[2] + "\n",
        )
        summary = json.loads(self.summary.read_text(encoding="utf-8"))
        self.assertEqual(
            [row["pair"] for row in summary["selectedPairs"]],
            ["24T6364/r4", "24T6407/r0"],
        )
        certificate_text = self.certificate.read_text(encoding="utf-8")
        self.assertNotIn("coefficientLine", certificate_text)
        for line in self.lines:
            self.assertNotIn(line, certificate_text)
        self.assertIn("sub-anchor", certificate_text)

    def test_requires_an_accepted_scoreable_anchor(self) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE verifications SET status='queued',scoreable=0 "
                "WHERE submission_id='sub-anchor'"
            )
        with self.assertRaisesRegex(ValueError, "accepted scoreable verifier row"):
            self.run_stage()
        self.assertFalse(self.manifest.exists())

    def test_nonidentical_existing_seal_is_not_overwritten(self) -> None:
        self.run_stage()
        original_certificate = self.certificate.read_bytes()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE targets SET team_count=4 WHERE label='24T6407' AND r=0"
            )
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            self.run_stage()
        self.assertEqual(self.certificate.read_bytes(), original_certificate)


if __name__ == "__main__":
    unittest.main()
