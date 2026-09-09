from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import stage_all_exact_frobenius_unowned as stager
import stage_frobenius_gold as frobenius


def coefficient_line(seed: int) -> str:
    coefficients = [seed + 1, seed] + [0] * 22 + [1]
    return ",".join(str(value) for value in coefficients)


def digest(line: str) -> str:
    return hashlib.sha256(line.encode("ascii")).hexdigest()


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
                r INTEGER,
                scoreable INTEGER
            );
            CREATE TABLE polynomials (
                submission_id TEXT,
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


class AllExactFrobeniusStagerTests(unittest.TestCase):
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
        self.input = self.data / "candidates.jsonl"
        self.proof = self.data / "proof.json"
        self.manifest = self.outbox / "stage.txt"
        self.certificate = self.data / "stage_certificate.json"
        self.summary = self.data / "stage_summary.json"
        create_ledger(self.database)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_exact_certificate(self, specs: list[dict]) -> None:
        candidate_rows = []
        proof_rows = []
        with sqlite3.connect(self.database) as connection:
            for index, spec in enumerate(specs):
                source_id = f"sub-source-{index}"
                source_label = f"24T{100 + index}"
                line = spec["line"]
                candidate = {
                    "coefficientBytes": len(line.encode("ascii")),
                    "coefficientLine": line,
                    "coefficientSha256": digest(line),
                    "factorIndex": 0,
                    "polynomialDiscriminantAbs": str(spec.get("poly_disc", 1000 + index)),
                    "targetR": spec["r"],
                }
                if "field_disc" in spec:
                    candidate["fieldDiscriminantAbs"] = str(spec["field_disc"])
                candidate_rows.append(
                    {
                        "candidates": [candidate],
                        "orbitTargets": [
                            {
                                "orbitSize": 24,
                                "targetLabel": spec["label"],
                                "targetT": int(spec["label"][3:]),
                            }
                        ],
                        "sourceLabel": source_label,
                        "sourcePolynomialIndex": index,
                        "sourceR": 8,
                        "sourceSubmissionId": source_id,
                        "status": "certified_multi",
                        "workerExitCode": 0,
                    }
                )
                proof_rows.append(
                    {
                        "assignments": [
                            {
                                "coefficientSha256": digest(line),
                                "factorIndex": 0,
                                "targetLabel": spec["label"],
                                "targetR": spec["r"],
                                "targetT": int(spec["label"][3:]),
                            }
                        ],
                        "remainingLabelAssignments": [[spec["label"]]],
                        "sourceLabel": source_label,
                        "sourcePolynomialIndex": index,
                        "sourceR": 8,
                        "sourceSubmissionId": source_id,
                        "status": "resolved",
                    }
                )
                connection.execute(
                    "INSERT INTO verifications VALUES (?,?,?,?,?,?)",
                    (source_id, index, "accepted", source_label, 8, 1),
                )

        input_text = "".join(
            json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
            for row in candidate_rows
        )
        self.input.write_text(input_text, encoding="utf-8")
        proof = {
            "input": str(self.input),
            "inputSha256": hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
            "method": frobenius.CERTIFICATE_METHOD,
            "rows": proof_rows,
            "selectedInputRowsSha256": frobenius.selected_rows_digest(candidate_rows),
            "summary": {
                "contradiction": 0,
                "resolved": len(proof_rows),
                "rows": len(proof_rows),
                "unresolved": 0,
            },
        }
        self.proof.write_text(
            json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def run_stage(self, max_team_count: int | None = None) -> dict:
        return stager.stage(
            root=self.root,
            data_dir=self.data,
            receipts_dir=self.receipts,
            database=self.database,
            manifest=self.manifest,
            certificate_output=self.certificate,
            summary_output=self.summary,
            expected_certificates=1,
            max_team_count=max_team_count,
        )

    def add_target(self, label: str, r: int, team_count: int) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO targets VALUES (?,?,?,?,?,?,?)",
                (label, int(label[3:]), r, team_count, None, 1, "snapshot-a"),
            )

    def test_unsynced_receipt_excludes_hash_and_all_alternatives_for_pair(self) -> None:
        receipted = coefficient_line(0)
        alternative = coefficient_line(1)
        survivor = coefficient_line(2)
        self.write_exact_certificate(
            [
                {"line": receipted, "label": "24T20", "r": 4},
                {"line": alternative, "label": "24T20", "r": 4},
                {"line": survivor, "label": "24T30", "r": 8},
            ]
        )
        self.add_target("24T20", 4, 1)
        self.add_target("24T30", 8, 3)
        receipt_manifest = self.outbox / "submitted.txt"
        receipt_manifest.write_text(receipted + "\n", encoding="ascii")
        receipt = {
            "manifest": str(receipt_manifest),
            "manifestHash": stager.sha256_path(receipt_manifest),
            "polynomials": 1,
            "response": {"submissionId": "sub-unsynced"},
        }
        (self.receipts / "sub_unsynced.json").write_text(
            json.dumps(receipt) + "\n", encoding="utf-8"
        )

        result = self.run_stage()

        self.assertEqual(result["polynomials"], 1)
        self.assertEqual(self.manifest.read_text(encoding="ascii"), survivor + "\n")
        summary = json.loads(self.summary.read_text(encoding="utf-8"))
        self.assertEqual(
            summary["sequentialSkipCounts"],
            {"receipt_hash": 1, "receipt_pair": 1},
        )
        certificate_text = self.certificate.read_text(encoding="utf-8")
        self.assertNotIn("coefficientLine", certificate_text)
        self.assertNotIn(receipted, certificate_text)
        self.assertIn("sub-unsynced", certificate_text)

    def test_field_dedupe_max_team_count_and_sealed_collision(self) -> None:
        worse = coefficient_line(3)
        better = coefficient_line(4)
        above_cap = coefficient_line(5)
        self.write_exact_certificate(
            [
                {
                    "line": worse,
                    "label": "24T40",
                    "r": 0,
                    "field_disc": 200,
                    "poly_disc": 10,
                },
                {
                    "line": better,
                    "label": "24T40",
                    "r": 0,
                    "field_disc": 100,
                    "poly_disc": 20,
                },
                {"line": above_cap, "label": "24T50", "r": 8},
            ]
        )
        self.add_target("24T40", 0, 2)
        self.add_target("24T50", 8, 5)

        result = self.run_stage(max_team_count=4)

        self.assertEqual(result["polynomials"], 1)
        self.assertEqual(result["projectedMarginalScoreExact"], "1/4")
        self.assertEqual(self.manifest.read_text(encoding="ascii"), better + "\n")
        original_certificate = self.certificate.read_bytes()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE targets SET team_count=3 WHERE label='24T40' AND r=0"
            )
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            self.run_stage(max_team_count=4)
        self.assertEqual(self.certificate.read_bytes(), original_certificate)
        self.assertEqual(self.manifest.read_text(encoding="ascii"), better + "\n")

    def test_unsynced_all_compatible_receipt_maps_every_possible_pair(self) -> None:
        survivor = coefficient_line(8)
        submitted = coefficient_line(9)
        self.write_exact_certificate(
            [{"line": survivor, "label": "24T80", "r": 0}]
        )
        self.add_target("24T80", 0, 2)

        receipt_manifest = self.outbox / "ambiguous_submitted.txt"
        receipt_manifest.write_text(submitted + "\n", encoding="ascii")
        manifest_sha = stager.sha256_path(receipt_manifest)
        mapping = {
            "artifactSha256": {"manifest": manifest_sha},
            "method": "test-all-compatible-receipt-map-v1",
            "receiptPolynomialPossiblePairs": [
                {
                    "coefficientSha256": digest(submitted),
                    "possiblePairs": ["24T81/r0", "24T82/r0"],
                }
            ],
        }
        (self.data / "ambiguous_receipt_mapping.json").write_text(
            json.dumps(mapping, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        receipt = {
            "manifest": str(receipt_manifest),
            "manifestHash": manifest_sha,
            "polynomials": 1,
            "response": {"submissionId": "sub-ambiguous-unsynced"},
        }
        (self.receipts / "sub_ambiguous_unsynced.json").write_text(
            json.dumps(receipt) + "\n", encoding="utf-8"
        )

        result = self.run_stage()

        self.assertEqual(result["polynomials"], 1)
        certificate = json.loads(self.certificate.read_text(encoding="utf-8"))
        exclusion = certificate["receiptExclusion"]
        self.assertIn("24T81/r0", exclusion["excludedExactPairs"])
        self.assertIn("24T82/r0", exclusion["excludedExactPairs"])
        receipt_audit = next(
            row
            for row in exclusion["receipts"]
            if row["submissionId"] == "sub-ambiguous-unsynced"
        )
        self.assertEqual(
            receipt_audit["manifestEvidence"][0]["possiblePairMappedPolynomials"],
            1,
        )

    def test_tampered_saved_input_is_rejected_before_outputs(self) -> None:
        line = coefficient_line(6)
        self.write_exact_certificate(
            [{"line": line, "label": "24T60", "r": 4}]
        )
        self.add_target("24T60", 4, 1)
        self.input.write_text(self.input.read_text() + "\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "input SHA-256"):
            self.run_stage()
        self.assertFalse(self.manifest.exists())

    def test_missing_historical_absolute_input_remaps_below_project_root(self) -> None:
        self.input.write_text("pinned payload\n", encoding="utf-8")

        resolved = stager.resolve_saved_input(
            self.proof,
            "/path/to/private-file",
            self.root,
        )

        self.assertEqual(resolved, self.input.resolve())

    def test_source_provenance_mismatch_is_rejected(self) -> None:
        line = coefficient_line(7)
        self.write_exact_certificate(
            [{"line": line, "label": "24T70", "r": 8}]
        )
        self.add_target("24T70", 8, 1)
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE verifications SET scoreable=0 WHERE submission_id='sub-source-0'"
            )

        with self.assertRaisesRegex(ValueError, "source provenance"):
            self.run_stage()
        self.assertFalse(self.manifest.exists())


if __name__ == "__main__":
    unittest.main()
