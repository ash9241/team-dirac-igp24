from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import run_v16_gold_pair_resolvents as runner
import stage_frobenius_gold as frobenius
import stage_v16_gold_pair_resolvents as stager


def coefficient_line(seed: int) -> str:
    values = [seed + 1, seed] + [0] * 22 + [1]
    return ",".join(str(value) for value in values)


def candidate(seed: int, factor_index: int, field_disc: int) -> dict:
    line = coefficient_line(seed)
    return {
        "factorIndex": factor_index,
        "targetR": 24,
        "coefficientLine": line,
        "coefficientBytes": len(line.encode("ascii")),
        "coefficientSha256": hashlib.sha256(line.encode("ascii")).hexdigest(),
        "fieldDiscriminantAbs": str(field_disc),
        "polynomialDiscriminantAbs": str(field_disc * 10),
    }


class V16GoldPairResolventTests(unittest.TestCase):
    def test_runner_canonicalizes_and_rejects_non_degree_24(self) -> None:
        line = coefficient_line(7)
        canonical, digest = runner.canonical_line(line)
        self.assertEqual(canonical, line)
        self.assertEqual(digest, hashlib.sha256(line.encode("ascii")).hexdigest())
        with self.assertRaisesRegex(ValueError, "degree-24"):
            runner.canonical_line("1,1")

    def test_exact_join_selects_best_duplicate_target_factor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidates.jsonl"
            rows = [
                {
                    "status": "certified_multi",
                    "sourceSubmissionId": "sub_574f9e56ce804797ab49160e2f9838e6",
                    "sourcePolynomialIndex": 4,
                    "sourceLabel": "24T16875",
                    "sourceR": 24,
                    "orbitTargets": [
                        {"targetLabel": "24T17570", "targetT": 17570, "orbitSize": 24},
                        {"targetLabel": "24T17678", "targetT": 17678, "orbitSize": 24},
                        {"targetLabel": "24T17415", "targetT": 17415, "orbitSize": 24},
                    ],
                    "candidates": [candidate(1, 0, 100), candidate(2, 1, 200), candidate(3, 2, 300)],
                },
                {
                    "status": "certified_multi",
                    "sourceSubmissionId": "sub_574f9e56ce804797ab49160e2f9838e6",
                    "sourcePolynomialIndex": 12,
                    "sourceLabel": "24T17260",
                    "sourceR": 24,
                    "orbitTargets": [
                        {"targetLabel": "24T17286", "targetT": 17286, "orbitSize": 24},
                        {"targetLabel": "24T17796", "targetT": 17796, "orbitSize": 24},
                        {"targetLabel": "24T17796", "targetT": 17796, "orbitSize": 24},
                    ],
                    "candidates": [candidate(4, 0, 400), candidate(5, 1, 500), candidate(6, 2, 50)],
                },
            ]
            payload = "".join(
                json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
                for row in rows
            )
            path.write_text(payload, encoding="utf-8")
            assignments = [
                ["24T17570", "24T17678", "24T17415"],
                ["24T17286", "24T17796", "24T17796"],
            ]
            proof_rows = []
            for row, labels in zip(rows, assignments):
                proof_rows.append(
                    {
                        "status": "resolved",
                        "sourceSubmissionId": row["sourceSubmissionId"],
                        "sourcePolynomialIndex": row["sourcePolynomialIndex"],
                        "sourceLabel": row["sourceLabel"],
                        "sourceR": row["sourceR"],
                        "assignments": [
                            {
                                "factorIndex": item["factorIndex"],
                                "coefficientSha256": item["coefficientSha256"],
                                "targetLabel": label,
                                "targetT": int(label[3:]),
                                "targetR": 24,
                            }
                            for item, label in zip(row["candidates"], labels)
                        ],
                    }
                )
            proof = {
                "method": frobenius.CERTIFICATE_METHOD,
                "inputSha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                "selectedInputRowsSha256": frobenius.selected_rows_digest(rows),
                "summary": {"rows": 2, "resolved": 2, "unresolved": 0, "contradiction": 0},
                "rows": proof_rows,
            }

            selected, alternatives = stager.exact_join(proof, rows, path)

            self.assertEqual(
                {(row["targetLabel"], row["targetR"]) for row in selected},
                {("24T17570", 24), ("24T17796", 24)},
            )
            chosen = next(row for row in selected if row["targetLabel"] == "24T17796")
            self.assertEqual(chosen["fieldDiscriminantAbs"], 50)
            self.assertEqual(len(alternatives), 1)

    def test_atomic_new_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sealed.txt"
            stager.atomic_new(path, b"first\n")
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                stager.atomic_new(path, b"second\n")
            self.assertEqual(path.read_bytes(), b"first\n")


if __name__ == "__main__":
    unittest.main()
