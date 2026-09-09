import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import stage_single_exact_census as census


def polynomial_line(constant=1):
    return ",".join([str(constant)] + ["0"] * 23 + ["1"])


class SingleExactCensusTests(unittest.TestCase):
    def test_canonical_polynomial_shape(self):
        line = polynomial_line()
        self.assertEqual(census.canonical_polynomial_line(line), line)
        self.assertIsNone(census.canonical_polynomial_line("1,0,1"))
        self.assertIsNone(
            census.canonical_polynomial_line(",".join(["0"] * 24 + ["1"]))
        )

    def test_pair_sum_single_certificate(self):
        line = polynomial_line()
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        row = {
            "status": "certified",
            "workerExitCode": 0,
            "coefficientLine": line,
            "coefficientSha256": digest,
            "factorIndex": 0,
            "orbitCertificate": {
                "actualDegrees": [12, 24, 48],
                "expectedDegrees": [12, 24, 48],
                "exponents": [1, 1, 1],
            },
            "orbitTargets": [
                {
                    "orbitIndex": 2,
                    "orbitSize": 24,
                    "targetLabel": "24T123",
                    "targetT": 123,
                }
            ],
            "sourceLabel": "24T456",
            "sourcePolynomialIndex": 7,
            "sourceR": 8,
            "sourceSubmissionId": "sub_source",
            "targetLabel": "24T123",
            "targetR": 4,
            "targetT": 123,
        }
        result = census.validate_pair_sum(
            row, (), census.ROOT / "data" / "fixture.jsonl", 1, "$"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["targetLabel"], "24T123")
        self.assertNotIn("coefficientLine", result["proof"])

    def test_pair_factor_index_is_within_degree24_sublist(self):
        line = polynomial_line()
        row = {
            "status": "certified",
            "coefficientLine": line,
            "factorIndex": 1,
            "orbitCertificate": {
                "actualDegrees": [12, 24, 48],
                "expectedDegrees": [12, 24, 48],
                "exponents": [1, 1, 1],
            },
            "orbitTargets": [
                {"orbitIndex": 1, "orbitSize": 24, "targetLabel": "24T3", "targetT": 3}
            ],
            "sourceLabel": "24T4",
            "sourcePolynomialIndex": 0,
            "sourceR": 0,
            "sourceSubmissionId": "sub_source",
            "targetLabel": "24T3",
            "targetR": 0,
            "targetT": 3,
        }
        self.assertIsNone(
            census.validate_pair_sum(
                row, (), census.ROOT / "data" / "fixture.jsonl", 1, "$"
            )
        )

    def test_pair_sum_rejects_multi_degree24(self):
        line = polynomial_line()
        row = {
            "status": "certified",
            "coefficientLine": line,
            "factorIndex": 0,
            "orbitCertificate": {
                "actualDegrees": [24, 24],
                "expectedDegrees": [24, 24],
                "exponents": [1, 1],
            },
            "orbitTargets": [
                {"orbitIndex": 1, "orbitSize": 24, "targetLabel": "24T1", "targetT": 1}
            ],
            "sourceLabel": "24T2",
            "sourcePolynomialIndex": 0,
            "sourceR": 0,
            "sourceSubmissionId": "sub_source",
            "targetLabel": "24T1",
            "targetR": 0,
            "targetT": 1,
        }
        self.assertIsNone(
            census.validate_pair_sum(
                row, (), census.ROOT / "data" / "fixture.jsonl", 1, "$"
            )
        )

    def test_nested_pair_requires_exact_outer_mapping(self):
        line = polynomial_line()
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        row = {
            "status": "certified",
            "coefficientLine": line,
            "coefficientSha256": digest,
            "factorIndex": 0,
            "orbitCertificate": {
                "actualDegrees": [24],
                "expectedDegrees": [24],
                "exponents": [1],
            },
            "orbitTargets": [
                {"orbitIndex": 1, "orbitSize": 24, "targetLabel": "24T3", "targetT": 3}
            ],
            "sourceLabel": "24T4",
            "sourcePolynomialIndex": 0,
            "sourceR": 0,
            "sourceSubmissionId": "sub_source",
            "targetLabel": "24T3",
            "targetR": 0,
            "targetT": 3,
        }
        parent = {
            "candidate": row,
            "candidateSha256": digest,
            "mappedTargetR": [0, 8],
            "realizedTargetR": 8,
            "sourceCoefficientSha256": "a" * 64,
            "sourceLabel": "24T4",
            "sourcePolynomialIndex": 0,
            "sourceR": 0,
            "sourceSubmissionId": "sub_source",
            "status": "exact_signature_miss",
            "targetLabel": "24T3",
        }
        self.assertIsNone(
            census.validate_pair_sum(
                row, (parent,), census.ROOT / "data" / "fixture.jsonl", 1, "$/candidate"
            )
        )

    def test_twist_requires_one_resolved_action_label(self):
        line = polynomial_line()
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        row = {
            "actionResolutionMethod": "all-block-systems-same-target",
            "actionSystemCount": 1,
            "allBlockSystemsTargetLabels": ["24T12"],
            "coefficientLine": line,
            "coefficientSha256": digest,
            "genericActionProof": "exact generic twist proof",
            "ramificationPrime": 101,
            "sourceCoefficientSha256": "b" * 64,
            "sourceFieldDiscAbs": "17",
            "sourceLabel": "24T12",
            "sourcePolynomialIndex": 1,
            "sourceR": 0,
            "sourceSquarefreeModRamificationPrime": True,
            "sourceSubmissionId": "sub_source",
            "status": "certified_even_generic_negative_quadratic_twist",
            "targetLabel": "24T12",
            "targetR": 24,
            "targetT": 12,
            "twistDirectRealRootCount": 24,
            "twistIrreducible": True,
            "twistSign": "negative",
        }
        result = census.validate_twist(
            row, (), census.ROOT / "data" / "fixture.jsonl", 1, "$"
        )
        self.assertIsNotNone(result)
        row["allBlockSystemsTargetLabels"] = ["24T13"]
        self.assertIsNone(
            census.validate_twist(
                row, (), census.ROOT / "data" / "fixture.jsonl", 1, "$"
            )
        )

    def test_best_key_prefers_field_then_polynomial_discriminant(self):
        first = {
            "fieldDiscriminantAbs": "100",
            "polynomialDiscriminantAbs": "1000",
            "coefficientBytes": 50,
            "coefficientSha256": "a" * 64,
        }
        second = {
            "fieldDiscriminantAbs": "101",
            "polynomialDiscriminantAbs": "1",
            "coefficientBytes": 1,
            "coefficientSha256": "b" * 64,
        }
        self.assertLess(census.best_key(first), census.best_key(second))

    def test_receipt_exclusions_accepts_trailing_prime_hint_comment(self):
        line = polynomial_line()
        digest = hashlib.sha256(line.encode("ascii")).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipts = root / "receipts"
            data = root / "data"
            receipts.mkdir()
            data.mkdir()
            manifest = root / "manifest.txt"
            manifest.write_text(
                line + " # poly_disc_primes=[2,3]\n",
                encoding="utf-8",
            )
            manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
            (receipts / "sub_test.json").write_text(
                json.dumps(
                    {
                        "manifest": str(manifest),
                        "manifestHash": manifest_hash,
                        "polynomials": 1,
                        "response": {"submissionId": "sub_test"},
                    }
                ),
                encoding="utf-8",
            )
            connection = sqlite3.connect(":memory:")
            connection.executescript(
                """
                CREATE TABLE polynomials(
                    submission_id TEXT,
                    polynomial_index INTEGER,
                    coefficient_hash TEXT
                );
                CREATE TABLE verifications(
                    submission_id TEXT,
                    polynomial_index INTEGER,
                    label TEXT,
                    r INTEGER
                );
                """
            )
            try:
                hashes, pairs, audit = census.receipt_exclusions(
                    receipts, data, connection, {}
                )
            finally:
                connection.close()
        self.assertEqual(hashes, {digest})
        self.assertEqual(pairs, set())
        self.assertEqual(audit["receiptCount"], 1)
        self.assertEqual(audit["receiptPolynomialHashes"], 1)


if __name__ == "__main__":
    unittest.main()
