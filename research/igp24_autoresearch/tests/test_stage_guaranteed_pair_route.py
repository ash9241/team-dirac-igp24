from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import stage_guaranteed_pair_route as stage


def coefficient_line(seed: int = 3) -> str:
    return ",".join(str(value) for value in [1, seed] + [0] * 22 + [1])


def action() -> dict:
    return {
        "sourceLabel": "24T7829",
        "sourceT": 7829,
        "length24OrbitCount": 1,
        "orbitSizes": [12, 24, 48],
        "targets": [
            {
                "orbitIndex": 6,
                "orbitSize": 24,
                "targetLabel": "24T8394",
                "targetT": 8394,
                "imageOrder": 6144,
                "kernelOrder": 1,
            }
        ],
    }


def route(result: Path) -> dict:
    return {
        "output": str(result),
        "source": {
            "submissionId": "sub_source",
            "polynomialIndex": 641,
            "coefficientSha256": "a" * 64,
            "label": "24T7829",
            "r": 24,
        },
        "target": {"label": "24T8394", "r": 24},
    }


def worker_row() -> dict:
    line = coefficient_line()
    orbit = {
        "actualDegrees": [12, 24, 48],
        "expectedDegrees": [12, 24, 48],
        "exponents": [1, 1, 1],
    }
    return {
        "status": "certified_multi",
        "workerExitCode": 0,
        "sourceSubmissionId": "sub_source",
        "sourcePolynomialIndex": 641,
        "sourceCoefficientSha256": "a" * 64,
        "sourceLabel": "24T7829",
        "sourceR": 24,
        "transform": {"kind": "x+c*x^2", "c": 1},
        "reduction": "best",
        "orbitCertificate": orbit,
        "orbitTargets": action()["targets"],
        "attempts": [
            {
                "transform": 1,
                "resolventSha256": "b" * 64,
                "certificate": orbit,
            }
        ],
        "candidates": [
            {
                "factorIndex": 0,
                "targetR": 24,
                "coefficientLine": line,
                "coefficientBytes": len(line.encode("ascii")),
                "coefficientSha256": hashlib.sha256(line.encode("ascii")).hexdigest(),
                "polynomialDiscriminantAbs": "123",
                "fieldDiscriminantAbs": "101",
                "nfdiscSeconds": 0.01,
            }
        ],
    }


class GuaranteedPairStageTests(unittest.TestCase):
    def test_accepts_exact_single_candidate_certified_multi_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary) / "result.jsonl"
            result.write_text(json.dumps(worker_row()) + "\n", encoding="utf-8")
            selected = stage.validate_worker_result(
                route(result), action(), result.resolve(), data_root=result.parent
            )
            self.assertEqual(selected["targetPair"], ("24T8394", 24))
            self.assertEqual(
                selected["coefficientSha256"],
                worker_row()["candidates"][0]["coefficientSha256"],
            )

    def test_rejects_wrong_real_signature_without_frobenius_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = Path(temporary) / "result.jsonl"
            bad = copy.deepcopy(worker_row())
            bad["candidates"][0]["targetR"] = 22
            result.write_text(json.dumps(bad) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(stage.GuardFailure, "validation failed"):
                stage.validate_worker_result(
                    route(result), action(), result.resolve(), data_root=result.parent
                )

    def test_outbox_snapshot_maps_and_excludes_exact_target_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outbox = root / "outbox"
            outbox.mkdir()
            manifest = outbox / "planned.txt"
            other = outbox / "other.txt"
            line = coefficient_line(7)
            digest = hashlib.sha256(line.encode("ascii")).hexdigest()
            other.write_text(line + "\n", encoding="ascii")
            database = root / "ledger.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE polynomials(
                      submission_id TEXT, polynomial_index INTEGER,
                      coefficient_hash TEXT
                    );
                    CREATE TABLE verifications(
                      submission_id TEXT, polynomial_index INTEGER,
                      label TEXT, r INTEGER
                    );
                    """
                )
            with sqlite3.connect(database) as connection:
                hashes, pairs, meta = stage.outbox_snapshot(
                    outbox,
                    manifest,
                    {digest: {("24T8394", 24)}},
                    connection,
                )
            self.assertEqual(hashes, {digest})
            self.assertEqual(pairs, {("24T8394", 24)})
            self.assertEqual(meta["canonicalPolynomialRows"], 1)


if __name__ == "__main__":
    unittest.main()
