from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import stage_v13_route_17513 as stage


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def lineage(path: Path) -> None:
    write_json(
        path,
        {
            "schemaVersion": "v13-exact-route-lineage-certificate-v1",
            "status": "three_closed_one_exact_worker_ready",
            "counts": {"conditionalRoutes": 4, "unresolvedExecutableRoutes": 1},
            "routes": [
                {
                    "source": {
                        "submissionId": stage.SOURCE_SUBMISSION,
                        "polynomialIndex": stage.SOURCE_INDEX,
                        "coefficientSha256": stage.SOURCE_HASH,
                        "label": stage.SOURCE_LABEL,
                        "r": stage.SOURCE_R,
                    },
                    "route": {
                        "targetLabel": stage.TARGET_LABEL,
                        "mappedTargetR": [8, 16, 20],
                        "censusExactCertificateSha256": "census-hash",
                    },
                    "liveSnapshot": {"reachableLiveGoldR": [16, 20]},
                    "outcome": "one_exact_pair_resolvent_required",
                }
            ],
        },
    )


def candidate(path: Path, target_r: int) -> str:
    values = [1] + [0] * 23 + [1]
    line = ",".join(str(value) for value in values)
    digest = hashlib.sha256(line.encode()).hexdigest()
    payload = {
        "status": "certified",
        "workerExitCode": 0,
        "sourceSubmissionId": stage.SOURCE_SUBMISSION,
        "sourcePolynomialIndex": stage.SOURCE_INDEX,
        "sourceCoefficientSha256": stage.SOURCE_HASH,
        "sourceLabel": stage.SOURCE_LABEL,
        "sourceR": stage.SOURCE_R,
        "targetLabel": stage.TARGET_LABEL,
        "targetT": 16970,
        "targetR": target_r,
        "coefficientLine": line,
        "coefficientBytes": len(line.encode()),
        "coefficientSha256": digest,
        "fieldDiscriminantAbs": "1234567",
        "factorIndex": 0,
        "orbitTargets": [
            {
                "orbitIndex": 1,
                "orbitSize": 24,
                "kernelOrder": 1,
                "targetLabel": stage.TARGET_LABEL,
            }
        ],
        "orbitCertificate": {
            "actualDegrees": [12, 24, 48, 48, 144],
            "expectedDegrees": [12, 24, 48, 48, 144],
            "exponents": [1, 1, 1, 1, 1],
        },
        "attempts": [{"resolventSha256": "resolvent-hash"}],
    }
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return digest


def database(path: Path, target_r: int, digest: str, owned: bool) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE polynomials (
            submission_id TEXT, polynomial_index INTEGER,
            coefficient_hash TEXT, PRIMARY KEY(submission_id,polynomial_index)
        );
        CREATE TABLE verifications (
            submission_id TEXT, polynomial_index INTEGER, status TEXT,
            label TEXT, r INTEGER, scoreable INTEGER, in_baseline INTEGER,
            scoring_status TEXT, field_disc_abs TEXT,
            PRIMARY KEY(submission_id,polynomial_index)
        );
        CREATE TABLE targets (
            label TEXT, r INTEGER, team_count INTEGER, discovered INTEGER,
            generated_at TEXT, PRIMARY KEY(label,r)
        );
        CREATE TABLE baseline_pairs (label TEXT, r INTEGER, PRIMARY KEY(label,r));
        """
    )
    connection.execute(
        "INSERT INTO polynomials VALUES (?,?,?)",
        (stage.SOURCE_SUBMISSION, stage.SOURCE_INDEX, stage.SOURCE_HASH),
    )
    connection.execute(
        "INSERT INTO verifications VALUES (?,?,?,?,?,?,?,?,?)",
        (
            stage.SOURCE_SUBMISSION,
            stage.SOURCE_INDEX,
            "accepted",
            stage.SOURCE_LABEL,
            stage.SOURCE_R,
            1,
            0,
            "scoreable",
            "source-field",
        ),
    )
    connection.execute(
        "INSERT INTO targets VALUES (?,?,?,?,?)",
        (
            stage.TARGET_LABEL,
            target_r,
            1 if owned else 0,
            1 if owned else 0,
            "2026-07-21T23:50:22Z",
        ),
    )
    if owned:
        connection.execute(
            "INSERT INTO polynomials VALUES (?,?,?)", ("owned", 0, "owned-hash")
        )
        connection.execute(
            "INSERT INTO verifications VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "owned",
                0,
                "accepted",
                stage.TARGET_LABEL,
                target_r,
                1,
                0,
                "scoreable",
                "owned-field",
            ),
        )
    connection.commit()
    connection.close()


def run(tmp_path: Path, target_r: int, owned: bool) -> tuple[dict, Path, Path]:
    lineage_path = tmp_path / "lineage.json"
    candidate_path = tmp_path / "candidate.jsonl"
    database_path = tmp_path / "ledger.sqlite3"
    manifest_path = tmp_path / "manifest.txt"
    certificate_path = tmp_path / "certificate.json"
    lineage(lineage_path)
    digest = candidate(candidate_path, target_r)
    database(database_path, target_r, digest, owned)
    result = stage.stage(
        candidate_path,
        lineage_path,
        database_path,
        manifest_path,
        certificate_path,
        allow_incomplete_target_cache=True,
    )
    return result, manifest_path, certificate_path


class StageV13Route17513Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_live_gold_is_staged_with_coefficient_free_certificate(self) -> None:
        result, manifest, certificate = run(self.root, 16, owned=False)
        self.assertEqual(result["status"], "staged_exact_live_gold")
        self.assertTrue(manifest.exists())
        payload = json.loads(certificate.read_text())
        self.assertEqual(payload["target"]["projectedMarginalScoreExact"], "1")
        self.assertNotIn("coefficientLine", certificate.read_text())

    def test_owned_r8_branch_is_closed_without_manifest(self) -> None:
        result, manifest, certificate = run(self.root, 8, owned=True)
        self.assertEqual(result["status"], "closed_exact_signature_miss")
        self.assertFalse(manifest.exists())
        self.assertIsNone(json.loads(certificate.read_text())["manifest"])

    def test_outputs_are_absent_guarded(self) -> None:
        result, manifest, certificate = run(self.root, 20, owned=False)
        self.assertEqual(result["status"], "staged_exact_live_gold")
        with self.assertRaises(FileExistsError):
            stage.stage(
                self.root / "candidate.jsonl",
                self.root / "lineage.json",
                self.root / "ledger.sqlite3",
                manifest,
                certificate,
                allow_incomplete_target_cache=True,
            )


if __name__ == "__main__":
    unittest.main()
