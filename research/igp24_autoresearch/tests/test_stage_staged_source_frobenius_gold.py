from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import stage_staged_source_frobenius_gold as stager


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


class StagedSourceProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.line = ",".join(map(str, [1] + [0] * 23 + [1]))
        self.digest = hashlib.sha256(self.line.encode("utf-8")).hexdigest()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_safe_and_ambiguous_duplicate_derivations_are_rechecked(self) -> None:
        manifest = self.root / "manifest.txt"
        manifest.write_text(self.line + "\n", encoding="utf-8")
        stage_path = self.root / "stage.json"
        stage = {
            "status": "staged_exact",
            "networkCalls": 0,
            "submissionCalls": 0,
            "batchSize": 1,
            "manifest": {"path": str(manifest), "sha256": sha256(manifest)},
            "candidates": [
                {
                    "coefficientLine": self.line,
                    "coefficientSha256": self.digest,
                    "fieldDiscriminantAbs": "17",
                    "target": {"label": "24T10", "r": 8},
                    "factorCertificate": {
                        "resolventSquarefree": True,
                        "actualDegrees": [24],
                        "expectedDegrees": [24],
                        "exponents": [1],
                    },
                }
            ],
        }
        write_json(stage_path, stage)
        result_candidate = {
            "status": "certified",
            "coefficientLine": self.line,
            "coefficientSha256": self.digest,
            "targetLabel": "24T10",
            "targetR": 8,
            "fieldDiscriminantAbs": "17",
            "orbitCertificate": {
                "actualDegrees": [24],
                "expectedDegrees": [24],
                "exponents": [1],
            },
        }
        results_path = self.root / "results.jsonl"
        rows = [
            {
                "status": "exact_frozen_gold_hit",
                "exactFrozenGoldHit": True,
                "routeMode": mode,
                "realizedTargetR": 8,
                "candidateSha256": self.digest,
                "candidate": copy.deepcopy(result_candidate),
            }
            for mode in ("safe", "ambiguous")
        ]
        results_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        row = {
            "sourceLabel": "24T10",
            "sourceR": 8,
            "sourceFieldDiscriminantAbs": "17",
        }
        certificate = {
            "safeResults": {"path": str(results_path), "sha256": sha256(results_path)},
            "safeStageCertificate": {
                "path": str(stage_path),
                "sha256": sha256(stage_path),
            },
        }
        stager.validate_safe_source_proof(
            row, certificate, manifest, 0, self.line, self.digest
        )

        rows[1]["candidate"]["orbitCertificate"]["actualDegrees"] = [12, 12]
        results_path.write_text(
            "".join(json.dumps(value) + "\n" for value in rows), encoding="utf-8"
        )
        certificate["safeResults"]["sha256"] = sha256(results_path)
        with self.assertRaisesRegex(ValueError, "proof envelope|disagree"):
            stager.validate_safe_source_proof(
                row, certificate, manifest, 0, self.line, self.digest
            )

    def test_f5_envelope_is_cross_linked_to_result_and_plan(self) -> None:
        upstream = {
            "submissionId": "upstream-sub",
            "polynomialIndex": 3,
            "coefficientSha256": "b" * 64,
            "label": "24T20",
            "r": 4,
            "t": 20,
            "fieldDiscAbs": "19",
        }
        action = {
            "sourceLabel": "24T20",
            "sourceT": 20,
            "targetLabel": "24T10",
            "targetT": 10,
        }
        result = {
            "status": "resolved_not_frozen_live_signature",
            "candidate": {
                "coefficientLine": self.line,
                "coefficientSha256": self.digest,
                "irreducible": True,
                "r": 8,
            },
            "target": {"label": "24T10", "r": 8, "t": 10},
            "exactAction": action,
            "source": upstream,
            "factorDegrees": [
                {"degree": 6, "exponent": 1},
                {"degree": 12, "exponent": 1},
            ],
            "pairResolventSha256": "a" * 64,
        }
        result_digest = stager.canonical_json_sha256(result)
        results_path = self.root / "f5-results.jsonl"
        results_path.write_text(json.dumps(result) + "\n", encoding="utf-8")
        plan_path = self.root / "f5-plan.json"
        plan = {
            "networkCalls": 0,
            "submissionCalls": 0,
            "exactActions": {"24T20": action},
            "selected": [{"source": upstream}],
        }
        write_json(plan_path, plan)
        construction = {
            "uniqueDegree12Factor": True,
            "matchingResultRowSha256": result_digest,
            "exactAction": action,
            "factorDegrees": result["factorDegrees"],
            "pairResolventSha256": "a" * 64,
            "upstreamSubmissionId": "upstream-sub",
            "upstreamPolynomialIndex": 3,
            "upstreamCoefficientSha256": "b" * 64,
            "upstreamLabel": "24T20",
            "upstreamR": 4,
            "upstreamFieldDiscriminantAbs": "19",
        }
        certificate = {
            "f5Results": {
                "path": str(results_path),
                "sha256": sha256(results_path),
                "matchingRowSha256": result_digest,
            },
            "f5Plan": {"path": str(plan_path), "sha256": sha256(plan_path)},
            "f5ExactConstruction": construction,
        }
        row = {"sourceLabel": "24T10", "sourceR": 8}
        stager.validate_f5_source_proof(row, certificate, self.line, self.digest)

        certificate["f5ExactConstruction"] = copy.deepcopy(construction)
        certificate["f5ExactConstruction"]["exactAction"]["targetT"] = 11
        with self.assertRaisesRegex(ValueError, "construction certificate"):
            stager.validate_f5_source_proof(
                row, certificate, self.line, self.digest
            )

    def test_only_all_compatible_gold_routes_are_eligible(self) -> None:
        orbit = {
            "routes": [
                {
                    "sourceR": 8,
                    "targetLabel": "24T10",
                    "mappedTargetR": [4],
                    "goldR": [4],
                    "allCompatibleClassesGold": False,
                },
                {
                    "sourceR": 8,
                    "targetLabel": "24T11",
                    "mappedTargetR": [4],
                    "goldR": [4],
                    "allCompatibleClassesGold": True,
                },
            ]
        }
        self.assertEqual(stager.exact_gold_routes(orbit, 8), {("24T11", 4)})
        self.assertEqual(
            stager.exact_gold_routes(orbit, 8, allow_ambiguous_exact_routes=True),
            {("24T10", 4), ("24T11", 4)},
        )


if __name__ == "__main__":
    unittest.main()
